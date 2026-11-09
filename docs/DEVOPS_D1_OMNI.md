# d1-Omni: llama-server за LiteLLM

> 2026-11-09. Собрано по карточке модели, README llama-server и исходникам llama.cpp (master) и LiteLLM (1.108.3,
> main). На стенде связка ещё не прогонялась: чек-лист в §5 и есть первый прогон.

d1-Omni-2B поднимаем на Ubuntu через llama-server b11960+ и подключаем в LiteLLM 1.104.3+ как провайдер
`hosted_vllm`. Аудио, картинки и видео клиенты передают только внутри `state`: поле `audio` LiteLLM пересылает, но
llama-server его не читает, и запись теряется без ошибки.

Схема: `клиент → LiteLLM /v1/systemone → llama-server :9931 /v1/systemone`.

## 1. Требования

| Что | Значение |
|---|---|
| llama.cpp | b11960 (v0.8.0) или новее. Готовые сборки Ubuntu в [GitHub Releases](https://github.com/ggml-org/llama.cpp/releases): x64 CPU, CUDA 12.8 / 13.4, Vulkan, ROCm |
| LiteLLM | 1.104.3 или новее: маршрут `/v1/systemone` и провайдер `hosted_vllm` |
| Модель | `LiquidAI/d1-Omni-2B-GGUF`: `d1-Omni-2B-Q8_0.gguf` (1.85 ГБ) и `mmproj-d1-Omni-2B-Q8_0.gguf` (0.33 ГБ). Без mmproj запрос с медиа получает 501 |
| ffmpeg | В `PATH` процесса llama-server, если будет видео |
| Порт | 9931 — порт llama-server по умолчанию (в примерах карточки модели 8080). Доступ только с хоста LiteLLM |

Весь промпт вопроса (state и медиа) считается одним батчем на 8–16 тыс. токенов. На CPU это медленно; замеров на
CPU и GPU пока нет.

## 2. llama-server

Файлы модели, закреплённые на ревизии:

```bash
REV=36707287af55fc9ab03e34bda6a6cc82075499b1
sudo mkdir -p /opt/models/d1-omni && cd /opt/models/d1-omni
for f in d1-Omni-2B-Q8_0.gguf mmproj-d1-Omni-2B-Q8_0.gguf; do
  sudo curl -fL -o "$f" "https://huggingface.co/LiquidAI/d1-Omni-2B-GGUF/resolve/$REV/$f"
done
```

Ключ — в `/etc/llama-server.env` (права 600): `LLAMA_API_KEY=<секрет>`.

```ini
# /etc/systemd/system/llama-d1-omni.service
[Unit]
Description=llama-server d1-Omni-2B
After=network-online.target
Wants=network-online.target

[Service]
EnvironmentFile=/etc/llama-server.env
ExecStart=/opt/llama.cpp/llama-server \
  -m /opt/models/d1-omni/d1-Omni-2B-Q8_0.gguf \
  --mmproj /opt/models/d1-omni/mmproj-d1-Omni-2B-Q8_0.gguf \
  -b 8192 -ub 8192 \
  --host 10.0.0.5 --port 9931
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

- `-b` и `-ub` должны вмещать весь промпт вопроса. Промпт d1-Omni обрезается до 16 384 токенов. Для длинных
  расшифровок ставьте `-b 16384 -ub 16384`, памяти уйдёт больше. Картинка занимает до половины `-ub`.
- `--host` — внутренний адрес, не `0.0.0.0`.
- Тот же ключ прописывается в LiteLLM как `api_key` (§3).

Проверка на хосте:

```bash
curl -s -H "Authorization: Bearer $LLAMA_API_KEY" http://10.0.0.5:9931/v1/models \
  | jq '.data[0].architecture.output_modalities'
# ["decisions"]
```

## 3. LiteLLM

```yaml
model_list:
  - model_name: d1-omni
    litellm_params:
      model: hosted_vllm/d1-omni
      api_base: http://10.0.0.5:9931        # /v1/systemone LiteLLM добавит сам
      api_key: os.environ/LLAMACPP_API_KEY
    model_info:
      mode: decisions                       # health check через decisions, модель видна во вкладке Decisions
```

- Для этой модели не включайте `supports_audio_input`, `supports_video_input` и `drop_params`. Клиент, приславший
  поле `audio`, должен получить 400, а не 200 без учёта записи (§4).
- `/chat/completions` с этой моделью не работает: она не генерирует текст.

## 4. Медиа: только внутри `state`

| Клиент шлёт | Результат |
|---|---|
| `audio`, у модели `supports_audio_input: true` | LiteLLM пересылает поле, llama-server его не читает. Ответ без записи, ошибки нет |
| `audio`, флага нет | 400 от LiteLLM. С `drop_params` поле молча выбрасывается |
| `videos` | 400 от llama-server: `"videos" is not supported` |
| `images` | 400 в LiteLLM 1.108.3 и раньше; в main принимается ([PR #48621](https://github.com/BerriAI/litellm/pull/48621)), в стабильный релиз ещё не вошло |
| `files` (формат llama-server) | LiteLLM собирает тело только из своих полей, `files` не доходит |
| Части сообщений в `state` | Работает: LiteLLM пересылает `state` как есть, llama-server вынимает `image_url`, `input_audio`, `video_url` |

Запрос с аудио:

```bash
curl https://litellm.example/v1/systemone \
  -H "Authorization: Bearer $LITELLM_KEY" -H 'Content-Type: application/json' -d @- <<JSON
{"model": "d1-omni",
 "state": [{"role": "user", "content": [
   {"type": "text", "text": "Запись входящего звонка."},
   {"type": "input_audio", "input_audio": {"data": "$(base64 -w0 call.wav)", "format": "wav"}}]}],
 "questions": {"kind": {"type": "choice", "instructions": "Что на записи?",
   "criteria": {"human": "Живой человек", "machine": "Автоответчик", "ivr": "Голосовое меню"}}}}
JSON
```

- Картинка: `{"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,..."}}`. Видео:
  `{"type": "video_url", "video_url": {"url": "data:video/mp4;base64,..."}}`. Принимаются только data URL.
- Типы вопросов: `noul`, `choice` (до 255 вариантов), `score` (2–10 уровней). `multiple`, `text`, `number`, `date`,
  `box` llama.cpp не поддерживает.
- base64 в `state` LiteLLM считает текстом запроса: при логировании промптов (spend logs, Langfuse) клипы попадут в
  логи целиком.
- 30 с WAV 16 кГц моно — около 1.3 МБ в base64. Проверьте лимит размера тела на LiteLLM и балансировщике.

## 5. Проверка после выкладки

- [ ] Сервис `llama-d1-omni` активен, `/v1/models` на llama-server отдаёт `["decisions"]`.
- [ ] Текстовый запрос через LiteLLM (`state` строкой, один вопрос `noul`) возвращает 200 и `answers`.
- [ ] Запрос с аудио в `state`: `usage.input_tokens` на сотни токенов больше, чем у того же запроса без записи.
  Иначе запись не дошла.
- [ ] Запрос с полем `audio` получает 400, а не 200.
- [ ] Порт 9931 недоступен ниоткуда, кроме хоста LiteLLM.

## Источники

- [LiquidAI/d1-Omni-2B-GGUF](https://huggingface.co/LiquidAI/d1-Omni-2B-GGUF) — карточка модели, ревизия `3670728`
- [llama-server README, POST /v1/systemone](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md)
- [llama.cpp server-decision.cpp](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/server-decision.cpp) — какие поля тела читает llama-server
- [LiteLLM: Decisions](https://docs.litellm.ai/docs/decisions)
- [LiteLLM: провайдер hosted_vllm](https://github.com/BerriAI/litellm/blob/main/litellm/llms/hosted_vllm/decisions/transformation.py) и [сборка тела System One](https://github.com/BerriAI/litellm/blob/main/litellm/llms/base_llm/decisions/transformation.py)
