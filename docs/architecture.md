# Architecture

This document describes the internal architecture of VVNext: system layers, node roles, protocol design, data flow, and configuration pipeline.

## System Layers

```
+-----------------------------------------------------------------------+
|                         CLI Layer (cli.py)                             |
|   init / status / add-node / deploy / health / sub / audit / keys     |
+-----------------------------------------------------------------------+
         |              |              |              |
+--------+--+    +------+------+   +--+--------+   +-+----------+
| Inventory |    | Config Gen  |   | Overlay   |   | State      |
| (Pydantic)|    | (sing-box   |   | (WG topo  |   | (persist   |
| inventory |    |  JSON)      |   |  compute) |   |  allocations|
| .py       |    | config_gen  |   | overlay   |   | state.py   |
|           |    | erator.py   |   | .py       |   |            |
+-----------+    +-------------+   +-----------+   +------------+
         |              |              |
+--------+--------------+--------------+----------------------------+
|                    Infrastructure Layer                            |
|  ssh.py       deploy.py      bootstrap.py      dns.py    keys.py  |
+-------------------------------------------------------------------+
         |
+--------+----------------------------------------------------------+
|                    Subscription Layer                              |
|  builder.py -> classifier.py -> formats/{mihomo,shadowrocket,     |
|                                          singbox}.py              |
+-------------------------------------------------------------------+
         |
+--------+----------------------------------------------------------+
|                    Monitoring Layer                                |
|  health.py (TCP/UDP/TLS checks, Telegram)    audit.py (drift,     |
|                                               security)           |
+-------------------------------------------------------------------+
```

## Node Roles

VVNext defines three node roles, each with distinct responsibilities:

### Near (Ingress) Nodes

**Location**: Close to users (HK, JP, TW, SG)

Near nodes accept client connections and serve as the ingress point for all traffic. Each near node runs multiple protocol listeners:

| Protocol | Port | Transport | Purpose |
|---|---|---|---|
| VLESS+Reality (overlay) | port_base+1, +3, +4, ... | TCP | Traffic forwarded to far nodes via WG |
| VLESS+Reality (direct) | port_base+2 | TCP | Traffic exits at near node's IP |
| Hysteria2+Salamander | 443 | UDP | Low-latency, QUIC-based, direct exit |
| VLESS+WS+CDN | 2053 | TCP/WS | CDN-fronted, passes through Cloudflare |
| AnyTLS | 8443 | TCP | Mimics standard TLS, direct exit |

**Key property**: Near nodes hold Reality keypairs, HY2 ACME certs, and WG private keys for outbound tunnels.

### Far (Egress) Nodes

**Location**: Target country (typically US)

Far nodes provide exit IPs. They run only a WireGuard listener:

| Protocol | Port | Transport | Purpose |
|---|---|---|---|
| WireGuard | wg_port (e.g., 51941) | UDP | Accept WG tunnels from near nodes |

All traffic arriving through WG exits to the internet from the far node's public IP.

### Residential Nodes

**Location**: Home ISP (behind NAT)

Residential nodes are far nodes reachable via Tailscale instead of direct IP. They provide home ISP IP addresses (valuable for AI services that block datacenter IPs).

| Property | Value |
|---|---|
| Access method | Tailscale IP (100.x.x.x) |
| SSH | Via Tailscale IP |
| NAT | true |
| WG | Reverse tunnel from near node |

## Protocol Matrix

Which protocols support overlay (multi-hop) vs. direct-exit:

| Protocol | Overlay (via WG) | Direct Exit | CDN-Fronted | Anti-Blocking |
|---|---|---|---|---|
| VLESS+Reality | Yes | Yes | No | TLS fingerprint mimicry |
| Hysteria2+Salamander | No | Yes | No | QUIC obfuscation |
| VLESS+WS+CDN | No | Yes | Yes | Hidden behind Cloudflare |
| AnyTLS | No | Yes | No | Mimics standard HTTPS |

**Overlay** means the traffic enters a near node and exits from a far node via WireGuard tunnel. Only VLESS+Reality supports overlay mode because it uses TCP, which tunnels cleanly through WG.

## Per-Exit Inbound Model

This is VVNext's core routing design. Instead of complex server-side route rules, each exit destination gets its own inbound listener on a distinct port:

