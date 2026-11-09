"""§9 the compat server: whip-insight's systemone request, 400 naming a type, token, limits, start-up refusal."""

from __future__ import annotations

import base64
import io

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError

from app.catalog import Catalog
from app.compat import create_compat_app
from app.registry import Registry
from app.settings import Settings
from tests.conftest import D1_2B, load

WI = load("requests/whip_insight_systemone.json")
TOKEN = "0123456789abcdef" * 3  # 48 characters of 16 kinds, like `openssl rand -hex 24`


def compat(settings, local) -> TestClient:
    app = create_compat_app(Registry(settings, Catalog(settings.probes_dir / "catalog.json"), local=local))
    return TestClient(app, base_url="http://127.0.0.1:8791")


@pytest.fixture
def client(settings, local):
    return compat(settings, local)


def test_whip_insight_request_is_answered_in_system_one_names(client, fake_backend):
    r = client.post("/v1/systemone", json=WI)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["model"] == f"{D1_2B}-20261105" and body["provider"] == "local-mlx" and body["id"].startswith("so-")
    assert body["usage"] == {"input_tokens": 400, "output_tokens": 0, "cost": 0}
    a = body["answers"]
    assert a["std_refund_request"] == {"type": "noul", "noul": 0.9}
    assert set(a["call_kind"]) == {"type", "choice", "confidence", "probabilities"}
    assert a["refund_evidence"]["choice"] == "t1" and set(a["refund_evidence"]["probabilities"]) == {f"t{i}" for i in range(1, 7)}
    assert set(a["satisfaction"]) == {"type", "score", "confidence", "probabilities", "legend"}
    assert fake_backend.calls[0]["score_readout"] == "mean"  # score = probability-weighted level, as OpenRouter's
    assert 0 <= a["satisfaction"]["score"] <= 2


def test_default_model_when_none_given(client):
    body = {k: v for k, v in WI.items() if k != "model"}
    assert client.post("/v1/systemone", json=body).status_code == 200


@pytest.mark.parametrize("q,typ", [
    ({"type": "text", "instructions": "?"}, "text"),
    ({"type": "date", "instructions": "?"}, "date"),
    ({"type": "box", "instructions": "?"}, "box"),
    ({"type": "choice", "multiple": True, "instructions": "?", "criteria": {"a": None, "b": None}}, "multi"),
])
def test_other_types_are_400_naming_the_type(client, fake_backend, q, typ):
    body = {**WI, "questions": {**WI["questions"], "extra_q": q}}
    r = client.post("/v1/systemone", json=body)
    assert r.status_code == 400
    assert r.json() == {"error": {"code": "unsupported_question_type", "type": typ, "question": "extra_q"}}
    assert not fake_backend.calls


def test_extensions_header_opens_the_extended_types(client):
    ext = load("requests/extensions_ru.json")
    r = client.post("/v1/systemone", json={"state": ext["state"], "questions": ext["questions"]},
                    headers={"X-Playground-Extensions": "1"})
    assert r.status_code == 200 and r.json()["answers"]["products"]["choices"] == ["deposit"]


