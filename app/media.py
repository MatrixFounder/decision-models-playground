"""Media uploads (DESIGN §14 UC-6, §15 `POST /api/media`): a picture is checked and kept; a recording becomes what
d1-Omni hears (WAV, 16 kHz, mono, 16-bit), cut to `audio.max_seconds`.

- The file lands in DATA_DIR/media under its content hash (`<sha256[:16]>.<ext>`): the same upload is one file, and
  a request names it by that path (`MediaItem.path`, read by `mlx_local` from DATA_DIR/media only).
- The format is read from the file's first bytes, never from its name. ffmpeg is told that format (`-f`), reads
  local files only (`-protocol_whitelist file`) and never stdin (`-nostdin`), and runs without a shell: a playlist or a
  concat script posing as audio is refused instead of followed.
- The WAV is written bit-exact without metadata, so one recording gives one hash; the result is checked with `wave`.
"""

from __future__ import annotations

import hashlib
import io
import logging
import os
import re
import shutil
import subprocess
import uuid
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image

log = logging.getLogger("playground.media")

MAX_UPLOAD = 16 * 1024 * 1024  # the UI's body limit (§15)
FFMPEG_TIMEOUT_S = 60
IMAGE_FORMATS = {"PNG": "png", "JPEG": "jpg", "WEBP": "webp"}  # what d1 reads (compat checks the same three)
NAME_RE = re.compile(r"^[0-9a-f]{16}\.(wav|png|jpg|webp)$")
DURATION_RE = re.compile(r"Duration: (\d+):(\d{2}):(\d{2}(?:\.\d+)?)")


class MediaError(ValueError):
    """The upload is not a picture or a recording this playground reads (400)."""


class NoFfmpeg(RuntimeError):
    """ffmpeg is not installed or FFMPEG_BIN points nowhere (503)."""


# ISO-BMFF brands of pictures: an iPhone HEIC or an AVIF starts with `ftyp` like an M4A, and is not audio
PICTURE_BRANDS = {b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis", b"mif1", b"msf1", b"avif", b"avis"}


def audio_format(head: bytes) -> str | None:
    """ffmpeg's demuxer for a recording, from its first bytes; None when it is not one of these."""
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return "wav"
    if head[:3] == b"ID3" or len(head) > 1 and head[0] == 0xFF and head[1] & 0xE0 == 0xE0:
        return "mp3"
    if head[:4] == b"OggS":
        return "ogg"
    if head[:4] == b"fLaC":
        return "flac"
    if head[4:8] == b"ftyp":  # MP4 / M4A, iPhone voice memos; not a HEIC or AVIF picture
        return None if head[8:12] in PICTURE_BRANDS else "mov"
    if head[:4] == b"\x1a\x45\xdf\xa3":  # WebM / Matroska, a browser's recording
        return "matroska"
    if head[:6] == b"#!AMR\n" or head[:9] == b"#!AMR-WB\n":  # ffmpeg's amr demuxer reads both
        return "amr"
    return None


@dataclass
class Saved:
    type: str
    path: str  # relative to DATA_DIR/media
    sha256: str
    bytes: int
    seconds: float | None = None
    source_seconds: float | None = None  # the recording's own length, when ffmpeg reported it
    cut: bool | None = None  # audio: the kept clip is shorter than the recording, or it may be (no length reported)
    width: int | None = None
    height: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if v is not None}


