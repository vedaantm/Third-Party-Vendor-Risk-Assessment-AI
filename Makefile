# VendorIQ developer commands. Run `make help` for the list.
#
# Needs GNU make and uv. On Windows: `winget install ezwinports.make` and
# `winget install astral-sh.uv`, or run the commands under a target by hand.
#
# `check` is what CLAUDE.md requires before every commit: lint (ruff, ruff
# format, mypy), the frontend type check and both test suites. CI runs the
# same targets. Lint is a ratchet: existing findings are recorded in
# backend/lint-baseline.json and only new ones fail (see
# backend/scripts/lint_baseline.py).

NPM ?= npm

.DEFAULT_GOAL := help
.PHONY: help install hooks check lint lint-baseline audit test test-backend test-frontend typecheck eval-smoke migrate db-up test-postgres up

help: ## List the targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "} {printf "  %-14s %s\n", $$1, $$2}'

install: hooks ## Install locked backend (incl. dev group) and frontend dependencies
	cd backend && uv sync --locked
	cd frontend && $(NPM) ci

hooks: ## Use the repo's git hooks (.githooks: pre-commit, commit-msg)
	git config core.hooksPath .githooks

check: lint typecheck test ## Everything that must pass before a commit

lint: ## ruff, ruff format and mypy: no findings beyond the baseline
	cd backend && uv run --locked python scripts/lint_baseline.py

lint-baseline: ## Re-record the lint baseline after fixing findings
	cd backend && uv run --locked python scripts/lint_baseline.py --update

# ChromaDB advisories with no fixed release. All four are in Chroma's HTTP
# server and its auth providers; VendorIQ embeds Chroma in-process
# (PersistentClient) and never runs that server. Phase 4 removes Chroma.
PIP_AUDIT_IGNORE := 	--ignore-vuln PYSEC-2026-311 	--ignore-vuln PYSEC-2026-3813 	--ignore-vuln PYSEC-2026-3814 	--ignore-vuln PYSEC-2026-3815

audit: frontend/node_modules ## Known vulnerabilities in runtime dependencies (pip-audit, npm audit)
	cd backend && uv export --locked --no-dev --no-hashes --format requirements-txt -q -o .audit-requirements.txt
	cd backend && uvx --from pip-audit==2.10.1 pip-audit -r .audit-requirements.txt --disable-pip --no-deps --progress-spinner off $(PIP_AUDIT_IGNORE); status=$$?; rm -f .audit-requirements.txt; exit $$status
	cd frontend && $(NPM) audit --omit=dev --audit-level=high

test: test-backend test-frontend ## Backend and frontend tests (offline)

test-backend: ## Backend tests: no network, no Supabase, no real chroma_db
	cd backend && uv run --locked pytest tests -q

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

migrate: ## Apply migrations to DATABASE_URL from backend/.env (Supabase)
	cd backend && uv run --locked alembic upgrade head

db-up: ## Start only the local Postgres container (127.0.0.1:5432)
	docker compose up -d --wait postgres

LOCAL_POSTGRES_URL ?= postgresql+asyncpg://vendoriq:vendoriq-local@127.0.0.1:5432/vendoriq

test-postgres: export TEST_POSTGRES_URL := $(LOCAL_POSTGRES_URL)
test-postgres: db-up ## Migration tests against the local Postgres (needs Docker)
	cd backend && uv run --locked pytest tests/test_migrations.py -q

up: backend/.env ## Start the full stack on the local Postgres (never Supabase)
	docker compose up --build

# A fresh clone has no backend/.env, which docker-compose.yml loads. Start
# from the example; never overwrite an existing one. The stack runs without
# a Groq key, but evaluations need one.
backend/.env:
	cp backend/.env.example backend/.env
	@echo "Created backend/.env from backend/.env.example."
	@echo "Set GROQ_API_KEY in it (free at https://console.groq.com) to run evaluations."
