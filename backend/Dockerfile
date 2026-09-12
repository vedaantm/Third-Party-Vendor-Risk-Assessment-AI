# backend/Dockerfile

FROM python:3.11-slim

WORKDIR /app

# System libraries needed for lxml (your scraper uses it)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libxml2-dev \
    libxslt1-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Python packages first (this layer gets cached —
# rebuilds are fast unless requirements.txt changes)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Now copy your actual code
COPY . .

EXPOSE 8000

CMD ["uvicorn", "main_api:app", "--host", "0.0.0.0", "--port", "8000", "--loop", "asyncio"]