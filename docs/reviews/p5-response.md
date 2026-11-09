# P5 — ответ на adversarial-ревью

> Ревью: [p5-critique.md](p5-critique.md), VDD adversarial, 2026-11-09, вердикт WARNING (0 CRITICAL, 2 HIGH, 5 MEDIUM,
> 13 LOW). Снимок дерева `208fdef2cc02db2d`, подтверждён ревьюером без изменений. Вынос `app/batch.py` из прогона
> набора ревьюер сверил построчно: поведение то же (12 из 13 подсадок в нём красные в тестах наборов).
> После исправлений: `make test` — 719 passed, 1 skipped (pytest), 54 passed (node).
> Подсадки строителя: 39 регрессий на копии дерева — на каждое исправление, на каждую зелёную подсадку ревьюера и на
> правила `OPENROUTER_BASE_URL`; все 39 красные, каждое восстановление сверено по sha256, копия после них совпадает с
> исходной ([p5-plantings.log](p5-plantings.log)).
> Повторная приёмка UC-2 вживую — 180 вызовов, $0.0026, 14 с (DESIGN §11.1).
>
> Вне снимка ревью в той же ветке — `OPENROUTER_BASE_URL` (по просьбе владельца: корень OpenRouter из `.env` и
> `base_url` движка вместо адресов в коде, §7.2, §17). Его правила проверены тестами `tests/test_base_url.py` и
> подсадками того же журнала (строки «URL …»), но не ревьюером.

«Тест» — что падает, если исправление откатить. «X…», «S…», «W…», «B…» — подсадки ревьюера, оставшиеся зелёными.

## HIGH

| Находка | Исправление | Тест |
|---|---|---|
| F1. Отчёт падал (500, навсегда) на вопросах `text`/`number`/`date`: вариантов нет, `max()` пустого | Извлечение читается как P(found) — один исход, без «смены лучшего»; `top_of` пустого — `None`. Число с диапазоном отвечает интервалом без вероятности: такие ответы считаются отдельно (`unmeasured`, «сравнивать нечего») | `test_extraction_questions_do_not_break_the_report` (пресет `demo_extraction`, 2 повтора, 2 формулировки), `test_extraction_questions_read_p_found_and_never_crash` |
| F2. «Лучший — первый показанный» сравнивался с 1/n и при несбалансированной сетке (> 4 вариантов, цикл): модель без тяги читалась как тяга (0.5 против 0.2) | Ожидаемая доля — по этим же порядкам: устойчивый ответ формулировки (лучший по всем её порядкам), отмеченный там, где он стоял первым. Экран отмечает тягу, если доля выше ожидаемой на 0.15+ или подъём ≥ 0.05 (`pullsFirst`) | `test_first_position_is_read_against_these_orders_not_one_in_n`; JS «a pull to the first place is read against the expected share…» |

## MEDIUM

| Находка | Исправление | Тест |
|---|---|---|
| F3. Короткий список порядков сравнивался сам с собой, эффект размывался (смена 0.6 вместо 1.0) | Порядки вопроса — свои: ячейки одного порядка объединены, каждый порядок против исходного один раз. Одновременная перестановка нескольких `choice` описана в §11.1 | `test_a_cycled_question_is_never_compared_with_itself` |
| F4. Формулировка шла в своём порядке вариантов и вопросов | Каждая формулировка приводится к порядку базы до оси порядка | `test_a_formulation_is_asked_in_the_base_order` |
| F5. Оценка останавливала цикл событий (4.7 с), лимит проверялся после построения сетки | Сетка считается арифметически до построения; оценка — один раз на ячейку (кэш по запросу) и вне цикла; построение при запуске — тоже | `test_the_estimate_reads_each_cell_once_and_off_the_loop`, `test_the_grid_cap_is_checked_before_the_grid_is_built` |
| F6. Деньги и терминальные события эксперимента без тестов (X7, X14) | Тесты по образцу P4 | `test_an_unsaved_experiment_is_charged_and_released`, `test_a_cancelled_experiment_charges_the_calls_in_flight`, `test_a_report_that_cannot_be_built_still_ends_the_experiment` |
| F7. Две защиты `run()` без тестов (W2, W3) | — | JS «a grid changed while its estimate was on the way is not sent», «a run started while the confirm was open is not doubled» |

