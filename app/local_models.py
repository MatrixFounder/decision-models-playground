"""The local model manager (docs/ARCHITECTURE.md §3.6, figure 2): one model in memory, one call at a time.

    unloaded --request--> loading --ok--> ready --request--> busy --answer--> ready
    loading --error--> unloaded;  ready --idle or eviction--> unloaded

- The UI (:8790) and the compat server (:8791) share one manager in one process, so one model serves both.
- `asyncio.Lock` is FIFO: a request for another model waits for the running call, then evicts the loaded model.
- Waiting is bounded twice: at most `max_waiting` callers queue (`Busy` beyond), and each waits at most its
  deadline (`WaitTimeout`). A call already running on MLX cannot be interrupted.
- Every MLX operation (load, call, unload, the memory figure) runs on ONE dedicated daemon thread: MLX streams are
  tied to a thread, and a hung operation must not keep the interpreter from exiting (review P2).
- Facts from P0 (docs/SPIKE.md §2): load 1.3-1.7 s; 2.1-2.5 GB after load; `del` + `gc.collect()` +
  `mx.clear_cache()` return MLX memory to 0; d1-Omni's first audio call is 1.8 s, so loading Omni warms it up.
"""

from __future__ import annotations

import asyncio
import gc
import json
import logging
import os
import queue
import re
import threading
import time
from collections.abc import Callable
from concurrent.futures import Executor, Future
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, TypeVar


log = logging.getLogger("playground.local")
T = TypeVar("T")
GB = 1024**3
DISK_TTL_S = 60


class LoadError(Exception):
    """The model could not be loaded: not downloaded, wrong mlx-vlm, out of memory."""


class Busy(Exception):
    """Too many callers already wait for the local model."""


class WaitTimeout(Exception):
    """The caller's deadline passed while it waited for the local model."""


@dataclass
class Loaded:
    repo: str
    model: Any
    processor: Any
    path: str
    revision: str
    snapshot: str
    loaded_at: float
    load_ms: int


class Backend(Protocol):
    """What the manager and the engine need from MLX; tests pass a fake."""

    def load(self, repo: str, warm_audio: bool) -> Loaded: ...
    def unload(self, loaded: Loaded) -> None: ...
    def memory_gb(self) -> float | None: ...
    def disk_gb(self, repo: str) -> float | None: ...
    def count_tokens(self, repo: str, text: str) -> int: ...
    def set_size(self, repo: str) -> int: ...
    def validate(self, questions: dict[str, Any]) -> None: ...


class MlxThread(Executor):
    """A one-thread executor on a daemon thread: every MLX call on the same OS thread, and a hung call never blocks
    the interpreter's exit (ThreadPoolExecutor joins its workers at exit)."""

    def __init__(self, name: str = "mlx"):
        self._q: queue.SimpleQueue = queue.SimpleQueue()
        self._stopped = False
        self._guard = threading.Lock()  # the stop check and the enqueue happen together
        self._thread = threading.Thread(target=self._work, name=name, daemon=True)
        self._thread.start()
        self.ident = self._thread.ident

    def _work(self) -> None:
        while True:
            item = self._q.get()
            if item is None:
                return
            fut, fn, args = item
            if not fut.set_running_or_notify_cancel():
                continue
            try:
                fut.set_result(fn(*args))
            except BaseException as e:
                fut.set_exception(e)

    def submit(self, fn, /, *args, **kwargs) -> Future:
        fut: Future = Future()
        with self._guard:
            if self._stopped:  # the Executor contract: never a future that cannot resolve (review round 2)
                raise RuntimeError("the MLX thread is shut down")
            self._q.put((fut, (lambda *a: fn(*a, **kwargs)) if kwargs else fn, args))
        return fut

    def shutdown(self, wait: bool = True, *, cancel_futures: bool = False) -> None:
        with self._guard:
            self._stopped = True
            self._q.put(None)
        if wait:
            self._thread.join()


