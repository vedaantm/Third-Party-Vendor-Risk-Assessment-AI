"""baseline: evaluations, cert_cache, jobs

The schema that Base.metadata.create_all used to create at startup,
generated from app/db/models.py.

EXISTING DATABASES (e.g. the current Supabase project) already have these
tables. Don't upgrade them — mark them as being at this revision instead,
once, after confirming the schema matches:

    cd backend && uv run alembic stamp 0001

New databases: `uv run alembic upgrade head` (the Docker image does this
on start).

Revision ID: 0001
Revises:
Create Date: 2026-11-14

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("cert_cache", "evaluations", "jobs")


class AlreadyCreatedError(RuntimeError):
    """The tables exist but the database was never stamped."""


def _refuse_if_tables_exist() -> None:
    if context.is_offline_mode():
        return   # `--sql` only prints the DDL; there is no database to inspect
    existing = sorted(set(TABLES) & set(sa.inspect(op.get_bind()).get_table_names()))
    if existing:
        raise AlreadyCreatedError(
            f"Tables {existing} already exist — this database was created by the "
            f"old Base.metadata.create_all startup step, not by migrations. "
            f"Confirm its schema matches app/db/models.py, then run "
            f"`uv run alembic stamp 0001` once instead of upgrading."
        )


def upgrade() -> None:
    _refuse_if_tables_exist()

    op.create_table(
        "cert_cache",
        sa.Column("vendor_name", sa.String(length=255), nullable=False),
        sa.Column("certifications_found", sa.JSON(), nullable=True),
        sa.Column("certification_evidence_tiers", sa.JSON(), nullable=True),
        sa.Column("regulatory_frameworks_disclosed", sa.JSON(), nullable=True),
        sa.Column("evidence", sa.JSON(), nullable=True),
        sa.Column("sources_checked", sa.JSON(), nullable=True),
        sa.Column("edgar_cik", sa.String(length=50), nullable=True),
        sa.Column("cached_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("vendor_name"),
    )
    op.create_table(
        "evaluations",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("vendor_name", sa.String(length=255), nullable=False),
        sa.Column("company_name", sa.String(length=255), nullable=False),
        sa.Column("evaluation_id", sa.String(length=255), nullable=False),
        sa.Column("financial_score", sa.Float(), nullable=True),
        sa.Column("compliance_score", sa.Float(), nullable=True),
        sa.Column("operational_score", sa.Float(), nullable=True),
        sa.Column("reputation_score", sa.Float(), nullable=True),
        sa.Column("strategic_score", sa.Float(), nullable=True),
        sa.Column("composite_score", sa.Float(), nullable=True),
        sa.Column("risk_score", sa.Float(), nullable=True),
        sa.Column("fit_score", sa.Float(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("recommendation", sa.String(length=50), nullable=True),
        sa.Column("final_explanation", sa.Text(), nullable=True),
        sa.Column("red_flags", sa.JSON(), nullable=True),
        sa.Column("weights", sa.JSON(), nullable=True),
        sa.Column("agent_outputs", sa.JSON().with_variant(postgresql.JSONB(), "postgresql"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("evaluation_id"),
    )
    op.create_index(op.f("ix_evaluations_company_name"), "evaluations", ["company_name"], unique=False)
    op.create_index(op.f("ix_evaluations_vendor_name"), "evaluations", ["vendor_name"], unique=False)
    op.create_table(
        "jobs",
        sa.Column("job_id", sa.String(length=50), nullable=False),
        sa.Column("vendor_name", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("current_step", sa.Text(), nullable=True),
        sa.Column("progress", sa.Integer(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("job_id"),
    )


def downgrade() -> None:
    op.drop_table("jobs")
    op.drop_index(op.f("ix_evaluations_vendor_name"), table_name="evaluations")
    op.drop_index(op.f("ix_evaluations_company_name"), table_name="evaluations")
    op.drop_table("evaluations")
    op.drop_table("cert_cache")
