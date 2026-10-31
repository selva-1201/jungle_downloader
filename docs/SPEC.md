In this repository: "Jungle Media Downloader", a Flask + yt-dlp + ffmpeg web app. Your job is to make it PRODUCTION READY for a
free, ad-supported, public website, hosted on an Oracle Cloud VM, growing ORGANICALLY with zero
marketing/proxy budget.

============================================================
0. WORKING RULES
============================================================
- First read the whole repo (app.py, templates/index.html, terms.html, privacy.html, Dockerfile,
  Procfile, requirements.txt, robots.txt, sitemap.xml, static files). Then write PLAN.md
  summarizing what you found and your assumptions. Do not ask me questions unless truly blocked;
  state assumptions in PLAN.md and continue.
- Work in phases (below). One git commit per phase (conventional commits). At the end of each
  phase output: summary, files changed, how to test it, remaining TODOs. Do not start the next
  phase until the current phase's tests pass.
- NEVER run destructive git commands (filter-repo, force-push, reset --hard). Print the exact
  commands for me to run instead.
- Never read/print/commit real secrets. Use .env.example with placeholders.
- No database, no user accounts, no login. Keep everything stateless apart from in-memory TTL
  caches. Redis only as an OPTIONAL backend behind an env flag, off by default.
- Keep the existing look and feel (dark theme, Jungle branding, "STATUS SAVE" WhatsApp feature,
  FAQ section, Video/Audio tabs, preview player). Do not redesign; improve.
- Keep dependencies minimal and pin every version.
- Code must run on BOTH linux/arm64 (Oracle Ampere A1) and linux/amd64 (fallback AMD micro).
- Write pytest tests for every security-relevant function. Add comments only where logic is
  non-obvious.

============================================================
1. PRODUCT DECISIONS (already made, do not revisit)
============================================================
- Target host: Oracle Cloud VM, Ubuntu 24.04, Docker Compose, Caddy reverse proxy, Cloudflare
  in front. No budget for proxies right now.
- Launch focus: TikTok, Instagram, X/Twitter, Facebook, Reddit, Pinterest, Vimeo, SoundCloud,
  Dailymotion, Twitch, LinkedIn, Snapchat, JioSaavn, Bilibili, plus YouTube as "best effort".
- Homepage shows only ~15 popular site chips. "+ More Sites" opens a searchable modal that
  lazy-loads from /api/sites. Marketing copy says "1000+ sites supported" (see section 5.4).
- Adult sites: NOT listed anywhere (list, SEO text, FAQ, landing pages, sitemap), controlled by
  env flag ALLOW_ADULT. DEFAULT = false. When false the backend rejects them with
  "This site is not supported". The flag must never be toggled on the ad-monetized domain;
  document this in README (ad-network policy compliance is continuous; a separate domain/
  deployment would be needed for adult-friendly traffic).
- Generic extractor (arbitrary web pages) is DISABLED by default (ALLOW_GENERIC=false).
- Pages: / (index), /terms, /privacy, /dmca, /contact, /supported-sites, plus per-platform
  landing pages (section 7). Serve clean URLs; 301 redirect /terms.html -> /terms etc.
- Ads: NOT enabled at launch. Build the ad slot system now behind ADS_ENABLED=false
  (section 8). No pop-unders by default, no fake buttons, no hidden tabs, no auto-closing tabs.
- Analytics: Cloudflare Web Analytics only (cookieless). No Google Analytics.

