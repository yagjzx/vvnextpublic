# VVNext

[![CI](https://github.com/yagjzx/vvnextpublic/actions/workflows/ci.yml/badge.svg)](https://github.com/yagjzx/vvnextpublic/actions)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![License: Apache-2.0](https://img.shields.io/badge/License-Apache%202.0-green.svg)](LICENSE)

**Open-source sing-box fleet management CLI for multi-node proxy networks.**

VVNext automates the full lifecycle of a sing-box proxy fleet: provisioning, config generation, deployment, subscription distribution, health monitoring, and security auditing. It targets IT teams at 100--1000 user organizations that need reliable, multi-hop proxy infrastructure with geographic diversity.

## Why VVNext

- **Single source of truth** -- one `inventory.yaml` drives all config generation, deployment, and subscriptions.
- **Multi-hop overlay** -- WireGuard tunnels from near-ingress nodes to far-egress nodes. Client picks a port, that port determines the exit country.
- **4 protocols** -- VLESS+Reality, Hysteria2+Salamander, VLESS+WS+CDN, AnyTLS. Protocol diversity defeats blocking.
- **3 subscription formats** -- Mihomo/Clash, Shadowrocket, sing-box/Hiddify. One rebuild serves all clients.
- **Atomic deploys** -- upload, validate remotely, atomic replace, auto-rollback on failure. No manual `scp` + `systemctl` loops.
- **Zero-downtime operations** -- add/remove nodes, rotate keys, re-render, redeploy. No fleet-wide outage.

## Features

| Category | Details |
|---|---|
| Protocols | VLESS+Reality, Hysteria2+Salamander, VLESS+WS+CDN, AnyTLS |
| Overlay | WireGuard tunnels with persistent IP allocation via `state.yaml` |
| Node roles | Near (ingress: HK/JP/TW), Far (egress: US), Residential (home ISP via Tailscale) |
| Subscriptions | Mihomo, Shadowrocket, sing-box -- with intelligent proxy group classification |
| Deployment | Atomic replace + auto-rollback, max 2 parallel, remote `sing-box check` validation |
| Health checks | TCP/UDP port probes, WG tunnel ping, TLS cert expiry, Telegram alerts with debounce |
| Security audit | Config drift detection, SSH hardening, UFW status, fail2ban, Tailscale SSH check |
| Bootstrap | Provider-aware (GCP/AWS/DO/home), sysctl tuning, sing-box install, firewall setup |
| DNS | Cloudflare API integration or manual mode |
| Init wizard | 13-step pipeline: SSH verify -> bootstrap -> render -> deploy -> subscribe |

## Quick Start

### Install

```bash
pip install vvnext
```

### First-time setup

```bash
# Interactive wizard -- walks through all 13 steps
vvnext init

# Or non-interactive from a config file
vvnext init --config my-fleet.yaml
```

### Verify

```bash
vvnext status           # Fleet overview: nodes, roles, protocols, ports
vvnext health           # TCP/UDP/WG checks on all nodes
vvnext health --detail  # Full check details
```

### Day-2 operations

```bash
vvnext add-node --ip 1.2.3.4 --role near --region jp
vvnext deploy                  # Render + deploy all nodes
vvnext deploy hk-gcp-a         # Deploy specific node
vvnext sub rebuild              # Regenerate all subscription files
vvnext sub server start         # Start HTTPS subscription server
vvnext keys rotate              # Rotate all key materials
vvnext audit                    # Security + config drift audit
```

## CLI Reference

```
vvnext [OPTIONS] COMMAND [ARGS]

Commands:
  init              Interactive fleet setup wizard (--config for non-interactive)
  status            Fleet overview: nodes, roles, regions, protocols, ports
  add-node          Add a node (--ip, --role, --region required)
  remove-node       Remove a node by name
  deploy            Deploy sing-box configs (all nodes or specific targets)
  health            Run health checks (--detail, --telegram)
  sub rebuild       Rebuild subscription files (--format to filter)
  sub server        Start/stop HTTPS subscription server
  audit             Security + config drift audit
  keys generate     Generate key materials for all nodes
  keys rotate       Rotate key materials (--node for specific node)

Options:
  -V, --version     Show version and exit
  -i, --inventory   Path to inventory.yaml (default: config/inventory.yaml)
  -s, --settings    Path to settings.yaml (default: config/settings.yaml)
```

## Architecture Overview

```
                         Clients (Mihomo / Shadowrocket / Hiddify)
                                        |
                     +------------------+------------------+
                     |                  |                  |
                 Near: HK          Near: JP          Near: TW
              (VLESS/HY2/CDN/   (VLESS/HY2/CDN/   (VLESS/HY2/CDN/
               AnyTLS)           AnyTLS)            AnyTLS)
                 |   \               |   \               |
                 |    \              |    \               |
               [WG Overlay]       [WG Overlay]        [WG Overlay]
                 |      \            |      \            |
              Far: US-A  Far: US-B  |   Far: US-A       |
              (exit IP)  (exit IP)  |   (exit IP)       |
                                    |                   |
                              [Residential: US-HOME]
                              (via Tailscale + WG)
```

**Per-Exit Inbound model**: each near node listens on multiple ports. Each port maps to a specific exit via WireGuard overlay. The client's port choice determines which country the traffic exits from.

**Data flow**: Client -> Near node (port N) -> WG tunnel -> Far node -> Internet

## Project Layout

```
config/
  inventory.example.yaml    # Node fleet definition (copy to inventory.yaml)
  settings.example.yaml     # Global settings (copy to settings.yaml)
  routing_rules.yaml        # Domain-based routing rules
src/vvnext/
  cli.py                    # Typer CLI commands
  inventory.py              # Pydantic inventory model + validation
  settings.py               # Global settings model
  state.py                  # Persistent WG IP allocations
  overlay.py                # WireGuard topology computation
  config_generator.py       # sing-box JSON config builder
  deploy.py                 # Atomic deploy with rollback
  health.py                 # TCP/UDP/TLS health checks + Telegram
  audit.py                  # Config drift + security audit
  bootstrap.py              # Provider-aware node provisioning
  keys.py                   # Key material generation
  dns.py                    # DNS record management
  ssh.py                    # SSH client (paramiko wrapper)
  subscription/
    builder.py              # Subscription orchestrator
    classifier.py           # Proxy group bucket classifier
    server.py               # HTTPS subscription server
    formats/
      mihomo.py             # Mihomo/Clash YAML output
      shadowrocket.py       # Shadowrocket base64 output
      singbox.py            # sing-box/Hiddify JSON output
tests/
  unit/                     # 214 unit tests
  integration/              # 4 integration tests (full pipeline)
```

## Configuration

### Inventory (`config/inventory.yaml`)

The single source of truth for your fleet:

```yaml
defaults:
  runtime: singbox
  ssh_user: root

servers:
  - name: hk-gcp-a
    role: near
    region: hk
    provider: gcp
    public_ip: "1.2.3.4"
    port_base: 20000
    sni: "dl.google.com"
    hy2_sni: "hk.example.com"
    cdn_domain: "hk-cdn.example.com"
    dns_name: "hk-a.example.com"
    protocols: [vless_reality, hysteria2, vless_ws_cdn, anytls]
    wg_peers: [us-gcp-a]

  - name: us-gcp-a
    role: far
    region: us
    provider: gcp
    public_ip: "5.6.7.8"
    wg_port: 51941
```

### Settings (`config/settings.yaml`)

Global project configuration:

```yaml
project_name: "MyProxy"
domain: "example.com"

ssh:
  user: "root"
  key_path: "~/.ssh/id_ed25519"

dns:
  provider: "cloudflare"       # or "manual"

subscription:
  port: 8443
  formats: [mihomo, shadowrocket, singbox]

alerting:
  telegram:
    enabled: true
    bot_token: ""              # or set VVNEXT_TG_TOKEN env var
    chat_id: ""
```

## Documentation

- [Architecture](docs/architecture.md) -- system layers, protocol matrix, data flow
- [Quick Start Guide](docs/quickstart.md) -- step-by-step first deployment
- [Advanced Topics](docs/advanced.md) -- residential nodes, monitoring, troubleshooting

## Development

```bash
# Clone and set up
git clone https://github.com/yagjzx/vvnextpublic.git
cd vvnextpublic
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# Run tests (231 tests, ~1s)
pytest

# Run linter
ruff check src/ tests/

# Run with coverage
pytest --cov=vvnext --cov-report=term-missing
```

## Contributing

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/my-feature`
3. Install dev dependencies: `pip install -e ".[dev]"`
4. Run tests: `pytest`
5. Run linter: `ruff check src/ tests/`
6. Submit a pull request

Please follow these guidelines:
- Write tests for new features
- Keep functions focused and under 50 lines where practical
- Use Pydantic models for data validation
- Document public functions with docstrings

## License

Apache-2.0 -- see [LICENSE](LICENSE) for details.
