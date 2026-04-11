"""Tests for bootstrap.py -- all SSH calls mocked."""

import pytest
from unittest.mock import MagicMock, call, patch

from vvnext.bootstrap import (
    bootstrap_node,
    _create_service_user,
    _install_packages,
    _tune_sysctl,
    _install_singbox,
    _install_wireguard,
    _setup_ufw,
    _install_fail2ban,
    _generate_self_signed_cert,
    _setup_wg_mss_clamp,
)
from vvnext.inventory import ServerEntry
from vvnext.settings import Settings


def _make_ssh():
    """Create a mock SshClient."""
    ssh = MagicMock()
    # Default: exec returns success
    ssh.exec.return_value = ("", "", 0)
    return ssh


def _make_near_node(provider="dmit", name="hk-dmit-a"):
    """Create a near-role ServerEntry for testing."""
    return ServerEntry(
        name=name,
        role="near",
        region="hk",
        provider=provider,
        public_ip="10.0.0.1",
        port_base=20000,
        sni="dl.google.com",
        hy2_sni="hk.test.com",
        cdn_domain="hk-cdn.test.com",
        dns_name="hk-a.test.com",
    )


def _make_far_node(provider="gcp", name="us-gcp-a"):
    """Create a far-role ServerEntry for testing."""
    return ServerEntry(
        name=name,
        role="far",
        region="us",
        provider=provider,
        public_ip="10.0.0.3",
        wg_port=51941,
    )


def _make_settings(mirror=""):
    """Create a Settings instance for testing."""
    return Settings(bootstrap={"mirror": mirror})


class TestBootstrapFullSequence:
    """Verify all steps called in order."""

    def test_bootstrap_full_sequence(self):
        ssh = _make_ssh()
        node = _make_near_node()
        settings = _make_settings()

        bootstrap_node(ssh, node, settings)

        # Collect all commands executed
        cmds = [c.args[0] for c in ssh.exec.call_args_list]

        # Verify ordering: service user -> packages -> sysctl -> singbox -> wg -> ufw -> fail2ban -> cert -> mss
        # Find index of key markers for each phase
        useradd_idx = next(i for i, c in enumerate(cmds) if "useradd" in c or "id simba" in c)
        apt_update_idx = next(i for i, c in enumerate(cmds) if "apt-get update" in c)
        modprobe_idx = next(i for i, c in enumerate(cmds) if "modprobe nf_conntrack" in c)
        # sing-box install: look for the GitHub API call specifically
        singbox_idx = next(i for i, c in enumerate(cmds) if "api.github.com" in c)
        # WireGuard install: the dedicated _install_wireguard step
        wireguard_idx = next(
            i for i, c in enumerate(cmds)
            if "wireguard-tools" in c and i > singbox_idx
        )
        ufw_idx = next(i for i, c in enumerate(cmds) if "ufw" in c)
        fail2ban_idx = next(i for i, c in enumerate(cmds) if "fail2ban" in c)
        openssl_idx = next(i for i, c in enumerate(cmds) if "openssl" in c)
        mss_idx = next(i for i, c in enumerate(cmds) if "TCPMSS" in c)

        assert useradd_idx < apt_update_idx
        assert apt_update_idx < modprobe_idx
        assert modprobe_idx < singbox_idx
        assert singbox_idx < wireguard_idx
        assert wireguard_idx < ufw_idx
        assert ufw_idx < fail2ban_idx
        assert fail2ban_idx < openssl_idx
        assert openssl_idx < mss_idx


class TestGcpSkipsUfw:
    """provider=gcp -> no UFW commands."""

    def test_bootstrap_gcp_skips_ufw(self):
        ssh = _make_ssh()
        node = _make_near_node(provider="gcp", name="hk-gcp-a")
        settings = _make_settings()

        bootstrap_node(ssh, node, settings)

        cmds = [c.args[0] for c in ssh.exec.call_args_list]
        ufw_cmds = [c for c in cmds if "ufw" in c]
        assert ufw_cmds == [], f"GCP node should not have UFW commands, got: {ufw_cmds}"

    def test_non_gcp_has_ufw(self):
        ssh = _make_ssh()
        node = _make_near_node(provider="dmit", name="hk-dmit-a")
        settings = _make_settings()

        bootstrap_node(ssh, node, settings)

        cmds = [c.args[0] for c in ssh.exec.call_args_list]
        ufw_cmds = [c for c in cmds if "ufw" in c]
        assert len(ufw_cmds) > 0, "Non-GCP node should have UFW commands"


