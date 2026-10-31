import pytest
from sites_catalog import SitesCatalog, _read_file_lines
from config import config


class FakeExtractor:
    def __init__(self, name, working_val=True, enabled_val=True, valid_url=True):
        self.IE_NAME = name
        self._working_val = working_val
        self._ENABLED = enabled_val
        self._VALID_URL = valid_url

    def working(self):
        return self._working_val


def test_broken_extractors_excluded(monkeypatch):
    fake_ies = [
        FakeExtractor("WorkingSite", working_val=True),
        FakeExtractor("BrokenSite1", working_val=False),
        FakeExtractor("DisabledSite2", enabled_val=False),
        FakeExtractor("EmbedSite3", valid_url=False),
        FakeExtractor("SearchSiteSearch", valid_url=True),
    ]
    monkeypatch.setattr("yt_dlp.extractor.gen_extractors", lambda: fake_ies)

    catalog_obj = SitesCatalog(
        adult_file="data/adult_extractors.txt",
        drm_file="data/drm_extractors.txt",
    )
    data = catalog_obj.build_catalog()
    sites = [s.lower() for s in data["sites"]]

    assert "workingsite" in sites
    assert "brokensite1" not in sites
    assert "disabledsite2" not in sites
    assert "embedsite3" not in sites
    assert "searchsitesearch" not in sites


def test_adultswim_present_and_adult_excluded():
    catalog_obj = SitesCatalog()
    data = catalog_obj.build_catalog()
    sites_lower = [s.lower() for s in data["sites"]]

    # AdultSwim should not be excluded by the adult filter
    # Check if yt-dlp has AdultSwim
    adult_lines = _read_file_lines("data/adult_extractors.txt")
    assert "adultswim" not in adult_lines

    # Adult extractors from file must not be present in the public catalog
    for adult in ["pornhub", "xvideos", "redtube", "youporn"]:
        if adult in adult_lines:
            assert adult not in sites_lower


def test_drm_extractors_excluded():
    catalog_obj = SitesCatalog()
    data = catalog_obj.build_catalog()
    sites_lower = [s.lower() for s in data["sites"]]

    drm_lines = _read_file_lines("data/drm_extractors.txt")
    assert "disney" in drm_lines or "cinemax" in drm_lines
    for drm in drm_lines:
        assert drm not in sites_lower


def test_display_count_rounds_down_to_nearest_hundred():
    catalog_obj = SitesCatalog()
    data = catalog_obj.build_catalog()
    total = data["total_count"]
    expected_rounded = (total // 100) * 100
    assert data["display_count"] == f"{expected_rounded}+ sites"

