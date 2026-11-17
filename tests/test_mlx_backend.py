"""The real MLX backend and the snapshot dates, with mlx-vlm, the HF cache and mlx.core replaced — review P2."""

from __future__ import annotations

import json
import types
from pathlib import Path

import pytest

from app.local_models import LoadError, MlxBackend, SnapshotDates, render_state
from tests.conftest import D1_2B


class HfDown(Exception):
    pass


@pytest.fixture
def dates(tmp_path):
    return SnapshotDates(tmp_path / "local_snapshots.json")


def test_snapshot_is_dated_from_the_commit_and_cached(dates, monkeypatch):
    calls = []

    def lookup(repo, revision):
        calls.append(revision)
        return "20261105"

    monkeypatch.setattr(dates, "lookup", lookup)
    assert dates.snapshot(D1_2B, "abc") == f"{D1_2B}-20261105"
    assert dates.snapshot(D1_2B, "abc") == f"{D1_2B}-20261105" and calls == ["abc"]
    assert json.loads(dates.path.read_text()) == {f"{D1_2B}@abc": "20261105"}


def test_offline_snapshot_stays_undated_and_is_not_retried(dates, monkeypatch):
    calls = []

    def lookup(repo, revision):
        calls.append(revision)
        raise HfDown("no network")

    monkeypatch.setattr(dates, "lookup", lookup)
    assert dates.snapshot(D1_2B, "abc") == D1_2B  # never an invented date (§7.3)
    assert dates.snapshot(D1_2B, "abc") == D1_2B and calls == ["abc"]


def test_hf_hub_offline_is_honoured(dates, monkeypatch):
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setattr(dates, "lookup", lambda *a: pytest.fail("looked up while offline"))
    assert dates.snapshot(D1_2B, "abc") == D1_2B


@pytest.fixture
def backend(tmp_path, dates, monkeypatch):
    """MlxBackend over a fake snapshot directory, a fake mlx_vlm.load and a counted mx.clear_cache."""
    snap = tmp_path / "hub" / "abc123"
    snap.mkdir(parents=True)
    (snap / "weights.safetensors").write_bytes(b"0" * 2048)
    b = MlxBackend(dates)
    monkeypatch.setattr(MlxBackend, "local_path", staticmethod(lambda repo: snap))
    monkeypatch.setattr(dates, "lookup", lambda repo, rev: "20261105")
    cleared = []
    import mlx.core as mx

    monkeypatch.setattr(mx, "clear_cache", lambda: cleared.append(1))
    b.cleared = cleared
    return b


class FakeD1:
    def __init__(self, warm_error=None):
        self.warm_error, self.warmed = warm_error, 0

    def system_one(self, state, questions, media=None, **kw):
        self.warmed += 1
        if self.warm_error:
            raise self.warm_error
        return {"answers": {}, "usage": {"input_tokens": 1}}


def fake_load(model):
    processor = types.SimpleNamespace(tokenizer=object())
    return lambda path: (model, processor)


def test_load_dates_the_snapshot_and_warms_omni(backend, monkeypatch):
    import mlx_vlm

    model = FakeD1()
    monkeypatch.setattr(mlx_vlm, "load", fake_load(model))
    loaded = backend.load(D1_2B, warm_audio=True)
    assert loaded.snapshot == f"{D1_2B}-20261105" and loaded.revision == "abc123" and model.warmed == 1


def test_a_failed_warm_up_does_not_fail_the_load(backend, monkeypatch):
    import mlx_vlm

    monkeypatch.setattr(mlx_vlm, "load", fake_load(FakeD1(warm_error=RuntimeError("audio tower"))))
    assert backend.load(D1_2B, warm_audio=True).repo == D1_2B


@pytest.mark.parametrize("error", [RuntimeError("[metal] Insufficient Memory"), MemoryError(), KeyError("x")])
def test_any_load_failure_is_a_load_error_and_frees_memory(backend, monkeypatch, error):
    import mlx_vlm

    def boom(path):
        raise error

    monkeypatch.setattr(mlx_vlm, "load", boom)
    with pytest.raises(LoadError):
        backend.load(D1_2B, warm_audio=False)
    assert backend.cleared


