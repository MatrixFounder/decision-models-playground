"""A labelled dataset run over several engines (docs/DESIGN.md §12, UC-3).

1. The estimate of every item on every paid engine is reserved first; over what is left → `budget_stop`, no call.
2. Each engine works through the items in its own lane: a local model one item at a time (the manager's queue is
   short), a cloud model `concurrency` at once. Cloud engines run side by side; local engines take turns, a whole
   engine each, since one model fits in memory and alternating items would reload it every call.
3. Every call goes through Runner.call: the engine's profile filter, the media check, the crash guard.
4. Should the actual cost outgrow the reservation, more is reserved per call, at the dearer of the estimate and
   the engine's dearest call so far; each lane's first call goes alone, so a low estimate overshoots the day by at
   most one call per paid engine. When the day refuses, the paid items left are marked `budget` and not asked —
   free (local) items go on — and the run ends with `budget_stop` after it is saved.
5. The record (kind `dataset`) keeps item ids, labels and compact answers — never a call's text, which stays in
   the dataset — and the report is written to reports/<dataset>-<day>-<run id>.md and .json.

Every run ends with exactly one terminal event: `run_done`, `budget_stop` or `run_error`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app import __version__
from app.budget import Budget
from app.datasets import MAX_ITEMS, Dataset, DatasetItem, DatasetStore
from app.engines.base import BaseEngine
from app.reports import build_report, render_markdown
from app.runner import Emit, Runner, mlx_vlm_version
from app.schemas import DecisionRequest, EngineResult, RunOptions
from app.store import Store, local_day, new_id, write_durably

log = logging.getLogger("playground.datasets")
DATASET_TERMINAL = ("run_done", "budget_stop", "run_error")


class DatasetRunIn(BaseModel):
    """POST /api/datasets/{name}/run and /estimate."""

    model_config = ConfigDict(extra="forbid")

    engines: list[str] = Field(min_length=1)
    limit: int | None = Field(None, ge=1, le=MAX_ITEMS)  # the first N items
    options: RunOptions = Field(default_factory=RunOptions)
    title: str | None = None
    tags: list[str] = Field(default_factory=list)


def compact(res: EngineResult) -> dict[str, Any]:
    """What a dataset run keeps of one answer: no raw body, no text."""
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


def lane_width(engine: BaseEngine) -> int:
    return 1 if engine.kind == "mlx_local" else max(1, engine.spec.concurrency)


class DatasetRunner:
    def __init__(self, runner: Runner, datasets: DatasetStore, store: Store, budget: Budget, reports_dir: Path):
        self.runner, self.datasets, self.store, self.budget, self.reports_dir = runner, datasets, store, budget, reports_dir

    @staticmethod
    def request(ds: Dataset, item: DatasetItem) -> DecisionRequest:
        return DecisionRequest(state=item.state, questions=ds.questions)

    def sent_keys(self, engine: BaseEngine, ds: Dataset) -> list[str]:
        """The questions the engine's profile lets through: the same for every item of a dataset."""
        return self.runner.split(engine, DecisionRequest(state="x", questions=ds.questions))[0]

    def items(self, ds: Dataset, run_in: DatasetRunIn) -> list[DatasetItem]:
        return ds.items[: run_in.limit] if run_in.limit else list(ds.items)

    def estimate(self, ds: Dataset, engines: list[BaseEngine], items: list[DatasetItem]) -> dict[str, Any]:
        per_engine: dict[str, float] = {}
        for e in engines:
            keys = self.sent_keys(e, ds)
            per_engine[e.spec.id] = sum(e.estimate_cost(self.request(ds, i).subset(keys)) or 0.0 for i in items) if keys else 0.0
        return {"per_engine": per_engine, "total_usd": sum(per_engine.values()), "calls": len(items) * len(engines),
                "items": len(items), "budget": self.budget.status()}

    async def execute(self, ds: Dataset, engines: list[BaseEngine], run_in: DatasetRunIn, emit: Emit) -> dict | None:
        items = self.items(ds, run_in)
        est = self.estimate(ds, engines, items)
        reserved = est["total_usd"]
        # a NaN estimate (a config typo) compares false with everything: it must stop the run, not skip the check
        if not math.isfinite(reserved) or reserved > 0 and not self.budget.reserve(reserved):
            await emit("budget_stop", {"estimate_usd": reserved if math.isfinite(reserved) else None,
                                       **self.budget.status()})
            return None
        results: dict[str, dict[str, EngineResult]] = {e.spec.id: {} for e in engines}
        sent = {e.spec.id: self.sent_keys(e, ds) for e in engines}  # what each engine is asked (its profile)
        committed = 0.0  # actual costs so far plus what the calls in flight may cost
        seen: dict[str, float] = {}  # per engine: the dearest call so far — the next one may cost as much
        stopped = False  # the day's budget refused a paid call: no paid call after it (free ones go on)
        saved = False
        done = 0
        total = len(items) * len(engines)

        def spent() -> float:
            return sum(r.usage.cost_usd or 0.0 for per in results.values() for r in per.values())

        def top_up(more: float) -> bool:
            nonlocal reserved
            if self.budget.reserve(more):
                reserved += more
                return True
            return False

        async def one(e: BaseEngine, item: DatasetItem) -> None:
            """One item on one engine. Never raises: whatever fails here is that item's failure, so no lane stops
            and leaves the others calling after the run ended (review P4)."""
            nonlocal committed, stopped, done
            try:
                req = self.request(ds, item)
                keys = sent[e.spec.id]
                need = (e.estimate_cost(req.subset(keys)) or 0.0) if keys else 0.0
                if not math.isfinite(need) or need < 0:
                    raise ValueError(f"estimate {need!r}")
                hold = max(need, seen.get(e.spec.id, 0.0))
                if hold > 0 and (stopped or committed + hold > reserved and not top_up(committed + hold - reserved)):
                    stopped = True
                    res = EngineResult.build(e.spec.id, list(ds.questions), {}, {}, request_failure="budget",
                                             detail="the day's budget ran out during the run")
                else:
                    committed += hold  # a cancel leaves it here: a call in flight may be billed
                    res = await self.runner.call(e, req, run_in.options)
                    cost = res.usage.cost_usd or 0.0
                    committed += cost - hold
                    seen[e.spec.id] = max(seen.get(e.spec.id, 0.0), cost)
            except Exception as exc:  # the engine itself is guarded by Runner.call: this is the estimate, a bug
                log.exception("dataset run: item %s on %s", item.id, e.spec.id)
                res = EngineResult.build(e.spec.id, list(ds.questions), {}, {}, request_failure="provider_error",
                                         detail=f"run error: {type(exc).__name__}")
            results[e.spec.id][item.id] = res
            done += 1
            await emit("progress", {"done": done, "total": total, "engine_id": e.spec.id, "item_id": item.id,
                                    "status": res.status, "request_failure": res.request_failure,
                                    "cost_usd": res.usage.cost_usd})

        async def lane(e: BaseEngine) -> None:
            queue = iter(items)  # shared by the lane's workers: each item is taken once
            first = next(queue, None)
            if first is None:
                return
            # the first call alone: its real cost is known before the lane's calls run side by side, so a low
            # estimate overshoots the day by one call per engine, not by the lane's width (review P4)
            await one(e, first)

            async def worker() -> None:
                for item in queue:
                    await one(e, item)

            async with asyncio.TaskGroup() as tg:
                for _ in range(lane_width(e)):
                    tg.create_task(worker())

        async def local_turns() -> None:
            # one model in memory: a whole local engine at a time. Lanes side by side would swap the models on every
            # item (live UC-3: 170 of 200 s were loads)
            for e in [e for e in engines if e.kind == "mlx_local"]:
                await lane(e)

        run_id, t0 = new_id(), time.time()
        try:
            await emit("run_started", {"run_id": run_id, "dataset": ds.spec.name, "engines": [e.spec.id for e in engines],
                                       "items": len(items), "calls": total, "estimate_usd": est["total_usd"],
                                       "per_engine": est["per_engine"]})
            async with asyncio.TaskGroup() as tg:  # a failure cancels the other lanes: no call after the end
                tg.create_task(local_turns())
                for e in engines:
                    if e.kind != "mlx_local":
                        tg.create_task(lane(e))
            cost = spent()
            record = {
                "id": run_id,
                "created_at": datetime.fromtimestamp(t0, UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                # the day the money is counted on: the end, not the start — a run past midnight counts against the
                # new day's limit, which has to cover it (review P4)
                "day": local_day(),
                "kind": "dataset",
                "title": run_in.title or ds.spec.title or ds.spec.name,
                "tags": run_in.tags,
                "dataset": {"name": ds.spec.name, "title": ds.spec.title, "items": len(items),
                            "total_items": len(ds.items), "limit": run_in.limit, "label_source": ds.spec.label_source,
                            "source": ds.spec.source, "question_set": ds.spec.question_set,
                            "items_sha256": ds.items_sha256},
                "request": {"questions": {k: q.model_dump(mode="json", exclude_none=True)
                                          for k, q in ds.questions.items()}},
                "options": run_in.options.model_dump(),
                "engines": [{"engine_id": e.spec.id, "kind": e.kind, "model": e.spec.model or e.spec.repo,
                             "zdr": e.spec.zdr} for e in engines],
                "items": [{"id": i.id, "labels": i.labels,
                           "results": {eid: compact(per[i.id]) for eid, per in results.items() if i.id in per}}
                          for i in items],
                "estimate_usd": est["total_usd"],
                "cost_usd": cost,
                "budget_stopped": stopped,
                "versions": {"app": __version__, "mlx_vlm": mlx_vlm_version(),
                             "models": {eid: next((r.snapshot for r in reversed(per.values()) if r.snapshot), None)
                                        for eid, per in results.items()}},
            }
            self.store.save(record)
            saved = True
        except Exception as e:
            log.exception("dataset run failed")
            await emit("run_error", {"message": f"{type(e).__name__}: {e}"[:500]})
            return None
        finally:
            if not saved:  # an exception or a cancel: finished calls and the calls in flight may all be billed
                self.budget.charge_unsaved(max(spent(), committed))
            self.budget.release(reserved)
        files, summary, report_error = None, [], None
        try:  # off the loop: a 5 000-item report takes seconds, and the compat server shares this loop
            report = await asyncio.to_thread(build_report, record)
            summary = summarize(report)
            files = await asyncio.to_thread(self.write_report, record, report)
        except Exception as e:  # the record is saved; the report can be rebuilt from it (GET /api/reports/{id})
            log.exception("report of %s not written", run_id)
            report_error = f"{type(e).__name__}: {e}"[:300]
        payload = {"run_id": run_id, "cost_usd": record["cost_usd"], "day": record["day"], "summary": summary,
                   "report_files": files, "report_error": report_error, "budget": self.budget.status()}
        await emit("budget_stop" if stopped else "run_done", payload)
        return record

    def write_report(self, record: dict[str, Any], report: dict[str, Any] | None = None) -> dict[str, str]:
        report = report if report is not None else build_report(record)
        stem = f"{record['dataset']['name']}-{record['day']}-{record['id']}"
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        write_durably(self.reports_dir / f"{stem}.json", json.dumps(report, ensure_ascii=False, indent=1))
        write_durably(self.reports_dir / f"{stem}.md", render_markdown(report))
        return {"json": f"reports/{stem}.json", "md": f"reports/{stem}.md"}


def summarize(report: dict[str, Any]) -> list[dict[str, Any]]:
    """The headline per engine and question, for the run's last event."""
    out = []
    for g in report["groups"]:
        m = g["metrics"]
        out.append({"question": g["question"], "engine_id": g["engine_id"], "snapshot": g["snapshot"],
                     "answered": m["answered"], "items": m["items"], "brier": m["brier"],
                     "whip": m.get("whip"), "selective": m.get("selective"), "accuracy": m.get("accuracy")})
    return out
