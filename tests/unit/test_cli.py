"""Tests for the VVNext CLI (typer app)."""
from __future__ import annotations

import yaml
from typer.testing import CliRunner
from vvnext.cli import app

runner = CliRunner()


# ---------------------------------------------------------------------------
# Help + version
# ---------------------------------------------------------------------------


def test_help():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "sing-box" in result.stdout.lower() or "fleet management" in result.stdout.lower()


def test_version():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "0.1.0" in result.stdout


# ---------------------------------------------------------------------------
# Commands that need inventory (should fail gracefully when missing)
# ---------------------------------------------------------------------------


def test_status_no_inventory():
    result = runner.invoke(app, ["status"])
    assert result.exit_code != 0


def test_health_no_inventory():
    result = runner.invoke(app, ["health"])
    assert result.exit_code != 0


def test_sub_rebuild_no_inventory():
    result = runner.invoke(app, ["sub", "rebuild"])
    assert result.exit_code != 0


def test_deploy_no_inventory():
    result = runner.invoke(app, ["deploy"])
    assert result.exit_code != 0


def test_audit_no_inventory():
    result = runner.invoke(app, ["audit"])
    assert result.exit_code != 0


def test_keys_generate_no_inventory():
    result = runner.invoke(app, ["keys", "generate"])
    assert result.exit_code != 0


# ---------------------------------------------------------------------------
# status with valid inventory
# ---------------------------------------------------------------------------


def test_status_with_inventory(tmp_path):
    """Create a minimal inventory file and test status command."""
    inv_data = {
        "servers": [
            {
                "name": "hk-gcp-a",
                "role": "near",
                "region": "hk",
                "provider": "gcp",
                "public_ip": "10.0.0.1",
                "port_base": 20000,
                "sni": "dl.google.com",
                "hy2_sni": "hk.test.com",
                "cdn_domain": "hk-cdn.test.com",
                "dns_name": "hk-a.test.com",
                "wg_peers": ["us-gcp-a"],
            },
            {
                "name": "us-gcp-a",
                "role": "far",
                "region": "us",
                "provider": "gcp",
                "public_ip": "10.0.0.3",
                "wg_port": 51941,
            },
        ]
    }
    inv_file = tmp_path / "inventory.yaml"
    inv_file.write_text(yaml.dump(inv_data))
    result = runner.invoke(app, ["status", "--inventory", str(inv_file)])
    assert result.exit_code == 0
    assert "hk-gcp-a" in result.stdout
    assert "us-gcp-a" in result.stdout
    assert "near" in result.stdout
    assert "far" in result.stdout


# ---------------------------------------------------------------------------
# status shows ports
# ---------------------------------------------------------------------------


def test_status_shows_ports(tmp_path):
    """Status should display key port info for each node."""
    inv_data = {
        "servers": [
            {
                "name": "jp-gcp-a",
                "role": "near",
                "region": "jp",
                "provider": "gcp",
                "public_ip": "10.0.0.2",
                "port_base": 21000,
                "sni": "dl.google.com",
                "hy2_sni": "jp.test.com",
                "cdn_domain": "jp-cdn.test.com",
                "dns_name": "jp-a.test.com",
                "wg_peers": ["us-gcp-a"],
            },
            {
                "name": "us-gcp-a",
                "role": "far",
                "region": "us",
                "provider": "gcp",
                "public_ip": "10.0.0.3",
                "wg_port": 51941,
            },
        ]
    }
    inv_file = tmp_path / "inventory.yaml"
    inv_file.write_text(yaml.dump(inv_data))
    result = runner.invoke(app, ["status", "--inventory", str(inv_file)])
    assert result.exit_code == 0
    assert "reality:21001" in result.stdout
    assert "wg:51941" in result.stdout


# ---------------------------------------------------------------------------
# remove-node
# ---------------------------------------------------------------------------


