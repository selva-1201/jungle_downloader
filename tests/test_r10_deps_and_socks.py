import os
import pytest
from config import AppConfig


def test_config_rejects_socks_ytdlp_proxy():
    with pytest.raises(ValueError, match="SOCKS proxies are not supported in YTDLP_PROXY"):
        AppConfig(YTDLP_PROXY="socks5://user:pass@127.0.0.1:1080")


def test_config_rejects_socks_proxy_rules():
    with pytest.raises(ValueError, match="SOCKS proxies are not supported in PROXY_RULES"):
        AppConfig(PROXY_RULES={"instagram": "socks5h://127.0.0.1:1080"})


def test_config_accepts_http_proxies():
    cfg = AppConfig(
        YTDLP_PROXY="http://proxy.example.com:8080",
        PROXY_RULES={"youtube": "https://proxy2.example.com:8443"},
    )
    assert cfg.YTDLP_PROXY == "http://proxy.example.com:8080"
    assert cfg.PROXY_RULES["youtube"] == "https://proxy2.example.com:8443"


def test_requirements_lock_and_dockerfile():
    assert os.path.exists("requirements.lock")
    with open("requirements.lock", "r", encoding="utf-8") as f:
        lock_content = f.read()

    assert "gunicorn==23.0.0" in lock_content
    assert "gevent==" in lock_content
    assert "greenlet==" in lock_content

    with open("Dockerfile", "r", encoding="utf-8") as f:
        dockerfile = f.read()

    assert "requirements.lock" in dockerfile


def test_ci_contains_pip_check():
    ci_path = os.path.join(".github", "workflows", "ci.yml")
    assert os.path.exists(ci_path)
    with open(ci_path, "r", encoding="utf-8") as f:
        ci_content = f.read()

    assert "pip check" in ci_content
