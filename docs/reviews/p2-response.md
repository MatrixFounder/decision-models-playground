# P2 — ответ на adversarial-ревью

> Ревью: [p2-critique.md](p2-critique.md), VDD adversarial, 2026-11-09, вердикт WARNING (0 CRITICAL, 4 HIGH, 5 MEDIUM,
> 31 LOW). Снимок дерева `ff3b35392ee6216c`, подтверждён ревьюером без изменений.
> После исправлений: `make test` — 369 passed, 1 skipped (pytest), 11 passed (node); `make smoke-local` на настоящих
> mlx-vlm, моделях и токенизаторе — 6 из 6 проверок, журнал [p2-smoke.json](p2-smoke.json).

«Тест» — что падает, если исправление откатить.

## HIGH

| Находка | Исправление | Тест |
|---|---|---|
| `instructions`-словарь уходит в mlx-vlm как путь к файлу или URL (файловый оракул, SSRF, зависание потока MLX) | compat: только непустой текст, иначе 400; движок: такой вопрос `unsupported` и не отправляется | `test_compat.py::test_instructions_must_be_text`; `test_mlx_engine.py::test_media_dict_in_instructions_never_reaches_the_model`, `::test_only_media_dict_questions_means_no_call`; smoke 3a |
| `images` принимает EPS, Ghostscript работает на данных клиента | `Image.open(..., formats=["PNG","JPEG","WEBP"])` | `test_compat.py::test_only_png_jpeg_webp_and_no_ghostscript` (EPS, TIFF; подпроцессы запрещены) |
| Вложенный `state` считается компактно, а mlx-vlm пишет его с `indent=2` (×7.7) | `render_state` = рендер mlx-vlm; им считаются и проверка символов compat, и токены движка | `test_compat.py::test_nested_state_is_measured_as_the_model_reads_it`; `test_mlx_engine.py::test_nested_state_is_too_long_before_any_load`; `test_mlx_backend.py::test_render_state_is_what_mlx_vlm_writes`, `::test_the_nested_state_from_the_review_is_counted_in_full`; smoke 3b |
| Длина текста вопросов не ограничена | Считается вся строка: `state` + все вопросы + служебные токены, картинки, аудио | `test_compat.py::test_question_text_counts_toward_the_row` (58 000 → 413, 20 000 → 200), `::test_long_options_count_too`, `::test_the_row_bound_is_the_configured_context`; `test_mlx_engine.py::test_question_text_is_counted` |

## MEDIUM

| Находка | Исправление | Тест |
|---|---|---|
| Картинки декодируются в цикле событий до очереди; очередь без предела | ≤ 4 картинки по ≤ 16 Мп; декодирование в рабочем потоке; `local.max_waiting` = 8 → 429 `busy` / `rate_limited` | `test_compat.py::test_images_limits`, `::test_images_are_decoded_off_the_event_loop`, `::test_busy_queue_is_429`; `test_local_models.py::test_the_queue_is_bounded` |
| Chunked-тело читается целиком до проверки размера | Тело считается по мере чтения, обрыв на пределе | `test_compat.py::test_chunked_body_stops_at_the_limit` (на уровне ASGI: TestClient сам буферизует тело) |
| У локальных вызовов нет дедлайна, остановка ждёт всю очередь | Ожидание ограничено `timeout_s` → `WaitTimeout`; поток MLX — демон; `aclose` с таймаутом | `test_local_models.py::test_waiting_past_the_deadline_is_a_wait_timeout`, `::test_aclose_returns_while_a_call_hangs`; `test_entrypoint.py::test_both_servers_start_and_stop_cleanly_on_sigterm` |
| Отображение ошибок compat без теста | Тест на каждое: 413, 503 без путей, 400, 500 `model_error`, 429 | `test_compat.py::test_error_mapping_for_whip_insight`, `::test_busy_queue_is_429`, `::test_odd_model_output_is_a_json_500_or_tolerated` |
| `MlxBackend` и `SnapshotDates` без тестов | Тесты с подменой `mlx_vlm.load`, кэша HF, `HfApi`, `mlx.core` | `tests/test_mlx_backend.py` (18 тестов) |

## LOW

