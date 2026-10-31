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
  - Refactored `app.py` wiring all security and hardening layers.
  - Verified via `tests/test_hardening.py` and `tests/test_app.py`.

### Test Verification
- Total Tests: **51 passed, 0 failed** in 0.88s.

---

## Phase 1: Reliability, Abuse Control, Performance — [NEXT]
- **1.1 Single extraction per video**: In-memory TTL cache (`cachetools.TTLCache`) with random opaque tokens for `/api/preview` and `/api/stream`.
- **1.2 Concurrency & event loop**: `gevent.monkey.patch_all()` as the first statement; `BoundedSemaphore` for `MAX_CONCURRENT_FFMPEG` (3) and `MAX_CONCURRENT_EXTRACT` (6) returning 503 on saturation.
- **1.3 Rate limiting & bot protection**: Flask-Limiter keyed on real client IP; Cloudflare Turnstile verification.
- **1.4 yt-dlp lifecycle**: Daily automated staging update + smoke test script.
- **1.5 Frontend fixes**: Compile standalone Tailwind CSS (remove CDN runtime); replace 5-second timer with cookie-based download start detection; reserve layout slots for ads.

---

## Open Risks & Notes for Operator
1. **Purging Git History for `cookies.txt`**:
   The file `cookies.txt` has been removed from the working tree and is ignored by git and docker, but it remains in older git commits (e.g. commit `e165b23`). Run the following command manually to permanently purge it from history:
   ```bash
   pip install git-filter-repo
   git filter-repo --invert-paths --path cookies.txt --force
   git push origin --force --all
   ```
2. **Oracle Cloud IMDS Egress Rule**:
   Run `sudo bash deploy/block_imds.sh` on the Ubuntu host once Docker is installed to ensure containers cannot query the instance metadata service at `169.254.169.254`.
3. **Datacenter IP Throttling**:
   Until Phase 1 caching and Phase 2 proxy routing are implemented, direct extraction on datacenter IPs without proxies may experience upstream 429 rate limiting on high-frequency requests.