============================================================
2. ENV CONFIG (create config.py + .env.example; no hardcoded values in app code)
============================================================
SITE_URL, ENV (dev|prod), DEBUG=false, PORT=5000, LOG_LEVEL,
ALLOW_ADULT=false, ALLOW_GENERIC=false,
ADS_ENABLED=false, ADS_SCRIPT_URLS (comma list), CONSENT_REQUIRED_COUNTRIES (default EEA+UK+CH),
CACHE_BACKEND=memory|redis, REDIS_URL, META_CACHE_TTL=1500,
MAX_DURATION_SEC=1200, MAX_FILESIZE_MB=500, PROXY_MAX_MB=300,
MAX_CONCURRENT_FFMPEG=3, MAX_CONCURRENT_EXTRACT=6, FFMPEG_TIMEOUT_SEC=900,
RATE_LIMIT_EXTRACT="10/minute;100/day", RATE_LIMIT_STREAM="6/minute;40/day",
TURNSTILE_SITE_KEY, TURNSTILE_SECRET (optional; if empty, skip verification),
TRUST_CLOUDFLARE_IP=true,
YT_MODE=best_effort|proxy|disabled, YTDLP_PROXY (global, optional),
PROXY_RULES (JSON: extractor-key -> proxy URL, e.g. {"Youtube": "http://..."}),
PROXIED_DAILY_BYTE_BUDGET_GB (default 0 = unlimited),
COOKIES_FILE (path OUTSIDE the repo, optional; never baked into the image),
MEDIA_HOST (optional, e.g. dl.example.com; see 6.4),
CONTACT_EMAIL, DMCA_EMAIL, BLOCKED_URLS_FILE.

============================================================
3. PHASE 0 - BLOCKERS (security + cleanup)
============================================================
3.1 Secrets/hygiene
- A cookies.txt with Google session cookies was committed earlier (account already signed out
  everywhere). Remove it from the working tree, add to .gitignore AND .dockerignore along with
  .env, *.pem, venvs, .git, __pycache__. Create .dockerignore.
- Print (do not run) the git-filter-repo commands to purge cookies.txt from history.
- Cookies become optional: read from COOKIES_FILE only if set and the file exists.

3.2 SSRF guard (module security.py, fully unit-tested)
- validate_public_url(url): scheme must be http/https; no userinfo; port only 80/443 (or
  none); hostname required; resolve ALL A/AAAA records; reject if any address is not
  ipaddress.is_global (covers loopback, RFC1918, link-local 169.254.0.0/16 incl. Oracle IMDS
  169.254.169.254, CGNAT, multicast, ULA, IPv4-mapped IPv6); reject numeric/octal/hex IP
  tricks; length limit 2048.
- Apply it to: the user URL, every URL extracted by yt-dlp before we fetch/pass it to ffmpeg,
  and every redirect hop (use requests with allow_redirects=False and a manual hop loop,
  max 5 hops, re-validating each).
- yt-dlp opts: enable_file_urls False, no generic extractor unless ALLOW_GENERIC, socket
  timeout, no playlist (noplaylist True, playlist_items 1), no_color, quiet, cachedir False.
- ffmpeg: build args as a list (shell=False), use "-protocol_whitelist http,https,tcp,tls,crypto",
  place "--" or "-i" safely so a URL starting with "-" can never be a flag, pass explicit
  "-t" duration cap, "-max_muxing_queue_size", and kill the process on client disconnect.
- Defense in depth on the host (document + script in deploy/): block container egress to
  169.254.169.254 via the DOCKER-USER iptables chain (container traffic is FORWARD, not
  OUTPUT), persisted across reboot; enforce IMDSv2 on the instance.

3.3 Adult/blocked-site enforcement
- Write scripts/gen_blocklist.py that enumerates yt_dlp.extractor.gen_extractors(), and
  produces data/adult_extractors.txt using keyword/domain heuristics on IE_NAME and
  _VALID_URL. It must output candidates for MY MANUAL REVIEW (known false positive example:
  "AdultSwim" is a Cartoon Network site and must NOT be blocked). Commit the reviewed file.
- Enforcement function: given a URL, find the matching extractor (ignore Generic) and reject
  if IE_NAME (or base name before ":") is in the blocklist and ALLOW_ADULT is false.
  Also support data/blocked_domains.txt and BLOCKED_URLS_FILE for DMCA takedowns
  (exact URL / domain / extractor), hot-reloaded on mtime change.
- Also maintain data/drm_extractors.txt for known DRM-only sites to exclude from the
  supported list.

