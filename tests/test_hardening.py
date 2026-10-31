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


def test_get_real_client_ip_trusted_proxy():
    # Case 1: Untrusted direct socket IP -> ignore CF-Connecting-IP spoofing
    builder = EnvironBuilder(
        path="/",
        headers={"CF-Connecting-IP": "203.0.113.199"},
        environ_base={"REMOTE_ADDR": "198.51.100.22"}  # Not a Cloudflare or local IP
    )
    req = Request(builder.get_environ())
    assert get_real_client_ip(req) == "198.51.100.22"

    # Case 2: Trusted Cloudflare socket IP -> accept CF-Connecting-IP
    builder_cf = EnvironBuilder(
        path="/",
        headers={"CF-Connecting-IP": "203.0.113.199"},
        environ_base={"REMOTE_ADDR": "104.16.1.1"}  # Cloudflare edge IP
    )
    req_cf = Request(builder_cf.get_environ())
    assert get_real_client_ip(req_cf) == "203.0.113.199"

    # Case 3: Local Caddy reverse proxy on loopback -> accept CF-Connecting-IP
    builder_local = EnvironBuilder(
        path="/",
        headers={"CF-Connecting-IP": "203.0.113.199"},
        environ_base={"REMOTE_ADDR": "127.0.0.1"}
    )
    req_local = Request(builder_local.get_environ())
    assert get_real_client_ip(req_local) == "203.0.113.199"


def test_apply_security_headers():
    resp = Response("OK")
    secured = apply_security_headers(resp)
    assert secured.headers.get("X-Content-Type-Options") == "nosniff"
    assert secured.headers.get("X-Frame-Options") == "SAMEORIGIN"
    assert secured.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"
    assert "Content-Security-Policy-Report-Only" in secured.headers

