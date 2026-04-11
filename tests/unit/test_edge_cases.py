import yaml
import pytest

from vvnext.subscription.formats.mihomo import (
    build_mihomo_subscription,
    dump_mihomo_yaml,
    _needs_quoting,
)
from vvnext.subscription.builder import build_all_subscriptions


# ---------------------------------------------------------------------------
# Go yaml.v3 float workaround
# ---------------------------------------------------------------------------

class TestGoYamlFloat:
    @pytest.mark.parametrize("value", [
        "1e10", "3e4", ".5", "1.0", "0.5", "1e-3",
        ".inf", ".Inf", ".INF", "+.inf", "-.inf",
        ".nan", ".NaN", ".NAN",
        "1.5e10", "3.14",
    ])
    def test_float_strings_are_quoted(self, value):
        assert _needs_quoting(value), f"{value!r} should need quoting"
        output = dump_mihomo_yaml({"test": value})
        parsed = yaml.safe_load(output)
        # After round-trip, value must still be a string
        assert isinstance(parsed["test"], str), f"{value!r} was coerced to {type(parsed['test'])}"
        assert parsed["test"] == value

    @pytest.mark.parametrize("value", [
        "hello", "vless", "xtls-rprx-vision",
        "dl.google.com", "/ws", "abcd1234",
    ])
    def test_normal_strings_not_quoted(self, value):
        assert not _needs_quoting(value)

    @pytest.mark.parametrize("value", [
        "123", "0", "-5", "+42",
    ])
    def test_pure_integers_not_quoted(self, value):
        assert not _needs_quoting(value)

    def test_float_in_proxy_entry(self):
        """Ensure a proxy entry with a float-like short_id survives YAML round-trip."""
        nodes = [{
            "name": "test",
            "type": "vless",
            "server": "test.com",
            "port": 443,
            "uuid": "test-uuid",
            "reality-opts": {"public-key": "pk", "short-id": "1e10"},
        }]
        groups = [{"name": "Auto-Select", "type": "fallback", "proxies": ["test"],
                    "url": "http://www.gstatic.com/generate_204", "interval": 300}]
        output = build_mihomo_subscription(nodes, groups, {})
        parsed = yaml.safe_load(output)
        short_id = parsed["proxies"][0]["reality-opts"]["short-id"]
        assert isinstance(short_id, str)
        assert short_id == "1e10"


# ---------------------------------------------------------------------------
# CDN node no skip-cert-verify
# ---------------------------------------------------------------------------

class TestCdnNode:
    def test_cdn_node_no_skip_cert_verify(self):
        """CDN nodes should not have skip-cert-verify field."""
        nodes = [{
            "name": "HK-A | CDN",
            "type": "vless",
            "server": "hk-cdn.test.com",
            "port": 2053,
            "uuid": "test-uuid",
            "tls": False,
            "network": "ws",
            "ws-opts": {"path": "/ws"},
        }]
        groups = [{"name": "Auto-Select", "type": "fallback", "proxies": ["HK-A | CDN"],
                    "url": "http://www.gstatic.com/generate_204", "interval": 300}]
        output = build_mihomo_subscription(nodes, groups, {})
        parsed = yaml.safe_load(output)
        cdn_proxy = parsed["proxies"][0]
        assert "skip-cert-verify" not in cdn_proxy


# ---------------------------------------------------------------------------
# Node deduplication
# ---------------------------------------------------------------------------

class TestDeduplication:
    def test_no_duplicate_names_in_output(self, tmp_path):
        """No duplicate names should appear in subscription output."""
        # Create duplicate client nodes
        client_nodes = [
            {"name": "HK-A | Reality", "type": "vless", "server": "hk-a.test.com",
             "port": 20001, "uuid": "uuid1", "flow": "xtls-rprx-vision", "tls": True,
             "servername": "dl.google.com",
             "reality-opts": {"public-key": "pk", "short-id": "sid"}},
            {"name": "HK-A | Reality", "type": "vless", "server": "hk-a.test.com",
             "port": 20001, "uuid": "uuid1", "flow": "xtls-rprx-vision", "tls": True,
             "servername": "dl.google.com",
             "reality-opts": {"public-key": "pk", "short-id": "sid"}},
        ]
        manifests = [
            {
                "near_node": "hk-gcp-a",
                "nodes": [
                    {"link_id": "a", "route_profile": "near-direct", "egress_mode": "direct",
                     "protocol": "vless-reality", "listen_port": 20001, "transport": "tcp"},
                    {"link_id": "b", "route_profile": "near-direct", "egress_mode": "direct",
                     "protocol": "vless-reality", "listen_port": 20001, "transport": "tcp"},
                ],
            }
        ]
        results = build_all_subscriptions(manifests, client_nodes, {}, tmp_path, formats=["mihomo"])
        parsed = yaml.safe_load(results["mihomo"].read_text())
        names = [p["name"] for p in parsed["proxies"]]
        assert len(names) == len(set(names)), f"Duplicate names found: {names}"
