"""P0 spike: a local d1 model through mlx-vlm (docs/DESIGN.md §7.3, §8, §22 P0).

Measures, for one model per process (the Mac holds one model, §8):
- the load call and its time, memory after load (MLX active/peak, process max RSS);
- `model.system_one` answers and latency for the System One types, the x-extensions, the whip-insight request;
- the validation errors for an unknown type and bad criteria (what the compat server maps to 400, §9.1);
- choice 24/100/240 options and score 10 levels (probe steps 2-3, §13.1);
- audio on d1-Omni (call_kind from synthetic clips, 4-30 s);
- a growing state with a fact at the end (reads_full_context, memory growth), stopped by a memory guard;
- unload: memory after `del` + `mx.clear_cache()`.

Writes tests/fixtures/mlx/<engine_id>/*.json (responses, no keys) and _measurements.json.

    .venv/bin/python scripts/spike/mlx_spike.py --engine mlx-d1-2b --repo LiquidAI/d1-2B-MLX-8bit
    .venv/bin/python scripts/spike/mlx_spike.py --engine mlx-d1-omni --repo LiquidAI/d1-Omni-2B-MLX-8bit --audio
"""

from __future__ import annotations

import argparse
import gc
import json
import platform
import resource
import statistics
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REQ = ROOT / "tests" / "fixtures" / "requests"
AUDIO = ROOT / "tests" / "fixtures" / "audio"
GB = 1024**3


