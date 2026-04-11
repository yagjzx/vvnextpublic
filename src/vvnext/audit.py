"""Config drift detection and security audit."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from vvnext.ssh import SshClient
from vvnext.inventory import Inventory, ServerEntry
from vvnext.settings import Settings


@dataclass
class AuditFinding:
    node: str
    category: str  # "config_drift", "security", "service"
    severity: str  # "critical", "warning", "info"
    message: str
    detail: str = ""


@dataclass
class AuditReport:
    findings: list[AuditFinding] = field(default_factory=list)

    @property
    def critical_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == "critical")

    @property
    def warning_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == "warning")

    def summary(self) -> str:
        return f"{len(self.findings)} findings ({self.critical_count} critical, {self.warning_count} warning)"


def _check_ssh_password_auth(ssh: SshClient, node_name: str) -> AuditFinding | None:
    """Check if password auth is disabled in sshd_config."""
    out, _, rc = ssh.exec("grep -i '^PasswordAuthentication' /etc/ssh/sshd_config", check=False)
    if rc != 0:
        # grep found nothing — could mean commented out or default
        return AuditFinding(
            node=node_name,
            category="security",
            severity="warning",
            message="PasswordAuthentication not explicitly set in sshd_config",
            detail="No PasswordAuthentication directive found",
        )
    line = out.strip().split("\n")[0].strip()
    # Check if password auth is enabled
    parts = line.split()
    if len(parts) >= 2 and parts[1].lower() == "yes":
        return AuditFinding(
            node=node_name,
            category="security",
            severity="warning",
            message="SSH password authentication is enabled",
            detail=f"Found: {line}",
        )
    return None


def _check_ufw_status(ssh: SshClient, node: ServerEntry) -> AuditFinding | None:
    """Check UFW is active. Skip for GCP (uses VPC firewall)."""
    if node.provider == "gcp":
        return None
    out, _, rc = ssh.exec("ufw status", check=False)
    if rc != 0 or "inactive" in out.lower():
        return AuditFinding(
            node=node.name,
            category="security",
            severity="warning",
            message="UFW firewall is not active",
            detail=out.strip() if out.strip() else "ufw command failed",
        )
    return None


def _check_fail2ban(ssh: SshClient, node_name: str) -> AuditFinding | None:
    """Check fail2ban is running."""
    out, _, rc = ssh.exec("systemctl is-active fail2ban", check=False)
    status = out.strip()
    if status != "active":
        return AuditFinding(
            node=node_name,
            category="security",
            severity="warning",
            message="fail2ban is not running",
            detail=f"Status: {status}",
        )
    return None


def _check_tailscale_ssh(ssh: SshClient, node: ServerEntry) -> AuditFinding | None:
    """Check Tailscale --ssh is NOT enabled (only for nodes with tailscale_ip)."""
    if not node.tailscale_ip:
        return None
    out, _, rc = ssh.exec("tailscale status --json", check=False)
    if rc != 0:
        return None  # Tailscale not installed or not running, skip
    try:
        ts_data = json.loads(out)
        # Check if SSH is enabled in the self node
        self_node = ts_data.get("Self", {})
        if self_node.get("SSH", False):
            return AuditFinding(
                node=node.name,
                category="security",
                severity="critical",
                message="Tailscale SSH is enabled (security risk)",
                detail="Tailscale --ssh bypasses standard SSH hardening",
            )
    except (json.JSONDecodeError, KeyError):
        pass
    return None


def _check_service_active(ssh: SshClient, node_name: str, service: str = "sing-box") -> AuditFinding | None:
    """Check systemd service is active."""
    out, _, rc = ssh.exec(f"systemctl is-active {service}", check=False)
    status = out.strip()
    if status != "active":
        return AuditFinding(
            node=node_name,
            category="service",
            severity="critical",
            message=f"{service} service is not active",
            detail=f"Status: {status}",
        )
    return None


def audit_config_drift(ssh: SshClient, node: ServerEntry, local_config: dict) -> list[AuditFinding]:
    """Compare local config with remote config on node."""
    findings: list[AuditFinding] = []
    out, _, rc = ssh.exec("cat /etc/sing-box/config.json", check=False)
    if rc != 0:
        findings.append(AuditFinding(
            node=node.name,
            category="config_drift",
            severity="critical",
            message="Cannot read remote config",
            detail=f"Failed to read /etc/sing-box/config.json (rc={rc})",
        ))
        return findings

    try:
        remote_config = json.loads(out)
    except json.JSONDecodeError as e:
        findings.append(AuditFinding(
            node=node.name,
            category="config_drift",
            severity="critical",
            message="Remote config is not valid JSON",
            detail=str(e),
        ))
        return findings

    # Normalize for comparison: sort keys
    local_normalized = json.dumps(local_config, sort_keys=True)
    remote_normalized = json.dumps(remote_config, sort_keys=True)

    if local_normalized != remote_normalized:
        findings.append(AuditFinding(
            node=node.name,
            category="config_drift",
            severity="critical",
            message="Config drift detected: local and remote configs differ",
            detail="Run diff to see exact changes",
        ))

    return findings


def audit_security(ssh: SshClient, node: ServerEntry) -> list[AuditFinding]:
    """Security checks: SSH config, UFW, fail2ban, Tailscale."""
    findings: list[AuditFinding] = []

    result = _check_ssh_password_auth(ssh, node.name)
    if result:
        findings.append(result)

    result = _check_ufw_status(ssh, node)
    if result:
        findings.append(result)

    result = _check_fail2ban(ssh, node.name)
    if result:
        findings.append(result)

    result = _check_tailscale_ssh(ssh, node)
    if result:
        findings.append(result)

    return findings


def audit_fleet(
    inventory: Inventory,
    settings: Settings,
    local_configs: dict[str, dict] | None = None,
) -> AuditReport:
    """Run full audit on all nodes.

    Checks:
    1. Config drift: compare local rendered config vs remote /etc/sing-box/config.json
    2. SSH key auth: password auth should be disabled
    3. UFW status: should be enabled (skip GCP)
    4. fail2ban status: should be running
    5. sing-box service: should be active
    6. Tailscale --ssh: should NOT be enabled (security risk)
    """
    report = AuditReport()
    all_nodes = [s for s in inventory.servers if s.phase == "live"]

    for node in all_nodes:
        # Determine SSH target
        ssh_target = inventory.get_ssh_target(node, inventory.defaults)
        user, host = ssh_target.split("@", 1)
        ssh = SshClient(
            host=host,
            user=user,
            key_path=settings.ssh.key_path,
            timeout=settings.ssh.timeout,
        )
        try:
            ssh.connect()

            # Config drift (only if local config provided)
            if local_configs and node.name in local_configs:
                drift_findings = audit_config_drift(ssh, node, local_configs[node.name])
                report.findings.extend(drift_findings)

            # Security checks
            security_findings = audit_security(ssh, node)
            report.findings.extend(security_findings)

            # Service check
            service_finding = _check_service_active(ssh, node.name)
            if service_finding:
                report.findings.append(service_finding)

        except Exception as e:
            report.findings.append(AuditFinding(
                node=node.name,
                category="service",
                severity="critical",
                message=f"Cannot connect to node: {e}",
            ))
        finally:
            ssh.close()

    return report
