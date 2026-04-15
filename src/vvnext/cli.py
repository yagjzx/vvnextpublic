"""VVNext CLI -- sing-box fleet management tool.

Commands:
  init          Interactive fleet setup wizard (or --config for non-interactive)
  status        Fleet overview
  add-node      Add a node to the fleet
  remove-node   Remove a node from the fleet
  deploy        Deploy configs to nodes
  health        Run health checks
  sub           Subscription management (rebuild / server start/stop)
  audit         Security + config drift audit
  keys          Key management (generate / rotate)
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
import yaml

from vvnext import __version__

app = typer.Typer(
    name="vvnext",
    help="sing-box multi-node proxy fleet management tool",
    add_completion=False,
)

# Sub-apps for nested commands
sub_app = typer.Typer(help="Subscription management")
app.add_typer(sub_app, name="sub")

keys_app = typer.Typer(help="Key management")
app.add_typer(keys_app, name="keys")

# --- Default paths ---
DEFAULT_CONFIG_DIR = Path("config")
DEFAULT_INVENTORY = DEFAULT_CONFIG_DIR / "inventory.yaml"
DEFAULT_SETTINGS = DEFAULT_CONFIG_DIR / "settings.yaml"
DEFAULT_STATE = Path("state.yaml")
DEFAULT_RENDERED = Path("rendered")
DEFAULT_MATERIALS = DEFAULT_RENDERED / "materials"
DEFAULT_ROUTING_RULES = DEFAULT_CONFIG_DIR / "routing_rules.yaml"


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"vvnext {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False, "--version", "-V",
        help="Show version and exit.",
        callback=_version_callback,
        is_eager=True,
    ),
) -> None:
    """sing-box multi-node proxy fleet management tool."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_inventory_or_exit(path: Path):
    """Load inventory from YAML file, exit with error if missing or invalid."""
    from vvnext.inventory import load_inventory

    if not path.exists():
        typer.echo(
            typer.style(f"Error: inventory file not found: {path}", fg=typer.colors.RED),
            err=True,
        )
        raise typer.Exit(1)
    try:
        return load_inventory(path)
    except Exception as exc:
        typer.echo(
            typer.style(f"Error loading inventory: {exc}", fg=typer.colors.RED),
            err=True,
        )
        raise typer.Exit(1)


def _load_settings_or_exit(path: Path):
    """Load settings from YAML file. Returns default settings if file missing."""
    from vvnext.settings import load_settings

    try:
        return load_settings(path)
    except Exception as exc:
        typer.echo(
            typer.style(f"Error loading settings: {exc}", fg=typer.colors.RED),
            err=True,
        )
        raise typer.Exit(1)


def _load_state_or_default(path: Path):
    """Load state from YAML file. Returns empty State if missing."""
    from vvnext.state import load_state

    return load_state(path)


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------


