import pytest
from unittest.mock import patch, MagicMock
import httpx

from vvnext.inventory import Inventory
from vvnext.settings import Settings
from vvnext.dns import (
    build_dns_plan,
    format_manual_instructions,
    upsert_dns_records,
    _get_zone_id,
    _cf_headers,
)


@pytest.fixture
def dns_inventory():
    return Inventory(servers=[
        {"name": "hk-gcp-a", "role": "near", "region": "hk", "provider": "gcp",
         "public_ip": "1.2.3.4", "port_base": 20000, "sni": "dl.google.com",
         "hy2_sni": "hk.example.com", "cdn_domain": "hk-cdn.example.com",
         "dns_name": "hk-a.example.com", "wg_peers": ["us-gcp-a"]},
        {"name": "jp-gcp-a", "role": "near", "region": "jp", "provider": "gcp",
         "public_ip": "5.6.7.8", "port_base": 21000, "sni": "dl.google.com",
         "hy2_sni": "jp.example.com", "cdn_domain": "jp-cdn.example.com",
         "dns_name": "jp-a.example.com", "wg_peers": ["us-gcp-a"]},
        {"name": "us-gcp-a", "role": "far", "region": "us", "provider": "gcp",
         "public_ip": "9.10.11.12", "wg_port": 51941},
    ])


@pytest.fixture
def dns_settings():
    return Settings(domain="example.com", dns={"provider": "manual"})


@pytest.fixture
def cf_settings():
    return Settings(
        domain="example.com",
        dns={"provider": "cloudflare", "cf_api_token": "test-token-abc"},
    )


def test_build_dns_plan(dns_inventory, dns_settings):
    """Verify correct records for near nodes."""
    plan = build_dns_plan(dns_inventory, dns_settings)
    # 2 near nodes * 2 records each (dns_name + cdn_domain) = 4 records
    assert len(plan) == 4
    names = [r["name"] for r in plan]
    assert "hk-a.example.com" in names
    assert "hk-cdn.example.com" in names
    assert "jp-a.example.com" in names
    assert "jp-cdn.example.com" in names
    # Far nodes should NOT appear
    for r in plan:
        assert r["node"] != "us-gcp-a"


def test_dns_plan_cdn_proxied(dns_inventory, dns_settings):
    """cdn_domain records should have proxied=True."""
    plan = build_dns_plan(dns_inventory, dns_settings)
    cdn_records = [r for r in plan if "cdn" in r["name"]]
    assert len(cdn_records) == 2
    for r in cdn_records:
        assert r["proxied"] is True


def test_dns_plan_dns_name_not_proxied(dns_inventory, dns_settings):
    """dns_name records should have proxied=False."""
    plan = build_dns_plan(dns_inventory, dns_settings)
    dns_records = [r for r in plan if "cdn" not in r["name"]]
    assert len(dns_records) == 2
    for r in dns_records:
        assert r["proxied"] is False


def test_format_manual_instructions(dns_inventory, dns_settings):
    """Human-readable output format."""
    plan = build_dns_plan(dns_inventory, dns_settings)
    output = format_manual_instructions(plan)
    assert "DNS Records to configure:" in output
    assert "hk-a.example.com" in output
    assert "1.2.3.4" in output
    assert "No (Direct)" in output
    assert "Yes (CDN)" in output
    assert "hk-gcp-a" in output


def test_format_manual_instructions_empty():
    """Empty plan produces appropriate message."""
    output = format_manual_instructions([])
    assert output == "No DNS records to configure."


def test_upsert_manual_mode(dns_inventory, dns_settings):
    """provider=manual -> no API calls, returns plan."""
    results = upsert_dns_records(dns_inventory, dns_settings)
    assert len(results) == 4
    for r in results:
        assert r["action"] == "manual"
        assert "name" in r
        assert "type" in r
        assert "content" in r


def test_upsert_cf_create_new(dns_inventory, cf_settings):
    """Mock httpx, verify POST for new records."""
    zone_resp = MagicMock()
    zone_resp.json.return_value = {"result": [{"id": "zone-123"}]}
    zone_resp.raise_for_status = MagicMock()

    list_resp = MagicMock()
    list_resp.json.return_value = {"result": []}  # No existing records
    list_resp.raise_for_status = MagicMock()

    create_resp = MagicMock()
    create_resp.json.return_value = {"result": {"id": "rec-new"}}
    create_resp.raise_for_status = MagicMock()

    with patch("vvnext.dns.httpx") as mock_httpx:
        mock_httpx.get = MagicMock(side_effect=[zone_resp] + [list_resp] * 4)
        mock_httpx.post = MagicMock(return_value=create_resp)

        results = upsert_dns_records(dns_inventory, cf_settings)

    assert len(results) == 4
    for r in results:
        assert r["action"] == "created"
    # 4 records created via POST
    assert mock_httpx.post.call_count == 4


def test_upsert_cf_update_existing(dns_inventory, cf_settings):
    """Mock httpx, verify PUT for existing records."""
    zone_resp = MagicMock()
    zone_resp.json.return_value = {"result": [{"id": "zone-123"}]}
    zone_resp.raise_for_status = MagicMock()

    # Existing records found
    list_resp = MagicMock()
    list_resp.json.return_value = {"result": [{"id": "existing-rec-1"}]}
    list_resp.raise_for_status = MagicMock()

    update_resp = MagicMock()
    update_resp.json.return_value = {"result": {"id": "existing-rec-1"}}
    update_resp.raise_for_status = MagicMock()

    with patch("vvnext.dns.httpx") as mock_httpx:
        mock_httpx.get = MagicMock(side_effect=[zone_resp] + [list_resp] * 4)
        mock_httpx.put = MagicMock(return_value=update_resp)

        results = upsert_dns_records(dns_inventory, cf_settings)

    assert len(results) == 4
    for r in results:
        assert r["action"] == "updated"
    # 4 records updated via PUT
    assert mock_httpx.put.call_count == 4


def test_upsert_cf_missing_token(dns_inventory):
    """Cloudflare mode without token raises ValueError."""
    settings = Settings(
        domain="example.com",
        dns={"provider": "cloudflare", "cf_api_token": ""},
    )
    with pytest.raises(ValueError, match="API token not configured"):
        upsert_dns_records(dns_inventory, settings)


def test_upsert_cf_missing_domain(dns_inventory):
    """Cloudflare mode without domain raises ValueError."""
    settings = Settings(
        domain="",
        dns={"provider": "cloudflare", "cf_api_token": "some-token"},
    )
    with pytest.raises(ValueError, match="Domain not configured"):
        upsert_dns_records(dns_inventory, settings)
