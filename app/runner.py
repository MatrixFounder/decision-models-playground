"""One comparative run over several engines (docs/DESIGN.md §10, figure 3).

1. Budget: the estimate of every paid engine; over what is left → `budget_stop`, no call.
2. Profile: questions an engine's profile refuses get `unsupported` and are not sent.
3. Engines run in parallel (each engine bounds itself by `concurrency`).
4. `engine_done` / `engine_failed` per engine as soon as it finishes.
5. The record goes to runs/<day>/<id>.json and one line to runs/index.jsonl; then `run_done`.

Every run ends with exactly one terminal event: `run_done`, `budget_stop` or `run_error`.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from importlib import metadata
from typing import Any

from app import __version__
from app.budget import Budget
from app.engines.base import BaseEngine
from app.registry import Registry
from app.schemas import DecisionRequest, EngineResult, RunIn, RunOptions
from app.store import Store, local_day, new_id

log = logging.getLogger("playground.runner")
Emit = Callable[[str, dict[str, Any]], Awaitable[None]]
TERMINAL = ("run_done", "budget_stop", "run_error")


def _mlx_vlm_version() -> str | None:
    try:
        return metadata.version("mlx-vlm")
    except metadata.PackageNotFoundError:
        return None


class RunRejected(ValueError):
    """The run cannot start: unknown or unavailable engines. The API answers 400 before streaming."""


class Runner:
    def __init__(self, registry: Registry, store: Store, budget: Budget):
        self.registry, self.store, self.budget = registry, store, budget

    def engines_for(self, ids: list[str]) -> list[BaseEngine]:
        unknown = [i for i in ids if i not in self.registry.engines]
        if unknown:
            raise RunRejected(f"unknown engines: {', '.join(unknown)}")
        engines = [self.registry.get(i) for i in dict.fromkeys(ids)]
        down = [f"{e.spec.id} ({e.available()[1]})" for e in engines if not e.available()[0]]
        if down:
            raise RunRejected(f"engines not available: {'; '.join(down)}")
        return engines

    @staticmethod
    def split(engine: BaseEngine, req: DecisionRequest) -> tuple[list[str], dict[str, str]]:
        """The keys to send and the keys the engine's profile refuses (§10 step 2)."""
        profile = engine.profile()
        refused = {k: "unsupported" for k, q in req.questions.items() if profile.refuse(q)}
        return [k for k in req.questions if k not in refused], refused

    def estimate(self, run_in: RunIn) -> dict[str, Any]:
        engines = self.engines_for(run_in.engines)
        per_engine: dict[str, float | None] = {}
        for e in engines:
            keys, _ = self.split(e, run_in.request)
            per_engine[e.spec.id] = e.estimate_cost(run_in.request.subset(keys)) if keys else 0.0
        total = sum(v or 0.0 for v in per_engine.values())
        return {"per_engine": per_engine, "total_usd": total, "budget": self.budget.status()}

    async def call(self, engine: BaseEngine, req: DecisionRequest, opts: RunOptions) -> EngineResult:
        keys, refused = self.split(engine, req)
        all_keys = list(req.questions)
        if not keys:
            return EngineResult.build(engine.spec.id, all_keys, {}, refused, detail="no question fits the engine's profile")
        try:
            res = await engine.run(req.subset(keys), opts)
        except Exception as e:  # an engine bug must not sink the run
            log.exception("engine %s crashed", engine.spec.id)
            return EngineResult.build(engine.spec.id, all_keys, {}, refused, request_failure="provider_error",
                                      detail=f"engine error: {type(e).__name__}")
        return EngineResult.build(
            engine.spec.id, all_keys, res.answers, {**refused, **res.question_failures},
            request_failure=res.request_failure, detail=res.detail, snapshot=res.snapshot,
            latency_ms=res.latency_ms, usage=res.usage, raw=res.raw, timings=res.timings,
        )

    async def execute(self, run_in: RunIn, emit: Emit, kind: str = "compare") -> dict[str, Any] | None:
        reserved = 0.0
        try:
            engines = self.engines_for(run_in.engines)
            est = self.estimate(run_in)
            reserved = est["total_usd"]
            # free engines (local models) run even when the day's money is spent
            if reserved > 0 and not self.budget.reserve(reserved):
                reserved = 0.0
                await emit("budget_stop", {"estimate_usd": est["total_usd"], **self.budget.status()})
                return None

            run_id = new_id()
            now = time.time()
            req = run_in.request
            await emit("run_started", {"run_id": run_id, "engines": [e.spec.id for e in engines],
                                       "estimate_usd": est["total_usd"], "per_engine": est["per_engine"]})

            async def one(e: BaseEngine) -> EngineResult:
                await emit("engine_started", {"engine_id": e.spec.id})
                res = await self.call(e, req, run_in.options)
                await emit("engine_failed" if res.status == "failed" else "engine_done",
                           {"engine_id": e.spec.id, "result": res.model_dump(mode="json")})
                return res

            results = await asyncio.gather(*(one(e) for e in engines))
            record = {
                "id": run_id,
                "created_at": datetime.fromtimestamp(now, UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "day": local_day(now),
                "kind": kind,
                "title": run_in.title,
                "tags": run_in.tags,
                "request": req.model_dump(mode="json", exclude_none=True),
                "state_sha256": hashlib.sha256(req.state_text().encode()).hexdigest(),
                "options": run_in.options.model_dump(),
                "engines": [{"engine_id": e.spec.id, "kind": e.kind, "model": e.spec.model or e.spec.repo,
                             "zdr": e.spec.zdr} for e in engines],
                "results": [r.model_dump(mode="json") for r in results],
                "estimate_usd": est["total_usd"],
                "cost_usd": sum(r.usage.cost_usd or 0.0 for r in results),
                "versions": {"app": __version__, "mlx_vlm": _mlx_vlm_version(),
                             "models": {r.engine_id: r.snapshot for r in results if r.snapshot}},
            }
            try:
                self.store.save(record)
            except Exception:
                self.budget.charge_unsaved(record["cost_usd"])  # the providers billed it either way
                raise
            finally:
                self.budget.release(reserved)  # the saved record (or the unsaved charge) now carries the real cost
                reserved = 0.0
            await emit("run_done", {"run_id": run_id, "cost_usd": record["cost_usd"], "day": record["day"],
                                    "budget": self.budget.status()})
            return record
        except Exception as e:
            log.exception("run failed")
            await emit("run_error", {"message": f"{type(e).__name__}: {e}"[:500]})
            return None
        finally:
            if reserved:
                self.budget.release(reserved)


def sse(event: str, payload: dict[str, Any]) -> str:
    # allow_nan=False: a bare NaN is not JSON and the browser would drop the frame (review P1)
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False, allow_nan=False)}\n\n"
