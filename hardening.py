import ipaddress
import re
import unicodedata
import urllib.parse
from flask import Request, Response
from config import config
from security import SSRFValidationError

# Official Cloudflare public IP ranges
CLOUDFLARE_IPV4_NETWORKS = [
    ipaddress.ip_network(net) for net in [
        "173.245.48.0/20", "103.21.244.0/22", "103.22.200.0/22", "103.31.4.0/22",
        "141.101.64.0/18", "108.162.192.0/18", "190.93.240.0/20", "188.114.96.0/20",
        "197.234.240.0/22", "198.41.128.0/17", "162.158.0.0/15", "104.16.0.0/13",
        "104.24.0.0/14", "172.64.0.0/13", "131.0.72.0/22"
    ]
]

CLOUDFLARE_IPV6_NETWORKS = [
    ipaddress.ip_network(net) for net in [
        "2400:cb00::/32", "2606:4700::/32", "2803:f800::/32",
        "2405:b500::/32", "2405:8100::/32", "2a06:98c0::/29", "2c0f:f248::/32"
    ]
]


def is_cloudflare_ip(ip_str: str) -> bool:
    """Checks whether an incoming socket IP belongs to Cloudflare's network."""
    if not ip_str:
        return False
    try:
        ip = ipaddress.ip_address(ip_str.strip())
        if ip.version == 4:
            return any(ip in net for net in CLOUDFLARE_IPV4_NETWORKS)
        return any(ip in net for net in CLOUDFLARE_IPV6_NETWORKS)
    except ValueError:
        return False


def normalize_client_ip(ip_str: str) -> str:
    """
    Normalizes an IP address string:
    - IPv4 addresses are returned unchanged.
    - IPv4-mapped IPv6 addresses (e.g. ::ffff:192.0.2.1) are normalized to standard IPv4.
    - Native IPv6 addresses are normalized to their /64 network string (e.g. 2001:db8:1:2::/64)
      to prevent abuse from IPv6 address rotation within the same allocated subnet.
    """
    if not ip_str:
        return "127.0.0.1"
    try:
        ip = ipaddress.ip_address(ip_str.strip())
        if ip.version == 4:
            return str(ip)
        if ip.ipv4_mapped:
            return str(ip.ipv4_mapped)
        network = ipaddress.ip_network(f"{ip}/64", strict=False)
        return str(network)
    except ValueError:
        return ip_str.strip()


def get_real_client_ip(req: Request) -> str:
    """
    Extracts the authenticated client IP address.
    Trusts X-Real-IP (and falls back to the socket address) ONLY when the
    immediate peer address is in TRUSTED_PROXY_CIDRS.
    Ignores CF-Connecting-IP and X-Forwarded-For entirely.
    Normalizes IPv6 clients to their /64 network string and IPv4-mapped IPv6 to IPv4.
    """
    peer = (req.remote_addr or "").strip()
    if not peer:
        return "127.0.0.1"

    try:
        peer_ip = ipaddress.ip_address(peer)
    except ValueError:
        return peer

    is_trusted = False
    for cidr in config.TRUSTED_PROXY_CIDRS:
        try:
            network = ipaddress.ip_network(cidr.strip(), strict=False)
            if peer_ip in network:
                is_trusted = True
                break
        except ValueError:
            continue

    raw_ip = peer
    if is_trusted:
        x_real_ip = req.headers.get("X-Real-IP")
        if x_real_ip:
            x_real_ip = x_real_ip.strip()
            try:
                ipaddress.ip_address(x_real_ip)
                raw_ip = x_real_ip
            except ValueError:
                raw_ip = peer

    return normalize_client_ip(raw_ip)



def sanitize_filename(filename: str, ext: str = ".mp4") -> str:
    """
    Sanitizes arbitrary user or video title strings into a safe filename.
    Strips CR, LF, nulls, path traversal separators, and quotes.
    Caps filename length to at most 150 characters before extension.
    Strips Unicode bidirectional override characters (U+202A-202E, U+2066-2069).
    """
    if not filename:
        filename = "Jungle_Download"

    if ext and filename.lower().endswith(ext.lower()):
        base = filename[:-len(ext)]
    else:
        base = filename

    # Strip bidi override characters (U+202A-202E, U+2066-2069)
    clean = re.sub(r"[\u202a-\u202e\u2066-\u2069]", "", base)

    # Strip newlines, control characters, nulls
    clean = re.sub(r"[\r\n\x00-\x1f\x7f]", "", clean).strip()

    # Strip path traversal and filesystem special chars
    clean = re.sub(r'[/\\?%*:|"<>]+', "_", clean)
    clean = clean.strip("._ ")

    # Cap base filename length to at most 150 characters before extension
    if len(clean) > 150:
        clean = clean[:150].strip("._ ")

    if not clean:
        clean = "Jungle_Download"

    return f"{clean}{ext}"


