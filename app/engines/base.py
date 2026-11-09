"""The engine protocol, BaseEngine and the answer-name table (docs/DESIGN.md §6.2, §6.3, §7.1, §7.7)."""

from __future__ import annotations

import asyncio
import json
import math
from typing import Any, ClassVar

import httpx

from app.schemas import (
    MAX_CHOICE_OPTIONS,
    MAX_SCORE_LEVELS,
    Answer,
    CapabilityProfile,
    DecisionRequest,
    EngineResult,
    EngineSpec,
    ExtractResult,
    Question,
    RunOptions,
)

RAW_LIMIT = 64 * 1024
SUM_TOLERANCE = 0.001

# §7.7: where each internal field lives in a provider's answer. Dotted paths read nested keys.
ANSWER_NAMES: dict[str, dict[str, str]] = {
    # System One: OpenRouter, TypeSafe, Ollama, llama.cpp, d1-2B/d1-Omni through mlx-vlm's system_one
    "system_one": {"noul": "noul", "choice": "choice", "probabilities": "probabilities",
                   "confidence": "confidence", "score": "score", "legend": "legend"},
    # mlx-vlm's shared decision API (`mlx_vlm server --decision-model`, /v1/decisions); unused in version 1
    "mlx_decisions": {"noul": "probability", "choice": "value", "probabilities": "probabilities",
                      "confidence": "metadata.confidence", "score": "value", "legend": "metadata.legend"},
}


class EngineFailure(Exception):
    """A request-level failure (§6.3): `cls` is the failure class, `detail` a short reason without keys."""

    def __init__(self, cls: str, detail: str, status: int | None = None):
        super().__init__(detail)
        self.cls, self.detail, self.status = cls, detail, status


def _get(obj: dict, path: str) -> Any:
    for part in path.split("."):
        if not isinstance(obj, dict):
            return None
        obj = obj.get(part)
    return obj


def _prob(x: Any) -> float | None:
    if isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x):
        return None
    if -1e-9 <= x <= 1 + 1e-9:
        return min(1.0, max(0.0, float(x)))
    return None


def _distribution(q: Question, raw: Any) -> dict[str, float] | None:
    """A choice or score distribution over exactly the question's options, each in [0, 1], summing to 1 ± 0.001."""
    if not isinstance(raw, dict) or set(map(str, raw)) != set(q.options()):
        return None
    probs = {str(k): _prob(v) for k, v in raw.items()}
    if any(v is None for v in probs.values()) or abs(sum(probs.values()) - 1) > SUM_TOLERANCE:
        return None
    return {k: probs[k] for k in q.options()}  # the request's order


def normalize_answer(q: Question, raw: Any, names: dict[str, str] = ANSWER_NAMES["system_one"]) -> Answer | str:
    """One provider answer as an `Answer`, or the question failure class (§6.2, §6.3)."""
    if not isinstance(raw, dict):
        return "invalid_answer"
    if raw.get("type") == "refusal":
        return "refused"
    conf = _prob(_get(raw, names["confidence"]))
    if q.type == "noul":
        p = _prob(_get(raw, names["noul"]))
        if p is None:
            return "invalid_answer"
        top = "true" if p >= 0.5 else "false"
        return Answer(type="noul", probabilities={"true": p, "false": 1 - p}, top=top, top_p=max(p, 1 - p),
                      vendor_confidence=conf)
    if q.type in ("choice", "score"):
        if "choices" in raw:  # a multiple-choice answer to a single choice
            return "invalid_answer"
        probs = _distribution(q, _get(raw, names["probabilities"]))
        if probs is None:
            return "invalid_answer"
        if q.type == "choice":
            picked = _get(raw, names["choice"])
            if picked is not None and picked not in probs:
                return "invalid_answer"
        top = max(probs, key=probs.get)  # ties: the first in the request's order
        expected = sum(int(k) * p for k, p in probs.items()) if q.type == "score" else None
        return Answer(type=q.type, probabilities=probs, top=top, top_p=probs[top], expected=expected,
                      vendor_confidence=conf)
    if q.type == "multi":
        raw_p = raw.get("probabilities")
        if not isinstance(raw_p, dict) or set(raw_p) != set(q.options()):
            return "invalid_answer"
        probs = {k: _prob(raw_p[k]) for k in q.options()}
        if any(v is None for v in probs.values()):
            return "invalid_answer"
        top = max(probs, key=probs.get)
        return Answer(type="multi", probabilities=probs, top=top, top_p=probs[top])
    # text / number / date: typed extraction of the local d1 models (§7.3)
    found = _prob(raw.get("found"))
    interval = raw.get("interval")
    span = raw.get("span")
    extract = ExtractResult(
        value=raw.get(q.type),
        text=raw.get("text") if isinstance(raw.get("text"), str) else None,
        span=tuple(span) if isinstance(span, list) and len(span) == 2 else None,
        found=found,
        interval=tuple(interval) if isinstance(interval, list) and len(interval) == 2 else None,
        unit=raw.get("unit"),
    )
    probs = {"found": found} if found is not None else {}
    return Answer(type=q.type, probabilities=probs, top_p=found, extract=extract)


