"""The stability report of an experiment (docs/TASK.md §5.2, UC-2): engine × axis → metric, and a heatmap.

Per engine and question, from the record alone (pure):

- **cells** — a formulation and an order; each holds the answers of its repeats. A cell's *mean* is the mean
  probability of every option over its answered repeats.
- **the reference option** — P(true) for a `noul`; P(found) for `text`, `number` and `date` (the value itself is not
  kept by the record); for `choice`, `multi` and `score` the top option of the mean it is compared from.
- **repeats** — per cell, the spread (max − min) of the reference option over the repeats and whether the top answer
  changed. `diff_se` is the standard error of a difference of two cell means from the pooled within-cell spread:
  an order or wording |ΔP| below about 2 × `diff_se` is within noise (the raw spread of single repeats overstates the
  noise of a mean, review P5).
- **order** (`choice`, `multi`) — each question against its own orders: order j pools every cell that showed it (a
  shorter list cycles), and is compared with order 0 once, never with itself. |ΔP| of order 0's top option, and
  whether the top changed. The orders of several choice questions change together: an order effect of one question
  can include the reorder of another (documented, TASK §5.2 rule 2).
  *First position* (`choice` only): how often the top answer was the option shown first, against the share an
  order-invariant model would give on these very orders — the pooled top of its formulation, counted where it was
  shown first. Not 1/n: beyond 4 options the orders are random and need not put every option first equally often
  (review P5). *Lift*: the mean probability of an option when it is shown first minus when it is not, averaged over
  options — a pull to the first place that does not change the top shows here.
- **formulation** — every wording against the base, order by order: |ΔP| of the reference option and whether the top
  changed.

Failed calls are left out of the means and counted. Probabilities that are not finite numbers count as no answer.
"""

from __future__ import annotations

import math
from typing import Any

FLOOR = 2  # repeats needed to speak of a spread
EXTRACT = ("text", "number", "date")  # answered with {"found": p}: one outcome, no options to order


def finite(x: Any) -> bool:
    return isinstance(x, int | float) and not isinstance(x, bool) and math.isfinite(x)


def options_of(q: dict[str, Any]) -> list[str]:
    if q["type"] == "noul":
        return ["true", "false"]
    if q["type"] == "score":
        return [str(i) for i in range(len(q.get("criteria") or []))]
    if q["type"] in EXTRACT:
        return ["found"]
    return list(q.get("criteria") or {})


def top_of(probs: dict[str, float], options: list[str]) -> str | None:
    """The most probable option; a tie goes to the option listed first in the base wording (not the shown order).
    None when there is nothing to choose from."""
    if not probs:
        return None
    rank = {o: i for i, o in enumerate(options)}
    return max(probs, key=lambda o: (probs[o], -rank.get(o, len(options))))


def mean_of(answers: list[dict[str, float]], options: list[str]) -> dict[str, float] | None:
    if not answers or not options:
        return None
    return {o: sum(a.get(o, 0.0) for a in answers) / len(answers) for o in options}


def _stats(values: list[float]) -> dict[str, float | None]:
    return {"mean": round(sum(values) / len(values), 4) if values else None,
            "max": round(max(values), 4) if values else None}


def _share(hits: list[bool]) -> float | None:
    return round(sum(hits) / len(hits), 4) if hits else None


