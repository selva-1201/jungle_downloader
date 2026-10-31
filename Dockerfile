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
ARG TAILWIND_SHA256_X64=dc61b3ac6b8c9ca874c0cc4c57b2409791a64c5540404ca5f5367360babc313a
ARG TAILWIND_SHA256_ARM64=55fd0b241214eff3de1e8ee4f22796662f2d2e7a49bcfca7477cfd0bac398195

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

# Copy assets required for Tailwind purging and generation
COPY tailwind.config.js .
COPY templates/ templates/
COPY static/ static/

# Detect architecture (arm64 vs amd64), download standalone binary, verify sha256 checksum
RUN ARCH="${TARGETARCH:-$(dpkg --print-architecture)}" && \
    case "$ARCH" in \
        "arm64"|"aarch64") \
            TAILWIND_BIN="tailwindcss-linux-arm64" && \
            EXPECTED_SHA="$TAILWIND_SHA256_ARM64" ;; \
        *) \
            TAILWIND_BIN="tailwindcss-linux-x64" && \
            EXPECTED_SHA="$TAILWIND_SHA256_X64" ;; \
    esac && \
    echo "Downloading Tailwind ${TAILWIND_VERSION} for ${ARCH} (${TAILWIND_BIN})..." && \
    curl -fsSL -o /usr/local/bin/tailwindcss \
        "https://github.com/tailwindlabs/tailwindcss/releases/download/${TAILWIND_VERSION}/${TAILWIND_BIN}" && \
    echo "${EXPECTED_SHA}  /usr/local/bin/tailwindcss" | sha256sum -c - && \
    chmod +x /usr/local/bin/tailwindcss && \
    /usr/local/bin/tailwindcss -i static/css/input.css -o /build/static/css/app.css --minify -c tailwind.config.js

# ------------------------------------------------------------------------------
# Stage 2: Automated Test Target (installs dev deps and runs pytest)
# ------------------------------------------------------------------------------
FROM python:3.11-slim-bookworm AS test

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONUNBUFFERED=1

COPY requirements.txt requirements-dev.txt ./
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt -r requirements-dev.txt

COPY . .
COPY --from=tailwind-builder /build/static/css/app.css /app/static/css/app.css

CMD ["pytest", "-q"]

# ------------------------------------------------------------------------------
# Stage 3: Final Production Runtime Image
# ------------------------------------------------------------------------------
FROM python:3.11-slim-bookworm AS runtime

# Install system dependencies: ffmpeg (for audio/video multiplexing), ca-certificates
# (curl dropped; healthcheck uses Python stdlib urllib.request)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Create dedicated non-root user and group
RUN groupadd -g 10001 appgroup && \
    useradd -u 10001 -g appgroup -d /home/appuser -m -s /sbin/nologin appuser

WORKDIR /app

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=5000 \
    TMPDIR=/tmp \
    HOME=/tmp \
    XDG_CACHE_HOME=/tmp/.cache \
    XDG_CONFIG_HOME=/tmp/.config

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

EXPOSE 5000

# Container Healthcheck using Python stdlib (no curl needed in runtime container)
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5000/healthz', timeout=3)" || exit 1

# Production entrypoint with Gunicorn and Gevent worker class (single worker for in-memory token cache & rate limiter)
CMD ["gunicorn", "--worker-class", "gevent", "--workers", "1", "--worker-connections", "1000", "--bind", "0.0.0.0:5000", "--timeout", "120", "--graceful-timeout", "30", "--access-logfile", "-", "--error-logfile", "-", "app:app"]
