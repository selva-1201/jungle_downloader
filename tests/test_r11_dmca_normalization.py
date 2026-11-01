import os
import time
import pytest
from app import app
from blocklist import BlocklistManager, normalize_url
from cache_store import cache_store


def test_normalize_url_variants():
    # Strips www, fragment, and tracking query params, keeps functional param 'v'
    url1 = "https://www.youtube.com/watch?v=dQw4w9WgXcQ&utm_source=twitter&si=abcdef#t=10"
    url2 = "https://youtube.com/watch?si=999&v=dQw4w9WgXcQ"
    url3 = "https://www.youtube.com/watch?utm_campaign=winter&v=dQw4w9WgXcQ&fbclid=xyz"

    norm1 = normalize_url(url1)
    norm2 = normalize_url(url2)
    norm3 = normalize_url(url3)

    assert norm1 == "https://youtube.com/watch?v=dQw4w9WgXcQ"
    assert norm2 == norm1
    assert norm3 == norm1


def test_blocked_urls_prefix_and_dmca_variants(tmp_path):
    urls_file = tmp_path / "blocked_urls.txt"
    urls_file.write_text(
        "https://example.com/watch?v=blocked123\n"
        "https://takedown.example/clips/\n"
        "domain:piratedomain.org\n"
        "extractor:vimeo\n",
        encoding="utf-8"
    )

    mgr = BlocklistManager(
        adult_file=str(tmp_path / "adult.txt"),
        blocked_domains_file=str(tmp_path / "domains.txt"),
        blocked_urls_file=str(urls_file)
    )

    # 1. Variant of exact URL with www, tracking params, and fragment
    variant_url = "https://www.example.com/watch?utm_source=mail&v=blocked123&si=777#frag"
    blocked, reason = mgr.is_url_blocked(variant_url)
    assert blocked is True
    assert "DMCA" in reason or "removed" in reason

    # 2. Prefix matching under /clips/
    clip_url = "https://www.takedown.example/clips/special_episode?fbclid=123"
    blocked_clip, _ = mgr.is_url_blocked(clip_url)
    assert blocked_clip is True

    # 3. domain: prefix
    sub_url = "https://deep.sub.piratedomain.org/media.mp4"
    blocked_dom, _ = mgr.is_url_blocked(sub_url)
    assert blocked_dom is True

    # 4. extractor: prefix
    vimeo_url = "https://vimeo.com/987654321"
    blocked_ext, _ = mgr.is_url_blocked(vimeo_url)
    assert blocked_ext is True

    # 5. Unrelated URL is allowed
    clean_url = "https://www.youtube.com/watch?v=allowed_clip"
    blocked_clean, _ = mgr.is_url_blocked(clean_url)
    assert blocked_clean is False


def test_token_issued_before_block_is_refused_on_hot_reload(tmp_path, monkeypatch):
    from blocklist import blocklist_manager

    urls_file = tmp_path / "blocked_urls.txt"
    urls_file.write_text("# Initial empty blocklist\n", encoding="utf-8")

    monkeypatch.setattr(blocklist_manager, "blocked_urls_file", str(urls_file))
    blocklist_manager._mtimes["urls"] = 0.0
    blocklist_manager.reload_if_modified()

    target_url = "https://www.youtube.com/watch?v=hotreload123"

    # Pre-populate token in cache_store
    dummy_info = {
        "id": "hotreload123",
        "title": "Hot Reload Video",
        "extractor": "youtube",
        "formats": [
            {"format_id": "18", "url": "https://example.com/video.mp4", "ext": "mp4", "acodec": "aac", "vcodec": "h264"}
        ]
    }
    token = cache_store.store_metadata(dummy_info, original_url=target_url)

    client = app.test_client()

    # Initial probe succeeds (204)
    res_before = client.get(f"/api/stream?token={token}&probe=1")
    assert res_before.status_code == 204

    # Now edit the file to add this URL to blocklist and update mtime
    time.sleep(0.05)
    with open(urls_file, "a", encoding="utf-8") as f:
        f.write("https://www.youtube.com/watch?v=hotreload123\n")
    future_mtime = time.time() + 2
    os.utime(urls_file, (future_mtime, future_mtime))

    # Token re-check on /api/stream must now reject with 400
    res_stream = client.get(f"/api/stream?token={token}&probe=1")
    assert res_stream.status_code == 400
    assert "DMCA" in res_stream.get_json()["error"] or "removed" in res_stream.get_json()["error"]

    # Token re-check on /api/preview must also reject with 400
    res_preview = client.get(f"/api/preview?token={token}")
    assert res_preview.status_code == 400
    assert "DMCA" in res_preview.get_json()["error"] or "removed" in res_preview.get_json()["error"]
