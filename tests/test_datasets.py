"""§12.1–12.2: the dataset format, labels, upload parsing, presets in folders, the tag import and the synthesis."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
import yaml

from app.datasets import (DatasetError, DatasetExists, DatasetItem, DatasetNotFound, DatasetSpec, DatasetStore,
                          normalize_label, parse_items)
from app.presets import PresetNotFound, load_preset, normalize_id, preset_files
from app.schemas import Question
from scripts import import_std_tags, synth_dialogs

NOUL = {"is_tag": Question(type="noul", instructions="Есть ли тег?")}
CHOICE = Question(type="choice", instructions="?", criteria={"a": "A", "b": "B"})
SCORE = Question(type="score", instructions="?", criteria=["низко", "средне", "высоко"])


@pytest.fixture
def presets(tmp_path) -> Path:
    root = tmp_path / "presets"
    (root / "std_tags").mkdir(parents=True)
    (root / "synth").mkdir()
    (root / "std_tags" / "x_tag.yaml").write_text(yaml.safe_dump(
        {"title": "X", "tag": {"folder": "etiquette"},
         "questions": {"is_tag": {"type": "noul", "instructions": "X?", "criteria": {"true": "x"}}}}), encoding="utf-8")
    (root / "top.yaml").write_text(yaml.safe_dump({"questions": {"q": {"type": "noul", "instructions": "?"}}}))
    (root / "broken.yaml").write_text("questions: {q: {type: nope}}")
    shutil.copy(Path(__file__).parents[1] / "presets" / "synth" / "neutral_ru.yaml", root / "synth" / "neutral_ru.yaml")
    return root


@pytest.mark.parametrize("raw,label", [("true", "true"), ("Да", "true"), ("1", "true"), ("yes", "true"),
                                       ("false", "false"), ("нет", "false"), ("0", "false")])
def test_noul_labels_read_as_true_or_false(raw, label):
    assert normalize_label(NOUL["is_tag"], raw) == label


def test_choice_and_score_labels():
    assert normalize_label(CHOICE, "a") == "a"
    assert normalize_label(SCORE, "2") == "2" and normalize_label(SCORE, "средне") == "1"
    for q, bad in ((CHOICE, "z"), (SCORE, "3"), (SCORE, "очень"), (NOUL["is_tag"], "maybe")):
        with pytest.raises(ValueError):
            normalize_label(q, bad)
    with pytest.raises(ValueError, match="P6"):
        normalize_label(Question(type="text", instructions="?"), "x")


def test_jsonl_items_with_json_booleans_and_default_ids():
    items = parse_items('{"state": "a", "labels": {"is_tag": true}}\n\n{"id": "x", "state": "b", "labels": {"is_tag": "нет"}}',
                        "jsonl", NOUL)
    assert [(i.id, i.labels["is_tag"]) for i in items] == [("item-0001", "true"), ("x", "false")]


def test_csv_items_label_columns_and_meta():
    csv = "id,state,label,split\np1,оставьте сообщение,да,positive\np2,перезвоню,нет,negative\n"
    items = parse_items(csv, "csv", NOUL)
    assert [i.labels for i in items] == [{"is_tag": "true"}, {"is_tag": "false"}]
    assert items[0].meta == {"split": "positive"}
    two = {"a": NOUL["is_tag"], "b": CHOICE}
    with pytest.raises(DatasetError, match="label:<key>"):
        parse_items(csv, "csv", two)
    items = parse_items("state,label:a,label:b\nx,да,b\n", "csv", two)
    assert items[0].labels == {"a": "true", "b": "b"}


@pytest.mark.parametrize("content,fmt,msg", [
    ("not json", "jsonl", "line 1"),
    ("[1]", "jsonl", "JSON object"),
    ('{"state": ""}', "jsonl", "empty state"),
    ('{"id": "a b", "state": "x"}', "jsonl", "item id"),
    ('{"id": "a", "state": "x"}\n{"id": "a", "state": "y"}', "jsonl", "repeated"),
    ('{"state": "x", "labels": {"nope": "true"}}', "jsonl", "unknown question"),
    ('{"state": "x", "labels": {"is_tag": "maybe"}}', "jsonl", "true or false"),
    ("text\nx\n", "csv", "state"),
    ("", "jsonl", "no items"),
    ("x", "xml", "jsonl or csv"),
])
def test_bad_uploads_say_what_to_change(content, fmt, msg):
    with pytest.raises(DatasetError, match=msg):
        parse_items(content, fmt, NOUL)


def test_csv_quirks_say_what_to_change():
    """Review P4: extra fields were dropped, a repeated column won silently, `;` files read as headerless."""
    with pytest.raises(DatasetError, match="more fields than the header"):
        parse_items("state,label\nоставьте, пожалуйста,да\n", "csv", NOUL)
    with pytest.raises(DatasetError, match="repeated CSV columns: label"):
        parse_items("state,label,label\nx,да,нет\n", "csv", NOUL)
    items = parse_items("\ufeffid;state;label\na;Оставьте сообщение, после сигнала;да\n", "csv", NOUL)  # Excel, RU
    assert items[0].state == "Оставьте сообщение, после сигнала" and items[0].labels == {"is_tag": "true"}
    items = parse_items('state,label\n"Оставьте сообщение, после сигнала",да\n', "csv", NOUL)  # quoted comma
    assert items[0].state == "Оставьте сообщение, после сигнала"


def test_a_long_csv_cell_is_read():
    """A transcript of 140 000 characters in one cell: the csv default limit (131 072) made it a 500."""
    long = "Клиент: алло. " * 10_000
    items = parse_items(f'state,label\n"{long}",да\n', "csv", NOUL)
    assert items[0].state == long  # as it was, all 140 000 characters


def test_a_whole_float_label_is_its_integer():
    assert parse_items('{"state": "x", "labels": {"q": 2.0}}', "jsonl", {"q": SCORE})[0].labels == {"q": "2"}
    assert parse_items('{"state": "x", "labels": {"is_tag": 1.0}}', "jsonl", NOUL)[0].labels == {"is_tag": "true"}


def test_llm_labels_wait_for_p7():
    with pytest.raises(ValueError, match="P7"):
        DatasetSpec(name="a", question_set="top", label_source="llm:gpt")


def test_the_item_limit_is_5000():
    import app.datasets as ds

    assert ds.MAX_ITEMS == 5000
    with pytest.raises(DatasetError, match="5001 items: at most 5000"):
        parse_items("\n".join('{"state": "x"}' for _ in range(5001)), "jsonl", NOUL)
    assert len(parse_items("\n".join('{"state": "x"}' for _ in range(5000)), "jsonl", NOUL)) == 5000


def test_label_errors_are_reported_together():
    content = "\n".join(json.dumps({"id": f"i{n}", "state": "x", "labels": {"is_tag": "?"}}) for n in range(15))
    with pytest.raises(DatasetError, match=r"and 5 more"):
        parse_items(content, "jsonl", NOUL)


def test_too_many_items(monkeypatch):
    import app.datasets as ds

    monkeypatch.setattr(ds, "MAX_ITEMS", 3)
    with pytest.raises(DatasetError, match="at most 3"):
        parse_items("\n".join('{"state": "x"}' for _ in range(4)), "jsonl", NOUL)


def test_store_writes_reads_and_refuses_to_overwrite(tmp_path, presets):
    store = DatasetStore(tmp_path / "datasets", presets)
    spec = DatasetSpec(name="std_x", question_set="presets/std_tags/x_tag.yaml", label_source="test_phrases")
    items = [DatasetItem(id="pos-01", state="фраза", labels={"is_tag": "да"}),
             DatasetItem(id="neg-01", state="другая", labels={"is_tag": "false"})]
    store.create(spec, items)
    ds = store.load("std_x")
    assert [i.labels["is_tag"] for i in ds.items] == ["true", "false"]  # normalized on write
    assert ds.questions["is_tag"].criteria == {"true": "x"} and len(ds.items_sha256) == 64
    assert store.names() == ["std_x"]
    assert store.summary("std_x")["labels"] == {"is_tag": {"true": 1, "false": 1}}
    with pytest.raises(DatasetExists):
        store.create(spec, items)
    store.create(spec, items[:1], overwrite=True)
    assert len(store.load("std_x").items) == 1
    assert not list((tmp_path / "datasets" / "std_x").glob("*.tmp"))


@pytest.mark.parametrize("name", ["..", ".", "Upper", "a/b", "", "x" * 65])
def test_dataset_names(tmp_path, name):
    store = DatasetStore(tmp_path / "datasets")
    with pytest.raises(DatasetNotFound):
        store.load(name)
    with pytest.raises(ValueError):
        DatasetSpec(name=name, questions={"q": Question(type="noul", instructions="?")})


def test_a_spec_has_a_question_set_or_questions_not_both():
    with pytest.raises(ValueError, match="not both"):
        DatasetSpec(name="a")
    with pytest.raises(ValueError, match="not both"):
        DatasetSpec(name="a", question_set="top", questions={"q": Question(type="noul", instructions="?")})
    with pytest.raises(ValueError, match="label_source"):
        DatasetSpec(name="a", question_set="top", label_source="whatever")


def test_a_dataset_whose_preset_is_gone_says_so(tmp_path, presets):
    store = DatasetStore(tmp_path / "datasets", presets)
    store.create(DatasetSpec(name="d", question_set="top"), [DatasetItem(id="a", state="x")])
    (presets / "top.yaml").unlink()
    with pytest.raises(DatasetError, match="no preset"):
        store.load("d")
    assert "error" in store.summary("d")


def test_presets_in_folders(presets):
    ids = preset_files(presets)
    assert set(ids) == {"top", "broken", "std_tags/x_tag"}  # synth/ holds templates, not presets
    assert normalize_id("presets/std_tags/x_tag.yaml") == "std_tags/x_tag"
    assert load_preset("std_tags/x_tag", presets)["questions"]["is_tag"]["criteria"] == {"true": "x"}
    for bad in ("../etc/passwd", "std_tags/../top", "nope"):
        with pytest.raises(PresetNotFound):
            load_preset(bad, presets)


# --- the import of whip-insight's tags ------------------------------------------------------------------------------

@pytest.fixture
def whip(tmp_path) -> Path:
    tags = tmp_path / "whip" / "taxonomy" / "standard-dictionaries" / "tags"
    tags.mkdir(parents=True)
    (tags.parent / "folders.json").write_text(json.dumps({"folders": [{"key": "dialing", "name": "Дозвон"}]}))
    (tags / "std_answering_machine.json").write_text(json.dumps({
        "key": "std_answering_machine", "name": "Автоответчик", "folder": "dialing",
        "description": "Звонок попал на автоответчик: «оставьте сообщение после сигнала».",
        "tests": {"positive": ["Оставьте сообщение после сигнала.", "  "], "negative": ["Оставьте номер."]}},
        ensure_ascii=False), encoding="utf-8")
    (tags / "std_other.json").write_text(json.dumps({"key": "std_other", "name": "Другое", "description": "",
                                                     "tests": {}}), encoding="utf-8")
    return tmp_path / "whip"


def test_import_std_tags(tmp_path, whip, presets, capsys):
    data = tmp_path / "data"
    assert import_std_tags.main(["--whip-insight", str(whip), "--data-dir", str(data),
                                 "--presets-dir", str(presets)]) == 0
    preset = load_preset("std_tags/std_answering_machine", presets)
    q = preset["questions"]["is_tag"]
    assert q == {"type": "noul", "instructions": "Есть ли в звонке: Автоответчик?",
                 "criteria": {"true": "Звонок попал на автоответчик: «оставьте сообщение после сигнала»."}}
    assert preset["tag"]["folder_name"] == "Дозвон"
    assert "criteria" not in load_preset("std_tags/std_other", presets)["questions"]["is_tag"]  # no description
    ds = DatasetStore(data / "datasets", presets).load("std_answering_machine")
    assert [(i.id, i.labels["is_tag"]) for i in ds.items] == [("pos-01", "true"), ("neg-01", "false")]
    assert ds.spec.label_source == "test_phrases" and ds.spec.question_set == "presets/std_tags/std_answering_machine.yaml"
    assert "std_other: no test phrases" in capsys.readouterr().out
    # a second run keeps the dataset unless --overwrite
    assert import_std_tags.main(["--whip-insight", str(whip), "--data-dir", str(data), "--presets-dir", str(presets),
                                 "--tags", "std_answering_machine"]) == 0
    assert "kept" in capsys.readouterr().out
    assert import_std_tags.main(["--whip-insight", str(whip), "--data-dir", str(data), "--presets-dir", str(presets),
                                 "--tags", "nope"]) == 1
    assert import_std_tags.main(["--whip-insight", str(tmp_path / "none"), "--presets-only"]) == 2


def test_import_refuses_a_key_that_is_a_path(tmp_path, whip, presets, capsys):
    tags = whip / "taxonomy" / "standard-dictionaries" / "tags"
    (tags / "evil.json").write_text(json.dumps({"key": "../evil", "name": "x", "tests": {"positive": ["a"]}}))
    code = import_std_tags.main(["--whip-insight", str(whip), "--data-dir", str(tmp_path / "d"),
                                 "--presets-dir", str(presets)])
    assert code == 1 and "is not [a-z0-9_]" in capsys.readouterr().err
    assert not (presets / "evil.yaml").exists() and not (presets.parent / "evil.yaml").exists()
    # --tags names keys, not file stems
    (tags / "evil.json").write_text(json.dumps({"key": "std_renamed", "name": "y", "tests": {"positive": ["a"]}}))
    assert import_std_tags.main(["--whip-insight", str(whip), "--presets-only", "--presets-dir", str(presets),
                                 "--tags", "std_renamed"]) == 0
    assert (presets / "std_tags" / "std_renamed.yaml").is_file()


def test_the_committed_tag_presets_read():
    """presets/std_tags/ is generated from whip-insight and committed: every file is a valid question set."""
    ids = [i for i in preset_files() if i.startswith("std_tags/")]
    assert len(ids) == 56
    for i in ids:
        q = load_preset(i)["questions"]["is_tag"]
        assert q["type"] == "noul" and q["instructions"].startswith("Есть ли в звонке: ")


# --- synthetic dialogues --------------------------------------------------------------------------------------------

def test_synth_dialogs(tmp_path, presets):
    store = DatasetStore(tmp_path / "data" / "datasets", presets)
    store.create(DatasetSpec(name="std_x", question_set="std_tags/x_tag", label_source="test_phrases"),
                 [DatasetItem(id="pos-01", state="Добрый день!", labels={"is_tag": "true"}),
                  DatasetItem(id="neg-01", state="Алло", labels={"is_tag": "false"})])
    args = ["--from", "std_x", "--data-dir", str(tmp_path / "data"), "--presets-dir", str(presets), "--seed", "3"]
    assert synth_dialogs.main(args) == 0
    ds = store.load("synth_std_x")
    assert len(ds.items) == 4 and [i.id for i in ds.items][:2] == ["pos-01-d1", "pos-01-d2"]
    assert all(i.labels == {"is_tag": "true"} for i in ds.items[:2])
    # the tag's folder (etiquette) puts the phrase in the operator's mouth, exactly once
    assert all(i.state.count("Оператор: Добрый день!") == 1 for i in ds.items[:2])
    assert ds.spec.question_set == "std_tags/x_tag" and "seed 3" in ds.spec.source
    first = [i.state for i in ds.items]
    assert synth_dialogs.main([*args, "--overwrite"]) == 0
    assert [i.state for i in store.load("synth_std_x").items] == first  # deterministic for a seed
    assert synth_dialogs.main(args) == 1  # exists, no --overwrite


def test_every_synth_dialog_has_one_slot():
    t = synth_dialogs.load_template("neutral_ru")
    assert len(t["dialogs"]) >= 4 and all(d.count("{line}") == 1 for d in t["dialogs"])
    assert synth_dialogs.speaker(t, {"folder": "etiquette"}) == "Оператор"
    assert synth_dialogs.speaker(t, {"folder": "dialing"}) == "Клиент"
    # the tag's own speaker wins over its folder: operator-side sales tags speak as the operator
    assert synth_dialogs.speaker(t, {"folder": "sales", "speaker": "operator"}) == "Оператор"
    assert synth_dialogs.speaker(t, {"folder": "etiquette", "speaker": "client"}) == "Клиент"


@pytest.mark.parametrize("description,speaker", [
    ("… Проверяется в речи оператора (область «участник»); в анкете …", "operator"),
    ("Проверяется только у оператора (область «участник»): у клиента …", "operator"),
    ("Проверяется у оператора (область «участник») как пункт начала разговора.", "operator"),
    ("Проверяется в речи клиента (область «участник»).", "client"),
    ("Проверяется у клиента (область «участник»); в форме …", "client"),
    ("Звонок попал на автоответчик.", None),
])
def test_the_speaker_is_read_from_the_description(description, speaker):
    assert import_std_tags.speaker_of(description) == speaker


def test_operator_side_tags_synthesize_as_the_operator():
    """Review P4: by folder alone, std_secretary_bypass, std_needs_discovery, std_objection_handling,
    std_next_contact, std_cp_call and std_escalation put an operator's phrase in the client's mouth."""
    t = synth_dialogs.load_template("neutral_ru")
    tags = {i: load_preset(i)["tag"] for i in preset_files() if i.startswith("std_tags/")}
    for key in ("std_secretary_bypass", "std_needs_discovery", "std_objection_handling", "std_next_contact",
                "std_cp_call", "std_escalation"):
        assert tags[f"std_tags/{key}"]["speaker"] == "operator", key
        assert synth_dialogs.speaker(t, tags[f"std_tags/{key}"]) == "Оператор", key
    for tag in tags.values():  # whatever a description says, the synthesis follows it
        if tag.get("speaker"):
            assert synth_dialogs.speaker(t, tag) == {"operator": "Оператор", "client": "Клиент"}[tag["speaker"]]
