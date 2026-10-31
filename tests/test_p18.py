import os
import re
import pytest


def test_no_standalone_cf_clearance_or_unsafe_prune():
    docs_to_check = ["DEPLOY.md", "RUNBOOK.md"]
    for doc in docs_to_check:
        with open(doc, "r", encoding="utf-8") as f:
            content = f.read()

        # Must never recommend deleting docker volumes with prune
        assert "prune -af --volumes" not in content, f"Found unsafe prune -af --volumes in {doc}"

        # Must never reference standalone 'cf_clearance ' without token suffix in app documentation
        # Match 'cf_clearance ' or 'cf_clearance\n' or 'cf_clearance`' where it's not cf_clearance_token
        standalone_matches = re.findall(r"\bcf_clearance\b(?!_token)", content)
        assert len(standalone_matches) == 0, f"Found standalone cf_clearance in {doc}: {standalone_matches}"


def test_root_placeholder_files_removed():
    # Root robots.txt and sitemap.xml must be absent (served dynamically from Flask routes)
    assert not os.path.exists("robots.txt"), "Root robots.txt should be removed"
    assert not os.path.exists("sitemap.xml"), "Root sitemap.xml should be removed"


def test_caddyfile_improvements():
    with open("Caddyfile", "r", encoding="utf-8") as f:
        caddy = f.read()

    # Commented media.{$DOMAIN} block
    assert "media.{$DOMAIN}" in caddy
    assert "Access-Control-Allow-Origin" in caddy

    # Reasonable static max-age (3600s with revalidation)
    assert "max-age=3600, must-revalidate" in caddy

    # No Tanglish phrases in comments
    assert "paakkalam" not in caddy.lower()
    assert "thadukkum" not in caddy.lower()
    assert "podhum" not in caddy.lower()


def test_readme_present_and_complete():
    assert os.path.exists("README.md")
    with open("README.md", "r", encoding="utf-8") as f:
        readme = f.read()

    assert "ALLOW_ADULT" in readme
    assert "Single Gevent Worker" in readme
    assert "Growth & SEO Checklist" in readme
    assert "Google Search Console" in readme
