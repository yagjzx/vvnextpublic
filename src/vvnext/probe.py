"""Probe remote machines via SSH and infer role/region/provider via GeoIP."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import httpx

from vvnext.ssh import SshClient


@dataclass
class ProbeResult:
    os: str = ""
    arch: str = ""
    mem_mb: int = 0
    disk_gb: int = 0
    hostname: str = ""
    kernel: str = ""


@dataclass
class GeoResult:
    country: str = ""
    country_code: str = ""
    region_name: str = ""
    city: str = ""
    isp: str = ""
    org: str = ""
    as_number: str = ""
    as_name: str = ""
    query_ip: str = ""


# ---------------------------------------------------------------------------
# GeoIP country → region mapping
# ---------------------------------------------------------------------------

_COUNTRY_TO_REGION = {
    "HK": "hk",
    "JP": "jp",
    "TW": "tw",
    "SG": "sg",
    "US": "us",
    "KR": "kr",
    "DE": "de",
    "GB": "gb",
    "NL": "nl",
    "FR": "fr",
}

# Near regions (ingress nodes are placed close to users)
_NEAR_REGIONS = {"hk", "jp", "tw", "sg", "kr"}

# ASN → provider mapping
_ASN_PROVIDER_MAP = {
    "AS15169": "gcp",
    "AS396982": "gcp",
    "AS16509": "aws",
    "AS14618": "aws",
    "AS14061": "do",
    "AS906": "dmit",
    "AS13335": "cloudflare",
    "AS20473": "vultr",
    "AS16276": "ovh",
    "AS24940": "hetzner",
    "AS63949": "linode",
}

# SNI pool for Reality handshake
SNI_POOL = [
    "dl.google.com",
    "www.microsoft.com",
    "www.apple.com",
    "addons.mozilla.org",
    "updates.cdn-apple.com",
    "swdist.apple.com",
    "swcdn.apple.com",
    "mesu.apple.com",
    "xp.apple.com",
    "gdmf.apple.com",
    "www.samsung.com",
    "www.logitech.com",
    "www.asus.com",
    "www.amd.com",
    "www.intel.com",
    "download.docker.com",
    "registry-1.docker.io",
    "production.cloudflare.docker.com",
    "cdn.jsdelivr.net",
]


def probe_machine(ssh: SshClient) -> ProbeResult:
    """Gather OS, arch, memory, disk, hostname from a remote machine."""
    result = ProbeResult()

    out, _, _ = ssh.exec("hostname", check=False)
    result.hostname = out.strip()

    out, _, _ = ssh.exec("uname -s", check=False)
    result.os = out.strip().lower()

    out, _, _ = ssh.exec("uname -m", check=False)
    result.arch = out.strip()

    out, _, _ = ssh.exec("uname -r", check=False)
    result.kernel = out.strip()

    # Memory in MB
    out, _, _ = ssh.exec(
        "awk '/MemTotal/ {printf \"%d\", $2/1024}' /proc/meminfo",
        check=False,
    )
    try:
        result.mem_mb = int(out.strip())
    except ValueError:
        pass

    # Disk in GB (root partition)
    out, _, _ = ssh.exec(
        "df -BG / | awk 'NR==2 {gsub(/G/,\"\",$2); print $2}'",
        check=False,
    )
    try:
        result.disk_gb = int(out.strip())
    except ValueError:
        pass

    return result


def infer_geo(ip: str) -> GeoResult:
    """Query ip-api.com for GeoIP information."""
    result = GeoResult(query_ip=ip)
    try:
        resp = httpx.get(
            f"http://ip-api.com/json/{ip}",
            params={"fields": "country,countryCode,regionName,city,isp,org,as,query"},
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json()
            result.country = data.get("country", "")
            result.country_code = data.get("countryCode", "")
            result.region_name = data.get("regionName", "")
            result.city = data.get("city", "")
            result.isp = data.get("isp", "")
            result.org = data.get("org", "")
            as_field = data.get("as", "")
            if as_field:
                parts = as_field.split(" ", 1)
                result.as_number = parts[0]
                result.as_name = parts[1] if len(parts) > 1 else ""
    except Exception:
        pass
    return result


def infer_role(geo: GeoResult) -> str:
    """Infer node role from GeoIP data. Asian IPs → near, US IPs → far."""
    region = _COUNTRY_TO_REGION.get(geo.country_code, "")
    if region in _NEAR_REGIONS:
        return "near"
    return "far"


def infer_region(geo: GeoResult) -> str:
    """Map country code to VVNext region identifier."""
    return _COUNTRY_TO_REGION.get(geo.country_code, geo.country_code.lower())


def infer_provider(geo: GeoResult) -> str:
    """Infer cloud provider from ASN."""
    return _ASN_PROVIDER_MAP.get(geo.as_number, "unknown")


def detect_nat(ssh: SshClient, public_ip: str) -> bool:
    """Detect if machine is behind NAT by comparing internal vs public IP."""
    out, _, _ = ssh.exec(
        "hostname -I | awk '{print $1}'",
        check=False,
    )
    internal_ip = out.strip()
    if not internal_ip:
        return False
    return internal_ip != public_ip
