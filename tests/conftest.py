import pytest
from gevent.lock import BoundedSemaphore

import app as app_module
from app import app, limiter
import concurrency
import delivery
from config import config
from cache_store import cache_store


@pytest.fixture(autouse=True)
def reset_limiter_and_semaphores():
    """Autouse fixture resetting rate limiter storage and semaphores between tests."""
    # Reset limiter before test
    try:
        limiter.reset()
    except Exception:
        pass
    if hasattr(limiter, "_storage") and hasattr(limiter._storage, "reset"):
        try:
            limiter._storage.reset()
        except Exception:
            pass

    # Refresh semaphores and circuit breaker before test
    concurrency.extract_semaphore = BoundedSemaphore(config.MAX_CONCURRENT_EXTRACT)
    app_module.extract_semaphore = concurrency.extract_semaphore
    app.extract_semaphore = concurrency.extract_semaphore

    concurrency.ffmpeg_semaphore = BoundedSemaphore(config.MAX_CONCURRENT_FFMPEG)
    app_module.ffmpeg_semaphore = concurrency.ffmpeg_semaphore
    app.ffmpeg_semaphore = concurrency.ffmpeg_semaphore

    concurrency.proxy_semaphore = BoundedSemaphore(config.MAX_CONCURRENT_PROXY)
    app_module.proxy_semaphore = concurrency.proxy_semaphore
    app.proxy_semaphore = concurrency.proxy_semaphore

    delivery.youtube_extract_semaphore = BoundedSemaphore(config.MAX_CONCURRENT_YOUTUBE)
    app_module.youtube_extract_semaphore = delivery.youtube_extract_semaphore
    app.youtube_extract_semaphore = delivery.youtube_extract_semaphore

    delivery.youtube_circuit_breaker.failures = 0
    delivery.youtube_circuit_breaker.last_failure_time = 0
    delivery.proxy_router.site_backoffs.clear()
    delivery.proxy_router.daily_bytes_used = 0
    with cache_store._lock:
        cache_store._memory_cache.clear()

    yield

    delivery.proxy_router.site_backoffs.clear()
    delivery.proxy_router.daily_bytes_used = 0
    with cache_store._lock:
        cache_store._memory_cache.clear()

    # Clean up after test
    try:
        limiter.reset()
    except Exception:
        pass
    if hasattr(limiter, "_storage") and hasattr(limiter._storage, "reset"):
        try:
            limiter._storage.reset()
        except Exception:
            pass

    concurrency.extract_semaphore = BoundedSemaphore(config.MAX_CONCURRENT_EXTRACT)
    app_module.extract_semaphore = concurrency.extract_semaphore
    app.extract_semaphore = concurrency.extract_semaphore

    concurrency.ffmpeg_semaphore = BoundedSemaphore(config.MAX_CONCURRENT_FFMPEG)
    app_module.ffmpeg_semaphore = concurrency.ffmpeg_semaphore
    app.ffmpeg_semaphore = concurrency.ffmpeg_semaphore

    concurrency.proxy_semaphore = BoundedSemaphore(config.MAX_CONCURRENT_PROXY)
    app_module.proxy_semaphore = concurrency.proxy_semaphore
    app.proxy_semaphore = concurrency.proxy_semaphore

    delivery.youtube_extract_semaphore = BoundedSemaphore(config.MAX_CONCURRENT_YOUTUBE)
    app_module.youtube_extract_semaphore = delivery.youtube_extract_semaphore
    app.youtube_extract_semaphore = delivery.youtube_extract_semaphore


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client
