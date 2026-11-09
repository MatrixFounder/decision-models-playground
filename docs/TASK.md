# TASK 001 — Decision Models Playground v1 (increments P0–P6)

> **Status: done, 2026-11-09.** Increments P0–P6 are implemented, reviewed and accepted (§8). The next
> increment, P7, is work-item [WI-1](backlog/wi-1-p7-systemone-http-and-llm-baseline.md) in
> [BACKLOG.md](BACKLOG.md).
>
> This TASK holds the requirement half of the retired `docs/DESIGN.md` (owner's answers of 2026-11-09).
> The design half is [ARCHITECTURE.md](ARCHITECTURE.md). Old `DESIGN.md §N` references in code and
> reviews resolve through the concordance in [DESIGN.md](DESIGN.md).

<!-- contract:meta -->

## 0. Meta Information

- **Task ID:** 001
- **Slug:** decision-models-playground-v1
- **Base revision:** `98f91b6` (first commit; it added the design document this TASK replaces)
- **Status:** done (P0–P6). P7 → WI-1.
- **Actors:** the owner (single user and researcher); the playground; the whip-insight stand.
- **Sources of model facts:**
  - whip-insight `docs/audit/decision-models-2026-10-16/README.md` §13 — models, ZDR, capabilities
    (cited as "audit §N");
  - whip-insight `docs/SPEC-decision-service.md` — contract `decision/1`, capability probe §5.6,
    decision points §8.5 (cited as "SPEC §N");
  - Liquid model cards on Hugging Face; OpenRouter documentation;
  - live measurements of P0: [SPIKE.md](SPIKE.md).
- **Reference layout:** `unlimited-ocr-demo` (OCR Playground): FastAPI, engine registry in
  `config.yaml`, vanilla JS with SSE, history in `runs/`, Makefile.
- **Review records:** [reviews/](reviews/) — adversarial (VDD) critique and response per increment
  (`pN-critique.md`, `pN-response.md`, planting logs) and review rounds 2–3 (`r2-*`, `r3-*`).

<!-- contract:rtm -->

## 1. Requirements Traceability Matrix

| ID | Requirement | MVP? | Sub-features | Use cases | Increment | Status |
|---|---|---|---|---|---|---|
| R-1 | Engine registry and internal contract | yes | R-1.1…R-1.4 | all | P1 | done |
| R-2 | OpenRouter Decisions engine | yes | R-2.1…R-2.4 | UC-1…UC-4 | P1 | done |
| R-3 | Local d1 engine through `mlx-vlm` | yes | R-3.1…R-3.4 | UC-1, UC-5…UC-7 | P2 | done |
| R-4 | Compat server `POST /v1/systemone` | yes | R-4.1…R-4.5 | UC-5 | P2 | done |
| R-5 | Workbench comparative run | yes | R-5.1…R-5.4 | UC-1 | P1 | done |
| R-6 | Cost and daily budget | yes | R-6.1…R-6.4 | UC-1…UC-4, UC-8 | P1, P4, P6 | done |
| R-7 | Capability probe, catalog and matrix | yes | R-7.1…R-7.5 | UC-4 | P3 | done |
| R-8 | Labelled datasets and calibration report | yes | R-8.1…R-8.5 | UC-3 | P4 | done |
| R-9 | Stability experiments | yes | R-9.1…R-9.4 | UC-2 | P5 | done |
| R-10 | Turn pointer | yes | R-10.1…R-10.4 | UC-8 | P6 | done |
| R-11 | Extraction and multi-select (d1 extensions) | yes | R-11.1…R-11.3 | UC-7 | P6 | done |
| R-12 | Audio input | yes | R-12.1…R-12.4 | UC-6 | P6 | done |
| R-13 | Security of the local UI and data | yes | R-13.1…R-13.3 | all | P1–P6 | done |
| R-14 | `systemone_http` engine and LLM baseline (goal G4) | no | R-14.1…R-14.4 | UC-1, UC-3 | P7 | moved to WI-1 |

Sub-features:

- **R-1** — R-1.1 engines from `config.yaml`, each with availability and a reason. R-1.2 the request is a
  superset of System One: `noul`, `choice`, `score` plus `multi`, `text`, `number`, `date` and `media`.
  R-1.3 one normalized `Answer` / `EngineResult` for every engine. R-1.4 failure classes per question and
  per request.
- **R-2** — R-2.1 ZDR and `data_collection: deny` by default. R-2.2 dated snapshot from the response.
  R-2.3 cost from `usage.cost`. R-2.4 root override by `OPENROUTER_BASE_URL` or engine `base_url`
  (https; http on loopback only).
- **R-3** — R-3.1 d1-2B and d1-Omni-2B, 8-bit. R-3.2 one model in memory, FIFO queue, idle unload after
  600 s. R-3.3 preflight length check against 32 768 tokens. R-3.4 dated snapshot from the HF revision.
- **R-4** — R-4.1 System One request and answer names. R-4.2 bearer token mandatory off loopback.
  R-4.3 `400 unsupported_question_type` with type and key. R-4.4 limits: 6 MB, 64 questions, context.
  R-4.5 Host and Origin checks, `GET /health`.
- **R-5** — R-5.1 question editor. R-5.2 per-engine SSE results. R-5.3 question × engine table with
  probabilities, top, latency, cost, snapshot. R-5.4 history, rerun, comparison of two runs, links, a
  draft kept in the browser.
- **R-6** — R-6.1 estimate before every run. R-6.2 daily limit `DAILY_BUDGET_USD`. R-6.3 fact from
  `usage.cost`. R-6.4 unsaved and failed runs counted, `budget_stop` in batches.
- **R-7** — R-7.1 probe steps of whip-insight SPEC §5.6.2 plus d1 extensions. R-7.2 evidence rule
  AD-18. R-7.3 the profile filters questions before calls. R-7.4 OpenRouter catalog and ZDR list.
  R-7.5 matrix with a stale flag and a long-context step behind an estimate.
- **R-8** — R-8.1 JSONL/CSV import with labels. R-8.2 import of the 56 whip-insight standard tags.
  R-8.3 synthetic dialogues. R-8.4 dataset run under the budget. R-8.5 report: whip-insight threshold,
  target threshold, curve, reliability diagram, Brier, ECE, confusion matrix, export without call text.
- **R-9** — R-9.1 up to 5 alternate formulations. R-9.2 option orders and N repeats. R-9.3 noise
  against `diff_se`, first-position pull. R-9.4 heatmap and a limit of 2 000 calls.
- **R-10** — R-10.1 whip-insight route rules. R-10.2 turns numbered from `t0`. R-10.3 one or two
  passes. R-10.4 pass costs in the estimate and the fact, the evidence turn highlighted.
- **R-11** — R-11.1 `text`/`number`/`date` with the `span` highlighted. R-11.2 Russian normalization
  marked "normalized by the playground". R-11.3 `multi` threshold per option.
- **R-12** — R-12.1 upload format detected by bytes. R-12.2 ffmpeg → WAV 16 kHz mono 16-bit, first
  `audio.max_seconds` (30) s. R-12.3 d1-Omni only. R-12.4 preset `call_kind`.
- **R-13** — R-13.1 UI on loopback only. R-13.2 trusted Host, foreign Origin refused, CSP, no framing.
  R-13.3 keys only in `.env`, call data outside git.
- **R-14** — R-14.1 `systemone_http` for Ollama, llama.cpp and the own compat server. R-14.2
  `openrouter_llm` in `logprobs` and `verbal` modes. R-14.3 probe and reports for them. R-14.4 LLM
  labelling of datasets.

<!-- contract:problem -->

## 2. General Description

### 2.1 Problem and goals

A decision model reads a state (`state`: text, JSON, pictures; d1-Omni also audio) and answers closed
questions with probabilities. It does not generate text. Examples: Jev (TypeSafe), d1 (Liquid AI).

whip-insight needs to choose which model to trust with semantic tags, call type and call checks. That
choice requires measurements on the owner's own calls, in Russian, with measured thresholds.

| Id | Goal | Outcome |
|---|---|---|
| G1 | Comparison and calibration | One call and one question set → probabilities of several models side by side; thresholds fitted on a labelled dataset |
| G2 | Local runtime for whip-insight | d1-2B and d1-Omni-2B on the Mac served as `POST /v1/systemone`, a whip-insight dev profile |
| G3 | New d1 capabilities | Audio input, value extraction with `span`, multi-select: decision points V, X, M of SPEC §8.5 |
| G4 | Decision model vs LLM | The same question through an ordinary LLM: agreement, cost, latency (WI-1) |

**Why not LM Studio.** On 2026-11-09 LM Studio did not load d1-Omni-2B-MLX
(`Model type lfm3_omni not supported`). LM Studio runs models through text generation; the d1 cards
state "chat apps and mlx_vlm.generate cannot run it" (audit §13.7). The playground calls the Python
API of `mlx-vlm` directly.

### 2.2 Connection with whip-insight

The playground writes nothing into whip-insight.

| What | Direction | How |
|---|---|---|
| Dev profile | whip-insight → playground | A `cvj.decision_configs` row pointing at the compat server (§5.5); a separate whip-insight work-item after task-049 |
| Contract | shared | The compat server speaks System One; whip-insight's `systemone` adapter maps it to `decision/1` |
| Presets | whip-insight → playground | The import script reads `taxonomy/standard-dictionaries/tags/*.json`; the stand is not touched |
| Findings | playground → whip-insight | The owner copies thresholds, wording rules, option order, pointer accuracy into SPEC §15 |
| Probes | shared rule | The same probe set and evidence rule (UC-4), so profiles match |

<!-- contract:decisions -->

## 3. Owner Decisions

| Id | Date | Decision |
|---|---|---|
| D-1 | 2026-11-09 | First version covers all four goals G1–G4 |
| D-2 | 2026-11-09 | Engines of the first version: OpenRouter Decisions; local MLX (`mlx-vlm`); Ollama and llama.cpp; LLM baseline |
| D-3 | 2026-11-09 | Data sources: manual entry and files, synthetic examples, presets from the whip-insight tag catalog |
| D-3a | 2026-11-09 | Rejected: importing tasks from the whip-insight stand by API |
| D-4 | 2026-11-09 | Stack as OCR Playground: FastAPI + vanilla JS + SSE, `config.yaml`, `runs/`, Makefile |
| D-5 | 2026-11-09 | Mac memory 16–24 GB: one local model in memory, 8-bit by default |
| D-6 | 2026-11-09 | Real transcripts may go to OpenRouter, with ZDR by default |
| D-7 | 2026-11-09 | LLM baseline: a list of models in `config.yaml` |
| D-8 | 2026-11-09 | The compat server is reachable by the whip-insight stand over the LAN, only with a bearer token |
| D-9 | 2026-11-08 | Open d1 weights (LFM Open License v1.0) are allowed in development and production (whip-insight OQ-3) |
| D-9a | 2026-11-08 | The license condition, revenue below $10 000 000 a year, is checked at every installation |
| D-10 | 2026-11-09 | The Ollama `d1:2b` spike moves from P0 to P7 |
| D-11 | 2026-11-09 | P7 moves out of this TASK into the backlog as WI-1 |

<!-- contract:out-of-scope -->

## 4. Scope

In scope: goals G1–G3, use cases UC-1…UC-8 on the engines `openrouter_decisions` and `mlx_local`.

Out of scope:

- a production service: the playground has one user, one Mac, and its UI listens on `127.0.0.1` only;
- replacing the whip-insight replay tool (I3). Replay is a measured batch run over saved tasks, by
  which the administrator switches a type; the playground is research;
- writing into whip-insight: configs, types, tasks (the links are the compat server and preset files,
  §2.2);
- importing tasks from the whip-insight stand by API (D-3);
- training or fine-tuning models;
- goal G4, engines `systemone_http` and `openrouter_llm`, LLM labelling (WI-1).

<!-- contract:use-cases -->

## 5. Use Cases

Common actors: **Owner** — the single user; **System** — the playground (UI/API on
`127.0.0.1:8790`, compat server on `:8791`); **Provider** — OpenRouter or the in-process `mlx-vlm`
model.

Common precondition for every use case: `make setup` has created `.venv`, `.env` and `config.yaml`.

### 5.1 UC-1 — Compare engines on one call (G1)

**Actors:** Owner, System, Provider.

**Preconditions:**

- At least one engine is available: `OPENROUTER_API_KEY` is set, or a local model is downloaded
  (`make models`).
- The day's budget is not exhausted.

**Main scenario:**

1. Owner pastes a transcript into Workbench as text or JSON, or loads a preset.
2. Owner defines 1–5 questions in the question editor.
3. Owner selects 3–6 engines.
4. System shows the cost estimate per engine and the remaining daily budget.
5. Owner starts the run.
6. System checks the estimate against the remaining budget.
7. System matches each question against each engine's capability profile. An unsupported question
   gets `unsupported` and is not sent.
8. System calls the engines in parallel: cloud engines up to `concurrency` each, local engines one
   call at a time.
9. System streams each engine's result as soon as it is ready.
10. System saves the run record to `runs/<day>/<id>.json` and appends a line to `runs/index.jsonl`.
11. Owner reads the question × engine table: probability bars, top answer, latency, cost, snapshot.

**Alternative scenarios:**

- **A1: estimate exceeds the remaining budget (step 6).** The run does not start; the screen names
  the budget.
- **A2: an engine without ZDR is selected (step 5).** The run starts only with the per-run no-ZDR
  consent. The checkbox clears after the start.
- **A3: a whole request of one engine fails (step 8).** The cells of that engine show
  "request: <class>". Other engines continue.
- **A4: the browser tab closes during the run (step 9).** The run completes and is saved.
- **A5: Owner reruns a saved run from History.** System shows the estimate first and asks the no-ZDR
  consent separately; the Workbench checkbox is not read. Only Workbench runs (kind `compare`) can be
  rerun; other kinds → 400.
- **A6: Owner compares two saved runs.** `/?compare=<id1>,<id2>` shows the probability difference per
  question.

**Postconditions:**

- The run record and its index line exist.
- The run's cost is counted in the day's spend.

**Acceptance criteria:**

- Every requested question key lands in exactly one of `answers` and `question_failures` of an engine
  result. Exception: when the whole request failed (`request_failure`), unanswered keys without an own
  failure are covered by it.
- Probabilities lie in [0, 1]. For `choice` and `score` they sum to 1 ± 0.001. `top` is one of the
  options. A violation gives `invalid_answer` for that question.
- `expected` of a `score` answer equals Σ i·p_i over the returned probabilities.
- A run record contains no API key. `raw` is at most 64 KB of serialized JSON.
- Every SSE stream ends with exactly one of `run_done`, `budget_stop`, `run_error`.
- `/?run=<id>` opens a run, `/?compare=<id1>,<id2>` compares two, `/?tab=history` opens History.
- The Workbench draft (transcript and questions) survives a page reload.

### 5.2 UC-2 — Test answer stability: wording, option order, repeats (G1)

**Actors:** Owner, System, Provider.

**Preconditions:** Workbench holds a state and questions; the chosen engines are available.

**Main scenario:**

1. Owner presses "Experiment…" in Workbench. System copies the state and questions to the Experiments
   screen as a snapshot.
2. Owner adds up to five alternate formulations as JSON of the same questions, for example in English.
3. Owner sets option orders (`k`, `seed`), repeats `N` and engines.
4. System computes the grid and the call count and shows the estimate.
5. Owner confirms. System runs the calls repeat by repeat: the first repeat of every cell first.
6. System saves a record of kind `experiment` and builds the report.
7. Owner reads the engine × axis → metric table and the heatmaps, and exports Markdown.

**Rules:**

| Axis | Values | Metric |
|---|---|---|
| Formulation | sets of `instructions`/`criteria`, e.g. RU and EN | ΔP of the reference option, change of the top answer |
| Option order | all permutations up to 4 options, otherwise `k` orders (default 6) | mean \|ΔP\| of the original top option, share of top changes |
| Repeats | `N` runs of one request (default 5, at most 20) | range of P (max − min); top changes between repeats |

1. An alternate formulation keeps the keys, types, option ids and level count of the base; it is
   brought to the base order of questions and options.
2. Orders change only for `choice` and `multi`. Up to 4 options → all orders (up to 24). More → `k`
   orders: the original first, the others random and fixed by `seed` and the question key.
   - Several such questions take order i each at once; a shorter list of orders cycles.
   - So the order effect of one question can include the reorder of another. One experiment should
     reorder one `choice` question.
3. Calls = formulations × orders × repeats × engines; at most 2 000.
4. Reference option: P(true) for `noul`; P(found) for `text`, `number`, `date`; the top option of the
   base mean for `choice`, `multi`, `score`.
5. Noise: |ΔP| below about two `diff_se` is within noise. `diff_se` is the standard error of the
   difference of two means, from the pooled spread inside cells.
6. Order effect: each order of a question against its original order; an order is never compared with
   itself.
7. First-position pull: the share of answers won by the option shown first, against the share a model
   without pull gives on the same orders. "Lift" is the mean rise of an option's probability when it is
   shown first. The screen flags pull when the share exceeds the expected one by ≥ 0.15 or lift ≥ 0.05.
   `multi` gets lift only.
8. Failed calls are excluded from the means and counted. A non-finite probability is no answer.

**Why.** `jev-1.13` tends towards the first option (audit §4.4) ⇒ the order axis exists. Repeat range
overstated the noise of means 3–4 times (review P5) ⇒ noise is judged by `diff_se`.

**Alternative scenarios:**

- **A1: a formulation does not match the base (step 2).** 422 naming the mismatch.
- **A2: the grid exceeds 2 000 calls (step 4).** 400 before the stream starts.
- **A3: the budget runs out mid-run (step 5).** Repeat-by-repeat order thins the grid instead of
  cutting the last formulations. The record is saved; the stream ends with `budget_stop`.
- **A4: settings change while the estimate is pending.** The run does not start.
- **A5: Owner presses "Same settings" on a report.** The form takes the report's state, formulations,
  orders, repeats and engines.
- **A6: Owner tries to rerun an experiment as a Workbench run.** 400.

**Postconditions:** a record of kind `experiment`; one History row per engine; `/?report=<id>` opens
the report.

**Acceptance criteria:**

- The call count is computed before the grid is built; a grid over 2 000 calls is refused with 400.
- The no-ZDR consent covers one experiment.
- Accepted per §8.2 (UC-2 record).

### 5.3 UC-3 — Calibrate a threshold on a labelled dataset (G1)

**Actors:** Owner, System, Provider; the whip-insight repository as a file source.

**Preconditions:** a dataset exists: an imported file, `make import-tags`, or `make synth`.

**Main scenario:**

1. Owner imports a dataset on the Datasets screen: a JSONL or CSV file with labels; questions from a
   preset or from JSON.
2. System validates the labels against the question set.
3. Owner selects engines. System shows the estimate for all items on all engines.
4. Owner confirms. System runs the items: a cloud engine up to `concurrency` calls at once, a local
   engine one call at a time, local engines one after another, each as a whole.
5. System saves a record of kind `dataset`: item ids, labels and compressed answers, without item text.
6. System writes the report files `reports/<dataset>-<day>-<id>.md|json`.
7. Owner reads the report and exports Markdown or JSON.

**Metric rules:**

1. Selective reading (`noul`, threshold t): an item is decided if max(p, 1 − p) ≥ t; the answer is
   p ≥ 0.5. Coverage = decided / all. Accuracy = correct among decided.
2. whip-insight reading (`noul`): the verdict is "yes" when P(true), rounded to 3 decimals, is ≥ t
   (SPEC §8.2.6, D-32). Agreement = verdicts matching the label among answered items.
3. "whip-insight threshold" — the t with the highest agreement; ties → the smallest t. Reported with
   accuracy and recall of "yes".
4. "Target threshold" — the smallest t whose accuracy of decided items reaches the target (default
   0.90, whip-insight §12) with at least 10 decided items.
5. `choice` and `score`: an answer counts when p_top ≥ t; top-1 accuracy, coverage, confusion matrix.
6. Thresholds 0.50…0.99, step 0.01, per engine and per answering snapshot.
7. Brier = mean (p − y)², multiclass for `choice`/`score`. ECE over 10 bins, on the top probability for
   `choice`/`score`. Reliability diagram.
8. A call without an answer is excluded from agreement and included in the coverage denominator. An
   item not asked because of the budget is excluded from both and counted as "not asked".
9. A non-finite probability counts as no answer and does not break the report.
10. A floating model id that changed snapshot mid-run gives two rows.
11. A report contains no call text: ids, labels, probabilities and metrics only.

**Why.** whip-insight asks a tag in window mode only after a lexical rule fires. It sends fragments,
while a dataset asks every item as is ⇒ a threshold from here is a starting point for whip-insight
replay, not its result.

**Dataset rules:**

- Format: `datasets/<name>/dataset.yaml` + `items.jsonl` (ARCHITECTURE §4).
- At most 5 000 items. Label errors are reported together, up to ten.
- Labels: `noul` — true/false (also yes/no, да/нет, 1/0); `choice` — an option id; `score` — a level
  index or level text. `multi`, `text`, `number`, `date` take no labels: a label for them is an error
  (`app/datasets.py` `LABELLED_TYPES`).
- CSV: the delimiter `,`, `;` or tab that occurs most in the header; a cell up to 16 MB; extra fields in
  a row and repeated columns are errors.
- Label source: `test_phrases` or `manual`. `llm:<engine_id>` arrives with WI-1.
- Tag import (`scripts/import_std_tags.py`): 56 standard tags → presets `presets/std_tags/<key>.yaml`
  with question `is_tag` (`criteria.true` = the tag description, no `limits`) and datasets from the
  tag's test phrases (`tests.positive` → true, `tests.negative` → false). When the description names
  whose speech the tag checks (26 tags), the preset keeps `tag.speaker`. A tag key matches
  `[a-z0-9_]{1,64}`.
- Synthetic dialogues (`scripts/synth_dialogs.py`): each phrase in `--per-phrase` neutral dialogues of
  `presets/synth/neutral_ru.yaml` as one turn. The speaker is `tag.speaker`, otherwise by tag folder
  (operator in "Politeness" and "Operator speech", client elsewhere). Deterministic by `--seed`.
- No line of the neutral template triggers the inclusion rules of any of the 56 tags
  (`tests/test_synth_neutral.py`).

**Alternative scenarios:**

- **A1: labels do not match the questions (step 2).** 400 listing what to fix.
- **A2: a dataset with this name exists (step 1).** 409 unless `overwrite`.
- **A3: the budget runs out mid-run (step 4).** Paid items get `budget` and are not asked; free (local)
  items continue; the record is saved; the stream ends with `budget_stop`.
- **A4: a failure outside the engine (estimate, profile).** `provider_error` for that item; the lanes
  continue.
- **A5: Owner asks for another target.** `GET /api/reports/{id}?target=` recomputes the report from the
  record.

**Postconditions:** the record, the index row (one status per engine, the dataset name), the report
files.

**Acceptance criteria:**

- "Run" takes a fresh estimate of exactly this request and asks for confirmation when the run is paid
  or uses an engine without ZDR; the button stays inactive until the estimate arrives.
- A day overrun caused by an underestimate is at most one call per paid engine.
- Accepted per §8.2 (UC-3 record).

### 5.4 UC-4 — Probe capabilities and read the matrix (G1, G3)

**Actors:** Owner, System, Provider; the OpenRouter public catalog.

**Preconditions:** the engine is configured and available.

**Main scenario:**

1. Owner opens the Capability Matrix. System lists configured engines with their last probe, then a
   collapsed list of catalog models absent from `config.yaml`.
2. Owner presses "Probe" on an engine row, optionally with "long".
3. With "long", System shows the estimate and asks for confirmation.
4. System runs the steps (table below), reading each one under the evidence rule.
5. System writes the profile to `probes/<engine_id>/<snapshot>.json` and `probes/<engine_id>/latest.json`.
6. The matrix shows each cell as ✓ served, ✗ not served, ? not checked, — impossible for this engine
   kind, with the probe date and cost.
7. Later runs filter questions by the profile.

| Step | Request | Finds |
|---|---|---|
| N, C, S | one `noul`, `choice` (3 options), `score` (4 levels) on a short state | types |
| C24, C100, C240 | `choice` with 24, 100, 240 options | `max_choice_options` |
| S10 | `score` with 10 levels | `max_score_levels` |
| — | `usage.cost` and a dated `model` in the answer | `reports_cost`, `reports_snapshot` |
| M, T, NUM, D, A | (local d1) `multi`, `text`, `number`, `date`; audio on d1-Omni | extensions |
| CAL, LONG | (on "long") a calibration request, then a state of ≈ 90 % of the context with a fact at the end | `reads_full_context` |

**Rules:**

1. Evidence rule (whip-insight AD-18, SPEC §5.3): an answer or a refusal answer → served;
   `unsupported`, `invalid_answer`, or `refused` with HTTP 400/422 → not served; network, 401–404,
   timeouts, 429, `too_long`, `skipped` → not observed, the previous value stays.
2. A size limit is the largest served size; "not served" at a size closes all larger sizes. A served
   240 means "at least": the kind's ceiling (255 options) stays in effect, the profile records 240.
3. No profile is written when no request got a 2xx answer (`no_profile`). A 200 that does not parse
   (`invalid_answer`) counts as 2xx.
4. LONG: a repeated dialogue with the fact in its last 5 %, question `noul`; P(true) ≥ 0.9 →
   `reads_full_context`. Size: fitted from N and CAL to 90 % of the engine's context. A LONG that read
   less than 75 % of the context reads as "not observed", unless the engine's byte limit stopped it. After
   `too_long`, one more attempt at 8/9 of the first, with its own budget reserve.
5. A profile confirms or narrows the capabilities of its engine kind, never widens them. It applies
   only when its kind and model equal the current `config.yaml`; otherwise the default profile applies
   and the matrix marks the row "stale".
6. "Stale" also marks a profile when a later run answered with another dated snapshot. The mark stays
   until the next probe.
7. Deviation from whip-insight: SPEC §5.6.2 writes `reads_full_context: false` without a LONG step
   (D27); the playground writes `null` ("not checked"). An export to whip-insight maps `null` → `false`.
8. Inherited limitation of SPEC §5.6.2: the fact is at the end only, and no control request exists. A
   provider that cuts the start of the state, or a model biased to "yes", passes LONG.
9. Catalog: `GET /api/v1/models?output_modalities=decisions`, `GET /api/v1/endpoints/zdr` and the full
   model list `GET /api/v1/models`, without a key. Cached in `probes/catalog.json`; refreshed by a button
   and at start when older than one day.

**Alternative scenarios:**

- **A1: a probe of this engine is already running.** 409.
- **A2: the engine has `zdr: false`.** The probe runs only with `allow_no_zdr`.
- **A3: the probe deadline (600 s) passes.** The remaining steps are `skipped`.
- **A4: the budget runs out.** The stream ends with `budget_stop`.
- **A5: a local probe is running.** Compat requests wait in the same model queue.

**Postconditions:** the profile files; a record of kind `probe` counted in the day's spend (it cannot be
opened in Workbench, rerun or compared).

