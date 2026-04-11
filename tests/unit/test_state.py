from vvnext.state import State, WgNodeAllocation, WgPeerAllocation, load_state, save_state

def test_empty_state():
    s = State()
    assert s.wg_allocations == {}
    assert s.last_deploy is None

def test_load_state_missing_file(tmp_path):
    s = load_state(tmp_path / "nonexistent.yaml")
    assert s.wg_allocations == {}

def test_save_and_load_state(tmp_path):
    path = tmp_path / "state.yaml"
    state = State(
        wg_allocations={
            "us-gcp-a": WgNodeAllocation(
                wg_port=51941,
                peers={"hk-gcp-a": WgPeerAllocation(near_ip="10.240.10.2", far_ip="10.240.10.1")}
            )
        },
        last_deploy="2026-04-09T14:30:00Z"
    )
    save_state(state, path)
    loaded = load_state(path)
    assert loaded.wg_allocations["us-gcp-a"].wg_port == 51941
    assert loaded.wg_allocations["us-gcp-a"].peers["hk-gcp-a"].near_ip == "10.240.10.2"
    assert loaded.last_deploy == "2026-04-09T14:30:00Z"