@app.command()
def init(
    config: Optional[Path] = typer.Option(
        None, "--config", "-c", help="Config file for non-interactive mode"
    ),
    resume: bool = typer.Option(False, "--resume", help="Resume from checkpoint"),
) -> None:
    """Interactive fleet setup wizard.

    Without --config: interactive questionary-based wizard.
    With --config: non-interactive setup from config file.
    With --resume: resume from last checkpoint.

    13-step pipeline:
    1. Verify SSH connectivity
    2. Probe node environment
    3. Generate inventory.yaml + settings.yaml
    4. Generate key materials
    5. Bootstrap all nodes
    6. Compute WG overlay topology
    7. Render all configs
    8. Deploy configs
    9. Set up DNS
    10. Generate subscriptions
    11. Start subscription server
    12. Health check
    13. Output subscription URLs
    """
    if config is not None:
        # Non-interactive mode
        if not config.exists():
            typer.echo(
                typer.style(f"Error: config file not found: {config}", fg=typer.colors.RED),
                err=True,
            )
            raise typer.Exit(1)

        typer.echo(f"Running init from config: {config}")
        try:
            cfg_data = yaml.safe_load(config.read_text())
        except Exception as exc:
            typer.echo(
                typer.style(f"Error reading config: {exc}", fg=typer.colors.RED),
                err=True,
            )
            raise typer.Exit(1)

        # Load or create checkpoint for resume support
        checkpoint_path = Path("state") / "init_checkpoint.json"
        completed_steps: set[int] = set()
        if resume and checkpoint_path.exists():
            import json as _json
            completed_steps = set(_json.loads(checkpoint_path.read_text()).get("completed", []))
            typer.echo(f"Resuming from checkpoint ({len(completed_steps)} steps done)")

        def _save_checkpoint(step_num: int) -> None:
            checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
            import json as _json
            completed_steps.add(step_num)
            checkpoint_path.write_text(_json.dumps({"completed": sorted(completed_steps)}))

        def _step(num: int, label: str) -> bool:
            """Print step header, return True if should execute (not already done)."""
            if num in completed_steps:
                typer.echo(typer.style(f"[{num}/13] {label} (skipped, already done)", fg=typer.colors.YELLOW))
                return False
            typer.echo(typer.style(f"[{num}/13] {label}...", fg=typer.colors.CYAN))
            return True

        # -- Imports needed across steps --
        from vvnext.inventory import Inventory, ServerEntry, Defaults
        from vvnext.settings import load_settings
        from vvnext.config_generator import (
            build_near_config, build_far_config, build_manifest, build_client_nodes,
        )

        # -- Load essential state so resumed runs have access --
        settings = load_settings(DEFAULT_SETTINGS)

        # If resuming, rebuild inv from saved inventory.yaml; otherwise parse cfg
        if resume and DEFAULT_INVENTORY.exists():
            from vvnext.inventory import load_inventory
            inv = load_inventory(DEFAULT_INVENTORY)
        else:
            inv = None  # Will be built in Step 1

        # Load topo from state if available (needed by steps 8+)
        topo: dict | None = None
        if resume and DEFAULT_STATE.exists():
            from vvnext.state import load_state
            _st = load_state(DEFAULT_STATE)
            topo = getattr(_st, "topo", None) or _st.get("topo", None) if isinstance(_st, dict) else None

        # Load materials if available (needed by steps 8+)
        materials: dict | None = None
        if resume and DEFAULT_MATERIALS.exists():
            from vvnext.keys import generate_all_materials
            if inv is not None:
                materials = generate_all_materials(inv, DEFAULT_MATERIALS)

        # --- Step 1: Parse config and build initial inventory ---
        if _step(1, "Validate config"):
            nodes_cfg = cfg_data.get("nodes", [])
            if not nodes_cfg:
                typer.echo(typer.style("Error: no nodes in config", fg=typer.colors.RED), err=True)
                raise typer.Exit(1)

            servers = []
            for nc in nodes_cfg:
                # Use "pending" phase initially; promoted to "live" in Step 3
                # after resources are allocated (validator skips "pending" nodes)
                phase = nc.get("phase", "pending")
                servers.append(ServerEntry(
                    name=nc.get("name", f"{nc.get('region', 'xx')}-{nc.get('provider', 'unknown')}-a"),
                    role=nc["role"],
                    region=nc["region"],
                    provider=nc.get("provider", "unknown"),
                    public_ip=nc["ip"],
                    sni=nc.get("sni"),
                    port_base=nc.get("port_base"),
                    hy2_sni=nc.get("hy2_sni"),
                    cdn_domain=nc.get("cdn_domain"),
                    dns_name=nc.get("dns_name"),
                    wg_port=nc.get("wg_port"),
                    wg_peers=nc.get("wg_peers"),
                    tailscale_ip=nc.get("tailscale_ip"),
                    ssh_target=nc.get("ssh_target"),
                    nat=nc.get("nat"),
                    phase=phase,
                ))
            inv = Inventory(servers=servers)
            typer.echo(f"  -> {len(servers)} node(s) loaded")
            _save_checkpoint(1)

        # --- Step 2: Verify SSH connectivity ---
        if _step(2, "Verify SSH connectivity"):
            from vvnext.ssh import SshClient
            ssh_key = cfg_data.get("ssh_key", settings.ssh.key_path)
            failed_ssh: list[str] = []
            for node in inv.servers:
                if node.phase not in ("live", "pending"):
                    continue
                host = node.tailscale_ip or node.public_ip
                try:
                    ssh = SshClient(host=host, user=settings.ssh.user, key_path=ssh_key, timeout=settings.ssh.timeout)
                    ssh.connect()
                    ssh.exec("hostname", check=False)
                    ssh.close()
                    typer.echo(typer.style(f"  {node.name} ({host}): OK", fg=typer.colors.GREEN))
                except Exception as exc:
                    typer.echo(typer.style(f"  {node.name} ({host}): FAILED ({exc})", fg=typer.colors.RED))
                    failed_ssh.append(node.name)
            if failed_ssh:
                typer.echo(typer.style(f"  {len(failed_ssh)} node(s) unreachable: {', '.join(failed_ssh)}", fg=typer.colors.RED))
                raise typer.Exit(1)
            _save_checkpoint(2)

        # --- Step 3: Allocate resources for nodes missing them ---
        if _step(3, "Allocate resources"):
            from vvnext.allocator import allocate_port_base, allocate_wg_port, pick_sni
            for node in inv.servers:
                if node.phase not in ("live", "pending"):
                    continue
                if node.role == "near":
                    if node.port_base is None:
                        node.port_base = allocate_port_base(inv)
                        typer.echo(f"  {node.name}: port_base={node.port_base}")
                    if node.sni is None:
                        node.sni = pick_sni(inv)
                        typer.echo(f"  {node.name}: sni={node.sni}")
                    domain = cfg_data.get("domain", "example.com")
                    if node.hy2_sni is None:
                        node.hy2_sni = f"{node.name}.{domain}"
                    if node.cdn_domain is None:
                        node.cdn_domain = f"{node.name}-cdn.{domain}"
                    if node.dns_name is None:
                        node.dns_name = f"{node.name}.{domain}"
                elif node.role in ("far", "residential"):
                    if node.wg_port is None:
                        node.wg_port = allocate_wg_port(inv)
                        typer.echo(f"  {node.name}: wg_port={node.wg_port}")
            # Auto-assign wg_peers if not set: near nodes peer with all far nodes
            far_names = [s.name for s in inv.servers if s.role in ("far", "residential") and s.phase in ("live", "pending")]
            for node in inv.servers:
                if node.role == "near" and node.wg_peers is None and node.phase in ("live", "pending"):
                    node.wg_peers = far_names
                    typer.echo(f"  {node.name}: wg_peers={far_names}")
            # Promote pending nodes to live now that resources are allocated
            for node in inv.servers:
                if node.phase == "pending":
                    node.phase = "live"
            _save_checkpoint(3)

        # --- Step 4: Generate key materials ---
        if _step(4, "Generate key materials"):
            from vvnext.keys import generate_all_materials
            materials = generate_all_materials(inv, DEFAULT_MATERIALS)  # noqa: F841 - used in Step 8
            typer.echo(f"  -> Generated materials: UUID, {len(materials.get('reality', {}))} Reality, {len(materials.get('wg', {}))} WG")
            _save_checkpoint(4)

        # --- Step 5: Write inventory.yaml ---
        if _step(5, "Write inventory.yaml"):
            inv_data = {"defaults": inv.defaults.model_dump(), "servers": [s.model_dump(exclude_none=True) for s in inv.servers]}
            DEFAULT_INVENTORY.parent.mkdir(parents=True, exist_ok=True)
            DEFAULT_INVENTORY.write_text(yaml.dump(inv_data, default_flow_style=False, sort_keys=False))
            typer.echo(f"  -> {DEFAULT_INVENTORY}")
            _save_checkpoint(5)

        # --- Step 6: Bootstrap all nodes ---
        if _step(6, "Bootstrap all nodes"):
            from vvnext.bootstrap import bootstrap_node
            from vvnext.ssh import SshClient
            ssh_key = cfg_data.get("ssh_key", settings.ssh.key_path)
            for node in inv.servers:
                if node.phase != "live":
                    continue
                typer.echo(f"  Bootstrapping {node.name}...")
                host = node.tailscale_ip or node.public_ip
                ssh = SshClient(host=host, user=settings.ssh.user, key_path=ssh_key, timeout=settings.ssh.timeout)
                ssh.connect()
                try:
                    bootstrap_node(ssh, node, settings)
                    typer.echo(typer.style(f"    {node.name}: OK", fg=typer.colors.GREEN))
                except Exception as exc:
                    typer.echo(typer.style(f"    {node.name}: FAILED ({exc})", fg=typer.colors.RED))
                finally:
                    ssh.close()
            _save_checkpoint(6)

        # --- Step 7: Compute WG overlay topology ---
        if _step(7, "Compute WG overlay"):
            from vvnext.overlay import compute_topology
            from vvnext.state import load_state, save_state
            _state = load_state(DEFAULT_STATE)
            topo, _state = compute_topology(inv, _state)  # noqa: F841 - used in Step 8
            save_state(_state, DEFAULT_STATE)
            typer.echo(f"  -> {len(topo)} WG peer pairs")
            _save_checkpoint(7)

        # --- Step 8: Render configs ---
        if _step(8, "Render configs"):
            # Ensure materials and topo are loaded (may have been set in earlier steps or resume preamble)
            if materials is None:
                from vvnext.keys import generate_all_materials
                materials = generate_all_materials(inv, DEFAULT_MATERIALS)
            if topo is None:
                from vvnext.overlay import compute_topology
                from vvnext.state import load_state
                _state = load_state(DEFAULT_STATE)
                topo, _ = compute_topology(inv, _state)
            import json as _json
            for node in inv.servers:
                if node.phase != "live":
                    continue
                out_dir = DEFAULT_RENDERED / node.name
                out_dir.mkdir(parents=True, exist_ok=True)
                if node.role == "near":
                    config_dict = build_near_config(node, inv, topo, materials, inv.defaults)
                    manifest = build_manifest(node, inv, topo, materials, inv.defaults)
                    client_nodes = build_client_nodes(node, inv, topo, materials, inv.defaults)
                    (out_dir / "manifest.json").write_text(_json.dumps(manifest, indent=2))
                    (out_dir / "client_nodes.json").write_text(_json.dumps(client_nodes, indent=2))
                else:
                    config_dict = build_far_config(node, inv, topo, materials, inv.defaults)
                (out_dir / "config.json").write_text(_json.dumps(config_dict, indent=2))
                typer.echo(f"  {node.name}: config.json rendered")
            _save_checkpoint(8)

        # --- Step 9: Deploy configs ---
        if _step(9, "Deploy configs"):
            from vvnext.deploy import deploy_fleet
            import json as _json
            configs: dict[str, dict] = {}
            for node in inv.servers:
                if node.phase != "live":
                    continue
                cp = DEFAULT_RENDERED / node.name / "config.json"
                if cp.exists():
                    configs[node.name] = _json.loads(cp.read_text())
            results = deploy_fleet(inv, configs, settings)
            for name, ok in results.items():
                if ok:
                    typer.echo(typer.style(f"  {name}: OK", fg=typer.colors.GREEN))
                else:
                    typer.echo(typer.style(f"  {name}: FAILED", fg=typer.colors.RED))
            _save_checkpoint(9)

        # --- Step 10: Set up DNS ---
        if _step(10, "Set up DNS"):
            from vvnext.dns import upsert_dns_records, format_manual_instructions, build_dns_plan
            plan = build_dns_plan(inv, settings)
            if settings.dns_provider == "cloudflare" and settings.cf_token:
                results = upsert_dns_records(inv, settings)
                typer.echo(f"  -> {len(results)} DNS record(s) created/updated")
            else:
                typer.echo("  DNS provider not configured. Manual DNS setup required:")
                typer.echo(format_manual_instructions(plan))
            _save_checkpoint(10)

        # --- Step 11: Build subscriptions ---
        if _step(11, "Build subscriptions"):
            from vvnext.subscription.builder import build_all_subscriptions
            import json as _json
            routing_rules: dict = {}
            if DEFAULT_ROUTING_RULES.exists():
                routing_rules = yaml.safe_load(DEFAULT_ROUTING_RULES.read_text()) or {}
            manifests: list[dict] = []
            all_client_nodes: list[dict] = []
            for node in inv.near_nodes():
                mp = DEFAULT_RENDERED / node.name / "manifest.json"
                cnp = DEFAULT_RENDERED / node.name / "client_nodes.json"
                if mp.exists():
                    manifests.append(_json.loads(mp.read_text()))
                if cnp.exists():
                    all_client_nodes.extend(_json.loads(cnp.read_text()))
            sub_dir = DEFAULT_RENDERED / "subscription"
            results = build_all_subscriptions(manifests, all_client_nodes, routing_rules, sub_dir)
            for fmt, path in results.items():
                typer.echo(f"  {fmt}: {path}")
            _save_checkpoint(11)

        # --- Step 12: Health check ---
        if _step(12, "Health check"):
            from vvnext.health import check_fleet
            report = check_fleet(inv, settings, detail=True)
            typer.echo(f"  {report.summary()}")
            for r in report.failed:
                typer.echo(typer.style(f"  [FAIL] {r.node} | {r.check_type} | {r.target}", fg=typer.colors.RED))
            _save_checkpoint(12)

        # --- Step 13: Output subscription URLs ---
        if _step(13, "Output subscription URLs"):
            sub_dir = DEFAULT_RENDERED / "subscription"
            typer.echo("  Subscription files:")
            for f in sorted(sub_dir.iterdir()) if sub_dir.exists() else []:
                typer.echo(f"    {f}")
            sub_settings = settings.subscription
            typer.echo(f"\n  To serve subscriptions: vvnext sub server start")
            typer.echo(f"  Subscription port: {sub_settings.port}")
            _save_checkpoint(13)

        # Clean up checkpoint on full success
        if checkpoint_path.exists():
            checkpoint_path.unlink()
        typer.echo(typer.style("\nInit complete!", fg=typer.colors.GREEN, bold=True))
    else:
        # Interactive mode
        typer.echo(
            "Interactive mode requires the 'questionary' package.\n"
            "For non-interactive setup, use: vvnext init --config <config.yaml>\n\n"
            "Example config file format:\n"
            "  nodes:\n"
            "    - ip: 10.0.0.1\n"
            "      role: near\n"
            "      region: hk\n"
            "    - ip: 10.0.0.2\n"
            "      role: far\n"
            "      region: us\n"
            "  domain: example.com\n"
            "  ssh_key: ~/.ssh/id_ed25519"
        )


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


