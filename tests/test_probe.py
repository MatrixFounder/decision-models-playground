"""§13 the capability probe and the matrix — the same set and rule of evidence as whip-insight §5.3."""

from __future__ import annotations

import asyncio
import json
import logging

import httpx
import pytest
from fastapi.testclient import TestClient

import app.probe as probe_mod
from app.catalog import Catalog
from app.engines.openrouter_decisions import OpenRouterDecisions
from app.main import create_app
from app.probe import (CATALOG_TYPES, NOT_SERVED, SERVED, UNOBSERVED, ProbeStore, got_2xx, read, size_limit,
                       valid_engine_id)
from app.schemas import Answer, EngineResult
from tests.conftest import D1_OMNI, UI, echo_answers, load, parse_sse

CORE = load("requests/core_ru.json")
COST = 0.000002  # echo_answers' usage.cost


def result(**kw) -> EngineResult:
    return EngineResult(engine_id="e", status="ok", **kw)


@pytest.mark.parametrize("res,reading", [
    (result(answers={"q": Answer(type="noul", probabilities={"true": 0.9, "false": 0.1})}), SERVED),
    (result(question_failures={"q": "refused"}), SERVED),  # a refusal answer proves the type is understood
    (result(question_failures={"q": "unsupported"}), NOT_SERVED),
    (result(question_failures={"q": "invalid_answer"}), NOT_SERVED),
    (result(request_failure="refused", http_status=400), NOT_SERVED),
    (result(request_failure="refused", http_status=422), NOT_SERVED),
    (result(request_failure="refused", http_status=401), UNOBSERVED),
    (result(request_failure="refused", http_status=404), UNOBSERVED),
    (result(request_failure="timed_out"), UNOBSERVED),
    (result(request_failure="rate_limited"), UNOBSERVED),
    (result(request_failure="too_long"), UNOBSERVED),
    (result(request_failure="unreachable"), UNOBSERVED),
    (result(request_failure="skipped"), UNOBSERVED),
])
def test_reading_of_one_request(res, reading):
    assert read(res) == reading


@pytest.mark.parametrize("res,two", [
    (result(answers={"q": Answer(type="noul", probabilities={"true": 0.9, "false": 0.1})}), True),
    (result(question_failures={"q": "refused"}), True),
    (result(question_failures={"q": "invalid_answer"}), True),
    (result(question_failures={"q": "unsupported"}), False),  # from a 400 naming the type
    (result(request_failure="refused", http_status=400), False),
    (result(request_failure="provider_error", http_status=200), True),  # a 200 whose body was not JSON
    (result(request_failure="timed_out"), False),
])
def test_what_counts_as_a_2xx_answer(res, two):
    assert got_2xx(res) is two


def test_size_rule_examples_from_whip_insight():
    # "C240 timed out (unobserved), C100 answered, the stored limit is 240: the limit stays 240."
    readings = {3: SERVED, 24: SERVED, 100: SERVED, 240: UNOBSERVED}
    assert size_limit(readings, 240) == 240
    # "The same probe with no stored limit gives 100."
    assert size_limit(readings, None) == 100
    # "C100 answered a 400 that does not name the type: the limit is 24, and 240 is not supported either."
    assert size_limit({3: SERVED, 24: SERVED, 100: NOT_SERVED, 240: SERVED}, 240) == 24


@pytest.fixture
def client(settings, upstream, local):
    app = create_app(settings, transport=upstream.transport, refresh_catalog=False, local=local)
    with TestClient(app, base_url=UI) as c:
        yield c


def probe(client, engine_id, long_context=False, **extra):
    body = {"engine_id": engine_id, "long_context": long_context, **extra}
    with client.stream("POST", "/api/probe", json=body) as r:
        assert r.status_code == 200, r.read()
        return parse_sse("".join(r.iter_text()))


def run_once(client, request: dict, engines: list[str]) -> dict:
    with client.stream("POST", "/api/runs", json={"request": request, "engines": engines}) as r:
        events = parse_sse("".join(r.iter_text()))
    return [p for n, p in events if n in ("engine_done", "engine_failed")][0]["result"]


def budget(client) -> dict:
    return client.get("/api/budget").json()


def test_cloud_probe_writes_a_profile_the_runner_uses(client, upstream, settings):
    upstream.default = echo_answers
    events = probe(client, "or-d1.1")
    names = [n for n, _ in events]
    assert names[0] == "probe_started" and names[-1] == "probe_done" and names.count("step_done") == 7
    profile = events[-1][1]["profile"]
    assert profile["types"] == ["noul", "choice", "score"] and profile["not_served"] == []
    assert profile["max_choice_options"] == 240 and profile["max_score_levels"] == 10
    assert profile["reports_cost"] is True and profile["reports_snapshot"] is True
    assert profile["snapshot"] == "liquid/d1.1-20261027" and profile["reads_full_context"] is None
    saved = json.loads((settings.probes_dir / "or-d1.1" / "latest.json").read_text())
    assert saved["types"] == profile["types"]
    assert (settings.probes_dir / "or-d1.1" / "liquid_d1.1-20261027.json").is_file()
    assert not list((settings.probes_dir / "or-d1.1").glob("*.tmp"))
    # every probe call carried ZDR and none sent multi
    assert all(c["body"]["provider"]["zdr"] is True for c in upstream.calls)
    # the run is in the history as a probe — one status entry, no duplicate tag — and its cost counts
    row = client.get("/api/runs").json()["runs"][0]
    assert row["kind"] == "probe" and row["cost_usd"] == pytest.approx(7 * COST)
    assert row["status"] == {"or-d1.1": "ok"} and row["tags"] == []
    assert budget(client)["spent_usd"] == pytest.approx(7 * COST)
    assert engine_profile(client, "or-d1.1")["source"] == "probe"
    record = client.get(f"/api/runs/{row['id']}").json()
    assert record["versions"]["models"] == {"or-d1.1": "liquid/d1.1-20261027"} and "mlx_vlm" in record["versions"]


