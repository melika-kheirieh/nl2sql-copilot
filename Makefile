# ==============================================================
# Makefile — NL2SQL Copilot
# Practical, low-drift, but not underpowered
# ==============================================================

SHELL := /bin/bash
.DEFAULT_GOAL := help

# ---------- Config ----------
VENV_DIR   ?= .venv
PY         ?= $(if $(wildcard $(VENV_DIR)/bin/python),$(VENV_DIR)/bin/python,python3)
PIP        ?= $(if $(wildcard $(VENV_DIR)/bin/pip),$(VENV_DIR)/bin/pip,pip)
UVICORN    ?= $(if $(wildcard $(VENV_DIR)/bin/uvicorn),$(VENV_DIR)/bin/uvicorn,uvicorn)
RUFF       ?= $(if $(wildcard $(VENV_DIR)/bin/ruff),$(VENV_DIR)/bin/ruff,ruff)
MYPY       ?= $(if $(wildcard $(VENV_DIR)/bin/mypy),$(VENV_DIR)/bin/mypy,mypy)
PYTEST     ?= $(if $(wildcard $(VENV_DIR)/bin/pytest),$(VENV_DIR)/bin/pytest,pytest)

HOST       ?= 0.0.0.0
PORT       ?= 8000

# Auto-pick app module (adjust if your app exports a different name than "app")
APP_MODULE ?= $(if $(wildcard main.py),main:app,app.main:app)

# What we typecheck (keep it tight; avoid vendor/data folders)
MYPY_TARGETS ?= app nl2sql adapters tests scripts
MYPY_FLAGS   ?= --pretty

# Infra (Docker Compose)
INFRA_COMPOSE ?= infra/docker-compose.yml

# ---------- Help ----------
.PHONY: help
help: ## Show available targets
	@awk 'BEGIN {FS = ":.*##"; printf "\nTargets:\n"} \
	/^[a-zA-Z0-9_.-]+:.*##/ {printf "  %-18s %s\n", $$1, $$2} \
	END {printf "\n"}' $(MAKEFILE_LIST)

# ---------- Setup ----------
.PHONY: venv install
venv: ## Create virtualenv if missing
	@test -d $(VENV_DIR) || python3 -m venv $(VENV_DIR)

install: ## Install Python dependencies
	$(MAKE) venv
	$(PIP) install -r requirements.txt

# ---------- Quality ----------
.PHONY: format fmt-check lint typecheck test test-all cov check

format: ## Auto-format & fix (ruff)
	$(RUFF) format .
	$(RUFF) check . --fix

fmt-check: ## Check formatting only (CI-friendly)
	$(RUFF) format . --check
	$(RUFF) check .

lint: ## Lint only (ruff)
	$(RUFF) check .

typecheck: ## Type-check (mypy) — scoped (no data/ vendor)
	$(MYPY) $(MYPY_FLAGS) $(MYPY_TARGETS)

test: ## Run fast test suite
	PYTHONPATH=$$PWD $(PYTEST) -q

test-all: ## Run full test suite
	PYTHONPATH=$$PWD $(PYTEST)

cov: ## Coverage (requires pytest-cov)
	PYTHONPATH=$$PWD $(PYTEST) --cov --cov-report=term-missing

check: ## Unified quality gate: fmt-check + typecheck + tests
	$(MAKE) fmt-check
	$(MAKE) typecheck
	$(MAKE) test

# ---------- Run ----------
.PHONY: run
run: ## Run FastAPI backend (reload)
	$(UVICORN) $(APP_MODULE) --reload --host $(HOST) --port $(PORT)

# ---------- Benchmarks ----------
.PHONY: bench-ui
bench-ui: ## Run Streamlit benchmark dashboard
	streamlit run ui/benchmark_app.py

# ---------- Infra / Observability ----------
.PHONY: infra-up infra-down infra-restart infra-ps infra-logs infra-reset
infra-up: ## Bring up infra stack (compose)
	docker compose -f $(INFRA_COMPOSE) up -d --build

infra-down: ## Tear down infra stack
	docker compose -f $(INFRA_COMPOSE) down

infra-restart: ## Restart infra stack
	$(MAKE) infra-down
	$(MAKE) infra-up

infra-ps: ## Show infra stack status
	docker compose -f $(INFRA_COMPOSE) ps

infra-logs: ## Tail infra stack logs
	docker compose -f $(INFRA_COMPOSE) logs -f

infra-reset: ## Hard reset infra containers (useful when container_name conflicts happen)
	@set -e; \
	echo "Resetting infra stack..."; \
	docker compose -f $(INFRA_COMPOSE) down --remove-orphans || true; \
	docker rm -f nl2sql nl2sql-prom nl2sql-grafana nl2sql-alertmanager nl2sql-alert-receiver 2>/dev/null || true; \
	echo "Done. Now run: make infra-up"

# ---------- Smoke (system + metrics) ----------
.PHONY: smoke
smoke: ## Run system smoke test and validate Prometheus metrics
	./scripts/smoke_metrics.sh

# ---------- Prometheus ----------
.PHONY: prom-check
prom-check: ## Validate Prometheus config/rules (local promtool or Docker fallback)
	@if command -v promtool >/dev/null 2>&1; then \
		echo "Running promtool locally..."; \
		promtool check rules infra/prometheus/rules.yml && \
		promtool check config infra/prometheus/prometheus.yml; \
	else \
		echo "promtool not found; running via Docker..."; \
		docker run --rm -v $$(pwd)/infra/prometheus:/etc/prometheus prom/prometheus \
			promtool check rules /etc/prometheus/rules.yml; \
		docker run --rm -v $$(pwd)/infra/prometheus:/etc/prometheus prom/prometheus \
			promtool check config /etc/prometheus/prometheus.yml; \
	fi

# ---------- Cleanup ----------
.PHONY: clean clean-all
clean: ## Remove Python cache/build artifacts
	@shopt -s globstar nullglob; \
	rm -rf .pytest_cache .mypy_cache .ruff_cache .coverage **/__pycache__/

clean-all: ## Clean + remove venv
	$(MAKE) clean
	rm -rf $(VENV_DIR)
