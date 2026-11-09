"""Labelled datasets (docs/DESIGN.md §12.1): datasets/<name>/dataset.yaml + items.jsonl.

dataset.yaml names the question set — a preset (`question_set: presets/std_tags/std_answering_machine.yaml`) or
inline `questions` — and where the labels come from. Each line of items.jsonl is one item:
`{"id": "pos-01", "state": "…", "labels": {"is_tag": "true"}, "meta": {"split": "positive"}}`.

Labels are checked against the question set when a dataset is written and when it is read: `noul` takes true/false
(yes/no, да/нет, 1/0 are read as such), `choice` an option id, `score` a level index or the level's text. Metrics of
P4 cover these three types; labels for the others arrive with their modes (P6).
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from app.presets import PRESETS, PresetError, load_preset
from app.schemas import MAX_STATE_DEPTH, Question, json_depth, valid_key
from app.store import write_durably

NAME = re.compile(r"[a-z0-9][a-z0-9_.-]{0,63}")
ITEM_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,99}")
MAX_ITEMS = 5000
LABELLED_TYPES = ("noul", "choice", "score")
# llm:<engine_id> arrives with the LLM engines (P7): its report must say "agreement with the LLM", never accuracy
# (whip-insight R12.3), and P4's report has no such wording yet — so P4 refuses it rather than mislabel it
LABEL_SOURCE = re.compile(r"test_phrases|manual")
CSV_FIELD_LIMIT = 16 * 1024 * 1024  # the UI's body limit: a whole transcript fits in one cell
TRUE_WORDS = {"true", "1", "yes", "да", "y"}
FALSE_WORDS = {"false", "0", "no", "нет", "n"}


class DatasetError(ValueError):
    """A dataset that cannot be written or read; the message says what to change."""


class DatasetNotFound(DatasetError):
    pass


class DatasetExists(DatasetError):
    pass


def valid_name(name: str) -> bool:
    return bool(NAME.fullmatch(name)) and set(name) != {"."}


class DatasetItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    state: str | dict | list
    labels: dict[str, str] = Field(default_factory=dict)
    meta: dict[str, Any] = Field(default_factory=dict)

    @field_validator("labels", mode="before")
    @classmethod
    def _label_text(cls, v: Any) -> Any:
        # JSON true/false and numbers arrive as themselves; a label is kept as text ("true", "2")
        if isinstance(v, dict):
            def text(x: Any) -> Any:
                if isinstance(x, bool):
                    return str(x).lower()
                if isinstance(x, float) and x.is_integer():
                    return str(int(x))  # a JSON 1.0 is the label 1
                return str(x) if isinstance(x, int | float) else x

            return {k: text(x) for k, x in v.items()}
        return v

    @model_validator(mode="after")
    def _check(self) -> DatasetItem:
        if not ITEM_ID.fullmatch(self.id):
            raise ValueError(f"item id {self.id!r}: letters, digits and . _ : -, up to 100")
        if isinstance(self.state, str) and not self.state.strip() or not self.state:
            raise ValueError(f"item {self.id}: an empty state")
        if not isinstance(self.state, str) and json_depth(self.state) > MAX_STATE_DEPTH:
            raise ValueError(f"item {self.id}: state nested deeper than {MAX_STATE_DEPTH} levels")
        return self


class DatasetSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    title: str | None = None
    question_set: str | None = None  # a preset id or its path under presets/
    questions: dict[str, Question] | None = None  # inline, instead of a preset
    label_source: str = "manual"  # test_phrases | manual | llm:<engine_id> (P7)
    language: str | None = None
    source: str | None = None  # where the items came from, for the report
    description: str | None = None

    @model_validator(mode="after")
    def _check(self) -> DatasetSpec:
        if not valid_name(self.name):
            raise ValueError(f"dataset name {self.name!r}: lowercase letters, digits and . _ -, up to 64")
        if not LABEL_SOURCE.fullmatch(self.label_source):
            raise ValueError("label_source is test_phrases or manual (labels by an LLM arrive with P7)")
        if (self.question_set is None) == (self.questions is None):
            raise ValueError("a dataset names a question_set (a preset) or has inline questions, not both")
        if self.questions is not None:
            bad = [k for k in self.questions if not valid_key(k)]
            if bad or not self.questions:
                raise ValueError(f"question keys must be valid keys: {bad[:5]}")
        return self


class DatasetUpload(BaseModel):
    """POST /api/datasets: a JSONL or CSV file read by the browser, and the question set its labels answer."""

    model_config = ConfigDict(extra="forbid")

    name: str
    title: str | None = None
    question_set: str | None = None
    questions: dict[str, Question] | None = None
    label_source: str = "manual"
    language: str | None = None
    format: Literal["jsonl", "csv"]
    content: str
    overwrite: bool = False

    def spec(self) -> DatasetSpec:
        return DatasetSpec(name=self.name, title=self.title, question_set=self.question_set, questions=self.questions,
                           label_source=self.label_source, language=self.language, source="upload")


def normalize_label(q: Question, raw: str) -> str:
    """A label as the metrics read it: noul "true"/"false", choice an option id, score a level index."""
    text = str(raw).strip()
    if q.type == "noul":
        low = text.lower()
        if low in TRUE_WORDS:
            return "true"
        if low in FALSE_WORDS:
            return "false"
        raise ValueError(f"a noul label is true or false, not {text!r}")
    if q.type == "choice":
        if text in (q.criteria or {}):
            return text
        raise ValueError(f"{text!r} is not an option of this choice")
    if q.type == "score":
        levels = list(q.criteria or [])
        if text.isdigit() and int(text) < len(levels):
            return str(int(text))
        if text in levels:
            return str(levels.index(text))
        raise ValueError(f"{text!r} is not a level index (0–{len(levels) - 1}) or a level of this score")
    raise ValueError(f"labels for {q.type} questions arrive with their mode (P6); P4 measures {', '.join(LABELLED_TYPES)}")


def check_labels(items: list[DatasetItem], questions: dict[str, Question]) -> list[DatasetItem]:
    """Labels normalized against the question set; every problem in one message (up to ten)."""
    errors, out = [], []
    for item in items:
        labels = {}
        for key, raw in item.labels.items():
            q = questions.get(key)
            if q is None:
                errors.append(f"item {item.id}: label for an unknown question {key!r}")
                continue
            try:
                labels[key] = normalize_label(q, raw)
            except ValueError as e:
                errors.append(f"item {item.id}, {key}: {e}")
        out.append(item.model_copy(update={"labels": labels}))
    if errors:
        more = f" (and {len(errors) - 10} more)" if len(errors) > 10 else ""
        raise DatasetError("; ".join(errors[:10]) + more)
    return out


def parse_items(content: str, fmt: str, questions: dict[str, Question]) -> list[DatasetItem]:
    """Items from an uploaded JSONL or CSV text. CSV: a `state` column; `id` (else row numbers); `label` for a
    one-question set or `label:<key>` per question; any other column goes to `meta`."""
    rows: list[dict[str, Any]] = []
    if fmt == "jsonl":
        for n, line in enumerate(content.splitlines(), 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except ValueError as e:
                raise DatasetError(f"line {n}: not JSON ({e.msg})") from None
            if not isinstance(row, dict):
                raise DatasetError(f"line {n}: an item is a JSON object")
            rows.append({"id": f"item-{n:04d}", **row})
    elif fmt == "csv":
        rows = _csv_rows(content, questions)
    else:
        raise DatasetError("format is jsonl or csv")
    return _items_of(rows, questions)


def csv_delimiter(content: str) -> str:
    """`,`, `;` (Excel in a Russian locale) or a tab: the one the header line uses most."""
    header = content.lstrip("\ufeff").split("\n", 1)[0]
    counts = {d: header.count(d) for d in (",", ";", "\t")}
    best = max(counts, key=lambda d: counts[d])
    return best if counts[best] else ","


def _csv_rows(content: str, questions: dict[str, Question]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    csv.field_size_limit(CSV_FIELD_LIMIT)  # the default 131,072 characters made a long transcript a 500
    reader = csv.DictReader(io.StringIO(content.lstrip("\ufeff")), delimiter=csv_delimiter(content))
    n = 0
    try:
        fields = reader.fieldnames or []
        if "state" not in fields:
            raise DatasetError("a CSV needs a header with a `state` column (separated by `,` `;` or a tab)")
        dup = sorted({f for f in fields if fields.count(f) > 1})
        if dup:
            raise DatasetError(f"repeated CSV columns: {', '.join(dup)}")
        only = next(iter(questions)) if len(questions) == 1 else None
        for n, rec in enumerate(reader, 1):
            if None in rec:
                raise DatasetError(f"CSV row {n}: more fields than the header (an unquoted separator in a cell?)")
            row: dict[str, Any] = {"id": rec.get("id") or f"item-{n:04d}", "state": rec.get("state") or "",
                                   "labels": {}, "meta": {}}
            for col, value in rec.items():
                if col in ("id", "state") or col is None or value in (None, ""):
                    continue
                if col == "label":
                    if only is None:
                        raise DatasetError("`label` fits a one-question set; name the question: `label:<key>`")
                    row["labels"][only] = value
                elif col.startswith("label:"):
                    row["labels"][col[6:]] = value
                else:
                    row["meta"][col] = value
            rows.append(row)
    except csv.Error as e:
        raise DatasetError(f"CSV row {n + 1}: {e}") from None
    return rows


def _items_of(rows: list[dict[str, Any]], questions: dict[str, Question]) -> list[DatasetItem]:
    if not rows:
        raise DatasetError("no items")
    if len(rows) > MAX_ITEMS:
        raise DatasetError(f"{len(rows)} items: at most {MAX_ITEMS} in one dataset")
    items = []
    for row in rows:
        try:
            items.append(DatasetItem.model_validate(row))
        except ValidationError as e:
            err = e.errors()[0]
            raise DatasetError(f"item {row.get('id')}: {err['msg']}") from None
    ids = [i.id for i in items]
    dup = sorted({i for i in ids if ids.count(i) > 1})
    if dup:
        raise DatasetError(f"repeated item ids: {', '.join(dup[:5])}")
    return check_labels(items, questions)


class Dataset(BaseModel):
    spec: DatasetSpec
    questions: dict[str, Question]
    items: list[DatasetItem]
    items_sha256: str

    def label_counts(self) -> dict[str, dict[str, int]]:
        out: dict[str, dict[str, int]] = {k: {} for k in self.questions}
        for item in self.items:
            for k, v in item.labels.items():
                out[k][v] = out[k].get(v, 0) + 1
        return out


class DatasetStore:
    def __init__(self, root: Path, presets_root: Path = PRESETS):
        self.root, self.presets_root = root, presets_root

    def _dir(self, name: str) -> Path:
        if not valid_name(name):
            raise DatasetNotFound(f"no dataset {name!r}")
        return self.root / name

    def names(self) -> list[str]:
        if not self.root.is_dir():
            return []
        return sorted(p.name for p in self.root.iterdir() if (p / "dataset.yaml").is_file() and valid_name(p.name))

    def questions_of(self, spec: DatasetSpec) -> dict[str, Question]:
        if spec.questions is not None:
            return spec.questions
        try:
            preset = load_preset(spec.question_set or "", self.presets_root)
        except PresetError as e:
            raise DatasetError(f"dataset {spec.name}: {e}") from None
        if not preset["questions"]:
            raise DatasetError(f"dataset {spec.name}: preset {preset['id']} has no questions")
        return {k: Question.model_validate(q) for k, q in preset["questions"].items()}

    def load(self, name: str) -> Dataset:
        d = self._dir(name)
        try:
            raw_spec = yaml.safe_load((d / "dataset.yaml").read_text(encoding="utf-8"))
            text = (d / "items.jsonl").read_text(encoding="utf-8")
        except FileNotFoundError:
            raise DatasetNotFound(f"no dataset {name!r}") from None
        except (OSError, yaml.YAMLError, UnicodeDecodeError) as e:
            raise DatasetError(f"dataset {name}: {type(e).__name__}") from None
        try:
            spec = DatasetSpec.model_validate({**(raw_spec or {}), "name": name})
        except ValidationError as e:
            raise DatasetError(f"dataset {name}: dataset.yaml — {e.errors()[0]['msg']}") from None
        questions = self.questions_of(spec)
        items = parse_items(text, "jsonl", questions)
        return Dataset(spec=spec, questions=questions, items=items,
                       items_sha256=hashlib.sha256(text.encode()).hexdigest())

    def summary(self, name: str) -> dict[str, Any]:
        try:
            ds = self.load(name)
        except DatasetError as e:
            return {"name": name, "error": str(e)[:300]}
        return {"name": name, "title": ds.spec.title, "items": len(ds.items), "label_source": ds.spec.label_source,
                "language": ds.spec.language, "question_set": ds.spec.question_set, "source": ds.spec.source,
                "questions": {k: q.type for k, q in ds.questions.items()}, "labels": ds.label_counts()}

    def create(self, spec: DatasetSpec, items: list[DatasetItem], overwrite: bool = False) -> Path:
        d = self._dir(spec.name)
        if (d / "dataset.yaml").exists() and not overwrite:
            raise DatasetExists(f"dataset {spec.name} exists; overwrite it explicitly")
        items = check_labels(items, self.questions_of(spec))
        d.mkdir(parents=True, exist_ok=True)
        lines = "".join(json.dumps(i.model_dump(exclude_defaults=True), ensure_ascii=False) + "\n" for i in items)
        write_durably(d / "items.jsonl", lines)  # the items first: a dataset.yaml never points at missing items
        write_durably(d / "dataset.yaml", yaml.safe_dump(spec.model_dump(mode="json", exclude_none=True),
                                                         allow_unicode=True, sort_keys=False))
        return d
