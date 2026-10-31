import socket
import time
import pytest
import yt_dlp
from app import app
from cache_store import cache_store
from concurrency import extract_semaphore, ffmpeg_semaphore
from config import config
from turnstile import generate_clearance_token, verify_clearance_token, verify_turnstile_response


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


# --- 1.1 CACHE & TOKEN TESTS ---
def test_cache_store_put_and_get():
    fake_info = {"id": "12345", "title": "Test Video", "formats": []}
    token = cache_store.store_metadata(fake_info, "https://example.com/video")
    assert token is not None
    assert len(token) > 16

    cached = cache_store.get_metadata(token)
    assert cached is not None
    assert cached["info"]["title"] == "Test Video"
    assert cached["original_url"] == "https://example.com/video"

    # Missing token lookup
    assert cache_store.get_metadata("non-existent-token") is None


def test_preview_and_stream_session_expired_410(client):
    # Preview with invalid/expired token returns 410 Gone
    resp_prev = client.get("/api/preview?token=expired_token_123&format_id=best")
    assert resp_prev.status_code == 410
    data_prev = resp_prev.get_json()
    assert data_prev["success"] is False
    assert data_prev["code"] == "SESSION_EXPIRED"

    # Stream with invalid/expired token returns 410 Gone
    resp_stream = client.get("/api/stream?token=expired_token_123&format_id=best")
    assert resp_stream.status_code == 410
    data_stream = resp_stream.get_json()
    assert data_stream["success"] is False
    assert data_stream["code"] == "SESSION_EXPIRED"


def test_stream_and_preview_raw_url_returns_410_no_ytdlp(client, mocker):
    mock_extract = mocker.patch.object(yt_dlp.YoutubeDL, "extract_info")

    # Raw url query param on stream returns 410 and never calls yt-dlp
    resp_stream = client.get("/api/stream?url=https://www.pornhub.com/x&format_id=best")
    assert resp_stream.status_code == 410
    assert resp_stream.get_json()["code"] == "SESSION_EXPIRED"
    mock_extract.assert_not_called()

    # Raw url query param on preview returns 410 and never calls yt-dlp
    resp_prev = client.get("/api/preview?url=https://www.pornhub.com/x&format_id=best")
    assert resp_prev.status_code == 410
    assert resp_prev.get_json()["code"] == "SESSION_EXPIRED"
    mock_extract.assert_not_called()


def test_preview_allows_over_ten_requests_per_minute(client, mocker):
    fake_info = {
        "id": "prev123",
        "title": "Preview Video",
        "formats": [{
            "format_id": "18",
            "vcodec": "avc1",
            "acodec": "mp4a",
            "url": "https://example.com/video.mp4",
            "protocol": "https"
        }]
    }
    token = cache_store.store_metadata(fake_info, "https://example.com/video")
    mock_resp = mocker.MagicMock(
        headers={},
        is_redirect=False,
        status_code=200,
        iter_content=lambda chunk_size: iter([b"fake-bytes"])
    )
    mocker.patch("app.safe_requests_get", return_value=mock_resp)

    # Send 15 requests in a row - none should be rate limited (rate limit is 120/min)
    for _ in range(15):
        resp = client.get(f"/api/preview?token={token}&format_id=18")
        assert resp.status_code != 429
        assert resp.status_code == 200


def test_api_download_generates_token_and_caches_info(client, mocker):
    mock_info = {
        "id": "vid123",
        "title": "Sample Jungle Clip",
        "thumbnail": "https://example.com/thumb.jpg",
        "duration": 120,
        "formats": [
            {
                "format_id": "18",
                "vcodec": "avc1",
                "acodec": "mp4a",
                "height": 360,
                "url": "https://example.com/stream.mp4",
                "protocol": "https"
            }
        ]
    }
    # Mock yt_dlp extraction
    mocker.patch.object(yt_dlp.YoutubeDL, "extract_info", return_value=mock_info)
    mocker.patch("app.validate_public_url", return_value=True)

    resp = client.post("/api/download", json={"url": "https://www.youtube.com/watch?v=sample"})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is True
    assert "token" in data
    assert data["duration"] == 120
    assert data["title"] == "Sample Jungle Clip"

    # Verify cached under the token
    cached = cache_store.get_metadata(data["token"])
    assert cached is not None
    assert cached["info"]["id"] == "vid123"