@app.command()
def status(
    inventory: Path = typer.Option(DEFAULT_INVENTORY, "--inventory", "-i"),
) -> None:
    """Show fleet overview: nodes, roles, regions, protocols, ports."""
    inv = _load_inventory_or_exit(inventory)

    near = inv.near_nodes()
    far = inv.far_nodes()
    all_live = [s for s in inv.servers if s.phase == "live"]

    typer.echo(typer.style("Fleet Status", fg=typer.colors.CYAN, bold=True))
    typer.echo(f"  Nodes: {len(all_live)} live ({len(near)} near, {len(far)} far)")
    typer.echo("")

    # Table header
    header = f"{'Name':<20} {'Role':<12} {'Region':<8} {'Provider':<10} {'IP':<18} {'Ports'}"
    typer.echo(typer.style(header, bold=True))
    typer.echo("-" * len(header))

    for node in all_live:
        ports = _format_ports(node, inv.defaults)
        line = f"{node.name:<20} {node.role:<12} {node.region:<8} {node.provider:<10} {node.public_ip:<18} {ports}"
        typer.echo(line)


def _format_ports(node, defaults) -> str:
    """Format key ports for a node."""
    parts: list[str] = []
    if node.role == "near":
        if node.port_base is not None:
            parts.append(f"reality:{node.port_base + 1}")
        parts.append("hy2:443")
        parts.append(f"cdn:{defaults.near.cdn_port}")
        parts.append(f"anytls:{defaults.near.anytls_port}")
    elif node.wg_port is not None:
        parts.append(f"wg:{node.wg_port}")
    return ", ".join(parts) if parts else "-"


# ---------------------------------------------------------------------------
# add-node
# ---------------------------------------------------------------------------


