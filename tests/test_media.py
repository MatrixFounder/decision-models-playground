"""Media uploads (DESIGN §14 UC-6, §15 POST /api/media): a recording becomes WAV 16 kHz mono 16-bit, cut to
`audio.max_seconds`; a picture is checked; the format comes from the bytes, and ffmpeg never follows a playlist."""

from __future__ import annotations

import io
import shutil
import subprocess
import wave
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.main import create_app
from app.media import MediaError, MediaStore, NoFfmpeg, audio_format
from tests.conftest import FIX, UI

AUDIO = FIX / "audio"
FFMPEG = shutil.which("ffmpeg")
needs_ffmpeg = pytest.mark.skipif(FFMPEG is None, reason="ffmpeg is not installed")


@pytest.fixture
def client(settings, upstream, local):
    app = create_app(settings, transport=upstream.transport, refresh_catalog=False, local=local)
    with TestClient(app, base_url=UI) as c:
        yield c


def store(tmp_path: Path, max_seconds: float = 30, ffmpeg: str = "ffmpeg") -> MediaStore:
    return MediaStore(tmp_path / "media", ffmpeg, max_seconds, 16000, 16_000_000)


def png(w: int = 4, h: int = 3, mode: str = "RGB", fmt: str = "PNG") -> bytes:
    buf = io.BytesIO()
    Image.new(mode, (w, h)).save(buf, format=fmt)
    return buf.getvalue()


def wav_info(path: Path) -> tuple[int, int, int, int]:
    with wave.open(str(path), "rb") as w:
        return w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()


def test_formats_come_from_the_bytes():
    assert audio_format((AUDIO / "ivr_ru.wav").read_bytes()[:16]) == "wav"
    assert audio_format(b"ID3\x04" + bytes(12)) == "mp3"
    assert audio_format(b"\xff\xfb\x90\x00" + bytes(12)) == "mp3"
    assert audio_format(b"OggS" + bytes(12)) == "ogg"
    assert audio_format(b"fLaC" + bytes(12)) == "flac"
    assert audio_format(b"\x00\x00\x00\x1cftypM4A \x00\x00") == "mov"
    assert audio_format(b"\x1a\x45\xdf\xa3" + bytes(12)) == "matroska"
    assert audio_format(b"#!AMR\n" + bytes(10)) == "amr"
    for other in (b"#EXTM3U\n", b"ffconcat version 1.0\n", b"RIFF\x00\x00\x00\x00AVI ", b"\x89PNG\r\n\x1a\n", b""):
        assert audio_format(other) is None


@needs_ffmpeg
def test_a_recording_becomes_what_d1_omni_hears(client, settings):
    r = client.post("/api/media", content=(AUDIO / "ivr_ru.wav").read_bytes())
    assert r.status_code == 200, r.text
    m = r.json()
    assert m["type"] == "audio" and m["path"].endswith(".wav") and len(m["sha256"]) == 64
    assert m["seconds"] == pytest.approx(8.7, abs=0.1) and m["source_seconds"] == pytest.approx(8.7, abs=0.1)
    kept = settings.data_dir / "media" / m["path"]
    assert wav_info(kept)[:3] == (1, 2, 16000)
    again = client.post("/api/media", content=(AUDIO / "ivr_ru.wav").read_bytes()).json()
    assert again["path"] == m["path"]  # one recording, one file (bit-exact, no metadata)
    assert client.get(f"/api/media/{m['path']}").content == kept.read_bytes()
    assert not [p for p in (settings.data_dir / "media").iterdir() if p.name.startswith(".upload-")]


@needs_ffmpeg
def test_a_long_recording_is_cut_to_max_seconds(tmp_path):
    saved = store(tmp_path, max_seconds=5).save((AUDIO / "conversation_30s_ru.wav").read_bytes())
    assert saved.seconds == pytest.approx(5.0, abs=0.01)
    assert saved.source_seconds == pytest.approx(30, abs=0.2)  # the screen says what was cut
    assert wav_info(tmp_path / "media" / saved.path)[3] == 5 * 16000


