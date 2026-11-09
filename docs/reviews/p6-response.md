# P6 — ответ на adversarial-ревью

> Ревью: [p6-critique.md](p6-critique.md), VDD adversarial, 2026-11-09, вердикт WARNING (0 CRITICAL, 1 HIGH, 4 MEDIUM,
> 21 LOW). Снимок дерева `8674df277b2e0abb`, подтверждён ревьюером в начале и в конце. Арифметику указателя ревьюер
> сверил с маршрутом whip-insight: округление — на 404 005 значениях, деление на блоки — на 18 145 парах (B, N),
> расхождений нет.
> После исправлений и повторной проверки: `make test` — 880 passed, 1 skipped (pytest), 63 passed (node).
> Подсадки строителя: 49 регрессий на копии дерева — на каждое исправление и на каждую зелёную подсадку
> ревьюера (P2, P4, P7, P10, P11, P12, P14, P15, P16, P18); все 49 красные (одна переделана: первая правка ломала синтаксис и краснела не тем тестом), каждое восстановление сверено по
> sha256, копия после них совпадает с исходной ([p6-plantings.log](p6-plantings.log)).
> Живая проверка F3 в браузере: прогон с указателем на d1-2B показывает текст реплики во время прогона, без повторного
> открытия.

«Тест» — что падает, если исправление откатить. «P…» — подсадки ревьюера, оставшиеся зелёными.

## HIGH

| Находка | Исправление | Тест |
|---|---|---|
| F1. Числа из цифр и слов читались неверно: «3 тысячи 500 рублей» → 1000, «2 миллиона триста тысяч» → 1 300 000; ведущая «.» срезалась (P18) | Разбор по токенам: группы по шкалам по убыванию, группа — одно число цифрами или слова в порядке числа, не вперемешку; группа после шкалы меньше 1000; «тыс»/«млн»/«млрд» требуют числа; ведущие «.» и «,» не срезаются. «3 тысячи 500» → 3500, «2 миллиона триста тысяч» → 2 300 000, «сто 5», «5 сто», «.5» → `null` | `test_numbers_read`, `test_numbers_that_would_be_a_guess_stay_none` (все случаи ревьюера) |

## MEDIUM

| Находка | Исправление | Тест |
|---|---|---|
| F2. Окна whip-insight получали 400: `t0` не принимался, повтор реплики (два попадания) — тоже; варианты не по порядку реплик | `t0` — реплика; реплики транскрипта нумеруются с `t0`, как в `turns.js`; повтор реплики в окнах допустим, варианты — различные реплики по номеру (`fragmentTurns`). Фикстура — запросы, собранные `detect()` whip-insight | `test_whip_insights_own_windows_are_taken_as_they_are`, `test_whip_insight_windows_run_with_the_routes_options`, `test_out_of_order_fragments_give_the_routes_order`, `test_turns_are_lines_numbered_from_zero_as_whip_insight_numbers_them` |
| F3. Во время прогона реплика указателя показывалась без текста (вид держал транскрипт, а не реплики) | `run_started` прогона с указателем несёт отправленный запрос; вид берёт его и перерисовывается. Подсветка `span` сверяется с тем, что читали движки (JSON — без подсветки) | `test_a_yes_cites_a_turn_and_the_pass_is_paid` (запрос в `run_started`), `test_a_run_without_the_pointer_does_not_echo_its_request`; JS «a live pointer run shows the turns the engines read…» |
| F4. Тест «оценка и резерв покрывают проходы» оставался зелёным без проходов в сумме (P7) | Тест сверяет сумму движка с основным вызовом плюс проходы точно; прогон, где основной вызов в день помещается, а с проходами — нет, останавливается `budget_stop` без вызовов | `test_the_estimate_is_the_main_call_plus_the_passes`, `test_the_reserve_stops_a_run_the_passes_would_push_over_the_day` |
| F5. Ошибка в коде указателя после оплаченных вызовов давала `run_error` без записи и без учёта денег; другой движок продолжал платные вызовы после `run_error` | Ошибка указателя — отказ `internal` этого указателя, ответы и оплаченные проходы сохраняются. Прогон ждёт все движки (`gather(..., return_exceptions=True)`): вызовов после терминального события нет; расход завершившихся движков засчитывается как несохранённый | `test_a_pointer_bug_after_a_billed_pass_keeps_the_answers_and_the_money`, `test_an_engine_that_breaks_waits_for_the_others_and_their_money_counts` |