**Acceptance criteria:**

- A question type the profile marks "not served" is never sent to that engine.
- The matrix columns: seven types and audio, max options, max levels, full-context read (with tokens),
  context, price, ZDR, expiration date, `usage.cost`, snapshot, probe date and cost.
- Accepted per §8.2 (UC-4 record).

### 5.5 UC-5 — Serve local d1 to the whip-insight stand (G2)

**Actors:** the whip-insight stand (skills-executor, adapter `systemone`); Owner; System (compat server
and local model manager).

**Preconditions:**

- The model is downloaded.
- For LAN access: `SYSTEMONE_TOKEN` (≥ 32 characters, ≥ 10 distinct, no surrounding spaces) and
  `COMPAT_HOST=0.0.0.0` in `.env`.

**Main scenario:**

1. Owner runs `make run` (both servers) or `make run-compat`.
2. The stand sends `POST /v1/systemone` with `Authorization: Bearer <token>` and the body
   `{model, state, questions, images?}`.
3. System checks the token before reading the body, then Host, Origin, body size, request validity
   and length.
4. System queues the call to the local model in the same FIFO queue as UI calls, loading the model when
   needed.
5. System answers with System One names, a dated snapshot, `provider: "local-mlx"` and
   `usage` with `cost: 0`.

**Contract rules** (full contract: ARCHITECTURE §5):