@needs_ffmpeg
@pytest.mark.parametrize("codec,ext", [("aac", "m4a"), ("libopus", "webm"), ("libmp3lame", "mp3"), ("flac", "flac")])
def test_phone_and_browser_formats(tmp_path, codec, ext):
    src = tmp_path / f"clip.{ext}"
    made = subprocess.run([FFMPEG, "-nostdin", "-loglevel", "error", "-i", str(AUDIO / "ivr_ru.wav"), "-c:a", codec,
                           str(src)], capture_output=True)
    if made.returncode != 0:
        pytest.skip(f"this ffmpeg has no {codec} encoder")
    saved = store(tmp_path).save(src.read_bytes())
    assert saved.type == "audio" and saved.seconds == pytest.approx(8.7, abs=0.15)
    assert wav_info(tmp_path / "media" / saved.path)[:3] == (1, 2, 16000)


@needs_ffmpeg
@pytest.mark.parametrize("data", [
    b"#EXTM3U\n#EXTINF:1,\nfile:///etc/passwd\n",  # a playlist is not a recording: never handed to ffmpeg
    b"ffconcat version 1.0\nfile '/etc/passwd'\n",
    b"RIFF\x24\x00\x00\x00WAVEjunkjunkjunkjunk",  # claims WAV, is not
    b"ID3\x04\x00\x00\x00\x00\x00\x00" + b"\x00" * 64,  # an MP3 header with no audio
    png(fmt="GIF", mode="P"),  # a picture d1 does not read
    b"plain text",
])
def test_what_is_not_a_picture_or_a_recording_is_400_and_leaves_nothing(client, settings, data):
    r = client.post("/api/media", content=data)
    assert r.status_code == 400
    assert str(settings.data_dir) not in r.text and ".upload-" not in r.text  # no server path in the answer
    assert not list((settings.data_dir / "media").iterdir())


def test_a_picture_is_checked_and_kept(client, settings):
    r = client.post("/api/media", content=png(640, 480))
    assert r.status_code == 200
    m = r.json()
    assert m["type"] == "image" and m["path"].endswith(".png") and (m["width"], m["height"]) == (640, 480)
    jpg = client.post("/api/media", content=png(8, 8, fmt="JPEG")).json()
    assert jpg["path"].endswith(".jpg")


def test_a_picture_over_the_pixel_limit_is_refused(tmp_path):
    s = MediaStore(tmp_path / "media", "ffmpeg", 30, 16000, 1_000_000)
    with pytest.raises(MediaError, match="over 1000000 pixels"):
        s.save(png(1001, 1000, mode="1"))
    assert s.save(png(1000, 1000, mode="1")).type == "image"  # the limit itself passes


def test_empty_and_oversized_uploads(client):
    assert client.post("/api/media", content=b"").status_code == 400
    big = b"\x00" * (16 * 1024 * 1024 + 1)
    assert client.post("/api/media", content=big).status_code == 413

    def chunks():  # no Content-Length: counted while it streams
        for _ in range(17):
            yield b"\x00" * (1024 * 1024)

    assert client.post("/api/media", content=chunks()).status_code == 413


def test_no_ffmpeg_is_503_with_what_to_do(tmp_path):
    with pytest.raises(NoFfmpeg, match="FFMPEG_BIN"):
        store(tmp_path, ffmpeg="/nonexistent/ffmpeg").save((AUDIO / "ivr_ru.wav").read_bytes())


def test_no_ffmpeg_over_http(settings, upstream, local):
    settings.ffmpeg_bin = "/nonexistent/ffmpeg"
    app = create_app(settings, transport=upstream.transport, refresh_catalog=False, local=local)
    with TestClient(app, base_url=UI) as c:
        r = c.post("/api/media", content=(AUDIO / "ivr_ru.wav").read_bytes())
    assert r.status_code == 503 and "FFMPEG_BIN" in r.json()["detail"]


