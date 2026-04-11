import pytest
import json
from unittest.mock import MagicMock, patch

from vvnext.inventory import Inventory, ServerEntry
from vvnext.settings import Settings
from vvnext.ssh import SshClient
from vvnext.audit import (
    AuditFinding,
    AuditReport,
    audit_config_drift,
    audit_security,
    _check_ssh_password_auth,
    _check_ufw_status,
    _check_fail2ban,
    _check_tailscale_ssh,
    _check_service_active,
)


def _make_near_node(**overrides) -> ServerEntry:
    defaults = {
        "name": "hk-gcp-a", "role": "near", "region": "hk", "provider": "gcp",
        "public_ip": "1.2.3.4", "port_base": 20000, "sni": "dl.google.com",
        "hy2_sni": "hk.test.com", "cdn_domain": "hk-cdn.test.com",
        "dns_name": "hk-a.test.com",
    }
    defaults.update(overrides)
    return ServerEntry(**defaults)


def _make_residential_node(**overrides) -> ServerEntry:
    defaults = {
        "name": "us-home-att2", "role": "residential", "region": "us",
        "provider": "home", "public_ip": "99.1.2.3", "wg_port": 51941,
        "tailscale_ip": "100.64.0.5",
    }
    defaults.update(overrides)
    return ServerEntry(**defaults)


def _mock_ssh(exec_results: dict[str, tuple[str, str, int]]) -> MagicMock:
    """Create a mock SshClient that returns different results per command pattern."""
    mock = MagicMock(spec=SshClient)

    def exec_side_effect(cmd, check=True):
        for pattern, result in exec_results.items():
            if pattern in cmd:
                return result
        return ("", "", 1)

    mock.exec = MagicMock(side_effect=exec_side_effect)
    return mock


# --- Config Drift Tests ---

def test_config_drift_match():
    """Identical configs -> no findings."""
    config = {"inbounds": [{"type": "vless"}], "outbounds": [{"type": "direct"}]}
    ssh = _mock_ssh({
        "cat /etc/sing-box/config.json": (json.dumps(config), "", 0),
    })
    node = _make_near_node()
    findings = audit_config_drift(ssh, node, config)
    assert len(findings) == 0


def test_config_drift_mismatch():
    """Different configs -> critical finding."""
    local_config = {"inbounds": [{"type": "vless"}], "outbounds": [{"type": "direct"}]}
    remote_config = {"inbounds": [{"type": "vless"}], "outbounds": [{"type": "socks"}]}
    ssh = _mock_ssh({
        "cat /etc/sing-box/config.json": (json.dumps(remote_config), "", 0),
    })
    node = _make_near_node()
    findings = audit_config_drift(ssh, node, local_config)
    assert len(findings) == 1
    assert findings[0].severity == "critical"
    assert findings[0].category == "config_drift"
    assert "drift" in findings[0].message.lower()


def test_config_drift_remote_unreadable():
    """Cannot read remote config -> critical finding."""
    ssh = _mock_ssh({
        "cat /etc/sing-box/config.json": ("", "No such file", 1),
    })
    node = _make_near_node()
    findings = audit_config_drift(ssh, node, {"key": "value"})
    assert len(findings) == 1
    assert findings[0].severity == "critical"
    assert "Cannot read" in findings[0].message


def test_config_drift_invalid_json():
    """Remote config is invalid JSON -> critical finding."""
    ssh = _mock_ssh({
        "cat /etc/sing-box/config.json": ("not valid json{{{", "", 0),
    })
    node = _make_near_node()
    findings = audit_config_drift(ssh, node, {"key": "value"})
    assert len(findings) == 1
    assert "not valid JSON" in findings[0].message


# --- SSH Password Auth Tests ---

def test_ssh_password_auth_enabled():
    """PasswordAuthentication yes -> warning."""
    ssh = _mock_ssh({
        "PasswordAuthentication": ("PasswordAuthentication yes\n", "", 0),
    })
    finding = _check_ssh_password_auth(ssh, "hk-gcp-a")
    assert finding is not None
    assert finding.severity == "warning"
    assert "password authentication" in finding.message.lower()


def test_ssh_password_auth_disabled():
    """PasswordAuthentication no -> no finding."""
    ssh = _mock_ssh({
        "PasswordAuthentication": ("PasswordAuthentication no\n", "", 0),
    })
    finding = _check_ssh_password_auth(ssh, "hk-gcp-a")
    assert finding is None


