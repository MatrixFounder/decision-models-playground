# P4 — ответ на adversarial-ревью

> Ревью: [p4-critique.md](p4-critique.md), VDD adversarial, 2026-11-09, вердикт WARNING (0 CRITICAL, 1 HIGH, 7 MEDIUM,
> 20 LOW). Снимок дерева `4c5069ee75ceea73`, подтверждён ревьюером без изменений.
> После исправлений: `make test` — 648 passed, 1 skipped (pytest), 39 passed (node).
> Подсадки строителя: 37 регрессий на копии дерева, все 37 красные, каждое восстановление сверено по sha256
> ([p4-plantings.log](p4-plantings.log)). В первом проходе три остались зелёными: два теста были пустыми (оценка ещё
> не стояла; второй отчёт был того же набора) и усилены, а тест нейтральности шаблона пропускался вне соседства с
> whip-insight — теперь путь задаёт `WHIP_INSIGHT`.
> Повторная приёмка UC-3 вживую на новом пути прогона — $0.0016, 17.6 с; отчёт приёмки без текстов —
> [p4-uc3-report.md](p4-uc3-report.md) и `.json`.

«Тест» — что падает, если исправление откатить. «F…» — находки ревью, «P…» — его подсадки, оставшиеся зелёными.

## HIGH

| Находка | Исправление | Тест |
|---|---|---|
| F1. Экран «Наборы» не снимал согласие на движки без ZDR: после одного прогона все следующие уходили к провайдеру без ZDR без нового согласия (§18) | `takeConsent()`: согласие читается и снимается при старте, как в Workbench; `body()` больше не читает галочку; текст подтверждения называет движки без ZDR | `tests/js/datasets_view.test.mjs` «the no-ZDR consent belongs to one run» |

## MEDIUM

| Находка | Исправление | Тест |
|---|---|---|
| F2. Остановка по бюджету в облачной полосе помечала бесплатные локальные элементы `budget` | Бюджет проверяет только платный вызов (`hold > 0`); локальные идут дальше | `test_a_cloud_budget_stop_leaves_the_free_local_items` |
| F3. Исключение в одной полосе (оценка, профиль) оставляло другие полосы звонить после `run_error`, вне резерва | `one()` не бросает: сбой — `provider_error` элемента; полосы в `asyncio.TaskGroup` | `test_an_estimate_that_fails_mid_run_is_one_items_failure` |
| F4. Синтетика брала говорящего по папке: шесть операторских тегов (`std_secretary_bypass`, `std_needs_discovery`, `std_objection_handling`, `std_next_contact`, `std_cp_call`, `std_escalation`) говорили голосом клиента | Импорт читает «Проверяется у оператора / в речи клиента» из описания в `tag.speaker` (26 тегов); синтетика берёт его раньше папки | `test_the_speaker_is_read_from_the_description`, `test_operator_side_tags_synthesize_as_the_operator` |
| F5. «Нейтральный» шаблон срабатывал на `std_broken_promises` («должен был приехать») и `std_send_documents` («по почте») | Две строки переписаны; тест прогоняет каждую строку шаблона через правила включения всех 56 тегов whip-insight (фразы, основы `*`, разрывы, `all_of`) | `tests/test_synth_neutral.py` (без соседнего whip-insight пропускается; сам сопоставитель проверен отдельно) |
| F6. Ячейка CSV длиннее 131 072 символов — 500 | `csv.field_size_limit` = 16 МБ (предел тела UI); `csv.Error` → 400 с номером строки | `test_a_long_csv_cell_is_read` |
| F7. Платный прогон мог стартовать без подтверждения или с цифрами другого выбора | `run()` берёт свежую оценку ровно этого запроса и подтверждает, если платно или без ZDR; изменение выбора стирает показанную оценку, «Запустить» ждёт новую | `datasets_view.test.mjs`: «a paid run is confirmed on a fresh estimate…», «a free run starts without a dialog; a failed estimate starts nothing», «a late estimate never lands…» |
| F8. Перерисовка сбрасывала форму импорта (файл, имя, JSON вопросов) и раскрытые `<details>` | Форма строится один раз и переиспользуется (пересобирается только при смене списка пресетов); раскрытые блоки помнятся по ключу | Только вручную в браузере: DOM в node не поднимается |

## LOW