@pytest.mark.parametrize("name", ["../config.yaml", "probe", "x.wav", "0123456789abcdef.exe", "0123456789ABCDEF.wav",
                                  "0123456789abcdef.wav"])
def test_only_names_the_store_gave_out_are_served(client, name):
    assert client.get(f"/api/media/{name}").status_code == 404


def test_a_foreign_page_cannot_upload(client):
    r = client.post("/api/media", content=png(), headers={"Origin": "https://evil.example"})
    assert r.status_code == 403


# --- review P6 -------------------------------------------------------------------------------------------------------
def wav_header(frames: int = 0, channels: int = 1, rate: int = 16000) -> bytes:
    import struct

    data = b"\x00\x00" * channels * frames
    return (b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVE" + b"fmt "
            + struct.pack("<IHHIIHH", 16, 1, channels, rate, rate * 2 * channels, 2 * channels, 16)
            + b"data" + struct.pack("<I", len(data)) + data)


@needs_ffmpeg
def test_ffmpeg_reads_the_named_format_and_follows_no_script(tmp_path):
    # a concat script handed to ffmpeg as "wav": without `-f`, ffmpeg 8 detects ffconcat and reads the file it names
    secret = tmp_path / "secret.wav"
    secret.write_bytes((AUDIO / "network_message_ru.wav").read_bytes())
    script = f"ffconcat version 1.0\nfile '{secret}'\n".encode()
    s = store(tmp_path)
    with pytest.raises(MediaError, match="as wav audio"):
        s.save_audio(script, "wav")
    assert not list((tmp_path / "media").glob("*.wav"))  # nothing of the secret was kept


def test_the_ffmpeg_argv(tmp_path, monkeypatch):
    import app.media as media_mod

    seen = []

    def fake_run(cmd, **kw):
        seen.append(cmd)
        raise subprocess.TimeoutExpired(cmd, 1)

    monkeypatch.setattr(media_mod.subprocess, "run", fake_run)
    monkeypatch.setattr(media_mod.shutil, "which", lambda name: "/opt/ffmpeg")
    with pytest.raises(MediaError, match="took over"):
        store(tmp_path, max_seconds=12).save_audio(b"RIFF....WAVE", "mov")
    cmd = seen[0]
    assert cmd[0] == "/opt/ffmpeg" and "-nostdin" in cmd
    i = cmd.index("-i")
    assert cmd[i - 4:i] == ["-protocol_whitelist", "file", "-f", "mov"]  # the format named, local files only
    assert cmd[cmd.index("-t") + 1] == "12" and cmd[cmd.index("-ar") + 1] == "16000" and cmd[cmd.index("-ac") + 1] == "1"
    assert cmd[cmd.index("-c:a") + 1] == "pcm_s16le" and cmd[-1].endswith(".wav")


def test_the_upload_limit_is_16_mib_and_reaches_exactly_it(client):
    from app.media import MAX_UPLOAD

    assert MAX_UPLOAD == 16 * 1024 * 1024

    def stream(n):  # no Content-Length: the counter in the endpoint decides
        chunk = 1024 * 1024
        while n > 0:
            yield b"\x00" * min(chunk, n)
            n -= chunk

    assert client.post("/api/media", content=stream(MAX_UPLOAD)).status_code == 400  # in: read, then not a format
    assert client.post("/api/media", content=stream(MAX_UPLOAD + 1)).status_code == 413


def test_files_the_store_did_not_name_are_not_served(client, settings):
    root = settings.data_dir / "media"
    for name in ("notes.txt", ".upload-0123.in", "0123456789abcdef.txt"):
        (root / name).write_text("x")
        assert client.get(f"/api/media/{name}").status_code == 404
    saved = client.post("/api/media", content=png()).json()
    assert client.get(f"/api/media/{saved['path']}").status_code == 200  # the store's own name is served


