# Decision Models Playground. Every target runs through the project's .venv; the pyenv interpreter is never
# installed into (docs/DESIGN.md §20, §22).

# the owner's pyenv 3.14.8 when present, else the python3 on PATH (review P1)
PYENV_PYTHON ?= $(firstword $(wildcard $(HOME)/.pyenv/versions/3.14.8/bin/python) $(shell command -v python3))
VENV         := .venv
PY           := $(VENV)/bin/python
ARCH         := $(shell uname -m)

PORT         ?= $(shell grep -E '^APP_PORT=' .env 2>/dev/null | cut -d= -f2)
PORT         := $(if $(strip $(PORT)),$(strip $(PORT)),8790)
COMPAT_PORT  ?= $(shell grep -E '^COMPAT_PORT=' .env 2>/dev/null | cut -d= -f2)
COMPAT_PORT  := $(if $(strip $(COMPAT_PORT)),$(strip $(COMPAT_PORT)),8791)
LOG          ?= /tmp/decision-playground.log

.DEFAULT_GOAL := help
.PHONY: help setup models run run-compat run-bg stop check test smoke-local clean-runs spike spike-openrouter spike-mlx spike-audio clean-venv

help:
	@echo "make setup            - create .venv, install requirements (+ requirements-mlx.txt on Apple Silicon)"
	@echo "make models           - download the enabled MLX models into HF_HOME"
	@echo "make run              - UI and /api on http://127.0.0.1:$(PORT) plus the compat server :8791 (foreground)"
	@echo "make run-compat       - only the compat server POST /v1/systemone (for whip-insight)"
	@echo "make run-bg / stop    - the same in the background (log $(LOG)) / stop it"
	@echo "make check            - availability of every engine, catalog cache, today's budget"
	@echo "make test             - Python and frontend tests (no network, no keys, no models)"
	@echo "make smoke-local      - opt-in: the real local path (loads d1-2B and d1-Omni), log in docs/reviews/"
	@echo "make clean-runs       - delete runs/ (asks first)"
	@echo "make spike            - P0: OpenRouter + both local d1 models (docs/SPIKE.md)"
	@echo "make spike-openrouter - P0: catalog, ZDR list, Decisions calls (needs OPENROUTER_API_KEY in .env)"
	@echo "make spike-mlx        - P0: d1-2B and d1-Omni through mlx-vlm, one model per process"
	@echo "make spike-audio      - regenerate the synthetic Russian clips (macOS say + ffmpeg)"
	@echo "make clean-venv       - delete .venv"

$(PY):
	@$(PYENV_PYTHON) -m venv $(VENV)

setup: $(PY)
	@$(PY) -m pip install -q -U pip
	@$(PY) -m pip install -q -r requirements.txt
	@if [ "$$(uname -s)" = "Darwin" ] && [ "$(ARCH)" = "arm64" ]; then $(PY) -m pip install -q -r requirements-mlx.txt; fi
	@test -f .env || (cp .env.example .env && chmod 600 .env && echo "==> created .env from .env.example")
	@test -f config.yaml || (cp config.example.yaml config.yaml && echo "==> created config.yaml from config.example.yaml")
	@echo "==> setup complete"

models: $(PY)
	@$(PY) -c "from huggingface_hub import snapshot_download as d; [print(d(r)) for r in ('LiquidAI/d1-2B-MLX-8bit', 'LiquidAI/d1-Omni-2B-MLX-8bit')]"

run: $(PY)
	@$(PY) -m app

run-compat: $(PY)
	@$(PY) -m app --compat-only

run-bg: $(PY)
	@$(PY) -m app > $(LOG) 2>&1 & echo "==> started, log: $(LOG)"

stop:
	@pids=$$( (lsof -ti tcp:$(PORT) -sTCP:LISTEN; lsof -ti tcp:$(COMPAT_PORT) -sTCP:LISTEN) 2>/dev/null | sort -u); \
	if [ -n "$$pids" ]; then kill $$pids && echo "==> stopped (PID $$pids)"; else echo "==> nothing listens on $(PORT) or $(COMPAT_PORT)"; fi

check: $(PY)
	@$(PY) -m app.check

test: $(PY)
	@$(PY) -m pytest
	@if command -v node >/dev/null; then node --test --test-timeout=10000 tests/js/*.test.mjs; else echo "==> node not found: frontend tests skipped"; fi

smoke-local: $(PY)
	@$(PY) scripts/smoke_local.py

clean-runs:
	@printf "Delete runs/ with every saved run? [y/N] "; read a; [ "$$a" = "y" ] && rm -rf runs && echo "==> runs/ deleted" || echo "==> kept"

spike: spike-openrouter spike-mlx

spike-openrouter: $(PY)
	@$(PY) scripts/spike/openrouter_spike.py

spike-mlx: $(PY)
	@$(PY) scripts/spike/mlx_spike.py --engine mlx-d1-2b --repo LiquidAI/d1-2B-MLX-8bit
	@$(PY) scripts/spike/mlx_spike.py --engine mlx-d1-omni --repo LiquidAI/d1-Omni-2B-MLX-8bit --audio

spike-audio:
	@scripts/spike/make_audio.sh tests/fixtures/audio

clean-venv:
	rm -rf $(VENV)
