"""Entry point: python -m app (UI and /api on APP_HOST:APP_PORT, loopback only)."""

from __future__ import annotations

import sys


def main() -> None:
    if sys.prefix == sys.base_prefix:
        sys.exit("Запущено вне виртуального окружения: используйте .venv/bin/python -m app (или `make run`).")

    import uvicorn

    from app.settings import get_settings

    settings = get_settings()
    settings.ensure_dirs()
    print(f"Decision Models Playground → http://{settings.app_host}:{settings.app_port}")
    uvicorn.run("app.main:app", host=settings.app_host, port=settings.app_port, log_level="info")


if __name__ == "__main__":
    main()