3.4 Hardening
- debug=False always in prod; remove app.run(debug=True).
- Never return str(e) to clients. Log full exceptions server-side with a request id; return
  friendly mapped errors ("Could not process this link", "Video too long (max 20 min)",
  "Site temporarily blocking us, try again later", "This site is not supported").
- Sanitize Content-Disposition filename: strip CR/LF/control chars and path separators, emit
  both filename="..." (ASCII fallback) and filename*=UTF-8''... (RFC 5987). Tamil/Unicode titles
  must work.
- Security headers (Flask-Talisman or manual): HSTS, X-Content-Type-Options, X-Frame-Options
  or frame-ancestors, Referrer-Policy, Permissions-Policy, and a CSP. Start CSP in
  Report-Only mode, designed so ad/consent/Turnstile/Cloudflare-analytics origins can be added
  via config.
- Real client IP: only trust CF-Connecting-IP when the connection comes from Cloudflare ranges
  (or when behind Caddy configured with trusted_proxies); otherwise use the socket address.
  Use werkzeug ProxyFix correctly. Add tests.
- Routes: /healthz (cheap, no external calls), /robots.txt and /sitemap.xml generated using
  SITE_URL (fix the "[jungle_url]" placeholders), custom 404/500 pages matching the theme.

PHASE 0 ACCEPTANCE: tests prove that http://169.254.169.254/, http://127.0.0.1, http://[::1],
http://localhost, http://0x7f000001, http://2130706433, a hostname resolving to a private IP,
and a redirect to a private IP are all rejected; adult-extractor URLs are rejected when
ALLOW_ADULT=false; filenames with \r\n are neutralized; no secret files remain in the tree.

============================================================
4. PHASE 1 - RELIABILITY, ABUSE CONTROL, PERFORMANCE
============================================================
4.1 One extraction per video (currently yt-dlp runs 3x: download -> preview -> stream)
- /api/download extracts ONCE, stores the info dict in a TTL cache (cachetools.TTLCache with a
  lock; optional Redis backend), and returns metadata plus an opaque random token per request.
- /api/preview and /api/stream take token + format_id, NOT the raw URL. Cache miss/expiry ->
  HTTP 410 with a JSON code; the frontend transparently re-calls /api/download once and retries.
- Never put extracted CDN URLs into the browser unless intentionally using direct-link mode.
- Because the cache is in-process, run gunicorn with ONE process (gevent worker class, high
  worker_connections) unless CACHE_BACKEND=redis. Document this trade-off.

4.2 Concurrency/event loop
- Put gevent monkey.patch_all() as the very first statement of the entrypoint. Make sure ffmpeg
  pipe reads are cooperative (gevent.subprocess or non-blocking reads) so a few streams never
  starve the server. Use BoundedSemaphores for MAX_CONCURRENT_FFMPEG and
  MAX_CONCURRENT_EXTRACT; when full, wait briefly (queue up to ~10 s) then return 503 with
  "Server busy, try again in a minute" + Retry-After.
- Enforce MAX_DURATION_SEC, MAX_FILESIZE_MB, FFMPEG_TIMEOUT_SEC. Always clean up child
  processes and temp files in finally blocks (also on GeneratorExit/client disconnect). Use a
  tmpfs/temp dir with a startup + periodic janitor for orphan files.

4.3 Rate limiting and bot protection
- Flask-Limiter (memory storage by default, Redis if configured), keyed on the real client IP,
  with the env-configured limits on /api/download and /api/stream|preview. Return JSON 429 with
  a friendly message and Retry-After.
- Cloudflare Turnstile (invisible/managed) verified server-side on /api/download only; on
  success set a short-lived signed cookie (e.g. 15 min) so the user is not challenged on every
  link. If TURNSTILE_SECRET is unset, skip (dev mode). No accounts needed.

4.4 yt-dlp lifecycle
- Pin yt-dlp (and curl-cffi only if actually used; remove unused deps) in requirements.txt.
- scripts/update_ytdlp.sh + a systemd timer (daily): install the latest yt-dlp into a STAGING
  venv/layer, run scripts/smoke_test.py (extract metadata, no download, for a small list of
  stable public URLs from several platforms defined in tests/smoke_urls.json), swap only if
  the smoke test passes, otherwise roll back and send an alert (Telegram bot via env, free).
  Smoke test must also be runnable manually and report per-site pass/fail.

