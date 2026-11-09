"""§11, UC-2: an experiment through the API — the grid sent, the stream, the record, the report, the budget."""

from __future__ import annotations

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.engines.openrouter_decisions import OpenRouterDecisions
from app.main import create_app
from tests.conftest import UI, echo_answers, parse_sse

STATE = "Оператор: У нас новый вклад. Клиент: Интересно, расскажите."
TOPIC = {"type": "choice", "instructions": "Тема звонка?",
         "criteria": {"credit": "Кредит", "deposit": "Вклад", "complaint": "Жалоба"}}
YES = {"type": "noul", "instructions": "Клиенту интересно?"}
EN = {"topic": {"type": "choice", "instructions": "Call topic?",
                "criteria": {"credit": "Credit", "deposit": "Deposit", "complaint": "Complaint"}},
      "yes": {"type": "noul", "instructions": "Is the client interested?"}}


@pytest.fixture
def client(settings, upstream, local):
    app = create_app(settings, transport=upstream.transport, refresh_catalog=False, local=local)
    with TestClient(app, base_url=UI) as c:
        yield c


def body(**over):
    return {"request": {"state": STATE, "questions": {"topic": TOPIC, "yes": YES}}, "engines": ["or-d1.1"],
            "formulations": {"en": EN}, "repeats": 2, **over}


def run(client, **over):
    with client.stream("POST", "/api/experiments", json=body(**over)) as r:
        assert r.status_code == 200, r.read()
        return parse_sse("".join(r.iter_text()))


def answering(pick):
    """An upstream whose choice answer is `pick(shown option ids)` at 0.8; noul 0.7 in Russian, 0.6 in English."""
    def handler(body):
        res = json.loads(echo_answers(body).content)
        q = body["questions"]["topic"]
        ids = list(q["criteria"])
        top = pick(ids)
        res["answers"]["topic"] = {"type": "choice", "choice": top, "confidence": 0.8,
                                   "probabilities": {i: (0.8 if i == top else 0.1) for i in ids}}
        res["answers"]["yes"]["noul"] = 0.6 if q["instructions"] == "Call topic?" else 0.7
        return httpx.Response(200, json=res)
    return handler


def test_the_estimate_counts_the_grid(client):
    est = client.post("/api/experiments/estimate", json=body(engines=["or-d1.1", "or-jev-1.14"])).json()
    assert est["grid"] == {"formulations": ["base", "en"], "orders": 6, "repeats": 2, "variants": 24}
    assert est["calls"] == 48 and est["total_usd"] > 0


def test_an_experiment_streams_saves_and_reports(client, upstream):
    upstream.default = answering(lambda ids: "deposit")  # a model that reads: the answer does not follow the order
    events = run(client)
    names = [n for n, _ in events]
    assert names[0] == "run_started" and names[-1] == "run_done" and names.count("progress") == 24
    # the grid that went out: 6 orders of the choice, each twice per wording; the noul never reordered
    shown = [list(c["body"]["questions"]["topic"]["criteria"]) for c in upstream.calls]
    assert len({tuple(s) for s in shown}) == 6 and shown.count(["credit", "deposit", "complaint"]) == 4
    record = client.get(f"/api/runs/{events[-1][1]['run_id']}").json()
    assert record["kind"] == "experiment" and len(record["calls"]) == 24
    assert record["calls"][0]["formulation"] == "base" and record["experiment"]["n_orders"] == 6
    assert record["request"]["state"] == STATE  # an experiment is one Workbench state, kept as the Workbench keeps it
    row = client.get("/api/runs", params={"kind": "experiment"}).json()["runs"][0]
    assert row["status"] == {"or-d1.1": "ok"} and row["snapshots"]["or-d1.1"] == "liquid/d1.1-20261027"
    rep = client.get(f"/api/reports/{record['id']}").json()
    g = {x["question"]: x["metrics"] for x in rep["groups"]}
    assert g["topic"]["order"]["dp_max"] == 0.0 and g["topic"]["order"]["flip_share"] == 0.0
    assert g["topic"]["order"]["first_position_share"] == pytest.approx(1 / 3, abs=1e-3)  # deposit is first in 2 of 6
    assert g["yes"]["formulation"]["dp_mean"] == pytest.approx(0.1) and g["yes"]["order"] is None
    assert g["topic"]["repeats"]["spread_max"] == 0.0
    md = client.get(f"/api/reports/{record['id']}", params={"format": "md", "lang": "en"})
    assert md.status_code == 200 and md.text.startswith(f"# Experiment {record['id']}")
    assert "attachment" in md.headers["content-disposition"]


