import os
import logging
import subprocess
from flask import Flask, render_template, request, jsonify, Response
import yt_dlp
import requests
from urllib.parse import quote

app = Flask(__name__)

log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)


def format_size(bytes_size):
    if not bytes_size: return "-- MB"
    mb = bytes_size / (1024 * 1024)
    return f"{mb:.1f} MB"


COOKIES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cookies.txt')

def fetch_fresh_metadata(video_url):
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'extractor_args': {
            'youtube': {'skip': ['dash', 'hls']},
        }
    }
    if os.path.exists(COOKIES_FILE):
        ydl_opts['cookiefile'] = COOKIES_FILE
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        return ydl.extract_info(video_url, download=False)



def is_fragmented(fmt):
    protocol = fmt.get('protocol', '')
    url = fmt.get('url', '')
    return ('m3u8' in protocol or 'dash' in protocol or '.m3u8' in url or '.mpd' in url)


def build_ffmpeg_headers(headers_dict):
    if not headers_dict: return ""
    return "".join(f"{k}: {v}\r\n" for k, v in headers_dict.items())


# --- THE UNIVERSAL FFMPEG ENGINE ---
def stream_universal_ffmpeg(video_fmt, audio_fmt, is_audio_only):
    CHUNK_SIZE = 1024 * 1024
    ff_cmd = ['ffmpeg', '-hide_banner', '-loglevel', 'error']

    if is_audio_only:
        target_fmt = audio_fmt if audio_fmt else video_fmt
        url = target_fmt.get('url')
        headers = build_ffmpeg_headers(target_fmt.get('http_headers', {}))

        if headers: ff_cmd.extend(['-headers', headers])
        # FIX: Using -vn (ignore video) instead of explicit mapping prevents missing stream crashes
        ff_cmd.extend(['-i', url, '-vn', '-f', 'mp3', '-b:a', '192k', 'pipe:1'])
        content_type = 'audio/mpeg'

    elif audio_fmt:
        v_url = video_fmt.get('url')
        v_head = build_ffmpeg_headers(video_fmt.get('http_headers', {}))
        a_url = audio_fmt.get('url')
        a_head = build_ffmpeg_headers(audio_fmt.get('http_headers', {}))

        if v_head: ff_cmd.extend(['-headers', v_head])
        ff_cmd.extend(['-i', v_url])

        if a_head: ff_cmd.extend(['-headers', a_head])
        ff_cmd.extend(['-i', a_url])

        ff_cmd.extend([
            '-map', '0:v:0', '-map', '1:a:0',
            '-c:v', 'copy', '-c:a', 'aac', '-b:a', '128k',
            '-f', 'mp4', '-movflags', 'frag_keyframe+empty_moov+default_base_moof', 'pipe:1'
        ])
        content_type = 'video/mp4'

    else:
        v_url = video_fmt.get('url')
        v_head = build_ffmpeg_headers(video_fmt.get('http_headers', {}))

        if v_head: ff_cmd.extend(['-headers', v_head])
        ff_cmd.extend([
            '-i', v_url,
            '-map', '0:v?', '-map', '0:a?',
            '-c:v', 'copy', '-c:a', 'aac', '-b:a', '128k',
            '-f', 'mp4', '-movflags', 'frag_keyframe+empty_moov+default_base_moof', 'pipe:1'
        ])
        content_type = 'video/mp4'

    def gen():
        proc = subprocess.Popen(ff_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            while True:
                chunk = proc.stdout.read(CHUNK_SIZE)
                if not chunk:
                    err = proc.stderr.read()
                    if err: log.error(f"FFMPEG Error: {err.decode('utf-8', errors='ignore')}")
                    break
                yield chunk
        except GeneratorExit:
            pass
        finally:
            proc.kill()
            proc.wait()

    return gen(), content_type


def stream_via_direct_url(target_format, media_type):
    stream_url = target_format.get('url', '')
    magic_headers = dict(target_format.get('http_headers', {}))
    magic_headers['Connection'] = 'keep-alive'

    r = requests.get(stream_url, headers=magic_headers, stream=True, timeout=30)
    content_type = 'audio/mpeg' if media_type == 'audio' else 'video/mp4'

    def gen():
        try:
            for chunk in r.iter_content(chunk_size=1024 * 1024):
                if chunk: yield chunk
        except (ConnectionError, GeneratorExit):
            pass
        finally:
            r.close()

    return gen(), content_type, r.headers.get('Content-Length')


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/download', methods=['POST'])
def download_video():
    data = request.get_json()
    video_url = data.get('url')

    if not video_url: return jsonify({'success': False, 'error': 'Please provide a valid URL.'}), 400

    try:
        info = fetch_fresh_metadata(video_url)
        formats = info.get('formats', [])
        duration = info.get('duration', 0)

        # FIX: Check if the original stream actually contains an audio track anywhere
        has_any_audio = any(f.get('acodec', 'none') != 'none' for f in formats)

        audio_options, video_options = [], []

        for f in formats:
            fmt_id = f.get('format_id')
            if not fmt_id: continue

            vcodec = f.get('vcodec', 'none')
            acodec = f.get('acodec', 'none')
            filesize = f.get('filesize') or f.get('filesize_approx')

            if not filesize and duration:
                tbr = f.get('tbr') or f.get('vbr') or f.get('abr')
                if tbr: filesize = int((tbr * 1000 * duration) / 8)

            size_str = format_size(filesize)
            protocol = f.get('protocol', '')

            if protocol in ['mhtml'] or (vcodec == 'none' and acodec == 'none'): continue

            if vcodec == 'none' and acodec != 'none':
                abr = f.get('abr', 0) or f.get('tbr', 0) or 0
                audio_options.append({
                    'format': 'MP3',
                    'quality': f"{int(abr)} kbps" if abr else "Audio",
                    'size': size_str,
                    'format_id': fmt_id,
                    'sort_val': abr or 0
                })

            elif vcodec != 'none':
                height = f.get('height', 0) or 0

                # FIX: If height is missing (common on Twitter/X), try to extract it from resolution
                if not height:
                    res = f.get('resolution', '')
                    if isinstance(res, str) and 'x' in res:
                        try: height = int(res.split('x')[1])
                        except: pass

                quality_str = f"{height}p" if height else "Video"

                video_options.append({
                    'format': 'MP4',
                    'quality': quality_str,
                    'size': size_str,
                    'format_id': fmt_id,
                    'sort_val': height or 0
                })

        unique_audio = list({a['quality']: a for a in sorted(audio_options, key=lambda x: x['sort_val'], reverse=True)}.values())
        unique_video = list({v['quality']: v for v in sorted(video_options, key=lambda x: x['sort_val'], reverse=True)}.values())

        # FIX: ONLY add the 'Extract Audio' button if the video ACTUALLY has sound.
        if not unique_audio and unique_video and has_any_audio:
            unique_audio.append({
                'format': 'MP3',
                'quality': 'Extract Audio',
                'size': unique_video[0]['size'],
                'format_id': 'auto_extract',
                'sort_val': 1
            })

        preview_format_id = 'best'
        prog_combined = [f for f in formats if f.get('vcodec', 'none') != 'none' and f.get('acodec', 'none') != 'none' and not is_fragmented(f)]
        if prog_combined:
            prog_combined.sort(key=lambda x: abs((x.get('height') or 360) - 480))
            preview_format_id = prog_combined[0]['format_id']
        elif unique_video:
            preview_format_id = unique_video[0]['format_id']

        return jsonify({
            'success': True,
            'title': info.get('title', 'Media Download'),
            'thumbnail': info.get('thumbnail', ''),
            'preview_format_id': preview_format_id,
            'original_url': video_url,
            'audio_formats': unique_audio,
            'video_formats': unique_video,
        })

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/stream')
def stream_download():
    page_url = request.args.get('url')
    format_id = request.args.get('format_id', 'best')
    filename = request.args.get('filename', 'Jungle_Download')
    media_type = request.args.get('type', 'video')

    if not page_url: return "Missing URL parameter.", 400

    try:
        info = fetch_fresh_metadata(page_url)
        formats = info.get('formats', [])

        if format_id == 'auto_extract':
            target_format = next((f for f in reversed(formats) if f.get('acodec', 'none') != 'none'), formats[-1])
        else:
            target_format = next((f for f in formats if f.get('format_id') == format_id), formats[-1] if formats else None)

        if not target_format: return "No valid format found.", 404

        fragmented = is_fragmented(target_format)
        has_audio = target_format.get('acodec', 'none') != 'none'

        ext = '.mp3' if media_type == 'audio' else '.mp4'
        if not filename.endswith(ext): filename = filename.rsplit('.', 1)[0] + ext

        try:
            filename.encode('latin-1')
            final_filename = filename.replace('"', "'")
        except UnicodeEncodeError:
            final_filename = f"{info.get('id', 'download')}{ext}"

        base_headers = {
            'Content-Disposition': f'attachment; filename="{final_filename}"',
            'X-Accel-Buffering': 'no',
            'Cache-Control': 'no-cache, no-store, must-revalidate',
        }

        if media_type == 'audio':
            generator, content_type = stream_universal_ffmpeg(target_format, None, is_audio_only=True)
            base_headers['Content-Type'] = content_type
            return Response(generator, status=200, headers=base_headers)

        elif media_type == 'video':
            if has_audio:
                if fragmented:
                    generator, content_type = stream_universal_ffmpeg(target_format, None, is_audio_only=False)
                else:
                    generator, content_type, content_length = stream_via_direct_url(target_format, media_type)
                    if content_length: base_headers['Content-Length'] = content_length
            else:
                best_audio = next((f for f in reversed(formats) if f.get('acodec') != 'none' and f.get('vcodec') == 'none'), None)
                if not best_audio:
                    best_audio = next((f for f in reversed(formats) if f.get('acodec') != 'none'), None)

                if best_audio:
                    generator, content_type = stream_universal_ffmpeg(target_format, best_audio, is_audio_only=False)
                else:
                    generator, content_type = stream_universal_ffmpeg(target_format, None, is_audio_only=False)

            base_headers['Content-Type'] = content_type
            return Response(generator, status=200, headers=base_headers)

    except Exception as e:
        return f"Stream error: {str(e)}", 500


@app.route('/api/preview')
def preview_video():
    page_url = request.args.get('url')
    format_id = request.args.get('format_id', 'best')

    if not page_url: return "Missing URL.", 400

    try:
        info = fetch_fresh_metadata(page_url)
        formats = info.get('formats', [])
        preview_format = next((f for f in formats if f.get('format_id') == format_id), formats[-1] if formats else None)

        if not preview_format: return "Format not found.", 404

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
            if range_header: headers['Range'] = range_header

            r = requests.get(stream_url, headers=headers, stream=True, timeout=30)

            response_headers = {
                'Content-Type': 'video/mp4' if is_video else 'audio/mpeg',
                'Accept-Ranges': 'bytes',
                'X-Accel-Buffering': 'no',
                'Cache-Control': 'no-cache',
            }
            if 'Content-Range' in r.headers: response_headers['Content-Range'] = r.headers['Content-Range']
            if 'Content-Length' in r.headers: response_headers['Content-Length'] = r.headers['Content-Length']

            def gen():
                for chunk in r.iter_content(chunk_size=512 * 1024):
                    if chunk: yield chunk

            return Response(gen(), status=206 if range_header else 200, headers=response_headers)

    except Exception as e:
        return f"Preview error: {str(e)}", 500


@app.route('/terms.html')
def terms_page():
    return render_template('terms.html')

@app.route('/privacy.html')
def privacy_page():
    return render_template('privacy.html')

# if __name__ == '__main__':
#     app.run(debug=True, threaded=True)
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True, threaded=True)
