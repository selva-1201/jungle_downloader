import subprocess



def test_bash_syntax():
    """Verify syntax of deploy bash scripts using bash -n."""
    scripts = [
        "deploy/block_internal_egress.sh",
        "deploy/cloudflare-ufw.sh",
        "deploy/host-setup.sh",
    ]
    for script in scripts:
        res = subprocess.run(["bash", "-n", script], capture_output=True, text=True)
        assert res.returncode == 0, f"Syntax check failed for {script}: {res.stderr}"


def test_block_internal_egress_dry_run():
    """Verify deploy/block_internal_egress.sh --dry-run prints correctly scoped rules."""
    res = subprocess.run(
        ["bash", "deploy/block_internal_egress.sh", "--dry-run"],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, f"--dry-run failed: {res.stderr}"
    output = res.stdout

    lines = [line.strip() for line in output.splitlines() if line.strip()]
    drop_lines = [line for line in lines if " DROP" in line]
    assert len(drop_lines) > 0, "No DROP rules generated"

    for line in drop_lines:
        # Every DROP must have an explicit interface match (-i)
        assert "-i " in line, f"DROP rule without -i interface match: {line}"
        # If dropping 172.16.0.0/12 on bridge, must not drop inter-bridge traffic
        if "-d 172.16.0.0/12" in line:
            assert "! -o br-+" in line or "! -o docker0" in line, (
                f"172.16.0.0/12 DROP missing ! -o bridge check: {line}"
            )

    # Must contain operator checklist
    assert "=== [Operator Verification Checklist] ===" in output
    assert "169.254.169.254" in output


def test_cloudflare_ufw_dry_run():
    """Verify deploy/cloudflare-ufw.sh --dry-run prints scoped rules."""
    res = subprocess.run(
        ["bash", "deploy/cloudflare-ufw.sh", "--dry-run"],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, f"--dry-run failed: {res.stderr}"
    output = res.stdout

    lines = [line.strip() for line in output.splitlines() if line.strip()]
    drop_lines = [line for line in lines if " DROP" in line]
    assert len(drop_lines) > 0, "No DROP rules generated in cloudflare-ufw.sh"

    for line in drop_lines:
        # Every DROP must specify the external interface via -i
        assert "-i " in line, f"Cloudflare DROP rule lacks -i: {line}"
        assert "--ctorigdstport" in line, f"Cloudflare DROP rule lacks --ctorigdstport: {line}"

    assert "=== [Operator Verification Checklist] ===" in output


def test_host_setup_dns_config():
    """Verify host-setup.sh configures public DNS in daemon.json."""
    with open("deploy/host-setup.sh", "r", encoding="utf-8") as f:
        content = f.read()

    assert '"dns": ["1.1.1.1", "8.8.8.8"]' in content
    assert "169.254.169.254" in content
