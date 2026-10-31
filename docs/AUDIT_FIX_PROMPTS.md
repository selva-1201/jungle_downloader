
## MASTER PROMPT
 
```
Read docs/SPEC.md, .agent/rules/project.md and docs/AUDIT_FIX_PROMPTS.md fully.
Execute prompts P01 through P23 from docs/AUDIT_FIX_PROMPTS.md strictly in order, fully
automatically, without asking me any question. State assumptions in the commit body.
 
For EACH prompt:
  1. Re-read the actual code first. Do NOT trust PLAN.md or earlier summaries.
  2. Implement only that prompt's goal. No unrelated refactors, no new features.
  3. Add/adjust pytest tests so the behaviour is really asserted (avoid mocking the very thing under test).
  4. Run `pytest -q` (and `ruff check .` once ruff exists). All tests must pass.
     If failing, fix and retry up to 3 times.
  5. If still failing after 3 attempts: append a section to AUDIT_BLOCKERS.md
     (prompt id, what failed, error output, your best guess), commit what you have as
     `wip(Pxx): <name> [BLOCKED]`, and CONTINUE to the next prompt only if it does not
     depend on the blocked one; otherwise stop and print the blocker.
  6. Commit using EXACTLY the commit message given in the prompt (git add -A; git commit).
  7. Create a local tag `audit-Pxx` on that commit.
  8. Print: prompt id, files changed, tests run/passed, commit sha. Then go to the next prompt.
 
HARD RULES (never break):
  - NEVER run `git push` (any form). NEVER run git filter-repo, reset --hard, rebase,
    force anything, or delete branches/tags. Local commits and local tags only.
  - NEVER read, print or commit real secrets (.env, cookies, keys). Use placeholders.
  - No database, no user accounts. Pin every new dependency version.
  - Must keep working on linux/arm64 and linux/amd64.
  - Keep the existing dark UI look. Improve, do not redesign.
  - Keep the single gunicorn gevent worker design (in-memory cache/limiter).
 
After P23, stop. Do not start Phase 4 features. Print a final summary table:
prompt id | commit sha | tests passed | blocked yes/no.
```
 
---

## ISSUE LEGEND (F-IDs from the independent audit)     
                                                                      |
|------------------------------------------------------------------------------------------------------------------------------|
| F1  Critical  /api/stream and /api/preview accept raw url= and bypass blocklist, Turnstile, breaker, semaphore, duration cap |
| F2  High      Preview re-extracts per Range request, preview rate limit too low, location.assign navigates to JSON errors    |
| F3  Critical  Turnstile enforced server-side but the frontend has no widget, so the site breaks when keys are set            |
| F4  Critical  Outbound fetches not validated: requests follows redirects, ffmpeg -i inputs, DNS TOCTOU                       |
| F5  High      ffmpeg_semaphore acquired for progressive downloads too and leaks when the generator never runs                |
| F6  High      data/ baked into the image, so DMCA blocklist hot reload does not work in Docker                               |
| F7  High      MAX_FILESIZE_MB and FFMPEG_TIMEOUT_SEC unused, live streams not rejected, proxy path uncapped                  |
| F8  High      record_proxied_bytes never called, proxy not used for ffmpeg/preview                                           |
| F9  High      update_ytdlp_docker.sh runs pytest that is not in the image, so it always rolls back                           |
| F10 High      False claims in terms/privacy/FAQ (zero-log, no limits, 100% safe, open source, 1800+)                         |
| F11 High      Tailwind CDN still used in all templates, 5-second timer for download button                                   |
| F12 Med       ENV=production in docs vs code checks "prod", so HSTS is never sent                                            |
| F13 Med       urlsplit().port raises a bare ValueError, giving a 500                                                         |
| F14 Med       No request id, no server-side exception logging, LOG_LEVEL unused                                              |
| F15 Med       Substring "bot" matches "both"/"robot", wrong error mapping and breaker trips                                  |
| F16 Med       stream_via_direct_url: no raise_for_status, gzip vs Content-Length mismatch                                    |
| F17 Med       request.url_root gives http:// behind Caddy, so canonical/OG/JSON-LD are wrong                                 |
| F18 Med       Origin open to the whole internet, so Cloudflare protections can be bypassed                                   |
| F19 Med       Procfile uses a sync gunicorn worker, contradicting the gevent single-worker design                            |
| F20 Med       requirements missing redis/pytest/pytest-mock/ruff, curl-cffi unused                                           |
| F21 Med       TTLCache stores full yt-dlp info dicts, memory risk                                                            |
| F22 Med       RUNBOOK/DEPLOY errors (wrong curl, wrong messages, prune --volumes deletes certs)                              |
| F23 Med       ffmpeg -headers only applied to the first input                                                                |
| F24 Med       Blocklist runs ~1800 suitable() regexes per request, duplicate manager instances                               |
| F25 Med       host-setup.sh: no SSH key-only, no IMDSv2 docs, docker reload does not apply log-opts                          |
| F26 Low       index.html typos: stray CJK in class, unclosed footer class, clearUrlInput .add bug                            |
| F27 Low       Dockerfile Tailwind download has no -f flag and no checksum                                                    |
| F28 Low       Weak tests: shared rate-limit bucket, semaphore leak, over-mocking, missing security tests                     |
| F29 Low       Misc: manifest icon paths, dead root robots/sitemap, _ads.html unwired, hardcoded CSP                          |
| F30 Low       Sitemap has only 4 URLs (Phase 4 pages come later)                                                             |

 
## ISSUE INDEX (what each prompt fixes)
 