- Accepted types: `noul`, `choice`, `score`. Another type → 400
  `{"error": {"code": "unsupported_question_type", "type": "<type>", "question": "<key>"}}`; whip-insight
  reads it as `unsupported` for that question (AD-18). Extension types require the header
  `X-Playground-Extensions: 1`, which whip-insight does not send.
- `media` → 400: a file path from a remote client would read the Mac's files. Audio over the network is
  not accepted.
- 1–64 questions; keys and option ids match `^[a-z][a-z0-9_]{0,63}$` in full; `choice` 2–255 options;
  `score` 2–10 levels; `instructions` non-empty text.
- Body ≤ 6 MB, counted while reading; up to 4 images (PNG, JPEG, WebP), each ≤ 16 MP.
- The rendered state plus all questions fit `context_tokens`, else 413.
- `score` in the answer is the weighted level (as OpenRouter), not d1's median level.
- `GET /health` needs no token and reveals no paths, repositories or error texts.

**Why.** mlx-vlm `system_one` accepts an empty `questions` and 300 options (SPIKE §2.6), while
whip-insight relies on System One limits ⇒ the compat server validates with its own rules.

**Alternative scenarios:**

- **A1: missing or wrong token.** 401 without details.
- **A2: `COMPAT_HOST` off loopback without a token.** The process does not start.
- **A3: the request carries an `Origin` header** (a browser page). 403.
- **A4: Host is not allowed.** 400 `forbidden_host`.
- **A5: more than 8 callers wait, or the wait exceeds the engine's `timeout_s`.** 429 `busy`.
- **A6: unknown model id.** 404 `model_not_found`. Model not downloaded or not loadable, or a broken
  tokenizer → 503 `model_unavailable`.

