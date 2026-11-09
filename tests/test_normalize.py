"""§21 normalization and contract: recorded P0 answers → Answer; bad answers → invalid_answer."""

from __future__ import annotations

import glob
import json
from pathlib import Path

import pytest

from app.engines.base import normalize_answer
from app.schemas import Answer, DecisionRequest, Question
from tests.conftest import FIX, load

CORE = DecisionRequest.model_validate(load("requests/core_ru.json"))
EXT = DecisionRequest.model_validate(load("requests/extensions_ru.json"))


def recorded_200() -> list[Path]:
    return [Path(p) for p in sorted(glob.glob(str(FIX / "openrouter" / "*" / "*.json")))
            if json.loads(Path(p).read_text()).get("status") == 200 and "/chat/" not in p]


@pytest.mark.parametrize("path", recorded_200(), ids=lambda p: f"{p.parent.name}/{p.stem}")
def test_every_recorded_openrouter_answer_normalizes(path: Path):
    rec = json.loads(path.read_text())
    # P0 also sent `box` and `multiple`, which the internal contract does not carry: OpenRouter answered `multiple`
    # with a plain choice (SPIKE §3.2), so the flag is dropped and the answer read as a choice.
    questions = {k: {f: v for f, v in q.items() if f != "multiple"}
                 for k, q in rec["request"]["questions"].items() if q["type"] != "box"}
    if not questions:
        pytest.skip("box only")
    req = DecisionRequest.model_validate({"state": rec["request"]["state"], "questions": questions})
    for key, raw in rec["body"]["answers"].items():
        if key not in req.questions:
            continue
        q = req.questions[key]
        a = normalize_answer(q, raw)
        assert isinstance(a, Answer), (key, a)
        assert set(a.probabilities) == set(q.options())
        assert a.top in q.options()
        if q.type in ("choice", "score"):
            assert abs(sum(a.probabilities.values()) - 1) <= 0.001


def test_noul_maps_to_true_false():
    a = normalize_answer(CORE.questions["callback_agreed"], {"type": "noul", "noul": 0.3})
    assert a.probabilities == {"true": 0.3, "false": 0.7}
    assert a.top == "false" and a.top_p == 0.7


def test_score_expected_is_computed_not_taken():
    raw = or_raw = load("openrouter/liquid__d1.1/core_ru_0.json")["body"]["answers"]["interest"]
    a = normalize_answer(CORE.questions["interest"], raw)
    assert a.expected == pytest.approx(sum(int(k) * p for k, p in raw["probabilities"].items()))
    assert a.top == "2"
    assert or_raw["score"] == pytest.approx(a.expected, abs=1e-6)  # OpenRouter's score is the weighted index


def test_local_d1_median_score_is_ignored_in_favour_of_expected():
    raw = load("mlx/mlx-d1-2b/core_ru.json")["response"]["answers"]["interest"]
    assert raw["score"] == 2.0  # system_one's default readout: the median level
    a = normalize_answer(CORE.questions["interest"], raw)
    assert a.expected == pytest.approx(1.825, abs=0.001)


def test_vendor_confidence_is_kept_but_separate():
    raw = load("openrouter/liquid__d1.1/core_ru_0.json")["body"]["answers"]["call_topic"]
    a = normalize_answer(CORE.questions["call_topic"], raw)
    assert a.vendor_confidence == pytest.approx(raw["confidence"])
    assert a.top_p == pytest.approx(raw["probabilities"]["deposit"])
    assert a.top_p != a.vendor_confidence


@pytest.mark.parametrize("engine", ["mlx-d1-2b", "mlx-d1-omni"])
def test_local_extensions_normalize(engine):
    resp = load(f"mlx/{engine}/extensions_ru.json")["response"]["answers"]
    products = normalize_answer(EXT.questions["products"], resp["products"])
    assert products.type == "multi" and products.probabilities["deposit"] > 0.5
    app_id = normalize_answer(EXT.questions["application_id"], resp["application_id"])
    assert app_id.extract.value == "А-48213"
    i, j = app_id.extract.span
    assert EXT.state[i:j] == "А-48213"
    est = normalize_answer(EXT.questions["amount_estimate"], resp["amount_estimate"])
    assert est.extract.interval is not None and est.extract.value is not None
    amount = normalize_answer(EXT.questions["amount"], resp["amount"])
    assert amount.extract.value is None and "тысяч" in amount.extract.text  # P0: mlx-vlm cannot parse it


BAD = [
    ("noul above 1", "callback_agreed", {"type": "noul", "noul": 1.2}),
    ("noul missing", "callback_agreed", {"type": "noul"}),
    ("noul as bool", "callback_agreed", {"type": "noul", "noul": True}),
    ("choice sum 0.9", "call_topic", {"type": "choice", "choice": "credit",
                                      "probabilities": {"credit": 0.5, "deposit": 0.3, "complaint": 0.1}}),
    ("choice unknown option", "call_topic", {"type": "choice", "choice": "x",
                                             "probabilities": {"credit": 1, "deposit": 0, "complaint": 0}}),
    ("choice option missing", "call_topic", {"type": "choice", "probabilities": {"credit": 1, "deposit": 0}}),
    ("choice extra option", "call_topic", {"type": "choice", "probabilities": {"credit": 1, "deposit": 0, "complaint": 0, "x": 0}}),
    ("choice without probabilities", "call_topic", {"type": "choice", "choice": "credit"}),
    ("choice as multiple", "call_topic", {"type": "choice", "choices": ["credit"],
                                          "probabilities": {"credit": 1, "deposit": 0, "complaint": 0}}),
    ("score negative", "interest", {"type": "score", "probabilities": {"0": -0.1, "1": 0.6, "2": 0.5, "3": 0}}),
    ("score wrong levels", "interest", {"type": "score", "probabilities": {"0": 0.5, "1": 0.5}}),
    ("score NaN", "interest", {"type": "score", "probabilities": {"0": float("nan"), "1": 0, "2": 1, "3": 0}}),
    ("not a dict", "interest", ["score"]),
]


@pytest.mark.parametrize("name,key,raw", BAD, ids=[b[0] for b in BAD])
def test_bad_answers_are_invalid(name, key, raw):
    assert normalize_answer(CORE.questions[key], raw) == "invalid_answer"


def test_refusal_is_refused():
    assert normalize_answer(CORE.questions["call_topic"], {"type": "refusal"}) == "refused"


def test_rounded_jev_probabilities_pass():
    q = Question(type="choice", instructions="?", criteria={"a": None, "b": None, "c": None})
    a = normalize_answer(q, {"type": "choice", "choice": "a", "probabilities": {"a": 0.33, "b": 0.33, "c": 0.3405}})
    assert isinstance(a, Answer)  # sum 1.0005 is inside ± 0.001
