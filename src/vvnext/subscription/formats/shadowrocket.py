"""Shadowrocket base64-encoded subscription builder.

Each node becomes a URI line:
- vless:// for VLESS Reality and WS-CDN
- hysteria2:// for HY2
- anytls:// for AnyTLS (if supported by client)

All lines are joined and base64-encoded.
"""
from __future__ import annotations

import base64
from urllib.parse import quote, urlencode


def build_shadowrocket_subscription(client_nodes: list[dict]) -> str:
    """Build Shadowrocket base64-encoded subscription.

    Returns a base64-encoded string of all node URIs, one per line.
    """
    lines: list[str] = []
    for node in client_nodes:
        uri = _node_to_uri(node)
        if uri:
            lines.append(uri)
    raw = "\n".join(lines)
    return base64.b64encode(raw.encode()).decode()


# ---------------------------------------------------------------------------
# Internal: node to URI conversion
# ---------------------------------------------------------------------------

def _node_to_uri(node: dict) -> str:
    """Convert a single Clash-format proxy node to a share URI."""
    node_type = node.get("type", "")
    name = node.get("name", "")

    if node_type == "vless":
        return _vless_uri(node, name)
    elif node_type == "hysteria2":
        return _hy2_uri(node, name)
    elif node_type == "anytls":
        return _anytls_uri(node, name)
    return ""


def _vless_uri(node: dict, name: str) -> str:
    """Build vless:// URI for Reality or WS-CDN nodes."""
    uuid = node.get("uuid", "")
    server = node.get("server", "")
    port = node.get("port", 0)
    fragment = quote(name, safe="")

    # WS-CDN mode: no TLS, ws transport
    network = node.get("network", "")
    if network == "ws":
        ws_opts = node.get("ws-opts", {})
        path = ws_opts.get("path", "/ws")
        params = {
            "security": "none",
            "type": "ws",
            "path": path,
        }
        return f"vless://{uuid}@{server}:{port}?{urlencode(params)}#{fragment}"

    # Reality mode
    reality_opts = node.get("reality-opts", {})
    params = {
        "encryption": "none",
        "flow": node.get("flow", "xtls-rprx-vision"),
        "security": "reality",
        "sni": node.get("servername", ""),
        "fp": node.get("client-fingerprint", "random"),
        "pbk": reality_opts.get("public-key", ""),
        "sid": reality_opts.get("short-id", ""),
        "type": "tcp",
    }
    return f"vless://{uuid}@{server}:{port}?{urlencode(params)}#{fragment}"


def _hy2_uri(node: dict, name: str) -> str:
    """Build hysteria2:// URI."""
    password = node.get("password", "")
    server = node.get("server", "")
    port = node.get("port", 0)
    fragment = quote(name, safe="")

    params: dict[str, str] = {}
    obfs = node.get("obfs", "")
    if obfs:
        params["obfs"] = obfs
        params["obfs-password"] = node.get("obfs-password", "")
    sni = node.get("sni", "")
    if sni:
        params["sni"] = sni

    query = urlencode(params) if params else ""
    base = f"hysteria2://{password}@{server}:{port}"
    if query:
        base += f"?{query}"
    return f"{base}#{fragment}"


def _anytls_uri(node: dict, name: str) -> str:
    """Build anytls:// URI."""
    password = node.get("password", "")
    server = node.get("server", "")
    port = node.get("port", 0)
    fragment = quote(name, safe="")
    return f"anytls://{password}@{server}:{port}#{fragment}"
