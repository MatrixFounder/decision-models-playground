"""Local d1 models through mlx-vlm, in process (docs/DESIGN.md §7.3, §8).

P0 facts this engine relies on (docs/SPIKE.md §2):
- `model.system_one(state, questions, media)` → {"answers": {...}, "usage": {"input_tokens": n, "output_tokens": 0}};
- answers carry System One names plus `choices`, `text`/`number`/`date` with `span`/`found`, `interval`;
- `score` defaults to the median level: the engine asks for `mean` and reads `expected` from `probabilities` anyway;
- `confidence` is p_max (kept as vendor_confidence only);
- memory grows with the square of the packed row (state + a group of questions + media), not of the state alone.

Review P2 hardening:
- a question whose `instructions` is not text is `unsupported`: d1 reads a dict there as a media item and opens its
  `path` — any file on the Mac, or a URL fetched with no timeout;
- `precheck` runs before the queue: mlx-vlm's own question checks, then the exact token count of the whole row as
  mlx-vlm will render it (a dict state is written with indent=2). Nothing refused there loads or evicts a model.
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import platform
from importlib import metadata
from pathlib import Path
from typing import Any

from app.engines.base import ANSWER_NAMES, BaseEngine, normalize_answer, truncate_raw
from app.local_models import Busy, Loaded, LoadError, WaitTimeout, render_state
from app.schemas import MAX_STATE_DEPTH, json_depth
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

MIN_MLX_VLM = (0, 7, 11)
DEFAULT_CONTEXT = 32768
# Token allowances on top of the counted text (conservative): the template around a question, an option's code and
# separators, a picture (d1 caps it at 1,638,400 px: 16-px patches merged 2x2 ≈ 1,600 tokens plus markup), an audio
# clip (≈ 12.5 tokens a second, SPIKE §2.5, at the 30 s default of `audio.max_seconds`).
QUESTION_TOKENS = 32
OPTION_TOKENS = 4
AUDIO_TOKENS = 500
# A state whose compact JSON is this many times context_tokens is never rendered: no real transcript fits that
# (Russian dialogue is ~2.6 characters a token), and rendering is the cost to avoid (review round 2).
RENDER_FACTOR = 16


def image_tokens() -> int:
    """A picture's allowance from d1's pixel cap (`D1_VISION_MAX_PIXELS` raises it): 1,024 pixels a token, plus markup."""
    return int(os.environ.get("D1_VISION_MAX_PIXELS") or 1_638_400) // 1024 + 100


class TooLong(Exception):
    def __init__(self, tokens: int, limit: int, what: str = "tokens in the row"):
        super().__init__(f"{tokens} {what} > context_tokens {limit}")
        self.tokens, self.limit = tokens, limit


def d1_question(q: Question) -> dict[str, Any]:
    """An internal question in d1's own JSON (mlx-vlm's check_question)."""
    out: dict[str, Any] = {"type": "choice" if q.type == "multi" else q.type, "instructions": q.instructions}
    if q.type == "multi":
        out["multiple"] = True
    if q.criteria is not None:
        out["criteria"] = q.criteria
    if q.range is not None:
        out["range"] = list(q.range)
    if q.step is not None:
        out["step"] = q.step
    if q.unit:
        out["unit"] = q.unit
    return out


def question_text(key: str, q: Question) -> str:
    """Everything a question adds to the row as text: its key, instructions, option ids and descriptions, levels.
    A multi writes each option id once more, as its own yes/no marker (mlx-vlm prompt.py)."""
    parts = [key, q.instructions if isinstance(q.instructions, str) else str(q.instructions)]
    if isinstance(q.criteria, dict):
        parts += [f"{k} {v or ''}" for k, v in q.criteria.items()]
        if q.type == "multi":
            parts += list(q.criteria)
    elif isinstance(q.criteria, list):
        parts += q.criteria
    if q.unit:
        parts.append(q.unit)
    return "\n".join(parts)


def groups(questions: dict[str, Question], size: int) -> list[dict[str, Question]]:
    """The questions as mlx-vlm packs them: `size` at a time after the state, in order (d1.py `_request`)."""
    items = list(questions.items())
    return [dict(items[i:i + size]) for i in range(0, len(items), max(1, size))]


def allowance(questions: dict[str, Question], images: int = 0, audio: int = 0) -> int:
    options = sum(len(q.criteria) for q in questions.values() if isinstance(q.criteria, (dict, list)))
    return QUESTION_TOKENS * len(questions) + OPTION_TOKENS * options + image_tokens() * images + AUDIO_TOKENS * audio


def compact_len(state: Any) -> int:
    """The state's length as whip-insight counts it (compact JSON, or the text itself) — cheap, renders nothing."""
    if state is None:
        return 0
    return len(state) if isinstance(state, str) else len(json.dumps(state, ensure_ascii=False))


