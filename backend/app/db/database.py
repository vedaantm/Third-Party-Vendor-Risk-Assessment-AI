# backend/app/db/database.py
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from sqlalchemy.pool import NullPool

from app.settings import settings


def build_engine(url: str, *, pooled: bool = True, ssl: str | None = None) -> AsyncEngine:
    """
    The app's engine settings for a given URL. Postgres (asyncpg) URLs get
    the Supabase options; anything else (e.g. SQLite in tests) is left alone.
    pooled=False is for one-off processes such as migrations. ssl is
    "require" or "disable" and defaults to the DATABASE_SSL setting.
    """
    if not url.startswith("postgresql+asyncpg"):
        return create_async_engine(url, poolclass=NullPool)

    # asyncpg through SQLAlchemy requires ssl passed as connect_args,
    # not as a query parameter — strip it from the URL if present.
    url = url.replace("?ssl=require", "").replace("&ssl=require", "")
    connect_args: dict = {
        "statement_cache_size": 0,  # required for Supabase Transaction Pooler (pgbouncer)
    }
    if (ssl or settings.database_ssl) == "require":
        connect_args["ssl"] = "require"
    if not pooled:
        return create_async_engine(url, poolclass=NullPool, connect_args=connect_args)
    return create_async_engine(
        url,
        echo=False,
        pool_size=5,
        max_overflow=10,
        pool_pre_ping=True,
        pool_recycle=300,
        connect_args=connect_args,
    )


engine = build_engine(settings.database_url.get_secret_value())

AsyncSessionLocal = sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False
)

class Base(DeclarativeBase):
    pass

async def get_db():
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()