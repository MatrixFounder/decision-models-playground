# Task 002-01: Stubs and tests of the `predict` family and model folders

Parent: [TASK 002](../TASK.md). Plan: [PLAN.md](../PLAN.md). Requirements: R-15, R-16, R-17.

## Goal

The structure of TASK 002 exists as importable stubs. Tests state R-15…R-17 and fail on the stubs.

## Changes

| File | Stub |
|---|---|
| `app/schemas.py` | `EngineSpec.path: str \| None`; a validator refuses `path` on a kind other than `mlx_local` |
| `app/local_models.py` | `ModelInfo(api, set_size, image_tokens)`; `decision_api(cfg)`; `model_info(cfg)`; `resolve_folder(path)`; `local_folders(engines)`; `Loaded.api = "system_one"`; `MlxBackend(snapshots, folders=None)`; `MlxBackend.info(repo)`; `validate(questions, api="system_one")` |
| `app/engines/mlx_local.py` | `MlxLocal.api`; `predict_call(loaded, state, questions, media)`; `system_one_answers(answers)` |
| `tests/conftest.py` | `FakeBackend.predict_repos`, `FakeBackend.info`, `FakeBackend.validate(questions, api)`; `FakePredictModel` that answers in `mlx_decisions` names and runs mlx-vlm's `decision.predict` checks |

## Tests

| Test | File | Requirement |
|---|---|---|
| `decision_api` gives `predict` for the d1-3B config and `system_one` for d1-2B; it agrees with mlx-vlm's `get_model_type` | `test_mlx_backend.py` | R-15.1 |
| `model_info`: set size 1 and picture allowance 2 916 for d1-3B; `d1_set_size` for d1-2B | `test_mlx_backend.py` | R-15.5 |
| `validate(…, "predict")` refuses `multiple`, `text`, `number`, `date`, 11 levels, with `question '<key>'` | `test_mlx_backend.py` | R-15.4 |
| `load` of a `predict` config sets `Loaded.api`; a model without `predict` is a `LoadError`; no audio warm-up | `test_mlx_backend.py` | R-15.6 |
| A folder model: `local_path` is the folder, revision `local`, undated snapshot, no date lookup | `test_mlx_backend.py` | R-16.2, R-16.3 |
| `local_folders`: `~` and relative paths resolved; one repo from two sources is `ValueError` | `test_mlx_backend.py` | R-16.1, R-16.2a |
| A `path` without `config.json` makes the engine unavailable with a reason | `test_mlx_engine.py` | R-16.4 |
| `EngineSpec` refuses `path` on `openrouter_decisions` | `test_mlx_engine.py` | R-16.1 |
| The engine's default profile of a `predict` model: 3 types, `image` only, also with `audio: true` | `test_mlx_engine.py` | R-15.2 |
| A run on a `predict` engine: answers normalized from `mlx_decisions`; `raw` has no `model` key | `test_mlx_engine.py` | R-15.3 |
| A run with `multi` and `text` questions: those are `unsupported`, the rest answered; no load for a run with only them | `test_mlx_engine.py` | R-15.4 |
| `invoke` of a `predict` model refuses an audio item with `this checkpoint takes no audio` | `test_mlx_engine.py` | R-15.4 |
| The precheck of a `predict` engine counts one question per row and the 2 916-token picture allowance | `test_mlx_engine.py` | R-15.5 |
| `system_one_answers` maps `bool`, `choice`, `score` | `test_compat.py` | R-17.1 |
| Compat with `model: mlx-d1-3b`: 200 in System One names; `images` reach `predict` as pictures | `test_compat.py` | R-17.1, R-17.3 |
| Compat with the extensions header and `multi` on a `predict` engine: 400 `unsupported_question_type` | `test_compat.py` | R-17.2 |
| On the real d1-3B tokenizer: the precheck count is not below the `predict` row (skipped without the model) | `test_mlx_backend.py` | R-15.5, TASK §6 |

## Acceptance

- [x] every stub imports; `make test` collects the new tests
- [x] the new tests fail on the stubs, the existing tests pass
