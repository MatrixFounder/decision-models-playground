"""§12, UC-3: a dataset run over engines — the stream, the budget, the record, the report, and no call text kept."""

from __future__ import annotations

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.engines.openrouter_decisions import OpenRouterDecisions
from app.main import create_app
from tests.conftest import UI, echo_answers, parse_sse

PHRASES = [("pos-01", "Оставьте сообщение после сигнала", "true"), ("pos-02", "Вы позвонили на автоответчик", "true"),
           ("neg-01", "Оставьте номер, он перезвонит", "false"), ("neg-02", "Напишите мне в чат", "false")]
QUESTIONS = {"is_tag": {"type": "noul", "instructions": "Есть ли в звонке: Автоответчик?"}}
COST = 0.000002


def jsonl(rows=PHRASES) -> str:
    return "\n".join(json.dumps({"id": i, "state": s, "labels": {"is_tag": lab}}, ensure_ascii=False) for i, s, lab in rows)


@pytest.fixture
def client(settings, upstream, local):
    app = create_app(settings, transport=upstream.transport, refresh_catalog=False, local=local)
    with TestClient(app, base_url=UI) as c:
        yield c


def upload(client, name="am", content=None, **extra):
    body = {"name": name, "questions": QUESTIONS, "format": "jsonl", "content": content or jsonl(),
            "label_source": "test_phrases", **extra}
    return client.post("/api/datasets", json=body)


def run(client, name="am", **body):
    with client.stream("POST", f"/api/datasets/{name}/run", json={"engines": ["or-d1.1"], **body}) as r:
        assert r.status_code == 200, r.read()
        return parse_sse("".join(r.iter_text()))


def by_phrase(p_of):
    """An upstream answering P(true) by the state's text."""
    def handler(body):
        res = json.loads(echo_answers(body).content)
        res["answers"]["is_tag"]["noul"] = p_of(body["state"])
        return httpx.Response(200, json=res)
    return handler


GOOD = by_phrase(lambda s: 0.95 if "сигнал" in s or "автоответчик" in s else 0.08)


def test_upload_list_and_preview(client):
    r = upload(client)
    assert r.status_code == 201 and r.json()["items"] == 4 and r.json()["labels"] == {"is_tag": {"true": 2, "false": 2}}
    assert upload(client).status_code == 409
    assert upload(client, overwrite=True).status_code == 201
    assert [d["name"] for d in client.get("/api/datasets").json()["datasets"]] == ["am"]
    d = client.get("/api/datasets/am", params={"preview": 2}).json()
    assert [i["id"] for i in d["preview"]] == ["pos-01", "pos-02"] and d["questions"]["is_tag"]["type"] == "noul"
    assert [i["id"] for i in client.get("/api/datasets/am", params={"ids": "neg-02,zzz"}).json()["items"]] == ["neg-02"]
    assert client.get("/api/datasets/nope").status_code == 404
    assert upload(client, name="bad", content='{"state": "x", "labels": {"is_tag": "?"}}').status_code == 400
    assert upload(client, name="Bad Name").status_code == 400
    r = client.post("/api/datasets", json={"name": "q", "question_set": "std_tags/std_answering_machine",
                                           "format": "csv", "content": "state,label\nоставьте сообщение,да\n"})
    assert r.status_code == 201 and r.json()["question_set"] == "std_tags/std_answering_machine"


