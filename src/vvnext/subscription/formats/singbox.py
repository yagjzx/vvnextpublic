"""sing-box client JSON config builder (Hiddify format).

Converts Clash-format nodes to sing-box outbound format and builds
a complete sing-box client config with routing rules.
"""
from __future__ import annotations


def build_singbox_subscription(
    client_nodes: list[dict],
    proxy_groups: list[dict],
) -> dict:
    """Build sing-box client JSON config (Hiddify format).

    Converts Clash-format nodes to sing-box outbound format.
    Uses rule_set references from SagerNet/sing-geosite.
    """
    outbounds: list[dict] = []
    outbound_tags: list[str] = []

    # Convert each client node to a sing-box outbound
    for node in client_nodes:
        ob = _clash_node_to_singbox_outbound(node)
        if ob:
            outbounds.append(ob)
            outbound_tags.append(ob["tag"])

    # Build selector/urltest groups from proxy_groups
    group_outbounds = _build_group_outbounds(proxy_groups, outbound_tags)

    # Standard outbounds
    standard = [
        {"type": "direct", "tag": "direct"},
        {"type": "block", "tag": "block"},
        {"type": "dns", "tag": "dns-out"},
    ]

    all_outbounds = group_outbounds + outbounds + standard

    return {
        "log": {"level": "warn", "timestamp": True},
        "dns": {
            "servers": [
                {"tag": "google", "address": "https://dns.google/dns-query"},
                {"tag": "local", "address": "local"},
            ],
        },
        "inbounds": [
            {
                "type": "mixed",
                "tag": "mixed-in",
                "listen": "127.0.0.1",
                "listen_port": 7890,
            },
        ],
        "outbounds": all_outbounds,
        "route": {
            "rules": [
                {"action": "sniff"},
            ],
            "final": "Auto-Select",
        },
    }


def _clash_node_to_singbox_outbound(node: dict) -> dict | None:
    """Convert a single Clash-format proxy node to sing-box outbound.

    Supported: vless (reality + ws), hysteria2, anytls.
    """
    node_type = node.get("type", "")
    name = node.get("name", "")

    if node_type == "vless":
        return _vless_outbound(node, name)
    elif node_type == "hysteria2":
        return _hy2_outbound(node, name)
    elif node_type == "anytls":
        return _anytls_outbound(node, name)
    return None


# ---------------------------------------------------------------------------
# Internal: outbound converters
# ---------------------------------------------------------------------------

def _vless_outbound(node: dict, tag: str) -> dict:
    """Build sing-box VLESS outbound (Reality or WS)."""
    ob: dict = {
        "type": "vless",
        "tag": tag,
        "server": node.get("server", ""),
        "server_port": node.get("port", 0),
        "uuid": node.get("uuid", ""),
    }

    # WS transport (CDN mode)
    network = node.get("network", "")
    if network == "ws":
        ws_opts = node.get("ws-opts", {})
        ob["transport"] = {
            "type": "ws",
            "path": ws_opts.get("path", "/ws"),
        }
        return ob

    # Reality mode
    ob["flow"] = node.get("flow", "xtls-rprx-vision")
    reality_opts = node.get("reality-opts", {})
    ob["tls"] = {
        "enabled": True,
        "server_name": node.get("servername", ""),
        "reality": {
            "enabled": True,
            "public_key": reality_opts.get("public-key", ""),
            "short_id": reality_opts.get("short-id", ""),
        },
    }
    return ob


def _hy2_outbound(node: dict, tag: str) -> dict:
    """Build sing-box Hysteria2 outbound."""
    ob: dict = {
        "type": "hysteria2",
        "tag": tag,
        "server": node.get("server", ""),
        "server_port": node.get("port", 0),
        "password": node.get("password", ""),
    }

    obfs = node.get("obfs", "")
    if obfs:
        ob["obfs"] = {
            "type": obfs,
            "password": node.get("obfs-password", ""),
        }

    sni = node.get("sni", "")
    if sni:
        ob["tls"] = {
            "enabled": True,
            "server_name": sni,
        }

    return ob


def _anytls_outbound(node: dict, tag: str) -> dict:
    """Build sing-box AnyTLS outbound."""
    return {
        "type": "anytls",
        "tag": tag,
        "server": node.get("server", ""),
        "server_port": node.get("port", 0),
        "password": node.get("password", ""),
    }


# ---------------------------------------------------------------------------
# Internal: group outbounds
# ---------------------------------------------------------------------------

def _build_group_outbounds(
    proxy_groups: list[dict],
    outbound_tags: list[str],
) -> list[dict]:
    """Convert Clash proxy groups to sing-box selector/urltest outbounds."""
    groups: list[dict] = []
    tag_set = set(outbound_tags)

    for pg in proxy_groups:
        name = pg["name"]
        pg_type = pg.get("type", "select")
        proxies = pg.get("proxies", [])

        # Filter to only include tags that exist as outbounds or group names
        # Keep special values like DIRECT
        valid_proxies: list[str] = []
        all_group_names = {g["name"] for g in proxy_groups}
        for p in proxies:
            if p in tag_set or p in all_group_names or p == "DIRECT":
                valid_proxies.append(p)

        # Map DIRECT -> direct for sing-box
        mapped = ["direct" if p == "DIRECT" else p for p in valid_proxies]

        if pg_type == "fallback":
            groups.append({
                "type": "urltest",
                "tag": name,
                "outbounds": mapped or ["direct"],
                "url": pg.get("url", "http://www.gstatic.com/generate_204"),
                "interval": f"{pg.get('interval', 300)}s",
            })
        else:
            groups.append({
                "type": "selector",
                "tag": name,
                "outbounds": mapped or ["direct"],
            })

    return groups
