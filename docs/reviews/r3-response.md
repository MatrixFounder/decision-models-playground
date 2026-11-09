# Раунд 3 (узкий) — ответ на adversarial-ревью исправлений раунда 2

> Ревью: [r3-critique.md](r3-critique.md), 2026-11-09, только изменения после раунда 2 (коммит `8aa2bea`, 31 файл).
> Вердикт WARNING: 0 CRITICAL, 0 HIGH, 1 MEDIUM, 22 LOW. HIGH и все три MEDIUM раунда 2 закрыты и подтверждены
> замерами; 9 тестов не краснели при откате исправления.
> После исправлений: `make test` — 447 passed, 1 skipped (pytest), 21 passed (node); `make smoke-local` — 7 из 7.
> Свои подсадки (копия дерева, проверка применения правки и отката по sha256, байткод выключен): 11 из 11 красные.

## MEDIUM

| Находка | Исправление | Тест |
|---|---|---|
| `multi` без описаний: mlx-vlm пишет id трижды, считали дважды — строка недооценивалась на 30 %, тест закреплял ошибку | `question_text` повторяет текст mlx-vlm: `"id": описание-или-id` и маркер `"id":`; одиночный `choice` — `код описание-или-id` (раньше переоценка в 2.7 раза) | `test_mlx_backend.py::test_the_count_is_an_upper_bound_of_mlx_vlms_real_row` — семь форм вопросов против настоящей строки mlx-vlm на настоящем токенизаторе: подсчёт ≥ строки и < 2× + 3000; `test_compat.py::test_multi_option_without_description_is_counted_three_times` |

## LOW