class TestSysctlModprobeOrder:
    """modprobe nf_conntrack BEFORE sysctl."""

    def test_sysctl_modprobe_order(self):
        ssh = _make_ssh()

        _tune_sysctl(ssh)

        cmds = [c.args[0] for c in ssh.exec.call_args_list]

        # modprobe must be the FIRST command
        assert "modprobe nf_conntrack" in cmds[0], (
            f"First command must be modprobe nf_conntrack, got: {cmds[0]}"
        )

        # sysctl --system must come after modprobe
        modprobe_idx = next(i for i, c in enumerate(cmds) if "modprobe nf_conntrack" in c)
        sysctl_idx = next(i for i, c in enumerate(cmds) if "sysctl --system" in c)
        assert modprobe_idx < sysctl_idx, "modprobe must come before sysctl --system"

    def test_sysctl_writes_config_file(self):
        ssh = _make_ssh()

        _tune_sysctl(ssh)

        cmds = [c.args[0] for c in ssh.exec.call_args_list]
        config_cmds = [c for c in cmds if "99-singbox.conf" in c]
        assert len(config_cmds) == 1, "Should write sysctl config file"
        assert "net.ipv4.ip_forward" in config_cmds[0]
        assert "nf_conntrack_max" in config_cmds[0]


class TestCertPermissions:
    """Verify chmod 644 not 600."""

    def test_cert_permissions_644(self):
        ssh = _make_ssh()
        node = _make_near_node()

        _generate_self_signed_cert(ssh, node)

        cmds = [c.args[0] for c in ssh.exec.call_args_list]

        # Verify 644 permissions on key and cert
        chmod_cmds = [c for c in cmds if "chmod" in c]
        assert len(chmod_cmds) == 2, "Should chmod both key.pem and cert.pem"
        for cmd in chmod_cmds:
            assert "644" in cmd, f"Cert permissions must be 644, got: {cmd}"
            assert "600" not in cmd, f"Cert permissions must NOT be 600, got: {cmd}"

    def test_cert_ownership_simba(self):
        ssh = _make_ssh()
        node = _make_near_node()

        _generate_self_signed_cert(ssh, node)

        cmds = [c.args[0] for c in ssh.exec.call_args_list]
        chown_cmds = [c for c in cmds if "chown" in c]
        assert len(chown_cmds) == 2
        for cmd in chown_cmds:
            assert "simba:simba" in cmd

    def test_cert_skipped_for_far_node(self):
        ssh = _make_ssh()
        node = _make_far_node()

        _generate_self_signed_cert(ssh, node)

        # No commands should be issued for far nodes
        ssh.exec.assert_not_called()


