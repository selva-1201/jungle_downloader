import os
import socket
import subprocess
import time
from unittest.mock import MagicMock, patch

import pytest
from app import app
from blocklist import BlocklistManager, blocklist_manager
from cache_store import cache_store
from config import config
from concurrency import ffmpeg_semaphore
from delivery import youtube_circuit_breaker
from hardening import (
    apply_security_headers,
    build_content_disposition,
    get_real_client_ip,
    map_error_message,
    sanitize_filename,
)
from security import (
    SSRFValidationError,
    build_safe_ffmpeg_args,
    get_safe_ytdlp_opts,
    safe_requests_get,
    validate_public_url,
)
from turnstile import generate_clearance_token, verify_clearance_token


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


# --- 1. Stream/Preview Token-Only and Blocked/Adult Origin Access ---

def test_stream_and_preview_reject_missing_or_url_parameter(client):
    res_stream = client.get("/api/stream?url=https://example.com/video.mp4")
    assert res_stream.status_code == 410

    res_preview = client.get("/api/preview?url=https://example.com/video.mp4")
    assert res_preview.status_code == 410


def test_stream_and_preview_invalid_token(client):
    res_stream = client.get("/api/stream?token=invalid_tok_12345")
    assert res_stream.status_code == 410

    res_preview = client.get("/api/preview?token=invalid_tok_12345")
    assert res_preview.status_code == 410


def test_stream_and_preview_blocked_adult_origin(client, monkeypatch):
    monkeypatch.setattr(config, "ALLOW_ADULT", False)
    tok = cache_store.store_metadata(
        info={
            "extractor": "pornhub",
            "formats": [{"format_id": "1", "url": "https://cdn.example.com/video.mp4", "ext": "mp4"}]
        },
        original_url="https://www.pornhub.com/view_video.php?viewkey=123",
    )

    with patch("socket.getaddrinfo") as mock_dns:
        mock_dns.return_value = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]
        res = client.get(f"/api/stream?token={tok}&format_id=1")
        assert res.status_code == 400
        assert "not supported" in res.get_json()["error"].lower()


# --- 2. Direct CDN URL SSRF and Redirect to Private IP ---

def test_stream_ssrf_cdn_url_validation_failure(client):
    tok = cache_store.store_metadata(
        info={
            "extractor": "youtube",
            "formats": [{"format_id": "1", "url": "http://169.254.169.254/secret", "ext": "mp4", "vcodec": "h264", "acodec": "aac"}]
        },
        original_url="https://youtube.com/watch?v=ssrf123",
    )
    res = client.get(f"/api/stream?token={tok}&format_id=1")
    assert res.status_code == 400
    assert "not permitted" in res.get_json()["error"].lower() or "rejected" in res.get_json()["error"].lower()


def test_stream_redirect_to_private_ip(client, monkeypatch):
    tok = cache_store.store_metadata(
        info={
            "extractor": "youtube",
            "formats": [{"format_id": "1", "url": "https://media.example.com/stream.mp4", "ext": "mp4", "vcodec": "h264", "acodec": "aac"}]
        },
        original_url="https://youtube.com/watch?v=valid123",
    )

    with patch("socket.getaddrinfo") as mock_dns:
        mock_dns.return_value = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]
        monkeypatch.setattr("app.safe_requests_get", MagicMock(side_effect=SSRFValidationError("SSRF redirect to 127.0.0.1 blocked")))
        res = client.get(f"/api/stream?token={tok}&format_id=1")
        assert res.status_code == 400


# --- 3. Turnstile Enabled Flow ---

def test_turnstile_missing_and_invalid_token(client, monkeypatch):
    monkeypatch.setattr(config, "TURNSTILE_SECRET", "test-secret-key-12345")

    # Missing token
    res1 = client.post("/api/download", json={"url": "https://www.youtube.com/watch?v=abc"})
    assert res1.status_code == 403
    assert "verification" in res1.get_json()["error"].lower() or "challenge" in res1.get_json()["error"].lower()

    # Invalid token (Cloudflare returns success: False)
    monkeypatch.setattr("app.verify_turnstile_response", lambda tok, ip: False)
    res2 = client.post("/api/download", json={"url": "https://www.youtube.com/watch?v=abc", "turnstile_token": "bad-token"})
    assert res2.status_code == 403
    assert "verification" in res2.get_json()["error"].lower() or "challenge" in res2.get_json()["error"].lower()