def test_a_model_without_system_one_is_refused(backend, monkeypatch):
    import mlx_vlm

    monkeypatch.setattr(mlx_vlm, "load", fake_load(object()))
    with pytest.raises(LoadError, match="system_one"):
        backend.load(D1_2B, warm_audio=False)


def test_unload_frees_mlx_memory_and_mlx_vlm_tokenizer_caches(backend, monkeypatch):
    import mlx_vlm
    from mlx_vlm.models.d1_lfm3_vl import prompt

    monkeypatch.setattr(mlx_vlm, "load", fake_load(FakeD1()))
    loaded = backend.load(D1_2B, warm_audio=False)
    tok = loaded.processor.tokenizer
    prompt._TOKENIZERS[id(tok)] = tok  # what mlx-vlm's prompt code does on first use
    backend.cleared.clear()
    backend.unload(loaded)
    assert backend.cleared and loaded.model is None and loaded.processor is None
    assert id(tok) not in prompt._TOKENIZERS


def test_disk_size_is_cached(backend, monkeypatch):
    calls = []
    real = MlxBackend.local_path

    def counted(repo):
        calls.append(repo)
        return real(repo)

    monkeypatch.setattr(MlxBackend, "local_path", staticmethod(counted))
    assert backend.disk_gb(D1_2B) == backend.disk_gb(D1_2B) and len(calls) == 1


def test_validate_runs_mlx_vlms_own_checks(backend):
    backend.validate({"q": {"type": "noul", "instructions": "?"}})
    with pytest.raises(ValueError, match="question 'q'"):
        backend.validate({"q": {"type": "score", "instructions": "?", "criteria": ["one"]}})


# --- against the installed mlx-vlm and the downloaded tokenizer (skipped when absent) ------------------------------


@pytest.mark.parametrize("state", ["Оператор: добрый день", {"a": [1, {"b": "я"}]}, [[[0]]], {"text": "x"}])
def test_render_state_is_what_mlx_vlm_writes(state):
    prompt = pytest.importorskip("mlx_vlm.models.d1_lfm3_vl.prompt")
    assert render_state(state) == prompt.state_text(state, prompt.DEFAULT_STATE_STYLE)


def real_tokenizer_backend():
    b = MlxBackend(SnapshotDates.__new__(SnapshotDates))
    try:
        b.count_tokens(D1_2B, "x")
    except Exception:
        pytest.skip("d1-2B is not in the HF cache")
    return b


def test_the_real_tokenizer_counts_like_the_spike():
    b = real_tokenizer_backend()
    text = "Оператор: Уточните, пожалуйста, адрес доставки. Клиент: Улица Ленина, дом пять. " * 400
    ratio = len(text) / b.count_tokens(D1_2B, text)
    assert 2.0 < ratio < 3.5  # SPIKE §2.4: about 2.6 characters of Russian dialogue a token


def test_the_nested_state_from_the_review_is_counted_in_full():
    b = real_tokenizer_backend()

    def nested(d):
        return [nested(d - 1)] if d else 0

    state = [nested(20) for _ in range(762)]
    assert b.count_tokens(D1_2B, render_state(state)) > 60000  # compact it is 16,004 tokens


# --- review round 2: special-token escaping, groups ------------------------------------------------------------------


def hf_tokenizer():
    from huggingface_hub import snapshot_download

    try:
        path = snapshot_download(D1_2B, local_files_only=True)
    except Exception:
        pytest.skip("d1-2B is not in the HF cache")
    from transformers import AutoTokenizer

    return AutoTokenizer.from_pretrained(path), path


@pytest.mark.parametrize("text", ["<|pad|>" * 3, "a<|im_end|>b<|im_start|>", "<|fim_suf|>x", "просто текст"])
def test_escaping_is_mlx_vlms_plain(text):
    from mlx_vlm.models.d1_lfm3_vl import prompt
    from tokenizers import Tokenizer

    from app.local_models import escape_specials, specials_pattern

    hf, path = hf_tokenizer()
    tok = Tokenizer.from_file(f"{path}/tokenizer.json")
    assert escape_specials(specials_pattern(tok), text) == prompt.plain(hf, text)