class TestSingboxMirrorFallback:
    """When mirror is set, use it instead of GitHub."""

    def test_singbox_mirror_fallback(self):
        ssh = _make_ssh()
        mirror_url = "https://mirror.example.com/sing-box-latest.tar.gz"

        _install_singbox(ssh, mirror=mirror_url)

        cmds = [c.args[0] for c in ssh.exec.call_args_list]

        # Should use mirror URL
        mirror_cmds = [c for c in cmds if mirror_url in c]
        assert len(mirror_cmds) == 1, "Should download from mirror URL"

        # Should NOT hit GitHub API
        github_cmds = [c for c in cmds if "github.com" in c]
        assert len(github_cmds) == 0, "Should not hit GitHub when mirror is set"

    def test_singbox_github_default(self):
        ssh = _make_ssh()

        _install_singbox(ssh, mirror="")

        cmds = [c.args[0] for c in ssh.exec.call_args_list]

        # Should hit GitHub API
        github_cmds = [c for c in cmds if "github.com" in c]
        assert len(github_cmds) > 0, "Should use GitHub API when no mirror"

    def test_singbox_installs_binary(self):
        ssh = _make_ssh()

        _install_singbox(ssh, mirror="")

        cmds = [c.args[0] for c in ssh.exec.call_args_list]
        install_cmds = [c for c in cmds if "install -m 755" in c and "sing-box" in c]
        assert len(install_cmds) == 1, "Should install sing-box binary"

    def test_singbox_creates_systemd_service(self):
        ssh = _make_ssh()

        _install_singbox(ssh, mirror="")

        cmds = [c.args[0] for c in ssh.exec.call_args_list]
        daemon_cmds = [c for c in cmds if "systemctl daemon-reload" in c]
        enable_cmds = [c for c in cmds if "systemctl enable sing-box" in c]
        assert len(daemon_cmds) == 1
        assert len(enable_cmds) == 1


class TestCreateServiceUser:
    """Test service user creation."""

    def test_user_already_exists(self):
        ssh = _make_ssh()
        # id command returns 0 -> user exists
        ssh.exec.return_value = ("uid=1000(simba)", "", 0)

        _create_service_user(ssh)

        # Should only call 'id simba', nothing else
        assert ssh.exec.call_count == 1
        assert "id simba" in ssh.exec.call_args_list[0].args[0]

    def test_user_created_fresh(self):
        ssh = _make_ssh()
        # First call (id) fails -> user doesn't exist, rest succeed
        ssh.exec.side_effect = [
            ("", "no such user", 1),  # id simba
            ("", "", 0),  # useradd
            ("", "", 0),  # usermod
            ("", "", 0),  # echo sudoers
            ("", "", 0),  # chmod sudoers
        ]

        _create_service_user(ssh)

        cmds = [c.args[0] for c in ssh.exec.call_args_list]
        assert any("useradd" in c for c in cmds)
        assert any("sudoers" in c for c in cmds)


class TestUfwPorts:
    """Test UFW port configuration by role."""

    def test_ufw_opens_near_ports(self):
        ssh = _make_ssh()
        node = _make_near_node(provider="dmit")

        _setup_ufw(ssh, node)

        cmds = [c.args[0] for c in ssh.exec.call_args_list]
        # SSH
        assert any("ufw allow 22/tcp" in c for c in cmds)
        # Reality port
        assert any("ufw allow 20000/tcp" in c for c in cmds)
        # HY2 port
        assert any("ufw allow 20001/udp" in c for c in cmds)
        # WS-CDN
        assert any("ufw allow 2053/tcp" in c for c in cmds)
        # AnyTLS
        assert any("ufw allow 8443/tcp" in c for c in cmds)

    def test_ufw_opens_wg_port(self):
        ssh = _make_ssh()
        node = _make_far_node(provider="dmit", name="us-dmit-a")

        _setup_ufw(ssh, node)

        cmds = [c.args[0] for c in ssh.exec.call_args_list]
        assert any("ufw allow 51941/udp" in c for c in cmds)


class TestWgMssClamp:
    """Test WG MSS clamp setup."""

    def test_mss_clamp_iptables_rule(self):
        ssh = _make_ssh()

        _setup_wg_mss_clamp(ssh)

        cmds = [c.args[0] for c in ssh.exec.call_args_list]
        mss_cmds = [c for c in cmds if "TCPMSS" in c]
        assert len(mss_cmds) == 1
        assert "--clamp-mss-to-pmtu" in mss_cmds[0]
        assert "-t mangle" in mss_cmds[0]

    def test_mss_clamp_persisted(self):
        ssh = _make_ssh()

        _setup_wg_mss_clamp(ssh)

        cmds = [c.args[0] for c in ssh.exec.call_args_list]
        assert any("iptables-persistent" in c for c in cmds)
        assert any("netfilter-persistent save" in c for c in cmds)
