"""
backend/app/services/cache.py

Redis Cache Layer
=================

WHY REDIS?
    Currently, every cert cache read goes to Supabase (PostgreSQL) in AWS us-east-1.
    That's a network round trip — typically 60-100ms per read.

    Redis is an in-memory key-value store running locally (or in the same network).
    Reads from Redis take < 1ms. That's 60-100x faster.

    For a cert cache that's checked on EVERY evaluation, this matters.

WHAT WE CACHE IN REDIS:
    1. Cert discovery results (7-day TTL, same as Supabase)
    2. Groq per-minute token counts (60-second TTL, for precise rate limiting)
    3. OFAC check results (24-hour TTL, list doesn't change frequently)

THREE-TIER CACHE STRATEGY:
    Read path:  Redis (L1, fast) → Supabase (L2, persistent) → Run pipeline (cache miss)
    Write path: Redis (immediate) + Supabase (persistent backup)

    This means:
    - If Redis restarts, Supabase still has the data
    - If Supabase is slow, Redis serves the cache
    - Both caches are invalidated together on write

GRACEFUL DEGRADATION:
    If Redis is not running, the system falls back to Supabase-only mode.
    This means development works without Docker.
    Set REDIS_URL=redis://localhost:6379 in .env to enable.
"""

import json
import os
import asyncio
import time
from typing import Any
from datetime import timedelta

# We import redis lazily so the app starts even if redis is not installed
_redis_client = None
_redis_available = None   # None = not checked yet, True/False = checked

# After a failed connection, wait this long before trying again. Without a
# retry, one failure (e.g. Redis starting after the app) disabled Redis until
# the process restarted; retrying on every call would instead add the 2s
# connect timeout to every cache read while Redis is down.
REDIS_RETRY_INTERVAL_SECONDS = 30.0
_redis_retry_at = 0.0     # time.monotonic() after which a reconnect is allowed


async def _get_redis():
    """
    Get or create the Redis client.
    Returns None if Redis is not available (graceful degradation).
    """
    global _redis_client, _redis_available, _redis_retry_at

    if _redis_client is not None:
        return _redis_client

    if _redis_available is False and time.monotonic() < _redis_retry_at:
        return None   # Down recently — don't retry on every call

    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379")

    try:
        import redis.asyncio as aioredis

        client = aioredis.from_url(
            redis_url,
            decode_responses=True,  # Return strings, not bytes
            socket_connect_timeout=2,  # Don't wait long if Redis is down
        )

        # Test connection
        await client.ping()

        _redis_client   = client
        _redis_available = True
        print(f"✅ Redis connected: {redis_url}")
        return client

    except Exception as e:
        _redis_available = False
        _redis_retry_at = time.monotonic() + REDIS_RETRY_INTERVAL_SECONDS
        print(f"⚠ Redis not available ({e}) — using Supabase-only cache mode, "
              f"retrying in {REDIS_RETRY_INTERVAL_SECONDS:.0f}s")
        return None


# ─── Cert Cache (Redis L1) ────────────────────────────────────────────────────

CERT_CACHE_TTL_SECONDS = 7 * 24 * 60 * 60   # 7 days, same as Supabase


async def get_cert_cache_redis(vendor_name: str) -> dict | None:
    """
    Try to get cert discovery results from Redis.
    Returns None if not in Redis (caller should check Supabase next).

    Cache key: cert:v2:{vendor_name_normalized}
    The version prefix invalidates the entire cache when the data format or
    discovery logic changes (v2: stricter registry name matching).
    """
    client = await _get_redis()
    if not client:
        return None   # Redis down — skip to Supabase

    key = f"cert:v2:{vendor_name.lower().strip()}"

    try:
        raw = await client.get(key)
        if raw is None:
            return None   # Cache miss

        data = json.loads(raw)
        print(f"  ✅ Redis cert cache HIT for '{vendor_name}' (<1ms)")
        return data

    except Exception as e:
        print(f"  ⚠ Redis read error: {e}")
        return None   # Fall back to Supabase


async def set_cert_cache_redis(vendor_name: str, cert_result: dict) -> None:
    """
    Store cert discovery results in Redis with 7-day TTL.
    Called alongside the Supabase write so both caches are in sync.
    """
    client = await _get_redis()
    if not client:
        return   # Redis down — Supabase write still happens

    key = f"cert:v2:{vendor_name.lower().strip()}"

    try:
        await client.setex(
            key,
            CERT_CACHE_TTL_SECONDS,
            json.dumps(cert_result),
        )
        print(f"  ✅ Redis cert cache SET for '{vendor_name}' (7-day TTL)")

    except Exception as e:
        print(f"  ⚠ Redis write error: {e}")


