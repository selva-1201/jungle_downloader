import pytest
from flask import Flask, Response, Request
from werkzeug.test import EnvironBuilder
from hardening import (
    sanitize_filename,
    build_content_disposition,
    map_error_message,
    is_cloudflare_ip,
    get_real_client_ip,
    apply_security_headers
)
from security import SSRFValidationError


def test_sanitize_filename_removes_crlf_and_special_chars():
    # Attack with carriage return, newline, and quotes
    malicious = "My_Video\r\nX-Injected-Header: evil\n\"quote\"/../../etc/passwd.mp4"
    cleaned = sanitize_filename(malicious, ext=".mp4")
    assert "\r" not in cleaned
    assert "\n" not in cleaned
    assert "/" not in cleaned
    assert "\\" not in cleaned
    assert '"' not in cleaned
    assert cleaned.endswith(".mp4")


def test_build_content_disposition_with_unicode_tamil():
    # Tamil title: "காதல்_பாடல்" (Love song)
    tamil_title = "காதல்_பாடல்"
    cd = build_content_disposition(tamil_title, ext=".mp4")

    # Must contain both filename and filename*=UTF-8''
    assert 'filename="' in cd
    assert "filename*=UTF-8''" in cd
    # UTF-8 percent-encoded Tamil chars must be present
    assert "%E0%AE%95" in cd  # 'க' in UTF-8 hex
    # No CR/LF in the header value
    assert "\r" not in cd
    assert "\n" not in cd


def test_map_error_message_shields_internal_errors():
    # Never return raw Exception to client
    raw_error = Exception("DB Connection Failed: /var/run/secrets/token at line 42")
    user_msg = map_error_message(raw_error)
    assert "/var/run/secrets" not in user_msg
    assert "DB Connection" not in user_msg
    assert "Could not process this link" in user_msg

    # SSRF error mapping
    ssrf_err = SSRFValidationError("Private or non-global IP 127.0.0.1 blocked")
    ssrf_msg = map_error_message(ssrf_err)
    assert "127.0.0.1" not in ssrf_msg
    assert "not permitted" in ssrf_msg

    # Duration cap mapping
    dur_err = Exception("Video duration 3600 exceeds max allowed duration 1200")
    dur_msg = map_error_message(dur_err)
    assert "Video too long" in dur_msg

    # Upstream 429 rate limit mapping
    rate_err = Exception("HTTP Error 429: Too Many Requests from YouTube")
    rate_msg = map_error_message(rate_err)
    assert "Site temporarily blocking us" in rate_msg


def test_is_cloudflare_ip():
    assert is_cloudflare_ip("173.245.48.5") is True
    assert is_cloudflare_ip("104.16.12.34") is True
    assert is_cloudflare_ip("2400:cb00:10::1") is True
    assert is_cloudflare_ip("8.8.8.8") is False
    assert is_cloudflare_ip("127.0.0.1") is False


def test_get_real_client_ip_trusted_proxy_and_x_real_ip():
    # 1. Peer in trusted range (127.0.0.1/32, 172.16.0.0/12, 10.0.0.0/8) -> X-Real-IP is used
    builder_loopback = EnvironBuilder(
        path="/",
        headers={"X-Real-IP": "203.0.113.199"},
        environ_base={"REMOTE_ADDR": "127.0.0.1"}
    )
    req_loopback = Request(builder_loopback.get_environ())
    assert get_real_client_ip(req_loopback) == "203.0.113.199"

    builder_docker_bridge = EnvironBuilder(
        path="/",
        headers={"X-Real-IP": "198.51.100.50"},
        environ_base={"REMOTE_ADDR": "172.18.0.2"}  # Within 172.16.0.0/12
    )
    req_docker = Request(builder_docker_bridge.get_environ())
    assert get_real_client_ip(req_docker) == "198.51.100.50"

    builder_private_10 = EnvironBuilder(
        path="/",
        headers={"X-Real-IP": "198.51.100.60"},
        environ_base={"REMOTE_ADDR": "10.0.2.15"}  # Within 10.0.0.0/8
    )
    req_10 = Request(builder_private_10.get_environ())
    assert get_real_client_ip(req_10) == "198.51.100.60"

    # 2. Peer NOT trusted -> spoofed X-Real-IP is ignored; returns peer socket address
    builder_untrusted = EnvironBuilder(
        path="/",
        headers={"X-Real-IP": "203.0.113.199"},
        environ_base={"REMOTE_ADDR": "198.51.100.22"}  # Public untrusted peer
    )
    req_untrusted = Request(builder_untrusted.get_environ())
    assert get_real_client_ip(req_untrusted) == "198.51.100.22"

    # 3. Invalid X-Real-IP value falls back to the peer socket address
    builder_invalid_ip = EnvironBuilder(
        path="/",
        headers={"X-Real-IP": "not-an-ip-address; drop table;"},
        environ_base={"REMOTE_ADDR": "127.0.0.1"}
    )
    req_invalid_ip = Request(builder_invalid_ip.get_environ())
    assert get_real_client_ip(req_invalid_ip) == "127.0.0.1"

    builder_invalid_octet = EnvironBuilder(
        path="/",
        headers={"X-Real-IP": "999.999.999.999"},
        environ_base={"REMOTE_ADDR": "172.18.0.2"}
    )
    req_invalid_octet = Request(builder_invalid_octet.get_environ())
    assert get_real_client_ip(req_invalid_octet) == "172.18.0.2"

    # 4. CF-Connecting-IP and X-Forwarded-For are completely ignored
    builder_cf_ignored = EnvironBuilder(
        path="/",
        headers={
            "CF-Connecting-IP": "1.2.3.4",
            "X-Forwarded-For": "5.6.7.8"
        },
        environ_base={"REMOTE_ADDR": "127.0.0.1"}
    )
    req_cf_ignored = Request(builder_cf_ignored.get_environ())
    assert get_real_client_ip(req_cf_ignored) == "127.0.0.1"



def test_apply_security_headers():
    resp = Response("OK")
    secured = apply_security_headers(resp)
    assert secured.headers.get("X-Content-Type-Options") == "nosniff"
    assert secured.headers.get("X-Frame-Options") == "SAMEORIGIN"
    assert secured.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"
    assert "Content-Security-Policy-Report-Only" in secured.headers