def truncate_raw(data: Any) -> dict | None:
    if data is None:
        return None
    text = json.dumps(data, ensure_ascii=False)
    if len(text.encode()) <= RAW_LIMIT:
        return data if isinstance(data, dict) else {"_value": data}
    return {"_truncated": True, "_text": text.encode()[:RAW_LIMIT].decode(errors="ignore")}


class BaseEngine:
    """What every engine shares: a semaphore by `concurrency`, a deadline, one retry after 2 s on network/5xx."""

    kind: ClassVar[str] = "base"
    retry_delay_s: float = 2.0

    def __init__(self, spec: EngineSpec, ctx: Any, transport: httpx.AsyncBaseTransport | None = None):
        self.spec = spec
        self.ctx = ctx  # settings, catalog, budget config (app.registry.EngineContext)
        self.transport = transport
        self.sem = asyncio.Semaphore(max(1, spec.concurrency))
        self._client: httpx.AsyncClient | None = None

    # --- protocol -------------------------------------------------------------------------------------------------
    def available(self) -> tuple[bool, str]:
        return True, "ok"

    def profile(self) -> CapabilityProfile:
        return CapabilityProfile(types=["noul", "choice", "score"], max_choice_options=MAX_CHOICE_OPTIONS,
                                 max_score_levels=MAX_SCORE_LEVELS)

    def estimate_cost(self, req: DecisionRequest) -> float | None:
        return 0.0

    async def run(self, req: DecisionRequest, opts: RunOptions) -> EngineResult:
        raise NotImplementedError

    def describe(self) -> dict[str, Any]:
        ok, reason = self.available()
        return {
            "id": self.spec.id,
            "kind": self.kind,
            "model": self.spec.model or self.spec.repo,
            "enabled": self.spec.enabled,
            "available": ok,
            "reason": reason,
            "zdr": self.spec.zdr,
            "paid": False,
            "profile": self.profile().model_dump(),
        }

    # --- shared ---------------------------------------------------------------------------------------------------
    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(transport=self.transport, headers={"User-Agent": "decision-models-playground/0.1"})
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def post(self, url: str, body: dict, headers: dict[str, str], deadline: float) -> httpx.Response:
        """POST with the deadline; one retry after `retry_delay_s` on a network error, 5xx, 524 or 529 (§7.1)."""
        loop = asyncio.get_running_loop()
        for attempt in (1, 2):
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise EngineFailure("timed_out", "the deadline passed before the call")
            try:
                r = await self.client.post(url, json=body, headers=headers, timeout=min(self.spec.timeout_s, remaining))
            except httpx.TimeoutException:
                raise EngineFailure("timed_out", f"no answer in {min(self.spec.timeout_s, remaining):.0f} s") from None
            except httpx.TransportError as e:
                if attempt == 1 and deadline - loop.time() > self.retry_delay_s:
                    await asyncio.sleep(self.retry_delay_s)
                    continue
                raise EngineFailure("unreachable", type(e).__name__) from None
            if r.status_code >= 500:
                if attempt == 1 and deadline - loop.time() > self.retry_delay_s:
                    await asyncio.sleep(self.retry_delay_s)
                    continue
                raise EngineFailure("provider_error", f"HTTP {r.status_code} after a retry", r.status_code)
            return r
        raise AssertionError("unreachable")