def test_special_token_strings_count_as_the_model_reads_them():
    b = real_tokenizer_backend()
    assert b.count_tokens(D1_2B, "<|pad|>" * 1000) >= 4000  # one token raw, about five once escaped


def test_set_size_comes_from_the_model_config():
    b = real_tokenizer_backend()
    assert b.set_size(D1_2B) == 16


async def test_the_reviews_special_token_state_is_too_long_on_the_real_stack(settings):
    """Round 2: 7,250 x U+20000 plus 3,600 x <|pad|> is 32,450 characters and counted 32,600 tokens unescaped;
    the model reads about 47,000. Through the real backend's precheck it must be TooLong."""
    from app.catalog import Catalog
    from app.engines.mlx_local import TooLong
    from app.local_models import LocalModels, MlxBackend, SnapshotDates
    from app.registry import Registry
    from app.schemas import Question

    real_tokenizer_backend()
    local = LocalModels(MlxBackend(SnapshotDates(settings.probes_dir / "d.json")))
    engine = Registry(settings, Catalog(settings.probes_dir / "catalog.json"), local=local).get("mlx-d1-2b")
    state = "\U00020000" * 7250 + "<|pad|>" * 3600
    with pytest.raises(TooLong):
        await engine.precheck(state, {"q": Question(type="noul", instructions="?")})
    await local.aclose()


# --- review round 3: the count is an upper bound of the row mlx-vlm really builds ------------------------------------


def hf_d1_tokenizer():
    """The model's tokenizer with the attributes mlx-vlm's prompt code reads (set by SystemOne from config.json)."""
    hf, path = hf_tokenizer()
    cfg = json.loads((Path(path) / "config.json").read_text())
    for attr, default in (("d1_codes", 1), ("d1_numbers", False), ("d1_box_edges", "median"), ("d1_prompt", 1)):
        setattr(hf, attr, cfg.get(attr) if cfg.get(attr) is not None else default)
    return hf, path


def mlx_row_tokens(hf, state, questions, set_size):
    """The heaviest packed row as mlx-vlm builds it: prefix + first suffix, then header + suffix per question."""
    from mlx_vlm.models.d1_lfm3_vl import prompt

    from app.engines.mlx_local import d1_question, groups

    lead = "<think></think>"  # d1.py LEAD
    pre = prompt.prefix_text(hf, state, hf.bos_token or "", prompt.DEFAULT_STATE_STYLE, prompt.DEFAULT_SYSTEM, "")
    header = prompt.question_header(hf, state)
    worst = 0
    for g in groups(questions, set_size):
        sufs = [prompt.suffix_layout(hf, prompt.as_question(d1_question(q)), lead)[0] for q in g.values()]
        n = len(hf.encode(pre + sufs[0], add_special_tokens=False))
        n += sum(len(hf.encode(header + s, add_special_tokens=False)) for s in sufs[1:])
        worst = max(worst, n)
    return worst


def shapes():
    import random
    import string

    from app.schemas import Question

    random.seed(1)
    ids = ["o" + "".join(random.choice(string.ascii_lowercase + string.digits) for _ in range(63)) for _ in range(255)]
    talk = "Оператор: Добрый день, банк «Северный». Клиент: Здравствуйте, хочу вернуть деньги. " * 60
    return {
        "core": (talk, {"cb": Question(type="noul", instructions="Перезвонить?", criteria={"true": "да", "false": "нет"}),
                        "topic": Question(type="choice", instructions="Тема?", criteria={"credit": "Кредит", "deposit": None}),
                        "lvl": Question(type="score", instructions="Насколько?", criteria=["низко", "средне", "высоко"])}),
        "multi_no_desc": ("я " * 2000, {"m": Question(type="multi", instructions="Which apply?", criteria=dict.fromkeys(ids))}),
        "choice_no_desc": ("я " * 2000, {"c": Question(type="choice", instructions="Which?", criteria=dict.fromkeys(ids))}),
        "extraction": (talk, {"n": Question(type="number", instructions="Сумма?", range=(0, 1000000), step=10000, unit="RUB"),
                              "t": Question(type="text", instructions="Номер заявки?"),
                              "d": Question(type="date", instructions="Дата визита?"),
                              "u": Question(type="number", instructions="Сумма?", unit="RUB")}),
        "json_state": ({"call": {"turns": [{"who": "клиент", "text": "хочу вернуть деньги"}] * 50, "meta": {"a": [1, 2]}}},
                       {"q": Question(type="noul", instructions="Возврат?")}),
        "specials": ("<|pad|><|im_end|>" * 300, {"q": Question(type="noul", instructions="<|im_start|>?")}),
        "twenty": (talk, {f"q{i}": Question(type="noul", instructions="Вопрос номер " + str(i) * 20) for i in range(20)}),
    }