def test_ssh_password_auth_not_set():
    """No PasswordAuthentication directive -> warning."""
    ssh = _mock_ssh({
        "PasswordAuthentication": ("", "", 1),
    })
    finding = _check_ssh_password_auth(ssh, "hk-gcp-a")
    assert finding is not None
    assert finding.severity == "warning"
    assert "not explicitly set" in finding.message


# --- UFW Tests ---

def test_ufw_inactive():
    """UFW not active -> warning."""
    node = _make_near_node(provider="dmit")
    ssh = _mock_ssh({
        "ufw status": ("Status: inactive\n", "", 0),
    })
    finding = _check_ufw_status(ssh, node)
    assert finding is not None
    assert finding.severity == "warning"
    assert "not active" in finding.message.lower()


def test_ufw_active():
    """UFW active -> no finding."""
    node = _make_near_node(provider="dmit")
    ssh = _mock_ssh({
        "ufw status": ("Status: active\n", "", 0),
    })
    finding = _check_ufw_status(ssh, node)
    assert finding is None


def test_ufw_skip_gcp():
    """GCP provider -> no UFW check."""
    node = _make_near_node(provider="gcp")
    ssh = _mock_ssh({})
    finding = _check_ufw_status(ssh, node)
    assert finding is None


# --- fail2ban Tests ---

def test_fail2ban_not_running():
    """fail2ban inactive -> warning."""
    ssh = _mock_ssh({
        "systemctl is-active fail2ban": ("inactive\n", "", 3),
    })
    finding = _check_fail2ban(ssh, "hk-gcp-a")
    assert finding is not None
    assert finding.severity == "warning"
    assert "fail2ban" in finding.message


def test_fail2ban_running():
    """fail2ban active -> no finding."""
    ssh = _mock_ssh({
        "systemctl is-active fail2ban": ("active\n", "", 0),
    })
    finding = _check_fail2ban(ssh, "hk-gcp-a")
    assert finding is None


# --- Tailscale SSH Tests ---

def test_tailscale_ssh_enabled():
    """Tailscale --ssh enabled -> critical finding."""
    ts_status = json.dumps({"Self": {"SSH": True}})
    node = _make_residential_node()
    ssh = _mock_ssh({
        "tailscale status --json": (ts_status, "", 0),
    })
    finding = _check_tailscale_ssh(ssh, node)
    assert finding is not None
    assert finding.severity == "critical"
    assert "Tailscale SSH" in finding.message


def test_tailscale_ssh_disabled():
    """Tailscale --ssh disabled -> no finding."""
    ts_status = json.dumps({"Self": {"SSH": False}})
    node = _make_residential_node()
    ssh = _mock_ssh({
        "tailscale status --json": (ts_status, "", 0),
    })
    finding = _check_tailscale_ssh(ssh, node)
    assert finding is None


def test_tailscale_ssh_no_tailscale_ip():
    """Node without tailscale_ip -> skip check."""
    node = _make_near_node()  # no tailscale_ip
    ssh = _mock_ssh({})
    finding = _check_tailscale_ssh(ssh, node)
    assert finding is None


# --- Service Active Tests ---

def test_service_active():
    """sing-box active -> no finding."""
    ssh = _mock_ssh({
        "systemctl is-active sing-box": ("active\n", "", 0),
    })
    finding = _check_service_active(ssh, "hk-gcp-a")
    assert finding is None


def test_service_inactive():
    """sing-box inactive -> critical finding."""
    ssh = _mock_ssh({
        "systemctl is-active sing-box": ("inactive\n", "", 3),
    })
    finding = _check_service_active(ssh, "hk-gcp-a")
    assert finding is not None
    assert finding.severity == "critical"
    assert "sing-box" in finding.message


# --- AuditReport Tests ---

def test_audit_report_summary():
    """Verify summary string format."""
    report = AuditReport(findings=[
        AuditFinding(node="a", category="security", severity="critical", message="m1"),
        AuditFinding(node="b", category="security", severity="warning", message="m2"),
        AuditFinding(node="c", category="security", severity="warning", message="m3"),
        AuditFinding(node="d", category="security", severity="info", message="m4"),
    ])
    assert report.critical_count == 1
    assert report.warning_count == 2
    assert report.summary() == "4 findings (1 critical, 2 warning)"


def test_audit_report_empty():
    """Empty report has zero counts."""
    report = AuditReport()
    assert report.critical_count == 0
    assert report.warning_count == 0
    assert report.summary() == "0 findings (0 critical, 0 warning)"
