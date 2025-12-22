# ==============================================================
# Makefile — NL2SQL Copilot
# Practical, low-drift, but not underpowered.
# ==============================================================
SHELL := /bin/bash
.ONESHELL:
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := help

# ---------- Config ----------
VENV_DIR ?= .venv

PY      ?= $(if $(wildcard $(VENV_DIR)/bin/python),$(VENV_DIR)/bin/python,python3)
PIP     ?= $(if $(wildcard $(VENV_DIR)/bin/pip),$(VENV_DIR)/bin/pip,pip)
UVICORN ?= $(if $(wildcard $(VENV_DIR)/bin/uvicorn),$(VENV_DIR)/bin/uvicorn,uvicorn)
RUFF    ?= $(if $(wildcard $(VENV_DIR)/bin/ruff),$(VENV_DIR)/bin/ruff,ruff)
MYPY    ?= $(if $(wildcard $(VENV_DIR)/bin/mypy),$(VENV_DIR)/bin/mypy,mypy)
PYTEST  ?= $(if $(wildcard $(VENV_DIR)/bin/pytest),$(VENV_DIR)/bin/pytest,pytest)

APP_HOST ?= 127.0.0.1
APP_PORT ?= 8000

INFRA_COMPOSE ?= infra/docker-compose.yml
PROM_CONFIG   ?= infra/prometheus/prometheus.yml
PROM_RULES    ?= infra/prometheus/rules.yml

# API key used by scripts/smoke_*. Default aligns with infra docker-compose env.
API_KEY ?= dev-key

# ---------- Help ----------
.PHONY: help
help: ## Show available targets
	@awk 'BEGIN {FS = ":.*##"} /^[a-zA-Z0-9_.-]+:.*##/ {printf "\033[36m%-24s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST) | sort

# ---------- Virtualenv / deps ----------
.PHONY: venv
venv: ## Create local venv at .venv
	python3 -m venv $(VENV_DIR)
	$(PY) -m pip install --upgrade pip

.PHONY: install
install: ## Install project dependencies into the venv
	$(PIP) install -r requirements.txt

.PHONY: install-dev
install-dev: ## Install dev dependencies (if you have a requirements-dev.txt)
	@if [[ -f requirements-dev.txt ]]; then \
		$(PIP) install -r requirements-dev.txt; \
	else \
		echo "requirements-dev.txt not found; skipping."; \
	fi

# ---------- Quality ----------
.PHONY: format
format: ## Format code (ruff format)
	$(RUFF) format .

.PHONY: lint
lint: ## Lint (ruff)
	$(RUFF) check .

.PHONY: lint-fix
lint-fix: ## Lint and auto-fix (ruff --fix)
	$(RUFF) check . --fix

.PHONY: typecheck
typecheck: ## Type-check (mypy)
	$(MYPY) .

.PHONY: qa
qa: format lint typecheck ## Run format + lint + typecheck

# ---------- Tests ----------
.PHONY: test
test: ## Run unit tests
	PYTHONPATH=$$PWD $(PYTEST) -q

.PHONY: test-all
test-all: ## Run full test suite (unit + extras)
	PYTHONPATH=$$PWD $(PYTEST)

# ---------- App (local) ----------
.PHONY: run
run: ## Run API locally on $(APP_HOST):$(APP_PORT)
	$(UVICORN) app.main:application --host $(APP_HOST) --port $(APP_PORT)

.PHONY: run-reload
run-reload: ## Run API locally with auto-reload
	$(UVICORN) app.main:application --reload --host $(APP_HOST) --port $(APP_PORT)

# ---------- Prometheus ----------
.PHONY: prom-check
prom-check: ## Validate Prometheus config/rules (local promtool or Docker fallback)
	@if command -v promtool >/dev/null 2>&1; then \
		echo "Running promtool locally..."; \
		echo "Checking $(PROM_RULES)"; \
		promtool check rules $(PROM_RULES); \
		echo; \
		echo "Checking $(PROM_CONFIG)"; \
		promtool check config $(PROM_CONFIG); \
	else \
		echo "promtool not found; using Docker image prom/prometheus for checks..."; \
		docker run --rm -v "$$PWD:/work" -w /work prom/prometheus:latest \
			promtool check rules $(PROM_RULES); \
		docker run --rm -v "$$PWD:/work" -w /work prom/prometheus:latest \
			promtool check config $(PROM_CONFIG); \
	fi

# ---------- Infra (Docker Compose) ----------
.PHONY: infra-up
infra-up: ## Start infra stack (Prometheus/Grafana/Alertmanager + nl2sql)
	docker compose -f $(INFRA_COMPOSE) up -d --build

.PHONY: infra-down
infra-down: ## Stop infra stack
	docker compose -f $(INFRA_COMPOSE) down

.PHONY: infra-restart
infra-restart: ## Restart infra stack
	docker compose -f $(INFRA_COMPOSE) down
	docker compose -f $(INFRA_COMPOSE) up -d --build

.PHONY: infra-ps
infra-ps: ## Show running containers for the infra stack
	docker compose -f $(INFRA_COMPOSE) ps

.PHONY: infra-logs
infra-logs: ## Tail infra logs
	docker compose -f $(INFRA_COMPOSE) logs -f --tail=200

# ---------- Smoke (system + metrics) ----------
.PHONY: smoke
smoke: ## Run smoke tests (system + Prometheus metrics validation)
	API_KEY="$(API_KEY)" ./scripts/smoke_metrics.sh

.PHONY: demo
demo: ## Full demo: prom-check -> infra-up -> smoke
	$(MAKE) prom-check
	$(MAKE) infra-up
	$(MAKE) smoke
	@echo
	@echo "Done."
	@echo "API:        http://$(APP_HOST):$(APP_PORT)/docs"
	@echo "Prometheus: http://$(APP_HOST):9090"
	@echo "Grafana:    http://$(APP_HOST):3000"

# ---------- Cleanup ----------
.PHONY: clean
clean: ## Remove python cache artifacts
	rm -rf .pytest_cache .mypy_cache .ruff_cache
	find . -type d -name "__pycache__" -print0 | xargs -0 rm -rf

.PHONY: clean-docker
clean-docker: ## Stop infra and remove volumes (careful)
	docker compose -f $(INFRA_COMPOSE) down -v

# ---------- Convenience ----------
.PHONY: curl-health
curl-health: ## Hit /healthz on the local API
	curl -fsS "http://$(APP_HOST):$(APP_PORT)/healthz" && echo

.PHONY: curl-metrics
curl-metrics: ## Hit /metrics on the local API
	curl -fsS "http://$(APP_HOST):$(APP_PORT)/metrics" | head -n 30
