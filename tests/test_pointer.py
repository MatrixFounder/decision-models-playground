"""The turn pointer (DESIGN §14, UC-8) by whip-insight's rules (SPEC-decision-service §8.2.4, decision-run.js):
turns from t0, whip-insight's own windows, the preferred answer, one pass up to B, two passes past B, the whole request
too_long past B², the money of the passes, and a pointer bug that must not sink billed answers (review P6)."""

from __future__ import annotations

import json
import math

import httpx
import pytest
from fastapi.testclient import TestClient

from app import pointer as ptr
from app import runner as runner_mod
from app.main import create_app
from app.runner import Runner, fold_usage, sent
from app.schemas import Answer, CapabilityProfile, DecisionRequest, EngineResult, Question, RunIn, Usage
from tests.conftest import UI, echo_answers, load, parse_sse

TRANSCRIPT = "Оператор: Здравствуйте, банк.\n\nКлиент: Хочу открыть вклад.\nОператор: Конечно, на какой срок?\n"
NOUL = {"type": "noul", "instructions": "Клиент хочет открыть вклад"}
WINDOWS = load("requests/whip_insight_windows.json")


# --- the rules -------------------------------------------------------------------------------------------------------
def test_turns_are_lines_numbered_from_zero_as_whip_insight_numbers_them():
    assert ptr.turns_of(TRANSCRIPT) == [
        {"turn": "t0", "participant": "Оператор", "text": "Здравствуйте, банк."},
        {"turn": "t1", "participant": "Клиент", "text": "Хочу открыть вклад."},
        {"turn": "t2", "participant": "Оператор", "text": "Конечно, на какой срок?"},
    ]
    # no label, a time is not a label (no space after the colon), a long prefix is not a name
    turns = ptr.turns_of("просто текст\nв 10:30 перезвоню\n" + "x" * 41 + ": y")
    assert [t["participant"] for t in turns] == [None, None, None]
    assert turns[1]["text"] == "в 10:30 перезвоню"


def test_a_line_is_split_on_newlines_only_and_a_bare_speaker_names_the_next_line():
    # U+2028, U+0085 and form feed are inside a line, not new turns (str.splitlines would split them)
    assert [t["text"] for t in ptr.turns_of("Клиент: да нет\x0cладно\u0085хорошо\r\nОператор: ок")] == [
        "да нет\x0cладно\u0085хорошо", "ок"]
    turns = ptr.turns_of("Оператор:\nЗдравствуйте.\n\nКлиент:\nДа.\nКлиент: И ещё.\nПока:")
    assert turns == [
        {"turn": "t0", "participant": "Оператор", "text": "Здравствуйте."},
        {"turn": "t1", "participant": "Клиент", "text": "Да."},
        {"turn": "t2", "participant": "Клиент", "text": "И ещё."},
        {"turn": "t3", "participant": None, "text": "Пока:"},  # a last bare label said nothing: a turn of its own
    ]


@pytest.mark.parametrize("bundle", ["BUNDLE2", "BUNDLE3C"])
def test_whip_insights_own_windows_are_taken_as_they_are(bundle):
    # built by whip-insight's detect(): turns from t0, two hits in one turn are two fragments of it
    w = WINDOWS[bundle]
    req = DecisionRequest(state=w["state"], questions={"q1": Question(type="noul", instructions=w["question"]["instructions"])})
    sent_req, turns = ptr.prepare(req)
    assert turns == w["pointer"]["options"] == ["t0", "t2"]
    assert sent_req.state == w["state"]  # the windows go as they are, repeats included
    assert ptr.instruction(req.questions["q1"], Answer(type="noul", probabilities={"true": 0.9, "false": 0.1})) == \
        w["pointer"]["instructions"]["true"]  # the route's own instruction, byte for byte