| Prompt | Name | Fixes |
|---|---|---|
| P01 | token-only-stream-preview | F1, F2(part), test infra |
| P02 | ssrf-all-outbound | F4, F13, F23 |
| P03 | resource-leaks-semaphore | F5, E1, E2 |
| P04 | size-time-live-caps | F7 |
| P05 | proxy-consistency-budget | F8, E3 |
| P06 | turnstile-frontend | F3, E4 |
| P07 | deploy-config-consistency | F6, F9, F12, F19, F20 |
| P08 | frontend-bugfixes | F26, E5 |
| P09 | download-flow-cookie-polling | F2(rest), F11(part) |
| P10 | tailwind-cdn-removal-a11y-seo | F11, F17, F29(part), E6 |
| P11 | legal-pages-truthful | F10 |
| P12 | structured-logging-request-id | F14 |
| P13 | error-mapping-and-stream-hardening | F15, F16, E7 |
| P14 | cache-memory-and-blocklist-perf | F21, F24 |
| P15 | origin-lock-and-host-hardening | F18, F25 |
| P16 | container-egress-lockdown | F4(part), E8 |
| P17 | dockerfile-compose-hardening | F27, E9 |
| P18 | docs-and-caddy-fixes | F22, F29(part) |
| P19 | sites-catalog-working-filter | E10, F10(part) |
| P20 | sitemap-from-routes | F30 |
| P21 | ci-lint-makefile | SPEC 6.8, 9 |
| P22 | test-suite-hardening | F28 |
| P23 | final-verification-report | all |
 
Extra issues (not in the original 30):
E1 ffmpeg stderr PIPE can deadlock. E2 preview `requests` response never closed. E3 `YT_MODE=proxy` unsupported.
E4 `SECRET_KEY` default fallback and non-Secure clearance cookie. E5 `data.duration` never sent by API.
E6 keywords mention DRM sites (hotstar, sonyliv). E7 unknown `format_id` silently falls back to the last format.
E8 Docker containers can reach VCN/RFC1918 ranges. E9 compose 2 GB limit exceeds the 1 GB AMD fallback.
E10 `sites_catalog` does not filter broken extractors.
 
---
 
## P01: token-only-stream-preview
**Fixes:** F1, F2 (backend part), test infra
 
**Problem:** `/api/stream` and `/api/preview` have an `elif page_url:` branch that accepts a raw `url=`.
It bypasses blocklist, Turnstile, YouTube breaker, extract semaphore, duration cap, and re-extracts. The frontend uses it.
 
**Do:**
1. In `app.py`, delete the `url=` fallback branches in `stream_download` and `preview_video`.
   Both require a valid `token`; otherwise return 410 JSON `{"success":false,"code":"SESSION_EXPIRED",...}`.
2. In `index.html`: store `data.token` from `/api/download`. Build preview URLs as
   `/api/preview?token=...&format_id=...` and stream URLs as `/api/stream?token=...&format_id=...&filename=...&type=...`.
   Never put `original_url` in those URLs. Add helper `ensureFreshToken()` that, on a 410 from any call,
   re-calls `/api/download` once with the original input URL, updates the token, retries once.
3. Preview limiter: give `/api/preview` its own more generous limit (new env `RATE_LIMIT_PREVIEW`,
   default `120/minute;1000/day`) because `<video>` issues many Range requests. Add to `config.py` and `.env.example`.
4. Add `tests/conftest.py` with an autouse fixture that resets the Flask-Limiter storage and releases/refreshes
   semaphores between tests, so rate-limit state never leaks across tests.
5. Add `duration` to the `/api/download` JSON response (frontend already reads it).
**Tests:** `/api/stream?url=https://www.pornhub.com/x&format_id=best` returns 410 and never calls yt-dlp;
same for preview; token flow still works; preview allows >10 requests/min with a valid token.
 
**Commit:** `fix(P01): token-only stream/preview, remove url= bypass [F1,F2]`
 
---
 
## P02: ssrf-all-outbound
**Fixes:** F4, F13, F23
 
**Problem:** Only the user URL is validated. `requests.get` in `stream_via_direct_url` and `preview_video`
follows redirects unvalidated; ffmpeg `-i` URLs are unvalidated; DNS is resolved again after validation (TOCTOU);
`urlsplit(...).port` can raise a bare `ValueError`; `-headers` only applies to the first ffmpeg input.
 
**Do:**
1. `security.py`: wrap `parsed.port` access in try/except and raise `SSRFValidationError`.
2. Replace both `requests.get` calls in `app.py` with `safe_requests_get` (manual redirect hops, each hop validated,
   max 5). Extend `safe_requests_get` with `proxies` and `stream` support, and validate the final URL too.
3. Add `validate_extracted_urls(info)`: validate every `format['url']` that will be fetched, and also
   `fragments`/`manifest_url` where present. Drop (not crash on) formats that fail validation, in `/api/download`
   before caching.
4. Call `validate_public_url` on every URL passed to ffmpeg (video and audio) immediately before `Popen`.
5. Reduce TOCTOU: add a `resolve_and_pin(url)` helper returning the validated IP; for `requests` use a custom
   `HTTPAdapter`/connection that connects to the pinned IP while keeping Host/SNI. Document that ffmpeg/yt-dlp
   cannot be pinned and are covered by egress rules (P16).
