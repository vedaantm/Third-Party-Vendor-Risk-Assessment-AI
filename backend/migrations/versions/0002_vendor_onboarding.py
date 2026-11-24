"""vendor onboarding: vendor_engagements, vendor_documents, questionnaire_answers

New tenant-owned tables for vendor document onboarding (ADR 0005). Every
table has org_id NOT NULL and an index on (org_id, created_at). Existing
tables are untouched, so downgrading drops only these three.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-23

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON_DOCUMENT = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "vendor_engagements",
        sa.Column("id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("org_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("vendor_name", sa.String(length=255), nullable=False),
        sa.Column("company_name", sa.String(length=255), nullable=False),
        sa.Column("invite_token_hash", sa.String(length=64), nullable=False),
        sa.Column("invite_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('invited', 'submitted', 'closed')",
            name="ck_vendor_engagements_status",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("invite_token_hash"),
    )
    op.create_index(
        "ix_vendor_engagements_org_id_created_at",
        "vendor_engagements",
        ["org_id", "created_at"],
        unique=False,
    )

    op.create_table(
        "vendor_documents",
        sa.Column("id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("org_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("engagement_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("content_type", sa.String(length=255), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("storage_key", sa.String(length=512), nullable=False),
        sa.Column("parse_status", sa.String(length=20), nullable=False),
        sa.Column("parse_error", sa.Text(), nullable=True),
        sa.Column("extracted", JSON_DOCUMENT, nullable=True),
        sa.Column("evidence_tier", sa.String(length=40), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "kind IN ('soc2', 'iso27001', 'other', 'unknown')",
            name="ck_vendor_documents_kind",
        ),
        sa.CheckConstraint(
            "parse_status IN ('pending', 'parsing', 'parsed', 'failed')",
            name="ck_vendor_documents_parse_status",
        ),
        sa.ForeignKeyConstraint(
            ["engagement_id"], ["vendor_engagements.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_vendor_documents_engagement_id",
        "vendor_documents",
        ["engagement_id"],
        unique=False,
    )
    op.create_index(
        "ix_vendor_documents_org_id_created_at",
        "vendor_documents",
        ["org_id", "created_at"],
        unique=False,
    )

    op.create_table(
        "questionnaire_answers",
        sa.Column("id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("org_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("engagement_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("question_id", sa.String(length=100), nullable=False),
        sa.Column("value", JSON_DOCUMENT, nullable=True),
        sa.Column("source_document_id", sa.Uuid(as_uuid=False), nullable=True),
        sa.Column("source_page", sa.Integer(), nullable=True),
        sa.Column("source_quote", sa.Text(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('suggested', 'accepted', 'edited', 'unanswered')",
            name="ck_questionnaire_answers_status",
        ),
        sa.ForeignKeyConstraint(
            ["engagement_id"], ["vendor_engagements.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["source_document_id"], ["vendor_documents.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "engagement_id",
            "question_id",
            name="uq_questionnaire_answers_engagement_question",
        ),
    )
    op.create_index(
        "ix_questionnaire_answers_org_id_created_at",
        "questionnaire_answers",
        ["org_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_questionnaire_answers_org_id_created_at", table_name="questionnaire_answers"
    )
    op.drop_table("questionnaire_answers")
    op.drop_index(
        "ix_vendor_documents_org_id_created_at", table_name="vendor_documents"
    )
    op.drop_index("ix_vendor_documents_engagement_id", table_name="vendor_documents")
    op.drop_table("vendor_documents")
    op.drop_index(
        "ix_vendor_engagements_org_id_created_at", table_name="vendor_engagements"
    )
    op.drop_table("vendor_engagements")
