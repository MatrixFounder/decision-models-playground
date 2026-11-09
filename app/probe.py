"""The capability probe (docs/DESIGN.md §13.1): a fixed set of requests per engine, read into a profile.

The same set and the same rule of evidence as whip-insight (SPEC-decision-service.md §5.6.2, decision-service.md
§5.3), so the two profiles agree:
- N, C, S: one `noul`, one `choice` of 3 options, one `score` of 4 levels on a short state → `types`;
- C24, C100, C240: a choice of that many options → `max_choice_options` (the size rule below);
- S10: a score of 10 levels → `max_score_levels`;
- every answer: `usage.cost` reported → `reports_cost`; a dated `model` → `reports_snapshot`;
- local d1 only: M (`multi`), T (`text`), NUM (`number`), D (`date`), and A (audio) on d1-Omni;
- LONG, by request only: a state of about 90 % of the context with one fact in its last 5 %, as a `noul`:
  P(true) ≥ 0.9 → `reads_full_context`. CAL, N's question on a few filler lines, comes first: with N it fits
  tokens = overhead + ratio × characters, which sizes LONG. A LONG that filled less than 75 % of the context (and
  was not held back by the engine's own byte limit) proves nothing and reads as unobserved.

The reading of one request (whip-insight §5.3): answered or a refusal answer → served; `unsupported`,
`invalid_answer`, or refused with HTTP 400/422 → not served; anything else (network, 401-404, timeouts, rate limits,
`too_long`, `skipped`) → unobserved, which keeps the stored value. A probe in which no request got a 2xx answer
writes no profile (`no_profile`, with the dominant failure class).

Probe calls bypass the runner's profile filter (they measure it), go through the daily budget, and are saved as a
run of kind `probe`, so their cost counts. The profile is stored in probes/<engine_id>/<snapshot>.json and
probes/<engine_id>/latest.json; engines read latest.json as their profile (BaseEngine.probed / profile).
"""

from __future__ import annotations

import json
import logging
import math
import re
import shutil
import time
from collections import Counter
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

from app import __version__
from app.budget import Budget
from app.engines.base import BaseEngine
from app.runner import mlx_vlm_version
from app.schemas import DecisionRequest, EngineResult, Question, RunOptions
from app.settings import ROOT
from app.store import Store, local_day, new_id, write_durably

log = logging.getLogger("playground.probe")
Emit = Callable[[str, dict[str, Any]], Awaitable[None]]
PROBE_TERMINAL = ("probe_done", "budget_stop", "probe_error")
PROBED_KINDS = ("openrouter_decisions", "mlx_local")

SERVED, NOT_SERVED, UNOBSERVED = "served", "not_served", "unobserved"
DATED = re.compile(r"-[0-9]{8}$")
ENGINE_ID = re.compile(r"[A-Za-z0-9._-]{1,100}")
LONG_SHARE = 0.9  # of the context the long state fills (SPEC §5.6.2 item 2.4)
LONG_RETRY_SHARE = 0.8  # once more after `too_long`: 80 % of the context (8/9 of a byte-capped first attempt)
QUESTION_ROOM = 2000  # bytes left for the question and the body around the state
LONG_MIN_SHARE = 0.75  # a long step that filled less is no evidence about the whole context
LONG_THRESHOLD = 0.9  # P(true) on the fact
CAL_REPS = 10  # filler lines of the calibration step (~3,300 characters)
PROBE_DEADLINE_S = 600.0  # steps not started by then are skipped (unobserved): a local probe holds the model
AUDIO_CLIP = ROOT / "app" / "assets" / "probe_audio_ru.wav"


class ProbeIn(BaseModel):
    """POST /api/probe and /api/probe/estimate."""

    model_config = ConfigDict(extra="forbid")

    engine_id: str
    long_context: bool = False  # CAL and LONG: slow on a local model, costs on a cloud one
    allow_no_zdr: bool = False  # the probe's text is synthetic; an engine with `zdr: false` still needs consent


