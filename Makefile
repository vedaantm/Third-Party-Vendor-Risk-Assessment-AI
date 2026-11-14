# VendorIQ developer commands. Run `make help` for the list.
#
# Needs GNU make. On Windows: `winget install ezwinports.make`, or run the
# commands under a target by hand.
#
# `check` is what CLAUDE.md requires before every commit: the frontend type
# check plus both test suites. Lint (ruff) and Python type checking (mypy)
# join it once Phase 0 pins and configures those tools (items 2 and 8).
# Today ruff reports 108 existing issues in backend/app and mypy isn't a
# dependency yet, so adding them now would make every commit red.

PYTHON ?= python
NPM    ?= npm

.DEFAULT_GOAL := help
.PHONY: help install check test test-backend test-frontend typecheck eval-smoke up

help: ## List the targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "} {printf "  %-14s %s\n", $$1, $$2}'

install: ## Install backend (with test) and frontend dependencies
	$(PYTHON) -m pip install -r backend/requirements.txt -r backend/requirements-dev.txt
	cd frontend && $(NPM) ci

check: typecheck test ## Everything that must pass before a commit

test: test-backend test-frontend ## Backend and frontend tests (offline)

test-backend: ## Backend tests: no network, no Supabase, no real chroma_db
	$(PYTHON) -m pytest backend/tests -q

test-frontend: frontend/node_modules ## Frontend tests (Vitest)
	cd frontend && $(NPM) test

typecheck: frontend/node_modules ## Frontend TypeScript check
	cd frontend && $(NPM) run typecheck

frontend/node_modules: frontend/package-lock.json
	cd frontend && $(NPM) ci
	@touch $@

eval-smoke: ## Prompt-change evals (Phase 2) - fails until they exist
	@echo "eval-smoke: there is no eval harness yet; it arrives in Phase 2." >&2
	@echo "CLAUDE.md requires it for prompt changes, so those are blocked until then." >&2
	@exit 1

up: ## Start frontend, backend and Redis (uses backend/.env, i.e. Supabase)
	docker compose up --build
