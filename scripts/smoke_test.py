#!/usr/bin/env python3
import json
import os
import sys
import time
from typing import Dict, List
import yt_dlp

DEFAULT_CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tests", "smoke_urls.json")


def run_smoke_test(config_path: str = DEFAULT_CONFIG_PATH) -> bool:
    """
    Extracts metadata (download=False) for key platforms.
    Reports pass/fail per site and returns overall success status.
    """
    if not os.path.exists(config_path):
        print(f"Error: smoke test config file '{config_path}' not found.")
        return False

    with open(config_path, "r", encoding="utf-8") as f:
        targets: List[Dict[str, str]] = json.load(f)

    print(f"\n=======================================================")
    print(f"  Jungle Media Downloader — yt-dlp Smoke Test")
    print(f"  yt-dlp version: {yt_dlp.version.__version__}")
    print(f"  Target platforms: {len(targets)}")
    print(f"=======================================================\n")

    results = []
    overall_pass = True

    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": False,
        "socket_timeout": 15,
        "cachedir": False,
    }

    # Use cookies if available
    cookies_path = os.getenv("COOKIES_FILE", "")
    if cookies_path and os.path.exists(cookies_path):
        ydl_opts["cookiefile"] = cookies_path

    proxy = os.getenv("YTDLP_PROXY", "")
    if proxy:
        ydl_opts["proxy"] = proxy

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        for target in targets:
            platform = target.get("platform", "Unknown")
            url = target.get("url", "")
            start = time.time()
            try:
                info = ydl.extract_info(url, download=False)
                elapsed = time.time() - start
                title = info.get("title", "No Title")[:35]
                formats_count = len(info.get("formats", []))
                print(f"  [PASS] {platform:12} ({elapsed:4.2f}s) | formats: {formats_count:2} | \"{title}\"")
                results.append({"platform": platform, "success": True, "error": None})
            except Exception as e:
                elapsed = time.time() - start
                err_msg = str(e).split("\n")[0][:60]
                print(f"  [FAIL] {platform:12} ({elapsed:4.2f}s) | error: {err_msg}")
                results.append({"platform": platform, "success": False, "error": str(e)})
                overall_pass = False

    passed_count = sum(1 for r in results if r["success"])
    total_count = len(results)
    print(f"\nSummary: {passed_count}/{total_count} platforms operational.")

    return overall_pass


if __name__ == "__main__":
    cfg = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_CONFIG_PATH
    success = run_smoke_test(cfg)
    sys.exit(0 if success else 1)

