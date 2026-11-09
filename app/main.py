"""The UI and /api on 127.0.0.1:8790 (docs/DESIGN.md §5, §15): engines, runs (SSE), history, budget, presets,
catalog, estimate, local models."""

from __future__ import annotations

import asyncio
import functools
import json
import logging
import socket
import subprocess
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from app import __version__
from app.budget import Budget
from app.catalog import Catalog
from app.dataset_run import DATASET_TERMINAL, DatasetRunIn, DatasetRunner
from app.datasets import DatasetError, DatasetExists, DatasetNotFound, DatasetStore, DatasetUpload, parse_items
from app.engines.base import BaseEngine
from app.engines.mlx_local import MlxLocal
from app.experiments import EXPERIMENT_TERMINAL, ExperimentError, ExperimentIn, ExperimentRunner, check_size
from app.local_models import LocalModels, make_local
from app.presets import PresetError, PresetNotFound, list_presets, load_preset
from app.probe import PROBE_TERMINAL, PROBED_KINDS, ProbeIn, Prober, matrix_rows, valid_engine_id
from app.registry import Registry, load_config
from app.reports import build_report, render_markdown
from app.runner import TERMINAL, Runner, RunRejected, sse
from app.schemas import RunIn, RunOptions
from app.settings import ROOT, UI_HOSTS, Settings, get_settings, is_loopback
from app.stability import build_stability
from app.stability import render_markdown as stability_markdown
from app.store import Store

WEB = ROOT / "web"
log = logging.getLogger("playground.main")
UNLOAD_WAIT_S = 5
MAX_UI_BODY = 16 * 1024 * 1024  # a run's request; a transcript is well under 1 MB (review round 3, noted)
# DESIGN §18: the browser never frames this page and loads nothing from elsewhere (clickjacking, review P1)
SECURITY_HEADERS = {
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": "default-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}


@functools.lru_cache(maxsize=4)
def compat_address(host: str) -> str:
    """What whip-insight should call: the Mac's Bonjour name when the server listens on every interface (§9.3).
    `scutil --get LocalHostName` is the name mDNS answers to; the BSD hostname can differ on a DHCP-named Mac.
    Computed once (a subprocess), and called off the event loop."""
    if host != "0.0.0.0":
        return host
    try:
        name = subprocess.run(["scutil", "--get", "LocalHostName"], capture_output=True, text=True,
                              timeout=2).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        name = ""
    name = name or socket.gethostname().removesuffix(".local")
    return f"{name}.local"


def build_state(settings: Settings, transport=None, local: LocalModels | None = None) -> dict[str, Any]:
    settings.ensure_dirs()
    catalog = Catalog(settings.probes_dir / "catalog.json", base=settings.openrouter_base_url)
    config, _ = load_config(settings.config_path)
    owns_local = local is None
    if local is None:
        local = make_local(settings, config)
    registry = Registry(settings, catalog, transport=transport, local=local)
    store = Store(settings.runs_dir)
    budget = Budget(settings.daily_budget_usd, store)
    prober = Prober(store, budget, registry.ctx.probes, settings.data_dir / "media")
    runner = Runner(registry, store, budget)
    datasets = DatasetStore(settings.data_dir / "datasets")
    dataset_runner = DatasetRunner(runner, datasets, store, budget, settings.data_dir / "reports")
    experiment_runner = ExperimentRunner(runner, store, budget)
    return {"settings": settings, "catalog": catalog, "registry": registry, "store": store, "budget": budget,
            "runner": runner, "prober": prober, "datasets": datasets, "dataset_runner": dataset_runner,
            "experiment_runner": experiment_runner,
            "tasks": set(), "local": local,
            "owns_local": owns_local}


