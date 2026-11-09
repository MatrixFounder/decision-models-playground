"""§19: the estimate covers every billed P0 call; the daily limit stops a run before any call."""

from __future__ import annotations

import glob
import json
from pathlib import Path

import pytest

from app.budget import Budget
from app.catalog import Catalog
from app.registry import Registry
from app.schemas import DecisionRequest
from app.store import Store
from tests.conftest import FIX

ENGINE_BY_DIR = {"typesafe__jev-1.14": "or-jev-1.14", "liquid__d1.1": "or-d1.1", "alias-liquid__d1-latest": "or-d1-latest"}


def billed_p0_calls():
    out = []
    for p in sorted(glob.glob(str(FIX / "openrouter" / "*" / "*.json"))):
        rec = json.loads(Path(p).read_text())
        d = Path(p).parent.name
        if d in ENGINE_BY_DIR and rec.get("status") == 200:
            qs = {k: {f: v for f, v in q.items() if f != "multiple"} for k, q in rec["request"]["questions"].items()
                  if q["type"] != "box"}
            if qs:
                out.append(pytest.param(ENGINE_BY_DIR[d], rec["request"]["state"], qs, rec["body"]["usage"]["cost"],
                                        id=f"{d}/{Path(p).stem}"))
    return out


@pytest.mark.parametrize("engine_id,state,questions,cost", billed_p0_calls())
def test_estimate_is_not_below_the_billed_cost(settings, engine_id, state, questions, cost):
    reg = Registry(settings, Catalog(settings.probes_dir / "catalog.json"))
    est = reg.get(engine_id).estimate_cost(DecisionRequest(state=state, questions=questions))
    assert est >= cost
    assert est < cost * 6  # conservative, not absurd


def test_reserve_respects_the_limit(tmp_path):
    b = Budget(0.001, Store(tmp_path / "runs"))
    assert b.reserve(0.0006)
    assert not b.reserve(0.0006)
    b.release(0.0006)
    assert b.reserve(0.0006)
    assert b.status()["remaining_usd"] == pytest.approx(0.0004)


def test_spent_counts_saved_runs_of_today(tmp_path):
    from app.store import local_day, new_id

    store = Store(tmp_path / "runs")
    store.save({"id": new_id(), "created_at": "2026-11-09T00:00:00Z", "day": local_day(), "kind": "compare",
                "engines": [], "results": [], "cost_usd": 0.25})
    store.save({"id": new_id(), "created_at": "2026-11-08T00:00:00Z", "day": "2000-01-01", "kind": "compare",
                "engines": [], "results": [], "cost_usd": 5.0})
    b = Budget(1.0, store)
    assert b.spent() == pytest.approx(0.25)
    assert not b.reserve(0.8)