def test_a_narrower_profile_narrows_the_runs(client, upstream):
    """After a green probe (stored limit 240), C100 and C240 answer a 400 that names no type: not served, so the
    limit is 24. Read as unobserved (no http_status through the engine or the runner), the stored 240 would stand."""
    upstream.default = echo_answers
    assert probe(client, "or-d1.1")[-1][1]["profile"]["max_choice_options"] == 240

    def handler(body):
        n = len(next(iter(body["questions"].values())).get("criteria") or {})
        if n >= 100:
            return httpx.Response(400, json={"error": {"message": "Invalid request parameters", "code": 400}})
        return echo_answers(body)

    upstream.default = handler
    profile = probe(client, "or-d1.1")[-1][1]["profile"]
    assert profile["observed"]["C100"]["http_status"] == 400 and profile["max_choice_options"] == 24
    q30 = {"type": "choice", "instructions": "?", "criteria": {f"o{i}": None for i in range(30)}}
    res = run_once(client, {"state": "x", "questions": {"big": q30, "yes": {"type": "noul", "instructions": "?"}}},
                   ["or-d1.1"])
    assert res["question_failures"] == {"big": "unsupported"} and "yes" in res["answers"]


def test_a_type_the_provider_refuses_leaves_the_profile(client, upstream):
    def handler(body):
        q = next(iter(body["questions"].values()))
        if q["type"] == "score":
            issues = [{"code": "invalid_union", "path": ["questions", "q", "type"], "message": "no score"}]
            return httpx.Response(400, json={"error": {"message": json.dumps(issues), "code": 400}})
        return echo_answers(body)

    upstream.default = handler
    profile = probe(client, "or-d1.1")[-1][1]["profile"]
    assert profile["types"] == ["noul", "choice"] and profile["max_score_levels"] is None
    row = {r["id"]: r for r in client.get("/api/matrix").json()["rows"]}["or-d1.1"]
    assert row["cells"]["score"] == "no" and row["cells"]["multi"] == "never"


# --- no_profile and the stored value (whip-insight §5.3) -------------------------------------------------------------

def test_no_2xx_answer_writes_no_profile(client, upstream, settings):
    upstream.default = echo_answers
    probe(client, "or-d1.1")
    upstream.default = lambda body: httpx.Response(401, json={"error": {"message": "User not found.", "code": 401}})
    done = probe(client, "or-d1.1")[-1][1]
    assert done["no_profile"] is True and done["failure"] == "refused"  # whip-insight: with the dominant class
    saved = json.loads((settings.probes_dir / "or-d1.1" / "latest.json").read_text())
    assert saved["max_choice_options"] == 240  # the earlier profile stands
    assert client.get("/api/runs").json()["runs"][0]["status"] == {"or-d1.1": "failed"}


def test_unreadable_200_answers_write_an_empty_profile(client, upstream, settings):
    """E2: no_profile only when no request got a 2xx. Answers that cannot be read are 2xx and not served: a model
    that answers in another schema ends with `types` empty (SPEC §5.6.5), not with the old profile."""
    upstream.default = echo_answers
    probe(client, "or-d1.1")
    upstream.default = lambda body: httpx.Response(200, json={
        "model": "liquid/d1.1-20261027", "answers": {"q": {"type": "predicate", "value": True}},
        "usage": {"input_tokens": 100, "cost": COST}})
    done = probe(client, "or-d1.1")[-1][1]
    assert done["no_profile"] is False and done["profile"]["types"] == []
    assert done["profile"]["not_served"] == ["noul", "choice", "score"]
    assert json.loads((settings.probes_dir / "or-d1.1" / "latest.json").read_text())["types"] == []
    assert engine_profile(client, "or-d1.1")["types"] == []


def test_a_400_on_every_step_is_no_profile(client, upstream):
    upstream.default = lambda body: httpx.Response(400, json={"error": {"message": "Invalid request", "code": 400}})
    done = probe(client, "or-d1.1")[-1][1]
    assert done["no_profile"] is True and done["failure"] == "refused"


def test_a_types_own_request_unobserved_keeps_the_stored_type(client, upstream):
    """whip-insight: "A type's own request (N, C, S) follows rows 1 to 3: unobserved keeps the stored value"."""
    upstream.default = echo_answers
    probe(client, "or-d1.1")

    def handler(body):
        q = next(iter(body["questions"].values()))
        if q["type"] == "choice" and len(q["criteria"]) == 3:
            return httpx.Response(408, json={"error": {"message": "timeout", "code": 408}})
        return echo_answers(body)

    upstream.default = handler
    profile = probe(client, "or-d1.1")[-1][1]["profile"]
    assert profile["observed"]["C"]["reading"] == "unobserved"
    assert "choice" in profile["types"] and profile["max_choice_options"] == 240


def test_whip_insight_example_4_n_answered_c_401(client, upstream):
    """"A set with N answered and C refused with 401 keeps the stored choice; the write goes ahead because one
    request got a 2xx." (decision-service.md §5.3)"""
    upstream.default = echo_answers
    first = probe(client, "or-d1.1")[-1][1]

    def handler(body):
        if next(iter(body["questions"].values()))["type"] == "noul":
            return echo_answers(body)
        return httpx.Response(401, json={"error": {"message": "User not found.", "code": 401}})

    upstream.default = handler
    done = probe(client, "or-d1.1")[-1][1]
    assert done["no_profile"] is False and done["run_id"] != first["run_id"]
    assert done["profile"]["types"] == ["noul", "choice", "score"]
    assert done["profile"]["max_choice_options"] == 240 and done["profile"]["max_score_levels"] == 10