def test_token_required_when_set(settings, local):
    settings.systemone_token = SecretStr(TOKEN)
    c = compat(settings, local)
    assert c.post("/v1/systemone", json=WI).status_code == 401
    assert c.post("/v1/systemone", json=WI, headers={"Authorization": f"Bearer {TOKEN}x"}).json() == {"error": {"code": "unauthorized"}}
    assert c.post("/v1/systemone", json=WI, headers={"Authorization": TOKEN}).status_code == 401
    assert c.post("/v1/systemone", json=WI, headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 200
    assert c.get("/health").status_code == 200  # health needs no token


def test_browser_origin_is_refused(client, fake_backend):
    assert client.post("/v1/systemone", json=WI, headers={"Origin": "http://evil.example"}).status_code == 403
    assert not fake_backend.calls


def test_no_ui_and_no_api_on_this_port(client):
    for path in ("/", "/api/engines", "/docs", "/openapi.json", "/static/js/app.js"):
        assert client.get(path).status_code == 404


def test_body_over_6_mb_is_413(client, fake_backend):
    r = client.post("/v1/systemone", content=b'{"state": "' + b"x" * (6 * 1024 * 1024) + b'"}',
                    headers={"Content-Type": "application/json"})
    assert r.status_code == 413 and not fake_backend.calls


def test_state_over_context_is_413(client, fake_backend):
    # 32,768 tokens at whip-insight's 1 token per character, checked before the model is loaded
    r = client.post("/v1/systemone", json={**WI, "state": "я" * 32769})
    assert r.status_code == 413 and not fake_backend.loads


@pytest.mark.parametrize("body,code", [
    ({**WI, "questions": {f"q{i}": {"type": "noul", "instructions": "?"} for i in range(65)}}, 400),
    ({**WI, "questions": {}}, 400),
    ({**WI, "questions": {"Bad": {"type": "noul", "instructions": "?"}}}, 400),
    ({**WI, "questions": {"q": {"type": "score", "instructions": "?", "criteria": ["one"]}}}, 400),
    ({**WI, "questions": {"q": {"type": "choice", "instructions": "?", "criteria": {f"o{i}": None for i in range(256)}}}}, 400),
    ({**WI, "model": "or-d1.1"}, 404),
    ({**WI, "model": "nope"}, 404),
    ({**WI, "model": "mlx-d1-omni"}, 503),  # not downloaded
    ({**WI, "state": None}, 400),
    ({**WI, "media": [{"type": "audio", "path": "/etc/passwd"}]}, 400),
])
def test_rejections_before_the_model(client, fake_backend, body, code):
    assert client.post("/v1/systemone", json=body).status_code == code
    assert not fake_backend.calls


def test_invalid_json_is_400(client):
    r = client.post("/v1/systemone", content=b"{not json", headers={"Content-Type": "application/json"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_json"


def png(w=8, h=8) -> str:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (w, h), (200, 30, 30)).save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode()


def test_images_reach_the_model(client, fake_backend):
    assert client.post("/v1/systemone", json={**WI, "images": [png()]}).status_code == 200
    assert len(fake_backend.calls[0]["media"]) == 1


@pytest.mark.parametrize("images", [["not base64!"], [base64.b64encode(b"not a picture").decode()], [png()] * 9, "x"])
def test_bad_images_are_400(client, fake_backend, images):
    assert client.post("/v1/systemone", json={**WI, "images": images}).status_code == 400
    assert not fake_backend.calls


def test_huge_picture_dimensions_are_refused_before_decoding(client, fake_backend):
    # an 8000 x 6000 PNG of one colour compresses to a few hundred KB but decodes to 144 MB
    assert client.post("/v1/systemone", json={**WI, "images": [png(8000, 6000)]}).status_code == 400
    assert not fake_backend.calls


def test_health_reports_the_default_model(client):
    h = client.get("/health").json()
    assert h == {"status": "ok", "model": "mlx-d1-2b", "state": "unloaded", "mlx_vlm_version": "0.7.12"}


@pytest.mark.parametrize("env", [
    {"compat_host": "0.0.0.0"},
    {"compat_host": "192.168.1.10"},
    {"compat_host": "0.0.0.0", "systemone_token": "short"},
    {"systemone_token": "x" * 31},
    {"app_host": "0.0.0.0"},
])
def test_unsafe_settings_refuse_to_start(env):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **env)


def test_lan_with_token_is_allowed():
    s = Settings(_env_file=None, compat_host="0.0.0.0", systemone_token=TOKEN)
    assert s.compat_token == TOKEN


# --- review P2 -------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("instructions", [
    {"type": "audio", "path": "http://169.254.169.254/latest/meta-data/"},
    {"type": "image", "path": "/Users/sergey/Desktop/secret.png"},
    {"type": "video", "path": "/etc/hosts"},
    ["a", "list"],
])
def test_instructions_must_be_text(client, fake_backend, instructions):
    """HIGH: d1 reads a dict in `instructions` as a media item and opens its path or URL."""
    body = {**WI, "questions": {"q": {"type": "noul", "instructions": instructions}}}
    r = client.post("/v1/systemone", json=body)
    assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_request"
    assert not fake_backend.calls and not fake_backend.loads


def eps() -> str:
    ps = b"%!PS-Adobe-3.0 EPSF-3.0\n%%BoundingBox: 0 0 10 10\nnewpath 0 0 moveto 10 10 lineto stroke\nshowpage\n"
    return base64.b64encode(ps).decode()


def tiff() -> str:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (8, 8)).save(buf, "TIFF")
    return base64.b64encode(buf.getvalue()).decode()


@pytest.mark.parametrize("image", [eps(), tiff()], ids=["eps", "tiff"])
def test_only_png_jpeg_webp_and_no_ghostscript(client, fake_backend, monkeypatch, image):
    """HIGH: Pillow hands EPS to Ghostscript; only three decoders are allowed."""
    import subprocess

    def no_subprocess(*a, **k):
        raise AssertionError("a decoder started a subprocess")

    monkeypatch.setattr(subprocess, "Popen", no_subprocess)
    monkeypatch.setattr(subprocess, "check_call", no_subprocess)
    r = client.post("/v1/systemone", json={**WI, "images": [image]})
    assert r.status_code == 400 and not fake_backend.calls


