import pytest
from app import app
from config import config


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


def test_healthz_route(client):
    resp = client.get("/healthz")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "ok"


def test_robots_txt_route(client):
    resp = client.get("/robots.txt")
    assert resp.status_code == 200
    assert resp.mimetype == "text/plain"
    assert "Disallow: /api/" in resp.text
    assert "Sitemap:" in resp.text


def test_sitemap_xml_route(client):
    resp = client.get("/sitemap.xml")
    assert resp.status_code == 200
    assert "application/xml" in resp.mimetype or "text/xml" in resp.mimetype
    assert "<urlset" in resp.text
    assert "/terms" in resp.text
    assert "/privacy" in resp.text


def test_terms_and_privacy_clean_urls_and_redirects(client):
    # Clean URLs
    assert client.get("/terms").status_code == 200
    assert client.get("/privacy").status_code == 200

    # 301 Permanent Redirects for legacy .html paths
    resp_terms = client.get("/terms.html")
    assert resp_terms.status_code == 301
    assert resp_terms.headers.get("Location") == "/terms"

    resp_privacy = client.get("/privacy.html")
    assert resp_privacy.status_code == 301
    assert resp_privacy.headers.get("Location") == "/privacy"


def test_custom_404_page(client):
    resp = client.get("/does-not-exist")
    assert resp.status_code == 404
    assert "Page Not Found" in resp.text


def test_security_headers_present(client):
    resp = client.get("/healthz")
    assert resp.headers.get("X-Content-Type-Options") == "nosniff"
    assert resp.headers.get("X-Frame-Options") == "SAMEORIGIN"


def test_frontend_html_integrity_and_footer_links(client):
    from html.parser import HTMLParser

    class TagCounter(HTMLParser):
        def __init__(self):
            super().__init__()
            self.tags = []
            self.errors = []
            self.in_footer = False
            self.footer_tag_stack = []

        def handle_starttag(self, tag, attrs):
            self.tags.append((tag, dict(attrs)))
            if tag == "footer":
                self.in_footer = True
            if self.in_footer and tag not in ("img", "br", "hr", "input", "meta", "link"):
                self.footer_tag_stack.append(tag)

        def handle_endtag(self, tag):
            if self.in_footer and self.footer_tag_stack:
                if self.footer_tag_stack[-1] == tag:
                    self.footer_tag_stack.pop()
            if tag == "footer":
                self.in_footer = False

    resp = client.get("/")
    assert resp.status_code == 200
    html_text = resp.text

    # 1. No stray Chinese characters
    assert "确定" not in html_text

    # 2. No DRM/Adult keywords in metadata
    assert "hotstar" not in html_text.lower()
    assert "sonyliv" not in html_text.lower()
    assert "jiosaavn" not in html_text.lower()

    # 3. Footer links must point to clean routes /terms and /privacy, NOT .html
    assert 'href="/terms"' in html_text
    assert 'href="/privacy"' in html_text
    assert 'href="/terms.html"' not in html_text
    assert 'href="/privacy.html"' not in html_text

    # 4. Tag balance in footer
    parser = TagCounter()
    parser.feed(html_text)
    assert len(parser.footer_tag_stack) == 0, f"Unbalanced tags in footer: {parser.footer_tag_stack}"

    # 5. Supported sites page uses data-site attribute without inline onclick
    resp_sites = client.get("/supported-sites")
    assert resp_sites.status_code == 200
    assert 'onclick="filterBy' not in resp_sites.text
    assert 'data-site=' in resp_sites.text
    assert resp.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"
    assert "Content-Security-Policy-Report-Only" in resp.headers


def test_api_download_rejects_ssrf_attempt(client):
    resp = client.post("/api/download", json={"url": "http://169.254.169.254/opc/v2/instance/"})
    assert resp.status_code == 400
    data = resp.get_json()
    assert data["success"] is False
    assert any(msg in data["error"].lower() for msg in ["not permitted", "not supported"])


def test_api_download_rejects_adult_extractor(client):
    resp = client.post("/api/download", json={"url": "https://www.pornhub.com/view_video.php?viewkey=ph5e9754"})
    assert resp.status_code == 400
    data = resp.get_json()
    assert data["success"] is False
    assert "not supported" in data["error"].lower()


