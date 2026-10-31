#!/usr/bin/env bash
# ==============================================================================
# scripts/disk_ram_alert.sh
# Resource Monitor & Alerting Script for Disk and RAM Thresholds
# ==============================================================================
# Usage:
#   Run manually or configure as a periodic cron job (e.g. every 5 minutes):
#   */5 * * * * /opt/jungle_downloader/scripts/disk_ram_alert.sh >> /var/log/jungle_resource.log 2>&1
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Configurable thresholds
DISK_THRESHOLD_PCT="${DISK_THRESHOLD_PCT:-85}"
RAM_THRESHOLD_PCT="${RAM_THRESHOLD_PCT:-90}"

# Safely extract TELEGRAM variables from .env if present
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

# 1. Check Disk Usage
# df output percentage for root filesystem
DISK_USAGE_PCT=$(df / | awk 'NR==2 {gsub("%","",$5); print $5}')
if [[ -z "${DISK_USAGE_PCT}" ]]; then
    DISK_USAGE_PCT=0
fi

# 2. Check RAM Usage
if command -v free >/dev/null 2>&1; then
    RAM_USAGE_PCT=$(free | awk '/Mem:/ {if ($2 > 0) printf("%d", ($3/$2)*100); else print 0}')
elif [[ -f /proc/meminfo ]]; then
    MEM_TOTAL=$(grep MemTotal /proc/meminfo | awk '{print $2}')
    MEM_AVAIL=$(grep MemAvailable /proc/meminfo | awk '{print $2}')
    if [[ -n "${MEM_TOTAL}" && -n "${MEM_AVAIL}" && "${MEM_TOTAL}" -gt 0 ]]; then
        MEM_USED=$((MEM_TOTAL - MEM_AVAIL))
        RAM_USAGE_PCT=$((MEM_USED * 100 / MEM_TOTAL))
    else
        RAM_USAGE_PCT=0
    fi
else
    RAM_USAGE_PCT=0
fi

TIMESTAMP=$(date -u +'%Y-%m-%dT%H:%M:%SZ')
echo "[${TIMESTAMP}] Resource Check: Disk: ${DISK_USAGE_PCT}% (Threshold: ${DISK_THRESHOLD_PCT}%), RAM: ${RAM_USAGE_PCT}% (Threshold: ${RAM_THRESHOLD_PCT}%)"

ALERTS=()

if [[ "${DISK_USAGE_PCT}" -ge "${DISK_THRESHOLD_PCT}" ]]; then
    ALERTS+=("⚠️ Disk usage critical: ${DISK_USAGE_PCT}% (>= ${DISK_THRESHOLD_PCT}%) on $(hostname)")
fi

if [[ "${RAM_USAGE_PCT}" -ge "${RAM_THRESHOLD_PCT}" ]]; then
    ALERTS+=("⚠️ RAM usage critical: ${RAM_USAGE_PCT}% (>= ${RAM_THRESHOLD_PCT}%) on $(hostname)")
fi

if [[ ${#ALERTS[@]} -gt 0 ]]; then
    ALERT_MSG=$(printf "%s\n" "${ALERTS[@]}")
    send_alert "${ALERT_MSG}"
fi
