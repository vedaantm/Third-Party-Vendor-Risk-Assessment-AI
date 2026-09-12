# backend/main_api.py

from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

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

# CORS — allows any frontend or tool to call this API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
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