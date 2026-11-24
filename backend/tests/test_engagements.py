"""
Engagement and vendor portal endpoints (app/api/engagements.py).

The app runs in-process through httpx's ASGI transport against a temporary
SQLite database. Uploads go to a per-test tmp directory, and the document
parser is replaced by a recorder, so nothing is parsed or sent anywhere.
"""

import asyncio
import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from conftest import make_sessionmaker
from sqlalchemy import select

import main_api
from app.api import engagements
from app.db.database import get_db
from app.db.models import VendorDocument, VendorEngagement
from app.services.documents import pipeline
from app.settings import settings

app = main_api.app

OTHER_ORG = "00000000-0000-0000-0000-00000000bbbb"
PDF = b"%PDF-1.7\n1 0 obj << /Type /Catalog >> endobj\n%%EOF\n"
BODY = {"vendor_name": "Okta", "company_name": "Acme Manufacturing Corp"}


# ── Fixtures ──────────────────────────────────────────────


@pytest.fixture
def Session(db_url):
    engine, Session = asyncio.run(make_sessionmaker(db_url))

    async def override_get_db():
        async with Session() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = override_get_db
    yield Session
    app.dependency_overrides.clear()
    asyncio.run(engine.dispose())


@pytest.fixture
def storage(tmp_path, monkeypatch):
    path = tmp_path / "documents"
    monkeypatch.setattr(settings, "document_storage_dir", str(path))
    return path


@pytest.fixture(autouse=True)
def parse_calls(monkeypatch):
    """The parser never runs here; record what would have been queued."""
    calls = []

    async def record(document_id: str) -> None:
        calls.append(document_id)

    monkeypatch.setattr(pipeline, "parse_document", record)
    return calls


def call(method, url, **kwargs):
    async def go():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            return await client.request(method, url, **kwargs)

    return asyncio.run(go())


