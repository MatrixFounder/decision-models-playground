"""Calibration metrics of a labelled dataset run (docs/DESIGN.md §12.3). Pure functions: observations in, numbers out.

Two readings of a threshold t, because they answer different questions:

- **whip-insight's verdict** (SPEC-decision-service.md §8.2.6, decision-service.md §5.12): a `noul` is true when
  P(true), rounded to 3 decimals, is ≥ t. *Agreement* = items whose verdict equals the label, over the items the
  model answered — the replay tool's formula. The population differs: whip-insight asks a windows-mode tag only
  after its lexical rule fires, with fragments as the state; a dataset asks every item as it is. A threshold found
  here is a starting point for the replay on real calls, not its result.
- **selective** (UC-3, the coverage ↔ accuracy curve): an item is decided when the preferred answer's probability is
  ≥ t — max(p, 1 − p) for a `noul`, the top option's for `choice` and `score` (which is whip-insight's own rule
  for those two). *Coverage* = decided over all labelled items (a failed call is not decided); *accuracy* = correct
  among the decided. The suggested threshold is the smallest t whose accuracy reaches the target with at least
  MIN_DECIDED items decided (one right answer at t = 0.99 is not a threshold).

A probability that is not a finite number counts as no answer (the engines clamp; a hand-edited record may not).

Brier: the mean squared error of the probabilities (multi-class for choice and score). ECE: 10 equal bins of the
preferred answer's probability (of P(true) for a `noul`), |mean probability − observed frequency| weighted by count.
"""

from __future__ import annotations

import math
from typing import Any

THRESHOLDS = [round(0.50 + i / 100, 2) for i in range(50)]  # 0.50 … 0.99
TARGET = 0.9  # whip-insight §12: at least 90 % agreement at the chosen threshold
BINS = 10
MIN_DECIDED = 10


def finite(p: Any) -> bool:
    return isinstance(p, int | float) and not isinstance(p, bool) and math.isfinite(p)


def r3(p: float) -> float:
    """whip-insight compares the stored number, rounded to 3 decimals (D-32)."""
    return round(p, 3)


def _bins(pairs: list[tuple[float, float]]) -> tuple[list[dict[str, Any]], float | None]:
    """pairs of (predicted probability, outcome 0/1) → reliability bins and ECE."""
    if not pairs:
        return [], None
    acc = [[0, 0.0, 0.0] for _ in range(BINS)]
    for p, y in pairs:
        b = min(BINS - 1, int(p * BINS))
        acc[b][0] += 1
        acc[b][1] += p
        acc[b][2] += y
    bins, ece = [], 0.0
    for i, (n, sp, sy) in enumerate(acc):
        if not n:
            continue
        mean_p, freq = sp / n, sy / n
        bins.append({"lo": i / BINS, "hi": (i + 1) / BINS, "n": n, "mean_p": round(mean_p, 4), "freq": round(freq, 4)})
        ece += n / len(pairs) * abs(mean_p - freq)
    return bins, round(ece, 4)


