#!/usr/bin/env bash
# ==============================================================================
# Host Security: Block Container Egress to Oracle Cloud IMDS (169.254.169.254)
# ==============================================================================
# Container traffic routes through Docker's FORWARD chain, specifically
# intercepted by the DOCKER-USER table before routing to external/host interfaces.
# Running this rule prevents any compromised container from querying the
# Oracle Cloud Instance Metadata Service (IMDS).
# ==============================================================================

set -euo pipefail

IMDS_IP="169.254.169.254"

echo "[*] Configuring iptables DOCKER-USER chain to block ${IMDS_IP}..."

# Check if rule already exists to avoid duplication
if iptables -C DOCKER-USER -d "${IMDS_IP}" -j DROP 2>/dev/null; then
    echo "[+] Rule already exists in DOCKER-USER chain."
else
    iptables -I DOCKER-USER -d "${IMDS_IP}" -j DROP
    echo "[+] Successfully added DROP rule for ${IMDS_IP} to DOCKER-USER chain."
fi

# Persistence on Ubuntu across reboots
if command -v netfilter-persistent >/dev/null 2>&1; then
    netfilter-persistent save
    echo "[+] Saved iptables rules via netfilter-persistent."
elif command -v iptables-save >/dev/null 2>&1; then
    iptables-save > /etc/iptables/rules.v4 2>/dev/null || true
    echo "[+] Saved rules to /etc/iptables/rules.v4 (if directory exists)."
fi

echo "[*] IMDS protection configured."