class SnapshotDates:
    """`<repo>-<YYYYMMDD>` from the commit date of the cached revision (ARCHITECTURE §3.5.4), cached in probes/. Offline the
    snapshot stays undated — never invented — and the failed lookup is not repeated in this session."""

    def __init__(self, path: Path):
        self.path = path
        self.failed: set[str] = set()
        try:
            self.cache: dict[str, str] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.cache = {}

    def lookup(self, repo: str, revision: str) -> str | None:
        from huggingface_hub import HfApi

        when = HfApi().model_info(repo, revision=revision, timeout=5).last_modified
        return when.strftime("%Y%m%d") if when else None

    def snapshot(self, repo: str, revision: str) -> str:
        key = f"{repo}@{revision}"
        day = self.cache.get(key)
        if day is None and key not in self.failed and os.environ.get("HF_HUB_OFFLINE", "0") in ("0", "", "false"):
            try:
                day = self.lookup(repo, revision)
            except Exception as e:
                log.info("no commit date for %s: %s", key, e)
                day = None
            if day:
                self.cache[key] = day
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self.path.write_text(json.dumps(self.cache, indent=1), encoding="utf-8")
            else:
                self.failed.add(key)
        return f"{repo}-{day}" if day else repo


BREAK = "\u200b"  # mlx-vlm's prompt.BREAK


def specials_pattern(tok: Any) -> re.Pattern | None:
    """mlx-vlm's `_specials_cached` on a `tokenizers.Tokenizer`: every special added token longer than one
    character, longest first."""
    names = {t.content for t in tok.get_added_tokens_decoder().values() if t.special}
    names = sorted((n for n in names if len(n) > 1 and BREAK not in n), key=len, reverse=True)
    return re.compile("|".join(map(re.escape, names))) if names else None


def escape_specials(pattern: re.Pattern | None, text: str) -> str:
    """mlx-vlm's `plain()`: a zero-width space after the first character of each special-token string, repeated
    for matches that overlapped a broken one."""
    if pattern is None:
        return text
    n = 1
    while n:
        text, n = pattern.subn(lambda m: m[0][0] + BREAK + m[0][1:], text)
    return text


def rendered_length(state: Any, limit: int) -> int:
    """len(render_state(state)) without building the string; stops once past `limit` (then returns > limit).
    json.dumps(indent=2): a non-empty container at level L writes its brackets, per item a newline and 2(L+1)
    spaces, commas between items, and a newline plus 2L spaces before the closing bracket; a dict item adds its
    key and ": "."""
    if state is None:
        return 0
    if isinstance(state, str):
        return len(state)
    total, stack = 0, [(state, 0)]
    while stack:
        v, level = stack.pop()
        if isinstance(v, (dict, list)):
            n = len(v)
            if not n:
                total += 2
            else:
                total += 2 + n * (1 + 2 * (level + 1)) + (n - 1) + 1 + 2 * level
                if isinstance(v, dict):
                    for k, x in v.items():
                        key = k if isinstance(k, str) else json.dumps(k)
                        total += len(json.dumps(key, ensure_ascii=False)) + 2
                        stack.append((x, level + 1))
                else:
                    stack.extend((x, level + 1) for x in v)
        else:
            total += len(json.dumps(v, ensure_ascii=False))
        if total > limit:
            return total
    return total


def render_state(state: Any) -> str:
    """The state exactly as mlx-vlm's prompt writes it (`state_block`, style `json_only`): a string as it is, any
    other JSON value with indent=2. A compact count of a nested dict undercounts the model's row 7.7x (review P2)."""
    if state is None:
        return ""
    return state if isinstance(state, str) else json.dumps(state, ensure_ascii=False, indent=2)


