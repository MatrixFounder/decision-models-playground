# P3 — ответ на adversarial-ревью

> Ревью: [p3-critique.md](p3-critique.md), VDD adversarial, 2026-11-09, вердикт WARNING (0 CRITICAL, 0 HIGH, 5 MEDIUM,
> 27 LOW). Снимок дерева `25a880c591b1178e`, подтверждён ревьюером без изменений.
> После исправлений: `make test` — 545 passed, 1 skipped (pytest), 26 passed (node).
> Подсадки строителя: 61 регрессия на копии дерева, все 61 красные, каждое восстановление сверено по sha256, копия
> после них совпадает с исходной ([p3-plantings.log](p3-plantings.log)).
> Повторная приёмка UC-4 вживую на пяти моделях с длинным шагом — $0.0047 (DESIGN §13.1).

«Тест» — что падает, если исправление откатить. «P…» и «J…» — подсадки из журнала.

## MEDIUM

| Находка | Исправление | Тест |
|---|---|---|
| `no_profile` решался по «обслужен», а не по «получен 2xx» (E2) | `got_2xx`: ответ, ответ-отказ, `invalid_answer` или статус 2xx. Нет ни одного → `no_profile` с преобладающим классом отказа; иначе профиль из чтений (здесь `types: []`) | `test_unreadable_200_answers_write_an_empty_profile`, `test_what_counts_as_a_2xx_answer`, `test_no_2xx_answer_writes_no_profile`, `test_a_400_on_every_step_is_no_profile` (P15, P22, P42) |
| Зелёная проба снижала предел вариантов с 255 до 240 (E3) | Профиль хранит 240, как whip-insight, и `max_choice_probed`/`max_score_probed`. Наложение читает «наибольший проверенный размер обслужен» как «не меньше» — остаётся потолок вида. Матрица пишет «≥240» | `test_the_largest_probed_size_keeps_the_ceiling`, `test_the_overlay_never_widens[…probed3…]`; JS `limitView` (P23, J4) |
| Чужой или устаревший `latest.json` (E4, E15) | `BaseEngine.probed()` берёт профиль только тех же вида и модели, что в `config.yaml`; иначе профиль по умолчанию, пометка «устарел», и проба стартует без «сохранённого». Пометка и при прогоне, ответившем другим датированным снимком (индекс хранит снимки, сравнение по ULID). `reads_full_context` не переносится на новый снимок. `fingerprint()` удалён | `test_a_foreign_latest_json_is_ignored`, `test_a_run_on_a_new_snapshot_marks_the_profile_stale`, `test_reads_full_context_stays_with_its_snapshot` (P24, P25, P32) |
| Сбой после оплаченных вызовов — ни записи, ни списания (E1) | `Prober.call` охраняет `engine.run`, как раннер; сбой сборки профиля сохраняет запись с `error` и даёт `probe_error`; любой выход без записи (исключение, отмена) списывает стоимость сделанных шагов через `charge_unsaved`; резерв снимается в `finally` | `test_an_engine_crash_mid_probe_is_saved_and_charged`, `test_a_failed_save_still_charges`, `test_a_crash_while_building_the_profile_saves_the_record` (P7, P8, P27, P48) |
| Тесты не сторожат «подтверждает или сужает, не расширяет» и «не наблюдалось — прежнее значение» | Тесты на подложенный `latest.json` (тип вне потолка, пределы выше потолка, сужение, аудио) и на свой запрос типа без ответа; пример 4 whip-insight | `test_the_overlay_never_widens` (7 случаев), `test_a_types_own_request_unobserved_keeps_the_stored_type`, `test_whip_insight_example_4_n_answered_c_401` (P1–P4) |

## LOW

