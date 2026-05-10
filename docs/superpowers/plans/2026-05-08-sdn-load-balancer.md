# SDN 动态负载均衡调度器 — 完整实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 基于 Ryu 控制器 + Mininet 仿真，实现一个"固定周期采集 + 阈值触发重路由"的动态负载均衡调度器，并通过对照实验验证其效果。

**Architecture:** Mininet 构建双路径拓扑（4 交换机 + 4 主机），Ryu 控制器作为 SDN 控制平面，通过 OpenFlow 协议与交换机通信。控制器周期性查询端口统计信息计算链路利用率，当利用率超阈值时将部分流量从拥塞路径重路由至轻载路径。

**Tech Stack:** Python 3.9 / Ryu SDN Framework 4.34 / Mininet / Open vSwitch (OVS) 3.3.4 / OpenFlow 1.3 / iperf / Conda (sdn)

**项目定位：固定拓扑下的 SDN 动态流量工程原型**

本项目只解决一个问题：**双路径拥塞时的动态 reroute**。方法是 telemetry → threshold → explicit flow install。不做通用 SDN controller，不做动态 shortest-path recompute，不做 ECMP/per-flow scheduling。

**两个控制器的角色：**

| 控制器 | 角色 | 架构 |
|--------|------|------|
| `base_controller.py` | 对照实验的基准（无负载均衡） | 最小化：table-miss + topology discovery + datapath registration |
| `load_balancer.py` | 核心创新（动态流量工程） | 真正 SDN：拓扑感知 + 显式路径安装 + ARP 单播转发 + 拥塞感知 reroute |

`base_controller.py` 的存在意义是提供对照数据，证明负载均衡有效。它不是项目的核心——`load_balancer.py` 才是。

---

## 评分标准对齐检查表

| 评分项 | 占比 | 本计划覆盖点 |
|--------|------|-------------|
| 报告（简介、原理、设计实现、结果分析、见解） | 60% | 全部技术环节均产出可截图/记录的数据，供报告使用 |
| 附件（源代码、数据、演示视频/录屏、运行说明） | 30% | 每个 Task 末尾提醒保存代码快照、截图和数据 |
| 心得体会 | 10% | 不在本计划范围内，自行撰写 |

**课程要求关键条款对照：**
- "能够实现基本的功能，允许不完善，但要可运行，能够通过自测用例验证" — 每个环节末尾均给出验证方式
- "如果明确说明不完善地方，不会扣分；若分析到位，反而会考虑酌情加分" — 附录中标注可选扩展项，可在报告中说明
- "允许在已有框架下二次开发，但必须说明自己的开发工作体现在哪" — 基于 Ryu 框架开发，需在报告中说明
- "切忌从网上直接拿一个软件交差" — 本计划仅指导思路，代码需自行编写

---

## 拓扑与流量模型总览

```
        ┌──────────────────────────────────┐
        │          Ryu Controller          │
        │   (OpenFlow 控制平面, TCP 6633)   │
        └──────────┬───────────┬───────────┘
                   │  OpenFlow │
        ┌──────────▼───────────▼───────────┐
        │                                  │
   h1 ──┤ s1 ════════ s2 ════════ s4 ├── h3
        │  ║  路径 A   ║                   │
   h2 ──┤  ║          ║          ║ ├── h4
        │  ╚══════════ s3 ════════╝        │
        │          路径 B                   │
        └──────────────────────────────────┘

链路带宽: 10 Mbps (所有链路)
路径 A: s1 → s2 → s4
路径 B: s1 → s3 → s4
```

### 端口映射表（拓扑构建后需验证）

| 交换机 | 端口 1 | 端口 2 | 端口 3 | 端口 4 |
|--------|--------|--------|--------|--------|
| s1 | h1 | h2 | s2 (路径A) | s3 (路径B) |
| s2 | s1 | s4 | — | — |
| s3 | s1 | s4 | — | — |
| s4 | h3 | h4 | s2 (路径A) | s3 (路径B) |

> **注意：** 实际端口号由 Mininet 的 `addLink()` 调用顺序决定。上表假设 addLink 的调用顺序如拓扑脚本所示。运行后需用 `ovs-ofctl show` 命令验证实际端口映射，并据此调整控制器代码中的端口配置。

---

## 环境准备

### 已验证的环境

- Windows 11 + WSL2 (Ubuntu 24.04)
- VS Code + Remote - WSL 扩展
- Conda 虚拟环境 `sdn` (Python 3.9)
- 已安装：Mininet、Ryu 4.34、OVS 3.3.4、iperf、networkx、matplotlib、numpy

### 项目目录结构

```
/root/SDN/
├── topo/               # Mininet 拓扑脚本
│   └── dual_path_topo.py
├── controller/         # Ryu 控制器代码
│   ├── base_controller.py      # 静态最短路径控制器（对照实验用）
│   └── load_balancer.py        # 动态负载均衡控制器（核心）
├── scripts/            # 流量生成与数据采集脚本
│   ├── run_experiment.sh
│   └── plot_utilization.py
├── data/               # 实验数据（CSV/JSON/日志）
│   └── screenshots/
├── figures/            # 生成的图表
└── docs/               # 文档
```

---

## 环节 1：前置知识补充与开发环境搭建

**复杂度：** 低 | **难度：** 低 | **预估工程量：** 2-3 小时

### 1.1 关键概念速览

#### 什么是 SDN？

传统网络中，每台交换机/路由器自己决定怎么转发数据包（控制平面和数据平面耦合在一起）。SDN（Software-Defined Networking）把"怎么转发"这个决策权抽出来，交给一个集中的**控制器**软件来做。交换机只负责"按指令转发"（数据平面）。

**为什么需要它？** 集中控制让网络策略变更变得简单——你改控制器代码就能改变整个网络的转发行为，不用逐台配置设备。

#### 什么是 OpenFlow？

OpenFlow 是 SDN 架构中控制器和交换机之间的**通信协议**。控制器通过 OpenFlow 向交换机下发"流表规则"（match-action rules），告诉交换机："遇到什么样的数据包，该怎么处理"。

**流表（Flow Table）的组成：**
- **匹配字段（Match Fields）**：匹配数据包的哪些字段（如目的 IP、源 IP、入端口等）
- **动作（Actions）**：匹配成功后执行的操作（如从指定端口转发、丢弃、修改包头等）
- **优先级（Priority）**：多条规则匹配时，高优先级的先执行
- **计数器（Counters）**：记录匹配的包数和字节数

**为什么需要流表？** 流表是 OpenFlow 交换机的"转发表"。传统交换机只有 MAC 地址表，OpenFlow 交换机可以用任意字段匹配，这让转发策略极其灵活。

#### 什么是 Ryu？

Ryu 是一个用 Python 写的 SDN 控制器框架。它帮你处理了 OpenFlow 协议的底层细节，你只需要写 Python 事件处理函数就能响应网络事件（如交换机连接、数据包到达等）。

#### 什么是 Mininet？

Mininet 是一个网络仿真工具，能在一台 Linux 机器上创建虚拟的交换机、主机和链路，模拟出一个完整的网络拓扑。底层用的是 Linux 的网络命名空间（namespace）和 Open vSwitch（OVS）。

#### 什么是 LLDP？

LLDP（Link Layer Discovery Protocol）是链路层发现协议。在 SDN 中，控制器用它来**自动发现拓扑**——控制器让每台交换机从各端口发出 LLDP 包，如果另一台交换机收到了，就知道"这两台交换机的这两个端口是连通的"。

**为什么需要它？** 你的双路径拓扑中，控制器需要知道 s1-s2-s4 和 s1-s3-s4 这两条路径的存在，才能做路径选择。LLDP 让控制器自动构建出整个网络拓扑图。

### 1.2 配置环境

以下是经过验证的完整、正确的环境配置指令流程。

**第一步：安装系统级依赖与工具**

Mininet 和底层抓包工具需要直接操作 Linux 网络命名空间，必须通过系统的包管理器安装。

```bash
sudo apt update
# 安装 Mininet、网络带宽测试工具 iperf 及抓包工具 wireshark
sudo apt install -y mininet iperf wireshark-common
```

**第二步：配置 Conda 虚拟环境**

Ryu 框架对高版本 Python 兼容性较差，使用 Python 3.9 作为稳定的隔离环境。

```bash
# 创建并激活隔离的 Python 3.9 虚拟环境
conda create -n sdn python=3.9 -y
conda activate sdn
```

**第三步：安装 Ryu 及其兼容性依赖**

解决新版打包工具和运行库导致的构建及导入错误。

```bash
# 降级 setuptools 绕过新版对源码根目录 flat-layout 的严格检查，并安装构建所需的 pbr 包
pip install "setuptools==59.5.0" pbr

# 禁用 pip 的构建隔离机制，强制使用当前环境中降级后的 setuptools 编译安装 Ryu
pip install ryu --no-build-isolation

# 降级 eventlet 版本，修复 Ryu 源码中引用被移除变量 ALREADY_HANDLED 导致的 ImportError
pip install eventlet==0.30.2
```

**第四步：安装开发辅助工具**

安装用于处理数据及可视化拓扑的 Python 依赖库。

```bash
# 安装网络图生成、数据绘图及数值计算库
pip install networkx matplotlib numpy
```

### 1.3 环境验证步骤

环境已配置完毕，以下为验证命令：

```bash
# 激活 conda 环境
conda activate sdn

# 验证 Mininet
sudo mn --test pingall
# 预期：0% dropped

# 验证 Ryu
ryu-manager --version
# 预期：ryu-manager 4.34

# 验证 OVS
ovs-vsctl --version
# 预期：Open vSwitch 3.3.4

# 验证 Python 依赖
python3 -c "import ryu, networkx, matplotlib, numpy; print('All imports OK')"
```

> **提醒：** 截图保存所有验证输出，作为环境搭建成功的证据。

### 1.4 关键命令速查表

#### Mininet 命令

| 命令 | 说明 |
|------|------|
| `sudo mn --test pingall` | 快速测试 Mininet 是否正常 |
| `sudo python3 topo/xxx.py` | 运行自定义拓扑 |
| `pingall` | Mininet CLI 中测试全网连通性 |
| `h1 ping h3` | 测试 h1→h3 连通性 |
| `net` | 显示拓扑连接关系 |
| `nodes` | 列出所有节点 |
| `dump` | 显示所有节点详细信息 |
| `iperf h1 h3` | h1 和 h3 之间 iperf 测试（TCP） |
| `h1 iperf -s &` | 在 h1 上启动 iperf 服务端 |
| `h3 iperf -c 10.0.0.1 -u -b 5M -t 30` | UDP 打流 30 秒 |
| `sh ovs-ofctl show s1 -O OpenFlow13` | 查看 s1 端口信息 |
| `sh ovs-ofctl dump-flows s1 -O OpenFlow13` | 查看 s1 流表 |
| `sh ovs-ofctl dump-ports s1 -O OpenFlow13` | 查看 s1 端口统计 |
| `sh ovs-ofctl del-flows s1 -O OpenFlow13` | 清空 s1 流表 |
| `xterm h1` | 打开 h1 的终端窗口 |
| `exit` | 退出 Mininet（清理所有虚拟网络） |

#### OVS 命令

