from vvnext.overlay import compute_topology
from vvnext.inventory import Inventory
from vvnext.state import State

def test_basic_topology(sample_inventory):
    state = State()
    topo, state = compute_topology(sample_inventory, state)
    # hk-gcp-a peers with us-gcp-a; jp-gcp-a peers with us-gcp-a
    assert len(topo) == 2
    assert ("hk-gcp-a", "us-gcp-a") in topo
    assert ("jp-gcp-a", "us-gcp-a") in topo

def test_subnet_persistence(sample_inventory):
    state = State()
    topo1, state1 = compute_topology(sample_inventory, state)
    # Same inventory + same state = same result
    topo2, state2 = compute_topology(sample_inventory, state1)
    for pair in topo1:
        assert topo1[pair]["near_ip"] == topo2[pair]["near_ip"]
        assert topo1[pair]["far_ip"] == topo2[pair]["far_ip"]

def test_add_node_preserves_existing(sample_inventory_data):
    inv1 = Inventory(**sample_inventory_data)
    state = State()
    topo1, state = compute_topology(inv1, state)
    old_alloc = topo1[("hk-gcp-a", "us-gcp-a")]["near_ip"]
    # Add second far node
    sample_inventory_data["servers"].append(
        {"name": "us-gcp-b", "role": "far", "region": "us", "provider": "gcp",
         "public_ip": "10.0.0.4", "wg_port": 51942}
    )
    sample_inventory_data["servers"][0]["wg_peers"] = ["us-gcp-a", "us-gcp-b"]
    inv2 = Inventory(**sample_inventory_data)
    topo2, state2 = compute_topology(inv2, state)
    # Existing allocation unchanged
    assert topo2[("hk-gcp-a", "us-gcp-a")]["near_ip"] == old_alloc
    # New allocation exists
    assert ("hk-gcp-a", "us-gcp-b") in topo2

def test_remove_node_preserves_others(sample_inventory_data):
    # Start with 2 near, 1 far
    inv = Inventory(**sample_inventory_data)
    state = State()
    topo, state = compute_topology(inv, state)
    jp_alloc = topo[("jp-gcp-a", "us-gcp-a")]["near_ip"]
    # Remove hk-gcp-a
    sample_inventory_data["servers"] = [s for s in sample_inventory_data["servers"] if s["name"] != "hk-gcp-a"]
    sample_inventory_data["servers"][0]["wg_peers"] = ["us-gcp-a"]  # fix jp
    inv2 = Inventory(**sample_inventory_data)
    topo2, state2 = compute_topology(inv2, state)
    # jp allocation unchanged
    assert topo2[("jp-gcp-a", "us-gcp-a")]["near_ip"] == jp_alloc
