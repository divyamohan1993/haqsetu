# =============================================================================
# HaqSetu - Developer Makefile
# Voice-First AI Civic Assistant for Rural India
# =============================================================================

.PHONY: help dev docker-dev docker-prod deploy-dev deploy-prod \
        test lint format clean seed setup \
        security-scan security-audit generate-keys

# Default target
help: ## Show this help message
	@echo ""
	@echo "HaqSetu - Available Commands"
	@echo "============================================"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'
	@echo ""

# ---------------------------------------------------------------------------
# Local Development
# ---------------------------------------------------------------------------

dev: ## Run locally with uvicorn (auto-reload)
	uvicorn src.main:app --host 0.0.0.0 --port 8000 --reload --reload-dir src

setup: ## Set up local development environment
	./scripts/setup_local.sh

# ---------------------------------------------------------------------------
# Docker
# ---------------------------------------------------------------------------

docker-dev: ## Run with Docker Compose (development)
	docker compose up --build

docker-prod: ## Run with Docker Compose (production config)
	docker compose -f docker-compose.yml -f docker-compose.prod.yml up --build -d

docker-down: ## Stop all Docker Compose services
	docker compose down

docker-logs: ## Tail Docker Compose logs
	docker compose logs -f

# ---------------------------------------------------------------------------
# Deployment
# ---------------------------------------------------------------------------

deploy-dev: ## Deploy to GCP (development environment)
	ENVIRONMENT=development ./scripts/deploy.sh

deploy-prod: ## Deploy to GCP (production environment)
	ENVIRONMENT=production ./scripts/deploy.sh

# ---------------------------------------------------------------------------
# Testing & Quality
# ---------------------------------------------------------------------------

test: ## Run pytest test suite
	pytest tests/ -v --tb=short

test-cov: ## Run tests with coverage report
	pytest tests/ -v --tb=short --cov=src --cov-report=term-missing --cov-report=html

lint: ## Run ruff linter
	ruff check src/ config/ tests/

format: ## Run ruff formatter
	ruff format src/ config/ tests/

format-check: ## Check formatting without applying changes
	ruff format --check src/ config/ tests/

typecheck: ## Run type checking (if mypy is installed)
	@command -v mypy >/dev/null 2>&1 && mypy src/ || echo "mypy not installed, skipping."

# ---------------------------------------------------------------------------
# Security
# ---------------------------------------------------------------------------

security-scan: ## Run bandit SAST scan
	bandit -r src/ -c pyproject.toml -ll

security-audit: ## Run pip-audit dependency vulnerability check
	pip-audit --strict --desc on

generate-keys: ## Generate or rotate secure keys in .env
	./scripts/generate_keys.sh

rotate-keys: ## Force-rotate all secure keys in .env
	./scripts/generate_keys.sh --rotate

# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

seed: ## Seed scheme data into Firestore
	python -m src.data.seed

# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------

clean: ## Remove build artifacts and caches
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	find . -type f -name "*.pyo" -delete 2>/dev/null || true
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".ruff_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "htmlcov" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "dist" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "build" -exec rm -rf {} + 2>/dev/null || true
	@echo "Cleaned."

clean-docker: ## Remove Docker images and volumes
	docker compose down -v --rmi local
	@echo "Docker resources cleaned."