def nested(depth: int):
    return [nested(depth - 1)] if depth else 0


def test_nested_state_is_measured_as_the_model_reads_it(client, fake_backend):
    """Round-1 HIGH, made non-vacuous in round 2: a depth-30 state whose compact row passes the cheap check
    (≈ 19K characters) but which mlx-vlm renders with indent=2 to ≈ 590K characters must get 413 — from the
    rendered count, before any load."""
    import json as _json

    state = [nested(29) for _ in range(300)]
    compact = len(_json.dumps(state, ensure_ascii=False))
    assert 15000 < compact < 25000  # the compact count alone passes 32,768
    r = client.post("/v1/systemone", json={**WI, "state": state})
    assert r.status_code == 413 and not fake_backend.loads
    assert max(len(t) for t in fake_backend.counted) > 300_000  # what was counted is the indent=2 rendering


@pytest.mark.timeout(10)
def test_deep_state_is_refused_before_anything_is_rendered(client, fake_backend):
    """Round-2 HIGH: 40 KB of nesting rendered with indent=2 is 800 M characters. It must be refused by the depth
    check, cheaply: little memory, little time, nothing counted."""
    import json as _json
    import time as _time
    import tracemalloc

    body = '{"model": "mlx-d1-2b", "state": ' + "[" * 20000 + "]" * 20000 + ', "questions": ' + \
        _json.dumps(WI["questions"]) + "}"
    tracemalloc.start()
    t0 = _time.perf_counter()
    r = client.post("/v1/systemone", content=body.encode(), headers={"Content-Type": "application/json"})
    elapsed = _time.perf_counter() - t0
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert r.status_code == 400 and "nested deeper than 32" in r.json()["error"]["message"]
    assert peak < 50 * 1024 * 1024 and elapsed < 1.0 and not fake_backend.counted


@pytest.mark.parametrize("depth,code", [(32, 200), (33, 400)])
def test_state_depth_cap_is_32(client, depth, code):
    state = [nested(depth - 1)]  # nested(d) is d lists deep; one more around it
    assert client.post("/v1/systemone", json={**WI, "state": state}).status_code == code


@pytest.mark.parametrize("chars,code", [(58000, 413), (20000, 200)])
def test_question_text_counts_toward_the_row(client, fake_backend, chars, code):
    """HIGH: memory follows the whole packed row; one noul with 58,000 characters of instructions was 20,001 tokens.
    Inputs are literals; the bound itself is pinned below."""
    body = {**WI, "questions": {"q": {"type": "noul", "instructions": "я" * chars}}}
    assert client.post("/v1/systemone", json=body).status_code == code


def test_the_row_bound_is_pinned_in_the_shipped_config():
    """Not the fixture's own value (round 2: that pin was circular): the example config and the code default."""
    import yaml

    from app.engines.mlx_local import DEFAULT_CONTEXT
    from app.settings import ROOT

    cfg = yaml.safe_load((ROOT / "config.example.yaml").read_text())
    local = [e for e in cfg["engines"] if e["kind"] == "mlx_local"]
    assert local and all(e["context_tokens"] == 32768 for e in local)  # SPIKE §2.4: 64K does not fit 24 GB
    assert DEFAULT_CONTEXT == 32768


def test_long_options_count_too(client, fake_backend):
    criteria = {f"o{i}": "д" * 216 for i in range(255)}  # 255 options of 216 characters: 13,260 real tokens each
    questions = {f"c{i}": {"type": "choice", "instructions": "?", "criteria": criteria} for i in range(3)}
    r = client.post("/v1/systemone", json={**WI, "questions": questions})
    assert r.status_code == 413 and not fake_backend.loads


def test_images_limits(client, fake_backend):
    assert client.post("/v1/systemone", json={**WI, "images": [png()] * 5}).status_code == 400  # at most 4
    assert client.post("/v1/systemone", json={**WI, "images": [png(5000, 4000)]}).status_code == 400  # 20 MP


