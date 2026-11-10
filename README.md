# Decision Models Playground

**English** | [Русский](README.ru.md)

A local test bench for decision models. One call and one set of questions run through several models at
once; their answers (probabilities, latency, cost, model snapshot) appear side by side. The playground
also runs a local `POST /v1/systemone` server, through which the whip-insight stand connects d1 models
running on the Mac.

> **Status (2026-11-09).** Increments P0–P6 are done ([TASK 001](docs/TASK.md)):
>
> - Workbench, history and daily budget with OpenRouter cloud models and local d1 (MLX);
> - compat server `/v1/systemone` for whip-insight;
> - capability probe and the "model × capability" matrix;
> - labelled datasets (whip-insight tags, synthetic dialogues, own files) with a calibration report and
>   a threshold for a whip-insight tag;
> - stability experiments (wording, option order, repeats);
> - research modes: turn pointer, extraction with Russian normalization, `multi` with a threshold, audio
>   for d1-Omni.
>
> Next: the `systemone_http` engine (Ollama, llama.cpp) and the LLM baseline — work-item
> [WI-1](docs/backlog/wi-1-p7-systemone-http-and-llm-baseline.md) in the [backlog](docs/BACKLOG.md).

## Contents

1. [What it is for](#what-it-is-for)
2. [Engines](#engines)
3. [Requirements](#requirements)
4. [Other platforms](#other-platforms)
5. [Quick start](#quick-start)
6. [Using it](#using-it)
7. [Make targets](#make-targets)
8. [Configuration](#configuration)
9. [Data, privacy and licenses](#data-privacy-and-licenses)
10. [Relation to whip-insight](#relation-to-whip-insight)
11. [Documentation](#documentation)
12. [Project layout](#project-layout)

## What it is for

A decision model (Jev by TypeSafe, d1 by Liquid AI, Microsoft-Decision-1 and others) answers closed
questions with probabilities and generates no text. To decide which model whip-insight can trust with
semantic tags, call type or call checks, the models have to be compared on real calls, in Russian, with
measured thresholds.

Four goals of the first version:

1. **Comparison and calibration.** Probabilities of different models side by side; RU/EN wording,
   option order, repeats; the "threshold → coverage and accuracy" curve on a labelled dataset.
2. **Local runtime for whip-insight.** d1-2B and d1-Omni-2B on Apple Silicon through `mlx-vlm`, served
   as `POST /v1/systemone` (port 8791, bearer token). LM Studio cannot do this: it does not load
   `lfm3_omni` and runs models through text generation.
3. **New d1 capabilities.** Audio input (call type from a recording), extraction of dates, amounts and
   numbers with a `span`, multi-select.
4. **Decision model vs LLM.** The same question through an ordinary LLM on OpenRouter: agreement, cost,
   latency (WI-1).

## Engines

| Engine kind | Models | Where it runs | Status |
|---|---|---|---|
| `openrouter_decisions` | `typesafe/jev-1.14`, `liquid/d1.1`, `~liquid/d1-latest`, others from the catalog | OpenRouter Decisions API, ZDR by default | done |
| `mlx_local` | `LiquidAI/d1-2B-MLX-8bit`, `LiquidAI/d1-Omni-2B-MLX-8bit` | in process on the Mac, `mlx-vlm >= 0.7.11`, one model in memory | done |
| `systemone_http` | Ollama `d1`, `d1:2b`; `llama-server` with d1-3B-GGUF; the own compat server | local servers with `/v1/systemone` | planned (WI-1) |
| `openrouter_llm` | LLM list in `config.yaml` | OpenRouter chat completions; `logprobs` or verbal confidence | planned (WI-1) |

Planned kinds may stay in `config.yaml`: the registry lists them as unavailable.

## Requirements

- macOS on Apple Silicon for local models (`make setup` installs `requirements-mlx.txt` only there). The UI,
  the compat server and cloud engines need only `requirements.txt`; see [Other platforms](#other-platforms).
- Python 3.14 (the Makefile prefers pyenv 3.14.8, otherwise `python3` on `PATH`). Everything installs
  into the project's `.venv`.
- An OpenRouter API key for cloud engines.
- `ffmpeg` for audio uploads (`brew install ffmpeg`).
- Node.js for the frontend tests (optional: `make test` skips them without `node`).
- About 4.6 GB of disk for the two 8-bit MLX models; 16–24 GB of RAM.

## Other platforms

The playground is built and tested on macOS with Apple Silicon. Elsewhere it runs with reduced scope.

| Platform | Works | Does not work |
|---|---|---|
| macOS, Apple Silicon | everything | — |
| macOS Intel, Linux | UI, `/api`, cloud engines (`openrouter_decisions`); Workbench, History, Datasets, Experiments, Matrix, turn pointer on cloud engines | local d1 models, see below |
| Windows (native) | nothing: not supported | use WSL2; the Linux row applies there |

On macOS Intel and Linux:

- `make setup` skips `requirements-mlx.txt`. `mlx_local` engines stay listed as unavailable: "needs macOS on Apple
  Silicon".
- The compat server starts but has no local model to serve: `POST /v1/systemone` answers 503 `model_unavailable`,
  `GET /health` reports `unavailable`. whip-insight cannot use it.
- Features of local d1 only are unavailable: audio (only d1-Omni hears it; uploads still convert), extraction
  (`text`, `number`, `date`) and `multi`. OpenRouter refuses these question types.
- Do not run `make models`, `make smoke-local`, `make spike-mlx`: they need the MLX stack. `make spike-audio` needs
  the macOS `say` command.
- `make test` skips the ~98 local-engine tests that need mlx-vlm and says so in its last lines.
- Durable writes use `fsync` instead of macOS `F_FULLFSYNC`. The compat address on the "Local models" screen is the
  host name plus `.local`; on Linux mDNS needs Avahi.

Windows: `app/store.py` imports `fcntl` (POSIX only), shutdown uses `loop.add_signal_handler`, and the Makefile needs
`make`, a POSIX shell and `lsof`.

**Checked how.** Only by simulation on the Mac: `requirements.txt` alone and `platform.machine()` reporting `x86_64`.
The app imports; pytest gives 773 passed, 98 skipped for mlx-vlm, 0 failed. Nothing was run on a real Linux, Intel Mac
or Windows machine.

## Quick start

```bash
make setup     # .venv, dependencies (+ requirements-mlx.txt on Apple Silicon), .env and config.yaml from the examples
$EDITOR .env   # OPENROUTER_API_KEY; mode 600
make models    # download d1-2B and d1-Omni (8-bit MLX, ~4.6 GB) into HF_HOME
make check     # which engines are ready, the catalog, today's spend
make run       # UI at http://127.0.0.1:8790 and the compat server on :8791
make test      # tests without network, keys or models
```

## Using it

Step-by-step instructions for every screen: [User Guide](docs/manuals/user-guide.md).

- **Workbench.** Paste a call (or pick a preset), define questions, tick engines, press "Run". The cost
  estimate shows before the run; the day's spend shows in the header. The draft (transcript and
  questions) survives a page reload.
- **History and links.** `/?run=<id>` opens a run, `/?compare=<id1>,<id2>` compares two,
  `/?report=<id>` opens a dataset or experiment report, `/?tab=history` opens History.
- **Local models.** One model in memory; it unloads after 10 minutes idle or by a button on the "Local
  models" screen.
- **Datasets.** `make import-tags` turns whip-insight's 56 standard tags into question presets and
  labelled datasets from their test phrases (`WHIP=../whip-insight`). `make synth TAG=std_answering_machine`
  places the phrases inside neutral dialogues. The "Datasets" screen runs a dataset over engines with a
  cost estimate and builds the report: the threshold for a whip-insight tag, the "coverage ↔ accuracy"
  curve, the reliability diagram, disagreements. An own dataset is a JSONL or CSV file with labels.
- **Experiments.** "Experiment…" in Workbench sends the current call and questions to the
  "Experiments" screen: the same questions in other wordings (for example in English), with options in
  other orders, N times each. The report: repeat range, probability shift from order and wording,
  first-option pull, a heatmap.
- **Turn pointer.** A checkbox in Workbench. A transcript line is a turn; "Name: text" names the
  participant. After "yes", and for the top option, the model points at the evidence turn (as the
  whip-insight pointer does); it is highlighted under the answer with its probability. Pointer passes
  are paid and are part of the estimate.
- **Extraction** (`text`, `number`, `date`; local d1). The found fragment is highlighted in the call
  text. If the model found "12 ноября 2026 года" or "триста пятьдесят тысяч рублей" but gave no value,
  the playground reads the fragment itself and marks it "normalized by the playground". `multi` has a
  threshold and shows the chosen options.
- **Audio and pictures.** "Media…" takes a recording (WAV, MP3, M4A, OGG, FLAC, WebM, AMR) and converts
  it to WAV 16 kHz mono, the first 30 seconds (`audio.max_seconds`); pictures: PNG, JPEG, WebP. Uploads
  live in `media/`; `make clean-media` deletes them. Only d1-Omni hears audio. The preset "Call kind"
  (`call_kind`) asks "human, answering machine, IVR…". The model hears synthesized speech as a robot:
  accuracy is checked on real recordings.
- **Capability matrix.** "Probe" on an engine row checks which question types, how many options and
  levels the model serves, with the same requests as whip-insight. The "long" checkbox adds a
  full-context read check; the screen shows its estimate first. Runs use the probe profile at once: a
  question the model does not serve is not sent.
- **Compat server for whip-insight.** Set `SYSTEMONE_TOKEN` (≥ 32 characters, e.g.
  `openssl rand -hex 24`) and `COMPAT_HOST=0.0.0.0` in `.env`; without a token the server listens on
  this Mac only. Check:

  ```bash
  curl -s http://127.0.0.1:8791/v1/systemone -H "Authorization: Bearer $SYSTEMONE_TOKEN" \
    -H 'Content-Type: application/json' -d @tests/fixtures/requests/whip_insight_systemone.json
  ```

## Make targets

| Target | What it does |
|---|---|
| `make setup` | Create `.venv`, install requirements (+ MLX on Apple Silicon), create `.env` and `config.yaml` from the examples |
| `make models` | Download the two 8-bit MLX models into `HF_HOME` |
| `make run` / `make run-compat` | UI + compat server in the foreground / the compat server only |
| `make run-bg` / `make stop` | The same in the background (log `/tmp/decision-playground.log`) / stop it |
| `make check` | Availability of every engine, the catalog cache, today's spend |
| `make test` | Python and frontend tests (no network, keys or models) |
| `make smoke-local` | Opt-in real local path: loads d1-2B and d1-Omni, writes a log to `docs/reviews/` |
| `make import-tags` | whip-insight's 56 standard tags → `presets/std_tags/` and `datasets/` (`WHIP=../whip-insight`) |
| `make synth` | A tag's phrases inside neutral dialogues → `datasets/synth_<tag>` (`TAG=…`) |
| `make clean-runs` / `make clean-media` | Delete `runs/` / `media/` (asks first) |
| `make spike` | P0 spike: OpenRouter and both local d1 models ([SPIKE.md](docs/SPIKE.md)) |

## Configuration

- `.env` — keys, hosts and ports, `HF_HOME`, `DAILY_BUDGET_USD` (default 1 USD), `FFMPEG_BIN`,
  `DATA_DIR`. Template with comments: [.env.example](.env.example).
- `config.yaml` — the engine registry, local model manager, compat server limits, audio, cost estimate.
  Template with comments: [config.example.yaml](config.example.yaml).
- UI and `/api` listen only on `127.0.0.1:8790`. The compat server listens on `:8791`, on the LAN only
  with `SYSTEMONE_TOKEN`.
- Budget: a daily limit on paid calls with an estimate before every run.

## Data, privacy and licenses

- Real calls stay on disk in `runs/` and `datasets/` (both in `.gitignore`); they go to OpenRouter with
  ZDR and `data_collection: deny`. A model without a ZDR endpoint is called only with a per-run consent.
- The browser keeps the Workbench draft (transcript and questions) in `localStorage` to survive a
  reload. It is a copy of the call outside `runs/`; `make clean-runs` does not remove it.
- Reports contain no call text: ids, labels, probabilities and metrics only.
- Open d1 weights are under LFM Open License v1.0: commercial use is allowed while the company's annual
  revenue is below $10 000 000. The owner approved them on 2026-11-08; a new installation re-checks the
  condition.

## Relation to whip-insight

The playground writes nothing into whip-insight. The links:

- **Compat server.** It becomes a whip-insight dev profile (a `cvj.decision_configs` row) — a separate
  whip-insight work-item after task-049.
- **Presets.** The import script reads tag files from the whip-insight repository.
- **Findings.** The owner copies thresholds and wording rules into the measurements of whip-insight
  `SPEC-decision-service.md` §15.

## Documentation

| Document | Contents |
|---|---|
| [docs/manuals/user-guide.md](docs/manuals/user-guide.md) | User guide: screens, tasks, local model context, compat server, troubleshooting ([Russian](docs/manuals/user-guide.ru.md)) |
| [docs/TASK.md](docs/TASK.md) | TASK 001: goals, owner decisions, use cases, requirements, acceptance records of P0–P6, risks |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Components, data model, engines, interfaces, security, reliability, configuration, tests |
| [docs/BACKLOG.md](docs/BACKLOG.md) | Work-items; WI-1 is the P7 plan |
| [docs/SPIKE.md](docs/SPIKE.md) | P0 spike: live measurements of mlx-vlm, OpenRouter Decisions and chat (Russian) |
| [docs/DEVOPS_D1_OMNI.md](docs/DEVOPS_D1_OMNI.md) | d1-Omni on Ubuntu: llama-server behind LiteLLM (Russian) |
| [docs/reviews/](docs/reviews/) | Adversarial review critique and response per increment |
| [docs/DESIGN.md](docs/DESIGN.md) | Retired design document: a map from its old section numbers to the new places |

## Project layout

```text
app/            FastAPI app, compat server, runner, engines/, local model manager, probe, datasets, experiments
web/            index.html, styles.css, js/ — vanilla JS UI, own SVG charts
presets/        question presets: call_kind, demos, std_tags/ (56 whip-insight tags), synth/ templates
scripts/        tag import, synthetic dialogues, local smoke test, spike/ (P0)
tests/          pytest suites, js/ frontend tests, fixtures/ (key-free provider records)
docs/           TASK, ARCHITECTURE, BACKLOG, SPIKE, reviews/
runs/ datasets/ probes/ reports/ media/   local data, not in git
```