def noul_metrics(obs: list[tuple[str, float | None]], target: float = TARGET) -> dict[str, Any]:
    """obs: (label "true"/"false", P(true) or None when the call gave no answer)."""
    n = len(obs)
    answered = [(lab == "true", float(p)) for lab, p in obs if finite(p)]
    m = len(answered)
    out: dict[str, Any] = {"type": "noul", "items": n, "answered": m, "positives": sum(1 for lab, _ in obs if lab == "true")}
    if not m:
        return {**out, "brier": None, "ece": None, "reliability": [], "curve": [], "whip": None, "selective": None}
    out["brier"] = round(sum((p - y) ** 2 for y, p in answered) / m, 4)
    out["reliability"], out["ece"] = _bins([(p, float(y)) for y, p in answered])
    curve = []
    for t in THRESHOLDS:
        tp = sum(1 for y, p in answered if r3(p) >= t and y)
        fp = sum(1 for y, p in answered if r3(p) >= t and not y)
        fn = sum(1 for y, p in answered if r3(p) < t and y)
        tn = m - tp - fp - fn
        decided = [(y, p) for y, p in answered if max(r3(p), r3(1 - p)) >= t]
        correct = sum(1 for y, p in decided if (r3(p) >= 0.5) == y)
        curve.append({
            "t": t,
            "agreement": round((tp + tn) / m, 4),
            "precision": round(tp / (tp + fp), 4) if tp + fp else None,
            "recall": round(tp / (tp + fn), 4) if tp + fn else None,
            "tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "decided": len(decided),
            "coverage": round(len(decided) / n, 4),
            "accuracy": round(correct / len(decided), 4) if decided else None,
        })
    best = max(curve, key=lambda c: (c["agreement"], -c["t"]))  # the highest agreement; on a tie the lowest t
    out["curve"] = curve
    out["whip"] = {"t": best["t"], "agreement": best["agreement"], "precision": best["precision"],
                   "recall": best["recall"], "meets_target": best["agreement"] >= target}
    out["selective"] = _suggest(curve, target)
    return out


def choice_metrics(obs: list[tuple[str, dict[str, float] | None]], options: list[str], target: float = TARGET,
                   kind: str = "choice") -> dict[str, Any]:
    """obs: (label option id or level index, the answer's probabilities or None). Score levels are options "0"…"n-1"."""
    n = len(obs)
    answered = [(lab, probs) for lab, probs in obs
                if probs and all(finite(v) for v in probs.values())]
    m = len(answered)
    out: dict[str, Any] = {"type": kind, "items": n, "answered": m, "options": options}
    if not m:
        return {**out, "brier": None, "ece": None, "reliability": [], "curve": [], "accuracy": None,
                "confusion": {}, "selective": None}

    rank = {o: i for i, o in enumerate(options)}  # a tie goes to the earlier option; a lookup, not options.index

    def top(probs: dict[str, float]) -> tuple[str, float]:
        k = max(probs, key=lambda o: (probs[o], -rank.get(o, len(options))))
        return k, probs[k]

    tops = [(lab, *top(probs), probs) for lab, probs in answered]
    out["accuracy"] = round(sum(1 for lab, k, _, _ in tops if k == lab) / m, 4)
    out["brier"] = round(sum(sum((probs.get(o, 0.0) - (1.0 if o == lab else 0.0)) ** 2 for o in options)
                             for lab, probs in answered) / m, 4)
    out["reliability"], out["ece"] = _bins([(p, 1.0 if k == lab else 0.0) for lab, k, p, _ in tops])
    confusion: dict[str, dict[str, int]] = {}
    for lab, k, _, _ in tops:
        confusion.setdefault(lab, {})[k] = confusion.setdefault(lab, {}).get(k, 0) + 1
    out["confusion"] = confusion
    curve = []
    for t in THRESHOLDS:
        decided = [(lab, k) for lab, k, p, _ in tops if r3(p) >= t]
        correct = sum(1 for lab, k in decided if k == lab)
        curve.append({"t": t, "decided": len(decided), "coverage": round(len(decided) / n, 4),
                      "accuracy": round(correct / len(decided), 4) if decided else None})
    out["curve"] = curve
    out["selective"] = _suggest(curve, target)
    return out


def _suggest(curve: list[dict[str, Any]], target: float) -> dict[str, Any]:
    """The smallest t whose selective accuracy reaches the target on at least MIN_DECIDED decided items, with its
    coverage; None when none does."""
    hit = next((c for c in curve if c["accuracy"] is not None and c["accuracy"] >= target
                and c["decided"] >= MIN_DECIDED), None)
    if hit is None:
        return {"t": None, "coverage": None, "accuracy": None, "decided": None, "target": target}
    return {"t": hit["t"], "coverage": hit["coverage"], "accuracy": hit["accuracy"], "decided": hit["decided"],
            "target": target}