```
Near Node: hk-gcp-a (port_base = 20000)
  Port 20001 -> WG tunnel -> us-gcp-a (US exit)     # overlay peer 0
  Port 20002 -> direct exit (HK IP)                 # direct Reality
  Port 20003 -> WG tunnel -> us-gcp-b (US exit)     # overlay peer 1
  Port 20004 -> WG tunnel -> us-home (residential)  # overlay peer 2
  Port 443   -> direct exit (HK IP)                 # HY2
  Port 2053  -> direct exit (HK IP)                 # CDN
  Port 8443  -> direct exit (HK IP)                 # AnyTLS
```

The client's subscription contains one proxy entry per port. The client app (Mihomo, Shadowrocket) selects which proxy to use, which implicitly selects the exit country.

### Port allocation scheme

```
port_base + 1  = first overlay peer (Reality)
port_base + 2  = direct Reality
port_base + 3  = second overlay peer (Reality)
port_base + 4  = third overlay peer (Reality)
port_base + N  = (N-2)th overlay peer, for N >= 3
```

The `+2` slot is always reserved for direct Reality. All other slots are overlay peers, ordered by `wg_peers` list in the inventory.

## WireGuard Overlay Topology

```
Near: hk-gcp-a ----[WG tunnel]----> Far: us-gcp-a
  near_ip: 10.240.10.2                far_ip: 10.240.10.1
  (WG outbound)                       (WG inbound, port 51941)

Near: hk-gcp-a ----[WG tunnel]----> Far: us-gcp-b
  near_ip: 10.240.10.6                far_ip: 10.240.10.5
  (WG outbound)                       (WG inbound, port 51942)

Near: jp-gcp-a ----[WG tunnel]----> Far: us-gcp-a
  near_ip: 10.240.10.10               far_ip: 10.240.10.9
  (WG outbound)                       (WG inbound, port 51941)
```

### IP allocation

WG IPs are allocated from `/30` blocks within `subnet_base` (default: `10.240.10.0`):

- Block 0: `.1` (far) / `.2` (near) -- first peer pair
- Block 1: `.5` (far) / `.6` (near) -- second peer pair
- Block N: `.(N*4+1)` (far) / `.(N*4+2)` (near)

Allocations are persistent in `state.yaml`. Adding or removing nodes does not shuffle existing IPs.

### Topology computation

The `overlay.py` module computes the topology:

1. Enumerate all desired (near, far) pairs from `wg_peers` in inventory
2. Reuse existing allocations from `state.yaml`
3. Allocate new `/30` blocks for new pairs
4. Update state with the new topology
5. Return topology dict keyed by `(near_name, far_name)` tuples

## Data Flow

### Client to Internet (overlay path)

```
Client device
  |
  | (VLESS+Reality, TCP, port 20001)
  v
Near node: hk-gcp-a
  |
  | sing-box route rule: inbound "vless-reality-overlay-us-gcp-a"
  |   -> outbound "wg-us-gcp-a"
  |
  | (WireGuard, UDP, port 51941)
  v
Far node: us-gcp-a
  |
  | sing-box route: final "direct"
  v
Internet (exit IP: us-gcp-a's public IP)
```

### Client to Internet (direct path)

```
Client device
  |
  | (HY2/Reality-direct/CDN/AnyTLS)
  v
Near node: hk-gcp-a
  |
  | sing-box route rule: inbound "hy2-in" -> outbound "direct"
  v
Internet (exit IP: hk-gcp-a's public IP)
```

## Configuration Pipeline

```
inventory.yaml ──┐
settings.yaml  ──┤
state.yaml     ──┤
                  v
         ┌───────────────┐
         │ Config Gen    │
         │ (overlay.py + │
         │  config_gen   │
         │  erator.py)   │
         └───────┬───────┘
                 │
     ┌───────────┼───────────┐
     v           v           v
  rendered/    rendered/   rendered/
  hk-gcp-a/   us-gcp-a/   jp-gcp-a/
  config.json  config.json config.json
  manifest.json            manifest.json
  client_nodes.json        client_nodes.json
                 │
         ┌───────┴───────┐
         │    Deploy      │
         │  (deploy.py)   │
         │  atomic + roll │
         │  back          │
         └───────────────┘
```

Steps:

1. **Load** inventory, settings, and state
2. **Compute** WG overlay topology (persistent IP allocation)
3. **Generate** key materials (Reality keypairs, WG keypairs, UUIDs, passwords)
4. **Render** per-node sing-box JSON configs + deployment manifests + client proxy entries
5. **Deploy** via SSH: upload -> remote validate -> atomic replace -> restart -> verify -> rollback on failure

## Subscription Pipeline

