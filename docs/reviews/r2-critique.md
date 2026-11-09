# VDD Critique: Decision Models Playground — round 2 (P1 + P2 fixes)

Every `path:line` is relative to the frozen copy
`/private/tmp/claude-501/-Users-sergey-dev-projects-decision-models-playground/dcadea80-b412-4ad7-b818-372950c81133/scratchpad/review-r2`.
Lines in mlx-vlm are relative to `.venv/lib/python3.14/site-packages/mlx_vlm/models/d1_lfm3_vl/`.

## 1. Executive Summary
- **Verdict**: WARNING. There are no CRITICAL findings, but there is 1 HIGH and 3 MEDIUM, and many LOW remain. This is not an approval.
- **Confidence**: High. Every HIGH and MEDIUM finding was reproduced in a throwaway copy. Token figures come from the real d1-2B `tokenizer.json`; no model was loaded and no network call was made.
- **Summary**: Most round-1 fixes are real. 95 of 115 plantings turned their named tests red. The original HIGH holes (media dict in `instructions`, EPS/Ghostscript, unbounded question text) are closed. The `indent=2` fix closed the old undercount but opened a new hole:
  - **HIGH, new.** The row is rendered in full, on the event loop, before any size bound applies. A 40 KB nested `state` makes the compat server build an 800-million-character string, which added 1.7 GB of RSS in 0.55 s. The cost grows with the square of the nesting depth, so a 200 KB body reaches about 20 G characters.
  - **MEDIUM.** The token count described as "exact" ignores mlx-vlm's escaping of special tokens. A state that passes both compat checks reaches the model as about 47K tokens instead of 32.6K. On the UI path the model sees 8× the counted tokens.
  - **MEDIUM.** A hung MLX call still keeps the process alive through SIGTERM ×2 and SIGINT. §8 says the opposite.
  - **MEDIUM.** The frontend suite has no timeout. A planted regression in the SSE terminal handling hung `node --test` for 5 minutes instead of failing.

  Eight named tests do not go red when their fix is reverted (§5).

## 2. Test Evidence (Exit Bar)
- **Command**: `cd <copy> && /Users/sergey/dev-projects/decision-models-playground/.venv/bin/python -m pytest -q -p no:cacheprovider -rs`, then `cd <copy> && node --test tests/js/*.test.mjs`.
- **Result**:
  - pytest: exit 0, `369 passed, 1 skipped in 10.20s`. The skip is `tests/test_normalize.py:32: box only`, the same legitimate skip as in round 1. The tests that need the real tokenizer (`tests/test_mlx_backend.py:174`, `:181`) ran, because the tokenizer is in the HF cache.
  - node (v24.22.1): exit 0. 11 tests, 11 pass, 0 fail.
- **Source**: run by the reviewer (Python 3.14.8, fastapi 0.162.2, starlette 1.10.0, uvicorn 0.54.0, pydantic 2.14.3, mlx-vlm 0.7.12, tokenizers 0.23.3, Pillow 13.0.0). No network, no keys, no model loads.
- **Tree state**: `fingerprint.sh review-r2` printed `c2162dfa2f83df85`, the expected value, both before the review and after it. My pytest run created `app/__pycache__`, `app/engines/__pycache__` and `tests/__pycache__`; all three were born at 13:39:25, during my first run. I deleted them, so the copy has no cache directories now. `-p no:cacheprovider` kept `.pytest_cache` from being created. Every probe and planting ran in `.../scratchpad/review-r2-plant`, whose fingerprint after all restores is also `c2162dfa2f83df85`, with an identical file list. My scripts and logs are in `.../scratchpad/r2adv/`.

**Planting summary** (details in §5). There were 115 plantings, one at a time. The harness addressed each file by its full path, required the pattern to occur exactly once, checked that the sha256 changed after the edit, ran the named test(s), and then restored the file and checked the original sha256. All 115 restores verified.

| Outcome | Count |
| :--- | :--- |
| Red | 95 |
| **Hung, killed by hand**: P1-L12 (node), X3 (pytest past its own 60 s timeout) | 2 |
| Green, as expected (no test was claimed, or I was probing a gap) | 10 |
| **Green against a named test** | 8 |

## 3. Risk Analysis
Severity: CRITICAL / HIGH / MEDIUM / LOW. Confidence per finding: High / Medium / Low.