| 命令 | 说明 |
|------|------|
| `ovs-vsctl show` | 查看 OVS 数据库中的交换机和端口 |
| `ovs-vsctl get-controller s1` | 查看 s1 的控制器配置 |
| `ovs-vsctl set-controller s1 tcp:127.0.0.1:6633` | 设置控制器地址 |
| `ovs-vsctl get bridge s1 protocols` | 查看 OpenFlow 版本 |
| `ovs-ofctl dump-flows s1 -O OpenFlow13` | 查看流表 |

#### Ryu 命令

| 命令 | 说明 |
|------|------|
| `ryu-manager app.py` | 运行控制器应用 |
| `ryu-manager app.py --observe-links` | 启用拓扑发现 |
| `ryu-manager --verbose app.py` | 详细日志模式 |
| `ryu-manager app.py --ofp-tcp-listen-port 6653` | 指定监听端口 |

#### iperf 命令

| 命令 | 说明 |
|------|------|
| `iperf -s` | 服务端模式 |
| `iperf -s -u` | UDP 服务端 |
| `iperf -c <ip>` | TCP 客户端 |
| `iperf -c <ip> -u -b 5M` | UDP 客户端，5Mbps |
| `iperf -c <ip> -u -b 5M -t 30` | 持续 30 秒 |
| `iperf -c <ip> -u -b 5M -t 30 -i 1` | 每秒输出一次统计 |
| `iperf -c <ip> -P 4` | 4 个并行流 |

---

## 环节 2：Mininet 拓扑设计与构建

**复杂度：** 中 | **难度：** 中 | **预估工程量：** 3-4 小时

### 2.1 关键概念

#### Mininet 的工作原理

Mininet 创建的"虚拟网络"本质上是以下 Linux 内核机制的组合：

1. **网络命名空间（Network Namespace）**：每台虚拟主机（h1, h2, h3, h4）都是一个独立的网络命名空间，拥有独立的网络栈（网卡、路由表、ARP 表等）。这就是为什么 h1 ping h3 时，h1 的路由表和 h3 的路由表是完全隔离的。

2. **veth pair（虚拟以太网对）**：两个命名空间之间的"虚拟网线"。一端在 h1 的命名空间里，另一端在 s1 的命名空间里。数据从一端写入，另一端就能读到，就像一根真实的网线。

3. **Open vSwitch（OVS）**：每台虚拟交换机（s1, s2, s3, s4）都是一个 OVS 实例。OVS 是一个支持 OpenFlow 的软件交换机，它维护流表，根据流表规则转发数据包。

4. **tc（Traffic Control）**：Linux 内核的流量控制工具。当你在 `addLink()` 中设置 `bw=10` 时，Mininet 用 tc 在 veth pair 上施加令牌桶限速，把链路带宽限制在 10Mbps。

**为什么要理解这些？** 当你调试网络问题时（比如 ping 不通、iperf 吞吐量为 0），你需要知道每一层在做什么：命名空间是否创建成功？veth pair 是否连通？OVS 流表是否正确？tc 限速是否生效？

#### Mininet Python API 详解

Mininet 提供了 Python API 来编程创建拓扑。以下是核心类和方法：

**`Mininet` 类 — 网络仿真主对象：**
```python
net = Mininet(
    controller=None,       # 不使用内置控制器（我们要用远程的 Ryu）
    switch=OVSSwitch,      # 使用 OVS 作为交换机实现
    link=TCLink,           # 使用支持带宽限制的链路类型
    autoSetMacs=True,      # 自动为每个主机分配 MAC 地址（按节点编号）
    autoStaticArp=True,    # 自动静态配置 ARP（避免 ARP 广播干扰实验）
)
```

参数说明：
- `controller=None`：默认 Mininet 会启动一个内置的 `OVSController`，我们需要连接到外部的 Ryu 控制器，所以设为 None，后面手动添加 `RemoteController`。
- `switch=OVSSwitch`：使用 Open vSwitch 虚拟交换机。另一种选择是 `UserSwitch`（用户态交换机），但 OVS 性能更好且支持 OpenFlow 1.3。
- `link=TCLink`：`TCLink` 是支持 Traffic Control 的链路类型，可以设置带宽、延迟、丢包率。默认的 `Link` 类型不支持这些参数。
- `autoSetMacs=True`：自动为每个主机分配可预测的 MAC 地址（如 h1 的 MAC 为 00:00:00:00:00:01），方便调试。

**`addController()` — 添加远程控制器：**
```python
c0 = net.addController(
    'c0',                          # 控制器名称
    controller=RemoteController,   # 使用远程控制器（不是内置的）
    ip='127.0.0.1',                # Ryu 控制器的 IP 地址
    port=6633,                     # Ryu 控制器的监听端口（默认 6633）
)
```

**`addSwitch()` — 添加交换机：**
```python
s1 = net.addSwitch(
    's1',                          # 交换机名称（也是 dpid）
    protocols='OpenFlow13',        # 指定 OpenFlow 1.3 协议版本
)
```

- `protocols='OpenFlow13'`：**必须**与控制器的 OpenFlow 版本一致。如果不指定，OVS 默认可能使用 OpenFlow 1.0，而我们的控制器代码使用的是 1.3 API，会导致版本不匹配错误。
- 交换机名称 `'s1'` 会自动映射为 dpid=1（`'s2'` → dpid=2，以此类推）。

**`addHost()` — 添加主机：**
```python
h1 = net.addHost(
    'h1',                          # 主机名称
    ip='10.0.0.1/24',              # IP 地址（可选，不设置则自动分配）
)
```

- 默认情况下，Mininet 会自动为每个主机分配 IP（h1→10.0.0.1, h2→10.0.0.2, ...）。
- 所有主机默认在同一个子网 10.0.0.0/24 中。

**`addLink()` — 添加链路：**
```python
net.addLink(
    h1, s1,                        # 两端节点
    bw=10,                         # 带宽 10 Mbps（需要 TCLink）
    delay='1ms',                   # 链路延迟 1ms（可选）
    loss=0,                        # 丢包率 0%（可选）
)
```

- `bw`：带宽限制，单位 Mbps。底层用 tc 的 `htb`（Hierarchical Token Bucket）队列规则实现。
- `delay`：单向延迟。底层用 tc 的 `netem` 模块实现。
- `loss`：随机丢包率，百分比。用于模拟不稳定的链路。
- 如果不设置 bw/delay/loss，链路就是"无限带宽、零延迟、零丢包"的虚拟链路。

#### 链路带宽限制的原理

当你设置 `bw=10` 时，Mininet 在 veth pair 的两端各配置了一个 tc 队列规则：

```
h1-eth0  ←——tc 限速 10Mbps——→  s1-eth1
```

具体来说：
- 在发送方向：`htb` 队列规则限制发送速率不超过 10Mbps
- tc 的令牌桶算法会平滑突发流量，使实际发送速率在 10Mbps 附近波动

**为什么要限制带宽？** 不限带宽的话，虚拟链路的实际带宽就是物理网卡的速度（通常 1Gbps+），你的 iperf 测试流量永远到不了瓶颈，负载均衡就无从谈起。

#### 远程控制器模式

默认情况下 Mininet 会启动一个内置的控制器（`OVSController`），它使用 `ovs-controller` 程序来处理 OpenFlow 消息。我们需要交换机连接到**我们自己启动的 Ryu 控制器**，所以要用 `RemoteController` 模式。

`RemoteController` 告诉 Mininet：不要启动内置控制器，而是把交换机配置为连接到指定 IP 和端口的外部控制器。Mininet 会执行 `ovs-vsctl set-controller s1 tcp:127.0.0.1:6633` 来配置每台交换机。

### 2.2 拓扑设计详解

#### 为什么选择双路径拓扑？

你的项目核心是"动态负载均衡"——当一条路径拥塞时，把流量切换到另一条路径。这要求拓扑中**至少有两条从源到目的的等价路径**。

```
路径 A: h1/h2 → s1 → s2 → s4 → h3/h4
路径 B: h1/h2 → s1 → s3 → s4 → h3/h4
```

- **等价路径**：两条路径的跳数相同（都是 3 跳），链路带宽相同（都是 10Mbps），这样对比才有意义。
- **瓶颈链路**：每条路径的总带宽是 10Mbps（受限于瓶颈链路），当流量超过 70%（7Mbps）时触发重路由。
- **拓扑对称性**：s2 和 s3 的角色对称，方便理解和调试。

#### 端口分配策略

在编写拓扑脚本时，端口分配由 `addLink()` 的调用顺序决定。第一条 addLink 的端口是 1，第二条是 2，以此类推。

**推荐的 addLink 调用顺序：**
```python
# 接入链路（先添加，端口号较小，便于记忆）
net.addLink(h1, s1)    # s1: port1=h1,  h1: port1=s1
net.addLink(h2, s1)    # s1: port2=h2,  h2: port1=s1
net.addLink(h3, s4)    # s4: port1=h3,  h3: port1=s4
net.addLink(h4, s4)    # s4: port2=h4,  h4: port1=s4

# 核心链路（后添加，端口号较大）
net.addLink(s1, s2, bw=10)  # s1: port3=s2,  s2: port1=s1  (路径 A)
net.addLink(s2, s4, bw=10)  # s2: port2=s4,  s4: port3=s2  (路径 A)
net.addLink(s1, s3, bw=10)  # s1: port4=s3,  s3: port1=s1  (路径 B)
net.addLink(s3, s4, bw=10)  # s3: port2=s4,  s4: port4=s3  (路径 B)
```

按此顺序，端口映射为：

| 交换机 | port 1 | port 2 | port 3 | port 4 |
|--------|--------|--------|--------|--------|
| s1 | h1 | h2 | s2 (路径A) | s3 (路径B) |
| s2 | s1 | s4 | — | — |
| s3 | s1 | s4 | — | — |
| s4 | h3 | h4 | s2 (路径A) | s3 (路径B) |

> **重要：** 这个端口映射是控制器代码中做路径选择的基础。控制器需要知道"要走路径 A，s1 应该从 port 3 转发"。运行拓扑后，**务必用 `ovs-ofctl show` 命令验证实际端口映射**。

#### 链路带宽为什么选 10Mbps？

- **够小**：iperf TCP 默认会尽量打满带宽，10Mbps 的链路很容易被打满，触发拥塞。
- **够大**：10Mbps 足够承载控制流量（ARP、LLDP 等），不会因为带宽太小导致控制面异常。
- **好计算**：10Mbps 的 70% 阈值是 7Mbps，数字好理解。

### 2.3 拓扑脚本编写

在 `/root/SDN/topo/` 下创建文件 `dual_path_topo.py`。

**编写步骤：**

**Step 1：导入必要的模块**
```python
#!/usr/bin/env python3
"""
双路径拓扑：4 交换机 + 4 主机
路径 A: s1 -> s2 -> s4
路径 B: s1 -> s3 -> s4
所有核心链路带宽 10 Mbps
"""

from mininet.net import Mininet
from mininet.node import RemoteController, OVSSwitch
from mininet.cli import CLI
from mininet.link import TCLink
from mininet.log import setLogLevel
```

模块说明：
- `Mininet`：网络仿真主类，用于创建和管理虚拟网络
- `RemoteController`：远程控制器类，让交换机连接到外部的 Ryu
- `OVSSwitch`：OVS 虚拟交换机，支持 OpenFlow 协议
- `CLI`：Mininet 命令行交互界面（`mininet>` 提示符）
- `TCLink`：支持 Traffic Control 的链路类型（可以设置带宽限制）
- `setLogLevel`：设置 Mininet 的日志级别（`info` 显示关键信息，`debug` 显示所有细节）

