# Advanced Topics

This guide covers residential node setup, monitoring, non-interactive deployment, key rotation, and troubleshooting.

## Residential Node Setup

Residential nodes provide home ISP IP addresses, which are valuable for accessing AI services (OpenAI, Anthropic, Google) that block datacenter IPs.

### Architecture

```
Client -> Near node (HK/JP/TW)
              |
              | WireGuard tunnel
              v
         Residential node (US home ISP)
              |
              | Tailscale (NAT traversal)
              |
         Home router / ISP
              |
              v
         Internet (residential IP)
```

### Prerequisites

- A machine at home (Mac Mini, Raspberry Pi, old laptop, etc.)
- Tailscale installed and connected to your tailnet
- Static Tailscale IP (100.x.x.x)

### Step 1: Install Tailscale on the residential node

```bash
curl -fsSL https://tailscale.com/install.sh | sh
tailscale up
```

Note the Tailscale IP:

```bash
tailscale ip -4
# 100.72.95.46
```

### Step 2: Add to inventory

```yaml
servers:
  - name: us-home-att2
    role: residential
    region: us
    provider: home
    public_ip: "73.xxx.xxx.xxx"     # Home ISP public IP (for reference)
    tailscale_ip: "100.72.95.46"    # Tailscale IP (used for SSH + WG)
    ssh_target: tailscale           # SSH via Tailscale
    wg_port: 51943
    nat: true
```

### Step 3: Configure WG peers

Add the residential node to near nodes' `wg_peers`:

```yaml
  - name: hk-gcp-a
    role: near
    # ...
    wg_peers: [us-gcp-a, us-gcp-b, us-home-att2]
```

### Step 4: Deploy

```bash
vvnext keys generate        # Generate WG keypair for residential node
vvnext deploy               # Deploy updated configs
vvnext sub rebuild           # Rebuild subscriptions with residential exit
```

### How it works

1. The near node creates a WG outbound tunnel to the residential node's Tailscale IP
2. Tailscale handles NAT traversal (the home machine is behind a router)
3. Traffic entering the WG tunnel exits from the home ISP's IP
4. The subscription classifier puts residential proxies in the `us_residential` bucket
5. The AI proxy group prefers residential exits: `Residential -> US overlay -> AnyTLS`

### Limitations

- Home ISP upload bandwidth limits throughput
- Power outages or ISP issues cause downtime
- Some ISPs use CGNAT, which may cause issues

## Custom Routing Rules

VVNext uses `config/routing_rules.yaml` to control domain-based proxy group assignment in subscriptions.

### Default rules

```yaml
server_routing:
  ai_residential:
    domains: [openai.com, anthropic.com, claude.ai, claude.com, cursor.sh, gemini.google.com]
    preferred_exit: residential
    fallback_exit: far
  streaming_us:
    domains: [netflix.com, disneyplus.com, hulu.com, hbomax.com]
    preferred_exit: far
  streaming_hk:
    domains: [bilibili.com]
    preferred_exit: near
  us_exclusive:
    domains: [linkedin.com, irs.gov]
    preferred_exit: far
  direct_cn:
    domains: [baidu.com, qq.com, taobao.com, jd.com, weibo.com]
    action: direct
```

### Adding custom rules

Add a new rule block to route traffic for specific domains:

```yaml
  gaming:
    domains: [store.steampowered.com, epicgames.com]
    preferred_exit: near
    fallback_exit: far
```

After editing, rebuild subscriptions:

```bash
vvnext sub rebuild
```

### Rule behavior

- `preferred_exit: residential` -- route through residential proxy group
- `preferred_exit: far` -- route through US overlay group
- `preferred_exit: near` -- route through nearest near node (direct exit)
- `action: direct` -- bypass proxy entirely (no encryption)
- `fallback_exit` -- used when preferred exit is unavailable

## Monitoring Setup

VVNext supports InfluxDB + Grafana for fleet monitoring.

### Step 1: Configure InfluxDB

Edit `config/settings.yaml`:

```yaml
monitoring:
  influxdb:
    enabled: true
    url: "http://influxdb.example.com:8086"
    org: "myorg"
    bucket: "vvnext"
```

### Step 2: Set up periodic health checks

Use cron to run health checks every 5 minutes:

```bash
# Add to crontab
*/5 * * * * /usr/local/bin/vvnext health --telegram 2>&1 | logger -t vvnext-health
```