| Severity | Confidence | Category | Issue | Impact | Recommendation | Verification |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **HIGH** | High | Security / Memory (new, introduced by the P2 fix) | **The row is rendered in full, on the event loop, before any bound.** The compat "cheap" check computes `len(row_text(state, parsed))` (`app/compat.py:170`). `row_text` → `render_state` → `json.dumps(state, indent=2)` (`app/engines/mlx_local.py:85-86`, `app/local_models.py:148`) runs synchronously before any size limit. Indentation costs O(depth²): `"["*d + "]"*d` has 2d compact characters and renders to 2d² characters. Python 3.14.8 parses and dumps depth 100,000 without a `RecursionError` (measured). **Measured through the compat app:**<br>• depth 10,000 (20 KB state): 200,000,910 characters, 0.09 s, max RSS 90 → 924 MB;<br>• depth 20,000 (40 KB): 800,000,910 characters, **0.55 s on the loop, max RSS 924 → 2,602 MB**, and only then 413.<br>By the square law, depth 100,000 (a 200 KB body, far under the 6 MB `max_body_mb`) is about 2×10¹⁰ characters. The UI path does the same rendering on the loop in `precheck` (`app/engines/mlx_local.py:169`). There, `row_text` is evaluated before `asyncio.to_thread`, and the multi-GB string then goes to the tokenizer. | One request from the whip-insight container or any token holder, or from **any local process in the default loopback mode with no token**, makes the Mac allocate tens of GB and blocks the shared event loop. Swap, a jetsam kill or a Metal OOM takes down the UI and the compat server together. Browsers are stopped by the `Origin` 403. The same O(d²) blow-up hits a UI run's record on disk (see the `store.py:66` row). | Bound the input before rendering. Either cap the nesting depth (say ≤ 32) and the compact length (≤ `context_tokens`) first, or compute the rendered length without building the string: walk the value, add `2·depth` per line, and stop at the limit. Do any remaining rendering and tokenizing in a worker thread. | Literal inputs: `state = json.loads("["*20000 + "]"*20000)` gets 413 with peak RSS growth under 50 MB and under 50 ms on the loop. A separate test pins the depth cap. Moving the cap must turn the test red. |
| **MEDIUM** | High | Robustness / Spec (§8) | **A hung MLX call still prevents exit.** §8 says: "Поток MLX — демон: зависший вызов не держит процесс. Остановка ждёт выгрузку не дольше 5 с (0.5 с после второго сигнала)" ("The MLX thread is a daemon: a hung call does not hold the process. Shutdown waits for the unload at most 5 s, 0.5 s after the second signal"). **Probe:** `python -m app --compat-only` with a backend whose `system_one` blocks forever and one POST in flight. The process was still alive 8 s after SIGTERM, 15 s after a second SIGTERM and 3 s after SIGINT; only SIGKILL ended it. An asyncio call-graph dump shows `Server.serve` stuck in `asyncio.Server.wait_closed()` (uvicorn `server.py:329`). Python 3.12+ waits there for open connections, so `serve()` never leaves `asyncio.gather` (`app/__main__.py:83`), and `aclose` (`:86`) never runs. Even past that, the request task sits in `_on_thread`, which absorbs every cancellation (`app/local_models.py:314-318`). `asyncio.run`'s final cancel-all would therefore hang as well. | `make stop` (SIGTERM) and Ctrl-C cannot stop a process whose MLX call hangs, for example after a Metal fault or swap death (see the HIGH and MEDIUM memory rows). `test_aclose_returns_while_a_call_hangs` (`tests/test_local_models.py:199`) proves only that `aclose()` returns. | Set `timeout_graceful_shutdown` on both `uvicorn.Config`s. In `_on_thread`, stop absorbing cancellation once `self.closing` is set, and abandon the future. After a bounded `aclose`, call `os._exit(0)` if tasks remain. | A subprocess test with a hanging fake backend (the `app.local_models.make_local` hook in `r2adv/hang_child.py` shows how): SIGTERM twice, then the exit code within 10 s. |
| **MEDIUM** | High (counts) / Medium (memory) | Memory / Spec (§7.3 "точный подсчёт", exact count) | **The token count ignores mlx-vlm's special-token escaping.** mlx-vlm feeds the state through `plain()`, which inserts U+200B after the first character of every special-token string (`prompt.py:746`, `:614-626`). `count_tokens` encodes the raw text (`app/local_models.py:249`), where each such string is one token. Measured with the real d1-2B tokenizer (122 special tokens): `<\|pad\|>` is 7 characters, 1 token counted and 5 read; `<\|fim_suf\|>` is 11 characters, 1 counted and 8 read.<br>• **Compat**: a state of 7,250 × U+20000 plus 3,600 × `<\|pad\|>` has 32,450 characters and counts 32,600 tokens, so it passes the character check and the token check. Through the compat app with the real tokenizer it got **200 and reached the model**, which reads **47,000** tokens (+44%).<br>• **UI path** (no character check): 32,000 × `<\|fim_suf\|>` counts 32,000 tokens; the model reads **256,000**. | SPIKE §2.4 / §8 says memory grows with the square of the row: 27.7K tokens peaked at 5.6–6.1 GB. My projection is about 16 GB at 47K, which is swap territory on 24 GB, and certain failure at 256K. RQ-10's guarantee does not hold. | Count what mlx-vlm feeds the model: apply the same escaping before encoding. Build the regex from the tokenizer's special added tokens (or call `prompt.plain` with a tokenizer shim), then encode. | Real-tokenizer test (skipped when absent): the state above, through compat, gets 413. A unit test checks that the counted text equals `plain(state_block(state))[:-2]`. |
| **MEDIUM** | High | Tests / Process | **`node --test` has no timeout.** `make test` runs `node --test tests/js/*.test.mjs` (`Makefile:67`) with the default `--test-timeout=0`. The helper waits on a promise that only a terminal event resolves (`tests/js/api.test.mjs:19-22`). Planting P1-L12 (the `fatal` on stream end removed, `web/js/api.js:77`) **hung for 302 s until I killed it**. | The JS half of the round-1 MEDIUM "no timeout" remains. The exact regression the round-1 fix guards against freezes `make test` instead of failing it. | Add `--test-timeout=10000` to `Makefile:67`, or `{ timeout }` per test, plus a `Promise.race` timeout inside `collect()`. | Re-plant P1-L12: the suite fails within the timeout. |
| **LOW** | High | Tests / Robustness | **pytest-timeout cannot end a hang inside `_on_thread`, and `MlxThread.submit` after shutdown returns a future that never resolves** (`app/local_models.py:96-104`; the Executor contract says it should raise `RuntimeError`). Planting X3 removed the `closing` guard (`:341`). `test_aclose_returns_while_a_call_hangs` then submitted to the stopped thread. The 60 s timeout fired ("Timeout", then "F"), but teardown could not cancel the task: the rerun of that single test hung 150 s and the full-suite run 500 s, until they were killed. | A future regression on this path freezes CI instead of failing it. The same mechanism as the MEDIUM shutdown row. | Make `submit` raise after shutdown. Make `_on_thread` honour cancellation once closing. | Re-plant X3: the test fails, and the process ends within 70 s. |
| **LOW** | High | Contract (§9.1 JSON errors) | **`images` that is not a list but has no `len` gives a 500 `text/plain`**: `len(body.get("images") or [])` (`app/compat.py:170`) raises `TypeError` for `"images": 5`. The probe got `500 text/plain "Internal Server Error"`. A long base64 *string* instead of a list makes `len` count characters, so the reply is a 413 instead of a 400. | It breaks the "every error is `{"error":{"code}}`" rule. whip-insight retries a 5xx. | Validate `images` (a list of strings) before the cheap check. | Tests: `images: 5` and `images: "<1 MB string>"` each get 400 `invalid_request`. |
| **LOW** | High | Contract | **A missing or corrupt `tokenizer.json` is a 500 `text/plain`.** `tokenizers.Tokenizer.from_file` raises a bare `Exception` (verified for both a missing and a corrupt file). `precheck` catches only `(OSError, ValueError)` (`app/engines/mlx_local.py:170`), and compat catches nothing else around `precheck` (`app/compat.py:179-186`). The probe got `500 text/plain`. In the UI it shows as `provider_error`, "engine error: Exception". | A partly downloaded model shows `available` (`disk_gb` ≠ None) but answers with a 500 instead of a 503 `model_unavailable`. | Catch `Exception` around tokenizer loading and map it to `LoadError`. | Test: `count_tokens` raises `Exception`, then 503 `model_unavailable` (compat) and `unreachable` (UI). |
| **LOW** | High | Robustness / Spec (§7.1 "Нормализация не бросает исключений", normalization never raises) | **`OverflowError` escapes the "never raising" normalisation.** `math.isfinite(10**400)` raises `OverflowError` (`app/engines/base.py:56`, `:74`). The `except` in `normalize_answer` (`:94`) lists only `TypeError, ValueError, KeyError, AttributeError`. `_count` and `_cost` (`app/engines/openrouter_decisions.py:41`, `:47`) have the same gap. Probe: a 401-digit integer in `answers.*.noul` or in `usage.input_tokens` makes the engine **raise**. `Runner.call` then records `provider_error` and the billed cost is lost. | It needs an odd alpha API (RQ-4), but the stated contract is broken, including the "usage first" promise. | Catch `ArithmeticError` (or `Exception`) in `normalize_answer`. Guard `_count`/`_cost` with `try` / `isinstance(x, float)`. | Parametrised test with a 401-digit integer in an answer and in usage: one `invalid_answer`, cost kept. |
| **LOW** | High | Config | **`COMPAT_HOST=::1` is accepted but unusable.** `is_loopback("::1")` is True (`app/settings.py:19-25`, `:68`). Compat appends the bare `::1` to `allowed_hosts` (`app/compat.py:69-71`), but Starlette compares the bracketed `[::1]`. The probe got 400 for `/health` and for POST. Separately, a LAN-IP `Host` that is not listed gets Starlette's `400 text/plain "Invalid host header"`, outside the JSON error contract. That is documented only as a comment in `config.example.yaml`. | The same class as the round-1 `APP_HOST=::1` finding, which was fixed only for the UI. whip-insight would read a plain-text 400 as a whole-request refusal. | Bracket IPv6 when appending, or limit `COMPAT_HOST` to names and IPv4. Optionally replace the plain 400 with the JSON `{"error":{"code":"forbidden"}}`. | Test: `compat_host="::1"` and base URL `http://[::1]:8791` give 200. |
| **LOW** | High (code reading) | UX / Data loss (partial fix) | **A second "Open" overwrites the draft backup.** `openRun` calls `stashDraft()` (`web/js/app.js:236`), which copies whatever `DRAFT_KEY` holds into `BACKUP_KEY` unconditionally (`:253-257`). After the first `openRun`, `onDraftChange()` (`:249`) has already saved the opened run as the draft. Opening a second run, or rerunning (`:227`), therefore replaces the user's real draft with run A. | The round-1 LOW ("opening a run overwrites the draft") is only half fixed: open two runs and the draft is gone. | Stash only when no backup exists, or mark drafts that come from an opened run and never stash those. | Manual or JSDOM test: edit draft D, open run A, open run B, restore. D comes back. |
| **LOW** | Medium | Privacy / UX (§18) | **Consent for a rerun comes from a checkbox the History tab cannot show.** `rerunRun` reads `#allow-no-zdr` (`web/js/app.js:231`), which sits in the Workbench and is visible only for the *Workbench* selection (`:136-139`, `web/index.html:67`). A rerun of a no-ZDR run cannot get consent from History; a box ticked for a pending Workbench run is consumed by a rerun with different engines. A rerun also spends money without an estimate (the round-1 recommendation). | It fails safe (refused) in the common case. The consent can be attributed to the wrong run. | Show a confirmation with the estimate and a per-rerun ZDR checkbox in History. | Manual check. |
| **LOW** | High | UI | **`setQJson(true)` throws with no message when the editor holds a duplicate key.** `toRequest` now throws (`web/js/app.js:289`), inside a click handler with no `catch`. | The JSON toggle silently does nothing. | Wrap it in `try` and show `e.message` in `#run-error`. | Manual check. |
| **LOW** | Medium | UI | **Comparing extraction answers shows nonsense.** `x.top` is `null` for text/number/date, so `pa` is undefined and `d` is NaN (`web/js/history.js:107-113`). The cell reads `null: — → 0.000 · Δ NaN`. | A cosmetic error in a research view. | Compare `extract.value` / `found` for extraction types. | JS test of the compare cell. |
| **LOW** | High | Memory (UI-only) | **Records are saved with `indent=1`** (`app/store.py:66`), so a deep JSON state in a UI run produces an O(d²) record file. Depth 20,000 gives about 400 M characters on disk. | Self-inflicted by the local user; it fills the disk and slows History. | Save records compactly, or bound the state depth at `RunIn` validation. | Test: a depth-20,000 state is refused at 422, or the record stays under 1 MB. |
| **LOW** | Medium | Logic | **The row counts every question, but mlx-vlm packs one row per `d1_set_size` group** (`d1.py:136`, `:173`). `row_text` adds all of up to 64 questions to one row (`app/engines/mlx_local.py:85-86`). | It errs on the safe side: a long state plus more than 16 questions gets a false 413 although every real row fits. | Count the state plus the largest group of `set_size` questions. | Test with 20 questions and a 30K state: 200 when each group fits. |
| **LOW** | Low | Memory | **Allowances can undercount.** For a `multi`, mlx-vlm writes each option id a second time as a marker (`prompt.py:821`); the code allows 4 tokens per option (`app/engines/mlx_local.py:46`). Picture tokens are fixed at 1,700, but `D1_VISION_MAX_PIXELS` in the environment raises d1's cap (`d1.py:132`). | Bounded and small; compat serves `multi` only with the extensions header. | Count the marker text for `multi`. Read the pixel cap from the environment or the config. | Unit test: a `multi` with 255 options of 64-character ids. |
| **LOW** | Medium | Storage | **The directory is not fsynced after `os.replace`** (`app/store.py:65-70`). §10.1 promises fsync plus rename. | After a power loss the rename can be lost while the fsynced index line survives. `get()` then returns None for a counted run. | `os.fsync` on the directory fd after `os.replace`. | Review. |
| **LOW** | High | Tests (vacuous) | **`test_nested_state_is_measured_as_the_model_reads_it` (`tests/test_compat.py:222`) stays green with compact counting (H3a).** The whip-insight questions add 910 characters, so the *compact* row is already 33,605 > 32,768. | The compat side of the round-1 HIGH has no guard. The engine and backend tests (H3b–d) do go red. | Use a nested state whose compact row is about 20K and whose rendered row is over 32,768. | Re-plant H3a: red. |
| **LOW** | High | Tests (vacuous) | **`test_images_are_decoded_off_the_event_loop` (`tests/test_compat.py:260`) stays green when decoding runs on the loop (M1c).** `TestClient` runs the app in a portal thread, never the main thread. | The round-1 MEDIUM "decoded on the event loop" has no guard. | Record the loop thread from inside the app (`threading.get_ident()` in a dependency) and compare against that. | Re-plant M1c: red. |
| **LOW** | High | Tests (vacuous) | **`test_an_adopted_load_is_not_unloaded_as_idle_at_once` (`tests/test_local_models.py:161`) stays green without `last_used` on adoption (L3).** With no earlier use, `unload_idle` falls back to a fresh `loaded_at`. | The round-1 LOW scenario (a repo last used an hour ago) is untested. | Set `local.st(D1_2B).last_used = time.time() - 3600` before the cancelled load. | Re-plant L3: red. |
| **LOW** | High | Tests (vacuous) | **`test_deadline_starts_after_the_queue` (`tests/test_openrouter_engine.py:301`) stays green with the deadline set before the semaphore (L14).** Three calls of 0.3 s finish inside the 1.0 s deadline (`2 × 0.5 + 0`). | The round-1 LOW fix is unguarded. | Four or more calls, or a 0.3 s deadline per call. | Re-plant L14: red. |
| **LOW** | High | Tests (vacuous) | **`test_odd_answer_costs_one_question_and_keeps_the_cost` (`tests/test_openrouter_engine.py:201`) never reaches the `except` wrapper (E1).** Every patch is caught by an explicit check first. | The "never raises" wrapper is untested; the OverflowError row shows the gap. | Add a payload that only the wrapper catches, for example the 401-digit integer once `ArithmeticError` is caught. | Re-plant E1: red. |
| **LOW** | High | Tests (circular pin) | **`test_the_row_bound_is_the_configured_context` (`tests/test_compat.py:240`) pins the fixture's own `context_tokens: 32768` (`tests/conftest.py:25`).** Changing `DEFAULT_CONTEXT` (H4d) or `config.example.yaml` to 65,536 (H4e) stays green. | The memory bound in the shipped config is unpinned. | Assert `config.example.yaml`'s `context_tokens` and `DEFAULT_CONTEXT` directly, as `test_max_request_bytes_is_pinned_in_the_example_config` does. | Re-plant H4d/H4e: red. |
| **LOW** | High | Tests | **A 413 from the model's tokenizer is untested in compat (M4a).** Removing `except TooLong` (`app/compat.py:181-182`) leaves all of `tests/test_compat.py` green. The fake tokenizer (2 characters per token) always loses to the character check. | Round-1 MEDIUM P7 is still open. With real text in emoji or CJK, an unmapped `TooLong` would surface as an uncaught 500. | Use a fake tokenizer of 2 tokens per character in a compat test. | Re-plant M4a: red. |
| **LOW** | High | Tests | **Old vacuities kept.** Removing `valid_id` (`app/store.py:137`) keeps `test_get_refuses_anything_but_an_indexed_id` green (L22). Removing the rows cache (`app/store.py:96`) keeps `test_rows_cache_follows_the_file` green (L23). Removing the explicit extraction type checks keeps `test_malformed_extractions_are_invalid_not_a_crash` green (L27), because the wrapper catches the `ValidationError`. | The tests name properties they do not check. | `get()` must not touch the index for a non-ULID (spy `rows`); count `read_text` calls for the cache. | Re-plant L22/L23: red. |
| **LOW** | High | Tests | **Several fixes have no test at all**, all confirmed green when planted: reconcile at start-up (`app/main.py:75`, C3); SSE `allow_nan=False` (`app/runner.py:160`, L11); the broad `refresh_quietly` catch (`app/catalog.py:76`, L28); the view-bound stream handler (`web/js/app.js:173`, H); compare `Object.hasOwn` and ULID order (`web/js/history.js:89`, `:100`, L24a/b; the claimed `questions.test.mjs` covers only `toRequest`); force-exit on a second signal (`app/__main__.py:75-76`, L22d). | Regressions on these paths go unnoticed. | One small test each. The handler and compare logic would need extracting into pure functions for `node --test`. | Re-plant each one: red. |
| **LOW** | High | Process | **The smoke log proves less than its labels say.** Check 2 "answers from audio" asserts only `status == "ok"` and that a load happened (`scripts/smoke_local.py:75-76`). The answering-machine clip came back as `network_message` 0.639 (`docs/reviews/p2-smoke.json`), and audio that was silently dropped would pass the same way. Check 1 calls a snapshot "dated" when its last character is a digit (`scripts/smoke_local.py:64`). | The audio path on the real stack is not actually proven. | Record `usage.input_tokens` with and without the clip and assert that the audio adds about 12.5 tokens a second. Match `-\d{8}$`. | A new smoke log. |
| **LOW** | High | Docs | `LocalConfig.max_waiting` comment says "compat 503" (`app/schemas.py:246`); the code returns 429 (`app/compat.py:194`), as §8/§9.1 say. | Misleads the operator. | Fix the comment. | Review. |
| **LOW** | Medium | Docs / Decisions | P2 "accepted as a decision", FIFO reloads when two models alternate: it is not written in `docs/DESIGN.md` (§8 says nothing about thrash). P1 decision "`state` may be a list, as OpenRouter accepts it": §6.1 records it, but `docs/SPIKE.md` has no measurement of an array state. | Decisions without a home, or without evidence. | Add one line to §8. Mark the array claim as taken from the API reference, or measure it. | Doc review. |
| **LOW** | Low | UI | `compat_address` uses `socket.gethostname()` (`app/main.py:45`), not Bonjour's `LocalHostName`. On a DHCP-named Mac they differ, so the address shown can be wrong for `*.local`. | A wrong URL in whip-insight's profile. | Use `scutil --get LocalHostName` + `.local`. | Manual check. |
| **LOW** | Medium | Robustness | `POST /api/local/models/{id}/unload` waits on the lock with no bound (`app/main.py:217`, `app/local_models.py:389`). | Behind a hung call, the UI request hangs forever. | Use `asyncio.timeout` and answer 409/503 `busy`. | Test with a blocked fake call. |

