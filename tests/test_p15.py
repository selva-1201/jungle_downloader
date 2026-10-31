import glob
import os
import subprocess
import pytest


def test_all_shell_scripts_syntax():
    """Shellcheck-style bash -n syntax test on all shell scripts."""
    scripts = glob.glob("scripts/*.sh") + glob.glob("deploy/*.sh")
    assert len(scripts) >= 5, f"Expected at least 5 shell scripts, found {len(scripts)}"

    for script_path in scripts:
        res = subprocess.run(["bash", "-n", script_path], capture_output=True, text=True)
        assert res.returncode == 0, f"Syntax error in {script_path}: {res.stderr}"


def test_cloudflare_ufw_dry_run_and_help():
    # Help flag
    res_help = subprocess.run(["bash", "deploy/cloudflare-ufw.sh", "--help"], capture_output=True, text=True)
    assert res_help.returncode == 0
    assert "Usage:" in res_help.stdout

    # Dry run execution (non-root safe)
    res_dry = subprocess.run(["bash", "deploy/cloudflare-ufw.sh", "--dry-run"], capture_output=True, text=True)
    assert res_dry.returncode == 0
    assert "Cloudflare Origin Lockdown" in res_dry.stdout
    assert "173.245.48.0/20" in res_dry.stdout or "Cloudflare public IP ranges" in res_dry.stdout


def test_host_setup_script_contents():
    with open("deploy/host-setup.sh", "r", encoding="utf-8") as f:
        content = f.read()

    # 1. Non-interactive iptables-persistent preseed
    assert "debconf-set-selections" in content
    assert "iptables-persistent/autosave_v4" in content

    # 2. SSH hardening drop-in and sshd -t check
    assert "/etc/ssh/sshd_config.d/99-jungle.conf" in content
    assert "PasswordAuthentication no" in content
    assert "PermitRootLogin no" in content
    assert "PubkeyAuthentication yes" in content
    assert "sshd -t" in content

    # 3. Docker restart step (not reload)
    assert "systemctl restart docker" in content


def test_deploy_md_imds_v2_and_cloudflare():
    with open("DEPLOY.md", "r", encoding="utf-8") as f:
        content = f.read()

    assert "IMDSv2" in content
    assert "--are-legacy-imds-endpoints-disabled true" in content
    assert "deploy/cloudflare-ufw.sh" in content

