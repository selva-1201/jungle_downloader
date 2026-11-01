import os
import re
import subprocess


def test_deploy_scripts_bash_syntax():
    scripts = [
        "deploy/host-setup.sh",
        "deploy/block_internal_egress.sh",
        "deploy/cloudflare-ufw.sh",
    ]
    for script in scripts:
        assert os.path.exists(script), f"Script {script} does not exist"
        res = subprocess.run(["bash", "-n", script], capture_output=True, text=True)
        assert res.returncode == 0, f"bash -n failed on {script}:\n{res.stderr}"


def test_imds_ip_defined_before_use_in_host_setup():
    script_path = "deploy/host-setup.sh"
    with open(script_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    def_line = None
    use_line = None

    for idx, line in enumerate(lines, start=1):
        if re.match(r"^IMDS_IP=", line.strip()) and def_line is None:
            def_line = idx
        if "${IMDS_IP}" in line and use_line is None:
            use_line = idx

    assert def_line is not None, "IMDS_IP definition not found in host-setup.sh"
    assert use_line is not None, "${IMDS_IP} usage not found in host-setup.sh"
    assert def_line < use_line, f"IMDS_IP defined on line {def_line} after use on line {use_line}"


def test_caddyfile_handle_blocked_metrics():
    with open("Caddyfile", "r", encoding="utf-8") as f:
        content = f.read()

    assert "handle @blocked_metrics" in content
    assert "respond 403" in content

    # handle @blocked_metrics must appear BEFORE the general catch-all handle
    pos_metrics = content.find("handle @blocked_metrics")
    pos_catch_all = content.find("handle {")

    assert pos_metrics != -1, "handle @blocked_metrics not found in Caddyfile"
    assert pos_catch_all != -1, "General handle { not found in Caddyfile"
    assert pos_metrics < pos_catch_all, "handle @blocked_metrics must appear before catch-all handle"
