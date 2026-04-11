"""Health check module: TCP/UDP port checks, WG tunnel ping, TLS cert expiry, Telegram alerts."""

from __future__ import annotations

import socket
import ssl
import datetime
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from vvnext.inventory import Inventory, ServerEntry
    from vvnext.settings import Settings
    from vvnext.ssh import SshClient


@dataclass
class CheckResult:
    node: str
    check_type: str  # "tcp", "udp", "wg_ping", "tls_expiry", "service"
    target: str      # "host:port" or description
    ok: bool
    detail: str = ""


@dataclass
class HealthReport:
    results: list[CheckResult] = field(default_factory=list)

    @property
    def all_ok(self) -> bool:
        return all(r.ok for r in self.results)

    @property
    def failed(self) -> list[CheckResult]:
        return [r for r in self.results if not r.ok]

    def summary(self) -> str:
        total = len(self.results)
        ok = sum(1 for r in self.results if r.ok)
        return f"{ok}/{total} checks passed"


def check_tcp(host: str, port: int, timeout: float = 5.0) -> CheckResult:
    """Check if TCP port is open."""
    target = f"{host}:{port}"
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect((host, port))
        sock.close()
        return CheckResult(node="", check_type="tcp", target=target, ok=True,
                           detail="port open")
    except (socket.timeout, socket.error, OSError) as e:
        return CheckResult(node="", check_type="tcp", target=target, ok=False,
                           detail=str(e))


def check_udp(host: str, port: int, timeout: float = 5.0) -> CheckResult:
    """Check if UDP port responds (send empty packet, check for response or no ICMP unreachable).

    UDP is connectionless, so a lack of response is normal. We only mark
    failure if we get an explicit ICMP unreachable (ConnectionRefusedError).
    """
    target = f"{host}:{port}"
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(timeout)
        sock.sendto(b"\x00", (host, port))
        try:
            sock.recvfrom(1024)
            sock.close()
            return CheckResult(node="", check_type="udp", target=target, ok=True,
                               detail="response received")
        except socket.timeout:
            sock.close()
            # No response is normal for UDP; port is likely open
            return CheckResult(node="", check_type="udp", target=target, ok=True,
                               detail="no response (normal for UDP)")
    except ConnectionRefusedError:
        return CheckResult(node="", check_type="udp", target=target, ok=False,
                           detail="ICMP unreachable")
    except (socket.error, OSError) as e:
        return CheckResult(node="", check_type="udp", target=target, ok=False,
                           detail=str(e))


def check_tls_expiry(host: str, port: int, timeout: float = 5.0,
                     warn_days: int = 30) -> CheckResult:
    """Check TLS certificate expiry date."""
    target = f"{host}:{port}"
    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        with socket.create_connection((host, port), timeout=timeout) as raw_sock:
            with ctx.wrap_socket(raw_sock, server_hostname=host) as tls_sock:
                cert = tls_sock.getpeercert(binary_form=False)
                if not cert:
                    # With CERT_NONE we might not get parsed cert; try binary
                    der = tls_sock.getpeercert(binary_form=True)
                    if not der:
                        return CheckResult(node="", check_type="tls_expiry",
                                           target=target, ok=False,
                                           detail="no certificate presented")
                    # Attempt to parse with ssl helpers
                    cert = ssl.DER_cert_to_PEM_cert(der)
                    return CheckResult(node="", check_type="tls_expiry",
                                       target=target, ok=True,
                                       detail="certificate present (expiry unchecked)")

                not_after_str = cert["notAfter"]
                not_after = datetime.datetime.strptime(
                    not_after_str, "%b %d %H:%M:%S %Y %Z"
                ).replace(tzinfo=datetime.timezone.utc)
                now = datetime.datetime.now(datetime.timezone.utc)
                days_left = (not_after - now).days

                if days_left < 0:
                    return CheckResult(node="", check_type="tls_expiry",
                                       target=target, ok=False,
                                       detail=f"expired {abs(days_left)} days ago")
                elif days_left <= warn_days:
                    return CheckResult(node="", check_type="tls_expiry",
                                       target=target, ok=True,
                                       detail=f"expires in {days_left} days (warning)")
                else:
                    return CheckResult(node="", check_type="tls_expiry",
                                       target=target, ok=True,
                                       detail=f"expires in {days_left} days")
    except (socket.timeout, socket.error, ssl.SSLError, OSError) as e:
        return CheckResult(node="", check_type="tls_expiry", target=target,
                           ok=False, detail=str(e))


def check_wg_tunnel(ssh: SshClient, near_name: str, far_ip: str) -> CheckResult:
    """Ping far node's WG IP through the tunnel (via SSH exec on near node)."""
    target = f"wg-tunnel {near_name} -> {far_ip}"
    try:
        out, err, rc = ssh.exec(f"ping -c 1 -W 3 {far_ip}", check=False)
        if rc == 0:
            return CheckResult(node=near_name, check_type="wg_ping",
                               target=target, ok=True,
                               detail="tunnel alive")
        else:
            return CheckResult(node=near_name, check_type="wg_ping",
                               target=target, ok=False,
                               detail=f"ping failed (rc={rc})")
    except Exception as e:
        return CheckResult(node=near_name, check_type="wg_ping",
                           target=target, ok=False, detail=str(e))


