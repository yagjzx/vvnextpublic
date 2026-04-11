"""Classify manifest entries into proxy group buckets.

Takes merged manifests (from all near nodes) and classifies each entry
into one of 16 buckets based on near_region, egress_mode, protocol,
and route_profile. Then builds Clash-format proxy groups from those buckets.
"""
from __future__ import annotations


# ---------------------------------------------------------------------------
# Bucket names
# ---------------------------------------------------------------------------

_PRIMARY_BUCKETS = [
    "hk_direct", "hk_cdn", "hk_hy2",
    "jp_direct", "jp_cdn", "jp_hy2",
    "tw_direct", "tw_cdn", "tw_hy2",
    "us_overlay", "us_residential",
    "anytls_hk", "anytls_jp", "anytls_tw",
]

_COMPUTED_BUCKETS = ["anytls_all", "us_overlay_all"]

ALL_BUCKETS = _PRIMARY_BUCKETS + _COMPUTED_BUCKETS


def _near_region(near_node: str) -> str:
    """Extract region prefix from a near node name like 'hk-gcp-a'."""
    return near_node.split("-")[0].lower()


# ---------------------------------------------------------------------------
# Public: classify
# ---------------------------------------------------------------------------

def classify_nodes(manifest_entries: list[dict]) -> dict[str, list[dict]]:
    """Classify manifest entries into buckets.

    Each entry must have: near_node, route_profile, egress_mode, protocol,
    and the corresponding client_node dict (or any extra fields are preserved).

    Returns a dict mapping bucket name to list of entries in that bucket.
    """
    buckets: dict[str, list[dict]] = {b: [] for b in ALL_BUCKETS}

    for entry in manifest_entries:
        region = _near_region(entry["near_node"])
        protocol = entry["protocol"]
        egress_mode = entry["egress_mode"]
        route_profile = entry.get("route_profile", "")

        # AnyTLS buckets (region-specific)
        if protocol == "anytls":
            key = f"anytls_{region}"
            if key in buckets:
                buckets[key].append(entry)
            continue

        # CDN buckets
        if protocol == "vless-ws-cdn":
            key = f"{region}_cdn"
            if key in buckets:
                buckets[key].append(entry)
            continue

        # HY2 buckets
        if protocol == "hysteria2":
            key = f"{region}_hy2"
            if key in buckets:
                buckets[key].append(entry)
            continue

        # US overlay
        if egress_mode == "overlay" and route_profile.startswith("us"):
            buckets["us_overlay"].append(entry)
            continue

        # US residential
        if "residential" in route_profile:
            buckets["us_residential"].append(entry)
            continue

        # Direct Reality buckets (near region)
        if egress_mode == "direct" and protocol == "vless-reality":
            key = f"{region}_direct"
            if key in buckets:
                buckets[key].append(entry)
            continue

    # Computed buckets
    buckets["anytls_all"] = (
        buckets["anytls_hk"] + buckets["anytls_jp"] + buckets["anytls_tw"]
    )
    buckets["us_overlay_all"] = list(buckets["us_overlay"])

    return buckets


# ---------------------------------------------------------------------------
# Public: build proxy groups
# ---------------------------------------------------------------------------

def _proxy_names(entries: list[dict]) -> list[str]:
    """Extract unique proxy names from entries, preserving order."""
    seen: set[str] = set()
    names: list[str] = []
    for e in entries:
        name = e.get("name", "")
        if name and name not in seen:
            seen.add(name)
            names.append(name)
    return names


