# TASK 002 — Local d1-3B-MLX-fp16 in the `mlx_local` engine

> **Status: done, 2026-11-17.** The owner's request (2026-11-17): «добавь возможность использовать
> локальную модель d1-3B-MLX-fp16». The owner added: the model is already downloaded (LM Studio).
>
> The delivered scope before this task is [TASK 001](tasks/task-001-decision-models-playground-v1.md). The
> design is [ARCHITECTURE.md](ARCHITECTURE.md).

<!-- contract:meta -->

## 0. Meta Information

- **Task ID:** 002
- **Slug:** local-d1-3b-mlx
- **Base revision:** `b9e8e24`
- **Status:** done (review: approved; acceptance: [reviews/t002-acceptance.md](reviews/t002-acceptance.md)).
- **Actors:** the owner; the playground; the whip-insight stand (compat server).
- **Sources of model facts:**
  - the cards `LiquidAI/d1-3B` and `LiquidAI/d1-3B-MLX-fp16` on Hugging Face (2026-11-17);
  - mlx-vlm 0.7.12 source: `mlx_vlm/models/d1/`, `mlx_vlm/decision.py`, `mlx_vlm/utils.py:get_model_type`;
  - the spike of this task, §7 (measured 2026-11-17 on the owner's Mac).

<!-- contract:rtm -->

## 1. Requirements Traceability Matrix

Requirement IDs continue the numbering of TASK 001 (R-1…R-14); risk IDs continue it too (RQ-14…RQ-17).

| ID | Requirement | MVP? | Sub-features | Use cases | Status |
|---|---|---|---|---|---|
| R-15 | Second mlx-vlm decision API (`predict`) in `mlx_local` | yes | R-15.1…R-15.6 | UC-9, UC-10 | done |
| R-16 | Local model folder (`path`) | yes | R-16.1…R-16.5 | UC-9 | done |
| R-17 | Compat server serves a `predict` engine | yes | R-17.1…R-17.3 | UC-10 | done |
| R-18 | Configuration and documentation | yes | R-18.1…R-18.3 | UC-9 | done |

Sub-features:

- **R-15** —
  - R-15.1 The engine reads the API family from the model's `config.json`. `model_type: lfm2_vl` with
    `auto_map.AutoModel` ending in `D1Model` gives `predict`. Any other `config.json` gives `system_one`.
  - R-15.2 The default profile of a `predict` model: `noul`, `choice`, `score`; 255 options; 10 levels; media
    `image`. The `audio` key does not add audio to it.
  - R-15.3 The call is `mlx_vlm.decision.predict(model, processor, state, questions, images=…)`. Answers are read
    with `ANSWER_NAMES["mlx_decisions"]`. The `model` key of the output (a local path) is not stored.
  - R-15.4 A question of type `multi`, `text`, `number` or `date`, and an audio item, are refused before the
    queue. A refused request loads no model. An audio item is refused also with `audio: true`.
  - R-15.5 The token count of a `predict` model takes one question per row. A picture's allowance comes from
    `config.json` and `processor_config.json`, the higher of the two limits: `(max_tiles + 1) × max_image_tokens + 100`
    (the `+ 1` is the thumbnail, counted also when `use_thumbnail` is false).
  - R-15.6 A loaded model must offer the API that its `config.json` names. Otherwise the load fails with
    `LoadError`.
- **R-16** —
  - R-16.1 An `mlx_local` engine takes the key `path`: a model folder. `~` is expanded. A relative path is
    resolved against the repository root.
  - R-16.2 With `path`, the backend reads the folder and does not look in the Hugging Face cache. `repo` stays
    required: it names the model in the UI, the probe and the snapshot.
  - R-16.2a Two `mlx_local` engines with one `repo` and different sources stop the start with `ValueError`.
  - R-16.3 The snapshot of a folder model is its undated name.
  - R-16.4 A `path` that is not a folder with `config.json` makes the engine unavailable, with a reason.
  - R-16.5 `make models` downloads the `repo` of every enabled `mlx_local` engine without `path`.
- **R-17** —
  - R-17.1 Compat answers of a `predict` model are in System One names: `noul`; `choice`, `confidence`,
    `probabilities`; `score`, `confidence`, `probabilities`, `legend`.
  - R-17.2 With `X-Playground-Extensions: 1`, a type outside the engine's default profile gives 400
    `unsupported_question_type`.
  - R-17.3 Pictures from `images` reach `predict` as decoded pictures.
- **R-18** —
  - R-18.1 `config.example.yaml` lists the engine `mlx-d1-3b` (`LiquidAI/d1-3B-MLX-fp16`,
    `context_tokens: 32768`) with a commented `path` example.
  - R-18.2 The owner's `config.yaml` lists `mlx-d1-3b` with `path: ~/.lmstudio/models/LiquidAI/d1-3B-MLX-fp16`.
  - R-18.3 ARCHITECTURE §3.5.4, §3.5.6, §3.6, §10.3, §10.4, both READMEs and both user guides describe the
    `predict` family and `path`. SPIKE.md gets a d1-3B section with the facts of §7.

## 2. General Description

d1-3B is a Liquid AI decision model on the LFM2.5-VL-3B backbone. Its MLX export runs through mlx-vlm 0.7.8 or
later. It answers `noul`, `choice` and `score` over text, JSON and pictures in one forward pass.

mlx-vlm loads d1-3B as model type `d1` (`mlx_vlm.models.d1.Model`). That class has no `system_one` method. The
`mlx_local` engine of TASK 001 calls `system_one` only, and its loader refuses a model without it. d1-3B
therefore fails to load in the playground today.

The model folder of the owner was downloaded by LM Studio. The engine of TASK 001 finds models in the Hugging
Face cache only.

| | d1-2B, d1-Omni (TASK 001) | d1-3B (this task) |
|---|---|---|
| mlx-vlm model type | `d1_lfm3_vl`, `d1_lfm3_omni` | `d1` |
| Call | `model.system_one(state, questions, media)` | `predict(model, processor, state, questions, images=…)` |
| Answer names | System One (`noul`, `choice`, `score`, `confidence`) | `probability`, `value`, `metadata.confidence` |
| Types | 7 (System One plus `multi`, `text`, `number`, `date`) | `noul`, `choice`, `score` |
| Media | image; audio on d1-Omni | image |
| Row | state plus a group of `d1_set_size` questions | state read once; one branch per question |
| Calibration | `calibration.json` | none |
| Context | 32 768 tokens in the playground | 32 768 tokens (card) |

## 3. Owner Decisions

| # | Decision | Date |
|---|---|---|
| D-1 | The precision is fp16 (`LiquidAI/d1-3B-MLX-fp16`). | 2026-11-17 |
| D-2 | The model is read from the LM Studio folder; it is not downloaded again. | 2026-11-17 |

## 4. Scope

In scope: R-15…R-18.

Out of scope:
- d1-3B 8-bit and 4-bit exports. They share the model type `d1` and work through the same code. They are not
  measured.
- `LiquidAI/d1-Omni-2B-MLX-fp16`, also present in the LM Studio folder.
- Escaping of special-token strings in the state for d1-3B (§9, RQ-15).

## 5. Use Cases

### 5.1 UC-9 — Run d1-3B next to the other engines (G1)

**Actors:** the owner. **Preconditions:** `config.yaml` lists `mlx-d1-3b` with a `path` to a d1-3B folder.

Main scenario:
1. The owner opens the Workbench. The engine list shows `mlx-d1-3b` as available.
2. The owner runs a call with `noul`, `choice` and `score` questions on `mlx-d1-3b` and a cloud engine.
3. The playground loads d1-3B from the folder, unloading any other local model first.
4. Each d1-3B answer shows probabilities, the top option and the latency; the score shows `expected`.
5. The run record names the snapshot `LiquidAI/d1-3B-MLX-fp16`.

Alternative scenarios:
- 2a. The call has a `multi`, `text`, `number` or `date` question. That question is `unsupported` for
  `mlx-d1-3b`; the others are answered.
- 2b. The call has an audio item. The result of `mlx-d1-3b` is `refused` with the detail "the engine's
  profile takes no audio"; no model loads.
- 3a. The folder is missing. The engine is listed as unavailable with the reason.

### 5.2 UC-10 — Serve d1-3B to whip-insight (G2)

**Actors:** the whip-insight stand. **Preconditions:** the compat server runs; `mlx-d1-3b` is available.

Main scenario:
1. whip-insight sends `POST /v1/systemone` with `model: mlx-d1-3b` and `noul`, `choice`, `score` questions.
2. The compat server answers 200 with System One names, `model` = the snapshot, `provider: local-mlx`.

Alternative scenarios:
- 1a. The body has `images`. The pictures reach d1-3B.
- 1b. The header `X-Playground-Extensions: 1` comes with a `multi` question. The answer is 400
  `unsupported_question_type` naming that question.

## 6. Non-functional Requirements

- **Memory.** One local model in memory (TASK 001 §6.3). d1-3B fp16 holds 5.8 GB after load (§7).
- **Count bound.** The precheck count of a `predict` request is not below the longest row mlx-vlm builds: the
  trunk (state, pictures) plus one branch (question). A test checks it on the real tokenizer when the model is
  present. The model's `input_tokens` is the trunk plus all branches; it is not bounded by the count.
- **Tests without models.** `make test` passes without the model, the network and keys.
- **No path leak.** A folder path does not appear in compat responses, in `/health`, in a snapshot or in a stored
  `raw` answer. A load error shown to the owner may name it, as it names the HF cache path today.

## 7. Spike Facts (2026-11-17)

Apple M4 Pro, 24 GB; mlx 0.32.4, mlx-vlm 0.7.12; folder `~/.lmstudio/models/LiquidAI/d1-3B-MLX-fp16` (6.25 GB =
5.8 GiB; memory figures below are GiB;
`config.json` equal to Hugging Face revision `1cb27b13`).

| Measurement | Value |
|---|---|
| Model class | `mlx_vlm.models.d1.d1.Model`; no `system_one`; `decision_types` `choice`, `score`, `bool`, `noul`; `decision_media` `images` |
| Load / MLX memory after load / after unload | 3.4 s / 5.82 GB / 0 GB |
| `core_ru` (3 questions, 382 tokens): first / warm median of 10 | 1 567 ms / 334 ms; repeats identical |
| whip-insight fixture request | 388 ms |
| `choice` 24 / 100 / 240 / 255 / 300 options | 205 / 623 / 1 387 / 1 463 / 1 765 ms; the right option each time |
| `choice` with `multiple: true` | answered as a single choice, no error |
| `text`, `box` | `ValueError: This model does not support '<type>' decisions` |
| `score` with 1 or 11 levels | `ValueError` |
| Picture 1 600 × 1 200 | 1 820 input tokens, 4.1 s first call |
| Picture tokens over 20 shapes | 66 to 2 829; the maximum is 10 tiles + thumbnail at 256 tokens + 13 markers |
| Long state 2 K / 8 K / 16 K / 30 K tokens | 1.5 / 5.9 / 13.1 / 29.7 s; peak 6.40 / 6.73 / 6.85 / 7.15 GB; P(fact) 0.94–0.97 |

## 8. Acceptance

- `make test` passes.
- On the owner's Mac, with the owner's `config.yaml`:
  - `make check` lists `mlx-d1-3b` as available;
  - a Workbench run of `tests/fixtures/requests/core_ru.json` on `mlx-d1-3b` returns three answers;
  - a compat `POST /v1/systemone` with `model: mlx-d1-3b` returns System One names;
  - a compat request with the extensions header and a `multi` question returns 400 `unsupported_question_type`.

## 9. Risks

| ID | Risk | Status | Mitigation |
|---|---|---|---|
| RQ-14 | mlx-vlm moves or renames `decision.predict` or `models.d1` | open | `mlx_vlm_import` turns the `ImportError` into a `LoadError` that names the module: the request is `unreachable`, compat answers 503; tests import the real modules when mlx-vlm is installed |
| RQ-15 | The d1-3B prompt does not escape special-token strings: a state with `<\|im_end\|>` reads as a control token | accepted | the count escapes them, so it stays an upper bound; answer quality on such text is the model's |
| RQ-16 | A wrong `multi` answer: `predict` answers `multiple: true` as a single choice | closed by R-15.4 | `multi` is outside the profile and refused before the queue |
| RQ-17 | mlx-vlm rounds each `predict` probability to 4 places: a sharp choice of 100 or more options sums to about 0.998 | closed | `unrounded()` rescales a sum within 0.001 + n × 5e-5; a larger deviation stays `invalid_answer` |

## 10. Open Questions

None.