def test_out_of_order_fragments_give_the_routes_order():
    frags = [{"turn": "t12", "participant": None, "text": "b"}, {"turn": "t3", "participant": None, "text": "a"},
             {"turn": "t12", "participant": None, "text": "c"}]
    assert ptr.turn_ids(ptr.fragments_of({"fragments": frags})) == ["t3", "t12"]  # numeric, not text order


def test_a_fragments_state_is_checked():
    for bad in ({"fragments": []}, {"fragments": [{"turn": "12", "text": "x"}]}, {"fragments": [{"turn": "t01", "text": "x"}]},
                {"fragments": [{"turn": "t1", "text": " "}]}, {"fragments": [{"turn": "t1", "text": "x", "extra": 1}]},
                {"fragments": [{"turn": "t1", "text": "a", "participant": 7}]},
                {"fragments": "t1"}, {"other": []}, ["t1"], None, "", "\n \n"):
        with pytest.raises(ptr.PointerError):
            ptr.fragments_of(bad)


def test_prepare_sends_turns_and_refuses_what_it_cannot_point():
    req = DecisionRequest(state=TRANSCRIPT, questions={"q": Question(**NOUL)})
    sent_req, turns = ptr.prepare(req)
    assert turns == ["t0", "t1", "t2"] and sent_req.state["fragments"][1]["participant"] == "Клиент"
    with pytest.raises(ptr.PointerError, match="media"):
        ptr.prepare(DecisionRequest(state=TRANSCRIPT, questions={"q": Question(**NOUL)},
                                    media=[{"type": "audio", "path": "a.wav"}]))
    with pytest.raises(ptr.PointerError, match="no question"):
        ptr.prepare(DecisionRequest(state=TRANSCRIPT, questions={"d": Question(type="date", instructions="Когда?")}))


def test_the_preferred_answer_and_the_instruction():
    q = Question(**NOUL)
    yes = Answer(type="noul", probabilities={"true": 0.49951, "false": 0.50049})  # rounds to 0.5: «да», as the route
    assert ptr.instruction(q, yes) == "В какой реплике видно, что Клиент хочет открыть вклад?"
    no = Answer(type="noul", probabilities={"true": 0.4994, "false": 0.5006})
    assert ptr.instruction(q, no) is None
    # 0.4995 × 1000 is exactly 499.5: the route's Math.round gives 0.5, a «да»; Python's round(p, 3) gives 0.499
    assert round(0.4995, 3) < 0.5
    assert ptr.instruction(q, Answer(type="noul", probabilities={"true": 0.4995, "false": 0.5005})) is not None
    # the question as the route writes it: not stripped, «?» added only when it does not end with one
    asked = Question(type="noul", instructions="Клиент хочет вклад? ")
    assert ptr.instruction(asked, yes) == "В какой реплике видно, что Клиент хочет вклад? "
    spaced = Question(type="noul", instructions=" Клиент хочет вклад")
    assert ptr.instruction(spaced, yes) == "В какой реплике видно, что  Клиент хочет вклад?"
    c = Question(type="choice", instructions="Тема?", criteria={"deposit": "Клиент говорит о вкладе", "other": None})
    assert ptr.instruction(c, Answer(type="choice", top="deposit")) == "В какой реплике видно: Клиент говорит о вкладе?"
    assert ptr.instruction(c, Answer(type="choice", top="other")) == "В какой реплике видно: other?"  # null: the id
    s = Question(type="score", instructions="Интерес?", criteria=["нет интереса", "есть интерес?"])
    assert ptr.instruction(s, Answer(type="score", top="1")) == "В какой реплике видно: есть интерес?"
    m = Question(type="multi", instructions="Продукты?", criteria={"a": None, "b": None})
    assert ptr.instruction(m, Answer(type="multi", top="a")) is None


def route_blocks(options: list[str], b: int) -> list[list[str]]:
    """decision-run.js pointerOf, line for line: size = Math.ceil(N / B); for (i = 0; i < N; i += size) slice."""
    size = math.ceil(len(options) / b)
    return [options[i:i + size] for i in range(0, len(options), size)]