def test_the_matrix_keeps_a_stored_no(client, upstream):
    """E7: a probe reads score as not served (✗); the next one times out on S. Score stays out of the profile, and
    the cell stays ✗, not '?' — the matrix agrees with the profile the runner applies."""
    def named_400(body):
        if next(iter(body["questions"].values()))["type"] == "score":
            issues = [{"code": "invalid_union", "path": ["questions", "q", "type"], "message": "no score"}]
            return httpx.Response(400, json={"error": {"message": json.dumps(issues), "code": 400}})
        return echo_answers(body)

    def score_times_out(body):
        if next(iter(body["questions"].values()))["type"] == "score":
            return httpx.Response(408, json={"error": {"message": "timeout", "code": 408}})
        return echo_answers(body)

    upstream.default = named_400
    probe(client, "or-d1.1")
    upstream.default = score_times_out
    profile = probe(client, "or-d1.1")[-1][1]["profile"]
    assert "score" not in profile["types"] and profile["not_served"] == ["score"]
    row = {r["id"]: r for r in client.get("/api/matrix").json()["rows"]}["or-d1.1"]
    assert row["cells"]["score"] == "no" and row["cells"]["noul"] == "yes"


def test_reports_cost_counts_refusal_answers(client, upstream):
    """Every answer — a refusal answer too — must carry usage.cost for `reports_cost` (whip-insight §5.3 item 4)."""
    def handler(body):
        if next(iter(body["questions"].values()))["type"] == "score":
            return httpx.Response(200, json={"model": "liquid/d1.1-20261027", "answers": {"q": {"type": "refusal"}},
                                             "usage": {"input_tokens": 100}})
        return echo_answers(body)

    upstream.default = handler
    profile = probe(client, "or-d1.1")[-1][1]["profile"]
    assert "score" in profile["types"] and profile["reports_cost"] is False


def test_the_snapshot_falls_back_to_the_config_model(client, upstream, settings):
    upstream.default = lambda body: echo_answers(body, model="liquid/d1.1")  # undated
    profile = probe(client, "or-d1-latest")[-1][1]["profile"]
    assert profile["snapshot"] == "~liquid/d1-latest" and profile["reports_snapshot"] is False
    assert (settings.probes_dir / "or-d1-latest" / "_liquid_d1-latest.json").is_file()


def test_too_long_on_a_short_step_is_logged(client, upstream, caplog):
    def handler(body):
        if len(next(iter(body["questions"].values())).get("criteria") or {}) == 240:
            return httpx.Response(413, json={"error": {"message": "too long", "code": 413}})
        return echo_answers(body)

    upstream.default = handler
    with caplog.at_level(logging.WARNING, logger="playground.probe"):
        probe(client, "or-d1.1")
    assert any("C240 is too long" in r.getMessage() for r in caplog.records)


# --- the overlay: a probe confirms or narrows the profile, never widens it -------------------------------------------

def plant(settings, engine_id: str, profile: dict) -> None:
    d = settings.probes_dir / engine_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "latest.json").write_text(json.dumps({"engine_id": engine_id, **profile}), encoding="utf-8")


def engine_profile(client, engine_id: str) -> dict:
    return {e["id"]: e for e in client.get("/api/engines").json()["engines"]}[engine_id]["profile"]


CLOUD = {"kind": "openrouter_decisions", "model": "liquid/d1.1"}
OMNI = {"kind": "mlx_local", "model": D1_OMNI}


@pytest.mark.parametrize("engine_id,probed,expected", [
    # a type outside the kind's ceiling never enters (OpenRouter drops `multi` silently)
    ("or-d1.1", {**CLOUD, "types": ["noul", "choice", "score", "multi"]}, {"types": ["noul", "choice", "score"]}),
    # limits above the ceiling are capped by it
    ("or-d1.1", {**CLOUD, "types": ["choice", "score"], "max_choice_options": 300, "max_score_levels": 12},
     {"max_choice_options": 255, "max_score_levels": 10}),
    # a narrower limit narrows
    ("or-d1.1", {**CLOUD, "types": ["choice"], "max_choice_options": 24, "max_choice_probed": 240},
     {"types": ["choice"], "max_choice_options": 24}),
    # the largest size tried and served means "at least": the ceiling stands (P0: 255 options served)
    ("or-d1.1", {**CLOUD, "types": ["choice"], "max_choice_options": 240, "max_choice_probed": 240},
     {"max_choice_options": 255}),
    # audio only when the probe heard it; pictures as the kind allows
    ("mlx-d1-omni", {**OMNI, "types": ["noul"], "media": []}, {"media": ["image"]}),
    ("mlx-d1-omni", {**OMNI, "types": ["noul"], "media": ["audio"]}, {"media": ["image", "audio"]}),
    ("mlx-d1-2b", {"kind": "mlx_local", "model": "LiquidAI/d1-2B-MLX-8bit", "types": ["noul"], "media": ["audio"]},
     {"media": ["image"]}),
])
def test_the_overlay_never_widens(client, settings, engine_id, probed, expected):
    plant(settings, engine_id, probed)
    profile = engine_profile(client, engine_id)
    assert profile["source"] == "probe"
    for k, v in expected.items():
        assert profile[k] == v, k


