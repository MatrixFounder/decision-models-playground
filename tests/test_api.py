"""§15 HTTP API with SSE, §10 run records, §18 local-only guards — against a fake OpenRouter."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from tests.conftest import echo_answers, load, or_body, parse_sse

CORE = load("requests/core_ru.json")


@pytest.fixture
def client(settings, upstream):
    app = create_app(settings, transport=upstream.transport, refresh_catalog=False)
    with TestClient(app) as c:
        yield c


def post_run(client, body):
    with client.stream("POST", "/api/runs", json=body) as r:
        assert r.status_code == 200, r.read()
        assert r.headers["content-type"].startswith("text/event-stream")
        return parse_sse("".join(r.iter_text()))


def body(engines=("or-jev-1.14", "or-d1.1", "or-d1-latest"), **kw):
    return {"request": {"state": CORE["state"], "questions": CORE["questions"]}, "engines": list(engines), **kw}


def test_engines_lists_availability_and_prices(client):
    engines = {e["id"]: e for e in client.get("/api/engines").json()["engines"]}
    assert engines["or-jev-1.14"]["available"] and engines["or-jev-1.14"]["price_per_mtok"] == 0.042
    assert engines["or-d1.1"]["zdr_endpoint"] is True
    assert engines["or-d1-latest"]["zdr_endpoint"] is None  # an alias is not an endpoint
    assert not engines["mlx-d1-2b"]["available"] and "P2" in engines["mlx-d1-2b"]["reason"]
    assert "sk-or-v1-test" not in json.dumps(engines)


def test_uc1_three_cloud_models(client, upstream):
    upstream.push("typesafe/jev-1.14", or_body("typesafe__jev-1.14", "core_ru_0"))
    upstream.push("liquid/d1.1", or_body("liquid__d1.1", "core_ru_0"))
    upstream.push("~liquid/d1-latest", or_body("alias-liquid__d1-latest", "core_ru_0"))
    events = post_run(client, body(tags=["demo"], title="UC-1"))
    names = [n for n, _ in events]
    assert names[0] == "run_started" and names[-1] == "run_done"
    assert names.count("engine_started") == 3 and names.count("engine_done") == 3
    run_id = events[0][1]["run_id"]
    done = {p["engine_id"]: p["result"] for n, p in events if n == "engine_done"}
    assert done["or-jev-1.14"]["answers"]["call_topic"]["top"] == "deposit"
    assert done["or-d1-latest"]["snapshot"] == "liquid/d1.1-20261027"

    record = client.get(f"/api/runs/{run_id}").json()
    assert record["kind"] == "compare" and record["title"] == "UC-1" and record["tags"] == ["demo"]
    assert record["cost_usd"] == pytest.approx(3.99e-05 + 2 * 7.88e-06)
    assert record["versions"]["models"]["or-jev-1.14"] == "typesafe/jev-1.14-20261014"
    assert len(record["state_sha256"]) == 64
    assert "sk-or-v1-test" not in json.dumps(record)

    rows = client.get("/api/runs").json()["runs"]
    assert rows[0]["id"] == run_id and rows[0]["status"] == {"or-jev-1.14": "ok", "or-d1.1": "ok", "or-d1-latest": "ok"}
    assert client.get("/api/runs", params={"tag": "demo"}).json()["runs"]
    assert not client.get("/api/runs", params={"tag": "other"}).json()["runs"]
    assert client.get("/api/budget").json()["spent_usd"] == pytest.approx(record["cost_usd"])


def test_extension_questions_are_not_sent(client, upstream):
    upstream.default = echo_answers
    ext = load("requests/extensions_ru.json")
    req = {"state": ext["state"], "questions": {**ext["questions"], "callback": CORE["questions"]["callback_agreed"]}}
    events = post_run(client, {"request": req, "engines": ["or-d1.1"]})
    result = [p for n, p in events if n == "engine_done"][0]["result"]
    assert set(result["question_failures"]) == set(ext["questions"])
    assert set(result["question_failures"].values()) == {"unsupported"}
    assert list(upstream.calls[0]["body"]["questions"]) == ["callback"]  # multi never reaches OpenRouter


def test_only_extensions_means_no_call(client, upstream):
    ext = load("requests/extensions_ru.json")
    events = post_run(client, {"request": {"state": ext["state"], "questions": ext["questions"]}, "engines": ["or-d1.1"]})
    assert [n for n, _ in events][-2:] == ["engine_failed", "run_done"]
    assert not upstream.calls


def test_failed_engine_does_not_sink_the_run(client, upstream):
    upstream.push("liquid/d1.1", or_body("liquid__d1.1", "core_ru_0"))
    # jev gets no scripted answer: 500, retried, provider_error
    events = post_run(client, body(engines=("or-jev-1.14", "or-d1.1")))
    names = [n for n, _ in events]
    assert "engine_failed" in names and "engine_done" in names and names[-1] == "run_done"


def test_budget_stop_before_any_call(settings, upstream):
    settings.daily_budget_usd = 1e-9
    app = create_app(settings, transport=upstream.transport, refresh_catalog=False)
    with TestClient(app) as c:
        events = post_run(c, body())
    assert [n for n, _ in events] == ["budget_stop"]
    assert events[0][1]["estimate_usd"] > 0
    assert not upstream.calls


async def test_free_run_is_not_stopped_by_a_spent_budget(settings, upstream):
    from app.main import build_state
    from app.schemas import RunIn

    settings.daily_budget_usd = 0.0
    state = build_state(settings, upstream.transport)
    jev = state["registry"].get("or-jev-1.14")
    jev.estimate_cost = lambda req: 0.0  # stands in for a free local engine until P2
    upstream.default = echo_answers
    events = []

    async def emit(name, payload):
        events.append(name)

    run_in = RunIn.model_validate(body(engines=("or-jev-1.14",)))
    record = await state["runner"].execute(run_in, emit)
    assert record is not None and events[-1] == "run_done"


def test_estimate_endpoint(client):
    est = client.post("/api/estimate", json=body()).json()
    assert set(est["per_engine"]) == {"or-jev-1.14", "or-d1.1", "or-d1-latest"}
    assert est["total_usd"] > 0 and est["budget"]["limit_usd"] == 1.0


def test_rerun_repeats_inputs(client, upstream):
    upstream.default = echo_answers
    first = post_run(client, body(engines=("or-d1.1",), tags=["x"]))
    run_id = first[0][1]["run_id"]
    with client.stream("POST", f"/api/runs/{run_id}/rerun") as r:
        again = parse_sse("".join(r.iter_text()))
    assert again[-1][0] == "run_done" and again[0][1]["run_id"] != run_id
    assert upstream.calls[0]["body"] == upstream.calls[1]["body"]


@pytest.mark.parametrize("bad,code", [
    ({"engines": ["nope"]}, 400),
    ({"engines": ["mlx-d1-2b"]}, 400),
    ({"engines": []}, 422),
    ({"request": {"state": "x", "questions": {"Bad": {"type": "noul", "instructions": "?"}}}}, 422),
    ({"request": {"state": "x", "questions": {"q": {"type": "score", "instructions": "?", "criteria": ["one"]}}}}, 422),
])
def test_rejected_runs(client, upstream, bad, code):
    r = client.post("/api/runs", json={**body(), **bad})
    assert r.status_code == code
    assert not upstream.calls


def test_cross_origin_write_is_refused(client, upstream):
    r = client.post("/api/runs", json=body(), headers={"Origin": "http://evil.example"})
    assert r.status_code == 403 and not upstream.calls
    assert client.get("/api/budget", headers={"Origin": "http://evil.example"}).status_code == 200


def test_foreign_host_header_is_refused(client):
    assert client.get("/api/budget", headers={"Host": "attacker.example"}).status_code == 400


def test_presets(client):
    presets = {p["id"]: p for p in client.get("/api/presets").json()["presets"]}
    assert {"call_kind", "demo_bank_call", "demo_extraction"} <= set(presets)
    assert all("error" not in p for p in presets.values())
    p = client.get("/api/presets/demo_bank_call").json()
    assert p["state"] == CORE["state"] and p["questions"] == CORE["questions"]
    assert client.get("/api/presets/missing").status_code == 404


def test_unknown_run_is_404(client):
    assert client.get("/api/runs/01JC0000000000000000000000").status_code == 404
    assert client.get("/api/runs/../../etc").status_code == 404


def test_index_page(client):
    r = client.get("/")
    assert r.status_code == 200 and "Decision Playground" in r.text
    assert client.get("/static/js/app.js").status_code == 200
