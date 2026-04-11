"""Tests for the built-in HTTPS subscription server."""

from __future__ import annotations

import io
import socket
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from vvnext.subscription.server import (
    SubscriptionHandler,
    SubscriptionServer,
    generate_systemd_unit,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _find_free_port() -> int:
    """Find an available ephemeral port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _make_handler(
    directory: str,
    method: str,
    path: str,
    token: str = "",
) -> tuple[SubscriptionHandler, io.BytesIO]:
    """Create a SubscriptionHandler and invoke it against a fake request.

    Returns (handler, response_body_buffer).
    """
    # Build the raw HTTP request line
    request_line = f"{method} {path} HTTP/1.1\r\nHost: localhost\r\n\r\n"
    rfile = io.BytesIO(request_line.encode())
    wfile = io.BytesIO()

    # Create a handler subclass with our settings baked in
    class TestHandler(SubscriptionHandler):
        auth_token = token

        def __init__(self):
            self.directory = directory
            # Minimal attributes required by BaseHTTPRequestHandler
            self.rfile = rfile
            self.wfile = wfile
            self.client_address = ("127.0.0.1", 12345)
            self.server = MagicMock()
            self.close_connection = True
            self.requestline = f"{method} {path} HTTP/1.1"
            self.command = method
            self.path = path
            self.request_version = "HTTP/1.1"
            self.headers = {}

    handler = TestHandler()
    return handler, wfile


def _get_response_code(wfile: io.BytesIO) -> int:
    """Extract the HTTP status code from the response buffer."""
    wfile.seek(0)
    first_line = wfile.readline().decode()
    # e.g., "HTTP/1.0 200 OK\r\n"
    parts = first_line.split()
    return int(parts[1])


def _get_response_body(wfile: io.BytesIO) -> bytes:
    """Extract the body from the response buffer (after the blank line)."""
    wfile.seek(0)
    raw = wfile.read()
    # Body comes after \r\n\r\n
    idx = raw.find(b"\r\n\r\n")
    if idx == -1:
        return b""
    return raw[idx + 4 :]


# ---------------------------------------------------------------------------
# Handler tests
# ---------------------------------------------------------------------------


class TestHandler:
    def test_handler_serves_file(self, tmp_path: Path) -> None:
        """Verify file serving from directory."""
        (tmp_path / "mihomo.yaml").write_text("proxies: []")
        handler, wfile = _make_handler(str(tmp_path), "GET", "/sub/mihomo.yaml")
        handler.do_GET()
        assert _get_response_code(wfile) == 200
        assert b"proxies: []" in _get_response_body(wfile)

    def test_handler_token_auth_valid(self, tmp_path: Path) -> None:
        """Valid token -> 200."""
        (tmp_path / "mihomo.yaml").write_text("proxies: []")
        handler, wfile = _make_handler(
            str(tmp_path), "GET", "/sub/mihomo.yaml?token=secret123", token="secret123"
        )
        handler.do_GET()
        assert _get_response_code(wfile) == 200

    def test_handler_token_auth_invalid(self, tmp_path: Path) -> None:
        """Invalid token -> 403."""
        (tmp_path / "mihomo.yaml").write_text("proxies: []")
        handler, wfile = _make_handler(
            str(tmp_path), "GET", "/sub/mihomo.yaml?token=wrong", token="secret123"
        )
        handler.do_GET()
        assert _get_response_code(wfile) == 403

    def test_handler_token_auth_missing(self, tmp_path: Path) -> None:
        """Missing token when required -> 403."""
        (tmp_path / "mihomo.yaml").write_text("proxies: []")
        handler, wfile = _make_handler(
            str(tmp_path), "GET", "/sub/mihomo.yaml", token="secret123"
        )
        handler.do_GET()
        assert _get_response_code(wfile) == 403

    def test_handler_no_token_configured(self, tmp_path: Path) -> None:
        """No token set -> all served."""
        (tmp_path / "mihomo.yaml").write_text("proxies: []")
        handler, wfile = _make_handler(str(tmp_path), "GET", "/sub/mihomo.yaml", token="")
        handler.do_GET()
        assert _get_response_code(wfile) == 200

    def test_handler_path_traversal_blocked(self, tmp_path: Path) -> None:
        """../../../etc/passwd -> 404."""
        (tmp_path / "mihomo.yaml").write_text("proxies: []")
        handler, wfile = _make_handler(
            str(tmp_path), "GET", "/sub/../../../etc/passwd"
        )
        handler.do_GET()
        assert _get_response_code(wfile) == 404

    def test_handler_nonexistent_file(self, tmp_path: Path) -> None:
        """Request for a file that does not exist -> 404."""
        handler, wfile = _make_handler(str(tmp_path), "GET", "/sub/nope.yaml")
        handler.do_GET()
        assert _get_response_code(wfile) == 404

    def test_handler_head_request(self, tmp_path: Path) -> None:
        """HEAD request returns headers but no body."""
        (tmp_path / "singbox.json").write_text('{"outbounds": []}')
        handler, wfile = _make_handler(str(tmp_path), "HEAD", "/sub/singbox.json")
        handler.do_HEAD()
        assert _get_response_code(wfile) == 200
        # HEAD should have Content-Length but body may be empty
        body = _get_response_body(wfile)
        assert body == b""

    def test_handler_content_type_yaml(self, tmp_path: Path) -> None:
        """YAML files get text/yaml content type."""
        (tmp_path / "mihomo.yaml").write_text("proxies: []")
        handler, wfile = _make_handler(str(tmp_path), "GET", "/sub/mihomo.yaml")
        handler.do_GET()
        wfile.seek(0)
        raw = wfile.read().decode(errors="replace")
        assert "text/yaml" in raw

    def test_handler_content_type_json(self, tmp_path: Path) -> None:
        """JSON files get application/json content type."""
        (tmp_path / "singbox.json").write_text("{}")
        handler, wfile = _make_handler(str(tmp_path), "GET", "/sub/singbox.json")
        handler.do_GET()
        wfile.seek(0)
        raw = wfile.read().decode(errors="replace")
        assert "application/json" in raw


# ---------------------------------------------------------------------------
# Server lifecycle tests
# ---------------------------------------------------------------------------


class TestServer:
    def test_server_start_stop(self, tmp_path: Path) -> None:
        """Start/stop lifecycle — no TLS, ephemeral port."""
        (tmp_path / "test.txt").write_text("hello")
        port = _find_free_port()
        server = SubscriptionServer(
            directory=tmp_path,
            port=port,
            bind="127.0.0.1",
        )
        assert not server.is_running
        server.start()
        try:
            assert server.is_running
        finally:
            server.stop()
        assert not server.is_running

    def test_server_start_idempotent(self, tmp_path: Path) -> None:
        """Calling start() twice does not raise."""
        port = _find_free_port()
        server = SubscriptionServer(directory=tmp_path, port=port, bind="127.0.0.1")
        server.start()
        try:
            server.start()  # should be a no-op
            assert server.is_running
        finally:
            server.stop()

    def test_server_stop_when_not_started(self, tmp_path: Path) -> None:
        """Calling stop() before start() does not raise."""
        server = SubscriptionServer(directory=tmp_path, port=0, bind="127.0.0.1")
        server.stop()  # should be a no-op

    def test_server_url(self) -> None:
        """URL generation with token."""
        server = SubscriptionServer(
            directory="/tmp/sub",
            port=8443,
            tls_cert="/path/to/cert.pem",
            tls_key="/path/to/key.pem",
            token="mytoken",
        )
        url = server.url("mihomo.yaml")
        assert url == "https://0.0.0.0:8443/sub/mihomo.yaml?token=mytoken"

    def test_server_url_no_token(self) -> None:
        """URL generation without token."""
        server = SubscriptionServer(
            directory="/tmp/sub",
            port=8443,
            tls_cert="/path/to/cert.pem",
            tls_key="/path/to/key.pem",
        )
        url = server.url("mihomo.yaml")
        assert url == "https://0.0.0.0:8443/sub/mihomo.yaml"

    def test_server_url_no_tls(self) -> None:
        """URL generation without TLS uses http://."""
        server = SubscriptionServer(
            directory="/tmp/sub",
            port=9090,
        )
        url = server.url("singbox.json")
        assert url == "http://0.0.0.0:9090/sub/singbox.json"


# ---------------------------------------------------------------------------
# systemd unit tests
# ---------------------------------------------------------------------------


class TestSystemdUnit:
    def test_systemd_unit_content(self, tmp_path: Path) -> None:
        """Verify unit file format with all options."""
        settings = MagicMock()
        settings.subscription.port = 8443
        settings.subscription.tls_cert = "/etc/ssl/cert.pem"
        settings.subscription.tls_key = "/etc/ssl/key.pem"
        settings.subscription.token = "abc123"

        unit = generate_systemd_unit(settings, tmp_path)

        assert "[Unit]" in unit
        assert "[Service]" in unit
        assert "[Install]" in unit
        assert "vvnext serve-subscription" in unit
        assert f"--directory={tmp_path}" in unit
        assert "--port=8443" in unit
        assert "--tls-cert=/etc/ssl/cert.pem" in unit
        assert "--tls-key=/etc/ssl/key.pem" in unit
        assert "--token=abc123" in unit
        assert "Restart=on-failure" in unit

    def test_systemd_unit_no_tls(self, tmp_path: Path) -> None:
        """Unit file without TLS omits cert/key args."""
        settings = MagicMock()
        settings.subscription.port = 9090
        settings.subscription.tls_cert = ""
        settings.subscription.tls_key = ""
        settings.subscription.token = ""

        unit = generate_systemd_unit(settings, tmp_path)

        assert "--port=9090" in unit
        assert "--tls-cert" not in unit
        assert "--tls-key" not in unit
        assert "--token" not in unit
