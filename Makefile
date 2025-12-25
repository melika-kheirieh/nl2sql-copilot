# ==============================================================
# Makefile — NL2SQL Copilot (SAFE / Docker-first Demo)
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
STREAMLIT ?= $(if $(wildcard $(VENV_DIR)/bin/streamlit),$(VENV_DIR)/bin/streamlit,streamlit)

APP_HOST ?= 127.0.0.1
APP_PORT ?= 8000
DEV_PORT ?= 8001

PORT ?= 8501

API_BASE ?= http://$(APP_HOST):$(APP_PORT)
API_KEY  ?= dev-key
export API_KEY

PROMETHEUS_URL ?= http://127.0.0.1:9090
GRAFANA_URL    ?= http://127.0.0.1:3000

INFRA_COMPOSE ?= $(if $(wildcard infra/docker-compose.yml),infra/docker-compose.yml,docker-compose.yml)

PROM_CONFIG ?= prometheus.yml
RULES_FILE  ?= rules.yml

# ---------- Help ----------
.PHONY: help
help: ## Show available targets
	@grep -E '^[a-zA-Z0-9_.-]+:.*## ' $(MAKEFILE_LIST) \
	| awk 'BEGIN {FS = ":.*## "}; {printf "  \033[36m%-24s\033[0m %s\n", $$1, $$2}'

# ---------- Python env ----------
.PHONY: venv
venv: ## Create venv
	python3 -m venv $(VENV_DIR)

.PHONY: install
install: ## Install project dependencies
	$(PIP) install -r requirements.txt

# ---------- Quality ----------
.PHONY: format
format: ## Format code
	$(RUFF) format .

.PHONY: lint
lint: ## Lint code
	$(RUFF) check .

.PHONY: typecheck
typecheck: ## Typecheck
	$(MYPY) . --exclude '^data/'

.PHONY: test
test: ## Run tests
	PYTHONPATH=$$PWD pytest -qq

.PHONY: cov
cov: ## Run tests with coverage (HTML + terminal)
	PYTHONPATH=$$PWD pytest -qq --cov=app --cov-report=term-missing --cov-report=html

.PHONY: metrics-check
metrics-check: ## Verify Prometheus rules + Grafana dashboards match defined metrics
	$(PY) scripts/verify_metrics_wiring.py

.PHONY: qa
qa: ## format + lint + typecheck + test + metrics-check
	$(MAKE) format lint typecheck test metrics-check

.PHONY: bench-ui
bench-ui: ## Run Streamlit benchmark dashboard
	@set -e; \
	if [ -f ui/benchmark_app.py ]; then \
		PYTHONPATH=$$PWD $(STREAMLIT) run ui/benchmark_app.py --server.port $(PORT); \
	elif [ -f benchmarks/bench_ui.py ]; then \
		PYTHONPATH=$$PWD $(STREAMLIT) run benchmarks/bench_ui.py --server.port $(PORT); \
	else \
		echo "❌ No Streamlit entrypoint found. Expected ui/benchmark_app.py"; \
		exit 1; \
	fi

# ---------- Benchmarks / Evaluation ----------
DEMO_DB ?= $(if $(wildcard data/demo.db),data/demo.db,/tmp/nl2sql_dbs/smoke_demo.sqlite)

.PHONY: eval-smoke
eval-smoke: ## Run direct pipeline smoke eval on demo DB (no Spider needed)
	PYTHONPATH=$$PWD \
	PYTEST_CURRENT_TEST=1 \
	python benchmarks/eval_lite.py --db-path $(DEMO_DB)

SPIDER_SPLIT ?= dev
EVAL_PRO_LIMIT ?= 200
EVAL_PRO_SMOKE_LIMIT ?= 20

.PHONY: eval-pro-smoke
eval-pro-smoke: ## Run Spider eval-pro (smoke preset)
	PYTHONPATH=$$PWD \
	python benchmarks/eval_spider_pro.py --spider --split $(SPIDER_SPLIT) --limit $(EVAL_PRO_SMOKE_LIMIT)

.PHONY: eval-pro
eval-pro: ## Run Spider eval-pro (default preset)
	PYTHONPATH=$$PWD \
	python benchmarks/eval_spider_pro.py --spider --split $(SPIDER_SPLIT) --limit $(EVAL_PRO_LIMIT)

.PHONY: plot-pro
plot-pro: ## Plot latest Spider eval-pro results (PNG artifacts)
	PYTHONPATH=$$PWD \
	python benchmarks/plot_results.py

# ---------- DEMO (Docker-first, SAFE) ----------
.PHONY: demo-up
demo-up: ## Start DEMO stack (Docker: nl2sql + Prometheus + Grafana)
	$(MAKE) infra-up
	$(MAKE) prom-ready
	$(MAKE) grafana-ready
	@echo "✅ Demo stack is up"
	@echo "API:        http://127.0.0.1:8000"
	@echo "Grafana:    http://127.0.0.1:3000"
	@echo "Prometheus:http://127.0.0.1:9090"

