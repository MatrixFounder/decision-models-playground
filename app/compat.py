"""The compat server: POST /v1/systemone over the local model manager (docs/DESIGN.md §9).

Contract for whip-insight's `systemone` adapter (its decision-service.md §5.2):
- body {model, state, questions, images?} as Ollama takes it; `model` is an engine id of kind mlx_local, default
  `compat.default_model`; extra fields such as `provider` or `keep_alive` are ignored;
- only noul, choice, score; another type → 400 {"error": {"code": "unsupported_question_type", "type", "question"}},
  which whip-insight reads as `unsupported` for that question (AD-18). The extended types need the header
  `X-Playground-Extensions: 1`, which whip-insight never sends;
- answers in System One names; `score` is the probability-weighted level (OpenRouter's meaning, not d1's median);
- 413 when the row (state as mlx-vlm renders it plus every question) exceeds `context_tokens`; 429 when the local
  queue is full or the wait passes the engine's timeout; 503 when the model cannot load; 500 `model_error` otherwise;
- every POST needs `Authorization: Bearer <SYSTEMONE_TOKEN>` when the token is set (scheme case-insensitive, token
  compared in constant time, 401 without details); without a token the server only ever listens on loopback;
- a request with an `Origin` header comes from a browser page, never from whip-insight: 403. Host must be loopback,
  `*.local` or listed in `compat.allowed_hosts`. No CORS, no UI, no docs.

Review P2 hardening: `instructions` only as text (d1 reads a dict there as a file path or URL); pictures only PNG,
JPEG or WebP (no Ghostscript behind EPS), decoded off the event loop; the body is counted while it streams in.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hmac
import io
import json
import logging
import math
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from app import __version__
from app.engines.mlx_local import MlxLocal, TooLong, cheap_chars, d1_question, json_safe, mlx_vlm_version
from app.local_models import Busy, LoadError, WaitTimeout
from app.registry import Registry
from app.schemas import MAX_STATE_DEPTH, SYSTEM_ONE_TYPES, Question, json_depth, valid_key
from app.settings import is_loopback
from app.store import new_id

log = logging.getLogger("playground.compat")
EXTENDED = ("multi", "text", "number", "date")
IMAGE_FORMATS = ["PNG", "JPEG", "WEBP"]


def error(status: int, code: str, **extra: Any) -> JSONResponse:
    return JSONResponse({"error": {"code": code, **extra}}, status_code=status)


def echo(value: Any) -> str | None:
    """A request value put back into an error body: only a short string (review P2)."""
    return value[:64] if isinstance(value, str) else None


def bearer_ok(header: str, token: str) -> bool:
    scheme, _, given = header.strip().partition(" ")
    return scheme.lower() == "bearer" and hmac.compare_digest(given.strip().encode(), token.encode())


def create_compat_app(registry: Registry) -> FastAPI:
    settings = registry.ctx.settings
    cfg = registry.config.compat
    token = settings.compat_token
    max_body = int(cfg.max_body_mb * 1024 * 1024)
    ext_header = cfg.extensions_header.lower()
    hosts = list(cfg.allowed_hosts)
    if settings.compat_host not in hosts and settings.compat_host != "0.0.0.0":
        hosts.append(settings.compat_host)

    app = FastAPI(title="Decision Models Playground — compat", version=__version__, docs_url=None, redoc_url=None,
                  openapi_url=None)

    def local_engine(name: Any) -> MlxLocal | None:
        """The engine a request names; no `model` means `compat.default_model`; a non-string name is not found."""
        if name is None:
            name = cfg.default_model
        engine = registry.engines.get(name) if isinstance(name, str) else None
        return engine if isinstance(engine, MlxLocal) else None

    @app.middleware("http")
    async def guard(request: Request, call_next):
        # Host first, answered in the JSON error shape whip-insight reads (Starlette's own is text/plain)
        if not host_allowed(request.headers.get("host", ""), hosts):
            return error(400, "forbidden_host")
        if request.headers.get("origin"):
            return error(403, "forbidden")
        if request.method == "POST":
            if token is not None and not bearer_ok(request.headers.get("authorization", ""), token):
                return error(401, "unauthorized")
            length = request.headers.get("content-length")
            if length is not None and (not (length.isascii() and length.isdigit()) or int(length) > max_body):
                return error(413, "too_large", limit_bytes=max_body)
        return await call_next(request)

    @app.get("/health")
    async def health() -> dict[str, Any]:
        """No token needed, so no paths, repos or messages: status, model id, state, mlx-vlm version (§9.1)."""
        engine = local_engine(None)
        ok = engine.available()[0] if engine else False
        d = registry.ctx.local.describe(engine.repo) if engine and registry.ctx.local else {}
        status = "unavailable" if not ok else ("degraded" if d.get("last_error") else "ok")
        version = mlx_vlm_version()
        out = {
            "status": status,
            "model": engine.spec.id if engine else None,
            "state": d.get("state"),
            "mlx_vlm_version": ".".join(map(str, version)) if version else None,
        }
        if status == "degraded":
            out["error"] = "load_failed"  # the message itself is on the UI's Local models screen
        return out

    async def read_body(request: Request) -> bytes | None:
        """The body, counted as it streams in: a chunked upload stops at the limit (review P2)."""
        buf = bytearray()
        async for chunk in request.stream():
            buf += chunk
            if len(buf) > max_body:
                return None
        return bytes(buf)

    @app.post("/v1/systemone")
    async def systemone(request: Request):
        body_bytes = await read_body(request)
        if body_bytes is None:
            return error(413, "too_large", limit_bytes=max_body)
        try:  # parsing 6 MB takes ~0.4 s: off the shared event loop (review round 3)
            body = await asyncio.to_thread(json.loads, body_bytes)
        except (ValueError, RecursionError):
            return error(400, "invalid_json")
        if not isinstance(body, dict):
            return error(400, "invalid_request", message="the body is a JSON object")

        engine = local_engine(body.get("model"))
        if engine is None:
            return error(404, "model_not_found", model=echo(body.get("model")))
        if not engine.available()[0]:
            return error(503, "model_unavailable")

        extensions = request.headers.get(ext_header) == "1"
        questions = body.get("questions")
        if not isinstance(questions, dict) or not 1 <= len(questions) <= cfg.max_questions:
            return error(400, "invalid_request", message=f"questions: 1 to {cfg.max_questions} named questions")
        parsed: dict[str, Question] = {}
        for key, q in questions.items():
            if not valid_key(key):
                return error(400, "invalid_request", message="a question key must match ^[a-z][a-z0-9_]{0,63}$")
            if not isinstance(q, dict):
                return error(400, "invalid_request", question=key, message="a question is an object")
            kind = "multi" if q.get("type") == "choice" and q.get("multiple") is True else q.get("type")
            if kind not in SYSTEM_ONE_TYPES and not (extensions and kind in EXTENDED):
                return error(400, "unsupported_question_type", type=echo(kind), question=key)
            if not isinstance(q.get("instructions"), str) or not q["instructions"].strip():
                # d1 reads a dict here as a media item and opens its path or URL (review P2)
                return error(400, "invalid_request", question=key, message="instructions must be non-empty text")
            try:
                parsed[key] = Question.model_validate(q)
            except ValidationError as e:
                return error(400, "invalid_request", question=key, message=e.errors()[0]["msg"][:200])

        state = body.get("state")
        images = body.get("images") or []
        if not (state is None or isinstance(state, (str, dict, list))):
            return error(400, "invalid_request", message="state is text, a JSON object or array")
        if not isinstance(images, list) or len(images) > cfg.max_images or not all(isinstance(i, str) for i in images):
            return error(400, "invalid_request", message=f"images: a list of at most {cfg.max_images} base64 strings")
        if state is None and not images:
            return error(400, "invalid_request", message="state is required")
        if body.get("media"):
            return error(400, "invalid_request", message="media is not accepted yet; send pictures in images")
        # cheap first, off the event loop, in this order (review round 3): whip-insight's 1 token per character over
        # the compact state and the heaviest question group (C speed), then the nesting walk on what is left —
        # nothing is rendered here (an indent=2 rendering of deep JSON grows with the square of the depth)
        size = registry.ctx.local.backend.set_size(engine.repo)

        def cheap() -> tuple[int, int]:
            chars = cheap_chars(state, parsed, size, len(images))
            if chars > engine.context_tokens:
                return chars, 0
            return chars, (json_depth(state) if not isinstance(state, str) else 0)

        try:
            chars, depth = await asyncio.to_thread(cheap)
        except RecursionError:
            return error(400, "invalid_request", message=f"state is nested deeper than {MAX_STATE_DEPTH} levels")
        if chars > engine.context_tokens:
            return error(413, "too_long", message=f"{chars} characters > {engine.context_tokens}")
        if depth > MAX_STATE_DEPTH:
            return error(400, "invalid_request", message=f"state is nested deeper than {MAX_STATE_DEPTH} levels")

        try:
            media = await asyncio.to_thread(decode_images, body.get("images"), cfg.max_images, cfg.max_image_pixels)
        except ValueError as e:
            return error(400, "invalid_request", message=str(e))

        try:
            await engine.precheck(state, parsed, images=len(media))
        except TooLong as e:
            return error(413, "too_long", message=str(e))
        except LoadError:
            return error(503, "model_unavailable")
        except ValueError as e:
            return error(400, "invalid_request", message=f"mlx-vlm: {e}"[:300])
        except Exception:
            log.exception("precheck failed")  # the caller gets the JSON contract, the log gets the cause
            return error(500, "model_error")

        d1 = {k: d1_question(q) for k, q in parsed.items()}
        try:
            out, loaded, _ = await registry.ctx.local.call(
                engine.repo, lambda m: engine.invoke(m, state, d1, media, score_readout="mean"),
                warm_audio=engine.spec.audio, wait_s=engine.spec.timeout_s)
        except (Busy, WaitTimeout):
            return error(429, "busy")  # whip-insight: rate_limited
        except LoadError:
            return error(503, "model_unavailable")  # the reason (paths) stays on the UI screen
        except ValueError as e:
            return error(400, "invalid_request", message=f"mlx-vlm: {e}"[:300])
        except Exception:
            log.exception("local model call failed")
            return error(500, "model_error")

        raw_answers = out.get("answers") if isinstance(out, dict) else None
        if not raw_answers or not _finite(raw_answers):  # before json_safe, which would turn NaN into null
            return error(500, "model_error")
        answers = json_safe(raw_answers)
        usage = (out.get("usage") or {}) if isinstance(out, dict) else {}
        return {
            "id": f"so-{new_id()}",
            "model": loaded.snapshot,
            "provider": "local-mlx",
            "answers": answers,
            "usage": {"input_tokens": usage.get("input_tokens"), "output_tokens": 0, "cost": 0},
        }

    return app


def host_allowed(host: str, allowed: list[str]) -> bool:
    """The Host header's name (port dropped) against the allowed names, case-insensitive; `*.suffix` matches any
    name ending in `.suffix` with something before the dot. A bare `*` (Starlette's "any host") is not honoured:
    this check fails closed. A bracketed IPv6 Host never matches (COMPAT_HOST takes IPv4 or names)."""
    name = host.rsplit(":", 1)[0] if host.count(":") == 1 else host
    name = name.lower()
    for pattern in allowed:
        pattern = pattern.lower()
        if name == pattern or pattern.startswith("*.") and name.endswith(pattern[1:]) and len(name) > len(pattern) - 1:
            return True
    return False


def _finite(value: Any) -> bool:
    if isinstance(value, dict):
        return all(_finite(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return all(_finite(v) for v in value)
    if hasattr(value, "item") and callable(value.item):  # numpy scalars (float32 is not a float subclass)
        value = value.item()
    return not isinstance(value, float) or math.isfinite(value)


def decode_images(images: Any, limit: int, max_pixels: int) -> list:
    """Ollama's `images`: base64 PNG/JPEG/WebP, decoded to pictures for system_one. Only those three decoders are
    allowed (Pillow would hand EPS to Ghostscript), and the size is checked from the header, before decoding."""
    if not images:
        return []
    if not isinstance(images, list) or len(images) > limit:
        raise ValueError(f"images: a list of at most {limit} base64 pictures")
    from PIL import Image, UnidentifiedImageError

    out = []
    for i, data in enumerate(images):
        try:
            raw = base64.b64decode(data, validate=True)
            img = Image.open(io.BytesIO(raw), formats=IMAGE_FORMATS)  # lazy: reads the header only
        except (binascii.Error, TypeError, ValueError, UnidentifiedImageError, OSError):
            raise ValueError(f"images[{i}] is not a base64 PNG, JPEG or WebP picture") from None
        if img.width * img.height > max_pixels:
            raise ValueError(f"images[{i}] has {img.width}x{img.height} pixels, more than {max_pixels}")
        try:
            img.load()
        except (OSError, ValueError, Image.DecompressionBombError):
            raise ValueError(f"images[{i}] could not be decoded") from None
        out.append(img.convert("RGB"))
    return out
