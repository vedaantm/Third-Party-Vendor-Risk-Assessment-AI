"""
Shared test configuration.

Offline by default: every test except those marked `live` runs with outbound
network blocked (loopback only), a throwaway ChromaDB, in-memory Redis
(fakeredis) and a temporary SQLite database in place of Supabase.

Run live tests explicitly:   python -m pytest backend/tests/ -m live
"""

import os
import socket
import tempfile
from pathlib import Path

import pytest

# ── Environment — must be set before any `app.*` import ───────────────────
# app.settings reads real environment variables before backend/.env, so
# setting EVERY Settings field here means no value from your .env — not the
# real DATABASE_URL, not the real CHROMA_PATH — ever reaches a test.
# tests/test_settings.py checks this list covers every field.

def _fresh_test_chroma_dir() -> str:
    """
    A throwaway ChromaDB directory under one fixed parent. ChromaDB keeps its
    SQLite file open until the process exits, so on Windows a run cannot
    delete its own directory; instead each run deletes the previous runs'.
    """
    import shutil
    parent = Path(tempfile.gettempdir()) / "vendorrisk-test-chroma"
    if parent.exists():
        for old in parent.iterdir():
            shutil.rmtree(old, ignore_errors=True)
    parent.mkdir(exist_ok=True)
    return tempfile.mkdtemp(dir=parent)


_TEST_CHROMA = _fresh_test_chroma_dir()


def _real_groq_key() -> str | None:
    """For live tests only: a real key from the shell (e.g. CI) or backend/.env."""
    key = os.environ.get("GROQ_API_KEY")
    if not key:
        from dotenv import dotenv_values
        key = dotenv_values(Path(__file__).resolve().parents[1] / ".env").get("GROQ_API_KEY")
    return key if key and not key.startswith("test-key") else None


REAL_GROQ_API_KEY = _real_groq_key()   # captured before TEST_ENV overwrites it

TEST_ENV = {
    "GROQ_API_KEY": "test-key-not-used",          # live tests swap in the real key
    "DATABASE_URL": "postgresql+asyncpg://test:test@127.0.0.1:1/test",
    "REDIS_URL": "redis://127.0.0.1:1/0",         # nothing listens on port 1
    "CHROMA_PATH": _TEST_CHROMA,
    "CORS_ALLOW_ORIGINS": "",                     # blank → default; tests assert it
    "CERT_CACHE_VALID_AFTER": "",                 # blank → no cutoff
    "CSA_STAR_API_KEY": "",
    "DATABASE_SSL": "require",                    # the production default
    "LOG_LEVEL": "WARNING",                       # quiet test output
    "LOG_FORMAT": "json",
    "AGENT_STRUCTURED_OUTPUT": "true",
}
os.environ.update(TEST_ENV)

FIXTURES = Path(__file__).parent / "fixtures"


# ── Live marker: opt-in, skipped by default ───────────────────────────────

def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "live: calls real external services (Groq, SEC, FedRAMP, OFAC); "
        "skipped unless selected with -m live",
    )


def pytest_collection_modifyitems(config, items):
    markexpr = config.getoption("markexpr") or ""
    if "live" in markexpr and "not live" not in markexpr:
        return
    skip = pytest.mark.skip(reason="live test — run with: -m live")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)


# ── Network guard ─────────────────────────────────────────────────────────

_LOOPBACK = {"127.0.0.1", "::1", "localhost"}


@pytest.fixture(autouse=True)
def _block_network(request, monkeypatch):
    """Fail loudly if an offline test tries to reach the internet."""
    if request.node.get_closest_marker("live"):
        return

    real_connect, real_connect_ex = socket.socket.connect, socket.socket.connect_ex

    def _check(address):
        host = address[0] if isinstance(address, tuple) else address
        if host not in _LOOPBACK:
            raise RuntimeError(f"Network access blocked in offline test: {address!r}")

    def connect(self, address):
        _check(address)
        return real_connect(self, address)

    def connect_ex(self, address):
        _check(address)
        return real_connect_ex(self, address)

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)


# ── Redis: "down" unless a test installs fakeredis ────────────────────────

@pytest.fixture(autouse=True)
def _redis_down_by_default(monkeypatch):
    from app.services import cache
    monkeypatch.setattr(cache, "_redis_client", None)
    monkeypatch.setattr(cache, "_redis_available", False)
    # Never reconnect on its own — REDIS_URL from backend/.env may point at a
    # real local Redis, which the loopback-only network guard would allow.
    monkeypatch.setattr(cache, "_redis_retry_at", float("inf"))


@pytest.fixture
def real_groq_key(monkeypatch):
    """Live tests: point settings at the real Groq key, or skip without one."""
    if not REAL_GROQ_API_KEY:
        pytest.skip("no real GROQ_API_KEY in the environment or backend/.env")
    from pydantic import SecretStr

    from app.settings import settings
    monkeypatch.setattr(settings, "groq_api_key", SecretStr(REAL_GROQ_API_KEY))


def install_fake_redis(monkeypatch, client=None):
    """
    Call from INSIDE the test's event loop — redis asyncio clients bind to
    the loop they are first used on.
    """
    import fakeredis

    from app.services import cache

    client = client or fakeredis.FakeAsyncRedis(decode_responses=True)
    monkeypatch.setattr(cache, "_redis_client", client)
    monkeypatch.setattr(cache, "_redis_available", True)
    return client


# ── Database: temporary SQLite in place of Supabase ───────────────────────

@pytest.fixture
def db_url(tmp_path):
    return f"sqlite+aiosqlite:///{(tmp_path / 'test.db').as_posix()}"


async def make_sessionmaker(db_url: str):
    """
    Create the schema and return an AsyncSession factory. NullPool means no
    connection outlives the event loop it was opened on.
    """
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import NullPool

    import app.db.models  # register tables
    from app.db.database import Base

    engine = create_async_engine(db_url, poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return engine, sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