def test_b_blocks_and_passes_against_the_route():
    assert ptr.width(None) == 240 and ptr.width(255) == 240 and ptr.width(100) == 100
    assert [ptr.passes(n, 240) for n in (1, 2, 240, 241, 240 * 240, 240 * 240 + 1)] == [0, 1, 1, 2, 2, 0]
    turns = [f"t{i}" for i in range(481)]
    bl = ptr.blocks(turns, 240)  # ceil(481 / 240) = 3 turns a block
    assert len(bl) == 161 and bl[0] == ["t0", "t1", "t2"] and bl[-1] == ["t480"]
    # an exact multiple of B, and B² itself: the sizes the route makes (a size of n // B + 1 differs here)
    for n, b in ((480, 240), (240 * 240, 240), (6, 3), (9, 3), (4, 2)):
        t = [f"t{i}" for i in range(n)]
        assert ptr.blocks(t, b) == route_blocks(t, b)
    assert [len(x) for x in ptr.blocks([f"t{i}" for i in range(480)], 240)] == [2] * 240
    assert [len(x) for x in ptr.blocks([f"t{i}" for i in range(57600)], 240)] == [240] * 240
    assert all(len(ptr.blocks([f"t{i}" for i in range(n)], b)) <= b for n, b in ((5, 2), (241, 240), (57600, 240)))


def test_the_estimate_prices_the_widest_passes_with_the_longest_instruction():
    c = Question(type="choice", instructions="Тема?", criteria={"a": "коротко", "b": "очень длинное описание варианта"})
    turns = [f"t{i}" for i in range(7)]  # B = 3: blocks [t0 t1 t2] [t3 t4 t5] [t6]
    one, two = ptr.worst_passes({"fragments": []}, "q", c, turns, 3)
    assert list(one.questions["q"].criteria) == ["t0", "t3", "t6"]  # block heads
    assert list(two.questions["q"].criteria) == ["t0", "t1", "t2"]  # the widest block
    assert one.questions["q"].instructions == "В какой реплике видно: очень длинное описание варианта?"
    assert ptr.worst_passes({"fragments": []}, "q", c, ["t0"], 2) == []
    assert ptr.worst_passes({"fragments": []}, "q", c, turns[:5], 2) == []  # past B² = 4: no pass


def test_a_null_option_is_sized_as_it_is_sent():
    # `"t12": null` on the wire: the estimate counts the null, not an empty description (review P6)
    q = Question(type="choice", instructions="?", criteria={"t0": None, "t12": None})
    assert q.text_size() == 1 + (2 + 4) + (3 + 4)


def test_folded_usage_is_never_more_certain_than_its_parts():
    prov = Usage(input_tokens=100, output_tokens=0, cost_usd=0.00002, cost_source="provider")
    passes = [Usage(input_tokens=50, output_tokens=0, cost_usd=0.00001, cost_source="provider")]
    both = fold_usage(prov, passes)
    assert (both.input_tokens, both.output_tokens, both.cost_source) == (150, 0, "provider")
    assert both.cost_usd == pytest.approx(0.00003)
    unknown = Usage()  # the main call reported nothing
    got = fold_usage(unknown, passes)
    assert got.cost_source == "none" and got.input_tokens is None and got.cost_usd == pytest.approx(0.00001)
    priced = Usage(input_tokens=50, cost_usd=0.00001, cost_source="price")
    assert fold_usage(prov, [priced]).cost_source == "price"
    local = Usage(input_tokens=10, output_tokens=0, cost_usd=0.0, cost_source="none")
    assert fold_usage(local, [local]) == Usage(input_tokens=20, output_tokens=0, cost_usd=0.0, cost_source="none")