## 4. Round-1 verification
Status values: **verified** (fix real and its named test red when planted), **partially**, **not fixed**, **regressed**.

### P1 — HIGH / MEDIUM
| Round-1 finding | Status | Planting → named test |
| :--- | :--- | :--- |
| HIGH: the editor loses `multiple: true` | verified | A1 (`questions.js:22`) → node red; A2 (`main.py:227` raw preset) → `test_preset_with_d1_multiple…` red |
| An unsaved run is not charged to the budget | verified (in memory only, as §19 says) | B (`runner.py:141`) red; X4 (`budget.py:24` ignores unsaved) red |
| A torn index line swallows the next run | verified | C1 (`store.py:82`) red; C2 (reconcile no-op) red; C3 (start-up `reconcile()` removed) **green, no test** |
| A request failure stamps `invalid_answer` | verified (UI cell `results.js:70-71` untested) | D (`schemas.py:186`) red |
| An odd 200 crashes the engine | partially (OverflowError row) | E2/E3/E4 red; E1 (`normalize_answer` wrapper removed) **green** |
| Clickjacking | verified | F (`main.py:97`) red |
| Rerun inherits no-ZDR consent | verified (server); UI consent path weak (LOW row) | G (`main.py:191`) red |
| Stream events go to the open old run | fixed by code reading, no test | H (`app.js:173` `mine = () => true`) **green** (declared manual) |
| Key checked only on success paths | verified | I1 (key in detail), I2 (key in raw) → red |
| No terminal-event test, no timeout | verified for pytest; partially overall (JS hang, X3 hang) | J1 (`run_error` suppressed) red after the 60 s timeout; J2 red; J3 (task never created) red after 60 s |
| APP_HOST check untested | verified | K (`settings.py:57`) red |
| No frontend tests | partially (11 tests, but no timeout: L12 hung) | see L1–L3, L12 |

