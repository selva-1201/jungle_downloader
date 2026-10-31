#!/usr/bin/env bash
set -euo pipefail

# scripts/update_ytdlp.sh
# Staging update and verification script for yt-dlp with Telegram alert fallback.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
STAGING_VENV="${PROJECT_ROOT}/.venv_staging"
LIVE_VENV="${PROJECT_ROOT}/.venv"
SMOKE_TEST="${SCRIPT_DIR}/smoke_test.py"

TELEGRAM_BOT_TOKEN="${TELEGRAM_BOT_TOKEN:-}"
TELEGRAM_CHAT_ID="${TELEGRAM_CHAT_ID:-}"

send_alert() {
    local message="$1"
    echo "[ALERT] $message"
    if [[ -n "$TELEGRAM_BOT_TOKEN" && -n "$TELEGRAM_CHAT_ID" ]]; then
        curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
            -d "chat_id=${TELEGRAM_CHAT_ID}" \
            -d "text=${message}" > /dev/null 2>&1 || true
    fi
}

echo "=== Starting yt-dlp Staging Update ==="

# 1. Create temporary staging virtualenv if needed
rm -rf "$STAGING_VENV"
python3 -m venv "$STAGING_VENV"

# 2. Upgrade pip and install latest yt-dlp into staging
"$STAGING_VENV/bin/pip" install --upgrade pip --quiet
"$STAGING_VENV/bin/pip" install --upgrade yt-dlp --quiet

NEW_VERSION="$("$STAGING_VENV/bin/python3" -c 'import yt_dlp; print(yt_dlp.version.__version__)')"
echo "Candidate yt-dlp version in staging: ${NEW_VERSION}"

# 3. Execute smoke test using staging python
if "$STAGING_VENV/bin/python3" "$SMOKE_TEST"; then
    echo "Smoke tests PASSED for yt-dlp ${NEW_VERSION}. Upgrading production venv..."
    "$LIVE_VENV/bin/pip" install "yt-dlp==${NEW_VERSION}" --quiet
    rm -rf "$STAGING_VENV"
    echo "=== yt-dlp update to ${NEW_VERSION} completed successfully ==="
else
    echo "Smoke tests FAILED for candidate yt-dlp ${NEW_VERSION}! Rolling back."
    rm -rf "$STAGING_VENV"
    send_alert "Jungle Media Downloader: yt-dlp update to ${NEW_VERSION} failed smoke tests. Rolling back."
    exit 1
fi

