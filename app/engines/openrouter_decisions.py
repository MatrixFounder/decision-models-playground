"""OpenRouter Decisions (docs/DESIGN.md §7.2): POST https://openrouter.ai/api/alpha/decisions, System One body.

Facts from P0 (docs/SPIKE.md §3) this engine relies on:
- an unknown question type fails the whole request with 400; `error.message` is a zod error serialized as a string,
  its `path` is ["questions", "<key>", "type"]. The engine marks that key `unsupported` and sends the rest once more;
- `box` outside Jev answers 400 "… question 1 is a box" (1-based index, not the key);
- `choice` with `multiple: true` answers 200 and silently drops the flag: `multi` is never sent;
- `model` in the answer is the dated snapshot, also for the alias `~liquid/d1-latest`;
- `usage.cost` comes on every answer, without flags.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any

from app.engines.base import ANSWER_NAMES, BaseEngine, EngineFailure, normalize_answer, truncate_raw
from app.schemas import (
    MAX_CHOICE_OPTIONS,
    MAX_SCORE_LEVELS,
    Answer,
    CapabilityProfile,
    DecisionRequest,
    EngineResult,
    Question,
    RunOptions,
    Usage,
)

URL = "https://openrouter.ai/api/alpha/decisions"
PROVIDER_ZDR = {"zdr": True, "data_collection": "deny"}
BOX_INDEX = re.compile(r"question (\d+) is an? (\w+)")


def system_one_question(q: Question) -> dict[str, Any]:
    """A question in System One's shape: noul, choice, score only (the caller filtered the rest)."""
    out: dict[str, Any] = {"type": q.type, "instructions": q.instructions}
    if q.criteria is not None:
        out["criteria"] = q.criteria
    return out


def unsupported_keys(body: Any, sent: list[str]) -> list[str]:
    """The question keys a 400 names as an unsupported type (zod `path`, or the `question N is a box` form)."""
    err = body.get("error") if isinstance(body, dict) else None
    message = err.get("message") if isinstance(err, dict) else None
    if not isinstance(message, str):
        return []
    named: list[str] = []
    try:
        issues = json.loads(message)
    except ValueError:
        issues = None
    if isinstance(issues, list):
        for issue in issues:
            path = issue.get("path") if isinstance(issue, dict) else None
            if (
                isinstance(path, list) and len(path) >= 3 and path[0] == "questions" and path[1] in sent
                and path[-1] == "type"
            ):
                named.append(path[1])
    for m in BOX_INDEX.finditer(message):
        i = int(m.group(1)) - 1
        if 0 <= i < len(sent):
            named.append(sent[i])
    return list(dict.fromkeys(named))


def error_text(body: Any, status: int) -> str:
    err = body.get("error") if isinstance(body, dict) else None
    msg = err.get("message") if isinstance(err, dict) else None
    if isinstance(msg, str):
        try:  # a zod list: keep its human message
            issues = json.loads(msg)
            msg = "; ".join(str(i.get("message")) for i in issues if isinstance(i, dict)) or msg
        except ValueError:
            pass
        return f"HTTP {status}: {msg[:300]}"
    return f"HTTP {status}"


