"""P0 spike: OpenRouter Decisions and chat completions (docs/DESIGN.md §7.2, §7.5, §13.2, §22 P0).

Without a key (public):
- GET /api/v1/models?output_modalities=decisions  -> tests/fixtures/openrouter/catalog_decisions.json
- GET /api/v1/endpoints/zdr, decision models only  -> tests/fixtures/openrouter/zdr_decisions.json
- GET /api/v1/models, LLMs that declare logprobs   -> tests/fixtures/openrouter/llm_logprobs.json (RQ-5)

With OPENROUTER_API_KEY (environment or the project's .env), for each decision model, always with
provider {zdr: true, data_collection: deny}:
- the core request (noul, choice, score), three repeats: latency, repeatability, usage.cost, dated `model`;
- the whip-insight adapter body (pointer pass: choice with null descriptions);
- types it may not serve: choice+multiple, text, number, date, box, an unknown type -> status and error body;
- choice 24/100/240 options, score 10 levels (probe steps 2-3);
- a bad key -> 401 body.
And one chat completion with logprobs to see `usage.cost` and `top_logprobs` (DESIGN §7.5 "проверить в P0").

Writes tests/fixtures/openrouter/<model-slug>/*.json. Keys never reach a file: only status, a header whitelist and
the response body are kept.

    .venv/bin/python scripts/spike/openrouter_spike.py            # public part, plus calls if a key is set
    .venv/bin/python scripts/spike/openrouter_spike.py --public-only
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
import statistics
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
REQ = ROOT / "tests" / "fixtures" / "requests"
OUT = ROOT / "tests" / "fixtures" / "openrouter"
BASE = "https://openrouter.ai"
MODELS = ["typesafe/jev-1.14", "liquid/d1.1", "~liquid/d1-latest"]
PROVIDER = {"zdr": True, "data_collection": "deny"}
KEEP_HEADERS = {"content-type", "x-generation-id", "openrouter-provider", "x-ratelimit-limit", "x-ratelimit-remaining"}
UA = {"User-Agent": "decision-models-playground/0.1 (spike)"}


def key_from_env() -> str | None:
    if os.environ.get("OPENROUTER_API_KEY"):
        return os.environ["OPENROUTER_API_KEY"]
    env = ROOT / ".env"
    if env.is_file():
        for line in env.read_text(encoding="utf-8").splitlines():
            m = re.match(r"\s*OPENROUTER_API_KEY\s*=\s*(.*)\s*$", line)
            if m and m.group(1).strip().strip("'\""):
                return m.group(1).strip().strip("'\"")
    return None


def load_json(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    data.pop("_comment", None)
    return data


def save(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def slug(model: str) -> str:
    return model.replace("/", "__").replace("~", "alias-")


def scrub(text: str, key: str | None) -> str:
    return text.replace(key, "<OPENROUTER_API_KEY>") if key else text


def public(client: httpx.Client) -> dict:
    found = {}
    r = client.get(f"{BASE}/api/v1/models", params={"output_modalities": "decisions"})
    r.raise_for_status()
    catalog = r.json()
    save(OUT / "catalog_decisions.json", catalog)
    ids = {m["id"] for m in catalog["data"]}
    found["catalog_ids"] = len(ids)
    found["catalog_models"] = {
        m["id"]: {
            "canonical_slug": m.get("canonical_slug"),
            "context_length": m.get("context_length"),
            "prompt_price": m["pricing"].get("prompt"),
            "input_modalities": m["architecture"].get("input_modalities"),
            "expiration_date": m.get("expiration_date"),
            "endpoints": m.get("endpoints"),
        }
        for m in catalog["data"]
        if m["id"] in MODELS
    }

    r = client.get(f"{BASE}/api/v1/endpoints/zdr")
    r.raise_for_status()
    zdr = [e for e in r.json()["data"] if e["model_id"] in ids]
    save(OUT / "zdr_decisions.json", {"source": f"{BASE}/api/v1/endpoints/zdr", "filter": "model_id in decision catalog", "data": zdr})
    found["zdr_decision_endpoints"] = {e["model_id"]: {"name": e["name"], "safety_retention": e["safety_retention"]} for e in zdr}

    r = client.get(f"{BASE}/api/v1/models")
    r.raise_for_status()
    llms = [
        {"id": m["id"], "prompt_price": m["pricing"].get("prompt"), "completion_price": m["pricing"].get("completion")}
        for m in r.json()["data"]
        if {"logprobs", "top_logprobs"} <= set(m.get("supported_parameters") or [])
    ]
    llms.sort(key=lambda m: float(m["prompt_price"] or 0))
    save(OUT / "llm_logprobs.json", {"source": f"{BASE}/api/v1/models", "count_all": len(r.json()["data"]), "with_logprobs": llms})
    found["llm_total"] = len(r.json()["data"])
    found["llm_with_logprobs"] = len(llms)
    return found


def call(client: httpx.Client, key: str, body: dict, path: str = "/api/alpha/decisions") -> dict:
    t0 = time.perf_counter()
    try:
        r = client.post(f"{BASE}{path}", json=body, headers={"Authorization": f"Bearer {key}"}, timeout=60)
    except httpx.HTTPError as e:
        return {"status": None, "error": f"{type(e).__name__}: {e}", "latency_ms": round((time.perf_counter() - t0) * 1000, 1)}
    ms = round((time.perf_counter() - t0) * 1000, 1)
    try:
        payload = r.json()
    except ValueError:
        payload = {"_text": scrub(r.text[:4000], key)}
    if isinstance(payload, dict) and "user_id" in payload:  # an error body names the account; fixtures are committed
        payload["user_id"] = "<user_id>"
    return {
        "status": r.status_code,
        "latency_ms": ms,
        "headers": {k: v for k, v in r.headers.items() if k.lower() in KEEP_HEADERS},
        "body": json.loads(scrub(json.dumps(payload, ensure_ascii=False), key)),
    }


def choice_request(n: int) -> dict:
    criteria = {f"o{i:03d}": f"Отдел номер {i}" for i in range(n)}
    criteria["o007"] = "Отдел возвратов: возврат денег за товар"
    return {"state": "Клиент: Хочу вернуть деньги за бракованный чайник.",
            "questions": {"dept": {"type": "choice", "instructions": "Какой отдел должен ответить клиенту?", "criteria": criteria}}}


def decisions(client: httpx.Client, key: str) -> dict:
    core = load_json(REQ / "core_ru.json")
    ext = load_json(REQ / "extensions_ru.json")
    wi = load_json(REQ / "whip_insight_systemone.json")
    summary: dict = {}
    for model in MODELS:
        d = OUT / slug(model)
        s: dict = {}

        def run(name: str, req: dict) -> dict:
            body = {"model": model, "state": req["state"], "questions": req["questions"], "provider": PROVIDER}
            res = call(client, key, body)
            save(d / f"{name}.json", {"request": body, **res})
            b = res.get("body") or {}
            return {
                "status": res["status"],
                "latency_ms": res["latency_ms"],
                "model": b.get("model"),
                "provider": b.get("provider"),
                "usage": b.get("usage"),
                "error": b.get("error"),
                "answer_types": {k: v.get("type") for k, v in (b.get("answers") or {}).items()},
            }

        s["core"] = [run(f"core_ru_{i}", core) for i in range(3)]
        oks = [r for r in s["core"] if r["status"] == 200]
        s["core_latency_median_ms"] = statistics.median([r["latency_ms"] for r in oks]) if oks else None
        s["whip_insight"] = run("whip_insight_systemone", {**wi})
        for qk, q in ext["questions"].items():
            s[f"ext_{qk}"] = run(f"ext_{qk}", {"state": ext["state"], "questions": {qk: q, "callback_agreed": core["questions"]["callback_agreed"]}})
        s["box"] = run("box", {"state": core["state"], "questions": {"logo": {"type": "box", "instructions": "Где логотип банка?"}}})
        s["unknown_type"] = run("unknown_type", {"state": core["state"], "questions": {"q": {"type": "predicate", "instructions": "Это жалоба?"}}})
        for n in (24, 100, 240, 255):
            s[f"choice_{n}"] = run(f"choice_{n}", choice_request(n))
        score10 = copy.deepcopy(core)
        score10["questions"] = {"anger": {"type": "score", "instructions": "Насколько клиент раздражён?", "criteria": [f"Уровень {i}" for i in range(10)]}}
        s["score_10"] = run("score_10", score10)
        summary[model] = s
        print(model, json.dumps({k: (v if not isinstance(v, list) else v[0]) for k, v in s.items()}, ensure_ascii=False)[:1500], flush=True)

    bad = call(client, "sk-or-v1-invalid", {"model": MODELS[0], "state": "x", "questions": {"q": {"type": "noul", "instructions": "?"}}})
    save(OUT / "error_401_bad_key.json", bad)
    summary["bad_key"] = {"status": bad["status"], "body": bad.get("body")}
    return summary


def chat(client: httpx.Client, key: str, model: str) -> dict:
    core = load_json(REQ / "core_ru.json")
    q = core["questions"]["call_topic"]
    letters = "ABC"
    options = "\n".join(f"{letters[i]}. {k}: {v}" for i, (k, v) in enumerate(q["criteria"].items()))
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": "Answer with a single capital letter: the option that fits best. No other text."},
            {"role": "user", "content": f"<state>\n{core['state']}\n</state>\n\nQuestion: {q['instructions']}\n{options}\n\nAnswer:"},
        ],
        "max_tokens": 1,
        "temperature": 0,
        "logprobs": True,
        "top_logprobs": 10,
        "provider": PROVIDER,
    }
    res = call(client, key, body, "/api/v1/chat/completions")
    save(OUT / "chat" / f"{slug(model)}.json", {"request": body, **res})
    b = res.get("body") or {}
    choice = (b.get("choices") or [{}])[0]
    return {
        "status": res["status"],
        "latency_ms": res["latency_ms"],
        "model": b.get("model"),
        "usage": b.get("usage"),
        "has_logprobs": bool((choice.get("logprobs") or {}).get("content")),
        "content": (choice.get("message") or {}).get("content"),
        "error": b.get("error"),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--public-only", action="store_true")
    ap.add_argument("--chat-model", default=None, help="LLM for the chat check; default: cheapest with logprobs among known ids")
    args = ap.parse_args()

    result: dict = {"run_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    with httpx.Client(headers=UA, timeout=30) as client:
        result["public"] = public(client)
        key = None if args.public_only else key_from_env()
        if key:
            result["decisions"] = decisions(client, key)
            llms = json.loads((OUT / "llm_logprobs.json").read_text())["with_logprobs"]
            ids = {m["id"] for m in llms}
            model = args.chat_model or next((m for m in ("openai/gpt-4.1-mini", "openai/gpt-4o-mini", "openai/gpt-4.1-nano") if m in ids), None)
            if model:
                result["chat"] = chat(client, key, model)
            else:
                result["chat"] = {"skipped": "no known cheap LLM with logprobs; pass --chat-model"}
        else:
            result["decisions"] = {"skipped": "no OPENROUTER_API_KEY in the environment or .env"}
    save(OUT / "_measurements.json", result)
    print(json.dumps(result.get("public"), ensure_ascii=False, indent=1)[:3000])
    return 0


if __name__ == "__main__":
    sys.exit(main())
