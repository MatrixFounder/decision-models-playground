"""Runs on disk (docs/DESIGN.md §10.1): runs/<day>/<id>.json plus one index line per run in runs/index.jsonl.

Records never hold a key; `raw` is cut to 64 KB by the engine; media are referenced by path and sha256.
"""

from __future__ import annotations

import json
import os
import secrets
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
ID_RE_CHARS = set(CROCKFORD)


def new_id() -> str:
    """A ULID: 48-bit milliseconds then 80 random bits, Crockford base32, sortable by time."""
    value = (int(time.time() * 1000) << 80) | secrets.randbits(80)
    return "".join(CROCKFORD[(value >> (5 * i)) & 31] for i in reversed(range(26)))


def local_day(ts: float | None = None) -> str:
    """The budget and folder day: the Mac's local date (DESIGN §10.1, §19)."""
    return datetime.fromtimestamp(ts or time.time()).strftime("%Y-%m-%d")


def valid_id(run_id: str) -> bool:
    return len(run_id) == 26 and set(run_id) <= ID_RE_CHARS


def index_row(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": record["id"],
        "created_at": record["created_at"],
        "day": record["day"],
        "kind": record["kind"],
        "title": record.get("title"),
        "tags": record.get("tags", []),
        "engines": [e["engine_id"] for e in record.get("engines", [])],
        "questions": list((record.get("request") or {}).get("questions", {})),
        "cost_usd": record.get("cost_usd") or 0.0,
        "status": {r["engine_id"]: r["status"] for r in record.get("results", [])},
    }


class Store:
    def __init__(self, runs_dir: Path):
        self.dir = runs_dir
        self.index = runs_dir / "index.jsonl"
        self._lock = threading.Lock()
        self._cache: tuple[tuple[int, int], list[dict[str, Any]]] | None = None

    def save(self, record: dict[str, Any]) -> Path:
        """The record first (fsynced, atomic rename), then its index line (fsynced). A crash between the two leaves
        a record without a line, which `reconcile()` picks up at the next start."""
        day = record["day"]
        path = self.dir / day / f"{record['id']}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, indent=1))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        dir_fd = os.open(path.parent, os.O_RDONLY)  # the rename itself survives a power loss
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
        self._append(index_row(record))
        return path

    def _append(self, row: dict[str, Any]) -> None:
        line = json.dumps(row, ensure_ascii=False) + "\n"
        with self._lock:
            self.dir.mkdir(parents=True, exist_ok=True)
            with self.index.open("a+b") as f:
                f.seek(0, os.SEEK_END)
                if f.tell():
                    f.seek(-1, os.SEEK_END)
                    if f.read(1) != b"\n":  # a line torn by a crash: never glue the next row onto it
                        line = "\n" + line
                f.write(line.encode("utf-8"))
                f.flush()
                os.fsync(f.fileno())
            self._cache = None

    def rows(self) -> list[dict[str, Any]]:
        """Index rows, cached until the file changes (the budget asks on every estimate)."""
        try:
            st = self.index.stat()
        except FileNotFoundError:
            return []
        key = (st.st_mtime_ns, st.st_size)
        with self._lock:
            if self._cache is not None and self._cache[0] == key:
                return self._cache[1]
        out = []
        for line in self.index.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue  # a torn line after a crash
            if isinstance(row, dict) and isinstance(row.get("id"), str):
                out.append(row)
        with self._lock:
            self._cache = (key, out)
        return out

    def reconcile(self) -> int:
        """Index the records that have no line (a crash between the record and its line). Returns how many."""
        known = {r["id"] for r in self.rows()}
        added = 0
        for path in sorted(self.dir.glob("*/*.json")):
            if path.stem in known or not valid_id(path.stem):
                continue
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
                self._append(index_row(record))
                added += 1
            except (OSError, ValueError, KeyError, TypeError):
                continue
        return added

    def list(self, limit: int = 100, tag: str | None = None, engine: str | None = None,
             kind: str | None = None) -> list[dict[str, Any]]:
        rows = self.rows()
        if tag:
            rows = [r for r in rows if tag in r.get("tags", [])]
        if engine:
            rows = [r for r in rows if engine in r.get("engines", [])]
        if kind:
            rows = [r for r in rows if r.get("kind") == kind]
        return list(reversed(rows))[:limit]

    def get(self, run_id: str) -> dict[str, Any] | None:
        if not valid_id(run_id):
            return None
        for row in reversed(self.rows()):
            if row["id"] == run_id and isinstance(row.get("day"), str) and "/" not in row["day"]:
                path = self.dir / row["day"] / f"{run_id}.json"
                return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
        return None

    def spent_on(self, day: str) -> float:
        return sum(float(r.get("cost_usd") or 0) for r in self.rows() if r.get("day") == day)
