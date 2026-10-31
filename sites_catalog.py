import hashlib
import json
import logging
import os
from typing import Any, Dict, List, Optional, Set
import yt_dlp
from config import config

logger = logging.getLogger(__name__)

POPULAR_SITES = [
    "YouTube",
    "Instagram",
    "TikTok",
    "Facebook",
    "Twitter",
    "Reddit",
    "Vimeo",
    "Twitch",
    "SoundCloud",
    "Dailymotion",
    "Pinterest",
    "LinkedIn",
    "Bilibili",
    "JioSaavn",
    "Snapchat",
]

# Explicit prettifications for popular or unevenly cased extractor base names
NAME_OVERRIDES = {
    "youtube": "YouTube",
    "tiktok": "TikTok",
    "instagram": "Instagram",
    "facebook": "Facebook",
    "twitter": "Twitter / X",
    "reddit": "Reddit",
    "vimeo": "Vimeo",
    "twitch": "Twitch",
    "soundcloud": "SoundCloud",
    "dailymotion": "Dailymotion",
    "pinterest": "Pinterest",
    "linkedin": "LinkedIn",
    "bilibili": "Bilibili",
    "jiosaavn": "JioSaavn",
    "snapchat": "Snapchat",
    "archive.org": "Archive.org",
    "bandcamp": "Bandcamp",
    "crunchyroll": "Crunchyroll",
    "mixcloud": "Mixcloud",
    "rumble": "Rumble",
    "streamable": "Streamable",
    "vk": "VK",
    "weibo": "Weibo",
    "douyin": "Douyin",
}


def _read_file_lines(filepath: str) -> Set[str]:
    if not filepath or not os.path.exists(filepath):
        return set()
    items = set()
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            for line in f:
                stripped = line.strip()
                if stripped and not stripped.startswith("#"):
                    items.add(stripped.lower())
    except Exception as e:
        logger.warning(f"Error reading {filepath}: {e}")
    return items


def prettify_site_name(base_name: str) -> str:
    """Prettifies an extractor base name for public UI presentation."""
    lower = base_name.lower()
    if lower in NAME_OVERRIDES:
        return NAME_OVERRIDES[lower]

    # If it has dots (like abc.net.au) or already has mixed case, keep it clean
    if "." in base_name or (any(c.isupper() for c in base_name) and any(c.islower() for c in base_name)):
        return base_name

    # If all lowercase, title-case it
    return base_name.capitalize()


class SitesCatalog:
    """
    Catalog of supported sites built from yt-dlp extractors at startup.
    Filters out Generic, adult sites (unless ALLOW_ADULT=True), DRM extractors,
    and non-working extractors.
    """

    def __init__(
        self,
        adult_file: str = "data/adult_extractors.txt",
        drm_file: str = "data/drm_extractors.txt"
    ):
        self.adult_file = adult_file
        self.drm_file = drm_file
        self._cached_catalog: Optional[Dict[str, Any]] = None
        self._etag: Optional[str] = None
        self.build_catalog()

    def build_catalog(self) -> Dict[str, Any]:
        """Builds and caches the filtered sites list and dynamic count."""
        adult_set = _read_file_lines(self.adult_file)
        drm_set = _read_file_lines(self.drm_file)

        raw_base_names: Set[str] = set()

        try:
            for ie in yt_dlp.extractor.gen_extractors():
                # Skip broken or explicitly disabled extractors
                if callable(getattr(ie, "working", None)):
                    try:
                        if not ie.working():
                            continue
                    except Exception:
                        pass
                if getattr(ie, "_ENABLED", True) is False:
                    continue
                if getattr(ie, "_WORKING", True) is False:
                    continue

                # Skip embed/helper extractors without standalone URLs
                if getattr(ie, "_VALID_URL", None) is False:
                    continue

                name = ie.IE_NAME
                base = name.split(":")[0].strip()
                base_lower = base.lower()

                if not base or base_lower in ("generic", "generictag", "default", "html5"):
                    continue

                if base_lower.endswith("search") or name.endswith(":search"):
                    continue

                if not config.ALLOW_ADULT and base_lower in adult_set:
                    continue

                if base_lower in drm_set:
                    continue

                raw_base_names.add(base)
        except Exception as e:
            logger.error(f"Error enumerating yt-dlp extractors: {e}")
            # Fallback to minimal popular list
            raw_base_names = set(POPULAR_SITES)

        # Prettify and deduplicate by lower-case representation
        prettified_map: Dict[str, str] = {}
        for base in raw_base_names:
            clean_name = prettify_site_name(base)
            prettified_map[clean_name.lower()] = clean_name

        sorted_sites = sorted(prettified_map.values(), key=lambda s: s.lower())
        total_count = len(sorted_sites)
        rounded_count = (total_count // 100) * 100
        display_count = f"{rounded_count}+ sites"

        catalog = {
            "popular": POPULAR_SITES,
            "sites": sorted_sites,
            "total_count": total_count,
            "display_count": display_count,
        }

        # Calculate strong ETag from catalog content + yt-dlp version
        content_hash = hashlib.md5(
            (json.dumps(catalog, sort_keys=True) + yt_dlp.version.__version__).encode("utf-8")
        ).hexdigest()
        self._etag = f'"{content_hash}"'
        self._cached_catalog = catalog

        logger.info(f"Built supported sites catalog: {total_count} sites ({display_count}).")
        return self._cached_catalog

    @property
    def catalog(self) -> Dict[str, Any]:
        if self._cached_catalog is None:
            return self.build_catalog()
        return self._cached_catalog

    @property
    def etag(self) -> str:
        if self._etag is None:
            self.build_catalog()
        return self._etag or '""'

    def is_match_etag(self, client_etag: Optional[str]) -> bool:
        if not client_etag:
            return False
        # Remove W/ prefix for weak comparison if needed
        clean_client = client_etag.strip()
        if clean_client.startswith("W/"):
            clean_client = clean_client[2:].strip()
        return clean_client == self.etag.strip()


# Global Singleton
sites_catalog = SitesCatalog()
