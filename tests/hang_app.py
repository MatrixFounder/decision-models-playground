"""A child process for tests/test_entrypoint.py (not a test module): `python -m app` whose local model call never
returns, as after a Metal fault (review round 2). Run as: python tests/hang_app.py --compat-only"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.local_models as lm  # noqa: E402
from app.local_models import Loaded, LocalModels  # noqa: E402


class Hang:
    def system_one(self, *args, **kwargs):
        threading.Event().wait()  # forever


class HangBackend:
    def load(self, repo, warm_audio):
        return Loaded(repo=repo, model=Hang(), processor=None, path="/hang", revision="r",
                      snapshot=f"{repo}-20261105", loaded_at=time.time(), load_ms=1)

    def unload(self, loaded):
        pass

    def memory_gb(self):
        return 1.0

    def disk_gb(self, repo):
        return 1.0

    def count_tokens(self, repo, text):
        return len(text) // 2

    def set_size(self, repo):
        return 16

    def validate(self, questions):
        pass


lm.make_local = lambda settings, config: LocalModels(HangBackend(), idle_unload_s=600)

if __name__ == "__main__":
    from app.__main__ import main

    sys.argv = ["python -m app", *sys.argv[1:]]
    main()
