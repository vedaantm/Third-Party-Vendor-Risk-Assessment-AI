# backend/app/api/engagements.py
"""
Vendor engagements and the vendor portal (ADR 0005)
====================================================

Client side (no login until Phase 1):
    POST /engagements               create one; the invite link is returned ONCE
    GET  /engagements               list, with documents and parse status
    GET  /engagements/{id}          one engagement, with documents
    POST /engagements/{id}/invite   issue a new link (the old one stops working)

Vendor side (the invite token is the only credential):
    GET  /portal/{token}            the engagement and the files uploaded so far
    POST /portal/{token}/documents  multipart upload of one PDF

Only the SHA-256 of an invite token is stored. An unknown, expired or
closed link, or one for another org, returns 404 (never 403), so a caller
can't tell a wrong token from a missing engagement. Every query filters by
org_id, which comes from current_org_id() and never from a request body.
"""

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.db.models import VendorDocument, VendorEngagement
from app.log import get_logger
from app.services.documents import pipeline as document_pipeline
from app.settings import settings

router = APIRouter()
log = get_logger(__name__)

NOT_FOUND = "Not found"
PDF_CONTENT_TYPES = {"application/pdf", "application/x-pdf"}
PDF_MAGIC = b"%PDF-"
READ_CHUNK = 64 * 1024
DEFAULT_PORTAL_ORIGIN = "http://localhost:5173"  # same default as main_api CORS
MIN_INVITE_DAYS, MAX_INVITE_DAYS = 1, 90
LONG_EXPIRY_DAYS = 30  # longer invites need acknowledge_long_expiry
LONG_EXPIRY_MESSAGE = (
    f"Invites longer than {LONG_EXPIRY_DAYS} days need acknowledge_long_expiry"
)


# ─── Tenancy ──────────────────────────────────────────────────────────────────


def current_org_id() -> str:
    """The caller's org. Phase 1 reads it from the verified token instead."""
    return str(settings.default_org_id)


DB = Annotated[AsyncSession, Depends(get_db)]
OrgId = Annotated[str, Depends(current_org_id)]


# ─── Invite tokens ────────────────────────────────────────────────────────────


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class InviteOptions(BaseModel):
    """
    The client picks how long a link works: 1 to 90 days, or INVITE_TTL_HOURS
    (default 7 days) when omitted. More than 30 days must be acknowledged.
    """

    # extra="forbid": org_id (or anything else) in the body is rejected
    model_config = ConfigDict(extra="forbid")

    expires_in_days: int | None = Field(
        default=None, ge=MIN_INVITE_DAYS, le=MAX_INVITE_DAYS
    )
    acknowledge_long_expiry: bool = False

    @model_validator(mode="after")
    def _long_expiry_acknowledged(self) -> "InviteOptions":
        days = self.expires_in_days
        if (
            days is not None
            and days > LONG_EXPIRY_DAYS
            and not self.acknowledge_long_expiry
        ):
            raise ValueError(LONG_EXPIRY_MESSAGE)
        return self

    def ttl(self) -> timedelta:
        if self.expires_in_days is None:
            return timedelta(hours=settings.invite_ttl_hours)
        return timedelta(days=self.expires_in_days)

    def is_long(self) -> bool:
        return (self.expires_in_days or 0) > LONG_EXPIRY_DAYS


def _new_invite(options: InviteOptions) -> tuple[str, str, datetime]:
    """(token, its hash, expiry). The token itself is never stored."""
    token = secrets.token_urlsafe(32)
    expires = datetime.now(UTC) + options.ttl()
    return token, hash_token(token), expires


def _portal_origin() -> str:
    """The frontend origin (first CORS origin), where the portal page lives."""
    raw = settings.cors_allow_origins or ""
    origins = [o.strip().rstrip("/") for o in raw.split(",") if o.strip()]
    return origins[0] if origins else DEFAULT_PORTAL_ORIGIN


def _invite_payload(token: str, expires: datetime, options: InviteOptions) -> dict:
    path = f"/portal/{token}"
    # Not persisted yet (no column until a later migration); returned so the
    # client has a record of the acknowledgement.
    acknowledged = datetime.now(UTC).isoformat() if options.is_long() else None
    return {
        "invite_token": token,
        "invite_path": path,
        "invite_url": _portal_origin() + path,
        "invite_expires_at": expires.isoformat(),
        "long_expiry_acknowledged_at": acknowledged,
    }


