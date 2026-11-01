from unittest.mock import MagicMock
import yt_dlp
from hardening import get_real_client_ip, normalize_client_ip


def test_normalize_client_ip_ipv4_and_mapped():
    # IPv4 unchanged
    assert normalize_client_ip("203.0.113.195") == "203.0.113.195"
    assert normalize_client_ip("127.0.0.1") == "127.0.0.1"

    # IPv4-mapped IPv6 normalized to standard IPv4
    assert normalize_client_ip("::ffff:203.0.113.195") == "203.0.113.195"
    assert normalize_client_ip("::ffff:192.0.2.1") == "192.0.2.1"


def test_normalize_client_ip_ipv6_to_64_subnet():
    ip1 = "2001:db8:abcd:12:1111:2222:3333:4444"
    ip2 = "2001:db8:abcd:12:ffff:eeee:dddd:cccc"
    diff_ip = "2001:db8:abcd:99:1111:2222:3333:4444"

    norm1 = normalize_client_ip(ip1)
    norm2 = normalize_client_ip(ip2)
    norm_diff = normalize_client_ip(diff_ip)

    # Same /64 produce identical normalized key
    assert norm1 == "2001:db8:abcd:12::/64"
    assert norm2 == "2001:db8:abcd:12::/64"
    assert norm1 == norm2

    # Different /64 produces different key
    assert norm_diff == "2001:db8:abcd:99::/64"
    assert norm1 != norm_diff


def test_get_real_client_ip_normalizes_trusted_proxy_ipv6():
    req = MagicMock()
    req.remote_addr = "127.0.0.1"  # trusted proxy loopback
    req.headers = {"X-Real-IP": "2606:4700:4700::1111"}
    assert get_real_client_ip(req) == "2606:4700:4700::/64"

    req.headers = {"X-Real-IP": "::ffff:198.51.100.1"}
    assert get_real_client_ip(req) == "198.51.100.1"


def test_ipv6_clients_in_same_64_share_rate_limit_bucket(client, mocker):
    mocker.patch.object(yt_dlp.YoutubeDL, "extract_info", return_value={"id": "test", "formats": []})
    mocker.patch("app.validate_public_url", return_value=True)

    trusted_proxy_peer = "127.0.0.1"
    # Address 1 in 2001:db8:1:1::/64
    ipv6_addr1 = "2001:db8:1:1:aaaa:bbbb:cccc:dddd"
    # Address 2 in same 2001:db8:1:1::/64
    ipv6_addr2 = "2001:db8:1:1:1111:2222:3333:4444"
    # Address 3 in different 2001:db8:2:2::/64
    ipv6_addr_diff = "2001:db8:2:2:1111:2222:3333:4444"

    # Make 5 requests from addr1 (5 of 10)
    for i in range(5):
        r = client.post(
            "/api/download",
            json={"url": f"https://www.youtube.com/watch?v=sample_{i}"},
            headers={"X-Real-IP": ipv6_addr1},
            environ_base={"REMOTE_ADDR": trusted_proxy_peer}
        )
        assert r.status_code == 200

    # Make 5 requests from addr2 (same /64, so total 10 of 10 used)
    for i in range(5, 10):
        r = client.post(
            "/api/download",
            json={"url": f"https://www.youtube.com/watch?v=sample_{i}"},
            headers={"X-Real-IP": ipv6_addr2},
            environ_base={"REMOTE_ADDR": trusted_proxy_peer}
        )
        assert r.status_code == 200

    # 11th request from addr1 in same /64 must hit 429!
    r_exceeded = client.post(
        "/api/download",
        json={"url": "https://www.youtube.com/watch?v=sample_10"},
        headers={"X-Real-IP": ipv6_addr1},
        environ_base={"REMOTE_ADDR": trusted_proxy_peer}
    )
    assert r_exceeded.status_code == 429
    assert r_exceeded.get_json()["code"] == "RATE_LIMIT_EXCEEDED"

    # Request from different /64 should still succeed (separate bucket)!
    r_diff = client.post(
        "/api/download",
        json={"url": "https://www.youtube.com/watch?v=sample_diff"},
        headers={"X-Real-IP": ipv6_addr_diff},
        environ_base={"REMOTE_ADDR": trusted_proxy_peer}
    )
    assert r_diff.status_code == 200
