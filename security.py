import ipaddress
import re
import socket
import urllib.parse
from typing import Dict, List, Optional
import requests
from config import config


class SSRFValidationError(ValueError):
    """Raised when a URL targets a private, internal, or prohibited network address."""
    pass


def _is_ip_global(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Strictly checks whether an IP address is public and globally routable."""
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped

    if not ip.is_global:
        return False

    # Defense-in-depth: explicit checks for private, loopback, link-local, and reserved ranges
    if (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    ):
        return False

    return True


def _check_numeric_or_special_ip(hostname: str) -> Optional[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    """Detects and parses integer, hex, or octal IP representations."""
    clean_host = hostname.strip("[]")

    # Integer IP e.g. 2130706433
    if clean_host.isdigit():
        try:
            return ipaddress.IPv4Address(int(clean_host))
        except Exception:
            pass

    # Hex IP e.g. 0x7f000001
    if clean_host.lower().startswith("0x"):
        try:
            return ipaddress.IPv4Address(int(clean_host, 16))
        except Exception:
            pass

    # Octal / dot-decimal representations e.g. 0177.0.0.1
    parts = clean_host.split(".")
    if len(parts) == 4 and all(p.isdigit() for p in parts):
        try:
            # If any part has a leading zero, libc parses as octal
            nums = [int(p, 8) if p.startswith("0") and len(p) > 1 else int(p) for p in parts]
            if all(0 <= n <= 255 for n in nums):
                return ipaddress.IPv4Address(".".join(str(n) for n in nums))
        except Exception:
            pass

    # Standard IP address parsing
    try:
        return ipaddress.ip_address(clean_host)
    except ValueError:
        return None


def validate_public_url(url: str) -> urllib.parse.ParseResult:
    """
    Validates that a URL is a safe, publicly routable HTTP/HTTPS destination.
    Rejects SSRF attempts including loopback, link-local (Oracle IMDS), RFC 1918,
    IPv4-mapped IPv6, octal/hex IP tricks, userinfo, and non-standard ports.
    """
    if not url or len(url) > 2048:
        raise SSRFValidationError("URL missing or exceeds maximum allowed length of 2048 characters.")

    parsed = urllib.parse.urlsplit(url)

    # 1. Scheme validation
    if parsed.scheme.lower() not in ("http", "https"):
        raise SSRFValidationError(f"Invalid URL scheme '{parsed.scheme}'. Only http and https are allowed.")

    # 2. Userinfo prohibited
    if parsed.username or parsed.password or "@" in parsed.netloc:
        raise SSRFValidationError("User authentication in URLs is prohibited.")

    # 3. Port validation (default 80/443 only)
    if parsed.port not in (None, 80, 443):
        raise SSRFValidationError(f"Prohibited destination port '{parsed.port}'. Only ports 80 and 443 are allowed.")

    # 4. Hostname validation
    hostname = parsed.hostname
    if not hostname:
        raise SSRFValidationError("URL must include a valid hostname.")

    # Check for direct or obfuscated IP literals
    direct_ip = _check_numeric_or_special_ip(hostname)
    if direct_ip is not None:
        if not _is_ip_global(direct_ip):
            raise SSRFValidationError(f"Private or non-global IP literal blocked: {direct_ip}")

    # 5. Resolve all A and AAAA DNS records and verify every returned address
    try:
        addr_info = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
    except socket.gaierror as e:
        raise SSRFValidationError(f"DNS resolution failure for '{hostname}': {e}")

    if not addr_info:
        raise SSRFValidationError(f"No IP addresses resolved for hostname '{hostname}'.")

    for family, _, _, _, sockaddr in addr_info:
        ip_str = sockaddr[0]
        try:
            resolved_ip = ipaddress.ip_address(ip_str)
        except ValueError:
            raise SSRFValidationError(f"Invalid resolved IP '{ip_str}' for hostname '{hostname}'.")

        if not _is_ip_global(resolved_ip):
            raise SSRFValidationError(f"Private or non-global IP '{resolved_ip}' blocked for '{hostname}'.")

    return parsed


def safe_requests_get(
    url: str,
    headers: Optional[Dict[str, str]] = None,
    stream: bool = False,
    timeout: int = 30,
    max_hops: int = 5
) -> requests.Response:
    """
    Executes an HTTP GET request with manual redirect validation.
    Ensures every redirect hop is rigorously validated against SSRF rules.
    """
    current_url = url
    for hop in range(max_hops):
        validate_public_url(current_url)

        resp = requests.get(
            current_url,
            headers=headers,
            stream=stream,
            timeout=timeout,
            allow_redirects=False
        )

        if resp.is_redirect or resp.status_code in (301, 302, 303, 307, 308):
            location = resp.headers.get("Location")
            if not location:
                return resp
            # Resolve relative redirects against current URL
            current_url = urllib.parse.urljoin(current_url, location)
            continue

        return resp

    raise SSRFValidationError(f"Too many redirects (exceeded limit of {max_hops}).")


def build_safe_ffmpeg_args(
    video_url: str,
    audio_url: Optional[str] = None,
    is_audio_only: bool = False,
    max_duration: int = 1200,
    headers_dict: Optional[Dict[str, str]] = None
) -> List[str]:
    """
    Constructs an explicit, injection-safe ffmpeg command argument list.
    Enforces protocol whitelists, duration limits, and safe operand placement.
    """
    ff_cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "error",
        "-protocol_whitelist", "http,https,tcp,tls,crypto",
        "-t", str(max_duration),
        "-max_muxing_queue_size", "1024"
    ]

    def _format_headers(h_dict: Optional[Dict[str, str]]) -> str:
        if not h_dict:
            return ""
        # Strip CR/LF from keys/values to prevent header injection in ffmpeg
        clean = []
        for k, v in h_dict.items():
            k_clean = re.sub(r"[\r\n]", "", str(k))
            v_clean = re.sub(r"[\r\n]", "", str(v))
            clean.append(f"{k_clean}: {v_clean}\r\n")
        return "".join(clean)

    if is_audio_only:
        target_url = audio_url if audio_url else video_url
        headers_str = _format_headers(headers_dict)
        if headers_str:
            ff_cmd.extend(["-headers", headers_str])
        ff_cmd.extend(["-i", target_url, "-vn", "-f", "mp3", "-b:a", "192k", "pipe:1"])

    elif audio_url:
        headers_str = _format_headers(headers_dict)
        if headers_str:
            ff_cmd.extend(["-headers", headers_str])
        ff_cmd.extend(["-i", video_url])
        ff_cmd.extend(["-i", audio_url])
        ff_cmd.extend([
            "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "128k",
            "-f", "mp4", "-movflags", "frag_keyframe+empty_moov+default_base_moof",
            "pipe:1"
        ])

    else:
        headers_str = _format_headers(headers_dict)
        if headers_str:
            ff_cmd.extend(["-headers", headers_str])
        ff_cmd.extend([
            "-i", video_url,
            "-map", "0:v?", "-map", "0:a?",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "128k",
            "-f", "mp4", "-movflags", "frag_keyframe+empty_moov+default_base_moof",
            "pipe:1"
        ])

    return ff_cmd


def get_safe_ytdlp_opts(extra_opts: Optional[Dict] = None) -> Dict:
    """
    Generates hardened default options for YoutubeDL.
    Disables local file access, restricts generic scrapers, and enforces timeouts.
    """
    opts = {
        "enable_file_urls": False,
        "noplaylist": True,
        "playlist_items": "1",
        "cachedir": False,
        "no_color": True,
        "quiet": True,
        "no_warnings": True,
        "socket_timeout": 15,
    }

    if not config.ALLOW_GENERIC:
        opts["allowed_extractors"] = ["default", "-generic"]

    if config.COOKIES_FILE:
        import os
        if os.path.exists(config.COOKIES_FILE):
            opts["cookiefile"] = config.COOKIES_FILE

    if config.YTDLP_PROXY:
        opts["proxy"] = config.YTDLP_PROXY

    if extra_opts:
        opts.update(extra_opts)

    return opts

