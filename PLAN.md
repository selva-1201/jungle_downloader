# Implementation Plan & Progress Tracking

## Overview
Hardening "Jungle Media Downloader" for production readiness on an Oracle Cloud VM (Ubuntu 24.04).

---

## Phase 0: Blockers (Security & Cleanup) — [DONE]

### Sub-step Completion
- [x] **0a: Cleanup & Configuration**
  - Removed `cookies.txt` from working tree.
  - Added `.gitignore` and `.dockerignore` covering `.env`, secrets, `.venv`, `cookies.txt`.
  - Created `config.py` with centralized, typed environment variables and defaults.
  - Created `.env.example` documenting all configuration keys.
  - Pinned all dependencies in `requirements.txt` including `yt-dlp==2026.10.26` and `pytest==9.2.0`.
- [x] **0b: SSRF Guard (Failing Tests First -> Implemented)**
  - Authored failing tests in `tests/test_security.py` covering link-local (Oracle IMDS `169.254.169.254`), loopback (`127.0.0.1`, `[::1]`, `localhost`), private RFC 1918, CGNAT, IPv4-mapped IPv6, octal/hex/decimal IP tricks (`0x7f000001`, `2130706433`, `0177.0.0.1`), DNS rebinding/private resolution, and redirect hopping.
  - Implemented `security.py` with `validate_public_url`, `safe_requests_get`, `build_safe_ffmpeg_args`, and `get_safe_ytdlp_opts`.
  - Created host defense script `deploy/block_imds.sh` to drop container egress to `169.254.169.254` via `DOCKER-USER` iptables chain.
- [x] **0c: Adult & Blocked Site Enforcement**
  - Created `scripts/gen_blocklist.py` extracting adult candidates with explicit whitelisting for mainstream platforms (including "AdultSwim").
  - Generated and committed `data/adult_extractors.txt`, `data/blocked_domains.txt`, and `data/drm_extractors.txt`.
  - Implemented `blocklist.py` with fast offline extractor matching and disk `mtime` hot-reloading.
  - Verified via `tests/test_blocklist.py`.
- [x] **0d: Hardening**
  - Disabled `DEBUG` in production; removed hardcoded `app.run(debug=True)`.
  - Implemented `hardening.py`:
    - Shielded error messages: exceptions mapped to user-friendly messages without leaking stack traces.
    - `sanitize_filename` & RFC 5987 `build_content_disposition` with UTF-8 support (Tamil, Unicode) and ASCII fallback, stripping CRLF injection.
    - Security headers middleware: HSTS, `nosniff`, `SAMEORIGIN`, `strict-origin-when-cross-origin`, and CSP Report-Only.
    - Cloudflare IP validation: only trusts `CF-Connecting-IP` when arriving via verified Cloudflare CIDRs or loopback reverse proxy.
  - Added `/healthz`, dynamic `/robots.txt` and `/sitemap.xml` using `SITE_URL`.
  - Added clean URLs for `/terms` and `/privacy` with 301 redirects from `.html` routes.
  - Added custom `templates/404.html` and `templates/500.html`.
  - Verified via `tests/test_hardening.py` and `tests/test_app.py`.

---

## Phase 1: Reliability, Abuse Control, Performance — [DONE]
- [x] **1.1 Single extraction per video**: In-memory TTL cache (`cache_store.py` / `cachetools.TTLCache`) storing info dict under opaque random tokens for `/api/preview` and `/api/stream`; 410 Gone with `SESSION_EXPIRED` on cache miss/expiry.
- [x] **1.2 Concurrency & event loop**: `BoundedSemaphore` for `MAX_CONCURRENT_FFMPEG` (3) and `MAX_CONCURRENT_EXTRACT` (6) returning 503 + Retry-After on saturation; duration capping (`MAX_DURATION_SEC`).
- [x] **1.3 Rate limiting & bot protection**: Flask-Limiter keyed on real client IP; Cloudflare Turnstile token verification with signed clearance cookie.
- [x] **1.4 yt-dlp lifecycle**: Daily update script (`scripts/update_ytdlp.sh`) + automated smoke testing script (`scripts/smoke_test.py`).
- [x] **1.5 Frontend fixes & cookie detection**: Cookie `dl_<token>` set by `/api/stream` on response start.

---

## Phase 2: Delivery Modes, YouTube Best-Effort, Proxy Routing, Sites List — [DONE]

### Sub-step Completion
- [x] **2.1 Hybrid Delivery Strategy**:
  - Implemented `is_direct_cdn_eligible` supporting `DIRECT_LINK_MODE` (`off`, `large_only`, `always`) and `PROXY_MAX_MB`.
  - Single progressive http(s) files without special headers/IP binding can direct-link / 302 redirect.
  - Proxy streaming preserves RFC 5987 `Content-Disposition` and sets download initiation detection cookie (`dl_{token}`).
