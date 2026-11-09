"""One comparative run over several engines (docs/DESIGN.md §10, figure 3).

1. Budget: the estimate of every paid engine; over what is left → `budget_stop`, no call.
2. Profile: questions an engine's profile refuses get `unsupported` and are not sent; media outside the profile
   refuses the request.
3. Engines run in parallel (each engine bounds itself by `concurrency`).
   A pointer run (`pointer: true`, DESIGN §14, UC-8) sends the transcript as turns and, after an engine's answer,
   asks its turn pointers one question at a time (app/pointer.py); the estimate and the reserve cover the passes.
4. `engine_done` / `engine_failed` per engine as soon as it finishes, its pointers included.
5. The record goes to runs/<day>/<id>.json and one line to runs/index.jsonl; then `run_done`.

Every run ends with exactly one terminal event: `run_done`, `budget_stop` or `run_error`.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from importlib import metadata
from typing import Any

from app import __version__
from app import pointer as ptr
from app.budget import Budget
from app.engines.base import BaseEngine
from app.registry import Registry
from app.schemas import CapabilityProfile, DecisionRequest, EngineResult, PointerResult, RunIn, RunOptions, Usage
from app.store import Store, local_day, new_id

log = logging.getLogger("playground.runner")
Emit = Callable[[str, dict[str, Any]], Awaitable[None]]
TERMINAL = ("run_done", "budget_stop", "run_error")


def mlx_vlm_version() -> str | None:
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

    @staticmethod
    def prepared(run_in: RunIn) -> tuple[DecisionRequest, list[str] | None]:
        """The request the run sends and, for a pointer run, its turn ids (raises PointerError)."""
        if not run_in.pointer:
            return run_in.request, None
        return ptr.prepare(run_in.request)

    @staticmethod
    def too_many_turns(turns: list[str] | None, profile: CapabilityProfile) -> str | None:
        """Past B² turns the route refuses the whole request at step 1 (`decision-run.js`): `too_long`, no call."""
        if turns is None or "choice" not in profile.types:
            return None
        b = ptr.width(profile.max_choice_options)
        return f"{len(turns)} turns, more than B² = {b * b} (B = {b})" if len(turns) > b * b else None

    def pointer_cost(self, engine: BaseEngine, req: DecisionRequest, turns: list[str],
                     profile: CapabilityProfile) -> float | None:
        """The pointer passes at their worst: every pointed question asks, every pass at its widest (§14)."""
        if "choice" not in profile.types:
            return 0.0
        b = ptr.width(profile.max_choice_options)
        keys, _ = self.split(engine, req)
        total = 0.0
        for k in keys:
            if req.questions[k].type not in ptr.POINTED:
                continue
            for pass_req in ptr.worst_passes(req.state, k, req.questions[k], turns, b):
                cost = engine.estimate_cost(pass_req)
                if cost is None:
                    return None
                total += cost
        return total

    def estimate(self, run_in: RunIn, profiles: dict[str, CapabilityProfile] | None = None) -> dict[str, Any]:
        """`profiles`: the snapshot a run takes once, so the estimate and the passes read the same B (review P6)."""
        engines = self.engines_for(run_in.engines)
        req, turns = self.prepared(run_in)
        profiles = profiles or {e.spec.id: e.profile() for e in engines}
        per_engine: dict[str, float | None] = {}
        pointers: dict[str, float | None] = {}
        for e in engines:
            keys, _ = self.split(e, req)
            if turns is not None and self.too_many_turns(turns, profiles[e.spec.id]):
                per_engine[e.spec.id] = pointers[e.spec.id] = 0.0  # refused before any call
                continue
            main = e.estimate_cost(req.subset(keys)) if keys else 0.0
            if turns is not None:
                pointers[e.spec.id] = self.pointer_cost(e, req, turns, profiles[e.spec.id]) if keys else 0.0
                main = None if main is None or pointers[e.spec.id] is None else main + pointers[e.spec.id]
            per_engine[e.spec.id] = main
        total = sum(v or 0.0 for v in per_engine.values())
        out = {"per_engine": per_engine, "total_usd": total, "budget": self.budget.status()}
        if turns is not None:
            out["pointer"] = {"turns": len(turns), "per_engine": pointers}
        return out

    async def call(self, engine: BaseEngine, req: DecisionRequest, opts: RunOptions) -> EngineResult:
        keys, refused = self.split(engine, req)
        all_keys = list(req.questions)
        off = sorted({m.type for m in req.media} - set(engine.profile().media))
        if off:  # the profile's media is a contract too: audio only where the probe heard it (§13)
            return EngineResult.build(engine.spec.id, all_keys, {}, {}, request_failure="refused",
                                      detail=f"the engine's profile takes no {', '.join(off)}")
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
            latency_ms=res.latency_ms, usage=res.usage, raw=res.raw, timings=res.timings, http_status=res.http_status,
        )

    async def point(self, engine: BaseEngine, req: DecisionRequest, res: EngineResult, turns: list[str],
                    opts: RunOptions, profile: CapabilityProfile) -> EngineResult:
        """The pointers of an engine's answers (§14): one per answer that asks one, in the questions' order. The
        passes' money and tokens join the engine's `usage`; each pointer keeps its own share. It runs after billed
        calls, so a bug in it fails that pointer, never the answers or the money (review P6)."""
        if not res.answers:
            return res
        pointers: dict[str, PointerResult] = {}
        passes: list[EngineResult] = []
        for key, q in req.questions.items():
            a = res.answers.get(key)
            text = ptr.instruction(q, a) if a is not None else None
            if text is None:
                continue
            try:
                pointers[key] = await self.point_one(engine, req.state, key, text, turns, opts, passes, profile)
            except Exception as e:
                log.exception("pointer of %s on %s failed", key, engine.spec.id)
                pointers[key] = PointerResult(instruction=text, failure="internal",
                                              detail=f"pointer error: {type(e).__name__}")
        if not passes:
            return res.model_copy(update={"pointers": pointers})
        return res.model_copy(update={"pointers": pointers, "usage": fold_usage(res.usage, [r.usage for r in passes])})

    async def point_one(self, engine: BaseEngine, state: Any, key: str, text: str, turns: list[str], opts: RunOptions,
                        spent: list[EngineResult], profile: CapabilityProfile) -> PointerResult:
        """One question's turn: no call for one turn; one pass up to B turns; past B, the block and then the turn."""
        if len(turns) == 1:
            return PointerResult(turn=turns[0], instruction=text)
        if "choice" not in profile.types:
            return PointerResult(instruction=text, failure="unsupported",
                                 detail="the engine's profile serves no choice: it cannot point")
        b = ptr.width(profile.max_choice_options)
        if ptr.passes(len(turns), b) == 0:  # past B² (the runner refuses such a request before the main call)
            return PointerResult(instruction=text, failure="too_long",
                                 detail=f"{len(turns)} turns, more than B² = {b * b} (B = {b})")
        mine: list[EngineResult] = []

        async def ask(candidates: list[str]) -> tuple[str | None, float | None, str | None, str | None]:
            r = await self.call(engine, ptr.pass_request(state, key, text, candidates), opts)
            mine.append(r)
            spent.append(r)
            if r.request_failure:
                return None, None, r.request_failure, r.detail
            if key in r.question_failures:
                return None, None, r.question_failures[key], r.detail
            a = r.answers.get(key)
            if a is None or a.top not in candidates:
                return None, None, "invalid_answer", None
            return a.top, a.probabilities.get(a.top), None, None

        def done(**kw: Any) -> PointerResult:
            costs = [r.usage.cost_usd for r in mine if r.usage.cost_usd is not None]
            return PointerResult(instruction=text, passes=sum(1 for r in mine if sent(r)),
                                 latency_ms=sum(r.latency_ms for r in mine), cost_usd=sum(costs) if costs else None, **kw)

        if len(turns) <= b:
            turn, p, failure, detail = await ask(turns)
            return done(turn=turn, probability=p, failure=failure, detail=detail)
        blocks = ptr.blocks(turns, b)
        head, p1, failure, detail = await ask([x[0] for x in blocks])
        if failure:
            return done(failure=failure, detail=f"pass 1: {detail}" if detail else "pass 1")
        block = next(x for x in blocks if x[0] == head)
        if len(block) == 1:
            return done(turn=head, probability=p1)
        turn, p2, failure, detail = await ask(block)
        if failure:
            return done(failure=failure, detail=f"pass 2: {detail}" if detail else "pass 2")
        return done(turn=turn, probability=None if p1 is None or p2 is None else p1 * p2)

    async def execute(self, run_in: RunIn, emit: Emit, kind: str = "compare") -> dict[str, Any] | None:
        reserved = 0.0
        try:
            engines = self.engines_for(run_in.engines)
            profiles = {e.spec.id: e.profile() for e in engines}  # one snapshot: the estimate and the passes agree
            est = self.estimate(run_in, profiles)
            reserved = est["total_usd"]
            # free engines (local models) run even when the day's money is spent
            if not math.isfinite(reserved) or reserved > 0 and not self.budget.reserve(reserved):
                reserved = 0.0
                total = est["total_usd"]
                await emit("budget_stop", {"estimate_usd": total if math.isfinite(total) else None,
                                           **self.budget.status()})
                return None

            run_id = new_id()
            now = time.time()
            req, turns = self.prepared(run_in)
            started = {"run_id": run_id, "engines": [e.spec.id for e in engines],
                       "estimate_usd": est["total_usd"], "per_engine": est["per_engine"]}
            if turns is not None:  # the screen shows the turns the engines read, not the text that was typed
                started["request"] = req.model_dump(mode="json", exclude_none=True)
            await emit("run_started", started)

            async def one(e: BaseEngine) -> EngineResult:
                await emit("engine_started", {"engine_id": e.spec.id})
                over = self.too_many_turns(turns, profiles[e.spec.id])
                if over:
                    res = EngineResult.build(e.spec.id, list(req.questions), {}, {}, request_failure="too_long",
                                             detail=over)
                else:
                    res = await self.call(e, req, run_in.options)
                    if turns is not None:
                        res = await self.point(e, req, res, turns, run_in.options, profiles[e.spec.id])
                await emit("engine_failed" if res.status == "failed" else "engine_done",
                           {"engine_id": e.spec.id, "result": res.model_dump(mode="json")})
                return res

            # every engine finishes before the run ends: no call goes on after a terminal event (review P6)
            outcomes = await asyncio.gather(*(one(e) for e in engines), return_exceptions=True)
            broken = [o for o in outcomes if isinstance(o, BaseException)]
            if broken:
                self.budget.charge_unsaved(sum(o.usage.cost_usd or 0.0 for o in outcomes if isinstance(o, EngineResult)))
                raise broken[0]
            results = list(outcomes)
            record = {
                "id": run_id,
                "created_at": datetime.fromtimestamp(now, UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "day": local_day(),  # the day the money is counted on: when it was spent, at the end
                "kind": kind,
                "title": run_in.title,
                "tags": run_in.tags,
                "request": req.model_dump(mode="json", exclude_none=True),
                "state_sha256": hashlib.sha256(req.state_text().encode()).hexdigest(),
                "options": run_in.options.model_dump(),
                "pointer": run_in.pointer,
                "engines": [{"engine_id": e.spec.id, "kind": e.kind, "model": e.spec.model or e.spec.repo,
                             "zdr": e.spec.zdr} for e in engines],
                "results": [r.model_dump(mode="json") for r in results],
                "estimate_usd": est["total_usd"],
                "cost_usd": sum(r.usage.cost_usd or 0.0 for r in results),
                "versions": {"app": __version__, "mlx_vlm": mlx_vlm_version(),
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


COST_RANK = {"provider": 0, "price": 1, "none": 2}


def fold_usage(main: Usage, passes: list[Usage]) -> Usage:
    """The engine's usage with its pointer passes: the money known is summed (the budget counts it); tokens stay
    unknown when the main call's are; the source is the weakest part's — a part with no cost makes it `none`."""
    parts = [main, *passes]
    costs = [u.cost_usd for u in parts if u.cost_usd is not None]

    def tokens(field: str) -> int | None:
        values = [getattr(u, field) for u in parts]
        return None if any(v is None for v in values) else sum(values)

    source = max((u.cost_source if u.cost_usd is not None else "none" for u in parts), key=COST_RANK.__getitem__)
    return Usage(input_tokens=tokens("input_tokens"), output_tokens=tokens("output_tokens"),
                 cost_usd=sum(costs) if costs else None, cost_source=source)


def sent(r: EngineResult) -> bool:
    """Whether a call reached the model: a request refused here (too long by bytes, no ZDR consent, the budget, a
    profile that refuses every question) never left the Mac and is not a pass."""
    if r.http_status is not None:
        return True
    if r.request_failure in ("too_long", "refused", "budget", "skipped"):
        return False
    profile_refused = (not r.answers and not r.request_failure and bool(r.question_failures)
                       and all(v == "unsupported" for v in r.question_failures.values()))
    return not profile_refused


def sse(event: str, payload: dict[str, Any]) -> str:
    # allow_nan=False: a bare NaN is not JSON and the browser would drop the frame (review P1)
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False, allow_nan=False)}\n\n"
