"""The internal contract (docs/DESIGN.md §6): a superset of System One in, one normalized shape out."""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")  # always used with fullmatch: `$` alone lets "key\n" through


def valid_key(k: Any) -> bool:
    return isinstance(k, str) and KEY_RE.fullmatch(k) is not None
QuestionType = Literal["noul", "choice", "score", "multi", "text", "number", "date"]
SYSTEM_ONE_TYPES = ("noul", "choice", "score")
EXTENSION_TYPES = ("multi", "text", "number", "date")
MAX_CHOICE_OPTIONS = 255
MAX_SCORE_LEVELS = 10
MAX_QUESTIONS = 64

MAX_STATE_DEPTH = 32  # nesting of a JSON state; indent=2 rendering grows with the square of the depth


def json_depth(value: Any, limit: int = MAX_STATE_DEPTH) -> int:
    """Nesting depth of a JSON value (containers only), walked without recursion; stops counting past `limit`."""
    deepest, stack = 0, [(value, 1)]
    while stack:
        v, d = stack.pop()
        if isinstance(v, dict):
            items = v.values()
        elif isinstance(v, list):
            items = v
        else:
            continue
        deepest = max(deepest, d)
        if deepest > limit:
            return deepest
        # containers only: scalars cannot deepen it (round 3: pushing 3 M zeros cost 0.3 s)
        stack.extend((x, d + 1) for x in items if isinstance(x, (dict, list)))
    return deepest


QuestionFailure = Literal["unsupported", "refused", "invalid_answer"]
RequestFailure = Literal["unreachable", "timed_out", "rate_limited", "refused", "too_long", "provider_error", "budget"]


