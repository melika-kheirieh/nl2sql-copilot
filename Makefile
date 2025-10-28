# Usage: `make <target>`  (e.g., `make test`)
# Lines with `##` appear in `make help`.

# ---------- Config ----------
PY          ?= python3
PIP         ?= pip
UVICORN     ?= uvicorn
PACKAGE_DIRS= nl2sql adapters app benchmarks
TESTS       = tests
DOCKER_IMG  = nl2sql-copilot
PORT        ?= 8000

.DEFAULT_GOAL := help

# ---------- Meta ----------
.PHONY: help
help: ## Show this help
	@awk 'BEGIN {FS := ":.*##"; printf "\n\033[1mAvailable targets:\033[0m\n"} /^[a-zA-Z0-9_.-]+:.*?##/ { printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2 } /^.PHONY:/ {gsub(/.PHONY: /, "", $$0)}' $(MAKEFILE_LIST)

# ---------- Dev basics ----------
.PHONY: install
install: ## Install runtime dependencies
	$(PIP) install -r requirements.txt

.PHONY: dev-install
dev-install: ## Install dev tools (ruff, mypy, pytest, coverage, uvicorn, etc.)
	$(PIP) install -U pip wheel
	$(PIP) install ruff mypy pytest pytest-cov uvicorn

# ---------- Quality ----------
.PHONY: format
format: ## Auto-format & quick-fix with ruff
	ruff format .
	ruff check . --fix

.PHONY: lint
lint: ## Lint only (no auto-fix)
	ruff check .
	mypy .

.PHONY: typecheck
typecheck: ## mypy type checking
	mypy .

# ---------- Tests ----------
.PHONY: test
test: ## Run unit tests (quiet)
	PYTHONPATH=$$PWD pytest -q

.PHONY: cov
cov: ## Run tests with coverage (package-only)
	PYTHONPATH=$$PWD pytest --cov=nl2sql --cov-report=term-missing

# ---------- App run ----------
.PHONY: run
run: ## Run FastAPI (dev, reload)
	$(UVICORN) app.main:app --reload --host 0.0.0.0 --port $(PORT)

# ---------- Benchmarks ----------
.PHONY: bench
bench: ## Run minimal benchmark script (no API keys needed with DummyLLM fallbacks)
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
	rm -rf __pycache__ .pytest_cache .mypy_cache

.PHONY: clean-all
clean-all: clean ## Remove build artifacts & coverage files
	rm -rf dist build .coverage *.egg-info