def test_the_largest_probed_size_keeps_the_ceiling(client, upstream):
    """E3: after a green probe a 250-option choice is still sent; the stored profile keeps whip-insight's 240."""
    upstream.default = echo_answers
    profile = probe(client, "or-d1.1")[-1][1]["profile"]
    assert profile["max_choice_options"] == 240 and profile["max_choice_probed"] == 240
    assert engine_profile(client, "or-d1.1")["max_choice_options"] == 255
    q250 = {"type": "choice", "instructions": "?", "criteria": {f"o{i}": None for i in range(250)}}
    assert "big" in run_once(client, {"state": "x", "questions": {"big": q250}}, ["or-d1.1"])["answers"]
    row = {r["id"]: r for r in client.get("/api/matrix").json()["rows"]}["or-d1.1"]
    assert row["max_choice_at_top"] is True and row["max_score_at_top"] is True


def test_a_foreign_latest_json_is_ignored(client, settings, upstream):
    """E4: a profile made on another model (config.yaml changed) narrows nothing and shows as stale."""
    plant(settings, "or-d1.1", {"kind": "mlx_local", "model": "other/model", "types": ["noul"]})
    assert engine_profile(client, "or-d1.1")["source"] == "default"
    upstream.default = echo_answers
    q = {"type": "choice", "instructions": "?", "criteria": {"a": None, "b": None}}
    assert "c" in run_once(client, {"state": "x", "questions": {"c": q}}, ["or-d1.1"])["answers"]
    row = {r["id"]: r for r in client.get("/api/matrix").json()["rows"]}["or-d1.1"]
    assert "other/model" in row["stale"] and row["cells"]["noul"] == "unchecked"
    # the next probe starts from nothing stored: the foreign file seeds no "unobserved keeps the stored value"
    upstream.default = lambda body: httpx.Response(401, json={"error": {"message": "no", "code": 401}})
    assert probe(client, "or-d1.1")[-1][1]["no_profile"] is True


@pytest.mark.parametrize("content", [
    ["noul"],
    {"engine_id": "or-d1.1", **CLOUD, "types": "noul"},
    {"engine_id": "or-d1.1", **CLOUD, "types": ["noul"], "max_choice_options": "many"},
    {"engine_id": "or-d1.1", **CLOUD, "types": ["noul"], "observed": ["N"]},
])
def test_a_malformed_latest_json_falls_back_to_the_default(client, settings, content):
    """E5: valid JSON of another shape is no profile — the engine list still answers."""
    d = settings.probes_dir / "or-d1.1"
    d.mkdir(parents=True, exist_ok=True)
    (d / "latest.json").write_text(json.dumps(content), encoding="utf-8")
    r = client.get("/api/engines")
    assert r.status_code == 200
    assert {e["id"]: e for e in r.json()["engines"]}["or-d1.1"]["profile"]["source"] == "default"
    assert client.get("/api/matrix").status_code == 200


def test_audio_outside_the_profile_is_not_sent(client, settings, fake_backend):
    fake_backend.downloaded.add(D1_OMNI)
    plant(settings, "mlx-d1-omni", {**OMNI, "types": ["noul"], "media": []})
    clip = settings.data_dir / "media" / "probe" / "x.wav"  # a real file: only the profile can stop the call
    clip.parent.mkdir(parents=True, exist_ok=True)
    clip.write_bytes(probe_mod.AUDIO_CLIP.read_bytes())
    req = {"state": None, "media": [{"type": "audio", "path": "probe/x.wav"}],
           "questions": {"q": {"type": "noul", "instructions": "?"}}}
    res = run_once(client, req, ["mlx-d1-omni"])
    assert res["request_failure"] == "refused" and "audio" in res["detail"]
    assert not fake_backend.calls


# --- the long-context step (SPEC §5.6.2 item 2.4) --------------------------------------------------------------------

def sized(ratio: float = 0.6, overhead: int = 25, model: str = "liquid/d1.1-20261027", long_p: float = 0.95,
          long_tokens: int | None = None, sizes: list | None = None, first_long: httpx.Response | None = None):
    """A System One answer whose input_tokens grow with the request (overhead + ratio × characters), so the probe
    can fit them. The long step's question gets `long_p`; `first_long` answers the first long request. At the default
    0.6 tokens a character, 90 % of d1.1's 65,536 tokens is ~175 KB: under OpenRouter's 250,000 bytes of text."""
    pending = [first_long] if first_long is not None else []

    def handler(body):
        q = next(iter(body["questions"].values()))
        chars = len(body["state"] or "") + len(json.dumps(body["questions"], ensure_ascii=False))
        tokens = int(overhead + ratio * chars)
        is_long = "кодовое слово" in q.get("instructions", "")
        if is_long and sizes is not None:
            sizes.append({"bytes": len(json.dumps(body, ensure_ascii=False).encode()), "chars": len(body["state"]),
                          "tokens": tokens})
        if is_long and pending:
            return pending.pop()
        data = json.loads(echo_answers(body, model=model).content)
        data["usage"]["input_tokens"] = long_tokens if is_long and long_tokens else tokens
        if is_long:
            data["answers"]["q"]["noul"] = long_p
        return httpx.Response(200, json=data)

    return handler


def test_the_long_step_fills_about_90_percent_of_the_context(client, upstream):
    sizes = []
    upstream.default = sized(sizes=sizes)
    events = probe(client, "or-d1.1", long_context=True)
    assert [p["step"] for n, p in events if n == "step_done"][-2:] == ["CAL", "LONG"]
    profile = events[-1][1]["profile"]
    assert len(sizes) == 1 and 0.85 * 65536 <= sizes[0]["tokens"] <= 0.95 * 65536
    assert profile["reads_full_context"] is True and profile["long_context_tokens"] == sizes[0]["tokens"]
    assert profile["observed"]["LONG"]["measured"] is True
    # the fact sits in the last 5 % of the state
    state = next(c["body"]["state"] for c in upstream.calls if "кодовое" in json.dumps(c["body"], ensure_ascii=False))
    assert state.endswith(probe_mod.FACT) and len(probe_mod.FACT) < 0.05 * len(state)


