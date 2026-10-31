import socket
import pytest
from unittest.mock import patch, MagicMock
import requests

from security import (
    validate_public_url,
    SSRFValidationError,
    safe_requests_get,
    build_safe_ffmpeg_args,
    get_safe_ytdlp_opts,
    validate_extracted_urls,
    resolve_and_pin,
    PinnedIPAdapter,
)


@pytest.mark.parametrize("invalid_url", [
    "http://169.254.169.254/",
    "http://169.254.169.254/opc/v2/instance/",
    "http://127.0.0.1",
    "http://127.0.0.1:5000/api",
    "http://[::1]",
    "http://[::1]:8080",
    "http://localhost",
    "http://localhost:5000",
    "http://0x7f000001",
    "http://2130706433",
    "http://0177.0.0.1",
    "http://10.0.0.1",
    "http://192.168.1.1",
    "http://172.16.0.1",
    "http://100.64.0.1",
    "http://[::ffff:127.0.0.1]",
    "file:///etc/passwd",
    "ftp://example.com/resource",
    "gopher://example.com/",
    "http://user:pass@example.com/",
    "http://example.com:8080/",
    "http://example.com:22/",
    "https://" + "a" * 2050 + ".com",
])
def test_validate_public_url_rejects_dangerous_urls(invalid_url):
    with pytest.raises(SSRFValidationError):
        validate_public_url(invalid_url)


def test_validate_public_url_rejects_hostname_resolving_to_private_ip():
    with patch("socket.getaddrinfo") as mock_dns:
        # Mock DNS returning a private RFC 1918 IP
        mock_dns.return_value = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.1.50", 80))
        ]
        with pytest.raises(SSRFValidationError, match="Private or non-global IP"):
            validate_public_url("http://private-internal-corp.net/video.mp4")


def test_validate_public_url_rejects_hostname_resolving_to_link_local_metadata():
    with patch("socket.getaddrinfo") as mock_dns:
        mock_dns.return_value = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", 80))
        ]
        with pytest.raises(SSRFValidationError, match="Private or non-global IP"):
            validate_public_url("http://metadata.malicious.com/")


def test_validate_public_url_accepts_valid_public_urls():
    with patch("socket.getaddrinfo") as mock_dns:
        mock_dns.return_value = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))
        ]
        res = validate_public_url("https://example.com/video.mp4")
        assert res.scheme == "https"
        assert res.hostname == "example.com"


def test_safe_requests_get_rejects_redirect_to_private_ip():
    # Hop 1: public -> Hop 2: 302 redirect to http://169.254.169.254/
    mock_resp1 = MagicMock()
    mock_resp1.is_redirect = True
    mock_resp1.status_code = 302
    mock_resp1.headers = {"Location": "http://169.254.169.254/opc/v2/instance"}

    with patch("requests.Session.get", return_value=mock_resp1):
        with patch("socket.getaddrinfo") as mock_dns:
            mock_dns.return_value = [
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))
            ]
            with pytest.raises(SSRFValidationError):
                safe_requests_get("https://example.com/initial")


def test_safe_requests_get_rejects_exceeding_max_redirects():
    mock_resp = MagicMock()
    mock_resp.is_redirect = True
    mock_resp.status_code = 302
    mock_resp.headers = {"Location": "https://example.com/redirect"}

    with patch("requests.Session.get", return_value=mock_resp):
        with patch("socket.getaddrinfo") as mock_dns:
            mock_dns.return_value = [
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))
            ]
            with pytest.raises(SSRFValidationError, match="Too many redirects"):
                safe_requests_get("https://example.com/redirect", max_hops=3)


def test_build_safe_ffmpeg_args_structure():
    v_url = "https://cdn.example.com/v.mp4"
    a_url = "https://cdn.example.com/a.mp3"
    args = build_safe_ffmpeg_args(v_url, a_url, is_audio_only=False, max_duration=600)

    # Check protocol whitelist
    assert "-protocol_whitelist" in args
    proto_idx = args.index("-protocol_whitelist")
    assert "http,https,tcp,tls,crypto" == args[proto_idx + 1]

    # Check duration cap
    assert "-t" in args
    t_idx = args.index("-t")
    assert args[t_idx + 1] == "600"

    # Check max muxing queue size
    assert "-max_muxing_queue_size" in args


