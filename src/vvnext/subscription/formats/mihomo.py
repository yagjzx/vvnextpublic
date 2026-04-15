"""Mihomo/Clash YAML subscription builder.

CRITICAL: Go yaml.v3 float workaround -- values like '1e10', '3e4', 'inf',
'nan', '1.0', '.5' must be quoted to prevent Go's yaml.v3 from parsing them
as floats. Uses custom YAML dumper with _GO_YAML_FLOAT_RE pattern.

Domain rules are inlined (not rule-providers) because MetaCubeX doesn't
support rule-providers on routers/MerlinClash.
"""
from __future__ import annotations

import re

import yaml


# ---------------------------------------------------------------------------
# Go yaml.v3 float detection
# ---------------------------------------------------------------------------

_GO_YAML_FLOAT_RE = re.compile(
    r"^[-+]?(\.[0-9]+|[0-9]+(\.[0-9]*)?)([eE][-+]?[0-9]+)?$"
    r"|^[-+]?(\.inf|\.Inf|\.INF)$"
    r"|^(\.nan|\.NaN|\.NAN)$"
)

_PURE_INT_RE = re.compile(r"^[-+]?[0-9]+$")


def _needs_quoting(value: str) -> bool:
    """Return True if a string value would be misinterpreted by Go yaml.v3."""
    if not isinstance(value, str):
        return False
    if _GO_YAML_FLOAT_RE.match(value) and not _PURE_INT_RE.match(value):
        return True
    return False


# ---------------------------------------------------------------------------
# Custom YAML dumper
# ---------------------------------------------------------------------------

class _GoSafeDumper(yaml.SafeDumper):
    """YAML dumper that quotes strings matching Go yaml.v3 float patterns."""


def _go_safe_str_representer(dumper: yaml.Dumper, data: str) -> yaml.Node:
    """Force quoting for strings that Go yaml.v3 would parse as floats."""
    if _needs_quoting(data):
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="'")
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


_GoSafeDumper.add_representer(str, _go_safe_str_representer)


def dump_mihomo_yaml(data: dict) -> str:
    """Custom YAML dumper that quotes Go yaml.v3 problematic strings."""
    return yaml.dump(
        data,
        Dumper=_GoSafeDumper,
        default_flow_style=False,
        allow_unicode=True,
        sort_keys=False,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_mihomo_subscription(
    client_nodes: list[dict],
    proxy_groups: list[dict],
    routing_rules: dict,
) -> str:
    """Build complete Mihomo/Clash YAML subscription.

    Domain rules are inlined (not rule-providers) because MetaCubeX
    doesn't support rule-providers on routers/MerlinClash.
    """
    config: dict = {
        "port": 7890,
        "socks-port": 7891,
        "allow-lan": True,
        "mode": "rule",
        "log-level": "info",
        "dns": {
            "enable": True,
            "enhanced-mode": "fake-ip",
            "fake-ip-range": "198.18.0.1/16",
            "nameserver": [
                "https://dns.google/dns-query",
                "https://cloudflare-dns.com/dns-query",
            ],
            "fake-ip-filter": [
                "*.lan",
                "*.local",
                "*.localhost",
                "time.*.com",
                "time.*.gov",
                "time.*.edu.cn",
                "time.*.apple.com",
                "time-ios.apple.com",
                "time-macos.apple.com",
                "ntp.*.com",
                "*.ntp.org.cn",
                "stun.*.*",
                "stun.*.*.*",
                "+.stun.*.*",
                "+.stun.*.*.*",
                "*.msftconnecttest.com",
                "*.msftncsi.com",
                "localhost.ptlogin2.qq.com",
                "*.pool.ntp.org",
            ],
        },
        "proxies": client_nodes,
        "proxy-groups": proxy_groups,
        "rules": _build_rules(routing_rules),
    }
    return dump_mihomo_yaml(config)


# ---------------------------------------------------------------------------
# Internal: build rules
# ---------------------------------------------------------------------------

_GROUP_MAP = {
    "ai_residential": "AI",
    "streaming_us": "Streaming-US",
    "streaming_hk": "Streaming-HK",
    "us_exclusive": "US-Exit",
    "gaming": "Auto-Select",
    "social": "Auto-Select",
    "download": "Auto-Select",
    "direct_cn": "DIRECT",
}


def _build_rules(routing_rules: dict) -> list[str]:
    """Convert routing_rules.yaml into inline Clash rules.

    Domain rules are inlined (not rule-providers) because MetaCubeX
    doesn't support rule-providers on routers/MerlinClash.
    """
    rules: list[str] = []

    server_routing = routing_rules.get("server_routing", {})

    for rule_name, rule_def in server_routing.items():
        domains = rule_def.get("domains", [])
        action = rule_def.get("action", "")

        if action == "direct":
            for domain in domains:
                rules.append(f"DOMAIN-SUFFIX,{domain},DIRECT")
            continue

        group = _GROUP_MAP.get(rule_name, "Auto-Select")
        for domain in domains:
            rules.append(f"DOMAIN-SUFFIX,{domain},{group}")

    # LAN / private IP ranges — always direct
    rules.extend([
        "IP-CIDR,127.0.0.0/8,DIRECT,no-resolve",
        "IP-CIDR,10.0.0.0/8,DIRECT,no-resolve",
        "IP-CIDR,172.16.0.0/12,DIRECT,no-resolve",
        "IP-CIDR,192.168.0.0/16,DIRECT,no-resolve",
        "IP-CIDR,100.64.0.0/10,DIRECT,no-resolve",
        "IP-CIDR,198.18.0.0/15,DIRECT,no-resolve",
        "IP-CIDR6,::1/128,DIRECT,no-resolve",
        "IP-CIDR6,fc00::/7,DIRECT,no-resolve",
        "IP-CIDR6,fe80::/10,DIRECT,no-resolve",
    ])

    # GeoIP and final match
    rules.append("GEOIP,CN,DIRECT")
    rules.append("MATCH,Auto-Select")

    return rules
