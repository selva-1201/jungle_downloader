# Production Operations Runbook

Operational procedures, incident response workflows, and maintenance runbooks for **Jungle Media Downloader**.

---

## 1. Incident: yt-dlp Extraction Broke (Platform API Change)

### Symptoms
- Downloads fail with `UNSUPPORTED_OR_BLOCKED` or `EXTRACTION_FAILED`.
- Smoke test reports unexpected failures on platforms that normally pass.

### Triage & Resolution
1. **Run the Smoke Test**:
   ```bash
   cd /opt/jungle_downloader
   python3 scripts/smoke_test.py
   ```
2. **Check for Upstream yt-dlp Release**:
   ```bash
   pip index versions yt-dlp
   ```
3. **Run Safe Docker Staged Update**:
   For Docker production deployments, execute the automated Docker updater. It updates `requirements.txt`, builds a candidate container, executes the smoke test inside it, and restarts the service on success:
   ```bash
   bash scripts/update_ytdlp_docker.sh
   ```
   *(For local/bare-metal virtualenv development, use `bash scripts/update_ytdlp.sh`).*
4. **Manual Container Rebuild & Rolling Update**:
   If manually pinning a new version:
   ```bash
   # Update pinned version in requirements.txt
   nano requirements.txt
   # Rebuild and restart the container stack
   docker compose build app
   docker compose up -d app
   ```

---

## 2. Incident: Datacenter IP Blocked (HTTP 429 / Bot Check)

### Symptoms
- YouTube, Instagram, or TikTok returns `HTTP Error 429: Too Many Requests` or bot-check challenges.
- In logs: `[WARN] Extractor <name> failed with 429 / bot detection`.
- YouTube Circuit Breaker trips to `OPEN`.

### Triage & Resolution
1. **Inspect Metrics on Localhost**:
   ```bash
   # Access internal metrics endpoint via container
   docker compose exec app curl -s localhost:5000/metrics | jq .
   ```

   Check:
   - `circuit_breaker.state`: If `OPEN`, it will automatically recover to `HALF-OPEN` after 300 seconds (`YT_CIRCUIT_BREAKER_COOLDOWN_SEC`).
   - `consecutive_failures`: Number of consecutive failures detected.

2. **Route Blocked Extractor Through Residential/Datacenter Proxy**:
   Instead of proxying all traffic (expensive), route only the blocked platform via `PROXY_RULES` in `.env`:
   ```dotenv
   PROXY_RULES={"youtube": "http://user:pass@proxy.example.com:8080", "instagram": "socks5://user:pass@proxy2.example.com:1080"}
   PROXIED_DAILY_BYTE_BUDGET_GB=50
   ```
3. **Restart the Application**:
   ```bash
   docker compose up -d app
   ```

---

## 3. Incident: Disk Full / Storage Exhaustion

### Symptoms
- Docker container crashes or fails to spawn ffmpeg child processes.
- Error: `No space left on device`.

### Triage & Resolution
1. **Check Disk and Inode Usage**:
   ```bash
   df -h
   df -i
   ```
2. **Inspect Docker Storage**:
   ```bash
   docker system df
   ```
3. **Prune Unused Docker Layers & Logs**:
   ```bash
   # Remove dangling images, stopped containers, and build cache safely.
   # CAUTION: Never pass --volumes! Passing --volumes will destroy the caddy_data volume and wipe SSL certificates.
   docker system prune -af

   # Truncate any oversized container logs
   sudo truncate -s 0 /var/lib/docker/containers/*/*-json.log
   ```
4. **Inspect `/tmp` tmpfs**:
   The `app` container mounts `/tmp` as a `512MB` tmpfs in memory. No persistent disk writes occur during media streaming.

5. **Automated Resource Monitoring (Cron)**:
   A lightweight resource watchdog script is provided in `scripts/disk_ram_alert.sh`. Set up a cron job to monitor disk and RAM usage and deliver instant Telegram alerts if usage exceeds thresholds (default: Disk >= 85%, RAM >= 90%):
   ```bash
   # Add to crontab (runs every 5 minutes):
   */5 * * * * /opt/jungle_downloader/scripts/disk_ram_alert.sh >> /var/log/jungle_resource.log 2>&1
   ```

