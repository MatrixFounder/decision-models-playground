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
    def refuse(questions):
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
