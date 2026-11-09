"""config.yaml → engine objects, each with its availability (docs/DESIGN.md §7.6, §17.2)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import yaml

from app.catalog import Catalog
from app.engines import KINDS, PLANNED
from app.engines.base import BaseEngine
from app.schemas import AppConfig, BudgetConfig, EngineSpec
from app.settings import ROOT, Settings


@dataclass
class EngineContext:
    settings: Settings
    catalog: Catalog
    budget: BudgetConfig
    local: Any = None  # app.local_models.LocalModels, shared by the UI and the compat server


class PlannedEngine(BaseEngine):
    """A configured kind that a later increment implements: listed, never called."""

    def __init__(self, spec: EngineSpec, ctx: Any, increment: str | None):
        super().__init__(spec, ctx)
        self.kind = spec.kind  # type: ignore[misc]
        self.increment = increment

    def available(self) -> tuple[bool, str]:
        if not self.spec.enabled:
            return False, "disabled in config.yaml"
        if self.increment:
            return False, f"engine kind {self.spec.kind} arrives in {self.increment}"
        return False, f"unknown engine kind {self.spec.kind!r}"


def load_config(path: Path) -> tuple[AppConfig, Path]:
    """config.yaml, else config.example.yaml (a fresh checkout still starts)."""
    source = path if path.is_file() else ROOT / "config.example.yaml"
    data = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
    return AppConfig.model_validate(data), source


class Registry:
    def __init__(self, settings: Settings, catalog: Catalog, config: AppConfig | None = None,
                 transport: httpx.AsyncBaseTransport | None = None, local: Any = None):
        if config is None:
            config, self.config_source = load_config(settings.config_path)
        else:
            self.config_source = None
        self.config = config
        self.ctx = EngineContext(settings=settings, catalog=catalog, budget=config.budget, local=local)
        self.engines: dict[str, BaseEngine] = {}
        for spec in config.engines:
            if spec.id in self.engines:
                raise ValueError(f"duplicate engine id {spec.id!r} in config")
            cls = KINDS.get(spec.kind)
            self.engines[spec.id] = (
                cls(spec, self.ctx, transport) if cls else PlannedEngine(spec, self.ctx, PLANNED.get(spec.kind))
            )

    def get(self, engine_id: str) -> BaseEngine:
        return self.engines[engine_id]

    def describe(self) -> list[dict[str, Any]]:
        return [e.describe() for e in self.engines.values()]

    async def aclose(self) -> None:
        for e in self.engines.values():
            await e.aclose()
