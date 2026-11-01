import ipaddress
import re
import socket
import urllib.parse
from typing import Dict, List, Optional
import requests
from requests.adapters import HTTPAdapter
import urllib3
from urllib3.connection import HTTPConnection, HTTPSConnection
from urllib3.connectionpool import HTTPConnectionPool, HTTPSConnectionPool
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

    try:
        parsed = urllib.parse.urlsplit(url)
        port = parsed.port
    except ValueError as e:
        raise SSRFValidationError(f"Invalid URL or port: {e}")

    # 1. Scheme validation
    if parsed.scheme.lower() not in ("http", "https"):
        raise SSRFValidationError(f"Invalid URL scheme '{parsed.scheme}'. Only http and https are allowed.")

    # 2. Userinfo prohibited
    if parsed.username or parsed.password or "@" in parsed.netloc:
        raise SSRFValidationError("User authentication in URLs is prohibited.")

    # 3. Port validation (default 80/443 only)
    if port not in (None, 80, 443):
        raise SSRFValidationError(f"Prohibited destination port '{port}'. Only ports 80 and 443 are allowed.")

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


def resolve_and_pin(url: str) -> str:
    """
    Validates URL and resolves its hostname, returning the validated IP address.
    Reduces TOCTOU window for outbound requests by pinning to the resolved IP.
    Note: ffmpeg and yt-dlp do not natively support IP pinning and are protected
    at the network level via container egress firewall rules (see P16).
    """
    parsed = validate_public_url(url)
    hostname = parsed.hostname
    addr_info = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
    if not addr_info:
        raise SSRFValidationError(f"No IP addresses resolved for hostname '{hostname}'.")
    return addr_info[0][4][0]


class PinnedHTTPConnection(HTTPConnection):
    pinned_ip: Optional[str] = None
    def _new_conn(self):
        return socket.create_connection((self.pinned_ip, self.port), self.timeout, self.source_address)


class PinnedHTTPSConnection(HTTPSConnection):
    pinned_ip: Optional[str] = None
    def _new_conn(self):
        return socket.create_connection((self.pinned_ip, self.port), self.timeout, self.source_address)


class PinnedIPPoolManager(urllib3.PoolManager):
    def __init__(self, pinned_ip: str, *args, **kwargs):
        self.pinned_ip = pinned_ip
        super().__init__(*args, **kwargs)

    def _new_pool(self, scheme, host, port, request_context=None):
        pinned = self.pinned_ip
        if scheme == "http":
            class CustomConn(PinnedHTTPConnection):
                pinned_ip = pinned
            class CustomPool(HTTPConnectionPool):
                ConnectionCls = CustomConn
            return CustomPool(host, port, **self.connection_pool_kw)
        elif scheme == "https":
            class CustomHTTPSConn(PinnedHTTPSConnection):
                pinned_ip = pinned
            class CustomHTTPSPool(HTTPSConnectionPool):
                ConnectionCls = CustomHTTPSConn
            return CustomHTTPSPool(host, port, **self.connection_pool_kw)
        return super()._new_pool(scheme, host, port, request_context=request_context)


class PinnedIPAdapter(HTTPAdapter):
    """
    Custom requests HTTPAdapter that forces connections to a pre-validated IP
    address while preserving the HTTP Host header and TLS SNI verification.
    """
    def __init__(self, pinned_ip: str, **kwargs):
        self.pinned_ip = pinned_ip
        super().__init__(**kwargs)

    def init_poolmanager(self, connections, maxsize, block=False, **pool_kwargs):
        self.poolmanager = PinnedIPPoolManager(
            self.pinned_ip,
            num_pools=connections,
            maxsize=maxsize,
            block=block,
            **pool_kwargs
        )


def safe_requests_get(
    url: str,
    headers: Optional[Dict[str, str]] = None,
    stream: bool = False,
    timeout: int = 30,
    max_hops: int = 5,
    proxies: Optional[Dict[str, str]] = None
) -> requests.Response:
    """
    Executes an HTTP GET request with manual redirect validation and IP pinning.
    Ensures every redirect hop is rigorously validated against SSRF rules.
    """
    current_url = url
    for hop in range(max_hops):
        validate_public_url(current_url)

        session = requests.Session()
        if proxies:
            session.proxies = proxies
        else:
            pinned_ip = resolve_and_pin(current_url)
            adapter = PinnedIPAdapter(pinned_ip)
            session.mount("http://", adapter)
            session.mount("https://", adapter)

        resp = session.get(
            current_url,
            headers=headers,
            stream=stream,
            timeout=timeout,
            allow_redirects=False
        )

        if resp.is_redirect or resp.status_code in (301, 302, 303, 307, 308):
            location = resp.headers.get("Location")
            if not location:
                validate_public_url(current_url)
                return resp
            if stream:
                resp.close()
            # Resolve relative redirects against current URL
            current_url = urllib.parse.urljoin(current_url, location)
            continue

        validate_public_url(current_url)
        return resp

    raise SSRFValidationError(f"Too many redirects (exceeded limit of {max_hops}).")


