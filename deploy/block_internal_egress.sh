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
# Scoping & Order inside DOCKER-USER:
#   1. conntrack RELATED,ESTABLISHED ACCEPT
#   2. bridge-to-bridge ACCEPT (-i br-+ -o br-+, -i docker0 -o docker0)
#   3. DROP rules strictly scoped to container egress:
#      -i br-+ ! -o br-+ -d <CIDR> -j DROP
#      -i docker0 ! -o docker0 -d <CIDR> -j DROP
#   Never matches inbound traffic from the external interface.
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

print_verification_checklist() {
    echo ""
    echo "=== [Operator Verification Checklist] ==="
    echo "1. Verify rules loaded: sudo iptables -S DOCKER-USER"
    echo "2. Container can reach internet: docker run --rm curlimages/curl -fsSL https://example.com"
    echo "3. Container cannot reach IMDS: docker run --rm curlimages/curl -s --connect-timeout 3 http://169.254.169.254/ (must time out / fail)"
    echo "4. Site is reachable from outside: curl -fsSL https://<your-domain>/"
}

if [[ "${VERIFY}" = true ]]; then
    print_verification_checklist
    exit 0
fi

if [[ "${DRY_RUN}" = false && "${EUID}" -ne 0 ]]; then
    echo "[ERROR] This script must be run as root (or with sudo). Use --dry-run to test without privileges."
    exit 1
fi

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

generate_rules() {
    local pos=1
    echo "iptables -I DOCKER-USER ${pos} -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT"; ((pos++))
    echo "iptables -I DOCKER-USER ${pos} -i br-+ -o br-+ -j ACCEPT"; ((pos++))
    echo "iptables -I DOCKER-USER ${pos} -i docker0 -o docker0 -j ACCEPT"; ((pos++))

    for cidr in "${BLOCKED_IPV4_CIDRS[@]}"; do
        echo "iptables -I DOCKER-USER ${pos} -i br-+ ! -o br-+ -d ${cidr} -j DROP"; ((pos++))
        echo "iptables -I DOCKER-USER ${pos} -i docker0 ! -o docker0 -d ${cidr} -j DROP"; ((pos++))
    done
}

generate_ipv6_rules() {
    local pos=1
    echo "ip6tables -I DOCKER-USER ${pos} -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT"; ((pos++))
    echo "ip6tables -I DOCKER-USER ${pos} -i br-+ -o br-+ -j ACCEPT"; ((pos++))
    echo "ip6tables -I DOCKER-USER ${pos} -i docker0 -o docker0 -j ACCEPT"; ((pos++))

    for cidr6 in "${BLOCKED_IPV6_CIDRS[@]}"; do
        echo "ip6tables -I DOCKER-USER ${pos} -i br-+ ! -o br-+ -d ${cidr6} -j DROP"; ((pos++))
        echo "ip6tables -I DOCKER-USER ${pos} -i docker0 ! -o docker0 -d ${cidr6} -j DROP"; ((pos++))
    done
}

if [[ "${DRY_RUN}" = true ]]; then
    echo "=== [DRY-RUN: Generated DOCKER-USER IPv4 Rules] ==="
    generate_rules
    echo ""
    echo "=== [DRY-RUN: Generated DOCKER-USER IPv6 Rules] ==="
    generate_ipv6_rules
    print_verification_checklist
    exit 0
fi

echo "=== [Container Egress Lockdown] ==="
iptables -N DOCKER-USER 2>/dev/null || true

while IFS= read -r cmd; do
    eval "${cmd}"
done < <(generate_rules)

if command -v ip6tables >/dev/null 2>&1; then
    ip6tables -N DOCKER-USER 2>/dev/null || true
    if ip6tables -L DOCKER-USER >/dev/null 2>&1; then
        while IFS= read -r cmd6; do
            eval "${cmd6}" 2>/dev/null || true
        done < <(generate_ipv6_rules)
    fi
fi

if command -v netfilter-persistent >/dev/null 2>&1; then
    netfilter-persistent save >/dev/null 2>&1 || true
    echo "[+] Firewall rules persisted via netfilter-persistent."
elif command -v iptables-save >/dev/null 2>&1; then
    mkdir -p /etc/iptables
    iptables-save > /etc/iptables/rules.v4 2>/dev/null || true
    echo "[+] Rules saved to /etc/iptables/rules.v4."
fi

print_verification_checklist
echo "=== [Container Egress Lockdown Complete] ==="
