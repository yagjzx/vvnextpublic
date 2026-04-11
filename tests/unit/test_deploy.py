"""Tests for deploy.py -- all SSH calls mocked."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from vvnext.deploy import (
    deploy_node,
    deploy_fleet,
    _upload_config,
    _validate_remote,
    _backup_current,
    _atomic_replace,
    _rollback,
    _upload_cdn_cert,
    _TMP_CONFIG_PATH,
    _SINGBOX_CONFIG_PATH,
    _SINGBOX_BACKUP_PATH,
    _WG_WARMUP_SECONDS,
)
from vvnext.inventory import ServerEntry, Inventory
from vvnext.settings import Settings


def _make_ssh():
    """Create a mock SshClient with all methods."""
    ssh = MagicMock()
    ssh.exec.return_value = ("", "", 0)
    ssh.upload.return_value = None
    return ssh


def _make_near_node(name="hk-gcp-a", port_base=20000):
    return ServerEntry(
        name=name,
        role="near",
        region="hk",
        provider="gcp",
        public_ip="10.0.0.1",
        port_base=port_base,
        sni="dl.google.com",
        hy2_sni="hk.test.com",
        cdn_domain="hk-cdn.test.com",
        dns_name="hk-a.test.com",
    )


def _make_far_node(name="us-gcp-a"):
    return ServerEntry(
        name=name,
        role="far",
        region="us",
        provider="gcp",
        public_ip="10.0.0.3",
        wg_port=51941,
    )


def _make_settings():
    return Settings()


def _make_config():
    return {
        "inbounds": [{"type": "vless", "listen": "0.0.0.0", "listen_port": 20000}],
        "outbounds": [{"type": "direct"}],
    }


def _make_inventory():
    return Inventory(
        servers=[
            _make_near_node("hk-gcp-a", port_base=20000),
            _make_near_node("jp-gcp-a", port_base=21000),
            _make_far_node("us-gcp-a"),
        ],
        defaults={"ssh_user": "root"},
    )


class TestDeploySuccessFlow:
    """Happy path: upload -> validate -> backup -> replace -> restart -> verify."""

    @patch("vvnext.deploy.time.sleep")
    def test_deploy_success_flow(self, mock_sleep):
        ssh = _make_ssh()
        node = _make_near_node()
        config = _make_config()
        settings = _make_settings()

        # Simulate success sequence:
        # - upload succeeds (mock)
        # - validate: sing-box check returns 0
        # - backup: test -f returns 0 (existing config), cp succeeds
        # - replace: mv succeeds
        # - restart: systemctl restart, then is-active returns "active"
        ssh.exec.side_effect = [
            # _validate_remote: sing-box check
            ("", "", 0),
            # _backup_current: test -f
            ("", "", 0),
            # _backup_current: cp
            ("", "", 0),
            # _atomic_replace: mv
            ("", "", 0),
            # _restart_singbox: systemctl restart
            ("", "", 0),
            # _restart_singbox: systemctl is-active
            ("active\n", "", 0),
        ]

        result = deploy_node(ssh, node, config, settings)

        assert result is True
        # Verify upload was called
        ssh.upload.assert_called_once()
        # Verify sleep was called for WG warm-up
        mock_sleep.assert_called_with(_WG_WARMUP_SECONDS)

    @patch("vvnext.deploy.time.sleep")
    def test_deploy_success_no_existing_config(self, mock_sleep):
        """No existing config -> backup returns False but deploy still succeeds."""
        ssh = _make_ssh()
        node = _make_near_node()
        config = _make_config()
        settings = _make_settings()

        ssh.exec.side_effect = [
            # _validate_remote
            ("", "", 0),
            # _backup_current: test -f -> no existing config
            ("", "", 1),
            # _atomic_replace: mv
            ("", "", 0),
            # _restart_singbox: systemctl restart
            ("", "", 0),
            # _restart_singbox: systemctl is-active
            ("active\n", "", 0),
        ]

        result = deploy_node(ssh, node, config, settings)
        assert result is True


class TestDeployValidationFailure:
    """sing-box check fails -> no replace, no restart."""

    @patch("vvnext.deploy.time.sleep")
    def test_deploy_validation_failure(self, mock_sleep):
        ssh = _make_ssh()
        node = _make_near_node()
        config = _make_config()
        settings = _make_settings()

        ssh.exec.side_effect = [
            # _validate_remote: sing-box check fails
            ("", "error in config", 1),
            # cleanup: rm temp file
            ("", "", 0),
        ]

        result = deploy_node(ssh, node, config, settings)

        assert result is False
        # Should NOT have called systemctl restart
        cmds = [c.args[0] for c in ssh.exec.call_args_list]
        assert not any("systemctl restart" in c for c in cmds)
        assert not any("mv" in c and "config.json" in c for c in cmds)
        # Should not have slept (no restart attempted)
        mock_sleep.assert_not_called()


class TestDeployRestartFailureRollback:
    """restart fails -> rollback from .bak."""

    @patch("vvnext.deploy.time.sleep")
    def test_deploy_restart_failure_rollback(self, mock_sleep):
        ssh = _make_ssh()
        node = _make_near_node()
        config = _make_config()
        settings = _make_settings()

        ssh.exec.side_effect = [
            # _validate_remote: sing-box check passes
            ("", "", 0),
            # _backup_current: test -f -> exists
            ("", "", 0),
            # _backup_current: cp
            ("", "", 0),
            # _atomic_replace: mv
            ("", "", 0),
            # _restart_singbox: systemctl restart
            ("", "", 0),
            # _restart_singbox: systemctl is-active -> FAILED
            ("failed\n", "", 3),
            # _rollback: test -f backup -> exists
            ("", "", 0),
            # _rollback: cp .bak -> config
            ("", "", 0),
            # _rollback: systemctl restart
            ("", "", 0),
            # _rollback: systemctl is-active -> recovered
            ("active\n", "", 0),
        ]

        result = deploy_node(ssh, node, config, settings)

        assert result is False
        # Verify rollback happened: cp .bak to config
        cmds = [c.args[0] for c in ssh.exec.call_args_list]
        rollback_cmds = [c for c in cmds if _SINGBOX_BACKUP_PATH in c and "cp" in c]
        assert len(rollback_cmds) == 2  # one for backup, one for rollback restore

    @patch("vvnext.deploy.time.sleep")
    def test_deploy_restart_failure_no_backup(self, mock_sleep):
        """Restart fails but no backup exists -- can't rollback."""
        ssh = _make_ssh()
        node = _make_near_node()
        config = _make_config()
        settings = _make_settings()

        ssh.exec.side_effect = [
            # _validate_remote
            ("", "", 0),
            # _backup_current: test -f -> no existing config
            ("", "", 1),
            # _atomic_replace: mv
            ("", "", 0),
            # _restart_singbox: systemctl restart
            ("", "", 0),
            # _restart_singbox: systemctl is-active -> FAILED
            ("failed\n", "", 3),
            # No rollback since has_backup=False
        ]

        result = deploy_node(ssh, node, config, settings)
        assert result is False