**Step 2：编写拓扑创建函数**
```python
def create_topology():
    # 创建网络对象
    net = Mininet(
        controller=None,       # 不用内置控制器
        switch=OVSSwitch,      # 使用 OVS
        link=TCLink,           # 支持带宽限制
        autoSetMacs=True,      # 自动设置 MAC 地址
    )

    # 添加远程控制器（连接到 Ryu）
    c0 = net.addController(
        'c0',
        controller=RemoteController,
        ip='127.0.0.1',
        port=6633,
    )

    # 添加交换机（protocols 指定 OpenFlow 版本）
    s1 = net.addSwitch('s1', protocols='OpenFlow13')
    s2 = net.addSwitch('s2', protocols='OpenFlow13')
    s3 = net.addSwitch('s3', protocols='OpenFlow13')
    s4 = net.addSwitch('s4', protocols='OpenFlow13')

    # 添加主机
    h1 = net.addHost('h1')
    h2 = net.addHost('h2')
    h3 = net.addHost('h3')
    h4 = net.addHost('h4')

    # 添加接入链路（不设带宽限制，默认无限带宽）
    net.addLink(h1, s1)
    net.addLink(h2, s1)
    net.addLink(h3, s4)
    net.addLink(h4, s4)

    # 添加核心链路 — 双路径，带宽 10 Mbps
    net.addLink(s1, s2, bw=10)  # 路径 A 第一段
    net.addLink(s2, s4, bw=10)  # 路径 A 第二段
    net.addLink(s1, s3, bw=10)  # 路径 B 第一段
    net.addLink(s3, s4, bw=10)  # 路径 B 第二段

    # 构建网络、启动控制器和交换机
    net.build()
    c0.start()
    net.start()

    # 等待交换机连接控制器
    import time
    time.sleep(2)

    print("\n=== 拓扑已启动 ===")
    print("路径 A: h1/h2 -> s1 -> s2 -> s4 -> h3/h4")
    print("路径 B: h1/h2 -> s1 -> s3 -> s4 -> h3/h4")
    print("链路带宽: 10 Mbps (核心链路)")
    print("进入 Mininet CLI...\n")

    # 进入交互式命令行
    CLI(net)

    # 退出时清理
    net.stop()
```

**Step 3：程序入口**
```python
if __name__ == '__main__':
    setLogLevel('info')
    create_topology()
```

**完整脚本保存位置：** `/root/SDN/topo/dual_path_topo.py`

### 2.4 运行与验证拓扑

#### 启动流程（需要两个终端）