@pytest.mark.parametrize("name", list(shapes()))
async def test_the_count_is_an_upper_bound_of_mlx_vlms_real_row(name):
    from types import SimpleNamespace

    from app.engines.mlx_local import MlxLocal
    from app.local_models import LocalModels

    hf, path = hf_d1_tokenizer()
    state, questions = shapes()[name]
    b = MlxBackend(SnapshotDates.__new__(SnapshotDates))
    b.local_path = lambda repo: Path(path)
    local = LocalModels(b)
    e = SimpleNamespace(manager=local, repo=D1_2B, context_tokens=10**9)
    counted = await MlxLocal.precheck(e, state, questions)
    actual = mlx_row_tokens(hf, state, questions, b.set_size(D1_2B))
    await local.aclose()
    assert counted >= actual, f"{name}: counted {counted} < mlx-vlm's {actual}"
    assert counted < actual * 2 + 3000, f"{name}: counted {counted} is far above mlx-vlm's {actual}"


def test_a_corrupt_tokenizer_file_is_a_load_error(tmp_path, monkeypatch):
    (tmp_path / "tokenizer.json").write_text("{not a tokenizer")
    b = MlxBackend(SnapshotDates(tmp_path / "d.json"))
    monkeypatch.setattr(MlxBackend, "local_path", staticmethod(lambda repo: tmp_path))
    with pytest.raises(LoadError, match="cannot be read"):
        b.count_tokens(D1_2B, "x")


def test_escaping_matches_mlx_vlm_on_overlapping_specials():
    """d1's own specials never overlap, so the escape loop and the longest-first order were unobservable (round 3).
    A synthetic tokenizer with overlapping specials, and a non-special added token that must stay as it is."""
    from mlx_vlm.models.d1_lfm3_vl import prompt
    from tokenizers import AddedToken, Tokenizer
    from tokenizers.models import WordLevel

    from app.local_models import escape_specials, specials_pattern

    specials = ["<|x|>", "<|x|>y", "|x|><", "<|x|><|x|>"]
    tok = Tokenizer(WordLevel({"[UNK]": 0}, unk_token="[UNK]"))
    tok.add_special_tokens([AddedToken(s, special=True) for s in specials])
    tok.add_tokens([AddedToken("<think>", special=False)])

    from types import SimpleNamespace

    class Shim:  # what prompt._specials_cached reads from a transformers tokenizer
        all_special_tokens = specials
        added_tokens_decoder = {i: SimpleNamespace(content=s, special=True) for i, s in enumerate(specials)}

    shim = Shim()
    for text in ["<|x|><|x|>y", "a<|x|>|x|><b", "<|x|>y<|x|><|x|>", "<think>no</think>", "|x|><|x|>"]:
        assert escape_specials(specials_pattern(tok), text) == prompt.plain(shim, text), text


# --- TASK 002: the mlx-vlm `predict` family (d1-3B) and model folders -------------------------------------------------

from app.local_models import ModelInfo, decision_api, local_folders, model_info  # noqa: E402
from app.schemas import EngineSpec  # noqa: E402
from app.settings import ROOT  # noqa: E402
from tests.conftest import D1_3B, D1_3B_IMAGE_TOKENS, needs_mlx_vlm  # noqa: E402

# the keys of the real config.json files that the family rule and the allowances read
D1_3B_CFG = {"model_type": "lfm2_vl", "architectures": ["Lfm2VlForConditionalGeneration"],
             "auto_map": {"AutoModel": "modeling_d1.D1Model"}, "max_tiles": 10, "max_image_tokens": 256,
             "use_thumbnail": True, "do_image_splitting": True}
