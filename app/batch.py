"""Many requests on several engines under the day's budget — what a dataset run (§12.4) and an experiment (§11)
share. The money rules live here once:

1. The estimate of every job on every paid engine is reserved first (`reserve`); a NaN estimate (a config typo)
   compares false with everything, so it stops the run instead of skipping the check.
2. Each engine works through the jobs in its own lane: a local model one job at a time (the manager's queue is
   short), a cloud model `concurrency` at once. Cloud engines run side by side; local engines take turns, a whole
   engine each, since one model fits in memory and alternating jobs would reload it every call (live UC-3: 170 of
   200 s were loads).
3. Every call goes through Runner.call: the engine's profile filter, the media check, the crash guard. Whatever else
   fails (an estimate, a bug) is that job's `provider_error`; no lane stops and leaves the others calling.
4. A call in flight holds the dearer of its estimate and the engine's dearest call so far, and each lane's first call
   goes alone, so a low estimate overshoots the day by at most one call per paid engine. When the actual cost
   outgrows the reservation more is reserved per call; when the day refuses, the paid jobs left are `budget` and not
   asked, free (local) jobs go on.
5. `close(saved)`: a run that was not saved (an exception, a cancel) is charged what it may have cost — finished
   calls and the calls in flight; the reservation is released either way.
"""

from __future__ import annotations

import asyncio
import logging
import math
from collections.abc import Awaitable, Callable
from typing import Any

from app import __version__
from app.budget import Budget
from app.engines.base import BaseEngine
from app.runner import Runner, mlx_vlm_version
from app.schemas import DecisionRequest, EngineResult, RunOptions

log = logging.getLogger("playground.batch")
Progress = Callable[[dict[str, Any]], Awaitable[None]]


def compact(res: EngineResult) -> dict[str, Any]:
    """What a dataset run or an experiment keeps of one answer: no raw body, no text."""
    return {
        "status": res.status,
        "answers": {k: {"probabilities": a.probabilities, "top": a.top, "top_p": a.top_p}
                    for k, a in res.answers.items()},
        "question_failures": res.question_failures,
        "request_failure": res.request_failure,  # the class and the status; no `detail`: a provider's message can
        "http_status": res.http_status,          # quote the request (review P4), and the record holds no call text
        "snapshot": res.snapshot,
        "latency_ms": res.latency_ms,
        "timings": res.timings,  # local: queue_ms, load_ms, call_ms
        "cost_usd": res.usage.cost_usd,
        "cost_source": res.usage.cost_source,
    }


def versions(results: dict[str, dict[str, EngineResult]]) -> dict[str, Any]:
    return {"app": __version__, "mlx_vlm": mlx_vlm_version(),
            "models": {eid: next((r.snapshot for r in reversed(per.values()) if r.snapshot), None)
                       for eid, per in results.items()}}


def lane_width(engine: BaseEngine) -> int:
    return 1 if engine.kind == "mlx_local" else max(1, engine.spec.concurrency)


