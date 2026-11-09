"""§12.3 metrics on toy sets whose numbers are worked out by hand."""

from __future__ import annotations

import pytest

from app.metrics import THRESHOLDS, choice_metrics, noul_metrics

# label, P(true): two positives, two negatives; the 0.6 negative is the only one a 0.5 threshold gets wrong
TOY = [("true", 0.95), ("true", 0.70), ("false", 0.60), ("false", 0.10)]


def at(m, t):
    return next(c for c in m["curve"] if c["t"] == t)


def test_thresholds_run_from_050_to_099():
    assert THRESHOLDS[0] == 0.5 and THRESHOLDS[-1] == 0.99 and len(THRESHOLDS) == 50


def test_whip_insight_agreement_by_hand():
    m = noul_metrics(TOY)
    c = at(m, 0.5)  # verdicts: yes, yes, yes, no → one false positive
    assert (c["tp"], c["fp"], c["fn"], c["tn"]) == (2, 1, 0, 1) and c["agreement"] == 0.75
    assert c["precision"] == round(2 / 3, 4) and c["recall"] == 1.0
    assert at(m, 0.61)["agreement"] == 1.0 and at(m, 0.70)["agreement"] == 1.0
    assert at(m, 0.71)["agreement"] == 0.75  # 0.70 is now a miss
    # the highest agreement, on a tie the lowest t
    assert m["whip"] == {"t": 0.61, "agreement": 1.0, "precision": 1.0, "recall": 1.0, "meets_target": True}


def test_selective_coverage_and_accuracy_by_hand():
    m = noul_metrics(TOY * 5)  # 20 items: the same shares, and enough decided for a suggestion
    assert at(m, 0.5)["coverage"] == 1.0 and at(m, 0.5)["accuracy"] == 0.75
    assert at(m, 0.61)["coverage"] == 0.75 and at(m, 0.61)["accuracy"] == 1.0  # 0.60 is undecided
    assert at(m, 0.9)["coverage"] == 0.5  # 0.95 and 0.10 (1 − 0.10 = 0.90) stay decided
    assert m["selective"] == {"t": 0.61, "coverage": 0.75, "accuracy": 1.0, "decided": 15, "target": 0.9}


def test_a_suggestion_needs_ten_decided_items():
    """One right answer at 0.99 is not a threshold (review P4): fewer than MIN_DECIDED decided → no suggestion."""
    assert noul_metrics(TOY)["selective"]["t"] is None  # 3 decided at best
    # one right answer at 0.995, twenty confidently wrong: only t ≥ 0.91 reaches 90 %, and there one item is decided
    lone = [("true", 0.995)] + [("false", 0.9)] * 20
    m = noul_metrics(lone)
    assert at(m, 0.99)["decided"] == 1 and at(m, 0.99)["accuracy"] == 1.0
    assert m["selective"]["t"] is None
    assert noul_metrics([("true", 0.995)] * 10)["selective"]["t"] == 0.5


@pytest.mark.parametrize("p", [float("nan"), float("inf"), float("-inf"), "0.9", True])
def test_a_probability_that_is_not_a_finite_number_is_no_answer(p):
    m = noul_metrics([("true", p), ("false", 0.1)])
    assert m["answered"] == 1 and m["items"] == 2
    c = choice_metrics([("a", {"a": p, "b": 0.1}), ("b", {"a": 0.2, "b": 0.8})], ["a", "b"])
    assert c["answered"] == 1


def test_choice_metrics_scale_with_many_options():
    """5 000 items × 255 options in well under a second (review P4: options.index inside max was quadratic)."""
    import time

    opts = [f"o{i}" for i in range(255)]
    probs = {o: 1 / 255 for o in opts} | {"o7": 0.5}
    t0 = time.perf_counter()
    m = choice_metrics([("o7", probs)] * 5000, opts)
    assert m["accuracy"] == 1.0 and time.perf_counter() - t0 < 1.0


def test_brier_and_ece_by_hand():
    m = noul_metrics(TOY)
    assert m["brier"] == round((0.05 ** 2 + 0.3 ** 2 + 0.6 ** 2 + 0.1 ** 2) / 4, 4)  # 0.1156
    # bins: 0.1 (y=0), 0.6 (y=0), 0.7 (y=1), 0.95 (y=1) — one each: ECE = mean |p − y|
    assert m["ece"] == round((0.1 + 0.6 + 0.3 + 0.05) / 4, 4)
    assert [b["n"] for b in m["reliability"]] == [1, 1, 1, 1]


def test_a_failed_call_counts_against_coverage_not_agreement():
    m = noul_metrics([*TOY, ("true", None)])
    assert m["items"] == 5 and m["answered"] == 4
    assert at(m, 0.61)["agreement"] == 1.0  # over the answered
    assert at(m, 0.61)["coverage"] == 0.6  # 3 decided of 5 labelled


def test_rounding_to_three_decimals_as_whip_insight():
    """D-32: the stored number, rounded to 3 decimals, decides: 0.69951 is 0.700 ≥ 0.70."""
    m = noul_metrics([("true", 0.69951), ("false", 0.2)])
    assert at(m, 0.70)["tp"] == 1


def test_target_not_reached():
    m = noul_metrics([("true", 0.4), ("false", 0.6)], target=0.9)
    assert m["whip"]["meets_target"] is False and m["whip"]["agreement"] == 0.5
    assert m["selective"]["t"] is None


def test_nothing_answered():
    m = noul_metrics([("true", None)])
    assert m["answered"] == 0 and m["brier"] is None and m["whip"] is None and m["curve"] == []


def test_choice_metrics_by_hand():
    opts = ["a", "b", "c"]
    obs = [("a", {"a": 0.9, "b": 0.05, "c": 0.05}), ("b", {"a": 0.6, "b": 0.3, "c": 0.1}),
           ("c", {"a": 0.1, "b": 0.1, "c": 0.8}), ("a", None)] * 5
    m = choice_metrics(obs, opts)
    assert m["items"] == 20 and m["answered"] == 15 and m["accuracy"] == round(2 / 3, 4)
    assert m["confusion"] == {"a": {"a": 5}, "b": {"a": 5}, "c": {"c": 5}}
    brier = ((0.1 ** 2 + 0.05 ** 2 + 0.05 ** 2) + (0.6 ** 2 + 0.7 ** 2 + 0.1 ** 2) + (0.1 ** 2 + 0.1 ** 2 + 0.2 ** 2)) / 3
    # (the five copies leave the mean unchanged)
    assert m["brier"] == round(brier, 4)
    # selective: top_p ≥ t decides; at 0.61 the wrong 0.6 answer drops out
    assert at(m, 0.5)["coverage"] == 0.75 and at(m, 0.5)["accuracy"] == round(2 / 3, 4)
    assert at(m, 0.61)["coverage"] == 0.5 and at(m, 0.61)["accuracy"] == 1.0
    assert m["selective"]["t"] == 0.61


def test_score_metrics_carry_their_type():
    m = choice_metrics([("1", {"0": 0.2, "1": 0.8})], ["0", "1"], kind="score")
    assert m["type"] == "score" and m["accuracy"] == 1.0


@pytest.mark.parametrize("p", [0.0, 1.0, 0.999, 0.05])
def test_extreme_probabilities_stay_in_their_bins(p):
    m = noul_metrics([("true", p)])
    assert len(m["reliability"]) == 1 and 0 <= m["reliability"][0]["lo"] <= p <= m["reliability"][0]["hi"]
