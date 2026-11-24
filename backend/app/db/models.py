# backend/app/db/models.py

"""
Database Models
===============

Tables:
    evaluations  — the final results of each vendor evaluation
    cert_cache   — cached certification discovery results (7-day TTL)
    jobs         — async job tracking for the 2-minute evaluation pipeline

    Vendor document onboarding (ADR 0005), tenant-owned (org_id NOT NULL):
    vendor_engagements     — a client's invite to one vendor
    vendor_documents       — files the vendor uploaded for an engagement
    questionnaire_answers  — security questionnaire answers for an engagement

WHY A JOBS TABLE?
    The evaluation pipeline takes ~2 minutes. HTTP connections time out.
    The async job pattern solves this:
        POST /evaluate   → creates a job record, starts background task, returns job_id (202)
        GET /jobs/{id}   → polls status (pending → running → complete/failed)

    The job table is the contract between the API and the background worker.
    Even if the server restarts mid-evaluation, the job status is in PostgreSQL.
    The client can resume polling when the server comes back up.
"""

import uuid
from datetime import UTC, datetime, timezone
from sqlalchemy import (
    JSON, CheckConstraint, DateTime, Float, ForeignKey, Index, Integer, String, Text,
    UniqueConstraint, Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from app.db.database import Base


# ─── Evaluation Model ─────────────────────────────────────────────────────────

class Evaluation(Base):
    __tablename__ = "evaluations"

    # Primary key — random UUID so it's globally unique
    id: Mapped[str] = mapped_column(
        String, primary_key=True,
        default=lambda: str(uuid.uuid4())
    )

    # Who was evaluated and by whom
    vendor_name:   Mapped[str] = mapped_column(String(255), index=True)
    company_name:  Mapped[str] = mapped_column(String(255), index=True)
    evaluation_id: Mapped[str] = mapped_column(String(255), unique=True)

    # The 5 agent scores
    financial_score:   Mapped[float | None] = mapped_column(Float, nullable=True)
    compliance_score:  Mapped[float | None] = mapped_column(Float, nullable=True)
    operational_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    reputation_score:  Mapped[float | None] = mapped_column(Float, nullable=True)
    strategic_score:   Mapped[float | None] = mapped_column(Float, nullable=True)

    # Meta-agent outputs
    composite_score:   Mapped[float | None] = mapped_column(Float, nullable=True)
    risk_score:        Mapped[float | None] = mapped_column(Float, nullable=True)
    fit_score:         Mapped[float | None] = mapped_column(Float, nullable=True)
    confidence:        Mapped[float | None] = mapped_column(Float, nullable=True)
    recommendation:    Mapped[str | None]   = mapped_column(String(50), nullable=True)

    # Full explanation text for the CPO
    final_explanation: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Store all red flags as a JSON array
    red_flags: Mapped[list | None] = mapped_column(JSON, nullable=True)

    # The weights used for this evaluation
    weights:       Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # JSONB on Postgres, matching the production Supabase column (changed
    # there by hand at some point); plain JSON elsewhere, e.g. SQLite tests.
    agent_outputs: Mapped[dict | None] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), nullable=True
    )

    # When it ran
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc)
    )

    def to_dict(self) -> dict:
        """Lightweight summary — used by list_evaluations."""
        return {
            "id":            self.id,
            "vendor_name":   self.vendor_name,
            "company_name":  self.company_name,
            "evaluation_id": self.evaluation_id,
            "scores": {
                "financial":   self.financial_score,
                "compliance":  self.compliance_score,
                "operational": self.operational_score,
                "reputation":  self.reputation_score,
                "strategic":   self.strategic_score,
                "composite":   self.composite_score,
                "risk":        self.risk_score,
                "fit":         self.fit_score,
            },
            "confidence":     self.confidence,
            "recommendation": self.recommendation,
            "red_flags":      self.red_flags,
            "created_at":     self.created_at.isoformat(),
        }