### Step 3: Grafana dashboard

Create a Grafana dashboard with panels for:

- **Node availability** -- percentage of passing health checks per node
- **Port status** -- TCP/UDP check results over time
- **TLS cert expiry** -- days until certificate expiration
- **WG tunnel latency** -- ping times through WG overlay
- **Alert history** -- Telegram alert frequency

### Telegram alert setup

```yaml
alerting:
  telegram:
    enabled: true
    bot_token: "123456:ABC-DEF..."   # or VVNEXT_TG_TOKEN env var
    chat_id: "-100123456789"
```

The health check module includes an alert debouncer that requires 3 consecutive failures before sending an alert, preventing alert storms from transient network issues.

## Non-Interactive Deployment

For CI/CD pipelines or automated provisioning, use `--config` mode:

### Config file format

```yaml
nodes:
  - ip: 1.2.3.4
    role: near
    region: hk
    sni: "dl.google.com"
    port_base: 20000
    hy2_sni: "hk.example.com"
    cdn_domain: "hk-cdn.example.com"
    dns_name: "hk-a.example.com"
  - ip: 5.6.7.8
    role: far
    region: us
    wg_port: 51941

domain: example.com
ssh_key: ~/.ssh/id_ed25519
dns_provider: cloudflare
```

### Running non-interactive init

```bash
# Full pipeline
vvnext init --config fleet.yaml

# Resume from checkpoint (if a previous run failed)
vvnext init --config fleet.yaml --resume
```

### CI/CD integration

```bash
#!/bin/bash
set -e

# Ensure secrets are in environment
export VVNEXT_CF_TOKEN="${CF_API_TOKEN}"
export VVNEXT_TG_TOKEN="${TG_BOT_TOKEN}"

# Deploy
vvnext deploy

# Verify
vvnext health
if [ $? -ne 0 ]; then
  echo "Health check failed after deploy"
  exit 1
fi

# Rebuild subscriptions
vvnext sub rebuild
```

## Key Rotation Procedures

### When to rotate keys

- Routine: every 90 days
- After a suspected compromise
- After removing team member access
- After removing a node from the fleet

### Full rotation (all nodes)

```bash
# 1. Rotate all key materials
vvnext keys rotate

# 2. Redeploy all nodes
vvnext deploy

# 3. Rebuild subscriptions
vvnext sub rebuild

# 4. Verify health
vvnext health --detail
```

After full rotation, all clients must re-import their subscription URL.

### Per-node rotation

```bash
# 1. Rotate materials for one node
vvnext keys rotate --node hk-gcp-a

# 2. Redeploy that node
vvnext deploy hk-gcp-a

# 3. Rebuild subscriptions (Reality public key changed)
vvnext sub rebuild

# 4. Verify
vvnext health --detail
```

### What gets rotated

| Material | Scope | Impact |
|---|---|---|
| VLESS UUID | Shared (all nodes) | All clients must update |
| Reality keypair | Per near node | Clients using that node must update |
| WG keypair | Per node | All peers of that node re-keyed |
| HY2 password | Shared | All HY2 clients must update |
| AnyTLS password | Shared | All AnyTLS clients must update |

### What does NOT change during rotation

- WG IP allocations in `state.yaml` (tunnel IPs stay the same)
- Port assignments (port_base values)
- DNS records
- Node names

## Troubleshooting

### WG tunnel not connecting

**Symptoms**: Health check passes for individual nodes but overlay proxies fail.

**Diagnosis**:

1. Check WG port is reachable:
   ```bash
   vvnext health --detail
   # Look for: us-gcp-a | udp | 5.6.7.8:51941
   ```

2. SSH to the near node and check WG interface:
   ```bash
   ssh root@hk-gcp-a
   wg show
   ```
   Look for `latest handshake` -- if missing, the tunnel is down.

3. Check sing-box logs on both ends:
   ```bash
   journalctl -u sing-box -n 50 --no-pager
   ```

**Common causes**:

- **Firewall blocking UDP**: Ensure the far node's WG port is open in both cloud firewall (GCP/AWS) and UFW.
- **Wrong WG public key**: Run `vvnext keys generate` to regenerate, then redeploy both nodes.
- **IP allocation mismatch**: Check `state.yaml` matches the rendered configs. Run `vvnext deploy` to re-sync.
- **MTU issues**: VVNext sets WG MTU to 1380 and adds MSS clamp iptables rules. If you see TCP timeouts through the tunnel but ping works, check that MSS clamp rules are in place:
  ```bash
  iptables -t mangle -L FORWARD
  # Should show: TCPMSS ... TCPMSS clamp to PMTU
  ```

