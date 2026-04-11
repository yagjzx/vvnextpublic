import pytest, json
from vvnext.config_generator import build_near_config, build_far_config, build_manifest, build_client_nodes
from vvnext.inventory import Inventory
from vvnext.overlay import compute_topology
from vvnext.state import State

@pytest.fixture
def materials():
    return {
        "vless_uuid": "test-uuid-1234-5678-abcd",
        "anytls_password": "test-anytls-pw",
        "hy2": {"password": "test-hy2-pw", "obfs_password": "test-obfs-pw"},
        "reality": {"hk-gcp-a": {"private_key": "fake-priv", "public_key": "fake-pub", "short_id": "abcd1234"}},
        "wg": {"us-gcp-a": {"private_key": "fake-wg-priv", "public_key": "fake-wg-pub"}},
    }

def test_near_config_has_all_inbounds(sample_inventory, materials):
    state = State()
    topo, state = compute_topology(sample_inventory, state)
    node = sample_inventory.get_node("hk-gcp-a")
    config = build_near_config(node, sample_inventory, topo, materials, sample_inventory.defaults)
    inbound_tags = {ib["tag"] for ib in config["inbounds"]}
    assert "vless-reality-in" in inbound_tags or any("vless-reality" in t for t in inbound_tags)
    assert "vless-reality-direct-in" in inbound_tags
    assert "hy2-in" in inbound_tags
    assert "vless-ws-cdn-in" in inbound_tags
    assert "anytls-in" in inbound_tags
    assert "anytls-direct-in" in inbound_tags

def test_near_config_has_wg_outbounds(sample_inventory, materials):
    state = State()
    topo, state = compute_topology(sample_inventory, state)
    node = sample_inventory.get_node("hk-gcp-a")
    config = build_near_config(node, sample_inventory, topo, materials, sample_inventory.defaults)
    outbound_tags = {ob["tag"] for ob in config["outbounds"]}
    assert "wg-us-gcp-a" in outbound_tags
    assert "direct" in outbound_tags

def test_far_config_has_wg_inbound(sample_inventory, materials):
    state = State()
    topo, state = compute_topology(sample_inventory, state)
    node = sample_inventory.get_node("us-gcp-a")
    config = build_far_config(node, sample_inventory, topo, materials, sample_inventory.defaults)
    inbound_tags = {ib["tag"] for ib in config["inbounds"]}
    assert any("wg" in t for t in inbound_tags)

def test_render_idempotency(sample_inventory, materials):
    state = State()
    topo, state = compute_topology(sample_inventory, state)
    node = sample_inventory.get_node("hk-gcp-a")
    c1 = json.dumps(build_near_config(node, sample_inventory, topo, materials, sample_inventory.defaults), sort_keys=True)
    c2 = json.dumps(build_near_config(node, sample_inventory, topo, materials, sample_inventory.defaults), sort_keys=True)
    assert c1 == c2

def test_manifest_has_route_profiles(sample_inventory, materials):
    state = State()
    topo, state = compute_topology(sample_inventory, state)
    node = sample_inventory.get_node("hk-gcp-a")
    manifest = build_manifest(node, sample_inventory, topo, materials, sample_inventory.defaults)
    profiles = {n["route_profile"] for n in manifest["nodes"]}
    assert len(profiles) > 0
