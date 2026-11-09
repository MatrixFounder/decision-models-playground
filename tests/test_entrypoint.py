"""python -m app as a process: start-up refusal (§9.2) and a clean SIGTERM with both servers up — review P2.
No model is loaded; the catalog is pre-seeded so nothing reaches the network."""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from app.settings import ROOT
from tests.conftest import load

PY = Path(sys.executable)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def env_for(tmp_path: Path, **extra: str) -> dict[str, str]:
    (tmp_path / "probes").mkdir(exist_ok=True)
    catalog = {"fetched_at": time.time(), "decisions": load("openrouter/catalog_decisions.json")["data"], "zdr": []}
    (tmp_path / "probes" / "catalog.json").write_text(json.dumps(catalog))
    env = {**os.environ, "DATA_DIR": str(tmp_path), "HF_HUB_OFFLINE": "1", "SYSTEMONE_TOKEN": "",
           "COMPAT_HOST": "127.0.0.1", "APP_HOST": "127.0.0.1"}
    env.update(extra)
    return env


def test_listening_on_the_lan_without_a_token_refuses_to_start(tmp_path):
    proc = subprocess.run([str(PY), "-m", "app"], cwd=ROOT, env=env_for(tmp_path, COMPAT_HOST="0.0.0.0"),
                          capture_output=True, text=True, timeout=30)
    assert proc.returncode != 0 and "SYSTEMONE_TOKEN" in proc.stderr + proc.stdout


@pytest.mark.timeout(60)
def test_both_servers_start_and_stop_cleanly_on_sigterm(tmp_path):
    ui, compat = free_port(), free_port()
    proc = subprocess.Popen([str(PY), "-m", "app"], cwd=ROOT,
                            env=env_for(tmp_path, APP_PORT=str(ui), COMPAT_PORT=str(compat)),
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        deadline = time.time() + 30
        while True:
            try:
                ok_ui = httpx.get(f"http://127.0.0.1:{ui}/api/budget", timeout=1).status_code == 200
                ok_compat = httpx.get(f"http://127.0.0.1:{compat}/health", timeout=1).status_code == 200
                if ok_ui and ok_compat:
                    break
            except httpx.HTTPError:
                pass
            assert proc.poll() is None, proc.stdout.read()
            assert time.time() < deadline, "servers did not come up"
            time.sleep(0.2)
        proc.send_signal(signal.SIGTERM)
        assert proc.wait(timeout=15) == 0
    finally:
        if proc.poll() is None:
            proc.kill()


def start_hung(tmp_path: Path) -> tuple[subprocess.Popen, int]:
    """The compat server with a model call that never returns, and one request stuck in it."""
    import threading

    port = free_port()
    proc = subprocess.Popen([str(PY), str(ROOT / "tests" / "hang_app.py"), "--compat-only"], cwd=ROOT,
                            env=env_for(tmp_path, COMPAT_PORT=str(port)),
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    deadline = time.time() + 30
    while True:
        try:
            if httpx.get(f"http://127.0.0.1:{port}/health", timeout=1).status_code == 200:
                break
        except httpx.HTTPError:
            pass
        assert proc.poll() is None and time.time() < deadline, "compat did not come up"
        time.sleep(0.2)
    request = load("requests/whip_insight_systemone.json")

    def stuck():
        try:
            httpx.post(f"http://127.0.0.1:{port}/v1/systemone", json=request, timeout=60)
        except httpx.HTTPError:
            pass  # the server goes away under it

    threading.Thread(target=stuck, daemon=True).start()
    time.sleep(1.0)  # the call is now inside the hung model
    return proc, port


@pytest.mark.timeout(60)
def test_one_sigterm_stops_a_process_whose_model_call_hangs(tmp_path):
    """Review round 2: SIGTERM ×2 and SIGINT used to leave the process alive; only SIGKILL ended it."""
    proc, _ = start_hung(tmp_path)
    try:
        proc.send_signal(signal.SIGTERM)
        assert proc.wait(timeout=20) is not None  # graceful 5 s, then a bounded close of the local manager
    finally:
        if proc.poll() is None:
            proc.kill()


@pytest.mark.timeout(60)
def test_a_second_sigterm_forces_the_exit(tmp_path):
    proc, _ = start_hung(tmp_path)
    try:
        proc.send_signal(signal.SIGTERM)
        time.sleep(0.5)
        t0 = time.time()
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=8)
        assert time.time() - t0 < 6
    finally:
        if proc.poll() is None:
            proc.kill()
