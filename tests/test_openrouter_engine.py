"""§6.3 failure classes and §7.2 behaviour of openrouter_decisions against a fake upstream (httpx.MockTransport)."""

from __future__ import annotations

import json

import httpx
import pytest

from app.catalog import Catalog
from app.registry import Registry
from app.schemas import DecisionRequest, RunOptions
from tests.conftest import echo_answers, load, or_body

CORE = DecisionRequest.model_validate(load("requests/core_ru.json"))
EXT = load("requests/extensions_ru.json")


def engine(settings, upstream, engine_id="or-d1.1"):
    reg = Registry(settings, Catalog(settings.probes_dir / "catalog.json"), transport=upstream.transport)
    return reg.get(engine_id)


async def run(settings, upstream, req=CORE, engine_id="or-d1.1", **opts):
    return await engine(settings, upstream, engine_id).run(req, RunOptions(**opts))


def assert_no_key(*objs):
    """§18: the key is in no result, detail or raw — on failure paths as much as on success."""
    for o in objs:
        text = o if isinstance(o, str) else json.dumps(o.model_dump(mode="json") if hasattr(o, "model_dump") else o)
        assert "sk-or-v1-test" not in text


def zod_400(*keys):
    issues = [{"code": "invalid_union", "note": "No matching discriminator", "discriminator": "type",
               "options": ["noul", "choice", "score", "box"], "path": ["questions", k, "type"],
               "message": "Invalid discriminator value. Expected 'noul' | 'choice' | 'score' | 'box'"} for k in keys]
    return httpx.Response(400, json={"error": {"message": json.dumps(issues, indent=2), "code": 400}})


async def test_ok_with_recorded_answer(settings, upstream):
    upstream.push("liquid/d1.1", or_body("liquid__d1.1", "core_ru_0"))
    r = await run(settings, upstream)
    assert r.status == "ok" and set(r.answers) == set(CORE.questions)
    assert r.snapshot == "liquid/d1.1-20261027"
    assert r.usage.cost_usd == pytest.approx(7.88e-06) and r.usage.cost_source == "provider"
    assert r.usage.input_tokens == 394
    sent = upstream.calls[0]
    assert sent["url"] == "https://openrouter.ai/api/alpha/decisions"
    assert sent["body"]["provider"] == {"zdr": True, "data_collection": "deny"}
    assert sent["auth"] == "Bearer sk-or-v1-test"
    assert "sk-or-v1-test" not in json.dumps(r.model_dump())


async def test_alias_reports_dated_snapshot(settings, upstream):
    upstream.push("~liquid/d1-latest", or_body("alias-liquid__d1-latest", "core_ru_0"))
    r = await run(settings, upstream, engine_id="or-d1-latest")
    assert r.snapshot == "liquid/d1.1-20261027"


async def test_400_naming_a_type_marks_it_unsupported_and_resends_the_rest(settings, upstream):
    req = DecisionRequest.model_validate({"state": EXT["state"], "questions": {
        "visit_date": EXT["questions"]["visit_date"], **load("requests/core_ru.json")["questions"]}})
    upstream.push("liquid/d1.1", zod_400("visit_date"), echo_answers)
    r = await run(settings, upstream, req)
    assert r.question_failures == {"visit_date": "unsupported"}
    assert set(r.answers) == set(CORE.questions) and r.status == "partial"
    assert "visit_date" not in upstream.calls[1]["body"]["questions"]
    assert len(upstream.calls) == 2


async def test_box_style_400_names_the_question_by_index(settings, upstream):
    body = {"error": {"message": "Liquid does not accept box questions; question 2 is a box", "code": 400}}
    upstream.push("liquid/d1.1", httpx.Response(400, json=body), echo_answers)
    r = await run(settings, upstream)
    assert r.question_failures == {"call_topic": "unsupported"}


async def test_recorded_zod_400_body_parses(settings, upstream):
    body = or_body("liquid__d1.1", "ext_visit_date")
    upstream.push("liquid/d1.1", httpx.Response(400, json=body), echo_answers)
    req = DecisionRequest.model_validate({"state": EXT["state"], "questions": {
        "visit_date": EXT["questions"]["visit_date"], "x": {"type": "noul", "instructions": "?"}}})
    r = await run(settings, upstream, req)
    assert r.question_failures == {"visit_date": "unsupported"} and "x" in r.answers


