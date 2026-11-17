"""§7.3 mlx_local over the manager (fake MLX), and UC-1 with a local model next to a cloud one."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.catalog import Catalog
from app.engines.mlx_local import d1_question
from app.main import create_app
from app.registry import Registry
from app.schemas import DecisionRequest, Question, RunOptions
from tests.conftest import UI, D1_2B, echo_answers, load, parse_sse

CORE = load("requests/core_ru.json")
EXT = load("requests/extensions_ru.json")


def engine(settings, local, engine_id="mlx-d1-2b"):
    return Registry(settings, Catalog(settings.probes_dir / "catalog.json"), local=local).get(engine_id)


def test_d1_question_shapes():
    assert d1_question(Question(type="multi", instructions="?", criteria={"a": None, "b": "B"})) == {
        "type": "choice", "multiple": True, "instructions": "?", "criteria": {"a": None, "b": "B"}}
    assert d1_question(Question(type="number", instructions="?", range=(0, 10), step=1, unit="RUB")) == {
        "type": "number", "instructions": "?", "range": [0, 10], "step": 1, "unit": "RUB"}
    assert d1_question(Question(type="date", instructions="?")) == {"type": "date", "instructions": "?"}


async def test_run_core_and_extensions(settings, local, fake_backend):
    req = DecisionRequest.model_validate({"state": EXT["state"], "questions": {**EXT["questions"], **CORE["questions"]}})
    r = await engine(settings, local).run(req, RunOptions())
    assert r.status == "ok", r.question_failures
    assert r.snapshot == f"{D1_2B}-20261105"
    assert r.usage.cost_usd == 0 and r.usage.cost_source == "none" and r.usage.input_tokens == 400
    assert r.answers["products"].type == "multi" and r.answers["products"].probabilities["deposit"] == 0.8
    assert r.answers["application_id"].extract.value == "А-48213"
    assert r.answers["amount_estimate"].extract.interval == (300000.0, 400000.0)
    sent = fake_backend.calls[0]
    assert sent["questions"]["products"]["multiple"] is True and sent["questions"]["products"]["type"] == "choice"
    assert sent["score_readout"] == "mean"
    interest = r.answers["interest"]
    assert interest.expected == pytest.approx(sum(int(k) * p for k, p in interest.probabilities.items()))


async def test_too_long_by_the_models_tokenizer(settings, local, fake_backend):
    req = DecisionRequest(state="я" * 70000, questions=CORE["questions"])  # 35,000 fake tokens > 32,768
    r = await engine(settings, local).run(req, RunOptions())
    assert r.request_failure == "too_long" and "tokens in the row > context_tokens 32768" in r.detail
    assert not fake_backend.calls and not fake_backend.loads  # refused before the queue: nothing loaded or evicted


async def test_load_failure_is_unreachable(settings, local, fake_backend):
    fake_backend.load_error = RuntimeError("Model type lfm3_omni not supported")
    r = await engine(settings, local).run(DecisionRequest.model_validate(CORE), RunOptions())
    assert r.request_failure == "unreachable" and "lfm3_omni" in r.detail


async def test_mlx_validation_error_is_refused(settings, local, fake_backend):
    fake_backend.call_error = ValueError("question 'q': \"type\" is one of noul, …")
    r = await engine(settings, local).run(DecisionRequest.model_validate(CORE), RunOptions())
    assert r.request_failure == "refused" and r.detail.startswith("mlx-vlm:")


async def test_crash_is_provider_error(settings, local, fake_backend):
    fake_backend.call_error = RuntimeError("[metal] command buffer failed")
    r = await engine(settings, local).run(DecisionRequest.model_validate(CORE), RunOptions())
    assert r.request_failure == "provider_error"


@pytest.mark.parametrize("path", ["../../etc/passwd", "/etc/passwd", "missing.wav"])
async def test_media_only_from_data_dir(settings, local, fake_backend, path):
    req = DecisionRequest(state="x", questions=CORE["questions"], media=[{"type": "audio", "path": path}])
    r = await engine(settings, local, "mlx-d1-omni").run(req, RunOptions())
    assert r.request_failure == "refused" and not fake_backend.calls


async def test_media_under_data_dir_reaches_the_model(settings, local, fake_backend):
    fake_backend.downloaded.add("LiquidAI/d1-Omni-2B-MLX-8bit")
    (settings.data_dir / "media" / "call.wav").write_bytes(b"RIFF")
    req = DecisionRequest(state=None, questions=CORE["questions"], media=[{"type": "audio", "path": "call.wav"}])
    r = await engine(settings, local, "mlx-d1-omni").run(req, RunOptions())
    assert r.status == "ok"
    assert fake_backend.calls[0]["media"] == [{"type": "audio", "path": str((settings.data_dir / "media" / "call.wav").resolve())}]


async def test_audio_refused_by_a_model_without_audio(settings, local, fake_backend):
    (settings.data_dir / "media" / "call.wav").write_bytes(b"RIFF")
    req = DecisionRequest(state=None, questions=CORE["questions"], media=[{"type": "audio", "path": "call.wav"}])
    r = await engine(settings, local).run(req, RunOptions())
    assert r.request_failure == "refused" and "audio" in r.detail


def test_uc1_local_next_to_cloud(settings, upstream, local, fake_backend):
    upstream.default = echo_answers
    app = create_app(settings, transport=upstream.transport, refresh_catalog=False, local=local)
    body = {"request": {"state": EXT["state"], "questions": {**CORE["questions"], "products": EXT["questions"]["products"]}},
            "engines": ["mlx-d1-2b", "or-d1.1"]}
    with TestClient(app, base_url=UI) as c:
        with c.stream("POST", "/api/runs", json=body) as r:
            events = parse_sse("".join(r.iter_text()))
        results = {p["engine_id"]: p["result"] for n, p in events if n in ("engine_done", "engine_failed")}
        assert results["mlx-d1-2b"]["status"] == "ok"
        assert results["or-d1.1"]["question_failures"] == {"products": "unsupported"}
        record = c.get(f"/api/runs/{events[0][1]['run_id']}").json()
        assert record["versions"]["models"]["mlx-d1-2b"] == f"{D1_2B}-20261105"
        assert record["cost_usd"] == pytest.approx(0.000002)

        models = c.get("/api/local/models").json()
        m = {x["id"]: x for x in models["models"]}
        assert m["mlx-d1-2b"]["state"] == "ready" and m["mlx-d1-2b"]["disk_gb"] == 2.08
        assert models["compat"]["token_set"] is False and models["compat"]["lan"] is False
        assert c.post("/api/local/models/mlx-d1-2b/unload").json() == {"unloaded": True}
        assert c.get("/api/local/models").json()["models"][0]["state"] == "unloaded"
        assert c.post("/api/local/models/or-d1.1/unload").status_code == 404


# --- review P2 -------------------------------------------------------------------------------------------------------


async def test_media_dict_in_instructions_never_reaches_the_model(settings, local, fake_backend):
    """HIGH: a dict in `instructions` is a media path for d1 — and it bypassed the DATA_DIR/media guard."""
    req = DecisionRequest.model_validate({"state": "x", "questions": {
        "spoken": {"type": "noul", "instructions": {"type": "image", "path": "/Users/sergey/Desktop/secret.png"}},
        "ok": {"type": "noul", "instructions": "Это жалоба?"}}})
    r = await engine(settings, local).run(req, RunOptions())
    assert r.question_failures == {"spoken": "unsupported"} and set(r.answers) == {"ok"}
    assert all(isinstance(q["instructions"], str) for c in fake_backend.calls for q in c["questions"].values())


async def test_only_media_dict_questions_means_no_call(settings, local, fake_backend):
    req = DecisionRequest.model_validate({"state": "x", "questions": {
        "spoken": {"type": "noul", "instructions": {"type": "audio", "path": "http://169.254.169.254/"}}}})
    r = await engine(settings, local).run(req, RunOptions())
    assert r.status == "failed" and r.question_failures == {"spoken": "unsupported"} and not fake_backend.loads


async def test_nested_state_is_too_long_before_any_load(settings, local, fake_backend):
    def nested(d):
        return [nested(d - 1)] if d else 0

    req = DecisionRequest(state={"s": [nested(29) for _ in range(300)]}, questions=CORE["questions"])
    r = await engine(settings, local).run(req, RunOptions())
    assert r.request_failure == "too_long" and not fake_backend.loads
    # rendered ≈ 590 K > 16 × 32,768: refused from the computed rendered length, nothing rendered or counted
    assert "rendered state characters" in r.detail and not fake_backend.counted


async def test_question_text_is_counted(settings, local, fake_backend):
    req = DecisionRequest(state="коротко", questions={"q": {"type": "noul", "instructions": "я" * 70000}})
    r = await engine(settings, local).run(req, RunOptions())
    assert r.request_failure == "too_long" and not fake_backend.loads


async def test_a_too_long_request_does_not_evict_the_loaded_model(settings, local, fake_backend):
    await engine(settings, local).run(DecisionRequest.model_validate(CORE), RunOptions())
    big = DecisionRequest(state="я" * 70000, questions=CORE["questions"])
    await engine(settings, local).run(big, RunOptions())
    assert fake_backend.loads == [D1_2B] and not fake_backend.unloads


async def test_mlx_vlm_refusal_happens_before_the_queue(settings, local, fake_backend, monkeypatch):
    def refuse(questions, api="system_one"):
        raise ValueError("question 'q': a score's criteria lists 2 to 10 levels")

    monkeypatch.setattr(fake_backend, "validate", refuse)
    r = await engine(settings, local).run(DecisionRequest.model_validate(CORE), RunOptions())
    assert r.request_failure == "refused" and not fake_backend.loads


async def test_queue_failures_map_to_classes(settings, local, fake_backend):
    local.max_waiting = 0
    r = await engine(settings, local).run(DecisionRequest.model_validate(CORE), RunOptions())
    assert r.request_failure == "rate_limited" and not fake_backend.calls


async def test_latency_is_the_call_and_timings_hold_the_rest(settings, local, fake_backend):
    fake_backend.load_delay, fake_backend.call_delay = 0.15, 0.03
    r = await engine(settings, local).run(DecisionRequest.model_validate(CORE), RunOptions())
    assert r.timings["load_ms"] >= 140 and r.latency_ms == r.timings["call_ms"] < 100


def test_shared_manager_survives_the_ui_shutting_down(settings, upstream, local):
    app = create_app(settings, transport=upstream.transport, refresh_catalog=False, local=local)
    with TestClient(app, base_url=UI):
        pass
    assert not local.closing  # the compat server still uses it
    app2 = create_app(settings, transport=upstream.transport, refresh_catalog=False)
    with TestClient(app2, base_url=UI):
        own = app2.state.local
    assert own.closing  # an app that built its own manager closes it


# --- review round 2 --------------------------------------------------------------------------------------------------


def test_a_state_deeper_than_32_is_refused_at_the_door():
    from pydantic import ValidationError

    def nested(d):
        return [nested(d - 1)] if d else 0

    DecisionRequest(state=[nested(31)], questions=CORE["questions"])  # depth 32
    with pytest.raises(ValidationError, match="nested deeper than 32"):
        DecisionRequest(state=[nested(32)], questions=CORE["questions"])


def test_a_deep_state_is_a_422_on_the_ui(settings, upstream, local):
    app = create_app(settings, transport=upstream.transport, refresh_catalog=False, local=local)
    deep = "[" * 1000 + "]" * 1000
    body = ('{"request": {"state": ' + deep + ', "questions": {"q": {"type": "noul", "instructions": "?"}}},'
            ' "engines": ["mlx-d1-2b"]}')
    with TestClient(app, base_url=UI) as c:
        r = c.post("/api/runs", content=body.encode(), headers={"Content-Type": "application/json"})
    assert r.status_code == 422


async def test_a_broken_tokenizer_is_unreachable(settings, local, fake_backend):
    from app.local_models import LoadError

    fake_backend.tokenizer_error = LoadError("corrupt tokenizer.json")  # what MlxBackend raises (round 3)
    r = await engine(settings, local).run(DecisionRequest.model_validate(CORE), RunOptions())
    assert r.request_failure == "unreachable" and not fake_backend.loads


async def test_a_huge_state_is_too_long_without_rendering(settings, local, fake_backend):
    req = DecisionRequest(state={"s": "я" * (32768 * 16 + 1)}, questions=CORE["questions"])
    r = await engine(settings, local).run(req, RunOptions())
    assert r.request_failure == "too_long" and not fake_backend.counted  # refused by the compact size alone


# --- review round 3 --------------------------------------------------------------------------------------------------


async def test_counting_runs_off_the_event_loop(settings, local, fake_backend, monkeypatch):
    import asyncio

    on_loop = []
    real = fake_backend.count_tokens

    def spy(repo, text):
        try:
            asyncio.get_running_loop()
            on_loop.append(True)
        except RuntimeError:
            on_loop.append(False)
        return real(repo, text)

    monkeypatch.setattr(fake_backend, "count_tokens", spy)
    await engine(settings, local).run(DecisionRequest.model_validate(CORE), RunOptions())
    assert on_loop and not any(on_loop)


async def test_precheck_counts_by_group_of_set_size(settings, local, fake_backend):
    """1 token a character: a 21,000-character state and 20 questions of 600 characters fit each group of 16
    (≈ 31.2 K) but not all 20 at once (≈ 33.7 K). Margins checked by planting a single group (round 3)."""
    fake_backend.tokens_per_char = 1
    questions = {f"q{i}": {"type": "noul", "instructions": "д" * 600} for i in range(20)}
    r = await engine(settings, local).run(DecisionRequest(state="я" * 21000, questions=questions), RunOptions())
    assert r.status == "ok", r.detail


async def test_a_state_over_the_render_bound_is_never_rendered(settings, local, fake_backend, monkeypatch):
    import app.engines.mlx_local as mlx_local

    monkeypatch.setattr(mlx_local, "render_state", lambda s: pytest.fail("rendered a state over the bound"))
    state = {"rows": [["я" * 30] * 30 for _ in range(30)]}  # 0.03 M compact, but deep lists render far longer
    state = {"s": [state] * 30}
    r = await engine(settings, local).run(DecisionRequest(state=state, questions=CORE["questions"]), RunOptions())
    assert r.request_failure == "too_long" and "rendered state characters" in r.detail


async def test_precheck_keeps_its_own_depth_guard(settings, local):
    def nested(d):
        return [nested(d - 1)] if d else 0

    with pytest.raises(ValueError, match="nested deeper"):
        await engine(settings, local).precheck([nested(40)], {"q": Question(type="noul", instructions="?")})


async def test_out_of_memory_while_counting_is_too_long(settings, local, fake_backend):
    fake_backend.tokenizer_error = MemoryError()
    r = await engine(settings, local).run(DecisionRequest.model_validate(CORE), RunOptions())
    assert r.request_failure == "too_long" and not fake_backend.loads


@pytest.mark.parametrize("value,tokens", [(None, 1700), ("3276800", 3300), ("1.6e6", 1700), ("-5", 1700), ("x", 1700)])
def test_image_allowance_follows_d1_vision_max_pixels(monkeypatch, value, tokens):
    from app.engines.mlx_local import image_tokens

    if value is None:
        monkeypatch.delenv("D1_VISION_MAX_PIXELS", raising=False)
    else:
        monkeypatch.setenv("D1_VISION_MAX_PIXELS", value)
    assert image_tokens() == tokens


# --- TASK 002: d1-3B, the mlx-vlm `predict` family, and model folders ---------------------------------------------------

from app.schemas import AppConfig, EngineSpec  # noqa: E402
from tests.conftest import D1_3B, D1_3B_IMAGE_TOKENS, FakeTokenizer  # noqa: E402


@pytest.fixture
def d1_3b(settings, local, fake_backend):
    fake_backend.downloaded.add(D1_3B)
    return engine(settings, local, "mlx-d1-3b")


def test_the_predict_profile_has_three_types_and_pictures(d1_3b):
    assert d1_3b.api == "predict"
    p = d1_3b.default_profile()
    assert p.types == ["noul", "choice", "score"] and p.media == ["image"]
    assert p.max_choice_options == 255 and p.max_score_levels == 10  # 300 options answered in the spike (TASK 002 §7)
    assert d1_3b.describe()["api"] == "predict"


def test_audio_true_adds_no_audio_to_a_predict_model(settings, local, fake_backend):
    fake_backend.downloaded.add(D1_3B)
    config = AppConfig.model_validate({"engines": [{"id": "x", "kind": "mlx_local", "repo": D1_3B, "audio": True}]})
    e = Registry(settings, Catalog(settings.probes_dir / "catalog.json"), config=config, local=local).get("x")
    assert e.default_profile().media == ["image"]


async def test_a_predict_run_reads_mlx_decisions_names(d1_3b, fake_backend):
    r = await d1_3b.run(DecisionRequest.model_validate(CORE), RunOptions())
    assert r.status == "ok", (r.request_failure, r.detail, r.question_failures)
    a = r.answers
    assert a["callback_agreed"].probabilities == {"true": 0.9, "false": pytest.approx(0.1)}
    assert a["call_topic"].top == "credit" and a["call_topic"].vendor_confidence == 0.9
    assert a["interest"].expected == pytest.approx(sum(int(k) * p for k, p in a["interest"].probabilities.items()))
    assert r.snapshot == D1_3B and r.usage.input_tokens == 382  # a folder model: undated
    assert "model" not in r.raw and ".lmstudio" not in str(r.raw)  # the folder path is not stored
    sent = fake_backend.calls[0]["questions"]
    assert all(set(q) <= {"type", "instructions", "criteria"} for q in sent.values())


async def test_extension_types_are_unsupported_on_d1_3b_and_the_rest_answered(settings, local, fake_backend):
    from app.runner import Runner

    fake_backend.downloaded.add(D1_3B)
    e = engine(settings, local, "mlx-d1-3b")
    req = DecisionRequest.model_validate({"state": EXT["state"], "questions": {**EXT["questions"], **CORE["questions"]}})
    keys, refused = Runner.split(e, req)
    assert set(keys) == set(CORE["questions"])
    assert set(refused) == set(EXT["questions"]) and set(refused.values()) == {"unsupported"}


async def test_a_multi_question_sent_straight_to_the_engine_is_refused_before_the_queue(d1_3b, fake_backend):
    """The runner never sends `multi` to d1-3B; the engine itself still refuses it: d1-3B would answer it as a
    single choice (TASK 002 §7), a wrong answer with no error."""
    req = DecisionRequest.model_validate({"state": "x", "questions": {"p": EXT["questions"]["products"]}})
    r = await d1_3b.run(req, RunOptions())
    assert r.request_failure == "refused" and r.http_status == 400 and "question 'p'" in r.detail
    assert not fake_backend.loads and not fake_backend.calls


async def test_an_audio_item_never_reaches_a_predict_model(d1_3b, fake_backend, settings):
    from app.engines.mlx_local import predict_call
    from app.local_models import Loaded

    loaded = Loaded(repo=D1_3B, model=object(), processor=None, path="", revision="local", snapshot=D1_3B,
                    loaded_at=0, load_ms=0, api="predict")
    with pytest.raises(ValueError, match="^this checkpoint takes no audio"):
        predict_call(loaded, None, {"q": {"type": "noul", "instructions": "?"}}, [{"type": "audio", "path": "x.wav"}])
    with pytest.raises(ValueError, match="^question 'q': "):
        predict_call(loaded, "x", {"q": {"type": "choice", "multiple": True, "instructions": "?",
                                         "criteria": {"a": None, "b": None}}}, None)


async def test_pictures_reach_predict_by_path(d1_3b, fake_backend, settings):
    (settings.data_dir / "media" / "photo.png").write_bytes(b"\x89PNG")
    req = DecisionRequest(state=None, questions=CORE["questions"], media=[{"type": "image", "path": "photo.png"}])
    r = await d1_3b.run(req, RunOptions())
    assert r.status == "ok"
    assert fake_backend.calls[0]["images"] == [str((settings.data_dir / "media" / "photo.png").resolve())]


async def test_the_predict_count_takes_one_question_per_row_and_its_picture_allowance(d1_3b, fake_backend):
    from app.engines.mlx_local import allowance

    qs = {k: Question.model_validate(q) for k, q in CORE["questions"].items()}
    fake_backend.counted.clear()
    tokens = await d1_3b.precheck("x" * 100, qs, images=1)
    rows = fake_backend.counted[1:]  # the state first, then one text per group
    assert len(rows) == len(qs)  # one question per row, not d1_set_size of them
    heaviest = max(len(FakeTokenizer().encode(t)) + allowance({k: q}) for t, (k, q) in zip(rows, qs.items()))
    assert tokens == len(FakeTokenizer().encode("x" * 100)) + heaviest + D1_3B_IMAGE_TOKENS


def test_path_is_for_mlx_local_engines_only():
    EngineSpec(id="a", kind="mlx_local", repo=D1_3B, path="~/m")
    with pytest.raises(ValueError, match="path"):
        EngineSpec(id="b", kind="openrouter_decisions", model="liquid/d1.1", path="~/m")


def test_a_path_without_config_json_makes_the_engine_unavailable(settings, local, fake_backend, tmp_path):
    config = AppConfig.model_validate({"engines": [
        {"id": "x", "kind": "mlx_local", "repo": D1_3B, "path": str(tmp_path / "nowhere")}]})
    e = Registry(settings, Catalog(settings.probes_dir / "catalog.json"), config=config, local=local).get("x")
    ok, reason = e.available()
    assert not ok and "nowhere" in reason and "config.json" in reason


async def test_audio_true_on_a_predict_engine_still_refuses_audio_before_any_load(settings, local, fake_backend):
    """Review TASK 002, C5: `audio: true` must not let an audio item reach the queue (load, eviction) for d1-3B."""
    fake_backend.downloaded.add(D1_3B)
    config = AppConfig.model_validate({"engines": [{"id": "x", "kind": "mlx_local", "repo": D1_3B, "audio": True}]})
    e = Registry(settings, Catalog(settings.probes_dir / "catalog.json"), config=config, local=local).get("x")
    (settings.data_dir / "media" / "call.wav").write_bytes(b"RIFF")
    req = DecisionRequest(state=None, questions=CORE["questions"], media=[{"type": "audio", "path": "call.wav"}])
    r = await e.run(req, RunOptions())
    assert r.request_failure == "refused" and "audio" in r.detail and not fake_backend.loads


def test_rounded_predict_probabilities_are_rescaled_within_the_rounding_budget():
    """Review TASK 002, C4: mlx-vlm's d1 `_answer` rounds to 4 places; a sharp 255-option choice then sums to about
    0.998 and failed normalization (the probe would shrink max_choice_options, pointer passes would be lost)."""
    from mlx_vlm.models.d1.d1 import _answer

    from app.engines.base import ANSWER_NAMES, normalize_answer
    from app.engines.mlx_local import unrounded

    names = ANSWER_NAMES["mlx_decisions"]
    options = {f"o{i:03d}": None for i in range(255)}
    p = [6e-6] * 255
    p[7] = 1 - 6e-6 * 254
    raw = _answer({"type": "choice", "criteria": options}, p)
    q = Question(type="choice", instructions="?", criteria=options)
    assert normalize_answer(q, raw, names) == "invalid_answer"  # the failure the review measured
    a = normalize_answer(q, unrounded(raw), names)
    assert a.top == "o007" and abs(sum(a.probabilities.values()) - 1) < 1e-9
    bad = {**raw, "probabilities": {k: v * 0.9 for k, v in raw["probabilities"].items()}}
    assert normalize_answer(q, unrounded(bad), names) == "invalid_answer"  # a sum off by 0.1 is no rounding


def test_a_valid_folder_makes_the_engine_available(settings, local, fake_backend, tmp_path):
    (tmp_path / "d1").mkdir()
    (tmp_path / "d1" / "config.json").write_text("{}")
    config = AppConfig.model_validate({"engines": [
        {"id": "x", "kind": "mlx_local", "repo": D1_3B, "path": str(tmp_path / "d1")}]})
    e = Registry(settings, Catalog(settings.probes_dir / "catalog.json"), config=config, local=local).get("x")
    assert e.available() == (True, "ok")
