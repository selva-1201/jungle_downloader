import datetime
import logging
import time
from typing import Any, Dict, Optional, Tuple
from gevent.lock import BoundedSemaphore
from config import config

logger = logging.getLogger(__name__)

# Dedicated semaphore for YouTube extractions to isolate YouTube bottlenecks
youtube_extract_semaphore = BoundedSemaphore(config.MAX_CONCURRENT_YOUTUBE)


# --- 5.1 HYBRID DELIVERY LOGIC ---
def is_direct_cdn_eligible(
    format_dict: Dict[str, Any],
    extractor_name: str = "",
    direct_link_mode: Optional[str] = None,
    proxy_max_mb: Optional[int] = None
) -> bool:
    """
    Evaluates whether a media format is eligible for direct CDN link download
    instead of proxy-streaming through our VM.

    Requirements per SPEC 5.1:
    - Format is a single progressive http(s) file (contains both video and audio tracks).
    - Needs no special headers/cookies.
    - Not IP-bound (NOT YouTube / googlevideo).
    - Conforms to DIRECT_LINK_MODE (off | large_only | always).
    """
    mode = (direct_link_mode or config.DIRECT_LINK_MODE).lower()
    if mode == "off":
        return False

    vcodec = format_dict.get("vcodec", "none")
    acodec = format_dict.get("acodec", "none")
    protocol = format_dict.get("protocol", "").lower()
    url = format_dict.get("url", "")

    # 1. Must have both audio and video tracks (progressive format)
    if vcodec in ("none", None) or acodec in ("none", None):
        return False

    # 2. Must be progressive HTTP/HTTPS, not HLS/DASH/fragmented streams
    if any(frag in protocol for frag in ("m3u8", "dash", "f4m", "rtmp", "mhtml")):
        return False
    if any(frag in url.lower() for frag in (".m3u8", ".mpd")):
        return False
    if not (url.startswith("http://") or url.startswith("https://")):
        return False

    # 3. Must NOT be IP-bound (e.g. YouTube / googlevideo CDN URLs are signed to client IP)
    ext_lower = (extractor_name or "").lower()
    url_lower = url.lower()
    if any(yt_term in ext_lower for yt_term in ("youtube", "yt")):
        return False
    if any(yt_domain in url_lower for yt_domain in ("googlevideo.com", "youtube.com", "youtu.be")):
        return False

    # 4. Check special headers requirement (e.g. cookies or custom headers)
    http_headers = format_dict.get("http_headers", {})
    if "Cookie" in http_headers or "Authorization" in http_headers:
        return False

    # 5. Evaluate based on DIRECT_LINK_MODE setting
    if mode == "always":
        return True

    if mode == "large_only":
        max_mb = proxy_max_mb or config.PROXY_MAX_MB
        filesize = format_dict.get("filesize") or format_dict.get("filesize_approx") or 0
        return filesize > (max_mb * 1024 * 1024)

    return False


# --- 5.2 YOUTUBE CIRCUIT BREAKER ---
class YouTubeCircuitBreaker:
    """
    Circuit breaker for YouTube extractions to prevent consecutive bot-check / 429
    blocks from exhausting resources and degrading other sites.
    """

    STATE_CLOSED = "CLOSED"        # Normal operations
    STATE_OPEN = "OPEN"            # Tripped, rejecting fast
    STATE_HALF_OPEN = "HALF_OPEN"  # Testing recovery with single probe

    def __init__(
        self,
        failure_threshold: Optional[int] = None,
        cooldown_seconds: Optional[int] = None
    ):
        self.failure_threshold = failure_threshold or config.YT_CIRCUIT_BREAKER_FAILURES
        self.cooldown_seconds = cooldown_seconds or config.YT_CIRCUIT_BREAKER_COOLDOWN_SEC
        self.consecutive_failures = 0
        self.state = self.STATE_CLOSED
        self.tripped_at = 0.0

    def is_available(self) -> Tuple[bool, Optional[str]]:
        """
        Returns (is_available, error_message).
        If unavailable, returns a friendly shielded error string.
        """
        if config.YT_MODE == "disabled":
            return False, "YouTube is temporarily unavailable, other sites work"

        if self.state == self.STATE_OPEN:
            now = time.time()
            if now - self.tripped_at >= self.cooldown_seconds:
                self.state = self.STATE_HALF_OPEN
                logger.info("YouTube Circuit Breaker entering HALF-OPEN state for recovery test.")
                return True, None
            return False, "YouTube is temporarily unavailable, other sites work"

        return True, None

    def record_success(self) -> None:
        """Called upon successful extraction to reset failure counts."""
        self.consecutive_failures = 0
        self.state = self.STATE_CLOSED

    def record_failure(self, error_message: str) -> None:
        """
        Called on extraction error.
        Increments counter and trips if bot detection or 429 occurs.
        """
        err_lower = (error_message or "").lower()
        bot_signals = [
            "sign in to confirm you're not a bot",
            "bot",
            "captcha",
            "429",
            "too many requests",
            "blocking us",
            "automated queries"
        ]

        if any(sig in err_lower for sig in bot_signals):
            self.consecutive_failures += 1
            logger.warning(
                f"YouTube bot block detected ({self.consecutive_failures}/{self.failure_threshold}): {error_message[:60]}"
            )
            if self.consecutive_failures >= self.failure_threshold or self.state == self.STATE_HALF_OPEN:
                self.state = self.STATE_OPEN
                self.tripped_at = time.time()
                logger.error(
                    f"YouTube Circuit Breaker TRIPPED to OPEN. Cooldown for {self.cooldown_seconds}s."
                )
        else:
            # Non-bot failure (e.g. video deleted, private video)
            if self.state == self.STATE_HALF_OPEN:
                self.consecutive_failures = 0
                self.state = self.STATE_CLOSED

    def reset(self) -> None:
        """Manual reset for tests or administrative control."""
        self.consecutive_failures = 0
        self.state = self.STATE_CLOSED
        self.tripped_at = 0.0