class StoredProfile(BaseModel):
    """latest.json as read back: anything of another shape is no profile (a hand edit must not break the engine
    list, review P3)."""

    model_config = ConfigDict(extra="allow")

    engine_id: str
    kind: str
    model: str | None = None
    snapshot: str | None = None
    probed_at: str | None = None
    types: list[str] = []
    not_served: list[str] = []
    max_choice_options: int | None = None
    max_score_levels: int | None = None
    max_choice_probed: int | None = None
    max_score_probed: int | None = None
    media: list[str] = []
    reports_cost: bool | None = None
    reports_snapshot: bool | None = None
    reads_full_context: bool | None = None
    long_context_tokens: int | None = None
    observed: dict[str, dict[str, Any]] = {}
    run_id: str | None = None
    cost_usd: float | None = None


STATE = (
    "Оператор: Добрый день, компания «Ромашка», чем могу помочь?\n"
    "Клиент: Здравствуйте, я хочу вернуть деньги за бракованный чайник, заказ номер 72015.\n"
    "Оператор: Оформила возврат, деньги придут до 14 ноября 2026 года, сумма 2 490 рублей.\n"
    "Клиент: Спасибо, буду ждать."
)
FILLER = (
    "Оператор: Уточните, пожалуйста, адрес доставки. Клиент: Улица Ленина, дом пять, квартира двенадцать. "
    "Оператор: Спасибо, курьер приедет с десяти до двух. Клиент: Хорошо, а можно оплатить картой? "
    "Оператор: Да, у курьера есть терминал. Клиент: Отлично. Оператор: Ещё вопросы есть? Клиент: Пока нет.\n"
)
FACT = "Клиент: И последнее — запишите кодовое слово для доступа к счёту: «малиновый закат»."
FACT_Q = Question(type="noul", instructions="Назвал ли клиент кодовое слово для доступа к счёту?",
                  criteria={"true": "Клиент продиктовал кодовое слово.", "false": "Кодовое слово не прозвучало."})


def choice_of(n: int) -> Question:
    criteria = {f"o{i:03d}": f"Отдел номер {i}" for i in range(n)}
    criteria["o007"] = "Отдел возвратов: возврат денег за товар"
    return Question(type="choice", instructions="Какой отдел должен ответить клиенту?", criteria=criteria)


def score_of(levels: list[str]) -> Question:
    return Question(type="score", instructions="Насколько клиент доволен решением?", criteria=levels)


# step name → its question on the short state. The order is the order of calls.
CORE_STEPS: dict[str, Question] = {
    "N": Question(type="noul", instructions="Просит ли клиент вернуть деньги?",
                  criteria={"true": "Клиент просит вернуть деньги.", "false": "Клиент не просит вернуть деньги."}),
    "C": Question(type="choice", instructions="Какова тема звонка?",
                  criteria={"refund": "Возврат денег", "delivery": "Доставка", "other": "Другое"}),
    "S": score_of(["Недоволен", "Скорее недоволен", "Скорее доволен", "Доволен"]),
    "C24": choice_of(24),
    "C100": choice_of(100),
    "C240": choice_of(240),
    "S10": score_of([f"Уровень {i}" for i in range(10)]),
}
EXTENSION_STEPS: dict[str, Question] = {
    "M": Question(type="multi", instructions="Какие темы есть в звонке?",
                  criteria={"refund": "Возврат денег", "delivery": "Доставка", "complaint": "Жалоба", "other": "Другое"}),
    "T": Question(type="text", instructions="Какой номер заказа назвал клиент?"),
    "NUM": Question(type="number", instructions="Какую сумму вернут клиенту?", unit="RUB"),
    "D": Question(type="date", instructions="До какой даты вернут деньги?"),
}
TYPE_OF_STEP = {"N": "noul", "C": "choice", "S": "score", "M": "multi", "T": "text", "NUM": "number", "D": "date"}
CHOICE_SIZES = {"C": 3, "C24": 24, "C100": 100, "C240": 240}
SCORE_SIZES = {"S": 4, "S10": 10}
ALL_TYPES = ("noul", "choice", "score", "multi", "text", "number", "date")
STEP_OF_TYPE = {t: s for s, t in TYPE_OF_STEP.items()}
# what an OpenRouter decision model can serve at most (OpenRouterDecisions.default_profile; a test keeps the two
# equal): the catalog rows' "never" cells
CATALOG_TYPES = ("noul", "choice", "score")