def test_a_model_that_leans_to_the_first_option_is_caught(client, upstream):
    """jev-1.13 "leans to the first option" (audit §4.4): the order axis exists for this."""
    upstream.default = answering(lambda ids: ids[0])
    events = run(client, formulations={})
    g = {x["question"]: x["metrics"] for x in
         client.get(f"/api/reports/{events[-1][1]['run_id']}").json()["groups"]}["topic"]
    assert g["order"]["first_position_share"] == 1.0 and g["order"]["first_position_chance"] == pytest.approx(0.3333)
    assert g["order"]["flip_share"] == pytest.approx(4 / 5)  # 4 of the 5 other orders show another option first
    assert g["order"]["dp_max"] == pytest.approx(0.7)


def test_a_local_engine_is_deterministic(client, fake_backend):
    events = run(client, engines=["mlx-d1-2b"], formulations={}, repeats=3)
    assert events[-1][0] == "run_done"
    g = {x["question"]: x["metrics"] for x in
         client.get(f"/api/reports/{events[-1][1]['run_id']}").json()["groups"]}
    assert g["yes"]["repeats"]["spread_max"] == 0.0 and g["topic"]["answered"] == 18
    assert fake_backend.loads == ["LiquidAI/d1-2B-MLX-8bit"]


def test_too_large_a_grid_is_refused_before_the_stream(client):
    r = client.post("/api/experiments", json=body(repeats=20, engines=["or-d1.1", "or-jev-1.14", "or-d1-latest"],
                                                  formulations={f"f{i}": EN for i in range(5)}))
    assert r.status_code == 400 and "at most 2000" in r.json()["detail"]
    big = body(repeats=20, engines=["or-d1.1", "or-jev-1.14", "or-d1-latest"], formulations={f"f{i}": EN for i in range(5)})
    assert client.post("/api/experiments/estimate", json=big).status_code == 400  # 6 × 6 × 20 × 3 = 2160
    big["engines"] = ["or-d1.1", "or-jev-1.14"]
    assert client.post("/api/experiments/estimate", json=big).json()["calls"] == 1440  # under the limit


@pytest.mark.parametrize("over,status", [
    ({"engines": ["nope"]}, 400),
    ({"engines": []}, 422),
    ({"repeats": 0}, 422),
    ({"formulations": {"en": {"topic": EN["topic"]}}}, 422),  # a wording without the noul question
    ({"orders": {"k": 1}}, 422),
])
def test_bad_experiments_are_refused_before_the_stream(client, over, status):
    assert client.post("/api/experiments", json=body(**over)).status_code == status


def test_an_experiment_over_the_budget_does_not_start(settings, upstream, local):
    settings.daily_budget_usd = 1e-9
    app = create_app(settings, transport=upstream.transport, refresh_catalog=False, local=local)
    with TestClient(app, base_url=UI) as c:
        with c.stream("POST", "/api/experiments", json=body()) as r:
            assert [n for n, _ in parse_sse("".join(r.iter_text()))] == ["budget_stop"]
    assert not upstream.calls


def test_the_budget_running_out_mid_experiment(client, upstream, monkeypatch):
    """The shared batch rules: after a $0.40 call the next holds $0.40; the day stops inside its $1."""
    upstream.default = lambda b: httpx.Response(200, json={**json.loads(echo_answers(b).content),
                                                           "usage": {"input_tokens": 100, "cost": 0.4}})
    monkeypatch.setattr(OpenRouterDecisions, "estimate_cost", lambda self, req: 0.000001)
    events = run(client, formulations={}, repeats=1, orders={"enabled": False})
    assert len(upstream.calls) == 1  # one cell only: the grid is 1 × 1 × 1
    events = run(client, formulations={}, repeats=4, orders={"enabled": False})
    assert events[-1][0] == "budget_stop" and events[-1][1]["run_id"]
    assert len(upstream.calls) == 1 + 1  # $0.40 spent earlier today: one more fits, the next would not
    assert client.get("/api/budget").json()["spent_usd"] == pytest.approx(0.8)