# --- 5.3 PROXY ROUTER & DAILY BUDGET ---
class ProxyRouter:
    """
    Manages per-extractor proxy rules, budget enforcement (PROXIED_DAILY_BYTE_BUDGET_GB),
    and egress hygiene tracking.
    """

    def __init__(self):
        self.current_date = datetime.date.today().isoformat()
        self.proxied_bytes_today = 0
        self.extractor_stats: Dict[str, Dict[str, Any]] = {}
        self.site_backoffs: Dict[str, float] = {}

    def _check_and_rollover_day(self) -> None:
        today = datetime.date.today().isoformat()
        if self.current_date != today:
            self.current_date = today
            self.proxied_bytes_today = 0

    def get_proxy_for_extractor(self, extractor_name: str) -> Optional[str]:
        """
        Resolves proxy URL for a specific extractor based on PROXY_RULES and daily budget.
        """
        self._check_and_rollover_day()

        # Check daily budget cap if set
        budget_gb = config.PROXIED_DAILY_BYTE_BUDGET_GB
        if budget_gb > 0:
            max_bytes = budget_gb * 1024 * 1024 * 1024
            if self.proxied_bytes_today >= max_bytes:
                logger.warning(
                    f"Proxied daily byte budget of {budget_gb} GB exceeded ({self.proxied_bytes_today} bytes). Routing direct."
                )
                return None

        # Per-extractor rules take highest precedence
        rules = config.PROXY_RULES
        ext_lower = (extractor_name or "").lower()

        for pattern, proxy_url in rules.items():
            if pattern.lower() == ext_lower or pattern.lower() in ext_lower:
                return proxy_url

        # Global fallback proxy
        if config.YTDLP_PROXY:
            return config.YTDLP_PROXY

        return None

    def record_proxied_bytes(self, num_bytes: int) -> None:
        """Tracks bandwidth transferred through proxy connections."""
        if num_bytes <= 0:
            return
        self._check_and_rollover_day()
        self.proxied_bytes_today += num_bytes

    def record_request(self, extractor: str) -> None:
        """Egress hygiene: increments per-extractor request metrics."""
        ext = extractor.lower() if extractor else "unknown"
        if ext not in self.extractor_stats:
            self.extractor_stats[ext] = {"requests": 0, "success": 0, "failures": 0, "last_status": "ok"}
        self.extractor_stats[ext]["requests"] += 1

    def record_result(self, extractor: str, success: bool, error: Optional[str] = None) -> None:
        """Records outcome for IP reputation monitoring."""
        ext = extractor.lower() if extractor else "unknown"
        if ext not in self.extractor_stats:
            self.record_request(ext)

        if success:
            self.extractor_stats[ext]["success"] += 1
            self.extractor_stats[ext]["last_status"] = "ok"
        else:
            self.extractor_stats[ext]["failures"] += 1
            self.extractor_stats[ext]["last_status"] = "error"
            if error and ("429" in error or "too many requests" in error.lower()):
                self.site_backoffs[ext] = time.time() + 60.0

    def is_site_throttled(self, extractor: str) -> bool:
        """Checks if site recently returned 429 and is currently in cooldown."""
        ext = extractor.lower() if extractor else "unknown"
        backoff_until = self.site_backoffs.get(ext, 0.0)
        return time.time() < backoff_until

    def get_metrics_snapshot(self) -> Dict[str, Any]:
        """Returns snapshot of current bandwidth and per-extractor metrics."""
        self._check_and_rollover_day()
        return {
            "date": self.current_date,
            "proxied_bytes_today": self.proxied_bytes_today,
            "proxied_mb_today": round(self.proxied_bytes_today / (1024 * 1024), 2),
            "budget_gb": config.PROXIED_DAILY_BYTE_BUDGET_GB,
            "extractors": self.extractor_stats
        }


# Global Singletons
youtube_circuit_breaker = YouTubeCircuitBreaker()
proxy_router = ProxyRouter()