def test_a_call_refused_here_is_not_a_pass():
    keys = ["q"]
    assert not sent(EngineResult.build("e", keys, {}, {}, request_failure="too_long", detail="bytes"))
    assert not sent(EngineResult.build("e", keys, {}, {}, request_failure="refused", detail="no ZDR consent"))
    assert sent(EngineResult.build("e", keys, {}, {}, request_failure="too_long", http_status=422))
    assert sent(EngineResult.build("e", keys, {}, {}, request_failure="refused", http_status=400))
    assert sent(EngineResult.build("e", keys, {}, {}, request_failure="timed_out"))
    assert sent(EngineResult.build("e", keys, {"q": Answer(type="noul", probabilities={"true": 1, "false": 0})}, {}))
    assert not sent(EngineResult.build("e", keys, {}, {"q": "unsupported"}, detail="no question fits the engine's profile"))
    assert sent(EngineResult.build("e", keys, {}, {"q": "unsupported"}, http_status=400))  # the provider said so
    assert sent(EngineResult.build("e", keys, {}, {"q": "invalid_answer"}))


# --- runs ------------------------------------------------------------------------------------------------------------
@pytest.fixture
def client(settings, upstream, local):
    app = create_app(settings, transport=upstream.transport, refresh_catalog=False, local=local)
    with TestClient(app, base_url=UI) as c:
        yield c


def post(client, body):
    with client.stream("POST", "/api/runs", json=body) as r:
        assert r.status_code == 200, r.read()
        return parse_sse("".join(r.iter_text()))


def run_body(state=TRANSCRIPT, questions=None, engines=("or-d1.1",), **kw):
    return {"request": {"state": state, "questions": questions or {"q": NOUL}}, "engines": list(engines),
            "pointer": True, **kw}


def noul(p: float, cost: float = 0.000002, model: str = "liquid/d1.1-20261027"):
    return lambda body: httpx.Response(200, json={"model": model, "answers": {
        k: {"type": "noul", "noul": p} for k in body["questions"]}, "usage": {"input_tokens": 100, "cost": cost}})


def pick(turn: str, p: float, cost: float = 0.000003, model: str = "liquid/d1.1-20261027"):
    def answer(body):
        (k, q), = body["questions"].items()
        ids = list(q["criteria"])
        probs = {i: (p if i == turn else (1 - p) / (len(ids) - 1)) for i in ids}
        return httpx.Response(200, json={"model": model, "usage": {"input_tokens": 50, "cost": cost},
                                         "answers": {k: {"type": "choice", "choice": turn, "probabilities": probs}}})
    return answer


def narrow(client, monkeypatch, b: int, types=("noul", "choice", "score"), engine="or-d1.1"):
    e = client.app.state.registry.get(engine)
    monkeypatch.setattr(e, "profile", lambda: CapabilityProfile(types=list(types), max_choice_options=b))


def result_of(events, engine="or-d1.1"):
    return next(p["result"] for n, p in events if n in ("engine_done", "engine_failed") and p["engine_id"] == engine)


def test_a_yes_cites_a_turn_and_the_pass_is_paid(client, upstream):
    upstream.push("liquid/d1.1", noul(0.8), pick("t1", 0.9))
    events = post(client, run_body())
    assert events[-1][0] == "run_done"
    res = result_of(events)
    assert res["pointers"]["q"] == {"turn": "t1", "probability": pytest.approx(0.9), "passes": 1, "failure": None,
                                    "detail": None, "latency_ms": res["pointers"]["q"]["latency_ms"],
                                    "instruction": "В какой реплике видно, что Клиент хочет открыть вклад?",
                                    "cost_usd": pytest.approx(0.000003)}
    main, pass1 = (c["body"] for c in upstream.calls)
    frags = {"fragments": ptr.turns_of(TRANSCRIPT)}
    assert main["state"] == frags and pass1["state"] == frags  # the model sees the turns it may cite
    assert pass1["questions"] == {"q": {"type": "choice", "instructions": res["pointers"]["q"]["instruction"],
                                        "criteria": {"t0": None, "t1": None, "t2": None}}}
    assert res["usage"]["cost_usd"] == pytest.approx(0.000005) and res["usage"]["input_tokens"] == 150
    # the live screen gets the turns the engines read, so the cited turn's text shows while the run streams
    started = next(p for n, p in events if n == "run_started")
    assert started["request"]["state"] == frags
    record = client.get(f"/api/runs/{started['run_id']}").json()
    assert record["pointer"] is True and record["request"]["state"] == frags
    assert record["cost_usd"] == pytest.approx(0.000005)
    assert client.get("/api/budget").json()["spent_usd"] == pytest.approx(0.000005)


