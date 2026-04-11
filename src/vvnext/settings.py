from __future__ import annotations
import os, yaml
from pathlib import Path
from pydantic import BaseModel, model_validator
from typing import Optional

class SshSettings(BaseModel):
    user: str = "root"
    key_path: str = "~/.ssh/id_ed25519"
    timeout: int = 30

class DnsSettings(BaseModel):
    provider: str = "manual"  # "cloudflare" | "manual"
    cf_api_token: str = ""

class SubscriptionSettings(BaseModel):
    port: int = 8443
    tls_cert: str = ""
    tls_key: str = ""
    formats: list[str] = ["mihomo", "shadowrocket", "singbox"]
    token: str = ""

class TelegramSettings(BaseModel):
    enabled: bool = False
    bot_token: str = ""
    chat_id: str = ""

class AlertingSettings(BaseModel):
    telegram: TelegramSettings = TelegramSettings()

class ProtocolSettings(BaseModel):
    vless_reality: bool = True
    hysteria2: bool = True
    vless_ws_cdn: bool = True
    anytls: bool = True

class BootstrapSettings(BaseModel):
    mirror: str = ""  # sing-box download mirror URL

class InfluxSettings(BaseModel):
    enabled: bool = False
    url: str = ""
    org: str = ""
    bucket: str = ""

class MonitoringSettings(BaseModel):
    influxdb: InfluxSettings = InfluxSettings()

class Settings(BaseModel):
    project_name: str = "VVNext"
    domain: str = ""
    ssh: SshSettings = SshSettings()
    dns: DnsSettings = DnsSettings()
    subscription: SubscriptionSettings = SubscriptionSettings()
    alerting: AlertingSettings = AlertingSettings()
    protocols: ProtocolSettings = ProtocolSettings()
    bootstrap: BootstrapSettings = BootstrapSettings()
    monitoring: MonitoringSettings = MonitoringSettings()

    @model_validator(mode="after")
    def apply_env_overrides(self) -> "Settings":
        token = os.environ.get("VVNEXT_CF_TOKEN")
        if token:
            self.dns.cf_api_token = token
        tg_token = os.environ.get("VVNEXT_TG_TOKEN")
        if tg_token:
            self.alerting.telegram.bot_token = tg_token
        return self

def load_settings(path: Path) -> Settings:
    if not path.exists():
        return Settings()
    data = yaml.safe_load(path.read_text()) or {}
    return Settings(**data)
