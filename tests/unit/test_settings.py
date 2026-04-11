import pytest
import os
from pathlib import Path
from vvnext.settings import Settings, load_settings

def test_default_settings():
    s = Settings()
    assert s.project_name == "VVNext"
    assert s.ssh.user == "root"
    assert s.protocols.vless_reality is True

def test_env_override_cf_token(monkeypatch):
    monkeypatch.setenv("VVNEXT_CF_TOKEN", "test-token-123")
    s = Settings()
    assert s.dns.cf_api_token == "test-token-123"

def test_env_override_tg_token(monkeypatch):
    monkeypatch.setenv("VVNEXT_TG_TOKEN", "tg-bot-token")
    s = Settings()
    assert s.alerting.telegram.bot_token == "tg-bot-token"

def test_load_settings_missing_file(tmp_path):
    s = load_settings(tmp_path / "nonexistent.yaml")
    assert s.project_name == "VVNext"

def test_load_settings_from_file(tmp_path):
    f = tmp_path / "settings.yaml"
    f.write_text('project_name: "TestProxy"\ndomain: "test.com"\n')
    s = load_settings(f)
    assert s.project_name == "TestProxy"
    assert s.domain == "test.com"

def test_load_settings_partial(tmp_path):
    f = tmp_path / "settings.yaml"
    f.write_text('ssh:\n  user: "simba"\n')
    s = load_settings(f)
    assert s.ssh.user == "simba"
    assert s.project_name == "VVNext"  # default preserved
