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
    assert set(r.question_failures) == set(CORE.questions)
    assert len(upstream.calls) == 1  # no retry on 4xx


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
    assert r.status == "ok" and "provider" not in upstream.calls[0]["body"]


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
