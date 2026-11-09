from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest
import yaml

from app.engines.base import BaseEngine
from app.settings import Settings

FIX = Path(__file__).parent / "fixtures"
OR_FIX = FIX / "openrouter"

ENGINES = [
    {"id": "or-jev-1.14", "kind": "openrouter_decisions", "model": "typesafe/jev-1.14", "max_request_bytes": 32000},
    {"id": "or-d1.1", "kind": "openrouter_decisions", "model": "liquid/d1.1"},
    {"id": "or-d1-latest", "kind": "openrouter_decisions", "model": "~liquid/d1-latest"},
    {"id": "or-no-zdr", "kind": "openrouter_decisions", "model": "liquid/d1.1", "zdr": False},
    {"id": "mlx-d1-2b", "kind": "mlx_local", "repo": "LiquidAI/d1-2B-MLX-8bit"},
]


def load(rel: str) -> dict:
    data = json.loads((FIX / rel).read_text(encoding="utf-8"))
    if isinstance(data, dict):
        data.pop("_comment", None)
    return data


def or_body(model_dir: str, name: str) -> dict:
    """A P0 response body recorded from OpenRouter."""
    return load(f"openrouter/{model_dir}/{name}.json")["body"]


@pytest.fixture(autouse=True)
def _no_retry_wait(monkeypatch):
    monkeypatch.setattr(BaseEngine, "retry_delay_s", 0.0)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    cfg = tmp_path / "config.yaml"
    cfg.write_text(yaml.safe_dump({"engines": ENGINES}), encoding="utf-8")
    s = Settings(_env_file=None, data_dir=tmp_path, config_path=cfg, openrouter_api_key="sk-or-v1-test", daily_budget_usd=1.0)
    s.ensure_dirs()
    catalog = {
        "fetched_at": time.time(),
        "decisions": load("openrouter/catalog_decisions.json")["data"],
        "zdr": load("openrouter/zdr_decisions.json")["data"],
    }
    (tmp_path / "probes" / "catalog.json").write_text(json.dumps(catalog), encoding="utf-8")
    return s


class Upstream:
    """A fake OpenRouter: per model a queue of answers (a dict body for 200, an httpx.Response, or an exception)."""

    def __init__(self):
        self.queues: dict[str, list] = {}
        self.calls: list[dict] = []
        self.default: Callable[[dict], httpx.Response] | None = None

    def push(self, model: str, *answers) -> Upstream:
        self.queues.setdefault(model, []).extend(answers)
        return self

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.calls.append({"url": str(request.url), "body": body, "auth": request.headers.get("authorization")})
        queue = self.queues.get(body.get("model"), [])
        if not queue:
            if self.default:
                return self.default(body)
            return httpx.Response(500, json={"error": {"message": "no scripted answer", "code": 500}})
        a = queue.pop(0)
        if isinstance(a, Exception):
            raise a
        if isinstance(a, httpx.Response):
            return a
        if callable(a):
            return a(body)
        return httpx.Response(200, json=a)

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)


@pytest.fixture
def upstream() -> Upstream:
    return Upstream()


def echo_answers(body: dict, model: str = "liquid/d1.1-20261027") -> httpx.Response:
    """A valid System One answer for whatever questions were sent."""
    answers = {}
    for k, q in body["questions"].items():
        if q["type"] == "noul":
            answers[k] = {"type": "noul", "noul": 0.8}
        elif q["type"] == "choice":
            ids = list(q["criteria"])
            p = {i: 0.0 for i in ids}
            p[ids[0]] = 1.0
            answers[k] = {"type": "choice", "choice": ids[0], "probabilities": p, "confidence": 1}
        else:
            n = len(q["criteria"])
            p = {str(i): (1.0 if i == n - 1 else 0.0) for i in range(n)}
            answers[k] = {"type": "score", "score": n - 1, "probabilities": p, "confidence": 1,
                          "legend": {str(i): c for i, c in enumerate(q["criteria"])}}
    return httpx.Response(200, json={"id": "gen-dec-1", "model": model, "provider": "Liquid", "answers": answers,
                                     "usage": {"input_tokens": 100, "output_tokens": 0, "cost": 0.000002}})


def parse_sse(text: str) -> list[tuple[str, dict]]:
    out = []
    for frame in text.strip().split("\n\n"):
        m = re.match(r"event: (\w+)\ndata: (.*)", frame, re.S)
        if m:
            out.append((m.group(1), json.loads(m.group(2))))
    return out
