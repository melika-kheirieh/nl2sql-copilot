
# ---------- Config ----------
VENV_DIR   ?= .venv
PY         ?= $(if $(wildcard $(VENV_DIR)/bin/python),$(VENV_DIR)/bin/python,python3)
PIP        ?= $(if $(wildcard $(VENV_DIR)/bin/pip),$(VENV_DIR)/bin/pip,pip)
UVICORN    ?= $(if $(wildcard $(VENV_DIR)/bin/uvicorn),$(VENV_DIR)/bin/uvicorn,uvicorn)
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
	$(PIP) install ruff mypy pytest pytest-cov uvicorn

# ---------- Quality ----------
.PHONY: format
format: ## Auto-format & fix with ruff
	$(VENV_DIR)/bin/ruff format .
	$(VENV_DIR)/bin/ruff check . --fix

.PHONY: lint
lint: ## Run linting and type checking
	$(VENV_DIR)/bin/ruff check .
	$(VENV_DIR)/bin/mypy .

.PHONY: typecheck
typecheck: ## Run type checking only
	$(VENV_DIR)/bin/mypy .

# ---------- Tests ----------
.PHONY: test
test: ## Run pytest quietly
	PYTHONPATH=$$PWD $(VENV_DIR)/bin/pytest -q

.PHONY: cov
cov: ## Run tests with coverage
	PYTHONPATH=$$PWD $(VENV_DIR)/bin/pytest --cov=nl2sql --cov-report=term-missing

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
	rm -rf __pycache__ .pytest_cache .mypy_cache

.PHONY: clean-all
clean-all: clean ## Remove build artifacts and coverage
	rm -rf dist build .coverage *.egg-info