def test_a_zdr_off_engine_needs_consent_per_experiment(client, upstream):
    upstream.default = echo_answers
    events = run(client, engines=["or-no-zdr"], formulations={}, repeats=1, orders={"enabled": False})
    record = client.get(f"/api/runs/{events[-1][1]['run_id']}").json()
    assert record["calls"][0]["results"]["or-no-zdr"]["request_failure"] == "refused" and not upstream.calls
    run(client, engines=["or-no-zdr"], formulations={}, repeats=1, orders={"enabled": False},
        options={"allow_no_zdr": True})
    assert len(upstream.calls) == 1 and "zdr" not in upstream.calls[0]["body"]["provider"]


# --- review P5 ---------------------------------------------------------------------------------------------------------

def test_extraction_questions_do_not_break_the_report(client, fake_backend):
    """F1: text/number/date (here the shipped demo_extraction preset) with 2 repeats and a second wording."""
    import yaml

    preset = yaml.safe_load(open("presets/demo_extraction.yaml", encoding="utf-8"))
    qs = preset["questions"]
    events = run(client, request={"state": preset["state"], "questions": qs}, engines=["mlx-d1-2b"],
                 formulations={"en": qs}, repeats=2, orders={"enabled": False})
    assert events[-1][0] == "run_done" and events[-1][1]["report_error"] is None
    run_id = events[-1][1]["run_id"]
    rep = client.get(f"/api/reports/{run_id}").json()
    kinds = {g["question"]: g["metrics"] for g in rep["groups"]}
    # a number estimated over a range answers an interval and no probability: answered, but nothing to compare
    assert kinds["amount_estimate"]["answered"] == 0 and kinds["amount_estimate"]["unmeasured"] == 4
    assert all(m["answered"] == 4 for k, m in kinds.items() if k != "amount_estimate")
    extract = [m for k, m in kinds.items() if m["type"] in ("text", "number", "date") and k != "amount_estimate"]
    assert extract and all(m["options"] == ["found"] and m["repeats"]["top_flip_share"] is None for m in extract)
    assert client.get(f"/api/reports/{run_id}", params={"format": "md"}).status_code == 200


def test_a_formulation_is_asked_in_the_base_order(client, upstream):
    """F4: a wording typed with its options and questions in another order is sent in the base's order."""
    upstream.default = echo_answers
    en = {"yes": EN["yes"], "topic": {**EN["topic"], "criteria": {"complaint": "Complaint", "credit": "Credit",
                                                                    "deposit": "Deposit"}}}
    run(client, formulations={"en": en}, repeats=1, orders={"enabled": False})
    sent = [(list(c["body"]["questions"]), list(c["body"]["questions"]["topic"]["criteria"])) for c in upstream.calls]
    assert sent == [(["topic", "yes"], ["credit", "deposit", "complaint"])] * 2


def test_the_estimate_reads_each_cell_once_and_off_the_loop(client, monkeypatch):
    """F5: 2 wordings × 6 orders × 5 repeats = 60 calls, 12 distinct requests: 12 estimates, on a worker thread."""
    import asyncio

    seen, on_loop = [], []
    real = OpenRouterDecisions.estimate_cost

    def spy(self, req):
        seen.append(id(req))
        try:
            asyncio.get_running_loop()
            on_loop.append(True)
        except RuntimeError:
            on_loop.append(False)
        return real(self, req)

    monkeypatch.setattr(OpenRouterDecisions, "estimate_cost", spy)
    est = client.post("/api/experiments/estimate", json=body(repeats=5)).json()
    assert est["calls"] == 60 and len(seen) == 12 and not any(on_loop)