### Hysteria2 port blocked

**Symptoms**: HY2 connections fail, other protocols work.

**Diagnosis**:

```bash
vvnext health --detail
# Look for: hk-gcp-a | udp | 1.2.3.4:443 | ICMP unreachable
```

**Common causes**:

- **ISP blocking UDP 443**: Some ISPs or networks block QUIC/UDP on port 443. Try from a different network.
- **ACME cert not issued**: HY2 uses ACME for TLS certs. Check if the domain resolves correctly:
  ```bash
  dig hk.example.com
  ```
  The domain must point to the near node's IP with no CDN proxy.
- **sing-box not listening**: SSH in and verify:
  ```bash
  ss -ulnp | grep 443
  ```

**Workaround**: If UDP 443 is consistently blocked on a network, clients should use VLESS+Reality (TCP) or VLESS+WS+CDN instead. The subscription Auto-Select group will fall back automatically.

### CDN path broken

**Symptoms**: CDN proxies fail, direct and overlay proxies work.

**Diagnosis checklist**:

1. **DNS resolution**: The CDN domain must resolve to Cloudflare IPs (orange cloud proxy):
   ```bash
   dig hk-cdn.example.com
   # Should return Cloudflare IPs (104.x.x.x or 172.x.x.x)
   ```

2. **Cloudflare proxy status**: In the Cloudflare dashboard, verify the record is proxied (orange cloud, not gray).

3. **Origin port**: Cloudflare only proxies certain ports. The default CDN port (2053) is in Cloudflare's allowed list. If you changed it, verify your port is supported.

4. **WebSocket**: The CDN path uses WebSocket transport. In Cloudflare dashboard, ensure WebSocket support is enabled (Settings > Network > WebSockets).

5. **Origin reachability**: Bypass CDN and test direct:
   ```bash
   curl -v --resolve hk-cdn.example.com:2053:1.2.3.4 http://hk-cdn.example.com:2053/ws
   ```

6. **TLS mode**: Cloudflare TLS mode should be "Full" or "Full (Strict)" if your origin has valid certs. For the CDN path (port 2053), VVNext uses plain HTTP between Cloudflare and origin, so "Flexible" also works.

### Config drift detected

**Symptoms**: `vvnext audit` reports config drift.

This means the config on the remote node differs from what VVNext would generate locally.

**Resolution**:

```bash
# Regenerate and redeploy
vvnext deploy <node-name>
```

If drift keeps recurring, check for:

- Manual edits to `/etc/sing-box/config.json` on the server
- Another tool or script modifying the config
- Inventory changes that weren't followed by a deploy

### sing-box crashes on startup

If sing-box fails after a deploy, VVNext auto-rolls back. To debug:

1. Check what config was deployed:
   ```bash
   ssh root@<node> cat /etc/sing-box/config.json | python3 -m json.tool
   ```

2. Run sing-box check manually:
   ```bash
   ssh root@<node> sing-box check -c /etc/sing-box/config.json
   ```

3. Common errors:
   - **"certificate file not found"**: Cert permissions issue. Certs must be 644 (not 600) because sing-box runs as non-root user `simba`.
   - **"listen address already in use"**: Port conflict. Check `ss -tlnp` for conflicting services.
   - **"unknown key"**: sing-box version too old for the config schema. Update sing-box.

### Subscription server not accessible

- Check the server is running: `ps aux | grep vvnext`
- Check the port is open: `ss -tlnp | grep 8443`
- If using self-signed TLS, clients may need to accept the certificate
- If behind a firewall, open the subscription port

### Deploy fails with "SSH connection failed"

- Verify the node IP in inventory matches the actual server
- Check SSH key permissions: `chmod 600 ~/.ssh/id_ed25519`
- For residential nodes, verify Tailscale is connected on both sides
- Test SSH manually: `ssh -i ~/.ssh/id_ed25519 root@<node-ip>`
- Increase SSH timeout in settings if the server is slow to respond:
  ```yaml
  ssh:
    timeout: 60  # seconds
  ```
