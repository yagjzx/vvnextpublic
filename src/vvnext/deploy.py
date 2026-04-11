"""Deploy sing-box configs to nodes with atomic replacement and auto-rollback."""

from __future__ import annotations

import json
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

from vvnext.ssh import SshClient
from vvnext.inventory import ServerEntry, Inventory
from vvnext.settings import Settings


_SINGBOX_CONFIG_PATH = "/etc/sing-box/config.json"
_SINGBOX_BACKUP_PATH = "/etc/sing-box/config.json.bak"
_SINGBOX_CERT_DIR = "/etc/sing-box/certs"
_TMP_CONFIG_PATH = "/tmp/config.json"
_WG_WARMUP_SECONDS = 5


def deploy_node(
    ssh: SshClient,
    node: ServerEntry,
    config: dict,
    settings: Settings,
    cdn_cert_path: Optional[Path] = None,
) -> bool:
    """Deploy sing-box config to a single node with atomic replace + auto-rollback.

    Steps:
    1. Upload CDN cert if provided
    2. Write config to local temp file, upload to /tmp/config.json on remote
    3. Remote validation: sing-box check -c /tmp/config.json (AUTHORITATIVE)
    4. Backup: cp /etc/sing-box/config.json /etc/sing-box/config.json.bak
    5. Atomic replace: mv /tmp/config.json /etc/sing-box/config.json
    6. Restart: systemctl restart sing-box
    7. Wait 5 seconds (WG tunnel warm-up)
    8. Verify: systemctl is-active sing-box
    9. On failure: auto-rollback from .bak

    Returns True on success, False on rollback.
    """
    # Step 1: Upload CDN cert if provided (before config, since config may reference it)
    if cdn_cert_path is not None:
        _upload_cdn_cert(ssh, cdn_cert_path)

    # Step 2: Upload config
    _upload_config(ssh, config)

    # Step 3: Remote validation (authoritative)
    if not _validate_remote(ssh):
        # Clean up temp file
        ssh.exec(f"rm -f {_TMP_CONFIG_PATH}", check=False)
        return False

    # Step 4: Backup current config
    has_backup = _backup_current(ssh)

    # Step 5: Atomic replace
    _atomic_replace(ssh)

    # Step 6-7: Restart and wait for WG warm-up
    if not _restart_singbox(ssh):
        # Step 9: Auto-rollback on failure
        if has_backup:
            _rollback(ssh)
        return False

    return True


def _upload_config(ssh: SshClient, config: dict) -> None:
    """Serialize config to JSON and upload to /tmp/config.json."""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False
    ) as f:
        json.dump(config, f, indent=2)
        tmp_path = Path(f.name)

    try:
        ssh.upload(tmp_path, _TMP_CONFIG_PATH)
    finally:
        tmp_path.unlink(missing_ok=True)


def _validate_remote(ssh: SshClient) -> bool:
    """Run sing-box check on remote. Remote validation is authoritative --
    local check may fail due to missing remote-only certs."""
    _, _, rc = ssh.exec(
        f"sing-box check -c {_TMP_CONFIG_PATH}", check=False
    )
    return rc == 0


def _backup_current(ssh: SshClient) -> bool:
    """Backup current config. Returns False if no existing config."""
    _, _, rc = ssh.exec(f"test -f {_SINGBOX_CONFIG_PATH}", check=False)
    if rc != 0:
        return False  # No existing config to backup
    ssh.exec(f"cp {_SINGBOX_CONFIG_PATH} {_SINGBOX_BACKUP_PATH}")
    return True


def _atomic_replace(ssh: SshClient) -> None:
    """mv /tmp/config.json /etc/sing-box/config.json"""
    ssh.exec(f"mv {_TMP_CONFIG_PATH} {_SINGBOX_CONFIG_PATH}")


def _restart_singbox(ssh: SshClient) -> bool:
    """Restart sing-box and wait for WG warm-up. Returns True if active."""
    ssh.exec("systemctl restart sing-box", check=False)

    # Wait for WG tunnel warm-up
    time.sleep(_WG_WARMUP_SECONDS)

    # Verify service is active
    out, _, rc = ssh.exec("systemctl is-active sing-box", check=False)
    return out.strip() == "active"


def _rollback(ssh: SshClient) -> bool:
    """Restore config from .bak and restart. Returns True if recovery successful."""
    _, _, rc = ssh.exec(f"test -f {_SINGBOX_BACKUP_PATH}", check=False)
    if rc != 0:
        return False  # No backup to restore from

    ssh.exec(f"cp {_SINGBOX_BACKUP_PATH} {_SINGBOX_CONFIG_PATH}")
    ssh.exec("systemctl restart sing-box", check=False)

    time.sleep(_WG_WARMUP_SECONDS)

    out, _, _ = ssh.exec("systemctl is-active sing-box", check=False)
    return out.strip() == "active"


def _upload_cdn_cert(ssh: SshClient, cert_path: Path) -> None:
    """Upload CDN Origin CA certificate to /etc/sing-box/certs/."""
    ssh.exec(f"mkdir -p {_SINGBOX_CERT_DIR}")
    remote_path = f"{_SINGBOX_CERT_DIR}/{cert_path.name}"
    ssh.upload(cert_path, remote_path)
    ssh.exec(f"chmod 644 {remote_path}")
    ssh.exec(f"chown simba:simba {remote_path}")


def deploy_fleet(
    inventory: Inventory,
    configs: dict[str, dict],
    settings: Settings,
    targets: list[str] | None = None,
    max_parallel: int = 2,
) -> dict[str, bool]:
    """Deploy to multiple nodes (max 2 parallel).

    Args:
        configs: {node_name: config_dict}
        targets: specific node names, or None for all
        max_parallel: max concurrent deploys (default 2, safety limit)

    Returns: {node_name: success_bool}
    """
    # Determine which nodes to deploy
    if targets is not None:
        node_names = targets
    else:
        node_names = list(configs.keys())

    results: dict[str, bool] = {}

    def _deploy_one(name: str) -> tuple[str, bool]:
        node = inventory.get_node(name)
        config = configs[name]

        # Determine SSH target
        host = node.tailscale_ip if node.tailscale_ip else node.public_ip
        ssh = SshClient(
            host=host,
            user=settings.ssh.user,
            key_path=settings.ssh.key_path,
            timeout=settings.ssh.timeout,
        )
        try:
            ssh.connect()
            success = deploy_node(ssh, node, config, settings)
            return name, success
        except Exception:
            return name, False
        finally:
            ssh.close()

    with ThreadPoolExecutor(max_workers=max_parallel) as executor:
        futures = {
            executor.submit(_deploy_one, name): name
            for name in node_names
        }
        for future in as_completed(futures):
            name, success = future.result()
            results[name] = success

    return results