def test_a_dataset_run_streams_saves_and_reports(client, upstream, settings):
    upload(client)
    upstream.default = GOOD
    est = client.post("/api/datasets/am/estimate", json={"engines": ["or-d1.1", "or-jev-1.14"]}).json()
    assert est["calls"] == 8 and est["total_usd"] > 0 and set(est["per_engine"]) == {"or-d1.1", "or-jev-1.14"}
    events = run(client, engines=["or-d1.1", "or-jev-1.14"])
    names = [n for n, _ in events]
    assert names[0] == "run_started" and names[-1] == "run_done" and names.count("progress") == 8
    assert [p["done"] for n, p in events if n == "progress"] == list(range(1, 9))
    done = events[-1][1]
    summary = {s["engine_id"]: s for s in done["summary"]}
    assert summary["or-d1.1"]["whip"]["agreement"] == 1.0 and summary["or-d1.1"]["answered"] == 4
    # the record: kind dataset, compact answers, ids and labels — no call text anywhere
    record = client.get(f"/api/runs/{done['run_id']}").json()
    assert record["kind"] == "dataset" and record["dataset"]["name"] == "am" and len(record["items"]) == 4
    assert record["cost_usd"] == pytest.approx(8 * COST)
    text = json.dumps(record, ensure_ascii=False)
    assert all(state not in text for _, state, _ in PHRASES) and '"raw"' not in text
    # the report files: Markdown and JSON in reports/, no call text either
    files = done["report_files"]
    for rel in files.values():
        body = (settings.data_dir / rel).read_text(encoding="utf-8")
        assert all(state not in body for _, state, _ in PHRASES)
    md = (settings.data_dir / files["md"]).read_text(encoding="utf-8")
    assert "# Отчёт калибровки: am" in md and "`or-d1.1`" in md and "100.0 %" in md
    # History: one row, a status per engine, the dataset's name; the budget counts it
    row = client.get("/api/runs", params={"kind": "dataset"}).json()["runs"][0]
    assert row["status"] == {"or-d1.1": "ok", "or-jev-1.14": "ok"} and row["dataset"] == "am"
    assert row["snapshots"]["or-d1.1"] == "liquid/d1.1-20261027"
    b = client.get("/api/budget").json()
    assert b["spent_usd"] == pytest.approx(8 * COST) and b["reserved_usd"] == 0


def test_the_report_endpoint(client, upstream):
    upload(client)
    upstream.default = by_phrase(lambda s: {"Оставьте сообщение после сигнала": 0.95, "Вы позвонили на автоответчик": 0.7,
                                            "Оставьте номер, он перезвонит": 0.6}.get(s, 0.1))
    run_id = run(client)[-1][1]["run_id"]
    rep = client.get(f"/api/reports/{run_id}").json()
    g = rep["groups"][0]
    assert g["engine_id"] == "or-d1.1" and g["snapshot"] == "liquid/d1.1-20261027"
    assert g["metrics"]["whip"]["t"] == 0.61 and g["metrics"]["whip"]["agreement"] == 1.0
    assert rep["items"][0] == {"id": "pos-01", "labels": {"is_tag": "true"},
                               "answers": {"or-d1.1": {"is_tag": {"true": 0.95, "false": pytest.approx(0.05)}}}}
    # another target recomputes; Markdown downloads
    assert client.get(f"/api/reports/{run_id}", params={"target": 0.99}).json()["target"] == 0.99
    md = client.get(f"/api/reports/{run_id}", params={"format": "md", "lang": "en"})
    assert md.status_code == 200 and md.text.startswith("# Calibration report: am")
    assert "attachment" in md.headers["content-disposition"]
    assert client.get(f"/api/reports/{run_id}", params={"target": 1.5}).status_code == 400
    assert client.get(f"/api/reports/{run_id}", params={"format": "xml"}).status_code == 400
    assert client.get("/api/reports/nope").status_code == 404


def test_disagreements_list_the_wrong_items(client, upstream):
    upload(client)
    upstream.default = by_phrase(lambda s: 0.9)  # says yes to everything: both negatives disagree
    rep = client.get(f"/api/reports/{run(client)[-1][1]['run_id']}").json()
    g = rep["groups"][0]
    assert g["errors_total"] == 2 and {e["id"] for e in g["errors"]} == {"neg-01", "neg-02"}


def test_a_run_over_the_budget_does_not_start(settings, upstream, local):
    settings.daily_budget_usd = 1e-9
    app = create_app(settings, transport=upstream.transport, refresh_catalog=False, local=local)
    with TestClient(app, base_url=UI) as c:
        upload(c)
        assert [n for n, _ in run(c)] == ["budget_stop"]
    assert not upstream.calls


