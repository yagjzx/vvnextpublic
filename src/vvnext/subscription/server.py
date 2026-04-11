"""Built-in HTTPS subscription server.

Serves subscription files from a directory over HTTPS.
Uses Python's http.server + ssl module — no Caddy or nginx needed.
Supports optional token-based URL auth to prevent unauthorized crawling.
"""

from __future__ import annotations

import http.server
import ssl
import threading
from pathlib import Path
from urllib.parse import urlparse, parse_qs


class SubscriptionHandler(http.server.SimpleHTTPRequestHandler):
    """HTTP handler for subscription files with optional token auth.

    URL format: https://host:port/sub/mihomo.yaml?token=xxx

    If token is configured:
      - Requests without valid token get 403
      - Token is checked from query parameter 'token'
    If no token configured:
      - All requests are served (no auth)

    Only serves files from the subscription output directory.
    Returns 404 for paths outside the allowed directory.
    """

    auth_token: str = ""  # class variable, set by server

    def _check_auth(self) -> bool:
        """Verify token auth if configured. Returns True if authorized."""
        if not self.auth_token:
            return True
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        tokens = qs.get("token", [])
        return len(tokens) == 1 and tokens[0] == self.auth_token

    def _resolve_path(self) -> Path | None:
        """Resolve the request path to a safe file path.

        Returns None if the path escapes the serving directory.
        """
        parsed = urlparse(self.path)
        # Strip the /sub/ prefix if present
        url_path = parsed.path
        if url_path.startswith("/sub/"):
            url_path = url_path[5:]  # remove "/sub/"
        elif url_path.startswith("/sub"):
            url_path = url_path[4:]  # remove "/sub"
        # Remove leading slash
        url_path = url_path.lstrip("/")

        if not url_path:
            return None

        # Resolve and check for path traversal
        try:
            serving_dir = Path(self.directory).resolve()
            target = (serving_dir / url_path).resolve()
            # Ensure the resolved path is within the serving directory
            if not str(target).startswith(str(serving_dir)):
                return None
            if not target.is_file():
                return None
            return target
        except (ValueError, OSError):
            return None

    def do_GET(self) -> None:
        """Handle GET with token auth check."""
        if not self._check_auth():
            self.send_error(403, "Forbidden")
            return

        target = self._resolve_path()
        if target is None:
            self.send_error(404, "Not Found")
            return

        try:
            content = target.read_bytes()
        except OSError:
            self.send_error(500, "Internal Server Error")
            return

        # Determine content type
        suffix = target.suffix.lower()
        content_type = {
            ".yaml": "text/yaml; charset=utf-8",
            ".yml": "text/yaml; charset=utf-8",
            ".json": "application/json; charset=utf-8",
            ".txt": "text/plain; charset=utf-8",
        }.get(suffix, "application/octet-stream")

        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def do_HEAD(self) -> None:
        """Handle HEAD with token auth check."""
        if not self._check_auth():
            self.send_error(403, "Forbidden")
            return

        target = self._resolve_path()
        if target is None:
            self.send_error(404, "Not Found")
            return

        suffix = target.suffix.lower()
        content_type = {
            ".yaml": "text/yaml; charset=utf-8",
            ".yml": "text/yaml; charset=utf-8",
            ".json": "application/json; charset=utf-8",
            ".txt": "text/plain; charset=utf-8",
        }.get(suffix, "application/octet-stream")

        try:
            size = target.stat().st_size
        except OSError:
            self.send_error(500, "Internal Server Error")
            return

        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(size))
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:
        """Suppress default stderr logging."""
        pass


class SubscriptionServer:
    """HTTPS subscription server manager.

    Usage:
        server = SubscriptionServer(
            directory="/path/to/rendered/subscription",
            port=8443,
            tls_cert="/path/to/cert.pem",
            tls_key="/path/to/key.pem",
            token="optional-auth-token",
        )
        server.start()   # starts in background thread
        server.stop()
    """

    def __init__(
        self,
        directory: str | Path,
        port: int = 8443,
        tls_cert: str = "",
        tls_key: str = "",
        token: str = "",
        bind: str = "0.0.0.0",
    ):
        self.directory = Path(directory)
        self.port = port
        self.tls_cert = tls_cert
        self.tls_key = tls_key
        self.token = token
        self.bind = bind
        self._server: http.server.HTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Start the server in a background daemon thread."""
        if self._server is not None:
            return  # already running

        serving_dir = str(self.directory)
        token = self.token

        # Create a handler class with the directory and token baked in
        class Handler(SubscriptionHandler):
            auth_token = token

            def __init__(self, *args, **kwargs):
                kwargs["directory"] = serving_dir
                super().__init__(*args, **kwargs)

        self._server = http.server.HTTPServer((self.bind, self.port), Handler)

        # Wrap with TLS if cert/key provided
        if self.tls_cert and self.tls_key:
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(self.tls_cert, self.tls_key)
            self._server.socket = ctx.wrap_socket(
                self._server.socket, server_side=True
            )

        self._thread = threading.Thread(
            target=self._server.serve_forever,
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        """Stop the server."""
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    @property
    def is_running(self) -> bool:
        """Check if the server is currently running."""
        return self._server is not None and self._thread is not None and self._thread.is_alive()

    def url(self, filename: str) -> str:
        """Get the full URL for a subscription file.

        Returns an HTTPS URL if TLS is configured, HTTP otherwise.
        Includes token in query string if configured.
        """
        scheme = "https" if (self.tls_cert and self.tls_key) else "http"
        base = f"{scheme}://{self.bind}:{self.port}/sub/{filename}"
        if self.token:
            base += f"?token={self.token}"
        return base


def generate_systemd_unit(settings: object, directory: Path) -> str:
    """Generate systemd unit file content for running the subscription server as a daemon.

    Args:
        settings: A Settings object (or anything with a .subscription attribute
                  containing port, tls_cert, tls_key, token fields).
        directory: Path to the subscription output directory to serve.

    Returns the unit file content as a string (user can write to /etc/systemd/system/).
    """
    sub = settings.subscription  # type: ignore[attr-defined]

    args = [
        f"--directory={directory}",
        f"--port={sub.port}",
    ]
    if sub.tls_cert:
        args.append(f"--tls-cert={sub.tls_cert}")
    if sub.tls_key:
        args.append(f"--tls-key={sub.tls_key}")
    if sub.token:
        args.append(f"--token={sub.token}")

    args_str = " ".join(args)

    return f"""\
[Unit]
Description=VVNext Subscription Server
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=/usr/bin/env vvnext serve-subscription {args_str}
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
"""
