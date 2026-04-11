import base64
import json

import pytest
import yaml

from vvnext.subscription.classifier import classify_nodes, build_proxy_groups
from vvnext.subscription.formats.mihomo import build_mihomo_subscription
from vvnext.subscription.formats.shadowrocket import build_shadowrocket_subscription
from vvnext.subscription.formats.singbox import (
    build_singbox_subscription,
    _clash_node_to_singbox_outbound,
)
from vvnext.subscription.builder import build_all_subscriptions


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_routing_rules():
    return {
        "server_routing": {
            "ai_residential": {
                "domains": ["openai.com", "anthropic.com"],
                "preferred_exit": "residential",
                "fallback_exit": "far",
            },
            "streaming_us": {
                "domains": ["netflix.com"],
                "preferred_exit": "far",
            },
            "streaming_hk": {
                "domains": ["bilibili.com"],
                "preferred_exit": "near",
            },
            "direct_cn": {
                "domains": ["baidu.com"],
                "action": "direct",
            },
        }
    }


@pytest.fixture
def sample_client_nodes():
    return [
        {
            "name": "HK-A -> US-A | Reality",
            "type": "vless",
            "server": "hk-a.test.com",
            "port": 20001,
            "uuid": "test-uuid",
            "flow": "xtls-rprx-vision",
            "tls": True,
            "client-fingerprint": "random",
            "servername": "dl.google.com",
            "reality-opts": {"public-key": "fake-pub", "short-id": "abcd1234"},
        },
        {
            "name": "HK-A Direct | Reality",
            "type": "vless",
            "server": "hk-a.test.com",
            "port": 20002,
            "uuid": "test-uuid",
            "flow": "xtls-rprx-vision",
            "tls": True,
            "client-fingerprint": "random",
            "servername": "dl.google.com",
            "reality-opts": {"public-key": "fake-pub", "short-id": "abcd1234"},
        },
        {
            "name": "HK-A | HY2",
            "type": "hysteria2",
            "server": "hk-a.test.com",
            "port": 443,
            "password": "hy2-pw",
            "obfs": "salamander",
            "obfs-password": "obfs-pw",
            "sni": "hk.test.com",
        },
        {
            "name": "HK-A | CDN",
            "type": "vless",
            "server": "hk-cdn.test.com",
            "port": 2053,
            "uuid": "test-uuid",
            "tls": False,
            "network": "ws",
            "ws-opts": {"path": "/ws"},
        },
        {
            "name": "HK-A | AnyTLS",
            "type": "anytls",
            "server": "hk-a.test.com",
            "port": 8443,
            "password": "anytls-pw",
        },
    ]


@pytest.fixture
def sample_manifests():
    return [
        {
            "near_node": "hk-gcp-a",
            "nodes": [
                {
                    "link_id": "hk-gcp-a-reality-overlay-us-gcp-a",
                    "overlay_node_name": "HK-A -> US-A",
                    "route_profile": "us-general",
                    "listen_port": 20001,
                    "transport": "tcp",
                    "egress_mode": "overlay",
                    "protocol": "vless-reality",
                },
                {
                    "link_id": "hk-gcp-a-reality-direct",
                    "overlay_node_name": "HK-A Direct",
                    "route_profile": "near-direct",
                    "listen_port": 20002,
                    "transport": "tcp",
                    "egress_mode": "direct",
                    "protocol": "vless-reality",
                },
                {
                    "link_id": "hk-gcp-a-hy2-direct",
                    "overlay_node_name": "HK-A HY2",
                    "route_profile": "near-direct",
                    "listen_port": 443,
                    "transport": "udp",
                    "egress_mode": "direct",
                    "protocol": "hysteria2",
                },
                {
                    "link_id": "hk-gcp-a-cdn-direct",
                    "overlay_node_name": "HK-A CDN",
                    "route_profile": "near-direct",
                    "listen_port": 2053,
                    "transport": "ws",
                    "egress_mode": "direct",
                    "protocol": "vless-ws-cdn",
                },
                {
                    "link_id": "hk-gcp-a-anytls-direct",
                    "overlay_node_name": "HK-A AnyTLS",
                    "route_profile": "near-direct",
                    "listen_port": 8443,
                    "transport": "tcp",
                    "egress_mode": "direct",
                    "protocol": "anytls",
                },
            ],
        }
    ]


