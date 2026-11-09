# Раунд 2 — ответ на adversarial-ревью исправлений P1 и P2

> Ревью: [r2-critique.md](r2-critique.md), 2026-11-09, вердикт WARNING (0 CRITICAL, 1 HIGH, 3 MEDIUM, ~30 LOW).
> Снимок `c2162dfa2f83df85`. Проверка раунда 1: 95 из 115 подсаженных регрессий поймали названные тесты; 8 тестов
> оказались пустыми, 2 подсадки повесили прогон.
> После исправлений: `make test` — 405 passed, 1 skipped (pytest), 16 passed (node); `make smoke-local` на настоящем
> стеке — 7 из 7 ([p2-smoke.json](p2-smoke.json)).

«Тест» — что падает, если исправление откатить. Подсадку для зависшей остановки я проверил сам в отдельной копии:
без исправления оба теста красные, с ним зелёные.

## HIGH и MEDIUM

| Находка | Исправление | Тест |
|---|---|---|
| HIGH (внесена исправлением P2): строка рендерится целиком в цикле событий до любых пределов; 40 КБ вложенности → 800 млн символов, +1.7 ГБ | Глубина `state` ≤ 32 на входе (`DecisionRequest` → 422, compat → 400); компактная длина ≤ 16 × `context_tokens` без рендера; рендер и подсчёт — в рабочем потоке; дешёвая проверка compat — по компактной длине | `test_compat.py::test_deep_state_is_refused_before_anything_is_rendered` (пик памяти < 50 МБ, < 1 с), `::test_state_depth_cap_is_32`; `test_mlx_engine.py::test_a_state_deeper_than_32_is_refused_at_the_door`, `::test_a_deep_state_is_a_422_on_the_ui`, `::test_a_huge_state_is_too_long_without_rendering` |
| MEDIUM: подсчёт не учитывает экранирование спецтокенов mlx-vlm (+44 %, на UI ×8) | `escape_specials` повторяет `plain()` mlx-vlm по спецтокенам `tokenizer.json`, затем подсчёт | `test_mlx_backend.py::test_escaping_is_mlx_vlms_plain` (сверка с настоящим `plain()`), `::test_special_token_strings_count_as_the_model_reads_them`, `::test_the_reviews_special_token_state_is_too_long_on_the_real_stack` |
| MEDIUM: зависший вызов MLX не даёт процессу остановиться | Первый сигнал — `begin_close()` (отмена больше не ждёт MLX), uvicorn `timeout_graceful_shutdown=5`; второй — принудительно и `os._exit` через 3 с; `submit` после остановки бросает `RuntimeError` | `test_entrypoint.py::test_one_sigterm_stops_a_process_whose_model_call_hangs`, `::test_a_second_sigterm_forces_the_exit` (подпроцесс `tests/hang_app.py`); `test_local_models.py::test_closing_stops_absorbing_cancellation`, `::test_submit_after_shutdown_raises_instead_of_hanging` |
| MEDIUM: у `node --test` нет таймаута | `--test-timeout=10000` в `make test`; `collect()` в тесте SSE падает через 3 с без терминального события | подсадка P1-L12 теперь падает, а не висит |

Тест, найденный по ходу: FastAPI отдавал на 422 весь вход и рекурсивно его сериализовал — глубокий `state` давал
`RecursionError` (500). Свой обработчик 422 отдаёт только `loc`, `msg`, `type` (`test_a_deep_state_is_a_422_on_the_ui`).

## LOW

