"""Question presets (docs/ARCHITECTURE.md §4.4): presets/<id>.yaml and presets/<folder>/<id>.yaml.

A preset holds `questions` (and optionally a `state`) for the Workbench, and is a dataset's question set. Its id is
its path under presets/ without `.yaml`: `call_kind`, `std_tags/std_answering_machine`. presets/synth/ holds the
synthetic-dialogue templates of scripts/synth_dialogs.py, not question presets.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from app.schemas import Question
from app.settings import ROOT

PRESETS = ROOT / "presets"
PRESET_ID = re.compile(r"[a-z0-9_]{1,64}(/[a-z0-9_]{1,64})?")
NOT_PRESETS = {"synth"}


class PresetError(ValueError):
    """A preset that does not read."""


class PresetNotFound(PresetError):
    """No preset by that id (or no valid id at all)."""


def preset_files(root: Path = PRESETS) -> dict[str, Path]:
    out = {}
    for path in sorted(root.rglob("*.yaml")):
        rel = path.relative_to(root).with_suffix("").as_posix()
        if PRESET_ID.fullmatch(rel) and rel.split("/")[0] not in NOT_PRESETS:
            out[rel] = path
    return out


def normalize_id(ref: str) -> str:
    """A preset id from its id or its path (`presets/std_tags/x.yaml` → `std_tags/x`), as dataset.yaml may name it."""
    ref = ref.strip().removeprefix("./").removeprefix("presets/").removesuffix(".yaml")
    if not PRESET_ID.fullmatch(ref):
        raise PresetNotFound(f"preset id {ref!r} is not presets/<id>.yaml or presets/<folder>/<id>.yaml")
    return ref


def read_preset(path: Path, preset_id: str) -> dict[str, Any]:
    """A preset with its questions in the internal form (d1's `choice` + `multiple` becomes `multi`, §6.1); a broken
    preset fails loudly here, not in the middle of a run."""
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise PresetError("a preset is a mapping")
    questions = {k: Question.model_validate(q).model_dump(mode="json", exclude_none=True)
                 for k, q in (data.get("questions") or {}).items()}
    return {**data, "id": preset_id, "questions": questions}


def load_preset(ref: str, root: Path = PRESETS) -> dict[str, Any]:
    preset_id = normalize_id(ref)
    path = preset_files(root).get(preset_id)
    if path is None:
        raise PresetNotFound(f"no preset {preset_id!r} under presets/")
    try:
        return read_preset(path, preset_id)
    except (ValueError, yaml.YAMLError) as e:
        raise PresetError(f"preset {preset_id!r} does not read: {str(e)[:300]}") from None


def list_presets(root: Path = PRESETS) -> list[dict[str, Any]]:
    out = []
    for pid, path in preset_files(root).items():
        group = pid.split("/")[0] if "/" in pid else ""
        try:
            p = read_preset(path, pid)
            out.append({"id": pid, "group": group, "title": p.get("title", pid), "questions": list(p["questions"]),
                        "has_state": bool(p.get("state"))})
        except (ValueError, yaml.YAMLError) as e:
            out.append({"id": pid, "group": group, "title": pid, "error": str(e)[:200]})
    return out