**Dev profile row in whip-insight** (a whip-insight work-item after task-049 and decision D35):

| `cvj.decision_configs` field | Value |
|---|---|
| `adapter` | `systemone` |
| `api_url` | `http://<mac-name>.local:8791/v1/systemone` (mDNS, no IP) |
| `api_auth_header` | `SYSTEMONE_TOKEN` (the bare token; whip-insight builds `Bearer`) |
| `require_zdr` | `false` |
| `input_price_per_mtok` | `0` |
| `context_tokens` | `32768` (memory limit of a 24 GB Mac, P0) |

M-L1 (path and answer names) holds by construction. M-L2 (whether the skills-executor container on the
VM reaches `*.local`) stays a measurement of that work-item (OQ-2).

**Acceptance criteria:**

- The whip-insight fixture request `tests/fixtures/requests/whip_insight_systemone.json` sent by
  `curl` gets a System One answer (README "Quick start").
- An unsupported type gets 400 with its type and key.
- Without a token the server listens on loopback only.

### 5.6 UC-6 — Classify a call recording by audio (G3)

**Actors:** Owner, System, d1-Omni (local), ffmpeg.

**Preconditions:** ffmpeg is installed (`brew install ffmpeg`, or `FFMPEG_BIN`); d1-Omni is downloaded.