# ─── CertCache Model ──────────────────────────────────────────────────────────

class CertCache(Base):
    """
    Persistent cert cache in PostgreSQL.
    Redis is the L1 fast cache. This is the L2 persistent backup.

    If Redis restarts, the next read misses Redis but hits Supabase here.
    Both caches are always written together.
    """
    __tablename__ = "cert_cache"

    vendor_name: Mapped[str] = mapped_column(
        String(255), primary_key=True
    )
    certifications_found:            Mapped[list | None] = mapped_column(JSON, nullable=True)
    certification_evidence_tiers:    Mapped[dict | None] = mapped_column(JSON, nullable=True)
    regulatory_frameworks_disclosed: Mapped[list | None] = mapped_column(JSON, nullable=True)
    evidence:                        Mapped[list | None] = mapped_column(JSON, nullable=True)
    sources_checked:                 Mapped[list | None] = mapped_column(JSON, nullable=True)
    edgar_cik:                       Mapped[str | None]  = mapped_column(String(50), nullable=True)
    cached_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc)
    )


# ─── Job Model — NEW ──────────────────────────────────────────────────────────

class Job(Base):
    """
    Async job tracking for the 2-minute vendor evaluation pipeline.

    The job lifecycle:
        pending  → The job was created but the background task hasn't started yet
        running  → The pipeline is actively running
        complete → Pipeline finished, result is in the result column
        failed   → Pipeline threw an exception, error is in the error column

    The progress field (0-100) lets the frontend show a real progress bar
    instead of a fake timer-based animation.

    The current_step field shows exactly what the pipeline is doing right now,
    which maps to the loading steps displayed in the React frontend.
    """
    __tablename__ = "jobs"

    # Primary key — same format as evaluation_id for consistency
    job_id: Mapped[str] = mapped_column(
        String(50), primary_key=True
    )

    # What is being evaluated
    vendor_name: Mapped[str] = mapped_column(String(255), nullable=False)

    # Job lifecycle
    status: Mapped[str] = mapped_column(
        String(20),
        default="pending",
        # Allowed values: pending, running, complete, failed
    )

    # Human-readable step label for the frontend loading animation
    # Examples: "Scraping financial data", "Running compliance agent", "Done"
    current_step: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Progress percentage (0-100) for the frontend progress bar
    progress: Mapped[int] = mapped_column(Integer, default=0)

    # The full evaluation result JSON (only populated when status == complete)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # Error message if the pipeline failed (only populated when status == failed)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Timestamps for monitoring and debugging
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    def to_status_dict(self) -> dict:
        """
        What the GET /jobs/{job_id} endpoint returns while the job is running.
        Lightweight — no result yet.
        """
        return {
            "job_id":       self.job_id,
            "vendor_name":  self.vendor_name,
            "status":       self.status,
            "current_step": self.current_step,
            "progress":     self.progress,
            "created_at":   self.created_at.isoformat(),
            "updated_at":   self.updated_at.isoformat(),
        }

    def to_complete_dict(self) -> dict:
        """
        What the GET /jobs/{job_id} endpoint returns when status == complete.
        Includes the full evaluation result.
        """
        d = self.to_status_dict()
        d["result"] = self.result
        return d


# ─── Vendor document onboarding (ADR 0005) ────────────────────────────────────
#
# Every row carries org_id NOT NULL. Until Phase 1 it is filled server-side
# from settings.default_org_id, never from a request body; Phase 1 swaps the
# source to the verified token. Ids are UUIDs (native uuid on Postgres) so
# Phase 1's row-level security can compare org_id to a uuid directly.

ENGAGEMENT_STATUSES = ("invited", "submitted", "closed")
DOCUMENT_KINDS = ("soc2", "iso27001", "other", "unknown")
PARSE_STATUSES = ("pending", "parsing", "parsed", "failed")
ANSWER_STATUSES = ("suggested", "accepted", "edited", "unanswered")