def test_the_budget_running_out_mid_run_stops_the_rest(client, upstream, monkeypatch):
    """The estimate was too low: the reservation is topped up per call while the day allows, then the items left
    are `budget`, not asked; the run is saved and ends with budget_stop (TASK §6.2). After the first $0.40 call a
    call holds $0.40, not its $0.000001 estimate: the day stops inside the limit."""
    import app.batch as batch

    monkeypatch.setattr(batch, "lane_width", lambda e: 1)  # one call at a time: the order is fixed
    upload(client)
    upstream.default = lambda body: httpx.Response(200, json={**json.loads(echo_answers(body).content),
                                                              "usage": {"input_tokens": 100, "cost": 0.4}})
    monkeypatch.setattr(OpenRouterDecisions, "estimate_cost", lambda self, req: 0.000001)
    events = run(client)
    assert events[-1][0] == "budget_stop" and events[-1][1]["run_id"]
    record = client.get(f"/api/runs/{events[-1][1]['run_id']}").json()
    failures = [it["results"]["or-d1.1"]["request_failure"] for it in record["items"]]
    # $1 a day: two calls of $0.40; a third would hold another $0.40
    assert failures == [None, None, "budget", "budget"]
    assert len(upstream.calls) == 2 and record["budget_stopped"] is True
    b = client.get("/api/budget").json()
    assert b["spent_usd"] == pytest.approx(0.8) and b["reserved_usd"] == 0
    assert "budget_stopped" in client.get(f"/api/reports/{record['id']}").json()


def test_an_engine_crash_is_one_items_failure(client, upstream, monkeypatch):
    upload(client)
    upstream.default = GOOD
    real, calls = OpenRouterDecisions.run, []

    async def crashing(self, req, opts):
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError("engine bug")
        return await real(self, req, opts)

    monkeypatch.setattr(OpenRouterDecisions, "run", crashing)
    events = run(client)
    assert events[-1][0] == "run_done"
    record = client.get(f"/api/runs/{events[-1][1]['run_id']}").json()
    assert sum(it["results"]["or-d1.1"]["request_failure"] == "provider_error" for it in record["items"]) == 1
    g = client.get(f"/api/reports/{record['id']}").json()["groups"][0]
    assert g["metrics"]["answered"] == 3 and g["metrics"]["items"] == 4


def test_a_failed_save_still_charges(client, upstream, monkeypatch):
    upload(client)
    upstream.default = GOOD

    def broken(record):
        raise OSError("disk full")

    monkeypatch.setattr(client.app.state.store, "save", broken)
    events = run(client)
    assert events[-1][0] == "run_error" and "disk full" in events[-1][1]["message"]
    b = client.get("/api/budget").json()
    assert b["spent_usd"] == pytest.approx(4 * COST) and b["reserved_usd"] == 0


def test_a_failed_report_file_still_ends_the_run(client, upstream, monkeypatch):
    upload(client)
    upstream.default = GOOD

    def broken(*args):
        raise OSError("read-only reports/")

    monkeypatch.setattr(client.app.state.dataset_runner, "write_report", broken)
    done = run(client)[-1]
    assert done[0] == "run_done" and "read-only" in done[1]["report_error"]
    assert client.get(f"/api/reports/{done[1]['run_id']}").status_code == 200  # rebuilt from the record