def test_images_are_decoded_off_the_event_loop(client, fake_backend, monkeypatch):
    import asyncio

    import app.compat as compat_module

    on_loop = []
    real = compat_module.decode_images

    def spy(*a):
        # TestClient runs the app off the main thread, so the main thread proves nothing (round 2): ask asyncio
        try:
            asyncio.get_running_loop()
            on_loop.append(True)
        except RuntimeError:
            on_loop.append(False)
        return real(*a)

    monkeypatch.setattr(compat_module, "decode_images", spy)
    assert client.post("/v1/systemone", json={**WI, "images": [png()]}).status_code == 200
    assert on_loop == [False]


def test_pixel_limit_is_checked_before_decoding(client, fake_backend, monkeypatch):
    from PIL import Image

    def no_load(self):
        raise AssertionError("decoded before the pixel check")

    big = png(8000, 6000)
    monkeypatch.setattr(Image.Image, "load", no_load)
    assert client.post("/v1/systemone", json={**WI, "images": [big]}).status_code == 400


async def test_chunked_body_stops_at_the_limit(settings, local, fake_backend):
    """At the ASGI level: TestClient buffers a whole body, so it cannot show streaming. The app must stop reading
    a body without Content-Length once it passes 6 MB, not take all 16 MiB offered."""
    app = create_compat_app(Registry(settings, Catalog(settings.probes_dir / "catalog.json"), local=local))
    pulled, sent = 0, []

    async def receive():
        nonlocal pulled
        pulled += 1
        return {"type": "http.request", "body": b"x" * (1024 * 1024), "more_body": pulled < 16}

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST", "scheme": "http",
             "path": "/v1/systemone", "raw_path": b"/v1/systemone", "query_string": b"", "root_path": "",
             "headers": [(b"host", b"127.0.0.1:8791"), (b"content-type", b"application/json"),
                         (b"transfer-encoding", b"chunked")],
             "client": ("127.0.0.1", 50000), "server": ("127.0.0.1", 8791)}
    await app(scope, receive, send)
    assert sent[0]["status"] == 413
    assert pulled <= 7  # 6 MiB of limit plus the chunk that crossed it, not 16
    assert not fake_backend.calls


def test_auth_comes_before_the_size_check(settings, local):
    settings.systemone_token = SecretStr(TOKEN)
    c = compat(settings, local)
    r = c.post("/v1/systemone", content=b"x" * (7 * 1024 * 1024), headers={"Content-Type": "application/json"})
    assert r.status_code == 401


@pytest.mark.parametrize("header", [f"bearer {TOKEN}", f"BEARER {TOKEN}", f"Bearer  {TOKEN}", f"Bearer {TOKEN} "])
def test_bearer_scheme_is_case_insensitive(settings, local, header):
    settings.systemone_token = SecretStr(TOKEN)
    assert compat(settings, local).post("/v1/systemone", json=WI, headers={"Authorization": header}).status_code == 200


@pytest.mark.parametrize("token", ["a" * 40, "ab" * 20, " " + "0123456789abcdef" * 3])
def test_uniform_or_padded_tokens_refuse_to_start(token):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, systemone_token=token)


@pytest.mark.parametrize("host,code", [("attacker.example", 400), ("sergeys-mac.local:8791", 200), ("localhost:8791", 200)])
def test_compat_host_header(client, host, code):
    assert client.get("/health", headers={"Host": host}).status_code == code


@pytest.mark.parametrize("q", [
    {"evil\n": {"type": "noul", "instructions": "?"}},
    {"q": {"type": "choice", "instructions": "?", "criteria": {"t1\n": None, "t2": None}}},
    {"q": {"type": "choice", "multiple": "false", "instructions": "?", "criteria": {"a": None, "b": None}}},
])
def test_strict_keys_and_flags(client, fake_backend, q):
    assert client.post("/v1/systemone", json={**WI, "questions": q}).status_code == 400
    assert not fake_backend.calls


@pytest.mark.parametrize("state", [True, 12345, 1.5])
def test_state_must_be_text_object_or_array(client, state):
    assert client.post("/v1/systemone", json={**WI, "state": state}).status_code == 400


def test_error_bodies_echo_only_short_strings(client):
    r = client.post("/v1/systemone", json={**WI, "questions": {"q": {"type": {"x": "y" * 1000}, "instructions": "?"}}})
    assert r.status_code == 400 and r.json()["error"]["type"] is None and len(r.text) < 300
    r = client.post("/v1/systemone", json={**WI, "model": "m" * 1000})
    assert r.status_code == 404 and len(r.json()["error"]["model"]) == 64