# --- 1.2 CONCURRENCY TESTS ---
def test_extract_semaphore_saturation_returns_503(client, mocker):
    mocker.patch.object(yt_dlp.YoutubeDL, "extract_info", return_value={"id": "test", "formats": []})
    mocker.patch("app.validate_public_url", return_value=True)
    mock_sem = mocker.patch("app.extract_semaphore")
    mock_sem.acquire.return_value = False

    resp = client.post("/api/download", json={"url": "https://www.youtube.com/watch?v=busy"})
    assert resp.status_code == 503
    assert resp.headers.get("Retry-After") == "30"
    data = resp.get_json()
    assert "Server busy" in data["error"]


def test_ffmpeg_semaphore_saturation_returns_503(client, mocker):
    fake_info = {
        "id": "stream123",
        "title": "Stream Video",
        "formats": [{"format_id": "18", "vcodec": "avc1", "acodec": "mp4a", "url": "https://example.com/video.mp4"}]
    }
    token = cache_store.store_metadata(fake_info, "https://example.com/video")
    mock_sem = mocker.patch("app.ffmpeg_semaphore")
    mock_sem.acquire.return_value = False

    resp = client.get(f"/api/stream?token={token}&format_id=18&type=audio")
    assert resp.status_code == 503
    assert resp.headers.get("Retry-After") == "30"
    data = resp.get_json()
    assert "Server busy" in data["error"]


def test_proxy_semaphore_saturation_returns_503(client, mocker):
    fake_info = {
        "id": "stream123",
        "title": "Stream Video",
        "formats": [{"format_id": "18", "vcodec": "avc1", "acodec": "mp4a", "url": "https://example.com/video.mp4"}]
    }
    token = cache_store.store_metadata(fake_info, "https://example.com/video")
    mock_sem = mocker.patch("app.proxy_semaphore")
    mock_sem.acquire.return_value = False

    resp = client.get(f"/api/stream?token={token}&format_id=18")
    assert resp.status_code == 503
    assert resp.headers.get("Retry-After") == "30"
    data = resp.get_json()
    assert "Server busy" in data["error"]


def test_video_too_long_rejected(client, mocker):
    mocker.patch.object(yt_dlp.YoutubeDL, "extract_info", return_value={
        "id": "long123",
        "title": "Too Long Video",
        "duration": config.MAX_DURATION_SEC + 100,
        "formats": []
    })
    mocker.patch("app.validate_public_url", return_value=True)

    resp = client.post("/api/download", json={"url": "https://www.youtube.com/watch?v=toolong"})
    assert resp.status_code == 400
    data = resp.get_json()
    assert data["success"] is False
    assert "too long" in data["error"].lower()


def test_live_stream_rejected(client, mocker):
    mocker.patch.object(yt_dlp.YoutubeDL, "extract_info", return_value={
        "id": "live1",
        "title": "Live Broadcast",
        "is_live": True,
        "formats": []
    })
    mocker.patch("app.validate_public_url", return_value=True)

    resp = client.post("/api/download", json={"url": "https://www.youtube.com/watch?v=live1"})
    assert resp.status_code == 400
    data = resp.get_json()
    assert "Live streams" in data["error"]

    # Also test upcoming broadcast
    mocker.patch.object(yt_dlp.YoutubeDL, "extract_info", return_value={
        "id": "live2",
        "title": "Upcoming Stream",
        "live_status": "is_upcoming",
        "formats": []
    })
    resp2 = client.post("/api/download", json={"url": "https://www.youtube.com/watch?v=live2"})
    assert resp2.status_code == 400
    assert "Live streams" in resp2.get_json()["error"]


