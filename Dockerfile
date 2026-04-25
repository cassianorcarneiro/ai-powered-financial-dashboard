FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# System packages: only what's strictly needed at runtime.
# Most dependencies (pandas, plotly, dash) ship pre-built wheels for python:3.12-slim,
# so we no longer pull build-essential/gcc/g++. Slimmer image, faster builds.
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies first (better layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application code
COPY . .

# Ensure the data directory exists even if the host folder is empty on first run
RUN mkdir -p /app/data

EXPOSE 8050

# Healthcheck: make sure the Dash server is responsive
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8050/ >/dev/null || exit 1

CMD ["python", "app.py"]
