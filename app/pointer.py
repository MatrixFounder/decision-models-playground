"""The turn pointer (DESIGN §14, UC-8): which turn of a transcript shows the answer.

The rules are whip-insight's (SPEC-decision-service §8.2.4 and the route's `decision-run.js`), so a measurement here
holds there:

1. The transcript's turns are `t0…t(N-1)`, numbered from 0 as whip-insight numbers turns (`turns.js`), one per
   non-empty line; «Клиент: …» names the participant, and a bare «Клиент:» line names the speaker of the next line.
   The state of every call is `{"fragments": [{"turn", "participant", "text"}]}`, the main questions' too: a pointer
   cites a turn the model was given. whip-insight's own windows state is taken as it is: a turn may repeat (one
   fragment per window), and the options are its distinct turns in turn order (`detect.js` `fragmentTurns`).
2. After the answer, the pointer asks for the preferred answer only: a `noul` whose P(true), rounded to three
   decimals, is ≥ 0.5 («да»; a «нет» asks nothing); the most probable option of a `choice` or level of a `score`.
3. The instruction is «В какой реплике видно, что <question>?» for a `noul` and «В какой реплике видно:
   <description>?» for an option or a level; the options are the turns, with null descriptions.
4. One turn is not asked: it is cited with probability None.
5. B = min(240, the engine's `max_choice_options`). Up to B turns, one `choice` over them. Past B, two passes: pass 1
   over blocks of ceil(N / B) consecutive turns, each named by its first turn; pass 2 over the chosen block. The
   probability of a two-pass turn is the product of the two passes'. Past B² turns the whole request is `too_long`
   and makes no call, as the route refuses it at step 1 (the runner applies it per engine, since B is the engine's).

The route points `boolean` questions only; §8.2.4 items 4–5 describe `choice` and `score` too, and the playground asks
them, so the floor of §8.2.4 item 10 can be measured on every type.
"""

from __future__ import annotations

import json
import math
import re
from typing import Any

from app.schemas import MAX_CHOICE_OPTIONS, Answer, DecisionRequest, Question

TURN_CEILING = 240  # whip-insight's B: TypeSafe's choice "works reliably up to roughly 240 options" (audit §4)
POINTED = ("noul", "choice", "score")
TURN_RE = re.compile(r"^t(0|[1-9][0-9]{0,5})$")
# «Клиент: текст», «participant01: текст», «Оператор (Анна): текст»: a short label before the first colon
SPEAKER_RE = re.compile(r"^([^:\n]{1,40}?)\s*:\s+(\S.*)$")
BARE_SPEAKER_RE = re.compile(r"^([^:\n]{1,40}?)\s*:$")  # «Оператор:» alone: the speaker of the next line
MAX_TURNS = TURN_CEILING * TURN_CEILING


class PointerError(ValueError):
    """The request cannot run with the pointer: the API answers 400 before streaming."""


def turns_of(text: str) -> list[dict[str, Any]]:
    """The transcript's turns: one per non-empty line (split on \\n only, not on U+2028 and the like), `t0…` in order;
    the participant when the line names one, or when the line before it is a bare «Имя:»."""
    out: list[dict[str, Any]] = []
    pending: str | None = None  # a bare «Имя:» line waiting for its words
    for raw in text.split("\n"):
        line = raw.strip()
        if not line:
            continue
        bare = BARE_SPEAKER_RE.match(line)
        if bare:
            if pending is not None:  # two bare labels in a row: the first one said nothing
                out.append({"turn": f"t{len(out)}", "participant": None, "text": f"{pending}:"})
            pending = bare.group(1).strip()
            continue
        m = SPEAKER_RE.match(line)
        if m:
            speaker, said = m.group(1).strip(), m.group(2).strip()
        else:
            speaker, said = pending, line
        pending = None
        out.append({"turn": f"t{len(out)}", "participant": speaker, "text": said})
    if pending is not None:
        out.append({"turn": f"t{len(out)}", "participant": None, "text": f"{pending}:"})
    return out


def turn_ids(frags: list[dict[str, Any]]) -> list[str]:
    """The pointer's options: the distinct turns, in turn order (`detect.js` `fragmentTurns`)."""
    return [f"t{n}" for n in sorted({int(f["turn"][1:]) for f in frags})]


