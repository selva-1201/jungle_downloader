from unittest.mock import patch
from app import app
import concurrency
from cache_store import cache_store


def test_preview_410_returns_session_expired():
    client = app.test_client()
    resp = client.get("/api/preview?token=non_existent_token_123")
    assert resp.status_code == 410
    data = resp.get_json()
    assert data["success"] is False
    assert data["code"] == "SESSION_EXPIRED"


def test_probe_validates_without_upstream_or_semaphore():
    client = app.test_client()
    dummy_info = {
        "id": "probe123",
        "title": "Probe Video",
        "extractor": "youtube",
        "formats": [
            {
                "format_id": "18",
                "ext": "mp4",
                "vcodec": "avc1",
                "acodec": "mp4a",
                "url": "https://example.com/video.mp4",
            }
        ],
    }
    token = cache_store.store_metadata(dummy_info, original_url="https://example.com/watch?v=123")

    with patch("app.safe_requests_get") as mock_get, patch("subprocess.Popen") as mock_popen:
        resp = client.get(f"/api/stream?token={token}&format_id=18&probe=1")
        assert resp.status_code == 204
        assert resp.data == b""
        # Ensure no upstream request or subprocess was spawned
        mock_get.assert_not_called()
        mock_popen.assert_not_called()


def test_probe_returns_410_on_expired_token():
    client = app.test_client()
    resp = client.get("/api/stream?token=non_existent_token_456&format_id=18&probe=1")
    assert resp.status_code == 410
    data = resp.get_json()
    assert data["code"] == "SESSION_EXPIRED"


def test_preview_ffmpeg_semaphore_exhaustion():
    client = app.test_client()
    dummy_info = {
        "id": "preview_ffmpeg",
        "title": "FFmpeg Preview Video",
        "extractor": "generic_site",
        "formats": [
            {
                "format_id": "137",
                "ext": "mp4",
                "vcodec": "avc1",
                "acodec": "none",
                "height": 480,
                "url": "https://example.com/video_only.mp4",
            },
            {
                "format_id": "140",
                "ext": "m4a",
                "vcodec": "none",
                "acodec": "mp4a",
                "url": "https://example.com/audio_only.m4a",
            },
        ],
    }
    token = cache_store.store_metadata(dummy_info, original_url="https://example.com/watch?v=123")

    # Acquire all permits from concurrency.ffmpeg_semaphore
    acquired_permits = []
    while True:
        got = concurrency.ffmpeg_semaphore.acquire(blocking=False)
        if not got:
            break
        acquired_permits.append(got)

    try:
        # Request should fail with 503 and Retry-After
        resp = client.get(f"/api/preview?token={token}&format_id=137")
        assert resp.status_code == 503
        assert resp.headers.get("Retry-After") == "30"
        data = resp.get_json()
        assert data["success"] is False
        assert "busy" in data["error"].lower()
    finally:
        for _ in acquired_permits:
            concurrency.ffmpeg_semaphore.release()


def test_preview_ffmpeg_permit_released_on_close():
    client = app.test_client()
    dummy_info = {
        "id": "preview_ffmpeg_rel",
        "title": "FFmpeg Preview Video Release",
        "extractor": "generic_site",
        "formats": [
            {
                "format_id": "137",
                "ext": "mp4",
                "vcodec": "avc1",
                "acodec": "none",
                "height": 480,
                "url": "https://example.com/video_only.mp4",
            },
            {
                "format_id": "140",
                "ext": "m4a",
                "vcodec": "none",
                "acodec": "mp4a",
                "url": "https://example.com/audio_only.m4a",
            },
        ],
    }
    token = cache_store.store_metadata(dummy_info, original_url="https://example.com/watch?v=123")

    def dummy_gen():
        yield b"preview-data"

    initial_permits = concurrency.ffmpeg_semaphore._value if hasattr(concurrency.ffmpeg_semaphore, "_value") else 3

    with patch("app.stream_universal_ffmpeg", return_value=(dummy_gen(), "video/mp4")):
        resp = client.get(f"/api/preview?token={token}&format_id=137")
        assert resp.status_code == 200
        assert resp.headers["Content-Type"] == "video/mp4"
        # Consume response to trigger close
        _ = resp.data
        resp.close()
        if hasattr(concurrency.ffmpeg_semaphore, "_value"):
            assert concurrency.ffmpeg_semaphore._value == initial_permits
