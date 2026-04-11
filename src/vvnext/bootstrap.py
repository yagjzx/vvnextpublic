"""Bootstrap a new VM for sing-box deployment.

Provider-aware: handles differences between GCP, AWS/DO/DMIT, home/residential,
and China mainland VPS.
"""

from __future__ import annotations

import json
from vvnext.ssh import SshClient
from vvnext.inventory import ServerEntry
from vvnext.settings import Settings


_SYSTEM_PACKAGES = [
    "curl", "wget", "jq", "unzip", "socat", "net-tools",
    "iptables",
]

_SYSCTL_SETTINGS = {
    # TCP tuning
    "net.core.rmem_max": "16777216",
    "net.core.wmem_max": "16777216",
    "net.ipv4.tcp_rmem": "4096 87380 16777216",
    "net.ipv4.tcp_wmem": "4096 65536 16777216",
    "net.ipv4.tcp_congestion_control": "bbr",
    "net.core.default_qdisc": "fq",
    # Forwarding
    "net.ipv4.ip_forward": "1",
    "net.ipv6.conf.all.forwarding": "1",
    # Conntrack (requires modprobe nf_conntrack first)
    "net.netfilter.nf_conntrack_max": "131072",
    "net.netfilter.nf_conntrack_tcp_timeout_established": "7200",
}


def bootstrap_node(ssh: SshClient, node: ServerEntry, settings: Settings) -> None:
    """Full bootstrap sequence for a new node.

    Steps:
    1. Create service user (simba) with sudo
    2. Install system packages (curl, wget, jq, etc.)
    3. Sysctl tuning (CRITICAL: modprobe nf_conntrack FIRST, then sysctl)
    4. Install sing-box (GitHub release, with mirror fallback for China)
    5. Install WireGuard tools
    6. UFW firewall setup (SKIP on GCP -- uses Cloud Firewall)
    7. Install fail2ban
    8. Generate self-signed certs for HY2/AnyTLS
    9. Set up WG MSS clamp iptables rules
    """
    _create_service_user(ssh)
    _install_packages(ssh)
    _tune_sysctl(ssh)
    _install_singbox(ssh, mirror=settings.bootstrap.mirror)
    _install_wireguard(ssh)
    _setup_ufw(ssh, node)
    _install_fail2ban(ssh)
    _generate_self_signed_cert(ssh, node)
    _setup_wg_mss_clamp(ssh)


def _create_service_user(ssh: SshClient, username: str = "simba") -> None:
    """Create a non-root service user with passwordless sudo."""
    # Check if user already exists
    _, _, rc = ssh.exec(f"id {username}", check=False)
    if rc == 0:
        return  # User already exists

    ssh.exec(f"useradd -m -s /bin/bash {username}")
    ssh.exec(f"usermod -aG sudo {username}")
    # Passwordless sudo
    ssh.exec(
        f"echo '{username} ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/{username}"
    )
    ssh.exec(f"chmod 440 /etc/sudoers.d/{username}")


def _install_packages(ssh: SshClient) -> None:
    """Install required system packages."""
    ssh.exec("apt-get update -qq")
    pkg_list = " ".join(_SYSTEM_PACKAGES)
    ssh.exec(f"DEBIAN_FRONTEND=noninteractive apt-get install -y -qq {pkg_list}")


def _tune_sysctl(ssh: SshClient) -> None:
    """Apply sysctl tunings.

    CRITICAL: Must run 'modprobe nf_conntrack' BEFORE setting nf_conntrack
    sysctl values, otherwise sysctl will fail with 'unknown key'.
    """
    # Load conntrack module FIRST -- required before any nf_conntrack sysctl
    ssh.exec("modprobe nf_conntrack")

    # Build sysctl config
    lines = []
    for key, value in _SYSCTL_SETTINGS.items():
        lines.append(f"{key} = {value}")
    content = "\\n".join(lines)
    ssh.exec(f"printf '{content}\\n' > /etc/sysctl.d/99-singbox.conf")
    ssh.exec("sysctl --system")


def _install_singbox(ssh: SshClient, mirror: str = "") -> None:
    """Download and install sing-box binary.

    Default: GitHub releases API to find latest version.
    Fallback: mirror URL if provided (for China mainland where GitHub is blocked).
    Install as systemd service.
    """
    if mirror:
        # Use mirror URL directly for China mainland nodes
        ssh.exec(f"curl -fsSL {mirror} -o /tmp/sing-box.tar.gz")
    else:
        # Query GitHub API for latest release
        ssh.exec(
            "curl -fsSL https://api.github.com/repos/SagerNet/sing-box/releases/latest"
            " -o /tmp/sb_release.json"
        )
        # Extract download URL for linux-amd64
        ssh.exec(
            "cat /tmp/sb_release.json | jq -r"
            " '.assets[] | select(.name | test(\"linux-amd64\")) | .browser_download_url'"
            " | head -1 > /tmp/sb_url.txt"
        )
        ssh.exec(
            "curl -fsSL $(cat /tmp/sb_url.txt) -o /tmp/sing-box.tar.gz"
        )

    # Extract and install
    ssh.exec("mkdir -p /tmp/sing-box-install")
    ssh.exec("tar -xzf /tmp/sing-box.tar.gz -C /tmp/sing-box-install --strip-components=1")
    ssh.exec("install -m 755 /tmp/sing-box-install/sing-box /usr/local/bin/sing-box")

    # Create config directory
    ssh.exec("mkdir -p /etc/sing-box")
    ssh.exec("mkdir -p /etc/sing-box/certs")

    # Install systemd service
    _install_singbox_service(ssh)

    # Cleanup
    ssh.exec("rm -rf /tmp/sing-box-install /tmp/sing-box.tar.gz /tmp/sb_release.json /tmp/sb_url.txt")