@pytest.mark.parametrize("p,reads", [(0.89, False), (0.90, True)])
def test_the_long_threshold_is_0_9(client, upstream, p, reads):
    upstream.default = sized(long_p=p)
    assert probe(client, "or-d1.1", long_context=True)[-1][1]["profile"]["reads_full_context"] is reads


def test_the_long_step_is_capped_by_the_byte_limit_and_still_counts(client, upstream):
    """Jev's max_request_bytes (32,000) keeps the request out of its slow mode: the state fills that, not 90 % of
    262,144 tokens, and the reading counts (the engine cannot be sent more)."""
    sizes = []
    upstream.default = sized(ratio=0.67, overhead=230, model="typesafe/jev-1.14-20261014", sizes=sizes)
    profile = probe(client, "or-jev-1.14", long_context=True)[-1][1]["profile"]
    assert len(sizes) == 1 and 25000 <= sizes[0]["bytes"] <= 32000
    assert profile["reads_full_context"] is True and profile["observed"]["LONG"]["capped"] is True


# P3 live (2026-11-09): OpenRouter's answer to a prompt over d1.1's input limit
INPUT_LIMIT_422 = ("HTTP 422: {\"error\":{\"message\":\"The prompt for question `q` is over the model's input limit of 65536 "
                   "tokens, images included, or 250000 bytes of text.\",\"type\":\"invalid_request_error\"}}")


@pytest.mark.parametrize("too_long", [
    httpx.Response(413, json={"error": {"message": "context too long", "code": 413}}),
    httpx.Response(422, json={"error": {"message": INPUT_LIMIT_422, "code": 422}}),
], ids=["413", "422-input-limit"])
def test_the_long_step_retries_smaller_after_too_long(client, upstream, too_long):
    """An input-limit 422 is `too_long`, not a refusal read as "not served": the step retries smaller."""
    sizes = []
    upstream.default = sized(sizes=sizes, first_long=too_long)
    profile = probe(client, "or-d1.1", long_context=True)[-1][1]["profile"]
    assert len(sizes) == 2 and sizes[1]["chars"] < sizes[0]["chars"]
    assert 0.75 * 65536 <= sizes[1]["tokens"] <= 0.85 * 65536  # 80 % of the context
    assert profile["reads_full_context"] is True and profile["observed"]["LONG.1"]["request_failure"] == "too_long"
    assert budget(client)["reserved_usd"] == 0


def test_the_long_step_stays_under_openrouters_text_bytes(client, upstream):
    """P3 live: Russian at d1's 0.35 tokens a character reaches 90 % of 65,536 tokens only at ~320 KB — over the
    API's 250,000 bytes of text. The state stops at the byte cap, and the reading counts (nothing more can be sent)."""
    sizes = []
    upstream.default = sized(ratio=0.348, overhead=26, sizes=sizes)
    profile = probe(client, "or-d1.1", long_context=True)[-1][1]["profile"]
    assert len(sizes) == 1 and 240_000 <= sizes[0]["bytes"] <= 250_000
    assert profile["observed"]["LONG"]["capped"] is True and profile["reads_full_context"] is True
    assert 0.6 * 65536 <= profile["long_context_tokens"] < 0.85 * 65536


def test_an_input_limit_422_in_a_workbench_run_is_too_long(client, upstream):
    upstream.default = lambda body: httpx.Response(422, json={"error": {"message": INPUT_LIMIT_422, "code": 422}})
    res = run_once(client, {"state": "x", "questions": {"q": {"type": "noul", "instructions": "?"}}}, ["or-d1.1"])
    assert res["request_failure"] == "too_long" and res["http_status"] == 422


def test_the_long_retry_is_reserved_again(client, upstream, monkeypatch):
    """The refused attempt may be billed: the retry needs its own reservation, and without money there is none."""
    reserved = []
    real = client.app.state.budget.reserve

    def spy(amount):
        reserved.append(amount)
        return real(amount) if len(reserved) == 1 else False

    monkeypatch.setattr(client.app.state.budget, "reserve", spy)
    too_long = httpx.Response(413, json={"error": {"message": "context too long", "code": 413}})
    sizes = []
    upstream.default = sized(sizes=sizes, first_long=too_long)
    profile = probe(client, "or-d1.1", long_context=True)[-1][1]["profile"]
    assert len(reserved) == 2 and reserved[1] > 0 and len(sizes) == 1  # asked for, refused, not sent
    assert profile["observed"]["LONG"]["request_failure"] == "too_long" and profile["reads_full_context"] is None


def test_the_long_step_needs_token_counts(client, upstream):
    """Without counts that grow with the request nothing sizes the state: LONG is skipped, not guessed."""
    upstream.default = echo_answers  # 100 input tokens whatever the size
    profile = probe(client, "or-d1.1", long_context=True)[-1][1]["profile"]
    assert profile["observed"]["LONG"]["request_failure"] == "skipped"
    assert profile["reads_full_context"] is None
    assert not [c for c in upstream.calls if "кодовое" in json.dumps(c["body"], ensure_ascii=False)]


def test_a_long_step_that_read_little_proves_nothing(client, upstream):
    upstream.default = sized(long_tokens=20000)  # 30 % of 65,536: the provider read a fraction
    profile = probe(client, "or-d1.1", long_context=True)[-1][1]["profile"]
    assert profile["observed"]["LONG"]["measured"] is False and profile["reads_full_context"] is None


