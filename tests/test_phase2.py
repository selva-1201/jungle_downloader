import time
import pytest
import yt_dlp
from app import app
from cache_store import cache_store
from config import config
from delivery import (
    is_direct_cdn_eligible,
    youtube_circuit_breaker,
    youtube_extract_semaphore,
    proxy_router,
)
from sites_catalog import sites_catalog, prettify_site_name


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


@pytest.fixture(autouse=True)
def reset_circuit_breaker():
    youtube_circuit_breaker.reset()
    yield
    youtube_circuit_breaker.reset()


# ============================================================
# 1. HYBRID DELIVERY TESTS (SPEC 5.1)
# ============================================================
def test_direct_cdn_eligible_evaluation():
    # Progressive format with audio and video
    sample_progressive = {
        "format_id": "18",
        "vcodec": "avc1.42001E",
        "acodec": "mp4a.40.2",
        "protocol": "https",
        "url": "https://cdn.example.org/video.mp4",
        "filesize": 400 * 1024 * 1024,  # 400 MB
    }

    # 1. Large file with large_only mode (> 300MB) -> True
    assert is_direct_cdn_eligible(sample_progressive, "vimeo", direct_link_mode="large_only", proxy_max_mb=300) is True

    # 2. Small file with large_only mode (< 300MB) -> False (proxy stream)
    small_file = dict(sample_progressive, filesize=50 * 1024 * 1024)
    assert is_direct_cdn_eligible(small_file, "vimeo", direct_link_mode="large_only", proxy_max_mb=300) is False

    # 3. DIRECT_LINK_MODE == "off" -> always False
    assert is_direct_cdn_eligible(sample_progressive, "vimeo", direct_link_mode="off") is False

    # 4. DIRECT_LINK_MODE == "always" -> True even for small file
    assert is_direct_cdn_eligible(small_file, "vimeo", direct_link_mode="always") is True

    # 5. IP-Bound YouTube must NEVER be direct CDN link
    assert is_direct_cdn_eligible(sample_progressive, "youtube", direct_link_mode="always") is False
    yt_url_fmt = dict(sample_progressive, url="https://rr3---sn-4g5ednks.googlevideo.com/videoplayback")
    assert is_direct_cdn_eligible(yt_url_fmt, "generic", direct_link_mode="always") is False

    # 6. Fragmented streams (HLS m3u8 / DASH mpd) cannot be direct progressive
    hls_fmt = dict(sample_progressive, protocol="m3u8_native", url="https://cdn.example.org/master.m3u8")
    assert is_direct_cdn_eligible(hls_fmt, "vimeo", direct_link_mode="always") is False

    # 7. Video-only or Audio-only format cannot be direct progressive
    video_only = dict(sample_progressive, acodec="none")
    assert is_direct_cdn_eligible(video_only, "vimeo", direct_link_mode="always") is False

    # 8. Special authorization or cookie headers required -> cannot be direct CDN link
    auth_fmt = dict(sample_progressive, http_headers={"Authorization": "Bearer token123"})
    assert is_direct_cdn_eligible(auth_fmt, "vimeo", direct_link_mode="always") is False


def test_stream_redirects_302_for_direct_cdn_eligible(client, mocker):
    large_format = {
        "format_id": "direct_large",
        "vcodec": "avc1",
        "acodec": "mp4a",
        "protocol": "https",
        "url": "https://cdn.testsite.org/media/large_video.mp4",
        "filesize": 450 * 1024 * 1024,
    }
    fake_info = {
        "id": "direct123",
        "title": "Large Direct Clip",
        "extractor": "vimeo",
        "formats": [large_format]
    }
    token = cache_store.store_metadata(fake_info, "https://vimeo.com/123456")
    mocker.patch("app.validate_public_url", return_value=True)

    resp = client.get(f"/api/stream?token={token}&format_id=direct_large")
    assert resp.status_code == 302
    assert resp.headers.get("Location") == "https://cdn.testsite.org/media/large_video.mp4"
    assert f"dl_{token}=1" in resp.headers.get("Set-Cookie", "")


