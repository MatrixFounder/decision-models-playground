"""§8 the local model manager: one model, one call at a time, eviction, idle unload, one MLX thread."""

from __future__ import annotations

import asyncio
import threading

import pytest

from app.local_models import LoadError, LocalModels
from tests.conftest import D1_2B, D1_OMNI, FakeBackend


def ask(loaded):
    return loaded.model.system_one("x", {"q": {"type": "noul", "instructions": "?"}})


async def test_loads_once_and_reuses(local, fake_backend):
    for _ in range(3):
        out, loaded, _ = await local.call(D1_2B, ask)
        assert out["answers"]["q"]["noul"] == 0.9
    assert fake_backend.loads == [D1_2B] and loaded.snapshot.endswith("-20261105")
    assert local.describe(D1_2B)["state"] == "ready" and local.describe(D1_2B)["calls"] == 3


async def test_another_model_evicts_the_loaded_one():
    backend = FakeBackend(downloaded=(D1_2B, D1_OMNI))
    local = LocalModels(backend)
    await local.call(D1_2B, ask)
    await local.call(D1_OMNI, ask)
    assert backend.loads == [D1_2B, D1_OMNI] and backend.unloads == [D1_2B]
    assert local.describe(D1_2B)["state"] == "unloaded" and local.current.repo == D1_OMNI


async def test_calls_never_overlap_and_queue_in_order(local, fake_backend):
    fake_backend.call_delay = 0.05
    running, peak, order = 0, 0, []
    guard = threading.Lock()

    def slow(i):
        def fn(loaded):
            nonlocal running, peak
            with guard:
                running += 1
                peak = max(peak, running)
            ask(loaded)
            order.append(i)
            with guard:
                running -= 1
        return fn

    await asyncio.gather(*(local.call(D1_2B, slow(i)) for i in range(5)))
    assert peak == 1 and order == [0, 1, 2, 3, 4]


async def test_every_mlx_operation_runs_on_one_thread():
    backend = FakeBackend(downloaded=(D1_2B, D1_OMNI))
    local = LocalModels(backend)
    for repo in (D1_2B, D1_OMNI, D1_2B):
        await local.call(repo, ask)
    await local.unload()
    assert backend.thread_ids == {local.mlx_thread_ident} and threading.get_ident() not in backend.thread_ids


async def test_load_error_leaves_the_model_unloaded_and_is_retried(local, fake_backend):
    fake_backend.load_error = RuntimeError("out of memory")
    with pytest.raises(LoadError, match="out of memory"):
        await local.call(D1_2B, ask)
    d = local.describe(D1_2B)
    assert d["state"] == "unloaded" and "out of memory" in d["last_error"] and local.current is None
    fake_backend.load_error = None
    await local.call(D1_2B, ask)
    assert local.describe(D1_2B)["last_error"] is None


async def test_not_downloaded_is_a_load_error(local):
    with pytest.raises(LoadError, match="not downloaded"):
        await local.call(D1_OMNI, ask)


async def test_idle_unload(fake_backend):
    local = LocalModels(fake_backend, idle_unload_s=0)
    await local.call(D1_2B, ask)
    assert await local.unload_idle() and fake_backend.unloads == [D1_2B]
    assert not await local.unload_idle()  # nothing loaded


async def test_idle_unload_waits_for_a_busy_model(fake_backend):
    local = LocalModels(fake_backend, idle_unload_s=0)
    fake_backend.call_delay = 0.1
    task = asyncio.create_task(local.call(D1_2B, ask))
    await asyncio.sleep(0.03)
    assert not await local.unload_idle()
    await task
    assert await local.unload_idle()


async def test_unload_of_another_model_is_a_no_op(local, fake_backend):
    await local.call(D1_2B, ask)
    assert not await local.unload(D1_OMNI)
    assert await local.unload(D1_2B) and not await local.unload(D1_2B)


async def test_cancel_while_queued_keeps_the_counter_right(local, fake_backend):
    fake_backend.call_delay = 0.1
    first = asyncio.create_task(local.call(D1_2B, ask))
    await asyncio.sleep(0.02)
    queued = asyncio.create_task(local.call(D1_2B, ask))
    await asyncio.sleep(0.01)
    assert local.describe(D1_2B)["waiting"] == 1
    queued.cancel()
    with pytest.raises(asyncio.CancelledError):
        await queued
    await first
    assert local.describe(D1_2B)["waiting"] == 0 and not local.lock.locked()


async def test_cancel_during_load_keeps_the_loaded_model_tracked(local, fake_backend):
    fake_backend.load_delay = 0.1
    task = asyncio.create_task(local.call(D1_2B, ask))
    await asyncio.sleep(0.03)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert local.current is not None and local.current.repo == D1_2B  # memory is accounted for, not leaked
    assert not local.lock.locked()
    await local.call(D1_2B, ask)
    assert fake_backend.loads == [D1_2B]


# --- review P2 -------------------------------------------------------------------------------------------------------


async def test_double_cancel_keeps_the_lock_until_the_load_ends(local, fake_backend):
    fake_backend.load_delay = 0.2
    task = asyncio.create_task(local.call(D1_2B, ask))
    await asyncio.sleep(0.03)
    task.cancel()
    await asyncio.sleep(0.03)
    task.cancel()  # a second cancel while the first is being absorbed
    await asyncio.sleep(0.03)
    assert local.lock.locked()  # MLX is still loading: the slot is not free
    with pytest.raises(asyncio.CancelledError):
        await task
    assert local.current is not None and local.current.repo == D1_2B and not local.lock.locked()
    await local.call(D1_2B, ask)
    assert fake_backend.loads == [D1_2B]  # adopted, not loaded twice