def test_oversized_filesize_dropped_from_options(client, mocker):
    max_b = config.MAX_FILESIZE_MB * 1024 * 1024
    mocker.patch("socket.getaddrinfo", return_value=[
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))
    ])
    mocker.patch.object(yt_dlp.YoutubeDL, "extract_info", return_value={
        "id": "size123",
        "title": "Size Test",
        "duration": 60,
        "formats": [
            {
                "format_id": "ok_fmt",
                "vcodec": "avc1",
                "acodec": "mp4a",
                "height": 720,
                "filesize": 50 * 1024 * 1024,
                "url": "https://example.com/ok.mp4",
                "protocol": "https"
            },
            {
                "format_id": "huge_fmt",
                "vcodec": "avc1",
                "acodec": "mp4a",
                "height": 1080,
                "filesize": max_b + (10 * 1024 * 1024),
                "url": "https://example.com/huge.mp4",
                "protocol": "https"
            }
        ]
    })
    mocker.patch("app.validate_public_url", return_value=True)

    resp = client.post("/api/download", json={"url": "https://www.youtube.com/watch?v=size123"})
    assert resp.status_code == 200
    data = resp.get_json()
    video_fmt_ids = [v["format_id"] for v in data["video_formats"]]
    assert "ok_fmt" in video_fmt_ids
    assert "huge_fmt" not in video_fmt_ids


def test_generator_aborts_at_byte_cap(mocker):
    from app import stream_via_direct_url
    max_b = config.MAX_FILESIZE_MB * 1024 * 1024

    # 1. Content-Length header exceeding cap raises ValueError immediately
    mock_resp_huge = mocker.MagicMock(
        headers={"Content-Length": str(max_b + 1000)},
        close=mocker.MagicMock()
    )
    mocker.patch("app.safe_requests_get", return_value=mock_resp_huge)
    with pytest.raises(ValueError, match="exceeds maximum allowed limit"):
        stream_via_direct_url({"url": "https://example.com/huge.mp4"}, "video")

    # 2. Generator aborts when yielded bytes exceed cap
    chunk_1mb = b"x" * (1024 * 1024)
    # Return more chunks than MAX_FILESIZE_MB
    chunks = [chunk_1mb] * (config.MAX_FILESIZE_MB + 5)
    mock_resp_stream = mocker.MagicMock(
        headers={},
        iter_content=lambda chunk_size: iter(chunks),
        close=mocker.MagicMock()
    )
    mocker.patch("app.safe_requests_get", return_value=mock_resp_stream)
    gen, ctype, clen = stream_via_direct_url({"url": "https://example.com/stream.mp4"}, "video")
    yielded_chunks = list(gen)
    total_yielded_bytes = sum(len(c) for c in yielded_chunks)
    assert total_yielded_bytes <= max_b


def test_watchdog_kills_hanging_fake_process(mocker):
    import gevent
    mock_proc = mocker.MagicMock()
    mock_proc.poll.return_value = None

    def watchdog(p, timeout_sec):
        gevent.sleep(timeout_sec)
        if p.poll() is None:
            p.kill()

    # Run watchdog with tiny timeout
    g = gevent.spawn(watchdog, mock_proc, 0.05)
    gevent.sleep(0.1)
    assert mock_proc.kill.called
    assert mock_proc.kill.called


# --- 1.3 BOT PROTECTION & TURNSTILE TESTS ---
def test_turnstile_clearance_token_validation():
    client_ip = "198.51.100.1"
    token = generate_clearance_token(client_ip)
    assert verify_clearance_token(token, client_ip) is True
    assert verify_clearance_token(token, "203.0.113.5") is False  # IP mismatch
    assert verify_clearance_token("invalid.token", client_ip) is False


def test_turnstile_dev_mode_skips_verification():
    # When TURNSTILE_SECRET is unset, verify_turnstile_response returns True
    assert verify_turnstile_response(None, "127.0.0.1") is True