@pytest.mark.parametrize("response,cls", [
    (httpx.Response(400, json={"error": {"message": "Invalid request parameters", "code": 400}}), "refused"),
    (httpx.Response(401, json={"error": {"message": "User not found.", "code": 401}}), "refused"),
    (httpx.Response(402, json={"error": {"message": "Insufficient credits", "code": 402}}), "refused"),
    (httpx.Response(404, json={"error": {"message": "No endpoints found", "code": 404}}), "refused"),
    (httpx.Response(413, json={"error": {"message": "too large", "code": 413}}), "too_long"),
    (httpx.Response(429, json={"error": {"message": "rate", "code": 429}}), "rate_limited"),
    (httpx.Response(408, text="timeout"), "timed_out"),
])
async def test_request_failure_classes(settings, upstream, response, cls):
    upstream.push("liquid/d1.1", response)
    r = await run(settings, upstream)
    assert r.request_failure == cls and r.status == "failed"
    assert r.question_failures == {} and r.answers == {}  # covered by request_failure, not invalid_answer
    assert len(upstream.calls) == 1  # no retry on 4xx
    assert_no_key(r)


@pytest.mark.parametrize("status", [500, 502, 503, 524, 529])
async def test_5xx_is_retried_once(settings, upstream, status):
    upstream.push("liquid/d1.1", httpx.Response(status), httpx.Response(status))
    r = await run(settings, upstream)
    assert r.request_failure == "provider_error" and len(upstream.calls) == 2


async def test_5xx_then_ok(settings, upstream):
    upstream.push("liquid/d1.1", httpx.Response(503), or_body("liquid__d1.1", "core_ru_0"))
    r = await run(settings, upstream)
    assert r.status == "ok" and len(upstream.calls) == 2


async def test_network_error_is_retried_then_unreachable(settings, upstream):
    err = httpx.ConnectError("refused")
    upstream.push("liquid/d1.1", err, err)
    r = await run(settings, upstream)
    assert r.request_failure == "unreachable" and len(upstream.calls) == 2


async def test_timeout_is_timed_out(settings, upstream):
    upstream.push("liquid/d1.1", httpx.ReadTimeout("slow"))
    r = await run(settings, upstream)
    assert r.request_failure == "timed_out"


async def test_refusal_answer_is_a_question_failure(settings, upstream):
    body = or_body("liquid__d1.1", "core_ru_0")
    body["answers"]["call_topic"] = {"type": "refusal"}
    upstream.push("liquid/d1.1", body)
    r = await run(settings, upstream)
    assert r.question_failures == {"call_topic": "refused"} and r.status == "partial"


async def test_invalid_answer_and_missing_answer(settings, upstream):
    body = or_body("liquid__d1.1", "core_ru_0")
    body["answers"]["call_topic"]["probabilities"]["credit"] = 0.5
    del body["answers"]["interest"]
    upstream.push("liquid/d1.1", body)
    r = await run(settings, upstream)
    assert r.question_failures == {"call_topic": "invalid_answer", "interest": "invalid_answer"}


async def test_size_limit_refuses_without_a_call(settings, upstream):
    big = DecisionRequest(state="я" * 20000, questions=CORE.questions)  # 40,000 bytes of Cyrillic
    r = await run(settings, upstream, big, engine_id="or-jev-1.14")
    assert r.request_failure == "too_long" and not upstream.calls


async def test_no_zdr_engine_needs_confirmation(settings, upstream):
    r = await run(settings, upstream, engine_id="or-no-zdr")
    assert r.request_failure == "refused" and not upstream.calls
    upstream.default = echo_answers
    r = await run(settings, upstream, engine_id="or-no-zdr", allow_no_zdr=True)
    assert r.status == "ok" and upstream.calls[0]["body"]["provider"] == {"data_collection": "deny"}


async def test_cost_by_price_when_not_reported(settings, upstream):
    body = or_body("liquid__d1.1", "core_ru_0")
    del body["usage"]["cost"]
    upstream.push("liquid/d1.1", body)
    r = await run(settings, upstream)
    assert r.usage.cost_source == "price" and r.usage.cost_usd == pytest.approx(394 * 2e-8)


def test_unavailable_without_key(settings, upstream):
    settings.openrouter_api_key = None
    e = engine(settings, upstream)
    assert e.available() == (False, "OPENROUTER_API_KEY is not set in .env")


def test_profile_never_takes_extensions(settings, upstream):
    p = engine(settings, upstream).profile()
    assert p.types == ["noul", "choice", "score"]  # multi is dropped silently by OpenRouter (SPIKE §3.2)


# --- review P1: failure paths, odd answers, limits ---------------------------------------------------------------------