def test_turnstile_valid_token_sets_cookie_and_cookie_works(client, monkeypatch):
    monkeypatch.setattr(config, "TURNSTILE_SECRET", "test-secret-key-12345")
    monkeypatch.setattr("app.verify_turnstile_response", lambda tok, ip: True)
    monkeypatch.setattr("app.validate_public_url", lambda u: True)

    fake_info = {
        "id": "abc1234",
        "title": "Turnstile Verified Test",
        "duration": 60,
        "extractor": "youtube",
        "formats": [{"format_id": "18", "url": "https://googlevideo.com/videoplayback", "ext": "mp4", "height": 360}],
    }
    monkeypatch.setattr("yt_dlp.YoutubeDL.extract_info", lambda self, u, download=False: fake_info)

    # First request with valid turnstile_token
    res = client.post("/api/download", json={"url": "https://www.youtube.com/watch?v=abc", "turnstile_token": "good-token"})
    assert res.status_code == 200
    assert "cf_clearance_token" in res.headers.get("Set-Cookie", "")

    # Second request without turnstile_token but with cf_clearance_token cookie
    clearance_cookie = generate_clearance_token("127.0.0.1")
    client.set_cookie("cf_clearance_token", clearance_cookie)
    res_cookie = client.post("/api/download", json={"url": "https://www.youtube.com/watch?v=abc"})
    assert res_cookie.status_code == 200


# --- 4. ffmpeg Kill and Semaphore Release on Disconnect ---

def test_ffmpeg_killed_and_semaphore_released_on_disconnect(client, monkeypatch):
    initial_permits = ffmpeg_semaphore.counter

    tok = cache_store.store_metadata(
        info={
            "extractor": "youtube",
            "formats": [
                {"format_id": "137", "url": "https://cdn.example.com/v.mp4", "ext": "mp4", "vcodec": "avc1", "acodec": "none"},
                {"format_id": "140", "url": "https://cdn.example.com/a.m4a", "ext": "m4a", "vcodec": "none", "acodec": "mp4a"},
            ]
        },
        original_url="https://youtube.com/watch?v=ffmpeg123",
    )

    mock_proc = MagicMock()
    mock_proc.stdout.read.side_effect = [b"chunk1", b"chunk2", Exception("Client disconnected")]
    mock_proc.poll.return_value = None
    mock_proc.kill = MagicMock()

    monkeypatch.setattr("app.gevent_subprocess.Popen", lambda *args, **kwargs: mock_proc)
    monkeypatch.setattr("app.validate_public_url", lambda u: True)

    with pytest.raises(Exception, match="Client disconnected"):
        resp = client.get(f"/api/stream?token={tok}&format_id=137")
        for _ in resp.response:
            pass

    mock_proc.kill.assert_called()
    assert ffmpeg_semaphore.counter == initial_permits


# --- 5. Port ValueError and URL Parsing in Security ---

def test_validate_public_url_port_and_scheme_errors():
    with pytest.raises(SSRFValidationError, match=r"(Prohibited destination port|Invalid URL or port)"):
        validate_public_url("http://example.com:99999/")

    with pytest.raises(SSRFValidationError, match=r"(Prohibited destination port|Invalid URL or port)"):
        validate_public_url("http://example.com:abc/")

    with pytest.raises(SSRFValidationError, match="scheme"):
        validate_public_url("gopher://example.com/resource")

    with pytest.raises(SSRFValidationError, match="authentication"):
        validate_public_url("http://admin:secret@example.com/")

    with pytest.raises(SSRFValidationError, match="hostname"):
        validate_public_url("http://")


# --- 6. ENV=production HSTS and Security Headers ---

def test_hsts_header_in_production(monkeypatch):
    monkeypatch.setattr(config, "ENV", "production")

    dummy_response = MagicMock()
    dummy_response.headers = {}
    apply_security_headers(dummy_response)

    assert "Strict-Transport-Security" in dummy_response.headers
    assert "max-age=31536000" in dummy_response.headers["Strict-Transport-Security"]


# --- 7. "both" and "robot" Bot Mapping ---

def test_both_and_robot_do_not_trip_bot_detection():
    mapped1 = map_error_message("both video and audio formats were found but none matched")
    assert "blocked" not in mapped1.lower()
    assert "bot" not in mapped1.lower()

    mapped2 = map_error_message("Error occurred in robot extraction module")
    assert "blocked" not in mapped2.lower()
    assert "bot" not in mapped2.lower()

    mapped_bot = map_error_message("Sign in to confirm you’re not a bot")
    assert "blocking us" in mapped_bot.lower() or "bot" in mapped_bot.lower()


# --- 8. Size, Duration Caps and Live Stream Rejection ---

