# Task 002-02: Backend, engine and compat logic

Parent: [TASK 002](../TASK.md). Plan: [PLAN.md](../PLAN.md). Depends on: [Task 002-01](task-002-01-stubs-and-tests.md).
Design: ARCHITECTURE §3.5.4, §3.5.6, §3.6, §5.3.

## Changes

1. `app/local_models.py`:
   - `decision_api(cfg)`: `predict` when `model_type` (lower case, `lfm2-vl` read as `lfm2_vl`) is `lfm2_vl` and
     `auto_map.AutoModel` ends in `D1Model`; otherwise `system_one`.
   - `model_info(cfg)`: `predict` → set size 1, picture allowance `(max_tiles + 1) × max_image_tokens + 100`
     (defaults 10 and 256); `system_one` → `d1_set_size` (default 4), picture allowance `None` (the pixel rule).
   - `resolve_folder(path)`: `expanduser`; a relative path joins the repository root.
   - `local_folders(engines)`: `{repo: folder}` of every `mlx_local` engine with `path`; `ValueError` when one repo
     has two sources.
   - `MlxBackend.local_path(repo)`: the folder when registered (it must hold `config.json`), else the HF cache.
   - `MlxBackend.info(repo)`: `model_info` of `config.json`; cached on success; a failure gives the `system_one`
     defaults and is retried after 60 s. `set_size(repo)` returns `info(repo).set_size`.
   - `MlxBackend.validate(questions, api)`: the `predict` checks of ARCHITECTURE §3.5.4 step 3.1.
   - `MlxBackend.load`: the family's method is required; `Loaded.api` is set; a folder gets revision `local` and
     the undated repo as snapshot; the audio warm-up runs for `system_one` only.
   - `make_local` passes `local_folders(config.engines)` to `MlxBackend`.
2. `app/engines/mlx_local.py`:
   - `MlxLocal.api` from `backend.info(repo)`; `default_profile()` by family; `available()` checks the folder;
     `describe()` adds `api`.
   - `precheck` passes the family to `validate`, the family's picture allowance to `allowance`.
   - `invoke` dispatches on `loaded.api`; `predict_call` builds `images` from media items and decoded pictures,
     refuses audio and `multiple`, drops `model` from the output.
   - `run` reads answers with `ANSWER_NAMES["mlx_decisions"]` for `predict`.
   - `system_one_answers(answers)` for compat.
3. `app/compat.py`: extension types also need the engine's default profile; `predict` answers go through
   `system_one_answers`.

## Acceptance

- [x] the tests of Task 002-01 pass
- [x] `make test` passes; no test needs the model
- [x] the real-tokenizer bound test passes with the d1-3B folder present
