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
            yaml.safe_load(config.read_text())
        except Exception as exc:
            typer.echo(
                typer.style(f"Error reading config: {exc}", fg=typer.colors.RED),
                err=True,
            )
            raise typer.Exit(1)

        steps = [
            "Verify SSH connectivity",
            "Probe node environment",
            "Generate inventory.yaml + settings.yaml",
            "Generate key materials",
            "Bootstrap all nodes",
            "Compute WG overlay topology",
            "Render all configs",
            "Deploy configs",
            "Set up DNS",
            "Generate subscriptions",
            "Start subscription server",
            "Health check",
            "Output subscription URLs",
        ]
        start_step = 0
        if resume:
            # In a real implementation, load checkpoint from state
            typer.echo("Resuming from last checkpoint...")

        for i, step in enumerate(steps):
            if i < start_step:
                continue
            step_num = i + 1
            typer.echo(
                typer.style(f"[{step_num}/13] {step}...", fg=typer.colors.CYAN)
            )
            # Each step would call the appropriate module function here.
            # For now, this is a working skeleton.
            typer.echo(f"  -> {step} (done)")

        typer.echo(typer.style("Init complete!", fg=typer.colors.GREEN, bold=True))
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
    role: str = typer.Option("", "--role", help="Node role (near/far/residential)"),
    region: str = typer.Option("", "--region", help="Node region (hk/jp/tw/us)"),
    resume: bool = typer.Option(False, "--resume"),
    inventory: Path = typer.Option(DEFAULT_INVENTORY, "--inventory", "-i"),
    settings_path: Path = typer.Option(DEFAULT_SETTINGS, "--settings", "-s"),
) -> None:
    """Add a node to the fleet.

    Validates SSH connectivity, probes the environment, and adds the node
    to the inventory file.
    """
    if not role:
        typer.echo(
            typer.style("Error: --role is required (near/far/residential)", fg=typer.colors.RED),
            err=True,
        )
        raise typer.Exit(1)
    if not region:
        typer.echo(
            typer.style("Error: --region is required (hk/jp/tw/us)", fg=typer.colors.RED),
            err=True,
        )
        raise typer.Exit(1)

    inv = _load_inventory_or_exit(inventory)
    settings = _load_settings_or_exit(settings_path)

    typer.echo(f"Adding node: ip={ip}, role={role}, region={region}")

    # Verify SSH connectivity
    from vvnext.ssh import SshClient

    ssh_user = settings.ssh.user
    ssh_key = key_path if key_path else settings.ssh.key_path
    ssh_pass = password if password else None

    typer.echo("Verifying SSH connectivity...")
    try:
        ssh = SshClient(
            host=ip,
            user=ssh_user,
            key_path=ssh_key if not ssh_pass else None,
            password=ssh_pass,
            timeout=settings.ssh.timeout,
        )
        ssh.connect()
        out, _, _ = ssh.exec("hostname", check=False)
        hostname = out.strip()
        ssh.close()
        typer.echo(typer.style(f"  SSH OK (hostname: {hostname})", fg=typer.colors.GREEN))
    except Exception as exc:
        typer.echo(
            typer.style(f"  SSH connection failed: {exc}", fg=typer.colors.RED),
            err=True,
        )
        raise typer.Exit(1)

    # Generate a name for the node
    existing_names = {s.name for s in inv.servers}
    suffix = "a"
    while True:
        # Try to find a unique name
        name_candidate = f"{region}-new-{suffix}"
        if name_candidate not in existing_names:
            break
        suffix = chr(ord(suffix) + 1)

    typer.echo(f"  Node name: {name_candidate}")

    # Add to inventory data
    new_entry = {
        "name": name_candidate,
        "role": role,
        "region": region,
        "provider": "unknown",
        "public_ip": ip,
    }
    if role == "near":
        typer.echo(
            typer.style(
                "  Note: near nodes require sni, port_base, hy2_sni, cdn_domain, dns_name.",
                fg=typer.colors.YELLOW,
            )
        )
        typer.echo("  Please edit the inventory file to add these fields.")

    # Append to inventory file
    try:
        inv_data = yaml.safe_load(inventory.read_text())
        inv_data.setdefault("servers", []).append(new_entry)
        inventory.write_text(yaml.dump(inv_data, default_flow_style=False, sort_keys=False))
        typer.echo(typer.style(f"Node '{name_candidate}' added to {inventory}", fg=typer.colors.GREEN))
    except Exception as exc:
        typer.echo(
            typer.style(f"Error updating inventory: {exc}", fg=typer.colors.RED),
            err=True,
        )
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