class TestDeployFleetParallelLimit:
    """Verify max_parallel=2."""

    @patch("vvnext.deploy.SshClient")
    @patch("vvnext.deploy.deploy_node")
    @patch("vvnext.deploy.ThreadPoolExecutor")
    def test_deploy_fleet_parallel_limit(
        self, mock_executor_cls, mock_deploy_node, mock_ssh_cls
    ):
        inventory = _make_inventory()
        configs = {
            "hk-gcp-a": _make_config(),
            "jp-gcp-a": _make_config(),
            "us-gcp-a": _make_config(),
        }
        settings = _make_settings()

        # Set up executor mock to track max_workers
        mock_executor = MagicMock()
        mock_executor_cls.return_value.__enter__ = MagicMock(return_value=mock_executor)
        mock_executor_cls.return_value.__exit__ = MagicMock(return_value=False)

        # Submit returns futures that resolve to (name, True)
        mock_future_a = MagicMock()
        mock_future_a.result.return_value = ("hk-gcp-a", True)
        mock_future_b = MagicMock()
        mock_future_b.result.return_value = ("jp-gcp-a", True)
        mock_future_c = MagicMock()
        mock_future_c.result.return_value = ("us-gcp-a", True)
        mock_executor.submit.side_effect = [mock_future_a, mock_future_b, mock_future_c]

        with patch("vvnext.deploy.as_completed", return_value=[mock_future_a, mock_future_b, mock_future_c]):
            deploy_fleet(inventory, configs, settings, max_parallel=2)

        # Verify ThreadPoolExecutor was created with max_workers=2
        mock_executor_cls.assert_called_once_with(max_workers=2)

    @patch("vvnext.deploy.SshClient")
    @patch("vvnext.deploy.deploy_node")
    @patch("vvnext.deploy.ThreadPoolExecutor")
    def test_deploy_fleet_targets_filter(
        self, mock_executor_cls, mock_deploy_node, mock_ssh_cls
    ):
        """Only deploy to specified targets."""
        inventory = _make_inventory()
        configs = {
            "hk-gcp-a": _make_config(),
            "jp-gcp-a": _make_config(),
            "us-gcp-a": _make_config(),
        }
        settings = _make_settings()

        mock_executor = MagicMock()
        mock_executor_cls.return_value.__enter__ = MagicMock(return_value=mock_executor)
        mock_executor_cls.return_value.__exit__ = MagicMock(return_value=False)

        mock_future = MagicMock()
        mock_future.result.return_value = ("hk-gcp-a", True)
        mock_executor.submit.return_value = mock_future

        with patch("vvnext.deploy.as_completed", return_value=[mock_future]):
            deploy_fleet(
                inventory, configs, settings, targets=["hk-gcp-a"]
            )

        # Only 1 submit call (for hk-gcp-a only)
        assert mock_executor.submit.call_count == 1