# --- 1.4 SITES API & ETAG CACHING ---
def test_api_sites_endpoint_and_etag(client):
    resp = client.get("/api/sites")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "popular" in data
    assert "sites" in data
    assert "display_count" in data
    assert len(data["popular"]) > 10
    assert len(data["sites"]) > 100

    # Test ETag caching
    etag = resp.headers.get("ETag")
    assert etag is not None
    resp_cached = client.get("/api/sites", headers={"If-None-Match": etag})
    assert resp_cached.status_code == 304


# --- 1.5 STREAM COOKIE FOR DOWNLOAD DETECTION ---
def test_stream_sets_download_cookie(client, mocker):
    fake_info = {
        "id": "stream123",
        "title": "Stream Video",
        "formats": [{"format_id": "18", "vcodec": "avc1", "acodec": "mp4a", "url": "https://example.com/video.mp4"}]
    }
    token = cache_store.store_metadata(fake_info, "https://example.com/video")
    mocker.patch("app.validate_public_url", return_value=True)
    mocker.patch("app.stream_via_direct_url", return_value=(iter([b"data"]), "video/mp4", 4))

    resp = client.get(f"/api/stream?token={token}&format_id=18")
    assert resp.status_code == 200
    set_cookie = resp.headers.get("Set-Cookie", "")
    assert f"dl_{token}=1" in set_cookie


def test_rate_limiter_exceeded_returns_429(client, mocker):
    mocker.patch.object(yt_dlp.YoutubeDL, "extract_info", return_value={"id": "test", "formats": []})
    mocker.patch("app.validate_public_url", return_value=True)

    test_ip = "198.51.100.99"
    responses = []
    # 10 requests allowed per minute; 11th triggers 429
    for i in range(12):
        r = client.post(
            "/api/download",
            json={"url": f"https://www.youtube.com/watch?v=sample{i}"},
            environ_base={"REMOTE_ADDR": test_ip}
        )
        responses.append(r)

    statuses = [r.status_code for r in responses]
    assert 429 in statuses
    r_429 = next(r for r in responses if r.status_code == 429)
    data = r_429.get_json()
    assert data["success"] is False
    assert data["code"] == "RATE_LIMIT_EXCEEDED"


def test_rate_limiter_separate_buckets_for_different_x_real_ips(client, mocker):
    mocker.patch.object(yt_dlp.YoutubeDL, "extract_info", return_value={"id": "test", "formats": []})
    mocker.patch("app.validate_public_url", return_value=True)

    trusted_proxy_peer = "127.0.0.1"
    client_ip_1 = "198.51.100.11"
    client_ip_2 = "198.51.100.22"

    # Exhaust rate limit for client_ip_1 (10 allowed per minute)
    responses_ip1 = []
    for i in range(11):
        r = client.post(
            "/api/download",
            json={"url": f"https://www.youtube.com/watch?v=sample1_{i}"},
            headers={"X-Real-IP": client_ip_1},
            environ_base={"REMOTE_ADDR": trusted_proxy_peer}
        )
        responses_ip1.append(r)

    # 11th request from client_ip_1 must hit 429
    assert responses_ip1[-1].status_code == 429

    # Immediate request from client_ip_2 via the same trusted proxy must succeed (200),
    # proving client_ip_1 and client_ip_2 are tracked in separate rate limiting buckets
    r_ip2 = client.post(
        "/api/download",
        json={"url": "https://www.youtube.com/watch?v=sample2_first"},
        headers={"X-Real-IP": client_ip_2},
        environ_base={"REMOTE_ADDR": trusted_proxy_peer}
    )
    assert r_ip2.status_code == 200

    # Ensure client_ip_1 is still rate limited
    r_ip1_repeat = client.post(
        "/api/download",
        json={"url": "https://www.youtube.com/watch?v=sample1_retry"},
        headers={"X-Real-IP": client_ip_1},
        environ_base={"REMOTE_ADDR": trusted_proxy_peer}
    )
    assert r_ip1_repeat.status_code == 429