def fragments_of(state: Any) -> list[dict[str, Any]]:
    """The fragments of a pointer run: a transcript is split into turns; a `{"fragments": [...]}` state (a repeated
    run, or whip-insight's own windows) is checked and taken as it is."""
    if isinstance(state, str):
        frags = turns_of(state)
    elif isinstance(state, dict) and set(state) == {"fragments"} and isinstance(state["fragments"], list):
        frags = state["fragments"]
        for f in frags:
            if (not isinstance(f, dict) or set(f) - {"turn", "participant", "text"}
                    or not isinstance(f.get("turn"), str) or not TURN_RE.fullmatch(f["turn"])
                    or not isinstance(f.get("text"), str) or not f["text"].strip()
                    or f.get("participant") is not None and not isinstance(f["participant"], str)):
                raise PointerError('each fragment is {"turn": "t<n>", "participant": text or null, "text": text}')
    else:
        raise PointerError('the pointer reads a transcript (text) or {"fragments": [...]}')
    if not frags:
        raise PointerError("the transcript has no turns")
    n = len(turn_ids(frags))
    if n > MAX_TURNS:
        raise PointerError(f"{n} turns: the pointer reads at most {MAX_TURNS} (B² with B = {TURN_CEILING})")
    return frags


def prepare(req: DecisionRequest) -> tuple[DecisionRequest, list[str]]:
    """The request every call of a pointer run sends, and the turn ids the pointer chooses among."""
    if req.media:
        raise PointerError("the pointer reads the turns of a transcript: remove the media")
    if not any(q.type in POINTED for q in req.questions.values()):
        raise PointerError("no question the pointer can serve: it points noul, choice and score answers")
    frags = fragments_of(req.state)
    state = {"fragments": [{"turn": f["turn"], "participant": f.get("participant"), "text": f["text"]} for f in frags]}
    return DecisionRequest(state=state, questions=req.questions), turn_ids(frags)


def width(max_choice_options: int | None) -> int:
    """B = min(240, the engine's max_choice_options)."""
    return min(TURN_CEILING, max_choice_options or MAX_CHOICE_OPTIONS)


def blocks(turns: list[str], b: int) -> list[list[str]]:
    """Pass 1's blocks: ceil(N / B) consecutive turns each, the last one shorter."""
    size = math.ceil(len(turns) / b)
    return [turns[i:i + size] for i in range(0, len(turns), size)]


def passes(n: int, b: int) -> int:
    """The calls a pointer over n turns makes at most: none for one turn or past B², one up to B, two past B."""
    if n <= 1 or n > b * b:
        return 0
    return 1 if n <= b else 2


def _text(instructions: Any) -> str:
    return instructions if isinstance(instructions, str) else json.dumps(instructions, ensure_ascii=False)


def _ask(prefix: str, what: str) -> str:
    # as the route builds it: the question as written, «?» added unless it already ends with one (detect.js)
    return prefix + what + ("" if re.search(r"\?\s*$", what) else "?")


def description(q: Question, option: str) -> str:
    """What an option or a level says: its description, else its id (a choice may leave descriptions null)."""
    c = q.criteria
    if q.type == "score" and isinstance(c, list):
        return c[int(option)]
    if isinstance(c, dict):
        return c.get(option) or option
    return option


def instruction(q: Question, a: Answer) -> str | None:
    """The pointer's instruction for this answer, or None when the answer asks no pointer (a «нет», another type)."""
    if q.type == "noul":
        p = a.probabilities.get("true")
        if p is None or math.floor(p * 1000 + 0.5) / 1000 < 0.5:  # the route's Math.round(p * 1000) / 1000 (D-32)
            return None
        return _ask("В какой реплике видно, что ", _text(q.instructions))
    if q.type in ("choice", "score") and a.top is not None:
        return _ask("В какой реплике видно: ", description(q, a.top))
    return None


def longest_instruction(q: Question) -> str:
    """The longest instruction a question's pointer can carry: what the estimate prices before the answer is known."""
    if q.type == "noul":
        return _ask("В какой реплике видно, что ", _text(q.instructions))
    return max((_ask("В какой реплике видно: ", description(q, o)) for o in q.options()), key=len)


def pass_request(state: dict, key: str, text: str, candidates: list[str]) -> DecisionRequest:
    """One pass: a `choice` over the candidate turns with null descriptions, on the run's state."""
    q = Question(type="choice", instructions=text, criteria={t: None for t in candidates})
    return DecisionRequest(state=state, questions={key: q})


def worst_passes(state: dict, key: str, q: Question, turns: list[str], b: int) -> list[DecisionRequest]:
    """The pass requests the estimate prices: the largest each pass can be, with the longest instruction."""
    n = passes(len(turns), b)
    if n == 0:
        return []
    text = longest_instruction(q)
    if n == 1:
        return [pass_request(state, key, text, turns)]
    bl = blocks(turns, b)
    widest = max(bl, key=len)
    return [pass_request(state, key, text, [x[0] for x in bl]), pass_request(state, key, text, widest)]
