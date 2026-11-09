"""The engine protocol, BaseEngine and the answer-name table (docs/ARCHITECTURE.md §3.5.1, §3.5.2, §3.5.6, §4.2)."""

from __future__ import annotations

import asyncio
import json
import logging
import math
from typing import Any, ClassVar

import httpx

from app import normalize_ru
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

log = logging.getLogger("playground.engines")
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


def _isfinite(x: Any) -> bool:
    """math.isfinite without the OverflowError a 400-digit int raises (review round 2)."""
    try:
        return math.isfinite(x)
    except (OverflowError, TypeError):
        return False


def _prob(x: Any) -> float | None:
    if isinstance(x, bool) or not isinstance(x, (int, float)) or not _isfinite(x):
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


def _number(x: Any) -> float | None:
    if isinstance(x, bool) or not isinstance(x, (int, float)) or not _isfinite(x):
        return None
    return float(x)


def _span(x: Any) -> tuple[int, int] | None | str:
    """A [start, end) character span, None when absent, "bad" when malformed."""
    if x is None:
        return None
    if (isinstance(x, list) and len(x) == 2 and all(isinstance(i, int) and not isinstance(i, bool) for i in x)
            and 0 <= x[0] <= x[1]):
        return (x[0], x[1])
    return "bad"


def normalize_answer(q: Question, raw: Any, names: dict[str, str] = ANSWER_NAMES["system_one"]) -> Answer | str:
    """One provider answer as an `Answer`, or the question failure class (§6.2, §6.3). Never raises: an alpha API
    (RQ-4) that sends an odd field costs that question, not the whole engine result (review P1)."""
    try:
        return _normalize(q, raw, names)
    except Exception as e:  # any odd value is this question's problem, never the engine's
        # logged: a bug in _normalize must not pass for the model misbehaving on every question (review round 3)
        log.warning("normalize_answer: %s on a %s answer", type(e).__name__, q.type)
        return "invalid_answer"


def _normalize(q: Question, raw: Any, names: dict[str, str]) -> Answer | str:
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
            if picked is not None and (not isinstance(picked, str) or picked not in probs):
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
    # text / number / date: typed extraction of the local d1 models (§7.3); a malformed field is invalid_answer
    found = _prob(raw.get("found"))
    if raw.get("found") is not None and found is None:
        return "invalid_answer"
    span = _span(raw.get("span"))
    interval = raw.get("interval")
    if interval is not None:
        bounds = [_number(x) for x in interval] if isinstance(interval, list) and len(interval) == 2 else [None]
        if None in bounds:
            return "invalid_answer"
        interval = tuple(bounds)
    value, text, unit = raw.get(q.type), raw.get("text"), raw.get("unit")
    if q.type == "number" and value is not None and _number(value) is None:
        return "invalid_answer"
    if q.type in ("text", "date") and value is not None and not isinstance(value, str):
        return "invalid_answer"
    if span == "bad" or text is not None and not isinstance(text, str) or unit is not None and not isinstance(unit, str):
        return "invalid_answer"
    normalized = normalize_ru.normalize(q.type, text) if value is None else None
    extract = ExtractResult(value=value, text=text, span=span, found=found, interval=interval, unit=unit,
                            normalized=normalized)
    probs = {"found": found} if found is not None else {}
    return Answer(type=q.type, probabilities=probs, top_p=found, extract=extract)


def _finite(data: Any) -> Any:
    """NaN and ±inf become None: they are not JSON, and a record or an SSE frame must stay parseable."""
    if isinstance(data, float) and not math.isfinite(data):
        return None
    if isinstance(data, dict):
        return {k: _finite(v) for k, v in data.items()}
    if isinstance(data, list):
        return [_finite(v) for v in data]
    return data


