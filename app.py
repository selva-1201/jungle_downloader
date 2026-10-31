import logging
import os
import subprocess
import gevent
from gevent import subprocess as gevent_subprocess
from urllib.parse import quote
from flask import Flask, render_template, request, jsonify, Response, redirect
from flask_limiter import Limiter
import yt_dlp
import requests

from config import config
from security import (
    validate_public_url,
    SSRFValidationError,
    build_safe_ffmpeg_args,
    get_safe_ytdlp_opts,
    safe_requests_get,
    validate_extracted_urls,
)
from blocklist import BlocklistManager
from hardening import (
    get_real_client_ip,
    sanitize_filename,
    build_content_disposition,
    map_error_message,
    apply_security_headers,
)
from concurrency import (
    extract_semaphore,
    ffmpeg_semaphore,
    proxy_semaphore,
    ReleasingIterator,
)
from cache_store import cache_store
from turnstile import (
    verify_turnstile_response,
    generate_clearance_token,
    verify_clearance_token,
)
from delivery import (
    is_direct_cdn_eligible,
    youtube_circuit_breaker,
    youtube_extract_semaphore,
    proxy_router,
)
from sites_catalog import sites_catalog

app = Flask(__name__)

log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

blocklist_mgr = BlocklistManager()


def get_limiter_client_ip():
    return get_real_client_ip(request)


# Rate Limiter keyed on authenticated client IP
limiter = Limiter(
    key_func=get_limiter_client_ip,
    app=app,
    default_limits=[],
    storage_uri="memory://" if config.CACHE_BACKEND == "memory" else config.REDIS_URL,
)


@app.errorhandler(429)
def ratelimit_exceeded(e):
    retry_after = getattr(e, "retry_after", 30) or 30
    resp = jsonify({
        "success": False,
        "code": "RATE_LIMIT_EXCEEDED",
        "error": "Rate limit exceeded. Please wait a moment and try again."
    })
    resp.status_code = 429
    resp.headers["Retry-After"] = str(retry_after)
    return resp


@app.after_request
def after_request_handler(response: Response) -> Response:
    return apply_security_headers(response)


def format_size(bytes_size):
    if not bytes_size:
        return "-- MB"
    mb = bytes_size / (1024 * 1024)
    return f"{mb:.1f} MB"


def is_fragmented(fmt):
    protocol = fmt.get('protocol', '')
    url = fmt.get('url', '')
    return ('m3u8' in protocol or 'dash' in protocol or '.m3u8' in url or '.mpd' in url)


def fetch_fresh_metadata(video_url: str):
    extractor_name = blocklist_mgr.find_extractor_name(video_url) or "generic"
    proxy_url = proxy_router.get_proxy_for_extractor(extractor_name)

    ydl_opts = get_safe_ytdlp_opts({
        'quiet': True,
        'no_warnings': True,
    })

    if proxy_url:
        ydl_opts['proxy'] = proxy_url

    if config.COOKIES_FILE and os.path.exists(config.COOKIES_FILE):
        ydl_opts['cookiefile'] = config.COOKIES_FILE

    if "youtube" in video_url.lower() or extractor_name.lower().startswith("youtube"):
        ydl_opts['extractor_args'] = {
            'youtube': {'player_client': ['android', 'web']}
        }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        return ydl.extract_info(video_url, download=False)


