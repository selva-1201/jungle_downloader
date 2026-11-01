from unittest.mock import patch
import gevent
from app import app
from concurrency import extract_semaphore
from security import validate_extracted_urls
import concurrency
import delivery


def test_strict_json_input_validation():
    client = app.test_client()

    # Body is not an object (e.g. array)
    resp = client.post("/api/download", json=[])
    assert resp.status_code == 400
    assert resp.get_json()["success"] is False

    # url is not a string (e.g. integer)
    resp = client.post("/api/download", json={"url": 123})
    assert resp.status_code == 400
    assert resp.get_json()["success"] is False

    # url is empty
    resp = client.post("/api/download", json={"url": "   "})
    assert resp.status_code == 400
    assert resp.get_json()["success"] is False


def test_slow_extractor_times_out_and_releases_permits(monkeypatch):
    from config import config
    monkeypatch.setattr(config, "EXTRACT_TIMEOUT_SEC", 0.05)
    client = app.test_client()

    def slow_fetch(url, proxy_url=None):
        gevent.sleep(0.2)
        return {"id": "slow", "formats": []}

    initial_global = concurrency.extract_semaphore._value if hasattr(concurrency.extract_semaphore, "_value") else 6
    initial_yt = delivery.youtube_extract_semaphore._value if hasattr(delivery.youtube_extract_semaphore, "_value") else 2

    with patch("app.validate_public_url"), patch("app.fetch_fresh_metadata", side_effect=slow_fetch):
        resp = client.post("/api/download", json={"url": "https://www.youtube.com/watch?v=slow123"})
        assert resp.status_code == 400
        data = resp.get_json()
        assert data["success"] is False
        assert "could not process this link" in data["error"].lower()

    # Permits must be released back
    if hasattr(concurrency.extract_semaphore, "_value"):
        assert concurrency.extract_semaphore._value == initial_global
    if hasattr(delivery.youtube_extract_semaphore, "_value"):
        assert delivery.youtube_extract_semaphore._value == initial_yt


def test_validate_extracted_urls_dns_memoization():
    fragments = [{"url": f"https://cdn.example.com/segment_{i}.ts"} for i in range(300)]
    info = {
        "formats": [
            {
                "format_id": "frag_stream",
                "url": "https://cdn.example.com/playlist.m3u8",
                "fragments": fragments,
            }
        ]
    }

    lookup_count = 0
    import socket

    def mock_getaddrinfo(host, port, *args, **kwargs):
        nonlocal lookup_count
        if host == "cdn.example.com":
            lookup_count += 1
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]

    with patch("socket.getaddrinfo", side_effect=mock_getaddrinfo):
        res = validate_extracted_urls(info)
        assert len(res["formats"]) == 1
        # Exactly 1 DNS lookup for cdn.example.com despite 300 fragments + 1 main url
        assert lookup_count == 1