def _install_singbox_service(ssh: SshClient) -> None:
    """Install sing-box systemd unit file."""
    unit = (
        "[Unit]\\n"
        "Description=sing-box service\\n"
        "After=network.target\\n"
        "\\n"
        "[Service]\\n"
        "Type=simple\\n"
        "User=simba\\n"
        "ExecStart=/usr/local/bin/sing-box run -c /etc/sing-box/config.json\\n"
        "Restart=on-failure\\n"
        "RestartSec=5\\n"
        "LimitNOFILE=65536\\n"
        "\\n"
        "[Install]\\n"
        "WantedBy=multi-user.target\\n"
    )
    ssh.exec(f"printf '{unit}' > /etc/systemd/system/sing-box.service")
    ssh.exec("systemctl daemon-reload")
    ssh.exec("systemctl enable sing-box")


def _install_wireguard(ssh: SshClient) -> None:
    """Install wireguard-tools."""
    ssh.exec("DEBIAN_FRONTEND=noninteractive apt-get install -y -qq wireguard-tools")


def _setup_ufw(ssh: SshClient, node: ServerEntry) -> None:
    """Configure UFW firewall.

    Skip entirely for GCP (provider='gcp') -- uses Cloud Firewall.
    Opens: SSH(22), sing-box ports based on role.
    """
    if node.provider == "gcp":
        return  # GCP uses Cloud Firewall, skip UFW

    ssh.exec("DEBIAN_FRONTEND=noninteractive apt-get install -y -qq ufw")

    # Default deny incoming
    ssh.exec("ufw default deny incoming")
    ssh.exec("ufw default allow outgoing")

    # Always allow SSH
    ssh.exec("ufw allow 22/tcp")

    # Role-specific ports
    if node.role == "near" and node.port_base is not None:
        # Reality port
        ssh.exec(f"ufw allow {node.port_base}/tcp")
        # HY2 port (port_base + 1)
        ssh.exec(f"ufw allow {node.port_base + 1}/udp")
        # WS-CDN port (2053 default)
        ssh.exec("ufw allow 2053/tcp")
        # AnyTLS port (8443 default)
        ssh.exec("ufw allow 8443/tcp")

    if node.wg_port is not None:
        ssh.exec(f"ufw allow {node.wg_port}/udp")

    # Enable UFW (non-interactive)
    ssh.exec("echo 'y' | ufw enable")


def _install_fail2ban(ssh: SshClient) -> None:
    """Install and enable fail2ban."""
    ssh.exec("DEBIAN_FRONTEND=noninteractive apt-get install -y -qq fail2ban")
    ssh.exec("systemctl enable fail2ban")
    ssh.exec("systemctl start fail2ban")


def _generate_self_signed_cert(ssh: SshClient, node: ServerEntry) -> None:
    """Generate self-signed TLS certificate for HY2/AnyTLS.

    Only for near nodes. Store in /etc/sing-box/certs/.
    Cert permissions: 644 (NOT 600 -- sing-box runs as non-root).
    """
    if node.role != "near":
        return  # Only near nodes need self-signed certs

    sni = node.sni or "localhost"
    ssh.exec("mkdir -p /etc/sing-box/certs")
    ssh.exec(
        f"openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:prime256v1"
        f" -keyout /etc/sing-box/certs/key.pem"
        f" -out /etc/sing-box/certs/cert.pem"
        f" -days 3650 -nodes"
        f" -subj '/CN={sni}'"
    )
    # CRITICAL: 644 not 600 -- sing-box runs as non-root user (simba)
    ssh.exec("chmod 644 /etc/sing-box/certs/key.pem")
    ssh.exec("chmod 644 /etc/sing-box/certs/cert.pem")
    ssh.exec("chown simba:simba /etc/sing-box/certs/key.pem")
    ssh.exec("chown simba:simba /etc/sing-box/certs/cert.pem")


def _setup_wg_mss_clamp(ssh: SshClient) -> None:
    """Set up iptables MSS clamp rules for WG tunnels.

    Prevents TCP fragmentation black holes through WG overlay.
    """
    ssh.exec(
        "iptables -t mangle -A FORWARD -p tcp --tcp-flags SYN,RST SYN"
        " -j TCPMSS --clamp-mss-to-pmtu"
    )
    # Persist iptables rules across reboots
    ssh.exec("DEBIAN_FRONTEND=noninteractive apt-get install -y -qq iptables-persistent")
    ssh.exec("netfilter-persistent save")