def test_a_local_engine_takes_the_items_one_at_a_time(client, fake_backend):
    """One model in memory and a short queue: a lane of width 1, so 12 items never overflow the queue (busy)."""
    fake_backend.call_delay = 0.01
    rows = [(f"i{n:02d}", f"фраза {n}", "true" if n % 2 else "false") for n in range(12)]
    upload(client, content=jsonl(rows))
    runner = client.app.state.runner
    real, active, peak = runner.call, [0], [0]

    async def counting(engine, req, opts):
        active[0] += 1
        peak[0] = max(peak[0], active[0])
        try:
            return await real(engine, req, opts)
        finally:
            active[0] -= 1

    runner.call = counting
    events = run(client, engines=["mlx-d1-2b"])
    assert peak[0] == 1
    assert events[-1][0] == "run_done"
    record = client.get(f"/api/runs/{events[-1][1]['run_id']}").json()
    assert all(it["results"]["mlx-d1-2b"]["status"] == "ok" for it in record["items"])
    assert len(fake_backend.calls) == 12


def test_local_engines_take_turns_not_swapping_the_model(client, fake_backend):
    """Live UC-3: two local lanes side by side reloaded the model on every item. Each local engine runs whole, in
    turn: two loads for two engines, whatever the item count."""
    from tests.conftest import D1_2B, D1_OMNI

    fake_backend.downloaded.add(D1_OMNI)
    upload(client, content=jsonl([(f"i{n}", f"фраза {n}", "true") for n in range(6)]))
    events = run(client, engines=["mlx-d1-2b", "mlx-d1-omni"])
    assert events[-1][0] == "run_done"
    assert fake_backend.loads == [D1_2B, D1_OMNI]
    record = client.get(f"/api/runs/{events[-1][1]['run_id']}").json()
    assert set(record["items"][0]["results"]["mlx-d1-2b"]["timings"]) >= {"queue_ms", "load_ms", "call_ms"}


def test_a_floating_model_that_moves_mid_run_shows_two_snapshots(client, upstream):
    upload(client)
    answers = iter(["liquid/d1.1-20261027", "liquid/d1.1-20261027", "liquid/d1.2-20261201", "liquid/d1.2-20261201"])
    upstream.default = lambda body: echo_answers(body, model=next(answers))
    with client.stream("POST", "/api/datasets/am/run", json={"engines": ["or-d1-latest"]}) as r:
        run_id = parse_sse("".join(r.iter_text()))[-1][1]["run_id"]
    groups = client.get(f"/api/reports/{run_id}").json()["groups"]
    assert sorted((g["snapshot"], g["metrics"]["answered"]) for g in groups) == [
        ("liquid/d1.1-20261027", 2), ("liquid/d1.2-20261201", 2)]


@pytest.mark.parametrize("body,status", [({"engines": ["nope"]}, 400), ({"engines": []}, 422),
                                          ({"engines": ["or-d1.1"], "limit": 0}, 422)])
def test_bad_run_requests_are_refused_before_the_stream(client, body, status):
    upload(client)
    assert client.post("/api/datasets/am/run", json=body).status_code == status
    assert client.post("/api/datasets/nope/run", json={"engines": ["or-d1.1"]}).status_code == 404


def test_limit_takes_the_first_items(client, upstream):
    upload(client)
    upstream.default = GOOD
    events = run(client, limit=2)
    assert events[0][1]["items"] == 2 and len(upstream.calls) == 2


def test_a_zdr_off_engine_needs_consent_per_run(client, upstream):
    upload(client)
    upstream.default = GOOD
    events = run(client, engines=["or-no-zdr"])
    record = client.get(f"/api/runs/{events[-1][1]['run_id']}").json()
    assert {it["results"]["or-no-zdr"]["request_failure"] for it in record["items"]} == {"refused"} and not upstream.calls
    run(client, engines=["or-no-zdr"], options={"allow_no_zdr": True})
    assert len(upstream.calls) == 4 and all("zdr" not in c["body"]["provider"] for c in upstream.calls)


# --- review P4: money, failures, ordering ---------------------------------------------------------------------------

def dear(cost: float):
    """An upstream whose every call costs `cost`, far above the engine's estimate."""
    return lambda body: httpx.Response(200, json={**json.loads(echo_answers(body).content),
                                                  "usage": {"input_tokens": 100, "cost": cost}})