### P1 — LOW
| Round-1 finding | Status | Planting → named test |
| :--- | :--- | :--- |
| Structured `instructions` turn into a string | verified | L1 → node red |
| A duplicate key or option id is lost silently | verified (`setQJson` silent throw, LOW row) | L2, L3 → node red |
| Opening a run overwrites the draft | **partially**: a second open overwrites the backup | not planted (declared manual; defect found by reading) |
| `data_collection: deny` dropped without ZDR | verified | L4 red |
| `user_id` in `raw` | verified | L5 red |
| `::1` passes, then the UI is unusable | verified for the UI; same class in compat (LOW row) | K red |
| `testserver` trusted | verified | L6 red |
| POST without Origin / `Origin: null` | verified | L7 (Sec-Fetch-Site), L8 (null accepted) → red |
| NaN or negative cost and budget | verified; SSE `allow_nan` untested | L9, L10 red; L11 **green** (declared untested) |
| A stream with no terminal event hangs the UI | verified in code; its test **hangs** when reverted | L12 → hung 302 s, killed |
| `ConnectTimeout` not retried | verified | L13 red |
| Deadline spent in the semaphore queue | fixed in code; **named test vacuous** | L14 **green** |
| `BOX_INDEX` too broad | verified | L15 red |
| Second-round 400 untested | verified | L16 red |
| 64 KB `raw` cap untested | verified | L17 red |
| `max_request_bytes` boundary unpinned | verified | L18a (+5000) red; L18b (example 37000) red |
| Upper tolerance unpinned | verified | L19a (0.05) red; L19b (0.0005) red |
| 2 s retry / local day unpinned | verified | L20, L21 red |
| Traversal test vacuous | **not fixed** (code safe; `valid_id` still unguarded) | L22 **green** |
| Record and index line not atomic | partially (no directory fsync) | C2 red |
| Index re-read on every keystroke | fixed in code; test does not prove caching | L23 **green** |
| `refresh_quietly` catches too little | verified by reading (no test claimed) | L28 green (expected) |
| Compare: prototype keys, A/B order | fixed in code; the claimed test does not cover `history.js` | L24a, L24b **green** |
| `getRun` errors hidden | verified by reading (`app.js:226`, `history.js:71-76,88-93`, `app.js:393`) | not planted (manual) |
| Empty structured `instructions` | verified | L25 red |
| A `config.yaml` typo is ignored | verified | L26 red |
| Extraction crashes on a malformed field | verified (outcome); explicit checks untested | L27 green (the wrapper covers it) |
| Module-level `app = create_app()` | verified (none in `app/main.py`) | — |
| Hard-coded `PYENV_PYTHON` | verified (`Makefile:5`) | — |
| `-q` in `addopts` | verified (`pytest.ini` has none) | — |
| ZDR box stays ticked | verified (`app.js:206-211`) | — |