def test_a_run_without_the_pointer_does_not_echo_its_request(client, upstream):
    upstream.default = echo_answers
    events = post(client, {**run_body(), "pointer": False})
    assert "request" not in next(p for n, p in events if n == "run_started")


def test_a_no_asks_no_pointer(client, upstream):
    upstream.push("liquid/d1.1", noul(0.3))
    res = result_of(post(client, run_body()))
    assert res["pointers"] == {} and len(upstream.calls) == 1


def test_one_turn_is_cited_without_a_call(client, upstream):
    upstream.push("liquid/d1.1", noul(0.9))
    res = result_of(post(client, run_body(state="Клиент: хочу вклад")))
    assert res["pointers"]["q"]["turn"] == "t0" and res["pointers"]["q"]["probability"] is None
    assert res["pointers"]["q"]["passes"] == 0 and len(upstream.calls) == 1


def test_whip_insight_windows_run_with_the_routes_options(client, upstream):
    w = WINDOWS["BUNDLE3C"]  # t0 twice (two hits), then t2
    upstream.push("liquid/d1.1", noul(0.9), pick("t2", 0.7))
    q = {"q1": {"type": "noul", "instructions": w["question"]["instructions"]}}
    res = result_of(post(client, run_body(state=w["state"], questions=q)))
    assert res["pointers"]["q1"]["turn"] == "t2"
    assert upstream.calls[1]["body"]["questions"]["q1"]["criteria"] == {"t0": None, "t2": None}
    assert upstream.calls[0]["body"]["state"] == w["state"]


def test_past_b_two_passes_and_the_product(client, upstream, monkeypatch):
    narrow(client, monkeypatch, 3)  # B = 3: seven turns are blocks [t0 t1 t2] [t3 t4 t5] [t6]
    state = "\n".join(f"Клиент: реплика {i}" for i in range(7))
    upstream.push("liquid/d1.1", noul(0.9), pick("t3", 0.6), pick("t4", 0.5))
    res = result_of(post(client, run_body(state=state)))
    assert [list(c["body"]["questions"]["q"].get("criteria") or {}) for c in upstream.calls[1:]] == [
        ["t0", "t3", "t6"], ["t3", "t4", "t5"]]
    assert res["pointers"]["q"]["turn"] == "t4" and res["pointers"]["q"]["probability"] == pytest.approx(0.3)
    assert res["pointers"]["q"]["passes"] == 2 and res["pointers"]["q"]["cost_usd"] == pytest.approx(0.000006)


def test_exactly_b_squared_turns_take_two_passes(client, upstream, monkeypatch):
    narrow(client, monkeypatch, 2)  # four turns, B = 2: blocks [t0 t1] [t2 t3], not too_long
    state = "a: 0\nb: 1\nc: 2\nd: 3"
    upstream.push("liquid/d1.1", noul(0.9), pick("t2", 0.8), pick("t3", 0.6))
    res = result_of(post(client, run_body(state=state)))
    assert res["pointers"]["q"]["turn"] == "t3" and res["pointers"]["q"]["passes"] == 2
    assert [list(c["body"]["questions"]["q"]["criteria"]) for c in upstream.calls[1:]] == [["t0", "t2"], ["t2", "t3"]]


