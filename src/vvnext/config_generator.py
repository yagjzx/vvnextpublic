"""sing-box JSON config generator for near and far nodes.

Produces complete sing-box server configs, deployment manifests,
and Clash-format client proxy entries.
"""
from __future__ import annotations

from vvnext.inventory import Inventory, ServerEntry, Defaults, node_short_label


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_near_config(
    node: ServerEntry,
    inventory: Inventory,
    topo: dict[tuple[str, str], dict],
    materials: dict,
    defaults: Defaults,
) -> dict:
    """Build complete sing-box JSON config for a near node."""
    inbounds: list[dict] = []
    outbounds: list[dict] = []
    route_rules: list[dict] = [{"action": "sniff"}]
    endpoints: list[dict] = []

    # Collect WG peers for this near node
    peers = _get_ordered_peers(node, inventory, topo)

    # --- Overlay inbounds (port_base+1, +3, +4, +5, ...) ---
    overlay_inbound_tags: list[str] = []
    for i, (far_name, alloc) in enumerate(peers):
        port_offset = _overlay_port_offset(i)
        port = node.port_base + port_offset
        tag = f"vless-reality-overlay-{far_name}"
        inbounds.append(
            _build_reality_overlay_inbound(node, port, tag, materials)
        )
        overlay_inbound_tags.append(tag)

        # WG endpoint for this peer (sing-box 1.13+)
        far_node = inventory.get_node(far_name)
        endpoints.append(
            _build_wg_endpoint(node.name, far_name, alloc, materials, far_node)
        )
        # Route: overlay inbound -> specific WG outbound
        route_rules.append({
            "inbound": [tag],
            "outbound": f"wg-{far_name}",
        })

    # --- Reality direct inbound (port_base+2) ---
    inbounds.append(_build_reality_direct_inbound(node, materials))

    # --- HY2 inbound (port 443) ---
    inbounds.append(_build_hy2_inbound(node, materials))

    # --- VLESS WS CDN inbound ---
    inbounds.append(_build_ws_cdn_inbound(node, materials, defaults))

    # --- AnyTLS inbound ---
    inbounds.append(_build_anytls_inbound(node, materials, defaults))

    # --- AnyTLS direct inbound ---
    inbounds.append(_build_anytls_direct_inbound(node, materials))

    # --- Direct inbounds route ---
    direct_inbound_tags = [
        "vless-reality-direct-in",
        "hy2-in",
        "vless-ws-cdn-in",
        "anytls-in",
        "anytls-direct-in",
    ]
    route_rules.append({
        "inbound": direct_inbound_tags,
        "outbound": "direct",
    })

    # --- Standard outbounds ---
    outbounds.append(_build_direct_outbound())
    outbounds.append(_build_block_outbound())

    config: dict = {
        "log": {"level": "warn", "timestamp": True},
        "dns": _build_dns(),
        "inbounds": inbounds,
        "outbounds": outbounds,
        "route": {
            "rules": route_rules,
            "final": "direct",
        },
    }
    if endpoints:
        config["endpoints"] = endpoints
    return config


def build_far_config(
    node: ServerEntry,
    inventory: Inventory,
    topo: dict[tuple[str, str], dict],
    materials: dict,
    defaults: Defaults,
) -> dict:
    """Build complete sing-box JSON config for a far node."""
    outbounds: list[dict] = []

    # Collect all near nodes that peer with this far node
    near_peers: list[tuple[str, dict]] = []
    for (near_name, far_name), alloc in sorted(topo.items()):
        if far_name == node.name:
            near_peers.append((near_name, alloc))

    endpoints: list[dict] = []
    if near_peers:
        endpoints.append(
            _build_far_wg_endpoint(node, near_peers, materials, inventory)
        )

    outbounds.append(_build_direct_outbound())
    outbounds.append(_build_block_outbound())

    config: dict = {
        "log": {"level": "warn", "timestamp": True},
        "dns": _build_dns(),
        "outbounds": outbounds,
        "route": {
            "rules": [{"action": "sniff"}],
            "final": "direct",
        },
    }
    if endpoints:
        config["endpoints"] = endpoints
    return config


