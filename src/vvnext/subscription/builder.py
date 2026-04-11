"""Orchestrate subscription generation for all formats.

Merges manifests from all near nodes, classifies nodes into buckets,
builds proxy groups, and generates output files for each format.
"""
from __future__ import annotations

import json
from pathlib import Path

from vvnext.subscription.classifier import classify_nodes, build_proxy_groups
from vvnext.subscription.formats.mihomo import build_mihomo_subscription
from vvnext.subscription.formats.shadowrocket import build_shadowrocket_subscription
from vvnext.subscription.formats.singbox import build_singbox_subscription


_ALL_FORMATS = ["mihomo", "shadowrocket", "singbox"]


def _merge_manifest_entries(
    manifests: list[dict],
    all_client_nodes: list[dict],
) -> list[dict]:
    """Merge manifests and attach client_node dicts to each entry.

    Each manifest has 'near_node' and 'nodes' (list of link entries).
    The all_client_nodes list has the corresponding Clash-format proxy dicts,
    in the same order as the flattened manifests.
    """
    entries: list[dict] = []
    idx = 0
    for manifest in manifests:
        near_node = manifest["near_node"]
        for node_entry in manifest["nodes"]:
            entry = dict(node_entry)
            entry["near_node"] = near_node
            # Attach the corresponding client_node if available
            if idx < len(all_client_nodes):
                entry["name"] = all_client_nodes[idx].get("name", "")
                entry["client_node"] = all_client_nodes[idx]
            idx += 1
            entries.append(entry)
    return entries


def _dedup_client_nodes(client_nodes: list[dict]) -> list[dict]:
    """Remove duplicate client nodes by name."""
    seen: set[str] = set()
    result: list[dict] = []
    for node in client_nodes:
        name = node.get("name", "")
        if name not in seen:
            seen.add(name)
            result.append(node)
    return result


def build_all_subscriptions(
    manifests: list[dict],
    all_client_nodes: list[dict],
    routing_rules: dict,
    output_dir: Path,
    formats: list[str] | None = None,
) -> dict[str, Path]:
    """Orchestrate subscription generation.

    1. Merge manifests from all near nodes
    2. Classify nodes into buckets
    3. Build proxy groups
    4. Generate each format
    5. Write output files

    Returns: {format_name: output_path}
    """
    if formats is None:
        formats = list(_ALL_FORMATS)

    # 1. Merge manifests with client node data
    entries = _merge_manifest_entries(manifests, all_client_nodes)

    # 2. Classify
    buckets = classify_nodes(entries)

    # 3. Build proxy groups
    proxy_groups = build_proxy_groups(buckets, routing_rules)

    # 4. Dedup client nodes
    deduped_nodes = _dedup_client_nodes(all_client_nodes)

    # 5. Generate each format
    output_dir.mkdir(parents=True, exist_ok=True)
    results: dict[str, Path] = {}

    if "mihomo" in formats:
        content = build_mihomo_subscription(deduped_nodes, proxy_groups, routing_rules)
        path = output_dir / "mihomo.yaml"
        path.write_text(content)
        results["mihomo"] = path

    if "shadowrocket" in formats:
        content = build_shadowrocket_subscription(deduped_nodes)
        path = output_dir / "shadowrocket.txt"
        path.write_text(content)
        results["shadowrocket"] = path

    if "singbox" in formats:
        config = build_singbox_subscription(deduped_nodes, proxy_groups)
        path = output_dir / "singbox.json"
        path.write_text(json.dumps(config, indent=2))
        results["singbox"] = path

    return results
