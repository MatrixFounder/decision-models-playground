# P1 — ответ на adversarial-ревью

> Ревью: [p1-critique.md](p1-critique.md), VDD adversarial, 2026-11-09, вердикт WARNING (0 CRITICAL, 1 HIGH, 11 MEDIUM,
> ~35 LOW). Ревьюер работал со снимком дерева `a8f45f0448bf422e` и подтвердил, что снимок не изменился.
> Исправления внесены в рабочее дерево поверх P2. Тесты после исправлений: `make test` — 293 passed, 1 skipped (pytest),
> 11 passed (node).

Строки ниже — по находкам в порядке отчёта. «Тест» — что падает, если исправление откатить.

## HIGH и MEDIUM

| Находка | Исправление | Тест |
|---|---|---|
| HIGH: редактор теряет `multiple: true`, multi уходит в облако как `choice` | `fromRequest` превращает `choice`+`multiple` в `multi`; `/api/presets/{id}` отдаёт вопросы, нормализованные `Question` | `tests/js/questions.test.mjs` «d1 choice + multiple…»; `test_api.py::test_preset_with_d1_multiple_comes_as_multi_and_never_reaches_the_cloud` |
| Незаписанный прогон не попадает в бюджет | `Budget.charge_unsaved`: стоимость прогона, чья запись упала, считается в расход дня (в памяти процесса) | `test_api.py::test_unsaved_run_still_counts_against_the_budget` |
| Оборванная строка индекса глотает следующий прогон | `Store._append` дописывает `\n`, если файл им не кончается; запись и строка с `fsync`; `reconcile()` при старте | `test_store.py::test_a_torn_line_does_not_swallow_the_next_run`, `::test_reconcile_indexes_records_without_a_line` |
| Отказ запроса помечает каждый вопрос `invalid_answer` | Контракт §6.2 уточнён: при `request_failure` вопросы без ответа покрыты им; ячейка UI показывает «запрос: <класс>» | `test_schemas.py::test_request_failure_covers_keys_without_answers`, `test_openrouter_engine.py::test_request_failure_classes` |
| Странный 200 роняет весь движок и теряет стоимость | `normalize_answer` не бросает исключений; `usage` читается первым, числа проверяются (`_count`, `_cost`); `model` только строка; `error_text` терпит скаляр | `test_openrouter_engine.py::test_odd_answer_…`, `::test_odd_usage_and_model_fields_never_crash`, `::test_odd_error_message_…` |
| Кликджекинг | `X-Frame-Options: DENY`, CSP `default-src 'self'; frame-ancestors 'none'`, `nosniff`, `no-referrer` на всех ответах UI | `test_api.py::test_page_cannot_be_framed` |
| Повтор наследует согласие «без ZDR» | `rerun` берёт `options` из тела запроса, по умолчанию без согласия; UI снимает галочку после каждого старта | `test_api.py::test_rerun_does_not_inherit_no_zdr_consent` |
| События прогона пишутся в открытый старый прогон | Обработчик потока привязан к своему виду (`handlerFor(view)`) | ручная проверка; логика в `web/js/app.js` |
| Ключ проверялся только на успешных путях | `assert_no_key` на всех путях отказа и в тексте SSE | `test_openrouter_engine.py::test_no_key_on_any_failure_path`, `test_api.py::test_no_key_in_the_stream_on_failures` |
| Нет тестов терминального события, нет таймаута | `pytest-timeout` (60 с); тесты `run_error`, падения движка, одного терминального события | `test_api.py::test_engine_crash_is_contained`, `::test_every_stream_ends_with_one_terminal_event`, `::test_unsaved_run_…` |
| Проверка APP_HOST без теста | `APP_HOST` только `127.0.0.1` или `localhost` | `test_api.py::test_unsafe_ui_settings_refuse_to_start`, `::test_loopback_ui_hosts_are_accepted` |
| Нет тестов фронтенда | `node --test tests/js/*.test.mjs` в `make test` | 11 тестов: редактор вопросов, SSE-парсер |

## LOW