**Main scenario:**

1. Owner presses "Media…" and picks a recording or a picture.
2. System detects the format by its first bytes: WAV, MP3, M4A/MP4, OGG, FLAC, WebM, AMR, AMR-WB
   (audio); PNG, JPEG, WebP (pictures).
3. System converts audio with ffmpeg to WAV 16 kHz mono 16-bit, the first `audio.max_seconds` (30)
   seconds, and stores it as `media/<sha256[:16]>.<ext>`.
4. System shows a media chip with a player, and "first 30 s of 95 s" when the recording was cut.
5. Owner chooses the preset `call_kind` and d1-Omni, and runs.
6. With no text (or whitespace only) and media present, Workbench sends `state: null`.
7. d1-Omni answers. An engine without audio in its profile gets no call.

**Rules:**

- Preset `call_kind` holds the 7 options of whip-insight §8.4: `conversation`, `answering_machine`,
  `ivr`, `voice_robot`, `network_message`, `no_speech`, `other`, with their descriptions.
- A recording without a known length (live browser WebM) that fills the clip is marked `cut: true`.
- Uploads are never deleted automatically: saved runs name them. `make clean-media` deletes all.

**Why.** The d1-Omni card: "Long spoken passages lose much of their accuracy, most of all outside
Latin-script languages". Speech-MASSIVE: 63.4 by ear vs 85.2 as text (audit §13.5) ⇒ clip length is
capped and is an experiment axis. Synthetic speech is heard as `voice_robot` (SPIKE §2.5) ⇒ accuracy is
measured only on the owner's real recordings (RQ-3).

**Alternative scenarios:**

- **A1: HEIC or AVIF.** 400 "save as JPEG or PNG".
- **A2: any other format.** 400.
- **A3: file over 16 MB.** 413.
- **A4: no ffmpeg.** 503 with the hint `FFMPEG_BIN`.
- **A5: media with the turn pointer.** 400: the pointer reads text turns.

**Acceptance criteria:** accepted per §8.2 (UC-6 record).

### 5.7 UC-7 — Extract a value with `span`, choose several options (G3)

**Actors:** Owner, System, local d1 (d1-2B, d1-Omni).

**Preconditions:** a local d1 engine is available.

**Main scenario:**

1. Owner adds a question of type `text`, `number` (optionally with a range) or `date`, or `multi`.
2. System sends it to the local d1 engines only; other engines report `unsupported` without a call.
3. The model returns the value, `span`, `found`, and for `number` an interval p10–p90.
4. System highlights the `span` in the state with context when the state is a string and
   `state[a:b]` equals the returned text.
5. When the model returned no value, System reads the text itself and shows the reading as
   "normalized by the playground".
6. For `multi`, the table shows P(yes) per option, a threshold field (default 0.5) and the options at
   or above it.

**Normalization rules (`extract.normalized`):**

- The model's value is never replaced.
- Date: "12 ноября 2026 (года | г.)"; a weekday before or after the date, which must be the weekday of
  that date; ISO; `DD.MM.YYYY` and `DD/MM/YYYY` day first → ISO. No year, a two-digit year or a
  non-existent day → `null`.