def question_metrics(qtype: str, options: list[str], forms: list[str], n_orders: int, orders: list[list[str]] | None,
                     answers: dict[tuple[str, int], list[dict[str, float]]], calls: int,
                     unmeasured: int = 0) -> dict[str, Any]:
    """`answers[(formulation, order)]` — the probabilities of each answered repeat of that cell; `orders` — this
    question's own orders (a cell `o` showed `orders[o % len(orders)]`)."""
    fixed_ref = {"noul": "true", **{t: "found" for t in EXTRACT}}.get(qtype)
    single = qtype in EXTRACT  # one outcome: "the top changed" has no meaning
    means = {cell: mean_of(a, options) for cell, a in answers.items()}
    answered = sum(len(a) for a in answers.values())

    def ref(cell_mean: dict[str, float]) -> str | None:
        return fixed_ref or top_of(cell_mean, options)

    # repeats: spread of the reference option, its variance (for the noise of a mean), changes of the top answer
    spreads, variances, sizes, flips = [], [], [], []
    for cell, ans in answers.items():
        if len(ans) < FLOOR or means[cell] is None:
            continue
        r = ref(means[cell])
        values = [a.get(r, 0.0) for a in ans]
        spreads.append(max(values) - min(values))
        mu = sum(values) / len(values)
        variances.append(sum((v - mu) ** 2 for v in values) / (len(values) - 1))
        sizes.append(len(values))
        if not single:
            flips.append(len({top_of(a, options) for a in ans}) > 1)
    diff_se = None
    if variances:
        diff_se = round(math.sqrt(sum(variances) / len(variances)) * math.sqrt(2 / (sum(sizes) / len(sizes))), 4)
    repeats = {**{f"spread_{k}": v for k, v in _stats(spreads).items()}, "diff_se": diff_se,
               "top_flip_share": _share(flips), "cells": len(spreads)}

    # order: each of this question's orders against its order 0, pooled over the cells that showed it
    order: dict[str, Any] | None = None
    if orders and len(orders) > 1 and qtype in ("choice", "multi"):
        size = len(orders)
        pooled: dict[tuple[str, int], list[dict[str, float]]] = {}
        for (f, o), ans in answers.items():
            pooled.setdefault((f, o % size), []).extend(ans)
        pmeans = {k: mean_of(v, options) for k, v in pooled.items()}
        dps, oflips = [], []
        for f in forms:
            base = pmeans.get((f, 0))
            if base is None:
                continue
            b = top_of(base, options)
            for j in range(1, size):
                m = pmeans.get((f, j))
                if m is None:
                    continue
                dps.append(abs(m[b] - base[b]))
                oflips.append(top_of(m, options) != b)
        # first position against an order-invariant model on these orders; lift of the option shown first
        first, expected = [], []
        by_pos: dict[str, tuple[list[float], list[float]]] = {o: ([], []) for o in options}
        for f in forms:
            all_f = [a for (ff, _), ans in answers.items() if ff == f for a in ans]
            invariant = top_of(mean_of(all_f, options) or {}, options)
            for (ff, o), ans in answers.items():
                if ff != f:
                    continue
                shown = orders[o % size]
                for a in ans:
                    first.append(top_of(a, options) == shown[0])
                    expected.append(invariant == shown[0])
                    for x in options:
                        by_pos[x][0 if x == shown[0] else 1].append(a.get(x, 0.0))
        lifts = [sum(f1) / len(f1) - sum(rest) / len(rest) for f1, rest in by_pos.values() if f1 and rest]
        choice = qtype == "choice"  # multi: independent P(selected); its argmax is noise — only the lift is read
        order = {**{f"dp_{k}": v for k, v in _stats(dps).items()},
                 "flip_share": _share(oflips),
                 "first_position_share": _share(first) if choice else None,
                 "first_position_expected": _share(expected) if choice else None,
                 "first_position_chance": round(1 / len(options), 4) if choice else None,
                 "first_position_lift": round(sum(lifts) / len(lifts), 4) if lifts else None,
                 "comparisons": len(dps), "orders": size}

    # formulation: each wording against the base, order by order
    formulation: dict[str, Any] | None = None
    if len(forms) > 1:
        dps, fflips, per = [], [], {}
        for f in forms[1:]:
            fd = []
            for o in range(n_orders):
                base, m = means.get((forms[0], o)), means.get((f, o))
                if base is None or m is None:
                    continue
                r = ref(base)
                d = abs(m[r] - base[r])
                fd.append(d)
                dps.append(d)
                if not single:
                    fflips.append(top_of(m, options) != top_of(base, options))
            per[f] = _stats(fd)
        formulation = {**{f"dp_{k}": v for k, v in _stats(dps).items()},
                       "flip_share": _share(fflips), "per_formulation": per, "comparisons": len(dps)}

    heatmap = [{"formulation": f, "order": o, "answered": len(answers.get((f, o), [])),
                "p": {k: round(v, 4) for k, v in (means.get((f, o)) or {}).items()} or None}
               for f in forms for o in range(n_orders)]
    # `unmeasured`: answered, but with no probability to compare — a number estimated over a range answers an interval
    return {"type": qtype, "options": options, "calls": calls, "answered": answered, "unmeasured": unmeasured,
            "repeats": repeats, "order": order, "formulation": formulation, "heatmap": heatmap}