| Находка | Решение | Тест |
|---|---|---|
| Структурные `instructions` превращаются в строку | Флаг `instructionsJson`, обратный `JSON.parse` | `questions.test.mjs` «structured instructions» |
| Повтор ключа или id варианта молча теряется | `toRequest` бросает переводимую ошибку | `questions.test.mjs` «duplicate…» |
| Открытие прогона затирает черновик | Черновик откладывается, кнопка «↶ вернуть черновик» | ручная проверка |
| Без ZDR теряется и `data_collection: deny` | `deny` уходит всегда, `zdr` — по флагу | `test_openrouter_engine.py::test_no_zdr_engine_needs_confirmation` |
| `user_id` аккаунта в `raw` | `scrub()` в движке OpenRouter | `test_api.py::test_no_key_in_the_stream_on_failures` |
| `::1` проходит проверку, но UI недоступен | `APP_HOST` сужен до двух имён | `test_unsafe_ui_settings_refuse_to_start` |
| `testserver` доверен в продакшене | Убран; тесты ходят на `http://127.0.0.1:8790` | `test_api.py::test_testserver_host_is_not_trusted` |
| POST без `Origin`, `Origin: null` | Отказ и при `Sec-Fetch-Site: cross-site`; тест на `null` | `test_api.py::test_foreign_writes_are_refused` |
| NaN/отрицательная стоимость и бюджет | Стоимость только конечная ≥ 0; `raw` без NaN; SSE `allow_nan=False`; `DAILY_BUDGET_USD` конечный ≥ 0 | `::test_odd_usage_…`, `test_unsafe_ui_settings_…` |
| Поток без терминального события вешает UI | `streamPost` выдаёт `fatal`; после терминального события — тишина | `tests/js/api.test.mjs` |
| `ConnectTimeout` не повторяется | Повтор как сетевая ошибка, итог `unreachable` | `::test_connect_timeout_is_retried`, `::test_connect_timeout_twice_is_unreachable` |
| Дедлайн тратится в очереди семафора; «after a retry» без повтора | Дедлайн после получения слота; текст по факту | `::test_deadline_starts_after_the_queue` |
| `BOX_INDEX` ловит любое «is a <слово>» | Только `is a box` | `::test_unrelated_400_mentioning_a_question_is_not_unsupported` |
| Второй раунд 400 без теста | Тест | `::test_second_round_400_is_refused_after_two_calls` |
| Предел `raw` 64 КБ без теста (и был не по итоговому размеру) | Обрезка подгоняется под сериализованный размер | `::test_raw_is_capped_at_64_kb` |
| Граница `max_request_bytes` не закреплена | Тест на 31 999 / 32 000 / 32 001 байт и на значение в `config.example.yaml` | `::test_max_request_bytes_boundary`, `::test_max_request_bytes_is_pinned_in_the_example_config` |
| Верх допуска суммы не закреплён | Суммы 1.002 и 0.998 — `invalid_answer`; 1.0009 и 0.9991 проходят | `test_normalize.py::test_bad_answers_are_invalid`, `::test_tolerance_inside_the_bound_passes` |
| 2 с повтора и локальный день не закреплены | Тесты | `::test_retry_delay_is_two_seconds`, `test_store.py::test_the_budget_day_is_the_local_day` |
| Тест обхода пути пустой | `Store.get` проверяется напрямую | `test_store.py::test_get_refuses_anything_but_an_indexed_id` |
| Запись и строка индекса не атомарны | `fsync` и `reconcile()` при старте | `test_store.py::test_reconcile_…` |
| Индекс перечитывается на каждое нажатие | Кэш строк по `(mtime, size)` | `test_store.py::test_rows_cache_follows_the_file` |
| `refresh_quietly` ловит не всё | Ловит любую ошибку в `error` | — |
| Сравнение: ключи прототипа, порядок A/B | `Object.hasOwn`, сортировка по ULID | `questions.test.mjs` (ключ `constructor`) |
| Ошибки `getRun` не показаны | Показываются в UI | ручная проверка |
| Пустые структурные `instructions` | Отклоняются | `test_schemas.py::test_invalid_questions` |
| Опечатка в `config.yaml` молча игнорируется | `EngineSpec` с `extra="forbid"` | `test_schemas.py::test_engine_config_typo_fails_loudly` |
| Извлечение падает на кривом `span`/`interval`/`unit` | Проверка типов, `invalid_answer` | `test_normalize.py::test_malformed_extractions_are_invalid_not_a_crash` |
| Модульный `app = create_app()` читает `.env` при импорте | Убран; приложение создаёт `app/__main__.py` | — |
| `PYENV_PYTHON` захардкожен | Запасной `python3` с PATH | — |
| `-q` в `addopts` прячет итог | Убран | — |
| `ZDR`-галочка остаётся отмеченной | Снимается после старта прогона и повтора | ручная проверка |

## Приняты как решения, без кода

| Находка | Решение и где записано |
|---|---|
| Каталог обновляется при старте, если кэш старше суток, а §13.2 говорит «по кнопке» | Оставлено: публичный запрос без ключа, нужен для цен бюджета. §13.2 уточнён |
| `state` принимает список, §6.1 — нет | Оставлено: OpenRouter принимает `state` как массив. §6.1 уточнён |
| Черновик с транскриптом живёт в `localStorage` | Записано в §18 и README; `make clean-runs` его не трогает |
| `too_long` не сверяется с `context_length` каталога | Не делается: оценка токенов для d1.1 завышена в 3.5 раза и отказывала бы в рабочих запросах; лишнее вернёт OpenRouter (413/400). §7.2 |
| При повторе после 5xx первая попытка могла быть оплачена и не учтена | Записано в §19: возможный недоучёт, отдельного учёта нет |