def build_manifest(
    node: ServerEntry,
    inventory: Inventory,
    topo: dict[tuple[str, str], dict],
    materials: dict,
    defaults: Defaults,
) -> dict:
    """Build deployment manifest with node metadata and route profiles."""
    nodes: list[dict] = []
    peers = _get_ordered_peers(node, inventory, topo)

    # Overlay entries
    for i, (far_name, alloc) in enumerate(peers):
        port_offset = _overlay_port_offset(i)
        port = node.port_base + port_offset
        far_node = inventory.get_node(far_name)
        near_label = node_short_label(node.name)
        far_label = node_short_label(far_name)
        nodes.append({
            "link_id": f"{node.name}-reality-overlay-{far_name}",
            "overlay_node_name": f"{near_label} -> {far_label}",
            "route_profile": _route_profile(far_node),
            "listen_port": port,
            "transport": "tcp",
            "egress_mode": "overlay",
            "protocol": "vless-reality",
        })

    # Direct Reality entry
    nodes.append({
        "link_id": f"{node.name}-reality-direct",
        "overlay_node_name": f"{node_short_label(node.name)} Direct",
        "route_profile": "near-direct",
        "listen_port": node.port_base + 2,
        "transport": "tcp",
        "egress_mode": "direct",
        "protocol": "vless-reality",
    })

    # HY2 entry
    nodes.append({
        "link_id": f"{node.name}-hy2-direct",
        "overlay_node_name": f"{node_short_label(node.name)} HY2",
        "route_profile": "near-direct",
        "listen_port": 443,
        "transport": "udp",
        "egress_mode": "direct",
        "protocol": "hysteria2",
    })

    # CDN entry
    nodes.append({
        "link_id": f"{node.name}-cdn-direct",
        "overlay_node_name": f"{node_short_label(node.name)} CDN",
        "route_profile": "near-direct",
        "listen_port": defaults.near.cdn_port,
        "transport": "ws",
        "egress_mode": "direct",
        "protocol": "vless-ws-cdn",
    })

    # AnyTLS entry
    nodes.append({
        "link_id": f"{node.name}-anytls-direct",
        "overlay_node_name": f"{node_short_label(node.name)} AnyTLS",
        "route_profile": "near-direct",
        "listen_port": defaults.near.anytls_port,
        "transport": "tcp",
        "egress_mode": "direct",
        "protocol": "anytls",
    })

    return {
        "near_node": node.name,
        "nodes": nodes,
    }


def build_client_nodes(
    node: ServerEntry,
    inventory: Inventory,
    topo: dict[tuple[str, str], dict],
    materials: dict,
    defaults: Defaults,
) -> list[dict]:
    """Build Clash-format proxy entries for client configs."""
    entries: list[dict] = []
    peers = _get_ordered_peers(node, inventory, topo)
    vless_uuid = materials.get("vless_uuid", "")
    reality = materials.get("reality", {}).get(node.name, {})
    hy2 = materials.get("hy2", {})
    anytls_password = materials.get("anytls_password", "")

    # Overlay Reality entries
    for i, (far_name, alloc) in enumerate(peers):
        port_offset = _overlay_port_offset(i)
        port = node.port_base + port_offset
        near_label = node_short_label(node.name)
        far_label = node_short_label(far_name)
        entries.append({
            "name": f"{near_label} -> {far_label} | Reality",
            "type": "vless",
            "server": node.dns_name,
            "port": port,
            "uuid": vless_uuid,
            "flow": "xtls-rprx-vision",
            "tls": True,
            "client-fingerprint": "random",
            "servername": node.sni,
            "reality-opts": {
                "public-key": reality.get("public_key", ""),
                "short-id": reality.get("short_id", ""),
            },
        })

    # Direct Reality entry
    entries.append({
        "name": f"{node_short_label(node.name)} Direct | Reality",
        "type": "vless",
        "server": node.dns_name,
        "port": node.port_base + 2,
        "uuid": vless_uuid,
        "flow": "xtls-rprx-vision",
        "tls": True,
        "client-fingerprint": "random",
        "servername": node.sni,
        "reality-opts": {
            "public-key": reality.get("public_key", ""),
            "short-id": reality.get("short_id", ""),
        },
    })

    # HY2 entry
    entries.append({
        "name": f"{node_short_label(node.name)} | HY2",
        "type": "hysteria2",
        "server": node.dns_name,
        "port": 443,
        "password": hy2.get("password", ""),
        "obfs": "salamander",
        "obfs-password": hy2.get("obfs_password", ""),
        "sni": node.hy2_sni,
    })

    # CDN WS entry
    entries.append({
        "name": f"{node_short_label(node.name)} | CDN",
        "type": "vless",
        "server": node.cdn_domain,
        "port": defaults.near.cdn_port,
        "uuid": vless_uuid,
        "tls": False,
        "network": "ws",
        "ws-opts": {"path": "/ws"},
    })

    # AnyTLS entry
    entries.append({
        "name": f"{node_short_label(node.name)} | AnyTLS",
        "type": "anytls",
        "server": node.dns_name,
        "port": defaults.near.anytls_port,
        "password": anytls_password,
    })

    return entries


