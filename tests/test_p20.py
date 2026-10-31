import xml.etree.ElementTree as ET
import pytest
from app import app, PUBLIC_ROUTES
from config import config


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


def test_every_sitemap_url_returns_200(client):
    res = client.get("/sitemap.xml")
    assert res.status_code == 200
    assert res.mimetype == "application/xml"

    content = res.get_data(as_text=True)
    assert "[jungle_url]" not in content

    # Parse XML
    root = ET.fromstring(content)
    namespace = {"ns": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    loc_elements = root.findall("ns:url/ns:loc", namespace)

    base = config.SITE_URL.rstrip("/")
    for loc_el in loc_elements:
        url = loc_el.text
        assert url.startswith(base)
        path = url[len(base):] if len(url) > len(base) else "/"
        # Test each route in the sitemap returns 200
        page_res = client.get(path)
        assert page_res.status_code == 200, f"Route {path} failed with {page_res.status_code}"


def test_robots_txt_format(client):
    res = client.get("/robots.txt")
    assert res.status_code == 200
    assert res.mimetype == "text/plain"

    content = res.get_data(as_text=True)
    assert "[jungle_url]" not in content
    assert f"Sitemap: {config.SITE_URL.rstrip('/')}/sitemap.xml" in content
    assert "Disallow: /api/" in content

    for route in PUBLIC_ROUTES:
        assert f"Allow: {route['path']}" in content


def test_error_pages_have_noindex(client):
    # 404 test
    res_404 = client.get("/nonexistent-path-for-testing-404")
    assert res_404.status_code == 404
    assert res_404.headers.get("X-Robots-Tag") == "noindex"
    assert '<meta name="robots" content="noindex">' in res_404.get_data(as_text=True)

    # 500 test via handle_500 directly or triggering internal error
    with app.test_request_context():
        from app import handle_500
        res_500 = handle_500(RuntimeError("test error"))
        assert res_500.status_code == 500
        assert res_500.headers.get("X-Robots-Tag") == "noindex"
        assert '<meta name="robots" content="noindex">' in res_500.get_data(as_text=True)

