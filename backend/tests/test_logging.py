"""
Structured logging: JSON shape, context variables, the request-ID
middleware, and the no-print() rule for backend/app.
"""

import asyncio
import io
import json
import logging
import pathlib

import httpx
import pytest
import structlog

import main_api
from app.log import configure_logging, get_logger
from app.services.cache import _redis_host
from app.settings import settings

APP_DIR = pathlib.Path(__file__).resolve().parents[1] / "app"


@pytest.fixture
def captured():
    """Route all logging into a buffer at INFO; returns a reader of parsed lines."""
    buf = io.StringIO()
    configure_logging("INFO", "json", stream=buf)
    structlog.contextvars.clear_contextvars()

    def lines():
        return [json.loads(line) for line in buf.getvalue().splitlines() if line.strip()]

    yield lines
    structlog.contextvars.clear_contextvars()
    configure_logging(settings.log_level, settings.log_format)


def request(method, url, **kwargs):
    async def go():
        transport = httpx.ASGITransport(app=main_api.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.request(method, url, **kwargs)
    return asyncio.run(go())


# ── Output shape ─────────────────────────────────────────────────────────────

def test_events_are_json_with_standard_fields(captured):
    get_logger("app.services.example").info("ofac.screened", vendor="Acme", status="clear")

    [line] = captured()
    assert line["event"] == "ofac.screened"
    assert line["level"] == "info"
    assert line["logger"] == "app.services.example"
    assert line["vendor"] == "Acme" and line["status"] == "clear"
    assert line["timestamp"].endswith("Z")


def test_bound_context_vars_appear_on_every_event(captured):
    structlog.contextvars.bind_contextvars(request_id="req-1", job_id="job-9")
    log = get_logger("t")
    log.info("one")
    log.warning("two")

    assert [(x["request_id"], x["job_id"]) for x in captured()] == [("req-1", "job-9")] * 2


def test_stdlib_loggers_share_the_json_format(captured):
    """uvicorn / sqlalchemy / alembic log through plain logging."""
    structlog.contextvars.bind_contextvars(request_id="req-2")
    logging.getLogger("uvicorn.error").warning("server %s", "starting")

    [line] = captured()
    assert line["event"] == "server starting"
    assert line["level"] == "warning" and line["logger"] == "uvicorn.error"
    assert line["request_id"] == "req-2"


def test_level_filters_lower_events(captured):
    buf = io.StringIO()
    configure_logging("WARNING", "json", stream=buf)
    log = get_logger("t")
    log.info("hidden")
    log.warning("shown")
    assert [json.loads(x)["event"] for x in buf.getvalue().splitlines()] == ["shown"]


def test_exceptions_are_rendered_into_the_event(captured):
    try:
        raise ValueError("boom")
    except ValueError:
        get_logger("t").exception("job.failed")

    [line] = captured()
    assert "ValueError: boom" in line["exception"]


def test_console_format_is_human_readable():
    buf = io.StringIO()
    try:
        configure_logging("INFO", "console", stream=buf)
        get_logger("t").info("rag.seeded", benchmarks=3)
    finally:
        configure_logging(settings.log_level, settings.log_format)
    out = buf.getvalue()
    assert "rag.seeded" in out and "benchmarks=3" in out
    with pytest.raises(json.JSONDecodeError):
        json.loads(out)


# ── Request-ID middleware ────────────────────────────────────────────────────

def test_request_id_is_generated_and_returned(captured):
    resp = request("GET", "/")
    rid = resp.headers["X-Request-ID"]
    assert len(rid) == 32 and int(rid, 16) >= 0

    [line] = [x for x in captured() if x["event"] == "http.request"]
    assert line["request_id"] == rid
    assert line["method"] == "GET" and line["path"] == "/" and line["status"] == 200
    assert isinstance(line["duration_ms"], float)


def test_valid_incoming_request_id_is_kept(captured):
    resp = request("GET", "/", headers={"X-Request-ID": "trace-abc.123_X"})
    assert resp.headers["X-Request-ID"] == "trace-abc.123_X"
    assert [x["request_id"] for x in captured() if x["event"] == "http.request"] == ["trace-abc.123_X"]


@pytest.mark.parametrize("bad", ["has space", "a" * 65, "new\\nline", "<script>", "../etc", "a,b"])
def test_invalid_incoming_request_id_is_replaced(captured, bad):
    resp = request("GET", "/", headers={"X-Request-ID": bad})
    rid = resp.headers["X-Request-ID"]
    assert rid != bad and len(rid) == 32


def test_each_request_gets_its_own_id(captured):
    a = request("GET", "/").headers["X-Request-ID"]
    b = request("GET", "/").headers["X-Request-ID"]
    assert a != b


def test_request_log_records_error_status(captured):
    request("GET", "/no-such-route")
    [line] = [x for x in captured() if x["event"] == "http.request"]
    assert line["status"] == 404


def test_request_context_does_not_leak_after_the_request(captured):
    request("GET", "/", headers={"X-Request-ID": "leak-check"})
    get_logger("t").info("after")
    assert "request_id" not in [x for x in captured() if x["event"] == "after"][0]


# ── No secrets, no print() ───────────────────────────────────────────────────

@pytest.mark.parametrize("url, expected", [
    ("redis://localhost:6379", "localhost:6379"),
    ("redis://:s3cret@cache.internal:6380/0", "cache.internal:6380"),
    ("rediss://user:s3cret@cache.internal", "cache.internal:6379"),
])
def test_redis_host_never_includes_credentials(url, expected):
    assert _redis_host(url) == expected


def test_no_print_calls_in_backend_app():
    """Plan done-criterion: grep -r "print(" backend/app returns nothing."""
    offenders = [
        f"{p.relative_to(APP_DIR).as_posix()}:{n}"
        for p in APP_DIR.rglob("*.py")
        for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
        if "print(" in line
    ]
    assert offenders == []