def test_error_mapping_for_whip_insight(client, fake_backend):
    """413 too_long, 503 model_unavailable (no paths), 400 for an mlx-vlm refusal, 500 model_error otherwise."""
    fake_backend.load_error = RuntimeError("cannot open /Users/sergey/.cache/huggingface/hub/x: Insufficient Memory")
    r = client.post("/v1/systemone", json=WI)
    assert r.status_code == 503 and r.json() == {"error": {"code": "model_unavailable"}} and "/Users/" not in r.text
    h = client.get("/health").json()
    assert h["status"] == "degraded" and h["error"] == "load_failed" and "/Users/" not in str(h)
    fake_backend.load_error = None
    fake_backend.call_error = ValueError("a choice's criteria …")
    assert client.post("/v1/systemone", json=WI).status_code == 400
    fake_backend.call_error = RuntimeError("[metal] Insufficient Memory")
    r = client.post("/v1/systemone", json=WI)
    assert r.status_code == 500 and r.json() == {"error": {"code": "model_error"}}
    fake_backend.call_error = None
    assert client.get("/health").json()["status"] == "ok"


def test_busy_queue_is_429(settings, local, fake_backend):
    local.max_waiting = 0
    r = compat(settings, local).post("/v1/systemone", json=WI)
    assert r.status_code == 429 and r.json()["error"]["code"] == "busy" and not fake_backend.calls


def test_odd_model_output_is_a_json_500_or_tolerated(client, fake_backend, monkeypatch):
    from tests.conftest import FakeModel

    real = FakeModel.system_one

    def no_usage(self, *a, **k):
        out = real(self, *a, **k)
        out["usage"] = None
        return out

    monkeypatch.setattr(FakeModel, "system_one", no_usage)
    assert client.post("/v1/systemone", json=WI).status_code == 200

    def nan(self, *a, **k):
        out = real(self, *a, **k)
        out["answers"]["std_refund_request"]["noul"] = float("nan")
        return out

    monkeypatch.setattr(FakeModel, "system_one", nan)
    r = client.post("/v1/systemone", json=WI)
    assert r.status_code == 500 and r.json()["error"]["code"] == "model_error"


# --- review round 2 --------------------------------------------------------------------------------------------------


def test_the_models_tokenizer_can_refuse_what_the_character_check_passed(client, fake_backend):
    """A state of 20,000 characters passes 1 token per character; at 2 tokens a character (emoji, CJK) the row is
    40,000 tokens: 413 from the tokenizer, mapped from TooLong."""
    fake_backend.tokens_per_char = 2
    r = client.post("/v1/systemone", json={**WI, "state": "x" * 20000})
    assert r.status_code == 413 and "tokens in the row" in r.json()["error"]["message"] and not fake_backend.loads


@pytest.mark.parametrize("images", [5, "QUJD" * 250_000, [5], {"a": 1}])
def test_images_of_the_wrong_type_are_a_json_400(client, fake_backend, images):
    r = client.post("/v1/systemone", json={**WI, "images": images})
    assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_request" and not fake_backend.calls


def test_a_broken_tokenizer_is_model_unavailable(client, fake_backend):
    fake_backend.tokenizer_error = Exception("No such file or directory (os error 2)")  # what tokenizers raises
    r = client.post("/v1/systemone", json=WI)
    assert r.status_code == 503 and r.json() == {"error": {"code": "model_unavailable"}}


def test_an_unlisted_host_is_a_json_400(client):
    r = client.get("/health", headers={"Host": "192.168.1.10:8791"})
    assert r.status_code == 400 and r.json() == {"error": {"code": "forbidden_host"}}


@pytest.mark.parametrize("host", ["::1", "fe80::1", "::"])
def test_ipv6_compat_host_refuses_to_start(host):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, compat_host=host, systemone_token=TOKEN)


def test_questions_count_by_group_not_all_at_once(client, fake_backend):
    """mlx-vlm packs 16 questions after the state per row: 20 questions of 600 characters with a 21,000-character
    state fit every row (31,112 characters), though all of them together would not (33,640)."""
    questions = {f"q{i}": {"type": "noul", "instructions": "д" * 600} for i in range(20)}
    r = client.post("/v1/systemone", json={**WI, "state": "я" * 21000, "questions": questions})
    assert r.status_code == 200


def test_multi_option_ids_are_counted_twice():
    from app.engines.mlx_local import question_text
    from app.schemas import Question

    multi = Question(type="multi", instructions="?", criteria={"alpha": None, "beta": None})
    single = Question(type="choice", instructions="?", criteria={"alpha": None, "beta": None})
    assert question_text("q", multi).count("alpha") == 2 and question_text("q", single).count("alpha") == 1
