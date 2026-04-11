from __future__ import annotations
import paramiko
from pathlib import Path
from typing import Optional


class SshClient:
    """Thin wrapper around Paramiko for fleet operations."""

    def __init__(self, host: str, user: str = "root",
                 key_path: Optional[str] = None, password: Optional[str] = None,
                 timeout: int = 30):
        self.host = host
        self.user = user
        self.key_path = key_path
        self.password = password
        self.timeout = timeout
        self._client: Optional[paramiko.SSHClient] = None

    def connect(self) -> None:
        self._client = paramiko.SSHClient()
        self._client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        kwargs = {"hostname": self.host, "username": self.user, "timeout": self.timeout}
        if self.password:
            kwargs["password"] = self.password
            kwargs["look_for_keys"] = False  # prevent MaxAuthTries exhaustion
        elif self.key_path:
            kwargs["key_filename"] = str(Path(self.key_path).expanduser())
        self._client.connect(**kwargs)

    def exec(self, cmd: str, check: bool = True) -> tuple[str, str, int]:
        if not self._client:
            self.connect()
        _, stdout, stderr = self._client.exec_command(cmd, timeout=self.timeout)
        out = stdout.read().decode()
        err = stderr.read().decode()
        rc = stdout.channel.recv_exit_status()
        if check and rc != 0:
            raise RuntimeError(f"SSH command failed (rc={rc}): {cmd}\n{err}")
        return out, err, rc

    def upload(self, local_path: Path, remote_path: str) -> None:
        if not self._client:
            self.connect()
        sftp = self._client.open_sftp()
        sftp.put(str(local_path), remote_path)
        sftp.close()

    def download(self, remote_path: str, local_path: Path) -> None:
        if not self._client:
            self.connect()
        sftp = self._client.open_sftp()
        sftp.get(remote_path, str(local_path))
        sftp.close()

    def close(self) -> None:
        if self._client:
            self._client.close()
            self._client = None

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *args):
        self.close()