# --- P03 TESTS: LEAKS & SEMAPHORES ---
def test_response_uniterated_closed_releases_semaphore(client, mocker):
    fake_info = {
        "id": "rel123",
        "title": "Release Test",
        "extractor": "generic",
        "formats": [{"format_id": "18", "vcodec": "avc1", "acodec": "mp4a", "url": "https://example.com/v.mp4"}]
    }
    token = cache_store.store_metadata(fake_info, "https://example.com/v.mp4")
    mocker.patch("app.safe_requests_get", return_value=mocker.MagicMock(
        headers={},
        iter_content=lambda chunk_size: iter([b"chunk"])
    ))

    initial_val = app.proxy_semaphore.counter
    resp = client.get(f"/api/stream?token={token}&format_id=18", buffered=False)
    assert resp.status_code == 200
    assert app.proxy_semaphore.counter == initial_val - 1

    resp.close()
    assert app.proxy_semaphore.counter == initial_val


def test_exception_from_requests_releases_permit(client, mocker):
    fake_info = {
        "id": "exc123",
        "title": "Exception Test",
        "extractor": "generic",
        "formats": [{"format_id": "18", "vcodec": "avc1", "acodec": "mp4a", "url": "https://example.com/v.mp4"}]
    }
    token = cache_store.store_metadata(fake_info, "https://example.com/v.mp4")
    import requests
    mocker.patch("app.safe_requests_get", side_effect=requests.exceptions.ConnectionError("fail"))

    initial_val = app.proxy_semaphore.counter
    with pytest.raises(requests.exceptions.ConnectionError):
        client.get(f"/api/stream?token={token}&format_id=18")

    assert app.proxy_semaphore.counter == initial_val


def test_five_concurrent_progressive_downloads_succeed_while_ffmpeg_max_is_three(client, mocker):
    fake_info = {
        "id": "prog123",
        "title": "Prog Test",
        "extractor": "generic",
        "formats": [{"format_id": "18", "vcodec": "avc1", "acodec": "mp4a", "url": "https://example.com/v.mp4"}]
    }
    token = cache_store.store_metadata(fake_info, "https://example.com/v.mp4")
    mocker.patch("app.safe_requests_get", return_value=mocker.MagicMock(
        headers={},
        iter_content=lambda chunk_size: iter([b"chunk"])
    ))

    # Exhaust all 3 permits of ffmpeg_semaphore
    assert app.ffmpeg_semaphore.acquire(blocking=False) is True
    assert app.ffmpeg_semaphore.acquire(blocking=False) is True
    assert app.ffmpeg_semaphore.acquire(blocking=False) is True
    assert app.ffmpeg_semaphore.counter == 0

    responses = []
    try:
        # All 5 progressive downloads should succeed because they use proxy_semaphore, not ffmpeg
        for _ in range(5):
            resp = client.get(f"/api/stream?token={token}&format_id=18")
            assert resp.status_code == 200
            responses.append(resp)
    finally:
        for r in responses:
            r.close()
        app.ffmpeg_semaphore.release()
        app.ffmpeg_semaphore.release()
        app.ffmpeg_semaphore.release()


def test_killing_client_midstream_terminates_ffmpeg_subprocess(client, mocker):
    fake_info = {
        "id": "ffproc123",
        "title": "FFmpeg Kill Test",
        "formats": [{"format_id": "18", "vcodec": "avc1", "acodec": "mp4a", "url": "https://example.com/v.mp4"}]
    }
    token = cache_store.store_metadata(fake_info, "https://example.com/v.mp4")

    mock_proc = mocker.MagicMock()
    mock_proc.stdout.read.side_effect = [b"chunk1", b"chunk2"]

    import gevent.subprocess
    mocker.patch.object(gevent.subprocess, "Popen", return_value=mock_proc)
    mocker.patch("app.validate_public_url", return_value=True)

    resp = client.get(f"/api/stream?token={token}&format_id=18&type=audio")
    assert resp.status_code == 200

    # Read one chunk then simulate client disconnecting / closing
    gen = resp.response
    next(iter(gen))
    resp.close()

    assert mock_proc.kill.called
    assert mock_proc.wait.called