| Находка | Решение | Тест |
|---|---|---|
| F9. Перебор дня — сумма ширин полос, а не «один вызов» | Первый вызов каждой полосы идёт один; вызов в полёте держит большее из оценки и самого дорогого вызова движка. Граница — один вызов на платный движок (§12.4, §19) | `test_concurrent_lanes_overshoot_by_at_most_one_call_per_engine` (было 10 вызовов и $4 на $1 — стало 2 и $0.80); `test_the_budget_running_out_mid_run_stops_the_rest` |
| F10. Отменённый прогон списывал только завершённые вызовы | Без записи списывается `max(spent, committed)` — и вызовы в полёте | `test_a_cancelled_run_charges_the_calls_in_flight` |
| F11. Не спрошенные из-за бюджета элементы оставались в знаменателе покрытия | Исключены из группы, счётчик `not_asked`; в таблицах «+N не спрошено» | `test_a_cloud_budget_stop_leaves_the_free_local_items` |
| F12. Отчёт строился в цикле событий; `options.index` внутри `max` — квадратично | Словарь рангов; отчёт строится один раз в `asyncio.to_thread` и переиспользуется для итога и файлов | `test_choice_metrics_scale_with_many_options` (5 000 × 255 < 1 с) |
| F13. `openReport` без защиты от гонки: отчёт B с текстами A | Счётчик `reportSeq`; тексты грузятся до замены отчёта и ставятся вместе с ним | «only the last report opened lands, with its own texts» |
| F14. Выбор движков не сверялся с доступными | `load()` убирает недоступные | «an engine that went away is unpicked…» |
| F15. Метки `llm:` проходили, отчёт писал «точность» | До P7 `llm:` не принимается («согласие с LLM» — вместе с LLM-движками) | `test_llm_labels_wait_for_p7` |
| F16. Docstring обещал перенос порога в whip-insight как есть | Формулировка: формула та же, выборка нет (лексический фильтр, фрагменты); то же в §12.4 | — |
| F17. «Порог по цели» по одному решённому элементу | Не меньше 10 решённых (`MIN_DECIDED`), число решённых — в результате | `test_a_suggestion_needs_ten_decided_items` |
| F18. `_bins` падал на NaN/inf; сбой отчёта после записи — запасной `run_error` | Неконечная вероятность — нет ответа; сбой отчёта даёт `report_error` в `run_done` | `test_a_probability_that_is_not_a_finite_number_is_no_answer`, `test_a_report_that_cannot_be_built_still_ends_the_run` |
| F19. Лишние поля CSV терялись, повторённый столбец побеждал молча, `;` — «нет заголовка», метка `1.0` отклонялась | Ошибки «больше полей, чем в заголовке» и «повторённые столбцы»; разделитель — самый частый из `,` `;` табуляции в заголовке; целое `1.0` — `1` | `test_csv_quirks_say_what_to_change`, `test_a_whole_float_label_is_its_integer` |
| F20. Зелёные подсадки P15, P17b, P20, P25, P27, P28, P29, P31, P32 | Тест на каждую | `test_cloud_lanes_do_not_wait_for_the_local_engines`, `test_the_in_flight_estimate_is_given_back`, `test_the_item_limit_is_5000`, `test_disagreements_round_as_whip_insight`, `test_history_status_of_a_dataset_run`, `test_the_report_endpoint_is_for_dataset_runs_only`, `test_dataset_texts_are_cut_to_300_characters`; P31, P32 — `datasets_view.test.mjs` |
| F21. Ослабленный тест второго SIGTERM не отличал жёсткий выход от быстрого чистого | Тест-приложение держит поток не-демон (`HANG_HOLD_EXIT`): чистый выход невозможен, только `os._exit(1)`; проверка — строго код 1. **Тест нашёл ошибку:** таймер жёсткого выхода висел на цикле событий, который принудительная остановка закрывала раньше 3 с, и при живом потоке не-демоне (рабочий `to_thread`) процесс не выходил вовсе. Таймер теперь — поток-демон (`threading.Timer`) | `test_a_second_sigterm_forces_the_exit` (с таймером цикла — красный: процесс не вышел за 10 с) |
| F22. Запись набора хранила `detail` — текст ошибки провайдера мог цитировать запрос | В записи только класс отказа и HTTP-статус | `test_the_record_keeps_no_provider_message` |
| F23. Матрицы ошибок не было на экране (§16) | Таблица для `choice`/`score` под графиками | `confusionRows` в `datasets_view.test.mjs` |
| F24. Open в Истории качал всю запись прогона набора | `rowActions().open` = `report` → открытие по id | `matrix.test.mjs` «only a comparative run opens…» |
| F25. Прогон через полночь считался в прошлом дне | День записи — день сохранения (наборы, Workbench, проба) | `test_a_run_past_midnight_counts_on_the_day_it_ends` |
| F26. Оценка NaN обходила проверку бюджета (наборы и Workbench) | NaN → `budget_stop` с `estimate_usd: null`; `budget` в `config.yaml` — только конечные числа ≥ 0 | `test_a_nan_estimate_stops_before_any_call` |
| F27. Ключ тега — путь до проверки; `--tags` по имени файла; «kept» на любую ошибку | Ключ `[a-z0-9_]{1,64}`, иначе тег пропущен и код 1; `--tags` по ключам; «kept» только для существующего набора, прочее — «not written» и код 1 | `test_import_refuses_a_key_that_is_a_path` |
| F28. Таблицу приёмки UC-3 нельзя проверить | Отчёт прогона (без текстов) — `docs/reviews/p4-uc3-report.md` и `.json`; в репозитории | Файлы; цифры совпадают с §12.4 |

## Повторная сверка (по вопросу владельца, после коммита `2523672`)

Каждая находка сверена с кодом и тестами заново. Нашлись три расхождения между ответом выше и кодом — исправлены:

| Находка | Что было | Исправление | Тест |
|---|---|---|---|
| F12 | Отчёт уходил из цикла событий только в конце прогона; `GET /api/reports/{id}` (каждая смена цели) строил его в общем с compat-сервером цикле | `build_report` и `render_markdown` в `asyncio.to_thread` и там | `test_the_report_endpoint_builds_off_the_event_loop` |
| F26 | Защита от оценки NaN в раннере Workbench была без теста | — | `test_a_nan_estimate_stops_a_workbench_run_too` |
| F27 | Ветка «not written» скрипта импорта была без теста | — | `test_import_says_kept_only_for_an_existing_dataset` |

F8 проверена в настоящем браузере (headless Chrome по DevTools-протоколу): после двух переключений движка и двух
смен языка поля формы импорта (имя, JSON вопросов, источник меток, «заменить»), её раскрытие и раскрытый список
элементов сохраняются. Проверка нашла побочный эффект правки F8: форма строилась один раз и при смене языка
оставалась на прежнем. Её подписи теперь с `data-i18n`, их переводит общий `applyStatic` на месте — после одной смены
языка подписи на новом языке, введённое имя на месте.

Тесты, сторожащие F1–F27: 37 случаев pytest (ни один не пропущен — whip-insight рядом) и 8 node — все проходят.
F16 (формулировка) и F28 (файлы в репозитории) тестов не требуют.
