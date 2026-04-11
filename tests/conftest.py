import pytest
from pathlib import Path
from vvnext.inventory import Inventory, ServerEntry, Defaults, load_inventory
from vvnext.settings import Settings, load_settings

@pytest.fixture
def sample_inventory_data():
    return {
        "defaults": {"ssh_user": "root"},
        "servers": [
            {"name": "hk-gcp-a", "role": "near", "region": "hk", "provider": "gcp",
             "public_ip": "10.0.0.1", "port_base": 20000, "sni": "dl.google.com",
             "hy2_sni": "hk.test.com", "cdn_domain": "hk-cdn.test.com",
             "dns_name": "hk-a.test.com", "wg_peers": ["us-gcp-a"]},
            {"name": "jp-gcp-a", "role": "near", "region": "jp", "provider": "gcp",
             "public_ip": "10.0.0.2", "port_base": 21000, "sni": "dl.google.com",
             "hy2_sni": "jp.test.com", "cdn_domain": "jp-cdn.test.com",
             "dns_name": "jp-a.test.com", "wg_peers": ["us-gcp-a"]},
            {"name": "us-gcp-a", "role": "far", "region": "us", "provider": "gcp",
             "public_ip": "10.0.0.3", "wg_port": 51941},
        ]
    }

@pytest.fixture
def sample_inventory(sample_inventory_data):
    return Inventory(**sample_inventory_data)
