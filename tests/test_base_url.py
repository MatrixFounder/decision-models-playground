"""The OpenRouter root is configuration, not code: OPENROUTER_BASE_URL in .env for the catalog and the calls, an
engine's own `base_url` in config.yaml for one engine. The key goes to that address: https, or http on this Mac."""

from __future__ import annotations

import httpx
import pytest
import yaml

from app.catalog import Catalog
from app.registry import Registry, load_config
from app.schemas import DecisionRequest, RunOptions
from app.settings import Settings, check_base_url
from tests.conftest import ENGINES, echo_answers

REQ = DecisionRequest(state="x", questions={"q": {"type": "noul", "instructions": "?"}})


@pytest.mark.parametrize("url,expected", [
    ("https://openrouter.ai", "https://openrouter.ai"),
    ("https://openrouter.ai/", "https://openrouter.ai"),
    ("  https://gw.example.com/openrouter/  ", "https://gw.example.com/openrouter"),  # a gateway under a prefix
    ("http://127.0.0.1:4000", "http://127.0.0.1:4000"),  # a local LiteLLM
    ("http://localhost:4000/or", "http://localhost:4000/or"),
])
def test_accepted_roots(url, expected):
    assert check_base_url(url, "X") == expected


@pytest.mark.parametrize("url,msg", [
    ("http://gw.lan:4000", "https, or http only"),  # the key in clear text over the network
    ("http://192.168.1.5", "https, or http only"),
    ("https://user:pass@gw.example.com", "no user"),
    ("https://gw.example.com/?key=1", "no user, password, query"),
    ("https://gw.example.com/#x", "no user, password, query"),
    ("ftp://openrouter.ai", r"http\(s\) URL"),
    ("openrouter.ai", r"http\(s\) URL"),
])
def test_refused_roots(url, msg):
    with pytest.raises(ValueError, match=msg):
        check_base_url(url, "X")


def test_the_setting_defaults_to_openrouter(tmp_path):
    assert Settings(_env_file=None, data_dir=tmp_path).openrouter_base_url == "https://openrouter.ai"
    assert Settings(_env_file=None, data_dir=tmp_path, openrouter_base_url="").openrouter_base_url == "https://openrouter.ai"
    with pytest.raises(ValueError, match="OPENROUTER_BASE_URL"):
        Settings(_env_file=None, data_dir=tmp_path, openrouter_base_url="http://gw.lan")


async def test_the_calls_go_to_the_configured_root(settings, upstream):
    upstream.default = echo_answers
    settings.openrouter_base_url = "https://gw.example.com/openrouter"
    reg = Registry(settings, Catalog(settings.probes_dir / "catalog.json"), transport=upstream.transport)
    await reg.get("or-d1.1").run(REQ, RunOptions())
    assert upstream.calls[-1]["url"] == "https://gw.example.com/openrouter/api/alpha/decisions"
    assert upstream.calls[-1]["auth"] == "Bearer sk-or-v1-test"


async def test_an_engine_can_have_its_own_root(settings, upstream, tmp_path):
    upstream.default = echo_answers
    engines = [{**e, "base_url": "http://127.0.0.1:4000/"} if e["id"] == "or-jev-1.14" else e for e in ENGINES]
    cfg = tmp_path / "own.yaml"
    cfg.write_text(yaml.safe_dump({"engines": engines}), encoding="utf-8")
    settings.config_path = cfg
    reg = Registry(settings, Catalog(settings.probes_dir / "catalog.json"), transport=upstream.transport)
    await reg.get("or-jev-1.14").run(REQ, RunOptions())
    await reg.get("or-d1.1").run(REQ, RunOptions())
    assert [c["url"] for c in upstream.calls] == ["http://127.0.0.1:4000/api/alpha/decisions",
                                                  "https://openrouter.ai/api/alpha/decisions"]


def test_an_engine_root_that_would_leak_the_key_is_refused(tmp_path):
    cfg = tmp_path / "bad.yaml"
    cfg.write_text(yaml.safe_dump({"engines": [{"id": "or", "kind": "openrouter_decisions", "model": "m",
                                                "base_url": "http://gw.lan:4000"}]}), encoding="utf-8")
    with pytest.raises(ValueError, match="engine or: base_url"):
        load_config(cfg)
    # a systemone server's base_url is its endpoint on the LAN, and gets no OpenRouter key: untouched
    cfg.write_text(yaml.safe_dump({"engines": [{"id": "s", "kind": "systemone_http",
                                                "base_url": "http://192.168.1.5:11434/v1/systemone"}]}))
    assert load_config(cfg)[0].engines[0].base_url == "http://192.168.1.5:11434/v1/systemone"


async def test_the_catalog_reads_the_configured_root(tmp_path):
    seen = []

    def handler(request):
        seen.append(str(request.url).split("?")[0])
        return httpx.Response(200, json={"data": []})

    cat = Catalog(tmp_path / "catalog.json", transport=httpx.MockTransport(handler), base="https://gw.example.com/or")
    await cat.refresh()
    assert seen == ["https://gw.example.com/or/api/v1/models", "https://gw.example.com/or/api/v1/endpoints/zdr",
                    "https://gw.example.com/or/api/v1/models"]
