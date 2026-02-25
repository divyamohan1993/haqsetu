# =============================================================================
# HaqSetu - Multi-stage Docker Build
# Voice-First AI Civic Assistant for Rural India
# =============================================================================

# ---------------------------------------------------------------------------
# Stage 1: Builder - install dependencies in an isolated layer
# ---------------------------------------------------------------------------
FROM python:3.13-slim AS builder

WORKDIR /app

# Install build dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc && \
    rm -rf /var/lib/apt/lists/*

# Copy only dependency specification first for layer caching
COPY pyproject.toml .

# Install the package and its dependencies
RUN pip install --no-cache-dir --upgrade pip setuptools && \
    pip install --no-cache-dir build && \
    pip install --no-cache-dir .

# ---------------------------------------------------------------------------
# Stage 2: Runtime - minimal image with only what's needed
# ---------------------------------------------------------------------------
FROM python:3.13-slim AS runtime

# Security labels
LABEL org.opencontainers.image.title="HaqSetu" \
      org.opencontainers.image.description="Voice-First AI Civic Assistant for Rural India" \
      org.opencontainers.image.vendor="HaqSetu" \
      org.opencontainers.image.source="https://github.com/divyamohan1993/haqsetu" \
      security.privileged="false"

WORKDIR /app

# Install runtime system dependencies
# SECURITY: Install security updates and minimal packages only
RUN apt-get update && \
    apt-get upgrade -y && \
    apt-get dist-upgrade -y && \
    apt-get install -y --no-install-recommends \
        curl \
        ca-certificates && \
    rm -rf /var/lib/apt/lists/* && \
    rm -rf /tmp/* /var/tmp/*

# Copy installed Python packages from builder
COPY --from=builder /usr/local/lib/python3.13/site-packages /usr/local/lib/python3.13/site-packages
COPY --from=builder /usr/local/bin/uvicorn /usr/local/bin/uvicorn

# Create non-root user BEFORE copying app code
RUN adduser --disabled-password --gecos '' --no-create-home --uid 10001 appuser

# Copy application source
COPY --chown=appuser:appuser . .

# Remove unnecessary files from the image
RUN rm -rf tests/ infrastructure/ scripts/ Makefile docker-compose*.yml \
    .git .gitignore .dockerignore .env.example .env .github .kiro \
    *.md pyproject.toml 2>/dev/null || true

USER appuser

EXPOSE 8000

# Python optimizations for containers
# SECURITY: PYTHONHASHSEED=random prevents hash collision DoS attacks
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app \
    PYTHONHASHSEED=random \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Health check for container orchestration
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:8000/api/v1/health || exit 1

CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4", "--header", "server:HaqSetu"]