def read(result: EngineResult, key: str = "q") -> str:
    """One probe request as whip-insight §5.3 reads it."""
    if key in result.answers or result.question_failures.get(key) == "refused":
        return SERVED  # a refusal answer proves the provider understood the type
    if result.question_failures.get(key) in ("unsupported", "invalid_answer"):
        return NOT_SERVED
    if result.request_failure == "refused" and result.http_status in (400, 422):
        return NOT_SERVED
    return UNOBSERVED


def has_body(result: EngineResult, key: str = "q") -> bool:
    """The provider answered with a body we read: an answer, a refusal answer, or an answer that was invalid."""
    return key in result.answers or result.question_failures.get(key) in ("refused", "invalid_answer")


def got_2xx(result: EngineResult) -> bool:
    """whip-insight's test for `no_profile`: did this request get a 2xx answer? An `unsupported` comes from a 400
    (or a local check that stands for one); a request failure got none, unless its status says otherwise."""
    return has_body(result) or (result.http_status is not None and 200 <= result.http_status < 300)


def size_limit(readings: dict[int, str], stored: int | None) -> int | None:
    """whip-insight §5.3 size rule, sizes ascending: served → supported; not served → unsupported with every larger
    size; unobserved → supported if the stored limit reaches it. The limit is the largest supported size."""
    supported: dict[int, bool] = {}
    sizes = sorted(readings)
    for i, s in enumerate(sizes):
        r = readings[s]
        if r == SERVED:
            supported[s] = True
        elif r == NOT_SERVED:
            for t in sizes[i:]:
                supported[t] = False
            break
        else:
            supported[s] = stored is not None and stored >= s
    ok = [s for s in sizes if supported.get(s)]
    return max(ok) if ok else None


def valid_engine_id(engine_id: str) -> bool:
    """A safe directory name under probes/: no separators, and not `.` or `..`."""
    return bool(ENGINE_ID.fullmatch(engine_id)) and set(engine_id) != {"."}


def safe_name(snapshot: str) -> str:
    name = re.sub(r"[^A-Za-z0-9._-]", "_", snapshot)[:200].strip(".") or "unknown"
    return f"{name}_" if name == "latest" else name  # never overwrite latest.json with a snapshot's file