.PHONY: demo-down
demo-down: ## Stop demo stack (including traffic)
	$(MAKE) demo-traffic-down
	$(MAKE) infra-down

.PHONY: demo-cache-showcase
demo-cache-showcase: ## Generate mixed traffic with repeats to show cache hits in Grafana
	@API_BASE=$${API_BASE:-http://127.0.0.1:8000} \
	API_KEY=$${API_KEY:-dev-key} \
	DB_PATH=$${DB_PATH:-/tmp/nl2sql_dbs/smoke_demo.sqlite} \
	bash scripts/demo_cache_showcase.sh

.PHONY: demo-zero demo-screenshot

# Reset infra + temp demo DBs to get a "cold start" screenshot
demo-zero:
	docker rm -f nl2sql-demo-traffic >/dev/null 2>&1 || true
	docker compose -f infra/docker-compose.yml down -v --remove-orphans || true
	rm -rf /tmp/nl2sql_dbs || true

# Reproducible screenshot workload (cold start -> traffic -> cache hits)
demo-screenshot: demo-zero infra-up
	# wait until API is actually responding (avoid "connection reset by peer")
	@until curl -fsS http://127.0.0.1:8000/healthz >/dev/null; do sleep 0.5; done
	API_BASE="http://127.0.0.1:8000" API_KEY="dev-key" $(MAKE) demo-smoke
	API_BASE="http://127.0.0.1:8000" API_KEY="dev-key" \
	DB_PATH="/tmp/nl2sql_dbs/smoke_demo.sqlite" PROM_BASE="http://127.0.0.1:9090" \
	python3 scripts/demo_cache_showcase.py

# ---------- Local DEV (explicit, separate) ----------
.PHONY: dev-up
dev-up: ## Start API locally (DEV ONLY, separate from demo)
	$(UVICORN) app.main:application --host 127.0.0.1 --port $(DEV_PORT)

# ---------- Infra (Docker Compose) ----------
.PHONY: infra-up
infra-up: ## Start infra stack (no build)
	docker compose -f $(INFRA_COMPOSE) up -d

.PHONY: infra-up-build
infra-up-build: ## Start infra stack with build
	docker compose -f $(INFRA_COMPOSE) up -d --build

.PHONY: infra-down
infra-down: ## Stop infra stack
	docker compose -f $(INFRA_COMPOSE) down

.PHONY: infra-restart
infra-restart: ## Restart infra stack (no build)
	docker compose -f $(INFRA_COMPOSE) down
	docker compose -f $(INFRA_COMPOSE) up -d

.PHONY: infra-ps
infra-ps: ## Show infra containers
	docker compose -f $(INFRA_COMPOSE) ps

# ---------- Demo traffic ----------
.PHONY: demo-traffic-up
demo-traffic-up: ## Start demo traffic warmer (Docker-only)
	docker compose -f $(INFRA_COMPOSE) --profile traffic up -d demo-traffic

.PHONY: demo-traffic-down
demo-traffic-down: ## Stop demo traffic warmer
	docker compose -f $(INFRA_COMPOSE) --profile traffic stop demo-traffic || true

# ---------- Health / Metrics ----------
.PHONY: curl-health
curl-health: ## Check API health (Docker demo)
	curl -fsS $(API_BASE)/healthz >/dev/null && echo "✅ API healthy"

.PHONY: curl-metrics
curl-metrics: ## Peek metrics
	curl -fsS $(API_BASE)/metrics | head -n 40

.PHONY: prom-ready
prom-ready: ## Check Prometheus readiness
	curl -fsS $(PROMETHEUS_URL)/-/ready >/dev/null

.PHONY: grafana-ready
grafana-ready: ## Check Grafana readiness
	curl -fsS $(GRAFANA_URL)/api/health >/dev/null

# ---------- Smoke / Metrics ----------
.PHONY: demo-smoke
demo-smoke: ## Run API smoke (Docker demo)
	API_BASE="$(API_BASE)" API_KEY="$(API_KEY)" \
	$(PY) scripts/smoke_api.py

.PHONY: demo-metrics
demo-metrics: prom-ready ## Validate Prometheus signals
	curl -fsS "$(PROMETHEUS_URL)/api/v1/query" \
		--data-urlencode 'query=cache_events_total' | head -c 2000; echo

# ---------- Clean ----------
.PHONY: clean-docker
clean-docker: ## Remove docker artifacts (careful)
	docker system prune -f

# ---------- Deprecated legacy aliases ----------
.PHONY: demo prom-up demos demo-ui-up evaluation reasoning
demo prom-up demos demo-ui-up evaluation reasoning:
	@echo "Deprecated target '$@'. Use 'make help' for canonical targets." 1>&2
	@exit 2