def validate_extracted_urls(info: Dict) -> Dict:
    """
    Validates all media URLs inside yt-dlp extracted info dictionary.
    Memoizes DNS resolution per hostname within one call to prevent redundant lookups.
    Validates unique hostnames of url/manifest_url/fragments, dropping any format
    with any bad host.
    """
    formats = info.get("formats", [])
    valid_formats = []
    host_cache: Dict[str, bool] = {}

    def _is_url_host_safe(u: Optional[str]) -> bool:
        if not u or not isinstance(u, str):
            return True
        host = None
        try:
            parsed = urllib.parse.urlsplit(u)
            if parsed.scheme.lower() not in ("http", "https"):
                return False
            host = parsed.hostname
            if not host:
                return False
            if host in host_cache:
                return host_cache[host]

            validate_public_url(u)
            host_cache[host] = True
            return True
        except SSRFValidationError:
            if host:
                host_cache[host] = False
            return False
        except Exception:
            if host:
                host_cache[host] = False
            return False

    for f in formats:
        fmt_url = f.get("url")
        if fmt_url and not _is_url_host_safe(fmt_url):
            continue

        manifest_url = f.get("manifest_url")
        if manifest_url and not _is_url_host_safe(manifest_url):
            continue

        fragments = f.get("fragments")
        if fragments and isinstance(fragments, list):
            bad_fragment = False
            seen_frag_hosts = set()
            for frag in fragments:
                if not isinstance(frag, dict):
                    continue
                frag_url = frag.get("url")
                if not frag_url or not isinstance(frag_url, str):
                    continue
                try:
                    frag_host = urllib.parse.urlsplit(frag_url).hostname
                except Exception:
                    bad_fragment = True
                    break
                if not frag_host:
                    bad_fragment = True
                    break
                if frag_host not in seen_frag_hosts:
                    seen_frag_hosts.add(frag_host)
                    if not _is_url_host_safe(frag_url):
                        bad_fragment = True
                        break
                elif host_cache.get(frag_host) is False:
                    bad_fragment = True
                    break
            if bad_fragment:
                continue

        valid_formats.append(f)

    info["formats"] = valid_formats
    return info


def build_safe_ffmpeg_args(
    video_url: str,
    audio_url: Optional[str] = None,
    is_audio_only: bool = False,
    max_duration: int = 1200,
    headers_dict: Optional[Dict[str, str]] = None,
    audio_headers_dict: Optional[Dict[str, str]] = None,
    proxy_url: Optional[str] = None
) -> List[str]:
    """
    Constructs an explicit, injection-safe ffmpeg command argument list.
    Enforces per-input protocol whitelists and rw_timeouts before EACH -i.
    Places duration cap (-t) and muxing queue size on output (after last -i, before pipe:1).
    Applies -headers before EACH -i (video and audio) using each format's own headers.
    """
    ff_cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "error",
    ]

    if proxy_url:
        if proxy_url.startswith("http://") or proxy_url.startswith("https://"):
            ff_cmd.extend(["-http_proxy", proxy_url])
        elif proxy_url.startswith("socks"):
            import logging
            logging.getLogger(__name__).warning("SOCKS proxy is not supported for ffmpeg; skipping -http_proxy.")

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

    def _build_input_options(h_dict: Optional[Dict[str, str]]) -> List[str]:
        opts = [
            "-protocol_whitelist", "http,https,tcp,tls,crypto",
            "-rw_timeout", str(int(config.FFMPEG_TIMEOUT_SEC * 1_000_000)),
        ]
        h_str = _format_headers(h_dict)
        if h_str:
            opts.extend(["-headers", h_str])
        return opts

    if is_audio_only:
        target_url = audio_url if audio_url else video_url
        h_dict = audio_headers_dict if (audio_url and audio_headers_dict) else headers_dict
        ff_cmd.extend(_build_input_options(h_dict))
        ff_cmd.extend([
            "-i", target_url,
            "-vn", "-f", "mp3", "-b:a", "192k",
            "-t", str(max_duration),
            "-max_muxing_queue_size", "1024",
            "pipe:1"
        ])

    elif audio_url:
        # Input 1: video
        ff_cmd.extend(_build_input_options(headers_dict))
        ff_cmd.extend(["-i", video_url])

        # Input 2: audio
        ff_cmd.extend(_build_input_options(audio_headers_dict or headers_dict))
        ff_cmd.extend(["-i", audio_url])

        # Output options after both inputs
        ff_cmd.extend([
            "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "128k",
            "-f", "mp4", "-movflags", "frag_keyframe+empty_moov+default_base_moof",
            "-t", str(max_duration),
            "-max_muxing_queue_size", "1024",
            "pipe:1"
        ])

    else:
        # Single input
        ff_cmd.extend(_build_input_options(headers_dict))
        ff_cmd.extend([
            "-i", video_url,
            "-map", "0:v?", "-map", "0:a?",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "128k",
            "-f", "mp4", "-movflags", "frag_keyframe+empty_moov+default_base_moof",
            "-t", str(max_duration),
            "-max_muxing_queue_size", "1024",
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
        "js_runtimes": {"deno": {}, "node": {}, "quickjs": {}},
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

