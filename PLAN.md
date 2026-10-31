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

## Phase 3: Oracle Deployment (deploy/ directory + DEPLOY.md) — [NEXT]
- **3.1 Dockerfile**: Multi-stage, python 3.11/3.12-slim, non-root user, multi-arch (linux/arm64, linux/amd64), HEALTHCHECK /healthz.
- **3.2 docker-compose.yml**: Services app + caddy (+ optional redis profile), restart policies, mem/cpu limits, log rotation.
- **3.3 Caddyfile**: Auto-TLS / Cloudflare Origin cert, Cloudflare trusted_proxies, flush_interval -1 for streaming, security headers.
- **3.4 Cloudflare plan**: Orange-cloud HTML, MEDIA_HOST grey-cloud media streaming option.
- **3.5 Host hardening & runbooks**: UFW, fail2ban, IMDS iptables egress rule, systemd unit, RUNBOOK.md.

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
