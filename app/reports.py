"""The calibration report of a dataset run (docs/DESIGN.md §12.3): JSON and Markdown, never a call's text.

Computed from the run record alone (item ids, labels, probabilities), so it can be recomputed for another target and
holds what whip-insight §12 allows its replay report: ids, labels, probabilities and metrics. Results are grouped by
engine and by the snapshot that answered: a floating id that moved mid-run shows two rows.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from app.metrics import TARGET, choice_metrics, noul_metrics

LABELLED = ("noul", "choice", "score")
CURVE_POINTS = (0.5, 0.6, 0.7, 0.8, 0.9)  # the replay tool's default thresholds
MAX_ERRORS = 50


def _options(q: dict[str, Any]) -> list[str]:
    if q["type"] == "score":
        return [str(i) for i in range(len(q.get("criteria") or []))]
    return list(q.get("criteria") or {})


def build_report(record: dict[str, Any], target: float = TARGET) -> dict[str, Any]:
    questions: dict[str, dict] = record["request"]["questions"]
    engines = [e["engine_id"] for e in record["engines"]]
    items = record.get("items") or []
    groups = []
    for key, q in questions.items():
        if q["type"] not in LABELLED:
            continue
        for eid in engines:
            rows = []  # (item id, label, answer or None, snapshot or None)
            not_asked = 0  # stopped by the day's budget: never sent, so neither answered nor failed
            for item in items:
                label = (item.get("labels") or {}).get(key)
                if label is None:
                    continue
                res = (item.get("results") or {}).get(eid) or {}
                if res.get("request_failure") == "budget":
                    not_asked += 1
                    continue
                rows.append((item["id"], label, (res.get("answers") or {}).get(key), res.get("snapshot")))
            if not rows:
                continue
            # failed calls carry no snapshot: they count against the snapshot that answered most
            snaps = Counter(s for _, _, a, s in rows if s)
            main = snaps.most_common(1)[0][0] if snaps else None
            for snap in sorted(snaps, key=lambda s: -snaps[s]) or [None]:
                part = [r for r in rows if r[3] == snap or (snap == main and not r[3])]
                groups.append({**_group(key, q, eid, snap, part, target),
                               "not_asked": not_asked if snap == main else 0})
    report_items = []
    for item in items:
        answers, failures = {}, {}
        for eid, res in (item.get("results") or {}).items():
            got = {k: a.get("probabilities") for k, a in (res.get("answers") or {}).items()}
            if got:
                answers[eid] = got
            fail = res.get("request_failure") or next(iter((res.get("question_failures") or {}).values()), None)
            if fail:
                failures[eid] = fail
        report_items.append({"id": item["id"], "labels": item.get("labels") or {}, "answers": answers,
                             **({"failures": failures} if failures else {})})
    return {
        "run_id": record["id"], "created_at": record["created_at"], "dataset": record.get("dataset") or {},
        "target": target, "cost_usd": record.get("cost_usd"), "budget_stopped": bool(record.get("budget_stopped")),
        "engines": record["engines"],
        "questions": {k: {"type": q["type"], "instructions": q.get("instructions")} for k, q in questions.items()},
        "groups": groups, "items": report_items,
    }


def _group(key: str, q: dict, eid: str, snap: str | None, rows: list[tuple], target: float) -> dict[str, Any]:
    if q["type"] == "noul":
        obs = [(label, (a or {}).get("probabilities", {}).get("true") if a else None) for _, label, a, _ in rows]
        m = noul_metrics(obs, target)
        errors = []
        if m["whip"]:
            t = m["whip"]["t"]
            errors = [{"id": i, "label": label, "p": round(p, 4)} for (i, label, _, _), (_, p) in zip(rows, obs, strict=True)
                      if p is not None and (round(p, 3) >= t) != (label == "true")]
    else:
        opts = _options(q)
        obs = [(label, (a or {}).get("probabilities") if a else None) for _, label, a, _ in rows]
        m = choice_metrics(obs, opts, target, kind=q["type"])
        errors = []
        for (i, label, a, _), (_, probs) in zip(rows, obs, strict=True):
            if probs:
                top = max(probs, key=lambda o: probs[o])
                if top != label:
                    errors.append({"id": i, "label": label, "top": top, "p": round(probs[top], 4)})
    return {"question": key, "engine_id": eid, "snapshot": snap, "metrics": m,
            "errors": errors[:MAX_ERRORS], "errors_total": len(errors)}


TEXT = {
    "ru": {
        "title": "Отчёт калибровки", "dataset": "Набор", "items": "элементов", "labels": "метки", "run": "Прогон",
        "cost": "стоимость", "target": "Цель", "privacy": "Текстов звонков в отчёте нет: только id, метки и вероятности.",
        "question": "Вопрос", "engine": "Движок", "snapshot": "Снимок", "answered": "Ответили",
        "whip_t": "Порог whip-insight", "agreement": "Согласие", "precision": "Точность «да»", "recall": "Полнота «да»",
        "sel_t": "Порог по цели", "coverage": "Покрытие", "accuracy": "Точность", "curve": "Кривая порога",
        "errors": "Расхождения", "none": "нет", "not_asked": "не спрошено", "budget": "Прогон остановлен дневным бюджетом: часть элементов не спрошена.",
        "whip_note": "Порог whip-insight: вердикт «да» при P(true) ≥ t (округление до 3 знаков), согласие — по ответившим. "
                     "Порог по цели: наименьший t, при котором точность решённых (max(p, 1−p) ≥ t) не ниже цели.",
        "confusion": "Матрица ошибок (строка — метка, столбец — ответ)", "top1": "Top-1",
    },
    "en": {
        "title": "Calibration report", "dataset": "Dataset", "items": "items", "labels": "labels", "run": "Run",
        "cost": "cost", "target": "Target", "privacy": "No call text in this report: ids, labels and probabilities only.",
        "question": "Question", "engine": "Engine", "snapshot": "Snapshot", "answered": "Answered",
        "whip_t": "whip-insight threshold", "agreement": "Agreement", "precision": "Precision (yes)",
        "recall": "Recall (yes)", "sel_t": "Threshold for target", "coverage": "Coverage", "accuracy": "Accuracy",
        "curve": "Threshold curve", "errors": "Disagreements", "none": "none", "not_asked": "not asked",
        "budget": "The run was stopped by the daily budget: some items were not asked.",
        "whip_note": "whip-insight threshold: the verdict is yes when P(true) ≥ t (rounded to 3 decimals); agreement "
                     "over the answered items. Threshold for target: the smallest t whose decided items "
                     "(max(p, 1−p) ≥ t) reach the target accuracy.",
        "confusion": "Confusion matrix (row: label, column: answer)", "top1": "Top-1",
    },
}


def _answered(g: dict[str, Any], tx: dict[str, str]) -> str:
    m = g["metrics"]
    return f"{m['answered']}/{m['items']}" + (f" (+{g['not_asked']} {tx['not_asked']})" if g.get("not_asked") else "")


def _pct(x: float | None) -> str:
    return "—" if x is None else f"{x * 100:.1f} %"


def _num(x: Any) -> str:
    return "—" if x is None else str(x)


def render_markdown(report: dict[str, Any], lang: str = "ru") -> str:
    tx = TEXT.get(lang, TEXT["ru"])
    ds = report["dataset"]
    out = [f"# {tx['title']}: {ds.get('name', '?')} ({report['run_id']})", ""]
    out.append(f"- {tx['dataset']}: `{ds.get('name')}` — {ds.get('items')} {tx['items']}, {tx['labels']}: "
               f"{ds.get('label_source')}" + (f", {ds.get('source')}" if ds.get("source") else ""))
    engines = ", ".join(f"`{e['engine_id']}`" for e in report["engines"])
    cost = report.get("cost_usd")
    out.append(f"- {tx['run']}: {report['created_at']}, {engines}, {tx['cost']} ${cost:.6f}" if cost is not None
               else f"- {tx['run']}: {report['created_at']}, {engines}")
    out.append(f"- {tx['target']}: {_pct(report['target'])}. {tx['privacy']}")
    if report.get("budget_stopped"):
        out.append(f"- **{tx['budget']}**")
    out += ["", f"> {tx['whip_note']}", ""]
    for key, q in report["questions"].items():
        groups = [g for g in report["groups"] if g["question"] == key]
        if not groups:
            continue
        instr = q["instructions"] if isinstance(q["instructions"], str) else ""
        out += [f"## {tx['question']} `{key}` ({q['type']}){': ' + instr if instr else ''}", ""]
        if q["type"] == "noul":
            out.append(f"| {tx['engine']} | {tx['snapshot']} | {tx['answered']} | Brier | ECE | {tx['whip_t']} | "
                       f"{tx['agreement']} | {tx['precision']} | {tx['recall']} | {tx['sel_t']} | {tx['coverage']} |")
            out.append("|" + "---|" * 11)
            for g in groups:
                m, w, s = g["metrics"], g["metrics"].get("whip") or {}, g["metrics"].get("selective") or {}
                out.append(f"| `{g['engine_id']}` | {_num(g['snapshot'])} | {_answered(g, tx)} | "
                           f"{_num(m['brier'])} | {_num(m['ece'])} | {_num(w.get('t'))} | {_pct(w.get('agreement'))} | "
                           f"{_pct(w.get('precision'))} | {_pct(w.get('recall'))} | {_num(s.get('t'))} | "
                           f"{_pct(s.get('coverage'))} |")
            out += ["", f"### {tx['curve']} ({tx['agreement']})", ""]
            out.append(f"| {tx['engine']} | " + " | ".join(f"t={t}" for t in CURVE_POINTS) + " |")
            out.append("|" + "---|" * (len(CURVE_POINTS) + 1))
            for g in groups:
                by_t = {c["t"]: c for c in g["metrics"]["curve"]}
                out.append(f"| `{g['engine_id']}` | " + " | ".join(_pct((by_t.get(t) or {}).get("agreement"))
                                                                for t in CURVE_POINTS) + " |")
        else:
            out.append(f"| {tx['engine']} | {tx['snapshot']} | {tx['answered']} | {tx['top1']} | Brier | ECE | "
                       f"{tx['sel_t']} | {tx['coverage']} |")
            out.append("|" + "---|" * 8)
            for g in groups:
                m, s = g["metrics"], g["metrics"].get("selective") or {}
                out.append(f"| `{g['engine_id']}` | {_num(g['snapshot'])} | {_answered(g, tx)} | "
                           f"{_pct(m.get('accuracy'))} | {_num(m['brier'])} | {_num(m['ece'])} | {_num(s.get('t'))} | "
                           f"{_pct(s.get('coverage'))} |")
            for g in groups:
                conf = g["metrics"].get("confusion") or {}
                if not conf:
                    continue
                opts = g["metrics"]["options"]
                out += ["", f"### {tx['confusion']}: `{g['engine_id']}`", "",
                        "| | " + " | ".join(f"`{o}`" for o in opts) + " |", "|" + "---|" * (len(opts) + 1)]
                for lab in opts:
                    if lab in conf:
                        out.append(f"| `{lab}` | " + " | ".join(str(conf[lab].get(o, 0)) for o in opts) + " |")
        out += ["", f"### {tx['errors']}", ""]
        for g in groups:
            errs = ", ".join(f"`{e['id']}` ({e['label']}, {e.get('top', '')}{' ' if 'top' in e else ''}p={e['p']})"
                             for e in g["errors"]) or tx["none"]
            more = f" … +{g['errors_total'] - len(g['errors'])}" if g["errors_total"] > len(g["errors"]) else ""
            out.append(f"- `{g['engine_id']}`: {errs}{more}")
        out.append("")
    return "\n".join(out).rstrip() + "\n"
