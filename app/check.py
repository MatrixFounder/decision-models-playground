"""python -m app.check: every engine's availability, the catalog cache and today's budget (docs/ARCHITECTURE.md §3.5.5)."""

from __future__ import annotations

import asyncio
import sys

from app.main import build_state
from app.settings import get_settings


def main() -> int:
    state = build_state(get_settings())
    catalog = state["catalog"]
    if catalog.stale:
        asyncio.run(catalog.refresh_quietly())
    print(f"config: {state['registry'].config_source}")
    s = catalog.summary()
    print(f"catalog: {s['decisions']} decision models, {s['zdr']} ZDR endpoints kept"
          + (f", error: {s['error']}" if s["error"] else ""))
    width = max(len(e["id"]) for e in state["registry"].describe()) if state["registry"].engines else 10
    for e in state["registry"].describe():
        mark = "ok " if e["available"] else "-- "
        extra = ""
        if e.get("paid"):
            zdr = {True: "ZDR endpoint", False: "no ZDR endpoint", None: "ZDR unknown"}[e.get("zdr_endpoint")]
            extra = f"  ${e.get('price_per_mtok')}/M in, {zdr}"
        print(f"  {mark}{e['id']:<{width}}  {e['kind']:<22} {e['model'] or '':<32} {e['reason']}{extra}")
    b = state["budget"].status()
    print(f"budget {b['day']}: spent ${b['spent_usd']:.6f} of ${b['limit_usd']:.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