# ---------------------------------------------------------------------------
# Internal: inbound builders
# ---------------------------------------------------------------------------

def _build_reality_overlay_inbound(
    node: ServerEntry, port: int, tag: str, materials: dict
) -> dict:
    """VLESS Reality overlay inbound on a specific port."""
    reality = materials.get("reality", {}).get(node.name, {})
    return {
        "type": "vless",
        "tag": tag,
        "listen": "::",
        "listen_port": port,
        "users": [
            {"uuid": materials.get("vless_uuid", ""), "flow": "xtls-rprx-vision"}
        ],
        "tls": {
            "enabled": True,
            "server_name": node.sni,
            "reality": {
                "enabled": True,
                "handshake": {"server": node.sni, "server_port": 443},
                "private_key": reality.get("private_key", ""),
                "short_id": [reality.get("short_id", "")],
            },
        },
    }


def _build_reality_direct_inbound(node: ServerEntry, materials: dict) -> dict:
    """VLESS Reality direct inbound on port_base+2."""
    reality = materials.get("reality", {}).get(node.name, {})
    return {
        "type": "vless",
        "tag": "vless-reality-direct-in",
        "listen": "::",
        "listen_port": node.port_base + 2,
        "users": [
            {"uuid": materials.get("vless_uuid", ""), "flow": "xtls-rprx-vision"}
        ],
        "tls": {
            "enabled": True,
            "server_name": node.sni,
            "reality": {
                "enabled": True,
                "handshake": {"server": node.sni, "server_port": 443},
                "private_key": reality.get("private_key", ""),
                "short_id": [reality.get("short_id", "")],
            },
        },
    }


def _build_hy2_inbound(node: ServerEntry, materials: dict) -> dict:
    """Hysteria2 inbound on port 443."""
    hy2 = materials.get("hy2", {})
    return {
        "type": "hysteria2",
        "tag": "hy2-in",
        "listen": "::",
        "listen_port": 443,
        "users": [{"password": hy2.get("password", "")}],
        "tls": {
            "enabled": True,
            "server_name": node.hy2_sni,
            "acme": {
                "domain": [node.hy2_sni],
                "email": "admin@example.com",
            },
        },
        "obfs": {
            "type": "salamander",
            "password": hy2.get("obfs_password", ""),
        },
    }


def _build_ws_cdn_inbound(
    node: ServerEntry, materials: dict, defaults: Defaults
) -> dict:
    """VLESS WS CDN inbound on cdn_port."""
    return {
        "type": "vless",
        "tag": "vless-ws-cdn-in",
        "listen": "::",
        "listen_port": defaults.near.cdn_port,
        "users": [{"uuid": materials.get("vless_uuid", "")}],
        "transport": {"type": "ws", "path": "/ws"},
    }


def _build_anytls_inbound(
    node: ServerEntry, materials: dict, defaults: Defaults
) -> dict:
    """AnyTLS inbound on anytls_port (8443)."""
    return {
        "type": "anytls",
        "tag": "anytls-in",
        "listen": "::",
        "listen_port": defaults.near.anytls_port,
        "users": [{"password": materials.get("anytls_password", "")}],
        "padding_scheme": "2+4-8+2",
    }


def _build_anytls_direct_inbound(node: ServerEntry, materials: dict) -> dict:
    """AnyTLS direct inbound on port 8444."""
    return {
        "type": "anytls",
        "tag": "anytls-direct-in",
        "listen": "::",
        "listen_port": 8444,
        "users": [{"password": materials.get("anytls_password", "")}],
        "padding_scheme": "2+4-8+2",
    }


# ---------------------------------------------------------------------------
# Internal: outbound builders
# ---------------------------------------------------------------------------

def _build_wg_endpoint(
    near_name: str,
    far_name: str,
    alloc: dict,
    materials: dict,
    far_node: ServerEntry,
) -> dict:
    """WireGuard endpoint from near to a specific far node (sing-box 1.13+ format)."""
    wg_far = materials.get("wg", {}).get(far_name, {})
    wg_near = materials.get("wg_near", {}).get(near_name, {})
    return {
        "type": "wireguard",
        "tag": f"wg-{far_name}",
        "mtu": 1380,
        "address": [f"{alloc['near_ip']}/30"],
        "private_key": wg_near.get("private_key", ""),
        "peers": [
            {
                "public_key": wg_far.get("public_key", ""),
                "address": far_node.public_ip,
                "port": alloc["wg_port"],
                "allowed_ips": ["0.0.0.0/0"],
            }
        ],
    }