## LOW

| Находка | Решение | Тест |
|---|---|---|
| F6. Больше B² реплик: основной вопрос всё равно спрашивался | Как маршрут: весь запрос движка `too_long` без вызова, в оценке — 0 | `test_past_b_squared_the_whole_request_is_too_long_and_makes_no_call` |
| F7. P(true) в [0.4995, 0.5): ячейка «false», а указатель процитировал реплику | Строка указателя помечена «“да” после округления» с объяснением правила маршрута | JS «a pointer under a «false» cell is marked…» |
| F8. Неизвестная цена основного вызова + цена прохода → `provider` | Источник — самый слабый из частей; токены неизвестны, если неизвестны у основного | `test_folded_usage_is_never_more_certain_than_its_parts` |
| F9. `passes` считал проход, отклонённый до отправки | Считаются только ушедшие к модели: не отказ здесь (длина в байтах, ZDR, бюджет, профиль) | `test_a_pass_refused_before_sending_is_not_counted`, `test_a_call_refused_here_is_not_a_pass` |
| F10. Размер блока и граница B² без теста (P2, P4) | Тест блоков против формулы маршрута при N = k·B и N = B²; прогон ровно на B² реплик — два прохода. `point_one` берёт границу из `passes()` | `test_b_blocks_and_passes_against_the_route`, `test_exactly_b_squared_turns_take_two_passes` |
| F11. Флаги ffmpeg без теста (P10, P11); без `-f` ffmpeg 8 читает ffconcat | Тест: concat-скрипт, поданный как wav, — 400, названный в нём файл не прочитан; argv проверен | `test_ffmpeg_reads_the_named_format_and_follows_no_script`, `test_the_ffmpeg_argv` |
| F12. `MAX_UPLOAD` не закреплён (P12) | Значение закреплено; тело ровно в предел без `Content-Length` проходит, на байт больше — 413 | `test_the_upload_limit_is_16_mib_and_reaches_exactly_it` |
| F13. Тест «только свои имена» ничего не проверял (P16) | Файлы с чужими именами лежат в `media/` — 404; своё имя — 200 | `test_files_the_store_did_not_name_are_not_served` |
| F14. Проверки результата ffmpeg без теста (P14, P15) | Пустой WAV — 400; подменный ffmpeg, пишущий стерео, — 400, ничего не сохранено | `test_a_recording_without_sound_is_refused`, `test_what_ffmpeg_writes_is_checked` |
| F15. Живой WebM без длины обрезался молча | `cut: true`, если длина неизвестна, а клип заполнен; экран: «первые 30 с (запись длиннее или её длина неизвестна)» | `test_the_cut_is_reported_even_when_the_recording_states_no_length` |
| F16. Пустое поле порога ставило 0 | Пустое или неверное поле оставляет прежний порог | JS «an emptied or invalid threshold field keeps the old threshold» |
| F17. День недели, «тыс» отдельно, «350 000 р.», ISO, «1,005 тыс.» | День недели сверяется с датой; ISO читается; единица с точкой; аббревиатура шкалы требует числа; десятичные — `Decimal` | `test_dates_read`, `test_dates_that_would_be_a_guess_stay_none`, `test_numbers_read` |
| F18. Исключение нормализатора делало ответ модели `invalid_answer` | Любая ошибка — `normalized: null`; текст длиннее 80 символов не читается | `test_a_reading_that_fails_never_sinks_the_models_answer` |
| F19. Две цифры §14.1 не совпадали с записями; подсказка «до 240» | $0.000164 и 32 422 байта; подсказка — «до B (B = min(240, вариантов движка))» | — (DESIGN §14.1, i18n) |
| F20. HEIC/AVIF читались как «mov audio»; AMR-WB не узнавался | Бренд `ftyp` проверяется: HEIC/AVIF — 400 «сохраните как JPEG или PNG»; `#!AMR-WB` — `amr` | `test_iphone_pictures_and_wideband_amr_are_told_apart` |
| F21. Инструкция обрезала пробелы вопроса | Вопрос подставляется как написан, как в `detect.js` | `test_the_preferred_answer_and_the_instruction`, `test_whip_insights_own_windows_are_taken_as_they_are` (байт в байт с маршрутом) |
| F22. `splitlines()` делил по U+2028; «Имя:» отдельной строкой становилось репликой | Деление только по `\n`; «Имя:» без текста называет участника следующей строки. Формат «строка = реплика» описан в §14.1 | `test_a_line_is_split_on_newlines_only_and_a_bare_speaker_names_the_next_line` |
| F23. Нет предела конвертаций, `.upload-*` после сбоя, загрузки не чистятся | Две конвертации одновременно; `.upload-*` удаляются при старте; тело в одной копии; `make clean-media`. Автоматически загрузки не удаляются: на них ссылаются записи | `test_at_most_two_conversions_at_once`, `test_temp_files_of_a_crashed_upload_are_swept_at_start` |
| F24. Оценка и проходы читали профиль в разное время | Профиль — один снимок на прогон; проход, который живой профиль уже не обслуживает, не отправляется | `test_the_passes_read_the_profile_the_estimate_read` |
| F25. Вариант без описания считался как пустая строка, на проводе — `null` | Считается как `null` (4 символа). Кавычки ключей поглощает множитель 1.5: живая оценка проходов Jev — $0.000196 против факта $0.000109 | `test_a_null_option_is_sized_as_it_is_sent` |
| F26. Текст из пробелов при медиа уходил текстом | `state: null` (`bodyState` в `draft.js`) | JS «with media, a blank text is no state» |