- Number: groups by descending scale, digits or words in number order, not mixed inside a group
  ("3 тысячи 500 рублей" → 3500; "2 миллиона триста тысяч" → 2 300 000; "сто 5" → `null`). A group after
  a scale is below 1000. "тыс.", "млн", "млрд" need a number before them; "тысяча", "миллион" do not.
- Spaces and narrow spaces between thousands, a decimal comma with exact arithmetic ("1,005 тыс." →
  1005), currency or unit around ("350 000 р."). Leading "." and "," are kept (".5" → `null`). Dots
  between thousands only with two groups or more ("1.500" → `null`).
- Text longer than 80 characters → `null`. A reading error → `null`.
- The `multi` threshold reads the answer only; it is not sent with the request. An empty or invalid
  field keeps the previous threshold.
- With a JSON state, offsets count mlx-vlm's rendering: no highlight, the text only.

**Why.** P0 found that the model finds the right fragment but mlx-vlm's parser gives `null` for Russian
forms ("12 ноября 2026 года", "12.11.2026", "350 000 рублей", numbers in words) (SPIKE §2.2).

**Alternative scenarios:**

- **A1: the model value is present.** It is shown as is; no normalization.
- **A2: the text cannot be read.** `normalized` is `null`; the answer still counts as the model gave it.

**Acceptance criteria:** accepted per §8.2 (UC-7 record).

### 5.8 UC-8 — Find the evidence turn (turn pointer) (G1)

**Actors:** Owner, System, Provider.

**Preconditions:** the transcript follows "line = turn" and "Name: text".

**Main scenario:**

1. Owner ticks "Turn pointer" in Workbench.
2. System splits the transcript into turns `t0…t(N−1)`. The state of every call of the run, main calls
   too, is `{"fragments": [{"turn", "participant", "text"}]}`.
3. System runs the main questions.
4. For the preferred answer System asks the pointer question: `noul` — "yes", when P(true) rounded as
   in the route (`Math.round(p × 1000) / 1000`, D-32) is ≥ 0.5; `choice`, `score` — the top option or
   level.
5. The pointer question is a `choice` over turns with `criteria: null`. Instruction: "В какой реплике
   видно, что <question>?" (`noul`) or "В какой реплике видно: <description>?" (an option or a level;
   an option without a description uses its id).
6. B = min(240, `max_choice_options` of the profile). Up to B turns → one call. More → pass 1 over
   blocks of ceil(N / B) consecutive turns (a block is named by its first turn), then pass 2 inside the
   chosen block. P = the product of the passes.
7. The screen shows "↳ t6 Client: …" under the answer, with the turn highlighted and its P, during the
   run as well.

**Rules** (whip-insight SPEC §8.2.4 and `env-test/skills-executor/decision-run.js`):

- Turn split: a non-empty line is a turn; lines split by `\n` only.
- "Name: text" (name up to 40 characters, a space after the colon) gives the participant. A line
  "Name:" without text names the participant of the next line.
- Numbering and timecodes of VTT/SRT become turns.
- A ready `{"fragments": [...]}` (a rerun, whip-insight windows) is taken as is after a shape check. A
  turn may repeat; pointer options are distinct turns by number (`detect.js` `fragmentTurns`).
- The question text is inserted as written, without trimming; a question mark is not doubled.
- One turn → no call, P `null`. More than B² turns → the whole request of that engine is `too_long`
  without a call, the main one too. B differs per engine, so the refusal is per engine. An engine
  without `choice` in its profile → pointer `unsupported`.
- A pass failure (request or question class) fails the pointer; the answer stays. An error in the
  pointer code fails that pointer as `internal`; passes made before it stay paid and recorded.
- Money: the estimate and the reserve count the passes in the worst case. The pass cost joins the
  engine's `usage`. Each pointer has its own `cost_usd`, `latency_ms`, `passes` (only calls sent to the
  model).
- P(true) in [0.4995, 0.5): the cell shows "false" while the route rounds to 0.500 and asks the
  pointer; the row is marked "“yes” after rounding".
- The pointer instruction carries the owner's question or option description, never call text.

**Why.** The route numbers turns from `t0` (`turns.js`), and turn labels shift the pointer probability
(review P6: 0.598 vs 0.698 on the same turn) ⇒ the playground uses the whip-insight numbering.

**Alternative scenarios:**

- **A1: media present.** 400.
- **A2: a state the pointer cannot read.** 400.
- **A3: an engine fails as a whole.** The run waits for the other engines.

**Acceptance criteria:**

- `run_started` of a pointer run carries the sent request (the turns).
- A rerun sends the same turns.
- Accepted per §8.2 (UC-8 record).

## 6. Non-functional Requirements

### 6.1 Security and data

- Keys live only in `.env` (mode 600). They never reach run records, logs, API answers or the UI. A
  `.env` readable by others gives a warning at start.
- OpenRouter calls carry `provider.data_collection: "deny"` always and `provider.zdr: true` unless the
  engine has `zdr: false`.
- A model without a ZDR endpoint (by the catalog ZDR list) is marked in the UI and called only with the
  per-run consent. The consent clears at the start; a rerun does not inherit it.
- The account setting "ZDR only" (Safety Retention under ZDR, audit §13.9) is shown, not changed.
- The UI has no authentication and listens on `127.0.0.1` or `localhost` only; `APP_HOST` accepts
  nothing else.
- UI protection against other pages: trusted Host is one of those two names; a write with a foreign
  `Origin` (including `null`) or `Sec-Fetch-Site: cross-site` → 403; `X-Frame-Options: DENY`; CSP
  `default-src 'self'` and `frame-ancestors 'none'`.
- Compat server access: UC-5.
- `runs/`, `datasets/`, `probes/`, `reports/`, `media/` are in `.gitignore`: they may hold real calls.
- The Workbench draft is a copy of the call in browser `localStorage`, outside `runs/`; `make clean-runs`
  does not remove it.
- Call text in `state` is data. It is never inserted into `instructions` (the pointer instruction holds
  owner text only).
- Uploads: format by bytes; ffmpeg without a shell, local files only, a fixed demuxer; the server path
  never appears in an error answer.

### 6.2 Cost and budget

1. Estimate before every call: (`tokens_per_char` × characters of state and questions +
   `overhead_tokens`) × input price from the catalog. Default 1.5 × characters + 200 (`budget` in
   `config.yaml`).
2. The default rule is not below the fact on all 30 paid P0 calls and not above it 6 times
   (`tests/test_budget.py`).
3. Price of a model absent from the catalog: the highest decision-model price in the catalog, otherwise
   `fallback_price_per_mtok` (0.2).
4. Fact: `usage.cost` of the answer when it is a finite number ≥ 0 (`cost_source: provider`); otherwise
   tokens × catalog price (`price`); otherwise `none`.
5. `DAILY_BUDGET_USD` (default 1.0) is a finite number ≥ 0, otherwise the process does not start. A run
   whose estimate exceeds the remainder does not start.
6. Spend belongs to the day the record is saved; a run across midnight counts in the new day.
7. A run whose record was not saved (disk, permissions), a run with a failed engine and a cancelled run
   are counted from the run's call journal, in process memory.