def build_stability(record: dict[str, Any]) -> dict[str, Any]:
    exp = record["experiment"]
    forms = list(exp["formulations"])
    base_qs = exp["formulations"][exp["base"]]
    n_orders = exp["n_orders"]
    groups = []
    for e in record["engines"]:
        eid = e["engine_id"]
        for key, q in base_qs.items():
            options = options_of(q)
            answers: dict[tuple[str, int], list[dict[str, float]]] = {(f, o): [] for f in forms for o in range(n_orders)}
            calls = unmeasured = 0
            snaps = set()
            for c in record.get("calls") or []:
                res = (c.get("results") or {}).get(eid)
                if res is None:
                    continue
                calls += 1
                a = (res.get("answers") or {}).get(key)
                probs = (a or {}).get("probabilities")
                if probs and all(finite(v) for v in probs.values()):
                    answers[(c["formulation"], c["order"])].append(probs)
                    if res.get("snapshot"):
                        snaps.add(res["snapshot"])
                elif a is not None:
                    unmeasured += 1
            m = question_metrics(q["type"], options, forms, n_orders, exp["orders"].get(key), answers, calls, unmeasured)
            groups.append({"engine_id": eid, "question": key, "snapshots": sorted(snaps), "metrics": m})
    return {"kind": "experiment", "run_id": record["id"], "created_at": record["created_at"], "cost_usd": record.get("cost_usd"),
            "budget_stopped": bool(record.get("budget_stopped")), "engines": record["engines"],
            "formulations": forms, "base": exp["base"], "n_orders": n_orders, "repeats": exp["repeats"],
            "orders": exp["orders"],
            "questions": {k: {"type": q["type"], "instructions": q.get("instructions")} for k, q in base_qs.items()},
            # what was asked, so the screen shows this experiment's request beside its report and can start it again
            "request": record.get("request"), "formulation_questions": exp["formulations"],
            "settings": {"orders_enabled": exp.get("orders_enabled", True), "k": exp.get("k"), "seed": exp.get("seed"),
                         "repeats": exp["repeats"], "engines": [e["engine_id"] for e in record["engines"]]},
            "groups": groups}