class ProbeStore:
    """probes/<engine_id>/<snapshot>.json, and latest.json — the profile engines read."""

    def __init__(self, root: Path):
        self.root = root

    def _dir(self, engine_id: str) -> Path:
        if not valid_engine_id(engine_id):
            raise ValueError(f"engine id {engine_id!r} is not a safe directory name")
        return self.root / engine_id

    def latest(self, engine_id: str) -> dict[str, Any] | None:
        try:
            raw = json.loads((self._dir(engine_id) / "latest.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        try:
            return StoredProfile.model_validate(raw).model_dump()
        except ValidationError as e:
            log.warning("probes/%s/latest.json is not a profile (%d errors): ignored", engine_id, e.error_count())
            return None

    def save(self, profile: dict[str, Any]) -> Path:
        d = self._dir(profile["engine_id"])
        d.mkdir(parents=True, exist_ok=True)
        text = json.dumps(profile, ensure_ascii=False, indent=1)
        path = d / f"{safe_name(profile.get('snapshot') or 'unknown')}.json"
        for target in (path, d / "latest.json"):
            write_durably(target, text)  # a power loss must not leave an empty latest.json (the default is wider)
        return path


def dominant_failure(steps: dict[str, EngineResult]) -> str | None:
    """The most common way the probe's requests failed (whip-insight's `failure` of a `no_profile`)."""
    counts = Counter(r.request_failure or r.question_failures.get("q") for r in steps.values() if not has_body(r))
    counts.pop(None, None)
    return counts.most_common(1)[0][0] if counts else None


def build_profile(engine: BaseEngine, steps: dict[str, EngineResult], stored: dict[str, Any] | None,
                  long_capped: bool = False) -> dict | None:
    """The profile the observations support, or None when no request got a 2xx answer (whip-insight: `no_profile`).
    `stored` is this engine's previous profile on the same model (BaseEngine.probed)."""
    if not any(got_2xx(r) for r in steps.values()):
        return None
    readings = {name: read(r) for name, r in steps.items()}
    stored = stored or {}
    stored_types, stored_no = set(stored.get("types") or []), set(stored.get("not_served") or [])
    types: list[str] = []
    no: list[str] = []  # decisively not served: the matrix keeps showing ✗ while later probes do not observe it
    for step, kind in TYPE_OF_STEP.items():
        r = readings.get(step)
        if r == SERVED:
            types.append(kind)
        elif r == NOT_SERVED:
            no.append(kind)
        elif kind in stored_types:  # unobserved, or not asked (no extensions on a cloud engine): the stored value
            types.append(kind)
        elif kind in stored_no:
            no.append(kind)
    a = readings.get("A")
    if a == SERVED:
        heard = True
    elif a == NOT_SERVED:
        heard = False
        no.append("audio")
    else:
        heard = "audio" in (stored.get("media") or [])
        if not heard and "audio" in stored_no:
            no.append("audio")
    choice = size_limit({CHOICE_SIZES[s]: readings[s] for s in CHOICE_SIZES if s in readings},
                        stored.get("max_choice_options")) if "choice" in types else None
    score = size_limit({SCORE_SIZES[s]: readings[s] for s in SCORE_SIZES if s in readings},
                       stored.get("max_score_levels")) if "score" in types else None
    # whip-insight: the first dated `model` seen, else the config's model
    snapshot = next((r.snapshot for r in steps.values() if r.snapshot and DATED.search(r.snapshot)), None) \
        or engine.spec.model or engine.spec.repo
    # the long-context reading belongs to one snapshot: never carried over to a new one (review P3)
    same = stored.get("snapshot") == snapshot
    reads_full = stored.get("reads_full_context") if same else None
    long_tokens = stored.get("long_context_tokens") if same else None
    long = steps.get("LONG")
    long_measured = False
    if long is not None and "q" in long.answers:
        tokens = long.usage.input_tokens
        long_measured = long_capped or (tokens is not None and tokens >= LONG_MIN_SHARE * engine.context_limit_tokens())
        if long_measured:
            reads_full = long.answers["q"].probabilities.get("true", 0) >= LONG_THRESHOLD
            long_tokens = tokens
    bodies = [r for r in steps.values() if has_body(r)]
    return {
        "engine_id": engine.spec.id,
        "kind": engine.kind,
        "model": engine.spec.model or engine.spec.repo,
        "snapshot": snapshot,
        "probed_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "types": types,
        "not_served": no,
        "max_choice_options": choice,
        "max_score_levels": score,
        # the largest sizes tried: a limit that reaches one means "at least", and the engine keeps its ceiling
        "max_choice_probed": max(CHOICE_SIZES.values()),
        "max_score_probed": max(SCORE_SIZES.values()),
        "media": ["audio"] if heard else [],
        "reports_cost": bool(bodies) and all(r.usage.cost_source == "provider" for r in bodies),
        "reports_snapshot": bool(bodies) and all(bool(r.snapshot and DATED.search(r.snapshot)) for r in bodies),
        "reads_full_context": reads_full,
        "long_context_tokens": long_tokens,  # how much the long step read: ✓ means "all of these tokens"
        "observed": {
            name: {"reading": readings[name], "latency_ms": r.latency_ms, "request_failure": r.request_failure,
                   "question_failure": r.question_failures.get("q"), "http_status": r.http_status,
                   "detail": r.detail, "input_tokens": r.usage.input_tokens,
                   **({"measured": long_measured, "capped": long_capped} if name == "LONG" else {})}
            for name, r in steps.items()
        },
        "app_version": __version__,
    }


def token_fit(n: EngineResult | None, n_req: DecisionRequest, cal: EngineResult | None,
              cal_req: DecisionRequest) -> tuple[float, float] | None:
    """tokens = overhead + ratio × characters, from N (the short state) and CAL (filler) — the same question, so the
    slope is the filler's own ratio and the template's tokens drop out. None without both counts or with an
    implausible slope. (N alone overstated the ratio by the template: 80 % of the context instead of 90 %.)"""
    if n is None or cal is None or not n.usage.input_tokens or not cal.usage.input_tokens:
        return None
    ratio = (cal.usage.input_tokens - n.usage.input_tokens) / (cal_req.text_size() - n_req.text_size())
    if not math.isfinite(ratio) or not 0.05 <= ratio <= 4.0:
        return None
    return ratio, max(0.0, n.usage.input_tokens - ratio * n_req.text_size())


def long_state(engine: BaseEngine, fit: tuple[float, float], scale: float = 1.0) -> tuple[str, bool]:
    """Filler up to LONG_SHARE of the engine's context by the token fit, with the fact in the last 5 %; capped by the
    bytes the engine can carry (its `max_request_bytes`; OpenRouter's 250,000 bytes of text), counted in UTF-8 with
    room for the question. `scale` shrinks the attempt (the retry after `too_long`). Returns the state and whether
    the byte cap held it back."""
    ratio, overhead = fit
    reps = int(((LONG_SHARE * engine.context_limit_tokens() - overhead) / ratio - len(FACT)) // len(FILLER))
    capped = False
    if limit := engine.max_text_bytes():
        by_bytes = (limit - QUESTION_ROOM - len(FACT.encode())) // len(FILLER.encode())
        if by_bytes < reps:
            reps, capped = by_bytes, True
    return FILLER * max(1, int(reps * scale)) + FACT, capped


def long_ceiling_tokens(engine: BaseEngine) -> int:
    """At most this many tokens in the long step (a token is at least a byte): what the estimate reserves."""
    return min(engine.context_limit_tokens(), engine.max_text_bytes() or 10**12)


class Prober:
    """Runs the probe set on one engine, under the budget, and saves the run and the profile. One probe per engine
    at a time."""

    def __init__(self, store: Store, budget: Budget, probes: ProbeStore, media_dir: Path):
        self.store, self.budget, self.probes, self.media_dir = store, budget, probes, media_dir
        self.busy: set[str] = set()

    def plan(self, engine: BaseEngine, long_context: bool) -> list[str]:
        names = list(CORE_STEPS)
        ceiling = engine.default_profile()
        names += [s for s, q in EXTENSION_STEPS.items() if q.type in ceiling.types]
        if "audio" in ceiling.media:
            names.append("A")
        if long_context:
            names += ["CAL", "LONG"]
        return names

    def request(self, name: str) -> DecisionRequest:
        """Every step but LONG, which `long_step` sizes from the earlier answers."""
        if name in CORE_STEPS:
            return DecisionRequest(state=STATE, questions={"q": CORE_STEPS[name]})
        if name in EXTENSION_STEPS:
            return DecisionRequest(state=STATE, questions={"q": EXTENSION_STEPS[name]})
        if name == "A":
            return DecisionRequest(state=None, media=[{"type": "audio", "path": f"probe/{AUDIO_CLIP.name}"}],
                                   questions={"q": Question(type="noul", instructions="Говорит ли на записи человек?")})
        if name == "CAL":
            return DecisionRequest(state=FILLER * CAL_REPS, questions={"q": CORE_STEPS["N"]})
        raise KeyError(name)

    def estimate(self, engine: BaseEngine, long_context: bool) -> float:
        """What the probe reserves: every short step by the engine's estimator, and LONG at its token ceiling."""
        total = sum(engine.estimate_cost(self.request(n)) or 0.0 for n in self.plan(engine, long_context) if n != "LONG")
        if long_context:
            total += engine.cost_of_tokens(long_ceiling_tokens(engine))
        return total

    def ensure_clip(self) -> None:
        target = self.media_dir / "probe" / AUDIO_CLIP.name
        if not target.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(AUDIO_CLIP, target)

    @staticmethod
    async def call(engine: BaseEngine, req: DecisionRequest, opts: RunOptions) -> EngineResult:
        """engine.run, guarded as the runner guards it: an engine bug is one step's `provider_error`, and the steps
        already paid for are still saved and charged (review P3)."""
        try:
            return await engine.run(req, opts)
        except Exception as e:
            log.exception("probe: engine %s crashed", engine.spec.id)
            return EngineResult.build(engine.spec.id, ["q"], {}, {}, request_failure="provider_error",
                                      detail=f"engine error: {type(e).__name__}")

    async def long_step(self, engine: BaseEngine, steps: dict[str, EngineResult], opts: RunOptions,
                        reserve_more: Callable[[float], bool]) -> bool:
        """LONG, sized by the token fit of N and CAL; after `too_long`, once more and smaller — reserved again, since
        the refused attempt may be billed. Fills steps["LONG"] (and "LONG.1", the refused attempt). Returns whether the
        engine's byte limit held the state back."""
        fit = token_fit(steps.get("N"), self.request("N"), steps.get("CAL"), self.request("CAL"))
        if fit is None:
            steps["LONG"] = EngineResult.build(engine.spec.id, ["q"], {}, {}, request_failure="skipped",
                                               detail="no token counts from N and CAL to size the long state")
            return False
        state, capped = long_state(engine, fit)
        res = await self.call(engine, DecisionRequest(state=state, questions={"q": FACT_Q}), opts)
        if res.request_failure == "too_long" and reserve_more(engine.cost_of_tokens(
                int(LONG_RETRY_SHARE * long_ceiling_tokens(engine)))):
            steps["LONG.1"] = res
            state, capped = long_state(engine, fit, LONG_RETRY_SHARE / LONG_SHARE)
            res = await self.call(engine, DecisionRequest(state=state, questions={"q": FACT_Q}), opts)
        steps["LONG"] = res
        return capped

    async def run(self, engine: BaseEngine, emit: Emit, long_context: bool = False,
                  allow_no_zdr: bool = False) -> dict[str, Any] | None:
        engine_id = engine.spec.id
        if engine_id in self.busy:  # the API answers 409 first; this closes the race between two requests
            await emit("probe_error", {"message": f"a probe of {engine_id} is already running"})
            return None
        self.busy.add(engine_id)
        try:
            return await self._run(engine, emit, long_context, RunOptions(allow_no_zdr=allow_no_zdr))
        finally:
            self.busy.discard(engine_id)

    async def _run(self, engine: BaseEngine, emit: Emit, long_context: bool, opts: RunOptions) -> dict | None:
        engine_id = engine.spec.id
        names = self.plan(engine, long_context)
        estimate = self.estimate(engine, long_context)
        if estimate > 0 and not self.budget.reserve(estimate):
            await emit("budget_stop", {"estimate_usd": estimate, **self.budget.status()})
            return None
        reserved = estimate
        steps: dict[str, EngineResult] = {}
        saved = False

        def reserve_more(amount: float) -> bool:
            nonlocal reserved
            if amount > 0 and not self.budget.reserve(amount):
                return False
            reserved += max(0.0, amount)
            return True

        def spent() -> float:
            return sum(r.usage.cost_usd or 0.0 for r in steps.values())

        try:
            run_id, t0 = new_id(), time.time()
            await emit("probe_started", {"run_id": run_id, "engine_id": engine_id, "steps": names,
                                         "estimate_usd": estimate})
            stored = engine.probed()
            long_capped = False
            for name in names:
                if time.time() - t0 > PROBE_DEADLINE_S:
                    steps[name] = EngineResult.build(engine_id, ["q"], {}, {}, request_failure="skipped",
                                                     detail=f"the probe's {PROBE_DEADLINE_S:.0f} s deadline passed")
                elif name == "LONG":
                    long_capped = await self.long_step(engine, steps, opts, reserve_more)
                else:
                    if name == "A":
                        self.ensure_clip()
                    steps[name] = await self.call(engine, self.request(name), opts)
                    if steps[name].request_failure == "too_long":
                        log.warning("probe %s: step %s is too long on a short state — check the engine's limits "
                                    "in config.yaml", engine_id, name)
                res = steps[name]
                await emit("step_done", {"engine_id": engine_id, "step": name, "reading": read(res),
                                         "latency_ms": res.latency_ms, "detail": res.detail,
                                         "cost_usd": res.usage.cost_usd})
            error = None
            try:
                profile = build_profile(engine, steps, stored, long_capped)
            except Exception as e:  # the record is still saved: its calls were paid for
                log.exception("probe %s: the profile could not be built", engine_id)
                profile, error = None, f"profile: {type(e).__name__}: {e}"[:500]
            cost = spent()
            record = {
                "id": run_id,
                "created_at": datetime.fromtimestamp(t0, UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "day": local_day(t0),
                "kind": "probe",
                "title": f"probe {engine_id}",
                "tags": [],  # the kind already says "probe" (History shows it once)
                "request": {"questions": {}},
                "probe": {"engine_id": engine_id, "steps": list(steps), "long_context": long_context,
                          "allow_no_zdr": opts.allow_no_zdr},
                "engines": [{"engine_id": engine_id, "kind": engine.kind,
                             "model": engine.spec.model or engine.spec.repo, "zdr": engine.spec.zdr}],
                "results": [{**r.model_dump(mode="json"), "engine_id": f"{engine_id}#{name}"}
                            for name, r in steps.items()],
                "estimate_usd": estimate,
                "cost_usd": cost,
                "versions": {"app": __version__, "mlx_vlm": mlx_vlm_version(),
                             "models": {engine_id: profile["snapshot"]} if profile else {}},
                "profile": profile,
                "error": error,
            }
            self.store.save(record)
            saved = True
        finally:
            if not saved:  # an exception or a cancel after paid calls: the money still counts (review P3)
                self.budget.charge_unsaved(spent())
            self.budget.release(reserved)
        if error:
            await emit("probe_error", {"run_id": run_id, "message": error, "budget": self.budget.status()})
            return None
        if profile is not None:
            profile["run_id"], profile["cost_usd"] = run_id, cost
            self.probes.save(profile)
        await emit("probe_done", {"run_id": run_id, "engine_id": engine_id, "profile": profile, "cost_usd": cost,
                                  "no_profile": profile is None,
                                  "failure": dominant_failure(steps) if profile is None else None,
                                  "budget": self.budget.status()})
        return profile


def cell(kind_types: list[str], probe: dict | None, capability: str) -> str:
    """A matrix cell (§13.2): yes / no / unchecked, or never when the engine kind cannot serve it at all (multi on
    OpenRouter is dropped silently, SPIKE §3.2). "no" stays until a probe serves it (`not_served`)."""
    if capability not in kind_types:
        return "never"
    if probe is None:
        return "unchecked"
    served = "audio" in (probe.get("media") or []) if capability == "audio" else capability in (probe.get("types") or [])
    if served:
        return "yes"
    step = "A" if capability == "audio" else STEP_OF_TYPE.get(capability)
    if capability in (probe.get("not_served") or []) or \
            (probe.get("observed") or {}).get(step, {}).get("reading") == NOT_SERVED:
        return "no"
    return "unchecked"


def newer_snapshot(rows: list[dict[str, Any]], engine_id: str, probe: dict[str, Any]) -> str | None:
    """SPEC §5.6.2 item 4: the latest run since the probe answered as another dated snapshot → the profile is stale
    until the next probe."""
    def since(row: dict[str, Any]) -> bool:  # ULIDs order by the millisecond; created_at only by the second
        if probe.get("run_id"):
            return str(row.get("id") or "") > probe["run_id"]
        return (row.get("created_at") or "") > (probe.get("probed_at") or "")

    for row in reversed(rows):
        snap = (row.get("snapshots") or {}).get(engine_id)
        if row.get("kind") == "probe" or not snap or not since(row):
            continue
        if snap != probe.get("snapshot") and DATED.search(snap):
            return f"run {row['id']} answered as {snap}; the probe saw {probe.get('snapshot')}"
        return None
    return None


def matrix_rows(registry: Any, probes: ProbeStore, rows: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Configured decision engines with their latest probe, then the catalog's decision models nobody configured."""
    catalog = registry.ctx.catalog
    out, seen = [], set()
    for e in registry.engines.values():
        if e.kind not in PROBED_KINDS:
            continue
        ok, reason = e.available()
        model = e.spec.model or e.spec.repo
        raw = probes.latest(e.spec.id) if valid_engine_id(e.spec.id) else None
        probe = e.probed()
        if raw and probe is None:
            stale = f"probed on {raw.get('kind')} {raw.get('model')}; config.yaml now names {e.kind} {model}"
        else:
            stale = newer_snapshot(rows or [], e.spec.id, probe) if probe else None
        default = e.default_profile()
        ceiling = list(default.types) + (["audio"] if "audio" in default.media else [])
        cloud = e.kind == "openrouter_decisions"
        m = catalog.model(e.spec.model) if cloud else None
        seen.add(e.spec.model)
        price = catalog.price_per_token(e.spec.model) if cloud else 0.0  # None: not in the catalog — unknown, not free

        def get(key: str) -> Any:
            return probe.get(key) if probe else None

        out.append({
            "id": e.spec.id, "source": "engine", "kind": e.kind, "model": model, "zdr": e.spec.zdr,
            "available": ok, "reason": reason, "stale": stale,
            "cells": {c: cell(ceiling, probe, c) for c in (*ALL_TYPES, "audio")},
            "max_choice_options": get("max_choice_options"),
            "max_choice_at_top": bool(probe and get("max_choice_options") and get("max_choice_probed")
                                      and get("max_choice_options") >= get("max_choice_probed")),
            "max_score_levels": get("max_score_levels"),
            "max_score_at_top": bool(probe and get("max_score_levels") and get("max_score_probed")
                                     and get("max_score_levels") >= get("max_score_probed")),
            "reports_cost": get("reports_cost"),
            "reports_snapshot": get("reports_snapshot"),
            "reads_full_context": get("reads_full_context"),
            "long_context_tokens": get("long_context_tokens"),
            "snapshot": get("snapshot"),
            "probed_at": get("probed_at"),
            "probe_cost_usd": get("cost_usd"),
            "context_length": (m or {}).get("context_length") or (None if cloud else e.context_limit_tokens()),
            "price_per_mtok": None if price is None else round(price * 1e6, 4),
            "input_modalities": ((m or {}).get("architecture") or {}).get("input_modalities"),
            "expiration_date": (m or {}).get("expiration_date"),
            "zdr_endpoint": catalog.zdr_endpoint(e.spec.model) if m else None,
        })
    for m in catalog.data.get("decisions", []):
        if m.get("id") in seen:
            continue
        price = catalog.price_per_token(m.get("id"))
        out.append({
            "id": m.get("id"), "source": "catalog", "kind": "openrouter_decisions", "model": m.get("id"),
            "available": None, "reason": "not configured in config.yaml", "stale": None,
            "cells": {c: cell(list(CATALOG_TYPES), None, c) for c in (*ALL_TYPES, "audio")},
            "context_length": m.get("context_length"),
            "price_per_mtok": None if price is None else round(price * 1e6, 4),
            "input_modalities": (m.get("architecture") or {}).get("input_modalities"),
            "expiration_date": m.get("expiration_date"),
            "zdr_endpoint": catalog.zdr_endpoint(m.get("id")),
        })
    return out