6. `build_safe_ffmpeg_args`: apply `-headers` before EACH `-i` (video and audio) using each format's own headers.
**Tests:** redirect to `http://169.254.169.254/` inside stream and preview paths is rejected; `http://example.com:99999`
and `http://[::1` raise `SSRFValidationError`; an extracted format with a private-IP URL is dropped; ffmpeg args contain
`-headers` before both inputs; pinned-IP adapter connects to the validated IP (mock socket).
 
**Commit:** `fix(P02): validate every outbound fetch, redirect hops, ffmpeg inputs, IP pinning [F4,F13,F23]`
 
---
 
## P03: resource-leaks-semaphore
**Fixes:** F5, E1, E2
 
**Problem:** `ffmpeg_semaphore` is acquired even for plain progressive proxy downloads, and is only released inside
a generator `finally`, so it leaks if the generator is never iterated or if `requests.get`/`Popen` raises.
ffmpeg `stderr=PIPE` is only read at EOF and can deadlock. Preview `requests` response is never closed.
 
**Do:**
1. Acquire `ffmpeg_semaphore` only when `needs_ffmpeg` is true. Plain progressive proxying gets a separate, larger
   semaphore `MAX_CONCURRENT_PROXY` (new env, default 20).
2. Write a `ReleasingIterator` wrapper class with `__iter__`, `__next__`, and `close()`; release the permit exactly once
   (guard with a flag) from `close()`, exhaustion, or `__del__`. Use `Response(..., direct_passthrough=False)` and
   `response.call_on_close(release)` as a second safety net.
3. Wrap acquisition-to-return in try/except so any exception before returning the Response releases the permit.
4. ffmpeg: use `stderr=subprocess.DEVNULL` unless `LOG_LEVEL=DEBUG`; if stderr is needed, drain it in a gevent greenlet.
   Use `gevent.subprocess` (or `gevent.os` non-blocking reads) so reads never block the loop.
   Always `proc.kill(); proc.wait()` in `finally`.
5. Preview progressive path: close `r` in a `finally` inside the generator and via `call_on_close`.
**Tests:** create a response and never iterate it, then close it: permit count returns to initial. Exception from
`requests.get` releases the permit. 5 concurrent progressive downloads succeed while `MAX_CONCURRENT_FFMPEG=3`.
Killing the client mid-stream terminates the ffmpeg subprocess (assert via mock `Popen.kill`).
 
**Commit:** `fix(P03): semaphore leaks, ffmpeg stderr deadlock, preview connection close [F5,E1,E2]`
 
---
 
## P04: size-time-live-caps
**Fixes:** F7
 
**Problem:** `MAX_FILESIZE_MB` and `FFMPEG_TIMEOUT_SEC` are defined but unused; live streams are not rejected;
proxied downloads have no byte cap.
 
**Do:**
1. `/api/download`: reject `info.get('is_live')` or `live_status in ('is_live','is_upcoming')` with a friendly 400.
   Treat a missing duration for non-live media as allowed but flagged; cap by size instead.
2. Drop formats whose known `filesize` exceeds `MAX_FILESIZE_MB` from the option lists.
3. Proxy stream: stop with an error when `Content-Length` > `MAX_FILESIZE_MB`, and keep a running byte counter
   that aborts the generator when the cap is exceeded (covers missing/lying Content-Length).
4. ffmpeg: add `-rw_timeout` (microseconds) and a gevent watchdog that kills the process after `FFMPEG_TIMEOUT_SEC`.
5. Add a total wall-clock timeout for the progressive proxy path too.
6. Add `filename` length cap (max 150 chars before extension) in `sanitize_filename`; strip bidi override chars
   (U+202A-202E, U+2066-2069).
**Tests:** live info rejected; oversized `filesize` dropped; generator aborts at the byte cap; watchdog kills a
hanging fake process; long and bidi filenames sanitized.
 
**Commit:** `fix(P04): enforce filesize/time/live caps, filename length and bidi strip [F7]`
 
---
 
## P05: proxy-consistency-budget
**Fixes:** F8, E3
 
**Problem:** `record_proxied_bytes` is never called so the daily budget never accrues; the extraction proxy is not used
for ffmpeg or preview (YouTube media is IP-bound, so downloads fail with a proxy); `YT_MODE=proxy` is not implemented.
 
**Do:**
1. Store the proxy used for extraction inside the cached payload (`cache_store.store_metadata(..., proxy=...)`), and use
   that same proxy for the stream and preview of that token.
2. Pass the proxy to ffmpeg via `-http_proxy` (HTTP proxies) and to `requests` via `proxies=`. For SOCKS proxies add
   `requests[socks]` pinned in requirements, or document HTTP-only and reject SOCKS for ffmpeg with a clear log.
3. Count bytes in every generator that streams through a proxy and call `proxy_router.record_proxied_bytes`.
   When the budget is exceeded: new extractions for proxy-required extractors return the friendly
   "temporarily unavailable" error, and existing streams finish.
4. Implement `YT_MODE`: `disabled` (reject), `best_effort` (try direct, use proxy if configured), `proxy`
   (require a proxy rule for YouTube, otherwise friendly error). Document in `.env.example`.