def test_a_block_of_one_turn_needs_no_second_pass(client, upstream, monkeypatch):
    narrow(client, monkeypatch, 2)  # three turns, B = 2: blocks [t0 t1] [t2]
    upstream.push("liquid/d1.1", noul(0.9), pick("t2", 0.7))
    res = result_of(post(client, run_body(state="a: 1\nb: 2\nc: 3")))
    assert res["pointers"]["q"]["turn"] == "t2" and res["pointers"]["q"]["probability"] == pytest.approx(0.7)
    assert res["pointers"]["q"]["passes"] == 1 and len(upstream.calls) == 2


def test_past_b_squared_the_whole_request_is_too_long_and_makes_no_call(client, upstream, monkeypatch):
    # the route refuses such a request at step 1 (decision-run.js): no main call either
    narrow(client, monkeypatch, 2)
    state = "\n".join(f"реплика {i}" for i in range(5))
    est = client.post("/api/estimate", json=run_body(state=state)).json()
    assert est["per_engine"]["or-d1.1"] == 0.0 and est["total_usd"] == 0.0
    events = post(client, run_body(state=state))
    res = result_of(events)
    assert res["status"] == "failed" and res["request_failure"] == "too_long" and "B² = 4" in res["detail"]
    assert not upstream.calls and events[-1][0] == "run_done"


def test_an_engine_without_choice_cannot_point(client, upstream, monkeypatch):
    narrow(client, monkeypatch, 240, types=("noul",))
    upstream.push("liquid/d1.1", noul(0.9))
    res = result_of(post(client, run_body()))
    assert res["pointers"]["q"]["failure"] == "unsupported" and len(upstream.calls) == 1


def test_a_failed_pass_fails_the_pointer_not_the_answer(client, upstream):
    upstream.push("liquid/d1.1", noul(0.9), httpx.Response(400, json={"error": {"message": "bad", "code": 400}}))
    events = post(client, run_body())
    res = result_of(events)
    assert res["status"] == "ok" and res["pointers"]["q"]["failure"] == "refused" and res["pointers"]["q"]["passes"] == 1
    assert events[-1][0] == "run_done"


def test_a_pass_refused_before_sending_is_not_counted(client, upstream, monkeypatch):
    real = Runner.call

    async def call(self, engine, req, opts):
        if "fragments" in json.dumps(req.state) and list(req.questions.values())[0].type == "choice":
            return EngineResult.build(engine.spec.id, list(req.questions), {}, {}, request_failure="too_long",
                                      detail="33258 bytes > max_request_bytes 32000")
        return await real(self, engine, req, opts)

    monkeypatch.setattr(Runner, "call", call)
    upstream.push("liquid/d1.1", noul(0.9))
    res = result_of(post(client, run_body()))
    assert res["pointers"]["q"]["failure"] == "too_long" and res["pointers"]["q"]["passes"] == 0
    assert len(upstream.calls) == 1


def test_a_choice_points_at_its_top_option(client, upstream):
    upstream.push("liquid/d1.1", echo_answers, pick("t0", 0.8))
    qs = {"topic": {"type": "choice", "instructions": "Тема?", "criteria": {"deposit": "Клиент говорит о вкладе", "credit": None}}}
    res = result_of(post(client, run_body(questions=qs)))
    assert res["pointers"]["topic"]["instruction"] == "В какой реплике видно: Клиент говорит о вкладе?"
    assert res["pointers"]["topic"]["turn"] == "t0"


def main_cost(client, body) -> float:
    """The main call's estimate on the request a pointer run sends (the fragments state)."""
    runner = client.app.state.runner
    req, _ = runner.prepared(RunIn.model_validate(body))
    e = client.app.state.registry.get("or-d1.1")
    keys, _ = runner.split(e, req)
    return e.estimate_cost(req.subset(keys))


