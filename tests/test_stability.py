"""§11: the grid of an experiment and its stability metrics, on small cases worked out by hand."""

from __future__ import annotations

import math

import pytest

from app.experiments import ExperimentIn, orders_of, plan
from app.stability import question_metrics

OPTS = ["a", "b", "c"]
ORDERS = [["a", "b", "c"], ["c", "b", "a"]]


def p(a, b, c):
    return {"a": a, "b": b, "c": c}


# two formulations × two orders; one cell has a single answered repeat
CHOICE = {
    ("base", 0): [p(.7, .2, .1), p(.6, .3, .1)],  # mean a .65 — the top
    ("base", 1): [p(.5, .2, .3), p(.5, .2, .3)],  # reordered: a .50 — still the top, |ΔP| .15
    ("en", 0): [p(.4, .5, .1), p(.4, .5, .1)],  # the wording flips the top to b; a is .25 below the base
    ("en", 1): [p(.3, .3, .4)],  # in English and reordered: c on top
}


def test_choice_metrics_by_hand():
    m = question_metrics("choice", OPTS, ["base", "en"], 2, ORDERS, CHOICE, calls=8)
    assert m["answered"] == 7 and m["calls"] == 8
    # repeats: spreads .10 (base·o0, of a), 0 (base·o1), 0 (en·o0, of b); en·o1 has one repeat only
    # the SE of a difference of two means: pooled SD sqrt((.005 + 0 + 0) / 3) × sqrt(2 / 2 repeats)
    assert m["repeats"] == {"spread_mean": round(.1 / 3, 4), "spread_max": 0.1, "diff_se": 0.0408, "top_flip_share": 0.0,
                            "cells": 3}
    # order: within base .15 and no flip; within en b .5 → .3 (|ΔP| .2) and c on top (a flip)
    o = m["order"]
    assert (o["dp_mean"], o["dp_max"], o["flip_share"], o["comparisons"]) == (0.175, 0.2, 0.5, 2)
    # first position: o0 shows a first, o1 shows c first; tops a a | a a | b b | c → a, a and c were first: 3 of 7
    assert o["first_position_share"] == round(3 / 7, 4) and o["first_position_chance"] == round(1 / 3, 4)
    # an order-invariant model answers its pooled top: a in base (first in o0: 2 answers), b in en (never first)
    assert o["first_position_expected"] == round(2 / 7, 4)
    # lift: a first .525 vs not .4333; b never first; c first .3333 vs not .1 → mean of .0917 and .2333
    assert o["first_position_lift"] == 0.1625
    # formulation, order by order against the base's top: o0 a .65 → .40, o1 a .50 → .30; both tops change
    f = m["formulation"]
    assert (f["dp_mean"], f["dp_max"], f["flip_share"]) == (0.225, 0.25, 1.0)
    assert f["per_formulation"] == {"en": {"mean": 0.225, "max": 0.25}}
    # the heatmap: one row per cell, the mean of its answered repeats
    assert [(r["formulation"], r["order"], r["answered"]) for r in m["heatmap"]] == [
        ("base", 0, 2), ("base", 1, 2), ("en", 0, 2), ("en", 1, 1)]
    assert m["heatmap"][0]["p"] == {"a": 0.65, "b": 0.25, "c": 0.1}


def test_noul_metrics_read_p_true():
    answers = {("base", 0): [{"true": .9, "false": .1}, {"true": .8, "false": .2}],
               ("en", 0): [{"true": .6, "false": .4}, {"true": .7, "false": .3}]}
    m = question_metrics("noul", ["true", "false"], ["base", "en"], 1, None, answers, calls=4)
    assert m["repeats"]["spread_mean"] == 0.1 and m["repeats"]["top_flip_share"] == 0.0
    assert m["order"] is None  # true/false has no order to change
    assert m["formulation"]["dp_mean"] == 0.2 and m["formulation"]["flip_share"] == 0.0


def test_a_top_that_changes_between_repeats():
    answers = {("base", 0): [p(.5, .4, .1), p(.4, .5, .1), p(.6, .3, .1)]}
    m = question_metrics("choice", OPTS, ["base"], 1, None, answers, calls=3)
    assert m["repeats"]["top_flip_share"] == 1.0 and m["repeats"]["spread_max"] == 0.2
    assert m["order"] is None and m["formulation"] is None


def test_one_repeat_has_no_spread_and_nothing_answered_is_empty():
    m = question_metrics("choice", OPTS, ["base"], 1, None, {("base", 0): [p(.5, .4, .1)]}, calls=1)
    assert m["repeats"]["spread_mean"] is None and m["repeats"]["cells"] == 0
    m = question_metrics("choice", OPTS, ["base"], 2, ORDERS, {("base", 0): [], ("base", 1): []}, calls=2)
    assert m["answered"] == 0 and m["order"]["dp_mean"] is None and m["heatmap"][0]["p"] is None


