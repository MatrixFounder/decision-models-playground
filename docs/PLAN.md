# Development Plan: TASK 002 — Local d1-3B-MLX-fp16

Requirements: [TASK 002](TASK.md). Design: [ARCHITECTURE.md](ARCHITECTURE.md) §3.5.4–3.5.6, §3.6, §5.3, §10.3–10.4.

<!-- contract:sequence -->

## Task Execution Sequence

### Stage 1: Structure, Stubs and Tests (Red)

- **Task 002.1** — Stubs of the `predict` family and of model folders; tests of R-15…R-17 that fail on the stubs.
  - Use Cases: UC-9, UC-10
  - Description File: [task-002-01-stubs-and-tests.md](tasks/task-002-01-stubs-and-tests.md)
  - Priority: Critical
  - Dependencies: none
  - [x] `EngineSpec.path`, `ModelInfo`, `decision_api`, `local_folders`, `Loaded.api`, `system_one_answers` exist
        as stubs and import
  - [x] `FakeBackend` and a fake `predict` model in `tests/conftest.py`
  - [x] new tests run and fail on the stubs; the 881 existing tests still pass

### Stage 2: Logic (Green)

- **Task 002.2** — Backend, engine and compat logic.
  - Use Cases: UC-9, UC-10
  - Description File: [task-002-02-logic.md](tasks/task-002-02-logic.md)
  - Priority: Critical
  - Dependencies: Task 002.1
  - [x] all tests of Task 002.1 pass; `make test` passes
  - [x] the count bound holds on the real d1-3B tokenizer (test runs when the folder is present)

### Stage 3: Configuration, Documentation, Acceptance

- **Task 002.3** — `config.example.yaml`, the owner's `config.yaml`, `make models`, READMEs, user guides, SPIKE.md;
  live acceptance of TASK §8.
  - Use Cases: UC-9, UC-10
  - Description File: [task-002-03-config-docs-acceptance.md](tasks/task-002-03-config-docs-acceptance.md)
  - Priority: High
  - Dependencies: Task 002.2
  - [x] config files, `scripts/download_models.py`, Makefile
  - [x] READMEs, user guides, SPIKE.md §2.7
  - [x] live acceptance of TASK §8 recorded in `docs/reviews/t002-acceptance.md`

## Schedule

<!-- contract:schedule -->

```json
{
  "schema": "plan-schedule/v1",
  "tasks": [
    {"id": "002.1", "title": "Stubs and tests", "stage": "Red", "est": 2, "deps": [], "status": "done"},
    {"id": "002.2", "title": "Backend, engine, compat logic", "stage": "Green", "est": 3, "deps": ["002.1"], "status": "done"},
    {"id": "002.3", "title": "Config, docs, acceptance", "stage": "Docs", "est": 2, "deps": ["002.2"], "status": "done"}
  ]
}
```

<!-- generated:plan-gantt-start -->
<!-- generated:plan-gantt-end -->