def test_download_flow_cookie_polling_and_iframe(client):
    resp = client.get("/")
    assert resp.status_code == 200
    html = resp.text

    # 1. No navigation on download start
    assert "window.location.assign" not in html

    # 2. No arbitrary 5 second timer
    assert "5000" not in html[html.find("function executeDownload"):]

    # 3. References download.js and cookie polling
    assert "js/download.js" in html
    assert "hasDownloadCookie" in html
    assert "triggerDownloadFrame" in html

    # 4. Verify static/js/download.js exists and implements pure cookie checks
    with open("static/js/download.js", "r", encoding="utf-8") as f:
        js_content = f.read()

    assert "dl_" in js_content
    assert "parseCookies" in js_content
    assert "hasDownloadCookie" in js_content
    assert "clearDownloadCookie" in js_content
    assert "buildStreamUrl" in js_content
    assert "triggerDownloadFrame" in js_content


def test_p10_tailwind_cdn_removal_a11y_seo(client):
    import glob
    import json
    from sites_catalog import sites_catalog

    # 1. No template contains cdn.tailwindcss.com
    template_files = glob.glob("templates/**/*.html", recursive=True)
    assert len(template_files) >= 5
    for t_file in template_files:
        with open(t_file, "r", encoding="utf-8") as f:
            content = f.read()
        assert "cdn.tailwindcss.com" not in content, f"Found cdn.tailwindcss.com in {t_file}"

    # 2. Canonical uses SITE_URL
    resp = client.get("/")
    assert resp.status_code == 200
    html = resp.text
    assert f'<link rel="canonical" href="{config.SITE_URL}/">' in html

    # 3. Dynamic display count appears and "1800" is absent
    display_count = sites_catalog.catalog["display_count"]
    assert display_count in html
    assert "1800" not in html

    # 4. Manifest icon URLs start with /static/
    with open("static/site.webmanifest", "r", encoding="utf-8") as f:
        manifest = json.load(f)
    for icon in manifest.get("icons", []):
        assert icon["src"].startswith("/static/"), f"Icon path {icon['src']} does not start with /static/"

    # 5. Accessibility basics
    assert 'aria-label="Clear URL input"' in html
    assert 'role="dialog"' in html
    assert 'aria-modal="true"' in html


def test_p11_legal_pages_truthful(client, monkeypatch):
    import glob

    # 1. The strings "never logs", "zero-logs", "100% safe" appear nowhere in templates
    template_files = glob.glob("templates/**/*.html", recursive=True)
    forbidden = ["never logs", "zero-logs", "100% safe"]
    for t_file in template_files:
        with open(t_file, "r", encoding="utf-8") as f:
            content = f.read().lower()
        for s in forbidden:
            assert s not in content, f"Forbidden string '{s}' found in {t_file}"

    # 2. Rendered privacy page mentions IP processing, Cloudflare, and contacts
    resp = client.get("/privacy")
    assert resp.status_code == 200
    assert "ip address" in resp.text.lower()
    assert "cloudflare" in resp.text.lower()
    assert config.LEGAL_LAST_UPDATED in resp.text
    assert config.CONTACT_EMAIL in resp.text

    # 3. Terms mentions DMCA and rate limits
    resp_terms = client.get("/terms")
    assert resp_terms.status_code == 200
    assert config.DMCA_EMAIL in resp_terms.text
    assert "rate limit" in resp_terms.text.lower()

    # 4. Ad text appears conditionally based on ADS_ENABLED
    monkeypatch.setattr(config, "ADS_ENABLED", False)
    resp_no_ads = client.get("/privacy")
    assert "advertising is currently disabled" in resp_no_ads.text.lower()
    assert "third-party ad networks" not in resp_no_ads.text

    monkeypatch.setattr(config, "ADS_ENABLED", True)
    resp_ads = client.get("/privacy")
    assert "third-party ad networks" in resp_ads.text

    # 5. Open source link conditional on PUBLIC_REPO_URL
    monkeypatch.setattr(config, "PUBLIC_REPO_URL", "")
    resp_no_repo = client.get("/terms")
    assert ">Open Source<" not in resp_no_repo.text

    monkeypatch.setattr(config, "PUBLIC_REPO_URL", "https://github.com/myorg/jungle")
    resp_repo = client.get("/terms")
    assert "https://github.com/myorg/jungle" in resp_repo.text
    assert "Open Source" in resp_repo.text