@pytest.fixture
def sample_proxy_groups(sample_manifests, sample_client_nodes):
    entries = []
    idx = 0
    for m in sample_manifests:
        for node_entry in m["nodes"]:
            entry = dict(node_entry)
            entry["near_node"] = m["near_node"]
            if idx < len(sample_client_nodes):
                entry["name"] = sample_client_nodes[idx]["name"]
            idx += 1
            entries.append(entry)
    buckets = classify_nodes(entries)
    return build_proxy_groups(buckets, {})


# ---------------------------------------------------------------------------
# Mihomo tests
# ---------------------------------------------------------------------------

class TestMihomo:
    def test_output_valid_yaml(self, sample_client_nodes, sample_proxy_groups, sample_routing_rules):
        output = build_mihomo_subscription(sample_client_nodes, sample_proxy_groups, sample_routing_rules)
        parsed = yaml.safe_load(output)
        assert isinstance(parsed, dict)
        assert "proxies" in parsed
        assert "proxy-groups" in parsed
        assert "rules" in parsed
        assert "dns" in parsed

    def test_proxy_groups_exist(self, sample_client_nodes, sample_proxy_groups, sample_routing_rules):
        output = build_mihomo_subscription(sample_client_nodes, sample_proxy_groups, sample_routing_rules)
        parsed = yaml.safe_load(output)
        group_names = {g["name"] for g in parsed["proxy-groups"]}
        assert "Auto-Select" in group_names
        assert "AI" in group_names
        assert "Streaming-US" in group_names
        assert "Direct" in group_names

    def test_rules_inline(self, sample_client_nodes, sample_proxy_groups, sample_routing_rules):
        output = build_mihomo_subscription(sample_client_nodes, sample_proxy_groups, sample_routing_rules)
        parsed = yaml.safe_load(output)
        rules = parsed["rules"]
        assert any("openai.com" in r for r in rules)
        assert any("GEOIP,CN,DIRECT" == r for r in rules)
        assert rules[-1] == "MATCH,Auto-Select"

    def test_direct_cn_rules(self, sample_client_nodes, sample_proxy_groups, sample_routing_rules):
        output = build_mihomo_subscription(sample_client_nodes, sample_proxy_groups, sample_routing_rules)
        parsed = yaml.safe_load(output)
        rules = parsed["rules"]
        assert "DOMAIN-SUFFIX,baidu.com,DIRECT" in rules


# ---------------------------------------------------------------------------
# Shadowrocket tests
# ---------------------------------------------------------------------------

class TestShadowrocket:
    def test_base64_decodable(self, sample_client_nodes):
        output = build_shadowrocket_subscription(sample_client_nodes)
        decoded = base64.b64decode(output).decode()
        lines = decoded.strip().split("\n")
        assert len(lines) == len(sample_client_nodes)

    def test_vless_reality_uri(self, sample_client_nodes):
        output = build_shadowrocket_subscription(sample_client_nodes)
        decoded = base64.b64decode(output).decode()
        lines = decoded.strip().split("\n")
        # First entry is VLESS Reality
        assert lines[0].startswith("vless://")
        assert "security=reality" in lines[0]
        assert "pbk=fake-pub" in lines[0]

    def test_hy2_uri(self, sample_client_nodes):
        output = build_shadowrocket_subscription(sample_client_nodes)
        decoded = base64.b64decode(output).decode()
        lines = decoded.strip().split("\n")
        # Third entry is HY2
        hy2_line = lines[2]
        assert hy2_line.startswith("hysteria2://")
        assert "obfs=salamander" in hy2_line

    def test_ws_cdn_uri(self, sample_client_nodes):
        output = build_shadowrocket_subscription(sample_client_nodes)
        decoded = base64.b64decode(output).decode()
        lines = decoded.strip().split("\n")
        # Fourth entry is CDN
        cdn_line = lines[3]
        assert cdn_line.startswith("vless://")
        assert "type=ws" in cdn_line
        assert "security=none" in cdn_line

    def test_anytls_uri(self, sample_client_nodes):
        output = build_shadowrocket_subscription(sample_client_nodes)
        decoded = base64.b64decode(output).decode()
        lines = decoded.strip().split("\n")
        # Fifth entry is AnyTLS
        anytls_line = lines[4]
        assert anytls_line.startswith("anytls://")


