# Production Deployment Guide: Oracle Cloud (Ubuntu 24.04)

Complete, step-by-step production deployment guide for **Jungle Media Downloader** on Oracle Cloud Infrastructure (OCI).

---

## 1. Architecture Overview

- **Host OS**: Ubuntu 24.04 LTS (Minimal or Server)
- **Primary Compute**: Ampere A1 (`VM.Standard.A1.Flex`, 4 OCPU, 24 GB RAM)
- **Fallback Compute**: AMD Micro (`VM.Standard.E2.1.Micro`, 1 OCPU, 1 GB RAM)
- **Container Stack**:
  - `jungle_app`: Python 3.11, Gunicorn with Gevent workers, ffmpeg, read-only root FS, tmpfs `/tmp`.
  - `jungle_caddy`: Caddy 2.9 reverse proxy with automatic TLS, Cloudflare trusted IP restoration, unbuffered media streaming (`flush_interval -1`), and strict `/metrics` blocking.
  - `jungle_redis`: (Optional profile) In-memory LRU cache.
- **Edge Security & CDN**: Cloudflare DNS, WAF, Turnstile Bot Protection, and optional `MEDIA_HOST` grey-cloud direct streaming.

---

## 2. Oracle Cloud (OCI) Account Setup & Hardening

### 2.1 Region Selection
Choose an Indian region for minimal latency to the primary user base:
- **Primary**: `ap-mumbai-1` (Mumbai)
- **Alternative**: `ap-hyderabad-1` (Hyderabad)

### 2.2 Critical: Upgrade to Pay-As-You-Go (PAYG)
> [!IMPORTANT]
> **Why Upgrade to PAYG?**
> Oracle Cloud free-tier accounts are subject to automatic reclamation of "idle" compute instances after 30 days. Upgrading to a **Pay-As-You-Go** account removes the idle reclamation policy permanently while retaining full access to the Always Free allowance (4 ARM OCPUs, 24 GB RAM, 200 GB block storage).

