#!/usr/bin/env bash
# ==============================================================================
# scripts/update_ytdlp_docker.sh
# Automated Weekly Docker Image Rebuilder & Verifier for yt-dlp
# ==============================================================================
# Usage:
#   Run manually or configure as a weekly cron job on the Docker host:
#   0 3 * * 0 /opt/jungle_downloader/scripts/update_ytdlp_docker.sh >> /var/log/jungle_update.log 2>&1
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
REQUIREMENTS_FILE="${PROJECT_ROOT}/requirements.txt"
BACKUP_FILE="${PROJECT_ROOT}/requirements.txt.bak"

# Safely extract TELEGRAM variables from .env if present (without echoing or executing arbitrary commands)
if [[ -f "${PROJECT_ROOT}/.env" ]]; then
    TELEGRAM_BOT_TOKEN_FILE=$(grep -E '^[[:space:]]*TELEGRAM_BOT_TOKEN=' "${PROJECT_ROOT}/.env" | head -n 1 | cut -d '=' -f 2- | tr -d '"\r' || true)
    TELEGRAM_CHAT_ID_FILE=$(grep -E '^[[:space:]]*TELEGRAM_CHAT_ID=' "${PROJECT_ROOT}/.env" | head -n 1 | cut -d '=' -f 2- | tr -d '"\r' || true)
    if [[ -n "${TELEGRAM_BOT_TOKEN_FILE}" ]]; then
        TELEGRAM_BOT_TOKEN="${TELEGRAM_BOT_TOKEN_FILE}"
    fi
    if [[ -n "${TELEGRAM_CHAT_ID_FILE}" ]]; then
        TELEGRAM_CHAT_ID="${TELEGRAM_CHAT_ID_FILE}"
    fi
fi

TELEGRAM_BOT_TOKEN="${TELEGRAM_BOT_TOKEN:-}"
TELEGRAM_CHAT_ID="${TELEGRAM_CHAT_ID:-}"

send_alert() {
    local message="$1"
    echo "[ALERT] ${message}"
    if [[ -n "${TELEGRAM_BOT_TOKEN}" && -n "${TELEGRAM_CHAT_ID}" ]]; then
        curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
            -d "chat_id=${TELEGRAM_CHAT_ID}" \
            -d "text=${message}" > /dev/null 2>&1 || true
    fi
}

echo "=== [$(date -u +'%Y-%m-%dT%H:%M:%SZ')] Starting Docker yt-dlp Update Check ==="
cd "${PROJECT_ROOT}"

# 1. Fetch current pinned yt-dlp version
CURRENT_VERSION=$(grep "^yt-dlp==" "${REQUIREMENTS_FILE}" | cut -d '=' -f 3 || true)
if [[ -z "${CURRENT_VERSION}" ]]; then
    echo "[ERROR] yt-dlp is not pinned in ${REQUIREMENTS_FILE}"
    exit 1
fi
echo "[INFO] Current pinned version in requirements.txt: ${CURRENT_VERSION}"

# 2. Query PyPI for latest stable release
LATEST_VERSION=$(curl -s --max-time 15 https://pypi.org/pypi/yt-dlp/json | grep -o '"version":"[^"]*"' | head -n 1 | cut -d '"' -f 4 || true)
if [[ -z "${LATEST_VERSION}" ]]; then
    echo "[WARN] Could not retrieve latest yt-dlp version from PyPI. Network or API issue."
    exit 0
fi
echo "[INFO] Latest version available on PyPI: ${LATEST_VERSION}"

if [[ "${CURRENT_VERSION}" == "${LATEST_VERSION}" ]]; then
    echo "[INFO] yt-dlp is already at the latest version (${CURRENT_VERSION}). Nothing to do."
    exit 0
fi

echo "[INFO] New version detected (${CURRENT_VERSION} -> ${LATEST_VERSION}). Proceeding with staged Docker build..."

# 3. Create backup of requirements.txt
cp "${REQUIREMENTS_FILE}" "${BACKUP_FILE}"

# Update requirements.txt to candidate version
sed -i "s/^yt-dlp==.*/yt-dlp==${LATEST_VERSION}/" "${REQUIREMENTS_FILE}"

# 4. Build candidate test stage and run pytest
TEST_TAG="jungle-downloader:candidate-${LATEST_VERSION}-test"
echo "[INFO] Building candidate test stage: ${TEST_TAG}..."

if ! docker build --target test -t "${TEST_TAG}" -f Dockerfile . ; then
    echo "[ERROR] Docker test stage build failed for yt-dlp ${LATEST_VERSION}! Rolling back."
    mv "${BACKUP_FILE}" "${REQUIREMENTS_FILE}"
    send_alert "Jungle Downloader: Docker test stage build failed for candidate yt-dlp ${LATEST_VERSION}. Rolled back."
    exit 1
fi

echo "[INFO] Running pytest test suite inside candidate test container..."
if ! docker run --rm "${TEST_TAG}" ; then
    echo "[ERROR] Candidate container failed pytest! Rolling back."
    mv "${BACKUP_FILE}" "${REQUIREMENTS_FILE}"
    docker rmi "${TEST_TAG}" 2>/dev/null || true
    send_alert "Jungle Downloader: Candidate container with yt-dlp ${LATEST_VERSION} failed pytest. Rolled back."
    exit 1
fi
docker rmi "${TEST_TAG}" 2>/dev/null || true

# 5. Build candidate runtime image
CANDIDATE_TAG="jungle-downloader:candidate-${LATEST_VERSION}"
echo "[INFO] Building candidate runtime image: ${CANDIDATE_TAG}..."

if ! docker build -t "${CANDIDATE_TAG}" -f Dockerfile . ; then
    echo "[ERROR] Docker build failed for yt-dlp ${LATEST_VERSION}! Rolling back."
    mv "${BACKUP_FILE}" "${REQUIREMENTS_FILE}"
    send_alert "Jungle Downloader: Docker build failed for candidate yt-dlp ${LATEST_VERSION}. Rolled back."
    exit 1
fi

# 6. Run live smoke test inside candidate runtime container
echo "[INFO] Running live smoke test inside candidate runtime container..."
if ! docker run --rm "${CANDIDATE_TAG}" python3 scripts/smoke_test.py ; then
    echo "[ERROR] Candidate container failed smoke test! Rolling back."
    mv "${BACKUP_FILE}" "${REQUIREMENTS_FILE}"
    docker rmi "${CANDIDATE_TAG}" 2>/dev/null || true
    send_alert "Jungle Downloader: Candidate container with yt-dlp ${LATEST_VERSION} failed smoke tests. Rolled back."
    exit 1
fi

# 7. Verification passed! Tag as production image and restart the service
echo "[INFO] Smoke tests passed! Promoting candidate to production..."
docker tag "${CANDIDATE_TAG}" jungle-downloader:latest
docker rmi "${CANDIDATE_TAG}" 2>/dev/null || true
rm -f "${BACKUP_FILE}"

# Re-deploy app container with zero downtime or fast restart
docker compose up -d --no-deps app

send_alert "Jungle Downloader: Successfully upgraded yt-dlp to ${LATEST_VERSION} in Docker production."
echo "=== Upgrade to yt-dlp ${LATEST_VERSION} completed successfully ==="

