"""`make models`: download the `repo` of every enabled mlx_local engine into the Hugging Face cache
(docs/ARCHITECTURE.md §10.4, TASK 002 R-16.5).

An engine with `path` reads its own folder (LM Studio, `hf download --local-dir`): nothing is downloaded for it, and a
folder without config.json is reported. The engines come from CONFIG_PATH (default config.yaml, else
config.example.yaml), as the app reads them.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.local_models import local_folders, resolve_folder  # noqa: E402
from app.registry import load_config  # noqa: E402
from app.settings import Settings  # noqa: E402


def main() -> int:
    config, source = load_config(Settings().config_path)
    local_folders(config.engines)  # one repo from two sources stops here, as it stops the app
    engines = [e for e in config.engines if e.kind == "mlx_local" and e.enabled and (e.repo or e.model)]
    print(f"==> engines from {source.name}")
    missing = 0
    for e in engines:
        if e.path:
            folder = resolve_folder(e.path)
            ok = (folder / "config.json").is_file()
            missing += not ok
            print(f"    {e.id}: reads {e.path} ({'ok' if ok else 'no config.json there'})")
    repos = list(dict.fromkeys(e.repo or e.model for e in engines if not e.path))
    if repos:
        from huggingface_hub import snapshot_download

        for repo in repos:
            print(f"==> {repo}")
            print(f"    {snapshot_download(repo)}")
    elif not engines:
        print("    no enabled mlx_local engine")
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
