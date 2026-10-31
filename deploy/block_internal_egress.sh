#!/usr/bin/env bash
# ==============================================================================
# deploy/block_internal_egress.sh
# Container Egress Lockdown (RFC 1918, Link-Local & Cloud IMDS Protection)
# ==============================================================================
# Purpose:
#   Prevents container breakouts and SSRF attacks from accessing internal cloud
#   infrastructure (OCI IMDS, VCN private subnets, host loopback gateway, and RFC1918).
#
# Blocked CIDRs:
#   - 169.254.0.0/16 (Link-local & Instance Metadata Service / IMDS)
#   - 10.0.0.0/8     (RFC 1918 Class A)
#   - 172.16.0.0/12  (RFC 1918 Class B)
#   - 192.168.0.0/16 (RFC 1918 Class C)
#   - 100.64.0.0/10  (Carrier-Grade NAT / CGNAT)
#
# Safe Allowances (Inserted before DROP rules):
#   - Established and related connections (so outgoing public responses return)
#   - Inter-container bridge traffic (app <-> caddy <-> redis on docker bridge)
#   - Docker embedded DNS (127.0.0.11)
#
# DNS Caveat:
#   Docker containers require a working DNS resolver. Container egress to public
#   DNS (e.g. 1.1.1.1, 8.8.8.8) is permitted. If the Docker host utilizes an internal
#   RFC1918 resolver (e.g. 10.x.x.x), an explicit ACCEPT rule for port 53 to that
#   resolver must be added before running this script.
# ==============================================================================

set -euo pipefail

DRY_RUN=false
VERIFY=false

for arg in "$@"; do
    case "${arg}" in
        --dry-run)
            DRY_RUN=true
            ;;
        --verify)
            VERIFY=true
            ;;
        --help|-h)
            echo "Usage: sudo bash deploy/block_internal_egress.sh [--verify] [--dry-run]"
            exit 0
            ;;
        *)
            echo "Unknown option: ${arg}"
            echo "Usage: sudo bash deploy/block_internal_egress.sh [--verify] [--dry-run]"
            exit 1
            ;;
    esac
done

if [[ "${VERIFY}" = true ]]; then
    echo "=== [Verification Instructions] ==="
    echo "Run the following test command to verify container egress block against IMDS:"
    echo "  docker run --rm curlimages/curl -s --connect-timeout 3 http://169.254.169.254/opc/v2/instance/"
    echo "Expected outcome: Connection timed out / Failed to connect (Exit 28)."
    echo ""
    echo "Run the following test command to verify inter-container communication works:"
    echo "  docker compose exec app curl -s http://caddy:80/healthz"
    echo "Expected outcome: 200 OK / redirection."
    exit 0
fi

if [[ "${DRY_RUN}" = false && "${EUID}" -ne 0 ]]; then
    echo "[ERROR] This script must be run as root (or with sudo). Use --dry-run to test without privileges."
    exit 1
fi

echo "=== [Container Egress Lockdown] ==="

BLOCKED_IPV4_CIDRS=(
    "169.254.0.0/16"
    "10.0.0.0/8"
    "172.16.0.0/12"
    "192.168.0.0/16"
    "100.64.0.0/10"
)

BLOCKED_IPV6_CIDRS=(
    "fc00::/7"
    "fe80::/10"
)

if [[ "${DRY_RUN}" = true ]]; then
    echo "[DRY-RUN] Would configure DOCKER-USER chain to block the following IPv4 ranges:"
    printf "  - %s\n" "${BLOCKED_IPV4_CIDRS[@]}"
    echo "[DRY-RUN] Would block IPv6 ranges (if ip6tables enabled):"
    printf "  - %s\n" "${BLOCKED_IPV6_CIDRS[@]}"
    echo "[DRY-RUN] Safe allowances (conntrack ESTABLISHED/RELATED, inter-bridge traffic, and 127.0.0.11 DNS) would precede drop rules."
    exit 0
fi

# Ensure DOCKER-USER chain exists in IPv4 iptables
iptables -N DOCKER-USER 2>/dev/null || true