1. Log in to the [Oracle Cloud Console](https://cloud.oracle.com).
2. Navigate to **Billing & Cost Management** &rarr; **Payment Information** &rarr; **Upgrade Account**.
3. Select **Pay As You Go** and complete credit/debit card verification (temporary \$100 hold that is reversed immediately).

### 2.3 Set a \$1 Monthly Budget Alert
To guarantee zero unexpected charges:
1. In the OCI Console, search for **Cost Management** &rarr; **Budgets**.
2. Click **Create Budget**:
   - **Name**: `Zero-Cost-Safety-Net`
   - **Budget Scope**: Compartment (root)
   - **Target Monthly Spend**: `$1.00 USD` (or ₹100 INR)
   - **Threshold**: `100% of budgeted amount` (or 80% forecast)
   - **Email Recipients**: Your alert email
3. Click **Create**. You will be alerted immediately if non-free resources incur any charges.

---

## 3. Provisioning the Compute Instance

### 3.1 Primary Option: Ampere A1 Flex (ARM64)
1. Navigate to **Compute** &rarr; **Instances** &rarr; **Create Instance**.
2. **Name**: `jungle-prod-01`
3. **Placement**: Choose any Availability Domain.
4. **Image**: `Canonical Ubuntu 24.04 Minimal` (or Server).
5. **Shape**:
   - Click **Change Shape** &rarr; **Ampere (Arm-based Processor)**.
   - Select **VM.Standard.A1.Flex**.
   - Configure: **4 OCPU** and **24 GB RAM** (fits within Always Free tier).
6. **Networking**:
   - Create new Virtual Cloud Network (VCN) or select existing.
   - Assign a public IPv4 address.
7. **Boot Volume**:
   - Specify **100 GB** to **200 GB** (Always Free tier includes up to 200 GB total).
8. **Add SSH Keys**:
   - Upload your local SSH public key (`~/.ssh/id_ed25519.pub`).
9. Click **Create**.

### 3.2 Fallback Option: AMD Micro (x86_64)
If Mumbai/Hyderabad reports `Out of host capacity` for Ampere A1:
- Select **AMD** &rarr; **VM.Standard.E2.1.Micro** (1 OCPU, 1 GB RAM, 50 GB boot volume).
- Note: The multi-stage `Dockerfile` and `docker-compose.yml` work identically on both ARM64 and AMD64 architectures. On AMD Micro, ensure the 4GB swap space configured in `host-setup.sh` is active.

### 3.3 Reserve a Static Public IP
1. Navigate to **Networking** &rarr; **IP Management** &rarr; **Reserved Public IPs**.
2. Click **Reserve Public IP Address** (e.g., `jungle-mumbai-ip`).
3. Associate it with your instance VNIC to ensure your IP does not change on reboot.

### 3.4 Enforce IMDSv2 (Instance Metadata Service v2)
To mitigate SSRF risks that probe cloud instance metadata endpoints (`169.254.169.254`), enforce IMDSv2 on your compute instance so legacy v1 requests without session tokens are refused:

1. **Via OCI CLI**:
   ```bash
   oci compute instance update \
     --instance-id <YOUR_INSTANCE_OCID> \
     --are-legacy-imds-endpoints-disabled true
   ```
2. **Via OCI Console**:
   - Go to **Compute** &rarr; **Instances** &rarr; Select your instance.
   - Click **Edit**.
   - Expand **Advanced Options** &rarr; **Management**.
   - Under **Instance Metadata Service**, select **Require numeric headers (IMDSv2 only)**.
   - Click **Save Changes**.

---

## 4. OCI VCN Security List (Firewall)

Oracle Cloud blocks incoming traffic at the Virtual Cloud Network level by default.

1. Navigate to **Networking** &rarr; **Virtual Cloud Networks** &rarr; Click your VCN &rarr; **Security Lists** &rarr; **Default Security List**.
2. Under **Ingress Rules**, click **Add Ingress Rules**:

| Source CIDR | IP Protocol | Destination Port Range | Description |
|---|---|---|---|
| `0.0.0.0/0` | TCP | `22` | SSH Remote Access |
| `0.0.0.0/0` | TCP | `80` | HTTP (Caddy ACME Challenge & Redirect) |
| `0.0.0.0/0` | TCP | `443` | HTTPS (Caddy TLS) |
| `0.0.0.0/0` | UDP | `443` | HTTPS HTTP/3 QUIC |

---

## 5. Cloudflare DNS & Edge Setup

### 5.1 DNS Records
1. In Cloudflare Dashboard, select your domain.
2. Under **DNS** &rarr; **Records**, add:
   - **Type**: `A` | **Name**: `@` (or `jungle`) | **IPv4**: `<YOUR_ORACLE_PUBLIC_IP>` | **Proxy status**: `Proxied (Orange Cloud)`
   - **Type**: `A` | **Name**: `www` | **IPv4**: `<YOUR_ORACLE_PUBLIC_IP>` | **Proxy status**: `Proxied (Orange Cloud)`

### 5.2 Video Streaming Policy & MEDIA_HOST Option
> [!WARNING]
> **Cloudflare Terms of Service (ToS) Risk**:
> Cloudflare Section 2.8 of the Self-Serve Subscription Agreement restricts proxying disproportionate volumes of non-HTML media (video/audio streams) through the free/pro proxy CDN without Cloudflare Stream or Enterprise arrangements. Proxying significant video bandwidth through an orange-clouded domain carries an account termination or origin-blocking risk.
> 
> **Mode A (Single Domain Default):**
> Keep `DOMAIN=example.com` orange-clouded. Suitable for light usage or short clip downloads.
> 
> **Mode B (Dual Domain / MEDIA_HOST for High Volume):**
> Add a DNS record for `media.example.com` set to **DNS Only (Grey Cloud)**.
> In `.env`, set:
> ```bash
> SITE_URL=https://example.com
> MEDIA_HOST=https://media.example.com
> ```
> Uncomment the `media.{$DOMAIN}` block in `Caddyfile`. This routes the HTML app through Cloudflare's DDoS/Turnstile edge while routing raw video streams directly to Caddy via Let's Encrypt TLS.
> 
> *Current Implementation Realities*:
> `MEDIA_HOST` is currently a configuration-level blueprint; client endpoints (`/api/stream`, `/api/preview`) are served relatively from the origin. Dual-domain frontend separation is an architectural extension point.
> Additionally, Phase 4 monetization features (advertising banners, GDPR/ePrivacy consent CMP templates `_ads.html`, `_consent.html`, and F30 dedicated pages) are reserved extension points and remain pending/disabled at launch.

### 5.3 SSL/TLS Encryption Mode
1. In Cloudflare, navigate to **SSL/TLS** &rarr; **Overview**.
2. Select **Full (strict)**.

### 5.4 Cloudflare Turnstile (Bot Protection)
1. In Cloudflare, navigate to **Turnstile** &rarr; **Add Site**.
2. **Domain**: `example.com` (and `localhost` for testing).
3. **Widget Mode**: Managed.
4. Copy the **Site Key** and **Secret Key** into `.env`.

---

## 6. Host Hardening & System Setup

### 6.1 Connect via SSH
```bash
ssh -i ~/.ssh/id_ed25519 ubuntu@<YOUR_ORACLE_PUBLIC_IP>
```

### 6.2 Clone Repository & Prepare Directory
```bash
sudo mkdir -p /opt/jungle_downloader
sudo chown ubuntu:ubuntu /opt/jungle_downloader
git clone https://github.com/selva-mern12/jungle_downloader.git /opt/jungle_downloader
cd /opt/jungle_downloader
```

### 6.3 Review and Run Host Hardening Script
Review the script to inspect the changes:
```bash
less deploy/host-setup.sh
```

Execute the hardening script with `sudo`:
```bash
sudo bash deploy/host-setup.sh
```
This automated script configures:
- 4GB Swap with `vm.swappiness=10`
- UFW rules (22, 80, 443 TCP/UDP)
- Fixes Oracle Cloud default `rules.v4` iptables drops
- Fail2ban with 3-strike SSH ban
- Automatic unattended security upgrades
- Docker IMDS metadata protection (`DOCKER-USER` drop for `169.254.169.254`)
- Network performance sysctl tuning

### 6.4 Install Docker & Docker Compose Plugin
```bash
# Add official Docker GPG key and repository
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc

echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

# Allow ubuntu user to execute docker without sudo
sudo usermod -aG docker ubuntu
newgrp docker
```

---

## 7. Application Configuration & Deployment

### 7.1 Generate Production Secrets & Environment (`.env`)
Generate a strong random secret key for session verification:
```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

Create and edit the `.env` file:
```bash
cp .env.example .env
nano .env
```
Ensure the following variables are configured:
```dotenv
# General Settings
SITE_URL=https://example.com
DOMAIN=example.com
ACME_EMAIL=admin@example.com
ENV=prod
DEBUG=false
PORT=5000
LOG_LEVEL=INFO
SECRET_KEY=replace_with_generated_64_hex_secret

# Bot Protection
TURNSTILE_SITE_KEY=0x4AAAAAA...
TURNSTILE_SECRET=0x4AAAAAA...
TRUST_CLOUDFLARE_IP=true

# Concurrency & Media Limits
MAX_CONCURRENT_FFMPEG=3
MAX_CONCURRENT_EXTRACT=6
MAX_CONCURRENT_YOUTUBE=2
PROXY_MAX_MB=300
DIRECT_LINK_MODE=large_only

# Resource Limits (Compose)
# For Ampere A1 (4 OCPU, 24GB RAM):
APP_MEM_LIMIT=2048M
APP_CPUS=2.0
# For AMD Micro Fallback (1 OCPU, 1GB RAM): set APP_MEM_LIMIT=1024M and APP_CPUS=1.0 (with 4GB swap active)

# YouTube Mode
YT_MODE=best_effort
YT_PLAYER_CLIENTS=
YT_CIRCUIT_BREAKER_FAILURES=3
YT_CIRCUIT_BREAKER_COOLDOWN_SEC=300

# Note on YouTube Extraction in Datacenters:
# Datacenter IPs are frequently blocked by YouTube (HTTP 429). The container includes Node.js
# for yt-dlp's JS engine. If smoke tests fail on YouTube, set YT_MODE=disabled until a residential
# proxy (YTDLP_PROXY / PROXY_RULES) is configured.

# Telegram Alerts (for automated yt-dlp updater and disk/RAM resource alerts)
TELEGRAM_BOT_TOKEN=123456789:ABCdefGHIjklMNOpqrSTUvwxYZ
TELEGRAM_CHAT_ID=-1001234567890

# Legal & Contacts
CONTACT_EMAIL=support@example.com
DMCA_EMAIL=dmca@example.com
```

### 7.2 Multi-Architecture Container Build (Docker Buildx)
Per SPEC section 6.1, the project supports dual-architecture compilation for ARM64 (Oracle Ampere A1) and AMD64 (x86_64):
```bash
# Set up Docker Buildx instance
docker buildx create --name jungle-builder --use 2>/dev/null || true
docker buildx inspect --bootstrap

# Build local target image:
docker compose build app
```

### 7.3 Validate Docker Compose Configuration
```bash
docker compose config
```

### 7.4 Build and Launch Stack
```bash
docker compose build --pull
docker compose up -d
```

Check running containers:
```bash
docker compose ps
docker compose logs -f
```

---

## 8. Systemd Service Setup (Auto-start on Boot)

Install the systemd unit so the Docker Compose stack manages itself across reboots:

```bash
sudo cp deploy/jungle-downloader.service /etc/systemd/system/jungle-downloader.service
sudo systemctl daemon-reload
sudo systemctl enable jungle-downloader.service
```

---

## 9. Automated yt-dlp Updates (Docker Staged Rebuild Cron)

Because the production application runs inside a read-only Docker container, in-place `pip install` cannot be used at runtime. Use `scripts/update_ytdlp_docker.sh` to automatically check for upstream releases, build a staged candidate image, run the automated smoke test suite inside the container, and promote it with zero downtime.

Configure a weekly cron job on the host:

```bash
crontab -e
```
Add the following entry (runs weekly on Sunday at 03:00 UTC):
```cron
0 3 * * 0 /opt/jungle_downloader/scripts/update_ytdlp_docker.sh >> /var/log/ytdlp_docker_update.log 2>&1
```
*(Optional: If running in a local/bare-metal virtualenv outside of Docker, use `scripts/update_ytdlp.sh` instead).*


---

## 10. Post-Deployment Verification

1. **Verify Health Endpoint**:
   ```bash
   # From host via Caddy:
   curl -k https://localhost/healthz
   # Or directly inside the app container:
   docker compose exec app python -c "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:5000/healthz').read().decode())"
   curl -i https://example.com/healthz
   ```
2. **Verify Metrics Endpoint Access**:
   ```bash
   # From local machine / public internet (must be blocked):
   curl -i https://example.com/metrics
   # Expected: HTTP 403 Forbidden

   # Internal check on host via container:
   docker compose exec app python -c "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:5000/metrics').read().decode())"
   ```

3. **Execute Smoke Test**:
   ```bash
   docker compose exec app python scripts/smoke_test.py
   ```
4. **Browser Test**:
   - Open `https://example.com`
   - Test preview and download of a supported URL
   - Verify Turnstile challenge completes and sets `cf_clearance_token` cookie
   - Verify download initiated and completed smoothly.

5. **Origin Lockdown (Cloudflare Only)**:
   Lock down host and Docker container inbound HTTP/HTTPS ports to accept traffic only from official Cloudflare proxy edges:
   ```bash
   sudo bash deploy/cloudflare-ufw.sh
   # If using MEDIA_HOST direct streaming:
   # sudo bash deploy/cloudflare-ufw.sh --allow-direct-media
   ```

