import json
import logging
import subprocess
import pytest
from app import app, extractor_counters
from logging_setup import JSONFormatter, mask_token


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


def test_json_formatter_and_masking():
    formatter = JSONFormatter()
    record = logging.LogRecord(
        name="test_logger",
        level=logging.INFO,
        pathname="test.py",
        lineno=10,
        msg="Processing token 1234567890abcdef1234567890abcdef for http://user:secret@example.com",
        args=(),
        exc_info=None
    )
    record.request_id = "req_12345678"
    record.extractor = "youtube"

    output = formatter.format(record)
    data = json.loads(output)

    assert data["level"] == "INFO"
    assert data["logger"] == "test_logger"
    assert data["request_id"] == "req_12345678"
    assert data["extractor"] == "youtube"
    assert "secret" not in data["message"]
    assert "1234567890abcdef1234567890abcdef" not in data["message"]
    assert "tok_***" in data["message"]
    assert "://***:***@" in data["message"]


def test_mask_token_utility():
    assert mask_token("https://jungle.local/api/stream?token=secret123456&type=video") == "https://jungle.local/api/stream?token=tok_***&type=video"
    assert mask_token("/api/stream/1234567890abcdef1234567890abcdef") == "/api/stream/tok_***"
    assert mask_token("/api/preview/1234567890abcdef1234567890abcdef") == "/api/preview/tok_***"
    assert mask_token("http://alice:password123@proxy.org:8080") == "http://***:***@proxy.org:8080"


def test_request_id_in_response_header(client):
    resp = client.get("/healthz")
    assert resp.status_code == 200
    req_id = resp.headers.get("X-Request-ID")
    assert req_id is not None
    assert len(req_id) >= 8

    # Custom header echoed if safe
    resp2 = client.get("/healthz", headers={"X-Request-ID": "custom-req-42"})
    assert resp2.headers.get("X-Request-ID") == "custom-req-42"


def test_error_responses_never_leak_raw_exception(client, monkeypatch):
    # Test that internal exception text does not appear in 400 or 500 response body
    secret_internal_str = "SUPER_SECRET_INTERNAL_DATABASE_FAILURE_XYZ"

    def faulty_download(*args, **kwargs):
        raise RuntimeError(secret_internal_str)

    # Trigger error via /api/download with mock failure
    monkeypatch.setattr("app.fetch_fresh_metadata", faulty_download)
    resp = client.post("/api/download", json={"url": "https://vimeo.com/123456"})
    assert resp.status_code in (400, 500)
    data = resp.get_json()
    assert secret_internal_str not in data.get("error", "")
    assert secret_internal_str not in resp.text


def test_extractor_counter_in_metrics(client, monkeypatch):
    monkeypatch.setattr("app.validate_public_url", lambda u: True)
    init_vimeo = extractor_counters.get("vimeo", 0)

    # Trigger a request that sets extractor
    client.post("/api/download", json={"url": "https://vimeo.com/987654"})
    assert extractor_counters.get("vimeo", 0) >= init_vimeo + 1

    # Local metrics endpoint reflects counts
    resp = client.get("/metrics", environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert resp.status_code == 200
    data = resp.get_json()
    assert "extractors" in data
    assert data["extractors"].get("vimeo", 0) >= 1


def test_disk_ram_alert_script():
    # Syntax check
    proc = subprocess.run(["bash", "-n", "scripts/disk_ram_alert.sh"], capture_output=True, text=True)
    assert proc.returncode == 0, f"Syntax error in script: {proc.stderr}"

    # Dry run execution with safe 100% threshold (does not send alerts)
    env = {"DISK_THRESHOLD_PCT": "100", "RAM_THRESHOLD_PCT": "100", "PATH": "/usr/bin:/bin:/usr/sbin:/sbin"}
    res = subprocess.run(["bash", "scripts/disk_ram_alert.sh"], capture_output=True, text=True, env=env)
    assert res.returncode == 0
    assert "Resource Check:" in res.stdout
