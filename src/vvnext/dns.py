"""DNS management via Cloudflare API or manual output."""

from __future__ import annotations

import httpx
from vvnext.inventory import Inventory
from vvnext.settings import Settings

CF_API_BASE = "https://api.cloudflare.com/client/v4"


def _cf_headers(token: str) -> dict:
    """Standard Cloudflare API headers."""
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


def _get_zone_id(domain: str, token: str) -> str:
    """Get Cloudflare zone ID for a domain."""
    resp = httpx.get(
        f"{CF_API_BASE}/zones",
        headers=_cf_headers(token),
        params={"name": domain},
    )
    resp.raise_for_status()
    data = resp.json()
    if not data.get("result"):
        raise ValueError(f"No zone found for domain: {domain}")
    return data["result"][0]["id"]


def _list_dns_records(zone_id: str, token: str, name: str = "") -> list[dict]:
    """List existing DNS records, optionally filtered by name."""
    params: dict = {"type": "A"}
    if name:
        params["name"] = name
    resp = httpx.get(
        f"{CF_API_BASE}/zones/{zone_id}/dns_records",
        headers=_cf_headers(token),
        params=params,
    )
    resp.raise_for_status()
    return resp.json().get("result", [])


def _create_record(zone_id: str, token: str, record: dict) -> dict:
    """Create a DNS record."""
    resp = httpx.post(
        f"{CF_API_BASE}/zones/{zone_id}/dns_records",
        headers=_cf_headers(token),
        json=record,
    )
    resp.raise_for_status()
    return resp.json().get("result", {})


def _update_record(zone_id: str, record_id: str, token: str, record: dict) -> dict:
    """Update an existing DNS record."""
    resp = httpx.put(
        f"{CF_API_BASE}/zones/{zone_id}/dns_records/{record_id}",
        headers=_cf_headers(token),
        json=record,
    )
    resp.raise_for_status()
    return resp.json().get("result", {})


def build_dns_plan(inventory: Inventory, settings: Settings) -> list[dict]:
    """Build list of DNS records that need to be created/updated.
    Does NOT make any API calls. Returns plan for review.

    Each entry: {name, type, content, proxied, node}
    """
    records: list[dict] = []
    for node in inventory.near_nodes():
        # dns_name -> public_ip (not proxied, direct connection)
        if node.dns_name:
            records.append({
                "name": node.dns_name,
                "type": "A",
                "content": node.public_ip,
                "proxied": False,
                "node": node.name,
            })
        # cdn_domain -> public_ip (proxied through CDN)
        if node.cdn_domain:
            records.append({
                "name": node.cdn_domain,
                "type": "A",
                "content": node.public_ip,
                "proxied": True,
                "node": node.name,
            })
    return records


def format_manual_instructions(records: list[dict]) -> str:
    """Format DNS records as human-readable instructions for manual setup."""
    if not records:
        return "No DNS records to configure."
    lines = ["DNS Records to configure:", ""]
    for r in records:
        proxied_str = "Yes (CDN)" if r["proxied"] else "No (Direct)"
        lines.append(f"  {r['name']}")
        lines.append(f"    Type:    {r['type']}")
        lines.append(f"    Content: {r['content']}")
        lines.append(f"    Proxied: {proxied_str}")
        lines.append(f"    Node:    {r['node']}")
        lines.append("")
    return "\n".join(lines)


def upsert_dns_records(inventory: Inventory, settings: Settings) -> list[dict]:
    """Create or update A records for all near nodes.

    For each near node:
    - dns_name -> public_ip (A record, proxied=False)
    - cdn_domain -> public_ip (A record, proxied=True for CDN)

    If settings.dns.provider == "manual":
        Return records as list for manual creation (print format).
    If settings.dns.provider == "cloudflare":
        Call CF API to create/update records.

    Returns list of {action, name, type, content, proxied} dicts.
    """
    plan = build_dns_plan(inventory, settings)

    if settings.dns.provider == "manual":
        results = []
        for r in plan:
            results.append({
                "action": "manual",
                "name": r["name"],
                "type": r["type"],
                "content": r["content"],
                "proxied": r["proxied"],
            })
        return results

    # Cloudflare mode
    token = settings.dns.cf_api_token
    if not token:
        raise ValueError("Cloudflare API token not configured (dns.cf_api_token)")

    domain = settings.domain
    if not domain:
        raise ValueError("Domain not configured (settings.domain)")

    zone_id = _get_zone_id(domain, token)
    results = []

    for r in plan:
        existing = _list_dns_records(zone_id, token, name=r["name"])
        record_payload = {
            "type": r["type"],
            "name": r["name"],
            "content": r["content"],
            "proxied": r["proxied"],
        }

        if existing:
            # Update existing record
            record_id = existing[0]["id"]
            _update_record(zone_id, record_id, token, record_payload)
            results.append({
                "action": "updated",
                "name": r["name"],
                "type": r["type"],
                "content": r["content"],
                "proxied": r["proxied"],
            })
        else:
            # Create new record
            _create_record(zone_id, token, record_payload)
            results.append({
                "action": "created",
                "name": r["name"],
                "type": r["type"],
                "content": r["content"],
                "proxied": r["proxied"],
            })

    return results