def test_the_grid_cap_is_checked_before_the_grid_is_built(client, monkeypatch):
    import app.experiments as ex

    def boom(exp):
        raise AssertionError("the plan was built for a grid over the cap")

    monkeypatch.setattr(ex, "plan", boom)
    r = client.post("/api/experiments", json=body(repeats=20, engines=["or-d1.1", "or-jev-1.14", "or-d1-latest"],
                                                  formulations={f"f{i}": EN for i in range(5)}))
    assert r.status_code == 400 and "2160 calls" in r.json()["detail"]


def test_exactly_2000_calls_is_allowed_and_the_limits_are_pinned(client):
    """F18: the cap and the repeats limit by literals, not by their own constants."""
    import app.experiments as ex

    assert ex.MAX_CALLS == 2000
    five = {"type": "choice", "instructions": "?", "criteria": {c: c for c in "abcde"}}
    exact = body(request={"state": STATE, "questions": {"topic": five}}, formulations={f"f{i}": {"topic": five} for i in range(4)},
                 orders={"k": 20}, repeats=20)  # 5 wordings × 20 orders × 20 repeats × 1 engine
    assert client.post("/api/experiments/estimate", json=exact).json()["calls"] == 2000
    assert client.post("/api/experiments/estimate", json=body(repeats=21)).status_code == 422
    assert client.post("/api/experiments/estimate", json=body(repeats=20)).status_code == 200


def test_the_media_goes_with_every_call(client, fake_backend, settings):
    img = settings.data_dir / "media" / "x.png"
    img.parent.mkdir(parents=True, exist_ok=True)
    img.write_bytes(b"\\x89PNG fake")
    req = {"state": STATE, "questions": {"yes": YES}, "media": [{"type": "image", "path": "x.png"}]}
    events = run(client, request=req, engines=["mlx-d1-2b"], formulations={}, repeats=3)
    assert events[-1][0] == "run_done" and len(fake_backend.calls) == 3
    assert all(c["media"] and c["media"][0]["type"] == "image" for c in fake_backend.calls)


def test_failed_calls_count_in_calls_not_in_answered(client, upstream):
    """S8: three of six calls fail: `calls` 6, `answered` 3."""
    n = []

    def half(b):
        n.append(1)
        return httpx.Response(500, json={"error": {"message": "down"}}) if len(n) % 2 else echo_answers(b)

    upstream.default = half
    events = run(client, formulations={}, repeats=6, orders={"enabled": False})
    g = {x["question"]: x["metrics"] for x in client.get(f"/api/reports/{events[-1][1]['run_id']}").json()["groups"]}
    assert g["yes"]["calls"] == 6 and 0 < g["yes"]["answered"] < 6


def test_the_estimate_leaves_out_questions_the_profile_refuses(client, settings):
    """B9 (unguarded since P4): a question the engine will not be asked costs nothing in the estimate."""
    import json as _json

    from app.schemas import DecisionRequest

    d = settings.probes_dir / "or-d1.1"
    d.mkdir(parents=True, exist_ok=True)
    (d / "latest.json").write_text(_json.dumps({"engine_id": "or-d1.1", "kind": "openrouter_decisions",
                                                "model": "liquid/d1.1", "types": ["noul"]}))
    est = client.post("/api/experiments/estimate", json=body(formulations={}, repeats=1,
                                                             orders={"enabled": False})).json()
    engine = client.app.state.registry.get("or-d1.1")
    only_yes = engine.estimate_cost(DecisionRequest(state=STATE, questions={"yes": YES}))
    assert est["per_engine"]["or-d1.1"] == pytest.approx(only_yes)


def test_a_budget_stop_thins_every_cell_instead_of_dropping_a_formulation(client, upstream, monkeypatch):
    """F8: repeat-major jobs: with money for two calls both wordings are asked once, not the base twice."""
    upstream.default = lambda b: httpx.Response(200, json={**json.loads(echo_answers(b).content),
                                                           "usage": {"input_tokens": 100, "cost": 0.4}})
    monkeypatch.setattr(OpenRouterDecisions, "estimate_cost", lambda self, req: 0.000001)
    events = run(client, repeats=4, orders={"enabled": False})
    record = client.get(f"/api/runs/{events[-1][1]['run_id']}").json()
    answered = [c["key"] for c in record["calls"] if c["results"]["or-d1.1"]["status"] != "failed"]
    assert answered == ["base|o0|r0", "en|o0|r0"]


