# TASK 002 — live acceptance (2026-11-17)

Requirements: [TASK 002](../TASK.md) §8. Stand: Apple M4 Pro, 24 GB; mlx 0.32.4, mlx-vlm 0.7.12. The owner's
`config.yaml` with `mlx-d1-3b`, `path: ~/.lmstudio/models/LiquidAI/d1-3B-MLX-fp16`.

A separate app instance ran on ports 8890/8891 with `DATA_DIR` outside the repository and `HF_HUB_OFFLINE=1`. The
owner's running instance (ports 8790/8791, started before the change) was not touched.

## Results

| Check | Result |
|---|---|
| `make check` (`python -m app.check`) | `ok mlx-d1-3b mlx_local LiquidAI/d1-3B-MLX-fp16 ok` |
| `GET /api/engines` | `mlx-d1-3b`: available, `api: predict`; `mlx-d1-2b`, `mlx-d1-omni`: `api: system_one` |
| Workbench run `core_ru` on `mlx-d1-3b` + `mlx-d1-2b`, first run of a fresh process | both `ok`; d1-3B: `callback_agreed` true 0.9627, `call_topic` deposit 0.9724, `interest` expected 1.4387; load 2 466 ms, call 994 ms, 382 input tokens; snapshot `LiquidAI/d1-3B-MLX-fp16` |
| Workbench run `extensions_ru` + `core_ru` on `mlx-d1-3b` | `partial`: `products`, `application_id`, `amount`, `amount_estimate`, `visit_date` `unsupported`; the 3 core questions answered; call 327 ms |
| Stored `raw` of the d1-3B results | no `model` key, no folder path |
| Compat `POST /v1/systemone`, whip-insight fixture, `model: mlx-d1-3b` | 200; `model: LiquidAI/d1-3B-MLX-fp16`, `provider: local-mlx`; answers in System One names (`noul`; `choice` + `confidence` + `probabilities`; `score` 1.3049 + `legend`); 421 input tokens; no path in the body |
| Compat, a 1 600 × 1 200 blue picture in `images`, `choice` red/green/blue | 200; `blue` 0.9894; 1 820 input tokens |
| Compat, `X-Playground-Extensions: 1` and a `multi` question on `mlx-d1-3b` | 400 `{"code": "unsupported_question_type", "type": "multi", "question": "products"}` |
| `GET /health` (compat) | `status`, `model`, `state`, `mlx_vlm_version` only |
| "Local models" before unload / `POST /api/local/models/mlx-d1-3b/unload` | `ready`, 5.819 GB, load 2 465 ms, 4 calls / 200 `{"unloaded": true}` |

## Defect found and fixed during acceptance

| | |
|---|---|
| Symptom | The first run of `core_ru` on `mlx-d1-3b` + `mlx-d1-2b` gave d1-3B `provider_error`, detail `engine error: _DeadlockError` |
| Cause | Two worker threads imported mlx-vlm for the first time through different modules (`mlx_vlm.decision` and `mlx_vlm.models.d1_lfm3_vl.prompt`); mlx-vlm's circular package imports deadlocked. 5 of 5 fresh processes reproduced it; two `system_one` validations did not (0 of 5) |
| Fix | `mlx_vlm_import()` in `app/local_models.py`: every lazy mlx-vlm import of the app goes through one lock |
| Proof | the reproduction script: 5 of 5 OK after the fix; test `test_both_families_validating_at_once_in_a_fresh_process_do_not_deadlock`; the acceptance run above, repeated on a fresh process |
| Status | fixed |

## Tests

`make test`: pytest 924 passed, 1 skipped; Node 63 passed. The 9 real-tokenizer bound tests of d1-3B ran on the LM Studio folder
(`test_the_count_bounds_the_real_predict_row`: 3 states × no picture, a 4 096 × 256 picture, a 1 600 × 1 200
picture).

## Code review (one reviewer, exhaustive prompt) and fix round

Verdict: approved, no blocking issue. Every finding was fixed in the same round.

| ID | Finding | Status | Proof |
|---|---|---|---|
| C1 | TASK §6 bound named `input_tokens` (trunk + all branches), the code bounds the longest row | fixed | TASK §6 reworded; `test_the_count_bounds_the_real_predict_row` |
| C2 | a cached `info()` failure kept a folder that appeared on `system_one` rules for 60 s | fixed | folder failures not kept; `load` re-reads a failure; `test_a_folder_that_appears_is_read_at_once_not_from_a_cached_failure`, `test_a_cached_hf_failure_is_reread_by_load` |
| C3 | a moved mlx-vlm module was a `provider_error`, not the `LoadError` RQ-14 named | fixed | `mlx_vlm_import` maps `ImportError` to `LoadError`; `test_a_moved_mlx_vlm_module_is_a_load_error_naming_it` |
| C4 | mlx-vlm rounds `predict` probabilities to 4 places: a sharp 255-option choice failed the 0.001 sum check | fixed | `unrounded()` within 0.001 + n × 5e-5 (RQ-17); `test_rounded_predict_probabilities_are_rescaled_within_the_rounding_budget` on mlx-vlm's own `_answer` |
| C5 | `audio: true` on a `predict` engine let an audio item reach the queue | fixed | `media_items` refuses it; `test_audio_true_on_a_predict_engine_still_refuses_audio_before_any_load` |
| C6 | `local_folders` counted disabled engines and compared unresolved paths | fixed | disabled engines skipped, paths resolved; `test_local_folders_resolves_paths_and_refuses_two_sources` (link and `..` cases) |
| Q1 | the import lock did not cover the model module that `mlx_vlm.load` imports | fixed | `_import_model_module` imports it under the lock first; ARCHITECTURE §3.6 documents the lock |
| Q2, T1 | the fake ran a copy of the `predict` checks; an engine test passed with the production check removed | fixed | the fake calls `predict_checks`; with the `multiple` check planted out, the engine test now fails |
| Q3 | constants after a function body (E305) | fixed | constants moved above `ModelInfo` |
| T2 | the compat path-leak assertion could not fail; no folder model end to end | fixed | the fake d1-3B is a folder model; compat asserts the undated snapshot |
| T3 | no tests for `processor_config.json`, the failure retry, a valid folder, `download_models.py` | fixed | four tests added |
| D1 | RQ-11…RQ-13 collided with TASK 001 | fixed | renumbered RQ-14…RQ-17 |
| D2 | READMEs called audio `unsupported` for d1-3B | fixed | `refused` |
| D3 | formula, `processor_config.json`, load time, GB vs GiB, plan statuses | fixed | TASK R-15.5, ARCHITECTURE §3.5.4/§3.6, user guides, PLAN |
| D4 | bare `TASK §N` citations in code now pointed at TASK 002 | fixed | retargeted to `TASK 001 §N` in 8 files |

After the round: pytest 932 passed, 1 skipped; Node 63 passed; the race script 3 of 3 OK; a fresh-process run of
`core_ru` on `mlx-d1-3b` + `mlx-d1-2b` through `make_local` and the owner's `config.yaml`: both `ok`, d1-3B 5.819 GiB.
`ruff` reports one error, `scripts/spike/mlx_spike.py:171` F821, present before TASK 002 and not in its scope.
