from __future__ import annotations
from vvnext.inventory import Inventory
from vvnext.state import State, WgNodeAllocation, WgPeerAllocation

def compute_topology(
    inventory: Inventory, state: State
) -> tuple[dict[tuple[str, str], dict], State]:
    """Compute WG overlay topology with persistent subnet allocation.
    Returns (topology_dict, updated_state).
    topology_dict keys are (near_name, far_name) tuples.
    """
    near_nodes = sorted(inventory.near_nodes(), key=lambda s: s.name)
    far_nodes = sorted(inventory.far_nodes(), key=lambda s: s.wg_port or 0)
    far_nodes = [f for f in far_nodes if f.wg_port is not None]

    # Determine which pairs should exist
    desired_pairs: list[tuple[str, str]] = []
    for near in near_nodes:
        for far in far_nodes:
            if near.wg_peers is None or far.name in near.wg_peers:
                desired_pairs.append((near.name, far.name))

    # Reuse existing allocations from state
    result: dict[tuple[str, str], dict] = {}
    used_offsets: set[int] = set()

    for far_name, alloc in state.wg_allocations.items():
        for near_name, peer in alloc.peers.items():
            pair = (near_name, far_name)
            if pair in desired_pairs:
                offset = _ip_to_offset(peer.near_ip, inventory.defaults.wg.subnet_base)
                used_offsets.add(offset)
                result[pair] = {
                    "near_ip": peer.near_ip,
                    "far_ip": peer.far_ip,
                    "wg_port": alloc.wg_port,
                }

    # Allocate new pairs
    next_offset = 0
    for pair in desired_pairs:
        if pair in result:
            continue
        while next_offset in used_offsets:
            next_offset += 1
        near_ip, far_ip = _offset_to_ips(next_offset, inventory.defaults.wg.subnet_base)
        far_node = inventory.get_node(pair[1])
        result[pair] = {
            "near_ip": near_ip,
            "far_ip": far_ip,
            "wg_port": far_node.wg_port,
        }
        used_offsets.add(next_offset)
        next_offset += 1

    # Update state
    new_state = State(
        wg_allocations={},
        last_deploy=state.last_deploy,
        bootstrap_checkpoints=state.bootstrap_checkpoints,
    )
    for (near_name, far_name), alloc in result.items():
        if far_name not in new_state.wg_allocations:
            far_node = inventory.get_node(far_name)
            new_state.wg_allocations[far_name] = WgNodeAllocation(
                wg_port=far_node.wg_port, peers={}
            )
        new_state.wg_allocations[far_name].peers[near_name] = WgPeerAllocation(
            near_ip=alloc["near_ip"], far_ip=alloc["far_ip"]
        )

    return result, new_state

def _offset_to_ips(offset: int, subnet_base: str) -> tuple[str, str]:
    base_parts = [int(x) for x in subnet_base.split(".")]
    block = offset * 4
    third = base_parts[2] + (block // 256)
    fourth = block % 256
    near_ip = f"{base_parts[0]}.{base_parts[1]}.{third}.{fourth + 2}"
    far_ip = f"{base_parts[0]}.{base_parts[1]}.{third}.{fourth + 1}"
    return near_ip, far_ip

def _ip_to_offset(ip: str, subnet_base: str) -> int:
    base_parts = [int(x) for x in subnet_base.split(".")]
    ip_parts = [int(x) for x in ip.split(".")]
    block = ((ip_parts[2] - base_parts[2]) * 256) + (ip_parts[3] - 2)
    return block // 4
