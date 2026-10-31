#!/usr/bin/env bash
# ==============================================================================
# Jungle Media Downloader - Host Hardening & Setup Script
# Target OS: Ubuntu 24.04 LTS (Oracle Cloud Infrastructure - Mumbai / Hyderabad)
# Compatible: ARM64 (Ampere A1) and AMD64 (x86_64)
# ==============================================================================
# Instructions:
# 1. Review this script carefully before running:
#    less deploy/host-setup.sh
# 2. Run with sudo:
#    sudo bash deploy/host-setup.sh
# ==============================================================================

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# ------------------------------------------------------------------------------
# 0. Root Check
# ------------------------------------------------------------------------------
if [[ "${EUID}" -ne 0 ]]; then
    log_error "This script must be run as root (use: sudo bash deploy/host-setup.sh)"
    exit 1
fi

echo "=============================================================================="
echo " Jungle Media Downloader - Oracle Cloud Host Initialization"
echo "=============================================================================="

# ------------------------------------------------------------------------------
# 1. Base Packages & Dependencies
# ------------------------------------------------------------------------------
log_info "Updating apt package index and installing base security tools..."
export DEBIAN_FRONTEND=noninteractive

# Preseed iptables-persistent to install non-interactively without prompt hang
if command -v debconf-set-selections >/dev/null 2>&1; then
    echo "iptables-persistent iptables-persistent/autosave_v4 boolean true" | debconf-set-selections
    echo "iptables-persistent iptables-persistent/autosave_v6 boolean true" | debconf-set-selections
fi

apt-get update -y
apt-get install -y --no-install-recommends \
    ufw \
    fail2ban \
    unattended-upgrades \
    iptables-persistent \
    netfilter-persistent \
    curl \
    ca-certificates \
    systemd-timesyncd \
    jq

log_success "Base security packages installed."

# ------------------------------------------------------------------------------
# 2. Configure 4GB Swap Space (Idempotent)
# ------------------------------------------------------------------------------
SWAP_FILE="/swapfile"
SWAP_SIZE_GB=4

if grep -q "${SWAP_FILE}" /proc/swaps; then
    log_info "Swap file (${SWAP_FILE}) already active. Skipping creation."
else
    log_info "Creating ${SWAP_SIZE_GB}GB swapfile at ${SWAP_FILE}..."
    fallocate -l "${SWAP_SIZE_GB}G" "${SWAP_FILE}" || dd if=/dev/zero of="${SWAP_FILE}" bs=1M count=$((SWAP_SIZE_GB * 1024))
    chmod 600 "${SWAP_FILE}"
    mkswap "${SWAP_FILE}"
    swapon "${SWAP_FILE}"

    # Persist in /etc/fstab
    if ! grep -q "${SWAP_FILE}" /etc/fstab; then
        echo "${SWAP_FILE} none swap sw 0 0" >> /etc/fstab
    fi
    log_success "${SWAP_SIZE_GB}GB Swap created and persisted."
fi

# Configure conservative swappiness (10) for server workloads
log_info "Configuring vm.swappiness=10..."
cat > /etc/sysctl.d/99-jungle-swap.conf << 'EOF'
vm.swappiness = 10
vm.vfs_cache_pressure = 50
EOF
sysctl -p /etc/sysctl.d/99-jungle-swap.conf > /dev/null

# ------------------------------------------------------------------------------
# 3. Time Synchronization (systemd-timesyncd)
# ------------------------------------------------------------------------------
log_info "Enabling system clock network time synchronization..."
systemctl enable --now systemd-timesyncd
timedatectl set-ntp true
log_success "Time synchronization active: $(timedatectl status | grep 'System clock synchronized' || true)"

# ------------------------------------------------------------------------------
# 4. Unattended Upgrades (Automatic Security Patches)
# ------------------------------------------------------------------------------
log_info "Configuring unattended-upgrades for automatic security updates..."
cat > /etc/apt/apt.conf.d/20auto-upgrades << 'EOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Download-Upgradeable-Packages "1";
APT::Periodic::AutocleanInterval "7";
APT::Periodic::Unattended-Upgrade "1";
EOF

systemctl enable --now unattended-upgrades
log_success "Unattended security upgrades active."

# ------------------------------------------------------------------------------
# 5. SSH Hardening & Fail2ban
# ------------------------------------------------------------------------------
log_info "Configuring SSH hardening drop-in (/etc/ssh/sshd_config.d/99-jungle.conf)..."
mkdir -p /etc/ssh/sshd_config.d
cat > /etc/ssh/sshd_config.d/99-jungle.conf << 'EOF'
PasswordAuthentication no
PermitRootLogin no
PubkeyAuthentication yes
EOF

if command -v sshd >/dev/null 2>&1; then
    log_info "Validating sshd configuration (sshd -t)..."
    if sshd -t; then
        systemctl reload ssh 2>/dev/null || systemctl reload sshd 2>/dev/null || true
        log_success "SSH hardening configuration validated and applied."
    else
        log_error "sshd -t validation failed! Reverting 99-jungle.conf to prevent SSH lockout."
        rm -f /etc/ssh/sshd_config.d/99-jungle.conf
        exit 1
    fi
fi

log_info "Configuring Fail2ban jail for SSH protection..."
mkdir -p /etc/fail2ban/jail.d
cat > /etc/fail2ban/jail.d/sshd.local << 'EOF'
[sshd]
enabled = true
port = 22
mode = normal
backend = systemd
maxretry = 3
findtime = 10m
bantime = 1h
EOF

