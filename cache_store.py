import json
import logging
import secrets
import threading
import time
from typing import Any, Dict, Optional
from cachetools import TTLCache
from config import config

logger = logging.getLogger(__name__)


class CacheStore:
    """
    In-memory TTL cache with optional Redis backend for extracted video metadata.
    Provides opaque single-use/multi-use session tokens to prevent repeated yt-dlp extractions.
    """

    def __init__(self, maxsize: int = 1000, ttl: Optional[int] = None):
        self.ttl = ttl or config.META_CACHE_TTL
        self.maxsize = maxsize
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
        Stores extracted info dict under an opaque random token.
        Returns the opaque token string.
        """
        token = secrets.token_urlsafe(24)
        payload = {
            "token": token,
            "original_url": original_url,
            "proxy": proxy,
            "created_at": time.time(),
            "info": info
        }

        if self._redis_client:
            try:
                self._redis_client.setex(
                    f"jungle:media:{token}",
                    self.ttl,
                    json.dumps(payload, default=str)
                )
                return token
            except Exception as e:
                logger.error(f"Redis store error: {e}, storing in memory fallback.")

        with self._lock:
            self._memory_cache[token] = payload

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

    def delete_metadata(self, token: str) -> None:
        """Removes metadata from cache."""
        if not token:
            return

        if self._redis_client:
            try:
                self._redis_client.delete(f"jungle:media:{token}")
            except Exception:
                pass

        with self._lock:
            self._memory_cache.pop(token, None)


# Global singleton cache instance
cache_store = CacheStore()