def test_reads_full_context_stays_with_its_snapshot(client, upstream):
    """E15: a floating alias moves to a new snapshot — the long-context reading does not move with it."""
    upstream.default = sized()
    assert probe(client, "or-d1-latest", long_context=True)[-1][1]["profile"]["reads_full_context"] is True
    assert probe(client, "or-d1-latest")[-1][1]["profile"]["reads_full_context"] is True  # same snapshot: kept
    upstream.default = sized(model="liquid/d1.2-20261201")
    profile = probe(client, "or-d1-latest")[-1][1]["profile"]
    assert profile["snapshot"] == "liquid/d1.2-20261201" and profile["reads_full_context"] is None


def test_a_local_long_step(client, fake_backend):
    fake_backend.sized_usage = True
    profile = probe(client, "mlx-d1-2b", long_context=True)[-1][1]["profile"]
    assert profile["reads_full_context"] is True  # the fake answers 0.9
    assert profile["long_context_tokens"] >= 0.85 * 32768


def test_the_estimate_reserves_the_long_step_at_its_ceiling(client):
    short = client.post("/api/probe/estimate", json={"engine_id": "or-d1.1"}).json()
    long = client.post("/api/probe/estimate", json={"engine_id": "or-d1.1", "long_context": True}).json()
    assert short["steps"] == ["N", "C", "S", "C24", "C100", "C240", "S10"] and long["steps"][-2:] == ["CAL", "LONG"]
    price = client.app.state.catalog.price_per_token("liquid/d1.1")
    assert long["estimate_usd"] - short["estimate_usd"] >= 65536 * price
    assert client.post("/api/probe/estimate", json={"engine_id": "nope"}).status_code == 400


# --- money, the stream, concurrency --------------------------------------------------------------------------------

def test_an_engine_crash_mid_probe_is_saved_and_charged(client, upstream, monkeypatch):
    """E1: the 4th call raises. The paid steps before it are saved and counted; the crash is one step's failure."""
    upstream.default = echo_answers
    real, calls = OpenRouterDecisions.run, []

    async def crashing(self, req, opts):
        calls.append(1)
        if len(calls) == 4:
            raise RuntimeError("engine bug")
        return await real(self, req, opts)

    monkeypatch.setattr(OpenRouterDecisions, "run", crashing)
    done = probe(client, "or-d1.1")[-1]
    assert done[0] == "probe_done" and done[1]["profile"]["observed"]["C24"]["request_failure"] == "provider_error"
    b = budget(client)
    assert b["spent_usd"] == pytest.approx(6 * COST) and b["reserved_usd"] == 0
    assert client.get("/api/runs").json()["runs"][0]["kind"] == "probe"


def test_a_failed_save_still_charges(client, upstream, monkeypatch):
    upstream.default = echo_answers

    def broken(record):
        raise OSError("disk full")

    monkeypatch.setattr(client.app.state.store, "save", broken)
    events = probe(client, "or-d1.1")
    assert events[-1][0] == "probe_error" and "disk full" in events[-1][1]["message"]
    b = budget(client)
    assert b["spent_usd"] == pytest.approx(7 * COST) and b["reserved_usd"] == 0


def test_a_crash_while_building_the_profile_saves_the_record(client, upstream, monkeypatch):
    upstream.default = echo_answers

    def broken(*a, **k):
        raise TypeError("bad stored value")

    monkeypatch.setattr(probe_mod, "build_profile", broken)
    events = probe(client, "or-d1.1")
    assert events[-1][0] == "probe_error" and "bad stored value" in events[-1][1]["message"]
    row = client.get("/api/runs").json()["runs"][0]
    assert row["kind"] == "probe" and row["cost_usd"] == pytest.approx(7 * COST)
    assert budget(client)["reserved_usd"] == 0


@pytest.mark.parametrize("behaviour", ["raises", "returns"])
def test_every_probe_stream_ends_with_a_terminal_event(client, monkeypatch, behaviour):
    """A probe that raises gets `probe_error` from the endpoint; one that ends without a terminal event gets the
    stream's fallback — never a stream that waits forever with every Probe button disabled."""
    async def run(self, engine, emit, **kw):
        if behaviour == "raises":
            raise RuntimeError("boom")

    monkeypatch.setattr(probe_mod.Prober, "run", run)
    events = probe(client, "or-d1.1")
    assert [n for n, _ in events] == ["probe_error"]


def test_the_probe_deadline_skips_the_remaining_steps(client, upstream, monkeypatch):
    monkeypatch.setattr(probe_mod, "PROBE_DEADLINE_S", -1.0)
    upstream.default = echo_answers
    done = probe(client, "or-d1.1")[-1][1]
    assert done["no_profile"] is True and done["failure"] == "skipped" and not upstream.calls


def test_one_probe_per_engine_at_a_time(client, upstream):
    prober = client.app.state.prober
    prober.busy.add("or-d1.1")
    assert client.post("/api/probe", json={"engine_id": "or-d1.1"}).status_code == 409
    events = []

    async def emit(name, payload):
        events.append(name)

    # the race the API check cannot close: the second run refuses on its own
    assert asyncio.run(prober.run(client.app.state.registry.get("or-d1.1"), emit)) is None
    assert events == ["probe_error"] and not upstream.calls
    prober.busy.discard("or-d1.1")


def test_probe_respects_the_budget(settings, upstream, local):
    settings.daily_budget_usd = 1e-9
    upstream.default = echo_answers
    app = create_app(settings, transport=upstream.transport, refresh_catalog=False, local=local)
    with TestClient(app, base_url=UI) as c:
        assert [n for n, _ in probe(c, "or-d1.1")] == ["budget_stop"]
    assert not upstream.calls