| Находка | Решение | Тест |
|---|---|---|
| Путь 400 → «не обслужен» через облачный движок не сторожен | Тест сужения идёт после зелёной пробы: «не наблюдалось» оставило бы 240, «не обслужен» даёт 24. Отдельно — отказ предпроверки локальной модели | `test_a_narrower_profile_narrows_the_runs`, `test_a_local_precheck_refusal_reads_as_not_served`, `test_the_runner_keeps_the_http_status_of_a_refusal` (P12, P13, P17) |
| LONG ничем не закреплён | Нижняя граница размера, порог 0.89/0.90, повтор после 413 и после 422, предел байт Jev ≥ 25 000 | `test_the_long_step_fills_about_90_percent_of_the_context`, `test_the_long_threshold_is_0_9`, `test_the_long_step_retries_smaller_after_too_long[413, 422]`, `test_the_long_step_is_capped_by_the_byte_limit_and_still_counts` (P5, P6, P10, P11) |
| Бюджет и поток ошибок без тестов | Тесты на оба пути; `stream_events` сам шлёт запасной терминальный `probe_error`/`run_error`, если работа кончилась без него, и не пропускает событий после терминального | `test_every_probe_stream_ends_with_a_terminal_event[raises, returns]` (P9) и тесты MEDIUM выше |
| `latest.json` другой формы роняет список движков (E5) | `StoredProfile` (pydantic) при чтении; другая форма — не профиль, предупреждение в журнал | `test_a_malformed_latest_json_falls_back_to_the_default` (4 формы) (P43) |
| Любая `ValueError` локальной модели — «не обслужен» (E14) | 400 только для собственных отказов mlx-vlm (`question '…'`, «serves noul, choice and score questions only», «this checkpoint takes no …»); остальное — `provider_error` | `test_a_local_engine_error_is_not_a_refusal`, `test_audio_not_heard_is_removed_and_stays_no` (P17b) |
| Проба движка без ZDR ничего не шлёт и пишет «нет ответа» (E10) | `/api/probe` отвечает 400, пока нет согласия `allow_no_zdr`; экран спрашивает согласие (текст пробы синтетический); согласие доходит до вызовов | `test_a_zdr_off_engine_is_probed_only_with_consent` (P29, P29b) |
| Проверка id пропускает `.` и `..`; плохой id отказывает после оплаты (E6) | `valid_engine_id` отвергает id из одних точек; `/api/probe` проверяет id до потока | `test_engine_ids_that_name_a_directory` (P14) |
| Две пробы одного движка одновременно (E9) | `Prober.busy`: API отвечает 409, `run` сам отказывает (гонка двух запросов) | `test_one_probe_per_engine_at_a_time` (P28, P28b) |
| Проба тратит деньги без оценки | `POST /api/probe/estimate`; с галочкой LONG экран показывает оценку и спрашивает подтверждение | `test_the_estimate_reserves_the_long_step_at_its_ceiling` |
| LONG не доходит до 90 % и без измеренной доли может засчитать половину контекста (E11) | Шаг CAL (вопрос N на ≈ 3 300 символах того же диалога): по N и CAL подгонка «накладные + доля × символы» — шаблон вопроса выпадает. Нет растущих счётчиков → LONG `skipped`. LONG короче 75 % контекста (и не упёршийся в байты) — «не наблюдалось». Вживую локальные d1 — 89.9 % | `test_the_long_step_needs_token_counts`, `test_a_long_step_that_read_little_proves_nothing`, `test_a_local_long_step` (P6, P26) |
| `reads_full_context` не говорит, сколько прочитано | `long_context_tokens` в профиле и в клетке матрицы («✓ 52K», подсказка) | `test_the_long_step_fills_about_90_percent_of_the_context`; JS `fullContextView` (J6) |
| LONG ловит только обрезку конца, контроля нет | Не исправлено, записано в DESIGN §13.1 как ограничение SPEC §5.6.2: контроль удвоил бы цену LONG и развёл бы набор с whip-insight. Предложение — в whip-insight | — |
| Повтор LONG не зарезервирован | Повтор резервируется отдельно; нет денег — повтора нет, шаг «не наблюдалось» | `test_the_long_retry_is_reserved_again`, `test_the_long_step_retries_smaller_after_too_long` (P35) |
| Матрица забывает сохранённое «нет» (E7) | `not_served` в профиле, переносится при «не наблюдалось»; клетка ✗ по нему | `test_the_matrix_keeps_a_stored_no`, `test_audio_not_heard_is_removed_and_stays_no` (P18, P19, P31) |
| Строки каталога «?» вместо «—»; пределы «?» при необслуженном типе | Клетки каталога по потолку OpenRouter (`CATALOG_TYPES`, тест держит его равным движку); предел «—», если тип не обслуживается | `test_matrix_lists_engines_then_the_catalog`, `test_the_catalog_ceiling_is_the_openrouter_engines`; JS `limitView` (P45, J5) |
| Облачная модель вне каталога — $0 (E8) | `None` («—») для облака, 0 — только для локальных | `test_a_cloud_engine_missing_from_the_catalog_has_no_price` (P46) |
| Ошибка обновления каталога не видна; перерисовка сбрасывает галочку и `<details>` | Ошибка обновления держится до следующего обновления, `catalog.error` показывается; галочки и раскрытие списка хранятся в объекте экрана | Вручную не проверено в браузере; модули проходят `node --check` |
| `media` профиля ничем не применяется | Раннер отклоняет носитель вне `profile().media` (`refused`, без вызова) | `test_audio_outside_the_profile_is_not_sent` (с настоящим файлом: остановить вызов может только профиль) (P30) |
| Мелкие расхождения с whip-insight (a–e) | (a) снимок — первый датированный, иначе `model` конфигурации; (b) `too_long` короткого шага пишется в журнал; (c) `reports_cost`/`reports_snapshot` считают и ответы-отказы; (d) без LONG — `null`, отклонение записано в DESIGN; (e) `failure` в `probe_done` и на экране | `test_the_snapshot_falls_back_to_the_config_model`, `test_too_long_on_a_short_step_is_logged`, `test_reports_cost_counts_refusal_answers`, `test_no_2xx_answer_writes_no_profile` (P16, P40, P41, P42) |
| История: двойная метка, статус по шагам, проба выбирается для сравнения (E16) | У пробы нет тега `probe` (вид уже говорит), в индексе одна запись статуса на движок; флажок сравнения выключен для не-`compare`; старые записи — тег, равный виду, скрывается | `test_cloud_probe_writes_a_profile_the_runner_uses`; JS `rowActions` (P38, J1, J7) |
| P3 без JS-тестов | `tests/js/matrix.test.mjs`: клетки, пределы, весь контекст, действия строки Истории, сообщение по виду | J1–J8 красные. Сам вызов в `app.js` (`openRun`) в node не грузится: проверка вынесена в `isWorkbenchRun` (J3) |
| `openRun` говорит «это проба» для любого вида | Сообщение по виду (`notWorkbenchMessage`); повтор не-`compare` останавливается до оценки | JS (J8) |
| Локальная проба занимает модель; общего срока нет | Подсказка на кнопке; срок пробы 600 с, шаги после него `skipped` | `test_the_probe_deadline_skips_the_remaining_steps` (P34) |
| `ProbeStore.save` без fsync | `write_durably` (fsync файла и каталога), общий с `Store.save` | `test_cloud_probe_writes_a_profile_the_runner_uses` (нет `.tmp`); остальное — осмотр |
| Аудиоклип пробы читается из `tests/` | `app/assets/probe_audio_ru.wav`; копия в `DATA_DIR/media/probe/` — перед шагом A, не до бюджета | `test_local_probe_covers_the_extensions_and_audio` (P47) |
| Мусор (a–e) | (a) `fingerprint` удалён; (b) комментарий исправлен; (c) импорты по порядку; (d) обе попытки LONG строятся одним `long_state`; (e) в записи пробы — `versions.mlx_vlm` и `models` | `test_cloud_probe_writes_a_profile_the_runner_uses` |
| DESIGN: «восемь типов» | «семь типов и аудио» | — |
| Вне рамки P3: цена `"nan"` и отрицательная | `price_per_token` → `None` для нечисла, бесконечности и отрицательной; `Budget.reserve` отвергает NaN и отрицательное | `test_a_catalog_price_must_be_a_finite_non_negative_number`, `test_the_budget_refuses_a_nan_reservation` (P36, P37) |

