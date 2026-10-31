# ==============================================================================
# Jungle Media Downloader - Multi-Stage Production Dockerfile
# Supports linux/arm64 (Oracle Ampere A1) and linux/amd64 (AMD x86_64)
# ==============================================================================

# ------------------------------------------------------------------------------
# Stage 1: Asset Builder (Tailwind CSS standalone binary)
# ------------------------------------------------------------------------------
FROM debian:bookworm-slim AS tailwind-builder

ARG TARGETARCH
ARG TAILWIND_VERSION=v3.4.17

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

# Copy assets required for Tailwind purging and generation
COPY tailwind.config.js .
COPY templates/ templates/
COPY static/ static/

# Detect architecture (arm64 vs amd64) and download the appropriate standalone Tailwind binary
RUN ARCH="${TARGETARCH:-$(dpkg --print-architecture)}" && \
    case "$ARCH" in \
        "arm64"|"aarch64") TAILWIND_BIN="tailwindcss-linux-arm64" ;; \
        *) TAILWIND_BIN="tailwindcss-linux-x64" ;; \
    esac && \
    echo "Downloading Tailwind ${TAILWIND_VERSION} for architecture: ${ARCH} (${TAILWIND_BIN})..." && \
    curl -sLo /usr/local/bin/tailwindcss \
        "https://github.com/tailwindlabs/tailwindcss/releases/download/${TAILWIND_VERSION}/${TAILWIND_BIN}" && \
    chmod +x /usr/local/bin/tailwindcss && \
    /usr/local/bin/tailwindcss -i static/css/input.css -o /build/static/css/app.css --minify -c tailwind.config.js

# ------------------------------------------------------------------------------
# Stage 2: Automated Test Target (installs dev deps and runs pytest)
# ------------------------------------------------------------------------------
FROM python:3.11-slim AS test

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt requirements-dev.txt ./
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt -r requirements-dev.txt

COPY . .
COPY --from=tailwind-builder /build/static/css/app.css /app/static/css/app.css

CMD ["pytest", "-q"]

# ------------------------------------------------------------------------------
# Stage 3: Final Production Runtime Image
# ------------------------------------------------------------------------------
FROM python:3.11-slim AS runtime

# Install system dependencies: ffmpeg (for audio/video multiplexing), curl (for HEALTHCHECK), ca-certificates
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Create dedicated non-root user and group
RUN groupadd -g 10001 appgroup && \
    useradd -u 10001 -g appgroup -d /home/appuser -m -s /sbin/nologin appuser

WORKDIR /app

# Install pinned Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy application source code
COPY . .

# Copy the freshly compiled, minified Tailwind CSS from the builder stage
COPY --from=tailwind-builder /build/static/css/app.css /app/static/css/app.css

# Remove any dev/test artifacts that may have been copied
RUN rm -rf bin/ tests/ .venv/ .git/ .pytest_cache/ *.pyc __pycache__

# Set directory ownership to non-root user
RUN chown -R appuser:appgroup /app /home/appuser

# Switch to non-root user
USER appuser

# Runtime environment settings - routes all cache/config/temp writes to writable tmpfs (/tmp)
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=5000 \
    TMPDIR=/tmp \
    HOME=/tmp \
    XDG_CACHE_HOME=/tmp/.cache \
    XDG_CONFIG_HOME=/tmp/.config

EXPOSE 5000

# Container Healthcheck
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -f http://127.0.0.1:5000/healthz || exit 1

# Production entrypoint with Gunicorn and Gevent worker class (single worker for in-memory token cache & rate limiter)
CMD ["gunicorn", "--worker-class", "gevent", "--workers", "1", "--worker-connections", "1000", "--bind", "0.0.0.0:5000", "--timeout", "120", "--graceful-timeout", "30", "--access-logfile", "-", "--error-logfile", "-", "app:app"]