- [x] **2.2 YouTube Best-Effort with Circuit Breaker**:
  - Dedicated concurrency semaphore (`youtube_extract_semaphore`) isolating YouTube from starving global extractors.
  - Built `YouTubeCircuitBreaker` tracking consecutive bot-check / 429 failures.
  - Trips to OPEN after 3 failures with friendly message ("YouTube is temporarily unavailable, other sites work").
  - Auto-recovers to HALF-OPEN after 300s cooldown.
  - yt-dlp `extractor_args` configured with `player_client` (`android`, `web`).
- [x] **2.3 Per-Extractor Proxy Routing & Daily Budget**:
  - Implemented `ProxyRouter` mapping extractors via `PROXY_RULES` (case-insensitive) with global `YTDLP_PROXY` fallback.
  - Enforced `PROXIED_DAILY_BYTE_BUDGET_GB` daily bandwidth cap with graceful degradation to direct.
  - Egress hygiene: in-memory per-extractor metrics and 429 spacing backoff (60s cooldown).
  - Protected `/metrics` endpoint accessible on localhost only.
- [x] **2.4 Supported Sites List**:
  - Implemented `sites_catalog.py` building filtered sites at startup from `gen_extractors()`.
  - Excludes `Generic`, adult extractors (when `ALLOW_ADULT=False`), DRM list, and broken extractors.
  - Deduplicated base names, prettified display names, sorted alphabetically.
  - Dynamically calculates count rounded down to nearest hundred ("1200+ sites").
  - `/api/sites` with ETag caching (HTTP 304).
  - `/supported-sites` standalone crawlable server-rendered page.
  - Removed ~1800 hardcoded strings from `templates/index.html`; lazy-loads from `/api/sites` on "+ More Sites" click.
- [x] **2.5 Smoke URLs & Smoke Test Update**:
  - Added TikTok, Instagram, Facebook, Reddit to `tests/smoke_urls.json`.
  - Executed `scripts/smoke_test.py` against live platform endpoints.
  - Reported results: Facebook (PASS), TikTok/Instagram/Reddit (failed due to datacenter IP/auth restrictions).
  - Marked failing entries `"expected_fail_on_datacenter": true` without deleting them.
  - Updated `scripts/smoke_test.py` to recognize `expected_fail_on_datacenter`.
- [x] **2.6 Browser Verification**:
  - Tested `/`, `/supported-sites`, and `/api/sites` with curl and headless Chromium.
  - Verified 1297 supported site chips, search filtering, and responsive UI layout.

### Test Verification
- Unit & Integration Tests: **73 passed, 0 failed** in 1.62s (`pytest`).
- Live Smoke Test: **6/9 operational, 3 expected datacenter blocks, 0 unexpected failures**.

---

## Phase 3: Oracle Deployment (deploy/ directory + DEPLOY.md) — [DONE]

### Sub-step Completion
- [x] **3.1 Multi-Stage Multi-Arch Dockerfile**:
  - Multi-stage build with `python:3.11-slim` runtime.
  - Stage 1 (`tailwind-builder`): Downloads standalone Tailwind CSS binary matching host/target architecture (`arm64` vs `amd64`) at build time; compiles minified `static/css/app.css` without Node.js.
  - `bin/` stays gitignored and `.dockerignore` excludes local binaries, tests, and secrets.
  - Hardened non-root user (`appuser:appgroup`, UID 10001).
  - Production Gunicorn configuration: single worker (`--workers 1`) to preserve in-memory token cache & rate limiter integrity, `--worker-connections 1000`, and `--graceful-timeout 30`.
  - Read-only root filesystem resilience: mapped `TMPDIR`, `HOME`, `XDG_CACHE_HOME`, and `XDG_CONFIG_HOME` to tmpfs `/tmp`.
  - Built-in `HEALTHCHECK` probing `http://127.0.0.1:5000/healthz`.
  - Pinned `ffmpeg`, `curl`, and runtime requirements.
- [x] **3.2 Production Docker Compose Stack (`docker-compose.yml`)**:
  - `app` service: read-only root filesystem with `tmpfs: /tmp:512m`, memory limit `2048M`, cpu limit `2.0`, `no-new-privileges:true`.
  - `caddy` service: Caddy 2.9 reverse proxy publishing 80, 443 (TCP/UDP HTTP/3 QUIC), memory limit `256M`. Environment passes `SITE_ADDRESS`.
  - Optional `redis` service profile for distributed caching (`profiles: ["redis"]`).
  - Container log rotation capped at 10MB x 3 files (`json-file`).
  - Verified with `docker compose config`.
