"""The UI and /api on 127.0.0.1:8790 (docs/DESIGN.md §5, §15): engines, runs (SSE), history, budget, presets,
catalog, estimate, local models."""

from __future__ import annotations

import asyncio
import socket
import subprocess
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import yaml
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from app import __version__
from app.budget import Budget
from app.catalog import Catalog
from app.engines.mlx_local import MlxLocal
from app.local_models import LocalModels, make_local
from app.registry import Registry, load_config
from app.runner import TERMINAL, Runner, RunRejected, sse
from app.schemas import Question, RunIn, RunOptions
from app.settings import ROOT, UI_HOSTS, Settings, get_settings, is_loopback
from app.store import Store

WEB = ROOT / "web"
UNLOAD_WAIT_S = 5
PRESETS = ROOT / "presets"
# DESIGN §18: the browser never frames this page and loads nothing from elsewhere (clickjacking, review P1)
SECURITY_HEADERS = {
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": "default-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}


def compat_address(host: str) -> str:
    """What whip-insight should call: the Mac's Bonjour name when the server listens on every interface (§9.3).
    `scutil --get LocalHostName` is the name mDNS answers to; the BSD hostname can differ on a DHCP-named Mac."""
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
    catalog = Catalog(settings.probes_dir / "catalog.json")
    config, _ = load_config(settings.config_path)
    owns_local = local is None
    if local is None:
        local = make_local(settings, config)
    registry = Registry(settings, catalog, transport=transport, local=local)
    store = Store(settings.runs_dir)
    budget = Budget(settings.daily_budget_usd, store)
    return {"settings": settings, "catalog": catalog, "registry": registry, "store": store, "budget": budget,
            "runner": Runner(registry, store, budget), "tasks": set(), "local": local, "owns_local": owns_local}


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

    def stream(request: Request, run_in: RunIn) -> StreamingResponse:
        state = st(request)
        try:
            state.runner.engines_for(run_in.engines)
        except RunRejected as e:
            raise HTTPException(400, str(e)) from None
        queue: asyncio.Queue = asyncio.Queue()

        async def emit(name: str, payload: dict[str, Any]) -> None:
            await queue.put((name, payload))

        # the run lives on if the browser goes away: it is saved and billed either way
        task = asyncio.create_task(state.runner.execute(run_in, emit))
        state.tasks.add(task)
        task.add_done_callback(state.tasks.discard)

        async def events() -> AsyncIterator[str]:
            while True:
                name, payload = await queue.get()
                yield sse(name, payload)
                if name in TERMINAL:
                    break

        return StreamingResponse(events(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

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
                "url": f"http://{compat_address(settings.compat_host)}:{settings.compat_port}/v1/systemone",
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

    # --- presets -----------------------------------------------------------------------------------------------------
    def preset_files() -> dict[str, Path]:
        return {p.stem: p for p in sorted(PRESETS.glob("*.yaml"))}

    def read_preset(path: Path) -> dict[str, Any]:
        """A preset with its questions in the internal form (d1's `choice` + `multiple` becomes `multi`, §6.1); a
        broken preset fails loudly here, not in the middle of a run."""
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        questions = {k: Question.model_validate(q).model_dump(mode="json", exclude_none=True)
                     for k, q in (data.get("questions") or {}).items()}
        return {**data, "id": path.stem, "questions": questions}

    @app.get("/api/presets")
    async def presets() -> dict[str, Any]:
        out = []
        for pid, path in preset_files().items():
            try:
                p = read_preset(path)
                out.append({"id": pid, "title": p.get("title", pid), "questions": list(p.get("questions") or {}),
                            "has_state": bool(p.get("state"))})
            except (ValueError, yaml.YAMLError) as e:
                out.append({"id": pid, "title": pid, "error": str(e)[:200]})
        return {"presets": out}

    @app.get("/api/presets/{preset_id}")
    async def preset(preset_id: str) -> dict[str, Any]:
        path = preset_files().get(preset_id)
        if path is None:
            raise HTTPException(404, "preset not found")
        try:
            return read_preset(path)
        except (ValueError, yaml.YAMLError) as e:
            raise HTTPException(422, f"broken preset: {str(e)[:300]}") from None

    # --- web ---------------------------------------------------------------------------------------------------------
    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(WEB / "index.html", headers={"Cache-Control": "no-cache"})

    app.mount("/static", StaticFiles(directory=WEB), name="static")
    return app

