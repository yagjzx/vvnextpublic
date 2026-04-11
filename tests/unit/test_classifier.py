import pytest
from vvnext.subscription.classifier import classify_nodes, build_proxy_groups


def _make_entry(near_node, protocol, egress_mode, route_profile, name=""):
    return {
        "near_node": near_node,
        "protocol": protocol,
        "egress_mode": egress_mode,
        "route_profile": route_profile,
        "name": name or f"{near_node}-{protocol}",
    }


class TestClassifyBasic:
    def test_classify_hk_direct_reality(self):
        entries = [
            _make_entry("hk-gcp-a", "vless-reality", "direct", "near-direct", "HK-A Direct | Reality"),
        ]
        buckets = classify_nodes(entries)
        assert len(buckets["hk_direct"]) == 1
        assert buckets["hk_direct"][0]["name"] == "HK-A Direct | Reality"

    def test_classify_hk_cdn(self):
        entries = [
            _make_entry("hk-gcp-a", "vless-ws-cdn", "direct", "near-direct", "HK-A | CDN"),
        ]
        buckets = classify_nodes(entries)
        assert len(buckets["hk_cdn"]) == 1

    def test_classify_jp_hy2(self):
        entries = [
            _make_entry("jp-gcp-a", "hysteria2", "direct", "near-direct", "JP-A | HY2"),
        ]
        buckets = classify_nodes(entries)
        assert len(buckets["jp_hy2"]) == 1

    def test_classify_multiple_nodes(self):
        entries = [
            _make_entry("hk-gcp-a", "vless-reality", "direct", "near-direct", "HK-A Direct"),
            _make_entry("hk-gcp-a", "hysteria2", "direct", "near-direct", "HK-A HY2"),
            _make_entry("jp-gcp-a", "vless-ws-cdn", "direct", "near-direct", "JP-A CDN"),
        ]
        buckets = classify_nodes(entries)
        assert len(buckets["hk_direct"]) == 1
        assert len(buckets["hk_hy2"]) == 1
        assert len(buckets["jp_cdn"]) == 1


class TestClassifyEmpty:
    def test_empty_input(self):
        buckets = classify_nodes([])
        for bucket_name, entries in buckets.items():
            assert entries == [], f"Bucket {bucket_name} should be empty"


class TestClassifyOverlayVsDirect:
    def test_overlay_goes_to_us_overlay(self):
        entries = [
            _make_entry("hk-gcp-a", "vless-reality", "overlay", "us-general", "HK-A -> US-A | Reality"),
        ]
        buckets = classify_nodes(entries)
        assert len(buckets["us_overlay"]) == 1
        assert len(buckets["hk_direct"]) == 0

    def test_direct_goes_to_region_direct(self):
        entries = [
            _make_entry("hk-gcp-a", "vless-reality", "direct", "near-direct", "HK-A Direct | Reality"),
        ]
        buckets = classify_nodes(entries)
        assert len(buckets["hk_direct"]) == 1
        assert len(buckets["us_overlay"]) == 0


class TestClassifyAnytls:
    def test_anytls_region_specific(self):
        entries = [
            _make_entry("hk-gcp-a", "anytls", "direct", "near-direct", "HK-A | AnyTLS"),
            _make_entry("jp-gcp-a", "anytls", "direct", "near-direct", "JP-A | AnyTLS"),
        ]
        buckets = classify_nodes(entries)
        assert len(buckets["anytls_hk"]) == 1
        assert len(buckets["anytls_jp"]) == 1
        assert len(buckets["anytls_tw"]) == 0
        # Computed
        assert len(buckets["anytls_all"]) == 2


class TestClassifyUsOverlayAll:
    def test_us_overlay_all_mirrors_us_overlay(self):
        entries = [
            _make_entry("hk-gcp-a", "vless-reality", "overlay", "us-general", "HK-A -> US-A"),
            _make_entry("jp-gcp-a", "vless-reality", "overlay", "us-general", "JP-A -> US-A"),
        ]
        buckets = classify_nodes(entries)
        assert len(buckets["us_overlay"]) == 2
        assert len(buckets["us_overlay_all"]) == 2


class TestProxyGroups:
    def test_group_names(self):
        entries = [
            _make_entry("hk-gcp-a", "vless-reality", "overlay", "us-general", "HK-A -> US-A | Reality"),
            _make_entry("hk-gcp-a", "vless-reality", "direct", "near-direct", "HK-A Direct | Reality"),
            _make_entry("hk-gcp-a", "hysteria2", "direct", "near-direct", "HK-A | HY2"),
            _make_entry("hk-gcp-a", "anytls", "direct", "near-direct", "HK-A | AnyTLS"),
        ]
        buckets = classify_nodes(entries)
        groups = build_proxy_groups(buckets, {})
        group_names = {g["name"] for g in groups}
        expected = {
            "Auto-Select", "AI", "Streaming-US", "Streaming-HK",
            "US-Exit", "HK-Exit", "JP-Exit", "TW-Exit",
            "Residential", "AnyTLS", "HY2-Fallback", "Direct",
        }
        assert group_names == expected

    def test_group_members(self):
        entries = [
            _make_entry("hk-gcp-a", "vless-reality", "overlay", "us-general", "HK-A -> US-A"),
            _make_entry("hk-gcp-a", "vless-reality", "direct", "near-direct", "HK-A Direct"),
        ]
        buckets = classify_nodes(entries)
        groups = build_proxy_groups(buckets, {})
        hk_exit = next(g for g in groups if g["name"] == "HK-Exit")
        assert "HK-A Direct" in hk_exit["proxies"]

    def test_empty_groups_fallback(self):
        buckets = classify_nodes([])
        groups = build_proxy_groups(buckets, {})
        # All groups should have at least one proxy (fallback to Auto-Select or DIRECT)
        for g in groups:
            assert len(g["proxies"]) > 0
