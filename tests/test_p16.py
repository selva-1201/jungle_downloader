import subprocess
import pytest


def test_block_internal_egress_syntax():
    res = subprocess.run(["bash", "-n", "deploy/block_internal_egress.sh"], capture_output=True, text=True)
    assert res.returncode == 0, f"Syntax error: {res.stderr}"

    res_wrapper = subprocess.run(["bash", "-n", "deploy/block_imds.sh"], capture_output=True, text=True)
    assert res_wrapper.returncode == 0, f"Syntax error in wrapper: {res_wrapper.stderr}"


def test_required_cidrs_present_in_script():
    with open("deploy/block_internal_egress.sh", "r", encoding="utf-8") as f:
        content = f.read()

    required_ipv4 = [
        "169.254.0.0/16",
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "100.64.0.0/10",
    ]
    for cidr in required_ipv4:
        assert cidr in content, f"Missing required IPv4 CIDR {cidr} in deploy/block_internal_egress.sh"

    required_ipv6 = [
        "fc00::/7",
        "fe80::/10",
    ]
    for cidr6 in required_ipv6:
        assert cidr6 in content, f"Missing required IPv6 CIDR {cidr6} in deploy/block_internal_egress.sh"

    # Verify allowances
    assert "RELATED,ESTABLISHED" in content
    assert "127.0.0.11" in content


def test_verify_and_dry_run_flags():
    # Test --verify flag prints instructions without modifying system
    res_verify = subprocess.run(
        ["bash", "deploy/block_internal_egress.sh", "--verify"],
        capture_output=True,
        text=True
    )
    assert res_verify.returncode == 0
    assert "curlimages/curl" in res_verify.stdout
    assert "169.254.169.254" in res_verify.stdout

    # Test --dry-run prints CIDRs
    res_dry = subprocess.run(
        ["bash", "deploy/block_internal_egress.sh", "--dry-run"],
        capture_output=True,
        text=True
    )
    assert res_dry.returncode == 0
    assert "169.254.0.0/16" in res_dry.stdout
    assert "10.0.0.0/8" in res_dry.stdout


def test_host_setup_mirrors_by_invoking_script():
    with open("deploy/host-setup.sh", "r", encoding="utf-8") as f:
        content = f.read()

    assert "block_internal_egress.sh" in content
