"""§10.1 run storage."""

from __future__ import annotations

import json
import time

import pytest

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


def record(day="2026-11-09", cost=0.1):
    return {"id": new_id(), "created_at": f"{day}T12:00:00Z", "day": day, "kind": "compare", "tags": [],
            "request": {"questions": {"q": {}}}, "engines": [], "results": [], "cost_usd": cost}


def test_a_torn_line_does_not_swallow_the_next_run(tmp_path):
    store = Store(tmp_path / "runs")
    store.save(record())
    with store.index.open("a") as f:
        f.write('{"id": "torn')  # a crash mid-append, no newline
    nxt = record(cost=0.25)
    store.save(nxt)
    assert nxt["id"] in [r["id"] for r in store.list()]
    assert store.get(nxt["id"]) is not None
    assert store.spent_on("2026-11-09") == pytest.approx(0.35)


def test_reconcile_indexes_records_without_a_line(tmp_path):
    store = Store(tmp_path / "runs")
    rec = record(cost=0.5)
    path = tmp_path / "runs" / rec["day"] / f"{rec['id']}.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(rec))  # written, then the process died before the index line
    assert store.get(rec["id"]) is None
    assert store.reconcile() == 1 and store.reconcile() == 0
    assert store.get(rec["id"])["cost_usd"] == 0.5 and store.spent_on(rec["day"]) == 0.5


@pytest.mark.parametrize("bad", ["../../etc/passwd", "..", "01JC000000000000000000000/", "", "x" * 26])
def test_get_refuses_anything_but_an_indexed_id(tmp_path, bad, monkeypatch):
    store = Store(tmp_path / "runs")
    store.save(record())
    monkeypatch.setattr(store, "rows", lambda: pytest.fail("a non-ULID reached the index"))
    assert store.get(bad) is None


def test_the_budget_day_is_the_local_day(monkeypatch):
    from app.store import local_day

    monkeypatch.setenv("TZ", "Europe/Moscow")
    time.tzset()
    try:
        # 2026-11-09 22:30 UTC is already 2026-11-10 in Moscow (UTC+3)
        assert local_day(1794263400) == "2026-11-10"
    finally:
        monkeypatch.delenv("TZ")
        time.tzset()


def test_rows_cache_follows_the_file(tmp_path, monkeypatch):
    from pathlib import Path

    store = Store(tmp_path / "runs")
    store.save(record())
    reads = []
    real = Path.read_text

    def counted(self, *a, **k):
        if self == store.index:
            reads.append(1)
        return real(self, *a, **k)

    monkeypatch.setattr(Path, "read_text", counted)
    assert len(store.rows()) == 1 and len(store.rows()) == 1 and len(reads) == 1  # read once, then cached
    store.save(record())
    assert len(store.rows()) == 2 and len(reads) == 2  # the file changed: read again


def test_the_record_its_directory_and_the_index_are_flushed_to_disk(tmp_path, monkeypatch):
    """Round 3: on macOS fsync(2) leaves the drive cache; full_fsync uses F_FULLFSYNC. The directory is synced
    after the rename, before the index line."""
    import os
    import stat

    import app.store as store_module

    synced = []
    real = store_module.full_fsync

    def spy(fd):
        mode = os.fstat(fd).st_mode
        synced.append("dir" if stat.S_ISDIR(mode) else "file")
        return real(fd)

    monkeypatch.setattr(store_module, "full_fsync", spy)
    rec = record()
    store_module.Store(tmp_path / "runs").save(rec)
    assert synced == ["file", "dir", "file"]  # the record, its directory after os.replace, the index line