---

## 4. Incident: High Memory / Bandwidth Saturation

### Symptoms
- Server load spikes; response times increase.
- Users report `503 Service Unavailable: Server busy, please retry in 10s`.

### Triage & Resolution
1. **Inspect Active Resource Usage**:
   ```bash
   docker stats --no-stream
   free -h
   ```
2. **Tune Concurrency Semaphores**:
   Adjust concurrency limits in `.env` downward to prevent server starvation:
   ```dotenv
   # Reduce concurrent ffmpeg muxing jobs
   MAX_CONCURRENT_FFMPEG=2

   # Reduce concurrent extraction jobs
   MAX_CONCURRENT_EXTRACT=4

   # Cap maximum proxied file size to conserve bandwidth
   PROXY_MAX_MB=150

   # Enforce direct CDN links for large files
   DIRECT_LINK_MODE=large_only
   ```
3. **Reload Configuration**:
   ```bash
   docker compose up -d app
   ```

---

## 5. Procedure: DMCA & Copyright Takedown Handling

### Policy
Jungle Media Downloader processes links ephemerally and stores no user media. However, valid rights-holder requests must be honored promptly by blacklisting specific URLs or entire extractor domains.

### Procedure (Zero Downtime)
1. **Review Notice**:
   Verify the notice received at `DMCA_EMAIL` contains the copyrighted work and infringing URL.
2. **Block Entire Domain**:
   If an entire platform or domain must be blocked, append the domain to `data/blocked_domains.txt`:
   ```bash
   echo "infringing-domain.com" >> data/blocked_domains.txt
   ```
   *Note: `BlocklistManager` monitors file `mtime` and reloads automatically without restarting the server.*
3. **Block Specific URL Pattern**:
   To block an individual URL or pattern:
   ```bash
   echo "https://example.com/watch?v=forbidden123" >> data/blocked_urls.txt
   ```
4. **Verify Block Takes Effect**:
   ```bash
   curl -s -X POST http://127.0.0.1:5000/api/download \
     -H "Content-Type: application/json" \
     -d '{"url": "https://infringing-domain.com/video"}'
   # Must return 400 with "This domain is not supported." or DMCA removal notice
   ```
5. **Reply to Notice**:
   Send confirmation acknowledging the restriction within 24 hours.

---

## 6. Disaster Recovery & Zero-DB Restoral

Because Jungle Media Downloader is completely **stateless** (no database, no user accounts, no persisted media files), recovery on a brand-new VM takes less than 5 minutes.

### Recovery from Scratch (New VM)
1. **Provision Ubuntu 24.04 instance** on Oracle Cloud (see `DEPLOY.md`).
2. **Clone Repository & Run Setup**:
   ```bash
   sudo mkdir -p /opt/jungle_downloader
   sudo chown $USER:$USER /opt/jungle_downloader
   git clone https://github.com/selva-mern12/jungle_downloader.git /opt/jungle_downloader
   cd /opt/jungle_downloader
   sudo bash deploy/host-setup.sh
   ```
3. **Restore Secrets**:
   Copy `.env` from your offline password manager / encrypted backup:
   ```bash
   nano .env
   ```
4. **Start Application**:
   ```bash
   docker compose up -d --build
   ```
5. **Verify Stack**:
   ```bash
   curl -i http://localhost:5000/healthz
   ```

---

## 7. Monitoring & Health Checks

- **External Monitoring**: Point [UptimeRobot](https://uptimerobot.com) or StatusCake to:
  `https://example.com/healthz` (Expects HTTP 200 `{"status": "ok"}`).
- **Telegram Smoke Test Alerts**:
  Configure `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` in the host environment for instant notification if daily automated tests fail.
- **Internal Metrics Inspection**:
  ```bash
  docker compose exec app curl -s localhost:5000/metrics
  ```
- **Log Inspection**:

  ```bash
  # Follow live JSON application logs
  docker compose logs -f --tail=100 app

  # Follow Caddy access and error logs
  docker compose logs -f --tail=100 caddy
  ```

