#!/usr/bin/env bash
# P0 spike: synthetic Russian call clips for d1-Omni (macOS `say`, voice Milena) as 16 kHz mono 16-bit PCM WAV,
# the format the d1-Omni card and mlx_vlm's read_audio accept. No real call data.
set -euo pipefail

OUT="${1:-tests/fixtures/audio}"
FFMPEG="${FFMPEG_BIN:-ffmpeg}"
VOICE="${VOICE:-Milena}"
mkdir -p "$OUT"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

to_wav() { "$FFMPEG" -loglevel error -y -i "$1" -ar 16000 -ac 1 -sample_fmt s16 "$2"; }

speak() {  # name, text
  say -v "$VOICE" -o "$TMP/$1.aiff" "$2"
  to_wav "$TMP/$1.aiff" "$TMP/$1.wav"
}

speak am "Здравствуйте. Вы позвонили в компанию Ромашка. Сейчас все операторы заняты. Оставьте, пожалуйста, ваше сообщение после звукового сигнала."
"$FFMPEG" -loglevel error -y -f lavfi -i "sine=frequency=1000:duration=0.7" -ar 16000 -ac 1 -sample_fmt s16 "$TMP/beep.wav"
"$FFMPEG" -loglevel error -y -i "$TMP/am.wav" -i "$TMP/beep.wav" \
  -filter_complex "[0:a][1:a]concat=n=2:v=0:a=1" -ar 16000 -ac 1 -sample_fmt s16 "$OUT/answering_machine_ru.wav"

speak ivr "Для связи с отделом продаж нажмите один. Для технической поддержки нажмите два. Чтобы прослушать меню ещё раз, нажмите звёздочку."
cp "$TMP/ivr.wav" "$OUT/ivr_ru.wav"

speak net "Абонент временно недоступен. Пожалуйста, перезвоните позднее."
cp "$TMP/net.wav" "$OUT/network_message_ru.wav"

speak conv "Алло. Да, добрый день, слушаю вас. Я по поводу заказа, который мне привезли вчера. Коробка была помята, а внутри треснул экран. Можно оформить возврат? Да, конечно, назовите номер заказа. Семь, два, ноль, один, пять. Спасибо, вижу заказ, оформляю возврат."
cp "$TMP/conv.wav" "$OUT/conversation_ru.wav"

# A ~30 s clip (the audio.max_seconds default) for latency: the conversation three times.
"$FFMPEG" -loglevel error -y -i "$TMP/conv.wav" -i "$TMP/conv.wav" -i "$TMP/conv.wav" \
  -filter_complex "[0:a][1:a][2:a]concat=n=3:v=0:a=1" -ar 16000 -ac 1 -sample_fmt s16 "$TMP/long.wav"
"$FFMPEG" -loglevel error -y -i "$TMP/long.wav" -t 30 -ar 16000 -ac 1 -sample_fmt s16 "$OUT/conversation_30s_ru.wav"

for f in "$OUT"/*.wav; do
  printf '%s\t%ss\n' "$f" "$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$f")"
done