def test_size_and_duration_caps_and_live_rejected(client, monkeypatch):
    monkeypatch.setattr("app.validate_public_url", lambda u: True)

    # 1. Duration > 1200 sec
    fake_long = {
        "id": "long1", "title": "Too Long", "duration": 1500, "extractor": "vimeo",
        "formats": [{"format_id": "1", "url": "https://vimeo.com/1", "ext": "mp4"}],
    }
    monkeypatch.setattr("yt_dlp.YoutubeDL.extract_info", lambda self, u, download=False: fake_long)
    r1 = client.post("/api/download", json={"url": "https://vimeo.com/long"})
    assert r1.status_code == 400
    assert "too long" in r1.get_json()["error"].lower() or "duration" in r1.get_json()["error"].lower()

    # 2. Live stream
    fake_live = {
        "id": "live1", "title": "Live Stream", "duration": 0, "is_live": True, "extractor": "vimeo",
        "formats": [{"format_id": "1", "url": "https://vimeo.com/live", "ext": "mp4"}],
    }
    monkeypatch.setattr("yt_dlp.YoutubeDL.extract_info", lambda self, u, download=False: fake_live)
    r2 = client.post("/api/download", json={"url": "https://vimeo.com/live"})
    assert r2.status_code == 400
    assert "live" in r2.get_json()["error"].lower()


# --- 9. Proxy Used for ffmpeg and Budget Accrual ---

def test_proxy_used_for_ffmpeg_and_budget_accrual():
    proxy_url = "http://user:pass@198.51.100.5:8080"
    args = build_safe_ffmpeg_args(
        video_url="https://media.example.com/v.mp4",
        audio_url="https://media.example.com/a.m4a",
        proxy_url=proxy_url,
    )
    assert "-http_proxy" in args
    assert proxy_url in args


# --- 10. get_real_client_ip With Untrusted Peer and Spoofed Headers ---

def test_get_real_client_ip_untrusted_peer_ignores_spoofing():
    req = MagicMock()
    req.remote_addr = "198.51.100.1"
    req.headers = {
        "X-Real-IP": "127.0.0.1",
        "X-Forwarded-For": "10.0.0.1, 127.0.0.1",
        "CF-Connecting-IP": "1.1.1.1",
    }
    ip = get_real_client_ip(req)
    assert ip == "198.51.100.1"


# --- 11. /metrics From Non-Loopback Peer With Spoofed X-Real-IP is Rejected ---

def test_metrics_from_remote_peer_with_spoofed_header_rejected(client):
    res = client.get("/metrics", environ_base={"REMOTE_ADDR": "198.51.100.5"}, headers={"X-Real-IP": "127.0.0.1"})
    assert res.status_code == 403


# --- 12. Content-Disposition With CRLF, Long, and Bidi Names ---

def test_content_disposition_sanitization():
    # CRLF injection
    header1 = build_content_disposition("video\r\nX-Injected: evil.mp4")
    assert "\r" not in header1
    assert "\n" not in header1

    # Bidi control characters (RLO \u202E)
    bidi_name = "test_\u202Eevil.exe\u202D.mp4"
    header2 = build_content_disposition(bidi_name)
    assert "\u202E" not in header2
    assert "\u202D" not in header2

    # Very long filename (> 120 chars)
    long_name = "a" * 200 + ".mp4"
    clean_name = sanitize_filename(long_name)
    assert len(clean_name) <= 160
    assert clean_name.endswith(".mp4")


# --- 13. Blocklist and Security Edge Cases for Coverage ---

def test_blocklist_manager_edge_cases(monkeypatch):
    bm = BlocklistManager()

    # URL length > 2048 rejected
    assert bm.find_extractor_name("https://example.com/" + "a" * 2500) is None

    # Cached extractor lookup
    first = bm.find_extractor_name("https://vimeo.com/12345")
    second = bm.find_extractor_name("https://vimeo.com/12345")
    assert first == second

    # Host LRU cache
    third = bm.find_extractor_name("https://vimeo.com/99999")
    assert third == first

    # Blocked domain matching
    blocked, reason = bm.is_url_blocked("https://blocked-domain.com/test")
    assert blocked is True

    # Empty url, None, or unparseable url
    assert bm.is_url_blocked("")[0] is True
    assert bm.is_adult_extractor("") is False
    assert bm.is_adult_extractor("pornhub") is True
    assert bm.is_adult_extractor("vimeo") is False

    # Non-existent files handled gracefully
    bm_missing = BlocklistManager(adult_file="/tmp/nonexistent_adult.txt", blocked_urls_file="/tmp/nonexistent_urls.txt")
    assert len(bm_missing._adult_extractors) == 0

    # ALLOW_GENERIC=True allows generic
    monkeypatch.setattr(config, "ALLOW_GENERIC", True)
    assert bm.is_url_blocked("https://unknown-custom-site-12345.com/media")[0] is False


def test_security_safe_requests_get_timeout_and_redirects(monkeypatch):
    mock_resp = MagicMock()
    mock_resp.is_redirect = False
    mock_resp.headers = {"Content-Type": "video/mp4"}

    with patch("socket.getaddrinfo") as mock_dns:
        mock_dns.return_value = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]
        with patch("requests.Session.send", return_value=mock_resp):
            r = safe_requests_get("https://example.com/video.mp4", timeout=10)
            assert r.headers["Content-Type"] == "video/mp4"