def _aware(value: datetime) -> datetime:
    """SQLite drops the timezone; every stored datetime is UTC."""
    return value if value.tzinfo else value.replace(tzinfo=UTC)


# ─── Serialisation (never includes invite_token_hash) ─────────────────────────


def _document_dict(doc: VendorDocument, *, client: bool) -> dict:
    data = {
        "id": doc.id,
        "filename": doc.filename,
        "kind": doc.kind,
        "content_type": doc.content_type,
        "size_bytes": doc.size_bytes,
        "sha256": doc.sha256,
        "parse_status": doc.parse_status,
        "parse_error": doc.parse_error,
        "created_at": _aware(doc.created_at).isoformat(),
    }
    if client:
        data["evidence_tier"] = doc.evidence_tier
        data["extracted"] = doc.extracted
    return data


def _engagement_dict(
    eng: VendorEngagement, docs: list[VendorDocument], *, client: bool
) -> dict:
    data = {
        "vendor_name": eng.vendor_name,
        "company_name": eng.company_name,
        "status": eng.status,
        "invite_expires_at": _aware(eng.invite_expires_at).isoformat(),
        "created_at": _aware(eng.created_at).isoformat(),
        "documents": [_document_dict(d, client=client) for d in docs],
    }
    if client:
        data = {"id": eng.id, **data}
    return data


# ─── Queries ──────────────────────────────────────────────────────────────────


async def _documents(
    db: AsyncSession, org_id: str, engagement_id: str
) -> list[VendorDocument]:
    result = await db.execute(
        select(VendorDocument)
        .where(
            VendorDocument.org_id == org_id,
            VendorDocument.engagement_id == engagement_id,
        )
        .order_by(VendorDocument.created_at)
    )
    return list(result.scalars())


async def _engagement_or_404(
    db: AsyncSession, org_id: str, engagement_id: str
) -> VendorEngagement:
    result = await db.execute(
        select(VendorEngagement).where(
            VendorEngagement.org_id == org_id, VendorEngagement.id == engagement_id
        )
    )
    eng = result.scalar_one_or_none()
    if eng is None:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    return eng


async def _engagement_for_token(
    db: AsyncSession, org_id: str, token: str
) -> VendorEngagement:
    """The engagement a live invite token opens, or 404."""
    result = await db.execute(
        select(VendorEngagement).where(
            VendorEngagement.org_id == org_id,
            VendorEngagement.invite_token_hash == hash_token(token),
        )
    )
    eng = result.scalar_one_or_none()
    if (
        eng is None
        or eng.status == "closed"
        or _aware(eng.invite_expires_at) <= datetime.now(UTC)
    ):
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    return eng


# ─── Client side ──────────────────────────────────────────────────────────────


class EngagementCreate(InviteOptions):
    vendor_name: str = Field(min_length=1, max_length=255)
    company_name: str = Field(min_length=1, max_length=255)

    @field_validator("vendor_name", "company_name")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value


@router.post("/engagements", status_code=201)
async def create_engagement(
    body: EngagementCreate,
    db: DB,
    org_id: OrgId,
):
    """Create an engagement. The response is the only place the invite link appears."""
    token, token_hash, expires = _new_invite(body)
    eng = VendorEngagement(
        org_id=org_id,
        vendor_name=body.vendor_name,
        company_name=body.company_name,
        invite_token_hash=token_hash,
        invite_expires_at=expires,
    )
    db.add(eng)
    await db.commit()
    log.info("engagement.created", engagement_id=eng.id)
    return {
        **_engagement_dict(eng, [], client=True),
        **_invite_payload(token, expires, body),
    }


@router.get("/engagements")
async def list_engagements(
    db: DB,
    org_id: OrgId,
):
    result = await db.execute(
        select(VendorEngagement)
        .where(VendorEngagement.org_id == org_id)
        .order_by(VendorEngagement.created_at.desc())
    )
    engagements = list(result.scalars())
    docs_result = await db.execute(
        select(VendorDocument)
        .where(VendorDocument.org_id == org_id)
        .order_by(VendorDocument.created_at)
    )
    by_engagement: dict[str, list[VendorDocument]] = {}
    for doc in docs_result.scalars():
        by_engagement.setdefault(doc.engagement_id, []).append(doc)
    return [
        _engagement_dict(e, by_engagement.get(e.id, []), client=True)
        for e in engagements
    ]