# --- UNIVERSAL FFMPEG ENGINE ---
def stream_universal_ffmpeg(video_fmt, audio_fmt, is_audio_only):
    CHUNK_SIZE = 1024 * 1024
    v_url = video_fmt.get('url') if video_fmt else None
    a_url = audio_fmt.get('url') if audio_fmt else None
    v_headers = dict(video_fmt.get('http_headers', {})) if video_fmt else {}
    a_headers = dict(audio_fmt.get('http_headers', {})) if audio_fmt else {}

    target_video_url = v_url or a_url or ""
    target_audio_url = a_url if not is_audio_only else None

    ff_cmd = build_safe_ffmpeg_args(
        video_url=target_video_url,
        audio_url=target_audio_url,
        is_audio_only=is_audio_only,
        max_duration=config.MAX_DURATION_SEC,
        headers_dict=v_headers,
        audio_headers_dict=a_headers
    )

    content_type = 'audio/mpeg' if is_audio_only else 'video/mp4'

    def gen():
        if target_video_url:
            validate_public_url(target_video_url)
        if target_audio_url:
            validate_public_url(target_audio_url)

        if config.LOG_LEVEL == "DEBUG":
            proc = gevent_subprocess.Popen(ff_cmd, stdout=gevent_subprocess.PIPE, stderr=gevent_subprocess.PIPE)
            def drain_stderr(p):
                try:
                    err = p.stderr.read()
                    if err:
                        logging.getLogger(__name__).debug(f"FFMPEG Error: {err.decode('utf-8', errors='ignore')}")
                except Exception:
                    pass
            gevent.spawn(drain_stderr, proc)
        else:
            proc = gevent_subprocess.Popen(ff_cmd, stdout=gevent_subprocess.PIPE, stderr=gevent_subprocess.DEVNULL)

        try:
            while True:
                chunk = proc.stdout.read(CHUNK_SIZE)
                if not chunk:
                    break
                yield chunk
        except GeneratorExit:
            pass
        finally:
            try:
                proc.kill()
            except Exception:
                pass
            try:
                proc.wait()
            except Exception:
                pass

    return gen(), content_type


def stream_via_direct_url(target_format, media_type, proxy_url=None):
    stream_url = target_format.get('url', '')
    magic_headers = dict(target_format.get('http_headers', {}))
    magic_headers['Connection'] = 'keep-alive'
    proxies = {'http': proxy_url, 'https': proxy_url} if proxy_url else None

    r = safe_requests_get(stream_url, headers=magic_headers, stream=True, timeout=60, proxies=proxies)
    content_type = 'audio/mpeg' if media_type == 'audio' else 'video/mp4'

    def gen():
        try:
            for chunk in r.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    yield chunk
        except (ConnectionError, GeneratorExit):
            pass
        finally:
            r.close()

    return gen(), content_type, r.headers.get('Content-Length')


# --- STATIC & LEGAL PAGES ---
@app.route('/')
def index():
    return render_template('index.html')


@app.route('/healthz')
def healthz():
    return jsonify({"status": "ok"}), 200


@app.route('/robots.txt')
def robots_txt():
    content = f"User-agent: *\nDisallow: /api/\nAllow: /\n\nSitemap: {config.SITE_URL}/sitemap.xml\n"
    return Response(content, mimetype="text/plain")


@app.route('/sitemap.xml')
def sitemap_xml():
    base = config.SITE_URL.rstrip('/')
    urls = ["/", "/terms", "/privacy", "/supported-sites"]
    xml_items = "\n".join(f"  <url><loc>{base}{u}</loc><changefreq>weekly</changefreq></url>" for u in urls)
    xml_content = f'<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n{xml_items}\n</urlset>'
    return Response(xml_content, mimetype="application/xml")


@app.route('/terms')
def terms_page():
    return render_template('terms.html')


@app.route('/terms.html')
def terms_redirect():
    return redirect('/terms', code=301)


@app.route('/privacy')
def privacy_page():
    return render_template('privacy.html')


@app.route('/privacy.html')
def privacy_redirect():
    return redirect('/privacy', code=301)


@app.route('/supported-sites')
def supported_sites_page():
    cat = sites_catalog.catalog
    return render_template(
        'supported_sites.html',
        popular=cat["popular"],
        sites=cat["sites"],
        total_count=cat["total_count"],
        display_count=cat["display_count"]
    )


@app.route('/api/sites')
def api_sites():
    client_etag = request.headers.get("If-None-Match")
    if sites_catalog.is_match_etag(client_etag):
        return Response(status=304)

    resp = jsonify(sites_catalog.catalog)
    resp.headers["ETag"] = sites_catalog.etag
    resp.headers["Cache-Control"] = "public, max-age=86400"
    return resp