class Question(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: QuestionType
    instructions: str | dict | list
    # noul: {"true": .., "false": ..}; choice/multi: {id: description | None}; score: [levels, lowest first]
    criteria: dict[str, str | None] | list[str] | None = None
    range: tuple[float, float] | None = None  # number: an estimate over [low, high] (x-extension)
    step: float | None = None
    unit: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _d1_multiple(cls, data: Any) -> Any:
        # d1's own form, {"type": "choice", "multiple": true}, is the internal `multi` (DESIGN §6.1)
        if isinstance(data, dict) and "multiple" in data:
            data = dict(data)
            multiple = data.pop("multiple")
            if not isinstance(multiple, bool):  # "false" is truthy: only a real boolean decides (review P2)
                raise ValueError("multiple is true or false")
            if multiple and data.get("type") == "choice":
                data["type"] = "multi"
        return data

    @model_validator(mode="after")
    def _check(self) -> Question:
        if not self.instructions or isinstance(self.instructions, str) and not self.instructions.strip():
            raise ValueError("instructions must not be empty")
        c = self.criteria
        if self.type == "noul":
            if c is not None and (not isinstance(c, dict) or not set(c) <= {"true", "false"}):
                raise ValueError('noul criteria is {"true": "...", "false": "..."}')
        elif self.type in ("choice", "multi"):
            if not isinstance(c, dict) or not 2 <= len(c) <= MAX_CHOICE_OPTIONS:
                raise ValueError(f"{self.type} criteria maps 2 to {MAX_CHOICE_OPTIONS} option ids to descriptions")
            bad = [k for k in c if not valid_key(k)]
            if bad:
                raise ValueError(f"option ids must match {KEY_RE.pattern}: {bad[:5]}")
        elif self.type == "score":
            if not isinstance(c, list) or not 2 <= len(c) <= MAX_SCORE_LEVELS or not all(isinstance(x, str) and x.strip() for x in c):
                raise ValueError(f"score criteria lists 2 to {MAX_SCORE_LEVELS} non-empty levels, lowest first")
        elif c is not None:
            raise ValueError(f"{self.type} takes no criteria")
        if self.range is not None:
            if self.type != "number" or not self.range[0] < self.range[1]:
                raise ValueError("range belongs to a number question and runs low to high")
        if self.step is not None and (self.range is None or self.step <= 0):
            raise ValueError("step needs a range and must be above 0")
        return self

    def options(self) -> list[str]:
        """The answer's probability keys: noul true/false, choice/multi ids, score level indexes."""
        if self.type == "noul":
            return ["true", "false"]
        if self.type in ("choice", "multi"):
            return list(self.criteria or {})
        if self.type == "score":
            return [str(i) for i in range(len(self.criteria or []))]
        return []

    def text_size(self) -> int:
        n = len(self.instructions) if isinstance(self.instructions, str) else len(str(self.instructions))
        c = self.criteria
        if isinstance(c, dict):
            n += sum(len(k) + len(v or "") for k, v in c.items())
        elif isinstance(c, list):
            n += sum(len(x) for x in c)
        return n


class MediaItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["image", "audio", "video"]
    path: str
    sha256: str | None = None


class DecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: str | dict | list | None = None
    questions: dict[str, Question]
    media: list[MediaItem] = Field(default_factory=list)

    @field_validator("questions")
    @classmethod
    def _keys(cls, v: dict[str, Question]) -> dict[str, Question]:
        if not 1 <= len(v) <= MAX_QUESTIONS:
            raise ValueError(f"1 to {MAX_QUESTIONS} questions")
        bad = [k for k in v if not valid_key(k)]
        if bad:
            raise ValueError(f"question keys must match {KEY_RE.pattern}: {bad[:5]}")
        return v

    @model_validator(mode="after")
    def _state_or_media(self) -> DecisionRequest:
        if self.state is None and not self.media:
            raise ValueError("state is required unless media carries the whole state")
        if not isinstance(self.state, str) and json_depth(self.state) > MAX_STATE_DEPTH:
            # mlx-vlm writes a JSON state with indent=2: the text grows with the square of the depth (review round 2)
            raise ValueError(f"state is nested deeper than {MAX_STATE_DEPTH} levels")
        return self

    def state_text(self) -> str:
        import json

        if self.state is None:
            return ""
        return self.state if isinstance(self.state, str) else json.dumps(self.state, ensure_ascii=False)

    def text_size(self) -> int:
        return len(self.state_text()) + sum(len(k) + q.text_size() for k, q in self.questions.items())

    def subset(self, keys: list[str]) -> DecisionRequest:
        return DecisionRequest(state=self.state, questions={k: self.questions[k] for k in keys}, media=self.media)


class ExtractResult(BaseModel):
    value: Any = None
    text: str | None = None
    span: tuple[int, int] | None = None
    found: float | None = None
    interval: tuple[float, float] | None = None
    unit: str | None = None


class Answer(BaseModel):
    type: str
    probabilities: dict[str, float] = Field(default_factory=dict)
    top: str | None = None
    top_p: float | None = None
    expected: float | None = None  # score: Σ i·p_i, computed here from probabilities (DESIGN §6.2)
    vendor_confidence: float | None = None  # kept, never used in metrics: its meaning differs by provider
    extract: ExtractResult | None = None


class Usage(BaseModel):
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float | None = None
    cost_source: Literal["provider", "price", "none"] = "none"


class EngineResult(BaseModel):
    engine_id: str
    status: Literal["ok", "partial", "failed"]
    answers: dict[str, Answer] = Field(default_factory=dict)
    question_failures: dict[str, QuestionFailure] = Field(default_factory=dict)
    request_failure: RequestFailure | None = None
    detail: str | None = None  # a short human reason for a failure, no keys
    snapshot: str | None = None
    latency_ms: int = 0
    usage: Usage = Field(default_factory=Usage)
    raw: dict | None = None
    timings: dict[str, int] | None = None  # local engines: queue_ms, load_ms, call_ms (latency_ms is the call)

    @classmethod
    def build(cls, engine_id: str, keys: list[str], answers: dict[str, Answer], failures: dict[str, str], **kw) -> EngineResult:
        """Every requested key lands in exactly one of answers and question_failures (DESIGN §6.2) — unless the
        request itself failed: then the keys without an answer or an own failure are covered by `request_failure`,
        not stamped `invalid_answer` (there was no answer to be invalid, review P1)."""
        failures = dict(failures)
        if not kw.get("request_failure"):
            for k in keys:
                if k not in answers and k not in failures:
                    failures[k] = "invalid_answer"
        answers = {k: a for k, a in answers.items() if k in keys and k not in failures}
        if kw.get("request_failure") or not answers:
            status = "failed"
        elif failures:
            status = "partial"
        else:
            status = "ok"
        return cls(engine_id=engine_id, status=status, answers=answers, question_failures=failures, **kw)


class CapabilityProfile(BaseModel):
    types: list[str]
    max_choice_options: int | None = None
    max_score_levels: int | None = None
    media: list[str] = Field(default_factory=list)
    source: Literal["default", "probe", "catalog"] = "default"
    probed_at: str | None = None

    def refuse(self, q: Question) -> bool:
        if q.type not in self.types:
            return True
        if q.type in ("choice", "multi") and self.max_choice_options and len(q.criteria or {}) > self.max_choice_options:
            return True
        if q.type == "score" and self.max_score_levels and len(q.criteria or []) > self.max_score_levels:
            return True
        return False


class EngineSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")  # a typo in config.yaml (`timeout: 60`) fails loudly

    id: str
    kind: str
    model: str | None = None
    repo: str | None = None
    base_url: str | None = None
    enabled: bool = True
    zdr: bool = True
    max_request_bytes: int | None = None
    concurrency: int = 4
    timeout_s: float = 30.0
    context_tokens: int | None = None
    audio: bool = False
    mode: str | None = None


class BudgetConfig(BaseModel):
    # P1 measured Jev at 0.95-1.65 tokens per character and d1.1 at about 0.43: 1.5 x chars + 200 covers every P0 call.
    tokens_per_char: float = 1.5
    overhead_tokens: int = 200
    fallback_price_per_mtok: float = 0.2


class LocalConfig(BaseModel):
    max_loaded_models: Literal[1] = 1  # DESIGN §8: a 16-24 GB Mac holds one model
    idle_unload_s: float = 600
    max_waiting: int = 8  # callers queued for the model beyond this get Busy (compat 429 busy, UI rate_limited)


class CompatConfig(BaseModel):
    default_model: str | None = None  # an engine id of kind mlx_local
    max_questions: int = MAX_QUESTIONS
    max_body_mb: float = 6
    max_images: int = 4  # whip-insight sends none; d1 caps each picture at 1.6 MP anyway
    max_image_pixels: int = 16_000_000  # a 12 MP phone photo passes; a decompression bomb does not
    extensions_header: str = "X-Playground-Extensions"
    allowed_hosts: list[str] = Field(default_factory=lambda: ["127.0.0.1", "localhost", "*.local"])


class AppConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    engines: list[EngineSpec] = Field(default_factory=list)
    budget: BudgetConfig = Field(default_factory=BudgetConfig)
    local: LocalConfig = Field(default_factory=LocalConfig)
    compat: CompatConfig = Field(default_factory=CompatConfig)


class RunOptions(BaseModel):
    allow_no_zdr: bool = False


class RunIn(BaseModel):
    """POST /api/runs."""

    model_config = ConfigDict(extra="forbid")

    request: DecisionRequest
    engines: list[str] = Field(min_length=1)
    tags: list[str] = Field(default_factory=list)
    title: str | None = None
    options: RunOptions = Field(default_factory=RunOptions)
