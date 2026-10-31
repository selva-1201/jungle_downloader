import os
import time
import pytest
from blocklist import BlocklistManager


@pytest.fixture
def temp_blocklist(tmp_path):
    adult_file = tmp_path / "adult.txt"
    domains_file = tmp_path / "domains.txt"
    urls_file = tmp_path / "urls.txt"

    adult_file.write_text("pornhub\nxvideos\nspankbang\n", encoding="utf-8")
    domains_file.write_text("copyright-infringer.com\nblocked-site.net\n", encoding="utf-8")
    urls_file.write_text("https://example.com/dmca-takedown-clip\n", encoding="utf-8")

    mgr = BlocklistManager(
        adult_file=str(adult_file),
        blocked_domains_file=str(domains_file),
        blocked_urls_file=str(urls_file)
    )
    return mgr, adult_file, domains_file, urls_file


def test_adult_extractor_rejection_when_disallowed(temp_blocklist):
    mgr, _, _, _ = temp_blocklist
    # PornHub should be blocked when allow_adult=False
    blocked, reason = mgr.is_url_blocked(
        "https://www.pornhub.com/view_video.php?viewkey=ph5e9754",
        allow_adult=False
    )
    assert blocked is True
    assert reason == "This site is not supported."


def test_adult_extractor_permitted_when_allowed(temp_blocklist):
    mgr, _, _, _ = temp_blocklist
    blocked, _ = mgr.is_url_blocked(
        "https://www.pornhub.com/view_video.php?viewkey=ph5e9754",
        allow_adult=True
    )
    assert blocked is False


def test_mainstream_and_whitelisted_extractors_allowed(temp_blocklist):
    mgr, _, _, _ = temp_blocklist
    # YouTube should be permitted
    blocked_yt, _ = mgr.is_url_blocked("https://www.youtube.com/watch?v=dQw4w9WgXcQ", allow_adult=False)
    assert blocked_yt is False

    # AdultSwim must be permitted
    blocked_as, _ = mgr.is_url_blocked("https://www.adultswim.com/videos/rick-and-morty/pilot", allow_adult=False)
    assert blocked_as is False


def test_dmca_exact_url_blocked(temp_blocklist):
    mgr, _, _, _ = temp_blocklist
    blocked, reason = mgr.is_url_blocked("https://example.com/dmca-takedown-clip")
    assert blocked is True
    assert "DMCA" in reason or "removed" in reason


def test_blocked_domain_and_subdomain(temp_blocklist):
    mgr, _, _, _ = temp_blocklist
    # Exact domain
    blocked, reason = mgr.is_url_blocked("https://copyright-infringer.com/watch?v=123")
    assert blocked is True
    assert "domain is not supported" in reason

    # Subdomain of blocked domain
    blocked_sub, reason_sub = mgr.is_url_blocked("https://cdn.sub.copyright-infringer.com/file.mp4")
    assert blocked_sub is True
    assert "domain is not supported" in reason_sub


def test_hot_reloading_on_mtime_change(temp_blocklist):
    mgr, _, domains_file, _ = temp_blocklist

    # Initially vimeo.com is not in blocked_domains
    test_url = "https://vimeo.com/123456789"
    blocked, _ = mgr.is_url_blocked(test_url)
    assert blocked is False

    # Append vimeo.com to domains.txt and update mtime
    time.sleep(0.05)
    with open(domains_file, "a", encoding="utf-8") as f:
        f.write("vimeo.com\n")
    future_time = time.time() + 2
    os.utime(domains_file, (future_time, future_time))

    blocked_now, reason = mgr.is_url_blocked(test_url)
    assert blocked_now is True
    assert "domain is not supported" in reason
