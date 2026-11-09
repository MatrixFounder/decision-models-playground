# VDD Critique: P5 — experiments (grid, shared batch engine, stability metrics, Experiments screen)

Reviewer: adversarial, fresh context. Tree under review: `scratchpad/review-p5` (frozen), scope `reviews/p5-scope.diff`
(23 files) plus the code it calls (Runner.call/split, Budget, Store, stream_events, engines' estimate_cost/profile,
normalize_answer). All `path:line` below are relative to the frozen tree. Probes and plantings ran only in copies
(`scratchpad/review-p5-plant`, `scratchpad/review-p5-exp`); scripts and logs are in
`/private/tmp/claude-501/-Users-sergey-dev-projects-decision-models-playground/dcadea80-b412-4ad7-b818-372950c81133/scratchpad/p5adv/`
(`test_zz_p5probe.py`, `probe_text.py`, `test_zz_p5loop.py`, `probe_estimate_cost.py`, `expect_share.py`,
`plant.py`, `plant.log`, `plant-x1.log`).

## 1. Executive Summary
- **Verdict**: WARNING
  - No CRITICAL finding and the full suites pass, but two HIGH and five MEDIUM logic/test findings remain, so the
    exit bar of SKILL.md §2 is not met. WARNING is not an approval.
- **Confidence**: High (every HIGH and MEDIUM finding was reproduced end to end through the API in a copy, measured,
  or proven by a planting; browser-only LOW findings are read from the code: Medium there)
- **Summary**: The batch refactor keeps P4's money rules: 12 of 13 batch plantings go RED in the dataset suite (the
  13th, B9, was unguarded already in P4), and I found no observable behaviour difference. The weak spots are in the
  new code: the stability report crashes (500, permanently) for any experiment whose request carries a `text`,
  `number` or `date` question answered by a local d1 (the shipped `demo_extraction` preset with the default 5
  repeats), and the headline "top shown first" metric is compared with 1/n even when the design is not balanced —
  with more than 4 options (original order + k−1 random) an order-invariant model is reported at 0.5 against a
  "chance" of 0.2, and the screen marks it as a pull to the first option, which is exactly the jev-1.13 question
  the axis exists to answer. Cycled order lists also dilute the order effect with self-comparisons, a formulation
  keeps its own option order when the order axis is off, and the estimate re-serializes the same state for every
  call on the event loop (4.7 s stall measured).

## 2. Test Evidence (Exit Bar)
- **Command**: in the frozen tree, `PYTHONDONTWRITEBYTECODE=1 WHIP_INSIGHT=/Users/sergey/dev-projects/whip-insight /Users/sergey/dev-projects/decision-models-playground/.venv/bin/python -m pytest -p no:cacheprovider -q`
  (run twice, the second with `-rs`) and `node --test --test-timeout=10000 tests/js/*.test.mjs` (node v24.22.1)
- **Result**: pytest exit 0 — `681 passed, 1 skipped in 33.36s` (and `33.71s`); the skip is
  `tests/test_normalize.py:32: box only`, `tests/test_synth_neutral.py` ran. node exit 0 — `tests 47, pass 47,
  fail 0`. Logs: `/private/tmp/claude-501/-Users-sergey-dev-projects-decision-models-playground/dcadea80-b412-4ad7-b818-372950c81133/scratchpad/p5adv/pytest-frozen.log`,
  `/private/tmp/claude-501/-Users-sergey-dev-projects-decision-models-playground/dcadea80-b412-4ad7-b818-372950c81133/scratchpad/p5adv/node-frozen.log`.
- **Source**: run by the reviewer
- **Tree state**: fingerprint `208fdef2cc02db2d` before the review, `208fdef2cc02db2d` after both test runs,
  `208fdef2cc02db2d` at the end (`reviews/fingerprint.sh`). No `__pycache__` or `.pytest_cache` in the frozen tree
  at any check; the tree directory's mtime is unchanged (19:19). Both copies were verified at `208fdef2cc02db2d`
  after every probe and after all plantings.

