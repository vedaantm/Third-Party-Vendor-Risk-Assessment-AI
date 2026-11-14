# backend/Dockerfile

FROM python:3.11-slim

# uv installs exactly the versions pinned in uv.lock
COPY --from=ghcr.io/astral-sh/uv:0.13.7 /uv /uvx /bin/

WORKDIR /app

# System libraries needed for lxml (your scraper uses it)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libxml2-dev \
    libxslt1-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Python packages first (this layer gets cached —
# rebuilds are fast unless pyproject.toml or uv.lock change).
# Into the image's system Python, so `uvicorn` stays on PATH; no dev group.
ENV UV_PROJECT_ENVIRONMENT=/usr/local \
    UV_PYTHON_DOWNLOADS=never \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --locked --no-dev

# Now copy your actual code
COPY . .

EXPOSE 8000

# Apply migrations, then start the API. A database created by the old
# create_all startup step is refused until stamped (see migrations/versions/0001_baseline.py).
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn main_api:app --host 0.0.0.0 --port 8000 --loop asyncio"]