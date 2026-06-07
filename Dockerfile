# Multi-arch friendly (works on x86 WSL2 and Oracle ARM Ampere). Slim Python base.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Install deps first for better layer caching.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# App code.
COPY pyproject.toml ./
COPY sportsup ./sportsup

# Persisted state + logs live on mounted volumes (see docker-compose.yml).
VOLUME ["/app/data", "/app/logs"]

# Default to the safe, read-only plan view. Override the command to `run` for production.
ENTRYPOINT ["python", "-m", "sportsup"]
CMD ["plan"]