def load_json(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    data.pop("_comment", None)
    return data


def rss_gb() -> float:
    # macOS reports ru_maxrss in bytes
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / GB


def mem(mx) -> dict:
    return {
        "active_gb": round(mx.get_active_memory() / GB, 3),
        "peak_gb": round(mx.get_peak_memory() / GB, 3),
        "cache_gb": round(mx.get_cache_memory() / GB, 3),
        "max_rss_gb": round(rss_gb(), 3),
    }


def snapshot_of(repo: str) -> dict:
    """`<repo>-<YYYYMMDD of the HF revision>` (DESIGN §7.3) from the cached revision and its commit date."""
    from huggingface_hub import HfApi, snapshot_download

    path = Path(snapshot_download(repo, local_files_only=True))
    sha = path.name
    out = {"repo": repo, "revision": sha, "path": str(path), "disk_gb": None, "commit_date": None}
    out["disk_gb"] = round(sum(f.stat().st_size for f in path.rglob("*") if f.is_file()) / GB, 3)
    try:
        info = HfApi().model_info(repo, revision=sha)
        when = info.last_modified
        out["commit_date"] = when.isoformat() if when else None
        out["snapshot"] = f"{repo}-{when:%Y%m%d}" if when else None
    except Exception as e:  # offline: the snapshot stays unknown, not invented
        out["commit_date_error"] = f"{type(e).__name__}: {e}"
        out["snapshot"] = None
    return out


def timed(fn, *args, **kwargs):
    t0 = time.perf_counter()
    result = fn(*args, **kwargs)
    return result, round((time.perf_counter() - t0) * 1000, 1)


def choice_request(n: int) -> dict:
    criteria = {f"o{i:03d}": f"Отдел номер {i}" for i in range(n)}
    criteria["o007"] = "Отдел возвратов: возврат денег за товар"
    return {
        "state": "Клиент: Хочу вернуть деньги за бракованный чайник.",
        "questions": {"dept": {"type": "choice", "instructions": "Какой отдел должен ответить клиенту?", "criteria": criteria}},
    }


SCORE10 = {
    "state": "Клиент: Это уже третий раз! Если сегодня не вернёте деньги, иду в суд и пишу во все соцсети.",
    "questions": {
        "anger": {
            "type": "score",
            "instructions": "Насколько клиент раздражён?",
            "criteria": [f"Уровень {i}: " + d for i, d in enumerate(
                ["спокоен", "почти спокоен", "слегка недоволен", "недоволен", "заметно недоволен",
                 "раздражён", "сильно раздражён", "зол", "очень зол", "в ярости"])],
        }
    },
}

BAD = {
    "unknown_type": {"q": {"type": "predicate", "instructions": "Это жалоба?"}},
    "bool_type": {"q": {"type": "bool", "instructions": "Это жалоба?"}},
    "score_one_level": {"q": {"type": "score", "instructions": "Насколько?", "criteria": ["один"]}},
    "score_eleven_levels": {"q": {"type": "score", "instructions": "Насколько?", "criteria": [str(i) for i in range(11)]}},
    "choice_list_criteria": {"q": {"type": "choice", "instructions": "Какой?", "criteria": ["a", "b"]}},
    "box_without_image": {"q": {"type": "box", "instructions": "Где логотип?"}},
}

FILLER = (
    "Оператор: Уточните, пожалуйста, адрес доставки. Клиент: Улица Ленина, дом пять, квартира двенадцать. "
    "Оператор: Спасибо, курьер приедет с десяти до двух. Клиент: Хорошо, а можно оплатить картой? "
    "Оператор: Да, у курьера есть терминал. Клиент: Отлично. Оператор: Ещё вопросы есть? Клиент: Пока нет. "
)
NEEDLE = "Клиент: И последнее — запишите кодовое слово для доступа к счёту: «малиновый закат»."
NEEDLE_Q = {
    "codeword_given": {
        "type": "noul",
        "instructions": "Назвал ли клиент кодовое слово для доступа к счёту?",
        "criteria": {"true": "Клиент продиктовал кодовое слово.", "false": "Кодовое слово не прозвучало."},
    }
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", required=True)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--audio", action="store_true", help="the model takes audio (d1-Omni)")
    ap.add_argument("--repeats", type=int, default=10)
    ap.add_argument("--long-steps", default="2000,8000,16000,32000,64000", help="target state tokens, ascending")
    ap.add_argument("--max-peak-gb", type=float, default=14.0, help="stop growing the state above this MLX peak")
    args = ap.parse_args()

    out_dir = ROOT / "tests" / "fixtures" / "mlx" / args.engine
    out_dir.mkdir(parents=True, exist_ok=True)
    meas: dict = {"engine": args.engine, "repo": args.repo, "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}

    def save(name: str, payload) -> None:
        (out_dir / f"{name}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    t0 = time.perf_counter()
    import mlx.core as mx
    import mlx_vlm
    from mlx_vlm import load

    meas["import_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    meas["versions"] = {
        "python": platform.python_version(),
        "mlx": mx.__version__,
        "mlx_vlm": mlx_vlm.__version__,
        "machine": platform.machine(),
        "macos": platform.mac_ver()[0],
    }
    meas["model"] = snapshot_of(args.repo)
    meas["memory_before_load"] = mem(mx)

    mx.reset_peak_memory()
    (model, processor), meas["load_ms"] = timed(load, meas["model"]["path"])
    meas["memory_after_load"] = mem(mx)
    meas["model_class"] = f"{type(model).__module__}.{type(model).__name__}"
    meas["decision_types"] = list(getattr(model, "decision_types", ()))
    meas["decision_media"] = list(getattr(model, "decision_media", ()))
    meas["calibration_loaded"] = getattr(getattr(model, "engine", None), "calibration", None) is not None
    print(f"loaded {args.repo} in {meas['load_ms']} ms; {meas['memory_after_load']}", flush=True)

    def ask(name: str, req: dict, media=None, **kw) -> dict:
        res, ms = timed(model.system_one, req.get("state"), req["questions"], media, **kw)
        save(name, {"request": name, "kwargs": kw, "media": media, "latency_ms": ms, "response": res})
        return {"latency_ms": ms, "input_tokens": res["usage"]["input_tokens"]}

    # --- System One types: cold, then warm repeats ---------------------------------------------------------------
    core = load_json(REQ / "core_ru.json")
    meas["core_cold"] = ask("core_ru", core)
    lat = []
    first = model.system_one(core["state"], core["questions"])
    max_dp = 0.0
    for _ in range(args.repeats):
        r, ms = timed(model.system_one, core["state"], core["questions"])
        lat.append(ms)
        for qk, a in r["answers"].items():
            b = first["answers"][qk]
            if "noul" in a:
                max_dp = max(max_dp, abs(a["noul"] - b["noul"]))
            for ok, p in (a.get("probabilities") or {}).items():
                max_dp = max(max_dp, abs(p - b["probabilities"][ok]))
    meas["core_warm"] = {
        "n": len(lat),
        "median_ms": round(statistics.median(lat), 1),
        "min_ms": min(lat),
        "max_ms": max(lat),
        "repeat_max_abs_dp": max_dp,
    }
    meas["core_score_mean"] = ask("core_ru_score_mean", core, score_readout="mean")
    meas["core_set_size_1"] = ask("core_ru_set_size_1", core, set_size=1)
    meas["memory_after_core"] = mem(mx)

    # --- x-extensions ---------------------------------------------------------------------------------------------
    ext = load_json(REQ / "extensions_ru.json")
    try:
        meas["extensions"] = ask("extensions_ru", ext)
    except Exception as e:
        meas["extensions"] = {"error": f"{type(e).__name__}: {e}"}
    for qk, q in ext["questions"].items():  # each alone: which one fails, if any
        try:
            meas.setdefault("extensions_each", {})[qk] = ask(f"extensions_ru_{qk}", {"state": ext["state"], "questions": {qk: q}})
        except Exception as e:
            meas.setdefault("extensions_each", {})[qk] = {"error": f"{type(e).__name__}: {e}"}

    # --- whip-insight systemone request ---------------------------------------------------------------------------
    wi = load_json(REQ / "whip_insight_systemone.json")
    meas["whip_insight"] = ask("whip_insight_systemone", wi)

    # --- validation errors ----------------------------------------------------------------------------------------
    errors = {}
    for name, case in BAD.items():
        try:
            model.system_one("Клиент: жалуюсь.", {"q": case["q"]})
            errors[name] = {"raised": None}
        except Exception as e:
            errors[name] = {"raised": type(e).__name__, "message": str(e)}
    try:
        model.system_one("x", {})
        errors["no_questions"] = {"raised": None}
    except Exception as e:
        errors["no_questions"] = {"raised": type(e).__name__, "message": str(e)}
    meas["validation_errors"] = errors
    save("validation_errors", errors)

    # --- probe steps 2-3 ------------------------------------------------------------------------------------------
    for n in (24, 100, 240, 255, 300):
        try:
            meas[f"choice_{n}"] = ask(f"choice_{n}", choice_request(n))
        except Exception as e:
            meas[f"choice_{n}"] = {"error": f"{type(e).__name__}: {e}"}
    meas["score_10"] = ask("score_10", SCORE10)

    # --- audio (d1-Omni) ------------------------------------------------------------------------------------------
    if args.audio:
        import yaml

        call_kind = yaml.safe_load((ROOT / "presets" / "call_kind.yaml").read_text(encoding="utf-8"))["questions"]
        meas["audio"] = {}
        for clip in sorted(AUDIO.glob("*.wav")):
            media = [{"type": "audio", "path": str(clip.relative_to(ROOT))}]
            try:
                mx.reset_peak_memory()
                meas["audio"][clip.stem] = ask(f"audio_{clip.stem}", {"state": None, "questions": call_kind}, media)
                meas["audio"][clip.stem]["peak_gb"] = round(mx.get_peak_memory() / GB, 3)
            except Exception as e:
                meas["audio"][clip.stem] = {"error": f"{type(e).__name__}: {e}"}
        # warm latency on the 10 s and 30 s clips
        for stem in ("answering_machine_ru", "conversation_30s_ru"):
            media = [{"type": "audio", "path": str((AUDIO / f"{stem}.wav").relative_to(ROOT))}]
            lat = [timed(model.system_one, None, call_kind, media)[1] for _ in range(5)]
            meas["audio"][f"{stem}_warm_median_ms"] = round(statistics.median(lat), 1)
        # audio together with a text state, as the transcript plus the recording
        media = [{"type": "audio", "path": "tests/fixtures/audio/answering_machine_ru.wav"}]
        meas["audio"]["with_text_state"] = ask(
            "audio_with_text_state",
            {"state": "Транскрипт: Здравствуйте, оставьте сообщение после сигнала.", "questions": call_kind},
            media,
        )
        # a spoken question: instructions as an audio entry
        try:
            spoken = {"kind": {"type": "noul", "instructions": {"type": "audio", "path": "tests/fixtures/audio/network_message_ru.wav"}}}
            meas["audio"]["spoken_question"] = ask("audio_spoken_question", {"state": "Абонент недоступен.", "questions": spoken})
        except Exception as e:
            meas["audio"]["spoken_question"] = {"error": f"{type(e).__name__}: {e}"}
        # media as a decoded waveform
        import wave

        import numpy as np

        with wave.open(str(AUDIO / "ivr_ru.wav"), "rb") as f:
            clip = np.frombuffer(f.readframes(f.getnframes()), dtype=np.int16).astype(np.float32) / 32768
        res, ms = timed(model.system_one, None, call_kind, [{"type": "audio", "audio": clip, "sampling_rate": 16000}])
        meas["audio"]["waveform_input"] = {"latency_ms": ms, "top": res["answers"]["call_kind"]["choice"]}

    # --- growing state: memory and reading the end ----------------------------------------------------------------
    meas["long_state"] = []
    per_char = None
    for target in [int(x) for x in args.long_steps.split(",") if x]:
        if per_char is None:
            probe = model.system_one(FILLER * 4, NEEDLE_Q)["usage"]["input_tokens"]
            per_char = probe / len(FILLER * 4)
        reps = max(1, int(target / per_char / len(FILLER)))
        state = FILLER * reps + NEEDLE
        gc.collect()
        mx.clear_cache()
        mx.reset_peak_memory()
        try:
            res, ms = timed(model.system_one, state, NEEDLE_Q)
            row = {
                "target_tokens": target,
                "chars": len(state),
                "input_tokens": res["usage"]["input_tokens"],
                "latency_ms": ms,
                "peak_gb": round(mx.get_peak_memory() / GB, 3),
                "codeword_given_p": res["answers"]["codeword_given"]["noul"],
            }
        except Exception as e:
            row = {"target_tokens": target, "chars": len(state), "error": f"{type(e).__name__}: {e}"}
        meas["long_state"].append(row)
        print("long", row, flush=True)
        save("long_state", meas["long_state"])
        if "error" in row:
            break
        # the dense set-attention mask grows with T^2: project the next step before running it
        grow = (row["peak_gb"] - meas["memory_after_load"]["active_gb"]) * 4
        if row["peak_gb"] + grow > args.max_peak_gb:
            meas["long_state_stopped"] = f"next step projected at {row['peak_gb'] + grow:.1f} GB > {args.max_peak_gb} GB"
            break

    # --- unload ---------------------------------------------------------------------------------------------------
    del model, processor
    gc.collect()
    mx.clear_cache()
    meas["memory_after_unload"] = mem(mx)
    meas["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    save("_measurements", meas)
    print(json.dumps({k: v for k, v in meas.items() if k not in ("validation_errors",)}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(1)