**P1 accepted as decisions.** All five are written where claimed:
- Catalog start-up refresh: §13.2.
- `state` as a list: §6.1, though there is no SPIKE evidence (LOW row).
- Draft in `localStorage`: §18 and `README.md:71`.
- `too_long` vs `context_length`: §7.2.
- Unbilled first attempt of a retry: §19.

All five are sound.

### P2 — HIGH / MEDIUM
| Round-1 finding | Status | Planting → named test |
| :--- | :--- | :--- |
| HIGH: an `instructions` dict becomes a file path or URL | verified | H1a (`compat.py:154`) red; H1b (`mlx_local.py:184`) red |
| HIGH: EPS → Ghostscript | verified | H2 (`compat.py:241` no `formats=`) red |
| HIGH: nested state counted compactly | fixed, but **the fix opened the new HIGH** (unbounded render) and the compat test is vacuous | H3a **green**; H3b, H3c, H3d red |
| HIGH: question text unbounded | partially (special-token undercount; circular bound pin) | H4a, H4b, H4c red; H4d, H4e green |
| Pictures on the loop, queue unbounded | verified (≤ 4 × 16 MP, `max_waiting`); decode-off-loop test vacuous | M1a, M1b, M1d, M1e red; M1c **green** |
| Chunked body read whole | verified | M2 red |
| No deadline; shutdown waits on the queue | **partially**: waiting is bounded, but the process still does not exit with a hung call (MEDIUM row) | M3a, M3b, M3c red |
| Compat error mapping untested | partially (tokenizer `TooLong` → 413 untested) | M4b, M4c, L4, L5, L5b, L13 red; M4a **green** |
| `MlxBackend` / `SnapshotDates` untested | verified | M5a (invented date), M5b (`clear_cache`), M5c (warm-up), M5d (`RuntimeError`) → red |

