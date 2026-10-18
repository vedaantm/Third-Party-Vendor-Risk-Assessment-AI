"""
Cache behaviour: Redis L1 (fakeredis), Supabase L2 (SQLite stand-in) and the
three-tier read/write path in routes.py, plus the auxiliary Redis features
(OFAC cache, Groq token tracking, health check).
"""

import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app.api import routes
from app.db.models import CertCache
from app.services import cache
from conftest import install_fake_redis, make_sessionmaker

CERT = {
    "certifications_found": ["SOC2_Type2", "FedRAMP"],
    "certification_evidence_tiers": {"SOC2_Type2": "CORROBORATED", "FedRAMP": "VERIFIED_REGISTRY"},
    "regulatory_frameworks_disclosed": ["GDPR"],
    "evidence": ["FedRAMP — authorized"],
    "sources_checked": ["FedRAMP Marketplace"],
    "edgar_cik": "0001660134",
}


class BrokenRedis:
    """Every command raises — simulates Redis failing mid-session."""

    def __getattr__(self, name):
        async def fail(*args, **kwargs):
            raise ConnectionError(f"redis {name} failed")
        return fail


# ── Redis L1 ──────────────────────────────────────────────

def test_set_then_get_roundtrip(monkeypatch):
    async def body():
        install_fake_redis(monkeypatch)
        await cache.set_cert_cache_redis("Okta", CERT)
        return await cache.get_cert_cache_redis("Okta")
    assert asyncio.run(body()) == CERT


def test_key_is_versioned_lowercased_and_trimmed_with_7_day_ttl(monkeypatch):
    async def body():
        client = install_fake_redis(monkeypatch)
        await cache.set_cert_cache_redis("  OKTA ", CERT)
        keys = await client.keys("*")
        ttl = await client.ttl("cert:v2:okta")
        hit = await cache.get_cert_cache_redis("okta")
        return keys, ttl, hit
    keys, ttl, hit = asyncio.run(body())
    assert keys == ["cert:v2:okta"]
    assert 7 * 86400 - 5 <= ttl <= 7 * 86400
    assert hit == CERT


def test_old_v1_keys_are_ignored(monkeypatch):
    async def body():
        client = install_fake_redis(monkeypatch)
        await client.set("cert:v1:okta", json.dumps(CERT))
        return await cache.get_cert_cache_redis("Okta")
    assert asyncio.run(body()) is None


def test_miss_returns_none(monkeypatch):
    async def body():
        install_fake_redis(monkeypatch)
        return await cache.get_cert_cache_redis("Nobody")
    assert asyncio.run(body()) is None


def test_invalidate_removes_entry(monkeypatch):
    async def body():
        install_fake_redis(monkeypatch)
        await cache.set_cert_cache_redis("Okta", CERT)
        await cache.invalidate_cert_cache("Okta")
        return await cache.get_cert_cache_redis("Okta")
    assert asyncio.run(body()) is None


def test_read_error_returns_none_instead_of_raising(monkeypatch):
    async def body():
        install_fake_redis(monkeypatch, BrokenRedis())
        return await cache.get_cert_cache_redis("Okta")
    assert asyncio.run(body()) is None


def test_write_error_does_not_raise(monkeypatch):
    async def body():
        install_fake_redis(monkeypatch, BrokenRedis())
        await cache.set_cert_cache_redis("Okta", CERT)
    asyncio.run(body())


def test_corrupt_entry_returns_none(monkeypatch):
    async def body():
        client = install_fake_redis(monkeypatch)
        await client.set("cert:v2:okta", "{not json")
        return await cache.get_cert_cache_redis("Okta")
    assert asyncio.run(body()) is None


def test_redis_down_get_returns_none_and_set_is_noop():
    # conftest default: Redis marked unavailable
    async def body():
        await cache.set_cert_cache_redis("Okta", CERT)
        return await cache.get_cert_cache_redis("Okta")
    assert asyncio.run(body()) is None


class FakeRedisModule:
    """Stands in for redis.asyncio.from_url; ping fails until `up` is set."""

    def __init__(self):
        self.up = False
        self.connects = 0

    def from_url(self, url, **kwargs):
        self.connects += 1
        module = self

        class Client:
            async def ping(self):
                if not module.up:
                    raise ConnectionError("connection refused")
                return True
        return Client()


