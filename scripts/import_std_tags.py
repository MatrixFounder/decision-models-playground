"""Import whip-insight's standard tags as presets and labelled datasets (docs/TASK.md §5.3, UC-3).

    .venv/bin/python scripts/import_std_tags.py --whip-insight ../whip-insight [--tags std_answering_machine,…]
                                                [--presets-only] [--overwrite]

Reads `taxonomy/standard-dictionaries/tags/*.json` (and `folders.json`) and writes, per tag:
- presets/std_tags/<key>.yaml — the question: a `noul` "Есть ли в звонке: <name>?", criteria.true = the tag's
  description (whip-insight puts `criteria` in a decision request only when the definition has it), and whose
  speech the tag reads when the description says so (`tag.speaker`, for scripts/synth_dialogs.py);
- DATA_DIR/datasets/<key>/ — the tag's test phrases: `tests.positive` → true, `tests.negative` → false.

Read-only on whip-insight. An existing dataset is kept unless --overwrite. The presets are regenerated each time.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.datasets import DatasetError, DatasetExists, DatasetItem, DatasetSpec, DatasetStore  # noqa: E402
from app.presets import PRESETS  # noqa: E402

TAGS = Path("taxonomy/standard-dictionaries/tags")
QUESTION_KEY = "is_tag"
KEY = re.compile(r"[a-z0-9_]{1,64}")  # a tag key names a file: no path in it
# whip-insight's descriptions say whose speech a tag reads: «Проверяется у оператора», «в речи клиента»
SPEAKER = re.compile(r"Проверяется\s+(?:только\s+)?(?:у|в\s+речи)\s+(оператора|клиента)")


def speaker_of(description: str) -> str | None:
    """`operator` or `client` when the description names it; the synthesis puts the phrase in that mouth."""
    m = SPEAKER.search(description or "")
    return {"оператора": "operator", "клиента": "client"}[m.group(1)] if m else None


def preset_of(tag: dict[str, Any], folders: dict[str, str], source: str) -> dict[str, Any]:
    question: dict[str, Any] = {"type": "noul", "instructions": f"Есть ли в звонке: {tag['name']}?"}
    if (tag.get("description") or "").strip():
        question["criteria"] = {"true": tag["description"].strip()}
    return {
        "title": f"Тег: {tag['name']}",
        "source": source,
        "tag": {"key": tag["key"], "name": tag["name"], "folder": tag.get("folder"),
                "folder_name": folders.get(tag.get("folder") or ""),
                **({"speaker": sp} if (sp := speaker_of(tag.get("description") or "")) else {})},
        "questions": {QUESTION_KEY: question},
    }


def items_of(tag: dict[str, Any]) -> list[DatasetItem]:
    tests = tag.get("tests") or {}
    out = []
    for split, label, prefix in (("positive", "true", "pos"), ("negative", "false", "neg")):
        phrases = [p for p in tests.get(split) or [] if isinstance(p, str) and p.strip()]
        width = max(2, len(str(len(phrases))))
        for n, phrase in enumerate(phrases, 1):
            out.append(DatasetItem(id=f"{prefix}-{n:0{width}d}", state=phrase.strip(), labels={QUESTION_KEY: label},
                                   meta={"split": split}))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--whip-insight", type=Path, required=True, help="the whip-insight checkout")
    ap.add_argument("--tags", help="comma-separated tag keys (default: all)")
    ap.add_argument("--presets-only", action="store_true", help="write the presets, not the datasets")
    ap.add_argument("--overwrite", action="store_true", help="replace existing datasets")
    ap.add_argument("--data-dir", type=Path, help="DATA_DIR (default: the app's setting)")
    ap.add_argument("--presets-dir", type=Path, default=PRESETS)
    args = ap.parse_args(argv)

    tags_dir = args.whip_insight / TAGS
    if not tags_dir.is_dir():
        print(f"no {TAGS} under {args.whip_insight}", file=sys.stderr)
        return 2
    try:
        folders = {f["key"]: f["name"] for f in json.loads((tags_dir.parent / "folders.json").read_text())["folders"]}
    except (OSError, ValueError, KeyError, TypeError):
        folders = {}
    wanted = {t.strip() for t in args.tags.split(",")} if args.tags else None
    if args.data_dir is None and not args.presets_only:
        from app.settings import get_settings

        args.data_dir = get_settings().data_dir
    store = DatasetStore(args.data_dir / "datasets", args.presets_dir) if not args.presets_only else None
    out_dir = args.presets_dir / "std_tags"
    out_dir.mkdir(parents=True, exist_ok=True)
    written = kept = failed = 0
    keys: set[str] = set()
    for path in sorted(tags_dir.glob("*.json")):
        tag = json.loads(path.read_text(encoding="utf-8"))
        key = tag.get("key") or path.stem
        if not isinstance(key, str) or not KEY.fullmatch(key):
            print(f"{path.name}: key {key!r} is not [a-z0-9_]{{1,64}} — skipped", file=sys.stderr)
            failed += 1
            continue
        keys.add(key)
        if wanted is not None and key not in wanted:
            continue
        source = f"whip-insight {TAGS.as_posix()}/{path.name}"
        preset = preset_of({**tag, "key": key}, folders, source)
        header = f"# Generated by scripts/import_std_tags.py from {source}: do not edit, run the script again.\n"
        (out_dir / f"{key}.yaml").write_text(header + yaml.safe_dump(preset, allow_unicode=True, sort_keys=False),
                                             encoding="utf-8")
        if store is None:
            continue
        items = items_of(tag)
        if not items:
            print(f"{key}: no test phrases, no dataset")
            continue
        spec = DatasetSpec(name=key, title=f"Тег: {tag['name']}", question_set=f"presets/std_tags/{key}.yaml",
                           label_source="test_phrases", language="ru", source=source)
        try:
            store.create(spec, items, overwrite=args.overwrite)
            written += 1
            pos = sum(1 for i in items if i.labels[QUESTION_KEY] == "true")
            print(f"{key}: {len(items)} items ({pos} true, {len(items) - pos} false)")
        except DatasetExists:
            kept += 1
            print(f"{key}: kept — the dataset exists (--overwrite replaces it)")
        except DatasetError as e:
            failed += 1
            print(f"{key}: not written — {e}", file=sys.stderr)
    if wanted is not None:
        missing = wanted - keys
        if missing:
            print(f"not found: {', '.join(sorted(missing))}", file=sys.stderr)
            return 1
    print(f"presets in {out_dir}; datasets written {written}, kept {kept}, failed {failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