### P2 — LOW
| Round-1 finding | Status | Planting → named test |
| :--- | :--- | :--- |
| A second cancel frees the lock mid-load | verified | L1 (old shield) red; X1 (no adoption) red |
| A cancelled failing load stays `loading` | verified | L2 red |
| An adopted load is unloaded as idle at once | fixed in code; **test vacuous** | L3 **green** |
| `/health` hides load errors | verified | L4 red |
| A non-`ValueError` failure gives a text 500 | partially (`images: 5` and a missing tokenizer still give a text 500) | L5, L5b, L5c red |
| No Host check; `/health` too verbose | verified (`::1` broken, LOW row) | L6a, L6b red |
| `KEY_RE` `$` lets `\n` through | verified | L7 red |
| `state` may be any JSON value | verified | L8 red |
| Error bodies echo request values | verified | L9 red |
| `multiple: "false"` makes a multi | verified | L10 red |
| Bearer scheme case-sensitive | verified | L11 red |
| Token strength = length only | verified | L12 red |
| Load error paths reach LAN callers | verified | L13 red |
| `disk_gb` walks the disk every time | verified | L14 red |
| `describe()` calls MLX | verified | L15 red |
| A refused request evicts and loads first | verified | L16 (precheck removed) red |
| Offline snapshot lookup stalls every load | verified | L17a, L17b red |
| A failed warm-up blocks Omni | verified | L18 red |
| mlx-vlm tokenizer caches leak | verified | L19 red |
| `latency_ms` includes queue and load | verified | L20a, L20b red |
| Compat address shown as `0.0.0.0` | verified by reading (hostname caveat, LOW row) | — |
| `Content-Length` with Unicode digits | verified by reading (`compat.py:92`) | — |
| The one-thread test passes with a pool | verified | L21 red |
| Lifecycle untested | partially (force-exit untested) | L22a, L22b, L22c red; L22d **green** |
| Chunked limit / auth before size unpinned | verified | M2, L23 red |
| "Refused before decoding" unchecked | verified | L24 red |
| Fakes too lenient | verified (`mlx_check` in the fakes; real-tokenizer tests ran) | — |
| README / §9.1 `media` | verified (§9.1, `README.md:7`) | — |
| `make stop` misses compat-only | verified (`Makefile:58-60`) | — |
| Live acceptance not repeatable | verified (`scripts/smoke_local.py`, log present); weak check 2 (LOW row) | — |

