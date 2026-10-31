import subprocess
import shutil
import pytest


def test_docker_compose_config_valid():
    """Validates docker compose config CLI output if docker is available."""
    if not shutil.which("docker"):
        pytest.skip("Docker CLI not available in test environment")

    res = subprocess.run(["docker", "compose", "config"], capture_output=True, text=True)
    assert res.returncode == 0, f"docker compose config failed: {res.stderr}"


def test_compose_yaml_hardening_properties():
    with open("docker-compose.yml", "r", encoding="utf-8") as f:
        content = f.read()

    # App hardening properties
    assert "init: true" in content
    assert "stop_grace_period: 30s" in content
    assert "cap_drop:" in content
    assert "- ALL" in content
    assert "nofile:" in content
    assert "65535" in content
    assert "${APP_MEM_LIMIT:-2048M}" in content
    assert "${APP_CPUS:-2.0}" in content
    assert "pids: 256" in content

    # Caddy healthcheck & pinning
    assert "caddy:2.9.1-alpine" in content or "caddy:2.9" in content
    assert "caddy_data:/data" in content
    assert "test: [\"CMD\", \"caddy\", \"version\"]" in content or "caddy version" in content


def test_dockerfile_hardening_properties():
    with open("Dockerfile", "r", encoding="utf-8") as f:
        content = f.read()

    # Tailwind SHA256 verification
    assert "TAILWIND_SHA256_X64=" in content
    assert "TAILWIND_SHA256_ARM64=" in content
    assert "sha256sum -c" in content
    assert "curl -fsSL" in content

    # Python base image & pip options
    assert "python:3.11-slim-bookworm" in content
    assert "PIP_DISABLE_PIP_VERSION_CHECK=1" in content
    assert "--no-cache-dir" in content

    # Healthcheck uses stdlib urllib without curl in runtime image
    assert "urllib.request" in content
    assert "http://127.0.0.1:5000/healthz" in content