def create(body=BODY):
    response = call("POST", "/api/v1/engagements", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def upload(token, data=PDF, *, filename="soc2.pdf", content_type="application/pdf"):
    return call(
        "POST",
        f"/api/v1/portal/{token}/documents",
        files={"file": (filename, data, content_type)},
    )


def as_other_org():
    app.dependency_overrides[engagements.current_org_id] = lambda: OTHER_ORG


def rows(Session, model):
    async def go():
        async with Session() as session:
            return list((await session.execute(select(model))).scalars())

    return asyncio.run(go())


def update_engagement(Session, engagement_id, **values):
    async def go():
        async with Session() as session:
            eng = await session.get(VendorEngagement, engagement_id)
            for key, value in values.items():
                setattr(eng, key, value)
            await session.commit()

    asyncio.run(go())


# ── Create, list, show ────────────────────────────────────


def test_create_returns_invite_once_and_stores_only_the_hash(Session):
    created = create()
    token = created["invite_token"]

    assert created["vendor_name"] == "Okta"
    assert created["status"] == "invited"
    assert created["documents"] == []
    assert created["invite_path"] == f"/portal/{token}"
    assert created["invite_url"] == f"http://localhost:5173/portal/{token}"
    assert "invite_token_hash" not in created

    [eng] = rows(Session, VendorEngagement)
    assert eng.org_id == str(settings.default_org_id)
    assert eng.invite_token_hash == hashlib.sha256(token.encode()).hexdigest()
    assert token not in eng.invite_token_hash
    expires = eng.invite_expires_at.replace(tzinfo=UTC)
    expected = datetime.now(UTC) + timedelta(hours=settings.invite_ttl_hours)
    assert abs((expires - expected).total_seconds()) < 60


def test_create_rejects_org_id_in_body(Session):
    response = call("POST", "/api/v1/engagements", json={**BODY, "org_id": OTHER_ORG})
    assert response.status_code == 422
    assert rows(Session, VendorEngagement) == []


@pytest.mark.parametrize(
    "body",
    [
        {"vendor_name": "Okta"},
        {"vendor_name": "  ", "company_name": "Acme"},
        {"vendor_name": "x" * 256, "company_name": "Acme"},
    ],
)
def test_create_validates_body(Session, body):
    assert call("POST", "/api/v1/engagements", json=body).status_code == 422


def test_list_and_detail_never_expose_tokens(Session, storage):
    created = create()
    token = created["invite_token"]
    assert upload(token).status_code == 201

    listing = call("GET", "/api/v1/engagements")
    detail = call("GET", f"/api/v1/engagements/{created['id']}")
    assert listing.status_code == detail.status_code == 200

    [eng] = rows(Session, VendorEngagement)
    for response in (listing, detail):
        assert token not in response.text
        assert eng.invite_token_hash not in response.text
        assert "invite_token" not in response.text
    [item] = listing.json()
    assert item["id"] == created["id"]
    assert [d["parse_status"] for d in item["documents"]] == ["pending"]
    assert detail.json()["documents"][0]["filename"] == "soc2.pdf"


def test_detail_unknown_engagement_404(Session):
    response = call("GET", "/api/v1/engagements/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 404


# ── Re-issue invite ───────────────────────────────────────


def test_reissue_invite_replaces_the_old_link(Session):
    created = create()
    old = created["invite_token"]

    response = call("POST", f"/api/v1/engagements/{created['id']}/invite")
    assert response.status_code == 200
    new = response.json()["invite_token"]
    assert new != old

    assert call("GET", f"/api/v1/portal/{old}").status_code == 404
    assert call("GET", f"/api/v1/portal/{new}").status_code == 200


def test_reissue_invite_renews_an_expired_link(Session):
    created = create()
    update_engagement(
        Session, created["id"], invite_expires_at=datetime.now(UTC) - timedelta(hours=1)
    )
    new = call("POST", f"/api/v1/engagements/{created['id']}/invite").json()[
        "invite_token"
    ]
    assert call("GET", f"/api/v1/portal/{new}").status_code == 200


# ── Portal ────────────────────────────────────────────────


def test_portal_shows_only_its_engagement(Session, storage):
    a = create()
    b = create({"vendor_name": "Datadog", "company_name": "Acme Manufacturing Corp"})
    assert upload(b["invite_token"]).status_code == 201

    response = call("GET", f"/api/v1/portal/{a['invite_token']}")
    assert response.status_code == 200
    body = response.json()
    assert body["vendor_name"] == "Okta"
    assert body["documents"] == []
    assert "id" not in body
    assert "invite_token_hash" not in response.text


@pytest.mark.parametrize("token", ["not-a-real-token", "x" * 43])
def test_portal_unknown_token_404(Session, token):
    create()
    assert call("GET", f"/api/v1/portal/{token}").status_code == 404
    assert upload(token).status_code == 404


def test_portal_expired_token_404(Session, storage):
    created = create()
    update_engagement(
        Session,
        created["id"],
        invite_expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    token = created["invite_token"]
    assert call("GET", f"/api/v1/portal/{token}").status_code == 404
    assert upload(token).status_code == 404
    assert rows(Session, VendorDocument) == []


def test_portal_closed_engagement_404(Session, storage):
    created = create()
    update_engagement(Session, created["id"], status="closed")
    assert call("GET", f"/api/v1/portal/{created['invite_token']}").status_code == 404
    assert upload(created["invite_token"]).status_code == 404


# ── Upload ────────────────────────────────────────────────


def test_valid_upload_stores_file_and_queues_parser(Session, storage, parse_calls):
    created = create()
    response = upload(created["invite_token"], filename="../../etc/SOC 2 Report.pdf")
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["duplicate"] is False
    assert body["filename"] == "SOC 2 Report.pdf"
    assert body["size_bytes"] == len(PDF)
    assert body["sha256"] == hashlib.sha256(PDF).hexdigest()
    assert body["parse_status"] == "pending"

    [doc] = rows(Session, VendorDocument)
    assert doc.org_id == str(settings.default_org_id)
    assert doc.engagement_id == created["id"]
    assert doc.storage_key == f"{doc.org_id}/{created['id']}/{doc.id}.pdf"
    assert "SOC" not in doc.storage_key
    stored = storage / doc.storage_key
    assert stored.read_bytes() == PDF
    assert Path(stored).resolve().is_relative_to(storage.resolve())
    assert parse_calls == [doc.id]


def test_duplicate_upload_is_deduplicated_per_engagement(Session, storage, parse_calls):
    a = create()
    b = create({"vendor_name": "Datadog", "company_name": "Acme Manufacturing Corp"})
    first = upload(a["invite_token"])
    again = upload(a["invite_token"], filename="copy.pdf")
    other = upload(b["invite_token"])

    assert first.status_code == 201
    assert again.status_code == 200
    assert again.json()["duplicate"] is True
    assert again.json()["id"] == first.json()["id"]
    assert other.status_code == 201
    assert len(rows(Session, VendorDocument)) == 2
    assert len(parse_calls) == 2
    assert len([p for p in storage.rglob("*.pdf")]) == 2


@pytest.mark.parametrize(
    "data,content_type",
    [
        (b"PK\x03\x04 not a pdf", "application/pdf"),  # wrong magic bytes
        (PDF, "text/plain"),  # wrong content type
        (PDF, "application/octet-stream"),
        (b"", "application/pdf"),  # empty
    ],
)
def test_non_pdf_rejected(Session, storage, parse_calls, data, content_type):
    created = create()
    response = upload(created["invite_token"], data, content_type=content_type)
    assert response.status_code == 415
    assert rows(Session, VendorDocument) == []
    assert parse_calls == []
    assert not storage.exists() or not any(storage.rglob("*.pdf"))


def test_oversize_rejected(Session, storage, parse_calls):
    created = create()
    big = PDF + b"0" * settings.max_upload_bytes
    response = upload(created["invite_token"], big)
    assert response.status_code == 413
    assert rows(Session, VendorDocument) == []
    assert parse_calls == []


def test_upload_at_exact_limit_accepted(Session, storage):
    created = create()
    exact = PDF + b"0" * (settings.max_upload_bytes - len(PDF))
    assert upload(created["invite_token"], exact).status_code == 201


def test_upload_requires_a_file(Session):
    created = create()
    response = call("POST", f"/api/v1/portal/{created['invite_token']}/documents")
    assert response.status_code == 422


# ── Cross-engagement and cross-tenant ─────────────────────


def test_token_for_a_cannot_read_or_upload_to_b(Session, storage):
    a = create()
    b = create({"vendor_name": "Datadog", "company_name": "Acme Manufacturing Corp"})
    upload(b["invite_token"])

    view = call("GET", f"/api/v1/portal/{a['invite_token']}").json()
    assert view["vendor_name"] == "Okta" and view["documents"] == []
    # The route has no engagement id: a token only ever reaches its own
    # engagement, and anything else in the token slot is a 404.
    assert call("GET", f"/api/v1/portal/{b['id']}").status_code == 404
    assert upload(b["id"]).status_code == 404

    docs = rows(Session, VendorDocument)
    assert [d.engagement_id for d in docs] == [b["id"]]


def test_other_org_cannot_see_engagements_or_documents(Session, storage):
    created = create()
    assert upload(created["invite_token"]).status_code == 201

    as_other_org()
    listing = call("GET", "/api/v1/engagements")
    assert listing.status_code == 200 and listing.json() == []
    assert call("GET", f"/api/v1/engagements/{created['id']}").status_code == 404
    assert (
        call("POST", f"/api/v1/engagements/{created['id']}/invite").status_code == 404
    )
    assert call("GET", f"/api/v1/portal/{created['invite_token']}").status_code == 404
    assert upload(created["invite_token"], PDF + b"other").status_code == 404
    assert len(rows(Session, VendorDocument)) == 1


def test_document_from_another_org_is_not_listed(Session, storage):
    """A row with another org_id never shows up, even on our own engagement."""
    created = create()

    async def plant():
        async with Session() as session:
            session.add(
                VendorDocument(
                    org_id=OTHER_ORG,
                    engagement_id=created["id"],
                    filename="planted.pdf",
                    content_type="application/pdf",
                    size_bytes=1,
                    sha256="0" * 64,
                    storage_key="x/y/z.pdf",
                )
            )
            await session.commit()

    asyncio.run(plant())

    detail = call("GET", f"/api/v1/engagements/{created['id']}").json()
    portal = call("GET", f"/api/v1/portal/{created['invite_token']}").json()
    listing = call("GET", "/api/v1/engagements").json()
    for docs in (detail["documents"], portal["documents"], listing[0]["documents"]):
        assert docs == []


def test_engagement_from_another_org_returns_404(Session):
    """An engagement planted under another org is invisible to this org."""

    async def plant():
        async with Session() as session:
            eng = VendorEngagement(
                org_id=OTHER_ORG,
                vendor_name="Okta",
                company_name="Other Co",
                invite_token_hash=engagements.hash_token("other-token"),
                invite_expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
            session.add(eng)
            await session.commit()
            return eng.id

    other_id = asyncio.run(plant())

    assert call("GET", f"/api/v1/engagements/{other_id}").status_code == 404
    assert call("GET", "/api/v1/engagements").json() == []
    assert call("GET", "/api/v1/portal/other-token").status_code == 404