# JSONB on Postgres, plain JSON elsewhere (SQLite tests), like agent_outputs
JSON_DOCUMENT = JSON().with_variant(JSONB(), "postgresql")


def _uuid_str() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(UTC)


def _one_of(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class VendorEngagement(Base):
    """
    A client company inviting one vendor to upload documents. The vendor
    reaches it only through the invite link; the token itself is never
    stored, only its SHA-256 hash, and it stops working at invite_expires_at.
    """
    __tablename__ = "vendor_engagements"
    __table_args__ = (
        CheckConstraint(_one_of("status", ENGAGEMENT_STATUSES), name="ck_vendor_engagements_status"),
        Index("ix_vendor_engagements_org_id_created_at", "org_id", "created_at"),
    )

    id:                Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True, default=_uuid_str)
    org_id:            Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    vendor_name:       Mapped[str] = mapped_column(String(255), nullable=False)
    company_name:      Mapped[str] = mapped_column(String(255), nullable=False)   # the client
    invite_token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    invite_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status:            Mapped[str] = mapped_column(String(20), nullable=False, default="invited")
    created_at:        Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)


class VendorDocument(Base):
    """
    One uploaded file. storage_key is a path relative to the document storage
    directory (MinIO from Phase 4); parse results land in extracted.
    """
    __tablename__ = "vendor_documents"
    __table_args__ = (
        CheckConstraint(_one_of("kind", DOCUMENT_KINDS), name="ck_vendor_documents_kind"),
        CheckConstraint(_one_of("parse_status", PARSE_STATUSES), name="ck_vendor_documents_parse_status"),
        Index("ix_vendor_documents_org_id_created_at", "org_id", "created_at"),
        Index("ix_vendor_documents_engagement_id", "engagement_id"),
    )

    id:            Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True, default=_uuid_str)
    org_id:        Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    engagement_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("vendor_engagements.id", ondelete="CASCADE"), nullable=False
    )
    kind:          Mapped[str] = mapped_column(String(20), nullable=False, default="unknown")
    filename:      Mapped[str] = mapped_column(String(255), nullable=False)
    content_type:  Mapped[str] = mapped_column(String(255), nullable=False)
    size_bytes:    Mapped[int] = mapped_column(Integer, nullable=False)
    sha256:        Mapped[str] = mapped_column(String(64), nullable=False)
    storage_key:   Mapped[str] = mapped_column(String(512), nullable=False)
    parse_status:  Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    parse_error:   Mapped[str | None] = mapped_column(Text, nullable=True)
    extracted:     Mapped[dict | None] = mapped_column(JSON_DOCUMENT, nullable=True)
    evidence_tier: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_at:    Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)


class QuestionnaireAnswer(Base):
    """
    One answer per (engagement, question). Autofill writes "suggested" rows
    with their source document, page and quote; the client's accepted or
    edited answers are kept when autofill runs again.
    """
    __tablename__ = "questionnaire_answers"
    __table_args__ = (
        CheckConstraint(_one_of("status", ANSWER_STATUSES), name="ck_questionnaire_answers_status"),
        UniqueConstraint("engagement_id", "question_id", name="uq_questionnaire_answers_engagement_question"),
        Index("ix_questionnaire_answers_org_id_created_at", "org_id", "created_at"),
    )

    id:                 Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True, default=_uuid_str)
    org_id:             Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    engagement_id:      Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("vendor_engagements.id", ondelete="CASCADE"), nullable=False
    )
    question_id:        Mapped[str] = mapped_column(String(100), nullable=False)
    value:              Mapped[dict | list | str | bool | None] = mapped_column(JSON_DOCUMENT, nullable=True)
    source_document_id: Mapped[str | None] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("vendor_documents.id", ondelete="SET NULL"), nullable=True
    )
    source_page:        Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_quote:       Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence:         Mapped[float | None] = mapped_column(Float, nullable=True)
    status:             Mapped[str] = mapped_column(String(20), nullable=False, default="unanswered")
    created_at:         Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at:         Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, onupdate=_now
    )