> **⚠️ 关键警告：双路径拓扑存在物理环路，必须使用支持环路抑制的控制器！**
>
> 拓扑包含环路 `s1 → s2 → s4 → s3 → s1`。如果使用 Ryu 自带的 `simple_switch_13`（无任何环路防护），当控制器遇到未知目的 MAC 时会向所有端口泛洪（FLOOD），导致数据包在环路中无限复制，引发**广播风暴**。实测表现为 `pingall` 丢包率高达 83%，控制器在 108 秒内收到 166203 个 Packet-In，MAC 地址表出现震荡（MAC Flapping）。
>
> **使用我们自己实现的 `base_controller.py`**，它内置了三重环路防护机制：LLDP/IPv6 过滤、MAC 地址锁定（只学一次）、广播风暴时间窗去重。详见 [环节 3：基础控制器实现](#33-基础控制器实现对照基准)。

**终端 1 — 启动 Ryu 控制器：**

```bash
conda activate sdn
cd /root/SDN
ryu-manager controller/base_controller.py --observe-links
```

> `base_controller.py` 是对照实验的基准控制器，内置广播风暴抑制机制，能够在环路拓扑中稳定运行。`--observe-links` 参数启用链路发现（后续 `load_balancer.py` 会用到）。详见 [环节 3](#33-基础控制器实现对照基准)。

**终端 2 — 启动 Mininet 拓扑：**

```bash
conda activate sdn
cd /root/SDN
sudo python3 topo/dual_path_topo.py
```

> **注意：** Mininet 需要 root 权限（因为要创建网络命名空间），所以必须用 `sudo`。

#### 验证步骤

**验证 1：全网连通性**
```bash
mininet> pingall
```

预期输出：
```
*** Ping: testing ping reachability
h1 -> h2 h3 h4
h2 -> h1 h3 h4
h3 -> h1 h2 h4
h4 -> h1 h2 h3
*** Results: 0% dropped (12/12 received)
```

> **如果出现 83% 丢包：** 说明使用了 Ryu 自带的 `simple_switch_13` 而非我们的 `base_controller.py`，请退出 Mininet 后切换控制器重新启动。`base_controller.py` 内置广播风暴抑制机制，能够在环路拓扑中稳定运行。详见 `docs/遇到的问题.md` 中的广播风暴排查记录。

**验证 2：查看拓扑结构**
```bash
mininet> net
```

预期输出类似：
```
h1 h1-eth0:s1-eth1
h2 h2-eth0:s1-eth2
h3 h3-eth0:s4-eth1
h4 h4-eth0:s4-eth2
s1 lo:  s1-eth1:h1-eth0 s1-eth2:h2-eth0 s1-eth3:s2-eth1 s1-eth4:s3-eth1
s2 lo:  s2-eth1:s1-eth3 s2-eth2:s4-eth3
s3 lo:  s3-eth1:s1-eth4 s3-eth2:s4-eth4
s4 lo:  s4-eth1:h3-eth0 s4-eth2:h4-eth0 s4-eth3:s2-eth2 s4-eth4:s3-eth2
c0
```

> **关键：** 仔细检查每个端口的连接关系，确认与 2.2 节的端口映射表一致。特别注意 s1-eth3 连的是 s2（路径 A），s1-eth4 连的是 s3（路径 B）。

**验证 3：查看交换机端口信息**
```bash
mininet> sh ovs-ofctl show s1 -O OpenFlow13
```

预期输出类似：
```
OFPT_FEATURES_REPLY (xid=0x2): dpid:0000000000000001
n_tables:254, n_buffers:0
capabilities: FLOW_STATS TABLE_STATS PORT_STATS GROUP_STATS
...
 1(s1-eth1): addr:xx:xx:xx:xx:xx:xx
     config:     0
     state:      0
     current:    10GB-FD COPPER
     ...
 2(s1-eth2): addr:xx:xx:xx:xx:xx:xx
     ...
 3(s1-eth3): addr:xx:xx:xx:xx:xx:xx
     ...
 4(s1-eth4): addr:xx:xx:xx:xx:xx:xx
     ...
```

> **验证点：** 确认 s1 有 4 个端口，分别连接 h1(1), h2(2), s2(3), s3(4)。

**验证 4：查看流表**
```bash
mininet> sh ovs-ofctl dump-flows s1 -O OpenFlow13
```

> 使用 `base_controller.py` 控制器时，流表中应该有 table-miss 规则（`priority=0`，动作 `CONTROLLER:65535`）和一些 MAC 学习规则（`priority=1`）。不会有 STP 相关规则，因为该控制器不依赖 STP，而是通过广播风暴抑制来应对环路。

**验证 5：基本 iperf 测试**
```bash
mininet> h1 iperf -s &
mininet> h3 iperf -c 10.0.0.1 -t 5
```

预期：h3 到 h1 的 TCP 吞吐量接近 10Mbps（链路带宽上限）。

**验证 6：确认链路带宽限制生效**
```bash
mininet> iperf h1 h3
```

预期输出类似：
```
*** Iperf: testing TCP bandwidth between h1 and h3
*** Results: ['9.41 Mbits/sec', '9.78 Mbits/sec']
```

> 吞吐量应该接近 10Mbps（考虑到 TCP 开销，实际值通常在 9-10Mbps）。

> **保存证据：** 截图以上所有验证步骤的输出，特别是 `pingall` 结果、`net` 输出和 iperf 结果。

### 2.5 进阶：拓扑脚本的健壮性改进

#### 添加拓扑验证函数

在脚本中添加一个自动验证函数，启动后自动检查端口映射：

```python
def verify_topology(net):
    """验证拓扑结构是否正确"""
    print("\n=== 拓扑验证 ===")

    # 检查交换机端口数
    for sname in ['s1', 's2', 's3', 's4']:
        sw = net.get(sname)
        ports = sw.ports
        print(f"{sname}: {len(ports)} ports")

    # 检查连通性
    h1, h3 = net.get('h1', 'h3')
    result = h1.cmd('ping -c 1 -W 1 10.0.0.3')
    if '1 received' in result:
        print("h1 -> h3: OK")
    else:
        print("h1 -> h3: FAILED!")

    # 检查链路带宽
    print("\n带宽测试 (h1 -> h3, TCP, 5秒):")
    h1.cmd('iperf -s &')
    import time; time.sleep(1)
    result = h3.cmd('iperf -c 10.0.0.1 -t 5')
    print(result)
    h1.cmd('kill %iperf')
```

#### 添加清理函数

```python
def cleanup():
    """清理残留的 Mininet 网络"""
    import os
    os.system('sudo mn -c 2>/dev/null')
    os.system('sudo killall -9 ovs-vswitchd 2>/dev/null')
```

> 在每次运行拓扑前调用 `cleanup()`，确保没有残留的网络命名空间或 OVS 实例。

### 2.6 常见问题排查

#### Q: pingall 丢包率高达 83%，只有同交换机的主机能互通？

**症状：** `h1 -> h2` 和 `h2 -> h1` 正常，但 h3、h4 与 h1、h2 之间全部不通。`dump-flows s1` 显示 `priority=0` 的 table-miss 规则 `n_packets` 在短时间内达到数万甚至数十万。

**根本原因：广播风暴（Broadcast Storm）**

双路径拓扑 `s1 → s2 → s4 → s3 → s1` 存在物理环路。使用无 STP 的 `simple_switch_13` 时：
1. h1 ping h3 发出 ARP 广播包
2. s1 不知道 h3 在哪，泛洪到 s2 和 s3
3. s2 和 s3 分别泛洪给 s4
4. s4 从 s2 和 s3 都收到同一份包，继续泛洪回 s2 和 s3
5. 数据包在环路中指数级复制，耗尽带宽和控制器性能

**实测证据：**
- `dump-flows s1` 中 `priority=0` 的 table-miss 规则在 ~108 秒内累计 166203 个包
- 流表中出现 MAC 震荡：`in_port="s1-eth4", dl_src=00:00:00:00:00:04 actions=output:"s1-eth4"`（从 eth4 进来又从 eth4 出去，说明 MAC 学习被环路包污染）
- 只有 h1↔h2 成功，因为它们在同一交换机上，ARP 不经过核心链路

**解决方案：** 使用我们自己实现的 `base_controller.py`，它内置 IPv6/LLDP 过滤、MAC 地址锁定和广播风暴时间窗去重三重防护机制，能够在环路拓扑中稳定运行。详见 `docs/遇到的问题.md`。

#### Q: pingall 全部失败？

**排查步骤：**
1. **检查 Ryu 是否在另一终端启动：**
   ```bash
   ps aux | grep ryu-manager
   ```
   应该能看到 ryu-manager 进程。

2. **检查交换机是否连接到控制器：**
   ```bash
   mininet> sh ovs-vsctl get-controller s1
   ```
   应该输出 `tcp:127.0.0.1:6633`。

3. **检查 Ryu 是否在监听：**
   ```bash
   ss -tlnp | grep 6633
   ```

4. **WSL2 网络问题：** 确认 Ryu 和 Mininet 都在同一个 WSL2 实例内，用 `127.0.0.1` 没问题。

#### Q: 部分 ping 失败？

**可能原因：**
1. **MAC 学习需要时间：** `simple_switch_13` 需要几个包来学习 MAC 地址。等几秒再试。
2. **ARP 超时：** 如果 ARP 请求没得到回复，ping 会失败。检查是否有 ARP 泛洪被流表阻止。
3. **流表冲突：** 可能有残留的旧流表。清空重来：
   ```bash
   mininet> sh ovs-ofctl del-flows s1 -O OpenFlow13
   mininet> sh ovs-ofctl del-flows s2 -O OpenFlow13
   mininet> sh ovs-ofctl del-flows s3 -O OpenFlow13
   mininet> sh ovs-ofctl del-flows s4 -O OpenFlow13
   ```

#### Q: `TCLink` 报错？

**排查：**
1. 确保安装了 tc：`sudo apt install iproute2`
2. 检查内核模块：`lsmod | grep sch_htb`
3. WSL2 默认已支持 tc，如果仍报错，尝试更新 WSL2 内核

#### Q: iperf 吞吐量为 0？

**排查：**
1. 先确认 ping 通：`mininet> h1 ping -c 1 h3`
2. 确认 iperf 服务端在运行：`mininet> h1 ps aux | grep iperf`
3. 确认 IP 地址正确：`mininet> h1 ifconfig`

#### Q: 控制器端口被占用？

```bash
# 查看谁在用 6633 端口
ss -tlnp | grep 6633

# 如果有残留进程，杀掉
sudo killall -9 ryu-manager

# 或者换一个端口启动 Ryu
ryu-manager --ofp-tcp-listen-port 6653 ryu.app.simple_switch_13
# 同时修改拓扑脚本中的 port=6653
```

#### Q: 退出 Mininet 后网络没清理干净？

```bash
# 强制清理所有 Mininet 资源
sudo mn -c

# 检查是否有残留的网络命名空间
sudo ip netns list

# 如果有残留，手动删除
sudo ip netns delete h1
```

---

## 环节 3：Ryu 控制器基础开发

**复杂度：** 中 | **难度：** 高 | **预估工程量：** 5-6 小时

### 3.1 关键概念

#### Ryu 的事件驱动模型

Ryu 用**事件驱动**的方式工作。你不需要写主循环来轮询网络状态，而是注册事件处理函数，当特定事件发生时 Ryu 自动调用这些函数。

**核心机制：**
1. 你创建一个 Python 类，继承 `app_manager.RyuApp`
2. 在类中用 `@set_ev_cls` 装饰器标记哪些函数处理哪些事件
3. Ryu 启动后，自动监听 OpenFlow 消息，触发对应的事件，调用你的处理函数

**这个模型的好处：** 你只需要关注"当某个事件发生时，我该做什么"，不需要处理 TCP 连接管理、消息编解码等底层细节。

#### OpenFlow 1.3 协议详解

OpenFlow 1.3 是 SDN 中最广泛使用的协议版本。它定义了控制器和交换机之间的通信方式。

**消息类型分类：**

| 类别 | 消息 | 方向 | 用途 |
|------|------|------|------|
| **Controller-Switch** | Features Request/Reply | C→S, S→C | 控制器查询交换机能力 |
| | Packet-Out | C→S | 控制器让交换机发送数据包 |
| | FlowMod | C→S | 控制器向交换机添加/修改/删除流表 |
| | Port Stats Request/Reply | C→S, S→C | 查询端口统计信息 |
| | Barrier Request/Reply | C→S, S→C | 确保消息顺序执行 |
| **Asynchronous** | Packet-In | S→C | 交换机把未匹配的包送给控制器 |
| | Port Status | S→C | 端口状态变化通知 |
| | Error | S→C | 错误通知 |
| **Symmetric** | Hello | C↔S | 连接建立时的握手 |
| | Echo Request/Reply | C↔S | 心跳检测 |

**连接建立流程：**
```
交换机                          控制器
  │                               │
  │──── TCP 连接 (port 6633) ────→│
  │──── OFPT_HELLO ──────────────→│  协商 OpenFlow 版本
  │←─── OFPT_HELLO ──────────────│
  │──── OFPT_FEATURES_REQUEST ───→│  控制器查询交换机能力
  │←─── OFPT_FEATURES_REPLY ─────│  返回 dpid、端口数等
  │                               │
  │     连接建立，可以通信了        │
```

#### Ryu 事件与 OpenFlow 消息的对应关系

| Ryu 事件 | 对应的 OpenFlow 消息 | 触发时机 |
|----------|---------------------|---------|
| `EventOFPSwitchFeatures` | Features Reply | 交换机连接控制器时 |
| `EventOFPPacketIn` | Packet-In | 数据包匹配不到流表时 |
| `EventOFPPortStatsReply` | Port Stats Reply | 控制器查询端口统计后收到回复 |
| `EventOFPStateChange` | 内部事件 | 交换机连接/断开时 |
| `EventOFPPortStatus` | Port Status | 端口状态变化时（如链路断开） |

#### Packet-In 机制详解

当交换机收到一个数据包，它会按以下流程处理：

```
数据包到达
    │
    ▼
查流表（从优先级最高的规则开始）
    │
    ├── 匹配成功 → 执行动作（转发、丢弃等）→ 结束
    │
    └── 匹配失败（table-miss）
            │
            ▼
        检查 table-miss 规则
            │
            ├── 动作是 send_to_controller → 封装为 Packet-In 发送给控制器
            │
            ├── 动作是 drop → 丢弃
            │
            └── 没有 table-miss 规则 → 默认丢弃（OpenFlow 1.3 规范）
```

**Packet-In 消息包含：**
- `data`：原始数据包的字节内容
- `match`：匹配信息，包含 `in_port`（数据包从哪个端口进入）
- `buffer_id`：如果交换机缓存了这个包，返回 buffer_id（可以用于 Packet-Out 避免重传）
- `reason`：触发原因（`OFPR_NO_MATCH` = 无匹配规则，`OFPR_ACTION` = 流表动作要求发送）

**为什么需要 Packet-In？** 这是 SDN 的核心——"未知包交给控制器决策"。你的负载均衡器就是在 Packet-In 时做路径选择的。

#### Table-Miss 流表规则

OpenFlow 1.3 规范规定：如果没有匹配的流表规则，默认行为是**丢弃**数据包。这意味着如果你不下发任何规则，所有流量都会被丢弃。

**解决方案：** 在交换机连接时下发一条 **table-miss 规则**：
- 匹配字段：空（匹配所有包）
- 优先级：0（最低优先级）
- 动作：`output: CONTROLLER`（发送给控制器）

这样，所有未匹配其他规则的包都会被送到控制器，控制器可以决定如何处理。

#### Packet-Out 机制

控制器收到 Packet-In 后，可以：
1. **下发流表规则（FlowMod）**：告诉交换机"以后遇到同类包，这样处理"
2. **发送 Packet-Out**：把当前这个包从指定端口发出去（解决"第一个包怎么转发"的问题）

**为什么两个都需要？** FlowMod 解决的是"以后的同类包"，Packet-Out 解决的是"当前这个包"。如果不发 Packet-Out，第一个包就丢了。

### 3.2 Ryu 应用结构详解

#### 基本骨架

```python
from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER
from ryu.controller.handler import set_ev_cls
from ryu.ofproto import ofproto_v1_3

class MyApp(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]  # 声明支持的 OpenFlow 版本

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # 初始化你的数据结构

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        # 处理交换机连接事件
        pass
```

**关键元素说明：**

1. **`OFP_VERSIONS`**：声明你的控制器支持哪些 OpenFlow 版本。必须与交换机的协议版本一致。
   - `ofproto_v1_3.OFP_VERSION` = OpenFlow 1.3
   - 如果不设置，默认支持所有版本，但可能导致版本不匹配问题。

2. **`@set_ev_cls(事件类, 状态)`**：装饰器，告诉 Ryu 这个函数处理哪种事件。
   - 第一个参数：事件类（如 `ofp_event.EventOFPSwitchFeatures`）
   - 第二个参数：在哪个状态下触发（`CONFIG_DISPATCHER` = 交换机刚连接，还在协商；`MAIN_DISPATCHER` = 协商完成，正常通信）

3. **`ev` 参数**：事件对象，包含事件的所有数据。
   - `ev.msg`：OpenFlow 消息对象
   - `ev.msg.datapath`：交换机的 datapath 对象（代表一个交换机连接）
   - `ev.msg.datapath.ofproto`：OpenFlow 协议常量（如 `OFPP_FLOOD`）
   - `ev.msg.datapath.ofproto_parser`：消息构造器（用于创建 FlowMod、PacketOut 等消息）

#### datapath 对象

`datapath` 代表一个交换机连接，是控制器与交换机交互的核心对象：

```python
datapath = ev.msg.datapath
dpid = datapath.id                    # 交换机 ID（如 1, 2, 3, 4）
ofproto = datapath.ofproto            # 协议常量模块
parser = datapath.ofproto_parser      # 消息构造器

# 发送消息给交换机
datapath.send_msg(msg)                # 发送任意 OpenFlow 消息
```

#### 处理器状态（Dispatcher States）

Ryu 在交换机连接的不同阶段触发不同事件：

| 状态 | 说明 | 何时触发 |
|------|------|---------|
| `HANDSHAKE_DISPATCHER` | 正在进行 Hello 握手 | 很少使用 |
| `CONFIG_DISPATCHER` | 握手完成，正在交换 Features | 下发 table-miss 规则的时机 |
| `MAIN_DISPATCHER` | 正常工作状态 | 处理 Packet-In 的时机 |
| `DEAD_DISPATCHER` | 连接断开 | 清理资源 |

### 3.3 基础控制器实现：对照基准

`base_controller.py` 是对照实验中的"无负载均衡"基准控制器。它的存在意义是提供对比数据——证明 `load_balancer.py` 的动态 reroute 有效。

**定位：** 这是一个最小化控制器，不是项目核心。它采用传统 L2 学习交换机架构（MAC 学习 + 泛洪转发），不做任何集中式路径规划。为了让它在环路拓扑中能正常运行（`pingall` 通过），加入了三重防护：IPv6/LLDP 过滤、MAC 锁定、广播时间窗去重。

> **注意：** 这些防护机制是 learning-switch 架构在环路拓扑下的补丁，不是本项目的创新点。真正的 SDN 架构（`load_balancer.py`）通过显式路径安装从根源上消除泛洪，不需要这些补丁。

**实现思路：**
1. 交换机连接时，下发 table-miss 规则
2. 收到 Packet-In 时，先过滤 LLDP 和 IPv6 包，再对广播包做时间窗去重
3. MAC 地址锁定（只学一次），防止环路包导致端口映射漂移
4. 如果知道目的 MAC 的出端口，就下发流表规则并转发
5. 如果不知道目的 MAC 的出端口，就泛洪（FLOOD）

#### Step 1：创建文件并导入模块

在 `/root/SDN/controller/` 下创建 `base_controller.py`：

```python
#!/usr/bin/env python3
"""
基础 SDN 控制器 — L2 学习交换机
功能：MAC 地址学习 + 静态最短路径转发
用途：对照实验中的"无负载均衡"场景
"""

import time
from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER
from ryu.controller.handler import set_ev_cls
from ryu.ofproto import ofproto_v1_3
from ryu.lib.packet import packet, ethernet, ether_types
```

**导入模块说明：**
- `app_manager`：Ryu 应用管理器，提供 `RyuApp` 基类
- `ofp_event`：OpenFlow 事件类的集合
- `CONFIG_DISPATCHER, MAIN_DISPATCHER`：处理器状态常量
- `set_ev_cls`：事件处理装饰器
- `ofproto_v1_3`：OpenFlow 1.3 协议常量
- `packet, ethernet, ether_types`：数据包解析库

#### Step 2：定义控制器类和初始化

```python
class L2LearningSwitch(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # MAC 地址表: {dpid: {mac_addr: port_no}}
        # 例如: {1: {'00:00:00:00:00:01': 1, '00:00:00:00:00:03': 3}}
        self.mac_to_port = {}

        # 广播风暴缓存：{(dpid, src_mac, eth_type): timestamp}
        # 用于广播风暴抑制，防止环路拓扑中广播包（ARP 等）无限复制
        self.broadcast_cache = {}
        # 缓存最大容量，防止内存泄漏
        self.cache_limit = 1000
```

**`mac_to_port` 数据结构说明：**
- 外层 dict 的 key 是 `dpid`（交换机编号）
- 内层 dict 的 key 是 MAC 地址，value 是端口号
- 这个表记录了"从哪个端口能到达哪个 MAC 地址"

**`broadcast_cache` 数据结构说明：**
- key 是 `(dpid, src_mac, eth_type)` 三元组，value 是上次见到该广播包的时间戳
- 用于广播风暴抑制：0.5 秒内同一交换机收到相同源 MAC 和以太网类型的广播包，判定为环路包并丢弃
- 拦截范围覆盖所有目标 MAC 为 `ff:ff:ff:ff:ff:ff` 的广播包（包括 ARP、RARP 等），而非仅限 ARP
- `cache_limit` 机制：缓存超过 1000 条时自动清空，防止长期运行导致内存泄漏

#### Step 3：处理交换机连接事件

```python
    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        """交换机连接时，下发 table-miss 规则"""
        datapath = ev.msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

        # 下发 table-miss 规则
        # match 为空，表示匹配所有数据包
        match = parser.OFPMatch()

        # 动作：发送给控制器
        # OFPCML_NO_BUFFER = 不缓存，整个包都发给控制器
        actions = [parser.OFPActionOutput(
            ofproto.OFPP_CONTROLLER,
            ofproto.OFPCML_NO_BUFFER
        )]

        # 优先级为 0（最低），确保其他规则优先匹配
        self.add_flow(datapath, 0, match, actions)
        self.logger.info("Switch %s connected, table-miss installed", datapath.id)
```

**逐行解析：**

1. `@set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)`：
   - 监听 `EventOFPSwitchFeatures` 事件
   - 在 `CONFIG_DISPATCHER` 状态下触发（交换机刚连接时）
   - 这个事件携带了交换机的 Features Reply 消息

2. `datapath = ev.msg.datapath`：
   - 获取 datapath 对象，代表这个交换机
   - 后续所有与这个交换机的交互都通过 datapath

3. `match = parser.OFPMatch()`：
   - 创建一个空的匹配条件
   - 空匹配 = 匹配所有数据包（任何字段都不检查）

4. `actions = [parser.OFPActionOutput(ofproto.OFPP_CONTROLLER, ofproto.OFPCML_NO_BUFFER)]`：
   - 创建一个输出动作：把包发送到控制器端口
   - `OFPP_CONTROLLER` = 控制器端口（特殊端口号）
   - `OFPCML_NO_BUFFER` = 不使用交换机的缓冲区，把整个包发给控制器

5. `self.add_flow(datapath, 0, match, actions)`：
   - 下发流表规则，优先级为 0
   - 优先级 0 是最低的，任何其他规则都会优先匹配

#### Step 4：实现 add_flow 辅助方法

```python
    def add_flow(self, datapath, priority, match, actions, buffer_id=None):
        """向交换机添加一条流表规则"""
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

        # 创建指令：APPLY_ACTIONS 表示立即执行动作
        inst = [parser.OFPInstructionActions(
            ofproto.OFPIT_APPLY_ACTIONS,
            actions
        )]

        # 构造 FlowMod 消息
        kwargs = dict(
            datapath=datapath,
            priority=priority,
            match=match,
            instructions=inst,
        )

        # 如果有 buffer_id，加上它（避免重传已缓存的包）
        if buffer_id is not None:
            kwargs['buffer_id'] = buffer_id

        mod = parser.OFPFlowMod(**kwargs)
        datapath.send_msg(mod)
```

**FlowMod 消息解析：**
- `datapath`：目标交换机
- `priority`：流表优先级（数值越大优先级越高）
- `match`：匹配条件
- `instructions`：匹配成功后执行的指令
  - `OFPIT_APPLY_ACTIONS`：立即执行动作（还有 `OFPIT_WRITE_ACTIONS` 等其他类型）
- `buffer_id`：交换机缓存的包的 ID，如果设置，交换机会同时执行动作处理缓存的包（避免控制器再用 Packet-Out 发一次）

#### Step 5：处理 Packet-In 事件

```python
    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def packet_in_handler(self, ev):
        """收到 Packet-In 时：学习 MAC 地址，转发数据包"""
        msg = ev.msg
        datapath = msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        in_port = msg.match['in_port']

        # 解析数据包
        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocol(ethernet.ethernet)

        # 过滤 LLDP（SDN 控制器用来拓扑发现的，不需要学习 MAC）
        # 和 IPv6（消除 Mininet 主机初始化时的 IPv6 组播风暴）
        if eth.ethertype in (ether_types.ETH_TYPE_LLDP, ether_types.ETH_TYPE_IPV6):
            return

        dst = eth.dst    # 目的 MAC 地址
        src = eth.src    # 源 MAC 地址
        dpid = datapath.id  # 交换机 ID

        # ====== 广播风暴抑制 ======
        # 拦截目标 MAC 为全 F 的广播包（包含 ARP 广播及其他广播协议）
        # 利用时间窗机制打破双路径拓扑导致的广播环路
        if dst == 'ff:ff:ff:ff:ff:ff':
            cache_key = (dpid, src, eth.ethertype)
            now = time.time()

            # 定期清理缓存，防止内存泄漏
            if len(self.broadcast_cache) > self.cache_limit:
                self.broadcast_cache.clear()

            if cache_key in self.broadcast_cache:
                # 0.5 秒时间窗拦截重复广播包
                if now - self.broadcast_cache[cache_key] < 0.5:
                    return

            self.broadcast_cache[cache_key] = now

        # 初始化该交换机的 MAC 表
        self.mac_to_port.setdefault(dpid, {})

        # MAC 锁定：一旦某个 MAC 地址学习到某个端口，就不再更新
        # 防止环路传回的包导致端口映射漂移（MAC Flapping）
        if src not in self.mac_to_port[dpid]:
            # ====== MAC 地址学习 ======
            # 记录：从 in_port 进来的包，源地址是 src
            # 以后要发往 src 的包，从 in_port 出去就行了
            self.mac_to_port[dpid][src] = in_port
            self.logger.info("Switch %s: learn %s on port %d", dpid, src, in_port)

        # ====== 查找目的端口 ======
        if dst in self.mac_to_port[dpid]:
            # 已知目的端口
            out_port = self.mac_to_port[dpid][dst]
        else:
            # 未知目的，泛洪
            out_port = ofproto.OFPP_FLOOD

        # 构造输出动作
        actions = [parser.OFPActionOutput(out_port)]

        # ====== 下发流表规则（避免后续同类型包再触发 Packet-In）======
        if out_port != ofproto.OFPP_FLOOD:
            match = parser.OFPMatch(eth_dst=dst, eth_src=src)

            # 如果交换机缓存了这个包，用 buffer_id 下发规则
            # 交换机会自动处理缓存的包，不需要 Packet-Out
            if msg.buffer_id != ofproto.OFP_NO_BUFFER:
                self.add_flow(datapath, 1, match, actions, msg.buffer_id)
                return  # 注意：这里直接返回，因为包已经被交换机处理了
            else:
                self.add_flow(datapath, 1, match, actions)

        # ====== Packet-Out：发送当前数据包 ======
        # 如果是泛洪，或者没有 buffer_id，需要用 Packet-Out 手动发送
        # 如果交换机没缓存包，我们必须把原包数据完整的通过 Packet-Out 发回去
        # 如果交换机已缓存（buffer_id 有效），传 data=None 即可
        data = msg.data if msg.buffer_id == ofproto.OFP_NO_BUFFER else None
        out = parser.OFPPacketOut(
            datapath=datapath,
            buffer_id=msg.buffer_id,
            in_port=in_port,
            actions=actions,
            data=data,
        )
        datapath.send_msg(out)
```

**Packet-In 处理流程详解：**

1. **解析数据包：**
   - `msg.data` 是原始字节，需要用 `packet.Packet()` 解析
   - `eth = pkt.get_protocol(ethernet.ethernet)` 提取以太网帧头
   - `eth.dst` = 目的 MAC，`eth.src` = 源 MAC，`eth.ethertype` = 上层协议类型

2. **过滤 LLDP 和 IPv6 包：**
   - LLDP 包的 ethertype 是 `0x88CC`，Ryu 的 `topology.py` 模块会处理
   - IPv6 包的 ethertype 是 `0x86DD`，Mininet 主机初始化时会发送大量 IPv6 组播包（如 ND、MLD），不过滤会导致 Packet-In 风暴，严重拖慢 `pingall` 测试
   - 用 `in` 语法同时过滤两种类型，简洁高效

3. **广播风暴抑制：**
   - **为什么当前实现"看似"有效：** 代码将广播丢弃逻辑放在 MAC 地址学习之前。当环路传回重复的广播包时，控制器直接 return，使得交换机不会在不同端口上来回学习同一个源 MAC 地址（防止了 MAC 震荡）。同时 0.5 秒的时间窗能够阻断双路径拓扑中由环路引发的瞬时广播风暴。
   - **为什么拦截所有广播包而非仅 ARP：** OpenFlow 的 `OFPP_FLOOD` 动作不仅应用于广播包，还会应用于未知单播（Unknown Unicast）。如果仅拦截 ARP，当网络中出现目的 MAC 未知的单播数据包时，该包会在 `s1 → s2 → s4 → s3 → s1` 的环路中无限循环。通过拦截 `dst == 'ff:ff:ff:ff:ff:ff'` 的所有广播包，覆盖了 ARP、RARP 等所有广播协议。
   - **缓存 key 选择 `(dpid, src_mac, eth_type)`：** 比仅用 ARP 的 `(dpid, src_mac, dst_ip)` 更通用，适用于所有广播协议类型。
   - **内存泄漏防护：** `broadcast_cache` 设置了 `cache_limit = 1000` 的上限，超过时自动清空。否则每一次新的广播请求都会在字典中永久驻留一个键值 pair，长期运行会导致 OOM。
   - **已知局限：** 此机制只能抑制广播风暴。如果交换机尚未学习到目的 MAC，单播包仍会触发 `OFPP_FLOOD` 并在环路中循环。在 `base_controller.py`（learning-switch 架构）中，广播抑制是应对环路问题的核心防御机制，使 baseline 控制器能够在双路径拓扑中稳定运行。在后续的 `load_balancer.py` 中，进一步采用 topology-aware SDN routing，通过显式路径安装彻底消除 uncontrolled flooding，从根源上解决环路问题。

4. **MAC 锁定与学习：**
   - `if src not in self.mac_to_port[dpid]`：只在首次见到某 MAC 时学习，之后不再更新
   - 在存在环路的拓扑中，泛洪的包可能从非预期端口传回，导致 MAC 表被错误覆写（MAC Flapping）。锁定后，一旦 MAC 地址绑定到某个端口，就不会因环路包而漂移
   - 这是双路径拓扑中 `pingall` 能否通过的关键条件之一

5. **查找目的端口：**
   - 如果 MAC 表中有目的 MAC，直接查表得到出端口
   - 如果没有，泛洪到所有端口（除了入端口）

6. **下发流表 vs Packet-Out：**
   - 如果知道出端口且不是泛洪，下发流表规则（优先级 1，高于 table-miss 的 0）
   - 如果交换机缓存了包（`buffer_id != OFP_NO_BUFFER`），直接 return，交换机会自动处理
   - 如果没有缓存，需要 Packet-Out 手动发送

### 3.4 流表操作详解

#### FlowMod 消息的完整参数

```python
parser.OFPFlowMod(
    datapath=datapath,         # 目标交换机
    cookie=0,                  # 流表标识（可用于批量删除）
    cookie_mask=0,             # cookie 掩码
    table_id=0,                # 流表编号（OpenFlow 1.3 支持多级流表）
    command=ofproto.OFPFC_ADD, # 操作：ADD/MODIFY/DELETE/STRICT_DELETE
    idle_timeout=0,            # 空闲超时（秒），0 = 永不超时
    hard_timeout=0,            # 硬超时（秒），0 = 永不超时
    priority=1,                # 优先级
    buffer_id=ofproto.OFP_NO_BUFFER,  # 缓冲区 ID
    out_port=ofproto.OFPP_ANY, # 删除时的出端口过滤
    out_group=ofproto.OFPG_ANY,# 删除时的组表过滤
    flags=0,                   # 标志（如 OFPFF_SEND_FLOW_REM = 删除时通知控制器）
    match=match,               # 匹配条件
    instructions=instructions, # 指令列表
)
```

#### 常用匹配字段（Match Fields）

```python
# 匹配目的 MAC
match = parser.OFPMatch(eth_dst='00:00:00:00:00:03')

# 匹配源 MAC 和目的 MAC
match = parser.OFPMatch(eth_src='00:00:00:00:00:01', eth_dst='00:00:00:00:00:03')

# 匹配入端口
match = parser.OFPMatch(in_port=1)

# 匹配以太网类型（如 ARP = 0x0806, IPv4 = 0x0800）
match = parser.OFPMatch(eth_type=0x0800)

# 匹配目的 IP
match = parser.OFPMatch(eth_type=0x0800, ipv4_dst='10.0.0.3')

# 匹配五元组（TCP 流）
match = parser.OFPMatch(
    eth_type=0x0800,
    ip_proto=6,            # TCP
    ipv4_src='10.0.0.1',
    ipv4_dst='10.0.0.3',
    tcp_src_port=12345,
    tcp_dst_port=80,
)
```

#### 常用动作（Actions）

```python
# 从指定端口转发
actions = [parser.OFPActionOutput(port_no)]

# 泛洪
actions = [parser.OFPActionOutput(ofproto.OFPP_FLOOD)]

# 发送给控制器
actions = [parser.OFPActionOutput(ofproto.OFPP_CONTROLLER, ofproto.OFPCML_NO_BUFFER)]

# 修改目的 MAC（用于 NAT 或负载均衡）
actions = [parser.OFPActionSetField(eth_dst='00:00:00:00:00:05')]

# 修改源 MAC
actions = [parser.OFPActionSetField(eth_src='00:00:00:00:00:ff')]

# 多个动作（按顺序执行）
actions = [
    parser.OFPActionSetField(eth_dst='00:00:00:00:00:05'),
    parser.OFPActionOutput(3),
]
```

#### 删除流表规则

```python
def delete_flow(self, datapath, match, priority=1):
    """删除匹配的流表规则"""
    ofproto = datapath.ofproto
    parser = datapath.ofproto_parser

    mod = parser.OFPFlowMod(
        datapath=datapath,
        command=ofproto.OFPFC_DELETE,  # 删除操作
        out_port=ofproto.OFPP_ANY,     # 不限出端口
        out_group=ofproto.OFPG_ANY,    # 不限出组
        match=match,
        priority=priority,
    )
    datapath.send_msg(mod)
```

**删除操作的注意事项：**
- `OFPFC_DELETE`：删除所有匹配的规则（非严格匹配）
- `OFPFC_DELETE_STRICT`：严格删除，必须优先级和匹配字段都一致才删除
- 删除后，后续的包会触发新的 Packet-In

### 3.5 运行基础控制器并验证

#### 启动流程

**终端 1 — 启动 Ryu 控制器：**

```bash
conda activate sdn
cd /root/SDN
ryu-manager controller/base_controller.py --observe-links
```

> `--observe-links` 启用 Ryu 内置的拓扑发现模块（使用 LLDP），后续做负载均衡时会用到。

**终端 2 — 启动 Mininet 拓扑：**

```bash
conda activate sdn
cd /root/SDN
sudo python3 topo/dual_path_topo.py
```

#### 验证步骤

**验证 1：查看 Ryu 终端日志**

Ryu 终端应该输出：
```
Switch 1 connected, table-miss installed
Switch 2 connected, table-miss installed
Switch 3 connected, table-miss installed
Switch 4 connected, table-miss installed
```

> 如果没有看到这些日志，说明交换机没有连接到控制器。检查端口和网络配置。

**验证 2：测试全网连通性**
```bash
mininet> pingall
```

预期：0% dropped。如果失败，参考 2.6 节排查。

**验证 3：观察 MAC 学习过程**

在 Ryu 终端，你应该能看到类似日志：
```
Switch 1: learn 00:00:00:00:00:01 on port 1
Switch 1: learn 00:00:00:00:00:02 on port 2
Switch 2: learn 00:00:00:00:00:01 on port 1
...
```

**验证 4：查看流表**
```bash
mininet> sh ovs-ofctl dump-flows s1 -O OpenFlow13
```

预期输出类似：
```
 cookie=0x0, duration=5.234s, table=0, n_packets=3, n_bytes=186,
 priority=0 actions=CONTROLLER:65535
 cookie=0x0, duration=2.123s, table=0, n_packets=1, n_bytes=42,
 priority=1,dl_dst=00:00:00:00:00:01,dl_src=00:00:00:00:00:03 actions=output:1
 ...
```

- 第一条是 table-miss 规则（priority=0）
- 后面的是 MAC 学习规则（priority=1）

**验证 5：iperf 测试**
```bash
mininet> h1 iperf -s &
mininet> h3 iperf -c 10.0.0.1 -t 5
```

预期：TCP 吞吐量接近 10Mbps。

> **保存证据：** 截图 Ryu 终端日志、pingall 结果、流表内容和 iperf 结果。

### 3.6 理解数据包转发路径

用这个基础控制器时，转发是"逐跳学习"的。以 h1→h3 为例：

```
第 1 个包（ARP 请求）：
h1 → s1 (Packet-In) → 控制器学习 h1 的 MAC → 泛洪到所有端口
    → s2 (Packet-In) → 控制器学习 → 泛洪
    → s4 (Packet-In) → 控制器学习 → 泛洪
    → h3 收到 ARP 请求

第 2 个包（ARP 回复）：
h3 → s4 (Packet-In) → 控制器学习 h3 的 MAC → 从已知端口转发到 s2
    → s2 (Packet-In) → 转发到 s1
    → s1 (Packet-In) → 转发到 h1

第 3 个包（ICMP echo）：
h1 → s1 → 查流表，已知 h3 在 s2 方向 → 从 port3 发给 s2
    → s2 → 查流表 → 转发给 s4
    → s4 → 查流表 → 转发给 h3
```

**关键观察：** 交换机走哪条路径取决于 MAC 学习的顺序。在双路径拓扑中，`simple_switch_13` 可能把所有流量都引导到路径 A 或路径 B（取决于哪个端口先收到泛洪的包），无法做到负载均衡。这正是我们需要自己实现负载均衡控制器的原因。

### 3.7 调试技巧

#### 添加详细日志

```python
# 在控制器代码中添加日志
self.logger.info("消息内容: %s", variable)     # 一般信息
self.logger.debug("调试信息: %s", variable)    # 调试信息
self.logger.warning("警告信息: %s", variable)  # 警告
self.logger.error("错误信息: %s", variable)    # 错误
```

```bash
# 启动 Ryu 时设置详细日志模式
ryu-manager --verbose controller/base_controller.py
```

#### 查看交换机状态

```bash
# 查看交换机端口
mininet> sh ovs-ofctl show s1 -O OpenFlow13

# 查看流表
mininet> sh ovs-ofctl dump-flows s1 -O OpenFlow13

# 查看端口统计（字节数、包数）
mininet> sh ovs-ofctl dump-ports s1 -O OpenFlow13

# 清空流表
mininet> sh ovs-ofctl del-flows s1 -O OpenFlow13
```

#### 抓包调试

```bash
# 在交换机端口上抓包
mininet> sh tcpdump -i s1-eth3 -w /tmp/s1_eth3.pcap &

# 在主机上抓包
mininet> h1 tcpdump -i h1-eth0 -w /tmp/h1.pcap &
```

### 3.8 常见问题排查

#### Q: "unknown event" 错误？

**原因：** `@set_ev_cls` 装饰器的事件类或状态参数写错。

**检查：**
- 事件类是否正确导入（`from ryu.controller import ofp_event`）
- 状态参数是否匹配（`CONFIG_DISPATCHER` 用于交换机连接，`MAIN_DISPATCHER` 用于 Packet-In）

#### Q: 流表下发了但流量不走预期路径？

**排查：**
1. 检查流表优先级：是否有更高优先级的规则匹配了
2. 检查匹配字段：MAC 地址是否正确
3. 检查动作：出端口是否正确
4. 清空流表重来：`mininet> sh ovs-ofctl del-flows s1 -O OpenFlow13`

#### Q: Packet-In 频繁触发，控制器处理不过来？

**原因：** 没有下发流表规则，或者流表规则的匹配条件太严格。

**解决：** 确保已知的流量都有对应的流表规则，只有未知流量才触发 Packet-In。

#### Q: 交换机版本不匹配？

**错误信息：** `OFPBadRequestError: version mismatch`

**解决：** 确保拓扑脚本中 `protocols='OpenFlow13'` 和控制器中 `OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]` 一致。

#### Q: ARP 请求/回复处理不当？

**症状：** ping 第一次失败，第二次成功。

**原因：** 第一个 ARP 请求被泛洪，但回复没有正确转发。

**解决：** 确保 LLDP 包被过滤（`if eth.ethertype == ether_types.ETH_TYPE_LLDP: return`），ARP 包的处理逻辑正确。

---

## 环节 4：网络状态采集模块

**复杂度：** 高 | **难度：** 高 | **预估工程量：** 6-8 小时

### 4.1 关键概念

#### 端口统计查询（Port Stats）

OpenFlow 协议允许控制器向交换机发送 `OFPMPRequest` 消息请求端口统计信息。交换机会回复每个端口的：
- **rx_bytes / tx_bytes**：接收/发送的字节数（累计值）
- **rx_packets / tx_packets**：接收/发送的包数
- **rx_errors / tx_errors**：错误数

**为什么需要它？** 链路利用率 = (当前字节数 - 上次字节数) / 时间间隔 / 链路带宽。没有端口统计，你就不知道链路有多忙。

#### 链路利用率计算

```
利用率 = Δbytes × 8 / (Δt × bandwidth)
```
- `Δbytes` = 本次 tx_bytes - 上次 tx_bytes（取发送方向，因为我们要知道这条链路"出去"的流量有多大）
- `Δt` = 两次采集的时间间隔（秒）
- `bandwidth` = 链路带宽（bps），你的拓扑中是 10 Mbps = 10,000,000 bps
- 乘 8 是因为 bytes → bits

**注意：** 端口统计给出的是**单端口**的数据。一条链路连接两个交换机的两个端口，你需要选择一端来计算（通常选发送端）。

#### 拓扑发现（topology.py）

Ryu 自带的 `ryu.topology` 模块通过 LLDP 自动发现网络拓扑。它维护一个包含所有交换机、端口和链路的拓扑图。你需要用它来知道"哪些交换机之间有链路"，从而构建路径。

### 4.2 实现周期性端口统计采集

在 `/root/SDN/controller/` 下创建 `stats_collector.py`。核心逻辑：

1. 用 `EventOFPStateChange` 跟踪交换机的上线/下线
2. 用 `hub.spawn` 启动一个后台线程，每 3 秒向所有交换机发送端口统计请求
3. 用 `EventOFPPortStatsReply` 接收回复，计算链路利用率

**关键代码结构：**

```python
class StatsCollector(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.datapaths = {}           # {dpid: datapath}
        self.prev_port_stats = {}     # {(dpid, port_no): bytes}
        self.prev_time = None
        self.link_utilization = {}    # {(dpid, port_no): utilization_ratio}
        self.poll_interval = 3        # 采集间隔（秒）

        # 启动周期采集线程
        from ryu.lib import hub
        self.monitor_thread = hub.spawn(self._monitor)

    def _monitor(self):
        """周期性采集端口统计"""
        while True:
            self.request_port_stats()
            hub.sleep(self.poll_interval)

    def request_port_stats(self):
        """向所有交换机发送端口统计请求"""
        for dp in self.datapaths.values():
            parser = dp.ofproto_parser
            req = parser.OFPPortStatsRequest(dp, 0, dp.ofproto.OFPP_ANY)
            dp.send_msg(req)
```

**周期性调度机制说明：**

Ryu 基于 `eventlet`（协程库），`hub.spawn` 创建的是一个协程（不是线程），它和 Ryu 的事件循环在同一个线程中运行。`hub.sleep` 会把控制权交还给事件循环，等时间到了再继续执行。

这意味着：`_monitor` 中的 `hub.sleep(3)` 不会阻塞 Ryu 处理其他事件（如 Packet-In）。

### 4.3 测试统计采集

将统计采集功能集成到控制器中后：

**终端 1：**
```bash
ryu-manager controller/stats_collector.py --observe-links
```

**终端 2：**
```bash
sudo python3 topo/dual_path_topo.py
```

**在 Mininet CLI 中产生一些流量：**
```bash
mininet> h1 iperf -s &
mininet> h3 iperf -c 10.0.0.1 -t 30
```

**观察 Ryu 终端：** 你应该能看到类似 `Switch 1 Port 3: util=87.23%` 的日志输出。

> **关键验证点：**
> 1. 能看到所有交换机的端口统计
> 2. 有流量时利用率 > 0，无流量时利用率 ≈ 0
> 3. 数量级合理（iperf TCP 默认会尽量打满带宽，利用率应接近 100%）

### 4.4 理解数据流

```
Ryu 每 3 秒                   交换机回复
    │                            │
    ├── OFPPortStatsRequest ──→ s1
    ├── OFPPortStatsRequest ──→ s2    ←── OFPPortStatsReply (每个端口的 tx_bytes)
    ├── OFPPortStatsRequest ──→ s3
    └── OFPPortStatsRequest ──→ s4
```

控制器发送请求 → 交换机回复每个端口的累计字节数 → 控制器用 `Δbytes / Δt / bw` 算出瞬时利用率。

### 4.5 常见坑

1. **`OFPPortStatsReply` 只有发请求才会有**：必须先 `request_port_stats()`，否则不会触发事件
2. **初始值问题**：第一次采集时没有 `prev_port_stats`，无法计算差值。代码中用 `if key in self.prev_port_stats` 处理了这一点
3. **交换机 ID（dpid）与端口号**：dpid 是交换机编号（1, 2, 3, 4），port_no 是交换机上的端口号（1, 2, 3...），不是主机 IP
4. **数据溢出**：`tx_bytes` 是 64 位无符号整数，正常情况下不会溢出，但如果长时间运行（数天），要注意
5. **`request_port_stats()` 的调用位置**：需要在 `__init__` 之后用 Ryu 的 `hub.spawn` 来周期性调用

---

## 环节 5：负载均衡决策逻辑与动态流表下发

**复杂度：** 高 | **难度：** 高 | **预估工程量：** 8-10 小时

### 5.1 关键概念

#### 为什么需要"两条路径"？

在你的拓扑中，h1→h3 有两条等价路径：Path A（s1→s2→s4）和 Path B（s1→s3→s4）。传统交换机用 STP（生成树协议）会**阻塞其中一条**避免环路，只用一条路径。`base_controller.py` 采用的 learning-switch 架构同样只能依赖泛洪，无法主动选择路径。而真正的 SDN 控制器可以通过集中式的拓扑感知，在**所有交换机上显式安装路径流表**，同时使用两条路径——这就是负载均衡的本质。

#### 流表下发 vs 流表修改

- **下发（FlowMod, ADD）**：新增一条流表规则
- **修改（FlowMod, MODIFY）**：修改已有规则的动作
- **删除（FlowMod, DELETE）**：删除匹配的规则

在负载均衡场景中，当链路 A 拥塞时，你需要：
1. 删除或修改 s1 上"去往 h3/h4 走路径 A"的旧规则
2. 下发新规则"去往 h3/h4 走路径 B"

#### 重路由粒度

你可以按不同粒度做重路由：
- **按目的 IP**：所有去往 h3 的流量都改走路径 B
- **按源-目的 IP 对**：只把特定流（如 h1→h3）改走路径 B
- **按流（五元组）**：最细粒度，但流表条目会很多

**推荐：按目的 IP 做路径选择**，简单且效果好。

### 5.2 完整负载均衡控制器架构

创建 `/root/SDN/controller/load_balancer.py`。这是本项目的**核心创新**——与 `base_controller.py` 的 learning-switch 架构有本质区别。

**架构升级说明：** `load_balancer.py` 不再依赖 MAC 学习 + 泛洪转发，而是采用真正的 SDN 架构：
- **拓扑感知**：通过 LLDP 获取网络全貌，构建 adjacency graph
- **显式路径安装**：在路径上所有交换机预先安装流表，数据包沿预定路径逐跳转发，不依赖泛洪
- **ARP 单播转发**：ARP 请求不泛洪，控制器查表后只转发到目标 host 所在端口
- **拥塞感知 reroute**：telemetry → threshold → 路径切换

**应保留的核心能力：**
- Topology awareness（adjacency graph）
- Port stats collection（telemetry 核心）
- Explicit path installation（项目灵魂）
- Threshold-triggered reroute（合理且简单）

**应删除/弱化的逻辑：**
- 复杂 MAC learning：load_balancer 不依赖 learning switch，通过显式路径安装替代
- `OFPP_FLOOD`：正常数据流不应泛洪，仅在极端 fallback 时使用
- 广播 cache patch：真正 SDN 化后广播应非常少，不需要时间窗去重

**控制器模块划分：**
```
load_balancer.py
├── 拓扑发现（LLDP 邻居发现 + 交换机上线/下线事件）
├── Host 位置学习（记录每个 host 连在哪个交换机的哪个端口）
├── Packet-In 处理（ARP 单播转发 + 首包路径安装）
├── 路径计算与选择（基于拓扑图的最短路径 + 负载均衡选择）
├── 显式路径流表安装（在路径上所有交换机安装流表，非仅入口）
├── 端口统计采集（周期性查询端口收发字节数）
├── 链路利用率计算（Δbytes × 8 / (Δt × bandwidth)）
├── 拥塞检测与 reroute（阈值判断 + 路径切换）
└── 流表更新与清理（删除旧流表 + 安装新路径流表）
```

### 5.3 关键难点：端口映射与路径安装

#### 端口映射

你需要知道每台交换机的"哪个端口连着哪台设备"。这可以通过 LLDP 拓扑发现获取，也可以在 Mininet 中手动查看：

```bash
mininet> sh ovs-ofctl show s1 -O OpenFlow13
```

输出类似：
```
1(s1-eth1): addr:xx:xx:xx:xx:xx:xx  ← 连接 h1
2(s1-eth2): addr:xx:xx:xx:xx:xx:xx  ← 连接 h2
3(s1-eth3): addr:xx:xx:xx:xx:xx:xx  ← 连接 s2 (路径 A)
4(s1-eth4): addr:xx:xx:xx:xx:xx:xx  ← 连接 s3 (路径 B)
```

**自动化方案：** 用 `ryu.topology` 模块获取链路信息，自动构建 adjacency 表。

#### 路径安装策略

在双路径拓扑中，SDN 控制器的路径安装必须覆盖路径上的**所有交换机**，而不仅仅是入口交换机 s1。这是 `load_balancer.py` 与 `base_controller.py` 的核心区别之一。

**为什么必须在所有交换机上安装流表？**

`base_controller.py` 采用 learning-switch 架构：它只在 Packet-In 触发时被动学习 MAC，中间交换机靠泛洪 + MAC 学习自行建立转发表。这在环路拓扑中会导致广播风暴，且无法精确控制路径。

`load_balancer.py` 采用显式路径安装：当 h1 要发包给 h3 时，控制器在路径上的每一跳都预先安装好精确的流表规则，数据包沿着预定路径逐跳转发，不会有任何泛洪。

**默认状态（走路径 A）—— 在三台交换机上安装流表：**
```
# 路径 A: h1 → s1 → s2 → s4 → h3
s1: match: eth_dst=h3_mac  →  output: port3(→s2)
s2: match: eth_dst=h3_mac  →  output: port2(→s4)
s4: match: eth_dst=h3_mac  →  output: port1(→h3)
```

**触发重路由后（走路径 B）—— 删除旧流表，在三台交换机上安装新流表：**
```
# 路径 B: h1 → s1 → s3 → s4 → h3
s1: match: eth_dst=h3_mac  →  output: port4(→s3)
s3: match: eth_dst=h3_mac  →  output: port2(→s4)
s4: match: eth_dst=h3_mac  →  output: port1(→h3)  # s4 的出端口不变
```

**关键实现细节：**
- `install_path(path_name)` 方法需要遍历路径上所有交换机，逐一下发 FlowMod
- 切换路径时，必须先删除旧路径上所有交换机的流表，再安装新路径的流表
- s4 的出端口（port1→h3）在两条路径中相同，无需修改；需要修改的是 s1 和中间交换机（s2 或 s3）

### 5.4 决策逻辑详解

```python
def check_and_reroute(self):
    """检查链路利用率，必要时触发重路由"""
    if not self.topo_ready:
        return

    # 计算两条路径的利用率（取路径上最大链路利用率）
    util_a = self.get_path_utilization('A')
    util_b = self.get_path_utilization('B')

    self.logger.info("Path A: %.1f%%, Path B: %.1f%%, current: %s",
                     util_a * 100, util_b * 100, self.current_path)

    # 决策逻辑
    if self.current_path == 'A' and util_a > self.BW_THRESHOLD:
        if util_b < self.BW_SAFE:
            self.logger.info(">>> Path A congested (%.1f%%), rerouting to B",
                             util_a * 100)
            self.install_path('B')
            self.current_path = 'B'

    elif self.current_path == 'B' and util_b > self.BW_THRESHOLD:
        if util_a < self.BW_SAFE:
            self.logger.info(">>> Path B congested (%.1f%%), rerouting to A",
                             util_b * 100)
            self.install_path('A')
            self.current_path = 'A'
```

**决策逻辑说明：**
1. 只有当前路径拥塞（>70%）且另一条路径安全（<50%）时才切换
2. 避免"乒乓效应"：如果两条路径都拥塞，不切换（切换也没用）
3. 切换后，旧路径的流量会逐渐减少，利用率下降

### 5.5 常见坑

1. **删除流表后新规则未生效**：确保先 `delete_flow`，等一个 `hub.sleep(0.1)`，再 `add_flow`，否则可能冲突
2. **已有连接不会自动重路由**：TCP 连接的后续包仍然匹配旧流表。你需要先删除旧流表，新包触发 Packet-In 后才会安装新路径的流表
3. **ARP 处理（单播转发，不做 proxy ARP）**：ARP 请求（广播）触发 Packet-In 后，控制器查找目标 host 的位置（记录在 host_location 表中），然后只将 ARP 转发到目标 host 所在的端口（单播），而非泛洪。这比完整 proxy ARP 简单得多，且足够解决问题。泛洪会在环路拓扑中引发广播风暴，必须避免
4. **流表优先级**：你的负载均衡规则优先级要高于默认的 MAC 学习规则
5. **流表切换瞬间丢包**：删除旧流表和安装新流表之间有时间差，这个窗口期的包会被丢弃。对于课程作业来说，少量丢包是可接受的

---

## 环节 6：流量模型生成与对照实验执行

**复杂度：** 中 | **难度：** 中 | **预估工程量：** 4-5 小时

### 6.1 关键概念

#### iperf 流量生成

iperf 是一个网络性能测试工具，可以生成 TCP 或 UDP 流量并测量吞吐量、丢包率等。

- **iperf 服务端**：`iperf -s`（监听模式）
- **iperf 客户端**：`iperf -c <server_ip>`（发起测试）
- **UDP 模式**：`iperf -c <ip> -u -b 10M`（指定带宽 10Mbps）
- **TCP 模式**：默认，会尽量打满可用带宽

**为什么用 iperf？** 你需要可控的流量来测试负载均衡器。iperf 可以精确控制流量速率、持续时间、协议类型。

### 6.2 对照实验设计

| 实验 | 控制器 | 预期行为 |
|------|--------|---------|
| **实验 1：无负载均衡** | 基础 MAC 学习控制器（base_controller.py） | 所有流量走路径 A（或 B），另一条路径空闲 |
| **实验 2：有负载均衡** | 动态负载均衡控制器（load_balancer.py） | 路径 A 拥塞时自动切换到路径 B |

### 6.3 流量模型设计

#### 模型 1：单流逐步增大（验证阈值触发）

```
时间轴:
0s─────10s─────20s─────30s─────40s
  UDP 2M   UDP 5M   UDP 8M   UDP 12M
  (正常)   (正常)   (接近阈值) (超阈值!)
```

**iperf 命令：**
```bash
# h1 启动 iperf 服务端
mininet> h1 iperf -s -u &

# h3 作为客户端，分阶段增大带宽
mininet> h3 iperf -c 10.0.0.1 -u -b 2M -t 10
mininet> h3 iperf -c 10.0.0.1 -u -b 5M -t 10
mininet> h3 iperf -c 10.0.0.1 -u -b 8M -t 10
mininet> h3 iperf -c 10.0.0.1 -u -b 12M -t 10
```

#### 模型 2：多流并发（验证实际负载均衡效果）

```bash
# h1→h3 和 h2→h4 同时打流，合计打满单路径
mininet> h3 iperf -s -u &
mininet> h4 iperf -s -u &
mininet> h1 iperf -c 10.0.0.3 -u -b 5M -t 30 &
mininet> h2 iperf -c 10.0.0.4 -u -b 5M -t 30
```

#### 模型 3：TCP 长流（验证真实传输性能）

```bash
mininet> h3 iperf -s &
mininet> h4 iperf -s &
mininet> h1 iperf -c 10.0.0.3 -t 60 &
mininet> h2 iperf -c 10.0.0.4 -t 60
```

### 6.4 度量指标

| 指标 | 无负载均衡 | 有负载均衡 | 说明 |
|------|-----------|-----------|------|
| 吞吐量 | 路径 A 满载后下降 | 两条路径分担，总吞吐量更高 | 核心指标 |
| 丢包率 | 链路拥塞时大幅上升 | 保持在低水平 | 体现调度效果 |
| 链路利用率 | 路径 A 接近 100%，路径 B 0% | 两条路径均在 50-70% | 直观展示负载均衡 |

### 6.5 运行实验的完整流程

**实验 A：无负载均衡（Baseline）**

```
终端 1: ryu-manager controller/base_controller.py --observe-links 2>&1 | tee data/baseline_ryu.log
终端 2: sudo python3 topo/dual_path_topo.py
Mininet CLI: 执行流量模型，收集 iperf 数据
保存: data/baseline_*.log, Ryu 终端截图
```

**实验 B：有负载均衡**

```
终端 1: ryu-manager controller/load_balancer.py --observe-links 2>&1 | tee data/lb_ryu.log
终端 2: sudo python3 topo/dual_path_topo.py
Mininet CLI: 执行相同的流量模型
保存: data/lb_*.log, Ryu 终端截图
```

> **重要提醒：** 每次换控制器前，先退出 Mininet（`exit`），再重启 Ryu 和 Mininet，确保干净状态。

---

## 环节 7：结果观测、问题排查与调试

**复杂度：** 中 | **难度：** 中 | **预估工程量：** 3-4 小时

### 7.1 常见问题排查手册

#### 问题 1：交换机连接不上控制器

**症状：** Mininet 启动后，Ryu 终端没有 "Switch X connected" 日志

**排查：**
```bash
# 1. 确认 Ryu 在监听
ss -tlnp | grep 6633

# 2. 确认交换机配置了正确的控制器地址
mininet> sh ovs-vsctl get-controller s1

# 3. 手动设置控制器
mininet> sh ovs-vsctl set-controller s1 tcp:127.0.0.1:6633
```

#### 问题 2：流表已下发但流量不走预期路径

**排查：**
```bash
# 检查流表优先级
mininet> sh ovs-ofctl dump-flows s1 -O OpenFlow13

# 清空重来
mininet> sh ovs-ofctl del-flows s1 -O OpenFlow13
```

#### 问题 3：负载均衡触发后丢包严重

**原因：** 删除旧流表和安装新流表之间有时间差。

**缓解方案：** 先安装新规则再删除旧规则，或对课程作业来说少量丢包是可接受的。

#### 问题 4：iperf 显示 0 带宽

**排查：**
```bash
mininet> h1 ifconfig          # 确认 IP 地址
mininet> h1 ping -c 1 h3     # 先 ping 通
mininet> h1 ps aux | grep iperf  # 确认服务端在运行
```

#### 问题 5：控制器代码修改后不生效

```bash
# 退出 Mininet
mininet> exit
# Ctrl+C 停止 Ryu
# 重新启动 Ryu
ryu-manager controller/load_balancer.py --observe-links
# 重新启动 Mininet
sudo python3 topo/dual_path_topo.py
```

### 7.2 可视化与数据呈现建议

#### 链路利用率随时间变化图

从 Ryu 日志中提取利用率数据，用 matplotlib 画折线图。X 轴是时间，Y 轴是利用率百分比，两条线分别代表路径 A 和 B，用红色虚线标出阈值（70%）。

#### 吞吐量对比图

从 iperf 日志中提取吞吐量，画对比柱状图。X 轴是场景（无负载均衡/有负载均衡 × 不同流量速率），Y 轴是吞吐量或丢包率。

### 7.3 实验数据保存清单

| 数据类型 | 文件位置 | 说明 |
|---------|---------|------|
| Ryu 控制器源代码 | `controller/*.py` | 最终版本 |
| Mininet 拓扑脚本 | `topo/dual_path_topo.py` | |
| 实验数据（日志） | `data/*.log` | iperf 输出、Ryu 日志 |
| 图表 | `figures/*.png` | 利用率图、吞吐量对比图 |
| 截图 | `data/screenshots/` | pingall、流表、重路由日志 |
| 录屏 | `data/demo.*` | 演示视频/录屏 |
| 运行说明 | `README.md` | 如何运行项目 |

> **录屏建议：** 用 Windows 自带的 Xbox Game Bar（Win+G）或 OBS 录制演示过程。录制内容应包括：启动控制器 → 启动拓扑 → 打流 → 观察重路由日志 → 展示结果。

---

## 附录 A：可选扩展（加分项）

以下扩展项不是必须的，但如果你有余力，可以作为加分亮点：

### A.1 回退机制

当拥塞链路恢复后，将流量切回原路径。需要一个计数器，连续 N 个周期都安全后才切回。

### A.2 按流量粒度的负载均衡

不是把所有流量都切换到同一条路径，而是按流（源-目的 IP 对）分别选择路径。

### A.3 第二层对照：轮询 vs 贪心调度

- **轮询调度：** 每来一个新流，交替分配到路径 A 和 B
- **贪心调度：** 选择当前利用率更低的路径

### A.4 IPv6 支持

课程鼓励"底层代码涉及 IPv6 协议"。可以在控制器中添加 IPv6 包的解析和处理。

---

## 附录 B：开发检查点清单

- [ ] **环节 1：** Mininet、Ryu、OVS 安装成功，`pingall` 通过
- [ ] **环节 2：** 自定义拓扑脚本运行成功，双路径拓扑建立，全网 ping 通
- [ ] **环节 3：** 基础控制器能处理 Packet-In，MAC 学习转发正常工作
- [ ] **环节 4：** 能周期性采集端口统计，能看到利用率数据输出
- [ ] **环节 5：** 负载均衡逻辑实现，能检测拥塞并触发重路由
- [ ] **环节 6：** 对照实验执行完毕，有完整的 iperf 数据和 Ryu 日志
- [ ] **环节 7：** 有可视化图表，有录屏演示，数据文件整理完毕

---

## 附录 C：推荐的学习资源

| 资源 | 链接/说明 |
|------|---------|
| Ryu 官方文档 | https://ryu.readthedocs.io/en/latest/ |
| Ryu 应用示例 | https://github.com/faucetsdn/ryu/tree/master/ryu/app |
| Mininet 官方教程 | https://mininet.org/walkthrough/ |
| OpenFlow 1.3 规范 | https://opennetworking.org/wp-content/uploads/2014/10/openflow-spec-v1.3.0.pdf |
| SDN 入门博客 | 搜索 "SDN OpenFlow 入门教程" |
| iperf 使用指南 | `man iperf` 或搜索 "iperf tutorial" |