4.5 Frontend fixes
- Remove cdn.tailwindcss.com. Compile Tailwind with the standalone Tailwind CLI (arm64 and
  amd64 binaries) into a minified static/css/app.css, built in the Docker image. No Node needed
  at runtime.
- Replace the 5-second timer that re-enables the download button with a real "download
  started" signal: server sets a short-lived cookie (e.g. dl_<token>) when response headers are
  sent; JS polls for it (timeout 60 s) and then re-enables the button. Show states: preparing,
  downloading, failed (with the mapped error message), retry.
- Disable the button while a request is in flight (prevents double-click doubling load).
- Move the ~1800 inline site strings out of index.html (see 5.4).
- Reserve fixed-size containers for future ad slots to avoid layout shift (CLS), collapsed
  (display none) while ADS_ENABLED=false.
- Lazy-load images/iframes, preconnect only where needed, font-display swap, aim for
  Lighthouse mobile >= 90 on Performance/SEO/Best Practices/Accessibility. Add aria labels,
  focus states, sufficient contrast, keyboard support for the modal/FAQ.

============================================================
5. PHASE 2 - DELIVERY MODES, SITES LIST
============================================================
5.1 Download strategy ("hybrid", config driven)
- Default: proxy-stream through our VM with a proper Content-Disposition filename, for files up
  to PROXY_MAX_MB (typical TikTok/X/Instagram/Reddit clips are small, so bandwidth is fine on
  Oracle's 10 TB/month free egress).
- Direct CDN link mode only when: the format is a single progressive http(s) file (has both
  audio+video), needs no special headers/cookies, and is not IP-bound (NOT YouTube/googlevideo).
  Note for the UI: cross-origin direct links ignore the "download" attribute and may open in the
  browser tab, so prefer proxying small files and reserve direct links for large files; make this
  behavior env-configurable (DIRECT_LINK_MODE=off|large_only|always).
- Separate-stream formats (e.g. YouTube DASH 1080p+) and MP3 extraction go through ffmpeg
  under the concurrency/duration limits.

5.2 YouTube (important, no proxy budget)
- YT_MODE=best_effort: try with sensible yt-dlp extractor_args (player_client variations), and
  if blocked return a friendly message ("YouTube is temporarily unavailable, other sites work")
  rather than a stack trace. Never let YouTube failures degrade other sites (separate semaphore
  and a circuit breaker: after N consecutive bot-check failures, pause YouTube attempts for a
  few minutes).
- YouTube media is IP-bound to whichever IP did the extraction. If a proxy is configured, the
  matching download MUST use the same proxy (pass it to ffmpeg/requests for that job).
  Implement per-extractor proxy routing via PROXY_RULES so only YouTube (or other blocked
  sites) uses a paid proxy and everything else goes direct. Track proxied bytes and enforce
  PROXIED_DAILY_BYTE_BUDGET_GB, degrading gracefully when exceeded.

5.3 Egress/IP reputation hygiene
- Reduce upstream calls via the cache (4.1), per-site spacing/backoff on HTTP 429, honest
  retry limits (no tight retry loops), realistic default headers, and a per-extractor request
  counter exposed in logs/metrics so I can see which site triggers blocks.

5.4 Supported sites list
- /api/sites returns a JSON list built at startup from yt-dlp extractors: drop Generic,
  drop adult (unless ALLOW_ADULT), drop DRM list, drop broken ones, take the base name before
  ":" and dedupe, prettify display names, sort alphabetically, add a "popular" array for the
  homepage chips. Cache with ETag; cache-control for 1 day.
- Compute the count dynamically and display it rounded DOWN to the nearest hundred as
  "N+ sites" (expected ~1000+). Never claim more than the deduped, filtered count.
- Homepage: ~15 popular chips + "+ More Sites" modal (existing UI, with search) that fetches
  /api/sites lazily on first open. /supported-sites is a standalone, crawlable page rendering
  the same list server-side.

============================================================
6. PHASE 3 - ORACLE DEPLOYMENT (deploy/ directory + DEPLOY.md)
============================================================
6.1 Dockerfile: multi-stage, python:3.11-slim (or 3.12), ffmpeg from apt, non-root user, read-only
    root FS where possible with a tmpfs for /tmp, HEALTHCHECK on /healthz, no secrets in image,
    builds for linux/arm64 and linux/amd64 (docker buildx instructions).
6.2 docker-compose.yml: services app (+ optional redis profile) and caddy; restart
    unless-stopped; mem/cpu limits; log rotation (json-file max-size/max-file); env_file; cookies
    file mounted read-only from outside the repo path if used.
6.3 Caddyfile: automatic TLS or Cloudflare Origin Certificate, trusted_proxies set to Cloudflare
    ranges, gzip/zstd for text but not for media, flush_interval -1 and long timeouts for
    streaming routes, static files cache headers, security headers, request body limits,
    HTTP->HTTPS, basic per-IP rate limiting as a second layer.
6.4 Cloudflare plan: DNS proxied (orange) for the main site, SSL Full (strict), Bot Fight Mode,
    WAF managed rules on free tier, cache static assets only (bypass /api/*), Turnstile keys.
    IMPORTANT: Cloudflare's self-serve terms restrict serving large volumes of non-HTML
    (video/media) content through the proxied CDN on free plans (verify current terms). Provide
    the option MEDIA_HOST: serve /api/stream and /api/preview from a grey-cloud (DNS-only)
    subdomain pointing straight at the VM, with its own Let's Encrypt cert via Caddy, strict CORS,
    token-required, and tight rate limits, while the HTML site stays proxied. Document the
    trade-off (origin IP exposure on that subdomain) and make single-domain mode the simple default.
6.5 Host hardening script/docs: UFW (22 restricted, 80, 443), Oracle VCN security list matching,
    SSH key-only + fail2ban, unattended-upgrades, 2-4 GB swap, time sync, the IMDS iptables
    rule from 3.2, systemd unit to bring compose up on boot, Docker log limits.
6.6 Oracle-specific: note Always-Free idle reclaim (upgrade to Pay-As-You-Go while staying in
    free limits, set a $1 budget alert), A1 capacity fallback to AMD, choose Mumbai/Hyderabad
    region for latency to Indian users.
6.7 Observability: JSON logs with request id, per-extractor success/failure counters, a
    /metrics-style plain endpoint restricted to localhost, UptimeRobot-compatible /healthz,
    Telegram alert hook for smoke-test failures, disk/RAM alerts via a simple cron script.
6.8 CI: GitHub Actions workflow for lint (ruff), tests, docker build (multi-arch), and a
    scheduled workflow running the smoke test.
6.9 Backups: no DB, so just document how to rebuild from git + .env + cookies file.

============================================================
7. PHASE 4 - LEGAL, TRUST, SEO (organic growth)
============================================================
7.1 Rewrite terms.html and privacy.html so they are ACCURATE: we process links transiently;
    we do not store media; we may process IP addresses for rate limiting/security for a short
    retention window (state it); Cloudflare/Turnstile are used; ad networks and consent cookies
    when ads are enabled; how to opt out; contact email. Remove claims that become false
    ("never logs", "no tracking", "open source" unless the repo is actually public). Update the
    FAQ answer about ads/pop-ups to be truthful given ADS_ENABLED. Keep the current friendly tone.
7.2 Add /dmca (takedown procedure + email + required notice elements + counter-notice note, with
    a note that I may register a DMCA agent with the US Copyright Office) and /contact (email
    only, no form, address obfuscated). Wire the takedown workflow to data/blocked_domains.txt
    / BLOCKED_URLS_FILE so I can block a URL/domain/extractor without redeploying.
7.3 Consent banner (lightweight, self-built or open source like Klaro): shown only where required
    (use CF-IPCountry header; configurable), with Accept/Reject, remembers choice client-side,
    and ad scripts load ONLY after consent where consent is required.
7.4 Per-platform landing pages from a data file (data/platforms.json): /instagram-reels-downloader,
    /tiktok-video-downloader, /twitter-video-downloader (X), /facebook-video-downloader,
    /reddit-video-downloader, /pinterest-video-downloader, /youtube-video-downloader (honest
    best-effort copy), /mp3-downloader. Each page must have UNIQUE useful copy (how-to steps,
    FAQ, supported link formats, format/quality notes), not just a keyword swap. Include title,
    meta description, canonical, OpenGraph/Twitter tags, JSON-LD (WebApplication + FAQPage),
    internal links, breadcrumbs. Include "STATUS SAVE" (WhatsApp status) as its own page; it's a
    strong India-specific hook. Generate sitemap.xml from this list.
7.5 Prepare i18n structure so Tamil and Hindi versions of the key pages can be added later
    (hreflang, /ta/, /hi/ paths), but only ship English now.
7.6 Add a README section "Growth checklist": submit to Google Search Console and Bing Webmaster,
    structured-data testing, Core Web Vitals monitoring, and a content plan.
7.7 Add scripts/bulk_probe.py: iterate yt-dlp extractors in popularity order, take one URL from each extractor's _TESTS (skip only_matching/age-gated/adult-blocked), extract metadata only (no download), 4s delay between requests, max N from CLI arg, write data/site_status.json {site, status, error_class, checked_at}. Resumable. Never run it automatically.

============================================================
8. ADS-READY (disabled at launch)
============================================================
- Template partial templates/_ads.html with named slots: header_banner (728x90 desktop /
  320x50 mobile), below_result (300x250 or responsive), sidebar (desktop only, 300x250 /
  160x600). Each slot has a fixed min-height wrapper to prevent layout shift, a small "Advertisement" label,
  and lives at least ~100px away from any Download button. No ads inside the download button
  row, no elements that imitate download buttons.
- Everything driven by env: ADS_ENABLED, ADS_SCRIPT_URLS, slot snippets via config files
  (ads/slots.json), consent-gated. When ADS_ENABLED=false no ad markup or space renders.
- Frequency-capped interstitial countdown is OPTIONAL, off by default, same-page only
  (no new tabs, no hidden tabs, no auto-closing windows).
- Do NOT implement pop-unders, hidden tabs, auto-closing tabs, or anything that generates
  clicks/impressions the user did not knowingly make (invalid traffic -> account bans).
  Document in ADS.md how to add Adsterra/Monetag/direct affiliate banners safely and the review
  checklist for ad-network approval (all pages live, privacy/terms/contact present, no adult
  names visible, no broken pages, sufficient content).

============================================================
9. TESTING & DELIVERABLES
============================================================
- tests/: unit (SSRF, blocklist, filename sanitizer, limiter keying, cache/token flow, sites
  list builder), integration with mocked yt-dlp, and the smoke test. Provide `make test`,
  `make lint`, `make run`, `make build`.
- Simple load test script (locust or hey) proving: N concurrent extractions are queued, ffmpeg
  concurrency cap holds, memory stays bounded, orphan ffmpeg processes are not left behind.
- Docs: README.md (overview + local run), DEPLOY.md (Oracle step by step), RUNBOOK.md
  (yt-dlp broke, site blocked us, disk full, DMCA request, high bandwidth), ADS.md,
  .env.example, PLAN.md (assumptions + decisions + open risks).
- Final report: a checklist of every audit finding (cookies leak, SSRF, no rate limit, 3x
  extraction, unpinned yt-dlp, gevent blocking, broken robots/sitemap, Tailwind CDN, 5 s button
  timer, inaccurate privacy text, adult-site exposure, missing DMCA/contact/consent) mapped to
  the commit that fixed it, plus a "launch go/no-go" checklist and remaining risks
  (YouTube blocking without a proxy, ad-network approval, Oracle capacity).