def test_a_cloud_budget_stop_leaves_the_free_local_items(client, upstream, fake_backend, monkeypatch):
    """The day's money stops the paid calls only: a local engine costs nothing and answers every item."""
    rows = [(f"i{n}", f"фраза {n}", "true") for n in range(8)]
    upload(client, content=jsonl(rows))
    upstream.default = dear(0.4)
    monkeypatch.setattr(OpenRouterDecisions, "estimate_cost", lambda self, req: 0.000001)
    events = run(client, engines=["or-d1.1", "mlx-d1-2b"])
    assert events[-1][0] == "budget_stop"
    record = client.get(f"/api/runs/{events[-1][1]['run_id']}").json()
    local = [it["results"]["mlx-d1-2b"]["status"] for it in record["items"]]
    cloud = [it["results"]["or-d1.1"]["request_failure"] for it in record["items"]]
    assert local == ["ok"] * 8 and len(fake_backend.calls) == 8
    assert cloud.count("budget") >= 6 and client.get("/api/budget").json()["spent_usd"] <= 1.0
    groups = {g["engine_id"]: g for g in client.get(f"/api/reports/{record['id']}").json()["groups"]}
    assert groups["mlx-d1-2b"]["metrics"]["items"] == 8 and groups["mlx-d1-2b"]["not_asked"] == 0
    # items never asked leave the coverage denominator and are counted apart
    assert groups["or-d1.1"]["not_asked"] == cloud.count("budget")
    assert groups["or-d1.1"]["metrics"]["items"] == 8 - cloud.count("budget")


def test_concurrent_lanes_overshoot_by_at_most_one_call_per_engine(client, upstream, monkeypatch):
    """Review P4: with concurrency 4 every worker passed on a $0.000001 estimate before any $0.40 landed — 10 calls,
    $4 on a $1 day. Each lane's first call goes alone, later calls hold the dearest cost seen."""
    rows = [(f"i{n}", f"фраза {n}", "true") for n in range(8)]
    upload(client, content=jsonl(rows))
    upstream.default = dear(0.4)
    monkeypatch.setattr(OpenRouterDecisions, "estimate_cost", lambda self, req: 0.000001)
    events = run(client, engines=["or-d1.1", "or-jev-1.14"])
    assert events[-1][0] == "budget_stop"
    assert len(upstream.calls) == 2  # the two first calls; the next would hold $0.40 more than the day has
    assert client.get("/api/budget").json()["spent_usd"] == pytest.approx(0.8)


def test_the_in_flight_estimate_is_given_back(client, upstream, monkeypatch):
    """Each call holds its estimate while in flight and then its real cost: ten calls estimated at $0.10 and
    costing $0.01 fit a $1 day. Holding the estimate after the call (the decrement lost) stops the run early."""
    rows = [(f"i{n}", f"фраза {n}", "true") for n in range(10)]
    upload(client, content=jsonl(rows))
    upstream.default = dear(0.01)
    monkeypatch.setattr(OpenRouterDecisions, "estimate_cost", lambda self, req: 0.1)
    events = run(client)
    assert events[-1][0] == "run_done" and len(upstream.calls) == 10


def test_an_estimate_that_fails_mid_run_is_one_items_failure(client, upstream, monkeypatch):
    """Review P4: an exception outside engine.run escaped its lane; the other lanes kept calling after run_error,
    outside the reservation. Now it is that item's provider_error and the run ends normally. (Estimates are cached per
    request since P5, so the failure is planted in Runner.call itself — the profile, the split — not in the engine.)"""
    upload(client)
    upstream.default = GOOD
    runner = client.app.state.runner
    real, seen = runner.call, []

    async def flaky(engine, req, opts):
        if engine.spec.id == "or-jev-1.14":
            seen.append(1)
            if len(seen) == 2:
                raise RuntimeError("profile file broken")
        return await real(engine, req, opts)

    runner.call = flaky
    events = run(client, engines=["or-jev-1.14", "or-d1.1"])
    assert events[-1][0] == "run_done"
    record = client.get(f"/api/runs/{events[-1][1]['run_id']}").json()
    jev = [it["results"]["or-jev-1.14"]["request_failure"] for it in record["items"]]
    assert jev.count("provider_error") == 1 and len(upstream.calls) == 7
    assert all(it["results"]["or-d1.1"]["status"] == "ok" for it in record["items"])
    assert client.get("/api/budget").json()["reserved_usd"] == 0