| Находка | Решение | Тест |
|---|---|---|
| Обход глубины в compat — в цикле событий и до компактной проверки (0.8 с на 6 МБ) | Разбор JSON и дешёвые проверки — в рабочем потоке; компактная длина раньше глубины; `json_depth` не кладёт скаляры в стек | `test_compat.py::test_cheap_checks_run_off_the_event_loop`, `::test_a_short_deep_state_is_refused_by_depth`, `::test_deep_state_is_refused_before_anything_is_rendered` (теперь 413) |
| UI: состояние глубины 32 рендерилось в 11.8 млн символов (+1.3 ГБ) до отказа | `rendered_length` — длина рендера без рендера, с остановкой на пределе; совпадает с `render_state` на 5000 случайных структурах | `test_mlx_engine.py::test_a_state_over_the_render_bound_is_never_rendered` |
| «Подсчёт в рабочем потоке» без теста | Тест | `test_mlx_engine.py::test_counting_runs_off_the_event_loop` |
| Второй SIGTERM: жёсткий выход не закреплён | Тест проверяет код возврата 1 (`os._exit(1)`) — обычная остановка даёт 0 | `test_entrypoint.py::test_a_second_sigterm_forces_the_exit` |
| `begin_close()` без теста; на остановке клиент получал текстовый 500 | По первому сигналу вызовы, ждущие MLX, сразу получают `Busy` → compat JSON 429 `busy` | `test_entrypoint.py::test_one_sigterm_stops_a_process_whose_model_call_hangs` (ответ JSON 429 за < 2 с, код 0); `test_local_models.py::test_shutdown_answers_calls_in_flight_with_busy_at_once` |
| Подпроцессные тесты: утечка ребёнка, `sleep` вместо условия, коды возврата, непрочитанный PIPE | Убийство ребёнка при сбое старта; ожидание `state == "busy"`; коды возврата; вывод в файл | `tests/test_entrypoint.py` |
| Широкие `except`: молчаливая обёртка нормализации, «токенизатор недоступен» на любую ошибку, нетестированный 500 | Обёртка пишет предупреждение; ошибку чтения `tokenizer.json` в `LoadError` превращает сам бэкенд, `MemoryError` → `too_long`, остальное — 500 с записью в лог | `test_normalize.py::test_the_never_raising_wrapper_logs_what_it_swallows`, `test_mlx_backend.py::test_a_corrupt_tokenizer_file_is_a_load_error`, `test_mlx_engine.py::test_out_of_memory_while_counting_is_too_long`, `test_compat.py::test_an_unexpected_precheck_failure_is_a_logged_json_500` |
| `D1_VISION_MAX_PIXELS` через голый `int()` | Неверное значение — предупреждение и значение по умолчанию | `test_mlx_engine.py::test_image_allowance_follows_d1_vision_max_pixels` |
| `_isfinite` маскировался обёрткой | Тест вызывает `_normalize` напрямую | `test_normalize.py::test_a_400_digit_number_is_caught_by_the_explicit_check` |
| Проверка `Host`: без точки в суффиксе, голый `.local`, добавление `COMPAT_HOST` не тестировались | Тесты; задокументировано, что `*` не поддерживается | `test_compat.py::test_host_allowed`, `::test_the_configured_compat_host_is_allowed` |
| Групповой подсчёт в предпроверке не тестировался | Тест с запасом в обе стороны, проверен подсадкой одной группы | `test_mlx_engine.py::test_precheck_counts_by_group_of_set_size` |
| `fsync` каталога на macOS не сбрасывает кэш диска | `full_fsync`: `F_FULLFSYNC`, при отказе — `fsync`; для записи, каталога и строки индекса | `test_store.py::test_the_record_its_directory_and_the_index_are_flushed_to_disk` |
| Смена движков и режима текст/JSON не снимала метку «черновик из прогона» | Обе — правка пользователя; правило откладывания вынесено в `web/js/draft.js` | `tests/js/draft.test.mjs` |
| Повтор проверял «идёт ли прогон» только до диалогов | Повторная проверка после диалогов | ручная проверка |
| Сравнение извлечений не учитывало `found` | `found` в подписи; изменение ≥ 0.05 отмечается | `tests/js/views.test.mjs` |
| Связка `app.js` с обработчиком не тестировалась | Обработчик сам решает, текущий ли вид, по геттеру: в `app.js` осталась одна строка `getCurrent: () => ui.view` | `tests/js/views.test.mjs` (логика); связка — ручная проверка в браузере |
| `scutil` синхронно в обработчике | Один раз (кэш), вне цикла событий | `test_api.py::test_compat_address_uses_the_bonjour_name` |
| Брошенная при остановке загрузка и повторная загрузка стоящим в очереди | После получения очереди при остановке — `Busy` | `test_local_models.py::test_a_queued_caller_does_not_reload_behind_an_abandoned_load` |
| Проверка остановки и постановка в очередь `MlxThread` не атомарны | Под одной блокировкой | — (сегодня гонка недостижима; обзор) |
| Мёртвый реэкспорт, недостижимая проверка глубины, невидимый `BREAK`, стиль | Реэкспорт убран; проверка глубины в предпроверке оставлена как вторая линия и получила свой тест; `BREAK = "​"`; `import re` наверх; лишние пустые строки | `test_mlx_engine.py::test_precheck_keeps_its_own_depth_guard` |
| Экранирование проверено только на непересекающихся спецтокенах d1 | Синтетический токенизатор с пересекающимися спецтокенами и не-спецтокеном против `prompt.plain` | `test_mlx_backend.py::test_escaping_matches_mlx_vlm_on_overlapping_specials` |

## Отмеченное вне рамок раунда

| Замечание | Решение |
|---|---|
| У `POST /api/runs` нет предела размера тела | Предел 16 МБ (413), §15 |
| `json.loads` тела compat в цикле событий (0.4 с на 6 МБ) | В рабочем потоке |
| `asyncio.run` может ждать рабочие потоки при остановке | Теперь в них только ограниченные задачи: подсчёт строки ≤ 16 × `context_tokens` символов, разбор ≤ 6 МБ |