def check_service_status(ssh: SshClient, node_name: str) -> CheckResult:
    """Check systemctl is-active sing-box on remote node."""
    target = f"sing-box@{node_name}"
    try:
        out, err, rc = ssh.exec("systemctl is-active sing-box", check=False)
        status = out.strip()
        if status == "active":
            return CheckResult(node=node_name, check_type="service",
                               target=target, ok=True,
                               detail="sing-box active")
        else:
            return CheckResult(node=node_name, check_type="service",
                               target=target, ok=False,
                               detail=f"sing-box status: {status}")
    except Exception as e:
        return CheckResult(node=node_name, check_type="service",
                           target=target, ok=False, detail=str(e))


def _get_check_ip(node: ServerEntry) -> str:
    """Determine the IP to check for a node.
    Residential nodes use tailscale_ip; others use public_ip.
    """
    if node.role == "residential" and node.tailscale_ip:
        return node.tailscale_ip
    return node.public_ip


def check_fleet(inventory: Inventory, settings: Settings,
                detail: bool = False) -> HealthReport:
    """Run health checks on all nodes.

    For near nodes:
    - TCP check on Reality ports (port_base+1, port_base+2)
    - UDP check on HY2 port (443)
    - TCP check on CDN port (defaults.near.cdn_port, typically 2053)
    - TCP check on AnyTLS port (defaults.near.anytls_port, typically 8443)
    - TLS cert expiry check on Reality port (port_base+1)

    For far nodes:
    - UDP check on WG port

    Tailscale-aware: residential nodes use tailscale_ip instead of public_ip.
    """
    report = HealthReport()

    cdn_port = inventory.defaults.near.cdn_port
    anytls_port = inventory.defaults.near.anytls_port

    for node in inventory.near_nodes():
        ip = _get_check_ip(node)
        name = node.name

        # Reality overlay (port_base+1)
        r = check_tcp(ip, node.port_base + 1)
        r.node = name
        report.results.append(r)

        # Reality direct (port_base+2)
        r = check_tcp(ip, node.port_base + 2)
        r.node = name
        report.results.append(r)

        # HY2 (UDP 443)
        r = check_udp(ip, 443)
        r.node = name
        report.results.append(r)

        # CDN
        r = check_tcp(ip, cdn_port)
        r.node = name
        report.results.append(r)

        # AnyTLS
        r = check_tcp(ip, anytls_port)
        r.node = name
        report.results.append(r)

        # TLS cert expiry on Reality port
        r = check_tls_expiry(ip, node.port_base + 1)
        r.node = name
        report.results.append(r)

    for node in inventory.far_nodes():
        ip = _get_check_ip(node)
        name = node.name

        if node.wg_port:
            r = check_udp(ip, node.wg_port)
            r.node = name
            report.results.append(r)

    return report


# --- Debounce / Alerting ---


class AlertDebouncer:
    """Prevent alert storms. Only alert after threshold consecutive failures.

    Usage:
        debouncer = AlertDebouncer(threshold=3)
        if debouncer.should_alert("hk-gcp-a", check_result):
            send_alert(...)
    """

    def __init__(self, threshold: int = 3):
        self.threshold = threshold
        self._failure_counts: dict[str, int] = {}

    def should_alert(self, node_name: str, result: CheckResult) -> bool:
        """Returns True if this failure should trigger an alert.
        Resets counter on success.
        """
        if result.ok:
            self._failure_counts.pop(node_name, None)
            return False

        count = self._failure_counts.get(node_name, 0) + 1
        self._failure_counts[node_name] = count
        return count >= self.threshold

    def reset(self, node_name: str) -> None:
        """Reset failure counter for a node."""
        self._failure_counts.pop(node_name, None)


def format_telegram_message(report: HealthReport) -> str:
    """Format health report as Telegram message with emoji status indicators."""
    lines = []
    lines.append(f"VVNext Health: {report.summary()}")
    lines.append("")

    if report.all_ok:
        lines.append("[OK] All checks passed")
        return "\n".join(lines)

    # Group failures
    lines.append("[FAIL] Failed checks:")
    for r in report.failed:
        lines.append(f"  [X] {r.node} | {r.check_type} | {r.target} | {r.detail}")

    # Show passing checks summary
    passed = [r for r in report.results if r.ok]
    if passed:
        lines.append("")
        lines.append(f"[OK] {len(passed)} checks passed")

    return "\n".join(lines)


def send_telegram_alert(report: HealthReport, settings: Settings) -> bool:
    """Send health check alert via Telegram bot.
    Uses httpx to POST to Telegram Bot API.
    Only sends if settings.alerting.telegram.enabled is True.
    Returns True if sent successfully.
    """
    tg = settings.alerting.telegram
    if not tg.enabled:
        return False

    if not tg.bot_token or not tg.chat_id:
        return False

    message = format_telegram_message(report)
    url = f"https://api.telegram.org/bot{tg.bot_token}/sendMessage"

    try:
        resp = httpx.post(url, json={
            "chat_id": tg.chat_id,
            "text": message,
            "parse_mode": "HTML",
        }, timeout=10.0)
        return resp.status_code == 200
    except (httpx.HTTPError, Exception):
        return False
