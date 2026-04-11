# Quick Start Guide

This guide walks through deploying your first VVNext proxy fleet from scratch.

## Prerequisites

- **Python 3.10+** on your workstation (macOS, Linux, or WSL)
- **SSH key** (`~/.ssh/id_ed25519` or similar)
- **At least 2 VPS**: one near-ingress (HK/JP/TW), one far-egress (US)
- **A domain** with DNS you control (Cloudflare recommended)
- **Root SSH access** to all VPS nodes

### Recommended VPS providers

| Role | Provider | Notes |
|---|---|---|
| Near (ingress) | GCP, DMIT, Akile | Low latency to users |
| Far (egress) | GCP, AWS, DigitalOcean | US IPs for exit |
| Residential | Home ISP + Tailscale | AI service access |

### Hardware requirements (per node)

- CPU: 1 vCPU
- RAM: 512 MB minimum, 1 GB recommended
- Disk: 10 GB
- Network: 1 Gbps or better

## Step 1: Install VVNext

```bash
pip install vvnext
```

Verify the installation:

```bash
vvnext --version
# vvnext 0.1.0
```

## Step 2: Prepare DNS Records

Create DNS A records for each near node. You will need:

| Record | Points to | Purpose |
|---|---|---|
| `hk-a.example.com` | Near node IP | Reality/HY2/AnyTLS connections |
| `hk.example.com` | Near node IP | HY2 ACME cert domain |
| `hk-cdn.example.com` | Near node IP (CF proxied) | CDN-fronted VLESS |

If using Cloudflare:
- `hk-a.example.com` -- DNS only (gray cloud), no proxy
- `hk.example.com` -- DNS only (gray cloud), for ACME
- `hk-cdn.example.com` -- Proxied (orange cloud), for CDN

## Step 3: Run the Init Wizard

### Interactive mode

```bash
vvnext init
```

The wizard runs a 13-step pipeline:

#### Step 1: Verify SSH connectivity

The wizard tests SSH access to every node you provide. It uses your configured SSH key or prompts for credentials.

```
[1/13] Verify SSH connectivity...
  -> hk-gcp-a: SSH OK (hostname: hk-instance-1)
  -> us-gcp-a: SSH OK (hostname: us-instance-1)
```

#### Step 2: Probe node environment

Detects OS version, available tools, and provider-specific configuration (GCP metadata, etc.).

#### Step 3: Generate inventory.yaml + settings.yaml

Based on your inputs, VVNext generates the inventory and settings files:

- `config/inventory.yaml` -- node definitions, protocols, WG peers
- `config/settings.yaml` -- SSH settings, domain, DNS provider, subscription config

#### Step 4: Generate key materials

Generates all cryptographic materials:

- VLESS UUID (shared across all nodes)
- Reality X25519 keypairs (one per near node)
- WireGuard X25519 keypairs (one per node)
- HY2 password + obfuscation password
- AnyTLS password

Materials are stored in `rendered/materials/` and excluded from git.

#### Step 5: Bootstrap all nodes

Provisions each VPS:

- Creates `simba` service user
- Installs system packages and sing-box
- Tunes sysctl (BBR, forwarding, conntrack)
- Sets up UFW firewall (except GCP)
- Installs fail2ban
- Generates self-signed certs

#### Step 6: Compute WG overlay topology

Calculates WireGuard tunnel endpoints and allocates `/30` IP blocks:

```
  hk-gcp-a <-> us-gcp-a: 10.240.10.2 / 10.240.10.1
```

Allocations are saved to `state.yaml` for persistence.

#### Step 7: Render all configs

Generates sing-box JSON configs for every node:

- Near nodes: Reality inbounds (overlay + direct), HY2, CDN, AnyTLS, WG outbounds
- Far nodes: WG inbound with all near peer entries

Output: `rendered/<node-name>/config.json`

#### Step 8: Deploy configs

For each node:

1. Upload config to `/tmp/config.json`
2. Run `sing-box check -c /tmp/config.json` (remote validation)
3. Backup current config to `.bak`
4. Atomic replace: `mv /tmp/config.json /etc/sing-box/config.json`
5. `systemctl restart sing-box`
6. Wait 5 seconds for WG tunnel warm-up
7. Verify `systemctl is-active sing-box`
8. Auto-rollback from `.bak` if verification fails

#### Step 9: Set up DNS

Creates or updates DNS records via Cloudflare API (or reports manual records needed).

#### Step 10: Generate subscriptions

Builds subscription files in 3 formats:

- `rendered/subscription/mihomo.yaml` -- for Mihomo/Clash clients
- `rendered/subscription/shadowrocket.txt` -- for Shadowrocket (base64)
- `rendered/subscription/singbox.json` -- for sing-box/Hiddify clients

#### Step 11: Start subscription server