# ============================================================
# 2. YOUTUBE CIRCUIT BREAKER TESTS (SPEC 5.2)
# ============================================================
def test_youtube_circuit_breaker_state_transitions():
    cb = youtube_circuit_breaker
    cb.reset()

    # Initial state CLOSED
    avail, _ = cb.is_available()
    assert avail is True
    assert cb.state == "CLOSED"

    # Non-bot errors do not trip the circuit breaker
    cb.record_failure("Video deleted by uploader")
    assert cb.state == "CLOSED"
    assert cb.consecutive_failures == 0

    # Bot errors increment failures
    cb.record_failure("HTTP Error 429: Too Many Requests")
    assert cb.consecutive_failures == 1
    assert cb.state == "CLOSED"

    cb.record_failure("Sign in to confirm you're not a bot")
    assert cb.consecutive_failures == 2
    assert cb.state == "CLOSED"

    # Third consecutive failure trips to OPEN
    cb.record_failure("YouTube captcha required")
    assert cb.state == "OPEN"
    avail, msg = cb.is_available()
    assert avail is False
    assert "temporarily unavailable, other sites work" in msg

    # Cooldown transition to HALF-OPEN
    cb.tripped_at = time.time() - 305  # simulate > 300s elapsed
    avail_after_cooldown, _ = cb.is_available()
    assert avail_after_cooldown is True
    assert cb.state == "HALF_OPEN"

    # Successful probe resets to CLOSED
    cb.record_success()
    assert cb.state == "CLOSED"
    assert cb.consecutive_failures == 0


def test_youtube_circuit_breaker_fast_fails_youtube_while_allowing_others(client, mocker):
    mocker.patch("app.validate_public_url", return_value=True)

    # Trip YouTube Circuit Breaker
    youtube_circuit_breaker.state = "OPEN"
    youtube_circuit_breaker.tripped_at = time.time()

    # 1. YouTube request fast-fails with 503 and friendly message
    resp_yt = client.post("/api/download", json={"url": "https://www.youtube.com/watch?v=sample"})
    assert resp_yt.status_code == 503
    data_yt = resp_yt.get_json()
    assert "temporarily unavailable, other sites work" in data_yt["error"]

    # 2. Non-YouTube platforms (e.g. SoundCloud / Vimeo) remain accessible!
    mock_soundcloud_info = {
        "id": "sc123",
        "title": "SoundCloud Track",
        "extractor": "soundcloud",
        "formats": [{"format_id": "mp3", "vcodec": "none", "acodec": "mp3", "url": "https://example.com/audio.mp3"}]
    }
    mocker.patch.object(yt_dlp.YoutubeDL, "extract_info", return_value=mock_soundcloud_info)

    resp_sc = client.post("/api/download", json={"url": "https://soundcloud.com/user/track"})
    assert resp_sc.status_code == 200
    data_sc = resp_sc.get_json()
    assert data_sc["success"] is True
    assert data_sc["title"] == "SoundCloud Track"


def test_youtube_dedicated_semaphore_saturation_returns_503(client, mocker):
    mocker.patch("app.validate_public_url", return_value=True)
    mock_sem = mocker.patch("app.youtube_extract_semaphore")
    mock_sem.acquire.return_value = False

    resp = client.post("/api/download", json={"url": "https://www.youtube.com/watch?v=busy_yt"})
    assert resp.status_code == 503
    assert resp.headers.get("Retry-After") == "30"
    data = resp.get_json()
    assert "Server busy processing YouTube requests" in data["error"]


