import json
import time
import pytest
from app import app
from blocklist import blocklist_manager, MAX_URL_LENGTH
from cache_store import CacheStore, trim_info


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


def test_trimmed_info_removes_bloat_and_supports_flow():
    huge_info = {
        "id": "vid_abc123",
        "title": "Sample Title",
        "thumbnail": "https://example.com/thumb.jpg",
        "duration": 180,
        "extractor": "youtube",
        "webpage_url": "https://example.com/watch",
        "description": "Very long description " * 1000,
        "subtitles": {"en": [{"url": "https://example.com/sub.vtt"}]},
        "automatic_captions": {"en": [{"url": "https://example.com/auto.vtt"}]},
        "comments": [{"text": "great video"} for _ in range(500)],
        "formats": [
            {
                "format_id": "18",
                "url": "https://example.com/stream.mp4",
                "protocol": "https",
                "vcodec": "avc1",
                "acodec": "mp4a",
                "ext": "mp4",
                "height": 360,
                "http_headers": {"User-Agent": "custom-agent"},
                "extra_useless_field": "drop_me"
            }
        ]
    }

    trimmed = trim_info(huge_info)
    assert "description" not in trimmed
    assert "subtitles" not in trimmed
    assert "automatic_captions" not in trimmed
    assert "comments" not in trimmed
    assert trimmed["id"] == "vid_abc123"
    assert len(trimmed["formats"]) == 1
    assert "extra_useless_field" not in trimmed["formats"][0]
    assert trimmed["formats"][0]["format_id"] == "18"


def test_secrets_headers_absent_from_redis_payload(monkeypatch):
    class FakeRedis:
        def __init__(self):
            self.stored = {}

        def ping(self):
            return True

        def setex(self, name, time_val, value):
            self.stored[name] = value

        def get(self, name):
            return self.stored.get(name)

    fake_redis = FakeRedis()
    store = CacheStore(maxsize=100, ttl=300)
    store._redis_client = fake_redis

    info_with_secrets = {
        "id": "sec_test",
        "title": "Secret Test",
        "extractor": "vimeo",
        "formats": [
            {
                "format_id": "22",
                "url": "https://example.com/secret.mp4",
                "ext": "mp4",
                "http_headers": {
                    "Cookie": "session_id=SECRET_COOKIE_DATA",
                    "cookie": "lowercase_secret=1",
                    "Authorization": "Bearer SECRET_AUTH_TOKEN",
                    "authorization": "Basic SECRET_CREDS",
                    "User-Agent": "AllowedAgent/1.0"
                }
            }
        ]
    }

    token = store.store_metadata(info_with_secrets, "https://example.com/video")
    redis_key = f"jungle:media:{token}"
    assert redis_key in fake_redis.stored

    redis_payload_raw = fake_redis.stored[redis_key]
    assert "SECRET_COOKIE_DATA" not in redis_payload_raw
    assert "SECRET_AUTH_TOKEN" not in redis_payload_raw
    assert "SECRET_CREDS" not in redis_payload_raw

    redis_data = json.loads(redis_payload_raw)
    headers = redis_data["info"]["formats"][0]["http_headers"]
    assert "Cookie" not in headers
    assert "cookie" not in headers
    assert "Authorization" not in headers
    assert "authorization" not in headers
    assert headers.get("User-Agent") == "AllowedAgent/1.0"


def test_long_malicious_url_rejected_quickly():
    long_url = "https://example.com/" + ("a" * 3000)
    assert len(long_url) > MAX_URL_LENGTH

    t0 = time.time()
    blocked, reason = blocklist_manager.is_url_blocked(long_url)
    elapsed = time.time() - t0

    assert blocked is True
    assert "maximum permitted length" in reason
    assert elapsed < 0.01  # Instant rejection without scanning extractors


def test_extractor_lookup_cached_per_hostname_and_url():
    test_url = "https://vimeo.com/76979871"

    # First call primes cache
    ie1 = blocklist_manager.find_extractor_name(test_url)
    assert ie1 is not None

    # Hostname cache must contain vimeo.com
    assert "vimeo.com" in blocklist_manager._host_extractor_cache

    # Exact URL lookup hits _url_extractor_cache instantly
    t0 = time.time()
    for _ in range(100):
        ie_cached = blocklist_manager.find_extractor_name(test_url)
        assert ie_cached == ie1
    elapsed = time.time() - t0
    assert elapsed < 0.05

    # Different URLs on same host hit _host_extractor_cache
    t1 = time.time()
    for i in range(10):
        ie_host = blocklist_manager.find_extractor_name(f"https://vimeo.com/{1000 + i}")
        assert ie_host == ie1
    elapsed_host = time.time() - t1
    assert elapsed_host < 0.5