class MlxBackend:
    def __init__(self, snapshots: SnapshotDates):
        self.snapshots = snapshots
        self._disk: dict[str, tuple[float, float | None]] = {}
        self._tokenizers: dict[str, Any] = {}
        self._set_sizes: dict[str, int] = {}
        self._lock = threading.Lock()

    @staticmethod
    def local_path(repo: str) -> Path:
        from huggingface_hub import snapshot_download

        # never download behind the user's back: `make models` does that
        return Path(snapshot_download(repo, local_files_only=True))

    def load(self, repo: str, warm_audio: bool) -> Loaded:
        try:
            path = self.local_path(repo)
        except Exception as e:
            raise LoadError(f"{repo} is not downloaded (make models): {type(e).__name__}") from None
        t0 = time.perf_counter()
        try:
            from mlx_vlm import load

            model, processor = load(str(path))
            if not callable(getattr(model, "system_one", None)):
                raise LoadError(f"{repo} has no system_one: not a d1 checkpoint, or mlx-vlm is older than 0.7.11")
        except LoadError:
            self._clear()
            raise
        except Exception as e:
            self._clear()
            raise LoadError(f"loading {repo} failed: {type(e).__name__}: {e}"[:300]) from None
        if warm_audio:  # optional: a failed warm-up costs the first call 1.8 s, never the model
            try:
                import numpy as np

                silence = {"type": "audio", "audio": np.zeros(16000, dtype=np.float32), "sampling_rate": 16000}
                model.system_one(None, {"warm": {"type": "noul", "instructions": "Is anyone speaking?"}}, [silence])
            except Exception as e:
                log.warning("audio warm-up of %s failed: %s", repo, e)
        return Loaded(repo=repo, model=model, processor=processor, path=str(path), revision=path.name,
                      snapshot=self.snapshots.snapshot(repo, path.name), loaded_at=time.time(),
                      load_ms=int((time.perf_counter() - t0) * 1000))

    @staticmethod
    def _clear() -> None:
        try:
            import mlx.core as mx

            gc.collect()
            mx.clear_cache()
        except Exception:
            pass

    def unload(self, loaded: Loaded) -> None:
        tokenizer = getattr(loaded.processor, "tokenizer", None)
        loaded.model = loaded.processor = None
        try:  # mlx-vlm keeps every tokenizer it has seen in module-level caches (review P2)
            from mlx_vlm.models.d1_lfm3_vl import prompt

            if tokenizer is not None:
                prompt._TOKENIZERS.pop(id(tokenizer), None)
            prompt._aliases_cached.cache_clear()
            prompt._ids_cached.cache_clear()
        except Exception:
            pass
        self._clear()

    def memory_gb(self) -> float | None:
        try:
            import mlx.core as mx

            return round(mx.get_active_memory() / GB, 3)
        except Exception:
            return None

    def disk_gb(self, repo: str) -> float | None:
        """Size on disk, None when not downloaded; cached for a minute (it is asked on every availability check)."""
        now = time.monotonic()
        hit = self._disk.get(repo)
        if hit and now - hit[0] < DISK_TTL_S:
            return hit[1]
        try:
            path = self.local_path(repo)
            size = round(sum(f.stat().st_size for f in path.rglob("*") if f.is_file()) / GB, 3)
        except Exception:
            size = None
        self._disk[repo] = (now, size)
        return size

    def _tokenizer(self, repo: str) -> tuple[Any, Any]:
        """The model's tokenizer.json and the pattern of its special-token strings, without loading the model."""
        with self._lock:
            hit = self._tokenizers.get(repo)
            if hit is None:
                try:
                    from tokenizers import Tokenizer

                    tok = Tokenizer.from_file(str(self.local_path(repo) / "tokenizer.json"))
                except Exception as e:  # tokenizers raises a bare Exception for a missing or corrupt file
                    raise LoadError(f"the tokenizer of {repo} cannot be read: {type(e).__name__}") from None
                hit = self._tokenizers[repo] = (tok, specials_pattern(tok))
        return hit

    def count_tokens(self, repo: str, text: str) -> int:
        """Tokens of `text` as the model reads it: escaped the way mlx-vlm's `plain()` escapes the caller's text
        (a special-token string such as `<|pad|>` is one token raw and five once broken, review round 2)."""
        tok, pattern = self._tokenizer(repo)
        return len(tok.encode(escape_specials(pattern, text), add_special_tokens=False).ids)

    def set_size(self, repo: str) -> int:
        """Questions packed into one row after the state (`d1_set_size` in config.json; mlx-vlm's default is 4)."""
        if repo not in self._set_sizes:
            try:
                cfg = json.loads((self.local_path(repo) / "config.json").read_text(encoding="utf-8"))
                self._set_sizes[repo] = int(cfg.get("d1_set_size") or 4)
            except Exception:
                return 4  # not cached: the model may be downloaded later
        return self._set_sizes[repo]

    def validate(self, questions: dict[str, Any]) -> None:
        """mlx-vlm's own question checks (pure Python), before any model is loaded; ValueError names the question."""
        from mlx_vlm.models.d1_lfm3_vl.prompt import as_question, check_question

        for name, q in questions.items():
            try:
                check_question(q)
                as_question(q)
            except ValueError as e:
                raise ValueError(f"question {name!r}: {e}") from None


