import pytest
from vvnext.inventory import Inventory, node_short_label

def test_valid_inventory(sample_inventory):
    assert len(sample_inventory.near_nodes()) == 2
    assert len(sample_inventory.far_nodes()) == 1

def test_missing_near_field(sample_inventory_data):
    sample_inventory_data["servers"][0].pop("sni")
    with pytest.raises(ValueError, match="missing required field: sni"):
        Inventory(**sample_inventory_data)

def test_duplicate_port_base(sample_inventory_data):
    sample_inventory_data["servers"][1]["port_base"] = 20000
    with pytest.raises(ValueError, match="Duplicate port_base"):
        Inventory(**sample_inventory_data)

def test_invalid_wg_peer_reference(sample_inventory_data):
    sample_inventory_data["servers"][0]["wg_peers"] = ["nonexistent"]
    with pytest.raises(ValueError, match="not in inventory"):
        Inventory(**sample_inventory_data)

def test_node_short_label():
    assert node_short_label("hk-gcp-a") == "HK-A"
    assert node_short_label("us-dmit-a") == "US-DMIT-A"
    assert node_short_label("us-home-att2") == "US-ATT2"

def test_future_nodes_skip_validation(sample_inventory_data):
    sample_inventory_data["servers"].append(
        {"name": "sg-gcp-a", "role": "near", "region": "sg", "provider": "gcp",
         "public_ip": "10.0.0.9", "phase": "future"}
    )
    inv = Inventory(**sample_inventory_data)
    assert len(inv.near_nodes()) == 2  # future node not in live list

def test_get_node(sample_inventory):
    node = sample_inventory.get_node("hk-gcp-a")
    assert node.role == "near"
    assert node.port_base == 20000

def test_get_node_not_found(sample_inventory):
    with pytest.raises(KeyError, match="not found"):
        sample_inventory.get_node("nonexistent")

def test_get_ssh_target_public(sample_inventory):
    node = sample_inventory.get_node("hk-gcp-a")
    target = sample_inventory.get_ssh_target(node, sample_inventory.defaults)
    assert target == "root@10.0.0.1"

def test_residential_missing_tailscale():
    data = {
        "servers": [
            {"name": "us-home-x", "role": "residential", "region": "us",
             "provider": "home", "public_ip": "1.2.3.4", "wg_port": 51941}
        ]
    }
    with pytest.raises(ValueError, match="missing tailscale_ip"):
        Inventory(**data)

def test_far_missing_wg_port():
    data = {
        "servers": [
            {"name": "us-gcp-x", "role": "far", "region": "us",
             "provider": "gcp", "public_ip": "1.2.3.4"}
        ]
    }
    with pytest.raises(ValueError, match="missing wg_port"):
        Inventory(**data)

def test_remote_socks_no_wg_port_ok():
    """remote-socks nodes don't need wg_port"""
    data = {
        "servers": [
            {"name": "us-home-mac", "role": "residential", "region": "us",
             "provider": "home", "public_ip": "1.2.3.4",
             "tailscale_ip": "100.1.2.3",
             "protocols": ["remote-socks"]}
        ]
    }
    inv = Inventory(**data)
    assert len(inv.far_nodes()) == 1