def cheap_chars(state: Any, questions: dict[str, Question], size: int, images: int = 0) -> int:
    """whip-insight's 1 token per character over the compact state plus the heaviest group of questions."""
    worst = max((sum(len(question_text(k, q)) for k, q in g.items()) + allowance(g) for g in groups(questions, size)),
                default=0)
    return compact_len(state) + worst + image_tokens() * images


def mlx_vlm_version() -> tuple[int, ...] | None:
    try:
        return tuple(int(x) for x in metadata.version("mlx-vlm").split(".")[:3])
    except (metadata.PackageNotFoundError, ValueError):
        return None


class MlxLocal(BaseEngine):
    kind = "mlx_local"

    @property
    def repo(self) -> str:
        return self.spec.repo or self.spec.model or ""

    @property
    def manager(self):
        return self.ctx.local

    def available(self) -> tuple[bool, str]:
        if not self.spec.enabled:
            return False, "disabled in config.yaml"
        if not self.repo:
            return False, "no repo in config.yaml"
        if platform.system() != "Darwin" or platform.machine() != "arm64":
            return False, "needs macOS on Apple Silicon"
        version = mlx_vlm_version()
        if version is None:
            return False, "mlx-vlm is not installed (requirements-mlx.txt)"
        if version < MIN_MLX_VLM:
            return False, f"mlx-vlm {'.'.join(map(str, version))} < 0.7.11"
        if self.manager is None:
            return False, "the local model manager is not running"
        if self.manager.backend.disk_gb(self.repo) is None:
            return False, f"{self.repo} is not downloaded: make models"
        return True, "ok"

    def profile(self) -> CapabilityProfile:
        media = ["image"] + (["audio"] if self.spec.audio else [])
        return CapabilityProfile(types=["noul", "choice", "score", "multi", "text", "number", "date"],
                                 max_choice_options=MAX_CHOICE_OPTIONS, max_score_levels=MAX_SCORE_LEVELS,
                                 media=media, source="default")

    def estimate_cost(self, req: DecisionRequest) -> float:
        return 0.0

    @property
    def context_tokens(self) -> int:
        return self.spec.context_tokens or DEFAULT_CONTEXT

    def describe(self) -> dict[str, Any]:
        d = super().describe()
        d.update(paid=False, local=True, repo=self.repo, audio=self.spec.audio, context_tokens=self.context_tokens)
        if self.manager is not None:
            d.update(disk_gb=self.manager.backend.disk_gb(self.repo), **self.manager.describe(self.repo))
        return d

    def media_items(self, req: DecisionRequest) -> list[dict[str, Any]]:
        """Media by path, only from DATA_DIR/media (P6 uploads there); anything else is refused."""
        root = (self.ctx.settings.data_dir / "media").resolve()
        out = []
        for m in req.media:
            path = (root / m.path).resolve()
            if not path.is_relative_to(root) or not path.is_file():
                raise ValueError(f"media {m.path!r} is not a file under DATA_DIR/media")
            if m.type == "audio" and not self.spec.audio:
                raise ValueError("this model takes no audio")
            out.append({"type": m.type, "path": str(path)})
        return out

    async def precheck(self, state: Any, questions: dict[str, Question], images: int = 0, audio: int = 0) -> int:
        """Before the queue, in this order so nothing big is built on the way (review round 2):
        1. mlx-vlm's question checks (ValueError → refused);
        2. the state's nesting (≤ MAX_STATE_DEPTH) and compact size (≤ RENDER_FACTOR × context) — no rendering;
        3. off the event loop: the state as mlx-vlm renders and escapes it, plus the heaviest group of `set_size`
           questions, counted by the model's own tokenizer (TooLong). Returns the counted tokens."""
        backend = self.manager.backend
        await asyncio.to_thread(backend.validate, {k: d1_question(q) for k, q in questions.items()})
        if not isinstance(state, str) and json_depth(state) > MAX_STATE_DEPTH:
            raise ValueError(f"state is nested deeper than {MAX_STATE_DEPTH} levels")
        compact = compact_len(state)
        if compact > self.context_tokens * RENDER_FACTOR:
            raise TooLong(compact, self.context_tokens, "state characters, far beyond any row that fits,")

        def measure() -> int:
            size = backend.set_size(self.repo)
            state_tokens = backend.count_tokens(self.repo, render_state(state)) if state is not None else 0
            worst = max((backend.count_tokens(self.repo, "\n".join(question_text(k, q) for k, q in g.items()))
                         + allowance(g) for g in groups(questions, size)), default=0)
            return state_tokens + worst + allowance({}, images, audio)

        try:
            tokens = await asyncio.to_thread(measure)
        except Exception as e:  # tokenizers raises a bare Exception for a missing or corrupt tokenizer.json
            raise LoadError(f"the tokenizer of {self.repo} is not available: {type(e).__name__}") from None
        if tokens > self.context_tokens:
            raise TooLong(tokens, self.context_tokens)
        return tokens

    def invoke(self, loaded: Loaded, state: Any, questions: dict[str, Any], media: list | None,
               score_readout: str = "mean") -> dict[str, Any]:
        """On the MLX thread: one system_one call (the row was measured by `precheck`)."""
        return loaded.model.system_one(state, questions, media or None, score_readout=score_readout)

    async def run(self, req: DecisionRequest, opts: RunOptions) -> EngineResult:
        keys = list(req.questions)
        not_text = {k: "unsupported" for k, q in req.questions.items() if not isinstance(q.instructions, str)}

        def done(answers: dict[str, Answer], failures: dict[str, str] | None = None, latency_ms: int = 0,
                 **kw) -> EngineResult:
            return EngineResult.build(self.spec.id, keys, answers, {**not_text, **(failures or {})},
                                      latency_ms=latency_ms, **kw)

        if self.manager is None:
            return done({}, request_failure="unreachable", detail="the local model manager is not running")
        send = {k: q for k, q in req.questions.items() if k not in not_text}
        if not send:
            return done({}, detail="instructions must be text for a local d1 (spoken questions arrive in P6)")
        try:
            media = self.media_items(req)
        except ValueError as e:
            return done({}, request_failure="refused", detail=str(e))
        try:
            await self.precheck(req.state, send, images=sum(m["type"] == "image" for m in media),
                                audio=sum(m["type"] == "audio" for m in media))
        except TooLong as e:
            return done({}, request_failure="too_long", detail=str(e))
        except LoadError as e:
            return done({}, request_failure="unreachable", detail=str(e))
        except ValueError as e:
            return done({}, request_failure="refused", detail=f"mlx-vlm: {e}"[:300])

        d1 = {k: d1_question(q) for k, q in send.items()}
        try:
            out, loaded, timings = await self.manager.call(
                self.repo, lambda m: self.invoke(m, req.state, d1, media), warm_audio=self.spec.audio,
                wait_s=self.spec.timeout_s)
        except Busy as e:
            return done({}, request_failure="rate_limited", detail=str(e))
        except WaitTimeout as e:
            return done({}, request_failure="timed_out", detail=str(e))
        except LoadError as e:
            return done({}, request_failure="unreachable", detail=str(e))
        except ValueError as e:  # mlx-vlm refused what its own checks let through
            return done({}, request_failure="refused", detail=f"mlx-vlm: {e}"[:300])
        except Exception as e:
            return done({}, request_failure="provider_error", detail=f"{type(e).__name__}: {e}"[:300])

        answers: dict[str, Answer] = {}
        failures: dict[str, str] = {}
        raw_answers = out.get("answers") if isinstance(out, dict) else None
        for k, q in send.items():
            a = normalize_answer(q, (raw_answers or {}).get(k), ANSWER_NAMES["system_one"])
            if isinstance(a, str):
                failures[k] = a
            else:
                answers[k] = a
        u = (out.get("usage") if isinstance(out, dict) else None) or {}
        # latency is the model call itself; the queue and a load show in `timings` (cloud and local compare fairly)
        return done(answers, failures, latency_ms=timings["call_ms"], snapshot=loaded.snapshot, timings=timings,
                    usage=Usage(input_tokens=u.get("input_tokens"), output_tokens=u.get("output_tokens", 0),
                                cost_usd=0.0, cost_source="none"),
                    raw=truncate_raw(json_safe(out)))


def json_safe(value: Any) -> Any:
    """system_one returns plain Python, but numpy scalars can slip in: make the record serializable. Non-finite
    floats become None (they are not JSON)."""
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    if hasattr(value, "item") and callable(value.item):
        value = value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, Path):
        return str(value)
    return value