@app.route('/metrics')
def metrics():
    # Restricted to localhost
    client_ip = get_real_client_ip(request)
    remote = request.remote_addr or "127.0.0.1"
    if remote not in ("127.0.0.1", "::1", "localhost") and client_ip not in ("127.0.0.1", "::1", "localhost"):
        return jsonify({"error": "Unauthorized"}), 403
    return jsonify(proxy_router.get_metrics_snapshot())


@app.errorhandler(404)
def handle_404(e):
    return render_template('404.html'), 404


@app.errorhandler(500)
def handle_500(e):
    return render_template('500.html'), 500


# --- API ROUTES ---
@app.route('/api/download', methods=['POST'])
@limiter.limit(lambda: config.RATE_LIMIT_EXTRACT)
def download_video():
    data = request.get_json(silent=True) or {}
    video_url = data.get('url', '').strip()

    if not video_url:
        return jsonify({'success': False, 'error': 'Please provide a valid URL.'}), 400

    client_ip = get_real_client_ip(request)

    # 1. Bot Protection (Turnstile)
    if config.TURNSTILE_SECRET:
        clearance_token = request.cookies.get("cf_clearance_token")
        if not (clearance_token and verify_clearance_token(clearance_token, client_ip)):
            turnstile_token = data.get("turnstile_token") or request.headers.get("CF-Turnstile-Token")
            if not verify_turnstile_response(turnstile_token, client_ip):
                return jsonify({
                    'success': False,
                    'code': 'BOT_CHALLENGE_REQUIRED',
                    'error': 'Bot verification challenge failed. Please verify and retry.'
                }), 403

    # 2. Blocklist Enforcement (fast offline check before DNS resolution)
    is_blocked, reason = blocklist_mgr.is_url_blocked(video_url)
    if is_blocked:
        return jsonify({'success': False, 'error': reason}), 400

    # 3. SSRF Guard
    try:
        validate_public_url(video_url)
    except SSRFValidationError as e:
        return jsonify({
            'success': False,
            'error': map_error_message(e)
        }), 400

    extractor_name = blocklist_mgr.find_extractor_name(video_url) or "generic"
    is_youtube = ("youtube" in video_url.lower()) or (extractor_name.lower().startswith("youtube"))

    # 4. YouTube Circuit Breaker & Dedicated Semaphore
    acquired_yt = False
    if is_youtube:
        is_avail, cb_error = youtube_circuit_breaker.is_available()
        if not is_avail:
            return jsonify({
                'success': False,
                'error': cb_error or "YouTube is temporarily unavailable, other sites work"
            }), 503

        acquired_yt = youtube_extract_semaphore.acquire(blocking=True, timeout=5)
        if not acquired_yt:
            resp = jsonify({
                'success': False,
                'error': 'Server busy processing YouTube requests. Please retry in a moment.'
            })
            resp.headers["Retry-After"] = "30"
            return resp, 503

    # 5. Global Extract Semaphore
    acquired_global = extract_semaphore.acquire(blocking=True, timeout=10)
    if not acquired_global:
        if acquired_yt:
            youtube_extract_semaphore.release()
        resp = jsonify({
            'success': False,
            'error': 'Server busy, try again in a minute.'
        })
        resp.headers["Retry-After"] = "30"
        return resp, 503

    try:
        proxy_router.record_request(extractor_name)

        if proxy_router.is_site_throttled(extractor_name):
            return jsonify({
                'success': False,
                'error': 'Site temporarily blocking us, try again later.'
            }), 429

        info = fetch_fresh_metadata(video_url)
        info = validate_extracted_urls(info)
        proxy_router.record_result(extractor_name, success=True)
        if is_youtube:
            youtube_circuit_breaker.record_success()

        formats = info.get('formats', [])
        duration = info.get('duration', 0)

        # Video duration limit enforcement
        if duration and duration > config.MAX_DURATION_SEC:
            return jsonify({
                'success': False,
                'error': f"Video too long (maximum allowed is {config.MAX_DURATION_SEC // 60} minutes)."
            }), 400

        has_any_audio = any(f.get('acodec', 'none') != 'none' for f in formats)

        audio_options, video_options = [], []

        for f in formats:
            fmt_id = f.get('format_id')
            if not fmt_id:
                continue

            vcodec = f.get('vcodec', 'none')
            acodec = f.get('acodec', 'none')
            filesize = f.get('filesize') or f.get('filesize_approx')

            if not filesize and duration:
                tbr = f.get('tbr') or f.get('vbr') or f.get('abr')
                if tbr:
                    filesize = int((tbr * 1000 * duration) / 8)

            size_str = format_size(filesize)
            protocol = f.get('protocol', '')

            if protocol in ['mhtml'] or (vcodec == 'none' and acodec == 'none'):
                continue

            direct_eligible = is_direct_cdn_eligible(f, extractor_name)
            delivery_mode = "direct" if direct_eligible else "proxy"

            if vcodec == 'none' and acodec != 'none':
                abr = f.get('abr', 0) or f.get('tbr', 0) or 0
                audio_options.append({
                    'format': 'MP3',
                    'quality': f"{int(abr)} kbps" if abr else "Audio",
                    'size': size_str,
                    'format_id': fmt_id,
                    'delivery_mode': delivery_mode,
                    'sort_val': abr or 0
                })

            elif vcodec != 'none':
                height = f.get('height', 0) or 0
                if not height:
                    res = f.get('resolution', '')
                    if isinstance(res, str) and 'x' in res:
                        try:
                            height = int(res.split('x')[1])
                        except Exception:
                            pass

                quality_str = f"{height}p" if height else "Video"
                video_options.append({
                    'format': 'MP4',
                    'quality': quality_str,
                    'size': size_str,
                    'format_id': fmt_id,
                    'delivery_mode': delivery_mode,
                    'sort_val': height or 0
                })

        unique_audio = list({a['quality']: a for a in sorted(audio_options, key=lambda x: x['sort_val'], reverse=True)}.values())
        unique_video = list({v['quality']: v for v in sorted(video_options, key=lambda x: x['sort_val'], reverse=True)}.values())

        if not unique_audio and unique_video and has_any_audio:
            unique_audio.append({
                'format': 'MP3',
                'quality': 'Extract Audio',
                'size': unique_video[0]['size'],
                'format_id': 'auto_extract',
                'delivery_mode': 'proxy',
                'sort_val': 1
            })

        preview_format_id = 'best'
        prog_combined = [f for f in formats if f.get('vcodec', 'none') != 'none' and f.get('acodec', 'none') != 'none' and not is_fragmented(f)]
        if prog_combined:
            prog_combined.sort(key=lambda x: abs((x.get('height') or 360) - 480))
            preview_format_id = prog_combined[0]['format_id']
        elif unique_video:
            preview_format_id = unique_video[0]['format_id']

        # Store in TTL Cache under single-extraction token
        token = cache_store.store_metadata(info, video_url)

        resp = jsonify({
            'success': True,
            'token': token,
            'title': info.get('title', 'Media Download'),
            'thumbnail': info.get('thumbnail', ''),
            'duration': info.get('duration', 0),
            'preview_format_id': preview_format_id,
            'original_url': video_url,
            'audio_formats': unique_audio,
            'video_formats': unique_video,
        })

        if config.TURNSTILE_SECRET:
            clearance_tok = generate_clearance_token(client_ip)
            resp.set_cookie("cf_clearance_token", clearance_tok, max_age=900, httponly=True, samesite="Lax")

        return resp

    except Exception as e:
        proxy_router.record_result(extractor_name, success=False, error=str(e))
        if is_youtube:
            youtube_circuit_breaker.record_failure(str(e))

        err_msg = map_error_message(e)
        status_code = 429 if "blocking us" in err_msg.lower() else 400
        return jsonify({'success': False, 'error': err_msg}), status_code

    finally:
        extract_semaphore.release()
        if acquired_yt:
            youtube_extract_semaphore.release()