**P2 accepted as decisions.**
- FIFO reload when models alternate: the decision is sound, but it is **not written in DESIGN** (LOW row).
- Image allowance of 1,700 tokens: written in §7.3. It is sound for the default cap, but `D1_VISION_MAX_PIXELS` can raise the cap (LOW row).

## 5. Evidence notes

### Probes
Run in `review-r2-plant` with `FakeBackend`, unless a row says otherwise. Probe files were deleted afterwards.

| Probe | What | Result |
| :--- | :--- | :--- |
| Depth blow-up, standalone | `json.dumps(json.loads("["*d+"]"*d), indent=2)` | d = 1,000 / 5,000 / 20,000 → 2 M / 50 M / 800 M characters; the last took 0.21 s at 1.83 GB max RSS. Depth 100,000 parses and dumps without a `RecursionError`. |
| Depth blow-up, compat | the whip-insight request with that state | d = 10,000: 413 after 0.09 s, RSS 90 → 924 MB. d = 20,000: 413 after 0.55 s, RSS 924 → 2,602 MB. |
| Hung call at shutdown | `r2adv/hang_child.py` (`python -m app --compat-only`, a `system_one` that blocks forever) | Alive after SIGTERM (8 s), SIGTERM (15 s) and SIGINT (3 s). Call graph: `Server.wait_closed` (uvicorn `server.py:329`) ← `app/__main__.py:83`. The request task is in `_on_thread` (`app/local_models.py:316`). |
| Special tokens | the real d1-2B `tokenizer.json`, mlx-vlm's escaping replicated | Compat-shaped state: 32,450 characters, 32,600 counted, 47,000 read. Through compat with the real tokenizer: 200, model called. UI-shaped state: 352,000 characters, 32,000 counted, 256,000 read. |
| `images: 5` | compat | `500 text/plain` (`TypeError` at `app/compat.py:170`) |
| Tokenizer missing | `count_tokens` raises `Exception` | compat `500 text/plain`. `Tokenizer.from_file` raises a bare `Exception` for both a missing and a corrupt file. |
| Big integer | a 401-digit int in `answers.*.noul` / `usage.input_tokens` | The engine raised `OverflowError` in both cases |
| `COMPAT_HOST=::1` | `TestClient("http://[::1]:8791")` | 400 `Invalid host header` for `/health` and POST. A LAN IP gives the same `text/plain` 400. |
| Compact row of the nested test | the whip-insight questions plus the round-1 state | 910 + 32,695 = 33,605 > 32,768: the compact count alone already gives 413 |

