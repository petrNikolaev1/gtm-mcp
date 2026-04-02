FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y libpq-dev gcc && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Seed taxonomy cache — 2366 keywords + 112 industries for Apollo filter mapping
RUN mkdir -p /app/apollo_filters && \
    cp -n apollo_filters/*.json /app/apollo_filters/ 2>/dev/null || true && \
    cp -n apollo_filters/*.npz /app/apollo_filters/ 2>/dev/null || true

CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
