"""Synthetic dialogues from a labelled phrase dataset (docs/DESIGN.md §12.2).

    .venv/bin/python scripts/synth_dialogs.py --from std_answering_machine [--template neutral_ru]
                                              [--per-phrase 2] [--seed 7] [--name synth_std_answering_machine]

Each phrase of the source dataset (an imported tag: scripts/import_std_tags.py) is put into `--per-phrase` neutral
dialogues of presets/synth/<template>.yaml as one line, said by the operator or the client by the tag's folder. The
label is the phrase's own: the dialogues carry no tag. The result is a dataset with the same question set, so a model
is measured on a phrase in context, not alone. Deterministic for a seed.
"""

from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.datasets import DatasetError, DatasetItem, DatasetSpec, DatasetStore  # noqa: E402
from app.presets import PRESETS, load_preset  # noqa: E402


def load_template(name: str, presets_dir: Path = PRESETS) -> dict[str, Any]:
    path = presets_dir / "synth" / f"{name}.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    dialogs = [d for d in data.get("dialogs") or [] if isinstance(d, str) and d.count("{line}") == 1]
    if not dialogs:
        raise ValueError(f"{path}: no dialog with exactly one {{line}}")
    return {**data, "dialogs": dialogs}


def speaker(template: dict[str, Any], tag: dict[str, Any]) -> str:
    """Whose line the phrase is: the tag's own `speaker` (whip-insight's «Проверяется в речи оператора»), else by
    its folder. By folder alone, six operator-side sales and escalation tags spoke as the client (review P4)."""
    roles = template.get("roles") or {}
    if tag.get("speaker") in roles:
        return roles[tag["speaker"]]
    speakers = template.get("speakers") or {}
    return speakers.get(tag.get("folder") or "", speakers.get("default", "Клиент"))


def synthesize(items: list[DatasetItem], template: dict[str, Any], tag: dict[str, Any], per_phrase: int,
               seed: int) -> list[DatasetItem]:
    rng = random.Random(seed)
    who = speaker(template, tag)
    out = []
    for item in items:
        if not isinstance(item.state, str):
            continue
        phrase = " ".join(item.state.split())
        for n, dialog in enumerate(rng.sample(template["dialogs"], min(per_phrase, len(template["dialogs"]))), 1):
            state = dialog.replace("{line}", f"{who}: {phrase}").strip()
            out.append(DatasetItem(id=f"{item.id}-d{n}", state=state, labels=item.labels,
                                   meta={**item.meta, "phrase": item.id, "template": template.get("id")}))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--from", dest="source", required=True, help="the phrase dataset")
    ap.add_argument("--template", default="neutral_ru")
    ap.add_argument("--per-phrase", type=int, default=2)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--name", help="the new dataset (default: synth_<source>)")
    ap.add_argument("--overwrite", action="store_true")
    ap.add_argument("--data-dir", type=Path)
    ap.add_argument("--presets-dir", type=Path, default=PRESETS)
    args = ap.parse_args(argv)
    if args.data_dir is None:
        from app.settings import get_settings

        args.data_dir = get_settings().data_dir
    store = DatasetStore(args.data_dir / "datasets", args.presets_dir)
    try:
        src = store.load(args.source)
        template = load_template(args.template, args.presets_dir)
        tag = {}
        if src.spec.question_set:
            tag = load_preset(src.spec.question_set, args.presets_dir).get("tag") or {}
        items = synthesize(src.items, template, tag, max(1, args.per_phrase), args.seed)
        name = args.name or f"synth_{args.source}"[:64]
        spec = DatasetSpec(name=name, title=f"{src.spec.title or src.spec.name} — в диалогах", question_set=src.spec.question_set,
                           questions=None if src.spec.question_set else src.spec.questions,
                           label_source=src.spec.label_source, language=template.get("language") or src.spec.language,
                           source=f"synth:{template.get('id')} seed {args.seed} from {src.spec.name}")
        store.create(spec, items, overwrite=args.overwrite)
    except (DatasetError, OSError, ValueError, yaml.YAMLError) as e:
        print(f"synth: {e}", file=sys.stderr)
        return 1
    print(f"{name}: {len(items)} items from {len(src.items)} phrases × {args.per_phrase} dialogues")
    return 0


if __name__ == "__main__":
    sys.exit(main())