| Находка | Решение | Тест |
|---|---|---|
| Вторая отмена отпускает очередь посреди загрузки | `_on_thread` ждёт конца работы при любом числе отмен и принимает загруженную модель | `test_local_models.py::test_double_cancel_keeps_the_lock_until_the_load_ends` |
| Отмена при неудачной загрузке оставляет `loading` | Состояние сбрасывается в `finally` | `::test_cancel_during_a_failing_load_resets_the_state` |
| Принятая после отмены модель сразу выгружается как простаивающая | `last_used` ставится при принятии | `::test_an_adopted_load_is_not_unloaded_as_idle_at_once` |
| `/health` скрывает ошибку загрузки | `status: degraded`, `error: load_failed`; без подробностей | `test_compat.py::test_error_mapping_for_whip_insight` |
| Сбой не из `ValueError` — текстовый 500 | JSON 500 `model_error`; `usage: null` терпится; NaN → 500 (проверка до `json_safe`, тест нашёл, что раньше NaN тихо становился `null`) | `::test_odd_model_output_is_a_json_500_or_tolerated` |
| Нет проверки `Host` у compat; `/health` без токена показывает репозиторий и снимок | `TrustedHostMiddleware` (`127.0.0.1`, `localhost`, `*.local`, настраивается); `/health` урезан | `::test_compat_host_header`, `::test_health_reports_the_default_model` |
| `KEY_RE` с `$` пропускает `\n` | `valid_key` через `fullmatch` | `::test_strict_keys_and_flags` |
| `state` — любое JSON-значение | Только строка, объект, массив | `::test_state_must_be_text_object_or_array` |
| Тело ошибки повторяет произвольные значения | Только строки ≤ 64 символов | `::test_error_bodies_echo_only_short_strings` |
| `multiple: "false"` превращает вопрос в `multi` | Только булево | `::test_strict_keys_and_flags` |
| Схема Bearer чувствительна к регистру | Схема без учёта регистра, токен — `compare_digest` | `::test_bearer_scheme_is_case_insensitive` |
| Сила токена — только длина; права `.env` не проверяются | ≥ 10 разных символов, без пробелов по краям; предупреждение о правах `.env` | `::test_uniform_or_padded_tokens_refuse_to_start` |
| Ошибка загрузки с путями уходит клиенту в LAN | 503 без подробностей | `::test_error_mapping_for_whip_insight` («/Users/» нет в ответе) |
| `disk_gb` ходит по диску на каждой проверке | Кэш на минуту | `test_mlx_backend.py::test_disk_size_is_cached` |
| `describe()` вызывает MLX из цикла событий | Память снимается на потоке MLX после операции | `test_local_models.py::test_describe_makes_no_mlx_call` |
| Запрос, который всё равно отклонят, сначала вытесняет и грузит модель | Предпроверка до очереди отдельным токенизатором и проверками mlx-vlm | `test_mlx_engine.py::test_a_too_long_request_does_not_evict_the_loaded_model`, `::test_mlx_vlm_refusal_happens_before_the_queue`; smoke 3b–3c |
| Поиск даты снимка без сети тормозит каждую загрузку | Неудача кэшируется на процесс; `HF_HUB_OFFLINE` | `test_mlx_backend.py::test_offline_snapshot_…`, `::test_hf_hub_offline_is_honoured` |
| Сбой прогрева Omni делает модель незагружаемой | Прогрев некритичен; при сбое загрузки — `mx.clear_cache()` | `::test_a_failed_warm_up_does_not_fail_the_load`, `::test_any_load_failure_is_a_load_error_and_frees_memory` |
| mlx-vlm копит токенизаторы | Очистка `_TOKENIZERS` и lru-кэшей при выгрузке | `::test_unload_frees_mlx_memory_and_mlx_vlm_tokenizer_caches` |
| `latency_ms` включает очередь и загрузку | `latency_ms` = вызов; `timings` = очередь, загрузка, вызов | `test_mlx_engine.py::test_latency_is_the_call_and_timings_hold_the_rest`; `test_local_models.py::test_timings_split_queue_load_and_call` |
| Адрес compat в LAN показан как `0.0.0.0` | mDNS-имя Mac (`*.local`) | — (`compat_address`, ручная проверка) |
| `Content-Length` из не-ASCII цифр | `isascii() and isdigit()` | — (h11 отвергает такие заголовки раньше приложения) |
| Тест «один поток» проходит с пулом | Сравнение с идентификатором потока менеджера; параллельные вызовы | `test_local_models.py::test_concurrent_calls_still_run_on_the_one_mlx_thread`, `::test_every_mlx_operation_runs_on_one_thread` |
| Жизненный цикл без тестов | `aclose`, наблюдатель, общий менеджер, подпроцесс `python -m app` | `::test_aclose_unloads_and_stops_the_watcher`, `::test_the_watcher_unloads_an_idle_model`, `test_mlx_engine.py::test_shared_manager_survives_the_ui_shutting_down`, `tests/test_entrypoint.py` |
| Chunked-предел и порядок «токен до размера» не закреплены | Тесты | `test_compat.py::test_chunked_body_stops_at_the_limit`, `::test_auth_comes_before_the_size_check` |
| «Отказ до декодирования» не проверялся | `Image.load` подменён на падение | `::test_pixel_limit_is_checked_before_decoding` |
| Подделки слишком мягкие | `FakeBackend.validate` и `FakeModel` вызывают проверки mlx-vlm и падают на не-текстовых `instructions`; тесты с настоящим `tokenizer.json` (пропуск, если модели нет) | `tests/conftest.py::mlx_check`; `test_mlx_backend.py::test_the_real_tokenizer_counts_like_the_spike` |
| README и §9.1 расходятся с кодом по `media` | §9.1 уточнён: `media` в compat — с P6 | — |
| `make stop` не видит процесс только compat | Проверяются оба порта | — |
| Живую приёмку нельзя повторить | `make smoke-local` и его журнал | [p2-smoke.json](p2-smoke.json) |

## Приняты как решения, без кода

| Находка | Решение |
|---|---|
| FIFO-очередь перезагружает модель при чередовании двух моделей | Оставлено: справедливость очереди важнее; две модели одновременно не помещаются (§8). Чередование UI и compat на разных моделях — не рабочий сценарий |
| Счёт картинок в строке — оценка (1 700 токенов), а не точный подсчёт процессора | Оценка с запасом по d1-шному пределу 1.6 Мп; точный подсчёт требует процессора модели, то есть её загрузки |