def build_proxy_groups(
    buckets: dict[str, list[dict]],
    routing_rules: dict,
) -> list[dict]:
    """Build Clash proxy group definitions from classified buckets.

    Groups: Auto-Select, AI, Streaming-US, Streaming-HK, US-Exit, HK-Exit,
    JP-Exit, TW-Exit, Residential, AnyTLS, HY2-Fallback, Direct.
    """
    groups: list[dict] = []

    # Collect all non-direct proxies for Auto-Select
    all_proxies: list[str] = []
    for bucket_name in _PRIMARY_BUCKETS:
        all_proxies.extend(_proxy_names(buckets.get(bucket_name, [])))
    # Deduplicate preserving order
    seen: set[str] = set()
    unique_all: list[str] = []
    for p in all_proxies:
        if p not in seen:
            seen.add(p)
            unique_all.append(p)

    # Auto-Select: fallback with all proxies
    groups.append({
        "name": "Auto-Select",
        "type": "fallback",
        "proxies": unique_all or ["DIRECT"],
        "url": "http://www.gstatic.com/generate_204",
        "interval": 300,
    })

    # AI: residential -> US overlay -> anytls
    ai_proxies = (
        _proxy_names(buckets.get("us_residential", []))
        + _proxy_names(buckets.get("us_overlay", []))
        + _proxy_names(buckets.get("anytls_all", []))
    )
    groups.append({
        "name": "AI",
        "type": "fallback",
        "proxies": ai_proxies or ["Auto-Select"],
        "url": "http://www.gstatic.com/generate_204",
        "interval": 300,
    })

    # Streaming-US: US overlay
    streaming_us_proxies = _proxy_names(buckets.get("us_overlay", []))
    groups.append({
        "name": "Streaming-US",
        "type": "fallback",
        "proxies": streaming_us_proxies or ["Auto-Select"],
        "url": "http://www.gstatic.com/generate_204",
        "interval": 300,
    })

    # Streaming-HK: HK direct + HK CDN
    streaming_hk_proxies = (
        _proxy_names(buckets.get("hk_direct", []))
        + _proxy_names(buckets.get("hk_cdn", []))
    )
    groups.append({
        "name": "Streaming-HK",
        "type": "fallback",
        "proxies": streaming_hk_proxies or ["Auto-Select"],
        "url": "http://www.gstatic.com/generate_204",
        "interval": 300,
    })

    # US-Exit: US overlay all
    us_exit_proxies = _proxy_names(buckets.get("us_overlay_all", []))
    groups.append({
        "name": "US-Exit",
        "type": "fallback",
        "proxies": us_exit_proxies or ["Auto-Select"],
        "url": "http://www.gstatic.com/generate_204",
        "interval": 300,
    })

    # HK-Exit: HK direct + HK CDN + HK HY2
    hk_exit_proxies = (
        _proxy_names(buckets.get("hk_direct", []))
        + _proxy_names(buckets.get("hk_cdn", []))
        + _proxy_names(buckets.get("hk_hy2", []))
    )
    groups.append({
        "name": "HK-Exit",
        "type": "fallback",
        "proxies": hk_exit_proxies or ["Auto-Select"],
        "url": "http://www.gstatic.com/generate_204",
        "interval": 300,
    })

    # JP-Exit: JP direct + JP CDN + JP HY2
    jp_exit_proxies = (
        _proxy_names(buckets.get("jp_direct", []))
        + _proxy_names(buckets.get("jp_cdn", []))
        + _proxy_names(buckets.get("jp_hy2", []))
    )
    groups.append({
        "name": "JP-Exit",
        "type": "fallback",
        "proxies": jp_exit_proxies or ["Auto-Select"],
        "url": "http://www.gstatic.com/generate_204",
        "interval": 300,
    })

    # TW-Exit: TW direct + TW CDN + TW HY2
    tw_exit_proxies = (
        _proxy_names(buckets.get("tw_direct", []))
        + _proxy_names(buckets.get("tw_cdn", []))
        + _proxy_names(buckets.get("tw_hy2", []))
    )
    groups.append({
        "name": "TW-Exit",
        "type": "fallback",
        "proxies": tw_exit_proxies or ["Auto-Select"],
        "url": "http://www.gstatic.com/generate_204",
        "interval": 300,
    })

    # Residential
    residential_proxies = _proxy_names(buckets.get("us_residential", []))
    groups.append({
        "name": "Residential",
        "type": "fallback",
        "proxies": residential_proxies or ["Auto-Select"],
        "url": "http://www.gstatic.com/generate_204",
        "interval": 300,
    })

    # AnyTLS
    anytls_proxies = _proxy_names(buckets.get("anytls_all", []))
    groups.append({
        "name": "AnyTLS",
        "type": "fallback",
        "proxies": anytls_proxies or ["Auto-Select"],
        "url": "http://www.gstatic.com/generate_204",
        "interval": 300,
    })

    # HY2-Fallback: all HY2 from every region
    hy2_proxies = (
        _proxy_names(buckets.get("hk_hy2", []))
        + _proxy_names(buckets.get("jp_hy2", []))
        + _proxy_names(buckets.get("tw_hy2", []))
    )
    groups.append({
        "name": "HY2-Fallback",
        "type": "fallback",
        "proxies": hy2_proxies or ["Auto-Select"],
        "url": "http://www.gstatic.com/generate_204",
        "interval": 300,
    })

    # Direct
    groups.append({
        "name": "Direct",
        "type": "select",
        "proxies": ["DIRECT"],
    })

    return groups