@app.route('/api/stream')
@limiter.limit(lambda: config.RATE_LIMIT_STREAM)
def stream_download():
    token = request.args.get('token')
    format_id = request.args.get('format_id', 'best')
    filename = request.args.get('filename', 'Jungle_Download')
    media_type = request.args.get('type', 'video')

    if not token:
        return jsonify({
            "success": False,
            "code": "SESSION_EXPIRED",
            "error": "Download session expired. Please search again."
        }), 410

    cached = cache_store.get_metadata(token)
    if not cached:
        return jsonify({
            "success": False,
            "code": "SESSION_EXPIRED",
            "error": "Download session expired. Please search again."
        }), 410

    info = cached.get("info")
    page_url = cached.get("original_url")
    if not info:
        return jsonify({
            "success": False,
            "code": "SESSION_EXPIRED",
            "error": "Download session expired. Please search again."
        }), 410

    formats = info.get('formats', [])
    if format_id == 'auto_extract':
        target_format = next((f for f in reversed(formats) if f.get('acodec', 'none') != 'none'), formats[-1] if formats else None)
    else:
        target_format = next((f for f in formats if f.get('format_id') == format_id), formats[-1] if formats else None)

    if not target_format:
        return jsonify({"success": False, "error": "No valid format found."}), 404

    extractor_name = info.get('extractor', '')

    # --- 5.1 DIRECT LINK MODE CHECK ---
    if is_direct_cdn_eligible(target_format, extractor_name):
        direct_url = target_format.get('url')
        if direct_url:
            try:
                validate_public_url(direct_url)
                resp = redirect(direct_url, code=302)
                if token:
                    resp.set_cookie(f"dl_{token}", "1", max_age=60, path="/")
                return resp
            except SSRFValidationError:
                pass  # Fallback to proxy streaming if URL fails public check

    # --- 5.1 PROXY STREAMING MODE ---
    fragmented = is_fragmented(target_format)
    has_audio = target_format.get('acodec', 'none') != 'none'
    ext = '.mp3' if media_type == 'audio' else '.mp4'
    content_disp = build_content_disposition(filename, ext)

    base_headers = {
        'Content-Disposition': content_disp,
        'X-Accel-Buffering': 'no',
        'Cache-Control': 'no-cache, no-store, must-revalidate',
    }

    needs_ffmpeg = (media_type == 'audio') or fragmented or (media_type == 'video' and not has_audio)
    active_semaphore = ffmpeg_semaphore if needs_ffmpeg else proxy_semaphore

    acquired = active_semaphore.acquire(blocking=True, timeout=10)
    if not acquired:
        err_text = "Server busy converting media. Please try again in a minute." if needs_ffmpeg else "Server busy streaming downloads. Please try again in a moment."
        resp = jsonify({
            "success": False,
            "error": err_text
        })
        resp.headers["Retry-After"] = "30"
        return resp, 503

    permit_released = False
    def release_permit():
        nonlocal permit_released
        if not permit_released:
            permit_released = True
            try:
                active_semaphore.release()
            except Exception:
                pass

    try:
        if needs_ffmpeg:
            if media_type == 'audio':
                raw_gen, content_type = stream_universal_ffmpeg(target_format, None, is_audio_only=True)
            else:
                best_audio = next((f for f in reversed(formats) if f.get('acodec') != 'none' and f.get('vcodec') == 'none'), None)
                if not best_audio:
                    best_audio = next((f for f in reversed(formats) if f.get('acodec') != 'none'), None)
                raw_gen, content_type = stream_universal_ffmpeg(target_format, best_audio, is_audio_only=False)

            base_headers['Content-Type'] = content_type
            wrapped_gen = ReleasingIterator(raw_gen, on_release=release_permit)
            resp = Response(wrapped_gen, status=200, headers=base_headers, direct_passthrough=False)
            resp.call_on_close(release_permit)
            if token:
                resp.set_cookie(f"dl_{token}", "1", max_age=60, path="/")
            return resp

        else:
            # Single progressive stream with both audio and video
            proxy_url = proxy_router.get_proxy_for_extractor(extractor_name)
            raw_gen, content_type, content_length = stream_via_direct_url(target_format, media_type, proxy_url=proxy_url)
            base_headers['Content-Type'] = content_type
            if content_length:
                base_headers['Content-Length'] = str(content_length)

            wrapped_gen = ReleasingIterator(raw_gen, on_release=release_permit)
            resp = Response(wrapped_gen, status=200, headers=base_headers, direct_passthrough=False)
            resp.call_on_close(release_permit)
            if token:
                resp.set_cookie(f"dl_{token}", "1", max_age=60, path="/")
            return resp

    except Exception:
        release_permit()
        raise