@app.command()
def add_node(
    ip: str = typer.Option(..., "--ip", help="Node IP address"),
    password: str = typer.Option("", "--password", "-p", help="SSH password"),
    key_path: str = typer.Option("", "--key", "-k", help="SSH key path"),
    role: str = typer.Option("", "--role", help="Override auto-detected role (near/far/residential)"),
    region: str = typer.Option("", "--region", help="Override auto-detected region (hk/jp/tw/us)"),
    provider: str = typer.Option("", "--provider", help="Override auto-detected provider"),
    domain: str = typer.Option("", "--domain", "-d", help="Domain for DNS/cert (e.g. example.com)"),
    resume: bool = typer.Option(False, "--resume", help="Resume from checkpoint"),
    inventory: Path = typer.Option(DEFAULT_INVENTORY, "--inventory", "-i"),
    settings_path: Path = typer.Option(DEFAULT_SETTINGS, "--settings", "-s"),
) -> None:
    """Add a node to the fleet with full automated pipeline.

    13-step process:
    1. SSH probe + GeoIP inference
    2. Confirm role/region/provider
    3. Generate node_id
    4. Allocate resources (port_base/wg_port/SNI)
    5. Generate key materials
    6. Update inventory.yaml
    7. Compute WG topology
    8. Bootstrap remote node
    9. Render + deploy sing-box config
    10. DNS records
    11. Rebuild subscriptions
    12. Health check
    13. Summary
    """
    from vvnext.inventory import Inventory, ServerEntry, load_inventory
    from vvnext.settings import load_settings
    from vvnext.ssh import SshClient

    inv = _load_inventory_or_exit(inventory)
    settings = _load_settings_or_exit(settings_path)

    ssh_user = settings.ssh.user
    ssh_key = key_path if key_path else settings.ssh.key_path
    ssh_pass = password if password else None

    # Checkpoint support
    import json as _json
    checkpoint_dir = Path("state") / ".add_node"
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    cp_path = checkpoint_dir / f"{ip.replace('.', '_')}.json"
    completed_steps: set[int] = set()
    cp_data: dict = {}
    if resume and cp_path.exists():
        cp_data = _json.loads(cp_path.read_text())
        completed_steps = set(cp_data.get("completed", []))
        typer.echo(f"Resuming add-node for {ip} ({len(completed_steps)} steps done)")

    def _save_cp(step: int, extra: dict | None = None) -> None:
        completed_steps.add(step)
        if extra:
            cp_data.update(extra)
        cp_data["completed"] = sorted(completed_steps)
        cp_path.write_text(_json.dumps(cp_data, indent=2))

    def _step(num: int, label: str) -> bool:
        if num in completed_steps:
            typer.echo(typer.style(f"[{num}/13] {label} (skipped)", fg=typer.colors.YELLOW))
            return False
        typer.echo(typer.style(f"[{num}/13] {label}...", fg=typer.colors.CYAN))
        return True

    # --- Step 1: SSH probe + GeoIP ---
    node_id = cp_data.get("node_id", "")
    detected_role = role
    detected_region = region
    detected_provider = provider

    if _step(1, "SSH probe + GeoIP"):
        from vvnext.probe import probe_machine, infer_geo, infer_role, infer_region, infer_provider, detect_nat

        try:
            ssh = SshClient(
                host=ip, user=ssh_user,
                key_path=ssh_key if not ssh_pass else None,
                password=ssh_pass, timeout=settings.ssh.timeout,
            )
            ssh.connect()
            probe = probe_machine(ssh)
            is_nat = detect_nat(ssh, ip)
            ssh.close()
        except Exception as exc:
            typer.echo(typer.style(f"  SSH failed: {exc}", fg=typer.colors.RED), err=True)
            raise typer.Exit(1)

        geo = infer_geo(ip)
        typer.echo(f"  Host: {probe.hostname} | OS: {probe.os} {probe.arch} | Mem: {probe.mem_mb}MB | Disk: {probe.disk_gb}GB")
        typer.echo(f"  GeoIP: {geo.country} ({geo.country_code}) | {geo.city} | ISP: {geo.isp}")
        typer.echo(f"  ASN: {geo.as_number} {geo.as_name} | NAT: {is_nat}")

        if not detected_role:
            detected_role = infer_role(geo)
        if not detected_region:
            detected_region = infer_region(geo)
        if not detected_provider:
            detected_provider = infer_provider(geo)

        _save_cp(1, {
            "role": detected_role, "region": detected_region,
            "provider": detected_provider, "nat": is_nat,
        })
    else:
        detected_role = cp_data.get("role", detected_role)
        detected_region = cp_data.get("region", detected_region)
        detected_provider = cp_data.get("provider", detected_provider)

    # --- Step 2: Confirm role/region/provider ---
    if _step(2, "Confirm role/region/provider"):
        typer.echo(f"  Role: {detected_role} | Region: {detected_region} | Provider: {detected_provider}")
        _save_cp(2)

    # --- Step 3: Generate node_id ---
    if _step(3, "Generate node_id"):
        from vvnext.allocator import generate_node_id
        node_id = generate_node_id(detected_role, detected_region, detected_provider, inv)
        typer.echo(f"  Node ID: {node_id}")
        _save_cp(3, {"node_id": node_id})
    else:
        node_id = cp_data.get("node_id", node_id)

    # --- Step 4: Allocate resources ---
    if _step(4, "Allocate resources"):
        from vvnext.allocator import allocate_near_resources, allocate_far_resources
        if detected_role == "near":
            resources = allocate_near_resources(inv)
            # Derive domain-based fields
            dom = domain or settings.domain or "example.com"
            resources["hy2_sni"] = f"{node_id}.{dom}"
            resources["cdn_domain"] = f"{node_id}-cdn.{dom}"
            resources["dns_name"] = f"{node_id}.{dom}"
        else:
            resources = allocate_far_resources(inv)
        typer.echo(f"  Resources: {resources}")
        _save_cp(4, {"resources": resources})
    else:
        resources = cp_data.get("resources", {})

    # --- Step 5: Generate key materials ---
    if _step(5, "Generate key materials"):
        # Add the new node to inventory (as pending) for key generation
        new_node = ServerEntry(
            name=node_id, role=detected_role, region=detected_region,
            provider=detected_provider, public_ip=ip, phase="pending",
            nat=cp_data.get("nat"),
            **{k: v for k, v in resources.items() if k in ServerEntry.model_fields},
        )
        inv.servers.append(new_node)

        from vvnext.keys import generate_all_materials
        materials = generate_all_materials(inv, DEFAULT_MATERIALS)
        typer.echo(f"  Materials generated for {node_id}")
        _save_cp(5)
    else:
        # Rebuild the node object from checkpoint data for later steps
        new_node = ServerEntry(
            name=node_id, role=detected_role, region=detected_region,
            provider=detected_provider, public_ip=ip, phase="pending",
            nat=cp_data.get("nat"),
            **{k: v for k, v in resources.items() if k in ServerEntry.model_fields},
        )
        if not any(s.name == node_id for s in inv.servers):
            inv.servers.append(new_node)
        from vvnext.keys import generate_all_materials
        materials = generate_all_materials(inv, DEFAULT_MATERIALS)

    # Promote to live and assign wg_peers for near nodes
    new_node.phase = "live"
    if detected_role == "near" and new_node.wg_peers is None:
        new_node.wg_peers = [s.name for s in inv.servers if s.role in ("far", "residential") and s.phase == "live" and s.name != node_id]

    # --- Step 6: Update inventory.yaml ---
    if _step(6, "Update inventory.yaml"):
        inv_data = {
            "defaults": inv.defaults.model_dump(),
            "servers": [s.model_dump(exclude_none=True) for s in inv.servers],
        }
        inventory.parent.mkdir(parents=True, exist_ok=True)
        inventory.write_text(yaml.dump(inv_data, default_flow_style=False, sort_keys=False))
        typer.echo(f"  -> {inventory}")
        _save_cp(6)

    # --- Step 7: Compute WG topology ---
    if _step(7, "Compute WG topology"):
        from vvnext.overlay import compute_topology
        from vvnext.state import load_state, save_state
        state = load_state(DEFAULT_STATE)
        topo, state = compute_topology(inv, state)
        save_state(state, DEFAULT_STATE)
        typer.echo(f"  -> {len(topo)} WG peer pairs")
        _save_cp(7)
    else:
        from vvnext.overlay import compute_topology
        from vvnext.state import load_state
        state = load_state(DEFAULT_STATE)
        topo, _ = compute_topology(inv, state)

    # --- Step 8: Bootstrap remote node ---
    if _step(8, "Bootstrap node"):
        from vvnext.bootstrap import bootstrap_node
        try:
            ssh = SshClient(
                host=ip, user=ssh_user,
                key_path=ssh_key if not ssh_pass else None,
                password=ssh_pass, timeout=settings.ssh.timeout,
            )
            ssh.connect()
            bootstrap_node(ssh, new_node, settings)
            ssh.close()
            typer.echo(typer.style(f"  {node_id}: bootstrapped", fg=typer.colors.GREEN))
        except Exception as exc:
            typer.echo(typer.style(f"  Bootstrap failed: {exc}", fg=typer.colors.RED), err=True)
            raise typer.Exit(1)
        _save_cp(8)

    # --- Step 9: Render + deploy config ---
    if _step(9, "Render + deploy config"):
        from vvnext.config_generator import build_near_config, build_far_config, build_manifest, build_client_nodes
        from vvnext.deploy import deploy_node

        out_dir = DEFAULT_RENDERED / node_id
        out_dir.mkdir(parents=True, exist_ok=True)

        if detected_role == "near":
            config_dict = build_near_config(new_node, inv, topo, materials, inv.defaults)
            manifest = build_manifest(new_node, inv, topo, materials, inv.defaults)
            client_nodes = build_client_nodes(new_node, inv, topo, materials, inv.defaults)
            (out_dir / "manifest.json").write_text(_json.dumps(manifest, indent=2))
            (out_dir / "client_nodes.json").write_text(_json.dumps(client_nodes, indent=2))
        else:
            config_dict = build_far_config(new_node, inv, topo, materials, inv.defaults)

        (out_dir / "config.json").write_text(_json.dumps(config_dict, indent=2))

        # Deploy
        try:
            ssh = SshClient(
                host=ip, user=ssh_user,
                key_path=ssh_key if not ssh_pass else None,
                password=ssh_pass, timeout=settings.ssh.timeout,
            )
            ssh.connect()
            ok = deploy_node(ssh, new_node, config_dict, settings)
            ssh.close()
            if ok:
                typer.echo(typer.style(f"  {node_id}: deployed", fg=typer.colors.GREEN))
            else:
                typer.echo(typer.style(f"  {node_id}: deploy failed (rolled back)", fg=typer.colors.RED))
                raise typer.Exit(1)
        except typer.Exit:
            raise
        except Exception as exc:
            typer.echo(typer.style(f"  Deploy error: {exc}", fg=typer.colors.RED), err=True)
            raise typer.Exit(1)
        _save_cp(9)

    # --- Step 10: DNS records ---
    if _step(10, "DNS records"):
        from vvnext.dns import build_dns_plan, format_manual_instructions
        plan = build_dns_plan(inv, settings)
        if hasattr(settings, "cf_token") and settings.cf_token:
            from vvnext.dns import upsert_dns_records
            results = upsert_dns_records(inv, settings)
            typer.echo(f"  -> {len(results)} DNS record(s) created/updated")
        else:
            typer.echo("  DNS provider not configured. Manual setup required:")
            typer.echo(format_manual_instructions(plan))
        _save_cp(10)

    # --- Step 11: Rebuild subscriptions ---
    if _step(11, "Rebuild subscriptions"):
        from vvnext.subscription.builder import build_all_subscriptions
        routing_rules: dict = {}
        if DEFAULT_ROUTING_RULES.exists():
            routing_rules = yaml.safe_load(DEFAULT_ROUTING_RULES.read_text()) or {}
        manifests: list[dict] = []
        all_client_nodes: list[dict] = []
        for node in inv.near_nodes():
            mp = DEFAULT_RENDERED / node.name / "manifest.json"
            cnp = DEFAULT_RENDERED / node.name / "client_nodes.json"
            if mp.exists():
                manifests.append(_json.loads(mp.read_text()))
            if cnp.exists():
                all_client_nodes.extend(_json.loads(cnp.read_text()))
        sub_dir = DEFAULT_RENDERED / "subscription"
        results = build_all_subscriptions(manifests, all_client_nodes, routing_rules, sub_dir)
        for fmt, path in results.items():
            typer.echo(f"  {fmt}: {path}")
        _save_cp(11)

    # --- Step 12: Health check ---
    if _step(12, "Health check"):
        from vvnext.health import check_fleet
        report = check_fleet(inv, settings, detail=False)
        node_result = next((r for r in report.results if r.node == node_id), None)
        if node_result and node_result.ok:
            typer.echo(typer.style(f"  {node_id}: healthy", fg=typer.colors.GREEN))
        elif node_result:
            typer.echo(typer.style(f"  {node_id}: {node_result.detail}", fg=typer.colors.YELLOW))
        else:
            typer.echo(typer.style(f"  {node_id}: no health data", fg=typer.colors.YELLOW))
        _save_cp(12)

    # --- Step 13: Summary ---
    if _step(13, "Summary"):
        typer.echo(f"  Node: {node_id}")
        typer.echo(f"  Role: {detected_role} | Region: {detected_region} | Provider: {detected_provider}")
        typer.echo(f"  IP: {ip}")
        for k, v in resources.items():
            typer.echo(f"  {k}: {v}")
        _save_cp(13)

    # Clean up checkpoint on full success
    if cp_path.exists():
        cp_path.unlink()
    typer.echo(typer.style(f"\nNode '{node_id}' added successfully!", fg=typer.colors.GREEN, bold=True))


