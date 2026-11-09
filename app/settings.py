"""Settings from .env (docs/DESIGN.md §17.1). Keys live only here and never reach records, logs or API answers."""

from __future__ import annotations

import ipaddress
from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[1]


def is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", env_file_encoding="utf-8", extra="ignore")

    openrouter_api_key: SecretStr | None = None
    systemone_token: SecretStr | None = None
    app_host: str = "127.0.0.1"
    app_port: int = 8790
    compat_host: str = "127.0.0.1"
    compat_port: int = 8791
    hf_home: str | None = None
    daily_budget_usd: float = 1.0
    ffmpeg_bin: str = "ffmpeg"
    data_dir: Path = ROOT
    config_path: Path = ROOT / "config.yaml"

    @field_validator("openrouter_api_key", "systemone_token", "hf_home", mode="before")
    @classmethod
    def _blank_is_none(cls, v):
        return None if isinstance(v, str) and not v.strip() else v

    @field_validator("data_dir", "config_path", mode="after")
    @classmethod
    def _relative_to_root(cls, v: Path) -> Path:
        return v if v.is_absolute() else (ROOT / v).resolve()

    @model_validator(mode="after")
    def _ui_on_loopback(self):
        # DESIGN §3, §18: the UI has no auth, so it never listens beyond loopback.
        if not is_loopback(self.app_host):
            raise ValueError(f"APP_HOST must be a loopback address, not {self.app_host!r} (DESIGN §18)")
        return self

    @property
    def openrouter_key(self) -> str | None:
        return self.openrouter_api_key.get_secret_value() if self.openrouter_api_key else None

    @property
    def runs_dir(self) -> Path:
        return self.data_dir / "runs"

    @property
    def probes_dir(self) -> Path:
        return self.data_dir / "probes"

    def ensure_dirs(self) -> None:
        for d in ("runs", "datasets", "probes", "reports", "media"):
            (self.data_dir / d).mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