## Что нашла повторная приёмка

Первый живой круг после исправлений упал на d1.1 и `~d1-latest`. OpenRouter ответил **422**: *«The prompt for
question `q` is over the model's input limit of 65536 tokens, images included, or 250000 bytes of text»*. Верная доля
токенов (0.35 на символ против 0.42 по одному N) сделала состояние длиннее 250 КБ текста. Прежняя приёмка прошла
только потому, что завышенная доля случайно держала его ниже предела. Плюс 422 читался как «не обслужен».

- 400/422 с «over the model's input limit» — `too_long` (DESIGN §6.3); в Workbench тоже.
- Длинное состояние ограничено байтами, которые движок может отправить: `max_request_bytes` и 250 000 байт текста у
  API OpenRouter. Байты считаются точно, в UTF-8.
- Повтор после `too_long` — 8/9 первой попытки, поэтому он короче, даже когда первая упёрлась в байты.
- Тесты: `test_the_long_step_stays_under_openrouters_text_bytes`, `test_an_input_limit_422_in_a_workbench_run_is_too_long`,
  `test_the_long_step_retries_smaller_after_too_long[422-input-limit]` (P49–P52).

Итог второго круга: d1-2B и d1-Omni — 29 445 токенов (89.9 %); d1.1 и `~d1-latest` — 52 631 (80 %, предел
байт API); Jev — 11 100 (`max_request_bytes`). Типы и пределы как в первой приёмке; d1-Omni слышит аудио.