Starts an HTTPS server to serve subscription files to client apps.

#### Step 12: Health check

Runs TCP/UDP port probes and WG tunnel pings on all nodes.

#### Step 13: Output subscription URLs

Prints the subscription URLs to import into client apps.

### Non-interactive mode

For automation or repeatable deployments:

```bash
# Create a config file
cat > my-fleet.yaml << 'EOF'
nodes:
  - ip: 1.2.3.4
    role: near
    region: hk
  - ip: 5.6.7.8
    role: far
    region: us
domain: example.com
ssh_key: ~/.ssh/id_ed25519
EOF

# Run init with the config file
vvnext init --config my-fleet.yaml
```

To resume from a failed step:

```bash
vvnext init --config my-fleet.yaml --resume
```

## Step 4: Verify Your Fleet

### Check fleet status

```bash
vvnext status
```

Expected output:

```
Fleet Status
  Nodes: 2 live (1 near, 1 far)

Name                 Role         Region   Provider   IP                 Ports
-------------------------------------------------------------------------------------
hk-gcp-a             near         hk       gcp        1.2.3.4            reality:20001, hy2:443, cdn:2053, anytls:8443
us-gcp-a              far          us       gcp        5.6.7.8            wg:51941
```

### Run health checks

```bash
vvnext health --detail
```

Expected output:

```
Running health checks...

7/7 checks passed
  [OK] hk-gcp-a | tcp | 1.2.3.4:20001 | port open
  [OK] hk-gcp-a | tcp | 1.2.3.4:20002 | port open
  [OK] hk-gcp-a | udp | 1.2.3.4:443 | no response (normal for UDP)
  [OK] hk-gcp-a | tcp | 1.2.3.4:2053 | port open
  [OK] hk-gcp-a | tcp | 1.2.3.4:8443 | port open
  [OK] hk-gcp-a | tcp | 1.2.3.4:8444 | port open
  [OK] us-gcp-a | udp | 5.6.7.8:51941 | no response (normal for UDP)

All checks passed.
```

### Import subscription on a client

1. Copy the subscription URL from the init output
2. Open your client app (Mihomo, Shadowrocket, or Hiddify)
3. Add the subscription URL
4. Select a proxy (e.g., "HK -> US | Reality")
5. Test connectivity

## Step 5: Day-2 Operations

### Add a new node

```bash
vvnext add-node --ip 9.10.11.12 --role near --region jp --key ~/.ssh/id_ed25519
```

After adding, edit `config/inventory.yaml` to fill in near-node fields (sni, port_base, hy2_sni, cdn_domain, dns_name), then redeploy:

```bash
vvnext deploy
vvnext sub rebuild
```

### Remove a node

```bash
vvnext remove-node jp-new-a
vvnext deploy
vvnext sub rebuild
```

### Rotate keys

```bash
# Rotate all key materials
vvnext keys rotate
vvnext deploy
vvnext sub rebuild

# Rotate a specific node
vvnext keys rotate --node hk-gcp-a
vvnext deploy hk-gcp-a
vvnext sub rebuild
```

After key rotation, all clients must re-import their subscription.

### Run a security audit

```bash
vvnext audit
```

Sample output:

```
Audit: 2 findings (0 critical, 2 warning)
  [WARNING] us-gcp-a | security | SSH password authentication is enabled
  [WARNING] us-gcp-a | security | fail2ban is not running
```

### Deploy to specific nodes

```bash
# Deploy only to hk-gcp-a
vvnext deploy hk-gcp-a

# Deploy to multiple specific nodes
vvnext deploy hk-gcp-a jp-gcp-a
```

### Set up Telegram alerts

Edit `config/settings.yaml`:

```yaml
alerting:
  telegram:
    enabled: true
    bot_token: "123456:ABC-DEF..."
    chat_id: "-100123456789"
```

Or use environment variables:

```bash
export VVNEXT_TG_TOKEN="123456:ABC-DEF..."
```

Then run health checks with Telegram alerting:

```bash
vvnext health --telegram
```

## Troubleshooting

### SSH connection refused

- Verify the node IP is correct in inventory
- Check that your SSH key is authorized on the node
- For residential nodes, ensure Tailscale is connected

### sing-box fails to start after deploy

VVNext automatically rolls back to the previous config. Check:

```bash
vvnext health --detail
```

If the node shows `[FAIL]`, SSH in manually and check logs:

```bash
journalctl -u sing-box -n 50
```

### Subscription import fails

1. Verify the subscription server is running: `vvnext sub server start`
2. Check that the subscription URL is reachable from the client
3. Rebuild subscriptions: `vvnext sub rebuild`

### Health check shows UDP port closed

UDP checks may show false negatives. A "no response (normal for UDP)" result means the port is likely open. Only "ICMP unreachable" indicates a real problem.
