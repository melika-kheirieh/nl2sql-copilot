# ==============================================================
# Makefile — NL2SQL Copilot
# ==============================================================

# ---------- Config ----------
VENV_DIR   ?= .venv
PY         ?= $(if $(wildcard $(VENV_DIR)/bin/python),$(VENV_DIR)/bin/python,python3)
PIP        ?= $(if $(wildcard $(VENV_DIR)/bin/pip),$(VENV_DIR)/bin/pip,pip)
UVICORN    ?= $(if $(wildcard $(VENV_DIR)/bin/uvicorn),$(VENV_DIR)/bin/uvicorn,uvicorn)
RUFF       ?= $(if $(wildcard $(VENV_DIR)/bin/ruff),$(VENV_DIR)/bin/ruff,ruff)
MYPY       ?= $(if $(wildcard $(VENV_DIR)/bin/mypy),$(VENV_DIR)/bin/mypy,mypy)
PYTEST     ?= $(if $(wildcard $(VENV_DIR)/bin/pytest),$(VENV_DIR)/bin/pytest,pytest)

DOCKER_IMG ?= nl2sql-copilot
PORT       ?= 8000

.DEFAULT_GOAL := help

# ==============================================================
# Meta
# ==============================================================
.PHONY: help
help: ## Show this help
	@printf "\n\033[1mAvailable targets:\033[0m\n"
	@awk 'BEGIN {FS = ":.*##"} /^[[:alnum:]_.-]+:.*##/ {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

# ==============================================================
# Setup
# ==============================================================
.PHONY: venv
venv: ## Create virtual environment in .venv/
	python3 -m venv $(VENV_DIR)
	$(PIP) install --upgrade pip wheel

.PHONY: install
install: ## Install runtime dependencies inside venv
	$(PIP) install -r requirements.txt

.PHONY: dev-install
dev-install: ## Install dev tools (ruff, mypy, pytest, coverage, uvicorn, etc.)
	$(PIP) install -U pip wheel
	$(PIP) install ruff mypy pytest pytest-cov uvicorn pre-commit

.PHONY: bootstrap
bootstrap: venv dev-install ## Create venv and install dev tools

# ==============================================================
# Quality — Read-only (CI)
# ==============================================================
.PHONY: fmt-check
fmt-check: ## Verify formatting without modifying files
	$(RUFF) format . --check

.PHONY: lint
lint: ## Run linting
	$(RUFF) check .

.PHONY: typecheck
typecheck: ## Run type checking only
	$(MYPY) . --ignore-missing-imports --explicit-package-bases

# ==============================================================
# Quality — Write mode (local dev)
# ==============================================================
.PHONY: format
format: ## Auto-format & fix with ruff
	$(RUFF) format .
	$(RUFF) check . --fix

# ==============================================================
# Tests
# ==============================================================
.PHONY: test
test: ## Run fast test suite (exclude slow)
	PYTHONPATH=$$PWD $(PYTEST) -q -m "not slow"

.PHONY: test-all
test-all: ## Run full test suite including slow tests
	PYTHONPATH=$$PWD $(PYTEST) -q

.PHONY: cov
cov: ## Run tests with coverage
	PYTHONPATH=$$PWD $(PYTEST) --cov=nl2sql --cov-report=term-missing

# ==============================================================
# Unified gate for CI
# ==============================================================
.PHONY: check
check: ## Run format check, lint, typecheck, and fast tests
	$(MAKE) fmt-check
	$(MAKE) lint
	$(MAKE) typecheck
	$(MAKE) test

# ==============================================================
# Pre-commit
# ==============================================================
.PHONY: precommit
precommit: ## Run all pre-commit hooks on all files
	pre-commit run --all-files

# ==============================================================
# Run app
# ==============================================================
.PHONY: run
run: ## Run FastAPI app (reload mode)
	$(UVICORN) app.main:app --reload --host 0.0.0.0 --port $(PORT)

# ==============================================================
# Benchmarks
# ==============================================================
.PHONY: bench
bench: ## Run benchmark suite (DummyLLM fallback)
	$(PY) -m benchmarks.run

# ==============================================================
# Docker
# ==============================================================
.PHONY: docker-build
docker-build: ## Build Docker image
	docker build -t $(DOCKER_IMG) .

