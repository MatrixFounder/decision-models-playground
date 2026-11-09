"""Settings from .env (docs/DESIGN.md §17.1). Keys live only here and never reach records, logs or API answers."""

from __future__ import annotations

import ipaddress
import math
import os
from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[1]
MIN_TOKEN = 32
UI_HOSTS = ("127.0.0.1", "localhost")


def is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


OPENROUTER_BASE = "https://openrouter.ai"


def check_base_url(url: str, name: str) -> str:
    """An HTTP root the OpenRouter key is sent to: https, or http only to this Mac (a local gateway); no credentials,
    query or fragment in it. Returned without a trailing slash: paths are appended to it."""
    from urllib.parse import urlsplit

    raw = (url or "").strip()
    parts = urlsplit(raw)
    if parts.scheme not in ("https", "http") or not parts.hostname:
        raise ValueError(f"{name} is an http(s) URL such as {OPENROUTER_BASE}, not {raw!r}")
    if parts.scheme == "http" and not is_loopback(parts.hostname):
        raise ValueError(f"{name}: the OpenRouter key goes to this address — https, or http only to 127.0.0.1 or "
                         f"localhost (a local gateway)")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError(f"{name}: no user, password, query or fragment in the URL")
    return f"{parts.scheme}://{parts.netloc}{parts.path.rstrip('/')}"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", env_file_encoding="utf-8", extra="ignore")

    openrouter_api_key: SecretStr | None = None
    openrouter_base_url: str = OPENROUTER_BASE  # a proxy or a gateway (LiteLLM…) instead of OpenRouter itself
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

    @field_validator("openrouter_base_url", mode="before")
    @classmethod
    def _base_url(cls, v):
        return check_base_url(v if isinstance(v, str) and v.strip() else OPENROUTER_BASE, "OPENROUTER_BASE_URL")

    @field_validator("data_dir", "config_path", mode="after")
    @classmethod
    def _relative_to_root(cls, v: Path) -> Path:
        return v if v.is_absolute() else (ROOT / v).resolve()

    @model_validator(mode="after")
    def _hosts(self):
        # DESIGN §3, §18: the UI has no auth, so it never listens beyond loopback. Only the two names the Host and
        # Origin guards of app.main know: `::1` or 127.0.0.2 would pass a loopback test and then refuse every request.
        if self.app_host not in UI_HOSTS:
            raise ValueError(f"APP_HOST must be one of {', '.join(UI_HOSTS)}, not {self.app_host!r} (DESIGN §18)")
        if not math.isfinite(self.daily_budget_usd) or self.daily_budget_usd < 0:
            raise ValueError("DAILY_BUDGET_USD must be a finite number ≥ 0")
        # DESIGN §9.2: the compat server leaves loopback only behind a token of at least 32 characters.
        token = self.systemone_token.get_secret_value() if self.systemone_token else None
        if token is not None and len(token) < MIN_TOKEN:
            raise ValueError(f"SYSTEMONE_TOKEN must be at least {MIN_TOKEN} characters (DESIGN §9.2)")
        if token is not None and (len(set(token)) < 10 or token != token.strip()):
            # 32 x "a" is long and worthless; `openssl rand -hex 24` gives 48 characters of 16 kinds (review P2)
            raise ValueError("SYSTEMONE_TOKEN is too uniform: use `openssl rand -hex 24`")
        if ":" in self.compat_host:
            # Host checks compare names; a bracketed IPv6 literal never matches them (review round 2)
            raise ValueError("COMPAT_HOST is an IPv4 address or a host name (0.0.0.0 for every interface)")
        if not is_loopback(self.compat_host) and token is None:
            raise ValueError(f"COMPAT_HOST={self.compat_host} needs SYSTEMONE_TOKEN (DESIGN §9.2)")
        return self

    @property
    def compat_token(self) -> str | None:
        return self.systemone_token.get_secret_value() if self.systemone_token else None

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
    s = Settings()
    if s.hf_home:
        # huggingface_hub reads HF_HOME from the environment when it is first imported; .env is not the environment
        os.environ["HF_HOME"] = s.hf_home
    return s
