import os
import shutil
import subprocess
import pytest


def test_dockerignore_includes_tests_and_docs():
    """Assert .dockerignore does not exclude tests/ and documentation read by tests."""
    with open(".dockerignore", "r", encoding="utf-8") as f:
        lines = [line.strip() for line in f if line.strip() and not line.startswith("#")]

    # None of these should be ignored because the test stage needs them
    assert "tests/" not in lines
    assert "tests" not in lines
    assert "DEPLOY.md" not in lines
    assert "RUNBOOK.md" not in lines
    assert "README.md" not in lines
    assert "docs/" not in lines


def test_smoke_config_in_data():
    """Assert smoke config is stored in data/smoke_urls.json."""
    assert os.path.exists("data/smoke_urls.json")


def test_docker_build_test_stage():
    """docker build --target test . passes locally if docker exists and daemon is running."""
    if not shutil.which("docker"):
        pytest.skip("docker binary not found")

    daemon_check = subprocess.run(["docker", "info"], capture_output=True, text=True)
    if daemon_check.returncode != 0:
        pytest.skip("docker daemon is not accessible / running")

    res = subprocess.run(["docker", "build", "--target", "test", "."], capture_output=True, text=True)
    assert res.returncode == 0, f"docker build --target test failed: {res.stderr}"