# ---------------------------------------------------------------------------
# batch-add
# ---------------------------------------------------------------------------


@app.command()
def batch_add(
    machines_file: Path = typer.Argument(..., help="YAML file with machine list"),
    domain: str = typer.Option("", "--domain", "-d", help="Domain for DNS/cert"),
    inventory: Path = typer.Option(DEFAULT_INVENTORY, "--inventory", "-i"),
    settings_path: Path = typer.Option(DEFAULT_SETTINGS, "--settings", "-s"),
) -> None:
    """Add multiple nodes from a machines.yaml file.

    machines.yaml format:
      machines:
        - ip: 1.2.3.4
          role: near         # optional, auto-detected
          region: hk         # optional, auto-detected
          provider: gcp      # optional, auto-detected
        - ip: 5.6.7.8

    Pipeline:
    Phase 1: Parallel SSH probe + GeoIP (max 4 concurrent)
    Phase 2: Display plan table, confirm
    Phase 3: Sequential add (far nodes first, then near)
    Phase 4: Rebuild subscriptions once + health check
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from vvnext.inventory import load_inventory
    from vvnext.settings import load_settings
    from vvnext.ssh import SshClient
    from vvnext.probe import probe_machine, infer_geo, infer_role, infer_region, infer_provider, detect_nat
    from vvnext.allocator import (
        generate_node_id, allocate_port_base, allocate_wg_port, pick_sni,
        allocate_near_resources, allocate_far_resources,
    )

    if not machines_file.exists():
        typer.echo(typer.style(f"Error: file not found: {machines_file}", fg=typer.colors.RED), err=True)
        raise typer.Exit(1)

    machines = yaml.safe_load(machines_file.read_text()).get("machines", [])
    if not machines:
        typer.echo(typer.style("Error: no machines in file", fg=typer.colors.RED), err=True)
        raise typer.Exit(1)

    inv = _load_inventory_or_exit(inventory)
    settings = _load_settings_or_exit(settings_path)

    # === Phase 1: Parallel probe ===
    typer.echo(typer.style("Phase 1: Probing machines...", fg=typer.colors.CYAN, bold=True))

    probe_results: dict[str, dict] = {}

    def _probe_one(m: dict) -> tuple[str, dict]:
        ip = m["ip"]
        try:
            ssh = SshClient(
                host=ip, user=settings.ssh.user,
                key_path=settings.ssh.key_path,
                timeout=settings.ssh.timeout,
            )
            ssh.connect()
            probe = probe_machine(ssh)
            is_nat = detect_nat(ssh, ip)
            ssh.close()
            geo = infer_geo(ip)
            r = m.get("role") or infer_role(geo)
            reg = m.get("region") or infer_region(geo)
            prov = m.get("provider") or infer_provider(geo)
            return ip, {
                "ok": True, "role": r, "region": reg, "provider": prov,
                "hostname": probe.hostname, "mem_mb": probe.mem_mb,
                "country": geo.country_code, "city": geo.city, "nat": is_nat,
            }
        except Exception as exc:
            return ip, {"ok": False, "error": str(exc)}

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {executor.submit(_probe_one, m): m for m in machines}
        for future in as_completed(futures):
            ip, result = future.result()
            probe_results[ip] = result
            status = typer.style("OK", fg=typer.colors.GREEN) if result["ok"] else typer.style("FAIL", fg=typer.colors.RED)
            typer.echo(f"  {ip}: {status}")

    # Filter out failed probes
    valid = {ip: r for ip, r in probe_results.items() if r["ok"]}
    failed = {ip: r for ip, r in probe_results.items() if not r["ok"]}

    if failed:
        typer.echo(typer.style(f"\n{len(failed)} machine(s) unreachable:", fg=typer.colors.RED))
        for ip, r in failed.items():
            typer.echo(f"  {ip}: {r.get('error', 'unknown')}")

    if not valid:
        typer.echo(typer.style("Error: no reachable machines", fg=typer.colors.RED), err=True)
        raise typer.Exit(1)

    # === Phase 2: Plan table ===
    typer.echo(typer.style("\nPhase 2: Planned additions", fg=typer.colors.CYAN, bold=True))

    plan: list[dict] = []
    for ip, r in valid.items():
        node_id = generate_node_id(r["role"], r["region"], r["provider"], inv)
        # Temporarily add to inv so next ID generation skips it
        from vvnext.inventory import ServerEntry
        tmp = ServerEntry(
            name=node_id, role=r["role"], region=r["region"],
            provider=r["provider"], public_ip=ip, phase="pending",
        )
        inv.servers.append(tmp)

        entry = {"ip": ip, "node_id": node_id, **r, "node_obj": tmp}
        plan.append(entry)

    # Print plan table
    header = f"{'Node ID':<25} {'Role':<10} {'Region':<8} {'Provider':<10} {'IP':<18} {'Hostname'}"
    typer.echo(header)
    typer.echo("-" * len(header))
    for p in plan:
        typer.echo(f"{p['node_id']:<25} {p['role']:<10} {p['region']:<8} {p['provider']:<10} {p['ip']:<18} {p.get('hostname', '-')}")

    typer.echo(f"\n{len(plan)} node(s) to add.")

    # === Phase 3: Sequential execution (far first, then near) ===
    typer.echo(typer.style("\nPhase 3: Adding nodes...", fg=typer.colors.CYAN, bold=True))

    # Sort: far/residential first, then near (near depends on far for WG peers)
    plan.sort(key=lambda p: 0 if p["role"] in ("far", "residential") else 1)

    dom = domain or settings.domain or "example.com"
    added: list[str] = []
    add_failed: list[str] = []

    for p in plan:
        node_obj = p["node_obj"]
        typer.echo(f"\n--- Adding {p['node_id']} ({p['ip']}) ---")

        # Allocate resources
        if p["role"] == "near":
            resources = allocate_near_resources(inv)
            node_obj.port_base = resources["port_base"]
            node_obj.sni = resources["sni"]
            node_obj.hy2_sni = f"{p['node_id']}.{dom}"
            node_obj.cdn_domain = f"{p['node_id']}-cdn.{dom}"
            node_obj.dns_name = f"{p['node_id']}.{dom}"
            far_names = [s.name for s in inv.servers if s.role in ("far", "residential") and s.phase == "live"]
            node_obj.wg_peers = far_names
        else:
            resources = allocate_far_resources(inv)
            node_obj.wg_port = resources["wg_port"]

        node_obj.phase = "live"
        node_obj.nat = p.get("nat")

        # Generate keys
        from vvnext.keys import generate_all_materials
        materials = generate_all_materials(inv, DEFAULT_MATERIALS)

        # Compute topology
        from vvnext.overlay import compute_topology
        from vvnext.state import load_state, save_state
        state = load_state(DEFAULT_STATE)
        topo, state = compute_topology(inv, state)
        save_state(state, DEFAULT_STATE)

        # Bootstrap + render + deploy
        try:
            ssh = SshClient(
                host=p["ip"], user=settings.ssh.user,
                key_path=settings.ssh.key_path,
                timeout=settings.ssh.timeout,
            )
            ssh.connect()

            from vvnext.bootstrap import bootstrap_node
            bootstrap_node(ssh, node_obj, settings)

            from vvnext.config_generator import build_near_config, build_far_config, build_manifest, build_client_nodes
            from vvnext.deploy import deploy_node
            import json as _json

            out_dir = DEFAULT_RENDERED / p["node_id"]
            out_dir.mkdir(parents=True, exist_ok=True)

            if p["role"] == "near":
                config_dict = build_near_config(node_obj, inv, topo, materials, inv.defaults)
                manifest = build_manifest(node_obj, inv, topo, materials, inv.defaults)
                client_nodes = build_client_nodes(node_obj, inv, topo, materials, inv.defaults)
                (out_dir / "manifest.json").write_text(_json.dumps(manifest, indent=2))
                (out_dir / "client_nodes.json").write_text(_json.dumps(client_nodes, indent=2))
            else:
                config_dict = build_far_config(node_obj, inv, topo, materials, inv.defaults)

            (out_dir / "config.json").write_text(_json.dumps(config_dict, indent=2))

            ok = deploy_node(ssh, node_obj, config_dict, settings)
            ssh.close()

            if ok:
                typer.echo(typer.style(f"  {p['node_id']}: OK", fg=typer.colors.GREEN))
                added.append(p["node_id"])
            else:
                typer.echo(typer.style(f"  {p['node_id']}: deploy failed", fg=typer.colors.RED))
                add_failed.append(p["node_id"])
        except Exception as exc:
            typer.echo(typer.style(f"  {p['node_id']}: FAILED ({exc})", fg=typer.colors.RED))
            add_failed.append(p["node_id"])

    # Save inventory
    inv_data = {
        "defaults": inv.defaults.model_dump(),
        "servers": [s.model_dump(exclude_none=True) for s in inv.servers],
    }
    inventory.write_text(yaml.dump(inv_data, default_flow_style=False, sort_keys=False))

    # === Phase 4: Subscriptions + health ===
    typer.echo(typer.style("\nPhase 4: Rebuild subscriptions + health check", fg=typer.colors.CYAN, bold=True))

    if added:
        from vvnext.subscription.builder import build_all_subscriptions
        import json as _json

        routing_rules: dict = {}
        if DEFAULT_ROUTING_RULES.exists():
            routing_rules = yaml.safe_load(DEFAULT_ROUTING_RULES.read_text()) or {}

        manifests: list[dict] = []
        all_client_nodes: list[dict] = []
        for node in inv.near_nodes():
            mp = DEFAULT_RENDERED / node.name / "manifest.json"
            cnp = DEFAULT_RENDERED / node.name / "client_nodes.json"
            if mp.exists():
                manifests.append(_json.loads(mp.read_text()))
            if cnp.exists():
                all_client_nodes.extend(_json.loads(cnp.read_text()))

        if manifests:
            sub_dir = DEFAULT_RENDERED / "subscription"
            build_all_subscriptions(manifests, all_client_nodes, routing_rules, sub_dir)
            typer.echo("  Subscriptions rebuilt")

        from vvnext.health import check_fleet
        report = check_fleet(inv, settings, detail=False)
        typer.echo(f"  {report.summary()}")

    # Summary
    typer.echo(f"\nBatch complete: {len(added)} added, {len(add_failed)} failed")
    if add_failed:
        typer.echo(typer.style(f"  Failed: {', '.join(add_failed)}", fg=typer.colors.RED))
        raise typer.Exit(1)


# ---------------------------------------------------------------------------
# remove-node
# ---------------------------------------------------------------------------


@app.command()
def remove_node(
    name: str = typer.Argument(..., help="Node name to remove"),
    inventory: Path = typer.Option(DEFAULT_INVENTORY, "--inventory", "-i"),
) -> None:
    """Remove a node from the fleet."""
    inv = _load_inventory_or_exit(inventory)

    # Verify node exists
    found = any(s.name == name for s in inv.servers)
    if not found:
        typer.echo(
            typer.style(f"Error: node '{name}' not found in inventory", fg=typer.colors.RED),
            err=True,
        )
        raise typer.Exit(1)

    # Remove from inventory file
    try:
        inv_data = yaml.safe_load(inventory.read_text())
        inv_data["servers"] = [s for s in inv_data["servers"] if s.get("name") != name]
        inventory.write_text(yaml.dump(inv_data, default_flow_style=False, sort_keys=False))
        typer.echo(typer.style(f"Node '{name}' removed from {inventory}", fg=typer.colors.GREEN))
    except Exception as exc:
        typer.echo(
            typer.style(f"Error updating inventory: {exc}", fg=typer.colors.RED),
            err=True,
        )
        raise typer.Exit(1)


# ---------------------------------------------------------------------------
# deploy
# ---------------------------------------------------------------------------


@app.command()
def deploy(
    targets: Optional[list[str]] = typer.Argument(None, help="Node names to deploy (default: all)"),
    inventory: Path = typer.Option(DEFAULT_INVENTORY, "--inventory", "-i"),
    settings_path: Path = typer.Option(DEFAULT_SETTINGS, "--settings", "-s"),
) -> None:
    """Deploy sing-box configs to nodes.

    Reads rendered configs from the rendered/ directory and deploys them
    to the specified nodes (or all nodes if none specified).
    """
    inv = _load_inventory_or_exit(inventory)
    settings = _load_settings_or_exit(settings_path)

    from vvnext.deploy import deploy_fleet

    # Load rendered configs
    rendered_dir = DEFAULT_RENDERED
    if not rendered_dir.exists():
        typer.echo(
            typer.style(
                f"Error: rendered config directory not found: {rendered_dir}\n"
                "Run 'vvnext init' or render configs first.",
                fg=typer.colors.RED,
            ),
            err=True,
        )
        raise typer.Exit(1)

    import json

    configs: dict[str, dict] = {}
    all_live = [s for s in inv.servers if s.phase == "live"]
    target_names = targets if targets else [s.name for s in all_live]

    for name in target_names:
        config_path = rendered_dir / name / "config.json"
        if not config_path.exists():
            typer.echo(
                typer.style(f"Warning: no rendered config for {name} at {config_path}", fg=typer.colors.YELLOW),
            )
            continue
        try:
            configs[name] = json.loads(config_path.read_text())
        except Exception as exc:
            typer.echo(
                typer.style(f"Error reading config for {name}: {exc}", fg=typer.colors.RED),
                err=True,
            )
            continue

    if not configs:
        typer.echo(
            typer.style("Error: no configs to deploy", fg=typer.colors.RED),
            err=True,
        )
        raise typer.Exit(1)

    typer.echo(f"Deploying to {len(configs)} node(s): {', '.join(configs.keys())}")
    try:
        results = deploy_fleet(inv, configs, settings, targets=list(configs.keys()))
    except Exception as exc:
        typer.echo(
            typer.style(f"Deploy failed: {exc}", fg=typer.colors.RED),
            err=True,
        )
        raise typer.Exit(1)

    # Report results
    success_count = sum(1 for v in results.values() if v)
    fail_count = sum(1 for v in results.values() if not v)

    for name, ok in results.items():
        if ok:
            typer.echo(typer.style(f"  {name}: OK", fg=typer.colors.GREEN))
        else:
            typer.echo(typer.style(f"  {name}: FAILED", fg=typer.colors.RED))

    typer.echo("")
    typer.echo(f"Deploy complete: {success_count} succeeded, {fail_count} failed")
    if fail_count > 0:
        raise typer.Exit(1)


# ---------------------------------------------------------------------------
# health
# ---------------------------------------------------------------------------


@app.command()
def health(
    detail: bool = typer.Option(False, "--detail", "-d"),
    telegram: bool = typer.Option(False, "--telegram", "-t"),
    inventory: Path = typer.Option(DEFAULT_INVENTORY, "--inventory", "-i"),
    settings_path: Path = typer.Option(DEFAULT_SETTINGS, "--settings", "-s"),
) -> None:
    """Run health checks on all nodes."""
    inv = _load_inventory_or_exit(inventory)
    settings = _load_settings_or_exit(settings_path)

    from vvnext.health import check_fleet, send_telegram_alert

    typer.echo("Running health checks...")
    try:
        report = check_fleet(inv, settings, detail=detail)
    except Exception as exc:
        typer.echo(
            typer.style(f"Health check failed: {exc}", fg=typer.colors.RED),
            err=True,
        )
        raise typer.Exit(1)

    # Print summary
    typer.echo(f"\n{report.summary()}")

    if detail or not report.all_ok:
        for r in report.results:
            if r.ok:
                icon = typer.style("[OK]", fg=typer.colors.GREEN)
            else:
                icon = typer.style("[FAIL]", fg=typer.colors.RED)
            typer.echo(f"  {icon} {r.node} | {r.check_type} | {r.target} | {r.detail}")

    if report.all_ok:
        typer.echo(typer.style("\nAll checks passed.", fg=typer.colors.GREEN))
    else:
        typer.echo(
            typer.style(f"\n{len(report.failed)} check(s) failed.", fg=typer.colors.RED)
        )

    # Send Telegram alert if requested
    if telegram:
        typer.echo("Sending Telegram alert...")
        sent = send_telegram_alert(report, settings)
        if sent:
            typer.echo(typer.style("  Telegram alert sent.", fg=typer.colors.GREEN))
        else:
            typer.echo(
                typer.style("  Telegram alert not sent (check config).", fg=typer.colors.YELLOW)
            )

    if not report.all_ok:
        raise typer.Exit(1)


# ---------------------------------------------------------------------------
# sub rebuild
# ---------------------------------------------------------------------------


@sub_app.command("rebuild")
def sub_rebuild(
    formats: Optional[list[str]] = typer.Option(
        None, "--format", "-f", help="Formats: mihomo, shadowrocket, singbox"
    ),
    inventory: Path = typer.Option(DEFAULT_INVENTORY, "--inventory", "-i"),
    settings_path: Path = typer.Option(DEFAULT_SETTINGS, "--settings", "-s"),
) -> None:
    """Rebuild subscription files."""
    inv = _load_inventory_or_exit(inventory)
    _load_settings_or_exit(settings_path)

    from vvnext.subscription.builder import build_all_subscriptions

    output_dir = DEFAULT_RENDERED / "subscription"
    routing_rules_path = DEFAULT_ROUTING_RULES

    # Load routing rules if available
    routing_rules: dict = {}
    if routing_rules_path.exists():
        try:
            routing_rules = yaml.safe_load(routing_rules_path.read_text()) or {}
        except Exception as exc:
            typer.echo(
                typer.style(f"Warning: cannot read routing rules: {exc}", fg=typer.colors.YELLOW),
            )

    # Build manifests and client nodes from rendered configs
    import json

    manifests: list[dict] = []
    all_client_nodes: list[dict] = []

    rendered_dir = DEFAULT_RENDERED
    for node in inv.near_nodes():
        manifest_path = rendered_dir / node.name / "manifest.json"
        client_nodes_path = rendered_dir / node.name / "client_nodes.json"
        if manifest_path.exists():
            try:
                manifests.append(json.loads(manifest_path.read_text()))
            except Exception:
                pass
        if client_nodes_path.exists():
            try:
                all_client_nodes.extend(json.loads(client_nodes_path.read_text()))
            except Exception:
                pass

    if not manifests:
        typer.echo(
            typer.style(
                "Error: no manifests found in rendered/. Run render or deploy first.",
                fg=typer.colors.RED,
            ),
            err=True,
        )
        raise typer.Exit(1)

    fmt_list = formats if formats else None
    typer.echo(f"Building subscriptions (formats: {fmt_list or 'all'})...")
    try:
        results = build_all_subscriptions(
            manifests=manifests,
            all_client_nodes=all_client_nodes,
            routing_rules=routing_rules,
            output_dir=output_dir,
            formats=fmt_list,
        )
    except Exception as exc:
        typer.echo(
            typer.style(f"Subscription build failed: {exc}", fg=typer.colors.RED),
            err=True,
        )
        raise typer.Exit(1)

    for fmt, path in results.items():
        typer.echo(typer.style(f"  {fmt}: {path}", fg=typer.colors.GREEN))
    typer.echo(typer.style("Subscription rebuild complete.", fg=typer.colors.GREEN))


# ---------------------------------------------------------------------------
# sub server
# ---------------------------------------------------------------------------


@sub_app.command("server")
def sub_server(
    action: str = typer.Argument(..., help="start or stop"),
    settings_path: Path = typer.Option(DEFAULT_SETTINGS, "--settings", "-s"),
) -> None:
    """Start or stop the subscription HTTPS server."""
    if action not in ("start", "stop"):
        typer.echo(
            typer.style(f"Error: action must be 'start' or 'stop', got '{action}'", fg=typer.colors.RED),
            err=True,
        )
        raise typer.Exit(1)

    settings = _load_settings_or_exit(settings_path)

    from vvnext.subscription.server import SubscriptionServer

    sub_dir = DEFAULT_RENDERED / "subscription"
    sub_settings = settings.subscription

    if action == "start":
        if not sub_dir.exists():
            typer.echo(
                typer.style(
                    f"Error: subscription directory not found: {sub_dir}\n"
                    "Run 'vvnext sub rebuild' first.",
                    fg=typer.colors.RED,
                ),
                err=True,
            )
            raise typer.Exit(1)

        typer.echo(f"Starting subscription server on port {sub_settings.port}...")
        server = SubscriptionServer(
            directory=sub_dir,
            port=sub_settings.port,
            tls_cert=sub_settings.tls_cert,
            tls_key=sub_settings.tls_key,
            token=sub_settings.token,
        )
        try:
            server.start()
            typer.echo(typer.style("Subscription server started.", fg=typer.colors.GREEN))
            typer.echo("Press Ctrl+C to stop.")
            # Block until interrupted
            import signal

            def _handle_signal(sig, frame):
                server.stop()
                typer.echo("\nServer stopped.")
                raise typer.Exit(0)

            signal.signal(signal.SIGINT, _handle_signal)
            signal.signal(signal.SIGTERM, _handle_signal)
            # Keep the main thread alive
            import time

            while server.is_running:
                time.sleep(1)
        except Exception as exc:
            typer.echo(
                typer.style(f"Server failed: {exc}", fg=typer.colors.RED),
                err=True,
            )
            raise typer.Exit(1)

    elif action == "stop":
        typer.echo(
            "To stop the server, press Ctrl+C in the terminal running it,\n"
            "or use: kill $(pgrep -f 'vvnext sub server start')"
        )


# ---------------------------------------------------------------------------
# audit
# ---------------------------------------------------------------------------


@app.command()
def audit(
    inventory: Path = typer.Option(DEFAULT_INVENTORY, "--inventory", "-i"),
    settings_path: Path = typer.Option(DEFAULT_SETTINGS, "--settings", "-s"),
) -> None:
    """Run security + config drift audit."""
    inv = _load_inventory_or_exit(inventory)
    settings = _load_settings_or_exit(settings_path)

    from vvnext.audit import audit_fleet

    typer.echo("Running fleet audit...")
    try:
        report = audit_fleet(inv, settings)
    except Exception as exc:
        typer.echo(
            typer.style(f"Audit failed: {exc}", fg=typer.colors.RED),
            err=True,
        )
        raise typer.Exit(1)

    typer.echo(f"\nAudit: {report.summary()}")

    for finding in report.findings:
        if finding.severity == "critical":
            sev = typer.style("[CRITICAL]", fg=typer.colors.RED, bold=True)
        elif finding.severity == "warning":
            sev = typer.style("[WARNING]", fg=typer.colors.YELLOW)
        else:
            sev = typer.style("[INFO]", fg=typer.colors.CYAN)
        typer.echo(f"  {sev} {finding.node} | {finding.category} | {finding.message}")
        if finding.detail:
            typer.echo(f"         {finding.detail}")

    if report.critical_count > 0:
        raise typer.Exit(1)


# ---------------------------------------------------------------------------
# keys generate
# ---------------------------------------------------------------------------


@keys_app.command("generate")
def keys_generate(
    inventory: Path = typer.Option(DEFAULT_INVENTORY, "--inventory", "-i"),
) -> None:
    """Generate key materials for all nodes."""
    inv = _load_inventory_or_exit(inventory)

    from vvnext.keys import generate_all_materials

    materials_dir = DEFAULT_MATERIALS
    typer.echo(f"Generating key materials in {materials_dir}...")
    try:
        materials = generate_all_materials(inv, materials_dir)
    except Exception as exc:
        typer.echo(
            typer.style(f"Key generation failed: {exc}", fg=typer.colors.RED),
            err=True,
        )
        raise typer.Exit(1)

    typer.echo(typer.style("Key materials generated:", fg=typer.colors.GREEN))
    if "vless_uuid" in materials:
        typer.echo(f"  VLESS UUID: {materials['vless_uuid'][:8]}...")
    if "reality" in materials:
        typer.echo(f"  Reality keypairs: {len(materials['reality'])} node(s)")
    if "wg" in materials:
        typer.echo(f"  WG keypairs: {len(materials['wg'])} node(s)")


# ---------------------------------------------------------------------------
# keys rotate
# ---------------------------------------------------------------------------


@keys_app.command("rotate")
def keys_rotate(
    node: str = typer.Option("", "--node", "-n", help="Specific node to rotate"),
    inventory: Path = typer.Option(DEFAULT_INVENTORY, "--inventory", "-i"),
) -> None:
    """Rotate key materials.

    Regenerates key materials. If --node is specified, only rotates for that node.
    After rotation, you must re-render configs and redeploy.
    """
    inv = _load_inventory_or_exit(inventory)

    import shutil

    materials_dir = DEFAULT_MATERIALS

    if node:
        # Rotate for a specific node
        node_dir = materials_dir / node
        if node_dir.exists():
            shutil.rmtree(node_dir)
            typer.echo(f"Removed old materials for {node}")
        else:
            typer.echo(f"No existing materials for {node}")
    else:
        # Rotate all per-node materials (keep shared secrets)
        for srv in inv.servers:
            node_dir = materials_dir / srv.name
            if node_dir.exists():
                shutil.rmtree(node_dir)
        typer.echo("Removed all per-node key materials")

    # Regenerate
    from vvnext.keys import generate_all_materials

    typer.echo("Regenerating key materials...")
    try:
        generate_all_materials(inv, materials_dir)
    except Exception as exc:
        typer.echo(
            typer.style(f"Key rotation failed: {exc}", fg=typer.colors.RED),
            err=True,
        )
        raise typer.Exit(1)

    typer.echo(typer.style("Key materials rotated.", fg=typer.colors.GREEN))
    typer.echo("Remember to re-render configs and redeploy.")


# ---------------------------------------------------------------------------
# monitor
# ---------------------------------------------------------------------------


@app.command()
def monitor(
    once: bool = typer.Option(False, "--once", help="Collect once and exit"),
    interval: int = typer.Option(300, "--interval", help="Collection interval in seconds"),
    targets: Optional[list[str]] = typer.Argument(None, help="Node names (default: all)"),
    inventory: Path = typer.Option(DEFAULT_INVENTORY, "--inventory", "-i"),
    settings_path: Path = typer.Option(DEFAULT_SETTINGS, "--settings", "-s"),
) -> None:
    """Collect monitoring metrics from fleet nodes.

    --once: single collection, print table and exit.
    --interval N: collect every N seconds (default 300), push to InfluxDB if configured.
    """
    inv = _load_inventory_or_exit(inventory)
    settings = _load_settings_or_exit(settings_path)

    from vvnext.collector import collect_fleet, format_metrics_table, push_to_influxdb

    import time as _time

    target_list = list(targets) if targets else None

    while True:
        typer.echo(f"Collecting metrics ({_time.strftime('%H:%M:%S')})...")
        metrics = collect_fleet(inv, settings, targets=target_list)
        typer.echo(format_metrics_table(metrics))

        # Push to InfluxDB if configured
        influx = settings.monitoring.influxdb
        if influx.enabled and influx.url:
            ok = push_to_influxdb(metrics, settings)
            if ok:
                typer.echo(typer.style("  -> InfluxDB: OK", fg=typer.colors.GREEN))
            else:
                typer.echo(typer.style("  -> InfluxDB: FAILED", fg=typer.colors.RED))

        if once:
            break

        typer.echo(f"\nNext collection in {interval}s...")
        _time.sleep(interval)
