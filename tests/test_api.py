"""§15 HTTP API with SSE, §10 run records, §18 local-only guards — against a fake OpenRouter."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from tests.conftest import UI, echo_answers, load, or_body, parse_sse

CORE = load("requests/core_ru.json")


@pytest.fixture
def client(settings, upstream, local):
    app = create_app(settings, transport=upstream.transport, refresh_catalog=False, local=local)
    with TestClient(app, base_url=UI) as c:
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
    assert engines["mlx-d1-2b"]["available"] and engines["mlx-d1-2b"]["paid"] is False
    assert not engines["mlx-d1-omni"]["available"] and "make models" in engines["mlx-d1-omni"]["reason"]
    assert not engines["ollama-d1-2b"]["available"] and "P7" in engines["ollama-d1-2b"]["reason"]
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


def test_budget_stop_before_any_call(settings, upstream, local):
    settings.daily_budget_usd = 1e-9
    app = create_app(settings, transport=upstream.transport, refresh_catalog=False, local=local)
    with TestClient(app, base_url=UI) as c:
        events = post_run(c, body())
    assert [n for n, _ in events] == ["budget_stop"]
    assert events[0][1]["estimate_usd"] > 0
    assert not upstream.calls


def test_free_run_is_not_stopped_by_a_spent_budget(settings, upstream, local, fake_backend):
    settings.daily_budget_usd = 0.0
    app = create_app(settings, transport=upstream.transport, refresh_catalog=False, local=local)
    with TestClient(app, base_url=UI) as c:
        events = post_run(c, body(engines=("mlx-d1-2b",)))
        assert [n for n, _ in events][-1] == "run_done"
        assert post_run(c, body(engines=("mlx-d1-2b", "or-d1.1")))[0][0] == "budget_stop"
    assert len(fake_backend.calls) == 1 and not upstream.calls


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
    ({"engines": ["mlx-d1-omni"]}, 400),
    ({"engines": ["ollama-d1-2b"]}, 400),
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


# --- review P1 -------------------------------------------------------------------------------------------------------


def test_preset_with_d1_multiple_comes_as_multi_and_never_reaches_the_cloud(client, upstream):
    p = client.get("/api/presets/demo_extraction").json()
    assert p["questions"]["products"]["type"] == "multi" and "multiple" not in p["questions"]["products"]
    upstream.default = echo_answers
    req = {"state": p["state"], "questions": {**p["questions"], "callback": CORE["questions"]["callback_agreed"]}}
    events = post_run(client, {"request": req, "engines": ["or-d1.1"]})
    assert events[-1][0] == "run_done"
    assert list(upstream.calls[0]["body"]["questions"]) == ["callback"]


def test_unsaved_run_still_counts_against_the_budget(settings, upstream, local, monkeypatch):
    from app.store import Store

    upstream.default = echo_answers  # each answer reports cost 0.000002
    app = create_app(settings, transport=upstream.transport, refresh_catalog=False, local=local)
    with TestClient(app, base_url=UI) as c:
        est = c.post("/api/estimate", json=body(engines=("or-d1.1",))).json()["total_usd"]

        def broken(self, record):
            raise OSError(28, "No space left on device")

        monkeypatch.setattr(Store, "save", broken)
        first = post_run(c, body(engines=("or-d1.1",)))
        assert first[-1][0] == "run_error" and "No space left" in first[-1][1]["message"]
        status = c.get("/api/budget").json()
        assert status["spent_usd"] == pytest.approx(0.000002) and status["reserved_usd"] == 0
        app.state.budget.limit = 0.000002 + est * 0.5  # what is left cannot cover another run
        assert [n for n, _ in post_run(c, body(engines=("or-d1.1",)))] == ["budget_stop"]
    assert len(upstream.calls) == 1


def test_engine_crash_is_contained(client, monkeypatch):
    from app.engines.openrouter_decisions import OpenRouterDecisions

    async def boom(self, req, opts):
        raise RuntimeError("bug")

    monkeypatch.setattr(OpenRouterDecisions, "run", boom)
    events = post_run(client, body(engines=("or-d1.1",)))
    names = [n for n, _ in events]
    assert names[-1] == "run_done" and "engine_failed" in names
    failed = [p for n, p in events if n == "engine_failed"][0]["result"]
    assert failed["request_failure"] == "provider_error" and failed["detail"] == "engine error: RuntimeError"


def test_every_stream_ends_with_one_terminal_event(client, upstream, monkeypatch):
    upstream.default = echo_answers
    for engines in (("or-d1.1",), ("or-d1.1", "or-jev-1.14")):
        names = [n for n, _ in post_run(client, body(engines=engines))]
        assert sum(n in ("run_done", "budget_stop", "run_error") for n in names) == 1 and names[-1] == "run_done"


def test_rerun_does_not_inherit_no_zdr_consent(client, upstream):
    upstream.default = echo_answers
    first = post_run(client, body(engines=("or-no-zdr",), options={"allow_no_zdr": True}))
    run_id = first[0][1]["run_id"]
    assert len(upstream.calls) == 1
    with client.stream("POST", f"/api/runs/{run_id}/rerun") as r:
        again = parse_sse("".join(r.iter_text()))
    result = [p for n, p in again if n == "engine_failed"][0]["result"]
    assert result["request_failure"] == "refused" and len(upstream.calls) == 1
    with client.stream("POST", f"/api/runs/{run_id}/rerun", json={"allow_no_zdr": True}) as r:
        confirmed = parse_sse("".join(r.iter_text()))
    assert [n for n, _ in confirmed][-2:] == ["engine_done", "run_done"] and len(upstream.calls) == 2


def test_page_cannot_be_framed(client):
    for path in ("/", "/static/js/app.js", "/api/budget"):
        h = client.get(path).headers
        assert h["x-frame-options"] == "DENY" and "frame-ancestors 'none'" in h["content-security-policy"]


@pytest.mark.parametrize("headers", [{"Origin": "null"}, {"Origin": "http://127.0.0.1:9999"},
                                     {"Sec-Fetch-Site": "cross-site"}])
def test_foreign_writes_are_refused(client, upstream, headers):
    assert client.post("/api/runs", json=body(), headers=headers).status_code == 403
    assert not upstream.calls


def test_same_origin_write_passes(client, upstream):
    upstream.default = echo_answers
    r = client.post("/api/estimate", json=body(), headers={"Origin": "http://127.0.0.1:8790", "Sec-Fetch-Site": "same-origin"})
    assert r.status_code == 200


def test_testserver_host_is_not_trusted(client):
    assert client.get("/api/budget", headers={"Host": "testserver"}).status_code == 400
    assert client.get("/api/budget", headers={"Host": "localhost:8790"}).status_code == 200


@pytest.mark.parametrize("env", [{"app_host": "0.0.0.0"}, {"app_host": "192.168.1.5"}, {"app_host": "::1"},
                                 {"app_host": "127.0.0.2"}, {"daily_budget_usd": "nan"}, {"daily_budget_usd": -1},
                                 {"daily_budget_usd": "inf"}])
def test_unsafe_ui_settings_refuse_to_start(env):
    from pydantic import ValidationError

    from app.settings import Settings

    with pytest.raises(ValidationError):
        Settings(_env_file=None, **env)


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost"])
def test_loopback_ui_hosts_are_accepted(host):
    from app.settings import Settings

    assert Settings(_env_file=None, app_host=host).app_host == host


def test_no_key_in_the_stream_on_failures(client, upstream):
    import httpx

    upstream.push("liquid/d1.1", httpx.Response(401, json={"error": {"message": "User not found.", "code": 401}, "user_id": "user_3X"}))
    with client.stream("POST", "/api/runs", json=body(engines=("or-d1.1", "or-jev-1.14"))) as r:
        text = "".join(r.iter_text())
    assert "sk-or-v1-test" not in text and "user_3X" not in text


# --- review round 2 --------------------------------------------------------------------------------------------------


def test_reconcile_runs_at_start_up(settings, upstream, local):
    from app.store import local_day, new_id

    rid = new_id()
    day = local_day()
    path = settings.runs_dir / day / f"{rid}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"id": rid, "created_at": "2026-11-09T12:00:00Z", "day": day, "kind": "compare",
                                "engines": [], "results": [], "cost_usd": 0.5}))
    app = create_app(settings, transport=upstream.transport, refresh_catalog=False, local=local)
    with TestClient(app, base_url=UI) as c:
        assert c.get(f"/api/runs/{rid}").status_code == 200
        assert c.get("/api/budget").json()["spent_usd"] == pytest.approx(0.5)


def test_sse_frames_refuse_nan():
    from app.runner import sse

    with pytest.raises(ValueError):
        sse("run_done", {"cost_usd": float("nan")})


def test_unload_behind_a_hung_call_answers_409(settings, upstream, local, fake_backend, monkeypatch):
    import threading

    import app.main as main_module

    monkeypatch.setattr(main_module, "UNLOAD_WAIT_S", 0.2)
    fake_backend.call_delay = 1.5
    app = create_app(settings, transport=upstream.transport, refresh_catalog=False, local=local)
    with TestClient(app, base_url=UI) as c:
        t = threading.Thread(target=lambda: post_run(c, body(engines=("mlx-d1-2b",))))
        t.start()
        import time as _time

        _time.sleep(0.4)
        assert c.post("/api/local/models/mlx-d1-2b/unload").status_code == 409
        t.join()


async def test_catalog_refresh_survives_an_odd_shape(tmp_path):
    import httpx

    from app.catalog import Catalog

    def handler(request):
        return httpx.Response(200, json={"data": [1]})

    cat = Catalog(tmp_path / "catalog.json", transport=httpx.MockTransport(handler))
    await cat.refresh_quietly()
    assert cat.error and "TypeError" in cat.error


# --- review round 3 --------------------------------------------------------------------------------------------------


def test_an_oversized_ui_body_is_413(client, upstream):
    r = client.post("/api/runs", content=b"x", headers={"Content-Type": "application/json",
                                                        "Content-Length": str(17 * 1024 * 1024)})
    assert r.status_code == 413 and not upstream.calls


@pytest.mark.parametrize("out,expected", [("Sergeys-MacBook-Pro\n", "Sergeys-MacBook-Pro.local"), ("", None)])
def test_compat_address_uses_the_bonjour_name(monkeypatch, out, expected):
    import socket
    import subprocess

    import app.main as main_module

    main_module.compat_address.cache_clear()
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout=out))
    monkeypatch.setattr(socket, "gethostname", lambda: "dhcp-host.local")
    got = main_module.compat_address("0.0.0.0")
    assert got == (expected or "dhcp-host.local")
    assert main_module.compat_address("127.0.0.1") == "127.0.0.1"
    main_module.compat_address.cache_clear()