class OpenRouterDecisions(BaseEngine):
    kind = "openrouter_decisions"
    url = URL

    def available(self) -> tuple[bool, str]:
        if not self.spec.enabled:
            return False, "disabled in config.yaml"
        if not self.spec.model:
            return False, "no model in config.yaml"
        if not self.ctx.settings.openrouter_key:
            return False, "OPENROUTER_API_KEY is not set in .env"
        return True, "ok"

    def profile(self) -> CapabilityProfile:
        # Until the probe (P3): System One's three types; 255 options and 10 levels were accepted in P0.
        return CapabilityProfile(types=["noul", "choice", "score"], max_choice_options=MAX_CHOICE_OPTIONS,
                                 max_score_levels=MAX_SCORE_LEVELS, source="default")

    def price_per_token(self) -> float | None:
        return self.ctx.catalog.price_per_token(self.spec.model)

    def estimate_cost(self, req: DecisionRequest) -> float:
        cfg = self.ctx.budget
        tokens = cfg.tokens_per_char * req.text_size() + cfg.overhead_tokens
        price = self.price_per_token()
        if price is None:
            price = self.ctx.catalog.max_decision_price() or cfg.fallback_price_per_mtok / 1e6
        return tokens * price

    def describe(self) -> dict[str, Any]:
        d = super().describe()
        m = self.ctx.catalog.model(self.spec.model) or {}
        price = self.price_per_token()
        d.update(
            paid=True,
            price_per_mtok=None if price is None else round(price * 1e6, 4),
            context_length=m.get("context_length"),
            canonical_slug=m.get("canonical_slug"),
            expiration_date=m.get("expiration_date"),
            zdr_endpoint=self.ctx.catalog.zdr_endpoint(self.spec.model),
        )
        return d

    def body(self, req: DecisionRequest, keys: list[str]) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self.spec.model,
            "state": req.state,
            "questions": {k: system_one_question(req.questions[k]) for k in keys},
        }
        if self.spec.zdr:
            body["provider"] = dict(PROVIDER_ZDR)
        return body

    async def run(self, req: DecisionRequest, opts: RunOptions) -> EngineResult:
        t0 = time.perf_counter()
        keys = list(req.questions)
        failures: dict[str, str] = {}

        def done(answers: dict[str, Answer], more_failures: dict[str, str] | None = None, **kw) -> EngineResult:
            return EngineResult.build(self.spec.id, keys, answers, {**failures, **(more_failures or {})},
                                      latency_ms=int((time.perf_counter() - t0) * 1000), **kw)

        if req.media:
            return done({}, request_failure="refused", detail="media is not sent to OpenRouter yet (P6)")
        if not self.spec.zdr and not opts.allow_no_zdr:
            return done({}, request_failure="refused", detail="ZDR is off for this engine: confirm it for the run")
        key = self.ctx.settings.openrouter_key
        if not key:
            return done({}, request_failure="refused", detail="OPENROUTER_API_KEY is not set")

        sent = keys
        deadline = asyncio.get_running_loop().time() + max(self.spec.timeout_s * 2 + self.retry_delay_s, 1)
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        for round_ in (1, 2):
            body = self.body(req, sent)
            size = len(json.dumps(body, ensure_ascii=False).encode())
            if self.spec.max_request_bytes and size > self.spec.max_request_bytes:
                return done({}, request_failure="too_long",
                            detail=f"{size} bytes > max_request_bytes {self.spec.max_request_bytes}")
            try:
                async with self.sem:
                    r = await self.post(self.url, body, headers, deadline)
            except EngineFailure as e:
                return done({}, request_failure=e.cls, detail=e.detail)
            try:
                data = r.json()
            except ValueError:
                data = None
            status = r.status_code
            if status == 200 and isinstance(data, dict):
                return self._read(data, req, sent, done)
            if status == 400:
                named = unsupported_keys(data, sent)
                if named and round_ == 1:
                    failures.update({k: "unsupported" for k in named})
                    sent = [k for k in sent if k not in named]
                    if not sent:
                        return done({}, detail=error_text(data, status), raw=truncate_raw(data))
                    continue
            cls = {413: "too_long", 429: "rate_limited", 408: "timed_out"}.get(status, "refused")
            if status == 200:
                cls = "provider_error"
            return done({}, request_failure=cls, detail=error_text(data, status), raw=truncate_raw(data))
        raise AssertionError("unreachable")

    def _read(self, data: dict, req: DecisionRequest, sent: list[str], done) -> EngineResult:
        failures_here: dict[str, str] = {}
        answers: dict[str, Answer] = {}
        raw_answers = data.get("answers") if isinstance(data.get("answers"), dict) else {}
        for k in sent:
            a = normalize_answer(req.questions[k], raw_answers.get(k), ANSWER_NAMES["system_one"])
            if isinstance(a, str):
                failures_here[k] = a
            else:
                answers[k] = a
        u = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        usage = Usage(input_tokens=u.get("input_tokens"), output_tokens=u.get("output_tokens"))
        if isinstance(u.get("cost"), (int, float)):
            usage.cost_usd, usage.cost_source = float(u["cost"]), "provider"
        elif usage.input_tokens is not None and (price := self.price_per_token()) is not None:
            usage.cost_usd, usage.cost_source = usage.input_tokens * price, "price"
        return done(answers, failures_here, snapshot=data.get("model"), usage=usage, raw=truncate_raw(data))
