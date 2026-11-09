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