5. Expose proxied bytes and per-extractor counters in `/metrics` (already) and in logs (see P12).
**Tests:** bytes counted after streaming; budget exceeded degrades gracefully; the same proxy is used for extraction and
stream (mock); `YT_MODE=proxy` without a proxy rule returns the friendly error.
 
**Commit:** `fix(P05): consistent proxy for extract+stream, budget accrual, YT_MODE=proxy [F8,E3]`
 
---
 
## P06: turnstile-frontend
**Fixes:** F3, E4
 
**Problem:** With `TURNSTILE_SECRET` set, the backend returns 403 `BOT_CHALLENGE_REQUIRED` but the page has no widget,
so the site would be dead. `SECRET_KEY` falls back to a hardcoded default; the clearance cookie is not `Secure`.
 
**Do:**
1. Pass `turnstile_site_key=config.TURNSTILE_SITE_KEY` to `index.html` via `render_template`.
2. When the key is set: load `https://challenges.cloudflare.com/turnstile/v0/api.js` (defer), render an invisible/managed
   widget, and send its token as `turnstile_token` in the `/api/download` body. On `BOT_CHALLENGE_REQUIRED`, reset the widget
   and retry once automatically. When the key is empty, do nothing (no script, no widget).
3. `turnstile.py`: require a separate `SECRET_KEY` env for HMAC when `ENV=prod` (fail fast with a clear error if missing);
   never reuse the Turnstile secret. Add to `config.py` and `.env.example`.
4. Set the clearance cookie with `secure=(config.ENV in ("prod","production"))`, `httponly=True`, `samesite="Lax"`.
5. Allow the Cloudflare Turnstile origin in the CSP report-only policy via a config list `CSP_EXTRA_SCRIPT_SRC`.
**Tests:** cookie flags; missing `SECRET_KEY` in prod raises; template renders widget only when the key is set;
the 403 path still works.
 
**Commit:** `fix(P06): turnstile widget flow, separate SECRET_KEY, secure clearance cookie [F3,E4]`
 
---
 
## P07: deploy-config-consistency
**Fixes:** F6, F9, F12, F19, F20
 
**Do:**
1. **F6:** docker-compose `app` service: mount `./data:/app/data:ro` so DMCA/blocklist edits on the host are visible and
   hot-reload works. Keep `read_only: true`.
