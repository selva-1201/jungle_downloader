import json
import logging
import secrets
import threading
import time
from typing import Any, Dict, Optional
from cachetools import TTLCache
from config import config

logger = logging.getLogger(__name__)

MAX_PAYLOAD_BYTES = 512 * 1024  # 512 KB limit per cached media payload


def trim_format(fmt: Dict[str, Any], strip_secrets: bool = False) -> Dict[str, Any]:
    """
    Retains only essential streaming/format fields.
    Drops comments, subtitles, and internal parser artifacts.
    """
    if not isinstance(fmt, dict):
        return {}

    trimmed: Dict[str, Any] = {
        "format_id": fmt.get("format_id"),
        "url": fmt.get("url"),
        "protocol": fmt.get("protocol"),
        "vcodec": fmt.get("vcodec"),
        "acodec": fmt.get("acodec"),
        "ext": fmt.get("ext"),
        "height": fmt.get("height"),
        "resolution": fmt.get("resolution"),
        "tbr": fmt.get("tbr"),
        "abr": fmt.get("abr"),
        "vbr": fmt.get("vbr"),
        "filesize": fmt.get("filesize"),
        "filesize_approx": fmt.get("filesize_approx"),
    }
    if "fragments" in fmt:
        trimmed["fragments"] = fmt["fragments"]

    # Filter http_headers
    raw_headers = fmt.get("http_headers")
    if isinstance(raw_headers, dict):
        headers = dict(raw_headers)
        if strip_secrets:
            headers.pop("Cookie", None)
            headers.pop("cookie", None)
            headers.pop("Authorization", None)
            headers.pop("authorization", None)
        trimmed["http_headers"] = headers

    return {k: v for k, v in trimmed.items() if v is not None}


def trim_info(info: Dict[str, Any], strip_secrets: bool = False) -> Dict[str, Any]:
    """
    Trims raw yt-dlp info dict to essential fields needed for delivery and streaming.
    Drops huge descriptions, subtitles, automatic captions, comments, and thumbnails lists.
    """
    if not isinstance(info, dict):
        return {}

    trimmed: Dict[str, Any] = {
        "id": info.get("id"),
        "title": info.get("title"),
        "thumbnail": info.get("thumbnail"),
        "duration": info.get("duration"),
        "extractor": info.get("extractor"),
        "webpage_url": info.get("webpage_url"),
    }

    formats = info.get("formats", [])
    if isinstance(formats, list):
        trimmed["formats"] = [
            trim_format(f, strip_secrets=strip_secrets)
            for f in formats
            if isinstance(f, dict)
        ]
    else:
        trimmed["formats"] = []

    return {k: v for k, v in trimmed.items() if v is not None}


class CacheStore:
    """
    In-memory TTL cache with optional Redis backend for extracted video metadata.
    Provides opaque random tokens to prevent repeated yt-dlp extractions.
    Trims payload before caching and strips secrets before Redis storage.
    """

    def __init__(self, maxsize: Optional[int] = None, ttl: Optional[int] = None):
        self.ttl = ttl or config.META_CACHE_TTL
        self.maxsize = maxsize or config.CACHE_MAXSIZE
        self._lock = threading.Lock()
        self._memory_cache: TTLCache = TTLCache(maxsize=self.maxsize, ttl=self.ttl)

        self._redis_client = None
        if config.CACHE_BACKEND == "redis":
            try:
                import redis
                self._redis_client = redis.Redis.from_url(config.REDIS_URL, decode_responses=True)
                self._redis_client.ping()
                logger.info("Connected to Redis cache backend.")
            except Exception as e:
                logger.warning(f"Failed to connect to Redis ({e}), falling back to in-memory TTLCache.")
                self._redis_client = None

    def store_metadata(self, info: Dict[str, Any], original_url: str, proxy: Optional[str] = None) -> str:
        """
        Stores trimmed info dict under an opaque random token.
        Returns the opaque token string.
        """
        token = secrets.token_urlsafe(24)

        # 1. In-memory representation (trimmed, keeps headers needed for current instance)
        mem_info = trim_info(info, strip_secrets=False)

        # Byte size guard: if formats list is enormous (>512KB), keep best 35 formats (tail of yt-dlp worst-to-best list)
        try:
            raw_json = json.dumps(mem_info, default=str)
            if len(raw_json.encode("utf-8")) > MAX_PAYLOAD_BYTES:
                formats = mem_info.get("formats", [])
                if len(formats) > 35:
                    mem_info["formats"] = formats[-35:]
        except Exception:
            pass

        payload_memory = {
            "token": token,
            "original_url": original_url,
            "proxy": proxy,
            "created_at": time.time(),
            "info": mem_info
        }

        # 2. Redis representation (strictly strips Cookie / Authorization headers)
        if self._redis_client:
            try:
                redis_info = trim_info(info, strip_secrets=True)
                if len(redis_info.get("formats", [])) > 35:
                    redis_info["formats"] = redis_info["formats"][-35:]

                payload_redis = {
                    "token": token,
                    "original_url": original_url,
                    "proxy": proxy,
                    "created_at": payload_memory["created_at"],
                    "info": redis_info
                }

                self._redis_client.setex(
                    f"jungle:media:{token}",
                    self.ttl,
                    json.dumps(payload_redis, default=str)
                )
                return token
            except Exception as e:
                logger.error(f"Redis store error: {e}, storing in memory fallback.")

        with self._lock:
            self._memory_cache[token] = payload_memory

        return token

    def get_metadata(self, token: str) -> Optional[Dict[str, Any]]:
        """
        Retrieves cached media data by token.
        Returns None if expired or not found.
        """
        if not token:
            return None

        if self._redis_client:
            try:
                raw = self._redis_client.get(f"jungle:media:{token}")
                if raw:
                    return json.loads(raw)
                return None
            except Exception as e:
                logger.error(f"Redis retrieval error: {e}, falling back to memory.")

        with self._lock:
            return self._memory_cache.get(token)


# Global singleton cache instance
cache_store = CacheStore()