```
rendered/*/manifest.json ──┐
rendered/*/client_nodes.json ──┤
routing_rules.yaml ────────┤
                            v
                   ┌────────────────┐
                   │ Merger         │
                   │ (builder.py)   │
                   └────────┬───────┘
                            v
                   ┌────────────────┐
                   │ Classifier     │
                   │ (classifier.py)│
                   │ 16 buckets:    │
                   │  hk_direct,    │
                   │  hk_cdn, ...   │
                   └────────┬───────┘
                            v
                   ┌────────────────┐
                   │ Proxy Groups   │
                   │ Auto-Select,   │
                   │ AI, US-Exit,   │
                   │ Streaming-HK,  │
                   │ AnyTLS, ...    │
                   └────────┬───────┘
                            v
              ┌─────────────┼─────────────┐
              v             v             v
         mihomo.yaml   shadowrocket.txt  singbox.json
```

### Classifier buckets

The classifier sorts all proxy entries into 16 buckets:

| Bucket | Contents |
|---|---|
| `hk_direct` | HK near, Reality direct exit |
| `hk_cdn` | HK near, VLESS+WS+CDN |
| `hk_hy2` | HK near, Hysteria2 |
| `jp_direct`, `jp_cdn`, `jp_hy2` | Same for JP |
| `tw_direct`, `tw_cdn`, `tw_hy2` | Same for TW |
| `us_overlay` | Any near -> US far via WG overlay |
| `us_residential` | Any near -> US residential via WG |
| `anytls_hk`, `anytls_jp`, `anytls_tw` | AnyTLS per region |
| `anytls_all` | All AnyTLS (computed) |
| `us_overlay_all` | All US overlay (computed) |

### Proxy groups

Groups built from buckets:

| Group | Priority chain |
|---|---|
| Auto-Select | All proxies, fallback |
| AI | Residential -> US overlay -> AnyTLS |
| Streaming-US | US overlay |
| Streaming-HK | HK direct + HK CDN |
| US-Exit | US overlay all |
| HK-Exit | HK direct + CDN + HY2 |
| JP-Exit | JP direct + CDN + HY2 |
| TW-Exit | TW direct + CDN + HY2 |
| Residential | US residential |
| AnyTLS | All AnyTLS |
| HY2-Fallback | All HY2 across regions |
| Direct | DIRECT |

## State Management

`state.yaml` stores persistent allocations that must survive across renders and deploys:

```yaml
wg_allocations:
  us-gcp-a:
    wg_port: 51941
    peers:
      hk-gcp-a:
        near_ip: "10.240.10.2"
        far_ip: "10.240.10.1"
      jp-gcp-a:
        near_ip: "10.240.10.10"
        far_ip: "10.240.10.9"
  us-gcp-b:
    wg_port: 51942
    peers:
      hk-gcp-a:
        near_ip: "10.240.10.6"
        far_ip: "10.240.10.5"

last_deploy: "2026-04-10T14:30:00Z"

bootstrap_checkpoints:
  hk-gcp-a: 13
  us-gcp-a: 13
```

**Why state matters**: Without persistent allocations, adding a new node could reshuffle all WG IPs across the fleet, breaking every existing tunnel. The state file ensures stability: existing pairs keep their IPs, new pairs get new blocks.

## Security Model

- **SSH**: Key-based auth only. Password auth is audited and flagged.
- **TLS**: Reality uses TLS 1.3 fingerprint mimicry. HY2 uses ACME certs. CDN uses Cloudflare Origin CA.
- **WG**: X25519 keypairs per node, generated locally, never transmitted in cleartext.
- **Credentials**: UUIDs, passwords, and keys stored in `rendered/materials/`, excluded from git via `.gitignore`.
- **Audit**: `vvnext audit` checks config drift, SSH hardening, UFW, fail2ban, and Tailscale SSH status.
- **Deploy safety**: Max 2 parallel deploys, remote validation before replace, automatic rollback on failure.

## Bootstrap Sequence

Provider-aware node provisioning (`bootstrap.py`):

1. Create service user (`simba`) with passwordless sudo
2. Install system packages (curl, wget, jq, socat, iptables)
3. Sysctl tuning: BBR congestion control, IP forwarding, conntrack limits
4. Install sing-box from GitHub releases (with mirror fallback for China)
5. Install WireGuard tools
6. UFW firewall setup (skipped on GCP -- uses Cloud Firewall)
7. Install and enable fail2ban
8. Generate self-signed certs for HY2/AnyTLS (644 permissions, not 600)
9. Set up WG MSS clamp iptables rules (prevents TCP fragmentation black holes)