async def test_cancel_during_a_failing_load_resets_the_state(local, fake_backend):
    fake_backend.load_delay = 0.1
    fake_backend.load_error = RuntimeError("Insufficient Memory")
    task = asyncio.create_task(local.call(D1_2B, ask))
    await asyncio.sleep(0.03)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert local.describe(D1_2B)["state"] == "unloaded" and local.current is None


async def test_an_adopted_load_is_not_unloaded_as_idle_at_once(fake_backend):
    import time as _time

    local = LocalModels(fake_backend, idle_unload_s=60)
    local.st(D1_2B).last_used = _time.time() - 3600  # last used an hour ago (round 2: without it the test was vacuous)
    fake_backend.load_delay = 0.1
    task = asyncio.create_task(local.call(D1_2B, ask))
    await asyncio.sleep(0.03)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not await local.unload_idle()


async def test_waiting_past_the_deadline_is_a_wait_timeout(local, fake_backend):
    from app.local_models import WaitTimeout

    fake_backend.call_delay = 0.3
    first = asyncio.create_task(local.call(D1_2B, ask))
    await asyncio.sleep(0.03)
    ran = []
    with pytest.raises(WaitTimeout):
        await local.call(D1_2B, lambda m: ran.append(1), wait_s=0.05)
    assert not ran and local.describe(D1_2B)["waiting"] == 0
    await first


async def test_the_queue_is_bounded(local, fake_backend):
    from app.local_models import Busy

    local.max_waiting = 1
    fake_backend.call_delay = 0.2
    first = asyncio.create_task(local.call(D1_2B, ask))
    await asyncio.sleep(0.03)
    second = asyncio.create_task(local.call(D1_2B, ask))
    await asyncio.sleep(0.03)
    with pytest.raises(Busy):
        await local.call(D1_2B, ask)
    await asyncio.gather(first, second)


async def test_aclose_returns_while_a_call_hangs(fake_backend):
    """A hung MLX call (a URL that never answers) must not keep shutdown waiting: the thread is a daemon."""
    import time as _time

    from app.local_models import Busy

    local = LocalModels(fake_backend)
    fake_backend.call_delay = 2.0
    hung = asyncio.create_task(local.call(D1_2B, ask))
    await asyncio.sleep(0.05)
    t0 = _time.perf_counter()
    await local.aclose(timeout_s=0.2)
    assert _time.perf_counter() - t0 < 1.0
    with pytest.raises(Busy):
        await local.call(D1_2B, ask)  # closing: no new calls
    hung.cancel()
    assert local._thread._thread.daemon


async def test_aclose_unloads_and_stops_the_watcher(fake_backend):
    local = LocalModels(fake_backend)
    local.start_watcher(every_s=0.01)
    await local.call(D1_2B, ask)
    await local.aclose()
    assert fake_backend.unloads == [D1_2B] and local._watcher is None


async def test_the_watcher_unloads_an_idle_model(fake_backend):
    local = LocalModels(fake_backend, idle_unload_s=0.05)
    local.start_watcher(every_s=0.02)
    await local.call(D1_2B, ask)
    await asyncio.sleep(0.2)
    assert fake_backend.unloads == [D1_2B]
    await local.aclose()


async def test_concurrent_calls_still_run_on_the_one_mlx_thread():
    """Concurrent callers would spread over a pool; the manager's own daemon thread takes them all."""
    backend = FakeBackend(downloaded=(D1_2B, D1_OMNI))
    backend.call_delay = 0.02
    local = LocalModels(backend)
    await asyncio.gather(*(local.call(repo, ask) for repo in (D1_2B, D1_OMNI, D1_2B, D1_OMNI)))
    assert backend.thread_ids == {local.mlx_thread_ident}


async def test_timings_split_queue_load_and_call(local, fake_backend):
    fake_backend.load_delay, fake_backend.call_delay = 0.1, 0.05
    _, _, t = await local.call(D1_2B, ask)
    assert t["load_ms"] >= 90 and 40 <= t["call_ms"] < 90 and t["queue_ms"] < 50
    _, _, t = await local.call(D1_2B, ask)
    assert t["load_ms"] == 0


async def test_describe_makes_no_mlx_call(local, fake_backend, monkeypatch):
    await local.call(D1_2B, ask)
    assert local.describe(D1_2B)["memory_gb"] == 2.0

    def boom():
        raise AssertionError("describe called MLX")

    monkeypatch.setattr(fake_backend, "memory_gb", boom)
    assert local.describe(D1_2B)["memory_gb"] == 2.0  # the figure read on the MLX thread after the call


async def test_submit_after_shutdown_raises_instead_of_hanging(fake_backend):
    local = LocalModels(fake_backend)
    await local.aclose()
    with pytest.raises(RuntimeError):
        local._thread.submit(lambda: 1)


async def test_closing_stops_absorbing_cancellation(fake_backend):
    """Once shutdown began, a cancelled caller abandons the MLX work instead of waiting for it (round 2)."""
    import time as _time

    local = LocalModels(fake_backend)
    fake_backend.call_delay = 2.0
    task = asyncio.create_task(local.call(D1_2B, ask))
    await asyncio.sleep(0.05)
    local.begin_close()
    t0 = _time.perf_counter()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert _time.perf_counter() - t0 < 0.5
