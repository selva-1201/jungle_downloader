import os
import urllib.parse
from typing import Optional, Set, Tuple
import yt_dlp
from config import config


class BlocklistManager:
    """
    Manages adult extractor filtering and hot-reloading domain/URL blocklists.
    """

    def __init__(
        self,
        adult_file: str = "data/adult_extractors.txt",
        blocked_domains_file: str = "data/blocked_domains.txt",
        blocked_urls_file: Optional[str] = None
    ):
        self.adult_file = adult_file
        self.blocked_domains_file = blocked_domains_file
        self.blocked_urls_file = blocked_urls_file or config.BLOCKED_URLS_FILE

        self._adult_extractors: Set[str] = set()
        self._blocked_domains: Set[str] = set()
        self._blocked_urls: Set[str] = set()

        self._mtimes = {
            "adult": 0.0,
            "domains": 0.0,
            "urls": 0.0
        }

        # Shared YoutubeDL instance for fast extractor matching without network calls
        self._ydl = yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True})
        self.reload_if_modified()

    def _read_file_lines(self, filepath: str) -> Set[str]:
        if not filepath or not os.path.exists(filepath):
            return set()
        items = set()
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                for line in f:
                    stripped = line.strip()
                    if stripped and not stripped.startswith("#"):
                        items.add(stripped.lower())
        except Exception:
            pass
        return items

    def reload_if_modified(self) -> None:
        """Hot-reloads blocklist files if their disk mtime has changed."""
        # 1. Adult extractors
        if os.path.exists(self.adult_file):
            mtime = os.path.getmtime(self.adult_file)
            if mtime > self._mtimes["adult"]:
                self._adult_extractors = self._read_file_lines(self.adult_file)
                self._mtimes["adult"] = mtime

        # 2. Blocked domains
        if os.path.exists(self.blocked_domains_file):
            mtime = os.path.getmtime(self.blocked_domains_file)
            if mtime > self._mtimes["domains"]:
                self._blocked_domains = self._read_file_lines(self.blocked_domains_file)
                self._mtimes["domains"] = mtime

        # 3. Blocked URLs (DMCA takedowns)
        if os.path.exists(self.blocked_urls_file):
            mtime = os.path.getmtime(self.blocked_urls_file)
            if mtime > self._mtimes["urls"]:
                self._blocked_urls = self._read_file_lines(self.blocked_urls_file)
                self._mtimes["urls"] = mtime

    def find_extractor_name(self, url: str) -> Optional[str]:
        """Finds the matching yt-dlp extractor name without network access."""
        for ie in self._ydl._ies.values():
            if ie.suitable(url):
                return ie.IE_NAME
        return None

    def is_url_blocked(self, url: str, allow_adult: Optional[bool] = None) -> Tuple[bool, str]:
        """
        Evaluates whether a given URL is permitted.
        Returns (is_blocked, rejection_reason).
        """
        self.reload_if_modified()
        if allow_adult is None:
            allow_adult = config.ALLOW_ADULT

        clean_url = url.strip()
        lower_url = clean_url.lower()

        # 1. Exact URL match (DMCA / administrative takedown)
        if lower_url in self._blocked_urls or clean_url in self._blocked_urls:
            return True, "This link has been removed pursuant to a DMCA takedown notice or administrative request."

        # 2. Domain / parent-domain match
        try:
            parsed = urllib.parse.urlsplit(clean_url)
            hostname = (parsed.hostname or "").lower()
            if hostname:
                parts = hostname.split(".")
                for i in range(len(parts) - 1):
                    sub = ".".join(parts[i:])
                    if sub in self._blocked_domains:
                        return True, "This domain is not supported."
        except Exception:
            return True, "Invalid URL structure."

        # 3. Match against yt-dlp extractors
        ie_name = self.find_extractor_name(clean_url)
        if not ie_name:
            if not config.ALLOW_GENERIC:
                return True, "This site is not supported."
            return False, ""

        if ie_name.lower() == "generic" and not config.ALLOW_GENERIC:
            return True, "This site is not supported."

        base_name = ie_name.split(":")[0].lower()
        full_name = ie_name.lower()

        # 4. Adult content restriction
        if not allow_adult:
            if base_name in self._adult_extractors or full_name in self._adult_extractors:
                return True, "This site is not supported."

        return False, ""


# Singleton blocklist manager
blocklist_manager = BlocklistManager()