- [x] **3.3 Hardened Reverse Proxy & Client IP Trust**:
  - Dynamic site address configured from environment variable `SITE_ADDRESS` (`{$SITE_ADDRESS:localhost}`).
  - Strictly blocks `/metrics` from external/public traffic (`403 Forbidden`); accessible only from localhost loopback (`not remote_ip 127.0.0.1 ::1`).
  - Caddy forwards verified client IP in `X-Real-IP`.
  - Application `hardening.get_real_client_ip`: trusts `X-Real-IP` (falling back to peer socket address) ONLY when the immediate peer is within `TRUSTED_PROXY_CIDRS` (`172.16.0.0/12`, `10.0.0.0/8`, `127.0.0.1/32`).
  - Ignores `CF-Connecting-IP` and `X-Forwarded-For` entirely in the app.
  - Flask-Limiter `key_func` verified: separate client IPs get distinct rate limit buckets under trusted reverse proxy peers.
  - `flush_interval -1` and 600s transport timeouts on `/api/stream` and `/api/preview` for unbuffered media streaming.
  - Gzip/zstd compression enabled for text/JSON but explicitly excluded on streaming media.
  - 10MB request body limit and security headers (`nosniff`, `SAMEORIGIN`, `strict-origin-when-cross-origin`).
  - Commented `MEDIA_HOST` grey-cloud configuration for high-volume streaming Cloudflare TOS compliance.
  - Documented `/metrics` verification via `docker compose exec app curl -s localhost:5000/metrics` in both `DEPLOY.md` and `RUNBOOK.md`.

- [x] **3.4 Host Hardening Script (`deploy/host-setup.sh`) & Automated Updates**:
  - Non-destructive reviewable setup script for Oracle Ubuntu 24.04 (Mumbai/Hyderabad).
  - Configures 4GB swapfile with conservative `vm.swappiness=10`.
  - UFW firewall: allows 22 (SSH), 80 (HTTP), 443 (HTTPS TCP + UDP HTTP/3 QUIC); fixes Oracle default iptables drops.
  - Fail2ban jail for SSH with 3-retry max and 1-hour ban time.
  - Automatic unattended security upgrades configured.
  - Oracle Cloud IMDS metadata protection: blocks container egress to `169.254.169.254` via `DOCKER-USER` chain.
  - Docker daemon log limits default configuration (`/etc/docker/daemon.json`).
  - Docker deployment automated yt-dlp updater (`scripts/update_ytdlp_docker.sh`): builds staged candidate image, executes smoke test inside container, promotes image and restarts container.

- [x] **3.5 Systemd Unit & Documentation**:
  - Created `deploy/jungle-downloader.service` for automatic compose stack management on host boot.
  - Comprehensive `DEPLOY.md`: step-by-step for Oracle Cloud Ubuntu 24.04, Mumbai region, Ampere A1 (with AMD fallback), Pay-As-You-Go upgrade to prevent idle reclaim, and $1 monthly budget alert.
  - Comprehensive `RUNBOOK.md`: actionable operational runbooks for yt-dlp breakages, datacenter IP blocks (429), disk full emergencies, memory/bandwidth spikes, DMCA takedown procedures, and zero-DB disaster recovery.

---

## Phase 4: Legal, Trust, SEO (organic growth) — [NEXT]
- **4.1 Accurate terms & privacy**: Rewrite terms.html and privacy.html reflecting ephemeral link processing, short retention, no media storage, Cloudflare/Turnstile and ad networks.
- **4.2 DMCA & Contact pages**: Implement /dmca procedure and /contact route with obfuscated email; integrate takedown with `data/blocked_domains.txt` and `data/blocked_urls.txt`.
- **4.3 Consent banner**: Lightweight GDPR/ePrivacy cookie banner for EU/UK traffic using `CF-IPCountry`.
- **4.4 Platform landing pages**: Generate crawlable SEO landing pages (`/instagram-reels-downloader`, `/tiktok-video-downloader`, `/youtube-video-downloader`, `/status-save`, etc.) with unique copy and JSON-LD schema.
- **4.5 Growth & i18n checklist**: Google Search Console / Bing Webmaster setup guide and i18n scaffolding.


---

## Open Risks & Notes for Operator
1. **Purging Git History for `cookies.txt`**:
   The file `cookies.txt` has been removed from the working tree and is ignored by git and docker, but it remains in older git commits. Run the following command manually to permanently purge it from history:
   ```bash
   pip install git-filter-repo
   git filter-repo --invert-paths --path cookies.txt --force
   git push origin --force --all
   ```
2. **Oracle Cloud IMDS Egress Rule**:
   Run `sudo bash deploy/block_imds.sh` on the Ubuntu host once Docker is installed to ensure containers cannot query the instance metadata service at `169.254.169.254`.
3. **Datacenter IP Throttling**:
   YouTube, Instagram, Facebook, and TikTok aggressively challenge or block datacenter IP addresses (e.g. Oracle / AWS / GCP). The circuit breaker and per-extractor proxy rules in Phase 2 ensure these upstream blocks never crash or degrade downloads from other sites.
