"""Entry point: python -m app

One process, one event loop, two uvicorn servers sharing one local model manager (docs/DESIGN.md §5, §8):
- the UI and /api on APP_HOST:APP_PORT (loopback only);
- the compat server POST /v1/systemone on COMPAT_HOST:COMPAT_PORT (LAN only behind SYSTEMONE_TOKEN, §9.2).

    python -m app                # both
    python -m app --no-compat    # the UI only
    python -m app --compat-only  # the compat server only
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import os
import signal
import sys
import threading


def main() -> None:
    if sys.prefix == sys.base_prefix:
        sys.exit("Запущено вне виртуального окружения: используйте .venv/bin/python -m app (или `make run`).")
    ap = argparse.ArgumentParser(prog="python -m app")
    group = ap.add_mutually_exclusive_group()
    group.add_argument("--no-compat", action="store_true", help="do not start the compat server")
    group.add_argument("--compat-only", action="store_true", help="start only the compat server")
    args = ap.parse_args()

    from pydantic import ValidationError

    try:
        from app.settings import ROOT, get_settings

        settings = get_settings()
    except ValidationError as e:  # DESIGN §9.2: a bad host/token combination stops the process before it listens
        sys.exit(f"Настройки .env не приняты: {e.errors()[0]['msg']}")

    import uvicorn

    from app.catalog import Catalog
    from app.compat import create_compat_app
    from app.local_models import make_local
    from app.main import create_app
    from app.registry import Registry, load_config

    class Server(uvicorn.Server):
        def capture_signals(self):  # the entry point handles signals for both servers
            return contextlib.nullcontext()

    settings.ensure_dirs()
    env = ROOT / ".env"
    if env.is_file() and env.stat().st_mode & 0o077:
        print("ВНИМАНИЕ: .env читают другие пользователи; выполните chmod 600 .env (DESIGN §18)", file=sys.stderr)
    config, _ = load_config(settings.config_path)
    local = make_local(settings, config)
    servers: list[Server] = []
    if not args.compat_only:
        ui = create_app(settings, local=local)
        servers.append(Server(uvicorn.Config(ui, host=settings.app_host, port=settings.app_port, log_level="info",
                                             timeout_graceful_shutdown=5)))
        print(f"Decision Models Playground → http://{settings.app_host}:{settings.app_port}")
    if not args.no_compat:
        registry = Registry(settings, Catalog(settings.probes_dir / "catalog.json"), local=local)
        compat = create_compat_app(registry)
        servers.append(Server(uvicorn.Config(compat, host=settings.compat_host, port=settings.compat_port,
                                             log_level="info", timeout_graceful_shutdown=5)))
        auth = "token" if settings.compat_token else "no token, loopback only"
        print(f"compat /v1/systemone → http://{settings.compat_host}:{settings.compat_port} ({auth})")

    async def serve() -> None:
        loop = asyncio.get_running_loop()

        def stop() -> None:
            local.begin_close()  # no new local calls; a cancelled request stops waiting for MLX
            if any(s.should_exit for s in servers):
                # a second signal: force, and exit hard if something still holds on (a hung MLX call, review round 2).
                # The timer is a daemon thread, not loop.call_later: the forced shutdown can close the loop within
                # 3 s while a thread that is not a daemon (an asyncio.to_thread worker stuck in a tokenizer) keeps
                # the interpreter alive — a loop timer never fired and the process hung (review P4)
                for s in servers:
                    s.force_exit = True
                hard = threading.Timer(3, os._exit, args=(1,))
                hard.daemon = True
                hard.start()
            for s in servers:
                s.should_exit = True

        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop)
        local.start_watcher()
        try:
            await asyncio.gather(*(s.serve() for s in servers))
        finally:
            # bounded: a hung MLX call is abandoned (its thread is a daemon), the process still exits
            await local.aclose(timeout_s=0.5 if any(s.force_exit for s in servers) else 5)

    asyncio.run(serve())


if __name__ == "__main__":
    main()
