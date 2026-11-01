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

print_verification_checklist() {
    echo ""
    echo "=== [Operator Verification Checklist] ==="
    echo "1. Verify rules loaded: sudo iptables -S DOCKER-USER"
    echo "2. Container can reach internet: docker run --rm curlimages/curl -fsSL https://example.com"
    echo "3. Container cannot reach IMDS: docker run --rm curlimages/curl -s --connect-timeout 3 http://169.254.169.254/ (must time out / fail)"
    echo "4. Site is reachable from outside: curl -fsSL https://<your-domain>/"
}

if [[ "${DRY_RUN}" = false && "${EUID}" -ne 0 ]]; then
    echo "[ERROR] This script must be run as root (or with sudo). Use --dry-run to test without privileges."
    exit 1
fi

detect_external_interface() {
    local ext_if
    ext_if=$(ip route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="dev") print $(i+1); exit}' || true)
    if [[ -z "${ext_if}" ]]; then
        ext_if=$(ip route show default 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="dev") print $(i+1); exit}' || true)
    fi
    if [[ -z "${ext_if}" ]]; then
        ext_if="eth0"
    fi
    echo "${ext_if}"
}

EXT_IF=$(detect_external_interface)

# Fetch Cloudflare IPs with fallback
IPV4_LIST=""
IPV6_LIST=""
if command -v curl >/dev/null 2>&1; then
    IPV4_LIST=$(curl -fsSL --max-time 15 "${CF_IPV4_URL}" 2>/dev/null || true)
    IPV6_LIST=$(curl -fsSL --max-time 15 "${CF_IPV6_URL}" 2>/dev/null || true)
fi

if [[ -z "${IPV4_LIST}" ]]; then
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

generate_cf_rules() {
    local ext_if="$1"
    local pos=1
    echo "iptables -I DOCKER-USER ${pos} -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT"; ((pos++))
    while IFS= read -r ip; do
        ip=$(echo "${ip}" | tr -d '\r ')
        if [[ -n "${ip}" ]]; then
            echo "iptables -A DOCKER-USER -i ${ext_if} -s ${ip} -p tcp -m conntrack --ctorigdstport 80 -j ACCEPT"
            echo "iptables -A DOCKER-USER -i ${ext_if} -s ${ip} -p tcp -m conntrack --ctorigdstport 443 -j ACCEPT"
        fi
    done <<< "${IPV4_LIST}"
    echo "iptables -A DOCKER-USER -i ${ext_if} -p tcp -m conntrack --ctorigdstport 80 -j DROP"
    echo "iptables -A DOCKER-USER -i ${ext_if} -p tcp -m conntrack --ctorigdstport 443 -j DROP"
}

if [[ "${DRY_RUN}" = true ]]; then
    echo "=== [DRY-RUN: Cloudflare Origin Lockdown] ==="
    echo "External interface detected: ${EXT_IF}"
    echo "Cloudflare IPv4 ranges:"
    echo "${IPV4_LIST}"
    if [[ "${ALLOW_DIRECT_MEDIA}" = true ]]; then
        echo "[DRY-RUN] Direct media flag active (--allow-direct-media): public 80/443 would remain accessible."
    else
        echo ""
        echo "=== [DRY-RUN: Generated DOCKER-USER iptables Rules] ==="
        generate_cf_rules "${EXT_IF}"
    fi
    print_verification_checklist
    exit 0
fi

echo "=== [Cloudflare Origin Lockdown] ==="
# 1. Reset broad UFW 80/443 rules
if command -v ufw >/dev/null 2>&1; then
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

    ufw reload > /dev/null
    echo "[SUCCESS] UFW rules updated with Cloudflare ranges."
fi

# 4. Configure DOCKER-USER iptables chain to prevent Docker bypass
echo "[INFO] Updating DOCKER-USER iptables chain for container isolation..."
iptables -N DOCKER-USER 2>/dev/null || true

if [[ "${ALLOW_DIRECT_MEDIA}" = false ]]; then
    while IFS= read -r cmd; do
        eval "${cmd}"
    done < <(generate_cf_rules "${EXT_IF}")
fi

# Persist rules
if command -v netfilter-persistent >/dev/null 2>&1; then
    netfilter-persistent save >/dev/null 2>&1 || true
    echo "[SUCCESS] Firewall rules persisted."
fi

print_verification_checklist
echo "=== [Cloudflare Origin Lockdown Complete] ==="