def test_build_safe_ffmpeg_args_handles_dash_url():
    # Malicious URL starting with hyphen flag
    malicious_url = "-filter_complex_script=/etc/passwd"
    args = build_safe_ffmpeg_args(malicious_url, None, is_audio_only=True, max_duration=300)
    # Ensure it's passed as an input operand, not an injected flag
    assert "-i" in args
    idx = args.index("-i")
    assert args[idx + 1] == malicious_url


def test_get_safe_ytdlp_opts():
    opts = get_safe_ytdlp_opts()
    assert opts.get("enable_file_urls") is False
    assert opts.get("noplaylist") is True
    assert opts.get("playlist_items") == "1"
    assert opts.get("cachedir") is False
    assert opts.get("no_color") is True
    assert opts.get("quiet") is True


def test_validate_public_url_bare_value_errors():
    with pytest.raises(SSRFValidationError):
        validate_public_url("http://example.com:99999")
    with pytest.raises(SSRFValidationError):
        validate_public_url("http://[::1")


def test_validate_extracted_urls_drops_private_ip():
    info = {
        "formats": [
            {"format_id": "safe1", "url": "https://example.com/v.mp4"},
            {"format_id": "unsafe1", "url": "http://192.168.1.100/v.mp4"},
            {"format_id": "unsafe_manifest", "url": "https://example.com/m.mp4", "manifest_url": "http://10.0.0.1/m.mpd"},
            {"format_id": "unsafe_frag", "url": "https://example.com/f.mp4", "fragments": [{"url": "http://127.0.0.1/chunk1"}]},
        ]
    }
    with patch("socket.getaddrinfo") as mock_dns:
        mock_dns.return_value = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))
        ]
        cleaned = validate_extracted_urls(info)
        remaining = [f["format_id"] for f in cleaned["formats"]]
        assert remaining == ["safe1"]


def test_build_safe_ffmpeg_args_headers_before_both_inputs():
    args = build_safe_ffmpeg_args(
        video_url="https://example.com/v.mp4",
        audio_url="https://example.com/a.mp3",
        headers_dict={"User-Agent": "VideoUA"},
        audio_headers_dict={"User-Agent": "AudioUA"}
    )
    # Both inputs must be preceded by -headers
    first_i = args.index("-i")
    assert args[first_i - 2] == "-headers"
    assert "VideoUA" in args[first_i - 1]

    # Find second -i
    second_i = args.index("-i", first_i + 2)
    assert args[second_i - 2] == "-headers"
    assert "AudioUA" in args[second_i - 1]


def test_pinned_ip_adapter_connects_to_validated_ip():
    with patch("socket.create_connection") as mock_conn:
        session = requests.Session()
        session.mount("http://", PinnedIPAdapter("93.184.216.34"))
        try:
            session.get("http://example.com/test", timeout=5)
        except Exception:
            pass
        assert mock_conn.called
        assert mock_conn.call_args[0][0] == ("93.184.216.34", 80)
        assert mock_conn.call_args[0][1] == 5


def test_stream_and_preview_reject_redirect_to_private_ip(client):
    from cache_store import cache_store
    fake_info = {
        "id": "direct123",
        "title": "Redirect Test",
        "extractor": "generic",
        "formats": [{
            "format_id": "18",
            "vcodec": "avc1",
            "acodec": "mp4a",
            "url": "https://example.com/redirect_start.mp4",
            "protocol": "https"
        }]
    }
    token = cache_store.store_metadata(fake_info, "https://example.com/redirect_start.mp4")

    # Mock safe_requests_get or mock response hop to private IP
    mock_hop1 = MagicMock()
    mock_hop1.is_redirect = True
    mock_hop1.status_code = 302
    mock_hop1.headers = {"Location": "http://169.254.169.254/latest/meta-data"}

    with patch("requests.Session.get", return_value=mock_hop1):
        with patch("socket.getaddrinfo") as mock_dns:
            mock_dns.return_value = [
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))
            ]
            # Stream path rejects SSRF redirect
            with pytest.raises(SSRFValidationError):
                client.get(f"/api/stream?token={token}&format_id=18")

            # Preview path rejects SSRF redirect
            with pytest.raises(SSRFValidationError):
                client.get(f"/api/preview?token={token}&format_id=18")