# ============================================================
# 3. PROXY ROUTER & DAILY BUDGET TESTS (SPEC 5.2, 5.3)
# ============================================================
def test_proxy_router_routing_and_daily_budget(mocker):
    router = proxy_router
    router.proxied_bytes_today = 0
    mocker.patch.object(config, "PROXY_RULES", {"Youtube": "http://user:pass@ytproxy:8080"})
    mocker.patch.object(config, "YTDLP_PROXY", "http://globalproxy:8080")
    mocker.patch.object(config, "PROXIED_DAILY_BYTE_BUDGET_GB", 1)  # 1 GB cap

    # 1. YouTube matches per-extractor proxy rule
    assert router.get_proxy_for_extractor("youtube") == "http://user:pass@ytproxy:8080"
    assert router.get_proxy_for_extractor("YouTube") == "http://user:pass@ytproxy:8080"

    # 2. Other extractor falls back to global proxy
    assert router.get_proxy_for_extractor("tiktok") == "http://globalproxy:8080"

    # 3. Record bandwidth below budget
    router.record_proxied_bytes(500 * 1024 * 1024)  # 500 MB
    assert router.get_proxy_for_extractor("youtube") == "http://user:pass@ytproxy:8080"

    # 4. Exceeding 1GB daily budget gracefully degrades to direct (returns None)
    router.record_proxied_bytes(600 * 1024 * 1024)  # Total 1.1 GB > 1 GB cap
    assert router.get_proxy_for_extractor("youtube") is None


def test_egress_hygiene_metrics_and_429_spacing():
    router = proxy_router
    router.extractor_stats.clear()
    router.site_backoffs.clear()

    # Track requests & results
    router.record_request("instagram")
    router.record_result("instagram", success=False, error="HTTP Error 429: Too Many Requests")

    # Verify throttled for 60 seconds
    assert router.is_site_throttled("instagram") is True
    assert router.is_site_throttled("youtube") is False

    snapshot = router.get_metrics_snapshot()
    assert "instagram" in snapshot["extractors"]
    assert snapshot["extractors"]["instagram"]["failures"] == 1


