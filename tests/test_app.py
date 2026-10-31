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
