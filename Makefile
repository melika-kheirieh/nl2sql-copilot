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

# ---------- Meta ----------
.PHONY: help
help: ## Show this help
	@printf "\n\033[1mAvailable targets:\033[0m\n"
	@awk 'BEGIN {FS = ":.*##"} /^[[:alnum:]_.-]+:.*##/ {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

# ---------- Setup ----------
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

# ---------- Quality (read-only for CI) ----------
.PHONY: fmt-check
fmt-check: ## Verify formatting without modifying files
	$(RUFF) format . --check

.PHONY: lint
lint: ## Run linting
	$(RUFF) check .

.PHONY: typecheck
typecheck: ## Run type checking only
	$(MYPY) . --ignore-missing-imports --explicit-package-bases

# ---------- Quality (write mode for local dev) ----------
.PHONY: format
format: ## Auto-format & fix with ruff
	$(RUFF) format .
	$(RUFF) check . --fix

# ---------- Tests ----------
.PHONY: test
test: ## Run fast test suite (exclude slow)
	PYTHONPATH=$$PWD $(PYTEST) -q -m "not slow"

.PHONY: test-all
test-all: ## Run full test suite including slow tests
	PYTHONPATH=$$PWD $(PYTEST) -q

.PHONY: cov
cov: ## Run tests with coverage
	PYTHONPATH=$$PWD $(PYTEST) --cov=nl2sql --cov-report=term-missing

# ---------- Unified gate for CI ----------
.PHONY: check
check: ## Run format check, lint, typecheck, and fast tests
	make fmt-check
	make lint
	make typecheck
	make test

# ---------- Pre-commit ----------
.PHONY: precommit
precommit: ## Run all pre-commit hooks on all files
	pre-commit run --all-files

# ---------- Run ----------
.PHONY: run
run: ## Run FastAPI app (reload mode)
	$(UVICORN) app.main:app --reload --host 0.0.0.0 --port $(PORT)

# ---------- Benchmarks ----------
.PHONY: bench
bench: ## Run benchmark suite (DummyLLM fallback)
	$(PY) -m benchmarks.run

# ---------- Docker ----------
.PHONY: docker-build
docker-build: ## Build Docker image
	docker build -t $(DOCKER_IMG) .

.PHONY: docker-run
docker-run: ## Run Docker container on port $(PORT)
	docker run --rm -p $(PORT):8000 $(DOCKER_IMG)

# ---------- Clean ----------
.PHONY: clean
clean: ## Remove Python caches
	rm -rf __pycache__ .pytest_cache .mypy_cache .ruff_cache

.PHONY: clean-all
clean-all: clean ## Remove build artifacts and coverage
	rm -rf dist build .coverage *.egg-info

# ---------- Observability Stack ----------
.PHONY: obs-up obs-down obs-logs prom-up prom-check smoke

# Bring up Prometheus + Grafana via Docker Compose
prom-up:
	docker compose -f docker-compose.prom.yml up -d

# Validate Prometheus configs (fallback to Docker if promtool is missing)
prom-check:
	@if command -v promtool >/dev/null 2>&1; then \
		echo "🔍 Running promtool locally..."; \
		promtool check rules prometheus/rules.yml && promtool check config prometheus/prometheus.yml; \
	else \
		echo "⚠️ promtool not found, running via Docker..."; \
		docker run --rm -v $$(pwd)/prometheus:/etc/prometheus prom/prometheus \
			promtool check rules /etc/prometheus/rules.yml && \
		docker run --rm -v $$(pwd)/prometheus:/etc/prometheus prom/prometheus \
			promtool check config /etc/prometheus/prometheus.yml; \
	fi

# Generate sample traffic and print key metrics snapshot
smoke:
	./scripts/smoke_metrics.sh

# Bring up the stack, wait until services are ready, then run smoke
obs-up:
	@set -e; \
	\
	# 1) Up the stack
	$(MAKE) prom-up; \
	\
	# 2) Wait for Prometheus readiness
	#    - Tries the /-/ready endpoint (preferred). Falls back to port check.
	#    - Times out after ~90s.
	echo "⏳ Waiting for Prometheus (http://localhost:9090) ..."; \
	for i in $$(seq 1 30); do \
		# Check readiness endpoint
		if curl -fsS http://localhost:9090/-/ready >/dev/null 2>&1; then \
			echo "✅ Prometheus is ready"; \
			break; \
		fi; \
		# Fallback: check TCP port if /-/ready is not enabled
		if nc -z localhost 9090 >/dev/null 2>&1; then \
			echo "✅ Prometheus port is open (assuming ready)"; \
			break; \
		fi; \
		sleep 3; \
		if [ $$i -eq 30 ]; then \
			echo "❌ Prometheus did not become ready in time"; \
			exit 1; \
		fi; \
	done; \
	\
	# 3) Wait for Grafana login page
	#    - Checks that /login returns HTTP 200/302.
	echo "⏳ Waiting for Grafana (http://localhost:3000) ..."; \
	for i in $$(seq 1 30); do \
		code=$$(curl -s -o /dev/null -w "%{http_code}" http://localhost:3000/login || true); \
		if [ "$$code" = "200" ] || [ "$$code" = "302" ]; then \
			echo "✅ Grafana is up"; \
			break; \
		fi; \
		sleep 3; \
		if [ $$i -eq 30 ]; then \
			echo "❌ Grafana did not become ready in time"; \
			exit 1; \
		fi; \
	done; \
	\
	# 4) Run smoke to populate metrics
	echo "🚀 Running smoke traffic ..."; \
	$(MAKE) smoke; \
	echo "🎉 Observability stack is live. Open: Prometheus → http://localhost:9090 , Grafana → http://localhost:3000"
    # 5) Auto-import Grafana dashboard
	$(MAKE) grafana-import

# Tear down the observability stack
obs-down:
	docker compose -f docker-compose.prom.yml down

# Tail logs of both services
obs-logs:
	docker compose -f docker-compose.prom.yml logs -f

# ---------- Grafana Auto Import ----------
.PHONY: grafana-import

# Import dashboard JSON into Grafana via HTTP API
grafana-import:
	@set -e; \
	echo "⏳ Waiting for Grafana API to become ready..."; \
	for i in $$(seq 1 30); do \
		code=$$(curl -s -o /dev/null -w "%{http_code}" http://localhost:3000/api/health || true); \
		if [ "$$code" = "200" ]; then \
			echo "✅ Grafana API is ready"; \
			break; \
		fi; \
		sleep 3; \
		if [ $$i -eq 30 ]; then \
			echo "❌ Grafana API did not become ready in time"; \
			exit 1; \
		fi; \
	done; \
	\
	echo "📦 Importing dashboard ..."; \
	curl -s -X POST http://admin:admin@localhost:3000/api/dashboards/db \
		-H "Content-Type: application/json" \
		-d "{\"dashboard\": $$(cat prometheus/grafana_dashboard.json), \"overwrite\": true, \"folderId\": 0}" \
		| jq -r '.status' || true; \
	echo "🎉 Dashboard imported → http://localhost:3000/dashboards"
