# backend/main_api.py

import math
import re
import time
import uuid
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.log import configure_logging, get_logger
from app.settings import settings

# Before importing the rest of the app: some modules log at import time
configure_logging(settings.log_level, settings.log_format)

from app.api.routes import router
from app.db.database import engine

log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    The schema is managed by Alembic migrations (backend/migrations), not
    created here: run `uv run alembic upgrade head` (the Docker image does
    this before starting the API). See docs/adr/0001-schema-migrations.md.
    """
    yield
    # Cleanup on shutdown
    await engine.dispose()


app = FastAPI(
    title="Vendor Risk Platform",
    description="Agentic AI-based vendor risk evaluation API",
    version="1.0.0",
    lifespan=lifespan
)

DEFAULT_CORS_ORIGINS = "http://localhost:5173"   # Vite dev server / docker frontend


def _cors_origins(raw: str | None) -> list[str]:
    """
    CORS_ALLOW_ORIGINS: comma-separated origins allowed to call the API from
    a browser, e.g. "http://localhost:5173,https://vendoriq.example.com".
    Unset or blank → DEFAULT_CORS_ORIGINS. Trailing slashes are removed,
    since browsers send the Origin header without one.
    """
    value = raw if raw and raw.strip() else DEFAULT_CORS_ORIGINS
    return [o.strip().rstrip("/") for o in value.split(",") if o.strip()]


# CORS — only the configured frontend origins (previously "*")
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(settings.cors_allow_origins),
    allow_methods=["*"],
    allow_headers=["*"],
)

# A client-supplied X-Request-ID is kept only if it's short and plain, so a
# caller can't inject arbitrary text into the logs; otherwise we generate one.
_REQUEST_ID_OK = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


@app.middleware("http")
async def request_context(request: Request, call_next):
    """Bind request_id to every log line for this request, echo it back, log the request."""
    incoming = request.headers.get("x-request-id", "")
    request_id = incoming if _REQUEST_ID_OK.match(incoming) else uuid.uuid4().hex
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(request_id=request_id)
    started = time.perf_counter()
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        response.headers["X-Request-ID"] = request_id
        return response
    finally:
        log.info(
            "http.request",
            method=request.method,
            path=request.url.path,
            status=status,
            duration_ms=round((time.perf_counter() - started) * 1000, 1),
        )
        structlog.contextvars.clear_contextvars()


def _json_safe(value):
    """Replace NaN / ±Infinity (not valid JSON) with their string form."""
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    return value


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """
    Same 422 body as FastAPI's default handler, but JSON-safe: the default
    echoes the invalid input back, and a NaN/Infinity in it (accepted by
    Python's JSON parser) made the error response itself fail with a 500.
    """
    return JSONResponse(
        status_code=422,
        content={"detail": _json_safe(jsonable_encoder(exc.errors()))},
    )


# Register routes
app.include_router(router, prefix="/api/v1")


@app.get("/")
async def root():
    return {
        "name":    "Vendor Risk Platform",
        "version": "1.0.0",
        "status":  "running",
        "docs":    "/docs"
    }


@app.get("/health")
async def health():
    return {"status": "ok"}