@needs_ffmpeg
def test_a_recording_without_sound_is_refused(tmp_path):
    with pytest.raises(MediaError, match="no sound"):
        store(tmp_path).save(wav_header(frames=0))


def test_what_ffmpeg_writes_is_checked(tmp_path):
    # a stand-in ffmpeg that writes a stereo WAV: d1-Omni must never get it
    stereo = tmp_path / "stereo.wav"
    stereo.write_bytes(wav_header(frames=1600, channels=2))
    fake = tmp_path / "ffmpeg"
    fake.write_text(f'#!/bin/sh\nfor a; do last="$a"; done\ncp "{stereo}" "$last"\n')
    fake.chmod(0o755)
    with pytest.raises(MediaError, match="16-bit mono"):
        store(tmp_path, ffmpeg=str(fake)).save((AUDIO / "ivr_ru.wav").read_bytes())
    assert not list((tmp_path / "media").iterdir())


@needs_ffmpeg
def test_the_cut_is_reported_even_when_the_recording_states_no_length(tmp_path):
    full = store(tmp_path).save((AUDIO / "ivr_ru.wav").read_bytes())
    assert full.cut is False
    cut = store(tmp_path, max_seconds=5).save((AUDIO / "conversation_30s_ru.wav").read_bytes())
    assert cut.cut is True and cut.source_seconds == pytest.approx(30, abs=0.2)
    live = tmp_path / "live.webm"  # what a browser's MediaRecorder writes: `Duration: N/A`
    made = subprocess.run([FFMPEG, "-nostdin", "-loglevel", "error", "-i", str(AUDIO / "conversation_30s_ru.wav"),
                           "-c:a", "libopus", "-live", "1", "-f", "webm", str(live)], capture_output=True)
    if made.returncode != 0:
        pytest.skip("this ffmpeg has no libopus encoder")
    got = store(tmp_path, max_seconds=5).save(live.read_bytes())
    assert got.seconds == pytest.approx(5.0, abs=0.01) and got.source_seconds is None and got.cut is True


def test_iphone_pictures_and_wideband_amr_are_told_apart(tmp_path):
    for brand in (b"heic", b"heix", b"mif1", b"avif"):
        head = b"\x00\x00\x00\x18ftyp" + brand + b"\x00\x00\x00\x00"
        assert audio_format(head) is None
        with pytest.raises(MediaError, match="HEIC or AVIF"):
            store(tmp_path).save(head + b"\x00" * 64)
    assert audio_format(b"\x00\x00\x00\x1cftypM4A \x00\x00\x00\x00") == "mov"
    assert audio_format(b"#!AMR-WB\n" + bytes(7)) == "amr"


def test_temp_files_of_a_crashed_upload_are_swept_at_start(settings, upstream, local):
    root = settings.data_dir / "media"
    (root / ".upload-dead.in").write_bytes(b"x")
    (root / ".upload-dead.wav").write_bytes(b"x")
    app = create_app(settings, transport=upstream.transport, refresh_catalog=False, local=local)
    with TestClient(app, base_url=UI):
        assert not list(root.glob(".upload-*"))


def test_at_most_two_conversions_at_once(client, monkeypatch):
    import threading
    import time

    media = client.app.state.media
    lock, active, peak = threading.Lock(), [0], [0]

    def slow(data):
        with lock:
            active[0] += 1
            peak[0] = max(peak[0], active[0])
        time.sleep(0.3)
        with lock:
            active[0] -= 1
        raise MediaError("not a format")

    monkeypatch.setattr(media, "save", slow)
    codes = []
    threads = [threading.Thread(target=lambda: codes.append(client.post("/api/media", content=b"x").status_code))
               for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert codes == [400] * 5 and peak[0] == 2
