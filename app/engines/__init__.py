"""Engines by kind (docs/DESIGN.md §7). Kinds that arrive later are listed with the increment that brings them."""

from app.engines.base import BaseEngine
from app.engines.openrouter_decisions import OpenRouterDecisions

KINDS: dict[str, type[BaseEngine]] = {
    OpenRouterDecisions.kind: OpenRouterDecisions,
}

PLANNED: dict[str, str] = {
    "mlx_local": "P2",
    "systemone_http": "P7",
    "openrouter_llm": "P7",
}