def test_an_unsaved_experiment_is_charged_and_released(client, upstream, monkeypatch):
    """F6: the experiment's own money path — a failed save still counts what was spent."""
    upstream.default = echo_answers

    def broken(record):
        raise OSError("disk full")

    monkeypatch.setattr(client.app.state.store, "save", broken)
    events = run(client, formulations={}, repeats=2, orders={"enabled": False})
    assert events[-1][0] == "run_error"
    b = client.get("/api/budget").json()
    assert b["spent_usd"] == pytest.approx(2 * 0.000002) and b["reserved_usd"] == 0


def test_a_cancelled_experiment_charges_the_calls_in_flight(client, monkeypatch):
    import asyncio

    from app.experiments import ExperimentIn

    async def slow(self, req, opts):
        await asyncio.sleep(30)

    monkeypatch.setattr(OpenRouterDecisions, "run", slow)
    monkeypatch.setattr(OpenRouterDecisions, "estimate_cost", lambda self, req: 0.01)
    state = client.app.state
    exp = ExperimentIn.model_validate(body(formulations={}, repeats=2, orders={"enabled": False}))
    engines = state.runner.engines_for(exp.engines)

    async def go():
        async def emit(name, payload):
            pass

        task = asyncio.create_task(state.experiment_runner.execute(exp, engines, emit))
        await asyncio.sleep(0.1)  # the lane's first call is in flight
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(go())
    b = client.get("/api/budget").json()
    assert b["spent_usd"] == pytest.approx(0.01) and b["reserved_usd"] == 0


def test_a_report_that_cannot_be_built_still_ends_the_experiment(client, upstream, monkeypatch):
    import app.experiments as ex

    upstream.default = echo_answers

    def broken(record):
        raise ValueError("bad cell")

    monkeypatch.setattr(ex, "build_stability", broken)
    done = run(client, formulations={}, repeats=1, orders={"enabled": False})[-1]
    assert done[0] == "run_done" and "bad cell" in done[1]["report_error"] and done[1]["summary"] == []


def test_an_experiment_is_not_rerun_as_a_compare(client, upstream):
    """F12: the base request is not the experiment."""
    upstream.default = echo_answers
    run_id = run(client, formulations={}, repeats=1, orders={"enabled": False})[-1][1]["run_id"]
    assert client.post(f"/api/runs/{run_id}/rerun").status_code == 400


def test_the_report_kind_and_target(client, upstream):
    """F13: a target means nothing to an experiment; F14: `format=kind` answers from the index."""
    upstream.default = echo_answers
    run_id = run(client, formulations={}, repeats=1, orders={"enabled": False})[-1][1]["run_id"]
    assert client.get(f"/api/reports/{run_id}", params={"target": 0.3}).status_code == 200
    assert client.get(f"/api/reports/{run_id}", params={"format": "kind"}).json() == {"run_id": run_id,
                                                                                      "kind": "experiment"}
    assert client.get("/api/reports/nope", params={"format": "kind"}).status_code == 404
    rep = client.get(f"/api/reports/{run_id}").json()
    assert rep["request"]["state"] == STATE and rep["settings"]["engines"] == ["or-d1.1"]  # F15: what was asked


def test_a_batch_refuses_jobs_with_other_questions(client):
    """F19: `sent` comes from the first job; a job with other questions would fail inside the lanes."""
    from app.batch import Batch
    from app.schemas import DecisionRequest, RunOptions

    a = DecisionRequest(state="x", questions={"q": YES})
    b = DecisionRequest(state="x", questions={"q": TOPIC})
    state = client.app.state
    with pytest.raises(ValueError, match="other questions"):
        Batch(state.runner, state.budget, state.runner.engines_for(["or-d1.1"]), [("a", a), ("b", b)], RunOptions())
