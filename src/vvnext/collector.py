"""Monitoring metrics collector for sing-box fleet nodes.

Collects 5 categories of metrics via SSH:
- System: CPU, memory, disk, load average
- sing-box: active connections, up/down traffic
- Network: TCP connections, interface traffic
- WireGuard: peer status, last handshake
- Certificates: TLS certificate expiry days

Results can be pushed to InfluxDB or printed to stdout.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional

from vvnext.ssh import SshClient
from vvnext.inventory import Inventory, ServerEntry
from vvnext.settings import Settings


@dataclass
class NodeMetrics:
    node: str = ""
    timestamp: float = 0.0
    # System
    cpu_percent: float = 0.0
    mem_used_mb: int = 0
    mem_total_mb: int = 0
    disk_used_gb: int = 0
    disk_total_gb: int = 0
    load_1m: float = 0.0
    load_5m: float = 0.0
    load_15m: float = 0.0
    # sing-box
    singbox_active: bool = False
    singbox_connections: int = 0
    # Network
    tcp_established: int = 0
    tcp_time_wait: int = 0
    # WireGuard
    wg_peers_up: int = 0
    wg_peers_total: int = 0
    wg_last_handshake_ago_secs: int = 0
    # Certificates
    cert_expiry_days: int = -1
    # Errors during collection
    errors: list[str] = field(default_factory=list)


def collect_node_metrics(ssh: SshClient, node: ServerEntry) -> NodeMetrics:
    """Collect all metrics from a single node via SSH."""
    m = NodeMetrics(node=node.name, timestamp=time.time())

    # System metrics
    _collect_system(ssh, m)
    _collect_singbox(ssh, m)
    _collect_network(ssh, m)
    _collect_wireguard(ssh, m)
    _collect_certs(ssh, m)

    return m


def _collect_system(ssh: SshClient, m: NodeMetrics) -> None:
    """CPU, memory, disk, load average."""
    try:
        # Load average
        out, _, _ = ssh.exec("cat /proc/loadavg", check=False)
        parts = out.strip().split()
        if len(parts) >= 3:
            m.load_1m = float(parts[0])
            m.load_5m = float(parts[1])
            m.load_15m = float(parts[2])
    except Exception as e:
        m.errors.append(f"load: {e}")

    try:
        # CPU usage (1-second sample)
        out, _, _ = ssh.exec(
            "grep 'cpu ' /proc/stat | awk '{u=$2+$4; t=$2+$4+$5; printf \"%.1f\", u/t*100}'",
            check=False,
        )
        m.cpu_percent = float(out.strip()) if out.strip() else 0.0
    except Exception as e:
        m.errors.append(f"cpu: {e}")

    try:
        # Memory
        out, _, _ = ssh.exec(
            "awk '/MemTotal/ {t=$2} /MemAvailable/ {a=$2} END {printf \"%d %d\", t/1024, (t-a)/1024}' /proc/meminfo",
            check=False,
        )
        parts = out.strip().split()
        if len(parts) >= 2:
            m.mem_total_mb = int(parts[0])
            m.mem_used_mb = int(parts[1])
    except Exception as e:
        m.errors.append(f"mem: {e}")

    try:
        # Disk (root partition)
        out, _, _ = ssh.exec(
            "df -BG / | awk 'NR==2 {gsub(/G/,\"\"); printf \"%d %d\", $3, $2}'",
            check=False,
        )
        parts = out.strip().split()
        if len(parts) >= 2:
            m.disk_used_gb = int(parts[0])
            m.disk_total_gb = int(parts[1])
    except Exception as e:
        m.errors.append(f"disk: {e}")


def _collect_singbox(ssh: SshClient, m: NodeMetrics) -> None:
    """sing-box service status and connection count."""
    try:
        out, _, rc = ssh.exec("sudo systemctl is-active sing-box", check=False)
        m.singbox_active = out.strip() == "active"
    except Exception as e:
        m.errors.append(f"singbox_status: {e}")

    try:
        # Connection count via sing-box API (if available)
        out, _, _ = ssh.exec(
            "curl -s http://127.0.0.1:9090/connections 2>/dev/null | grep -o '\"id\"' | wc -l",
            check=False,
        )
        m.singbox_connections = int(out.strip()) if out.strip() else 0
    except Exception as e:
        m.errors.append(f"singbox_connections: {e}")


def _collect_network(ssh: SshClient, m: NodeMetrics) -> None:
    """TCP connection statistics."""
    try:
        out, _, _ = ssh.exec(
            "ss -t state established | wc -l",
            check=False,
        )
        count = int(out.strip()) if out.strip() else 0
        m.tcp_established = max(0, count - 1)  # subtract header line
    except Exception as e:
        m.errors.append(f"tcp_established: {e}")

    try:
        out, _, _ = ssh.exec(
            "ss -t state time-wait | wc -l",
            check=False,
        )
        count = int(out.strip()) if out.strip() else 0
        m.tcp_time_wait = max(0, count - 1)
    except Exception as e:
        m.errors.append(f"tcp_time_wait: {e}")


def _collect_wireguard(ssh: SshClient, m: NodeMetrics) -> None:
    """WireGuard peer status and last handshake."""
    try:
        out, _, rc = ssh.exec("sudo wg show all latest-handshakes", check=False)
        if rc != 0 or not out.strip():
            return

        now = time.time()
        worst_ago = 0
        peers = 0
        up = 0
        for line in out.strip().splitlines():
            parts = line.split()
            if len(parts) >= 3:
                peers += 1
                try:
                    last_hs = int(parts[2])
                    if last_hs > 0:
                        ago = int(now - last_hs)
                        if ago < 300:  # handshake within 5 minutes = up
                            up += 1
                        worst_ago = max(worst_ago, ago)
                except ValueError:
                    pass

        m.wg_peers_total = peers
        m.wg_peers_up = up
        m.wg_last_handshake_ago_secs = worst_ago
    except Exception as e:
        m.errors.append(f"wg: {e}")


def _collect_certs(ssh: SshClient, m: NodeMetrics) -> None:
    """TLS certificate expiry for sing-box certs."""
    try:
        out, _, rc = ssh.exec(
            "find /etc/sing-box/certs -name '*.pem' -o -name '*.crt' 2>/dev/null | head -1",
            check=False,
        )
        cert_path = out.strip()
        if not cert_path:
            return

        out, _, rc = ssh.exec(
            f"openssl x509 -enddate -noout -in {cert_path} 2>/dev/null"
            " | sed 's/notAfter=//'",
            check=False,
        )
        if rc != 0 or not out.strip():
            return

        # Parse expiry date
        out2, _, _ = ssh.exec(
            f"echo $(( ($(date -d '{out.strip()}' +%s) - $(date +%s)) / 86400 ))",
            check=False,
        )
        m.cert_expiry_days = int(out2.strip()) if out2.strip() else -1
    except Exception as e:
        m.errors.append(f"certs: {e}")


def collect_fleet(
    inventory: Inventory,
    settings: Settings,
    targets: list[str] | None = None,
) -> list[NodeMetrics]:
    """Collect metrics from all live nodes (or specified targets)."""
    results: list[NodeMetrics] = []

    for node in inventory.servers:
        if node.phase != "live":
            continue
        if targets and node.name not in targets:
            continue

        host = node.tailscale_ip or node.public_ip
        try:
            ssh = SshClient(
                host=host,
                user=settings.ssh.user,
                key_path=settings.ssh.key_path,
                timeout=settings.ssh.timeout,
            )
            ssh.connect()
            m = collect_node_metrics(ssh, node)
            ssh.close()
            results.append(m)
        except Exception as e:
            m = NodeMetrics(node=node.name, timestamp=time.time())
            m.errors.append(f"ssh: {e}")
            results.append(m)

    return results


def push_to_influxdb(
    metrics: list[NodeMetrics],
    settings: Settings,
) -> bool:
    """Push metrics to InfluxDB. Returns True on success."""
    influx = settings.monitoring.influxdb
    if not influx.enabled or not influx.url:
        return False

    try:
        import httpx
    except ImportError:
        return False

    # Build InfluxDB line protocol
    lines: list[str] = []
    for m in metrics:
        ts_ns = int(m.timestamp * 1e9)
        tags = f"node={m.node}"

        # System metrics
        lines.append(
            f"system,{tags} cpu={m.cpu_percent},"
            f"mem_used={m.mem_used_mb}i,mem_total={m.mem_total_mb}i,"
            f"disk_used={m.disk_used_gb}i,disk_total={m.disk_total_gb}i,"
            f"load_1m={m.load_1m},load_5m={m.load_5m},load_15m={m.load_15m} {ts_ns}"
        )

        # sing-box metrics
        active = 1 if m.singbox_active else 0
        lines.append(
            f"singbox,{tags} active={active}i,connections={m.singbox_connections}i {ts_ns}"
        )

        # Network
        lines.append(
            f"network,{tags} tcp_established={m.tcp_established}i,"
            f"tcp_time_wait={m.tcp_time_wait}i {ts_ns}"
        )

        # WireGuard
        lines.append(
            f"wireguard,{tags} peers_up={m.wg_peers_up}i,"
            f"peers_total={m.wg_peers_total}i,"
            f"last_handshake_ago={m.wg_last_handshake_ago_secs}i {ts_ns}"
        )

        # Certs
        if m.cert_expiry_days >= 0:
            lines.append(
                f"certs,{tags} expiry_days={m.cert_expiry_days}i {ts_ns}"
            )

    body = "\n".join(lines)

    try:
        resp = httpx.post(
            f"{influx.url}/api/v2/write",
            params={"org": influx.org, "bucket": influx.bucket, "precision": "ns"},
            headers={"Authorization": f"Token {influx.token}" if hasattr(influx, "token") else ""},
            content=body,
            timeout=10,
        )
        return resp.status_code in (200, 204)
    except Exception:
        return False


def format_metrics_table(metrics: list[NodeMetrics]) -> str:
    """Format metrics as a human-readable table."""
    lines: list[str] = []
    header = f"{'Node':<20} {'CPU%':>5} {'Mem':>10} {'Disk':>10} {'Load':>8} {'SB':>4} {'TCP':>6} {'WG':>6} {'Cert':>5}"
    lines.append(header)
    lines.append("-" * len(header))

    for m in metrics:
        mem = f"{m.mem_used_mb}/{m.mem_total_mb}" if m.mem_total_mb else "-"
        disk = f"{m.disk_used_gb}/{m.disk_total_gb}" if m.disk_total_gb else "-"
        load = f"{m.load_1m:.1f}"
        sb = "UP" if m.singbox_active else "DOWN"
        tcp = str(m.tcp_established)
        wg = f"{m.wg_peers_up}/{m.wg_peers_total}" if m.wg_peers_total else "-"
        cert = str(m.cert_expiry_days) if m.cert_expiry_days >= 0 else "-"
        lines.append(f"{m.node:<20} {m.cpu_percent:>5.1f} {mem:>10} {disk:>10} {load:>8} {sb:>4} {tcp:>6} {wg:>6} {cert:>5}")

        if m.errors:
            for err in m.errors:
                lines.append(f"  ! {err}")

    return "\n".join(lines)