# --- the request: ids, ZDR ------------------------------------------------------------------------------------------

@pytest.mark.parametrize("engine_id", ["nope", "ollama-d1-2b", "mlx-d1-omni"])
def test_probe_refuses_unknown_or_unavailable_engines(client, engine_id):
    assert client.post("/api/probe", json={"engine_id": engine_id}).status_code == 400


def test_a_zdr_off_engine_is_probed_only_with_consent(client, upstream):
    """E10: the probe's text is synthetic, but `zdr: false` still needs a yes — and then the calls go out."""
    upstream.default = echo_answers
    r = client.post("/api/probe", json={"engine_id": "or-no-zdr"})
    assert r.status_code == 400 and "ZDR" in r.json()["detail"] and not upstream.calls
    done = probe(client, "or-no-zdr", allow_no_zdr=True)[-1][1]
    assert done["profile"]["types"] == ["noul", "choice", "score"]
    assert upstream.calls and all("zdr" not in c["body"]["provider"] for c in upstream.calls)


def test_a_snapshot_file_never_overwrites_latest_json():
    assert probe_mod.safe_name("latest") != "latest" and probe_mod.safe_name("liquid/d1.1-20261027") == "liquid_d1.1-20261027"
    assert probe_mod.safe_name("..") == "unknown"


def test_the_runner_keeps_the_http_status_of_a_refusal(client, upstream):
    """Workbench runs record the status a refusal came with, as probes read it (DESIGN §6.3)."""
    upstream.default = lambda body: httpx.Response(400, json={"error": {"message": "Invalid request", "code": 400}})
    res = run_once(client, {"state": "x", "questions": {"q": {"type": "noul", "instructions": "?"}}}, ["or-d1.1"])
    assert res["request_failure"] == "refused" and res["http_status"] == 400


@pytest.mark.parametrize("engine_id,ok", [("or-d1.1", True), ("a.b_c-1", True), ("..", False), (".", False),
                                          ("a b", False), ("a/b", False), ("x" * 101, False)])
def test_engine_ids_that_name_a_directory(tmp_path, engine_id, ok):
    assert valid_engine_id(engine_id) is ok
    store = ProbeStore(tmp_path / "probes")
    if ok:
        assert store._dir(engine_id).parent == tmp_path / "probes"
    else:
        with pytest.raises(ValueError):
            store._dir(engine_id)


# --- local engines ---------------------------------------------------------------------------------------------------

def test_local_probe_covers_the_extensions_and_audio(client, fake_backend, settings):
    fake_backend.downloaded.add(D1_OMNI)
    events = probe(client, "mlx-d1-omni")
    profile = events[-1][1]["profile"]
    assert set(profile["types"]) == {"noul", "choice", "score", "multi", "text", "number", "date"}
    assert profile["media"] == ["audio"] and profile["reports_cost"] is False
    steps = [p["step"] for n, p in events if n == "step_done"]
    assert steps == ["N", "C", "S", "C24", "C100", "C240", "S10", "M", "T", "NUM", "D", "A"]
    assert engine_profile(client, "mlx-d1-omni")["media"] == ["image", "audio"]
    assert (settings.data_dir / "media" / "probe" / probe_mod.AUDIO_CLIP.name).is_file()
    assert probe_mod.AUDIO_CLIP.is_relative_to(probe_mod.ROOT / "app")  # not a test fixture


def test_a_local_refusal_reads_as_not_served(client, fake_backend, monkeypatch):
    from tests.conftest import FakeModel

    real = FakeModel.system_one

    def no_dates(self, state, questions, *a, **k):
        if any(q["type"] == "date" for q in questions.values()):
            raise ValueError("question 'q': dates are not served")
        return real(self, state, questions, *a, **k)

    monkeypatch.setattr(FakeModel, "system_one", no_dates)
    profile = probe(client, "mlx-d1-2b")[-1][1]["profile"]
    assert "date" not in profile["types"] and profile["observed"]["D"]["http_status"] == 400
    row = {r["id"]: r for r in client.get("/api/matrix").json()["rows"]}["mlx-d1-2b"]
    assert row["cells"]["date"] == "no" and row["cells"]["text"] == "yes" and row["cells"]["audio"] == "never"


def test_audio_not_heard_is_removed_and_stays_no(client, fake_backend, monkeypatch):
    """A checkpoint that refuses audio (mlx-vlm's own check) loses it; a later probe whose A step fails for another
    reason keeps it out, and the cell stays ✗."""
    from tests.conftest import FakeModel

    fake_backend.downloaded.add(D1_OMNI)
    real = FakeModel.system_one
    error = [ValueError("this checkpoint takes no audio")]

    def deaf(self, state, questions, media=None, *a, **k):
        if media:
            raise error[0]
        return real(self, state, questions, media, *a, **k)

    monkeypatch.setattr(FakeModel, "system_one", deaf)
    profile = probe(client, "mlx-d1-omni")[-1][1]["profile"]
    assert profile["media"] == [] and "audio" in profile["not_served"]
    assert engine_profile(client, "mlx-d1-omni")["media"] == ["image"]
    error[0] = RuntimeError("the GPU went away")  # unobserved: keeps the stored "no"
    profile = probe(client, "mlx-d1-omni")[-1][1]["profile"]
    assert profile["observed"]["A"]["reading"] == "unobserved" and profile["media"] == []
    row = {r["id"]: r for r in client.get("/api/matrix").json()["rows"]}["mlx-d1-omni"]
    assert row["cells"]["audio"] == "no"