## 3. Risk Analysis
Severity: CRITICAL / HIGH / MEDIUM / LOW. Confidence per finding: High / Medium / Low.

| # | Severity | Confidence | Category | Issue | Impact | Recommendation | Verification |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| F1 | **HIGH** | High | Logic / crash | The stability report crashes on extraction questions. A local d1 answers `text`/`number`/`date` with `probabilities = {"found": p}` (`app/engines/base.py:165-166`); `options_of` returns `[]` for these types (`app/stability.py:33-38`); `mean_of(…, [])` is `{}` (not None), so `ref({})` → `top_of({}, [])` → `max()` of an empty sequence (`app/stability.py:44`), reached from the repeats loop as soon as a cell has 2 answered repeats (`:70-73`) and from the formulation loop as soon as there are 2 wordings (`:113-116`). One such question fails the whole report: every engine and every other question. | Probe E1 (API, fake local backend): `run_done` carries `report_error: "ValueError: max() iterable argument is empty"`; `GET /api/reports/{id}` → **500** for `json` and `md` — permanently, since the report is rebuilt from the record; History "Open", the Experiments list and `/?report=` all fail. Path in the shipped UI: Workbench → preset `demo_extraction` (`presets/demo_extraction.yaml:21-37`: text, number, number, date) → "Эксперимент…" → d1-2B, default 5 repeats. `probe_text.py`: crash for all three types with ≥2 repeats or ≥2 formulations. | Treat a type without options explicitly: no axis metrics for `text`/`number`/`date` (or measure `found` as a two-outcome P), and make `top_of` total (return None on empty). The report should say "no probabilities to compare" for those questions. | A test: experiment with the `demo_extraction` questions on `mlx-d1-2b`, repeats 2 and one more formulation → `run_done.report_error is None`, `GET /api/reports/{id}` 200 (json and md), the multi question's metrics present. |
| F2 | **HIGH** | High | Logic / metric faithfulness | "Top shown first" is compared with `1/len(options)` (`app/stability.py:104`) although the shown orders are not balanced over first positions in two designs the grid produces: (a) more than 4 options — the original order plus k−1 random ones (`app/experiments.py:101-115`), so the option listed first in the base is first in 1 + (k−1)/n of k orders; (b) a shorter list cycled over `n_orders` that is not a multiple of its length (`app/experiments.py:143`, DESIGN `docs/DESIGN.md:621`). The share counts every answered repeat against `orders[o % len]` (`app/stability.py:95-100`), so the design's own imbalance is read as position bias. | Probe E3 (API): 5 options, k=6, a model that always answers `a` regardless of order → share **0.5** vs "chance" 0.2 (the seed-7 orders happen to put `a` first 3 of 6 times). In expectation (`expect_share.py`): n=5 k=6 → 0.333 vs 0.2; n=7 → 0.286 vs 0.143; n=10 → 0.25 vs 0.10 — all above the screen's warn threshold `share > 1.5 × chance` (`web/js/experiments.js:302`). Probe E2b: a 2-option question cycled over 5 orders → 0.6 vs 0.5. A model whose favourite is any other option is under-reported (1/6 vs 1/5) and a real pull can hide. This is the axis meant to answer "jev-1.13 leans to the first option" (DESIGN §11). | Either make the design balanced (for n > 4: rotations or a Latin square so each option is first equally often; cycle only whole periods) or compute the baseline from the shown orders: for each answered repeat, the indicator that an order-invariant reference answer (e.g. the top of the formulation's pooled mean) is shown first; report share vs that baseline. Keep 1/n only when the design is balanced and say which baseline was used. | E3 as a test: 5 options, k=6, order-invariant model → `first_position_share == first_position_chance` (no warn); E2b likewise; the always-first model still gives share 1.0. |
| F3 | **MEDIUM** | High | Logic / metric faithfulness | Order comparisons of a cycled question include the original against itself: `for o in range(1, n_orders)` (`app/stability.py:89`) runs over the global `n_orders`, while a shorter question shows `orders[o % len(orders)]`, so every `o` with `o % len == 0` compares the original order with the original order (pure repeat noise) and repeated orders are counted several times. The lockstep cycling (`app/experiments.py:142-143`) also changes every choice question's order at once, so question A's "order effect" includes question B's reorder. | Probe E2 (API, two choice questions of 3 and 2 options, a model that always picks the option shown first): 3-option flip share 0.8 = 4/5 (correct), 2-option flip share **0.6** (true 1.0) and |ΔP| 0.36 (true 0.6) — 2 of its 5 "other orders" are the original. The noise floor comparison then calls a real effect smaller than it is. | Compare each distinct order of the question once: iterate `o` in `range(1, len(orders))` over the cells whose `o % len(orders)` equals it (pool them), never an order with itself; document the lockstep confound or vary one question at a time. | E2 as a test: the 2-option question's `flip_share == 1.0`, `dp_mean == 0.6`, `comparisons == 1` per formulation. |
| F4 | **MEDIUM** | High | Logic / grid | A formulation keeps its own option order (and question order) unless the order axis rewrites it: validation compares option ids as sets (`app/experiments.py:94`), and `plan()` sends `q` unchanged for questions not in `orders` and iterates `qs.items()` in the formulation's key order (`app/experiments.py:143`). With orders off (or for a formulation typed with options or questions in another order), the formulation effect is wording + order. | Probe E4: base `{credit, deposit}`, `en` `{deposit, credit}`, orders off → `base|o0|r0` shows `[credit, deposit]`, `en|o0|r0` shows `[deposit, credit]`; the report attributes the difference to wording. | Normalise every formulation to the base's question order and option order (`reorder(q, list(base[k].criteria))`) before applying the order axis. | Test: E4 → both cells show `[credit, deposit]`; a formulation with questions in another key order sends them in the base order. |
| F5 | **MEDIUM** | High | Performance / availability | The estimate and the run rebuild and re-serialize the same state for every call on the event loop: `plan()` builds a DecisionRequest per (formulation, order) (`app/experiments.py:141-148`), `Batch.estimate()` calls `estimate_cost(req.subset(keys))` for every job (`app/batch.py:77-84`), again per call in `one()` (`app/batch.py:112`) and `Runner.call` builds `subset` again; the endpoints are `async def` with this work inline (`app/main.py:411-428`); the 2 000-call cap is checked only after the plan is built (`app/experiments.py:156-160`). The UI asks for a fresh estimate on every edit (`web/js/experiments.js:117-145`). The compat server shares this loop (review P4 F12). | Measured in a copy (`test_zz_p5loop.py`, ticker on the loop): JSON state 435 KB, 1 engine, 1 920 calls → `POST /api/experiments/estimate` stalls the loop **4.70 s**; an over-cap grid is refused only after a **1.61 s** stall. `probe_estimate_cost.py`: 2.2 MB state → plan 8.7 s + 16.8 s per paid engine. Only 96 of the 1 920 requests are distinct. A text state is cheap; a JSON state (Workbench JSON mode) is not. | Count the grid arithmetically before building it; estimate once per distinct request and multiply by repeats; run `plan`/`estimate` in `asyncio.to_thread` as the report already is; cache `text_size` per request. | A test like `test_the_report_endpoint_builds_off_the_event_loop`: `estimate_cost` is called once per distinct (formulation, order) request, and the estimate runs off the loop. |
| F6 | **MEDIUM** | High | Tests / money | The experiment's own money and terminal paths are unguarded. Planting X7 (`batch.close(saved)` → `close(True)` at `app/experiments.py:221`: an unsaved or cancelled experiment is never charged) and X14 (a report failure escapes `execute` at `app/experiments.py:225-227`) stay GREEN in the whole Python suite. The experiments suite alone is GREEN for 11 of 13 batch plantings (only B2 and B6 go RED there); the module claim "dataset tests guard it too" (`docs/DESIGN.md` §11.1) holds for `app/batch.py` itself but not for how `experiments.execute` uses it. F1 shows the report-failure path is real. | A future edit of `execute` can drop the unsaved charge (money on the day goes uncounted) or lose the terminal event without a red test. | Add experiment tests mirroring P4's: a failed save still charges; a cancelled experiment charges the calls in flight and releases the reservation; a report that cannot be built still ends with `run_done.report_error`. | Re-run plantings X7 and X14: both RED. |
| F7 | **MEDIUM** | High | Tests / consent to spend (UI) | `run()` re-checks after the estimate that the grid did not change (`web/js/experiments.js:160`) and that no run started while the dialog was open (`:169`); neither guard is tested: plantings W2 and W3 stay GREEN. Without W2's guard the confirm shows the figures of the previous grid while the new grid is sent (the P4 F7 class). | A regression silently restores "confirm $X, spend on another grid". | JS tests: change `repeats` while the run's estimate is pending → `run()` returns false and `runExperiment` is not called; a second `run()` resolving during the confirm → exactly one `runExperiment`. | Re-run W2, W3: RED. |
| F8 | **LOW** | High | Logic / budget | The grid is formulation-major (`app/experiments.py:141-148`) and each lane takes jobs in that order, so a budget stop mid-run leaves the later formulations (and the later orders) wholly unasked: the formulation axis disappears instead of thinning. | Partial experiments lose whole axes. | Order jobs repeat-major (`r` outermost, every cell's first repeat first). | Test: budget stop after half the calls → every (formulation, order) cell has ≥ 1 answered repeat. |
| F9 | **LOW** | Medium | Statistics / docs | The noise-floor rule compares |ΔP| between two cell means with the range of single repeats (`app/stability.py:9-11`, `web/js/i18n.js:66`, `docs/DESIGN.md:632-633`). With R=5 the range of single draws is about 2.3σ while the SD of a difference of 5-repeat means is about 0.63σ: effects up to ~3.6 standard errors are labelled noise. | Real order or wording effects are dismissed. | Compare with the standard error of the difference (or a permutation test over repeats); at least scale the spread by 1/√R and say so. | A unit case: two cells with means differing by 3 SE and spread larger than the difference → flagged as an effect. |
| F10 | **LOW** | Medium | Metric faithfulness | "Top shown first" counts the argmax only (`app/stability.py:95-100`): a probability tilt toward the option shown first that never flips the top is invisible, and for `multi` (independent P(selected), several near 1) the argmax is decided by noise. | The position-bias reading is coarse; for `multi` it is not meaningful. | Add a lift: mean P(option | shown first) − mean P(option | shown elsewhere), per option, pooled; skip argmax share for `multi`. | Unit case: a model adding +0.05 to the first-shown option without flipping the top → lift ≈ 0.05, share = chance. |
| F11 | **LOW** | High | Docs / evidence | DESIGN §11.1 acceptance: "«лучший — первый показанный» ровно 33.3 % при 1/n = 33.3 %" (`docs/DESIGN.md:644-648`). With the 3! balanced orders and no top change (UC-2 `run_done` summary: order |ΔP| ≤ 0.008, the cloud models 0.0–0.0003), exactly 1/3 is forced arithmetically for any order-invariant model; it adds no evidence beyond the |ΔP| figures. | The claim reads stronger than its evidence. | Cite the |ΔP| and flip share as the evidence; describe 33.3 % as expected under a balanced design. | Doc review. |
| F12 | **LOW** | High | API | `POST /api/runs/{id}/rerun` accepts an experiment id and runs a single compare of its base request (`app/main.py:291-303`; probe E6: 200, `run_started … run_done`). The UI hides it (`rowActions().rerun` false), the API does not. | A script or old link silently runs something else than the experiment. | Refuse kinds other than compare with 400 (as the UI does). | Test: rerun of an experiment id → 400. |
| F13 | **LOW** | High | API | The report endpoint validates `target` for experiments, where it means nothing: `GET /api/reports/{experiment}?target=0.3` → 400 (`app/main.py:435-436`, probe E6). | Cosmetic; a shared link with a dataset target fails for an experiment. | Validate `target` only for `kind == "dataset"`. | Test: experiment report with `target=0.3` → 200. |
| F14 | **LOW** | High | UI / performance | `/?report=<id>` fetches the full JSON report only to learn its `kind`, then the view fetches it again (`web/js/app.js:471-475`); a 5 000-item dataset report is built twice. On a failing report (F1) the error lands on the Workbench. | Double work on deep links. | Learn `kind` from a light source (index row, or a `kind`-only response), then open once. | Count `getReport` calls on a deep link: 1. |
| F15 | **LOW** | Medium | UI / state | Opening a past experiment (History, deep link) on a first visit snapshots the current Workbench into the setup panel (`web/js/experiments.js:93-94` via `web/js/app.js:261-265`): the panel beside an old report shows another request, and Run runs the Workbench's. | Misleading pairing of report and request. | Show the report's own request (the record has it) or label the panel as "next run". | Manual / JS test with a fake getRequest: opening a report does not call it. |
| F16 | **LOW** | Medium | UI / race | "Эксперимент…" (`web/js/app.js:427-431`) stays enabled while an experiment runs; `takeFromWorkbench()` (`web/js/experiments.js:80-91`) then replaces the request and the engine picks (bypassing the checkboxes disabled while running). | The panel no longer describes the running grid. | Ignore `takeFromWorkbench` while `running` (or disable the Workbench button). | JS test: `running = true`, take → request unchanged. |
| F17 | **LOW** | High | UI / errors | `run()` calls `this.body()` again after the estimate (`web/js/experiments.js:160`); a formulation edited into invalid JSON meanwhile throws an unhandled rejection with no message, and a changed grid returns false silently. `estimate()` returns early without clearing `estError` when no engine is picked (`:122-125`), so an old error stays. `load()` has no sequence guard (`:97-101`). | Clicks that do nothing; stale error text. | Catch and say "settings changed — estimate again"; clear `estError` on early return; guard `load()` with a counter. | JS tests for each. |
| F18 | **LOW** | High | Tests | Unguarded boundaries and paths (GREEN plantings): exactly 2 000 calls (X5, `app/experiments.py:158`), the repeats cap 20 (X12, `:70`), media kept on every call (X3, `:144`), ties broken by base order (S2, `app/stability.py:44`), failed calls counted in `calls` (S8, `:146-150`), the estimate over profile-refused questions (B9, `app/batch.py:79` — unguarded since P4), app.js dispatch of reports by kind (W10, `web/js/app.js:262` — app.js is not loaded by node). | Each can regress silently. | Add a test per row; for the limits, an input fixed by a literal (exactly 2 000 → 200; 21 repeats → 422) plus an assertion pinning the constant. | Re-run X5, X12, X3, S2, S8, B9: RED. |
| F19 | **LOW** | Medium | Design / invariant | `Batch` assumes every job has the same question keys and kinds and takes `sent` from `jobs[0]` (`app/batch.py:59-65`); nothing checks it. Experiments guarantee it by validation; a future caller with differing requests would turn `subset` KeyErrors into per-job `provider_error`. | Latent. | Assert the invariant in `__init__` (same keys, types, option and level counts) or split per distinct request. | Unit test: differing jobs → ValueError at construction. |
| F20 | **LOW** | High | UI / i18n | Experiments send no title or tags (`web/js/experiments.js:106-109`): History rows show bare ids. The order toggle says "(choice)" though `multi` is reordered too (`web/js/i18n.js:60`, `:142`). `versions()` lives in `dataset_run` and experiments import it from there (`app/experiments.py:34`). | Cosmetic / layering. | Title field (default "Experiment: …"); "(choice, multi)"; move `versions` to `app/batch.py`. | Review. |

**Batch refactor, "no behaviour change"**: read line by line against the P4 code in the scope diff — reservation
(`reserve`, NaN and the `> 0` guard), the hold (`max(need, seen)`), the lone first call, top-up, the free-after-stop
rule, `close()` (charge `max(spent, committed)` when unsaved, release always), the progress payload, the lanes
(local turns, cloud beside) and the record fields are the same. Two differences, neither observable in practice:
`sent` is taken from the first job instead of a dummy request (`split` does not read the state), and a negative
total now reserves 0 instead of a negative amount (an improvement). Request construction moved out of the per-item
guard, but P4 already built every request in `estimate()` before the run, so an invalid item failed the same way.

## 4. Hallucination Check
- [x] **Files**: every cited file exists in the frozen tree (`app/batch.py`, `app/experiments.py`, `app/stability.py`,
  `app/main.py`, `app/engines/base.py`, `web/js/experiments.js`, `web/js/app.js`, `web/js/i18n.js`, `docs/DESIGN.md`,
  `presets/demo_extraction.yaml`).
- [x] **Line Numbers**: confirmed with `cat -n` in the frozen tree for every cited range (stability 33-44, 70-73,
  89, 95-104, 113-116, 146-150; experiments 94, 101-115, 141-148, 156-160, 216-227; batch 59-65, 77-84, 112;
  main 291-303, 411-436; base 165-166; app.js 261-265, 427-431, 471-475; experiments.js 80-101, 117-145, 160, 169,
  302; i18n 60, 66, 142; DESIGN 621, 632-633, 644-648).

## 5. Probes (copies only)

| Id | What | Result |
| :--- | :--- | :--- |
| E1 | Experiment, one `text` question, `mlx-d1-2b`, repeats 2 | `run_done`, `report_error: ValueError: max() iterable argument is empty`; report 500 (json, md) |
| E2 | Two choice questions (3 and 2 options), model always picks the first shown | 3-option flip 0.8 (4/5), 2-option flip 0.6 (true 1.0), |ΔP| 0.36 (true 0.6) |
| E2b | 2-option question cycled over k=5 orders of a 5-option one, model always `x` | share 0.6 vs chance 0.5 |
| E3 | 5 options, k=6, model always `a` (order-invariant) | orders `abcde becad edcba aecdb acbde cbaed`; share 0.5 vs chance 0.2 |
| E4 | Formulation with options in another order, orders off | base shows `[credit, deposit]`, en `[deposit, credit]` |
| E5 | 120-call estimate | 200 |
| E6 | Rerun of an experiment id; report with `target=0.3` | rerun 200 (a compare run); report 400 |
| L1 | Estimate, JSON state 435 KB, 1 920 calls | loop stall 4.70 s; over-cap refusal 1.61 s; 2.2 MB: plan 8.7 s + 16.8 s per engine |

## 6. Plantings (copy `review-p5-plant`, `p5adv/plant.py`)

Mechanics: full paths; the pattern found exactly once; the edit verified (sha changed, new text present, old text
gone); `__pycache__` cleared before each run; restore in `finally`, verified by sha256 before the next planting.
Baselines green before the series (datasets 33, experiments+stability 30, Python subset 123, node 47). X1 first
failed as a measurement (pattern not found: indentation) and was re-run with the exact pattern
(`plant-x1.log`). Every restore verified; the copy's fingerprint is `208fdef2cc02db2d` after the series.
"datasets" = `tests/test_dataset_run.py`; "experiments" = `tests/test_experiments.py tests/test_stability.py`;
"py" = those plus `test_store.py`, `test_api.py`; "js" = all `tests/js/*.test.mjs`.

| Id | Planted regression | Result |
| :--- | :--- | :--- |
| B1 | batch: a lane's first call no longer alone | datasets RED (`test_a_cancelled_run_charges_the_calls_in_flight`); experiments GREEN |
| B2 | batch: a call holds only its estimate | datasets RED (3); experiments RED (`test_the_budget_running_out_mid_experiment`) |
| B3 | batch: unsaved charge forgets calls in flight | datasets RED; experiments GREEN |
| B4 | batch: local engines side by side | datasets RED (`test_local_engines_take_turns_not_swapping_the_model`); experiments GREEN |
| B5 | batch: local lane as wide as concurrency | datasets RED; experiments GREEN |
| B6 | batch: a refusal does not mark the run stopped | datasets RED (3); experiments RED |
| B7 | batch: NaN estimate passes `reserve()` | datasets RED; experiments GREEN |
| B8 | batch: an estimate failure escapes the job | datasets RED; experiments GREEN |
| B9 | batch: estimate counts profile-refused questions | datasets GREEN; experiments GREEN |
| B10 | batch: reservation kept on an unsaved run | datasets RED (2); experiments GREEN |
| B11 | batch: compact keeps the provider's `detail` | datasets RED; experiments GREEN |
| B12 | batch: in-flight hold never given back | datasets RED (2); experiments GREEN |
| B13 | batch: free jobs stop with the paid ones | datasets RED; experiments GREEN |
| X1 | orders_of: original not first beyond 4 options | py RED (`test_k_orders_beyond_four_options`) |
| X2 | plan: a shorter order list stops cycling | py RED |
| X3 | plan: media dropped from every call | py GREEN |
| X4 | validation: score level count not checked | py RED |
| X5 | cap: exactly 2 000 calls refused (`>` → `>=`) | py GREEN |
| X6 | validation: question type not checked | py RED |
| X7 | execute: unsaved experiment never charged (`close(True)`) | py GREEN |
| X8 | execute: estimate not reserved up front | py RED |
| X9 | execute: budget stop ends as `run_done` | py RED |
| X10 | record: the state not kept | py RED |
| X11 | plan: every formulation sends the base wording | py RED (2) |
| X12 | validation: repeats up to 50 | py GREEN |
| X13 | validation: k = 1 accepted | py RED |
| X14 | execute: report failure escapes (no own terminal event) | py GREEN |
| X15 | validation: option ids not checked | py RED |
| S1 | first position read against the original order | py RED (3) |
| S2 | ties broken by shown (dict) order | py GREEN |
| S3 | a spread from a single repeat | py RED (2) |
| S4 | non-finite probabilities counted as answers | py RED |
| S5 | formulation compared against the base's order 0 only | py RED |
| S6 | order 0 compared with itself | py RED (2) |
| S7 | chance as 1/orders | py RED (2) |
| S8 | `calls` counts only calls with an answer | py GREEN |
| M1 | index row of an experiment reads `items` | py RED |
| M2 | no pre-stream grid check | py RED |
| M3 | report endpoint builds a calibration report for an experiment | py RED (3) |
| W1 | experiments.js: no `estSeq` bump at run start | js RED (incidentally, via "a broken formulation stops the run") |
| W2 | experiments.js: no "changed meanwhile" check | js GREEN |
| W3 | experiments.js: no running re-check after the confirm | js GREEN |
| W4 | datasets.js: no `estSeq` bump at run start | js RED |
| W5 | experiments.js: consent read after it was cleared | js RED |
| W6 | experiments.js: a paid run not confirmed | js RED |
| W7 | experiments.js: a late report lands over a newer one | js RED |
| W8 | experiments.js: unavailable engines carried over | js RED |
| W9 | history.js: an experiment row has no report | js RED |
| W10 | app.js: an experiment report opens on the Datasets screen | js GREEN (app.js is not loaded by node) |
| W11 | experiments.js: late estimate lands (success path) | js RED (2) |

GREEN plantings map to findings: X7, X14 → F6; W2, W3 → F7; X3, X5, X12, S2, S8, B9, W10 → F18. The batch rows show
that `app/batch.py` is guarded by the dataset suite (12 of 13), not by the experiment suite (2 of 13).
