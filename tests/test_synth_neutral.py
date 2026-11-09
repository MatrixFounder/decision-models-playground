"""The synthetic dialogues must not fire a standard tag on their own (review P4): one line of the template matched
`std_broken_promises` («должен был приехать»), another `std_send_documents` («по почте»), and the dialogue's label —
the phrase's own — was wrong for those tags. Every line runs through whip-insight's include rules here.

The rules live in the whip-insight checkout (WHIP_INSIGHT, else next to this one); without it the test is skipped (the template changes
rarely, and `make import-tags` needs that checkout anyway)."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest

from scripts.synth_dialogs import load_template

# WHIP_INSIGHT (as `make import-tags WHIP=…`) names the checkout; by default it sits next to this repository
WHIP = Path(os.environ.get("WHIP_INSIGHT") or Path(__file__).resolve().parents[2] / "whip-insight")
TAGS = WHIP / "taxonomy" / "standard-dictionaries" / "tags"


def words(text: str) -> list[str]:
    """Whole words, case and punctuation ignored, ё folded to е (whip-insight's tag README)."""
    return re.findall(r"[a-zа-я0-9]+", text.lower().replace("ё", "е"))


def word_matches(pattern: str, word: str) -> bool:
    if pattern.endswith("*"):  # a stem takes up to 4 more characters
        stem = pattern[:-1]
        return word.startswith(stem) and len(word) - len(stem) <= 4
    return word == pattern


def phrase_hits(line: list[str], value: str, gap: int = 0) -> bool:
    parts = [w.replace("ё", "е") for w in re.findall(r"[a-zа-яё0-9]+\*?", value.lower())]
    if not parts:
        return False
    for start, w in enumerate(line):
        if not word_matches(parts[0], w):
            continue
        at, ok = start, True
        for p in parts[1:]:
            nxt = next((k for k in range(at + 1, min(len(line), at + 2 + gap)) if word_matches(p, line[k])), None)
            if nxt is None:
                ok = False
                break
            at = nxt
        if ok:
            return True
    return False


def rule_hits(line: list[str], rule: dict) -> bool:
    if rule.get("kind") == "phrase":
        return phrase_hits(line, rule["value"], rule.get("max_gap_words", 0))
    if rule.get("kind") == "all_of":
        return all(phrase_hits(line, p["value"], p.get("max_gap_words", 0)) for p in rule["pieces"])
    return False  # regex rules are admin-only; the standard tags have none


def test_the_matcher_reads_whip_insights_rules():
    assert phrase_hits(words("Курьер должен был приехать сегодня"), "должен был приехать", 2)
    assert phrase_hits(words("Оставьте ваше сообщение после звукового сигнала"), "сообщени* после сигнала", 2)
    assert not phrase_hits(words("Курьер приедет сегодня"), "должен был приехать", 2)
    assert phrase_hits(words("пришёл"), "пришел") and not phrase_hits(words("благодарностями"), "благодар*")


@pytest.mark.skipif(not TAGS.is_dir(), reason="no whip-insight checkout next to this one")
def test_no_template_line_fires_a_standard_tag():
    rules = {p.stem: json.loads(p.read_text(encoding="utf-8"))["definition"].get("include", [])
             for p in sorted(TAGS.glob("*.json"))}
    assert len(rules) == 56
    hits = []
    for path in sorted((Path(__file__).resolve().parents[1] / "presets" / "synth").glob("*.yaml")):
        for n, dialog in enumerate(load_template(path.stem)["dialogs"], 1):
            for raw in dialog.replace("{line}", "").splitlines():
                line = words(raw.split(":", 1)[-1])
                hits += [f"{path.stem} dialog {n}: {raw.strip()!r} → {tag} {r.get('rule_id')}"
                         for tag, rs in rules.items() for r in rs if rule_hits(line, r)]
    assert not hits, "\n".join(hits)
