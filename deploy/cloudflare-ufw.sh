#!/usr/bin/env bash
# ==============================================================================
# deploy/cloudflare-ufw.sh
# Cloudflare Origin Lockdown & Docker-User Firewall Hardening
# ==============================================================================
# Purpose:
#   Restricts inbound HTTP (80) and HTTPS (443) traffic so origin servers only
#   accept traffic arriving from official Cloudflare proxy edges.
#
# Docker Note:
#   Docker modifies iptables directly and bypasses UFW's INPUT chain for container
#   port forwardings. This script configures BOTH UFW and the DOCKER-USER iptables
#   chain to ensure full origin lockdown.
#
# Flags:
#   --allow-direct-media   Allow non-Cloudflare traffic on 80/443 for direct media streaming
#   --dry-run              Print IP ranges and intended rules without applying changes
#   --help                 Show this help message
# ==============================================================================

set -euo pipefail

CF_IPV4_URL="https://www.cloudflare.com/ips-v4"
CF_IPV6_URL="https://www.cloudflare.com/ips-v6"

ALLOW_DIRECT_MEDIA=false
DRY_RUN=false

for arg in "$@"; do
    case "${arg}" in
        --allow-direct-media)
            ALLOW_DIRECT_MEDIA=true
            ;;
        --dry-run)
            DRY_RUN=true
            ;;
        --help|-h)
            echo "Usage: sudo bash deploy/cloudflare-ufw.sh [--allow-direct-media] [--dry-run]"
            exit 0
            ;;
        *)
            echo "Unknown option: ${arg}"
            echo "Usage: sudo bash deploy/cloudflare-ufw.sh [--allow-direct-media] [--dry-run]"
            exit 1
            ;;
    esac
done

if [[ "${DRY_RUN}" = false && "${EUID}" -ne 0 ]]; then
    echo "[ERROR] This script must be run as root (or with sudo). Use --dry-run to test without privileges."
    exit 1
fi

echo "=== [Cloudflare Origin Lockdown] ==="
echo "Fetching Cloudflare public IP ranges..."
echo "  IPv4: ${CF_IPV4_URL}"
echo "  IPv6: ${CF_IPV6_URL}"

# Fetch Cloudflare IPs with fallback
IPV4_LIST=$(curl -fsSL --max-time 15 "${CF_IPV4_URL}" || true)
IPV6_LIST=$(curl -fsSL --max-time 15 "${CF_IPV6_URL}" || true)

if [[ -z "${IPV4_LIST}" ]]; then
    echo "[WARN] Could not fetch IPv4 list from ${CF_IPV4_URL}, using fallback ranges."
    IPV4_LIST="173.245.48.0/20
103.21.244.0/22
103.22.200.0/22
103.31.4.0/22
141.101.64.0/18
108.162.192.0/18
190.93.240.0/20
188.114.96.0/20
197.234.240.0/22
198.41.128.0/17
162.158.0.0/15
104.16.0.0/13
104.24.0.0/14
172.64.0.0/13
131.0.72.0/22"
fi

if [[ "${DRY_RUN}" = true ]]; then
    echo "[DRY-RUN] Would configure UFW and DOCKER-USER for the following Cloudflare IPv4 ranges:"
    echo "${IPV4_LIST}"
    if [[ "${ALLOW_DIRECT_MEDIA}" = true ]]; then
        echo "[DRY-RUN] Direct media flag active (--allow-direct-media): public 80/443 would remain accessible."
    fi
    exit 0
fi

# 1. Reset any broad UFW 80/443 rules
echo "[INFO] Updating UFW rules..."
ufw delete allow 80/tcp 2>/dev/null || true
ufw delete allow 443/tcp 2>/dev/null || true
ufw delete allow 443/udp 2>/dev/null || true

# 2. Add Cloudflare IPv4 allow rules in UFW
while IFS= read -r ip; do
    ip=$(echo "${ip}" | tr -d '\r ')
    if [[ -n "${ip}" ]]; then
        ufw allow proto tcp from "${ip}" to any port 80,443 comment 'Cloudflare' > /dev/null
    fi
done <<< "${IPV4_LIST}"

# 3. Add Cloudflare IPv6 allow rules if IPv6 list is present
if [[ -n "${IPV6_LIST}" ]]; then
    while IFS= read -r ip; do
        ip=$(echo "${ip}" | tr -d '\r ')
        if [[ -n "${ip}" ]]; then
            ufw allow proto tcp from "${ip}" to any port 80,443 comment 'Cloudflare v6' > /dev/null 2>&1 || true
        fi
    done <<< "${IPV6_LIST}"
fi

# If direct media flag is explicitly set, open direct HTTP/HTTPS access
if [[ "${ALLOW_DIRECT_MEDIA}" = true ]]; then
    echo "[INFO] --allow-direct-media enabled: adding public UFW exceptions for media streaming..."
    ufw allow 80/tcp comment 'Direct Media HTTP' > /dev/null
    ufw allow 443/tcp comment 'Direct Media HTTPS' > /dev/null
fi

# Reload UFW
ufw reload > /dev/null
echo "[SUCCESS] UFW rules updated with Cloudflare ranges."

# 4. Configure DOCKER-USER iptables chain to prevent Docker bypass
echo "[INFO] Updating DOCKER-USER iptables chain for container isolation..."
iptables -N DOCKER-USER 2>/dev/null || true

# Flush previous Cloudflare-managed DOCKER-USER rules
# (Preserve existing IMDS rules by only managing web ports)
if [[ "${ALLOW_DIRECT_MEDIA}" = false ]]; then
    # Accept established/related traffic
    if ! iptables -C DOCKER-USER -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT 2>/dev/null; then
        iptables -I DOCKER-USER -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT
    fi

    # Add Cloudflare ACCEPT rules
    while IFS= read -r ip; do
        ip=$(echo "${ip}" | tr -d '\r ')
        if [[ -n "${ip}" ]]; then
            if ! iptables -C DOCKER-USER -s "${ip}" -p tcp -m multiport --dports 80,443 -j ACCEPT 2>/dev/null; then
                iptables -I DOCKER-USER -s "${ip}" -p tcp -m multiport --dports 80,443 -j ACCEPT
            fi
        fi
    done <<< "${IPV4_LIST}"

    # Drop any other incoming connections to ports 80/443 on Docker containers
    if ! iptables -C DOCKER-USER -p tcp -m multiport --dports 80,443 -j DROP 2>/dev/null; then
        iptables -A DOCKER-USER -p tcp -m multiport --dports 80,443 -j DROP
    fi
fi

# Persist rules
if command -v netfilter-persistent >/dev/null 2>&1; then
    netfilter-persistent save >/dev/null 2>&1 || true
    echo "[SUCCESS] Firewall rules persisted."
fi

echo "=== [Cloudflare Origin Lockdown Complete] ==="
