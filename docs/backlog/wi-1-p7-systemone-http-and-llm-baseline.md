---
id: WI-1
type: work-item
status: open
opened_at: 2026-11-09
slug: wi-1-p7-systemone-http-and-llm-baseline
effort: L
value: 'closes goal G4 (decision model vs LLM: agreement, cost, latency) and connects any /v1/systemone server'
source: TASK 001 §8.1 (increment P7 of the retired docs/DESIGN.md §22.1)
---

# WI-1 — P7: `systemone_http` engine and LLM baseline

Increment P7 of [TASK 001](../TASK.md) was not started. It moved here on 2026-11-09, when the design
document was split into TASK 001 and [ARCHITECTURE.md](../ARCHITECTURE.md). Start it as its own TASK
(effort `L`, 3–3.5 days).

## Goal

Add two engine kinds and the reports that use them:

- `systemone_http` — any server with `POST /v1/systemone`: Ollama, `llama-server` (llama.cpp), the
  playground's own compat server.
- `openrouter_llm` — an ordinary LLM through OpenRouter chat completions, as the baseline of goal G4:
  the same question through a decision model and through an LLM — agreement, cost, latency.

Both kinds are already declared in `config.example.yaml` (disabled) and in `app/engines/__init__.py`
`PLANNED`. The registry shows them as unavailable until this work lands.

**Why the estimate changed.** The design gave P7 one day before P0–P6. The parts below add up to
3–3.5 days.

## Known facts on 2026-11-09 (before the work)

- Ollama and llama.cpp are not installed on the owner's Mac. LM Studio is installed but has no
  decisions path (RQ-7).
- OpenRouter catalog: `logprobs` is a property of an endpoint, not of a model.
  - `deepseek/deepseek-v4.1-flash`: 75 ZDR endpoints, 44 of them with `logprobs`; input price
    $0.022–0.45 per 1M (the model list says $0.051).
  - `openai/gpt-4o-mini`: 2 ZDR endpoints (Azure), both with `logprobs` ([SPIKE §4](../SPIKE.md)).
  - `qwen/qwen3.8-flash` (whip-insight builds call protocols on it): **no ZDR endpoints**. It can be
    called only with the per-run no-ZDR consent.
- whip-insight `llm_configs` on the stand holds two models: `qwen/qwen3.8-flash` and
  `deepseek/deepseek-v4.1-flash` (plan-032). The id of the whip-insight LLM arbiter is not fixed in
  code; the owner sets it.
- Both are reasoning models (`reasoning` in their parameters). The first token of an answer can be
  reasoning instead of a letter.
- 162 of 461 catalog LLMs declare `logprobs`/`top_logprobs` in `supported_parameters` (RQ-5). With
  ZDR the chosen provider can lack them; the engine then falls back to `verbal`.
- `usage.cost` arrives in every chat answer without flags; `usage: {include: true}` is deprecated and
  has no effect (SPIKE §4). The `logprobs` mode was checked on `openai/gpt-4o-mini` with ZDR (provider
  Azure): `top_logprobs` over the option letters arrive.

## P7.1 `systemone_http` (0.5 day)

1. Request — the same as whip-insight's `systemone` adapter (`decision-systemone.js` `build`):
   `{model, state, questions}`.
   - Only `noul`, `choice`, `score` are sent.
   - Extension types are sent only with `extensions: true` in the engine config. The engine then adds
     the header `X-Playground-Extensions: 1`, which the playground's own compat server needs.
   - Pictures from `media` go as `images` in base64. Audio is not sent over the network.
2. Response — System One names (ARCHITECTURE §3.5, answer-name table), parsed by `normalize_answer`.
   - `{"type": "refusal"}` → `refused`.
   - Snapshot — `model` of the response. For Ollama, the digest from `GET /api/tags` is appended
     (`d1:2b@<digest[:12]>`): a tag alone does not identify the weights that answered.
3. Failures (failure classes, ARCHITECTURE §4):
   - network → `unreachable`; deadline → `timed_out`; 429 → `rate_limited`;
   - 401/403/404 → `refused` with the reason; 413 → `too_long`;
   - 5xx → `provider_error` after one retry;
   - 400 with `unsupported_question_type` and a question key (compat format, ARCHITECTURE §5) →
     `unsupported` for that question;
   - an Ollama 400 is parsed by the sample recorded in the P7.2 spike.
4. Availability is checked in the background, not in `available()`: that method is synchronous and
   called often.
   - Every 30 s, and before a run: `GET health_url` with a 2 s timeout.
   - `health_url` is set in the config. Default: `<root of base_url>/health` (llama.cpp, own compat);
     for Ollama `/api/tags`, which also shows whether `model` is pulled.
   - Before the first check the engine is grey: "checking".
