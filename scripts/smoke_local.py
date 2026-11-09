"""Opt-in smoke test of the real local path (review P2): real mlx-vlm, real models, real tokenizer. Not run by
`make test` (it loads 2-2.5 GB models); run it on the Mac and hand its JSON to the next review.

    .venv/bin/python scripts/smoke_local.py [--out docs/reviews/p2-smoke.json]

Checks, through the same objects the app uses (make_local, MlxLocal):
1. d1-2B answers the core request; snapshot dated; timings split.
2. d1-Omni evicts d1-2B and answers an audio question from a synthetic clip.
3. Review P2 guards on the real stack: a media dict in `instructions` is unsupported and never reaches the model; a
   nested JSON state that is short when compact is refused as too long by the real tokenizer; a too-long request
   does not evict the loaded model.
4. Unload returns MLX memory to 0.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.catalog import Catalog  # noqa: E402
from app.local_models import make_local  # noqa: E402
from app.registry import Registry, load_config  # noqa: E402
from app.schemas import DecisionRequest, RunOptions  # noqa: E402
from app.settings import get_settings  # noqa: E402


def load(rel: str) -> dict:
    d = json.loads((ROOT / "tests" / "fixtures" / rel).read_text(encoding="utf-8"))
    d.pop("_comment", None)
    return d


def brief(r) -> dict:
    return {"status": r.status, "request_failure": r.request_failure, "detail": r.detail, "snapshot": r.snapshot,
            "input_tokens": r.usage.input_tokens,
            "latency_ms": r.latency_ms, "timings": r.timings, "question_failures": r.question_failures,
            "answers": {k: [a.top, round(a.top_p, 3) if a.top_p is not None else None] for k, a in r.answers.items()}}


async def main(out: Path) -> int:
    settings = get_settings()
    config, _ = load_config(settings.config_path)
    local = make_local(settings, config)
    reg = Registry(settings, Catalog(settings.probes_dir / "catalog.json"), config=config, local=local)
    d2b, omni = reg.get("mlx-d1-2b"), reg.get("mlx-d1-omni")
    log: dict = {"run_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "checks": {}}
    ok = True

    def check(name: str, passed: bool, data) -> None:
        nonlocal ok
        ok &= passed
        log["checks"][name] = {"passed": passed, "data": data}
        print(("PASS " if passed else "FAIL ") + name, flush=True)

    core = DecisionRequest.model_validate(load("requests/core_ru.json"))
    r = await d2b.run(core, RunOptions())
    import re

    check("1 d1-2B answers the core request with a dated snapshot",
          r.status == "ok" and re.search(r"-\d{8}$", r.snapshot or "") is not None, brief(r))

    clip = ROOT / "tests" / "fixtures" / "audio" / "answering_machine_ru.wav"
    media_dir = settings.data_dir / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy(clip, media_dir / clip.name)
    import yaml

    call_kind = yaml.safe_load((ROOT / "presets" / "call_kind.yaml").read_text(encoding="utf-8"))["questions"]
    audio_req = DecisionRequest(state=None, questions=call_kind, media=[{"type": "audio", "path": clip.name}])
    r = await omni.run(audio_req, RunOptions())
    check("2a d1-Omni evicts d1-2B and answers", r.status == "ok" and local.current.repo == omni.repo
          and r.timings["load_ms"] > 0, brief(r))
    # the clip really reached the model: ≈ 12.5 tokens a second of audio (SPIKE §2.5) over the same questions alone
    bare = await omni.run(DecisionRequest(state="", questions=call_kind), RunOptions())
    added = (r.usage.input_tokens or 0) - (bare.usage.input_tokens or 0)
    check("2b the 10.6 s clip added its audio tokens", 80 <= added <= 250,
          {"with_audio": r.usage.input_tokens, "without": bare.usage.input_tokens, "added": added})

    spoken = DecisionRequest.model_validate({"state": "x", "questions": {
        "spoken": {"type": "noul", "instructions": {"type": "image", "path": "/etc/hosts"}},
        "ok": {"type": "noul", "instructions": "Это тест?"}}})
    r = await omni.run(spoken, RunOptions())
    check("3a a media dict in instructions is unsupported", r.question_failures == {"spoken": "unsupported"}, brief(r))

    def nested(d):
        return [nested(d - 1)] if d else 0

    loads_before = local.states[omni.repo].calls
    deep = DecisionRequest(state={"s": [nested(20) for _ in range(762)]}, questions=core.questions)
    r = await d2b.run(deep, RunOptions())
    check("3b a nested state is too long by the real tokenizer, and d1-2B was not loaded for it",
          r.request_failure == "too_long" and local.current.repo == omni.repo, {"detail": r.detail})
    check("3c the refused request did not touch the loaded model", local.states[omni.repo].calls == loads_before, {})

    unloaded = await local.unload()
    import mlx.core as mx

    mem = round(mx.get_active_memory() / 1024**3, 3)
    check("4 unload returns MLX memory to 0", unloaded and mem == 0.0, {"active_gb": mem})
    await local.aclose()

    log["passed"] = ok
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(log, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"{'ALL PASSED' if ok else 'FAILED'} → {out}")
    return 0 if ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=ROOT / "docs" / "reviews" / "p2-smoke.json")
    sys.exit(asyncio.run(main(ap.parse_args().out)))
