# Jungle Media Downloader 🌴

A high-performance, production-ready, stateless web application for downloading online video and audio streams from **1000+ supported sites** (YouTube, Instagram, TikTok, Twitter/X, Facebook, Reddit, Vimeo, SoundCloud, Twitch, and more).

Powered by **Flask**, **gevent**, **yt-dlp**, **ffmpeg**, and **Caddy**.

---

## 🚀 Key Highlights & Architecture

- **Stateless & Ephemeral**: No database, no user accounts, no login. Download sessions are ephemeral and media is processed transiently in memory.
- **Single-Extraction Token Flow**: Single yt-dlp metadata extraction creates an opaque, time-limited token (`tok_...`). Stream and preview endpoints never accept raw target URLs.
- **SSRF Hardened**: Multi-layer DNS resolution check, private IP/RFC1918 blocking, redirect hop validation, and link-local/IMDS containment (`169.254.169.254`).
- **Hybrid Delivery Engine**:
  - Direct CDN 302 redirects for large whitelisted media assets to save server bandwidth.
  - Transparent progressive streaming for standard media.
  - Universal real-time ffmpeg transcoding for audio conversion and fragmented streams (HLS/DASH).
- **Origin & Edge Hardening**: Cloudflare Turnstile bot verification, Cloudflare-only UFW origin firewalling, and strict security headers (`X-Content-Type-Options: nosniff`, CSP).
- **Subnet-Aware Rate Limiting & CGNAT Trade-offs**:
  - IPv6 client addresses are normalized to their `/64` subnet string (`2001:db8:1:2::/64`), preventing abuse by users rotating ephemeral SLAAC/privacy addresses.
  - IPv4-mapped IPv6 addresses (`::ffff:192.0.2.1`) are normalized to standard IPv4.
  - CGNAT trade-off: Carrier-Grade NAT (CGNAT) pools share a single public IPv4 address across many mobile or residential users, causing them to share rate limit buckets. All rate limits are env-tunable (`RATE_LIMIT_EXTRACT`, `RATE_LIMIT_STREAM`, `RATE_LIMIT_PREVIEW`) to balance bot protection with shared IP access.
- **YouTube Extraction Realities & Datacenter IP Blocking**:
  - YouTube aggressively throttles or blocks requests originating from cloud and datacenter IP blocks (AWS, OCI, Hetzner, etc.), demanding bot-check challenges or returning HTTP 429.
  - The runtime container includes Node.js as an external JavaScript runtime alongside `yt-dlp[default]` to solve challenges.
  - In public cloud deployments without residential or rotating proxies (`YTDLP_PROXY` or `PROXY_RULES`), direct YouTube downloads may fail. Operators should run the smoke test (`python scripts/smoke_test.py`); if smoke testing fails due to datacenter IP blocks, set `YT_MODE=disabled` in `.env` until a residential proxy is configured.

---

## 🛠️ Local Development

### Prerequisites
- Python 3.11+
- ffmpeg & ffprobe installed locally
- Git

### Virtualenv Setup
```bash
# 1. Clone repository
git clone https://github.com/selva-mern12/jungle_downloader.git
cd jungle_downloader

# 2. Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# 3. Install dependencies
pip install --upgrade pip
pip install -r requirements.txt -r requirements-dev.txt

# 4. Configure local environment
cp .env.example .env

# 5. Start development server
python3 app.py
```
Visit `http://localhost:5000` in your browser.

### Docker Compose Run
```bash
# Run complete stack (Flask app + Caddy proxy)
docker compose up -d --build

# Inspect logs
docker compose logs -f
```

---

## ⚙️ Architecture Trade-off: Single Gevent Worker

Jungle Media Downloader defaults to a **single Gunicorn gevent worker** (`workers: 1`, `worker-connections: 1000`).

### Why Single-Worker?
1. **Zero Database Dependency**: In-memory rate limiting (`Flask-Limiter`) and token caching (`TTLCache`) work reliably without needing an external Redis instance.
2. **Cooperative Concurrency**: With gevent monkey-patching and asynchronous I/O, a single worker handles thousands of concurrent open streaming sockets with negligible RAM consumption (~80MB base).
3. **Strict Concurrency Control**: Process-level semaphores (`MAX_CONCURRENT_FFMPEG`, `MAX_CONCURRENT_EXTRACT`) accurately prevent CPU and bandwidth starvation.

### Scaling Horizontally
If deploying across multiple workers or nodes:
1. Set `CACHE_BACKEND=redis` and configure `REDIS_URL`.
2. Configure a centralized Redis rate limiter for `Flask-Limiter`.

---

## ⚠️ Important Policy: ALLOW_ADULT Warning

The application includes an adult extractor filter (`ALLOW_ADULT=false` by default).

> [!CAUTION]
> **Ad Network Compliance**:
> NEVER enable `ALLOW_ADULT=true` on a public domain monetized with third-party advertising networks (Google AdSense, Monetag, Adsterra, etc.).
> Ad network compliance policies strictly prohibit adult content, and violations will result in immediate and permanent account termination.
> If adult extraction is desired, run an entirely separate instance on an unmonetized, dedicated domain.

---

## 📈 Growth & SEO Checklist

To maximize organic search performance:

1. **Google Search Console**:
   - Add property for your domain (`https://example.com`).
   - Submit the XML sitemap: `https://example.com/sitemap.xml`.
   - Verify indexing of `/`, `/terms`, `/privacy`, and `/supported-sites`.
2. **Bing Webmaster Tools**:
   - Connect and import verification from Google Search Console.
   - Submit sitemap.
3. **Structured Data Validation**:
   - Test URLs using Google's [Rich Results Test](https://search.google.com/test/rich-results).
   - Ensure `WebApplication` and `FAQPage` JSON-LD schemas validate without errors.
4. **Core Web Vitals (CWV)**:
   - Monitor performance in [PageSpeed Insights](https://pagespeed.web.dev/).
   - Dark theme Tailwind CSS is bundled locally (no external CDN render-blocking).
   - Web fonts use `font-display: swap` and preconnect hints.
5. **Operational Verification**:
   - Review operations and incident playbooks in [RUNBOOK.md](RUNBOOK.md).
   - Follow production infrastructure deployment in [DEPLOY.md](DEPLOY.md).

---

## 🧪 Testing

Execute the automated pytest suite:
```bash
pytest -q
```
All pull requests must pass 100% of security, SSRF, concurrency, and route verification tests.