5. Token — `token_env: SYSTEMONE_TOKEN`: the name of a `.env` variable; the value never goes into
   `config.yaml`. Needed for the own compat server off loopback.
6. Cost 0. With `base_url` on loopback the engine joins the local lane in dataset runs and
   experiments (`lane_width`): Ollama and MLX share the Mac's memory and must not load at the same
   time. `timeout_s` for Ollama — 120: the first call loads the model.
7. `context_tokens` — in the config. Ollama's default window (`num_ctx`) is small and can truncate a
   long call silently (RQ-12); the probe's long step catches it (P7.4).
8. Engine `compat-self` in `config.example.yaml`: `systemone_http` on the playground's own
   `127.0.0.1:8791` with `token_env`. It checks "what whip-insight sees" with nothing installed. Its
   answers must equal `mlx_local` of the same model, except `score`: compat returns the weighted level.

## P7.2 Ollama and llama.cpp spike (0.25 day, needs an owner decision)

1. Ollama: `brew install ollama`, then `ollama pull d1:2b` (3.9 GB). Then
   `scripts/spike/systemone_spike.py --name ollama-d1-2b …`: a basic request, the whip-insight adapter
   body, extension types (expect 400 — record its format), 5 repeats. Fixtures go to
   `tests/fixtures/systemone/ollama-d1-2b/`.
2. Which weights the tag `d1:2b` carries: the same requests on `mlx-d1-2b`. |Δp| < 0.01 on every
   question → it is d1-2B. Otherwise it is another model and gets its own matrix row.
3. llama.cpp — optional: `brew install llama.cpp`, `llama-server -hf LiquidAI/d1-3B-GGUF:Q8_0`
   (≈ 3.5 GB). This is a new model, d1-3B, not a second copy of d1-2B.
4. Result — new rows in SPIKE §5. If the owner does not install Ollama, `systemone_http` is accepted on
   `compat-self`.

The Ollama `d1:2b` spike was moved here from P0 by the owner on 2026-11-09; the script is ready, no run
was made (SPIKE §5).

## P7.3 `openrouter_llm` (1–1.25 days)

1. Call — `POST <OPENROUTER_BASE_URL>/api/v1/chat/completions`, one per question, up to
   `concurrency` in parallel.
   - `provider: {zdr: true, data_collection: "deny", require_parameters: true}`. With
     `require_parameters` the request goes only to endpoints with `logprobs`.
   - Without ZDR — only with the per-run consent, as for decision engines (TASK §6.1).
2. Prompt — a system message (answer rules) and a user message: `state` (text or JSON), the
   instruction, the options as letters `A…Z` with descriptions.
   - `noul`: `A` = "yes" (`criteria.true`), `B` = "no".
   - `score`: letters follow the levels from the lowest up.
   - Profile: `noul`, `choice`, `score`, at most 26 options. The turn pointer of an LLM therefore gets
     B = 26 and two passes up to 676 turns (ARCHITECTURE §3.10).
   - `multi`, `text`, `number`, `date` — `unsupported`.
3. Mode `logprobs`: `max_tokens: 1`, `temperature: 0`, `logprobs: true`, `top_logprobs: 20`,
   reasoning off (`reasoning: {effort: "none"}` or `{enabled: false}` — the spike in step 6 decides
   which one the model accepts).
   - Forms of one letter (`B`, ` B`, `b`, Cyrillic `Б`, SPIKE §4) are summed: Σ exp(logprob). Then
     the mass is normalized over the option letters.
   - The mass outside the options is kept in `raw`. Less than 0.5 on the options → `invalid_answer`.
   - An answer without `logprobs` → `invalid_answer` with the reason "provider gave no logprobs". The
     probe chooses the mode; the call is not retried.
4. Mode `verbal`: `response_format` JSON schema `{answer: <letter>, confidence: 0..1}`; `max_tokens`
   from the config (generous for reasoning models).
   - `Answer` gets a new field `readout: "verbal"`: `top` — the chosen option, `probabilities` empty,
     `vendor_confidence` — the stated confidence.
   - The screen shows "verbal 0.8" without probability bars. UI and metrics never mix verbal
     confidence with probabilities.
5. Mode `auto`: the mode from the engine's probe (P7.4). Without a probe — `logprobs` if the catalog
   has a ZDR endpoint with `logprobs`, otherwise `verbal`.
6. Mini-spike at the start of this part (≈ $0.01): `deepseek-v4.1-flash` and `gpt-4o-mini` on
   `core_ru`. Check: the first token is a letter with reasoning off; `require_parameters` with ZDR
   gives `logprobs`; `usage.cost` is present. Records go to `tests/fixtures/openrouter/chat/`.
