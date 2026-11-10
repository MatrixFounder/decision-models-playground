# Decision Models Playground — User Guide

**English** | [Русский](user-guide.ru.md)

This guide describes how to work with the playground: screens, typical tasks, settings and failures. Installation and
the make targets are in the [README](../../README.md). Design details are in [ARCHITECTURE](../ARCHITECTURE.md).

State of the guide: 2026-11-10, increments P0–P6. The `systemone_http` and `openrouter_llm` engines are planned
([WI-1](../backlog/wi-1-p7-systemone-http-and-llm-baseline.md)) and are not covered here.

## Contents

1. [Before you start](#1-before-you-start)
2. [Screens](#2-screens)
3. [Workbench: one call through several engines](#3-workbench-one-call-through-several-engines)
4. [History](#4-history)
5. [Experiments: wording, option order, repeats](#5-experiments-wording-option-order-repeats)
6. [Datasets: calibrating a threshold](#6-datasets-calibrating-a-threshold)
7. [Matrix: what each engine can do](#7-matrix-what-each-engine-can-do)
8. [Local models](#8-local-models)
9. [Compat server for whip-insight](#9-compat-server-for-whip-insight)
10. [Budget and cost](#10-budget-and-cost)
11. [Where the data lives](#11-where-the-data-lives)
12. [Troubleshooting](#12-troubleshooting)

## 1. Before you start

Checklist of a first start (details in the README "Quick start"):

| Step | Command | Result |
|---|---|---|
| 1 | `make setup` | `.venv`, dependencies, `.env` and `config.yaml` from the examples |
| 2 | edit `.env` | `OPENROUTER_API_KEY` for cloud engines; the file mode is 600 |
| 3 | `make models` | d1-2B and d1-Omni (8-bit MLX, ~4.6 GB) in `HF_HOME`; Apple Silicon only |
| 4 | `make check` | availability of every engine, the catalog cache, today's spend |
| 5 | `make run` | UI at <http://127.0.0.1:8790>, compat server on `:8791` |

Stop the server with Ctrl+C in the terminal of `make run`. `make run-bg` starts it in the background (log
`/tmp/decision-playground.log`); `make stop` stops it.

`.env` and `config.yaml` are read once, at start. After every change to them, restart the server.

The header of every screen shows:

- the tabs of the six screens;
- the budget pill "Today: $spent / $limit";
- the language button. The interface opens in Russian; "EN" switches it to English. The choice is kept in the
  browser.

## 2. Screens

| Screen | Purpose |
|---|---|
| Workbench | One call and up to several questions through several engines; the answers side by side |
| History | Saved runs: open, rerun, compare two |
| Datasets | Labelled datasets; a run over engines; a calibration report with thresholds |
| Experiments | The same call in other wordings, option orders and repeats; a stability report |
| Matrix | Capabilities of each engine (question types, limits, context); the probe |
| Local models | The state of the local d1 models; unloading; the compat server's address |

Direct links:

| Link | Opens |
|---|---|
| `/?run=<id>` | a Workbench run |
| `/?compare=<id1>,<id2>` | the comparison of two runs |
| `/?report=<id>` | a dataset or experiment report |
| `/?tab=history` | History |

## 3. Workbench: one call through several engines

### 3.1 State

The left column holds the state: the call text or JSON.

- "Text" / "JSON" switches the input mode. Text is sent as a string. JSON is sent as an object, for example
  `{"fragments": [...]}` from whip-insight.
- "Preset…" loads a ready state and questions: `demo_bank_call` (noul, choice, score), `demo_extraction` (multi,
  text, number, date), `call_kind` (call kind by audio), 56 whip-insight tags `std_*` after `make import-tags`.
- "File…" loads `.txt`, `.md` or `.json`.
- "Media…" attaches a recording or a picture (§3.8).
- The line under the text field shows the character count.
- The draft (state and questions) is kept in the browser and survives a page reload. After an opened run replaces it,
  "↶ restore draft" brings it back.

### 3.2 Questions

"+ question" adds a question; "JSON" switches the list to JSON editing. Each question has a key, a type and the
"Question to the model" text.

| Type | What the model returns | Fields in the editor | Engines |
|---|---|---|---|
| `noul` | P(true) | "Yes means…", "No means…" | all |
| `choice` | a probability per option, the top option | option id + description, "+ option" | all |
| `score` | a probability per level, the expected level | levels, "+ level" | all |
| `multi` | P(yes) per option | as `choice` | local d1 only |
| `text` | a fragment of the state with `span` | — | local d1 only |
| `number` | a number, `span`; with a range, an interval p10–p90 | from, to, step, unit | local d1 only |
| `date` | an ISO date, `span` | — | local d1 only |

Rules:

- A key and an option id match `^[a-z][a-z0-9_]{0,63}$`: a lower-case Latin letter first, then letters, digits, `_`.
- `choice`: 2–255 options; `score`: 2–10 levels.
- A repeated key or option id is marked under the editor; the run does not start.
- The JSON format is the decision/1 format: `{"<key>": {"type", "instructions", "criteria"}}`. `multi` is
  `"type": "choice", "multiple": true`. Example: `presets/demo_extraction.yaml`.

### 3.3 Engines

The right column lists the engines from `config.yaml`. Badges of an engine:

| Badge | Meaning |
|---|---|
| `$0.02/M in` | input price per million tokens from the OpenRouter catalog; `$?` when the catalog has no price |
| `ZDR` / `no ZDR` / `ZDR ?` | the model has a Zero Data Retention endpoint / has none / unknown |
| `⌛ <date>` | the model's expiration date in the catalog |
| `local` | a local model; free |
| `unloaded` / `loading` / `in memory` / `busy` | the state of a local model (§8) |

Engines that cannot run are under "Unavailable (n)" with the reason (§12.1). "↻" refreshes the OpenRouter catalog:
prices, ZDR, expiration dates.

### 3.4 Running

1. Tick the engines.
2. Optional: "Run title" and "tags, comma separated". Tags filter History.
3. Optional: "Turn pointer" (§3.6).
4. Check the estimate: "Estimate $x · left $y". It is red when it exceeds the remainder of the day.
5. Press "Run".

A selected engine without ZDR shows "Allow engines without ZDR". Without that tick the run does not start. The tick
clears after the start: each run asks again.

The results stream in as each engine finishes. Closing the tab does not stop the run: it completes and is saved to
History.

### 3.5 Reading the results

The table has a row per question and a column per engine.

- A cell shows the probabilities as bars and the top answer.
- Under the question key: the type and, with two answers or more, the badge "agree" (every engine gave the same top
  answer) or "disagree".
- A cell without an answer names the class: `unsupported` (the engine does not serve this type), `refused`,
  `invalid_answer`, "request: <class>" (the whole request of this engine failed). Classes: §12.2.

The footer rows per engine:

| Row | Contents |
|---|---|
| Status | `ok`; `partial` (some questions failed); `failed` (no answers); or the class of the request failure |
| Latency | the model call; for a local model without the queue and the load |
| Cost | USD; the tooltip says "reported" (from the provider) or "by price" (tokens × catalog price) |
| Tokens | input / output tokens of the call |
| Snapshot | the dated model version that answered, e.g. `liquid/d1.1-20261027` |

Lines under the table give the error details of an engine.

### 3.6 Turn pointer

The turn pointer finds the turn of the transcript that supports the answer, as the whip-insight route does.

- Transcript format: a non-empty line is a turn `t0, t1, …`; "Name: text" names the participant. A line "Name:"
  without text names the participant of the next line. VTT/SRT numbers and timecodes become turns.
- After a "yes" of a `noul` and for the top option of `choice` / `score` the model is asked "In which turn …?". The
  answer appears under the cell: "↳ t6 Client: …" with its probability; the turn is highlighted.
- Up to B turns take one call, more take two passes. B = min(240, the engine's maximum of options). Over B² turns the
  request of that engine is not sent (`too_long`).
- The passes are paid and are part of the estimate.
- The pointer does not work with media (400).
- "“yes” after rounding": P(true) is in [0.4995, 0.5). The cell shows "no", while the whip-insight route rounds it to
  0.500, reads "yes" and asks the pointer.

### 3.7 Extraction and `multi`

Types `text`, `number`, `date` and `multi` run on local d1 only; cloud engines show `unsupported` without a call.

- The found fragment (`span`) is highlighted in the call text when the state is text.
- When the model found a fragment but returned no value, the playground reads the fragment and marks the reading
  "normalized by the playground". It reads Russian dates ("12 ноября 2026 года", `12.11.2026`) and numbers with spaces
  or in words ("триста пятьдесят тысяч рублей"). The model's own value is never replaced.
- `multi` has a "threshold" field under the question key (default 0.5). It changes only how the answer is read; it is
  not sent to the model.

### 3.8 Audio and pictures

- "Media…" takes audio (WAV, MP3, M4A, OGG, FLAC, WebM, AMR) and pictures (PNG, JPEG, WebP), up to 16 MB.
- Audio becomes WAV 16 kHz mono, the first 30 seconds (`audio.max_seconds` in `config.yaml`, up to 600). The chip
  shows "first 30 s of 95 s" when the recording was cut. ffmpeg is required.
- Only d1-Omni (`mlx-d1-omni`) hears audio. Other engines get no call when the request has audio.
- With media and an empty text the state is sent as `null`.
- For a call kind by ear: preset `call_kind` + `mlx-d1-omni`. The model hears synthetic speech as `voice_robot`; check
  accuracy on real recordings.
- Uploads stay in `media/`; `make clean-media` deletes them.

## 4. History

- Filters: a tag and an engine.
- Columns: when, title, engines, questions, cost.
- "Open" loads a Workbench run into the Workbench. A dataset or experiment record opens its report.
- "Rerun" repeats a Workbench run with the same inputs and engines. It shows the estimate first and asks the no-ZDR
  consent separately. Probes, datasets and experiments are not rerun from here.
- Tick two runs and press "Compare selected": the table shows P of run A's top option in both runs and the Δ.

## 5. Experiments: wording, option order, repeats

An experiment measures how stable the answers are.

1. In the Workbench, set the state and questions and press "Experiment…". The Experiments screen takes a copy; "↻
   Take from the Workbench" takes it again.
2. "Formulations": the Workbench questions are the base. "+ formulation" adds up to five more as JSON of the same
   questions: the same keys, types and option ids, the same number of levels. Example: the English wording.
3. "Order and repeats": "reorder the options (choice, multi)", `k` orders (default 6), `seed` (default 7), the
   number of repeats (default 5, 1–20). Up to 4 options take every order.
4. Tick the engines and confirm the estimate. One experiment takes at most 2 000 calls.

Advice: reorder one `choice` question per experiment. With several such questions the orders change together, and
the effect of one question includes the reorder of another.

Report metrics:

| Metric | Meaning |
|---|---|
| Repeat spread | max − min of P over repeats of one request |
| SE of a difference | the noise level; \|ΔP\| below about two SE is noise |
| Order \|ΔP\|, Order: changed | the probability shift and the share of top-answer changes caused by the option order |
| Shown first (expected) | how often the option shown first won; in brackets, the value of a model without a pull |
| First-place lift | how much the probability rises when an option stands first; about 0 without a pull |
| Formulation \|ΔP\|, Formulation: changed | the same for the wording |

The heatmap shows the probabilities per engine. "Markdown" exports the report. "↺ Same settings" copies the state,
formulations, orders, repeats and engines into a new experiment. If the daily budget runs out mid-run, the report
covers the cells asked.

## 6. Datasets: calibrating a threshold

A dataset is a set of calls with labels. Its report gives the threshold for a whip-insight tag.

### 6.1 Sources

- `make import-tags` (`WHIP=../whip-insight`): 56 whip-insight standard tags become presets `presets/std_tags/` and
  datasets from their test phrases.
- `make synth TAG=std_answering_machine`: the tag's phrases inside neutral dialogues, `datasets/synth_<tag>`.
- Own file, "Import a file" on the Datasets screen:
  - JSONL: a line `{"id", "state", "labels": {"<question>": "true"}, "meta"}`;
  - CSV: columns `state`, `id`, `label` (one question) or `label:<question>`; the delimiter `,`, `;` or tab;
  - the questions: a preset or own JSON;
  - the label source: "labelled by hand" or "test phrases";
  - "replace an existing dataset" overwrites a dataset with the same name.

Labels: `noul` — true/false (also yes/no, да/нет, 1/0); `choice` — an option id; `score` — a level index or text.
`multi`, `text`, `number`, `date` take no labels. At most 5 000 items; up to ten label errors are reported together.

### 6.2 Running

1. Pick a dataset on the left; the screen shows its first items.
2. Tick the engines; "First" limits the number of items.
3. "Run" shows the calls, the estimate and the budget left, and asks for confirmation.

Local engines run one call at a time and one engine after another. If the budget runs out, paid items get `budget`
and are not asked; local items continue.

### 6.3 Reading the report

| Column | Meaning |
|---|---|
| whip-insight t | the threshold with the highest agreement: the verdict is "yes" when P(true) ≥ t; this t goes to the whip-insight tag |
| Agreement | the share of verdicts matching the label among the answered items |
| Precision (yes), Recall (yes) | for the verdict "yes" at whip-insight t |
| t for target | the smallest t at which the accuracy of decided items reaches the target (default 0.90) |
| Coverage | the share of decided items: max(p, 1 − p) ≥ t |
| Accuracy of decided | the share of correct answers among decided items |
| Top-1 | for `choice` / `score`: the share of top answers equal to the label |

- "Target" changes the target accuracy (0.50–0.99) and recomputes the report.
- Charts: the threshold curve (coverage ↔ accuracy) and the reliability diagram. Dots on the diagonal mean that the
  probabilities match the share of correct answers.
- "Disagreements (n)" lists the items where the answer and the label differ.
- `choice` / `score` get a confusion matrix.
- "Markdown" and "JSON" export the report; the files are also in `reports/`. Reports hold no call text.

A threshold from a dataset is a starting point. whip-insight asks a tag on fragments after a lexical rule, and a
dataset asks each item as is.

## 7. Matrix: what each engine can do

The Matrix lists the configured engines, then the OpenRouter catalog models absent from `config.yaml`.

### 7.1 Columns

| Column | Contents |
|---|---|
| `noul` … `date`, `audio` | `✓` served, `✗` not served, `?` not checked, `—` impossible for this engine kind |
| ≤ options, ≤ levels | the largest `choice` and `score` sizes served |
| full context | ✓ when the long probe found a fact at the end of a state of ~90 % of the context; the tooltip gives the tokens |
| Context | the context limit in tokens: from the catalog for a cloud model, `context_tokens` for a local one (§8.2) |
| $/M in, ZDR, Expires | from the OpenRouter catalog |
| usage.cost | whether the provider reports the cost |
| Snapshot | the dated version seen by the probe |
| Probe | the date and cost of the last probe; the "Probe" button and the "long" checkbox |

### 7.2 Probing

"Probe" sends short synthetic requests: the three base types, `choice` with 24, 100 and 240 options, `score` with 10
levels; on local d1 also `multi`, `text`, `number`, `date` and audio on d1-Omni. The profile is saved in `probes/`.
Later runs use it at once: a question the engine does not serve is not sent.

- "long" adds the full-context check. It is slow on a local model and costs money on a cloud one; the screen shows the
  estimate first.
- A probe of a local model holds it: compat-server requests wait in the queue.
- "stale" marks a profile of another model version, or of a model that answered with a new snapshot later. Probe it
  again.
- "Refresh catalog" reloads prices, ZDR and the catalog list.

## 8. Local models

### 8.1 The "Local models" screen

| Column | Contents |
|---|---|
| Engine, Model | the engine id and the Hugging Face repository |
| State | `unloaded`, `loading`, `in memory`, `busy`; or `unavailable` with the reason |
| On disk | the model size in `HF_HOME` |
| Snapshot | `<repo>-<YYYYMMDD>` from the revision date; without a date when offline |
| MLX memory | the memory of the loaded model |
| Last call, Calls | the time of the last call and their count since start |
| Unload | frees the memory now |

Behaviour:

- One model is in memory at a time. A call to the other model unloads the current one and loads the other; the call
  waits for the load.
- An idle model unloads after 600 s (`local.idle_unload_s` in `config.yaml`).
- Calls to a local model, from the UI and the compat server alike, go one at a time through one queue. More than 8
  waiting callers (`local.max_waiting`) get `rate_limited` in the UI and 429 `busy` on the compat server.
- `timeout_s` of the engine (default 30) bounds the wait in the queue. A call that already runs on MLX is not
  interrupted.

The lower part of the screen describes the compat server (§9).

### 8.2 Context of a local model

**What it is.** `context_tokens` of an `mlx_local` engine in `config.yaml` is the prompt limit in tokens. The default
is 32 768 for both d1-2B and d1-Omni.

**Where to see it.**

- Matrix, "Context" column: the current limit.
- Matrix, "full context" column: whether the model read a state of ~90 % of this limit; the tooltip gives the tokens of
  that check (29 445 for 32 768).
- `curl -s http://127.0.0.1:8790/api/local/models`: the field `context_tokens` of each local engine.
- Workbench, footer row "Tokens": how many input tokens a particular call used.

**How the limit works.** Before the queue the playground counts the request with the model's tokenizer: the state as
mlx-vlm renders it, plus the heaviest group of questions, plus an allowance per picture and audio clip. A request over
the limit gets `too_long` in the UI and 413 on the compat server, without a model call. A state whose rendered length
exceeds 16 × `context_tokens` characters is refused before the count.

The limit is the playground's own check. The model accepts more: its `config.json` gives
`max_position_embeddings = 128000`, and the model card promises reliable reading up to ~64K tokens. The limit exists
because of memory: it grows about with the square of the length. P0 measurements of d1-Omni on a 24 GB Mac
([SPIKE §2.4](../SPIKE.md)):

| Tokens | Time | Peak memory |
|---|---|---|
| 1 781 | 0.9 s | 3.3 GB |
| 6 933 | 3.9 s | 3.4 GB |
| 13 877 | 9.8 s | 4.0 GB |
| 27 765 | 27.6 s | 6.1 GB |
| ~64K | not run | forecast ≈ 20 GB |

d1-2B gives about the same time and 0.5 GB less memory. Russian dialogue gives about 2.6 characters per token, so
32 768 tokens are about 85 000 characters. A 10-minute call is about 3 500 tokens.

**How to change it.**

1. In `config.yaml`, set `context_tokens` under the engine, for example:

   ```yaml
   - id: mlx-d1-omni
     kind: mlx_local
     repo: LiquidAI/d1-Omni-2B-MLX-8bit
     context_tokens: 49152
     timeout_s: 90
     audio: true
   ```

   Each local engine has its own value.
2. Restart the server.
3. Check the "Context" column of the Matrix.
4. After a raise, run "Probe" with "long" on this engine. It fills ~90 % of the new limit and shows the time and
   whether the model reads all of it.

When you raise the limit:

- Check the free memory: values above ~28K tokens were not measured on this Mac.
- Raise `timeout_s` as well. A call of 28K tokens takes ~28 s. A second caller waiting behind it for more than
  `timeout_s` (30 s) gets `timed_out`.
- The whip-insight dev profile holds its own `context_tokens` (32 768). Keep it equal to the playground's value.

A lower limit protects memory and makes long calls fail with `too_long` at once.

## 9. Compat server for whip-insight

The compat server serves the local d1 models as `POST /v1/systemone` with System One names. It starts with
`make run`; `make run-compat` starts it alone.

### 9.1 Access

| Mode | `.env` | Who can call |
|---|---|---|
| This Mac only | `COMPAT_HOST=127.0.0.1` (default), token optional | local processes |
| LAN | `COMPAT_HOST=0.0.0.0` and `SYSTEMONE_TOKEN` | any host with the token |

- The token: at least 32 characters, at least 10 distinct; `openssl rand -hex 24` gives one. Without the token a
  non-loopback `COMPAT_HOST` stops the start.
- The address for whip-insight is `http://<mac-name>.local:8791/v1/systemone`. The "Local models" screen shows it with
  the access mode, the token state and the default model.
- `compat.default_model` in `config.yaml` is used when a request has no `model` field. `model` takes an engine id,
  e.g. `mlx-d1-omni`.

Check from the repository folder:

```bash
curl -s http://127.0.0.1:8791/v1/systemone -H "Authorization: Bearer $SYSTEMONE_TOKEN" \
  -H 'Content-Type: application/json' -d @tests/fixtures/requests/whip_insight_systemone.json
```

`GET /health` needs no token. It returns `status` (`ok`, `degraded` after a model error, `unavailable`), the default
model's id and state, and the mlx-vlm version.

### 9.2 Contract and answers

- Types: `noul`, `choice`, `score`. `multi`, `text`, `number`, `date` need the header `X-Playground-Extensions: 1`;
  whip-insight does not send it.
- 1–64 questions; a body up to 6 MB; up to 4 pictures, each up to 16 MP. The `media` field (audio) is refused.
- The answer carries a dated snapshot, `provider: "local-mlx"` and `usage.cost: 0`.

| Code | Body `error.code` | Cause |
|---|---|---|
| 400 | `unsupported_question_type` | a type outside the accepted set; the body names the type and the question |
| 400 | `forbidden_host` | the Host header is not in `compat.allowed_hosts`; add the Mac's IP there if callers use it |
| 401 | — | no token or a wrong token |
| 403 | — | the request has an `Origin` header (a browser page) |
| 404 | `model_not_found` | an unknown `model` |
| 413 | `too_long` | the request is over `context_tokens` (§8.2) |
| 429 | `busy` | more than 8 callers wait, the wait exceeds `timeout_s`, or the server is shutting down |
| 503 | `model_unavailable` | the model is not downloaded, cannot load, or has a broken tokenizer |

## 10. Budget and cost

- `DAILY_BUDGET_USD` in `.env` (default 1.0) limits paid calls per day. Local models are free.
- Every run shows an estimate first: (1.5 × characters of state and questions + 200) × the input price.
  Coefficients: `budget` in `config.yaml`. A model without a catalog price takes the highest decision-model price in
  the catalog, otherwise $0.20 per million tokens.
- A run whose estimate exceeds the remainder does not start: "Estimate … exceeds the budget left …".
- The fact is `usage.cost` from the provider, otherwise tokens × catalog price.
- Spend belongs to the day the run is saved. A dataset run can overrun the day by at most one call per paid engine.
- To continue today, raise `DAILY_BUDGET_USD` and restart the server.

## 11. Where the data lives

| Folder | Contents |
|---|---|
| `runs/<day>/<id>.json`, `runs/index.jsonl` | run records with call texts, and their index |
| `datasets/<name>/` | `dataset.yaml` and `items.jsonl` |
| `probes/` | engine profiles, the catalog cache, local snapshot dates |
| `reports/` | dataset reports, Markdown and JSON, without call texts |
| `media/` | uploaded audio and pictures |

- These folders are in `.gitignore`; `DATA_DIR` in `.env` moves their root.
- Cloud calls go to OpenRouter with ZDR and `data_collection: deny`.
- The Workbench draft is a copy of the call in the browser's `localStorage`; `make clean-runs` does not remove it.
- `make clean-runs` and `make clean-media` delete `runs/` and `media/` after a confirmation.

## 12. Troubleshooting

### 12.1 An engine is unavailable

| Reason on the screen | What to do |
|---|---|
| `OPENROUTER_API_KEY is not set in .env` | set the key, restart |
| `disabled in config.yaml` | `enabled: true` for the engine, restart |
| `<repo> is not downloaded: make models` | `make models` |
| `mlx-vlm is not installed (requirements-mlx.txt)` | `make setup` on Apple Silicon |
| `mlx-vlm X < 0.7.11` | update mlx-vlm in `.venv` (`requirements-mlx.txt`) |
| `needs macOS on Apple Silicon` | local models run only there (README "Other platforms") |
| `engine kind … arrives in …` | a planned kind (WI-1) |

### 12.2 Failure classes in a result

| Class | Level | Cause and action |
|---|---|---|
| `unsupported` | question | the engine does not serve this type (profile or engine kind); choose another engine |
| `refused` | question | the model refused to answer |
| `invalid_answer` | question | the answer failed validation (sum of probabilities, unknown option) |
| `too_long` | request | over a limit: `context_tokens` (§8.2), `max_request_bytes`, the provider's limit, B² turns of the pointer |
| `rate_limited` | request | HTTP 429 from the provider; the local queue is full |
| `timed_out` | request | no answer in time; for a local model the queue wait exceeded `timeout_s` |
| `unreachable` | request | no connection; a local model not downloaded or not loadable |
| `provider_error` | request | 5xx after a retry, an unreadable answer, an engine crash |
| `budget` | request | not sent: the daily budget ran out |
| `refused` | request | other 4xx; media or ZDR rules |

### 12.3 Other messages

| Message or symptom | Cause and action |
|---|---|
| "The stream ended without a result" | the connection broke; open History, the run may be saved |
| "The catalog did not refresh" | no network to OpenRouter; the cached catalog stays in use |
| "WARNING: other users can read .env" at start | `chmod 600 .env` |
| An upload is refused with 503 | ffmpeg is missing: `brew install ffmpeg` or `FFMPEG_BIN` in `.env` |
| HEIC or AVIF is refused | save the picture as JPEG or PNG |
| The pointer is refused with 400 | the request has media; the pointer reads text turns only |
| "stale" in the Matrix | the profile is of another model version; probe it again |
| The first local call is slow | the model loads; later calls are fast until the idle unload |
