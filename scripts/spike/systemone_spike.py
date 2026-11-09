"""P0 spike: a running `/v1/systemone` server (Ollama, llama.cpp; later the playground's own compat server).

WI-1 (P7.2), docs/TASK.md §8.1. Sends the core request, the whip-insight adapter body and one request per extended type,
then records status, latency and body under tests/fixtures/systemone/<name>/.

    .venv/bin/python scripts/spike/systemone_spike.py --name ollama-d1-2b \
        --url http://127.0.0.1:11434/v1/systemone --model d1:2b
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
REQ = ROOT / "tests" / "fixtures" / "requests"


def load_json(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    data.pop("_comment", None)
    return data


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True)
    ap.add_argument("--url", required=True)
    ap.add_argument("--model", default=None)
    ap.add_argument("--token", default=None, help="Bearer token, never written to a file")
    ap.add_argument("--repeats", type=int, default=5)
    args = ap.parse_args()

    out = ROOT / "tests" / "fixtures" / "systemone" / args.name
    out.mkdir(parents=True, exist_ok=True)
    headers = {"Authorization": f"Bearer {args.token}"} if args.token else {}

    def post(name: str, req: dict) -> dict:
        body = {k: v for k, v in req.items() if k != "model"}
        if args.model:
            body["model"] = args.model
        t0 = time.perf_counter()
        try:
            r = httpx.post(args.url, json=body, headers=headers, timeout=120)
            res = {"status": r.status_code, "body": r.json() if r.content else None}
        except (httpx.HTTPError, ValueError) as e:
            res = {"status": None, "error": f"{type(e).__name__}: {e}"}
        res["latency_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        (out / f"{name}.json").write_text(json.dumps({"request": body, **res}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return res

    meas: dict = {"name": args.name, "url": args.url, "model": args.model, "run_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    core = load_json(REQ / "core_ru.json")
    first = post("core_ru", core)
    meas["core_cold"] = {"status": first["status"], "latency_ms": first["latency_ms"]}
    lat = [post("core_ru_repeat", core)["latency_ms"] for _ in range(args.repeats)]
    meas["core_warm_median_ms"] = statistics.median(lat)
    meas["whip_insight"] = post("whip_insight_systemone", load_json(REQ / "whip_insight_systemone.json"))["status"]
    ext = load_json(REQ / "extensions_ru.json")
    for qk, q in ext["questions"].items():
        r = post(f"ext_{qk}", {"state": ext["state"], "questions": {qk: q}})
        meas[f"ext_{qk}"] = {"status": r["status"], "body": r.get("body")}
    (out / "_measurements.json").write_text(json.dumps(meas, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(meas, ensure_ascii=False, indent=1)[:4000])
    return 0


if __name__ == "__main__":
    sys.exit(main())
