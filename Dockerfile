FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# curl backs the container healthcheck; tzdata is required by zoneinfo, which
# the app uses to render the "last update" timestamp. Debian slim images do not
# ship the zone database, so an unset tzdata would make every timezone lookup
# fail at runtime.
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
        tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependencies first so application edits do not invalidate the wheel cache.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Run as an unprivileged user. The data directory is created and owned here so
# the bind-mounted volume stays writable without granting root to the process.
RUN useradd --create-home --uid 1000 dashboard \
    && mkdir -p /app/data \
    && chown -R dashboard:dashboard /app

USER dashboard

EXPOSE 8050

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8050/healthz >/dev/null || exit 1

# Gunicorn rather than the Dash development server. A single worker keeps the
# CSV read-modify-write cycle inside one process, where the in-process lock in
# storage.py is sufficient; multiple workers would need external locking.
CMD ["gunicorn", \
     "--bind", "0.0.0.0:8050", \
     "--workers", "1", \
     "--threads", "4", \
     "--timeout", "180", \
     "--access-logfile", "-", \
     "app:server"]