@app.route('/api/preview')
@limiter.limit(lambda: config.RATE_LIMIT_PREVIEW)
def preview_video():
    token = request.args.get('token')
    format_id = request.args.get('format_id', 'best')

    if not token:
        return jsonify({
            "success": False,
            "code": "SESSION_EXPIRED",
            "error": "Preview session expired. Please search again."
        }), 410

    cached = cache_store.get_metadata(token)
    if not cached:
        return jsonify({
            "success": False,
            "code": "SESSION_EXPIRED",
            "error": "Preview session expired. Please search again."
        }), 410

    info = cached.get("info")
    if not info:
        return jsonify({
            "success": False,
            "code": "SESSION_EXPIRED",
            "error": "Preview session expired. Please search again."
        }), 410

    formats = info.get('formats', [])
    preview_format = next((f for f in formats if f.get('format_id') == format_id), formats[-1] if formats else None)

    if not preview_format:
        return jsonify({"success": False, "error": "Format not found."}), 404

    is_video = preview_format.get('vcodec', 'none') != 'none'
    has_audio = preview_format.get('acodec', 'none') != 'none'
    fragmented = is_fragmented(preview_format)

    if is_video and not has_audio:
        best_audio = next((f for f in reversed(formats) if f.get('acodec') != 'none' and f.get('vcodec') == 'none'), None)
        if not best_audio:
            best_audio = next((f for f in reversed(formats) if f.get('acodec') != 'none'), None)

        if best_audio:
            generator, content_type = stream_universal_ffmpeg(preview_format, best_audio, is_audio_only=False)
            return Response(generator, status=200, headers={
                'Content-Type': content_type,
                'X-Accel-Buffering': 'no',
                'Cache-Control': 'no-cache, no-store, must-revalidate',
            })

    if fragmented:
        is_audio_only_preview = not is_video
        generator, content_type = stream_universal_ffmpeg(preview_format, None, is_audio_only=is_audio_only_preview)
        return Response(generator, status=200, headers={
            'Content-Type': content_type,
            'X-Accel-Buffering': 'no',
            'Cache-Control': 'no-cache, no-store, must-revalidate',
        })

    else:
        stream_url = preview_format.get('url', '')
        headers = dict(preview_format.get('http_headers', {}))
        range_header = request.headers.get('Range')
        if range_header:
            headers['Range'] = range_header

        r = safe_requests_get(stream_url, headers=headers, stream=True, timeout=30)

        response_headers = {
            'Content-Type': 'video/mp4' if is_video else 'audio/mpeg',
            'Accept-Ranges': 'bytes',
            'X-Accel-Buffering': 'no',
            'Cache-Control': 'no-cache',
        }
        if 'Content-Range' in r.headers:
            response_headers['Content-Range'] = r.headers['Content-Range']
        if 'Content-Length' in r.headers:
            response_headers['Content-Length'] = r.headers['Content-Length']

        def gen():
            try:
                for chunk in r.iter_content(chunk_size=512 * 1024):
                    if chunk:
                        yield chunk
            finally:
                r.close()

        resp = Response(gen(), status=206 if range_header else 200, headers=response_headers, direct_passthrough=False)
        resp.call_on_close(r.close)
        return resp


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=config.PORT, debug=config.DEBUG)