def test_a_local_precheck_refusal_reads_as_not_served(client, fake_backend, monkeypatch):
    """mlx-vlm's question checks run before the queue (precheck): their refusal is a 400 for the probe too."""
    from tests.conftest import mlx_check

    def validate(questions):
        if any(q["type"] == "date" for q in questions.values()):
            raise ValueError("question 'q': a date is not served by this checkpoint")
        mlx_check(questions)

    monkeypatch.setattr(fake_backend, "validate", validate)
    profile = probe(client, "mlx-d1-2b")[-1][1]["profile"]
    assert profile["observed"]["D"]["http_status"] == 400 and profile["observed"]["D"]["reading"] == "not_served"
    assert "date" not in profile["types"]


def test_a_local_engine_error_is_not_a_refusal(client, fake_backend):
    """A ValueError that is not mlx-vlm's own check (here numpy's) is an engine error: unobserved, nothing removed."""
    fake_backend.call_error = ValueError("operands could not be broadcast together with shapes (3,) (4,)")
    done = probe(client, "mlx-d1-2b")[-1][1]
    assert done["no_profile"] is True and done["failure"] == "provider_error"


# --- the matrix -----------------------------------------------------------------------------------------------------

def test_matrix_lists_engines_then_the_catalog(client):
    rows = client.get("/api/matrix").json()["rows"]
    ids = [r["id"] for r in rows]
    assert ids[:5] == ["or-jev-1.14", "or-d1.1", "or-d1-latest", "or-no-zdr", "mlx-d1-2b"]
    by = {r["id"]: r for r in rows}
    assert by["or-jev-1.14"]["context_length"] == 262144 and by["or-jev-1.14"]["price_per_mtok"] == 0.042
    assert by["or-d1.1"]["zdr_endpoint"] is True and by["or-d1.1"]["cells"]["noul"] == "unchecked"
    assert by["or-no-zdr"]["zdr"] is False and by["or-d1.1"]["zdr"] is True
    assert by["mlx-d1-2b"]["context_length"] == 32768 and by["mlx-d1-2b"]["cells"]["multi"] == "unchecked"
    assert by["mlx-d1-2b"]["price_per_mtok"] == 0.0
    assert by["or-jev-1.14"]["cells"]["text"] == "never"
    catalog = [r for r in rows if r["source"] == "catalog"]
    assert catalog and "typesafe/jev-1.14" not in [r["id"] for r in catalog]
    assert any(r["id"] == "liquid/d1" and r["expiration_date"] == "2026-11-20" for r in catalog)
    # catalog models are OpenRouter decision models: the types OpenRouter cannot serve are "never", as configured ones
    assert all(r["cells"]["multi"] == "never" and r["cells"]["noul"] == "unchecked" for r in catalog)


def test_the_catalog_ceiling_is_the_openrouter_engines(client):
    assert list(CATALOG_TYPES) == client.app.state.registry.get("or-d1.1").default_profile().types


def test_a_cloud_engine_missing_from_the_catalog_has_no_price(settings, upstream, local):
    """E8: no catalog (a first start offline) is "unknown", not "$0"."""
    (settings.probes_dir / "catalog.json").unlink()
    app = create_app(settings, transport=upstream.transport, refresh_catalog=False, local=local)
    with TestClient(app, base_url=UI) as c:
        by = {r["id"]: r for r in c.get("/api/matrix").json()["rows"]}
    assert by["or-jev-1.14"]["price_per_mtok"] is None and by["mlx-d1-2b"]["price_per_mtok"] == 0.0


def test_a_run_on_a_new_snapshot_marks_the_profile_stale(client, upstream):
    """SPEC §5.6.2 item 4: a run's answer names a snapshot other than the probe's → stale until the next probe."""
    upstream.default = echo_answers
    probe(client, "or-d1-latest")
    q = {"type": "noul", "instructions": "?"}
    run_once(client, {"state": "x", "questions": {"q": q}}, ["or-d1-latest"])
    assert {r["id"]: r for r in client.get("/api/matrix").json()["rows"]}["or-d1-latest"]["stale"] is None
    upstream.default = lambda body: echo_answers(body, model="liquid/d1.2-20261201")
    run_once(client, {"state": "x", "questions": {"q": q}}, ["or-d1-latest"])
    stale = {r["id"]: r for r in client.get("/api/matrix").json()["rows"]}["or-d1-latest"]["stale"]
    assert "liquid/d1.2-20261201" in stale
    probe(client, "or-d1-latest")  # the next probe clears it
    assert {r["id"]: r for r in client.get("/api/matrix").json()["rows"]}["or-d1-latest"]["stale"] is None


def test_a_probe_record_cannot_be_rerun(client, upstream):
    upstream.default = echo_answers
    run_id = probe(client, "or-d1.1")[-1][1]["run_id"]
    assert client.post(f"/api/runs/{run_id}/rerun").status_code == 400


# --- prices: out of scope in P3, inherited by every probe ----------------------------------------------------------

@pytest.mark.parametrize("raw,price", [("0.00000002", 2e-8), ("nan", None), ("inf", None), ("-1", None), ("x", None)])
def test_a_catalog_price_must_be_a_finite_non_negative_number(tmp_path, raw, price):
    c = Catalog(tmp_path / "catalog.json")
    c.data = {"decisions": [{"id": "m", "pricing": {"prompt": raw}}]}
    assert c.price_per_token("m") == price


def test_the_budget_refuses_a_nan_reservation(client):
    b = client.app.state.budget
    assert b.reserve(float("nan")) is False and b.reserve(-1.0) is False and b.reserved == 0