def truncate_raw(data: Any) -> dict | None:
    """The provider's answer as stored in a record: finite numbers only, at most RAW_LIMIT bytes once serialized."""
    if data is None:
        return None
    data = _finite(data)
    text = json.dumps(data, ensure_ascii=False)
    if len(text.encode()) <= RAW_LIMIT:
        return data if isinstance(data, dict) else {"_value": data}
    cut = text.encode()[: RAW_LIMIT - 64].decode(errors="ignore")
    while True:  # escaping inside "_text" grows the text: shrink until the serialized result fits
        out = {"_truncated": True, "_text": cut}
        over = len(json.dumps(out, ensure_ascii=False).encode()) - RAW_LIMIT
        if over <= 0:
            return out
        cut = cut[: max(0, len(cut) - over - 16)]


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

    def default_profile(self) -> CapabilityProfile:
        """What the engine kind can serve at most, before any probe."""
        return CapabilityProfile(types=["noul", "choice", "score"], max_choice_options=MAX_CHOICE_OPTIONS,
                                 max_score_levels=MAX_SCORE_LEVELS)

    def probed(self) -> dict[str, Any] | None:
        """This engine's latest probe (validated by the probe store), when it was made on the kind and model the
        config names now: after `config.yaml` points the id at another model, the old profile is not this one's
        (SPEC §5.6.2 item 4; the matrix marks it stale)."""
        probes = getattr(self.ctx, "probes", None)
        p = probes.latest(self.spec.id) if probes is not None else None
        if p and p.get("kind") == self.kind and p.get("model") == (self.spec.model or self.spec.repo):
            return p
        return None

    def profile(self) -> CapabilityProfile:
        """The profile the runner checks questions against (§10 step 2, §13): the latest probe of this engine
        within the kind's ceiling, else the default. A probe can confirm or narrow, never widen."""
        default = self.default_profile()
        p = self.probed()
        if p is None:
            return default
        types = [t for t in default.types if t in (p.get("types") or [])]

        def limit(probed: int | None, top: int | None, ceiling: int | None) -> int | None:
            # the largest size the probe tried, served, says "at least that", not "at most": the ceiling stands
            # (P0 measured 255 options served; the probe stops at whip-insight's 240, review P3)
            if probed is None or top is not None and probed >= top:
                return ceiling
            return probed if ceiling is None else min(probed, ceiling)

        return CapabilityProfile(
            types=types,
            max_choice_options=limit(p.get("max_choice_options"), p.get("max_choice_probed"), default.max_choice_options),
            max_score_levels=limit(p.get("max_score_levels"), p.get("max_score_probed"), default.max_score_levels),
            # pictures are not probed, so they stay as the kind allows; audio only when the probe heard it
            media=[m for m in default.media if m != "audio" or "audio" in (p.get("media") or [])],
            source="probe",
            probed_at=p.get("probed_at"),
        )

    def context_limit_tokens(self) -> int:
        """The context the long-context probe step fills (§13.1 step 6)."""
        return 32768

    def max_text_bytes(self) -> int | None:
        """The most bytes of text one request may carry, whatever the context (§13.1: caps the long step)."""
        return self.spec.max_request_bytes

    def estimate_cost(self, req: DecisionRequest) -> float | None:
        return 0.0

    def cost_of_tokens(self, tokens: int) -> float:
        """The price of `tokens` input tokens (the probe's long step is sized in tokens, not characters)."""
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
            retry = attempt == 1 and deadline - loop.time() > self.retry_delay_s
            tried = "after a retry" if attempt == 2 else "no time left to retry"
            try:
                r = await self.client.post(url, json=body, headers=headers, timeout=min(self.spec.timeout_s, remaining))
            except (httpx.ConnectTimeout, httpx.PoolTimeout) as e:  # no connection yet: a network error (§7.1)
                if retry:
                    await asyncio.sleep(self.retry_delay_s)
                    continue
                raise EngineFailure("unreachable", f"{type(e).__name__}, {tried}") from None
            except httpx.TimeoutException:
                raise EngineFailure("timed_out", f"no answer in {min(self.spec.timeout_s, remaining):.0f} s") from None
            except httpx.TransportError as e:
                if retry:
                    await asyncio.sleep(self.retry_delay_s)
                    continue
                raise EngineFailure("unreachable", f"{type(e).__name__}, {tried}") from None
            if r.status_code >= 500:
                if retry:
                    await asyncio.sleep(self.retry_delay_s)
                    continue
                raise EngineFailure("provider_error", f"HTTP {r.status_code}, {tried}", r.status_code)
            return r
        raise AssertionError("unreachable")