# ---------------------------------------------------------------------------
# sing-box tests
# ---------------------------------------------------------------------------

class TestSingbox:
    def test_vless_reality_outbound(self):
        node = {
            "name": "HK-A | Reality",
            "type": "vless",
            "server": "hk-a.test.com",
            "port": 20001,
            "uuid": "test-uuid",
            "flow": "xtls-rprx-vision",
            "tls": True,
            "servername": "dl.google.com",
            "reality-opts": {"public-key": "fake-pub", "short-id": "abcd1234"},
        }
        ob = _clash_node_to_singbox_outbound(node)
        assert ob["type"] == "vless"
        assert ob["server_port"] == 20001
        assert ob["tls"]["reality"]["enabled"] is True
        assert ob["tls"]["reality"]["public_key"] == "fake-pub"

    def test_hy2_outbound(self):
        node = {
            "name": "HK-A | HY2",
            "type": "hysteria2",
            "server": "hk-a.test.com",
            "port": 443,
            "password": "hy2-pw",
            "obfs": "salamander",
            "obfs-password": "obfs-pw",
            "sni": "hk.test.com",
        }
        ob = _clash_node_to_singbox_outbound(node)
        assert ob["type"] == "hysteria2"
        assert ob["obfs"]["type"] == "salamander"
        assert ob["tls"]["server_name"] == "hk.test.com"

    def test_anytls_outbound(self):
        node = {
            "name": "HK-A | AnyTLS",
            "type": "anytls",
            "server": "hk-a.test.com",
            "port": 8443,
            "password": "anytls-pw",
        }
        ob = _clash_node_to_singbox_outbound(node)
        assert ob["type"] == "anytls"
        assert ob["password"] == "anytls-pw"

    def test_ws_cdn_outbound(self):
        node = {
            "name": "HK-A | CDN",
            "type": "vless",
            "server": "hk-cdn.test.com",
            "port": 2053,
            "uuid": "test-uuid",
            "network": "ws",
            "ws-opts": {"path": "/ws"},
        }
        ob = _clash_node_to_singbox_outbound(node)
        assert ob["type"] == "vless"
        assert ob["transport"]["type"] == "ws"
        assert "tls" not in ob  # CDN mode, no TLS

    def test_full_subscription(self, sample_client_nodes, sample_proxy_groups):
        config = build_singbox_subscription(sample_client_nodes, sample_proxy_groups)
        assert "outbounds" in config
        assert "inbounds" in config
        tags = {ob["tag"] for ob in config["outbounds"]}
        assert "direct" in tags
        assert "Auto-Select" in tags


# ---------------------------------------------------------------------------
# Builder tests
# ---------------------------------------------------------------------------

class TestBuilder:
    def test_all_formats(self, sample_manifests, sample_client_nodes, sample_routing_rules, tmp_path):
        results = build_all_subscriptions(
            sample_manifests, sample_client_nodes, sample_routing_rules, tmp_path,
        )
        assert "mihomo" in results
        assert "shadowrocket" in results
        assert "singbox" in results
        assert results["mihomo"].exists()
        assert results["shadowrocket"].exists()
        assert results["singbox"].exists()

    def test_single_format(self, sample_manifests, sample_client_nodes, sample_routing_rules, tmp_path):
        results = build_all_subscriptions(
            sample_manifests, sample_client_nodes, sample_routing_rules, tmp_path,
            formats=["mihomo"],
        )
        assert "mihomo" in results
        assert "shadowrocket" not in results
        assert "singbox" not in results

    def test_mihomo_file_is_valid_yaml(self, sample_manifests, sample_client_nodes, sample_routing_rules, tmp_path):
        results = build_all_subscriptions(
            sample_manifests, sample_client_nodes, sample_routing_rules, tmp_path,
            formats=["mihomo"],
        )
        content = results["mihomo"].read_text()
        parsed = yaml.safe_load(content)
        assert isinstance(parsed, dict)

    def test_singbox_file_is_valid_json(self, sample_manifests, sample_client_nodes, sample_routing_rules, tmp_path):
        results = build_all_subscriptions(
            sample_manifests, sample_client_nodes, sample_routing_rules, tmp_path,
            formats=["singbox"],
        )
        content = results["singbox"].read_text()
        parsed = json.loads(content)
        assert isinstance(parsed, dict)