def make_local(settings: Any, config: Any) -> LocalModels:
    """The real manager: MLX backend, commit dates cached in probes/local_snapshots.json."""
    backend = MlxBackend(SnapshotDates(settings.probes_dir / "local_snapshots.json"))
    return LocalModels(backend, idle_unload_s=config.local.idle_unload_s, max_waiting=config.local.max_waiting)


@dataclass
class ModelState:
    state: str = "unloaded"  # unloaded | loading | ready | busy
    last_used: float | None = None
    last_error: str | None = None
    calls: int = 0
    waiting: int = 0
    snapshot: str | None = None  # the last loaded snapshot, kept after unload


class LocalModels:
    def __init__(self, backend: Backend, idle_unload_s: float = 600, max_waiting: int = 8):
        self.backend = backend
        self.idle_unload_s = idle_unload_s
        self.max_waiting = max_waiting
        self.lock = asyncio.Lock()
        self.current: Loaded | None = None
        self.states: dict[str, ModelState] = {}
        self.memory_gb: float | None = None  # read on the MLX thread after each operation
        self.closing = False
        self._closed = asyncio.Event()  # set by begin_close: calls in flight stop waiting for MLX
        self._thread = MlxThread()
        self._watcher: asyncio.Task | None = None

    @property
    def mlx_thread_ident(self) -> int | None:
        return self._thread.ident

    @property
    def waiting(self) -> int:
        return sum(s.waiting for s in self.states.values())

    def st(self, repo: str) -> ModelState:
        return self.states.setdefault(repo, ModelState())

    def _adopt(self, loaded: Loaded) -> None:
        s = self.st(loaded.repo)
        self.current = loaded
        s.state, s.last_error, s.snapshot, s.last_used = "ready", None, loaded.snapshot, time.time()

    async def _on_thread(self, fn: Callable[..., T], *args, abandon_on_close: bool = False) -> T:
        """Run on the MLX thread. However often the caller is cancelled, it waits for the work to end before it
        lets go: the lock is never released while MLX is busy, and a model loaded for a caller who left is
        adopted (tracked, unloadable) rather than lost (review P2: a second cancel used to leak it).
        Shutting down changes that: a cancel is no longer absorbed, and with `abandon_on_close` the caller gets
        `Busy` as soon as shutdown begins — compat answers a JSON 429 at once instead of a text/plain 500 at the
        graceful timeout (review round 3). The abandoned work finishes, or not, on the daemon thread."""
        fut = asyncio.wrap_future(self._thread.submit(fn, *args))
        closed = asyncio.ensure_future(self._closed.wait()) if abandon_on_close else None
        cancelled = False
        try:
            while not fut.done():
                if closed is not None and closed.done():
                    raise Busy("the local model manager is shutting down")
                try:
                    await asyncio.wait({fut} if closed is None else {fut, closed},
                                       return_when=asyncio.FIRST_COMPLETED)
                except asyncio.CancelledError:
                    if self.closing:  # shutting down: abandon the MLX work instead of holding the exit
                        raise
                    cancelled = True
        finally:
            if closed is not None:
                closed.cancel()
        if cancelled:
            if fn == self.backend.load and fut.exception() is None:
                self._adopt(fut.result())
            raise asyncio.CancelledError
        return fut.result()

    def _measure(self) -> None:
        self.memory_gb = self.backend.memory_gb()

    async def _unload_locked(self) -> None:
        if self.current is None:
            return
        cur, self.current = self.current, None
        self.st(cur.repo).state = "unloaded"
        await self._on_thread(self.backend.unload, cur)
        await self._on_thread(self._measure)
        log.info("unloaded %s", cur.repo)

    async def call(self, repo: str, fn: Callable[[Loaded], T], warm_audio: bool = False,
                   wait_s: float | None = None) -> tuple[T, Loaded, dict[str, int]]:
        """Run `fn(loaded)` on the MLX thread with `repo` loaded, after the calls queued before it. Returns the
        result, the loaded model and timings: queue_ms, load_ms (0 when already loaded), call_ms."""
        if self.closing:
            raise Busy("the local model manager is shutting down")
        if self.waiting >= self.max_waiting:
            raise Busy(f"{self.waiting} requests already wait for the local model")
        s = self.st(repo)
        t0 = time.perf_counter()
        s.waiting += 1
        try:
            async with asyncio.timeout(wait_s):
                await self.lock.acquire()
        except TimeoutError:
            raise WaitTimeout(f"waited {wait_s:.0f} s for the local model") from None
        finally:
            s.waiting -= 1  # also when the caller was cancelled while queued
        timings = {"queue_ms": int((time.perf_counter() - t0) * 1000), "load_ms": 0, "call_ms": 0}
        if self.closing:  # shutdown began while this caller queued: never load again behind an abandoned load
            self.lock.release()
            raise Busy("the local model manager is shutting down")
        try:
            if self.current is None or self.current.repo != repo:
                await self._unload_locked()
                s.state = "loading"
                t1 = time.perf_counter()
                try:
                    self._adopt(await self._on_thread(self.backend.load, repo, warm_audio, abandon_on_close=True))
                except (asyncio.CancelledError, Busy):
                    raise
                except Exception as e:
                    s.last_error = str(e)[:300]
                    raise LoadError(s.last_error) from None
                finally:
                    if s.state == "loading":  # failed or cancelled before it finished
                        s.state = "unloaded"
                timings["load_ms"] = int((time.perf_counter() - t1) * 1000)
            loaded = self.current
            s.state = "busy"
            t2 = time.perf_counter()
            try:
                result = await self._on_thread(fn, loaded, abandon_on_close=True)
            finally:
                timings["call_ms"] = int((time.perf_counter() - t2) * 1000)
                s.state = "ready"
                s.last_used = time.time()
                s.calls += 1
            await self._on_thread(self._measure)
            return result, loaded, timings
        finally:
            self.lock.release()

    async def unload(self, repo: str | None = None) -> bool:
        """Unload `repo` (or whatever is loaded) after the running call; False when it was not loaded."""
        async with self.lock:
            if self.current is None or (repo is not None and self.current.repo != repo):
                return False
            await self._unload_locked()
            return True

    async def unload_idle(self) -> bool:
        if self.lock.locked() or self.current is None:
            return False
        last = self.st(self.current.repo).last_used or self.current.loaded_at
        if time.time() - last < self.idle_unload_s:
            return False
        return await self.unload(self.current.repo)

    def start_watcher(self, every_s: float = 30) -> None:
        async def watch():
            while True:
                await asyncio.sleep(every_s)
                try:
                    await self.unload_idle()
                except Exception:
                    log.exception("idle unload failed")

        if self._watcher is None:
            self._watcher = asyncio.create_task(watch())

    def begin_close(self) -> None:
        """From the first shutdown signal on: no new calls, calls in flight get Busy at once, and a cancelled
        caller no longer waits for MLX."""
        self.closing = True
        self._closed.set()

    async def aclose(self, timeout_s: float = 5) -> None:
        """Stop taking calls, try to unload within `timeout_s`, stop the MLX thread. A hung MLX call is abandoned:
        its thread is a daemon and does not hold the process."""
        self.closing = True
        if self._watcher is not None:
            self._watcher.cancel()
            self._watcher = None
        try:
            async with asyncio.timeout(timeout_s):
                await self.unload()
        except TimeoutError:
            log.warning("local model not unloaded within %s s at shutdown", timeout_s)
        self._thread.shutdown(wait=False)

    def describe(self, repo: str) -> dict[str, Any]:
        """The model's state for the UI and /health; no MLX call (memory is the figure read after the last one)."""
        s = self.st(repo)
        loaded = self.current if self.current and self.current.repo == repo else None
        return {
            "state": s.state,
            "last_used": s.last_used,
            "last_error": s.last_error,
            "calls": s.calls,
            "waiting": s.waiting,
            "loaded_at": loaded.loaded_at if loaded else None,
            "load_ms": loaded.load_ms if loaded else None,
            "snapshot": loaded.snapshot if loaded else s.snapshot,
            "memory_gb": self.memory_gb if loaded else None,
        }