2. **F12:** add `config.IS_PROD = ENV in ("prod","production")` and use it everywhere (HSTS, cookies). Update tests.
3. **F19:** delete `Procfile` (sync worker contradicts the single gevent worker design).
4. **F20:** `requirements.txt`: add pinned `redis`; remove `curl-cffi` if grep shows it is unused. Create
   `requirements-dev.txt` with pinned `pytest`, `pytest-mock`, `ruff`. Use versions that actually exist on PyPI
   (verify with `pip index versions`; PLAN.md's `pytest==9.2.0` may be wrong).
5. **F9:** `update_ytdlp_docker.sh`: the runtime image has no tests or pytest. Add a `test` target in the Dockerfile
   (stage that installs dev requirements and copies `tests/`); run pytest from that stage; run the smoke test in the
   runtime image. Source `.env` (if present) for `TELEGRAM_*` safely (no echo of values).
6. Make `.env.example` complete: `YT_CIRCUIT_BREAKER_FAILURES`, `YT_CIRCUIT_BREAKER_COOLDOWN_SEC`, `SECRET_KEY`,
   `MAX_CONCURRENT_PROXY`, `RATE_LIMIT_PREVIEW`, `CSP_EXTRA_SCRIPT_SRC`, `TELEGRAM_*`, and fix
   `REDIS_URL=redis://redis:6379/0` for Docker use.
**Tests:** `IS_PROD` for both spellings; HSTS present for `production`. Verify `docker compose config` if docker exists.
 
**Commit:** `fix(P07): data volume mount, ENV normalization, deps, updater test stage, drop Procfile [F6,F9,F12,F19,F20]`
 
---
 
## P08: frontend-bugfixes
**Fixes:** F26, E5
 
**Do (index.html and other templates):**
1. Remove the stray `确定` from the `trickModal` class list (`backdrop-blur-sm确定`).
2. Fix the footer `<p class="mb-2 text-gray-400>` missing closing quote and the broken `<a href=" https://...`.
3. `clearUrlInput`: use `document.getElementById('btnClearUrl').classList.add('hidden')` (currently `.add` on the element).
4. Footer links: `/terms` and `/privacy` (not `.html`) in all templates.
5. Ensure `durationLabel` logic works with the new `duration` field from P01.
6. Remove adult-site names and DRM sites (hotstar, sonyliv, etc.) from keywords/meta.
7. Fix `supported_sites.html` inline `onclick="filterBy('{{ site }}')"`: use a `data-site` attribute and an event listener
   so quotes in names cannot break out (XSS-safe).
8. In the sites modal, render site names with `textContent`, not `innerHTML` template strings.
**Tests:** parse rendered HTML with `html.parser` or BeautifulSoup (pin it in dev requirements) and assert: no `确定`,
all tags balanced for the footer, links point to `/terms` and `/privacy`.
 
**Commit:** `fix(P08): index.html typos, clear button, XSS-safe site lists, footer links [F26,E5,E6]`
 
---
 
## P09: download-flow-cookie-polling
**Fixes:** F2 (rest), F11 (5 s timer)
 
**Do:**
1. Replace `window.location.assign(streamUrl)` with a hidden `<iframe>` (or `<a download>` click) so server JSON errors do not
   navigate the page.
2. Poll `document.cookie` for `dl_<token>` every 500 ms (timeout 60 s). On detection: clear the spinner, re-enable buttons,
   expire the cookie client-side. On timeout or on an error response detected through a lightweight `HEAD`-free probe
   (use `fetch` with `Range: bytes=0-0` only when the cookie never appears): show the mapped error message in `#errorBox`,
   show a Retry button.
3. States: preparing, downloading, failed (mapped error), retry. Disable all buttons while a request is in flight.
4. On `SESSION_EXPIRED`, call `ensureFreshToken()` from P01 and retry once automatically.
5. Remove the 5 s `setTimeout`.
**Tests:** extract the JS logic into `static/js/download.js` (pure functions where possible) and unit-test with a small
Node-free approach, OR add Python tests asserting the template contains no `setTimeout(...5000` and references
`dl_`. Add a note in the commit body about manual browser verification steps.
 
**Commit:** `fix(P09): real download-started detection via cookie polling, no page navigation on error [F2,F11]`
 
---
 
## P10: tailwind-cdn-removal-a11y-seo
**Fixes:** F11, F17, F29 (part), E6
 
**Do:**
1. Remove `<script src="https://cdn.tailwindcss.com">` from ALL templates (index, terms, privacy, supported_sites, 404, 500).
   Use `<link rel="stylesheet" href="{{ url_for('static', filename='css/app.css') }}">`.
2. Make sure `tailwind.config.js` content globs include any new JS file, and that `app.css` is built in Docker only
   (it is gitignored or committed? decide: keep `static/css/app.css` committed as a dev fallback, the Docker build overwrites it).
3. Canonical/OG/Twitter/JSON-LD URLs use `config.SITE_URL`, not `request.url_root` (it returns http behind Caddy). Pass
   `site_url` to templates via a context processor.
4. Replace every hardcoded "1800+" / "1000+" with the dynamic `display_count` from `sites_catalog` (title, meta, OG, JSON-LD,
   FAQ, component text). Never claim more than the computed count.
5. Accessibility: `aria-label` on icon-only buttons (clear, close, download, WA trick), `role="dialog"` + `aria-modal`
   on modals, focus trap and Esc to close, visible `:focus-visible` outlines, FAQ keyboard support (details/summary is OK),
   `lang`, alt texts, sufficient contrast for text-gray-500 on dark background (use gray-400 min).
6. `font-display: swap` via the Google Fonts URL (`&display=swap` already) and preconnect only for fonts.
7. Lazy-load below-the-fold images/iframes. Fix `site.webmanifest` icon paths to `/static/...` (E-manifest 404) and add
   the matching `<link>`.
**Tests:** no template contains `cdn.tailwindcss.com`; canonical uses `SITE_URL`; display count appears and "1800" is absent;
manifest icon URLs start with `/static/`.
 
**Commit:** `fix(P10): remove Tailwind CDN, SITE_URL canonicals, dynamic site count, a11y basics [F11,F17,F29]`
 
---
 
## P11: legal-pages-truthful
**Fixes:** F10
 
**Problem:** terms/privacy/FAQ make claims that are false ("never logs", "zero-logs", "no tracking", "open source",
"no daily limits", "100% safe") while the app rate-limits, uses Turnstile, processes IPs, and plans ads.
 
**Do:**
1. Rewrite `terms.html` and `privacy.html` (keep the friendly tone and the existing look) to state accurately:
   links are processed transiently; no media is stored; IP addresses are processed for rate limiting and security with a
   short retention window (state 30 days or what logs actually keep); Cloudflare and Turnstile are used; ad networks and
   consent cookies when `ADS_ENABLED`; how to opt out; contact email from `config.CONTACT_EMAIL`; DMCA email.
   Remove "open source" unless the repo is public (make it conditional on a `PUBLIC_REPO_URL` env, default empty).
2. Make ad-related paragraphs conditional on `config.ADS_ENABLED` (Jinja).
3. FAQ in `index.html`: fix "no download limit" (state fair-use rate limits), "100% safe" (soften), pop-up ad answer
   (truthful for `ADS_ENABLED`), "Open source" wording, and the footer "Open Source" text.
4. Set "Last Updated" from a constant in `config.py`.
5. Add "Respect copyright" and an age/country compliance line if appropriate.
**Tests:** rendered privacy page mentions IP processing and Cloudflare; ad paragraph appears only when `ADS_ENABLED=true`;
the strings "never logs", "zero-logs", "100% safe" appear nowhere in templates.
 
**Commit:** `fix(P11): truthful terms, privacy and FAQ copy, conditional ads text [F10]`
 
---
 
## P12: structured-logging-request-id
**Fixes:** F14
 
**Do:**
1. New `logging_setup.py`: JSON formatter (stdlib only), honors `LOG_LEVEL`, outputs to stdout.
2. `before_request`: generate `g.request_id` (uuid4 hex[:12]); `after_request`: add `X-Request-ID` header and one access
   log line (method, path without query tokens, status, duration, client IP, extractor if known).
3. Never log the `token` query value or full URLs with credentials; mask tokens as `tok_***`.
4. In every `except Exception as e` in app.py: `logger.exception(...)` with request id and extractor; client still receives
   only the mapped message.
5. Add per-extractor counters to the periodic log line (every N minutes via a gevent greenlet, or on `/metrics` hit).
6. Add `scripts/disk_ram_alert.sh` (cron-friendly: df and free thresholds, Telegram alert via env) and mention in RUNBOOK.
**Tests:** log record is valid JSON with `request_id`; `str(e)` text never appears in a 400 response body; tokens masked.
 
**Commit:** `feat(P12): JSON logs, request ids, exception logging, resource alert script [F14]`
 
---
 
## P13: error-mapping-and-stream-hardening
**Fixes:** F15, F16, E7
 
**Do:**
1. `hardening.map_error_message` and `delivery.YouTubeCircuitBreaker.record_failure`: replace substring `"bot"` with a
   regex word-boundary match (`\bbot\b`, `not a bot`, `captcha`, `\b429\b`), so words like "both"/"robot" do not misclassify
   or trip the breaker.
2. `stream_via_direct_url`: send `Accept-Encoding: identity`; call `r.raise_for_status()` (map to friendly errors);
   do not forward a mismatching `Content-Length` when upstream is compressed; forward `Content-Length` only if identity.
3. `/api/stream` and `/api/preview`: if `format_id` is not found, return 404 instead of silently falling back to
   `formats[-1]`. Keep the special `best` and `auto_extract` values explicitly.
4. Validate `type` query param to `video|audio` only; default `video`.
5. Add `Content-Type` sniff protection: set from format `ext`/`mime` when available (webm, m4a) instead of always mp4/mp3
   for progressive pass-through; filename extension must match.
**Tests:** "both" does not trip the breaker; upstream 403/404 mapped to friendly errors; unknown `format_id` returns 404;
bad `type` rejected.
 
**Commit:** `fix(P13): precise bot detection, upstream status handling, strict format_id/type [F15,F16,E7]`
 
---
 
## P14: cache-memory-and-blocklist-perf
**Fixes:** F21, F24
 
**Do:**
1. Before caching, trim `info` to the fields actually needed (id, title, thumbnail, duration, extractor, webpage_url, and per
   format only: format_id, url, protocol, vcodec, acodec, ext, height, tbr/abr/vbr, filesize(_approx), http_headers
   (minus Cookie/Authorization unless required), fragments if needed). Drop descriptions, subtitles, comments, automatic captions.
2. Lower `TTLCache` maxsize default to 300 (`CACHE_MAXSIZE` env) and add a rough byte-size guard.
3. Redis backend: never store Cookie/Authorization headers; if headers are needed store them only in memory.
4. Blocklist: use a single shared `BlocklistManager` (app.py currently builds its own plus the module singleton).
5. Cache the extractor lookup: compute `find_extractor_name(url)` once per request and reuse; add a small LRU keyed by hostname
   to avoid scanning ~1800 `suitable()` regexes on repeated hosts; check the domain blocklist BEFORE extractor matching;
   cap URL length before regex matching to avoid ReDoS-prone inputs.
**Tests:** trimmed info still supports stream/preview flows; secrets headers absent from the Redis payload; extractor lookup
runs once per download request (mock counter); long malicious URL rejected quickly.
 
**Commit:** `perf(P14): trim cached metadata, single blocklist manager, extractor lookup cache [F21,F24]`
 
---
 
## P15: origin-lock-and-host-hardening
**Fixes:** F18, F25
 
**Do:**
1. `deploy/cloudflare-ufw.sh`: fetch Cloudflare IP ranges (print the URLs; allow the operator to run it) and allow 80/443
   only from those ranges via UFW; keep 22 restricted; idempotent; DOES NOT run automatically. Document the Docker/UFW bypass
   issue (Docker publishes ports through iptables; add rules to the `DOCKER-USER` chain as well) and implement that.
   Make the MEDIA_HOST grey-cloud case an explicit opt-in flag (`--allow-direct-media`).
2. `host-setup.sh`: add an sshd drop-in (`/etc/ssh/sshd_config.d/99-jungle.conf`): `PasswordAuthentication no`,
   `PermitRootLogin no`, `PubkeyAuthentication yes`. Validate with `sshd -t` before reload; abort if the check fails.
3. Replace `systemctl reload docker` with a documented restart step (daemon.json log options require a restart).
4. Add an IMDSv2 enforcement section to DEPLOY.md with the OCI CLI command (print only).
5. Install `iptables-persistent` non-interactively (debconf preseed) so it does not hang.
**Tests:** shellcheck-style syntax check with `bash -n` on each script (add as a pytest that runs `bash -n`).
 
**Commit:** `fix(P15): Cloudflare-only origin firewall script, SSH hardening, docker restart, IMDSv2 docs [F18,F25]`
 
---
 
## P16: container-egress-lockdown
**Fixes:** F4 (part), E8
 
**Problem:** only IMDS is blocked. A compromised or SSRF'd container could reach VCN/RFC1918, host loopback via the
docker gateway, and link-local ranges.
 
**Do:**
1. Extend `deploy/block_imds.sh` (rename to `deploy/block_internal_egress.sh`, keep a thin wrapper under the old name) to
   add `DOCKER-USER` DROP rules for: `169.254.0.0/16`, `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, `100.64.0.0/10`,
   BUT with ACCEPT rules first for the docker bridge subnet itself (app to caddy to redis) and established connections.
2. IPv6: if IPv6 is enabled for Docker, add `ip6tables` equivalents (ULA `fc00::/7`, link-local `fe80::/10`).
3. Idempotent (`-C` before `-I`), persisted with netfilter-persistent, with a `--verify` mode that runs a test
   `docker run --rm curlimages/curl` against 169.254.169.254 expecting failure (print only; not auto-run in tests).
4. Mirror the same rule set in `host-setup.sh` by calling the script, not duplicating logic.
5. Document the DNS caveat: containers need a public resolver; allow the docker embedded DNS (127.0.0.11) and the host
   resolver explicitly.
**Tests:** `bash -n`; a Python test parses the script for the required CIDRs.
 
**Commit:** `fix(P16): block RFC1918/link-local container egress with safe allowances [F4,E8]`
 
---
 
## P17: dockerfile-compose-hardening
**Fixes:** F27, E9
 
**Do:**
1. Dockerfile Tailwind download: use `curl -fsSL`; verify the SHA256 (pin per-arch checksums in `ARG`s; fail the build on mismatch).
2. Compose memory limit: make it `${APP_MEM_LIMIT:-2048M}` and document `1024M` for the AMD 1 GB fallback with swap;
   also `${APP_CPUS:-2.0}`.
3. Add `init: true` (tini) to the app service so orphaned ffmpeg zombies are reaped.
4. Add `pids_limit`, `cap_drop: [ALL]`, `ulimits.nofile`, and a `stop_grace_period: 30s`.
5. Caddy: pin the image by tag+digest note; add the `caddy` healthcheck.
6. Pip: `--no-cache-dir`, `PIP_DISABLE_PIP_VERSION_CHECK=1`; pin the Python base image minor (`python:3.11-slim-bookworm`).
7. Healthcheck: use `python -c "import urllib.request..."` instead of installing curl, if that lets you drop curl from the image.
8. `.dockerignore`: ensure `data/` is NOT excluded and `static/css/app.css` handling matches P10.
**Tests:** `docker compose config` passes (skip if docker is missing); Python test parses compose YAML for `init`, limits, `cap_drop`.
 
**Commit:** `chore(P17): verified Tailwind download, tini, caps, configurable limits, healthcheck [F27,E9]`
 
---
 
## P18: docs-and-caddy-fixes
**Fixes:** F22, F29 (part)
 
**Do:**
1. **Caddyfile:** add the commented `media.{$DOMAIN}` block that DEPLOY.md refers to (strict CORS to the main site,
   token-required, `flush_interval -1`, its own Let's Encrypt cert). Add basic per-IP rate limiting only if a documented
   plugin is used; otherwise remove the claim from docs. Replace the 30-day static cache with
   `max-age=3600, must-revalidate` unless filenames are versioned (use `?v={{ asset_version }}` and keep 30 days).
   Translate Tanglish comments to English (public repo).
2. **RUNBOOK.md:** fix `/api/preview` curl (use `/api/download` POST with JSON); expected messages must match the code; `/healthz`
   returns `{"status":"ok"}`; replace `docker system prune -af --volumes` with a safe variant that never touches
   `caddy_data` (certs); log truncation path advice; DMCA procedure must reflect the `./data` volume (P07) and the extractor-level
   block (`data/blocked_domains.txt`).
3. **DEPLOY.md:** cookie name is `cf_clearance_token`; add `docker buildx` multi-arch build steps (SPEC 6.1);
   consistent `ENV=prod`; add `SECRET_KEY` generation command; add the P15/P16 script steps in order; set Telegram vars for cron.
4. Remove the dead root `robots.txt` and `sitemap.xml` placeholders containing `[jungle_url]` (Flask routes serve real ones).
5. Add `README.md` (SPEC section 9) with overview, local run, `ALLOW_ADULT` warning (never enable on the ad-monetized domain),
   single-worker trade-off, and a Growth checklist (Search Console, Bing Webmaster, structured data test, CWV).
**Tests:** a pytest asserts DEPLOY/RUNBOOK mention no `cf_clearance ` standalone and no `prune -af --volumes`.
 
**Commit:** `docs(P18): fix runbook/deploy accuracy, add Caddy media block, README [F22,F29]`
 
---
 
## P19: sites-catalog-working-filter
**Fixes:** E10, F10 (part)
 
**Problem:** PLAN claims broken extractors are excluded, but there is no `ie.working()` check. Count could overstate.
 
**Do:**
1. In `sites_catalog.build_catalog`: skip extractors where `ie.working()` is False and those with `_ENABLED = False`.
2. Skip entries that are clearly not sites (IE names ending in `:playlist`, `:user`, `:search` already dedupe by base name;
   also drop base names that are only search/tag helper extractors if they have no standalone URL).
3. Re-review `data/adult_extractors.txt` with a second heuristic pass (script output only; add new candidates to a
   `data/adult_candidates_review.txt` for manual review, do NOT auto-merge). Keep the AdultSwim whitelist.
4. Cross-check the DRM list: `crunchyroll`, `deezer` etc. exist; remove names that do not match real extractor names.
5. `display_count` still rounds DOWN to the nearest hundred.
**Tests:** broken extractors excluded (mock one with `working()` False); count equals computed; `adultswim` still present;
no adult names in the catalog.
 
**Commit:** `fix(P19): exclude non-working extractors, refine adult/DRM lists, honest count [E10,F10]`
 
---
 
## P20: sitemap-from-routes
**Fixes:** F30
 
**Do:**
1. Generate `sitemap.xml` from a single registry list in code (`PUBLIC_ROUTES`), include `lastmod` from a build constant,
   use `SITE_URL`, canonical clean URLs only (`/terms`, `/privacy`, `/supported-sites`).
2. Add `robots.txt` Allow lines consistent with SPEC; include sitemap URL.
3. Add `<meta name="robots" content="noindex">` to 404/500.
4. Leave a clearly marked extension point (`PLATFORM_PAGES = []`) for Phase 4 landing pages, /dmca and /contact. Do NOT build
   Phase 4 now.
**Tests:** every sitemap URL returns 200; no placeholder `[jungle_url]` anywhere; robots references the sitemap with `SITE_URL`.
 
**Commit:** `fix(P20): sitemap and robots generated from route registry [F30]`
 
---
 
## P21: ci-lint-makefile
**Fixes:** SPEC 6.8 and 9 (previously missing)
 
**Do:**
1. `Makefile` with `test`, `lint`, `run`, `build` (multi-arch buildx instructions printed), `smoke`.
2. `pyproject.toml` or `ruff.toml` with sane rules; fix lint errors across the repo (no behavior changes).
3. `.github/workflows/ci.yml`: ruff, pytest, docker buildx build for `linux/amd64,linux/arm64` (no push to registries).
4. `.github/workflows/smoke.yml`: scheduled weekly smoke test; failures are non-blocking and report per-site results.
5. Remove unused imports/dead code identified in the audit: `is_cloudflare_ip` (keep if used by a test and documented),
   unused `COOKIE_NAME`, `delete_metadata` usage (call it or remove), unused config keys (`TRUST_CLOUDFLARE_IP`,
   `MEDIA_HOST`, `ADS_*`) must either be wired (see below) or documented as reserved in `.env.example`.
6. Wire `ADS_ENABLED` into template context (`ads_enabled`) and include `_ads.html` slots in `index.html` (all collapsed when
   disabled, per SPEC 8). Do not load any ad script.
**Tests:** `make test` passes; `ruff check .` clean; with `ADS_ENABLED=false` no `ad-slot` markup renders.
 
**Commit:** `chore(P21): Makefile, ruff, CI workflows, dead code cleanup, ads slots wired [spec-6.8]`
 
---
 
## P22: test-suite-hardening
**Fixes:** F28
 
**Do:**
1. Ensure the autouse limiter/semaphore reset from P01 covers every test module; remove cross-test order dependence.
2. Remove over-mocking where it hides bugs: tests for `/api/download` should use a fake `YoutubeDL` returning realistic info
   dicts, not patch `validate_public_url` globally; patch DNS (`socket.getaddrinfo`) instead.
3. Add the missing security tests listed in the audit:
   - stream/preview with blocked/adult origin cannot be reached (token only)
   - CDN URL SSRF and redirect to private IP in stream/preview
   - Turnstile enabled: missing/invalid token gets 403; valid token plus cookie flow works (mock Cloudflare verify)
   - ffmpeg kill and semaphore release on disconnect
   - port `ValueError` cases, `ENV=production` HSTS, "both"/"robot" mapping
   - size/duration caps and live rejection, proxy used for ffmpeg, budget accrual
   - `get_real_client_ip` with an untrusted peer and spoofed `X-Real-IP`
   - `/metrics` from a non-loopback peer with a spoofed `X-Real-IP: 127.0.0.1` header is rejected
   - Content-Disposition with CRLF, very long and bidi names
4. Add a coverage report (`pytest --cov`) and print a per-module summary; target at least 85% on `security.py`,
   `hardening.py`, `blocklist.py`, `delivery.py`.
5. Fix `/metrics` logic: use the real peer address only (ignore forwarded headers entirely); behind Caddy it must always be 403
   from outside.
**Commit:** `test(P22): remove over-mocking, add missing security tests, fix /metrics peer check [F28]`
 
---
 
## P23: final-verification-report
**Fixes:** all (verification only)
 
**Do:**
1. Run `pytest -q`, `ruff check .`, `bash -n` on every script, `docker compose config`, and (if docker is available)
   `docker build .`. Do not push or deploy anything.
2. Write `AUDIT_REPORT.md`: a table mapping every audit ID (F1-F30, E1-E10) to the prompt, the commit sha
   (`git log --grep`), the files, the tests proving it, and status (fixed / partial / blocked). Be honest; mark partial where partial.
3. Re-audit quickly: grep for `request.url_root`, `cdn.tailwindcss.com`, `url=` in stream/preview, `str(e)` in responses,
   `1800`, `window.location.assign`, `setTimeout(` with 5000, `Procfile`, and report any hit.
4. Print a go/no-go checklist for Oracle deploy and remaining risks (YouTube blocking without a proxy, ad-network approval,
   Oracle capacity, Phase 4 pages missing: /dmca, /contact, landing pages, consent banner).
5. Print, but do NOT run, the commands the operator must run manually: `git push`, the `git filter-repo` purge for `cookies.txt`,
   the host scripts (`host-setup.sh`, `cloudflare-ufw.sh`, `block_internal_egress.sh`).
**Commit:** `docs(P23): final audit report and verification [all]`
