#!/usr/bin/env python3
import json
import os
import sys
import time
from typing import Any, Dict, List
import yt_dlp

# Note: smoke_urls.json was previously located at tests/smoke_urls.json (now in data/ so it persists in Docker image)
DEFAULT_CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "smoke_urls.json")
OLD_CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tests", "smoke_urls.json")


def run_smoke_test(config_path: str = DEFAULT_CONFIG_PATH) -> bool:
    """
    Extracts metadata (download=False) for key platforms.
    Reports pass/fail per site and recognizes expected_fail_on_datacenter entries.
    """
    if not os.path.exists(config_path):
        if config_path == DEFAULT_CONFIG_PATH and os.path.exists(OLD_CONFIG_PATH):
            config_path = OLD_CONFIG_PATH
        else:
            print(f"Error: smoke test config file '{config_path}' not found.")
            return False

    with open(config_path, "r", encoding="utf-8") as f:
        targets: List[Dict[str, Any]] = json.load(f)

    print("\n=======================================================")
    print("  Jungle Media Downloader — yt-dlp Smoke Test")
    print(f"  yt-dlp version: {yt_dlp.version.__version__}")
    print(f"  Target platforms: {len(targets)}")
    print("=======================================================\n")

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
            expected_fail = target.get("expected_fail_on_datacenter", False)
            start = time.time()
            try:
                info = ydl.extract_info(url, download=False)
                elapsed = time.time() - start
                title = info.get("title", "No Title")[:35]
                formats_count = len(info.get("formats", []))
                print(f"  [PASS]          {platform:12} ({elapsed:4.2f}s) | formats: {formats_count:2} | \"{title}\"")
                results.append({"platform": platform, "success": True, "error": None, "expected_fail": expected_fail})
            except Exception as e:
                elapsed = time.time() - start
                err_msg = str(e).split("\n")[0][:60]
                tag = "[EXPECTED_FAIL]" if expected_fail else "[FAIL]"
                print(f"  {tag:15} {platform:12} ({elapsed:4.2f}s) | error: {err_msg}")
                results.append({"platform": platform, "success": False, "error": str(e), "expected_fail": expected_fail})
                if not expected_fail:
                    overall_pass = False

    passed_count = sum(1 for r in results if r["success"])
    expected_fail_count = sum(1 for r in results if not r["success"] and r.get("expected_fail"))
    unexpected_fail_count = sum(1 for r in results if not r["success"] and not r.get("expected_fail"))
    total_count = len(results)
    print(f"\nSummary: {passed_count}/{total_count} operational, {expected_fail_count} expected datacenter blocks, {unexpected_fail_count} unexpected failures.")

    return overall_pass


if __name__ == "__main__":
    cfg = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_CONFIG_PATH
    success = run_smoke_test(cfg)
    sys.exit(0 if success else 1)
