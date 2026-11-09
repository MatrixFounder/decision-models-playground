"""The UI and /api on 127.0.0.1:8790 (docs/DESIGN.md §5, §15). P1 routes: engines, runs (SSE), history, budget,
presets, catalog, estimate."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import yaml
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from app import __version__
from app.budget import Budget
from app.catalog import Catalog
from app.registry import Registry
from app.runner import TERMINAL, Runner, RunRejected, sse
from app.schemas import Question, RunIn
from app.settings import ROOT, Settings, get_settings
from app.store import Store

WEB = ROOT / "web"
PRESETS = ROOT / "presets"
LOCAL_HOSTS = ["127.0.0.1", "localhost", "testserver"]


def build_state(settings: Settings, transport=None) -> dict[str, Any]:
    settings.ensure_dirs()
    catalog = Catalog(settings.probes_dir / "catalog.json")
    registry = Registry(settings, catalog, transport=transport)
    store = Store(settings.runs_dir)
    budget = Budget(settings.daily_budget_usd, store)
    return {"settings": settings, "catalog": catalog, "registry": registry, "store": store, "budget": budget,
            "runner": Runner(registry, store, budget), "tasks": set()}


def create_app(settings: Settings | None = None, transport=None, refresh_catalog: bool = True) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        for k, v in build_state(settings, transport).items():
            setattr(app.state, k, v)
        if refresh_catalog and app.state.catalog.stale:
            task = asyncio.create_task(app.state.catalog.refresh_quietly())
            app.state.tasks.add(task)
            task.add_done_callback(app.state.tasks.discard)
        yield
        await app.state.registry.aclose()

    app = FastAPI(title="Decision Models Playground", version=__version__, lifespan=lifespan)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=LOCAL_HOSTS)
    allowed_origins = {f"http://{h}:{settings.app_port}" for h in ("127.0.0.1", "localhost")}

    @app.middleware("http")
    async def same_origin(request: Request, call_next):
        # The UI has no auth and a run spends money: a write from another site's page is refused (DESIGN §18).
        origin = request.headers.get("origin")
        if request.method not in ("GET", "HEAD") and origin and origin not in allowed_origins:
            return JSONResponse({"detail": "cross-origin request refused"}, status_code=403)
        response = await call_next(request)
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
    async def rerun(request: Request, run_id: str) -> StreamingResponse:
        record = st(request).store.get(run_id)
        if record is None:
            raise HTTPException(404, "run not found")
        try:
            run_in = RunIn(request=record["request"], engines=[e["engine_id"] for e in record["engines"]],
                           tags=record.get("tags", []), title=record.get("title"), options=record.get("options", {}))
        except ValidationError as e:
            raise HTTPException(400, f"the run cannot be repeated: {e.errors()[0]['msg']}") from None
        return stream(request, run_in)

    # --- presets -----------------------------------------------------------------------------------------------------
    def preset_files() -> dict[str, Path]:
        return {p.stem: p for p in sorted(PRESETS.glob("*.yaml"))}

    def read_preset(path: Path) -> dict[str, Any]:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for k, q in (data.get("questions") or {}).items():
            Question.model_validate(q)  # a broken preset fails loudly, not in the middle of a run
        return {"id": path.stem, **data}

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


app = create_app()
