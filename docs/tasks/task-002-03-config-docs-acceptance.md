# Task 002-03: Configuration, documentation, live acceptance

Parent: [TASK 002](../TASK.md). Plan: [PLAN.md](../PLAN.md). Depends on: [Task 002-02](task-002-02-logic.md).
Requirements: R-16.5, R-18; TASK §8.

## Changes

| File | Change |
|---|---|
| `config.example.yaml` | `path` in the key list; engine `mlx-d1-3b` (`LiquidAI/d1-3B-MLX-fp16`, `context_tokens: 32768`, commented `path`) |
| `config.yaml` (owner, not in git) | engine `mlx-d1-3b` with `path: ~/.lmstudio/models/LiquidAI/d1-3B-MLX-fp16` |
| `scripts/download_models.py`, `Makefile` | `make models` downloads the `repo` of each enabled `mlx_local` engine without `path` |
| `README.md`, `README.ru.md` | engine table and local models: d1-3B, `path` |
| `docs/manuals/user-guide.md`, `.ru.md` | local models: d1-3B types and media; `path` |
| `docs/SPIKE.md` | §2.7 d1-3B: the facts of TASK §7 |

## Live acceptance (owner's Mac)

- [x] `make check` lists `mlx-d1-3b` as available
- [x] a Workbench run of `core_ru.json` on `mlx-d1-3b` returns three answers
- [x] compat `POST /v1/systemone` with `model: mlx-d1-3b` returns System One names
- [x] compat with the extensions header and `multi` on `mlx-d1-3b` returns 400 `unsupported_question_type`
- [x] the results are recorded in `docs/reviews/t002-acceptance.md`
