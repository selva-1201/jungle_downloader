import socket
from unittest.mock import patch
from app import app
from cache_store import cache_store
import delivery


def test_conftest_resets_only_real_attributes():
    with open("tests/conftest.py", "r", encoding="utf-8") as f:
        conftest_code = f.read()

    # Must NOT assign invented attributes
    assert ".failures =" not in conftest_code
    assert ".last_failure_time =" not in conftest_code
    assert ".daily_bytes_used =" not in conftest_code

    # Real attributes and methods must exist on objects
    cb = delivery.youtube_circuit_breaker
    assert hasattr(cb, "reset")
    assert callable(cb.reset)
    assert hasattr(cb, "consecutive_failures")
    assert hasattr(cb, "state")
    assert hasattr(cb, "tripped_at")

    pr = delivery.proxy_router
    assert hasattr(pr, "proxied_bytes_today")
    assert hasattr(pr, "extractor_stats")
    assert hasattr(pr, "site_backoffs")


def test_oversized_info_keeps_best_formats():
    # Construct 60 formats with ascending heights (100 to 1080)
    # Adding fragments ensures serialized payload exceeds MAX_PAYLOAD_BYTES (512KB)
    formats = []
    for i in range(1, 61):
        height = i * 18
        formats.append({
            "format_id": f"fmt_{i}",
            "ext": "mp4",
            "vcodec": "avc1",
            "acodec": "mp4a",
            "height": height,
            "url": f"https://example.com/video_{i}.mp4",
            "fragments": [{"url": f"https://example.com/frag_{j}.mp4"} for j in range(300)],
        })

    raw_info = {
        "id": "vid_oversized",
        "title": "Oversized Format List Video",
        "extractor": "generic",
        "formats": formats,
    }

    token = cache_store.store_metadata(raw_info, original_url="https://example.com/watch?v=oversized")
    cached = cache_store.get_metadata(token)

    stored_formats = cached["info"]["formats"]
    assert len(stored_formats) == 35

    # Should keep the BEST 35 (highest height, tail of the list)
    # Formats 26 to 60 should be kept, formats 1 to 25 dropped
    stored_ids = {f["format_id"] for f in stored_formats}
    assert "fmt_60" in stored_ids
    assert "fmt_50" in stored_ids
    assert "fmt_26" in stored_ids
    assert "fmt_1" not in stored_ids
    assert "fmt_10" not in stored_ids


def test_every_offered_format_id_resolves_without_404():
    # 50 formats (oversized, >512KB via fragments list)
    formats = []
    for i in range(1, 51):
        height = i * 20
        formats.append({
            "format_id": f"fmt_{i}",
            "ext": "mp4",
            "vcodec": "avc1",
            "acodec": "mp4a",
            "height": height,
            "url": f"https://www.youtube.com/video_{i}.mp4",
            "fragments": [{"url": "https://www.youtube.com/f.mp4"}] * 350,
        })

    mock_info = {
        "id": "vid_test50",
        "title": "Test 50 Formats",
        "extractor": "youtube",
        "formats": formats,
    }

    client = app.test_client()

    fake_dns = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]

    with patch("socket.getaddrinfo", return_value=fake_dns), \
         patch("app.validate_public_url", return_value=(True, "www.youtube.com", "93.184.216.34")), \
         patch("app.fetch_fresh_metadata", return_value=mock_info):

        res = client.post("/api/download", json={"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"})
        assert res.status_code == 200
        data = res.get_json()
        assert data["success"] is True

        token = data["token"]
        video_fmts = data.get("video_formats", [])
        audio_fmts = data.get("audio_formats", [])

        all_offered_ids = [
            f["format_id"]
            for f in video_fmts + audio_fmts
            if f.get("format_id") and f["format_id"] != "auto_extract"
        ]

        assert len(all_offered_ids) > 0

        # Every offered format_id must resolve in /api/stream (probe=1 returns 204, NEVER 404)
        for idx, fmt_id in enumerate(all_offered_ids, start=1):
            res_stream = client.get(
                f"/api/stream?token={token}&format_id={fmt_id}&probe=1",
                headers={"X-Real-IP": f"198.51.100.{idx}"}
            )
            assert res_stream.status_code == 204, f"Offered format {fmt_id} failed to resolve: {res_stream.get_json()}"
