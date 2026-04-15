# VVNext

[![CI](https://github.com/yagjzx/vvnextpublic/actions/workflows/ci.yml/badge.svg)](https://github.com/yagjzx/vvnextpublic/actions)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![License: Apache-2.0](https://img.shields.io/badge/License-Apache%202.0-green.svg)](LICENSE)

[English](README.md) | **中文**

**开源 sing-box 多节点代理网络管理工具。**

VVNext 自动化管理 sing-box 代理网络的全生命周期：节点配置、配置生成、原子部署、订阅分发、健康监控、安全审计。面向 100-1000 人规模团队的运维人员。

## 为什么选择 VVNext

- **单一事实来源** -- 一个 `inventory.yaml` 驱动所有配置生成、部署和订阅。
- **多跳覆盖网络** -- WireGuard 隧道连接近端入口和远端出口。客户端通过端口选择出口国家。
- **4 种协议** -- VLESS+Reality, Hysteria2+Salamander, VLESS+WS+CDN, AnyTLS。协议多样性对抗封锁。
- **3 种订阅格式** -- Mihomo/Clash, Shadowrocket, sing-box/Hiddify。一次重建服务所有客户端。
- **原子部署** -- 上传、远端验证、原子替换、失败自动回滚。告别手动 `scp` + `systemctl`。
- **零停机运维** -- 增删节点、轮换密钥、重新渲染、重新部署，不会全网中断。

## 功能列表

| 分类 | 说明 |
|------|------|
| 协议 | VLESS+Reality, Hysteria2+Salamander, VLESS+WS+CDN, AnyTLS |
| 覆盖网络 | WireGuard 隧道，通过 `state.yaml` 持久分配 IP |
| 节点角色 | 近端 (入口: HK/JP/TW/SG/KR), 远端 (出口: US), 家宽 (通过 Tailscale 的家庭 ISP) |
| 订阅 | Mihomo, Shadowrocket, sing-box -- 智能代理组分类 |
| 部署 | 原子替换 + 自动回滚, 最多 2 台并行, 远端 `sing-box check` 验证 |
| 健康检查 | TCP/UDP 端口探测, WG 隧道 ping, TLS 证书过期, Telegram 告警(含防抖) |
| 安全审计 | 配置漂移检测, SSH 加固, UFW 状态, fail2ban, Tailscale SSH 检查 |
| 初始化 | 13 步管道（支持断点续传）: SSH 验证 -> 引导 -> 渲染 -> 部署 -> 订阅 |
| 添加节点 | SSH 探测 + GeoIP 自动推断角色/地区/厂商 + 资源分配 + 全流程部署 |
| 批量添加 | 并行探测, 计划展示, 顺序部署（远端优先） |
| 域名分流 | 基于域名的路由: AI -> 家宽出口, 流媒体 -> 远端出口, 中国域名 -> 直连 |
| 监控 | 5 类指标（系统/sing-box/网络/WG/证书）, InfluxDB 推送, 表格输出 |

## 快速开始

### 安装

```bash
pip install vvnext
```

### 首次部署

```bash
# 交互式向导 -- 引导完成全部 13 步
vvnext init

# 或从配置文件非交互部署
vvnext init --config my-fleet.yaml

# 从断点恢复
vvnext init --config my-fleet.yaml --resume
```

### 验证

```bash
vvnext status           # 网络总览: 节点, 角色, 协议, 端口
vvnext health           # TCP/UDP/WG 检查所有节点
vvnext health --detail  # 完整检查详情
```

### 日常运维

```bash
# 添加单个节点（自动通过 GeoIP 检测角色/地区/厂商）
vvnext add-node --ip 1.2.3.4

# 指定参数添加
vvnext add-node --ip 1.2.3.4 --role near --region jp --domain example.com

# 从文件批量添加
vvnext batch-add config/machines.yaml --domain example.com

# 部署
vvnext deploy                  # 渲染 + 部署所有节点
vvnext deploy hk-gcp-a         # 部署指定节点

# 订阅管理
vvnext sub rebuild              # 重新生成所有订阅文件
vvnext sub server start         # 启动 HTTPS 订阅服务器

# 监控
vvnext monitor --once           # 单次采集指标, 打印表格
vvnext monitor --interval 300   # 每 5 分钟采集, 推送到 InfluxDB

# 维护
vvnext keys rotate              # 轮换所有密钥材料
vvnext audit                    # 安全 + 配置漂移审计
```

## CLI 参考

```
vvnext [OPTIONS] COMMAND [ARGS]

命令:
  init              交互式初始化向导 (--config 非交互, --resume 断点续传)
  status            网络总览: 节点, 角色, 地区, 协议, 端口
  add-node          添加节点, 全自动管道 (--ip 必填, 角色/地区自动检测)
  batch-add         从 machines.yaml 批量添加 (并行探测 + 顺序部署)
  remove-node       按名称删除节点
  deploy            部署 sing-box 配置 (全部或指定节点)
  health            运行健康检查 (--detail, --telegram)
  monitor           采集监控指标 (--once 单次, --interval 守护模式)
  sub rebuild       重建订阅文件 (--format 过滤格式)
  sub server        启动/停止 HTTPS 订阅服务器
  audit             安全 + 配置漂移审计
  keys generate     为所有节点生成密钥材料
  keys rotate       轮换密钥 (--node 指定节点)

选项:
  -V, --version     显示版本并退出
  -i, --inventory   inventory.yaml 路径 (默认: config/inventory.yaml)
  -s, --settings    settings.yaml 路径 (默认: config/settings.yaml)
```

## 架构概览

```
                         客户端 (Mihomo / Shadowrocket / Hiddify)
                                        |
                     +------------------+------------------+
                     |                  |                  |
                 近端: HK          近端: JP          近端: TW
              (VLESS/HY2/CDN/   (VLESS/HY2/CDN/   (VLESS/HY2/CDN/
               AnyTLS)           AnyTLS)            AnyTLS)
                 |   \               |   \               |
                 |    \              |    \               |
               [WG 覆盖网络]       [WG 覆盖网络]       [WG 覆盖网络]
                 |      \            |      \            |
              远端: US-A  远端: US-B  |   远端: US-A      |
              (出口 IP)  (出口 IP)   |   (出口 IP)       |
                                     |                   |
                              [家宽: US-HOME]
                              (通过 Tailscale + WG)
```

**每出口独立入站模型**: 每个近端节点监听多个端口。每个端口通过 WireGuard 覆盖网络映射到特定出口。客户端选择端口即选择出口国家。

**数据流**: 客户端 -> 近端节点 (端口 N) -> WG 隧道 -> 远端节点 -> 互联网

## 域名分流

通过 `config/routing_rules.yaml` 配置域名路由规则:

```yaml
server_routing:
  ai_residential:                              # AI 服务走家宽出口
    domains: [openai.com, anthropic.com, claude.ai]
    preferred_exit: residential
    fallback_exit: far
  streaming_us:                                # 美国流媒体走远端出口
    domains: [netflix.com, disneyplus.com, hulu.com]
    preferred_exit: far
  direct_cn:                                   # 中国域名直连
    domains: [baidu.com, qq.com, taobao.com]
    action: direct
```

规则同时应用于 sing-box 服务端路由和 Mihomo 客户端订阅。

## 批量添加节点

创建 `machines.yaml`:

```yaml
machines:
  - ip: 203.0.113.10
    role: near           # 可选, 自动检测
    region: hk           # 可选, 自动检测
    provider: gcp        # 可选, 自动检测
  - ip: 198.51.100.20
  - ip: 192.0.2.30
```

执行:

```bash
vvnext batch-add machines.yaml --domain example.com
```

管道自动执行:
1. 并行 SSH 探测 + GeoIP 推断（最多 4 并发）
2. 展示计划表格, 等待确认
3. 顺序添加（远端优先, 因为近端 wg_peers 依赖远端存在）
4. 一次性重建订阅 + 健康检查

## 监控

```bash
# 单次采集, 打印表格
vvnext monitor --once

# 输出示例:
# Node                  CPU%        Mem       Disk     Load   SB    TCP     WG  Cert
# hk-gcp-a              12.5  1024/2048      15/40      0.5   UP    156    2/3    45
# us-gcp-a               5.2   512/1024       8/20      0.1 DOWN     30      -     -

# 守护模式, 每 5 分钟推送到 InfluxDB
vvnext monitor --interval 300

# 指定节点
vvnext monitor --once --targets hk-gcp-a --targets us-gcp-a
```

采集 5 类指标:
- **系统**: CPU%, 内存, 磁盘, 负载
- **sing-box**: 服务状态, 连接数
- **网络**: TCP ESTABLISHED/TIME_WAIT 连接数
- **WireGuard**: peer 在线数, 最后握手时间
- **证书**: TLS 证书过期天数

## 项目结构

```
config/
  inventory.example.yaml    # 节点定义 (复制为 inventory.yaml)
  settings.example.yaml     # 全局设置 (复制为 settings.yaml)
  routing_rules.yaml        # 域名分流规则
  machines.yaml.example     # 批量添加输入格式
src/vvnext/
  cli.py                    # Typer CLI 命令
  inventory.py              # Pydantic 节点模型 + 校验
  settings.py               # 全局设置模型
  state.py                  # WG IP 持久分配
  overlay.py                # WireGuard 拓扑计算
  config_generator.py       # sing-box JSON 配置生成 + 域名分流
  deploy.py                 # 原子部署 + 自动回滚
  health.py                 # TCP/UDP/TLS 健康检查 + Telegram
  audit.py                  # 配置漂移 + 安全审计
  bootstrap.py              # 分厂商节点初始化
  keys.py                   # 密钥材料生成
  dns.py                    # DNS 记录管理
  ssh.py                    # SSH 客户端 (paramiko)
  probe.py                  # SSH 探测 + GeoIP 推断
  allocator.py              # 资源分配 (端口/SNI/节点ID)
  collector.py              # 监控指标采集 + InfluxDB 推送
  subscription/
    builder.py              # 订阅编排器
    classifier.py           # 代理组分类器
    server.py               # HTTPS 订阅服务器
    formats/
      mihomo.py             # Mihomo/Clash YAML 输出
      shadowrocket.py       # Shadowrocket base64 输出
      singbox.py            # sing-box/Hiddify JSON 输出
tests/
  unit/                     # 232 单元测试
  integration/              # 4 集成测试 (全流程)
```

## 配置

### 节点清单 (`config/inventory.yaml`)

网络的单一事实来源:

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

### 全局设置 (`config/settings.yaml`)

```yaml
project_name: "MyProxy"
domain: "example.com"

ssh:
  user: "root"
  key_path: "~/.ssh/id_ed25519"

dns:
  provider: "cloudflare"       # 或 "manual"

subscription:
  port: 8443
  formats: [mihomo, shadowrocket, singbox]

alerting:
  telegram:
    enabled: true
    bot_token: ""              # 或设置 VVNEXT_TG_TOKEN 环境变量
    chat_id: ""

monitoring:
  influxdb:
    enabled: true
    url: "http://localhost:8086"
    org: "myorg"
    bucket: "vvnext"
    token: ""                  # 或设置 VVNEXT_INFLUX_TOKEN 环境变量
```

## 文档

- [架构设计](docs/architecture.md) -- 系统分层, 协议矩阵, 数据流
- [快速入门](docs/quickstart.md) -- 逐步首次部署
- [高级话题](docs/advanced.md) -- 家宽节点, 监控, 故障排除

## 开发

```bash
# 克隆并配置
git clone https://github.com/yagjzx/vvnextpublic.git
cd vvnextpublic
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# 运行测试 (232 个, ~1分钟)
pytest

# 运行 linter
ruff check src/ tests/

# 带覆盖率
pytest --cov=vvnext --cov-report=term-missing
```

## 贡献

1. Fork 仓库
2. 创建功能分支: `git checkout -b feature/my-feature`
3. 安装开发依赖: `pip install -e ".[dev]"`
4. 运行测试: `pytest`
5. 运行 linter: `ruff check src/ tests/`
6. 提交 Pull Request

## 许可证

Apache-2.0 -- 详见 [LICENSE](LICENSE)。
