import os
import pytest
from app import app
from config import AppConfig, config
from hardening import map_error_message


def test_rendered_html_without_public_repo_url_has_no_open_source(monkeypatch):
    monkeypatch.setattr(config, "PUBLIC_REPO_URL", "")
    client = app.test_client()
    res = client.get("/")
    assert res.status_code == 200
    html = res.get_data(as_text=True)

    # Must NOT have 'Open source' anywhere in rendered page
    assert "Open source" not in html
    assert "open source" not in html
    assert "Open Source" not in html


def test_rendered_html_with_public_repo_url_shows_open_source(monkeypatch):
    monkeypatch.setattr(config, "PUBLIC_REPO_URL", "https://github.com/example/jungle")
    client = app.test_client()
    res = client.get("/")
    assert res.status_code == 200
    html = res.get_data(as_text=True)

    assert "https://github.com/example/jungle" in html
    assert "Open source" in html or "Open Source" in html


def test_videourl_label_and_aria_present():
    client = app.test_client()
    res = client.get("/")
    html = res.get_data(as_text=True)

    assert 'id="videoUrl"' in html
    assert 'for="videoUrl"' in html
    assert 'aria-label="Media URL"' in html
    assert "safe, clean, and secure" not in html


def test_self_hosted_font_files_and_links():
    assert os.path.exists("static/fonts/space-grotesk.woff2")
    assert os.path.exists("static/css/fonts.css")

    client = app.test_client()
    res = client.get("/")
    html = res.get_data(as_text=True)

    assert "fonts.googleapis.com" not in html
    assert "fonts/space-grotesk.woff2" in html


def test_prod_startup_guards():
    # Prod with .local email must fail
    with pytest.raises(ValueError, match="cannot start with placeholder emails"):
        AppConfig(
            ENV="prod",
            SITE_URL="https://jungle.example.com",
            CONTACT_EMAIL="support@jungle.local",
            DMCA_EMAIL="dmca@example.com",
        )

    with pytest.raises(ValueError, match="cannot start with placeholder emails"):
        AppConfig(
            ENV="prod",
            SITE_URL="https://jungle.example.com",
            CONTACT_EMAIL="support@example.com",
            DMCA_EMAIL="dmca@jungle.local",
        )

    # Prod with localhost URL must fail
    with pytest.raises(ValueError, match="cannot start with localhost SITE_URL"):
        AppConfig(
            ENV="prod",
            SITE_URL="http://localhost:5000",
            CONTACT_EMAIL="support@example.com",
            DMCA_EMAIL="dmca@example.com",
        )

    # Valid prod configuration passes
    valid_cfg = AppConfig(
        ENV="prod",
        SITE_URL="https://jungle.example.com",
        CONTACT_EMAIL="support@example.com",
        DMCA_EMAIL="dmca@example.com",
    )
    assert valid_cfg.IS_PROD is True


def test_login_private_age_gated_error_mapping():
    # Private video
    priv_msg = map_error_message(Exception("Private video. Sign in if you've been granted access"))
    assert "private" in priv_msg.lower()
    assert "blocking us" not in priv_msg.lower()

    # Age restricted
    age_msg = map_error_message(Exception("Sign in to confirm your age"))
    assert "age" in age_msg.lower()
    assert "blocking us" not in age_msg.lower()

    # Login required
    login_msg = map_error_message(Exception("Sign in to view this video"))
    assert "login" in login_msg.lower() or "authorization" in login_msg.lower()
    assert "blocking us" not in login_msg.lower()

    # Bot error still maps to blocking us
    bot_msg = map_error_message(Exception("Sign in to confirm you're not a bot"))
    assert "blocking us" in bot_msg.lower()
