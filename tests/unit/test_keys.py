import pytest, json, uuid, re, base64
from unittest.mock import patch, MagicMock
from vvnext.keys import generate_reality_keypair, generate_wg_keypair, generate_uuid, generate_hy2_secrets, generate_anytls_password, generate_all_materials
from pathlib import Path

def test_reality_keypair_format():
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            stdout='PrivateKey: ABC123privatekey\nPublicKey: DEF456publickey\nShortID: ec3c3be4\n',
            returncode=0
        )
        kp = generate_reality_keypair()
        assert "private_key" in kp
        assert "public_key" in kp
        assert "short_id" in kp

def test_wg_keypair_format():
    kp = generate_wg_keypair()
    assert "private_key" in kp
    assert "public_key" in kp
    # WG keys are base64, 44 chars
    assert len(kp["private_key"]) == 44
    assert len(kp["public_key"]) == 44

def test_uuid_format():
    u = generate_uuid()
    uuid.UUID(u)  # raises if invalid

def test_hy2_secrets():
    s = generate_hy2_secrets()
    assert "password" in s
    assert "obfs_password" in s
    assert len(s["password"]) >= 16
    assert len(s["obfs_password"]) >= 16

def test_anytls_password():
    p = generate_anytls_password()
    assert len(p) >= 24
