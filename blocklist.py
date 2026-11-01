from collections import OrderedDict
import os
import threading
import urllib.parse
from typing import List, Optional, Set, Tuple
import yt_dlp
from config import config

MAX_URL_LENGTH = 2048

TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "utm_id", "utm_name", "fbclid", "si", "igsh", "gclid", "mc_cid", "mc_eid"
}


def is_tracking_param(param_name: str) -> bool:
    p = param_name.lower()
    return p in TRACKING_PARAMS or p.startswith("utm_")


def normalize_url(url: str, strip_scheme: bool = False) -> str:
    """
    Normalizes a URL for canonical blocklist matching:
    - Strips whitespace
    - Lowercases scheme and hostname
    - Strips 'www.' prefix from hostname
    - Strips fragment
    - Strips tracking query parameters (utm_*, fbclid, si, igsh, gclid, etc.)
    - Sorts remaining functional query parameters (e.g. keeping 'v', 'id')
    """
    if not url:
        return ""
    clean = url.strip()
    if not clean:
        return ""

    has_scheme = "://" in clean
    target = clean if has_scheme else ("https://" + clean)
    try:
        parsed = urllib.parse.urlsplit(target)
    except Exception:
        return clean

    host = (parsed.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]

    port_str = ""
    if parsed.port and not ((parsed.scheme == "http" and parsed.port == 80) or (parsed.scheme == "https" and parsed.port == 443)):
        port_str = f":{parsed.port}"
    netloc = f"{host}{port_str}" if host else ""

    params = []
    if parsed.query:
        for k, v in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True):
            if not is_tracking_param(k):
                params.append((k, v))
        params.sort()
    new_query = urllib.parse.urlencode(params)

    path = parsed.path or ""

    scheme = parsed.scheme.lower() if has_scheme and not strip_scheme else ""
    if strip_scheme or not has_scheme:
        res = f"{netloc}{path}"
        if new_query:
            res = f"{res}?{new_query}"
        return res
    else:
        return urllib.parse.urlunsplit((scheme, netloc, path, new_query, ""))


