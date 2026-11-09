from __future__ import annotations

import json
import re
import threading
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
    {"id": "mlx-d1-2b", "kind": "mlx_local", "repo": "LiquidAI/d1-2B-MLX-8bit", "context_tokens": 32768},
    {"id": "mlx-d1-omni", "kind": "mlx_local", "repo": "LiquidAI/d1-Omni-2B-MLX-8bit", "audio": True},
    {"id": "ollama-d1-2b", "kind": "systemone_http", "base_url": "http://127.0.0.1:11434/v1/systemone"},
]
UI = "http://127.0.0.1:8790"  # the only Host the UI trusts besides localhost (review P1: no testserver)
D1_2B = "LiquidAI/d1-2B-MLX-8bit"
D1_OMNI = "LiquidAI/d1-Omni-2B-MLX-8bit"


def load(rel: str) -> dict:
    data = json.loads((FIX / rel).read_text(encoding="utf-8"))
    if isinstance(data, dict):
        data.pop("_comment", None)
    return data


def or_body(model_dir: str, name: str) -> dict:
    """A P0 response body recorded from OpenRouter."""
    return load(f"openrouter/{model_dir}/{name}.json")["body"]


ORIGINAL_RETRY_DELAY = BaseEngine.retry_delay_s


@pytest.fixture(autouse=True)
def _no_retry_wait(monkeypatch):
    monkeypatch.setattr(BaseEngine, "retry_delay_s", 0.0)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    cfg = tmp_path / "config.yaml"
    cfg.write_text(yaml.safe_dump({"engines": ENGINES, "compat": {"default_model": "mlx-d1-2b"},
                                   "local": {"idle_unload_s": 600}}), encoding="utf-8")
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


# --- a fake MLX backend for the local model manager -------------------------------------------------------------------


