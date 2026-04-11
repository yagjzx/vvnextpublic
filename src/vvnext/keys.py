from __future__ import annotations
import json
import secrets
import subprocess
import uuid
from pathlib import Path
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives import serialization
import base64

def generate_reality_keypair() -> dict[str, str]:
    result = subprocess.run(
        ["sing-box", "generate", "reality-keypair"],
        capture_output=True, text=True, check=True
    )
    lines = result.stdout.strip().split("\n")
    kp = {}
    for line in lines:
        if "PrivateKey:" in line:
            kp["private_key"] = line.split(":", 1)[1].strip()
        elif "PublicKey:" in line:
            kp["public_key"] = line.split(":", 1)[1].strip()
    kp["short_id"] = secrets.token_hex(4)
    return kp

def generate_wg_keypair() -> dict[str, str]:
    private_key = X25519PrivateKey.generate()
    private_bytes = private_key.private_bytes(
        serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption()
    )
    public_bytes = private_key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    return {
        "private_key": base64.b64encode(private_bytes).decode(),
        "public_key": base64.b64encode(public_bytes).decode(),
    }

def generate_uuid() -> str:
    return str(uuid.uuid4())

def generate_hy2_secrets() -> dict[str, str]:
    return {
        "password": secrets.token_urlsafe(24),
        "obfs_password": secrets.token_urlsafe(24),
    }

def generate_anytls_password() -> str:
    return secrets.token_urlsafe(32)

def generate_all_materials(inventory, materials_dir: Path) -> dict:
    """Generate all key materials for the fleet. Returns materials dict."""
    materials_dir.mkdir(parents=True, exist_ok=True)
    materials = {}
    # Shared secrets
    uuid_path = materials_dir / "vless-uuid.txt"
    if not uuid_path.exists():
        uuid_path.write_text(generate_uuid())
    materials["vless_uuid"] = uuid_path.read_text().strip()

    anytls_path = materials_dir / "anytls-password.txt"
    if not anytls_path.exists():
        anytls_path.write_text(generate_anytls_password())
    materials["anytls_password"] = anytls_path.read_text().strip()

    hy2_path = materials_dir / "hy2-secrets.yaml"
    if not hy2_path.exists():
        import yaml
        hy2_path.write_text(yaml.dump(generate_hy2_secrets()))
    import yaml
    materials["hy2"] = yaml.safe_load(hy2_path.read_text())

    # Per-node keys
    for node in inventory.servers:
        if node.phase != "live":
            continue
        node_dir = materials_dir / node.name
        node_dir.mkdir(exist_ok=True)
        # Reality keypair (near nodes only)
        if node.role == "near":
            reality_path = node_dir / "reality-keypair.json"
            if not reality_path.exists():
                reality_path.write_text(json.dumps(generate_reality_keypair()))
            materials.setdefault("reality", {})[node.name] = json.loads(reality_path.read_text())
        # WG keypair (far + residential nodes)
        if node.role in ("far", "residential") and node.wg_port:
            wg_path = node_dir / "wg-keypair.json"
            if not wg_path.exists():
                wg_path.write_text(json.dumps(generate_wg_keypair()))
            materials.setdefault("wg", {})[node.name] = json.loads(wg_path.read_text())

    return materials