# --- the grid ---------------------------------------------------------------------------------------------------------

def test_every_order_up_to_four_options_the_original_first():
    assert orders_of(["a", "b", "c"], 6, 7, "q") == [["a", "b", "c"], ["a", "c", "b"], ["b", "a", "c"],
                                                      ["b", "c", "a"], ["c", "a", "b"], ["c", "b", "a"]]
    assert len(orders_of(list("abcd"), 6, 7, "q")) == 24


def test_k_orders_beyond_four_options():
    ids = list("abcdefg")
    o = orders_of(ids, 6, 7, "q")
    assert len(o) == 6 and o[0] == ids and len({tuple(x) for x in o}) == 6
    assert orders_of(ids, 6, 7, "q") == o  # fixed by the seed
    assert orders_of(ids, 6, 8, "q") != o and orders_of(ids, 6, 7, "other") != o


def base_request(**questions):
    return {"state": "Клиент: хочу вклад.", "questions": questions}


CH3 = {"type": "choice", "instructions": "Тема?", "criteria": {"credit": "Кредит", "deposit": "Вклад", "other": "Другое"}}
NOUL = {"type": "noul", "instructions": "Есть интерес?"}
SCORE = {"type": "score", "instructions": "Интерес?", "criteria": ["нет", "слабый", "сильный"]}


def test_the_grid_is_formulations_by_orders_by_repeats():
    exp = ExperimentIn(request=base_request(topic=CH3, interest=SCORE, yes=NOUL), engines=["e"], repeats=2,
                       formulations={"en": {"topic": {**CH3, "instructions": "Topic?"}, "interest": SCORE, "yes": NOUL}})
    pl = plan(exp)
    assert pl.n_orders == 6 and len(pl.jobs) == 2 * 6 * 2
    assert list(pl.formulations) == ["base", "en"] and set(pl.orders) == {"topic"}  # score and noul keep their order
    key, req = pl.jobs[5]  # repeat-major: base orders 0–5 of repeat 0 come first
    assert key == "base|o5|r0" and list(req.questions["topic"].criteria) == ["other", "deposit", "credit"]
    assert req.questions["interest"].criteria == ["нет", "слабый", "сильный"]
    assert pl.jobs[-1][0] == "en|o5|r1" and pl.jobs[-1][1].questions["topic"].instructions == "Topic?"


def test_two_choice_questions_take_their_orders_side_by_side():
    ch2 = {"type": "choice", "instructions": "?", "criteria": {"x": "X", "y": "Y"}}
    pl = plan(ExperimentIn(request=base_request(topic=CH3, two=ch2), engines=["e"], repeats=1))
    assert pl.n_orders == 6
    shown = [list(req.questions["two"].criteria) for _, req in pl.jobs]
    assert shown == [["x", "y"], ["y", "x"]] * 3  # the shorter list cycles


def test_orders_can_be_off():
    pl = plan(ExperimentIn(request=base_request(topic=CH3), engines=["e"], repeats=3, orders={"enabled": False}))
    assert pl.n_orders == 1 and len(pl.jobs) == 3 and pl.orders == {}


@pytest.mark.parametrize("forms,msg", [
    ({"en": {"topic": CH3}}, "the questions are"),
    ({"en": {"topic": CH3, "yes": {**NOUL, "type": "choice", "criteria": {"a": "", "b": ""}}}}, "type choice"),
    ({"en": {"topic": {**CH3, "criteria": {"credit": "", "deposit": "", "loan": ""}}, "yes": NOUL}}, "option ids"),
    ({"base": {"topic": CH3, "yes": NOUL}}, "own wording"),
    ({"bad name": {"topic": CH3, "yes": NOUL}}, "must be keys"),
    ({f"f{i}": {"topic": CH3, "yes": NOUL} for i in range(6)}, "at most 6"),
])
def test_formulations_must_match_the_base(forms, msg):
    with pytest.raises(ValueError, match=msg):
        ExperimentIn(request=base_request(topic=CH3, yes=NOUL), engines=["e"], formulations=forms)


def test_score_levels_must_match_in_number():
    with pytest.raises(ValueError, match="3 levels"):
        ExperimentIn(request=base_request(s=SCORE), engines=["e"],
                     formulations={"en": {"s": {**SCORE, "criteria": ["no", "yes"]}}})