8. A dataset run overruns the day by at most one call per paid engine.
9. Known gap: when a 5xx/524 is retried, the provider may bill the first attempt; `usage` reports only
   the second.
10. The UI shows the day's spend and limit in the header and the estimate before every run.

**Why (1–2).** Jev bills 0.95–1.65 tokens per character (choice options add service tokens), d1.1 about
0.43 (SPIKE §3) ⇒ "1 token per character" is not conservative for Jev.

### 6.3 Resources and performance

- Mac with 16–24 GB: one local model in memory (`local.max_loaded_models: 1`), 8-bit by default.
- `context_tokens` of local models: 32 768. **Why.** Memory grows with the square of the state length:
  27 765 tokens peaked at 5.6–6.1 GB and 27 s; 64K does not fit on 24 GB (SPIKE §2.4).
- Idle unload after 600 s (`local.idle_unload_s`); a manual "Unload" button.
- Local `latency_ms` is the model call only; queue, load and call times are in `timings`, so local and
  cloud latency compare.
- Heavy work (JSON parsing, preflight token counts, reports, ffmpeg) runs off the event loop shared by
  the UI and the compat server.

### 6.4 Compatibility with whip-insight

- Question keys and option ids: `^[a-z][a-z0-9_]{0,63}$` (decision/1 §4.1); `choice` 2–255 options;
  `score` 2–10 levels.
- Answer validation as decision/1 §4.3 (UC-1 criteria).
- Probe set and evidence rule as SPEC §5.6.2 / AD-18 (UC-4).
- Turn pointer rules and numbering as SPEC §8.2.4 and the route code (UC-8).
- Threshold reading as SPEC §8.2.6 (UC-3).
- Compat server: System One names and the 400 `unsupported_question_type` form (UC-5).
- Snapshot of a local model: `<repo>-<YYYYMMDD>` from the HF revision date, which whip-insight reads as
  dated (`-[0-9]{8}$`, OQ-2/M-9). Offline the snapshot has no date; a date is never invented.

### 6.5 Interface

- Screens: Workbench, Experiments, Datasets, Capability Matrix, History, Local Models. The research
  modes (pointer, extraction, multi, media) are Workbench options.
- Interface language: English or Russian (setting).
- Charts are small own SVG functions; nothing loads from a CDN.

### 6.6 Licensing

- Open d1 weights: LFM Open License v1.0 (D-9). Commercial use is allowed for a company with annual
  revenue below $10 000 000; the condition is re-checked at every installation (RQ-8).

## 7. Constraints and Assumptions

- Hardware: Apple Silicon for `mlx_local` (`mlx-vlm >= 0.7.11`, as the d1-2B-MLX and d1-Omni-2B-MLX
  cards require). P0 ran on an M4 Pro, 24 GB, macOS 26.6.2, Python 3.14.8, `mlx` 0.32.4, `mlx-vlm`
  0.7.12.
- OpenRouter Decisions is an alpha path ("Alpha: the shape may change", audit §13.4) (RQ-4).
- OpenRouter rejects requests over 250 000 bytes of text with 422; for Russian text this comes before
  the 65 536 tokens of d1.1 (≈ 52 000 tokens).
- OpenRouter answers `choice` with `multiple: true` as a plain `choice` (200) ⇒ `multi` is never sent
  to cloud engines (RQ-9).
- Assumption: transcripts follow "line = turn", "Name: text" (UC-8).
- Assumption: the owner runs one browser session; there is no multi-user state.

<!-- contract:acceptance -->

## 8. Increments and Acceptance

### 8.1 Increments

| Id | Content | Acceptance | Estimate | Done |
|---|---|---|---|---|
| P0 | Spike: `mlx-vlm` with d1-2B/d1-Omni; OpenRouter Decisions for `jev-1.14` and `d1.1` with ZDR | [SPIKE.md](SPIKE.md), key-free fixtures | 0.5–1 day | 2026-11-09 |
| P1 | Core: schemas, engine protocol, `openrouter_decisions`, Runner + SSE, Workbench, History, budget | UC-1 on three cloud models | 1.5 days | 2026-11-09 |
| P2 | `mlx_local`, model manager, compat server with token | UC-1 with a local d1; UC-5 with `curl` and the whip-insight fixture | 1 day | 2026-11-09 |
| P3 | Probe, catalog, capability matrix | UC-4 on three cloud and two local models | 1 day | 2026-11-09 |
| P4 | Datasets, whip-insight tag import, synthetic dialogues, metrics, report | UC-3 on `std_answering_machine` (59 phrases) | 1.5 days | 2026-11-09 |
| P5 | Experiments | UC-2 | 0.5 day | 2026-11-09 |
| P6 | Modes: turn pointer, extraction, multi, audio | UC-6, UC-7, UC-8 | 1.5 days | 2026-11-09 |
| P7 | `systemone_http`, `openrouter_llm`, probe and reports for them, LLM labelling; Ollama spike | moved to [WI-1](backlog/wi-1-p7-systemone-http-and-llm-baseline.md) | 3–3.5 days | — |

Every increment ended with an adversarial review, fixes and a re-check: `reviews/pN-critique.md`,
`reviews/pN-response.md`, `reviews/pN-plantings.log` (P3–P6), and rounds `r2-*`, `r3-*`.

### 8.2 Acceptance records

**UC-1 (P1, P2).** Live run of `jev-1.14`, `d1.1`, `~d1-latest` with ZDR; rerun and comparison of two
runs; 155 offline tests at P1. P2 repeated UC-1 with a local d1.

**UC-5 (P2).** `curl` with the whip-insight fixture request answered by the local model. Real-model
smoke log: [reviews/p2-smoke.json](reviews/p2-smoke.json) (`make smoke-local`).

**UC-4 (P3, 2026-11-09, $0.0034).**

- Jev 1.14, d1.1, `~d1-latest`: `noul`, `choice`, `score`, 240 options, 10 levels, `usage.cost` and a
  dated snapshot, full context read. `~d1-latest` answers with snapshot `liquid/d1.1-20261027`.
- Local d1-2B and d1-Omni: the same plus `multi`, `text`, `number`, `date`; d1-Omni also audio.
- Re-acceptance after the review ($0.0047 including a failed first round): the same types and limits.
  LONG read:
  - 29 445 tokens on d1-2B and d1-Omni (89.9 % of 32 768);
  - 52 631 tokens on d1.1 and `~d1-latest` (80 %, stopped by the 250 000-byte API limit);
  - 11 100 tokens on Jev (stopped by `max_request_bytes`).
- The first round caught a 422 "over the model's input limit" on d1.1, which introduced the byte limit
  and the `too_long` class. One LONG on `~d1-latest` did not answer within 30 s (not observed, value
  kept); its retry passed in 1.5 s.

**UC-3 (P4, 2026-11-09, `std_answering_machine`, 59 phrases, five engines, 295 calls, $0.0016, 18 s).**

