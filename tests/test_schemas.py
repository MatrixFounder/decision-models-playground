"""§6.1 request validation."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas import DecisionRequest, EngineResult, Question


def q(**kw):
    return Question.model_validate({"instructions": "?", **kw})


@pytest.mark.parametrize("kw", [
    {"type": "noul"},
    {"type": "noul", "criteria": {"true": "yes", "false": "no"}},
    {"type": "choice", "criteria": {"a": "A", "b": None}},
    {"type": "choice", "criteria": {f"o{i}": None for i in range(255)}},
    {"type": "score", "criteria": ["low", "high"]},
    {"type": "score", "criteria": [str(i) for i in range(10)]},
    {"type": "multi", "criteria": {"a": None, "b": None}},
    {"type": "number", "range": [0, 10], "step": 1, "unit": "RUB"},
    {"type": "date"},
])
def test_valid_questions(kw):
    q(**kw)


@pytest.mark.parametrize("kw", [
    {"type": "noul", "criteria": {"yes": "x"}},
    {"type": "choice", "criteria": {"a": "A"}},
    {"type": "choice", "criteria": {f"o{i}": None for i in range(256)}},
    {"type": "choice", "criteria": {"Bad-Id": None, "b": None}},
    {"type": "choice", "criteria": ["a", "b"]},
    {"type": "score", "criteria": ["one"]},
    {"type": "score", "criteria": [str(i) for i in range(11)]},
    {"type": "text", "criteria": {"a": None}},
    {"type": "number", "range": [10, 0]},
    {"type": "noul", "range": [0, 1]},
    {"type": "number", "step": 1},
    {"type": "box"},
    {"type": "noul", "instructions": "   "},
    {"type": "noul", "instructions": {}},
    {"type": "noul", "instructions": []},
])
def test_invalid_questions(kw):
    with pytest.raises(ValidationError):
        q(**kw)


def test_request_keys_and_counts():
    ok = {"type": "noul", "instructions": "?"}
    DecisionRequest(state="x", questions={"a1": ok})
    for questions in ({}, {"1a": ok}, {"A": ok}, {f"q{i}": ok for i in range(65)}):
        with pytest.raises(ValidationError):
            DecisionRequest(state="x", questions=questions)
    with pytest.raises(ValidationError):
        DecisionRequest(state=None, questions={"a": ok})


def test_result_partitions_every_key():
    r = EngineResult.build("e", ["a", "b", "c"], {}, {"a": "unsupported"})
    assert r.question_failures == {"a": "unsupported", "b": "invalid_answer", "c": "invalid_answer"}
    assert r.status == "failed"


def test_request_failure_covers_keys_without_answers():
    r = EngineResult.build("e", ["a", "b"], {}, {"a": "unsupported"}, request_failure="timed_out")
    assert r.question_failures == {"a": "unsupported"} and r.status == "failed"


def test_engine_config_typo_fails_loudly():
    from app.schemas import EngineSpec

    with pytest.raises(ValidationError):
        EngineSpec.model_validate({"id": "x", "kind": "openrouter_decisions", "timeout": 60})


def test_example_config_loads_with_the_strict_spec():
    from app.registry import load_config
    from app.settings import ROOT

    config, source = load_config(ROOT / "does-not-exist.yaml")
    assert source.name == "config.example.yaml" and len(config.engines) >= 5