class Batch:
    """`jobs` are (key, request) pairs asked of every engine; every request has the same questions keys and kinds,
    so what an engine's profile lets through is worked out once."""

    def __init__(self, runner: Runner, budget: Budget, engines: list[BaseEngine],
                 jobs: list[tuple[str, DecisionRequest]], options: RunOptions):
        self.runner, self.budget, self.engines, self.jobs, self.options = runner, budget, engines, jobs, options
        if jobs:  # what `sent` assumes: one question set — the same keys, types and sizes in every job (review P5)
            shape = self._shape(jobs[0][1])
            odd = next((key for key, req in jobs if self._shape(req) != shape), None)
            if odd is not None:
                raise ValueError(f"batch job {odd} asks other questions than the first job")
        self._costs: dict[tuple[str, int], float] = {}
        self.sent = {e.spec.id: runner.split(e, jobs[0][1])[0] if jobs else [] for e in engines}
        self.results: dict[str, dict[str, EngineResult]] = {e.spec.id: {} for e in engines}
        self.reserved = 0.0
        self.committed = 0.0  # actual costs so far plus what the calls in flight may cost
        self.seen: dict[str, float] = {}  # per engine: the dearest call so far — the next one may cost as much
        self.stopped = False  # the day's budget refused a paid call: no paid call after it (free ones go on)
        self.done = 0

    @property
    def total(self) -> int:
        return len(self.jobs) * len(self.engines)

    @staticmethod
    def _shape(req: DecisionRequest) -> tuple:
        return tuple(sorted((k, q.type, len(q.criteria or ())) for k, q in req.questions.items()))

    def cost_of(self, engine: BaseEngine, req: DecisionRequest) -> float:
        """The engine's estimate of one request, once per request object: an experiment repeats each cell's request,
        and a big state is read once per cell, not once per call (review P5)."""
        key = (engine.spec.id, id(req))
        if key not in self._costs:
            keys = self.sent[engine.spec.id]
            self._costs[key] = (engine.estimate_cost(req.subset(keys)) or 0.0) if keys else 0.0
        return self._costs[key]

    def estimate(self) -> dict[str, Any]:
        per_engine = {e.spec.id: sum(self.cost_of(e, req) for _, req in self.jobs) for e in self.engines}
        return {"per_engine": per_engine, "total_usd": sum(per_engine.values()), "calls": self.total,
                "budget": self.budget.status()}

    def reserve(self, total: float) -> bool:
        """The estimate up front; False (nothing reserved) when it is not a finite number or the day has no room."""
        if not math.isfinite(total) or total > 0 and not self.budget.reserve(total):
            return False
        self.reserved = max(0.0, total)
        return True

    def spent(self) -> float:
        return sum(r.usage.cost_usd or 0.0 for per in self.results.values() for r in per.values())

    def close(self, saved: bool) -> None:
        if not saved:  # an exception or a cancel: finished calls and the calls in flight may all be billed
            self.budget.charge_unsaved(max(self.spent(), self.committed))
        self.budget.release(self.reserved)
        self.reserved = 0.0

    def _top_up(self, more: float) -> bool:
        if self.budget.reserve(more):
            self.reserved += more
            return True
        return False

    async def one(self, e: BaseEngine, key: str, req: DecisionRequest, progress: Progress) -> None:
        """One job on one engine. Never raises: whatever fails here is that job's failure, so no lane stops and
        leaves the others calling after the run ended (review P4)."""
        try:
            need = self.cost_of(e, req)
            if not math.isfinite(need) or need < 0:
                raise ValueError(f"estimate {need!r}")
            hold = max(need, self.seen.get(e.spec.id, 0.0))
            if hold > 0 and (self.stopped or self.committed + hold > self.reserved
                             and not self._top_up(self.committed + hold - self.reserved)):
                self.stopped = True
                res = EngineResult.build(e.spec.id, list(req.questions), {}, {}, request_failure="budget",
                                         detail="the day's budget ran out during the run")
            else:
                self.committed += hold  # a cancel leaves it here: a call in flight may be billed
                res = await self.runner.call(e, req, self.options)
                cost = res.usage.cost_usd or 0.0
                self.committed += cost - hold
                self.seen[e.spec.id] = max(self.seen.get(e.spec.id, 0.0), cost)
        except Exception as exc:  # the engine itself is guarded by Runner.call: this is the estimate, a bug
            log.exception("batch: job %s on %s", key, e.spec.id)
            res = EngineResult.build(e.spec.id, list(req.questions), {}, {}, request_failure="provider_error",
                                     detail=f"run error: {type(exc).__name__}")
        self.results[e.spec.id][key] = res
        self.done += 1
        await progress({"done": self.done, "total": self.total, "engine_id": e.spec.id, "item_id": key,
                        "status": res.status, "request_failure": res.request_failure,
                        "cost_usd": res.usage.cost_usd})

    async def lane(self, e: BaseEngine, progress: Progress) -> None:
        queue = iter(self.jobs)  # shared by the lane's workers: each job is taken once
        first = next(queue, None)
        if first is None:
            return
        # the first call alone: its real cost is known before the lane's calls run side by side, so a low estimate
        # overshoots the day by one call per engine, not by the lane's width (review P4)
        await self.one(e, *first, progress)

        async def worker() -> None:
            for key, req in queue:
                await self.one(e, key, req, progress)

        async with asyncio.TaskGroup() as tg:
            for _ in range(lane_width(e)):
                tg.create_task(worker())

    async def run(self, progress: Progress) -> None:
        async def local_turns() -> None:
            for e in [e for e in self.engines if e.kind == "mlx_local"]:
                await self.lane(e, progress)

        async with asyncio.TaskGroup() as tg:  # a failure cancels the other lanes: no call after the end
            tg.create_task(local_turns())
            for e in self.engines:
                if e.kind != "mlx_local":
                    tg.create_task(self.lane(e, progress))