class TestDeployCdnCertUpload:
    """cdn_cert_path triggers upload."""

    @patch("vvnext.deploy.time.sleep")
    def test_deploy_cdn_cert_upload(self, mock_sleep):
        ssh = _make_ssh()
        node = _make_near_node()
        config = _make_config()
        settings = _make_settings()
        cert_path = Path("/tmp/test-origin-ca.pem")

        ssh.exec.side_effect = [
            # _upload_cdn_cert: mkdir
            ("", "", 0),
            # _upload_cdn_cert: chmod
            ("", "", 0),
            # _upload_cdn_cert: chown
            ("", "", 0),
            # _validate_remote
            ("", "", 0),
            # _backup_current: test -f
            ("", "", 0),
            # _backup_current: cp
            ("", "", 0),
            # _atomic_replace: mv
            ("", "", 0),
            # _restart_singbox: systemctl restart
            ("", "", 0),
            # _restart_singbox: systemctl is-active
            ("active\n", "", 0),
        ]

        result = deploy_node(ssh, node, config, settings, cdn_cert_path=cert_path)

        assert result is True
        # Upload called twice: once for CDN cert, once for config
        assert ssh.upload.call_count == 2
        # First upload should be the CDN cert
        first_upload = ssh.upload.call_args_list[0]
        assert first_upload.args[0] == cert_path
        assert "certs" in first_upload.args[1]

    def test_upload_cdn_cert_permissions(self):
        ssh = _make_ssh()
        cert_path = Path("/tmp/origin-ca.pem")

        _upload_cdn_cert(ssh, cert_path)

        cmds = [c.args[0] for c in ssh.exec.call_args_list]
        assert any("chmod 644" in c for c in cmds)
        assert any("chown simba:simba" in c for c in cmds)


class TestUploadConfig:
    """Test config serialization and upload."""

    def test_upload_config_calls_upload(self):
        ssh = _make_ssh()
        config = _make_config()

        _upload_config(ssh, config)

        ssh.upload.assert_called_once()
        # Remote path should be /tmp/config.json
        assert ssh.upload.call_args.args[1] == _TMP_CONFIG_PATH


class TestValidateRemote:
    """Test remote validation."""

    def test_validate_remote_success(self):
        ssh = _make_ssh()
        ssh.exec.return_value = ("", "", 0)

        assert _validate_remote(ssh) is True

    def test_validate_remote_failure(self):
        ssh = _make_ssh()
        ssh.exec.return_value = ("", "config error", 1)

        assert _validate_remote(ssh) is False


class TestBackupCurrent:
    """Test backup logic."""

    def test_backup_existing_config(self):
        ssh = _make_ssh()
        ssh.exec.side_effect = [
            ("", "", 0),  # test -f -> exists
            ("", "", 0),  # cp
        ]

        result = _backup_current(ssh)
        assert result is True

    def test_backup_no_existing_config(self):
        ssh = _make_ssh()
        ssh.exec.return_value = ("", "", 1)  # test -f -> not found

        result = _backup_current(ssh)
        assert result is False


class TestRollback:
    """Test rollback logic."""

    @patch("vvnext.deploy.time.sleep")
    def test_rollback_success(self, mock_sleep):
        ssh = _make_ssh()
        ssh.exec.side_effect = [
            ("", "", 0),  # test -f backup exists
            ("", "", 0),  # cp backup -> config
            ("", "", 0),  # systemctl restart
            ("active\n", "", 0),  # systemctl is-active
        ]

        result = _rollback(ssh)
        assert result is True

    @patch("vvnext.deploy.time.sleep")
    def test_rollback_no_backup(self, mock_sleep):
        ssh = _make_ssh()
        ssh.exec.return_value = ("", "", 1)  # test -f -> no backup

        result = _rollback(ssh)
        assert result is False


class TestAtomicReplace:
    """Test atomic replace."""

    def test_atomic_replace_mv(self):
        ssh = _make_ssh()

        _atomic_replace(ssh)

        cmds = [c.args[0] for c in ssh.exec.call_args_list]
        assert len(cmds) == 1
        assert "mv" in cmds[0]
        assert _TMP_CONFIG_PATH in cmds[0]
        assert _SINGBOX_CONFIG_PATH in cmds[0]