# ------------------------------------------------------------------------------
# 1. Safe Allowances (High Priority in DOCKER-USER)
# ------------------------------------------------------------------------------
# 1.1 Allow established & related return traffic
if ! iptables -C DOCKER-USER -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT 2>/dev/null; then
    iptables -I DOCKER-USER 1 -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT
    echo "[+] Added conntrack RELATED,ESTABLISHED allowance."
fi

# 1.2 Allow Docker embedded DNS (127.0.0.11)
if ! iptables -C DOCKER-USER -d 127.0.0.11 -p udp --dport 53 -j ACCEPT 2>/dev/null; then
    iptables -I DOCKER-USER 2 -d 127.0.0.11 -p udp --dport 53 -j ACCEPT
    echo "[+] Added Docker embedded DNS (UDP:53) allowance."
fi
if ! iptables -C DOCKER-USER -d 127.0.0.11 -p tcp --dport 53 -j ACCEPT 2>/dev/null; then
    iptables -I DOCKER-USER 3 -d 127.0.0.11 -p tcp --dport 53 -j ACCEPT
    echo "[+] Added Docker embedded DNS (TCP:53) allowance."
fi

# 1.3 Allow container-to-container bridge traffic on the same bridge (app <-> caddy <-> redis)
if ! iptables -C DOCKER-USER -i br-+ -o br-+ -j ACCEPT 2>/dev/null; then
    iptables -I DOCKER-USER 4 -i br-+ -o br-+ -j ACCEPT 2>/dev/null || true
fi
if ! iptables -C DOCKER-USER -i docker0 -o docker0 -j ACCEPT 2>/dev/null; then
    iptables -I DOCKER-USER 5 -i docker0 -o docker0 -j ACCEPT 2>/dev/null || true
fi

# ------------------------------------------------------------------------------
# 2. Block RFC 1918, Link-Local & IMDS Ranges
# ------------------------------------------------------------------------------
for cidr in "${BLOCKED_IPV4_CIDRS[@]}"; do
    if iptables -C DOCKER-USER -d "${cidr}" -j DROP 2>/dev/null; then
        echo "[INFO] Rule already exists for ${cidr}."
    else
        iptables -A DOCKER-USER -d "${cidr}" -j DROP
        echo "[+] Added DROP rule for ${cidr} to DOCKER-USER chain."
    fi
done

# ------------------------------------------------------------------------------
# 3. IPv6 Lockdown (if ip6tables is available and Docker IPv6 is active)
# ------------------------------------------------------------------------------
if command -v ip6tables >/dev/null 2>&1; then
    ip6tables -N DOCKER-USER 2>/dev/null || true
    if ip6tables -L DOCKER-USER >/dev/null 2>&1; then
        if ! ip6tables -C DOCKER-USER -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT 2>/dev/null; then
            ip6tables -I DOCKER-USER 1 -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT 2>/dev/null || true
        fi
        for cidr6 in "${BLOCKED_IPV6_CIDRS[@]}"; do
            if ! ip6tables -C DOCKER-USER -d "${cidr6}" -j DROP 2>/dev/null; then
                ip6tables -A DOCKER-USER -d "${cidr6}" -j DROP 2>/dev/null || true
                echo "[+] Added IPv6 DROP rule for ${cidr6} to DOCKER-USER chain."
            fi
        done
    fi
fi

# ------------------------------------------------------------------------------
# 4. Persist Firewall Rules
# ------------------------------------------------------------------------------
if command -v netfilter-persistent >/dev/null 2>&1; then
    netfilter-persistent save >/dev/null 2>&1 || true
    echo "[+] Firewall rules persisted via netfilter-persistent."
elif command -v iptables-save >/dev/null 2>&1; then
    mkdir -p /etc/iptables
    iptables-save > /etc/iptables/rules.v4 2>/dev/null || true
    echo "[+] Rules saved to /etc/iptables/rules.v4."
fi

echo "=== [Container Egress Lockdown Complete] ==="

