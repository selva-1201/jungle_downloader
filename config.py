import json
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional


def _get_bool(key: str, default: bool = False) -> bool:
    val = os.getenv(key)
    if val is None:
        return default
    return val.strip().lower() in ("true", "1", "yes", "on")


def _get_int(key: str, default: int) -> int:
    val = os.getenv(key)
    if val is None:
        return default
    try:
        return int(val.strip())
    except ValueError:
        return default


def _get_list(key: str, default: Optional[List[str]] = None) -> List[str]:
    val = os.getenv(key)
    if val is None:
        return default or []
    return [item.strip() for item in val.split(",") if item.strip()]


def _get_json_dict(key: str, default: Optional[Dict] = None) -> Dict:
    val = os.getenv(key)
    if val is None:
        return default or {}
    try:
        parsed = json.loads(val.strip())
        return parsed if isinstance(parsed, dict) else (default or {})
    except Exception:
        return default or {}


DEFAULT_CONSENT_COUNTRIES = [
    "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR",
    "DE", "GR", "HU", "IE", "IT", "LV", "LT", "LU", "MT", "NL",
    "PL", "PT", "RO", "SK", "SI", "ES", "SE", "GB", "CH", "IS", "LI", "NO"
]

DEFAULT_TRUSTED_PROXY_CIDRS = ["172.16.0.0/12", "10.0.0.0/8", "127.0.0.1/32"]


@dataclass
class AppConfig:

    SITE_URL: str = field(default_factory=lambda: os.getenv("SITE_URL", "http://localhost:5000").rstrip("/"))
    ENV: str = field(default_factory=lambda: os.getenv("ENV", "dev").lower())
    DEBUG: bool = field(default_factory=lambda: _get_bool("DEBUG", False))
    PORT: int = field(default_factory=lambda: _get_int("PORT", 5000))
    LOG_LEVEL: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO").upper())

    ALLOW_ADULT: bool = field(default_factory=lambda: _get_bool("ALLOW_ADULT", False))
    ALLOW_GENERIC: bool = field(default_factory=lambda: _get_bool("ALLOW_GENERIC", False))

    ADS_ENABLED: bool = field(default_factory=lambda: _get_bool("ADS_ENABLED", False))
    ADS_SCRIPT_URLS: List[str] = field(default_factory=lambda: _get_list("ADS_SCRIPT_URLS"))
    CONSENT_REQUIRED_COUNTRIES: List[str] = field(
        default_factory=lambda: _get_list("CONSENT_REQUIRED_COUNTRIES", DEFAULT_CONSENT_COUNTRIES)
    )

    CACHE_BACKEND: str = field(default_factory=lambda: os.getenv("CACHE_BACKEND", "memory").lower())
    REDIS_URL: str = field(default_factory=lambda: os.getenv("REDIS_URL", "redis://localhost:6379/0"))
    META_CACHE_TTL: int = field(default_factory=lambda: _get_int("META_CACHE_TTL", 1500))

    MAX_DURATION_SEC: int = field(default_factory=lambda: _get_int("MAX_DURATION_SEC", 1200))
    MAX_FILESIZE_MB: int = field(default_factory=lambda: _get_int("MAX_FILESIZE_MB", 500))
    PROXY_MAX_MB: int = field(default_factory=lambda: _get_int("PROXY_MAX_MB", 300))
    DIRECT_LINK_MODE: str = field(default_factory=lambda: os.getenv("DIRECT_LINK_MODE", "large_only").lower())

    MAX_CONCURRENT_FFMPEG: int = field(default_factory=lambda: _get_int("MAX_CONCURRENT_FFMPEG", 3))
    MAX_CONCURRENT_EXTRACT: int = field(default_factory=lambda: _get_int("MAX_CONCURRENT_EXTRACT", 6))
    MAX_CONCURRENT_YOUTUBE: int = field(default_factory=lambda: _get_int("MAX_CONCURRENT_YOUTUBE", 2))
    FFMPEG_TIMEOUT_SEC: int = field(default_factory=lambda: _get_int("FFMPEG_TIMEOUT_SEC", 900))

    RATE_LIMIT_EXTRACT: str = field(default_factory=lambda: os.getenv("RATE_LIMIT_EXTRACT", "10/minute;100/day"))
    RATE_LIMIT_STREAM: str = field(default_factory=lambda: os.getenv("RATE_LIMIT_STREAM", "6/minute;40/day"))
    RATE_LIMIT_PREVIEW: str = field(default_factory=lambda: os.getenv("RATE_LIMIT_PREVIEW", "120/minute;1000/day"))

    TURNSTILE_SITE_KEY: str = field(default_factory=lambda: os.getenv("TURNSTILE_SITE_KEY", ""))
    TURNSTILE_SECRET: str = field(default_factory=lambda: os.getenv("TURNSTILE_SECRET", ""))

    TRUST_CLOUDFLARE_IP: bool = field(default_factory=lambda: _get_bool("TRUST_CLOUDFLARE_IP", True))
    TRUSTED_PROXY_CIDRS: List[str] = field(
        default_factory=lambda: _get_list("TRUSTED_PROXY_CIDRS", DEFAULT_TRUSTED_PROXY_CIDRS)
    )


    YT_MODE: str = field(default_factory=lambda: os.getenv("YT_MODE", "best_effort").lower())
    YT_CIRCUIT_BREAKER_FAILURES: int = field(default_factory=lambda: _get_int("YT_CIRCUIT_BREAKER_FAILURES", 3))
    YT_CIRCUIT_BREAKER_COOLDOWN_SEC: int = field(default_factory=lambda: _get_int("YT_CIRCUIT_BREAKER_COOLDOWN_SEC", 300))
    YTDLP_PROXY: str = field(default_factory=lambda: os.getenv("YTDLP_PROXY", ""))
    PROXY_RULES: Dict = field(default_factory=lambda: _get_json_dict("PROXY_RULES", {}))
    PROXIED_DAILY_BYTE_BUDGET_GB: int = field(default_factory=lambda: _get_int("PROXIED_DAILY_BYTE_BUDGET_GB", 0))

    COOKIES_FILE: str = field(default_factory=lambda: os.getenv("COOKIES_FILE", ""))
    MEDIA_HOST: str = field(default_factory=lambda: os.getenv("MEDIA_HOST", ""))
    CONTACT_EMAIL: str = field(default_factory=lambda: os.getenv("CONTACT_EMAIL", "support@jungle.local"))
    DMCA_EMAIL: str = field(default_factory=lambda: os.getenv("DMCA_EMAIL", "dmca@jungle.local"))
    BLOCKED_URLS_FILE: str = field(default_factory=lambda: os.getenv("BLOCKED_URLS_FILE", "data/blocked_urls.txt"))


config = AppConfig()