@router.get("/engagements/{engagement_id}")
async def get_engagement(
    engagement_id: str,
    db: DB,
    org_id: OrgId,
):
    eng = await _engagement_or_404(db, org_id, engagement_id)
    return _engagement_dict(eng, await _documents(db, org_id, eng.id), client=True)


@router.post("/engagements/{engagement_id}/invite")
async def reissue_invite(
    engagement_id: str,
    db: DB,
    org_id: OrgId,
    body: InviteOptions | None = None,
):
    """A new invite link with a fresh expiry; the previous link stops working."""
    options = body or InviteOptions()
    eng = await _engagement_or_404(db, org_id, engagement_id)
    token, token_hash, expires = _new_invite(options)
    eng.invite_token_hash = token_hash
    eng.invite_expires_at = expires
    await db.commit()
    log.info("engagement.invite_reissued", engagement_id=eng.id)
    return {"id": eng.id, **_invite_payload(token, expires, options)}


# ─── Vendor portal ────────────────────────────────────────────────────────────


@router.get("/portal/{token}")
async def portal(
    token: str,
    db: DB,
    org_id: OrgId,
):
    eng = await _engagement_for_token(db, org_id, token)
    return _engagement_dict(eng, await _documents(db, org_id, eng.id), client=False)


async def _read_limited(upload: UploadFile, limit: int) -> bytes:
    """The whole upload, or 413 as soon as it passes `limit` bytes."""
    chunks: list[bytes] = []
    total = 0
    while chunk := await upload.read(READ_CHUNK):
        total += len(chunk)
        if total > limit:
            raise HTTPException(
                status_code=413, detail=f"File is larger than {limit} bytes"
            )
        chunks.append(chunk)
    return b"".join(chunks)


def _display_filename(raw: str | None) -> str:
    """The client's name for display only; never used as a path."""
    name = (raw or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
    return (name or "document.pdf")[:255]


@router.post("/portal/{token}/documents", status_code=201)
async def upload_document(
    token: str,
    background_tasks: BackgroundTasks,
    file: Annotated[UploadFile, File()],
    db: DB,
    org_id: OrgId,
):
    """
    Upload one PDF. It must be declared as a PDF and start with the PDF
    magic bytes. The same file uploaded again to this engagement returns the
    existing record (200) instead of a copy. New files are parsed in the
    background.
    """
    eng = await _engagement_for_token(db, org_id, token)

    content_type = (file.content_type or "").split(";")[0].strip().lower()
    if content_type not in PDF_CONTENT_TYPES:
        raise HTTPException(status_code=415, detail="Only PDF files are accepted")
    data = await _read_limited(file, settings.max_upload_bytes)
    if not data.startswith(PDF_MAGIC):
        raise HTTPException(status_code=415, detail="Only PDF files are accepted")

    sha256 = hashlib.sha256(data).hexdigest()
    existing = await db.execute(
        select(VendorDocument).where(
            VendorDocument.org_id == org_id,
            VendorDocument.engagement_id == eng.id,
            VendorDocument.sha256 == sha256,
        )
    )
    duplicate = existing.scalars().first()
    if duplicate is not None:
        return JSONResponse(
            status_code=200,
            content={**_document_dict(duplicate, client=False), "duplicate": True},
        )

    doc = VendorDocument(
        org_id=org_id,
        engagement_id=eng.id,
        filename=_display_filename(file.filename),
        content_type="application/pdf",
        size_bytes=len(data),
        sha256=sha256,
    )
    # Server-generated key; the client's filename never reaches the path
    doc.id = str(uuid.uuid4())
    doc.storage_key = f"{org_id}/{eng.id}/{doc.id}.pdf"
    path = Path(settings.document_storage_dir) / doc.storage_key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)

    db.add(doc)
    try:
        await db.commit()  # before the parser runs, so it can load the row
    except Exception:
        path.unlink(missing_ok=True)
        raise

    background_tasks.add_task(document_pipeline.parse_document, doc.id)
    log.info(
        "document.uploaded",
        engagement_id=eng.id,
        document_id=doc.id,
        size_bytes=len(data),
    )
    return {**_document_dict(doc, client=False), "duplicate": False}