def summarize(report: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for g in report["groups"]:
        m = g["metrics"]
        out.append({"engine_id": g["engine_id"], "question": g["question"], "answered": m["answered"],
                    "calls": m["calls"], "spread_mean": m["repeats"]["spread_mean"],
                    "order_dp_mean": (m["order"] or {}).get("dp_mean"),
                    "first_position_share": (m["order"] or {}).get("first_position_share"),
                    "formulation_dp_mean": (m["formulation"] or {}).get("dp_mean")})
    return out


TEXT = {
    "ru": {"title": "Эксперимент", "engines": "движки", "cost": "стоимость", "grid": "Сетка",
           "forms": "формулировки", "orders": "порядков", "repeats": "повторов", "question": "Вопрос",
           "engine": "Движок", "answered": "Ответили", "spread": "Размах повторов (ср./макс.)", "se": "SE разности",
           "rflip": "Смена ответа в повторах", "odp": "Порядок: |ΔP| (ср./макс.)", "oflip": "Порядок: смена лучшего",
           "first": "Лучший — первый показанный (ожидаемо)", "lift": "Подъём первого места",
           "fdp": "Формулировка: |ΔP| (ср./макс.)", "fflip": "Формулировка: смена лучшего",
           "heat": "Вероятности (среднее по повторам)", "cell": "Ячейка",
           "note": "|ΔP| порядка или формулировки меньше примерно двух «SE разности» — в пределах шума повторов. "
                   "«Ожидаемо» — доля, которую дала бы модель без тяги к первому месту на этих же порядках (не 1/n: "
                   "больше 4 вариантов — случайные порядки). «Подъём» — насколько вероятность варианта выше, когда он "
                   "стоит первым; без тяги около 0.",
           "unmeasured": "ответов без вероятностей (оценка числа в диапазоне): сравнивать нечего",
           "budget": "Прогон остановлен дневным бюджетом: часть ячеек не спрошена."},
    "en": {"title": "Experiment", "engines": "engines", "cost": "cost", "grid": "Grid",
           "forms": "formulations", "orders": "orders", "repeats": "repeats", "question": "Question",
           "engine": "Engine", "answered": "Answered", "spread": "Repeat spread (mean/max)", "se": "SE of a difference",
           "rflip": "Answer changed in repeats", "odp": "Order: |ΔP| (mean/max)", "oflip": "Order: top changed",
           "first": "Top shown first (expected)", "lift": "First-place lift",
           "fdp": "Formulation: |ΔP| (mean/max)", "fflip": "Formulation: top changed",
           "heat": "Probabilities (mean over repeats)", "cell": "Cell",
           "note": "An order or formulation |ΔP| below about two \"SE of a difference\" is within the noise of repeats. "
                   "\"Expected\" is the share a model with no pull to the first place would give on these very orders "
                   "(not 1/n: beyond 4 options the orders are random). \"Lift\" is how much higher an option's "
                   "probability is when it is shown first; about 0 with no pull.",
           "unmeasured": "answers without probabilities (a number estimated over a range): nothing to compare",
           "budget": "The run was stopped by the daily budget: some cells were not asked."},
}


def _n(x: Any) -> str:
    return "—" if x is None else str(x)


def _pct(x: Any) -> str:
    return "—" if x is None else f"{x * 100:.1f} %"


def render_markdown(report: dict[str, Any], lang: str = "ru") -> str:
    tx = TEXT.get(lang, TEXT["ru"])
    engines = ", ".join(f"`{e['engine_id']}`" for e in report["engines"])
    out = [f"# {tx['title']} {report['run_id']}", "",
           f"- {report['created_at']}, {tx['engines']}: {engines}, {tx['cost']} ${report.get('cost_usd') or 0:.6f}",
           f"- {tx['grid']}: {len(report['formulations'])} {tx['forms']} ({', '.join(report['formulations'])}) × "
           f"{report['n_orders']} {tx['orders']} × {report['repeats']} {tx['repeats']}", ""]
    if report.get("budget_stopped"):
        out += [f"**{tx['budget']}**", ""]
    out += [f"> {tx['note']}", ""]
    for key, q in report["questions"].items():
        groups = [g for g in report["groups"] if g["question"] == key]
        instr = q["instructions"] if isinstance(q["instructions"], str) else ""
        out += [f"## {tx['question']} `{key}` ({q['type']}){': ' + instr if instr else ''}", "",
                f"| {tx['engine']} | {tx['answered']} | {tx['spread']} | {tx['se']} | {tx['rflip']} | {tx['odp']} | "
                f"{tx['oflip']} | {tx['first']} | {tx['lift']} | {tx['fdp']} | {tx['fflip']} |", "|" + "---|" * 11]
        for g in groups:
            m = g["metrics"]
            r, o, f = m["repeats"], m["order"] or {}, m["formulation"] or {}
            first = (f"{_pct(o.get('first_position_share'))} ({_pct(o.get('first_position_expected'))})"
                     if o.get("first_position_share") is not None else "—")
            out.append(f"| `{g['engine_id']}` | {m['answered']}/{m['calls']} | {_n(r['spread_mean'])} / {_n(r['spread_max'])} | "
                       f"{_n(r.get('diff_se'))} | {_pct(r['top_flip_share'])} | {_n(o.get('dp_mean'))} / {_n(o.get('dp_max'))} | "
                       f"{_pct(o.get('flip_share'))} | {first} | {_n(o.get('first_position_lift'))} | "
                       f"{_n(f.get('dp_mean'))} / {_n(f.get('dp_max'))} | {_pct(f.get('flip_share'))} |")
        for g in groups:
            if g["metrics"].get("unmeasured"):
                out.append(f"\n`{g['engine_id']}`: {g['metrics']['unmeasured']} {tx['unmeasured']}.")
        for g in groups:
            m = g["metrics"]
            out += ["", f"### {tx['heat']}: `{g['engine_id']}`", "",
                    f"| {tx['cell']} | " + " | ".join(f"`{o}`" for o in m["options"]) + " |",
                    "|" + "---|" * (len(m["options"]) + 1)]
            for row in m["heatmap"]:
                p = row["p"] or {}
                out.append(f"| {row['formulation']} · o{row['order']} | " + " | ".join(_n(p.get(o)) for o in m["options"]) + " |")
        out.append("")
    return "\n".join(out).rstrip() + "\n"