async def test_no_key_on_any_failure_path(settings, upstream):
    err = httpx.ConnectError("refused")
    upstream.push("liquid/d1.1", httpx.Response(503), httpx.Response(503))
    upstream.push("liquid/d1.1", err, err)
    upstream.push("liquid/d1.1", zod_400("call_topic"), httpx.Response(401, json={"error": {"message": "x"}}))
    for _ in range(3):
        assert_no_key(await run(settings, upstream))


@pytest.mark.parametrize("patch,question", [
    (lambda b: b["answers"]["call_topic"].__setitem__("choice", ["deposit"]), "call_topic"),  # unhashable
    (lambda b: b["answers"]["call_topic"].__setitem__("choice", 7), "call_topic"),
    (lambda b: b["answers"]["interest"].__setitem__("probabilities", [0.1, 0.9]), "interest"),
    (lambda b: b["answers"].__setitem__("callback_agreed", "yes"), "callback_agreed"),
])
async def test_odd_answer_costs_one_question_and_keeps_the_cost(settings, upstream, patch, question):
    body = or_body("liquid__d1.1", "core_ru_0")
    patch(body)
    upstream.push("liquid/d1.1", body)
    r = await run(settings, upstream)
    assert r.question_failures == {question: "invalid_answer"} and r.status == "partial"
    assert r.usage.cost_usd == pytest.approx(7.88e-06)


@pytest.mark.parametrize("usage,model,expect_cost,expect_tokens,expect_snapshot", [
    ({"input_tokens": 394.5, "cost": 7.88e-06}, "liquid/d1.1-20261027", 7.88e-06, None, "liquid/d1.1-20261027"),
    ({"input_tokens": "394", "cost": 7.88e-06}, ["x"], 7.88e-06, None, None),
    ({"input_tokens": 394, "cost": float("nan")}, "m", 394 * 2e-8, 394, "m"),  # NaN cost: fall back to the price
    ({"input_tokens": 394, "cost": -1}, "m", 394 * 2e-8, 394, "m"),
    ({"input_tokens": 394, "cost": True}, "m", 394 * 2e-8, 394, "m"),
])
async def test_odd_usage_and_model_fields_never_crash(settings, upstream, usage, model, expect_cost, expect_tokens,
                                                     expect_snapshot):
    """Also: the stored raw stays JSON (no NaN), so the record and the SSE frame serialize."""
    body = or_body("liquid__d1.1", "core_ru_0")
    body["usage"], body["model"] = usage, model
    # raw bytes: a provider may send a bare NaN, which httpx's own json= encoder refuses to produce
    upstream.push("liquid/d1.1", httpx.Response(200, content=json.dumps(body).encode(),
                                                headers={"content-type": "application/json"}))
    r = await run(settings, upstream)
    assert r.status == "ok" and r.usage.input_tokens == expect_tokens and r.snapshot == expect_snapshot
    assert r.usage.cost_usd == pytest.approx(expect_cost)
    json.dumps(r.model_dump(mode="json"), allow_nan=False)


@pytest.mark.parametrize("message", ["404", "null", "[1, 2]", '{"a": 1}', 42])
async def test_odd_error_message_is_refused_not_a_crash(settings, upstream, message):
    upstream.push("liquid/d1.1", httpx.Response(400, json={"error": {"message": message, "code": 400}}))
    r = await run(settings, upstream)
    assert r.request_failure == "refused"


async def test_unrelated_400_mentioning_a_question_is_not_unsupported(settings, upstream):
    body = {"error": {"message": "question 1 is a duplicate", "code": 400}}
    upstream.push("liquid/d1.1", httpx.Response(400, json=body))
    r = await run(settings, upstream)
    assert r.request_failure == "refused" and r.question_failures == {} and len(upstream.calls) == 1


async def test_second_round_400_is_refused_after_two_calls(settings, upstream):
    upstream.push("liquid/d1.1", zod_400("call_topic"), zod_400("interest"))
    r = await run(settings, upstream)
    assert r.question_failures == {"call_topic": "unsupported"}
    assert r.request_failure == "refused" and len(upstream.calls) == 2


async def test_400_naming_every_question_sends_nothing_more(settings, upstream):
    upstream.push("liquid/d1.1", zod_400(*CORE.questions))
    r = await run(settings, upstream)
    assert set(r.question_failures.values()) == {"unsupported"} and len(upstream.calls) == 1
    assert r.request_failure is None and r.status == "failed"


async def test_raw_is_capped_at_64_kb(settings, upstream):
    body = or_body("liquid__d1.1", "core_ru_0")
    body["padding"] = "x" * 100_000
    upstream.push("liquid/d1.1", body)
    r = await run(settings, upstream)
    assert r.raw["_truncated"] is True and len(json.dumps(r.raw, ensure_ascii=False).encode()) <= 64 * 1024


