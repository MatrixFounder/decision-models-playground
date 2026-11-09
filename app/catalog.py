"""OpenRouter's public lists (docs/DESIGN.md §13.2), cached in probes/catalog.json.

- GET /api/v1/models?output_modalities=decisions: price, context, inputs, expiry, `canonical_slug` (dated snapshot).
- GET /api/v1/endpoints/zdr: ZDR endpoints; `model_id` is undated, the dated slug is only in `name` (SPIKE §3.6).
- GET /api/v1/models: LLMs, kept for prices and `supported_parameters` of the LLM baseline (§7.5).

No key is sent: both lists are public. P1 uses prices and ZDR flags; the capability matrix (P3) builds on it. The root
is OPENROUTER_BASE_URL (.env), https://openrouter.ai by default.
"""

from __future__ import annotations

import json
import math
import time
from pathlib import Path
from typing import Any

import httpx

from app.settings import OPENROUTER_BASE

UA = {"User-Agent": "decision-models-playground/0.1"}
STALE_S = 24 * 3600


class Catalog:
    def __init__(self, path: Path, transport: httpx.AsyncBaseTransport | None = None, base: str = OPENROUTER_BASE):
        self.path = path
        self.transport = transport
        self.base = base
        self.data: dict[str, Any] = {}
        self.error: str | None = None
        self._load()

    def _load(self) -> None:
        try:
            self.data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.data = {}

    @property
    def fetched_at(self) -> float | None:
        return self.data.get("fetched_at")

    @property
    def stale(self) -> bool:
        return not self.fetched_at or time.time() - self.fetched_at > STALE_S

    async def refresh(self) -> dict[str, Any]:
        async with httpx.AsyncClient(headers=UA, timeout=20, transport=self.transport) as client:
            dec = await client.get(f"{self.base}/api/v1/models", params={"output_modalities": "decisions"})
            dec.raise_for_status()
            zdr = await client.get(f"{self.base}/api/v1/endpoints/zdr")
            zdr.raise_for_status()
            llm = await client.get(f"{self.base}/api/v1/models")
            llm.raise_for_status()
        decision_ids = {m["id"] for m in dec.json()["data"]}
        llm_ids = {m["id"] for m in llm.json()["data"]}
        self.data = {
            "fetched_at": time.time(),
            "decisions": dec.json()["data"],
            "llms": [
                {k: m.get(k) for k in ("id", "context_length", "pricing", "supported_parameters", "expiration_date")}
                for m in llm.json()["data"]
            ],
            # the ZDR list is large (1,111 endpoints on 2026-11-09): keep the models this playground can call
            "zdr": [e for e in zdr.json()["data"] if e.get("model_id") in decision_ids | llm_ids],
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)
        self.error = None
        return self.summary()

    async def refresh_quietly(self) -> None:
        try:
            await self.refresh()
        except Exception as e:  # an odd catalog shape must not leave an unretrieved task exception at start-up
            self.error = f"{type(e).__name__}: {e}"[:300]

    def model(self, model_id: str | None) -> dict[str, Any] | None:
        if not model_id:
            return None
        for m in self.data.get("decisions", []) + self.data.get("llms", []):
            if m.get("id") == model_id or m.get("canonical_slug") == model_id:
                return m
        return None

    def price_per_token(self, model_id: str | None) -> float | None:
        """The prompt price; None for a missing, non-numeric, non-finite or negative one (a "nan" price would make
        every reservation NaN and every SSE frame unserializable, review P3)."""
        m = self.model(model_id)
        try:
            price = float(m["pricing"]["prompt"]) if m else None
        except (KeyError, TypeError, ValueError):
            return None
        return price if price is not None and math.isfinite(price) and price >= 0 else None

    def max_decision_price(self) -> float | None:
        prices = [self.price_per_token(m["id"]) for m in self.data.get("decisions", [])]
        prices = [p for p in prices if p is not None]
        return max(prices) if prices else None

    def zdr_endpoint(self, model_id: str | None) -> bool | None:
        """True or False by OpenRouter's ZDR list; None for an alias (`~…`) or an empty cache."""
        if not model_id or model_id.startswith("~") or not self.data.get("zdr"):
            return None
        return any(e.get("model_id") == model_id for e in self.data["zdr"])

    def summary(self) -> dict[str, Any]:
        return {
            "fetched_at": self.fetched_at,
            "decisions": len(self.data.get("decisions", [])),
            "llms": len(self.data.get("llms", [])),
            "zdr": len(self.data.get("zdr", [])),
            "error": self.error,
        }