def test_a_cancelled_run_charges_the_calls_in_flight(client, upstream, monkeypatch):
    """A shutdown mid-run: the calls already sent may be billed — the unsaved charge holds them too."""
    import asyncio

    from app.dataset_run import DatasetRunIn

    upload(client)

    async def slow(self, req, opts):
        await asyncio.sleep(30)

    monkeypatch.setattr(OpenRouterDecisions, "run", slow)
    monkeypatch.setattr(OpenRouterDecisions, "estimate_cost", lambda self, req: 0.01)
    state = client.app.state
    ds = state.datasets.load("am")
    engines = state.runner.engines_for(["or-d1.1"])

    async def go():
        async def emit(name, payload):
            pass

        task = asyncio.create_task(state.dataset_runner.execute(ds, engines, DatasetRunIn(engines=["or-d1.1"]), emit))
        await asyncio.sleep(0.05)  # the first call is in flight
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(go())
    b = client.get("/api/budget").json()
    assert b["spent_usd"] == pytest.approx(0.01) and b["reserved_usd"] == 0  # one call in flight, held at $0.01


def test_a_nan_estimate_stops_before_any_call(client, upstream, monkeypatch):
    upload(client)
    monkeypatch.setattr(OpenRouterDecisions, "estimate_cost", lambda self, req: float("nan"))
    events = run(client)
    assert [n for n, _ in events] == ["budget_stop"] and events[0][1]["estimate_usd"] is None
    assert not upstream.calls


def test_the_record_keeps_no_provider_message(client, upstream):
    """A provider's error text can quote the request: the record keeps the class and the status only."""
    upload(client)
    upstream.default = lambda body: httpx.Response(400, json={"error": {"message": f"bad state: {body['state']}",
                                                                        "code": 400}})
    record = client.get(f"/api/runs/{run(client)[-1][1]['run_id']}").json()
    text = json.dumps(record, ensure_ascii=False)
    assert all(state not in text for _, state, _ in PHRASES)
    assert record["items"][0]["results"]["or-d1.1"]["http_status"] == 400


def test_a_run_past_midnight_counts_on_the_day_it_ends(client, upstream, monkeypatch):
    import app.dataset_run as dr

    upload(client)
    upstream.default = GOOD
    monkeypatch.setattr(dr, "local_day", lambda ts=None: "2026-11-10" if ts is None else "2026-11-09")
    record = client.get(f"/api/runs/{run(client)[-1][1]['run_id']}").json()
    assert record["day"] == "2026-11-10"


def test_cloud_lanes_do_not_wait_for_the_local_engines(client, upstream, fake_backend, monkeypatch):
    import time

    from tests.conftest import FakeModel

    rows = [(f"i{n}", f"фраза {n}", "true") for n in range(6)]
    upload(client, content=jsonl(rows))
    cloud_at, local_at = [], []
    upstream.default = lambda body: (cloud_at.append(time.time()), GOOD(body))[1]
    real = FakeModel.system_one

    def timed(self, *a, **k):
        time.sleep(0.05)
        local_at.append(time.time())
        return real(self, *a, **k)

    monkeypatch.setattr(FakeModel, "system_one", timed)
    run(client, engines=["mlx-d1-2b", "or-d1.1"])
    assert len(cloud_at) == 6 and len(local_at) == 6
    assert max(cloud_at) < max(local_at)  # the cloud lane finished while the local one was still working


