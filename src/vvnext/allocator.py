"""Resource allocator for new nodes: port_base, wg_port, SNI, node ID."""

from __future__ import annotations

from vvnext.inventory import Inventory
from vvnext.probe import SNI_POOL


_PORT_BASE_START = 20000
_PORT_BASE_STEP = 10
_WG_PORT_START = 51941


def allocate_port_base(inventory: Inventory) -> int:
    """Allocate next available port_base (step=10, starting at 20000)."""
    used = {s.port_base for s in inventory.servers if s.port_base is not None}
    candidate = _PORT_BASE_START
    while candidate in used:
        candidate += _PORT_BASE_STEP
    return candidate


def allocate_wg_port(inventory: Inventory) -> int:
    """Allocate next available WireGuard port (starting at 51941)."""
    used = {s.wg_port for s in inventory.servers if s.wg_port is not None}
    candidate = _WG_PORT_START
    while candidate in used:
        candidate += 1
    return candidate


def pick_sni(inventory: Inventory) -> str:
    """Pick an unused SNI from the pool for Reality handshake."""
    used = {s.sni for s in inventory.servers if s.sni}
    for sni in SNI_POOL:
        if sni not in used:
            return sni
    # Fallback: reuse first SNI (duplicates are acceptable across regions)
    return SNI_POOL[0]


def generate_node_id(
    role: str,
    region: str,
    provider: str,
    inventory: Inventory,
) -> str:
    """Generate node ID in format: {region}-{provider}-{letter}.

    Examples: hk-gcp-a, us-do-b, jp-dmit-a
    """
    existing = {s.name for s in inventory.servers}
    letter = "a"
    while True:
        candidate = f"{region}-{provider}-{letter}"
        if candidate not in existing:
            return candidate
        letter = chr(ord(letter) + 1)
        if ord(letter) > ord("z"):
            raise ValueError("Exhausted all single-letter suffixes")


def allocate_near_resources(inventory: Inventory) -> dict:
    """Allocate all resources needed for a new near node."""
    return {
        "port_base": allocate_port_base(inventory),
        "sni": pick_sni(inventory),
    }


def allocate_far_resources(inventory: Inventory) -> dict:
    """Allocate all resources needed for a new far/residential node."""
    return {
        "wg_port": allocate_wg_port(inventory),
    }