def test_the_estimate_is_the_main_call_plus_the_passes(client, upstream, monkeypatch):
    body = run_body()
    est = client.post("/api/estimate", json=body).json()
    passes = est["pointer"]["per_engine"]["or-d1.1"]
    assert passes > 0 and est["pointer"]["turns"] == 3
    assert est["per_engine"]["or-d1.1"] == pytest.approx(main_cost(client, body) + passes, rel=1e-12)
    assert est["total_usd"] == pytest.approx(est["per_engine"]["or-d1.1"], rel=1e-12)
    narrow(client, monkeypatch, 3)
    two = client.post("/api/estimate", json=run_body(state="\n".join(f"Клиент: реплика {i}" for i in range(7)))).json()
    one = client.post("/api/estimate", json=run_body(state="a: 1\nb: 2")).json()
    assert two["pointer"]["per_engine"]["or-d1.1"] > one["pointer"]["per_engine"]["or-d1.1"]


def test_the_reserve_stops_a_run_the_passes_would_push_over_the_day(settings, upstream, local):
    app = create_app(settings, transport=upstream.transport, refresh_catalog=False, local=local)
    with TestClient(app, base_url=UI) as c:
        est = c.post("/api/estimate", json=run_body()).json()
        main = main_cost(c, run_body())
    passes = est["pointer"]["per_engine"]["or-d1.1"]
    settings.daily_budget_usd = main + passes / 2  # the main call fits the day, main plus passes does not
    app = create_app(settings, transport=upstream.transport, refresh_catalog=False, local=local)
    with TestClient(app, base_url=UI) as c:
        events = post(c, run_body())
        assert c.get("/api/budget").json()["reserved_usd"] == 0
    assert [n for n, _ in events] == ["budget_stop"] and not upstream.calls


def test_a_pointer_bug_after_a_billed_pass_keeps_the_answers_and_the_money(client, upstream, monkeypatch):
    def broken(r):
        raise TypeError("planted")

    monkeypatch.setattr(runner_mod, "sent", broken)  # raises after the pass was made and billed
    upstream.push("liquid/d1.1", noul(0.9), pick("t1", 0.9))
    events = post(client, run_body())
    assert events[-1][0] == "run_done"
    res = result_of(events)
    assert res["answers"]["q"]["top"] == "true"
    assert res["pointers"]["q"]["failure"] == "internal" and "TypeError" in res["pointers"]["q"]["detail"]
    assert res["usage"]["cost_usd"] == pytest.approx(0.000005)  # the main call and the billed pass
    assert client.get("/api/budget").json()["spent_usd"] == pytest.approx(0.000005)


def test_an_engine_that_breaks_waits_for_the_others_and_their_money_counts(client, upstream, monkeypatch):
    real = Runner.point

    async def point(self, engine, *a, **kw):
        if engine.spec.id == "or-jev-1.14":
            raise RuntimeError("planted")
        return await real(self, engine, *a, **kw)

    monkeypatch.setattr(Runner, "point", point)
    upstream.push("typesafe/jev-1.14", noul(0.9, model="typesafe/jev-1.14-20261014"))
    upstream.push("liquid/d1.1", noul(0.9), pick("t1", 0.9))
    events = post(client, run_body(engines=("or-jev-1.14", "or-d1.1")))
    names = [n for n, _ in events]
    assert names[-1] == "run_error"
    # d1.1 finished, pass included, before the run ended: no call goes on after the terminal event
    assert names.index("engine_done") < names.index("run_error") and len(upstream.calls) == 3
    budget = client.get("/api/budget").json()
    # every billed call counts: d1.1's main call and pass, and Jev's main call, made before its engine broke
    assert budget["reserved_usd"] == 0 and budget["spent_usd"] == pytest.approx(0.000002 + 0.000005)


def test_engines_that_all_break_after_billed_calls_still_count(client, upstream, monkeypatch):
    async def point(self, *a, **kw):  # every engine breaks after its billed main call (the reviewer's probe)
        raise RuntimeError("planted")

    monkeypatch.setattr(Runner, "point", point)
    upstream.push("typesafe/jev-1.14", noul(0.9, cost=0.001, model="typesafe/jev-1.14-20261014"))
    upstream.push("liquid/d1.1", noul(0.9, cost=0.002))
    events = post(client, run_body(engines=("or-jev-1.14", "or-d1.1")))
    assert events[-1][0] == "run_error" and len(upstream.calls) == 2
    budget = client.get("/api/budget").json()
    assert budget["reserved_usd"] == 0 and budget["spent_usd"] == pytest.approx(0.003)