## Наблюдение после исправлений

Нумерация с `t0` сдвинула вероятность указателя у d1-2B на том же звонке: та же реплика, P 0.598 вместо 0.698. Метки
реплик — часть входа модели, поэтому плейграунд нумерует их так же, как whip-insight (DESIGN §14.1).

## Повторная проверка (2026-11-09, по просьбе владельца)

Пробы ревьюера (`test_adv_p6*.py`, каждая проходила, пока дефект был в коде) прогнаны на исправленном дереве.
Статус каждой:

| Проба | Находка | Статус | Чем подтверждено |
|---|---|---|---|
| `test_whip_insight_windows_state_is_refused` ×2 | F2 | исправлено | проба падает: окна не отклоняются |
| `test_fragments_out_of_order_make_different_blocks` | F2 | исправлено | проба падает: блоки одинаковые |
| `test_main_cost_unknown_pass_cost_known_is_labelled_provider` | F8 | исправлено | проба падает: источник `none` |
| `test_past_b_squared_the_main_question_is_still_asked` | F6 | исправлено | проба падает: вызовов нет, движок `too_long` |
| `test_a_pass_refused_before_sending_counts_as_a_pass` | F9 | исправлено | проба падает: `passes: 0` на настоящем отказе Jev по `max_request_bytes` |
| `test_a_pointer_bug_after_billed_calls_loses_the_run_and_its_money` | F5 | исправлено | проба падает: `run_done`, запись есть, расход засчитан |
| `test_after_run_error_the_other_engine_keeps_spending` | F5 | **исправлено при повторной проверке** | см. ниже |
| `test_a_no_answer_with_a_yes_pointer` | F7 | исправлено пометкой, поведение сервера сохранено намеренно | проба проходит: указатель под «false» остаётся, как в маршруте; экран помечает его «“да” после округления» (JS-тест) |
| `test_the_reserve_with_passes`, `test_rerun_record_keeps_fragments` | — | держится | позитивные пробы проходят |

Найдено при повторной проверке: исправление F5 засчитывало только движки, которые вернули результат. Движок, упавший
после оплаченного основного вызова, свой расход терял; мой тест это закреплял (ждал $0.000005 при оплаченных
$0.000007). Исправлено: каждый вызов прогона пишется в журнал прогона (`_LEDGER` в `app/runner.py`), и если запись
не сохранена — движок упал, диск, отмена — засчитывается весь журнал. Проба ревьюера теперь падает: засчитано
$0.001002, ровно два оплаченных вызова, вызовов после `run_error` — 0.

| Тест | Подсадки (все красные, восстановление по sha256) |
|---|---|
| `test_an_engine_that_breaks_waits_for_the_others_and_their_money_counts` (исправлено ожидание: $0.000007), `test_engines_that_all_break_after_billed_calls_still_count`, `test_a_cancelled_run_counts_what_was_billed` | «Recheck …» в [p6-plantings.log](p6-plantings.log): журнал не пишется; журнал не засчитывается; засчитываются только завершившиеся движки; отменённый прогон не засчитывается |
