# Decision Models Playground. Every target runs through the project's .venv; the pyenv interpreter is never
# installed into (docs/DESIGN.md §20, §22).

PYENV_PYTHON ?= $(HOME)/.pyenv/versions/3.14.8/bin/python
VENV         := .venv
PY           := $(VENV)/bin/python
ARCH         := $(shell uname -m)

PORT         ?= $(shell grep -E '^APP_PORT=' .env 2>/dev/null | cut -d= -f2)
PORT         := $(if $(strip $(PORT)),$(strip $(PORT)),8790)
LOG          ?= /tmp/decision-playground.log

.DEFAULT_GOAL := help
.PHONY: help setup models run run-bg stop check test clean-runs spike spike-openrouter spike-mlx spike-audio clean-venv

help:
	@echo "make setup            - create .venv, install requirements (+ requirements-mlx.txt on Apple Silicon)"
	@echo "make models           - download the enabled MLX models into HF_HOME"
	@echo "make run              - UI and /api on http://127.0.0.1:$(PORT) (foreground)"
	@echo "make run-bg / stop    - the same in the background (log $(LOG)) / stop it"
	@echo "make check            - availability of every engine, catalog cache, today's budget"
	@echo "make test             - the test suite (no network, no keys)"
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

run-bg: $(PY)
	@$(PY) -m app > $(LOG) 2>&1 & echo "==> started, log: $(LOG)"

stop:
	@pid=$$(lsof -ti tcp:$(PORT) -sTCP:LISTEN 2>/dev/null); \
	if [ -n "$$pid" ]; then kill $$pid && echo "==> stopped (PID $$pid)"; else echo "==> nothing listens on $(PORT)"; fi

check: $(PY)
	@$(PY) -m app.check

test: $(PY)
	@$(PY) -m pytest

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