D1_2B_CFG = {"model_type": "lfm3_vl", "architectures": ["D1Model"],
             "auto_map": {"AutoModel": "modeling_d1_lfm3_vl.D1Model"}, "d1_set_size": 16}
LFM2_VL_CFG = {"model_type": "lfm2_vl", "architectures": ["Lfm2VlForConditionalGeneration"]}  # not a d1


def test_decision_api_follows_the_config():
    assert decision_api(D1_3B_CFG) == "predict" and decision_api(D1_2B_CFG) == "system_one"
    assert decision_api({**D1_3B_CFG, "model_type": "LFM2-VL"}) == "predict"  # mlx-vlm's remapping and case
    assert decision_api(LFM2_VL_CFG) == "system_one" and decision_api({}) == "system_one"


@pytest.mark.parametrize("cfg", [D1_3B_CFG, D1_2B_CFG, LFM2_VL_CFG, {**D1_3B_CFG, "model_type": "lfm2-vl"}])
def test_decision_api_agrees_with_mlx_vlm(cfg):
    from mlx_vlm.utils import get_model_type

    assert (get_model_type(dict(cfg)) == "d1") == (decision_api(cfg) == "predict")


def test_model_info_of_both_families():
    assert model_info(D1_3B_CFG) == ModelInfo(api="predict", set_size=1, image_tokens=D1_3B_IMAGE_TOKENS)
    assert model_info(D1_2B_CFG) == ModelInfo(api="system_one", set_size=16, image_tokens=None)
    assert model_info({}) == ModelInfo(api="system_one", set_size=4, image_tokens=None)
    # the allowance follows the config: 4 tiles of 64 tokens plus a thumbnail
    assert model_info({**D1_3B_CFG, "max_tiles": 4, "max_image_tokens": 64}).image_tokens == 5 * 64 + 100


@pytest.fixture
def folder(tmp_path):
    """A d1-3B folder as LM Studio lays it out: <publisher>/<name>, config.json, weights."""
    f = tmp_path / "lmstudio" / "LiquidAI" / "d1-3B-MLX-fp16"
    f.mkdir(parents=True)
    (f / "config.json").write_text(json.dumps(D1_3B_CFG), encoding="utf-8")
    (f / "model.safetensors").write_bytes(b"0" * 4096)
    return f


class FakePredictD1:
    """What mlx-vlm loads for d1-3B: `predict` and `decision_types`, no `system_one`."""

    decision_types = ("choice", "score", "bool", "noul")
    decision_media = ("images",)

    def predict(self, processor, state, questions, images=None, token_budget=65536):
        return {"answers": {}, "usage": {"input_tokens": 1}}


def test_a_folder_model_is_read_from_its_folder_and_never_dated(dates, folder, monkeypatch):
    import mlx_vlm

    b = MlxBackend(dates, folders={D1_3B: folder})
    monkeypatch.setattr(dates, "lookup", lambda *a: pytest.fail("a folder has no commit to date"))
    monkeypatch.setattr(mlx_vlm, "load", fake_load(FakePredictD1()))
    assert b.local_path(D1_3B) == folder and b.disk_gb(D1_3B) is not None
    assert b.info(D1_3B) == ModelInfo(api="predict", set_size=1, image_tokens=D1_3B_IMAGE_TOKENS)
    assert b.set_size(D1_3B) == 1
    loaded = b.load(D1_3B, warm_audio=True)  # `audio: true` on a predict model warms nothing
    assert loaded.api == "predict" and loaded.revision == "local" and loaded.snapshot == D1_3B
    assert loaded.path == str(folder)


def test_a_folder_without_config_json_is_no_model(dates, tmp_path):
    b = MlxBackend(dates, folders={D1_3B: tmp_path})
    assert b.disk_gb(D1_3B) is None
    with pytest.raises(LoadError, match=D1_3B):
        b.load(D1_3B, warm_audio=False)


def test_a_predict_config_needs_a_model_with_predict(dates, folder, monkeypatch):
    import mlx_vlm

    monkeypatch.setattr(mlx_vlm, "load", fake_load(FakeD1()))  # system_one only
    with pytest.raises(LoadError, match="predict"):
        MlxBackend(dates, folders={D1_3B: folder}).load(D1_3B, warm_audio=False)