def build_content_disposition(filename: str, ext: str = ".mp4") -> str:
    """
    Emits an RFC 6266 / RFC 5987 compliant Content-Disposition header.
    Provides an ASCII fallback for legacy clients and a UTF-8 percent-encoded
    filename* parameter to preserve international (e.g. Tamil, Hindi, Arabic) characters.
    """
    safe_name = sanitize_filename(filename, ext)

    # ASCII-safe fallback
    ascii_clean = unicodedata.normalize("NFKD", safe_name).encode("ascii", "ignore").decode("ascii")
    ascii_clean = re.sub(r'["\';]', "", ascii_clean).strip()
    if not ascii_clean or ascii_clean == ext:
        ascii_clean = f"download{ext}"

    # RFC 5987 UTF-8 encoding
    utf8_encoded = urllib.parse.quote(safe_name, safe="._-")

    return f"attachment; filename=\"{ascii_clean}\"; filename*=UTF-8''{utf8_encoded}"


LOGIN_PRIVATE_AGE_PATTERN = re.compile(
    r"\b(private video|sign in if you've been granted|login required|requires login|age-restricted|confirm your age|members-only|subscriber-only)\b|"
    r"this video is private|sign in to view|requires authentication",
    re.IGNORECASE,
)

BOT_PATTERN = re.compile(
    r"\b(bot|captcha|429)\b|not a bot|sign in to confirm you['’]?re not a bot|too many requests|blocking us|automated queries",
    re.IGNORECASE,
)


def map_error_message(exc: Exception) -> str:
    """Maps internal exceptions into safe, user-friendly messages without leaking traces."""
    if isinstance(exc, SSRFValidationError):
        return "This URL is not permitted or cannot be processed."

    err_str = str(exc)
    err_lower = err_str.lower()

    if "too long" in err_lower or "duration" in err_lower:
        return f"Video too long (maximum allowed is {config.MAX_DURATION_SEC // 60} minutes)."

    if LOGIN_PRIVATE_AGE_PATTERN.search(err_str):
        if "private" in err_lower:
            return "This media is private and cannot be downloaded."
        if "age" in err_lower or "confirm your age" in err_lower:
            return "This media is age-restricted and requires account verification."
        return "This media requires login or account authorization."

    if BOT_PATTERN.search(err_str):
        return "Site temporarily blocking us, try again later."

    if "403" in err_lower or "forbidden" in err_lower:
        return "Access to this media is forbidden or restricted by upstream provider."

    if "404" in err_lower or "not found" in err_lower:
        return "Media not found on upstream provider."

    if "not supported" in err_lower or "unsupported" in err_lower or "no suitable extractor" in err_lower:
        return "This site is not supported."

    if "private or non-global" in err_lower or "blocked" in err_lower:
        return "This site is not supported."

    return "Could not process this link. Please verify the URL and try again."


def apply_security_headers(response: Response) -> Response:
    """Applies modern security headers and CSP to all HTTP responses."""
    h = response.headers
    h["X-Content-Type-Options"] = "nosniff"
    h["X-Frame-Options"] = "SAMEORIGIN"
    h["Referrer-Policy"] = "strict-origin-when-cross-origin"
    h["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(), payment=()"

    # HSTS in production
    if config.IS_PROD:
        h["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"

    # CSP in Report-Only mode
    extra_scripts = (" " + " ".join(config.CSP_EXTRA_SCRIPT_SRC)) if config.CSP_EXTRA_SCRIPT_SRC else ""
    h["Content-Security-Policy-Report-Only"] = (
        "default-src 'self'; "
        f"script-src 'self' 'unsafe-inline' https:{extra_scripts}; "
        "style-src 'self' 'unsafe-inline' https:; "
        "img-src 'self' data: https:; "
        "media-src 'self' blob: https:; "
        "connect-src 'self' https:; "
        "font-src 'self' https: data:; "
        "frame-ancestors 'self';"
    )
    return response