def test_a_cancelled_run_counts_what_was_billed(client, upstream):
    import asyncio

    from app.runner import Runner as R

    runner: R = client.app.state.runner
    seen = []

    async def emit(name, payload):
        seen.append(name)

    async def go():
        upstream.push("liquid/d1.1", noul(0.9, cost=0.004))
        orig = runner.point

        async def slow_point(*a, **kw):  # the main call is billed, then the run is cancelled mid-pointer
            await asyncio.sleep(10)
            return await orig(*a, **kw)

        runner.point = slow_point
        try:
            task = asyncio.create_task(runner.execute(RunIn.model_validate(run_body()), emit))
            while len(upstream.calls) < 1:
                await asyncio.sleep(0.01)
            await asyncio.sleep(0.05)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        finally:
            del runner.point

    client.portal.call(go)
    budget = client.get("/api/budget").json()
    assert budget["reserved_usd"] == 0 and budget["spent_usd"] == pytest.approx(0.004)


def test_the_passes_read_the_profile_the_estimate_read(client, upstream, monkeypatch):
    e = client.app.state.registry.get("or-d1.1")
    seen = []

    def profile():  # a probe narrows the engine while the run is under way
        seen.append(1)
        b = 240 if len(seen) == 1 else 2
        return CapabilityProfile(types=["noul", "choice", "score"], max_choice_options=b)

    monkeypatch.setattr(e, "profile", profile)
    upstream.push("liquid/d1.1", noul(0.9), pick("t4", 0.9))
    events = post(client, run_body(state="\n".join(f"Клиент: {i}" for i in range(5))))
    res = result_of(events)
    # one snapshot: B = 240, one pass over five turns, priced. A fresh read (B = 2) would plan two unpriced passes
    # over blocks; instead the planned pass meets the engine's live profile, which refuses it before sending
    assert next(p for n, p in events if n == "run_started")["estimate_usd"] > 0
    assert res["pointers"]["q"]["failure"] == "unsupported" and res["pointers"]["q"]["passes"] == 0
    assert len(upstream.calls) == 1  # nothing beyond what the estimate priced


@pytest.mark.parametrize("change,match", [
    ({"request": {"state": TRANSCRIPT, "questions": {"q": NOUL}, "media": [{"type": "image", "path": "x.png"}]}}, "media"),
    ({"request": {"state": TRANSCRIPT, "questions": {"d": {"type": "date", "instructions": "Когда?"}}}}, "no question"),
    ({"request": {"state": {"turns": []}, "questions": {"q": NOUL}}}, "fragments"),
    ({"request": {"state": " \n ", "questions": {"q": NOUL}}}, "no turns"),
])
def test_a_run_the_pointer_cannot_serve_is_400_before_any_call(client, upstream, change, match):
    for path in ("/api/runs", "/api/estimate"):
        r = client.post(path, json={**run_body(), **change})
        assert r.status_code == 400 and match in r.json()["detail"]
    assert not upstream.calls


def test_a_rerun_points_again_on_the_same_turns(client, upstream):
    upstream.push("liquid/d1.1", noul(0.8), pick("t1", 0.9), noul(0.8), pick("t1", 0.9))
    first = post(client, run_body())
    with client.stream("POST", f"/api/runs/{first[0][1]['run_id']}/rerun") as r:
        again = parse_sse("".join(r.iter_text()))
    assert again[-1][0] == "run_done"
    bodies = [json.dumps(c["body"], sort_keys=True) for c in upstream.calls]
    assert len(bodies) == 4 and bodies[:2] == bodies[2:]
