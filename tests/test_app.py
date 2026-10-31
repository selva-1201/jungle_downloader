import pytest
from app import app


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