def _build_direct_outbound() -> dict:
    """Direct outbound."""
    return {"type": "direct", "tag": "direct"}


def _build_block_outbound() -> dict:
    """Block outbound."""
    return {"type": "block", "tag": "block"}


# ---------------------------------------------------------------------------
# Internal: far node WG endpoint
# ---------------------------------------------------------------------------

def _build_far_wg_endpoint(
    far_node: ServerEntry,
    near_peers: list[tuple[str, dict]],
    materials: dict,
    inventory: Inventory,
) -> dict:
    """WireGuard endpoint for a far node (sing-box 1.13+ format)."""
    wg_far = materials.get("wg", {}).get(far_node.name, {})
    peers = []
    addresses: list[str] = []
    for near_name, alloc in near_peers:
        wg_near = materials.get("wg_near", {}).get(near_name, {})
        peers.append({
            "public_key": wg_near.get("public_key", ""),
            "allowed_ips": [f"{alloc['near_ip']}/32"],
        })
        far_ip = alloc["far_ip"]
        addr = f"{far_ip}/30"
        if addr not in addresses:
            addresses.append(addr)
    return {
        "type": "wireguard",
        "tag": "wg-in",
        "system": True,
        "name": "wg0",
        "mtu": 1380,
        "address": addresses,
        "private_key": wg_far.get("private_key", ""),
        "listen_port": far_node.wg_port,
        "peers": peers,
    }


# ---------------------------------------------------------------------------
# Internal: route and DNS
# ---------------------------------------------------------------------------

def _build_route(
    node: ServerEntry,
    topo: dict[tuple[str, str], dict],
    inventory: Inventory,
) -> dict:
    """Build route section (used internally by build_near_config)."""
    rules: list[dict] = [{"action": "sniff"}]
    peers = _get_ordered_peers(node, inventory, topo)
    for _i, (far_name, _alloc) in enumerate(peers):
        tag = f"vless-reality-overlay-{far_name}"
        rules.append({"inbound": [tag], "outbound": f"wg-{far_name}"})
    direct_tags = [
        "vless-reality-direct-in",
        "hy2-in",
        "vless-ws-cdn-in",
        "anytls-in",
        "anytls-direct-in",
    ]
    rules.append({"inbound": direct_tags, "outbound": "direct"})
    return {"rules": rules, "final": "direct"}


def _build_dns() -> dict:
    """DNS config with local + remote servers (sing-box 1.12+ format)."""
    return {
        "servers": [
            {"tag": "google-doh", "type": "tls", "server": "8.8.8.8"},
            {"tag": "local", "type": "local"},
        ],
        "rules": [{"outbound": "any", "server": "local"}],
    }


# ---------------------------------------------------------------------------
# Internal: helpers
# ---------------------------------------------------------------------------

def _get_ordered_peers(
    node: ServerEntry,
    inventory: Inventory,
    topo: dict[tuple[str, str], dict],
) -> list[tuple[str, dict]]:
    """Get ordered list of (far_name, alloc) for a near node's WG peers.

    Order follows node.wg_peers if specified, otherwise sorted by far name.
    """
    peer_allocs: list[tuple[str, dict]] = []
    if node.wg_peers:
        for far_name in node.wg_peers:
            pair = (node.name, far_name)
            if pair in topo:
                peer_allocs.append((far_name, topo[pair]))
    else:
        for (near_name, far_name), alloc in sorted(topo.items()):
            if near_name == node.name:
                peer_allocs.append((far_name, alloc))
    return peer_allocs


def _overlay_port_offset(peer_index: int) -> int:
    """Compute port offset for overlay peer at given index.

    port_base+1 = first peer, +2 = direct (skipped), +3 = second peer,
    +4 = third peer, +5 = fourth peer, etc.
    """
    if peer_index == 0:
        return 1
    return peer_index + 2  # skip +2 (direct), so index 1->3, 2->4, 3->5


def _route_profile(far_node: ServerEntry) -> str:
    """Determine route profile from far node's region."""
    region = far_node.region.lower()
    if region == "us":
        return "us-general"
    elif region == "jp":
        return "jp-general"
    elif region in ("hk", "tw", "sg"):
        return f"{region}-general"
    return f"{region}-general"
