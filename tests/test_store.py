"""§10.1 run storage."""

from __future__ import annotations

import time

from app.store import Store, new_id, valid_id


def test_ids_are_ulids_sorted_by_time():
    a = new_id()
    time.sleep(0.002)
    b = new_id()
    assert valid_id(a) and valid_id(b) and a < b and len({new_id() for _ in range(1000)}) == 1000


def test_save_list_get_and_torn_index_line(tmp_path):
    store = Store(tmp_path / "runs")
    rec = {"id": new_id(), "created_at": "2026-11-09T12:00:00Z", "day": "2026-11-09", "kind": "compare",
           "tags": ["a"], "request": {"questions": {"q": {}}}, "engines": [{"engine_id": "e1"}],
           "results": [{"engine_id": "e1", "status": "ok"}], "cost_usd": 0.1}
    store.save(rec)
    with store.index.open("a") as f:
        f.write('{"id": "broken')  # a crash mid-write
    assert store.get(rec["id"])["tags"] == ["a"]
    rows = store.list()
    assert len(rows) == 1 and rows[0]["questions"] == ["q"] and rows[0]["engines"] == ["e1"]
    assert store.list(engine="e2") == [] and store.list(tag="a")
    assert (tmp_path / "runs" / "2026-11-09" / f"{rec['id']}.json").is_file()
    assert store.get("not-an-id") is None