def test_a_non_finite_probability_is_no_answer():
    from app.stability import build_stability

    record = {"id": "x", "created_at": "t", "engines": [{"engine_id": "e"}],
              "experiment": {"base": "base", "formulations": {"base": {"q": NOUL}}, "orders": {}, "n_orders": 1,
                             "repeats": 2},
              "calls": [{"key": f"base|o0|r{r}", "formulation": "base", "order": 0, "repeat": r,
                         "results": {"e": {"answers": {"q": {"probabilities": {"true": v, "false": 0.1}}}}}}
                        for r, v in enumerate([math.nan, 0.9])]}
    m = build_stability(record)["groups"][0]["metrics"]
    assert m["answered"] == 1 and m["calls"] == 2


# --- review P5 ---------------------------------------------------------------------------------------------------------

def test_extraction_questions_read_p_found_and_never_crash():
    """F1: text/number/date answer {"found": p}; with ≥2 repeats or wordings the report crashed on max() of nothing."""
    answers = {("base", 0): [{"found": .9}, {"found": .8}], ("en", 0): [{"found": .6}, {"found": .7}]}
    m = question_metrics("text", ["found"], ["base", "en"], 1, None, answers, calls=4)
    assert m["repeats"]["spread_mean"] == 0.1 and m["repeats"]["top_flip_share"] is None  # one outcome: no "top"
    assert m["formulation"]["dp_mean"] == 0.2 and m["formulation"]["flip_share"] is None and m["order"] is None
    assert question_metrics("date", ["found"], ["base"], 1, None, {("base", 0): []}, calls=1)["answered"] == 0


def first_shown(ids):  # a model that always answers the option shown first
    return {i: (0.8 if j == 0 else 0.2 / (len(ids) - 1)) for j, i in enumerate(ids)}


def answers_of(model, orders, n_orders, repeats=1, forms=("base",)):
    return {(f, o): [model(orders[o % len(orders)]) for _ in range(repeats)] for f in forms for o in range(n_orders)}


def test_first_position_is_read_against_these_orders_not_one_in_n():
    """F2: beyond 4 options the orders are the original plus random ones and need not put every option first equally
    often; an order-invariant model must not read as a pull to the first place."""
    ids = list("abcde")
    orders = orders_of(ids, 6, 7, "q")
    fixed = lambda shown: {i: (0.8 if i == "a" else 0.05) for i in ids}  # noqa: E731 — answers a whatever the order
    o = question_metrics("choice", ids, ["base"], 6, orders, answers_of(fixed, orders, 6), calls=6)["order"]
    assert o["first_position_share"] == o["first_position_expected"]  # no pull: share as expected on these orders
    assert o["first_position_share"] > o["first_position_chance"]  # though above 1/n: the design, not the model
    assert o["first_position_lift"] == 0.0  # an option's P does not depend on where it stands: the lift ignores the design
    biased = question_metrics("choice", ids, ["base"], 6, orders, answers_of(first_shown, orders, 6), calls=6)["order"]
    assert biased["first_position_share"] == 1.0 and biased["first_position_share"] > biased["first_position_expected"]


def test_the_lift_sees_a_pull_that_never_changes_the_top():
    """F10: +0.05 to whatever is shown first, the top unchanged: the share stays as expected, the lift shows it."""
    ids = list("abc")
    orders = orders_of(ids, 6, 7, "q")
    base = {"a": 0.6, "b": 0.25, "c": 0.15}
    tilt = lambda shown: {i: base[i] + (0.05 if i == shown[0] else 0.0) for i in ids}  # noqa: E731
    o = question_metrics("choice", ids, ["base"], 6, orders, answers_of(tilt, orders, 6), calls=6)["order"]
    assert o["first_position_share"] == o["first_position_expected"]
    assert o["first_position_lift"] == pytest.approx(0.05, abs=1e-4)


def test_a_cycled_question_is_never_compared_with_itself():
    """F3: a 2-option question shown over 6 orders of another: its order 1 against its order 0, once, pooled."""
    two = [["x", "y"], ["y", "x"]]
    o = question_metrics("choice", ["x", "y"], ["base"], 6, two, answers_of(first_shown, two, 6), calls=6)["order"]
    assert o["comparisons"] == 1 and o["flip_share"] == 1.0 and o["dp_mean"] == 0.6 and o["orders"] == 2


def test_multi_reads_the_lift_not_the_argmax():
    ids = ["a", "b", "c"]
    orders = orders_of(ids, 6, 7, "q")
    model = lambda shown: {i: (0.9 if i in ("a", "b") else 0.1) for i in ids}  # noqa: E731
    o = question_metrics("multi", ids, ["base"], 6, orders, answers_of(model, orders, 6), calls=6)["order"]
    assert o["first_position_share"] is None and o["first_position_lift"] == pytest.approx(0.0, abs=1e-9)


def test_ties_go_to_the_base_order_not_the_shown_one():
    from app.stability import top_of

    assert top_of({"c": 0.5, "a": 0.5, "b": 0.0}, ["a", "b", "c"]) == "a"
    assert top_of({}, ["a"]) is None