systemctl enable fail2ban
systemctl restart fail2ban
log_success "Fail2ban configured and restarted."

# ------------------------------------------------------------------------------
# 6. Oracle Cloud Default iptables Cleanup
# ------------------------------------------------------------------------------
# Note: Oracle Cloud Ubuntu images include default iptables rules in
# /etc/iptables/rules.v4 that reject non-port 22 traffic before UFW can process it.
log_info "Checking Oracle Cloud default iptables rules..."
if [[ -f /etc/iptables/rules.v4 ]]; then
    # Backup original rules
    cp /etc/iptables/rules.v4 /etc/iptables/rules.v4.backup.$(date +%s)
    # Remove default Oracle REJECT rules if present
    iptables -D INPUT -j REJECT --reject-with icmp-host-prohibited 2>/dev/null || true
    iptables -D FORWARD -j REJECT --reject-with icmp-host-prohibited 2>/dev/null || true
fi

# ------------------------------------------------------------------------------
# 7. UFW Firewall Configuration
# ------------------------------------------------------------------------------
log_info "Configuring UFW rules..."
ufw --force reset > /dev/null
ufw default deny incoming
ufw default allow outgoing

# Allow SSH (Port 22)
ufw allow 22/tcp comment 'SSH Access'

# Allow Web Traffic (HTTP 80, HTTPS 443 TCP & UDP for HTTP/3 QUIC)
ufw allow 80/tcp comment 'HTTP Web'
ufw allow 443/tcp comment 'HTTPS Web'
ufw allow 443/udp comment 'HTTPS HTTP/3 QUIC'

# Enable UFW
ufw --force enable
log_success "UFW firewall active and configured."

# ------------------------------------------------------------------------------
# 8. Oracle IMDS Protection (Drop Container Egress to 169.254.169.254)
# ------------------------------------------------------------------------------
# Ensures Docker containers cannot access Oracle Cloud's Instance Metadata Service.
log_info "Configuring Oracle Cloud IMDS egress drop in iptables DOCKER-USER chain..."
IMDS_IP="169.254.169.254"

# Ensure DOCKER-USER chain exists (Docker creates it, but we prepare it in case Docker isn't running yet)
iptables -N DOCKER-USER 2>/dev/null || true

if iptables -C DOCKER-USER -d "${IMDS_IP}" -j DROP 2>/dev/null; then
    log_info "Rule already present in DOCKER-USER chain."
else
    iptables -I DOCKER-USER -d "${IMDS_IP}" -j DROP
    log_success "Added DROP rule for Oracle IMDS (${IMDS_IP}) to DOCKER-USER chain."
fi

# Persist iptables rules across reboots
if command -v netfilter-persistent >/dev/null 2>&1; then
    netfilter-persistent save >/dev/null 2>&1 || true
    log_success "iptables rules persisted via netfilter-persistent."
fi

# ------------------------------------------------------------------------------
# 9. Docker Daemon Log Rotation Defaults
# ------------------------------------------------------------------------------
log_info "Configuring Docker daemon default log limits (/etc/docker/daemon.json)..."
mkdir -p /etc/docker
DAEMON_JSON="/etc/docker/daemon.json"

if [[ -f "${DAEMON_JSON}" ]]; then
    # Merge existing config with log-opts
    TMP_JSON=$(mktemp)
    jq '. + {"log-driver": "json-file", "log-opts": {"max-size": "10m", "max-file": "3"}}' "${DAEMON_JSON}" > "${TMP_JSON}" 2>/dev/null || true
    if [[ -s "${TMP_JSON}" ]]; then
        mv "${TMP_JSON}" "${DAEMON_JSON}"
    else
        rm -f "${TMP_JSON}"
    fi
else
    cat > "${DAEMON_JSON}" << 'EOF'
{
  "log-driver": "json-file",
  "log-opts": {
    "max-size": "10m",
    "max-file": "3"
  }
}
EOF
fi

if systemctl is-active --quiet docker; then
    log_info "Restarting Docker daemon to apply daemon.json log options..."
    systemctl restart docker 2>/dev/null || true
    log_success "Docker daemon restarted with updated log limits."
else
    log_info "Docker daemon is not currently active. New log limits will apply upon startup."
fi

# ------------------------------------------------------------------------------
# 10. Kernel & Network Tuning
# ------------------------------------------------------------------------------
log_info "Applying high-concurrency kernel network tuning..."
cat > /etc/sysctl.d/99-jungle-network.conf << 'EOF'
# Increase socket backlog and connection limits
net.core.somaxconn = 4096
net.ipv4.tcp_max_syn_backlog = 4096

# Enable TCP BBR congestion control if available
net.core.default_qdisc = fq
net.ipv4.tcp_congestion_control = bbr

# Increase memory limits for network buffers
net.core.rmem_max = 16777216
net.core.wmem_max = 16777216
EOF
sysctl -p /etc/sysctl.d/99-jungle-network.conf > /dev/null 2>&1 || true
log_success "Network tuning applied."

echo "=============================================================================="
log_success "Host hardening complete!"
echo "Summary of applied protections:"
echo " - 4GB Swap space active and persisted in /etc/fstab"
echo " - UFW firewall active: Ports 22 (SSH), 80 (HTTP), 443 (HTTPS TCP/UDP) allowed"
echo " - Oracle Cloud IMDS metadata (${IMDS_IP}) blocked from Docker containers"
echo " - Fail2ban active protecting SSH with 3-retry limit"
echo " - Unattended security upgrades configured"
echo " - Docker container logs capped at 10MB x 3"
echo "=============================================================================="