def test_max_request_bytes_is_pinned_in_the_example_config():
    import yaml

    from app.settings import ROOT

    cfg = yaml.safe_load((ROOT / "config.example.yaml").read_text())
    jev = next(e for e in cfg["engines"] if e["id"] == "or-jev-1.14")
    assert jev["max_request_bytes"] == 32000  # SPIKE/audit: Jev's long-context mode starts above ~32,000 bytes


@pytest.mark.parametrize("target,sent", [(31999, True), (32000, True), (32001, False)])
async def test_max_request_bytes_boundary(settings, upstream, target, sent):
    e = engine(settings, upstream, "or-jev-1.14")
    base = DecisionRequest(state="", questions=CORE.questions)
    pad = target - len(json.dumps(e.body(base, list(CORE.questions)), ensure_ascii=False).encode())
    req = DecisionRequest(state="x" * pad, questions=CORE.questions)
    assert len(json.dumps(e.body(req, list(CORE.questions)), ensure_ascii=False).encode()) == target
    upstream.default = echo_answers
    r = await e.run(req, RunOptions())
    assert (r.request_failure != "too_long") is sent and bool(upstream.calls) is sent


async def test_connect_timeout_is_retried(settings, upstream):
    upstream.push("liquid/d1.1", httpx.ConnectTimeout("syn"), or_body("liquid__d1.1", "core_ru_0"))
    r = await run(settings, upstream)
    assert r.status == "ok" and len(upstream.calls) == 2


async def test_connect_timeout_twice_is_unreachable(settings, upstream):
    upstream.push("liquid/d1.1", httpx.ConnectTimeout("syn"), httpx.ConnectTimeout("syn"))
    r = await run(settings, upstream)
    assert r.request_failure == "unreachable" and "after a retry" in r.detail


async def test_deadline_starts_after_the_queue(settings):
    """concurrency 1, each call 0.3 s, timeout 0.5 s: queued calls must not time out while waiting for the slot."""
    import asyncio

    async def slow(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.3)
        return echo_answers(json.loads(request.content))

    reg = Registry(settings, Catalog(settings.probes_dir / "catalog.json"), transport=httpx.MockTransport(slow))
    e = reg.get("or-d1.1")
    e.spec.concurrency, e.spec.timeout_s = 1, 0.5
    e.sem = asyncio.Semaphore(1)
    # 5 calls: with the deadline started before the slot, the 5th would wait 1.2 s > its 1.0 s deadline (round 2)
    results = await asyncio.gather(*(e.run(CORE, RunOptions()) for _ in range(5)))
    assert [r.status for r in results] == ["ok"] * 5


def test_retry_delay_is_two_seconds():
    from tests.conftest import ORIGINAL_RETRY_DELAY

    assert ORIGINAL_RETRY_DELAY == 2.0  # §7.1; the suite runs with 0 to stay fast


async def test_zod_400_on_another_field_is_not_unsupported(settings, upstream):
    """Only an issue at path [questions, <key>, type] names an unsupported type; a bad `criteria` is refused."""
    issues = [{"code": "invalid_type", "path": ["questions", "call_topic", "criteria"], "message": "Expected object"}]
    upstream.push("liquid/d1.1", httpx.Response(400, json={"error": {"message": json.dumps(issues), "code": 400}}))
    r = await run(settings, upstream)
    assert r.request_failure == "refused" and r.question_failures == {} and len(upstream.calls) == 1



async def test_a_400_digit_number_is_one_invalid_answer_and_keeps_the_cost(settings, upstream):
    body = or_body("liquid__d1.1", "core_ru_0")
    huge = "1" + "0" * 400
    text = json.dumps(body).replace('"noul": 0.9997594387538709', f'"noul": {huge}')
    text = text.replace('"input_tokens": 394', f'"input_tokens": {huge}')
    assert huge in text
    upstream.push("liquid/d1.1", httpx.Response(200, content=text.encode(), headers={"content-type": "application/json"}))
    r = await run(settings, upstream)
    assert r.question_failures == {"callback_agreed": "invalid_answer"} and r.usage.input_tokens is None
    assert r.usage.cost_usd == pytest.approx(7.88e-06)


def test_normalize_never_raises_whatever_the_inner_failure(monkeypatch):
    """The wrapper itself (round 2: every earlier payload was caught before it)."""
    import app.engines.base as base

    def boom(*a):
        raise RuntimeError("anything")

    monkeypatch.setattr(base, "_normalize", boom)
    assert base.normalize_answer(CORE.questions["call_topic"], {"type": "choice"}) == "invalid_answer"