class MediaStore:
    def __init__(self, root: Path, ffmpeg_bin: str, max_seconds: float, sample_rate: int, max_image_pixels: int):
        self.root = root
        self.ffmpeg_bin, self.max_seconds, self.sample_rate = ffmpeg_bin, max_seconds, sample_rate
        self.max_image_pixels = max_image_pixels

    def sweep(self) -> int:
        """Remove the temp files of uploads cut short by a crash (`.upload-*`); at start-up, before any upload."""
        n = 0
        if self.root.is_dir():
            for p in self.root.glob(".upload-*"):
                p.unlink(missing_ok=True)
                n += 1
        return n

    def path_of(self, name: str) -> Path | None:
        """An uploaded file by its name, or None: only names this store gives out."""
        if not NAME_RE.fullmatch(name):
            return None
        p = self.root / name
        return p if p.is_file() else None

    def save(self, data: bytes | bytearray) -> Saved:
        if not data:
            raise MediaError("the file is empty")
        if len(data) > MAX_UPLOAD:
            raise MediaError(f"the file is over {MAX_UPLOAD // (1024 * 1024)} MB")
        head = bytes(data[:16])
        fmt = audio_format(head)
        if fmt is not None:
            return self.save_audio(data, fmt)
        if head[4:8] == b"ftyp" and head[8:12] in PICTURE_BRANDS:
            raise MediaError("a HEIC or AVIF picture is not read: save it as JPEG or PNG")
        return self.save_image(data)

    def _keep(self, tmp: Path, ext: str, **kw: Any) -> Saved:
        digest = hashlib.sha256(tmp.read_bytes()).hexdigest()
        name = f"{digest[:16]}.{ext}"
        size = tmp.stat().st_size
        os.replace(tmp, self.root / name)  # the same content is the same name: a second upload replaces it with itself
        return Saved(path=name, sha256=digest, bytes=size, **kw)

    def save_image(self, data: bytes | bytearray) -> Saved:
        try:
            with Image.open(io.BytesIO(data), formats=list(IMAGE_FORMATS)) as img:
                fmt, (w, h) = img.format, img.size
                if w * h > self.max_image_pixels:
                    raise MediaError(f"the picture is {w}×{h}: over {self.max_image_pixels} pixels")
                img.verify()
        except MediaError:
            raise
        except (OSError, ValueError, Image.DecompressionBombError, SyntaxError):
            raise MediaError("not a picture (PNG, JPEG, WebP) or a recording (WAV, MP3, M4A, OGG, FLAC, WebM, AMR)") from None
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = self.root / f".upload-{uuid.uuid4().hex}"
        try:
            tmp.write_bytes(data)
            return self._keep(tmp, IMAGE_FORMATS[fmt], type="image", width=w, height=h)
        finally:
            tmp.unlink(missing_ok=True)

    def ffmpeg(self) -> str:
        found = shutil.which(self.ffmpeg_bin)
        if found is None:
            raise NoFfmpeg(f"ffmpeg not found ({self.ffmpeg_bin!r}): install it or set FFMPEG_BIN")
        return found

    def command(self, exe: str, fmt: str, src: Path, dst: Path) -> list[str]:
        """The ffmpeg argv: the format named (`-f`), local files only, never stdin, no shell (§14.1)."""
        return [exe, "-nostdin", "-hide_banner", "-nostats", "-loglevel", "info", "-protocol_whitelist", "file",
                "-f", fmt, "-i", str(src), "-t", f"{self.max_seconds:g}", "-map", "0:a:0", "-map_metadata", "-1",
                "-fflags", "+bitexact", "-flags:a", "+bitexact", "-ac", "1", "-ar", str(self.sample_rate),
                "-c:a", "pcm_s16le", "-f", "wav", "-y", str(dst)]

    def save_audio(self, data: bytes | bytearray, fmt: str) -> Saved:
        exe = self.ffmpeg()
        self.root.mkdir(parents=True, exist_ok=True)
        stem = self.root / f".upload-{uuid.uuid4().hex}"
        src, dst = stem.with_suffix(".in"), stem.with_suffix(".wav")
        try:
            src.write_bytes(data)
            cmd = self.command(exe, fmt, src, dst)
            try:
                out = subprocess.run(cmd, capture_output=True, timeout=FFMPEG_TIMEOUT_S, stdin=subprocess.DEVNULL)
            except subprocess.TimeoutExpired:
                raise MediaError(f"ffmpeg took over {FFMPEG_TIMEOUT_S} s") from None
            err = out.stderr.decode(errors="replace")
            if out.returncode != 0 or not dst.is_file():
                log.warning("ffmpeg failed (%s): %s", out.returncode, err[-2000:])
                raise MediaError(f"ffmpeg could not read the file as {fmt} audio")
            m = DURATION_RE.search(err)
            source = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)) if m else None
            try:
                with wave.open(str(dst), "rb") as w:
                    ok = (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, self.sample_rate)
                    frames = w.getnframes()
            except (wave.Error, EOFError):
                ok, frames = False, 0
            if not ok:
                raise MediaError("ffmpeg did not write a 16-bit mono WAV")
            if frames == 0:
                raise MediaError("the recording has no sound to keep")
            seconds = round(frames / self.sample_rate, 3)
            # a recording that states no length (a live WebM, `Duration: N/A`) and fills the clip may be longer
            cut = source > seconds + 0.05 if source is not None else seconds >= self.max_seconds - 0.01
            return self._keep(dst, "wav", type="audio", seconds=seconds, cut=cut,
                              source_seconds=round(source, 2) if source is not None else None)
        finally:
            src.unlink(missing_ok=True)
            dst.unlink(missing_ok=True)
