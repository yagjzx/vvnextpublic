from __future__ import annotations
import yaml
from pathlib import Path
from pydantic import BaseModel, model_validator
from typing import Optional

class WgDefaults(BaseModel):
    mtu: int = 1380
    persistent_keepalive_seconds: int = 25
    subnet_base: str = "10.240.10"

class NearDefaults(BaseModel):
    protocols: list[str] = ["vless-reality", "hysteria2", "vless-ws-cdn", "anytls"]
    hy2_obfs: str = "salamander"
    cdn_port: int = 2053
    anytls_port: int = 8443

class FarDefaults(BaseModel):
    protocols: list[str] = ["wireguard"]

class Defaults(BaseModel):
    runtime: str = "singbox"
    services: list[str] = ["sing-box"]
    ssh_user: str = "root"
    near: NearDefaults = NearDefaults()
    far: FarDefaults = FarDefaults()
    wg: WgDefaults = WgDefaults()

class ServerEntry(BaseModel):
    name: str
    role: str  # "near" | "far" | "residential"
    region: str
    provider: str
    public_ip: str
    phase: str = "live"
    # Near-specific
    sni: Optional[str] = None
    port_base: Optional[int] = None
    hy2_sni: Optional[str] = None
    cdn_domain: Optional[str] = None
    dns_name: Optional[str] = None
    wg_peers: Optional[list[str]] = None
    protocols: Optional[list[str]] = None
    # Far-specific
    wg_port: Optional[int] = None
    # Residential-specific
    tailscale_ip: Optional[str] = None
    ssh_target: Optional[str] = None  # "tailscale" | None
    nat: Optional[bool] = None
    access: Optional[str] = None

    @model_validator(mode="after")
    def validate_role_fields(self) -> "ServerEntry":
        if self.phase in ("future", "pending"):
            return self
        if self.role == "near":
            for f in ("sni", "port_base", "hy2_sni", "cdn_domain", "dns_name"):
                if getattr(self, f) is None:
                    raise ValueError(f"near node '{self.name}' missing required field: {f}")
        elif self.role in ("far", "residential"):
            if self.role == "residential" and not self.tailscale_ip:
                raise ValueError(f"residential node '{self.name}' missing tailscale_ip")
            protos = self.protocols or []
            if "remote-socks" not in protos and self.wg_port is None:
                raise ValueError(f"far/residential node '{self.name}' missing wg_port")
        return self

class Inventory(BaseModel):
    version: Optional[str] = None
    purpose: Optional[str] = None
    defaults: Defaults = Defaults()
    servers: list[ServerEntry]

    @model_validator(mode="after")
    def validate_cross_references(self) -> "Inventory":
        live = [s for s in self.servers if s.phase == "live"]
        names = {s.name for s in self.servers}
        # port_base uniqueness
        bases = [s.port_base for s in live if s.port_base is not None]
        if len(bases) != len(set(bases)):
            raise ValueError("Duplicate port_base values found")
        # wg_port uniqueness
        wg_ports = [s.wg_port for s in live if s.wg_port is not None]
        if len(wg_ports) != len(set(wg_ports)):
            raise ValueError("Duplicate wg_port values found")
        # wg_peers references
        for s in live:
            if s.wg_peers:
                for peer in s.wg_peers:
                    if peer not in names:
                        raise ValueError(f"Node '{s.name}' wg_peer '{peer}' not in inventory")
        return self

    def near_nodes(self) -> list[ServerEntry]:
        return [s for s in self.servers if s.role == "near" and s.phase == "live"]

    def far_nodes(self) -> list[ServerEntry]:
        return [s for s in self.servers if s.role in ("far", "residential") and s.phase == "live"]

    def get_node(self, name: str) -> ServerEntry:
        for s in self.servers:
            if s.name == name:
                return s
        raise KeyError(f"Node '{name}' not found")

    def get_ssh_target(self, node: ServerEntry, defaults: Defaults) -> str:
        if node.tailscale_ip:
            return f"{defaults.ssh_user}@{node.tailscale_ip}"
        return f"{defaults.ssh_user}@{node.public_ip}"

def load_inventory(path: Path) -> Inventory:
    data = yaml.safe_load(path.read_text())
    return Inventory(**data)

def node_short_label(name: str) -> str:
    parts = name.split("-")
    if len(parts) < 2:
        return name.upper()
    region = parts[0].upper()
    provider = parts[1]
    suffix = "-".join(parts[2:]).upper() if len(parts) > 2 else ""
    if provider == "gcp":
        return f"{region}-{suffix}" if suffix else region
    elif provider == "dmit":
        return f"{region}-DMIT-{suffix}" if suffix else f"{region}-DMIT"
    elif provider in ("home", "residential"):
        return f"{region}-{suffix}" if suffix else f"{region}-HOME"
    else:
        return f"{region}-{'-'.join(parts[1:]).upper()}"