@pytest.fixture
def unchecked_redis(monkeypatch):
    fake = FakeRedisModule()
    monkeypatch.setattr("redis.asyncio.from_url", fake.from_url)
    monkeypatch.setattr(cache, "_redis_available", None)
    monkeypatch.setattr(cache, "_redis_client", None)
    return fake


def test_connection_failure_degrades_to_none(unchecked_redis):
    assert asyncio.run(cache._get_redis()) is None
    assert cache._redis_available is False


def test_successful_connection_is_reused(unchecked_redis):
    unchecked_redis.up = True

    async def body():
        return await cache._get_redis(), await cache._get_redis()
    first, second = asyncio.run(body())
    assert first is not None and first is second
    assert unchecked_redis.connects == 1


@pytest.mark.xfail(strict=True, reason=(
    "cache.py gap: after one failed connection _redis_available is set to "
    "False permanently, so Redis is never retried until the process "
    "restarts. Desired: a later call reconnects once Redis is back (a "
    "retry backoff is fine — adjust this test's timing if one is added)."
))
def test_redis_is_retried_after_it_comes_back(unchecked_redis):
    async def body():
        assert await cache._get_redis() is None
        unchecked_redis.up = True
        return await cache._get_redis()
    assert asyncio.run(body()) is not None


@pytest.mark.xfail(strict=True, reason=(
    "cache.py gap: invalidate_cert_cache has no try/except around "
    "client.delete, unlike every other Redis helper, so a Redis error "
    "propagates to the caller. Desired: log and return, like the others."
))
def test_invalidate_does_not_raise_when_redis_errors(monkeypatch):
    async def body():
        install_fake_redis(monkeypatch, BrokenRedis())
        await cache.invalidate_cert_cache("Okta")
    asyncio.run(body())


# ── Auxiliary Redis features ──────────────────────────────

def test_ofac_cache_roundtrip(monkeypatch):
    async def body():
        install_fake_redis(monkeypatch)
        before = await cache.get_ofac_cache("Okta")
        await cache.set_ofac_cache("Okta", True)
        await cache.set_ofac_cache("Bad Co", False)
        return before, await cache.get_ofac_cache("okta"), await cache.get_ofac_cache("Bad Co")
    assert asyncio.run(body()) == (None, True, False)


def test_groq_token_tracking_and_wait(monkeypatch):
    async def body():
        install_fake_redis(monkeypatch)
        assert await cache.should_wait_for_rate_limit(2000) == 0.0
        await cache.track_groq_tokens(11_000)
        used = await cache.get_groq_tokens_used()
        wait = await cache.should_wait_for_rate_limit(2000)
        return used, wait
    used, wait = asyncio.run(body())
    assert used == 11_000
    assert 1.0 <= wait <= 61.0


def test_groq_token_tracking_without_redis_is_safe():
    async def body():
        await cache.track_groq_tokens(500)
        return await cache.get_groq_tokens_used(), await cache.should_wait_for_rate_limit(99_999)
    assert asyncio.run(body()) == (0, 5.0)


def test_health_healthy(monkeypatch):
    import fakeredis

    class FakeRedisWithInfo(fakeredis.FakeAsyncRedis):
        # fakeredis doesn't implement INFO; real Redis returns a dict
        async def info(self, section=None):
            return {"redis_version": "7.2.4"}

    async def body():
        install_fake_redis(monkeypatch, FakeRedisWithInfo(decode_responses=True))
        return await cache.redis_health()
    health = asyncio.run(body())
    assert health["status"] == "healthy"
    assert health["mode"] == "redis_primary"
    assert health["version"] == "7.2.4"
    assert health["latency_ms"] >= 0


def test_health_unavailable():
    assert asyncio.run(cache.redis_health()) == {"status": "unavailable", "mode": "supabase_only"}


def test_health_error(monkeypatch):
    async def body():
        install_fake_redis(monkeypatch, BrokenRedis())
        return await cache.redis_health()
    health = asyncio.run(body())
    assert health["status"] == "error" and health["mode"] == "supabase_only"


# ── Supabase L2 and the three-tier path (routes.py) ───────