def test_metrics_endpoint_access_control(client):
    # Allowed on localhost (127.0.0.1)
    resp_local = client.get("/metrics", environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert resp_local.status_code == 200
    data = resp_local.get_json()
    assert "extractors" in data
    assert "proxied_bytes_today" in data

    # Forbidden for external clients
    resp_remote = client.get("/metrics", environ_base={"REMOTE_ADDR": "203.0.113.195"})
    assert resp_remote.status_code == 403


# ============================================================
# 4. SUPPORTED SITES CATALOG & ROUTE TESTS (SPEC 5.4)
# ============================================================
def test_supported_sites_catalog_generation():
    cat = sites_catalog.catalog
    assert "popular" in cat
    assert "sites" in cat
    assert "total_count" in cat
    assert "display_count" in cat

    assert len(cat["popular"]) == 15
    assert cat["total_count"] > 1000
    assert cat["display_count"].endswith("+ sites")

    # Generic and adult extractors must be absent
    lower_sites = [s.lower() for s in cat["sites"]]
    assert "generic" not in lower_sites
    assert "pornhub" not in lower_sites
    assert "xvideos" not in lower_sites


def test_supported_sites_html_page(client):
    resp = client.get("/supported-sites")
    assert resp.status_code == 200
    assert "Supported Websites" in resp.text
    assert "Popular Platforms" in resp.text
    assert sites_catalog.catalog["display_count"] in resp.text


def test_site_name_prettifier():
    assert prettify_site_name("youtube") == "YouTube"
    assert prettify_site_name("tiktok") == "TikTok"
    assert prettify_site_name("instagram") == "Instagram"
    assert prettify_site_name("twitter") == "Twitter / X"
    assert prettify_site_name("abc.net.au") == "abc.net.au"
    assert prettify_site_name("vimeo") == "Vimeo"


# ============================================================
# 5. P05 PROXY CONSISTENCY & BUDGET ENFORCEMENT TESTS
# ============================================================
def test_bytes_counted_after_streaming(client, mocker):
    mocker.patch("app.validate_public_url", return_value=True)
    proxy_router.proxied_bytes_today = 0
    token = cache_store.store_metadata({
        "title": "Proxied Test",
        "extractor": "vimeo",
        "formats": [{
            "format_id": "prog1",
            "url": "https://cdn.example.org/stream.mp4",
            "vcodec": "avc1",
            "acodec": "mp4a",
            "protocol": "https"
        }]
    }, "https://vimeo.com/12345", proxy="http://proxy.test:8080")

    mock_resp = mocker.MagicMock()
    mock_resp.status_code = 200
    mock_resp.headers = {"Content-Length": "1024", "Content-Type": "video/mp4"}
    mock_resp.iter_content.return_value = [b"x" * 512, b"y" * 512]
    mocker.patch("app.safe_requests_get", return_value=mock_resp)

    resp = client.get(f"/api/stream?token={token}&format_id=prog1")
    assert resp.status_code == 200
    # Drain generator
    _ = resp.data
    assert proxy_router.proxied_bytes_today == 1024


def test_budget_exceeded_degrades_gracefully(client, mocker):
    mocker.patch("app.validate_public_url", return_value=True)
    mocker.patch.object(config, "PROXIED_DAILY_BYTE_BUDGET_GB", 1)
    mocker.patch.object(config, "PROXY_RULES", {"instagram": "http://instaproxy:8080"})
    proxy_router.proxied_bytes_today = 2 * 1024 * 1024 * 1024  # 2 GB > 1 GB cap

    resp = client.post("/api/download", json={"url": "https://www.instagram.com/p/C12345/"})
    assert resp.status_code == 503
    data = resp.get_json()
    assert "temporarily unavailable" in data["error"].lower()


def test_consistent_proxy_used_for_extraction_and_stream(client, mocker):
    mocker.patch("app.validate_public_url", return_value=True)
    mocker.patch("security.validate_public_url", return_value=True)
    mocker.patch.object(config, "PROXY_RULES", {"vimeo": "http://vimeoproxy:8080"})
    proxy_router.proxied_bytes_today = 0

    mock_info = {
        "title": "Vimeo Video",
        "extractor": "vimeo",
        "formats": [{
            "format_id": "v1",
            "url": "https://1.1.1.1/video.mp4",
            "vcodec": "avc1",
            "acodec": "mp4a",
            "protocol": "https"
        }]
    }
    mocker.patch("app.fetch_fresh_metadata", return_value=mock_info)
    mock_stream_direct = mocker.patch("app.stream_via_direct_url", return_value=(iter([b"chunk"]), "video/mp4", 5))

    resp = client.post("/api/download", json={"url": "https://vimeo.com/12345"})
    assert resp.status_code == 200
    token = resp.get_json()["token"]

    cached = cache_store.get_metadata(token)
    assert cached["proxy"] == "http://vimeoproxy:8080"

    resp_stream = client.get(f"/api/stream?token={token}&format_id=v1")
    assert resp_stream.status_code == 200
    _ = resp_stream.data

    mock_stream_direct.assert_called_once()
    assert mock_stream_direct.call_args[1]["proxy_url"] == "http://vimeoproxy:8080"


def test_yt_mode_proxy_without_rule_returns_friendly_error(client, mocker):
    mocker.patch("app.validate_public_url", return_value=True)
    mocker.patch.object(config, "YT_MODE", "proxy")
    mocker.patch.object(config, "PROXY_RULES", {})
    mocker.patch.object(config, "YTDLP_PROXY", "")

    resp = client.post("/api/download", json={"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"})
    assert resp.status_code == 503
    data = resp.get_json()
    assert "YouTube is temporarily unavailable, other sites work" in data["error"]


def test_yt_mode_disabled_rejects_youtube(client, mocker):
    mocker.patch("app.validate_public_url", return_value=True)
    mocker.patch.object(config, "YT_MODE", "disabled")

    resp = client.post("/api/download", json={"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"})
    assert resp.status_code == 400
    data = resp.get_json()
    assert "YouTube downloads are disabled." in data["error"]

