import subprocess
import pytest
from app import app
from config import config


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


def test_ads_disabled_by_default_renders_no_ad_slots(client, monkeypatch):
    monkeypatch.setattr(config, "ADS_ENABLED", False)
    res = client.get("/")
    assert res.status_code == 200
    html = res.get_data(as_text=True)

    # When ADS_ENABLED=false, no ad-slot or ad-container-wrapper markup should be rendered
    assert "ad-slot" not in html
    assert "ad-container-wrapper" not in html
    assert "header_banner" not in html
    assert "below_result" not in html
    assert "sidebar" not in html


def test_ads_enabled_renders_ad_slots(client, monkeypatch):
    monkeypatch.setattr(config, "ADS_ENABLED", True)
    res = client.get("/")
    assert res.status_code == 200
    html = res.get_data(as_text=True)

    assert "ad-slot-header_banner" in html
    assert "ad-slot-below_result" in html
    assert "ad-slot-sidebar" in html
    assert "Advertisement" in html


def test_ruff_check_clean():
    result = subprocess.run([".venv/bin/ruff", "check", "."], capture_output=True, text=True)
    assert result.returncode == 0, f"Ruff lint errors found:\n{result.stdout}\n{result.stderr}"
