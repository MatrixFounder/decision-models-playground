"""Experiments (docs/ARCHITECTURE.md §3.8, UC-2): one state and a grid of variants, each variant a separate call.

The grid is formulations × option orders × repeats, asked of every engine:

- **formulations** — the request's own questions ("base") and up to five more wordings (RU and EN, with and without
  a hint). Every wording has the same question keys and types, the same option ids and the same number of levels:
  otherwise nothing is compared.
- **orders** — the options of `choice` and `multi` questions shown in another order. Score levels are not reordered:
  their order is their meaning (lowest first). With at most 4 options every order (up to 24); with more, `k` orders
  (6 by default): the original and k − 1 distinct random ones, from `seed`. Several choice questions in one request
  take their own orders side by side (order i of each).
- **repeats** — the same call N times (5 by default): the noise every other effect is measured against.

The calls, lanes and the day's budget are app.batch's. The record (kind `experiment`) keeps the request (the state as
the Workbench keeps it), the grid and compact answers per call; app.stability reads it into the report.
"""

from __future__ import annotations

import asyncio
import hashlib
import itertools
import logging
import math
import random
import time
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.batch import Batch, compact, versions
from app.budget import Budget
from app.engines.base import BaseEngine
from app.runner import Emit, Runner
from app.schemas import DecisionRequest, Question, RunOptions, valid_key
from app.stability import build_stability, summarize
from app.store import Store, local_day, new_id

log = logging.getLogger("playground.experiments")
EXPERIMENT_TERMINAL = ("run_done", "budget_stop", "run_error")
MAX_FORMULATIONS = 6  # the base and five more
MAX_ALL_ORDERS = 4  # up to this many options every order is asked (4! = 24)
MAX_CALLS = 2000
ORDERED = ("choice", "multi")


class ExperimentError(ValueError):
    """An experiment that cannot be planned; the message says what to change."""


class OrderAxis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    k: int = Field(6, ge=2, le=24)  # orders of a question with more than 4 options, the original among them
    seed: int = 7


class ExperimentIn(BaseModel):
    """POST /api/experiments and /api/experiments/estimate."""

    model_config = ConfigDict(extra="forbid")

    request: DecisionRequest  # the state and the base wording
    base_name: str = "base"
    formulations: dict[str, dict[str, Question]] = Field(default_factory=dict)  # more wordings, by name
    orders: OrderAxis = Field(default_factory=OrderAxis)
    repeats: int = Field(5, ge=1, le=20)
    engines: list[str] = Field(min_length=1)
    options: RunOptions = Field(default_factory=RunOptions)
    title: str | None = None
    tags: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check(self) -> ExperimentIn:
        names = [self.base_name, *self.formulations]
        bad = [n for n in names if not valid_key(n)]
        if bad:
            raise ValueError(f"formulation names must be keys (letters, digits, _): {bad[:3]}")
        if self.base_name in self.formulations:
            raise ValueError(f"{self.base_name!r} is the request's own wording; name the others differently")
        if len(names) > MAX_FORMULATIONS:
            raise ValueError(f"at most {MAX_FORMULATIONS} formulations, the base included")
        base = self.request.questions
        for name, qs in self.formulations.items():
            if set(qs) != set(base):
                raise ValueError(f"formulation {name}: the questions are {sorted(base)}, not {sorted(qs)}")
            for k, q in qs.items():
                b = base[k]
                if q.type != b.type:
                    raise ValueError(f"formulation {name}, {k}: type {q.type}, the base has {b.type}")
                if q.type in ORDERED and set(q.criteria or {}) != set(b.criteria or {}):
                    raise ValueError(f"formulation {name}, {k}: the option ids must be the base's")
                if q.type == "score" and len(q.criteria or []) != len(b.criteria or []):
                    raise ValueError(f"formulation {name}, {k}: {len(b.criteria or [])} levels, as the base")
        return self


def orders_of(ids: list[str], k: int, seed: int, salt: str) -> list[list[str]]:
    """The orders a question's options are shown in, the original first. Every order up to MAX_ALL_ORDERS options;
    beyond, k distinct ones, random but fixed by the seed and the question."""
    if len(ids) <= MAX_ALL_ORDERS:
        return [list(p) for p in itertools.permutations(ids)]  # itertools yields the original first
    rng = random.Random(int(hashlib.sha256(f"{seed}:{salt}".encode()).hexdigest()[:12], 16))
    out, seen = [list(ids)], {tuple(ids)}
    total = math.factorial(len(ids))
    while len(out) < min(k, total):
        p = ids[:]
        rng.shuffle(p)
        if tuple(p) not in seen:
            seen.add(tuple(p))
            out.append(p)
    return out


def reorder(q: Question, order: list[str]) -> Question:
    return q.model_copy(update={"criteria": {i: (q.criteria or {})[i] for i in order}})


class Plan(BaseModel):
    formulations: dict[str, dict[str, Question]]  # the base first
    orders: dict[str, list[list[str]]]  # question key → its orders (choice and multi only)
    n_orders: int
    repeats: int
    jobs: list[tuple[str, DecisionRequest]]
    cells: list[dict[str, Any]]  # per job key: formulation, order index, repeat


def question_orders(exp: ExperimentIn) -> dict[str, list[list[str]]]:
    if not exp.orders.enabled:
        return {}
    return {key: orders_of(list(q.criteria or {}), exp.orders.k, exp.orders.seed, key)
            for key, q in exp.request.questions.items() if q.type in ORDERED}


def grid_size(exp: ExperimentIn) -> int:
    """The grid's cells, counted without building a request (the cap is checked before anything is built)."""
    n_orders = max((len(v) for v in question_orders(exp).values()), default=1)
    return (1 + len(exp.formulations)) * n_orders * exp.repeats


