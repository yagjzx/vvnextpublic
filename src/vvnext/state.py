from __future__ import annotations
import yaml
from pathlib import Path
from pydantic import BaseModel
from typing import Optional

class WgPeerAllocation(BaseModel):
    near_ip: str
    far_ip: str

class WgNodeAllocation(BaseModel):
    wg_port: int
    peers: dict[str, WgPeerAllocation]  # near_name -> allocation

class State(BaseModel):
    wg_allocations: dict[str, WgNodeAllocation] = {}  # far_name -> allocation
    last_deploy: Optional[str] = None
    bootstrap_checkpoints: dict[str, int] = {}  # node_name -> last completed step

def load_state(path: Path) -> State:
    if not path.exists():
        return State()
    data = yaml.safe_load(path.read_text()) or {}
    return State(**data)

def save_state(state: State, path: Path) -> None:
    path.write_text(yaml.dump(state.model_dump(), default_flow_style=False, sort_keys=False))
