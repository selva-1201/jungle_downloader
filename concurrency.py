import logging
import os
import shutil
import time
from typing import Optional
from gevent.lock import BoundedSemaphore
from config import config

logger = logging.getLogger(__name__)

# Semaphores controlling simultaneous heavy tasks
extract_semaphore = BoundedSemaphore(config.MAX_CONCURRENT_EXTRACT)
ffmpeg_semaphore = BoundedSemaphore(config.MAX_CONCURRENT_FFMPEG)

TEMP_DIR = os.getenv("JUNGLE_TEMP_DIR", "/tmp/jungle_media")
os.makedirs(TEMP_DIR, exist_ok=True)


def cleanup_temp_dir(max_age_seconds: int = 3600) -> int:
    """
    Janitor function: removes temporary files created by media operations
    older than max_age_seconds.
    """
    removed_count = 0
    now = time.time()
    if not os.path.exists(TEMP_DIR):
        return 0

    try:
        for entry in os.scandir(TEMP_DIR):
            try:
                if entry.is_file():
                    stat = entry.stat()
                    if now - stat.st_mtime > max_age_seconds:
                        os.unlink(entry.path)
                        removed_count += 1
                elif entry.is_dir():
                    stat = entry.stat()
                    if now - stat.st_mtime > max_age_seconds:
                        shutil.rmtree(entry.path, ignore_errors=True)
                        removed_count += 1
            except Exception as e:
                logger.debug(f"Error cleaning entry {entry.path}: {e}")
    except Exception as e:
        logger.warning(f"Error scanning temp directory {TEMP_DIR}: {e}")

    return removed_count


# Run startup cleanup
cleanup_temp_dir()