def test_disagreements_round_as_whip_insight(client, upstream):
    """D-32 in the list of disagreements too: P(true) 0.69951 is 0.700 — a yes at t = 0.70, not an error."""
    upload(client, content=jsonl([("a", "фраза а", "true"), ("b", "фраза б", "false")]))
    upstream.default = by_phrase(lambda s: 0.69951 if s == "фраза а" else 0.695)
    g = client.get(f"/api/reports/{run(client)[-1][1]['run_id']}").json()["groups"][0]
    assert g["metrics"]["whip"]["t"] == 0.7 and g["metrics"]["whip"]["agreement"] == 1.0
    assert g["errors_total"] == 0


def test_the_report_endpoint_is_for_dataset_runs_only(client, upstream):
    upstream.default = echo_answers
    with client.stream("POST", "/api/runs", json={"request": {"state": "x", "questions": QUESTIONS},
                                                 "engines": ["or-d1.1"]}) as r:
        run_id = parse_sse("".join(r.iter_text()))[-1][1]["run_id"]
    assert client.get(f"/api/reports/{run_id}").status_code == 404


def test_dataset_texts_are_cut_to_300_characters(client):
    upload(client, content=jsonl([("long", "а" * 400, "true")]))
    d = client.get("/api/datasets/am", params={"ids": "long"}).json()
    assert d["items"][0]["state"] == "а" * 300 + "…" and d["preview"][0]["state"] == "а" * 300 + "…"


def test_history_status_of_a_dataset_run():
    from app.store import index_row

    def rec(*states):
        return {"id": "x", "created_at": "t", "day": "d", "kind": "dataset", "dataset": {"name": "am"},
                "engines": [{"engine_id": "e"}],
                "items": [{"id": str(n), "results": {"e": {"status": s}}} for n, s in enumerate(states)]}

    assert index_row(rec("ok", "ok"))["status"] == {"e": "ok"}
    assert index_row(rec("ok", "failed"))["status"] == {"e": "partial"}
    assert index_row(rec("failed", "failed"))["status"] == {"e": "failed"}
    assert index_row(rec("ok"))["dataset"] == "am"


def test_a_report_that_cannot_be_built_still_ends_the_run(client, upstream, monkeypatch):
    import app.dataset_run as dr

    upload(client)
    upstream.default = GOOD

    def broken(record):
        raise ValueError("NaN in a bin")

    monkeypatch.setattr(dr, "build_report", broken)
    done = run(client)[-1]
    assert done[0] == "run_done" and "NaN in a bin" in done[1]["report_error"] and done[1]["summary"] == []


def test_the_report_endpoint_builds_off_the_event_loop(client, upstream, monkeypatch):
    """Review P4 (F12): every target change rebuilt the report on the loop the compat server shares."""
    import asyncio

    import app.main as main_mod

    upload(client)
    upstream.default = GOOD
    run_id = run(client)[-1][1]["run_id"]
    real, on_loop = main_mod.build_report, []

    def spy(record, target):
        try:
            asyncio.get_running_loop()
            on_loop.append(True)
        except RuntimeError:  # a worker thread has no running loop
            on_loop.append(False)
        return real(record, target)

    monkeypatch.setattr(main_mod, "build_report", spy)
    assert client.get(f"/api/reports/{run_id}", params={"target": 0.8}).status_code == 200
    assert client.get(f"/api/reports/{run_id}", params={"format": "md"}).status_code == 200
    assert on_loop == [False, False]


def test_a_nan_estimate_stops_a_workbench_run_too(client, upstream, monkeypatch):
    """F26 in the runner: NaN compares false, so `reserved > 0` skipped the budget check; now it is budget_stop."""
    monkeypatch.setattr(OpenRouterDecisions, "estimate_cost", lambda self, req: float("nan"))
    with client.stream("POST", "/api/runs", json={"request": {"state": "x", "questions": QUESTIONS},
                                                 "engines": ["or-d1.1"]}) as r:
        events = parse_sse("".join(r.iter_text()))
    assert [n for n, _ in events] == ["budget_stop"] and events[0][1]["estimate_usd"] is None
    assert not upstream.calls
