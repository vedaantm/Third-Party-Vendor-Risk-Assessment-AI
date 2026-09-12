# backend/app/db/database.py
import os
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from dotenv import load_dotenv

load_dotenv(override=True)

_raw_url = os.environ["DATABASE_URL"]

# asyncpg through SQLAlchemy requires ssl passed as connect_args,
# not as a query parameter — strip it from the URL if present.
_db_url = _raw_url.replace("?ssl=require", "").replace("&ssl=require", "")

engine = create_async_engine(
    _db_url,
    echo=False,
    pool_size=5,
    max_overflow=10,
    pool_pre_ping=True,
    pool_recycle=300,
    connect_args={
        "ssl": "require",
        "statement_cache_size": 0,  # required for Supabase Transaction Pooler (pgbouncer)
    },
)

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