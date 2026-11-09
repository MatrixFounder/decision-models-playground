"""The daily limit on paid calls (docs/DESIGN.md §19).

Spent today = the cost of today's saved runs (runs/index.jsonl) plus what running runs have reserved. A run whose
estimate exceeds what is left does not start. The reservation is released when the run is saved with its real cost.
"""

from __future__ import annotations

import threading

from app.store import Store, local_day


class Budget:
    def __init__(self, limit_usd: float, store: Store):
        self.limit = limit_usd
        self.store = store
        self.reserved = 0.0
        self.unsaved: dict[str, float] = {}  # day → cost of runs whose record could not be written
        self._lock = threading.Lock()

    def spent(self) -> float:
        day = local_day()
        return self.store.spent_on(day) + self.unsaved.get(day, 0.0)

    def charge_unsaved(self, amount: float) -> None:
        """Money spent by a run that could not be saved (disk full, permissions) still counts against the limit."""
        with self._lock:
            day = local_day()
            self.unsaved[day] = self.unsaved.get(day, 0.0) + max(0.0, amount)

    def status(self) -> dict:
        spent = self.spent()
        return {
            "day": local_day(),
            "spent_usd": round(spent, 8),
            "reserved_usd": round(self.reserved, 8),
            "limit_usd": self.limit,
            "remaining_usd": round(max(0.0, self.limit - spent - self.reserved), 8),
        }

    def reserve(self, amount: float) -> bool:
        with self._lock:
            if self.spent() + self.reserved + amount > self.limit:
                return False
            self.reserved += amount
            return True

    def release(self, amount: float) -> None:
        with self._lock:
            self.reserved = max(0.0, self.reserved - amount)