def check_size(exp: ExperimentIn, engines: int) -> None:
    calls = grid_size(exp) * engines
    if calls > MAX_CALLS:
        raise ExperimentError(f"{calls} calls: at most {MAX_CALLS} in one experiment — fewer repeats, orders (k) or "
                              f"formulations")


def plan(exp: ExperimentIn) -> Plan:
    base = exp.request.questions
    # every wording in the base's question order and option order: otherwise a formulation typed with its options in
    # another order compares wording and order at once (review P5)
    forms = {exp.base_name: base}
    for name, qs in exp.formulations.items():
        forms[name] = {k: reorder(qs[k], list(base[k].criteria or {})) if base[k].type in ORDERED else qs[k]
                       for k in base}
    orders = question_orders(exp)
    n_orders = max((len(v) for v in orders.values()), default=1)
    requests = {(f, o): DecisionRequest(state=exp.request.state, media=exp.request.media,
                                        questions={k: reorder(q, orders[k][o % len(orders[k])]) if k in orders else q
                                                   for k, q in qs.items()})
                for f, qs in forms.items() for o in range(n_orders)}
    jobs, cells = [], []
    # repeat-major: every cell's first repeat before any second one — a budget stop thins the grid instead of dropping
    # the later formulations whole (review P5)
    for r in range(exp.repeats):
        for f in forms:
            for o in range(n_orders):
                key = f"{f}|o{o}|r{r}"
                jobs.append((key, requests[(f, o)]))  # one request object per cell: its cost is estimated once
                cells.append({"key": key, "formulation": f, "order": o, "repeat": r})
    return Plan(formulations=forms, orders=orders, n_orders=n_orders, repeats=exp.repeats, jobs=jobs, cells=cells)


class ExperimentRunner:
    def __init__(self, runner: Runner, store: Store, budget: Budget):
        self.runner, self.store, self.budget = runner, store, budget

    def batch(self, exp: ExperimentIn, engines: list[BaseEngine]) -> tuple[Plan, Batch]:
        check_size(exp, len(engines))  # before the grid is built: a large state times 2 000 calls is seconds
        p = plan(exp)
        return p, Batch(self.runner, self.budget, engines, p.jobs, exp.options)

    def estimate(self, exp: ExperimentIn, engines: list[BaseEngine]) -> dict[str, Any]:
        p, b = self.batch(exp, engines)
        return {**b.estimate(), "grid": {"formulations": list(p.formulations), "orders": p.n_orders,
                                         "repeats": p.repeats, "variants": len(p.jobs)}}

    async def execute(self, exp: ExperimentIn, engines: list[BaseEngine], emit: Emit) -> dict | None:
        # off the loop: the grid and its estimate re-read the state per distinct cell (review P5)
        p, batch = await asyncio.to_thread(self.batch, exp, engines)
        est = await asyncio.to_thread(batch.estimate)
        if not batch.reserve(est["total_usd"]):
            total = est["total_usd"]
            await emit("budget_stop", {"estimate_usd": total if math.isfinite(total) else None, **self.budget.status()})
            return None
        saved = False

        async def progress(payload: dict[str, Any]) -> None:
            await emit("progress", payload)

        run_id, t0 = new_id(), time.time()
        try:
            await emit("run_started", {"run_id": run_id, "engines": [e.spec.id for e in engines], "calls": batch.total,
                                       "estimate_usd": est["total_usd"], "per_engine": est["per_engine"],
                                       "grid": {"formulations": list(p.formulations), "orders": p.n_orders,
                                                "repeats": p.repeats}})
            await batch.run(progress)
            req = exp.request
            record = {
                "id": run_id,
                "created_at": datetime.fromtimestamp(t0, UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "day": local_day(),  # the day the money is counted on: the end (review P4)
                "kind": "experiment",
                "title": exp.title,
                "tags": exp.tags,
                "request": req.model_dump(mode="json", exclude_none=True),
                "state_sha256": hashlib.sha256(req.state_text().encode()).hexdigest(),
                "experiment": {
                    "base": exp.base_name,
                    "formulations": {f: {k: q.model_dump(mode="json", exclude_none=True) for k, q in qs.items()}
                                     for f, qs in p.formulations.items()},
                    "orders": p.orders, "n_orders": p.n_orders, "repeats": p.repeats,
                    "k": exp.orders.k, "seed": exp.orders.seed, "orders_enabled": exp.orders.enabled,
                },
                "options": exp.options.model_dump(),
                "engines": [{"engine_id": e.spec.id, "kind": e.kind, "model": e.spec.model or e.spec.repo,
                             "zdr": e.spec.zdr} for e in engines],
                "calls": [{**c, "results": {eid: compact(per[c["key"]]) for eid, per in batch.results.items()
                                            if c["key"] in per}} for c in p.cells],
                "estimate_usd": est["total_usd"],
                "cost_usd": batch.spent(),
                "budget_stopped": batch.stopped,
                "versions": versions(batch.results),
            }
            self.store.save(record)
            saved = True
        except Exception as e:
            log.exception("experiment failed")
            await emit("run_error", {"message": f"{type(e).__name__}: {e}"[:500]})
            return None
        finally:
            batch.close(saved)
        summary, report_error = [], None
        try:  # off the loop, as the dataset report
            summary = summarize(await asyncio.to_thread(build_stability, record))
        except Exception as e:  # the record is saved; GET /api/reports/{id} rebuilds the report from it
            log.exception("report of experiment %s", run_id)
            report_error = f"{type(e).__name__}: {e}"[:300]
        await emit("budget_stop" if batch.stopped else "run_done",
                   {"run_id": run_id, "cost_usd": record["cost_usd"], "day": record["day"], "summary": summary,
                    "report_error": report_error, "budget": self.budget.status()})
        return record