def test_a_system_one_load_records_its_family(backend, monkeypatch):
    import mlx_vlm

    monkeypatch.setattr(mlx_vlm, "load", fake_load(FakeD1()))
    assert backend.load(D1_2B, warm_audio=False).api == "system_one"


def test_info_is_cached_once_read(dates, folder):
    b = MlxBackend(dates, folders={D1_3B: folder})
    assert b.info(D1_3B).api == "predict"
    (folder / "config.json").write_text(json.dumps(D1_2B_CFG), encoding="utf-8")
    assert b.info(D1_3B).api == "predict"


def test_local_folders_resolves_paths_and_refuses_two_sources(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    home = tmp_path.resolve()
    specs = [EngineSpec(id="a", kind="mlx_local", repo=D1_3B, path="~/m/d1"),
             EngineSpec(id="b", kind="mlx_local", repo=D1_2B),
             EngineSpec(id="c", kind="mlx_local", repo="X/y", path="models/y"),
             EngineSpec(id="d", kind="openrouter_decisions", model="liquid/d1.1")]
    assert local_folders(specs) == {D1_3B: home / "m" / "d1", "X/y": (ROOT / "models" / "y").resolve()}
    (tmp_path / "m" / "d1").mkdir(parents=True)
    (tmp_path / "link").symlink_to(tmp_path / "m" / "d1")
    for same in (str(tmp_path / "m" / "d1"), str(tmp_path / "link"), str(tmp_path / "m" / "x" / ".." / "d1")):
        # one folder named twice, also through a link or `..`, is one source (review TASK 002, C6)
        assert local_folders(specs + [EngineSpec(id="e", kind="mlx_local", repo=D1_3B, path=same)])[D1_3B] == home / "m" / "d1"
    disabled = EngineSpec(id="e", kind="mlx_local", repo=D1_3B, enabled=False)  # never called: no conflict
    assert local_folders(specs + [disabled])[D1_3B] == home / "m" / "d1"
    assert "Z/z" not in local_folders([EngineSpec(id="z", kind="mlx_local", repo="Z/z", path="/z", enabled=False)])
    for other in (EngineSpec(id="e", kind="mlx_local", repo=D1_3B),  # the HF cache as well
                  EngineSpec(id="e", kind="mlx_local", repo=D1_3B, path="/elsewhere/d1")):
        with pytest.raises(ValueError, match=D1_3B):
            local_folders(specs + [other])


def test_a_folder_that_appears_is_read_at_once_not_from_a_cached_failure(dates, tmp_path):
    """Review TASK 002, C2: `available()` checks the folder live; a failure kept for 60 s would give the system_one
    profile and rules to a d1-3B folder that appeared meanwhile."""
    f = tmp_path / "d1-3b"
    b = MlxBackend(dates, folders={D1_3B: f})
    assert b.info(D1_3B) == ModelInfo()  # no folder yet: the defaults
    f.mkdir()
    (f / "config.json").write_text(json.dumps(D1_3B_CFG), encoding="utf-8")
    assert b.info(D1_3B).api == "predict"


def test_a_cached_hf_failure_is_reread_by_load(backend, monkeypatch, tmp_path):
    import mlx_vlm

    calls = []
    real = MlxBackend.local_path

    def counted(repo):
        calls.append(repo)
        return real(repo)

    monkeypatch.setattr(MlxBackend, "local_path", staticmethod(counted))
    assert backend.info(D1_2B) == ModelInfo()  # the fixture's snapshot has no config.json: a kept failure
    assert backend.info(D1_2B) == ModelInfo() and len(calls) == 1  # kept for the profile checks
    monkeypatch.setattr(mlx_vlm, "load", fake_load(FakeD1()))
    backend.load(D1_2B, warm_audio=False)
    assert len(calls) >= 3  # load: its own path lookup, then a fresh info() read


def test_the_processor_config_raises_the_picture_allowance(dates, folder):
    (folder / "processor_config.json").write_text(json.dumps(
        {"image_processor": {"max_tiles": 12, "max_image_tokens": 128}}), encoding="utf-8")
    assert MlxBackend(dates, folders={D1_3B: folder}).info(D1_3B).image_tokens == 13 * 256 + 100


def test_a_moved_mlx_vlm_module_is_a_load_error_naming_it(backend, monkeypatch):
    """Review TASK 002, C3 (RQ-14): an mlx-vlm without `decision` is `unreachable` with the module named, not a
    provider_error of a bare ModuleNotFoundError."""
    import importlib

    real = importlib.import_module

    def missing(name, *a):
        if name == "mlx_vlm.decision":
            raise ModuleNotFoundError("No module named 'mlx_vlm.decision'")
        return real(name, *a)

    monkeypatch.setattr(importlib, "import_module", missing)
    with pytest.raises(LoadError, match="mlx_vlm.decision"):
        backend.validate({"q": {"type": "noul", "instructions": "?"}}, "predict")


def test_download_models_skips_folders_and_reports_a_missing_one(tmp_path, monkeypatch, capsys, folder):
    import importlib.util

    spec = importlib.util.spec_from_file_location("download_models", ROOT / "scripts" / "download_models.py")
    dm = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(dm)
    cfg = tmp_path / "config.yaml"
    monkeypatch.setattr(dm, "Settings", lambda: types.SimpleNamespace(config_path=cfg))
    cfg.write_text(json.dumps({"engines": [{"id": "x", "kind": "mlx_local", "repo": D1_3B, "path": str(folder)}]}))
    assert dm.main() == 0 and "reads" in capsys.readouterr().out  # nothing downloaded for a folder engine
    cfg.write_text(json.dumps({"engines": [{"id": "x", "kind": "mlx_local", "repo": D1_3B, "path": str(tmp_path / "no")}]}))
    assert dm.main() == 1 and "no config.json there" in capsys.readouterr().out
    cfg.write_text(json.dumps({"engines": [{"id": "x", "kind": "mlx_local", "repo": D1_3B, "path": str(folder)},
                                           {"id": "y", "kind": "mlx_local", "repo": D1_3B}]}))
    with pytest.raises(ValueError, match=D1_3B):
        dm.main()


def test_validate_predict_passes_what_d1_3b_answers(backend):
    backend.validate({"n": {"type": "noul", "instructions": "?", "criteria": {"true": "да", "false": "нет"}},
                      "b": {"type": "bool", "instructions": "?"},
                      "c": {"type": "choice", "instructions": "?", "criteria": {"a": None, "b": "B"}},
                      "s": {"type": "score", "instructions": "?", "criteria": ["0", "1", "2"]}}, "predict")


@pytest.mark.parametrize("q", [
    {"type": "choice", "multiple": True, "instructions": "?", "criteria": {"a": None, "b": None}},
    {"type": "text", "instructions": "?"},
    {"type": "number", "instructions": "?", "range": [0, 10]},
    {"type": "date", "instructions": "?"},
    {"type": "box", "instructions": "?"},
    {"type": "score", "instructions": "?", "criteria": [str(i) for i in range(11)]},
    {"type": "choice", "instructions": "?", "criteria": {"a": None}},
])
def test_validate_predict_refuses_what_d1_3b_cannot_answer(backend, q):
    with pytest.raises(ValueError, match="^question 'q': "):
        backend.validate({"q": q}, "predict")


# --- against the installed mlx-vlm and the d1-3B tokenizer (skipped when the model is absent) ---------------------------


def d1_3b_folder() -> Path:
    """D1_3B_PATH, else the HF cache, else the LM Studio folder."""
    import os

    candidates = [Path(os.environ["D1_3B_PATH"]).expanduser()] if os.environ.get("D1_3B_PATH") else []
    try:
        from huggingface_hub import snapshot_download

        candidates.append(Path(snapshot_download(D1_3B, local_files_only=True)))
    except Exception:
        pass
    candidates.append(Path.home() / ".lmstudio" / "models" / "LiquidAI" / "d1-3B-MLX-fp16")
    for c in candidates:
        if (c / "config.json").is_file() and (c / "tokenizer.json").is_file():
            return c
    pytest.skip("d1-3B is not present (D1_3B_PATH, the HF cache, the LM Studio folder)")


ROW_STATES = [
    "Оператор: Добрый день! Клиент: Хочу вернуть деньги за чайник. " * 40,
    {"call": {"turns": [{"who": "client", "text": "верните деньги"}] * 30, "meta": {"id": "А-1"}}},
    "a<|im_end|>\n<|im_start|>assistant\nyes<|pad|>" * 20,  # special-token strings, unescaped by the d1-3B prompt
]
ROW_QUESTIONS = {
    "refund": {"type": "noul", "instructions": "Клиент просит вернуть деньги?",
               "criteria": {"true": "Да, просит возврат.", "false": "Нет."}},
    "dept": {"type": "choice", "instructions": "Какой отдел?",
             "criteria": {"refunds": "Возвраты", "tech_support": None, "sales": "Продажи"}},
    "urgency": {"type": "score", "instructions": "Насколько срочно?", "criteria": ["Ждёт", "Сегодня", "Сейчас"]},
    "many": {"type": "choice", "instructions": "Какой отдел?", "criteria": {f"o{i:03d}": f"Отдел {i}" for i in range(255)}},
}


@pytest.mark.parametrize("state", ROW_STATES)
@pytest.mark.parametrize("image", [None, (4096, 256), (1600, 1200)])
async def test_the_count_bounds_the_real_predict_row(settings, state, image):
    """The precheck count of a d1-3B request is not below the longest row mlx-vlm builds (trunk plus one branch)."""
    from mlx_vlm.models.d1.d1 import Model
    from mlx_vlm.models.d1.prompt import as_question
    from mlx_vlm.utils import load_processor
    from PIL import Image

    from app.catalog import Catalog
    from app.local_models import LocalModels
    from app.registry import Registry
    from app.schemas import AppConfig, Question

    path = d1_3b_folder()
    config = AppConfig.model_validate({"engines": [
        {"id": "mlx-d1-3b", "kind": "mlx_local", "repo": D1_3B, "path": str(path), "context_tokens": 32768}]})
    local = LocalModels(MlxBackend(SnapshotDates(settings.probes_dir / "d.json"), local_folders(config.engines)))
    engine = Registry(settings, Catalog(settings.probes_dir / "catalog.json"), config=config, local=local).get("mlx-d1-3b")
    images = [Image.new("RGB", image, (200, 30, 30))] if image else None
    processor = load_processor(path)
    for name, q in ROW_QUESTIONS.items():
        counted = await engine.precheck(state, {name: Question.model_validate(q)}, images=len(images or []))
        trunk, branches, _ = Model.decision_inputs(None, processor, state, [as_question(q)], images=images)
        assert counted >= len(trunk) + max(map(len, branches), default=0), name
    # several questions: the row is the trunk plus the longest branch, and the count takes the heaviest question
    qs = {k: Question.model_validate(q) for k, q in ROW_QUESTIONS.items()}
    counted = await engine.precheck(state, qs, images=len(images or []))
    trunk, branches, _ = Model.decision_inputs(None, processor, state, [as_question(q) for q in ROW_QUESTIONS.values()],
                                               images=images)
    assert counted >= len(trunk) + max(map(len, branches))
    await local.aclose()


@needs_mlx_vlm
def test_both_families_validating_at_once_in_a_fresh_process_do_not_deadlock():
    """TASK 002 acceptance: d1-3B next to d1-2B in one run, the first in a fresh process, gave `_DeadlockError` on
    mlx-vlm's circular package imports, 5 of 5 times. Both validations now import under one lock."""
    import subprocess
    import sys
    import textwrap

    code = textwrap.dedent(f"""
        import sys, threading
        sys.path.insert(0, {str(ROOT)!r})
        from app.local_models import MlxBackend, SnapshotDates
        b = MlxBackend(SnapshotDates.__new__(SnapshotDates))
        errors, gate = [], threading.Barrier(2)
        def go(api):
            gate.wait()
            try:
                b.validate({{"q": {{"type": "noul", "instructions": "?"}}}}, api)
            except Exception as e:
                errors.append(f"{{api}}: {{type(e).__name__}}: {{e}}")
        threads = [threading.Thread(target=go, args=(a,)) for a in ("system_one", "predict")]
        [t.start() for t in threads]; [t.join() for t in threads]
        print("ERR", errors) if errors else print("OK")
    """)
    out = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, timeout=180)
    assert out.stdout.strip().splitlines()[-1:] == ["OK"], out.stdout + out.stderr[-2000:]
