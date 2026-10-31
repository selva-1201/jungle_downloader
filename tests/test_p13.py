import pytest
import requests
from app import app
from cache_store import cache_store
from delivery import YouTubeCircuitBreaker
from hardening import map_error_message


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


def test_both_or_robot_does_not_trip_circuit_breaker():
    cb = YouTubeCircuitBreaker(failure_threshold=2, cooldown_seconds=60)

    # Errors containing "both" or "robot" must NOT increment bot failure counter
    cb.record_failure("Downloaded both video and audio streams successfully")
    assert cb.state == cb.STATE_CLOSED
    assert cb.consecutive_failures == 0

    cb.record_failure("robot test automation suite completed")
    assert cb.state == cb.STATE_CLOSED
    assert cb.consecutive_failures == 0

    # Genuine bot failure should increment
    cb.record_failure("Sign in to confirm you're not a bot")
    assert cb.consecutive_failures == 1

    cb.record_failure("HTTP Error 429: Too Many Requests")
    assert cb.consecutive_failures == 2
    assert cb.state == cb.STATE_OPEN


def test_map_error_message_handles_upstream_and_bot_errors():
    # "both" does not trigger "Site temporarily blocking us"
    msg_both = map_error_message(Exception("Downloaded both audio and video streams"))
    assert "blocking us" not in msg_both

    # Upstream 403 / 404 mapping
    resp_403 = requests.Response()
    resp_403.status_code = 403
    err_403 = requests.HTTPError("403 Client Error: Forbidden", response=resp_403)
    msg_403 = map_error_message(err_403)
    assert "forbidden" in msg_403.lower() or "restricted" in msg_403.lower()

    resp_404 = requests.Response()
    resp_404.status_code = 404
    err_404 = requests.HTTPError("404 Client Error: Not Found", response=resp_404)
    msg_404 = map_error_message(err_404)
    assert "not found" in msg_404.lower()


def test_unknown_format_id_returns_404(client):
    info = {
        "id": "vid123",
        "title": "Test Video",
        "extractor": "generic",
        "formats": [
            {"format_id": "18", "vcodec": "avc1", "acodec": "mp4a", "ext": "mp4", "url": "https://example.com/v.mp4"}
        ]
    }
    token = cache_store.store_metadata(info, "https://example.com/v.mp4")

    # Unknown format_id must return 404 instead of silent fallback
    resp_stream = client.get(f"/api/stream?token={token}&format_id=unknown_999")
    assert resp_stream.status_code == 404
    data_stream = resp_stream.get_json()
    assert "not found" in data_stream["error"].lower()

    resp_preview = client.get(f"/api/preview?token={token}&format_id=unknown_999")
    assert resp_preview.status_code == 404
    data_preview = resp_preview.get_json()
    assert "not found" in data_preview["error"].lower()


def test_invalid_type_param_rejected(client):
    info = {
        "id": "vid123",
        "title": "Test Video",
        "extractor": "generic",
        "formats": [
            {"format_id": "18", "vcodec": "avc1", "acodec": "mp4a", "ext": "mp4", "url": "https://example.com/v.mp4"}
        ]
    }
    token = cache_store.store_metadata(info, "https://example.com/v.mp4")

    resp_stream = client.get(f"/api/stream?token={token}&format_id=18&type=malware")
    assert resp_stream.status_code == 400
    assert "Invalid media type" in resp_stream.get_json()["error"]

    resp_preview = client.get(f"/api/preview?token={token}&format_id=18&type=exe")
    assert resp_preview.status_code == 400
    assert "Invalid media type" in resp_preview.get_json()["error"]


def test_content_type_sniff_protection_and_extension(client, monkeypatch):
    info = {
        "id": "webm123",
        "title": "WebM Video",
        "extractor": "generic",
        "formats": [
            {
                "format_id": "247",
                "vcodec": "vp9",
                "acodec": "opus",
                "ext": "webm",
                "url": "https://example.com/video.webm",
                "http_headers": {}
            }
        ]
    }
    token = cache_store.store_metadata(info, "https://example.com/video.webm")

    # Mock safe_requests_get for stream pass-through
    import io
    fake_response = requests.Response()
    fake_response.status_code = 200
    fake_response.headers["Content-Type"] = "video/webm"
    fake_response.headers["Content-Length"] = "1024"
    fake_response.raw = io.BytesIO(b"fake_webm_bytes")

    def fake_get(*args, **kwargs):
        # Verify Accept-Encoding: identity is sent
        headers = kwargs.get("headers", {})
        assert headers.get("Accept-Encoding") == "identity"
        return fake_response

    monkeypatch.setattr("app.safe_requests_get", fake_get)

    resp = client.get(f"/api/stream?token={token}&format_id=247&type=video&filename=my_clip")
    assert resp.status_code == 200
    assert resp.headers["Content-Type"] == "video/webm"
    assert "my_clip.webm" in resp.headers["Content-Disposition"]