def create_app(settings: Settings | None = None, transport=None, refresh_catalog: bool = True,
               local: LocalModels | None = None, extra_hosts: tuple[str, ...] = ()) -> FastAPI:
    """`local`: the manager shared with the compat server (app.__main__); None builds one owned by this app.
    `extra_hosts`: Host names to trust besides 127.0.0.1 and localhost (tests only)."""
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        for k, v in build_state(settings, transport, local).items():
            setattr(app.state, k, v)
        app.state.local.start_watcher()
        app.state.store.reconcile()  # records written just before a crash, without their index line
        if refresh_catalog and app.state.catalog.stale:
            task = asyncio.create_task(app.state.catalog.refresh_quietly())
            app.state.tasks.add(task)
            task.add_done_callback(app.state.tasks.discard)
        yield
        await app.state.registry.aclose()
        if app.state.owns_local:
            await app.state.local.aclose()

    app = FastAPI(title="Decision Models Playground", version=__version__, lifespan=lifespan)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=[*UI_HOSTS, *extra_hosts])

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        # FastAPI's default puts the whole input back in the body and encodes it recursively: a deeply nested state
        # raised RecursionError (a 500) and a large one was echoed in full (review round 2). Location and message only.
        detail = [{"loc": list(e.get("loc", ())), "msg": str(e.get("msg", ""))[:300], "type": e.get("type")}
                  for e in exc.errors()[:20]]
        return JSONResponse({"detail": detail}, status_code=422)
    allowed_origins = {f"http://{h}:{settings.app_port}" for h in UI_HOSTS}

    @app.middleware("http")
    async def same_origin(request: Request, call_next):
        # The UI has no auth and a run spends money: a write from another site's page is refused (DESIGN §18).
        length = request.headers.get("content-length")
        if length is not None and (not (length.isascii() and length.isdigit()) or int(length) > MAX_UI_BODY):
            return JSONResponse({"detail": f"request body over {MAX_UI_BODY} bytes"}, status_code=413)
        origin = request.headers.get("origin")
        cross_site = request.headers.get("sec-fetch-site") == "cross-site"
        if request.method not in ("GET", "HEAD") and (origin and origin not in allowed_origins or cross_site):
            return JSONResponse({"detail": "cross-origin request refused"}, status_code=403)
        response = await call_next(request)
        response.headers.update(SECURITY_HEADERS)
        if request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-cache"  # a local tool: never run a stale module after an edit
        return response

    def st(request: Request) -> Any:
        return request.app.state

    def stream_events(request: Request, work, terminal: tuple[str, ...], fallback: str) -> StreamingResponse:
        """SSE over a background task: `work(emit)` ends with one of `terminal`. The task lives on if the browser
        goes away — a run or a probe is saved and billed either way. Exactly one terminal event per stream: a task
        that ends without one (a bug, a cancel) gets `fallback`, so no stream waits forever (review P3)."""
        state = st(request)
        queue: asyncio.Queue = asyncio.Queue()
        ended = False

        async def emit(name: str, payload: dict[str, Any]) -> None:
            nonlocal ended
            if ended:
                return
            ended = name in terminal
            await queue.put((name, payload))

        def finished(task: asyncio.Task) -> None:
            nonlocal ended
            state.tasks.discard(task)
            if not ended:
                ended = True
                why = "cancelled" if task.cancelled() else (
                    f"{type(task.exception()).__name__}" if task.exception() else "ended without a result")
                queue.put_nowait((fallback, {"message": f"the server's work {why}"}))

        task = asyncio.create_task(work(emit))
        state.tasks.add(task)
        task.add_done_callback(finished)

        async def events() -> AsyncIterator[str]:
            while True:
                name, payload = await queue.get()
                yield sse(name, payload)
                if name in terminal:
                    break

        return StreamingResponse(events(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    def stream(request: Request, run_in: RunIn) -> StreamingResponse:
        state = st(request)
        try:
            state.runner.engines_for(run_in.engines)
        except RunRejected as e:
            raise HTTPException(400, str(e)) from None
        return stream_events(request, lambda emit: state.runner.execute(run_in, emit), TERMINAL, "run_error")

    # --- engines, catalog, budget -------------------------------------------------------------------------------------
    @app.get("/api/engines")
    async def engines(request: Request) -> dict[str, Any]:
        state = st(request)
        return {"engines": state.registry.describe(), "catalog": state.catalog.summary(),
                "config": str(state.registry.config_source.name) if state.registry.config_source else None}

    @app.get("/api/catalog")
    async def catalog(request: Request) -> dict[str, Any]:
        c = st(request).catalog
        return {**c.summary(), "decisions": [
            {k: m.get(k) for k in ("id", "canonical_slug", "context_length", "pricing", "expiration_date", "architecture")}
            for m in c.data.get("decisions", [])
        ]}

    @app.post("/api/catalog/refresh")
    async def catalog_refresh(request: Request) -> dict[str, Any]:
        c = st(request).catalog
        await c.refresh_quietly()
        if c.error:
            raise HTTPException(502, f"catalog refresh failed: {c.error}")
        return c.summary()

    # --- probe and matrix (DESIGN §13) --------------------------------------------------------------------------
    def probe_engine(request: Request, body: ProbeIn) -> BaseEngine:
        """The engine a probe may run on, or the reason it may not — before anything streams or is paid."""
        state = st(request)
        engine = state.registry.engines.get(body.engine_id)
        if engine is None or engine.kind not in PROBED_KINDS:
            raise HTTPException(400, "no decision engine with this id")
        if not valid_engine_id(engine.spec.id):
            raise HTTPException(400, f"engine id {engine.spec.id!r} cannot name a probe directory: rename it in "
                                     "config.yaml (letters, digits, '.', '_', '-')")
        ok, reason = engine.available()
        if not ok:
            raise HTTPException(400, f"engine not available: {reason}")
        return engine

    @app.post("/api/probe/estimate")
    async def probe_estimate(request: Request, body: ProbeIn) -> dict[str, Any]:
        state = st(request)
        engine = probe_engine(request, body)
        return {"estimate_usd": state.prober.estimate(engine, body.long_context),
                "steps": state.prober.plan(engine, body.long_context), "budget": state.budget.status()}

    @app.post("/api/probe")
    async def probe(request: Request, body: ProbeIn) -> StreamingResponse:
        state = st(request)
        engine = probe_engine(request, body)
        if not engine.spec.zdr and not body.allow_no_zdr:
            raise HTTPException(400, "ZDR is off for this engine: confirm sending the probe's synthetic text "
                                     "without ZDR (allow_no_zdr)")
        if engine.spec.id in state.prober.busy:
            raise HTTPException(409, f"a probe of {engine.spec.id} is already running")

        async def work(emit):
            try:
                await state.prober.run(engine, emit, long_context=body.long_context, allow_no_zdr=body.allow_no_zdr)
            except Exception as e:
                log.exception("probe failed")
                await emit("probe_error", {"message": f"{type(e).__name__}: {e}"[:500],
                                           "budget": state.budget.status()})

        return stream_events(request, work, PROBE_TERMINAL, "probe_error")

    @app.get("/api/matrix")
    async def matrix(request: Request) -> dict[str, Any]:
        state = st(request)
        rows = matrix_rows(state.registry, state.registry.ctx.probes, state.store.rows())
        return {"rows": rows, "catalog": state.catalog.summary()}

    @app.get("/api/budget")
    async def budget(request: Request) -> dict[str, Any]:
        return st(request).budget.status()

    # --- runs --------------------------------------------------------------------------------------------------------
    @app.post("/api/estimate")
    async def estimate(request: Request, run_in: RunIn) -> dict[str, Any]:
        try:
            return st(request).runner.estimate(run_in)
        except RunRejected as e:
            raise HTTPException(400, str(e)) from None

    @app.post("/api/runs")
    async def create_run(request: Request, run_in: RunIn) -> StreamingResponse:
        return stream(request, run_in)

    @app.get("/api/runs")
    async def list_runs(request: Request, limit: int = 100, tag: str | None = None, engine: str | None = None,
                        kind: str | None = None) -> dict[str, Any]:
        return {"runs": st(request).store.list(min(max(limit, 1), 1000), tag, engine, kind)}

    @app.get("/api/runs/{run_id}")
    async def get_run(request: Request, run_id: str) -> dict[str, Any]:
        record = st(request).store.get(run_id)
        if record is None:
            raise HTTPException(404, "run not found")
        return record

    @app.post("/api/runs/{run_id}/rerun")
    async def rerun(request: Request, run_id: str, options: RunOptions | None = None) -> StreamingResponse:
        """The same inputs and engines. Consent is per run (§18): the record's `allow_no_zdr` is not inherited,
        the caller confirms it again in the body."""
        record = st(request).store.get(run_id)
        if record is None:
            raise HTTPException(404, "run not found")
        if record.get("kind", "compare") != "compare":  # an experiment's base request is not the experiment (review P5)
            raise HTTPException(400, f"a {record['kind']} record is not a Workbench run: it cannot be repeated here")
        try:
            run_in = RunIn(request=record["request"], engines=[e["engine_id"] for e in record["engines"]],
                           tags=record.get("tags", []), title=record.get("title"), options=options or RunOptions())
        except ValidationError as e:
            raise HTTPException(400, f"the run cannot be repeated: {e.errors()[0]['msg']}") from None
        return stream(request, run_in)

    # --- local models (DESIGN §8, §16) --------------------------------------------------------------------------------
    @app.get("/api/local/models")
    async def local_models(request: Request) -> dict[str, Any]:
        state = st(request)
        cfg = state.registry.config.compat
        return {
            "models": [e.describe() for e in state.registry.engines.values() if isinstance(e, MlxLocal)],
            "compat": {
                "url": f"http://{await asyncio.to_thread(compat_address, settings.compat_host)}:{settings.compat_port}"
                       "/v1/systemone",
                "lan": not is_loopback(settings.compat_host),
                "token_set": settings.compat_token is not None,
                "default_model": cfg.default_model,
                "extensions_header": cfg.extensions_header,
            },
        }

    @app.post("/api/local/models/{engine_id}/unload")
    async def unload_model(request: Request, engine_id: str) -> dict[str, Any]:
        engine = st(request).registry.engines.get(engine_id)
        if not isinstance(engine, MlxLocal):
            raise HTTPException(404, "no local engine with this id")
        try:
            async with asyncio.timeout(UNLOAD_WAIT_S):  # behind a hung call the button answers instead of hanging
                return {"unloaded": await st(request).local.unload(engine.repo)}
        except TimeoutError:
            raise HTTPException(409, "the model is busy; try again when the running call ends") from None

    # --- datasets and reports (DESIGN §12) ---------------------------------------------------------------------------
    def dataset_or_error(request: Request, name: str):
        try:
            return st(request).datasets.load(name)
        except DatasetNotFound as e:
            raise HTTPException(404, str(e)) from None
        except DatasetError as e:
            raise HTTPException(422, str(e)) from None

    @app.get("/api/datasets")
    async def list_datasets(request: Request) -> dict[str, Any]:
        store = st(request).datasets
        return {"datasets": [store.summary(n) for n in store.names()]}

    @app.get("/api/datasets/{name}")
    async def get_dataset(request: Request, name: str, preview: int = 20, ids: str | None = None) -> dict[str, Any]:
        """The dataset with its first `preview` items, or the items named in `ids` (the report's disagreements: the
        report holds no text, this local screen shows it). Texts are cut to 300 characters."""
        ds = dataset_or_error(request, name)

        def short(item) -> dict[str, Any]:
            text = item.state if isinstance(item.state, str) else json.dumps(item.state, ensure_ascii=False)
            return {"id": item.id, "state": text[:300] + ("…" if len(text) > 300 else ""), "labels": item.labels,
                    "meta": item.meta}

        out = {**st(request).datasets.summary(name),
               "questions": {k: q.model_dump(mode="json", exclude_none=True) for k, q in ds.questions.items()},
               "preview": [short(i) for i in ds.items[: min(max(preview, 0), 200)]]}
        if ids is not None:
            wanted = set(ids.split(",")[:500])
            out["items"] = [short(i) for i in ds.items if i.id in wanted]
        return out

    @app.post("/api/datasets", status_code=201)
    async def upload_dataset(request: Request, body: DatasetUpload) -> dict[str, Any]:
        store = st(request).datasets
        try:
            spec = body.spec()
            items = parse_items(body.content, body.format, store.questions_of(spec))
            store.create(spec, items, overwrite=body.overwrite)
        except DatasetExists as e:
            raise HTTPException(409, str(e)) from None
        except DatasetError as e:
            raise HTTPException(400, str(e)) from None
        except ValidationError as e:
            raise HTTPException(400, e.errors()[0]["msg"]) from None
        return store.summary(spec.name)

    def dataset_run_parts(request: Request, name: str, run_in: DatasetRunIn):
        state = st(request)
        ds = dataset_or_error(request, name)
        try:
            engines = state.runner.engines_for(run_in.engines)
        except RunRejected as e:
            raise HTTPException(400, str(e)) from None
        return ds, engines

    @app.post("/api/datasets/{name}/estimate")
    async def estimate_dataset(request: Request, name: str, run_in: DatasetRunIn) -> dict[str, Any]:
        state = st(request)
        ds, engines = dataset_run_parts(request, name, run_in)
        return await asyncio.to_thread(state.dataset_runner.estimate, ds, engines, run_in)  # 5 000 items: off the loop

    @app.post("/api/datasets/{name}/run")
    async def run_dataset(request: Request, name: str, run_in: DatasetRunIn) -> StreamingResponse:
        state = st(request)
        ds, engines = dataset_run_parts(request, name, run_in)
        return stream_events(request, lambda emit: state.dataset_runner.execute(ds, engines, run_in, emit),
                             DATASET_TERMINAL, "run_error")

    # --- experiments (DESIGN §11) -------------------------------------------------------------------------------------
    def experiment_parts(request: Request, exp: ExperimentIn):
        state = st(request)
        try:
            return state.runner.engines_for(exp.engines)
        except RunRejected as e:
            raise HTTPException(400, str(e)) from None

    @app.post("/api/experiments/estimate")
    async def estimate_experiment(request: Request, exp: ExperimentIn) -> dict[str, Any]:
        engines = experiment_parts(request, exp)
        try:  # off the loop: the grid re-reads the state per cell; the cap is checked before it is built
            return await asyncio.to_thread(st(request).experiment_runner.estimate, exp, engines)
        except ExperimentError as e:
            raise HTTPException(400, str(e)) from None

    @app.post("/api/experiments")
    async def run_experiment(request: Request, exp: ExperimentIn) -> StreamingResponse:
        state = st(request)
        engines = experiment_parts(request, exp)
        try:
            check_size(exp, len(engines))  # the grid's limit answers 400 before anything streams (arithmetic only)
        except ExperimentError as e:
            raise HTTPException(400, str(e)) from None
        return stream_events(request, lambda emit: state.experiment_runner.execute(exp, engines, emit),
                             EXPERIMENT_TERMINAL, "run_error")

    @app.get("/api/reports/{run_id}")
    async def report(request: Request, run_id: str, format: str = "json", lang: str = "ru", target: float = 0.9):
        if format == "kind":  # which screen opens it: from the index, without reading or building a report (review P5)
            row = next((r for r in st(request).store.rows() if r.get("id") == run_id), None)
            if row is None or row.get("kind") not in ("dataset", "experiment"):
                raise HTTPException(404, "no dataset run or experiment with this id")
            return {"run_id": run_id, "kind": row["kind"]}
        record = st(request).store.get(run_id)
        if record is None or record.get("kind") not in ("dataset", "experiment"):
            raise HTTPException(404, "no dataset run or experiment with this id")
        if record["kind"] == "dataset" and not 0.5 <= target <= 0.99:  # a target means nothing to an experiment
            raise HTTPException(400, "target is between 0.5 and 0.99")
        if format not in ("json", "md"):
            raise HTTPException(400, "format is json, md or kind")
        # off the event loop: a 5 000-item report takes seconds, and the compat server shares this loop (review P4)
        if record["kind"] == "experiment":
            rep = await asyncio.to_thread(build_stability, record)
            name, render = f"experiment-{record['day']}-{run_id}.md", stability_markdown
        else:
            rep = await asyncio.to_thread(build_report, record, target)
            name, render = f"{record['dataset']['name']}-{record['day']}-{run_id}.md", render_markdown
        if format == "md":
            text = await asyncio.to_thread(render, rep, lang)
            return PlainTextResponse(text, media_type="text/markdown; charset=utf-8",
                                     headers={"Content-Disposition": f'attachment; filename="{name}"'})
        return rep

    # --- presets -----------------------------------------------------------------------------------------------------
    @app.get("/api/presets")
    async def presets() -> dict[str, Any]:
        return {"presets": list_presets()}

    @app.get("/api/presets/{preset_id:path}")
    async def preset(preset_id: str) -> dict[str, Any]:
        try:
            return load_preset(preset_id)
        except PresetNotFound as e:
            raise HTTPException(404, str(e)) from None
        except PresetError as e:
            raise HTTPException(422, str(e)) from None

    # --- web ---------------------------------------------------------------------------------------------------------
    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(WEB / "index.html", headers={"Cache-Control": "no-cache"})

    app.mount("/static", StaticFiles(directory=WEB), name="static")
    return app