### Plantings
Harness: `r2adv/plant.py` (specs `r2adv/spec.py` → `r2plants.json`; results `r2plant_results.json`, log `r2plant_log.txt`). Each planting ran only its named tests, with `-x`. Plantings marked "tests/" ran the whole suite.

| Result | Plantings |
| :--- | :--- |
| **Red** (95) | P1: A1 A2 B C1 C2 D E2 E3 E4 F G I1 I2 J1 J2 J3 K L1 L2 L3 L4 L5 L6 L7 L8 L9 L10 L13 L15 L16 L17 L18a L18b L19a L19b L20 L21 L25 L26. P2: H1a H1b H2 H3b H3c H3d H4a H4b H4c M1a M1b M1d M1e M2 M3a M3b M3c M4b M4c M5a M5b M5c M5d L1 L2 L4 L5 L5b L5c L6a L6b L7 L8 L9 L10 L11 L12 L13 L14 L15 L16 L17a L17b L18 L19 L20a L20b L21 L22a L22b L22c L23 L24. Extra: X1 X2 X4. |
| **Hung** (2) | P1-L12 (node, killed at 302 s); X3 (pytest past its 60 s timeout, killed at 500 s; the single-test rerun X3b hung 150 s) |
| **Green against a named test** (8) | P1-E1, P1-L14, P1-L22, P1-L23, P1-L27, P2-H3a, P2-M1c, P2-L3 |
| Green, expected: no test was claimed, or I was probing a gap (10) | P1-C3, P1-H, P1-L11, P1-L24a, P1-L24b, P1-L28, P2-H4d, P2-H4e, P2-M4a, P2-L22d |

No red result came from a syntax, import or name error: the tails were checked. All 115 edits applied, and all 115 restores matched the original sha256.

## 6. Hallucination Check
- [x] **Files**: I confirmed every cited file in a listing of the copy. That includes `app/{compat,local_models,main,runner,schemas,settings,store,budget,catalog,__main__}.py`, `app/engines/{base,mlx_local,openrouter_decisions}.py`, `web/js/{app,api,history}.js`, `web/index.html`, `tests/{test_compat,test_local_models,test_openrouter_engine,test_store,conftest}.py`, `tests/js/api.test.mjs`, `scripts/smoke_local.py`, `Makefile`, `pytest.ini`, `docs/reviews/p2-smoke.json` and `README.md`. The mlx-vlm files (`prompt.py`, `d1.py`) were read and not changed.
- [x] **Line Numbers**: I printed each cited line with `sed -n` and compared it with the code quoted. The checked lines are:
  - `app/compat.py:69,75,92,154,170,175,179-186,194,241`
  - `app/engines/mlx_local.py:46,47,85-86,89,169,170,184`
  - `app/local_models.py:96-104,148,249,314-318,341,389,423`
  - `app/__main__.py:73-76,83,86`
  - `app/engines/base.py:56,94`
  - `app/engines/openrouter_decisions.py:41,47`
  - `app/settings.py:19-25,68`
  - `app/store.py:66,69,96,137`
  - `app/schemas.py:246`
  - `app/main.py:42-47,75,217`
  - `web/js/app.js:136-139,173,206-211,223-236,249,253-257,286-289`
  - `web/js/history.js:89,100,107-113`
  - `web/index.html:67`
  - `Makefile:5,58-60,67`
  - `pytest.ini:6`
  - `tests/js/api.test.mjs:19-22`
  - `tests/test_compat.py:222,240,260`
  - `tests/test_local_models.py:161,199`
  - `tests/test_openrouter_engine.py:201,301`
  - `tests/test_store.py:65,84`
  - `tests/conftest.py:25`
  - `scripts/smoke_local.py:64,75`
  - mlx-vlm `prompt.py:577,584,614-626,746,821` and `d1.py:132,136,173`