async def seed_row(Session, vendor_key, cached_at, data=CERT):
    async with Session() as s:
        s.add(CertCache(vendor_name=vendor_key, cached_at=cached_at, **{
            k: data[k] for k in (
                "certifications_found", "certification_evidence_tiers",
                "regulatory_frameworks_disclosed", "evidence", "sources_checked", "edgar_cik",
            )
        }))
        await s.commit()


def now():
    return datetime.now(timezone.utc)


def test_l2_hit_returns_data_and_warms_redis(monkeypatch, db_url):
    async def body():
        client = install_fake_redis(monkeypatch)
        _, Session = await make_sessionmaker(db_url)
        await seed_row(Session, "okta", now() - timedelta(days=1))
        async with Session() as s:
            data = await routes._read_cert_cache("Okta", s)
        return data, await client.get("cert:v2:okta")
    data, warmed = asyncio.run(body())
    assert data == CERT
    assert json.loads(warmed) == CERT


def test_l2_hit_works_with_redis_down(db_url):
    async def body():
        _, Session = await make_sessionmaker(db_url)
        await seed_row(Session, "okta", now() - timedelta(days=1))
        async with Session() as s:
            return await routes._read_cert_cache("Okta", s)
    assert asyncio.run(body()) == CERT


def test_l1_hit_skips_database(monkeypatch):
    class NoDB:
        async def execute(self, *a, **k):
            raise AssertionError("database should not be queried on a Redis hit")

    async def body():
        install_fake_redis(monkeypatch)
        await cache.set_cert_cache_redis("Okta", CERT)
        return await routes._read_cert_cache("Okta", NoDB())
    assert asyncio.run(body()) == CERT


@pytest.mark.parametrize("age_days, expect_hit", [(6, True), (8, False)])
def test_l2_rows_older_than_7_days_are_expired(db_url, age_days, expect_hit):
    async def body():
        _, Session = await make_sessionmaker(db_url)
        await seed_row(Session, "okta", now() - timedelta(days=age_days))
        async with Session() as s:
            return await routes._read_cert_cache("Okta", s)
    assert (asyncio.run(body()) is not None) is expect_hit


@pytest.mark.parametrize("offset_hours, expect_hit", [(-1, False), (1, True)])
def test_l2_rows_before_cert_cache_valid_after_are_expired(monkeypatch, db_url, offset_hours, expect_hit):
    cutoff = now() - timedelta(days=2)
    monkeypatch.setattr(routes, "CERT_CACHE_VALID_AFTER", cutoff)

    async def body():
        _, Session = await make_sessionmaker(db_url)
        await seed_row(Session, "okta", cutoff + timedelta(hours=offset_hours))
        async with Session() as s:
            return await routes._read_cert_cache("Okta", s)
    assert (asyncio.run(body()) is not None) is expect_hit


def test_l2_miss_returns_none(db_url):
    async def body():
        _, Session = await make_sessionmaker(db_url)
        async with Session() as s:
            return await routes._read_cert_cache("Nobody", s)
    assert asyncio.run(body()) is None


def test_write_goes_to_both_tiers_and_upserts(monkeypatch, db_url):
    updated = {**CERT, "certifications_found": ["ISO27001"], "edgar_cik": None}

    async def body():
        client = install_fake_redis(monkeypatch)
        _, Session = await make_sessionmaker(db_url)
        async with Session() as s:
            await routes._write_cert_cache("Okta", CERT, s)
            await s.commit()
        async with Session() as s:
            await routes._write_cert_cache("  OKTA ", updated, s)
            await s.commit()
        async with Session() as s:
            count = await s.scalar(select(func.count()).select_from(CertCache))
            row = await s.get(CertCache, "okta")
        return count, row, json.loads(await client.get("cert:v2:okta"))
    count, row, redis_value = asyncio.run(body())
    assert count == 1
    assert row.certifications_found == ["ISO27001"] and row.edgar_cik is None
    assert redis_value == updated


def test_cert_cache_valid_after_parsing():
    parse = routes._parse_cert_cache_valid_after
    assert parse(None) is None and parse("") is None and parse("  ") is None
    assert parse("2026-10-20") == datetime(2026, 10, 20, tzinfo=timezone.utc)
    assert parse("2026-10-20T09:00:00+05:30").utcoffset() == timedelta(hours=5, minutes=30)
    with pytest.raises(ValueError, match="CERT_CACHE_VALID_AFTER"):
        parse("next tuesday")