| Находка | Решение | Тест |
|---|---|---|
| `submit` после остановки — вечный future | `RuntimeError` | `::test_submit_after_shutdown_raises_instead_of_hanging` |
| `images: 5` / строка вместо списка → текстовый 500 / 413 | Проверка типа до дешёвой проверки → 400 | `test_compat.py::test_images_of_the_wrong_type_are_a_json_400` |
| Нет/битый `tokenizer.json` → текстовый 500 | Любая ошибка токенизатора → `LoadError` → 503 / `unreachable` | `::test_a_broken_tokenizer_is_model_unavailable`, `test_mlx_engine.py::test_a_broken_tokenizer_is_unreachable` |
| `OverflowError` выходит из нормализации | `_isfinite`, `_finite_number`; обёртка ловит любое исключение | `test_openrouter_engine.py::test_a_400_digit_number_is_one_invalid_answer_and_keeps_the_cost`, `::test_normalize_never_raises_whatever_the_inner_failure` |
| `COMPAT_HOST=::1` принят, но неработоспособен; текстовый 400 от Starlette | IPv6-литерал не принимается; своя проверка `Host` → JSON 400 `forbidden_host` | `::test_ipv6_compat_host_refuses_to_start`, `::test_an_unlisted_host_is_a_json_400` |
| Второе «Открыть» затирает отложенный черновик | Черновик помнит происхождение (`fromRun`); черновик, записанный открытым прогоном, не откладывается; правка пользователя снимает метку | ручная проверка |
| Согласие на повтор берётся из галочки Workbench | Повтор спрашивает сам: оценка и отдельное согласие на движки без ZDR | ручная проверка |
| Переключатель JSON молча падает на повторе ключа | Ошибка показывается | ручная проверка |
| Сравнение извлечений показывает `NaN` | `compareCell` сравнивает значения | `tests/js/views.test.mjs` |
| Запись прогона `indent=1` растёт с квадратом глубины | Глубина ≤ 32 на входе | `::test_a_deep_state_is_a_422_on_the_ui` |
| Строка считает все вопросы, а mlx-vlm пакует по `d1_set_size` | `state` + самая тяжёлая группа; `set_size` из `config.json` | `test_compat.py::test_questions_count_by_group_not_all_at_once`, `test_mlx_backend.py::test_set_size_comes_from_the_model_config` |
| Маркеры `multi` и предел пикселей не учтены | id вариантов `multi` считаются дважды; картинка — по `D1_VISION_MAX_PIXELS` | `::test_multi_option_ids_are_counted_twice` |
| Нет `fsync` каталога после переименования | `fsync` каталога | — (поведение ФС при сбое питания тестом не воспроизводится) |
| Пустые тесты: H3a, M1c, L3, L14, E1, закрепление предела, M4a, L22, L23, L27 | Каждый переписан так, чтобы падать при откате | `test_nested_state_is_measured_as_the_model_reads_it`, `test_images_are_decoded_off_the_event_loop` (проверка через `get_running_loop`), `test_an_adopted_load_…` (last_used час назад), `test_deadline_starts_after_the_queue` (5 вызовов), `test_normalize_never_raises_…`, `test_the_row_bound_is_pinned_in_the_shipped_config`, `test_the_models_tokenizer_can_refuse_…`, `test_get_refuses_…` (шпион `rows`), `test_rows_cache_follows_the_file` (счёт чтений), `test_malformed_extractions_…` (через `_normalize`) |
| Исправления без тестов: `reconcile` при старте, `allow_nan`, широкий `catch` каталога, обработчик потока по видам, сравнение | Тесты | `test_api.py::test_reconcile_runs_at_start_up`, `::test_sse_frames_refuse_nan`, `::test_catalog_refresh_survives_an_odd_shape`; `tests/js/views.test.mjs` (логика вынесена в `web/js/runview.js` и `compareRuns`) |
| Smoke-журнал доказывает меньше, чем названия проверок | Аудио: клип 10.6 с добавил 133 токена (12.5/с, как в SPIKE); снимок по `-\d{8}$` | [p2-smoke.json](p2-smoke.json), проверки 1 и 2b |
| Комментарий `max_waiting` говорит «503» | Исправлен: 429 `busy` | — |
| Решения без записи: FIFO-перезагрузки; массив `state` без замера | Строка в §8; §6.1 помечает массив как взятый из справочника API, не из замера | — |
| Адрес compat по `gethostname` | `scutil --get LocalHostName` + `.local` | — (ручная проверка) |
| `POST /api/local/models/{id}/unload` ждёт без предела | 5 с, затем 409 | `test_api.py::test_unload_behind_a_hung_call_answers_409` |