7. Cost (TASK §6.2):
   - input = `tokens_per_char × characters + system-message overhead`, per question;
   - output = 1 token (`logprobs`) or `max_tokens` (`verbal`);
   - price = the highest among the matching ZDR endpoints in the catalog, not the model's list price:
     on ZDR it is 2–9 times higher;
   - fact = `usage.cost` of each answer (SPIKE §4).
8. Snapshot — `model` of the response. Engine latency — time to the last answer, not the sum of calls.
9. An LLM answer can serve as a dataset reference label, marked with its source (P7.5).

## P7.4 Probe, matrix, reports (0.5 day)

1. `PROBED_KINDS` += `systemone_http`, `openrouter_llm`.
   - `systemone_http`: the full probe (ARCHITECTURE §3.9), with the long step (catches Ollama's silent
     truncation).
   - `openrouter_llm`: a short probe — N, C, S, the mode (`logprobs` arrived or not), 26 options by
     construction. An LLM probe costs less than one cent.
2. Matrix: column "mode" (`logprobs` or `verbal`) for LLMs; snapshot with digest for
   `systemone_http`.
3. Dataset report (TASK UC-3) for `verbal`: top-1 accuracy and agreement only. No Brier, ECE or
   threshold curve; marked "verbal confidence is not a probability". Stability report (TASK UC-2) for
   `verbal`: share of answer changes, no ΔP.
4. Workbench and History: a mode badge in the column header; comparing two runs compares `verbal` by
   the answer.

## P7.5 LLM labelling of datasets (0.5 day, can be deferred)

1. `POST /api/datasets/{name}/label` `{engine, limit}` — an LLM engine runs over a dataset. The result
   is a new dataset `<name>__llm_<engine>` with `label_source: llm:<engine_id>`. Estimate and budget as
   in a dataset run (ARCHITECTURE §3.8).
2. The report of such a dataset says "agreement with the LLM", not "accuracy" (whip-insight R12.3); no
   threshold is suggested.

Until then the dataset loader refuses `label_source: llm:<engine_id>` (`app/datasets.py`
`LABEL_SOURCE`): accepts only `test_phrases` and `manual`.

## Acceptance

- UC-1: one Workbench run on `demo_bank_call` — Jev 1.14, d1.1, `mlx-d1-2b`, `ollama-d1-2b` (or
  `compat-self`), an LLM in `logprobs` and an LLM in `verbal`. Probabilities, latency and cost side by
  side. The same with the turn pointer.
- G4: `std_answering_machine` (59 phrases) on decision models and LLMs. Agreement with the labels, cost
  of 59 calls, latency. Expected spend below $0.05.
- `compat-self` and `mlx-d1-2b` give the same probabilities on `core_ru`.
- VDD review, fixes, plantings; review files `docs/reviews/p7-*`.

## Owner decisions before the start

| Question | Options | Recommendation |
|---|---|---|
| Ollama | install (`brew install ollama` + `d1:2b`, 3.9 GB) / do not install | install: this is the spike deferred from P0 |
| llama.cpp + d1-3B | install (≈ 3.5 GB) / do not install | optional: a new model; acceptance passes without it |
| LLM models | id of the whip-insight arbiter and a second model | `deepseek/deepseek-v4.1-flash` (in whip-insight `llm_configs`, ZDR and `logprobs`) and `openai/gpt-4o-mini` (checked in P0). `qwen/qwen3.8-flash` — only without ZDR |
| P7.5 labelling | in P7 / later | in P7 if G4 shows LLM agreement with the labels above 90 %; otherwise later |

## Risks carried by this item

| Id | Risk | Mitigation |
|---|---|---|
| RQ-5 | Which LLMs give `logprobs` through OpenRouter | 162 of 461 by the catalog on 2026-11-09; `gpt-4o-mini` with ZDR checked (SPIKE §4). The engine checks `logprobs` in the answer, otherwise `verbal` |
| RQ-7 | LM Studio may add a decisions path | Then it is one more `systemone_http` with no code |
| RQ-11 | Reasoning LLM: the first token is not a letter, `logprobs` of the answer unavailable | Mini-spike P7.3.6: reasoning off by a parameter; otherwise the model goes to `verbal` |
| RQ-12 | Ollama's default small window (`num_ctx`) truncates a long call silently | Long probe step on `systemone_http` (P7.4); `context_tokens` from the probe |
| RQ-13 | `qwen/qwen3.8-flash` (whip-insight model) has no ZDR endpoints | Called only with the per-run no-ZDR consent; the default baseline uses ZDR models |