| Engine | Brier | whip-insight threshold | Agreement | Threshold for 90 % | Coverage |
|---|---|---|---|---|---|
| Jev 1.14 | 0.078 | 0.57 | 94.9 % | 0.50 | 100 % |
| d1.1 | 0.219 | 0.94 | 89.8 % | 0.94 | 54 % |
| `~d1-latest` (same snapshot as d1.1) | 0.233 | 0.93 | 88.1 % | 0.96 | 44 % |
| d1-2B (MLX) | 0.198 | 0.57 | 74.6 % | 0.88 | 36 % |
| d1-Omni (MLX) | 0.153 | 0.62 | 81.4 % | 0.75 | 56 % |

- On single phrases Jev reaches 90 % at threshold 0.5; d1.1 only at 0.94 and answering half of the
  phrases.
- d1.1 vs `~d1-latest` on one snapshot is repeat spread (audit §4.3), not the model: over three runs
  agreement was 94.9–96.6 % (Jev), 88.1–89.8 % (d1.1), 88.1–91.5 % (`~d1-latest`); local models are
  deterministic.
- 59 phrases are fewer than the 100 per point that whip-insight §12 asks for: this checks the tool, not
  a production threshold.
- Report without texts: [reviews/p4-uc3-report.md](reviews/p4-uc3-report.md) and `.json`.

**UC-2 (P5, 2026-11-09).** Bank call preset, RU and EN, 6 orders, 5 repeats; Jev 1.14, d1.1, d1-2B
local; 180 calls, $0.0026, 14 s.

- Order moves `choice` by ≤ 0.02 on d1-2B and ≤ 0.0005 on cloud models; the top answer never changes
  under any permutation: no first-position pull. ("Top = first shown" at 33.3 % is not evidence by
  itself: with all 3! orders and a constant answer it equals 1/3.)
- The English wording shifts `score` by 0.04–0.05 on all three models at a difference SE of
  0.005–0.008: a wording effect, not noise. The top answer never changes.
- Repeat range of `score`: d1.1 up to 0.05–0.10, Jev up to 0.03–0.04 (as audit §4.3); the local model is
  deterministic.
- Re-acceptance after review fixes: the same 180 calls, $0.0026, the same result.

**UC-7 (P6, `demo_extraction`, d1-2B and d1-Omni).** `amount` on both: `number: null`, "триста пятьдесят
тысяч рублей" → normalized 350000. `visit_date` on d1-2B: `null`, "12 ноября 2026 года" → 2026-11-12; on
d1-Omni — the model's own value. Every `span` matched its text.

**UC-8 (P6).** Measured before the switch to `t0` numbering (review P6): turns below are `t1…tN`.

- `demo_bank_call`, Jev 1.14, d1.1, d1-latest, d1-2B: all four pointed at the same turns — agreement →
  t6 (P 0.60–0.80), topic → t3 (0.95–1.0), interest → t4 (0.47–0.98). 12 passes, $0.000164 over the main
  calls.
- The same d1-2B run after the switch to `t0`: the same turns (now t5, t2, t3), but the agreement
  pointer P is 0.598 instead of 0.698.
- Two passes, 260 turns, evidence in t187: d1.1 → t187, P = 0.975, 0.9 s, $0.00045; d1-2B → t187,
  P = 0.30, 13.8 s. Jev → `too_long` without a call: `{"fragments"}` is twice the text in characters
  (23.7 thousand vs 11.9 thousand); the body is 32 422 bytes over `max_request_bytes` 32 000.

**UC-6 (P6).** The five SPIKE §2.5 clips through upload → d1-Omni + `call_kind`: answers, probabilities
and tokens equal P0 (261, 503, 384, 237, 180 tokens). The same 30-second clip through AAC (M4A):
`voice_robot` 0.53 vs 0.52. d1-2B takes no audio by profile — no call.

### 8.3 Verification

- `make test` — Python and frontend tests without network, keys or models.
- `make smoke-local` — opt-in real local path (loads d1-2B and d1-Omni), log in `reviews/`.
- `make check` — engine availability, catalog cache, today's spend.
- Test layers and files: ARCHITECTURE §11.

## 9. Risks

| Id | Risk or question | Status | Resolution |
|---|---|---|---|
| RQ-1 | Exact Python API of `mlx-vlm` for d1-2B/d1-Omni and answer field names | closed (P0) | ARCHITECTURE §3.5; SPIKE §2.1–§2.2 |
| RQ-2 | Memory of d1-Omni-8bit and latency with audio on this Mac | closed (P0) | 2.53 GB; a 30 s clip — 273 ms (SPIKE §2.3, §2.5) |
| RQ-3 | Accuracy of d1-Omni on Russian speech and the clip length limit | open | Experiment on the owner's recordings (OQ-1) |
| RQ-4 | OpenRouter Decisions is alpha; its shape may change | accepted | The engine isolates the mapping; P0 fixtures catch a change |
| RQ-5 | Which LLMs give `logprobs` through OpenRouter | → WI-1 | 162 of 461 by catalog; `gpt-4o-mini` with ZDR checked (SPIKE §4) |
| RQ-6 | Whether the whip-insight container on the VM reaches the Mac's `*.local` (M-L2) | open | The dev-profile work-item in whip-insight (OQ-2) |
| RQ-7 | LM Studio may add a decisions path | → WI-1 | Then it is one more `systemone_http` without code |
| RQ-8 | LFM license on another installation | open | Revenue check per the whip-insight README (OQ-3) |
| RQ-9 | OpenRouter silently ignores `multiple: true` (200, plain `choice`) | closed | `multi` is never sent to cloud engines |
| RQ-10 | Long state on local d1: memory grows with the square of the length; 64K does not fit in 24 GB | closed | `context_tokens` 32 768 on the whole prompt (rendered state and questions); fp16 not enabled; long calls go to the cloud |
| RQ-11 | Reasoning LLM: the first token is not a letter | → WI-1 | Mini-spike P7.3.6 |
| RQ-12 | Ollama's small default `num_ctx` truncates a long call silently | → WI-1 | Long probe step on `systemone_http` |
| RQ-13 | `qwen/qwen3.8-flash` (whip-insight model) has no ZDR endpoints | → WI-1 | Per-run no-ZDR consent only |

<!-- contract:open-questions -->

## 10. Open Questions

- OQ-1 — What accuracy does d1-Omni reach on the owner's real Russian recordings, and at which clip
  length does it drop? Blocks: using audio `call_kind` as a whip-insight decision point. Owner: the
  owner (recordings needed). Tracks RQ-3.
- OQ-2 — Does the skills-executor container on the whip-insight VM reach the Mac's `*.local` name
  (M-L2)? Blocks: the whip-insight dev-profile row (UC-5). Owner: the whip-insight work-item after
  task-049 and D35. Tracks RQ-6.
- OQ-3 — Does the LFM license condition hold on the next installation? Blocks: running open d1 weights
  there. Owner: the owner. Tracks RQ-8.
- P7 questions (Ollama, llama.cpp, LLM model ids, P7.5 timing) — owner decisions listed in WI-1.