## LOW

| Находка | Решение | Тест |
|---|---|---|
| F8. Остановка по бюджету отрезала последние формулировки | Задания — повтор за повтором | `test_a_budget_stop_thins_every_cell_instead_of_dropping_a_formulation` |
| F9. Шум сравнивался с размахом одиночных повторов (завышен в 3–4 раза) | `diff_se` — стандартная ошибка разности двух средних из объединённого разброса; правило «меньше ~2 SE — шум». Вживую это меняет вывод: сдвиг `score` от английской формулировки 0.04–0.05 при SE 0.005–0.008 — эффект, а не шум | `test_choice_metrics_by_hand` (0.0408 руками) |
| F10. Доля «первого» видела только argmax; у `multi` — шум | «Подъём первого места» — средняя разница вероятности варианта первым и не первым (от раскладки не зависит, видит тягу без смены ответа); у `multi` доли нет, только подъём | `test_the_lift_sees_a_pull_that_never_changes_the_top`, `test_multi_reads_the_lift_not_the_argmax` |
| F11. «Ровно 33.3 %» в приёмке подавалось как доказательство | Переписано: при 3! порядках и неизменном ответе доля равна 1/3 сама собой; доказательство — \|ΔP\| и смена | — (DESIGN §11.1) |
| F12. `rerun` эксперимента молча гонял сравнение | 400 для всего, кроме `compare` | `test_an_experiment_is_not_rerun_as_a_compare` |
| F13. `target` проверялся и у эксперимента | Только у набора | `test_the_report_kind_and_target` |
| F14. `/?report=` строил отчёт дважды | `?format=kind` — вид из индекса, отчёт — один раз на своём экране | `test_the_report_kind_and_target`; JS `reportScreen` |
| F15. Отчёт открывался рядом с текущим state Workbench | Экран и отчёт Workbench не читают; отчёт несёт свой запрос и настройки, кнопка «Те же настройки» | `test_the_report_kind_and_target` (запрос в отчёте); JS «opening the screen or a report does not read the Workbench…» |
| F16. «Эксперимент…» во время прогона подменял запрос | `takeFromWorkbench` во время прогона ничего не берёт | JS «nothing is taken from the Workbench while an experiment runs» |
| F17. Мелкие дыры ошибок `run()`/`estimate()`/`load()` | Второй `body()` в `try`, изменённые настройки — сообщение и новая оценка; ранний выход оценки чистит старую ошибку; у `load()` счётчик | JS «no engine picked: an old estimate error does not stay», «a grid changed…»; счётчик `load()` — без теста |
| F18. Непроверенные границы (X5, X12, X3, S2, S8, B9, W10) | Тест на каждую: ровно 2 000 вызовов — можно, 21 повтор — 422, медиа в каждом вызове, ничья — по порядку базы, неудачные вызовы считаются, вопрос, отклонённый профилем, не входит в оценку, экран отчёта по виду — чистая функция | `test_exactly_2000_calls_is_allowed_and_the_limits_are_pinned`, `test_the_media_goes_with_every_call`, `test_ties_go_to_the_base_order_not_the_shown_one`, `test_failed_calls_count_in_calls_not_in_answered`, `test_the_estimate_leaves_out_questions_the_profile_refuses`; JS `reportScreen` |
| F19. `Batch` не проверял, что вопросы у заданий одни | Проверка формы (ключи, типы, размеры) при создании | `test_a_batch_refuses_jobs_with_other_questions` |
| F20. Нет названия; «(choice)» без multi; `versions()` в `dataset_run` | Поле «Название»; «(choice, multi)»; `versions` — в `app/batch.py` | — |