.PHONY: docker-run
docker-run: ## Run Docker container on port $(PORT)
	docker run --rm -p $(PORT):8000 $(DOCKER_IMG)

# ==============================================================
# Clean
# ==============================================================
.PHONY: clean
clean: ## Remove Python caches
	rm -rf __pycache__ .pytest_cache .mypy_cache .ruff_cache

.PHONY: clean-all
clean-all: clean ## Remove build artifacts and coverage
	rm -rf dist build .coverage *.egg-info

# ==============================================================
INFRA_COMPOSE ?= infra/docker-compose.yml

# Observability Stack
# ==============================================================
.PHONY: obs-up obs-down obs-logs prom-up prom-check smoke grafana-import

prom-up: ## Bring up Prometheus + Grafana via Docker Compose
	docker compose -f $(INFRA_COMPOSE) up -d

prom-check: ## Validate Prometheus configs (local or Docker fallback)
	@if command -v promtool >/dev/null 2>&1; then \
		echo "🔍 Running promtool locally..."; \
		promtool check rules infra/prometheus/rules.yml && promtool check config infra/prometheus/prometheus.yml; \
	else \
		echo "⚠️ promtool not found, running via Docker..."; \
		docker run --rm -v $$(pwd)/infra/prometheus:/etc/prometheus prom/prometheus \
			promtool check rules /etc/infra/prometheus/rules.yml && \
		docker run --rm -v $$(pwd)/infra/prometheus:/etc/prometheus prom/prometheus \
			promtool check config /etc/infra/prometheus/prometheus.yml; \
	fi

smoke: ## Generate sample traffic and print key metrics snapshot
	./scripts/smoke_metrics.sh

obs-up: ## Start observability stack and verify readiness
	@set -e; \
	$(MAKE) prom-up; \
	echo "⏳ Waiting for Prometheus (http://localhost:9090)..."; \
	for i in $$(seq 1 30); do \
		if curl -fsS http://localhost:9090/-/ready >/dev/null 2>&1; then echo "✅ Prometheus is ready"; break; fi; \
		if nc -z localhost 9090 >/dev/null 2>&1; then echo "✅ Prometheus port is open (assuming ready)"; break; fi; \
		sleep 3; if [ $$i -eq 30 ]; then echo "❌ Prometheus did not become ready in time"; exit 1; fi; \
	done; \
	echo "⏳ Waiting for Grafana (http://localhost:3000)..."; \
	for i in $$(seq 1 30); do \
		code=$$(curl -s -o /dev/null -w "%{http_code}" http://localhost:3000/login || true); \
		if [ "$$code" = "200" ] || [ "$$code" = "302" ]; then echo "✅ Grafana is up"; break; fi; \
		sleep 3; if [ $$i -eq 30 ]; then echo "❌ Grafana did not become ready in time"; exit 1; fi; \
	done; \
	echo "🚀 Running smoke traffic..."; \
	$(MAKE) smoke; \
	echo "🎉 Observability stack is live → Prometheus: http://localhost:9090 , Grafana: http://localhost:3000"; \
	$(MAKE) grafana-import

obs-down: ## Tear down the observability stack
	docker compose -f $(INFRA_COMPOSE) down

obs-logs: ## Tail logs of both services
	docker compose -f $(INFRA_COMPOSE) logs -f

grafana-import: ## Import Grafana dashboard via HTTP API
	@set -e; \
	echo "⏳ Waiting for Grafana API to become ready..."; \
	for i in $$(seq 1 30); do \
		code=$$(curl -s -o /dev/null -w "%{http_code}" http://localhost:3000/api/health || true); \
		if [ "$$code" = "200" ]; then echo "✅ Grafana API is ready"; break; fi; \
		sleep 3; if [ $$i -eq 30 ]; then echo "❌ Grafana API did not become ready in time"; exit 1; fi; \
	done; \
	echo "📦 Importing dashboard..."; \
	curl -s -X POST http://admin:admin@localhost:3000/api/dashboards/db \
		-H "Content-Type: application/json" \
		-d "{\"dashboard\": $$(cat infra/prometheus/grafana_dashboard.json), \"overwrite\": true, \"folderId\": 0}" \
		| jq -r '.status' || true; \
	echo "🎉 Dashboard imported → http://localhost:3000/dashboards"