class BlocklistManager:
    """
    Manages adult extractor filtering and hot-reloading domain/URL blocklists.
    Caches extractor resolution per URL and per hostname for ultra-fast matching.
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
        self._blocked_url_prefixes: List[str] = []
        self._blocked_url_domains: Set[str] = set()
        self._blocked_url_extractors: Set[str] = set()

        self._mtimes = {
            "adult": 0.0,
            "domains": 0.0,
            "urls": 0.0
        }

        # LRU caches for extractor matching
        self._url_extractor_cache: OrderedDict[str, Optional[str]] = OrderedDict()
        self._host_extractor_cache: OrderedDict[str, str] = OrderedDict()
        self._cache_lock = threading.Lock()

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

    def _read_file_lines_raw(self, filepath: str) -> List[str]:
        if not filepath or not os.path.exists(filepath):
            return []
        items = []
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                for line in f:
                    stripped = line.strip()
                    if stripped and not stripped.startswith("#"):
                        items.append(stripped)
        except Exception:
            pass
        return items

    def _load_blocked_urls(self, filepath: str) -> None:
        raw_lines = self._read_file_lines_raw(filepath)
        self._blocked_urls = set(line.lower() for line in raw_lines)
        prefixes: List[str] = []
        domains: Set[str] = set()
        extractors: Set[str] = set()

        for line in raw_lines:
            lower_line = line.lower()
            if lower_line.startswith("domain:"):
                dom = line[len("domain:"):].strip().lower()
                if dom.startswith("www."):
                    dom = dom[4:]
                if dom:
                    domains.add(dom)
            elif lower_line.startswith("extractor:"):
                ext = line[len("extractor:"):].strip().lower()
                if ext:
                    extractors.add(ext)
            else:
                norm = normalize_url(line)
                if norm:
                    prefixes.append(norm)

        self._blocked_url_prefixes = prefixes
        self._blocked_url_domains = domains
        self._blocked_url_extractors = extractors

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
                self._load_blocked_urls(self.blocked_urls_file)
                self._mtimes["urls"] = mtime

    def find_extractor_name(self, url: str) -> Optional[str]:
        """
        Finds the matching yt-dlp extractor name without network access.
        Caches results per URL and per hostname to avoid scanning ~1800 regexes repeatedly.
        Caps URL length to prevent ReDoS on malicious inputs.
        """
        if not url or len(url) > MAX_URL_LENGTH:
            return None

        clean_url = url.strip()

        with self._cache_lock:
            if clean_url in self._url_extractor_cache:
                return self._url_extractor_cache[clean_url]

        parsed = urllib.parse.urlsplit(clean_url)
        hostname = (parsed.hostname or "").lower()

        # Fast path: check if this hostname has a known matching extractor
        if hostname:
            with self._cache_lock:
                cached_ie_name = self._host_extractor_cache.get(hostname)
            if cached_ie_name:
                ie = self._ydl._ies.get(cached_ie_name)
                if ie and ie.suitable(clean_url):
                    with self._cache_lock:
                        if len(self._url_extractor_cache) >= 1024:
                            self._url_extractor_cache.popitem(last=False)
                        self._url_extractor_cache[clean_url] = ie.IE_NAME
                    return ie.IE_NAME

        # Fallback: scan all registered extractors
        for ie in self._ydl._ies.values():
            if ie.suitable(clean_url):
                with self._cache_lock:
                    if hostname:
                        if len(self._host_extractor_cache) >= 500:
                            self._host_extractor_cache.popitem(last=False)
                        self._host_extractor_cache[hostname] = ie.IE_NAME
                    if len(self._url_extractor_cache) >= 1024:
                        self._url_extractor_cache.popitem(last=False)
                    self._url_extractor_cache[clean_url] = ie.IE_NAME
                return ie.IE_NAME

        with self._cache_lock:
            if len(self._url_extractor_cache) >= 1024:
                self._url_extractor_cache.popitem(last=False)
            self._url_extractor_cache[clean_url] = None
        return None

    def is_adult_extractor(self, extractor_name: str) -> bool:
        """Checks if extractor name belongs to adult extractors list."""
        if not extractor_name:
            return False
        self.reload_if_modified()
        lower = extractor_name.lower().split(":")[0]
        return lower in self._adult_extractors or extractor_name.lower() in self._adult_extractors

    def is_url_blocked(
        self,
        url: str,
        allow_adult: Optional[bool] = None,
        check_supported_site: bool = True
    ) -> Tuple[bool, str]:
        """
        Evaluates whether a given URL is permitted.
        Returns (is_blocked, rejection_reason).
        Performs normalized URL, domain, and extractor checks.
        """
        self.reload_if_modified()
        if allow_adult is None:
            allow_adult = config.ALLOW_ADULT

        if not url:
            return True, "Invalid URL structure."

        if len(url) > MAX_URL_LENGTH:
            return True, "URL exceeds maximum permitted length."

        clean_url = url.strip()
        lower_url = clean_url.lower()

        # 1. Exact raw match
        if lower_url in self._blocked_urls or clean_url in self._blocked_urls:
            return True, "This link has been removed pursuant to a DMCA takedown notice or administrative request."

        # 2. Normalized URL & Prefix match
        norm_url = normalize_url(clean_url)
        norm_no_scheme = normalize_url(clean_url, strip_scheme=True)
        for prefix in self._blocked_url_prefixes:
            if norm_url == prefix or norm_url.startswith(prefix):
                return True, "This link has been removed pursuant to a DMCA takedown notice or administrative request."
            if norm_no_scheme == prefix or norm_no_scheme.startswith(prefix):
                return True, "This link has been removed pursuant to a DMCA takedown notice or administrative request."
            prefix_lower = prefix.lower()
            if norm_url.lower() == prefix_lower or norm_url.lower().startswith(prefix_lower):
                return True, "This link has been removed pursuant to a DMCA takedown notice or administrative request."
            if norm_no_scheme.lower() == prefix_lower or norm_no_scheme.lower().startswith(prefix_lower):
                return True, "This link has been removed pursuant to a DMCA takedown notice or administrative request."

        # 3. Domain / parent-domain match (both from blocked_domains and domain: prefixes)
        try:
            target = clean_url if "://" in clean_url else ("https://" + clean_url)
            parsed = urllib.parse.urlsplit(target)
            hostname = (parsed.hostname or "").lower()
            if hostname.startswith("www."):
                hostname = hostname[4:]
            if hostname:
                parts = hostname.split(".")
                for i in range(len(parts) - 1):
                    sub = ".".join(parts[i:])
                    if sub in self._blocked_domains:
                        return True, "This domain is not supported."
                    if sub in self._blocked_url_domains:
                        return True, "This link has been removed pursuant to a DMCA takedown notice or administrative request."
        except Exception:
            return True, "Invalid URL structure."

        # 4. Extractor checks
        ie_name = self.find_extractor_name(clean_url)
        if ie_name:
            base_name = ie_name.split(":")[0].lower()
            full_name = ie_name.lower()

            # Extractor-level takedown (extractor: prefix in blocked_urls.txt)
            if base_name in self._blocked_url_extractors or full_name in self._blocked_url_extractors:
                return True, "This link has been removed pursuant to a DMCA takedown notice or administrative request."

            # Adult content restriction
            if not allow_adult:
                if base_name in self._adult_extractors or full_name in self._adult_extractors:
                    return True, "This site is not supported."

            if check_supported_site and ie_name.lower() == "generic" and not config.ALLOW_GENERIC:
                return True, "This site is not supported."
        elif check_supported_site:
            if not config.ALLOW_GENERIC:
                return True, "This site is not supported."

        return False, ""


# Singleton blocklist manager instance
blocklist_manager = BlocklistManager()
