"""A labelled dataset run over several engines (docs/DESIGN.md §12, UC-3).

The calls, their lanes and the day's budget are app.batch's (shared with experiments): the estimate is reserved
first, local engines take turns, cloud lanes run beside them, a low estimate overshoots the day by at most one call
per paid engine, and when the day refuses the paid items left are `budget` — the run then ends with `budget_stop`
after it is saved. The record (kind `dataset`) keeps item ids, labels and compact answers — never a call's text,
which stays in the dataset — and the report is written to reports/<dataset>-<day>-<run id>.md and .json.

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

from app.batch import Batch, compact, versions
from app.budget import Budget
from app.datasets import MAX_ITEMS, Dataset, DatasetItem, DatasetStore
from app.engines.base import BaseEngine
from app.reports import build_report, render_markdown
from app.runner import Emit, Runner
from app.schemas import DecisionRequest, RunOptions
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


class DatasetRunner:
    def __init__(self, runner: Runner, datasets: DatasetStore, store: Store, budget: Budget, reports_dir: Path):
        self.runner, self.datasets, self.store, self.budget, self.reports_dir = runner, datasets, store, budget, reports_dir

    @staticmethod
    def request(ds: Dataset, item: DatasetItem) -> DecisionRequest:
        return DecisionRequest(state=item.state, questions=ds.questions)

    def items(self, ds: Dataset, run_in: DatasetRunIn) -> list[DatasetItem]:
        return ds.items[: run_in.limit] if run_in.limit else list(ds.items)

    def batch(self, ds: Dataset, engines: list[BaseEngine], run_in: DatasetRunIn) -> Batch:
        jobs = [(i.id, self.request(ds, i)) for i in self.items(ds, run_in)]
        return Batch(self.runner, self.budget, engines, jobs, run_in.options)

    def estimate(self, ds: Dataset, engines: list[BaseEngine], run_in: DatasetRunIn) -> dict[str, Any]:
        b = self.batch(ds, engines, run_in)
        return {**b.estimate(), "items": len(b.jobs)}

    async def execute(self, ds: Dataset, engines: list[BaseEngine], run_in: DatasetRunIn, emit: Emit) -> dict | None:
        items = self.items(ds, run_in)
        batch = await asyncio.to_thread(self.batch, ds, engines, run_in)  # off the loop: 5 000 items
        est = await asyncio.to_thread(batch.estimate)
        if not batch.reserve(est["total_usd"]):
            total = est["total_usd"]
            await emit("budget_stop", {"estimate_usd": total if math.isfinite(total) else None,
                                       **self.budget.status()})
            return None
        saved = False

        async def progress(payload: dict[str, Any]) -> None:
            await emit("progress", payload)

        run_id, t0 = new_id(), time.time()
        try:
            await emit("run_started", {"run_id": run_id, "dataset": ds.spec.name, "engines": [e.spec.id for e in engines],
                                       "items": len(items), "calls": batch.total, "estimate_usd": est["total_usd"],
                                       "per_engine": est["per_engine"]})
            await batch.run(progress)
            results = batch.results
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
                "cost_usd": batch.spent(),
                "budget_stopped": batch.stopped,
                "versions": versions(results),
            }
            self.store.save(record)
            saved = True
        except Exception as e:
            log.exception("dataset run failed")
            await emit("run_error", {"message": f"{type(e).__name__}: {e}"[:500]})
            return None
        finally:
            batch.close(saved)
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
        await emit("budget_stop" if batch.stopped else "run_done", payload)
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
