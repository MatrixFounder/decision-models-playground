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


class Store:
    def __init__(self, runs_dir: Path):
        self.dir = runs_dir
        self.index = runs_dir / "index.jsonl"
        self._lock = threading.Lock()

    def save(self, record: dict[str, Any]) -> Path:
        day = record["day"]
        path = self.dir / day / f"{record['id']}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, path)
        row = {
            "id": record["id"],
            "created_at": record["created_at"],
            "day": day,
            "kind": record["kind"],
            "title": record.get("title"),
            "tags": record.get("tags", []),
            "engines": [e["engine_id"] for e in record.get("engines", [])],
            "questions": list((record.get("request") or {}).get("questions", {})),
            "cost_usd": record.get("cost_usd") or 0.0,
            "status": {r["engine_id"]: r["status"] for r in record.get("results", [])},
        }
        with self._lock, self.index.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        return path

    def rows(self) -> list[dict[str, Any]]:
        if not self.index.is_file():
            return []
        out = []
        for line in self.index.read_text(encoding="utf-8").splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                continue  # a torn last line after a crash
        return out

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
            if row["id"] == run_id:
                path = self.dir / row["day"] / f"{run_id}.json"
                return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
        return None

    def spent_on(self, day: str) -> float:
        return sum(float(r.get("cost_usd") or 0) for r in self.rows() if r.get("day") == day)
