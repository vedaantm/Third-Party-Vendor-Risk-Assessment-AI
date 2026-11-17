# backend/app/settings.py

"""
Application configuration — the one place the backend reads its environment.

Values come from real environment variables first, then backend/.env.
(Previously app/db/database.py loaded .env with override=True, so .env beat
the real environment; that is how a test once ended up pointing at the real
ChromaDB.) Every variable is documented in backend/.env.example.

Missing required values fail at import with a message naming each one,
rather than a KeyError deep inside the first request that needs it.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env",   # relative to the code, not the cwd
        env_file_encoding="utf-8",
        extra="ignore",                  # .env may hold unrelated keys (e.g. PYTHONPATH)
    )

    # ── Required ──────────────────────────────────────────
    groq_api_key: SecretStr
    database_url: SecretStr = Field(description="SQLAlchemy URL, e.g. postgresql+asyncpg://…")

    # ── Optional ──────────────────────────────────────────
    # TLS to Postgres: Supabase requires it; the local docker-compose
    # Postgres has no certificate, so the stack sets DATABASE_SSL=disable.
    database_ssl: Literal["require", "disable"] = "require"
    # Logging: one JSON object per line by default; "console" is easier to
    # read during local development.
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_format: Literal["json", "console"] = "json"
    # Agents' scoring call uses a JSON schema (structured output); false
    # falls back to the plain-text call, for eval comparisons.
    agent_structured_output: bool = True
    redis_url: str = "redis://localhost:6379"
    chroma_path: str = "./chroma_db"
    # Comma-separated; parsed by main_api._cors_origins (blank → default origin)
    cors_allow_origins: str | None = None
    # ISO date/datetime; parsed by routes._parse_cert_cache_valid_after
    cert_cache_valid_after: str | None = None
    csa_star_api_key: SecretStr | None = None


class SettingsError(RuntimeError):
    """Configuration is missing or invalid."""


def _describe(error: ValidationError) -> str:
    lines = []
    for item in error.errors():
        name = ".".join(str(p) for p in item["loc"]).upper()
        problem = "is required but not set" if item["type"] == "missing" else item["msg"]
        lines.append(f"  - {name} {problem}")
    return (
        "VendorIQ configuration is invalid:\n" + "\n".join(lines) +
        f"\nSet them in the environment or in {BACKEND_DIR / '.env'} "
        "(see backend/.env.example)."
    )


@lru_cache
def get_settings() -> Settings:
    try:
        return Settings()
    except ValidationError as e:
        raise SettingsError(_describe(e)) from None


settings = get_settings()