def test_remove_node(tmp_path):
    """Remove a node from inventory."""
    inv_data = {
        "servers": [
            {
                "name": "hk-gcp-a",
                "role": "near",
                "region": "hk",
                "provider": "gcp",
                "public_ip": "10.0.0.1",
                "port_base": 20000,
                "sni": "dl.google.com",
                "hy2_sni": "hk.test.com",
                "cdn_domain": "hk-cdn.test.com",
                "dns_name": "hk-a.test.com",
                "wg_peers": ["us-gcp-a"],
            },
            {
                "name": "us-gcp-a",
                "role": "far",
                "region": "us",
                "provider": "gcp",
                "public_ip": "10.0.0.3",
                "wg_port": 51941,
            },
        ]
    }
    inv_file = tmp_path / "inventory.yaml"
    inv_file.write_text(yaml.dump(inv_data))
    result = runner.invoke(app, ["remove-node", "us-gcp-a", "--inventory", str(inv_file)])
    assert result.exit_code == 0
    assert "removed" in result.stdout.lower()
    # Verify the node is gone from the file
    updated = yaml.safe_load(inv_file.read_text())
    names = [s["name"] for s in updated["servers"]]
    assert "us-gcp-a" not in names
    assert "hk-gcp-a" in names


def test_remove_node_not_found(tmp_path):
    """Removing a non-existent node should fail."""
    inv_data = {
        "servers": [
            {
                "name": "hk-gcp-a",
                "role": "near",
                "region": "hk",
                "provider": "gcp",
                "public_ip": "10.0.0.1",
                "port_base": 20000,
                "sni": "dl.google.com",
                "hy2_sni": "hk.test.com",
                "cdn_domain": "hk-cdn.test.com",
                "dns_name": "hk-a.test.com",
            },
        ]
    }
    inv_file = tmp_path / "inventory.yaml"
    inv_file.write_text(yaml.dump(inv_data))
    result = runner.invoke(app, ["remove-node", "nonexistent", "--inventory", str(inv_file)])
    assert result.exit_code != 0


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------


def test_init_no_config():
    """Init without --config should print interactive mode hint."""
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0
    assert "interactive" in result.stdout.lower() or "questionary" in result.stdout.lower()


def test_init_config_not_found(tmp_path):
    """Init with --config pointing to nonexistent file should fail."""
    result = runner.invoke(app, ["init", "--config", str(tmp_path / "nope.yaml")])
    assert result.exit_code != 0


def test_init_with_config(tmp_path):
    """Init with --config should parse nodes and start pipeline (fails at SSH)."""
    cfg = tmp_path / "init.yaml"
    cfg.write_text(yaml.dump({
        "nodes": [
            {"ip": "10.0.0.1", "role": "near", "region": "hk", "provider": "gcp"},
            {"ip": "10.0.0.2", "role": "far", "region": "us", "provider": "gcp"},
        ],
        "domain": "example.com",
    }))
    result = runner.invoke(app, ["init", "--config", str(cfg)])
    # Pipeline starts successfully (step 1 passes) but fails at SSH step (step 2)
    assert "Validate config" in result.stdout
    assert "2 node(s) loaded" in result.stdout


# ---------------------------------------------------------------------------
# sub server (action validation)
# ---------------------------------------------------------------------------


def test_sub_server_bad_action(tmp_path):
    """Sub server with invalid action should fail."""
    settings_file = tmp_path / "settings.yaml"
    settings_file.write_text(yaml.dump({}))
    result = runner.invoke(app, ["sub", "server", "restart", "--settings", str(settings_file)])
    assert result.exit_code != 0


# ---------------------------------------------------------------------------
# deploy with non-existent inventory
# ---------------------------------------------------------------------------


def test_deploy_custom_missing_inventory(tmp_path):
    """Deploy pointing at a non-existent inventory should fail."""
    result = runner.invoke(app, ["deploy", "--inventory", str(tmp_path / "nope.yaml")])
    assert result.exit_code != 0


# ---------------------------------------------------------------------------
# Subcommand help
# ---------------------------------------------------------------------------


def test_sub_help():
    result = runner.invoke(app, ["sub", "--help"])
    assert result.exit_code == 0
    assert "subscription" in result.stdout.lower() or "rebuild" in result.stdout.lower()


def test_keys_help():
    result = runner.invoke(app, ["keys", "--help"])
    assert result.exit_code == 0
    assert "key" in result.stdout.lower() or "generate" in result.stdout.lower()


def test_health_help():
    result = runner.invoke(app, ["health", "--help"])
    assert result.exit_code == 0


def test_audit_help():
    result = runner.invoke(app, ["audit", "--help"])
    assert result.exit_code == 0
