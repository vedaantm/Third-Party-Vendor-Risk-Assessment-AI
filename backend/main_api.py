# backend/main_api.py

import math
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.db.database import engine, Base
from app.api.routes import router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Runs once on startup — creates DB tables if they don't exist.
    You don't need to run any migration commands manually.
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print("✅ Database tables ready")
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
    allow_origins=_cors_origins(os.environ.get("CORS_ALLOW_ORIGINS")),
    allow_methods=["*"],
    allow_headers=["*"],
)

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