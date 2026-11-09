# P0 — спайк: локальные d1, OpenRouter Decisions, чат-вызов

> **Статус.** Выполнен 2026-11-09 на Mac владельца. Закрывает пометки «проверить в P0» из [DESIGN.md](DESIGN.md)
> и риски RQ-1, RQ-2 (§24). Ollama `d1:2b` владелец перенёс в P7 (2026-11-09): скрипт готов, прогона нет (§5).
>
> **Как повторить.** `make setup && make models && make spike` (ключ OpenRouter в `.env`). Фикстуры пишутся в
> `tests/fixtures/`, ключей и id аккаунта в них нет. Стоимость прогона OpenRouter — $0.0013.

## Содержание

1. [Итог: пометки и риски](#1-итог-пометки-и-риски)
2. [Локальные d1 через mlx-vlm](#2-локальные-d1-через-mlx-vlm)
3. [OpenRouter Decisions](#3-openrouter-decisions)
4. [OpenRouter chat completions](#4-openrouter-chat-completions)
5. [Ollama](#5-ollama)
6. [Что меняется для P1–P7](#6-что-меняется-для-p1p7)
7. [Фикстуры и скрипты](#7-фикстуры-и-скрипты)

**Стенд.** Apple M4 Pro, 24 ГБ, macOS 26.6.2; Python 3.14.8; `mlx` 0.32.4, `mlx-vlm` 0.7.12. Модели —
`LiquidAI/d1-2B-MLX-8bit` и `LiquidAI/d1-Omni-2B-MLX-8bit` из Hugging Face. Все тексты и аудио — синтетика.

## 1. Итог: пометки и риски

| Пометка или риск | Ответ | Где |
|---|---|---|
| §7.3: функции загрузки и вызова d1-2B / d1-Omni | `from mlx_vlm import load`; `model, processor = load(path)`; `model.system_one(state, questions, media=None, score_readout="median", set_size=None)`. Один API у обеих моделей | §2.1 |
| §7.3: имена полей ответа | Имена System One плюс расширения (`choices`, `text`/`number`/`date` со `span`, `found`, `interval`) | §2.2 |
| §7.3: аудио | `media=[{"type": "audio", "path": "x.wav"}]` (16-bit PCM WAV) или `{"type": "audio", "audio": wave, "sampling_rate": 16000}`; голосовой вопрос — `instructions` как такой же элемент | §2.5 |
| §7.5: стоимость чат-вызова | `usage.cost` приходит всегда, флаг `usage.include` устарел и ничего не делает | §4 |
| RQ-1 | Закрыт | §2.1–§2.2 |
| RQ-2: память и задержка d1-Omni | 2.53 ГБ после загрузки, пик ≈ 3 ГБ с 30-секундным клипом; клип 10 с — 150 мс, 30 с — 273 мс | §2.3, §2.5 |
| RQ-4: форма alpha-API | Зафиксирована фикстурами 2026-11-09; схема шире, чем в аудите (`assets`, медиа в `state`) | §3.1 |
| RQ-5: какие LLM дают `logprobs` | 162 из 461 по каталогу; проверено на `openai/gpt-4o-mini` | §4 |
| Аудит §13.7: называет ли `~liquid/d1-latest` ответившую модель | Да: `liquid/d1.1-20261027` | §3.1 |

## 2. Локальные d1 через mlx-vlm

### 2.1 API

```python
from mlx_vlm import load

model, processor = load("LiquidAI/d1-2B-MLX-8bit")   # путь из HF-кэша тоже годится
out = model.system_one(state, questions, media=None,  # media: картинки, аудио (Omni), видео
                       score_readout="median",        # median | mean | argmax
                       set_size=None)                 # вопросов за проход; по умолчанию 16 (config d1_set_size)
# {"answers": {name: answer}, "usage": {"input_tokens": n, "output_tokens": 0}}
```

- `load()` сам подключает процессор и `calibration.json` из папки модели (`attach_processor`). Класс модели выбирается
  по `architectures: ["D1Model"]` при `model_type` `lfm3_vl` (d1-2B) и `lfm3_omni` (d1-Omni).
- Вызов синхронный и занимает GPU: в приложении — `asyncio.to_thread` под общим `asyncio.Lock` (DESIGN §8).
- `model.system_one_batch([(state, questions, media), ...])` — несколько запросов за вызов. В P0 не замерялся.
- `mlx_vlm.predict(model, processor, state, questions)` — общий decision-API mlx-vlm с другими именами (`bool`,
  `value`, `probability`, `metadata.confidence`) и `score` как взвешенным средним. Плейграунду не нужен.
- `python -m mlx_vlm decide --model … --state … --questions '<json>'` — то же из командной строки.
- Повторы детерминированы: 10 одинаковых запросов дали Δp = 0 на обеих моделях.
- `set_size=1` (каждый вопрос отдельным проходом) — 450 мс против 194 мс на 3 вопроса, `input_tokens` 754 против 392.

### 2.2 Ответ

| Тип запроса | Поля ответа | Пример (d1-2B, фикстуры `core_ru`, `extensions_ru`) |
|---|---|---|
| `noul` | `noul` | `{"type": "noul", "noul": 0.9946}` |
| `choice` | `choice`, `confidence`, `probabilities` | `confidence` = p выбранного варианта |
| `score` | `score`, `confidence`, `probabilities`, `legend` | `score: 2.0` при `median`; `1.825` при `mean` |
| `choice` + `multiple: true` | `choices`, `probabilities` (P(yes) по варианту) | `["deposit", "insurance"]` — верно |
| `text` | `text`, `span`, `found` | `"А-48213"`, `span [76, 83]` |
| `number` (без `range`) | `number`, `text`, `span`, `found`, `unit` | `number: null`, `text: "триста пятьдесят тысяч рублей"` |
| `number` + `range`, `step` | `number` (медиана), `interval` [p10, p90], `unit` | `240000`, `[10000, 670000]` при верных 350 000 |
| `date` | `date` (ISO), `text`, `span`, `found` | d1-2B: `null` при `text: "12 ноября 2026 года"`; d1-Omni: `2026-11-12` |
| `box`, `boxes` | ровно одна картинка в `media`, иначе ошибка | не замерялся |

- `span` — символьные смещения в строке `state`: `state[76:83] == "А-48213"`.
- **`score` по умолчанию — медианный уровень, целое число.** OpenRouter отдаёт взвешенный индекс (`1.89` у d1.1 на том
  же вопросе). Движок `mlx_local` считает `expected` сам из `probabilities` (DESIGN §6.2).
- **`confidence` — `p_max`**, у OpenRouter — формула TypeSafe (§3.5).
- **Разбор чисел и дат по-русски неполный.** Модель находит верный фрагмент, а парсер mlx-vlm не превращает его в
  значение:

  | Фрагмент | Результат парсера |
  |---|---|
  | `12 ноября 2026` | `2026-11-12` |
  | `12 ноября 2026 года`, `12 ноября`, `в четверг, 12 ноября` | `None` |
  | `12.11.2026`, `12/11/2026` | `None`, флаг неоднозначности |
  | `350 000`, `350 тыс.`, `1 234,56` | `350000`, `350000`, `1234.56` |
  | `350 000 рублей`, `350 000 руб.`, `триста пятьдесят тысяч рублей` | `None` |

  Режим извлечения (P6) показывает `text` и `span` всегда и добавляет свою нормализацию: срезать «года»/«г.» и
  единицы, `ДД.ММ.ГГГГ` читать день-первым, числа словами. Значение такой нормализации помечается как
  «нормализовано плейграундом».
- Оценка `number` с `range` на этом примере промахнулась и дала широкий интервал: 240 000 (d1-2B) и 110 000
  (d1-Omni) при верных 350 000.

### 2.3 Память и задержка

| | d1-2B-MLX-8bit | d1-Omni-2B-MLX-8bit |
|---|---|---|
| Снимок (§7.3) | `LiquidAI/d1-2B-MLX-8bit-20261105` | `LiquidAI/d1-Omni-2B-MLX-8bit-20261105` |
| Ревизия HF | `3247d048…` (2026-11-05T19:38:21Z) | `61f06cdc…` (2026-11-05T19:38:25Z) |
| На диске | 2.08 ГБ | 2.54 ГБ |
| Импорт `mlx_vlm` | 1.4 с | — |
| Загрузка | 1.7 с | 1.3 с |
| Память MLX после загрузки | 2.06 ГБ | 2.53 ГБ |
| Кэш MLX после запросов | +1.1 ГБ (освобождается `mx.clear_cache()`) | +1.1 ГБ |
| Первый запрос (3 вопроса, 392 токена) | 1294 мс | 337 мс |
| Тёплый, медиана из 10 | 194 мс | 194.5 мс |
| 5 вопросов-расширений вместе | 291 мс | 240 мс |
| Запрос-фикстура whip-insight (4 вопроса) | 211 мс | 212 мс |
| `choice` 240 / 300 вариантов | 1006 / 1231 мс | 973 / 1255 мс |
| `score` 10 уровней | 101 мс | 101 мс |
| После выгрузки (`del`, `gc`, `mx.clear_cache()`) | 0 ГБ | 0 ГБ |

- Две 8-bit модели вместе заняли бы ≈ 4.6 ГБ плюс кэш. DESIGN оставляет одну модель в памяти (§8): запас нужен под
  длинный state (§2.4).
- Ограничения 255 вариантов у локальных d1 нет: 300 вариантов приняты, верный ответ выбран. Compat-сервер вводит
  лимит whip-insight сам (§6).

### 2.4 Длинный state

Наполнитель — повторяемый диалог, в конце — «кодовое слово»; вопрос `noul` «Назвал ли клиент кодовое слово?».

| Токенов | Символов | d1-2B: мс / пик ГБ / P(да) | d1-Omni: мс / пик ГБ / P(да) |
|---|---|---|---|
| 1 781 | 4 524 | 871 / 2.81 / 0.95 | 874 / 3.27 / 0.96 |
| 6 933 | 18 140 | 3 864 / 2.93 / 0.97 | 3 901 / 3.39 / 0.96 |
| 13 877 | 36 492 | 9 401 / 3.52 / 0.98 | 9 753 / 3.99 / 0.96 |
| 27 765 | 73 196 | 26 771 / 5.65 / 0.99 | 27 600 / 6.11 / 0.96 |

- Около 2.6 символа русского диалога на токен.
- Время растёт быстрее длины: ×15.6 токенов дают ×31 по времени. Звонок на 10 минут (≈ 9 000 символов, ≈ 3 500
  токенов) займёт около 2 с (интерполяция между строками 1 и 2).
- Шаг 64K не запускался: память растёт с квадратом длины (плотная маска set-attention, комментарий в
  `d1_lfm3_vl/d1.py`), прогноз ≈ 20 ГБ на 24-гигабайтном Mac. Карточка обещает надёжность до 64K токенов, но на этом
  Mac практический предел около 32K. `context_tokens` в `config.example.yaml` снижен до 32 768 (§6).
- Факт в конце state найден на всех длинах.

### 2.5 Аудио (d1-Omni)

Клипы — синтетика `say -v Milena` → WAV 16 кГц моно 16-bit (`scripts/spike/make_audio.sh`), вопрос — пресет
`call_kind` (7 вариантов, `presets/call_kind.yaml`), `state: None`.

| Клип | Длина | Токенов | Холодный / тёплый, мс | Ответ, p | Задуман |
|---|---|---|---|---|---|
| `answering_machine_ru` | 10.6 с | 261 | 1 760 / 150 | `network_message` 0.64 | `answering_machine` |
| `ivr_ru` | 8.7 с | 237 | 161 | `voice_robot` 0.72 | `ivr` |
| `network_message_ru` | 4.1 с | 180 | 124 | `network_message` 0.69 | `network_message` |
| `conversation_ru` | 20.5 с | 384 | 386 | `voice_robot` 0.47 | `conversation` |
| `conversation_30s_ru` | 30 с | 503 | 398 / 273 | `voice_robot` 0.52 | `conversation` |

- Аудио работает: путь к WAV, волна numpy (`waveform_input`), голосовой вопрос (`audio_spoken_question`),
  аудио вместе с текстовым `state`.
- Аудио дешёвое по токенам: около 12.5 токена на секунду речи. Пик памяти с 30-секундным клипом — 3.03 ГБ.
- Первый аудио-вызов после загрузки — 1.8 с (прогрев аудио-тракта), дальше 150–275 мс.
- **Точность на синтетике не измерить.** Синтезированный голос модель резонно слышит как робота: 1 из 5 клипов
  получил задуманную метку. Это проверка тракта, не точности. RQ-3 (русская речь, длина клипа) остаётся за записями
  владельца.

### 2.6 Ошибки проверки → 400 compat-сервера

`system_one` поднимает `ValueError` с ключом вопроса в тексте (`validation_errors.json`):

| Запрос | Сообщение |
|---|---|
| `type: "predicate"` или `"bool"` | `question 'q': "type" is one of noul, choice, score, text, number, date, box, boxes, not 'predicate'` |
| `score` на 1 или 11 уровней | `question 'q': a score's "criteria" lists 2 to 10 levels, lowest first: …` |
| `choice` со списком в `criteria` | `question 'q': a choice's "criteria" names the options: …; a list of levels is a score` |
| `box` без картинки | `a box or boxes question reads exactly one picture in media; this request has 0` |
| пустой `questions` | **ошибки нет** |

Compat-сервер проверяет запрос сам, до вызова модели: тип, число вопросов (1–64), уровни и варианты. Разбирать
текст `ValueError` не нужно.

## 3. OpenRouter Decisions

Модели `typesafe/jev-1.14`, `liquid/d1.1`, `~liquid/d1-latest`, всегда `provider: {zdr: true, data_collection:
"deny"}`. 32 платных вызова, $0.0012.

### 3.1 Ответ

```json
{"id": "gen-dec-1794213930-…", "model": "liquid/d1.1-20261027", "provider": "Liquid",
 "answers": {"callback_agreed": {"type": "noul", "noul": 0.99976},
             "call_topic": {"type": "choice", "choice": "deposit", "confidence": 0.99886,
                            "probabilities": {"credit": 0.00048, "deposit": 0.99924, "complaint": 0.00028}},
             "interest": {"type": "score", "score": 1.8925, "confidence": 0.8792,
                          "probabilities": {"0": 0.0015, "1": 0.1111, "2": 0.8807, "3": 0.0067}, "legend": {…}}},
 "usage": {"input_tokens": 394, "output_tokens": 0, "cost": 7.88e-06, "is_byok": false,
           "cost_details": {"upstream_inference_cost": 7.88e-06, "upstream_inference_prompt_cost": 7.88e-06,
                            "upstream_inference_completions_cost": 0}}}
```

- `model` — датированный снимок: `typesafe/jev-1.14-20261014`, `liquid/d1.1-20261027`. `~liquid/d1-latest` отвечает
  `liquid/d1.1-20261027`. Заголовок `x-generation-id` повторяет `id`.
- `noul` приходит без `confidence`.
- Jev округляет вероятности до 0.01 (`{"credit": 0, "deposit": 1, "complaint": 0}`); суммы всех 32 ответов в 1 ± 0.001.
- Один и тот же запрос: Jev — 950 входных токенов и 72 выходных (не оплачиваются), d1.1 — 394 и 0. Цена запроса:
  $0.0000399 и $0.0000079.
- Запрос-фикстура whip-insight (указатель — `choice` с `criteria: {t1: null, …}`) принят обеими моделями, обе выбрали
  `t2`.

### 3.2 Отказы

| Запрос | Jev 1.14 | d1.1 и `~d1-latest` |
|---|---|---|
| `text`, `number`, `date`, неизвестный тип | 400 на весь запрос | 400 на весь запрос |
| `box` без картинки | **200**, `box` с `confidence: 0.01` | 400 `Liquid does not accept box questions; question 1 is a box` |
| `choice` + `multiple: true` | **200**, флаг проигнорирован, обычный `choice` | **200**, то же |
| неверный ключ | 401 `{"error": {"message": "User not found.", "code": 401}}` | — |

Тело 400 на неизвестный тип — JSON ошибки zod внутри строки `error.message`:

```json
{"error": {"code": 400, "message": "[{\"code\": \"invalid_union\", \"note\": \"No matching discriminator\",
  \"discriminator\": \"type\", \"options\": [\"noul\", \"choice\", \"score\", \"box\"],
  \"path\": [\"questions\", \"visit_date\", \"type\"], …}]"}, "user_id": "<user_id>"}
```

- Ключ вопроса — `path[1]`. По нему движок помечает вопрос `unsupported`, как предписывает DESIGN §6.3.
- `box` на d1.1 называет номер вопроса с 1, а не ключ.
- **`multiple` молча теряется** — это не отказ. `multi` отмечается в профиле облачных движков как неподдерживаемый
  и в OpenRouter не уходит (DESIGN §6.3).

### 3.3 Лимиты

- `choice` на 24, 100, 240, 255 вариантов и `score` на 10 уровней приняты обеими моделями. На 240 вариантах обе
  выбрали верный `o007`.
- Больше 255 не пробовалось: это работа пробы P3.

### 3.4 Задержка и повторы

| Модель | Задержка с этого Mac, мс | max \|Δp\| за 3 повтора |
|---|---|---|
| `typesafe/jev-1.14` | 250–466, медиана 264 | 0.03 |
| `liquid/d1.1` | 266–456, медиана 351 | 0.055 |
| `~liquid/d1-latest` | 265–304, медиана 295 | 0.070 |

Локальные d1 детерминированы (§2.1); облачные — нет.

### 3.5 Что значит `confidence`

| Источник | choice: совпало с `p_max` | choice: совпало с формулой TypeSafe | score: `p_max` | score: TypeSafe |
|---|---|---|---|---|
| Jev 1.14 (OpenRouter) | 8 из 10 | 10 из 10 | 3 из 5 | 5 из 5 |
| d1.1 (OpenRouter) | 6 из 10 | 10 из 10 | 0 из 5 | 5 из 5 |
| d1-2B (mlx-vlm) | 10 из 10 | 9 из 10 | 5 из 5 | 3 из 5 |
| d1-Omni (mlx-vlm) | 16 из 16 | 9 из 16 | 5 из 5 | 3 из 5 |

Совпадения «не по своей» формуле — уверенные ответы, где обе формулы дают почти 1. OpenRouter пересчитывает
`confidence` по формуле TypeSafe (choice `(p_max − 1/n)/(1 − 1/n)`, score через MAD, аудит §3.4) для любой модели;
mlx-vlm отдаёт `p_max`.

### 3.6 Каталог и ZDR (без ключа)

- `GET /api/v1/models?output_modalities=decisions` — 35 id, как 2026-11-08. Новые поля: `canonical_slug` — датированный
  снимок (`typesafe/jev-1.14-20261014`; у алиасов без даты) и `endpoints: ["/api/alpha/decisions"]`.
  `supported_parameters` у всех пуст. `liquid/d1` истекает 2026-11-20.
- `GET /api/v1/endpoints/zdr` — 1 111 эндпоинтов. `model_id` без даты, датированный снимок только в `name`:
  `"Liquid | liquid/d1.1-20261027"`. У всех decision-эндпоинтов `safety_retention: false`.
- Сопоставление «модель → ZDR» в плейграунде — по `model_id`; снимок — по `canonical_slug` каталога.

## 4. OpenRouter chat completions

Один вызов `openai/gpt-4o-mini`, `max_tokens: 1`, `logprobs: true`, `top_logprobs: 10`, тот же `provider` с ZDR:

- 200 за 1 065 мс; ответил провайдер Azure; ответ `B` (вклад) — верно.
- `usage`: `prompt_tokens` 231, `completion_tokens` 1, `cost` 0.00003525, `cost_details` (`upstream_inference_prompt_cost`,
  `upstream_inference_completions_cost`), `is_byok`. Флаг учёта не нужен: документация называет `usage: {include:
  true}` устаревшим и не влияющим ни на что.
- `logprobs.content[0].top_logprobs`: `B` 0.0, ` B` −20.75, `Б` −28, `A` −28.5, `C` −30.25. Варианты-буквы
  встречаются в нескольких формах (`B`, ` B`, кириллическая `Б`): нормализация §7.5 складывает формы одной буквы.
- В каталоге 162 из 461 LLM объявляют `logprobs` и `top_logprobs` (`tests/fixtures/openrouter/llm_logprobs.json`). Это
  свойство модели: ZDR-эндпоинт может их не дать. Движок проверяет `logprobs` в ответе и иначе переходит в `verbal`.

## 5. Ollama

Не прогонялся: Ollama на Mac не установлен, владелец перенёс проверку в P7 (2026-11-09). Готово:
`scripts/spike/systemone_spike.py --name ollama-d1-2b --url http://127.0.0.1:11434/v1/systemone --model d1:2b`.

Факты со страниц `ollama.com/library/d1` и `/tags` (2026-11-09):

- Теги: `d1:latest` = `d1:3b` = `d1:3b-q8_0` (3.7 ГБ, 32K); `d1:2b` = `d1:2b-mlx-bf16` (один digest, 3.9 ГБ,
  125K); `d1:2b-mxfp8` (2.4 ГБ), `d1:2b-nvfp4` (1.7 ГБ). Теги `2b` помечены MLX; какую модель они несут (d1-2B по
  контексту 125K), страница не говорит.
- `POST /v1/systemone`: `model`, `state`, `images` (base64), 1–64 вопроса, `keep_alive`; типы `noul`, `choice`,
  `score` (2–10 уровней); ответ в именах System One. Минимальная версия Ollama не указана.

## 6. Что меняется для P1–P7

| Где | Изменение |
|---|---|
| `openrouter_decisions` (P1) | Неподдерживаемый тип и `multi` не отправлять; 400 разбирать по `path[1]` zod-ошибки; `cost` из `usage.cost`; снимок из `model` ответа |
| Нормализация (P1) | `expected` у `score` — из `probabilities`; `vendor_confidence` только хранить |
| `mlx_local` (P2) | `load()` + `system_one`; `asyncio.to_thread` под `Lock`; `score_readout` не важен, `expected` свой; снимок `<repo>-<дата ревизии HF>` через `HfApi().model_info(repo, revision=sha).last_modified`, офлайн — без даты |
| Менеджер моделей (P2) | Выгрузка: `del model` + `gc.collect()` + `mx.clear_cache()` возвращает память в 0; первый аудио-вызов прогревать |
| `config.example.yaml` | `context_tokens` у MLX-моделей 65 536 → 32 768: на 24 ГБ 64K не помещается |
| Compat (P2) | Проверять запрос до модели: 1–64 вопроса, типы, 2–10 уровней, ≤ 255 вариантов (локально лимита нет, whip-insight его ждёт) |
| Проба (P3) | `multi` у облачных — «нет» по построению, без запроса; `box` у Jev без картинки отвечает, значит проба `box` шлёт картинку |
| Извлечение (P6) | Своя RU-нормализация дат и чисел поверх `text`/`span` |
| Аудио (P6) | Синтетика годится для проверки тракта, не точности; RQ-3 — на записях владельца |
| `openrouter_llm` (P7) | Складывать формы буквы (`B`, ` B`, `Б`) в `top_logprobs`; `verbal`, если `logprobs` в ответе нет |
| Ollama (P7) | Прогнать `systemone_spike.py` на `d1:2b` |

## 7. Фикстуры и скрипты

| Путь | Что |
|---|---|
| `scripts/spike/mlx_spike.py` | Спайк одной MLX-модели в своём процессе |
| `scripts/spike/openrouter_spike.py` | Каталог, ZDR, Decisions, один чат-вызов; `--public-only` без ключа |
| `scripts/spike/systemone_spike.py` | Любой сервер `/v1/systemone` (Ollama, llama.cpp, compat) |
| `scripts/spike/make_audio.sh` | Синтетические русские клипы |
| `tests/fixtures/requests/` | `core_ru` (noul/choice/score), `extensions_ru` (multi/text/number/date), `whip_insight_systemone` (тело адаптера whip-insight §5.2) |
| `tests/fixtures/mlx/<engine>/` | Ответы `system_one`, `validation_errors.json`, `long_state.json`, `_measurements.json` |
| `tests/fixtures/openrouter/<model>/` | Запрос, статус, тело ответа на каждый вызов |
| `tests/fixtures/openrouter/catalog_decisions.json`, `zdr_decisions.json`, `llm_logprobs.json` | Публичные списки на 2026-11-09 |
| `tests/fixtures/openrouter/chat/` | Чат-вызов с `logprobs` |
| `tests/fixtures/audio/` | Пять WAV, 4–30 с, 2.3 МБ |
| `presets/call_kind.yaml` | 7 вариантов whip-insight §8.4 |

Фикстуры не содержат ключа и id аккаунта: `openrouter_spike.py` заменяет `user_id` в телах ошибок на `<user_id>`.
