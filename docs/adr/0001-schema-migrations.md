# ADR 0001: Manage the database schema with Alembic migrations

- **Status:** accepted
- **Date:** 2026-11-14
- **Phase:** 0, item 4

## Context

The API created its tables at startup with `Base.metadata.create_all`. That
only ever creates missing tables: it never adds, changes or removes a column
on a table that exists. Any schema change (for example storing the OFAC result
on saved evaluations, or Phase 1's `org_id` columns) would silently not reach
an existing database, and the app would fail at the first query that used it.

## Decision

- Alembic owns the schema. Migrations live in `backend/migrations/versions/`;
  `0001_baseline` is generated from `app/db/models.py` and is identical to what
  `create_all` produced (a test compares the two).
- The FastAPI lifespan no longer creates tables. The Docker image runs
  `alembic upgrade head` before starting uvicorn; locally, `make migrate`.
- `migrations/env.py` builds its engine with the app's `build_engine`, so
  migrations use the same Supabase options (SSL, `statement_cache_size=0`).
- Drift between models and migrations fails the test suite
  (`test_models_and_migrations_agree`, the equivalent of `alembic check`), so it
  runs in CI as part of the backend tests once CI exists (Phase 0 item 8).

## Consequences

- **One-time step for the existing Supabase database.** Its tables were created
  by `create_all`, so upgrading it would fail on "table already exists". The
  baseline refuses that case with a clear error instead of half-applying. After
  confirming the schema matches the models, run once:

  ```bash
  cd backend && uv run alembic stamp 0001
  ```

  Until then, the Docker backend refuses to start against that database.
- Every model change now needs a migration
  (`uv run alembic revision --autogenerate -m "..."`, then review it); the test
  suite fails until there is one.
- Migrations are tested against SQLite on every run, and against real
  PostgreSQL with `make test-postgres` (the local docker-compose Postgres,
  Phase 0 item 5), which CI should also run once it exists.
- Running `alembic check` against Supabase before stamping found one drift:
  `evaluations.agent_outputs` had been changed to JSONB by hand. The model
  and baseline now declare JSONB on Postgres, and Supabase was stamped at
  0001 after `alembic check` came back clean.
