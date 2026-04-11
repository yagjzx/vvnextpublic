"""Integration tests: verify module collaboration through the full pipeline."""

import json
import yaml
from vvnext.inventory import Inventory
from vvnext.state import State, load_state, save_state
from vvnext.keys import generate_wg_keypair
from vvnext.overlay import compute_topology
from vvnext.config_generator import build_near_config, build_far_config, build_manifest, build_client_nodes
from vvnext.subscription.builder import build_all_subscriptions

def _make_materials(inventory):
    """Create mock materials dict for testing (no sing-box binary needed)."""
    vless_uuid = "test-uuid-1234-5678-abcdefgh"
    materials = {
        "vless_uuid": vless_uuid,
        "reality": {},
        "wg": {},
        "hy2": {"password": "test-hy2-pass", "obfs_password": "test-obfs"},
        "anytls_password": "test-anytls-pass",
    }
    for node in inventory.servers:
        if node.role == "near":
            materials["reality"][node.name] = {
                "private_key": "test-reality-priv",
                "public_key": "test-reality-pub",
                "short_id": "abcd1234",
            }
        wg_pair = generate_wg_keypair()
        materials["wg"][node.name] = wg_pair
    return materials


def test_full_pipeline(sample_inventory, tmp_path):
    """Full render pipeline: inventory -> topology -> configs -> manifest -> subscriptions.

    Tests that all modules work together end-to-end.
    """
    state = State()

    # Step 1: Compute WG topology
    topo, state = compute_topology(sample_inventory, state)
    assert len(topo) > 0

    # Step 2: Create materials
    materials = _make_materials(sample_inventory)

    # Step 3: Render all configs
    all_manifests = []
    all_client_nodes = []
    for node in sample_inventory.near_nodes():
        config = build_near_config(node, sample_inventory, topo, materials, sample_inventory.defaults)
        assert "inbounds" in config
        assert "outbounds" in config
        assert "route" in config

        manifest = build_manifest(node, sample_inventory, topo, materials, sample_inventory.defaults)
        assert len(manifest["nodes"]) > 0
        all_manifests.append(manifest)

        client_nodes = build_client_nodes(node, sample_inventory, topo, materials, sample_inventory.defaults)
        assert len(client_nodes) > 0
        all_client_nodes.extend(client_nodes)

    for node in sample_inventory.far_nodes():
        config = build_far_config(node, sample_inventory, topo, materials, sample_inventory.defaults)
        assert "inbounds" in config

    # Step 4: Build subscriptions
    routing_rules = {
        "server_routing": {
            "ai_residential": {"domains": ["openai.com"], "preferred_exit": "residential"},
            "streaming_us": {"domains": ["netflix.com"], "preferred_exit": "far"},
            "direct_cn": {"domains": ["baidu.com"], "action": "direct"},
        }
    }

    output_dir = tmp_path / "subscription"
    output_dir.mkdir()
    result = build_all_subscriptions(
        manifests=all_manifests,
        all_client_nodes=all_client_nodes,
        routing_rules=routing_rules,
        output_dir=output_dir,
    )

    # Verify all 3 formats generated
    assert "mihomo" in result
    assert "shadowrocket" in result
    assert "singbox" in result

    # Verify mihomo is valid YAML
    mihomo_content = result["mihomo"].read_text()
    parsed = yaml.safe_load(mihomo_content)
    assert "proxies" in parsed
    assert "proxy-groups" in parsed
    assert "rules" in parsed

    # Verify singbox is valid JSON
    singbox_content = result["singbox"].read_text()
    parsed_sb = json.loads(singbox_content)
    assert "outbounds" in parsed_sb


def test_render_idempotency(sample_inventory, tmp_path):
    """Render twice with same inputs -- results must be identical."""
    state = State()
    topo, state = compute_topology(sample_inventory, state)
    materials = _make_materials(sample_inventory)

    configs_1 = {}
    configs_2 = {}
    for node in sample_inventory.near_nodes():
        configs_1[node.name] = build_near_config(node, sample_inventory, topo, materials, sample_inventory.defaults)
        configs_2[node.name] = build_near_config(node, sample_inventory, topo, materials, sample_inventory.defaults)

    for name in configs_1:
        assert json.dumps(configs_1[name], sort_keys=True) == json.dumps(configs_2[name], sort_keys=True)


def test_state_persistence(sample_inventory, tmp_path):
    """State survives save/load cycle and topology is stable."""
    state = State()
    topo1, state = compute_topology(sample_inventory, state)

    state_path = tmp_path / "state.yaml"
    save_state(state, state_path)
    loaded_state = load_state(state_path)

    topo2, loaded_state = compute_topology(sample_inventory, loaded_state)

    # Same topology after reload
    assert set(topo1.keys()) == set(topo2.keys())
    for key in topo1:
        assert topo1[key]["near_ip"] == topo2[key]["near_ip"]
        assert topo1[key]["far_ip"] == topo2[key]["far_ip"]


def test_add_node_stability(sample_inventory_data, tmp_path):
    """Adding a new far node doesn't change existing WG allocations."""
    inv1 = Inventory(**sample_inventory_data)
    state = State()
    topo1, state = compute_topology(inv1, state)

    # Add a new far node
    extended = sample_inventory_data.copy()
    extended["servers"] = list(sample_inventory_data["servers"]) + [
        {"name": "us-dmit-a", "role": "far", "region": "us", "provider": "dmit",
         "public_ip": "10.0.0.4", "wg_port": 51942},
    ]
    # Update wg_peers for near nodes
    for s in extended["servers"]:
        if s.get("wg_peers"):
            s["wg_peers"] = s["wg_peers"] + ["us-dmit-a"]

    inv2 = Inventory(**extended)
    topo2, state = compute_topology(inv2, state)

    # Existing allocations must be preserved
    for key in topo1:
        assert key in topo2
        assert topo1[key]["near_ip"] == topo2[key]["near_ip"]