async def invalidate_cert_cache(vendor_name: str) -> None:
    """
    Delete a vendor's cert cache from Redis.
    Call this when you want to force a fresh cert discovery on next evaluation.
    """
    client = await _get_redis()
    if not client:
        return

    key = f"cert:v2:{vendor_name.lower().strip()}"
    try:
        await client.delete(key)
        print(f"  ✅ Redis cert cache CLEARED for '{vendor_name}'")
    except Exception as e:
        print(f"  ⚠ Redis delete error: {e}")


# ─── Token Rate Limiter (Redis) ───────────────────────────────────────────────

async def track_groq_tokens(tokens_used: int, model: str = "llama-3.3-70b") -> None:
    """
    Track Groq token usage in Redis for precise rate limit management.
    Redis key expires after 60 seconds (one minute window).

    This replaces the blind sleep-based approach with precise tracking:
        Before: await asyncio.sleep(10)   ← guesswork
        After:  track actual token usage, wait only as long as needed

    Args:
        tokens_used: Number of tokens used in this call
        model: Which Groq model was used (different models have different limits)
    """
    client = await _get_redis()
    if not client:
        return   # No Redis — can't track, rely on retry logic

    key = f"groq:tokens:{model}:minute"

    try:
        pipe = client.pipeline()
        pipe.incrby(key, tokens_used)
        pipe.expire(key, 60)   # Reset after 1 minute
        await pipe.execute()

    except Exception as e:
        pass   # Non-critical — rate limit tracking is best-effort


async def get_groq_tokens_used(model: str = "llama-3.3-70b") -> int:
    """
    Get tokens used this minute for a model.
    Returns 0 if Redis is down (safe default).
    """
    client = await _get_redis()
    if not client:
        return 0

    key = f"groq:tokens:{model}:minute"
    try:
        val = await client.get(key)
        return int(val) if val else 0
    except Exception:
        return 0


async def should_wait_for_rate_limit(
    tokens_needed: int,
    model: str = "llama-3.3-70b",
    limit_per_minute: int = 12000,
) -> float:
    """
    Check if we need to wait before making a Groq API call.

    Before: await asyncio.sleep(10)   ← wastes time when budget is available
    After:  only wait if actually needed, for exactly as long as needed

    Returns:
        0.0 if can proceed immediately
        N   if should wait N seconds before calling

    Usage:
        wait = await should_wait_for_rate_limit(tokens_needed=2500)
        if wait > 0:
            await asyncio.sleep(wait)
    """
    used = await get_groq_tokens_used(model)

    if used + tokens_needed <= limit_per_minute:
        return 0.0   # Plenty of budget — proceed immediately

    # Need to wait — get the TTL of the tracking key
    client = await _get_redis()
    if not client:
        return 5.0   # Safe default without Redis

    key = f"groq:tokens:{model}:minute"
    try:
        ttl = await client.ttl(key)
        return max(1.0, float(ttl) + 1.0)   # Wait until window resets + 1 second buffer
    except Exception:
        return 5.0


# ─── OFAC Cache ───────────────────────────────────────────────────────────────

OFAC_CACHE_TTL_SECONDS = 24 * 60 * 60   # 24 hours


async def get_ofac_cache(vendor_name: str) -> bool | None:
    """
    Cache OFAC check results for 24 hours.
    The OFAC SDN list doesn't change every minute — caching this is safe.

    Returns:
        True  if vendor is known to be CLEAR
        False if vendor is known to be FLAGGED
        None  if no cached result (run the check)
    """
    client = await _get_redis()
    if not client:
        return None

    key = f"ofac:v1:{vendor_name.lower().strip()}"
    try:
        val = await client.get(key)
        if val is None:
            return None
        return val == "clear"
    except Exception:
        return None


async def set_ofac_cache(vendor_name: str, is_clear: bool) -> None:
    """Store OFAC check result for 24 hours."""
    client = await _get_redis()
    if not client:
        return

    key = f"ofac:v1:{vendor_name.lower().strip()}"
    try:
        await client.setex(key, OFAC_CACHE_TTL_SECONDS, "clear" if is_clear else "flagged")
    except Exception:
        pass


# ─── Health Check ─────────────────────────────────────────────────────────────

async def redis_health() -> dict:
    """
    Check Redis health status.
    Called by the /health endpoint so it's visible in dashboards.
    """
    client = await _get_redis()
    if not client:
        return {"status": "unavailable", "mode": "supabase_only"}

    try:
        latency_start = asyncio.get_event_loop().time()
        await client.ping()
        latency_ms = (asyncio.get_event_loop().time() - latency_start) * 1000

        info = await client.info("server")
        return {
            "status":     "healthy",
            "latency_ms": round(latency_ms, 2),
            "version":    info.get("redis_version", "unknown"),
            "mode":       "redis_primary",
        }
    except Exception as e:
        return {"status": "error", "error": str(e), "mode": "supabase_only"}