class FakeTokenizer:
    def encode(self, text: str, add_special_tokens: bool = False) -> list[int]:
        return list(range(max(1, len(text) // 2)))  # about 2 characters a token


def mlx_check(questions: dict) -> None:
    """mlx-vlm's own checks, so the fakes refuse what the real model refuses (review P2: lenient fakes hid bugs).
    A dict `instructions` passes check_question in mlx-vlm (it becomes a media path): the fake flags that too."""
    from mlx_vlm.models.d1_lfm3_vl.prompt import as_question, check_question

    for name, q in questions.items():
        if not isinstance(q.get("instructions"), str):
            raise AssertionError(f"question {name!r}: a non-text instructions reached the model (a media path)")
        try:
            check_question(q)
            as_question(q)
        except ValueError as e:
            raise ValueError(f"question {name!r}: {e}") from None


class FakeModel:
    """Answers like d1's system_one (System One names plus the x-extensions), from the questions it gets."""

    def __init__(self, repo: str, backend: FakeBackend):
        self.repo, self.backend = repo, backend

    def system_one(self, state, questions, media=None, score_readout="median", set_size=None):
        self.backend.thread_ids.add(threading.get_ident())
        mlx_check(questions)
        self.backend.calls.append({"repo": self.repo, "state": state, "questions": questions, "media": media,
                                   "score_readout": score_readout})
        if self.backend.call_delay:
            time.sleep(self.backend.call_delay)
        if self.backend.call_error:
            raise self.backend.call_error
        answers = {}
        for k, q in questions.items():
            t = q["type"]
            if t == "noul":
                answers[k] = {"type": "noul", "noul": 0.9}
            elif t == "choice" and q.get("multiple"):
                ids = list(q["criteria"])
                answers[k] = {"type": "choice", "choices": ids[:1], "probabilities": {i: (0.8 if j == 0 else 0.1) for j, i in enumerate(ids)}}
            elif t == "choice":
                ids = list(q["criteria"])
                p = {i: 0.1 / (len(ids) - 1) for i in ids[1:]} | {ids[0]: 0.9}
                answers[k] = {"type": "choice", "choice": ids[0], "confidence": 0.9, "probabilities": {i: p[i] for i in ids}}
            elif t == "score":
                n = len(q["criteria"])
                p = {str(i): (0.7 if i == n - 1 else 0.3 / (n - 1)) for i in range(n)}
                mean = sum(i * v for i, v in enumerate(p.values()))
                answers[k] = {"type": "score", "score": mean if score_readout == "mean" else float(n - 1), "confidence": 0.7,
                              "probabilities": p, "legend": {str(i): c for i, c in enumerate(q["criteria"])}}
            elif t == "number" and "range" in q:
                answers[k] = {"type": "number", "number": 350000.0, "interval": [300000.0, 400000.0], "unit": q.get("unit")}
            else:
                answers[k] = {"type": t, t: "А-48213" if t == "text" else None, "span": [76, 83], "found": 0.99,
                              **({} if t == "text" else {"text": "А-48213"})}
        return {"answers": answers, "usage": {"input_tokens": 400, "output_tokens": 0}}


class FakeBackend:
    def __init__(self, downloaded=(D1_2B,)):
        self.downloaded = set(downloaded)
        self.loads: list[str] = []
        self.unloads: list[str] = []
        self.calls: list[dict] = []
        self.thread_ids: set[int] = set()
        self.load_error: Exception | None = None
        self.load_delay = 0.0
        self.call_delay = 0.0
        self.call_error: Exception | None = None
        self.counted: list[str] = []  # every row text the precheck measured
        self.tokenizer_error: Exception | None = None
        self.tokens_per_char = 0  # 0: about 2 characters a token; 2: CJK/emoji-like text, 2 tokens a character

    def load(self, repo, warm_audio):
        from app.local_models import Loaded, LoadError

        self.thread_ids.add(threading.get_ident())
        if self.load_delay:
            time.sleep(self.load_delay)
        if self.load_error:
            raise self.load_error
        if repo not in self.downloaded:
            raise LoadError(f"{repo} is not downloaded")
        self.loads.append(repo)
        processor = type("P", (), {"tokenizer": FakeTokenizer()})()
        return Loaded(repo=repo, model=FakeModel(repo, self), processor=processor, path=f"/fake/{repo}",
                      revision="abc123", snapshot=f"{repo}-20261105", loaded_at=time.time(), load_ms=5)

    def unload(self, loaded):
        self.thread_ids.add(threading.get_ident())
        self.unloads.append(loaded.repo)

    def memory_gb(self):
        return 2.0

    def disk_gb(self, repo):
        return 2.08 if repo in self.downloaded else None

    def count_tokens(self, repo, text):
        self.counted.append(text)
        if self.tokenizer_error:
            raise self.tokenizer_error
        return len(text) * self.tokens_per_char if self.tokens_per_char else len(FakeTokenizer().encode(text))

    def set_size(self, repo):
        return 16  # d1-2B's and d1-Omni's config.json

    def validate(self, questions):
        mlx_check(questions)


@pytest.fixture
def fake_backend(monkeypatch) -> FakeBackend:
    # the availability checks look at the platform and mlx-vlm: make them pass on any machine
    import app.engines.mlx_local as mlx_local

    monkeypatch.setattr(mlx_local.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(mlx_local.platform, "machine", lambda: "arm64")
    monkeypatch.setattr(mlx_local, "mlx_vlm_version", lambda: (0, 7, 12))
    return FakeBackend()


@pytest.fixture
def local(fake_backend):
    from app.local_models import LocalModels

    return LocalModels(fake_backend, idle_unload_s=600)


def parse_sse(text: str) -> list[tuple[str, dict]]:
    out = []
    for frame in text.strip().split("\n\n"):
        m = re.match(r"event: (\w+)\ndata: (.*)", frame, re.S)
        if m:
            out.append((m.group(1), json.loads(m.group(2))))
    return out
