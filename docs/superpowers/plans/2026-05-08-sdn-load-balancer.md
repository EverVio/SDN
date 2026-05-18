# SDN AI-Powered 动态负载均衡调度器 — 完整实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 基于 Ryu 控制器 + Mininet Fat-Tree k=4 数据中心拓扑，实现一个"AI 预测驱动的主动式动态负载均衡调度器"。通过 K-Shortest Path 算法计算多条候选路径，结合机器学习模型预测链路拥塞趋势，在拥塞发生**之前**提前重路由。引入大小流分离机制：老鼠流使用 ECMP 哈希分流，大象流使用 ML 加权专用路径。通过三阶段对照实验（无负载均衡 → 阈值响应式 → AI 预测式）验证 AI 赋能的优势。

**Architecture:** Mininet 构建 Fat-Tree k=4 拓扑（20 交换机 + 16 主机，4 个 Pod），Ryu 控制器作为 SDN 控制平面。控制器使用 Yen's K-Shortest Path 算法在加权图上计算多条候选路径，边权由 `DynamicWeightEngine` 动态计算（基础跳数 + 当前利用率 + ML 预测利用率）。逐链路 Random Forest 模型替代旧的逐路径模型。引入大象流/老鼠流分离：通过 5 元组（src_ip, dst_ip, proto, src_port, dst_port）匹配流表，老鼠流 priority=10 ECMP 分流，大象流 priority=30 ML 加权专用路径。

**Tech Stack:** Python 3.12 / Ryu SDN Framework / Mininet / Open vSwitch / OpenFlow 1.3 / NetworkX / scikit-learn (RandomForestRegressor) / joblib / numpy / pandas / matplotlib

**项目定位：AI 赋能的 SDN 数据中心流量工程原型**

本项目解决一个问题：**Fat-Tree 数据中心多路径拥塞时的动态 reroute**。与传统"阈值触发"方案不同，本项目引入：
1. **K-Shortest Path 多路径计算**：Yen's 算法在加权图上计算 K 条候选路径
2. **ML 加权链路代价**：`w = α·hop_cost + β·current_util + γ·predicted_util`（α=1, β=2, γ=3）
3. **大小流分离**：5 元组流表匹配，老鼠流 ECMP 分流，大象流 ML 专用路径
4. **主动预防式路由切换**：ML 预测拥塞，提前迁移大象流

核心创新在于：telemetry → ML prediction → K-shortest path → elephant/mice separation → preemptive flow install。

**三个控制器的角色：**

| 控制器 | 角色 | 架构 | 对比意义 |
|--------|------|------|---------|
| `base_controller.py` | 基准对照（无负载均衡） | L2 学习交换机：MAC 学习 + 泛洪 | 证明负载均衡的必要性 |
| `threshold_balancer.py` | 对照组（阈值响应式） | K 路径 + TopologyManager + if util>70% 则切换 | 传统方法的延迟响应 |
| `predictive_balancer.py` | 实验组（AI 预测式） | K 路径 + DynamicWeightEngine + 大小流分离 + RF 预测 | **核心创新** |

实验对比维度：`无 LB` vs `阈值 LB` vs `AI LB`，突出 AI 预测的**提前切换能力**、**大小流分离的精细化调度**和**高负载下的吞吐量平稳度**。

---

## 评分标准对齐检查表

| 评分项 | 占比 | 本计划覆盖点 |
|--------|------|-------------|
| 报告（简介、原理、设计实现、结果分析、见解） | 60% | AI 模型原理、K-Shortest Path 算法、大小流分离、三阶段对照实验 |
| 附件（源代码、数据、演示视频/录屏、运行说明） | 30% | 完整 ML 流水线代码、逐链路模型、Fat-Tree 拓扑、可视化图表 |
| 心得体会 | 10% | 不在本计划范围内，自行撰写 |

**课程要求关键条款对照：**
- "能够实现基本的功能，允许不完善，但要可运行，能够通过自测用例验证" — 每个环节末尾给出验证方式
- "如果明确说明不完善地方，不会扣分；若分析到位，反而会考虑酌情加分" — 冷启动回退、冷却锁、大小流阈值等工程权衡可在报告中深入分析
- "允许在已有框架下二次开发，但必须说明自己的开发工作体现在哪" — 基于 Ryu 框架开发，K-Shortest Path、DynamicWeightEngine、大小流分离为自研
- "切忌从网上直接拿一个软件交差" — 本计划仅指导思路，代码需自行编写
- 鼓励方向第 15 条："AI/GNN/DNN/Transformer/LLM 技术在通信网络中的应用" — 本项目直接命中

---

## 拓扑与流量模型总览

```
Fat-Tree k=4 拓扑（20 交换机, 16 主机, 4 Pod）

Pod 0:              Pod 1:              Pod 2:              Pod 3:
  e1    e2            e3    e4            e5    e6            e7    e8
 / \  / \           / \  / \           / \  / \           / \  / \
h0_0 h0_1 h1_0 h1_1 h2_0 h2_1 h3_0 h3_1 h4_0 h4_1 h5_0 h5_1 h6_0 h6_1 h7_0 h7_1
  |    |    |    |    |    |    |    |    |    |    |    |    |    |    |    |
  a1    a2            a3    a4            a5    a6            a7    a8
   \  /               \  /               \  /               \  /
    \/                 \/                 \/                 \/
   c1        c2        c3        c4       (core layer)

链路带宽: edge↔agg = 10 Mbps, agg↔core = 10 Mbps, access = 100 Mbps
交换机 DPID: edge=1..8, aggregation=9..16, core=17..20
主机命名: h{pod}_{idx} (h0_0 ~ h3_3)
```

### 路径计算架构

```
┌─────────────────────────────────────────────────────┐
│                  Ryu Controller                      │
│  ┌───────────────────────────────────────────────┐  │
│  │        TopologyManager (NetworkX DiGraph)     │  │
│  │  - Yen's K-Shortest Paths (k=3)              │  │
│  │  - Dynamic edge weights                      │  │
│  │  - has_path() pre-check (no exception)       │  │
│  │  - select_ecmp_path() hash-based             │  │
│  └───────────────────────────────────────────────┘  │
│  ┌──────────────────┐  ┌─────────────────────────┐  │
│  │ DynamicWeightEngine│  │  Per-Link RF Models    │  │
│  │ w=α·hop+β·cur+γ·pred│  │  model_link_{name}.pkl │  │
│  └──────────────────┘  └─────────────────────────┘  │
│  ┌───────────────────────────────────────────────┐  │
│  │         Elephant/Mice Flow Separation         │  │
│  │  - 5-tuple parsing (IPv4/TCP/UDP)            │  │
│  │  - Mice: ECMP hash → priority 10             │  │
│  │  - Elephant: ML-weighted path → priority 30  │  │
│  └───────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────┘
```

### 优先级方案

| 优先级 | 用途 | 匹配字段 |
|--------|------|---------|
| 0 | table-miss → CONTROLLER | (all) |
| 10 | 老鼠流 ECMP 规则 | 5-tuple (eth_type, ipv4_src/dst, ip_proto, tcp/udp_src/dst) |
| 10 | MAC-only 回退规则 | eth_dst only |
| 30 | 大象流 ML 专用路径 | 5-tuple (同上) |

### 大象流检测参数

| 参数 | 值 | 说明 |
|------|-----|------|
| `ELEPHANT_THRESHOLD` | 1 Mbps | 流速率超过此值判定为大象流 |
| `FLOW_IDLE_TIMEOUT_MICE` | 60s | 老鼠流规则自动过期 |
| `FLOW_IDLE_TIMEOUT_ELEPHANT` | 300s | 大象流规则自动过期 |
| 检测方式 | Packet-In 字节累计 | 0.5s 最小观测窗口后判定 |

---

## 项目目录结构

```
/home/yang/SDN/
├── topo/                        # Mininet 拓扑脚本
│   ├── fat_tree_topo.py         # Fat-Tree k=4 拓扑生成器 ✅
│   └── dual_path_topo.py        # 旧双路径拓扑（保留兼容）
├── controller/                  # Ryu 控制器代码
│   ├── base_controller.py       # L2 学习交换机（对照基准）✅
│   ├── topology_manager.py      # NetworkX 拓扑管理器 ✅
│   │                              - Yen's K-Shortest Paths
│   │                              - has_path() / select_ecmp_path()
│   │                              - 加权图 + 生成树
│   ├── weight_engine.py         # DynamicWeightEngine ✅
│   │                              - ML 加权链路代价
│   │                              - 逐链路 RF 模型加载/推理
│   ├── stats_mixin.py           # 端口统计采集 Mixin ✅
│   │                              - 自适应轮询
│   │                              - Fat-Tree 链路标签
│   ├── threshold_balancer.py    # 阈值响应式负载均衡（对照组）✅
│   │                              - K 路径 + TopologyManager
│   │                              - 阈值决策 (util > 70%)
│   └── predictive_balancer.py   # AI 预测式负载均衡（实验组）✅
│                                  - K 路径 + DynamicWeightEngine
│                                  - 大小流分离 (5-tuple)
│                                  - 老鼠流 ECMP + 大象流 ML 路径
├── scripts/                     # 流量生成、数据采集、模型训练
│   ├── traffic_gen.py           # 动态流量生成器 ✅
│   ├── collect_training_data.py # 自动批量数据采集 ✅
│   ├── assemble_features.py     # 逐链路特征组装 ✅
│   └── train_model.py           # 逐链路模型训练 ✅
├── data/                        # 实验数据
│   ├── traffic_data.csv         # 采集工作文件
│   ├── traffic_data_*.csv       # 分批次原始数据
│   └── training_features.csv    # 组装后的训练特征
├── models/                      # ML 模型文件
│   └── model_link_{name}.pkl   # 逐链路 RF 模型 ✅ (e.g. model_link_edge_s1_p1.pkl)
├── figures/                     # 可视化图表
├── docs/
│   └── superpowers/plans/
│       ├── 2026-05-08-sdn-load-balancer.md  # 本文件
│       └── 2026-05-18-fat-tree-upgrade.md   # Fat-Tree 升级方案
└── README.md
```

---

## 环境准备

### 已验证的环境

- Windows 11 + WSL2 (Ubuntu 24.04)
- VS Code + Remote - WSL 扩展
- Python 3.12 (系统 Python)
- 已安装：Mininet、Ryu、OVS、iperf、networkx、matplotlib、numpy、scikit-learn

### 依赖安装

```bash
# ML 训练与推理
pip3 install --break-system-packages scikit-learn joblib networkx

# Ryu (需要降级 setuptools)
pip3 install --break-system-packages setuptools==67.8.0
pip3 install --break-system-packages ryu

# 验证
python3 -c "import ryu, sklearn, networkx, joblib; print('All imports OK')"
```

### 环境验证

```bash
# 验证 Mininet
sudo mn --test pingall
# 预期：0% dropped

# 验证 Ryu
ryu-manager --version

# 验证 Python 依赖
python3 -c "import ryu, sklearn, networkx, joblib; print('All imports OK')"
```

---

## Phase 1：Fat-Tree 拓扑设计与构建 ✅

**状态：已完成**

### 1.1 Fat-Tree k=4 拓扑生成器

**文件：** `topo/fat_tree_topo.py`

Fat-Tree k=4 提供：
- 16 个主机（4 Pod × 2 Edge/Pod × 2 Host/Edge）
- 8 个 Edge 交换机（DPID 1-8）
- 8 个 Aggregation 交换机（DPID 9-16）
- 4 个 Core 交换机（DPID 17-20）
- 每对跨 Pod 主机间有 4 条等价路径（通过不同 Core 交换机）

**关键函数：**
- `_edge_dpid(pod, idx)` / `_agg_dpid(pod, idx)` / `_core_dpid(idx)` — DPID 映射
- `create_topology()` — 返回 `(net, controller)` 元组
- `cleanup()` — 清理所有 OVS 交换机

**链路带宽：**
- Access（主机↔Edge）：10 Mbps（TCLink 要求必须指定带宽）
- Edge↔Aggregation：10 Mbps
- Aggregation↔Core：10 Mbps

### 1.2 验证

```bash
python3 -c "from topo.fat_tree_topo import create_topology, cleanup; print('Import OK')"
```

---

## Phase 2：TopologyManager — 加权图 + K-Shortest Path ✅

**状态：已完成**

**文件：** `controller/topology_manager.py`

### 2.1 核心数据结构

- `self.G` — NetworkX DiGraph，节点=交换机 DPID，边=物理链路
- `self.host_table` — MAC → (dpid, port) 主机位置表
- `self.link_ports` — (src_dpid, dst_dpid) → out_port 端口映射

### 2.2 已实现的图论算法

| 方法 | 功能 |
|------|------|
| `has_path(src, dst)` | `nx.has_path()` 预检，无异常捕获 |
| `compute_k_shortest_paths(src, dst, k, weight)` | Yen's K-Shortest Path 算法 |
| `select_ecmp_path(flow_tuple, k)` | 5 元组哈希 → 路径索引 |
| `set_edge_weight(src, dst, w)` / `get_edge_weight()` | 动态边权管理 |
| `path_to_ports(path)` | 节点路径 → 端口映射 |
| `get_path_util_keys(fwd, rev)` | 提取 (dpid, port) 集合用于利用率统计 |
| `compute_spanning_tree_ports()` | 最小生成树用于无环洪泛 |
| `compute_edge_disjoint_paths()` | 旧版兼容（内部调用 K-Shortest） |

### 2.3 Yen's 算法实现要点

- 使用 `nx.shortest_path` 作为基础最短路径预言机
- 候选路径用 min-heap 维护，按代价排序
- Spur path 计算时临时移除已确认路径的边，计算后恢复
- `nx.has_path()` 预检替代 `try/except nx.NetworkXNoPath`
- 返回 `[(path, cost), ...]` 按代价升序排列

### 2.4 测试

```bash
# 原有 10 个测试 + Fat-Tree 2 个新测试
python3 controller/test_topology_manager.py
```

---

## Phase 3：DynamicWeightEngine — ML 加权链路代价 ✅

**状态：已完成**

**文件：** `controller/weight_engine.py`

### 3.1 权重公式

```
w = α × hop_cost + β × current_util + γ × predicted_util
  = 1.0 × 1.0    + 2.0 × current   + 3.0 × predicted
```

- `hop_cost = 1.0`（常量，每跳基础代价）
- `current_util` = 当前链路利用率 [0, 1]
- `predicted_util` = ML 预测的下一周期利用率 [0, 1]
- γ=3.0 权重最高，意味着 ML 预测对路径选择影响最大

### 3.2 核心方法

| 方法 | 功能 |
|------|------|
| `load_models(model_dir)` | 加载 `model_link_{name}.pkl` 逐链路 RF 模型（按 `_{dpid}_{port}` 解析文件名） |
| `register_link(dpid, port)` | 注册链路用于监控 |
| `update_utilization(dpid, port, util)` | 更新当前利用率 + 喂入特征队列 |
| `predict_all()` | 对所有有足够数据的链路运行 ML 推理 |
| `compute_weight(src, src_port, dst, dst_port)` | 计算单条边的动态权重 |
| `apply_weights_to_topology(topo)` | 批量更新 TopologyManager 图中的所有边权 |
| `get_state_summary()` | 返回引擎状态摘要 |

### 3.3 逐链路模型

- 每条骨干链路训练一个独立的 RandomForestRegressor
- 模型文件命名：`model_link_{safe_name}.pkl`（safe_name 由 link_label 中的空格/斜杠替换为下划线）
- `weight_engine.py` 的 `load_models()` 按 `_{dpid}_{port}` 解析文件名中的两个数字段
- 特征：滑动窗口（WINDOW_SIZE=3）的近期利用率值
- 预测：下一周期的链路利用率

---

## Phase 4：StatsMixin — 端口统计采集 ✅

**状态：已完成**

**文件：** `controller/stats_mixin.py`

### 4.1 核心功能

- 每个轮询周期向所有交换机发送 `OFPPortStatsRequest`
- 计算每条链路的瞬时利用率：`util = delta_bytes * 8 / (delta_time * LINK_BW)`
- 写入 `data/traffic_data.csv`（timestamp, dpid, port_no, utilization, link_label）
- 自适应轮询：idle (<30%) → 5s, normal → 3s, warning (>50%) → 1s

### 4.2 Fat-Tree 链路标签

```python
def _get_link_label(self, dpid, port_no):
    if self.topo_manager is None:
        return f"s{dpid}_p{port_no}"
    if self.topo_manager.is_edge_port(dpid, port_no):
        return f"s{dpid}_p{port_no}_edge"      # 主机接入端口
    if hasattr(self, '_path_util_keys'):
        for path_name, keys in self._path_util_keys.items():
            if (dpid, port_no) in keys:
                return f"path_{path_name}"     # 活跃路径标签
    if dpid <= 8:   return f"edge_s{dpid}_p{port_no}"   # Edge 层
    if dpid <= 16:  return f"agg_s{dpid}_p{port_no}"    # Agg 层
    return f"core_s{dpid}_p{port_no}"                    # Core 层
```

这些标签同时用作模型文件名的来源：`train_model.py` 将 `link_label` 中的空格/斜杠替换为下划线，生成 `model_link_{safe_name}.pkl`。

---

## Phase 5：阈值响应式负载均衡控制器（对照组）✅

**状态：已完成**

**文件：** `controller/threshold_balancer.py`

### 5.1 架构

```
threshold_balancer.py
├── TopologyManager              # 拓扑发现 + K 路径计算
├── __init__                     # 双路径缓存 (path_fwd/rev A/B)
├── switch_features_handler()    # table-miss 规则
├── packet_in_handler()          # ARP 单播 + host 学习 + 数据包转发
├── _compute_paths()             # K-Shortest Paths (k=2, weight=None)
├── _install_full_path()         # 在路径交换机安装 eth_dst 流表
├── _switch_path()               # 先建后拆（Make-Before-Break）
├── _decision_loop()             # util > 70% → 切换到另一条路径
└── topology event handlers      # LLDP 邻居发现
```

### 5.2 与 PredictiveBalancer 的区别

| | threshold_balancer | predictive_balancer |
|---|---|---|
| 路径计算 | `compute_k_shortest_paths(k=2, weight=None)` | `compute_k_shortest_paths(k=3, weight='weight')` |
| 权重 | 无（纯跳数） | DynamicWeightEngine (α·hop + β·cur + γ·pred) |
| 决策 | 当前 util > 70% | ML 预测 + 大象流专用路径 |
| 流表匹配 | eth_dst only | 5-tuple (大象流/老鼠流) + eth_dst (回退) |
| ECMP | 无 | 有（老鼠流 hash 分流，大象流 ML 专用路径） |

---

## Phase 6：AI 预测式负载均衡控制器 + 大小流分离（核心创新）✅

**状态：已完成**

**文件：** `controller/predictive_balancer.py`

### 6.1 架构总览

```
predictive_balancer.py
├── TopologyManager + DynamicWeightEngine
├── __init__                     # K 路径缓存 + flow_table {bytes, first_seen, last_seen, path_fwd, is_elephant}
├── switch_features_handler()    # table-miss 规则
├── packet_in_handler()          # ARP + 5-tuple 解析 + ECMP/大象流路由
│   ├── _parse_flow_tuple()      # IPv4/TCP/UDP → 5 元组
│   ├── _update_flow_state()     # 字节累计 → 大象流检测
│   ├── _install_flow_rule()     # 5-tuple 流表安装 (priority 10/30)
│   ├── _select_elephant_path()  # ML 权重选路
│   └── _migrate_elephant_flow() # 大象流迁移 (DELETE_STRICT + 安装)
├── _compute_and_install_paths() # K-Shortest Paths (k=3, weight='weight')
├── _install_full_path_dynamic() # MAC-only 路径安装（K 条候选路径 + 回退用）
├── _switch_path()               # 先建后拆
├── _decision_loop()             # ML 预测 + 大象流监控
│   └── _check_elephant_flows()  # 检查大象流是否需要迁移
└── topology event handlers      # LLDP 邻居发现
```

### 6.2 Packet-In 处理流程

```
收到 Packet-In
  │
  ├─ LLDP/IPv6 → 丢弃
  ├─ ARP → 代理回复 / 生成树洪泛
  │
  └─ IPv4 数据包
       │
       ├─ 解析 5 元组 (src_ip, dst_ip, proto, src_port, dst_port)
       ├─ 更新流状态 (字节累计)
       │
       ├─ 大象流? (rate > 1 Mbps)
       │    ├─ ML 权重选路 → _select_elephant_path()
       │    ├─ 安装 priority 30 规则 → _migrate_elephant_flow()
       │    └─ 转发包
       │
       └─ 老鼠流
            ├─ ECMP 选路 → select_ecmp_path(hash(5-tuple), K)
            ├─ 安装 priority 10 规则 → _install_flow_rule()
            └─ 转发包
```

### 6.3 大象流迁移机制

1. 决策循环每轮调用 `_check_elephant_flows()`
2. 遍历 `flow_table` 中所有大象流
3. 用最新 ML 权重重新计算最优路径
4. 如果新路径与当前路径不同：
   - 用 `OFPFC_DELETE_STRICT` 删除旧的 priority 30 规则
   - 在新路径所有交换机上安装 priority 30 规则
   - 更新 `flow_rules_installed` 集合
5. 清理超时（300s）的空闲大象流条目

### 6.4 ECMP 老鼠流

- 使用 `hash(flow_tuple) % K` 选择路径索引
- 在当前交换机上安装 5-tuple 匹配的 priority 10 规则
- idle_timeout=60s 自动过期，防止流表溢出
- 不同老鼠流哈希到不同路径，实现跨 K 条路径的负载分散

### 6.5 决策循环

```
每 curr_poll_interval 秒:
  1. 喂入当前利用率到 DynamicWeightEngine
  2. 运行 ML 推理 (predict_all)
  3. 重新计算边权 (apply_weights_to_topology)
  4. 评估 K 条路径代价，选最优
  5. 如果最优路径 ≠ 当前路径 → _switch_path()
  6. 检查大象流是否需要迁移 → _check_elephant_flows()
```

---

## Phase 7：数据采集与模型训练 ✅

**状态：已完成**

### 7.1 数据采集

**文件：** `scripts/collect_training_data.py`

```bash
sudo python3 scripts/collect_training_data.py
# 10 批次 × 120s，约 28 分钟
# 使用 Fat-Tree 拓扑 + threshold_balancer 控制器
# 多对跨 Pod 主机流量：h0_0→h1_0, h0_1→h3_0, h2_0→h3_1
```

### 7.2 特征组装

**文件：** `scripts/assemble_features.py`

- 逐链路滑动窗口特征（WINDOW_SIZE=3）
- 过滤 edge 端口（只保留骨干链路）
- 按 `link_label` 分组，每个链路独立组装
- 输出：`data/training_features.csv`

### 7.3 模型训练

**文件：** `scripts/train_model.py`

- 每条骨干链路训练一个 RandomForestRegressor
- TimeSeriesSplit 交叉验证（temporal split，无 shuffle）
- GridSearchCV 超参数调优
- 输出：`models/model_link_{safe_name}.pkl`

### 7.4 完整训练流程

```bash
# Step 1: 采集训练数据（约 28 分钟）
sudo python3 scripts/collect_training_data.py

# Step 2: 组装逐链路特征
cd scripts && python3 assemble_features.py

# Step 3: 训练逐链路模型
cd scripts && python3 train_model.py

# Step 4: 验证模型文件
ls -la ../models/model_link_*.pkl
```

---

## Phase 8：对照实验与结果分析

**状态：待执行**

### 8.1 三阶段对照实验设计

| 实验 | 控制器 | 预期行为 | 对比意义 |
|------|--------|---------|---------|
| **Exp A: 无负载均衡** | `base_controller.py` | 流量全走单一路径，其他路径空闲 | 证明 LB 的必要性 |
| **Exp B: 阈值响应式** | `threshold_balancer.py` | 拥塞后才切换，K 路径但无 ECMP | 传统方法的局限 |
| **Exp C: AI 预测式** | `predictive_balancer.py` | 大象流提前切换 + 老鼠流 ECMP 分散 | **核心创新** |

### 8.2 度量指标

| 指标 | Exp A | Exp B | Exp C | 说明 |
|------|-------|-------|-------|------|
| 路径利用率分布 | 单路径满载 | 双路径交替 | K 路径分散 | 负载均衡程度 |
| 首次切换时间 | N/A | 拥塞后 | 拥塞前 | **AI 提前量** |
| 大象流检测 | 无 | 无 | 有 | 大小流分离效果 |
| ECMP 分散 | 无 | 无 | 有 | 老鼠流跨路径分散 |
| 平均吞吐量 | 低 | 中 | 高 | 高负载下平稳度 |
| 丢包率 | 高 | 中 | 低 | **核心对比指标** |

### 8.3 运行实验

```bash
# Exp A: 基线
ryu-manager controller/base_controller.py --observe-links 2>&1 | tee data/expA.log
sudo python3 scripts/collect_training_data.py

# Exp B: 阈值
ryu-manager controller/threshold_balancer.py --observe-links 2>&1 | tee data/expB.log
sudo python3 scripts/collect_training_data.py

# Exp C: AI + 大小流分离
ryu-manager controller/predictive_balancer.py --observe-links 2>&1 | tee data/expC.log
sudo python3 scripts/collect_training_data.py
```

### 8.4 结果可视化

```bash
python3 scripts/plot_results.py
# 生成 figures/utilization_comparison.png
# 生成 figures/prediction_accuracy.png
```

---

## 附录 A：常见问题排查

### Q: Ryu 启动失败？

```bash
# 检查 eventlet 兼容性
python3 -c "from eventlet.wsgi import ALREADY_HANDLED"

# 如果报错，需要 patch ryu/app/wsgi.py
# 添加 try/except 处理 ALREADY_HANDLED 缺失
```

### Q: ryu-manager 找不到？

```bash
which ryu-manager
# 如果在 /usr/local/bin/ryu-manager，确保 PATH 包含该目录

# 或直接使用绝对路径
/usr/local/bin/ryu-manager controller/predictive_balancer.py --observe-links
```

### Q: networkx 缺失？

```bash
sudo pip3 install --break-system-packages networkx
```

### Q: pingall 丢包率高？

确认使用我们的控制器（有显式路径安装或生成树洪泛），而非 Ryu 自带的 simple_switch。

### Q: 模型文件不存在？

```bash
# 确认训练流程已完成
ls -la models/model_link_*.pkl

# 如果没有，先执行训练
sudo python3 scripts/collect_training_data.py
cd scripts && python3 assemble_features.py
cd scripts && python3 train_model.py
```

---

## 附录 B：命令速查表

### Mininet 命令

| 命令 | 说明 |
|------|------|
| `pingall` | 测试全网连通性 |
| `iperf h1 h3` | TCP 吞吐量测试 |
| `sh ovs-ofctl show s1 -O OpenFlow13` | 查看 s1 端口信息 |
| `sh ovs-ofctl dump-flows s1 -O OpenFlow13` | 查看 s1 流表 |
| `sh ovs-ofctl dump-ports s1 -O OpenFlow13` | 查看 s1 端口统计 |

### Ryu 命令

| 命令 | 说明 |
|------|------|
| `ryu-manager controller/base_controller.py --observe-links` | 基础控制器 |
| `ryu-manager controller/threshold_balancer.py --observe-links` | 阈值负载均衡 |
| `ryu-manager controller/predictive_balancer.py --observe-links` | AI 负载均衡 + 大小流分离 |

### Python 脚本

| 命令 | 说明 |
|------|------|
| `sudo python3 scripts/collect_training_data.py` | 自动批量数据采集（10 批次） |
| `cd scripts && python3 assemble_features.py` | 组装逐链路训练特征 |
| `cd scripts && python3 train_model.py` | 训练逐链路 ML 模型 |
| `python3 scripts/plot_results.py` | 生成对比图表 |

---

## 附录 C：开发检查点清单

- [x] **Phase 1：** Fat-Tree k=4 拓扑生成器完成
- [x] **Phase 2：** TopologyManager — Yen's K-Shortest Paths + has_path() + select_ecmp_path()
- [x] **Phase 3：** DynamicWeightEngine — ML 加权链路代价
- [x] **Phase 4：** StatsMixin — Fat-Tree 链路标签 + 自适应轮询
- [x] **Phase 5：** threshold_balancer — K 路径 + TopologyManager 对照组
- [x] **Phase 6：** predictive_balancer — K 路径 + DynamicWeightEngine + 大小流分离
- [x] **Phase 7：** 数据采集 + 特征组装 + 逐链路模型训练
- [ ] **Phase 8：** 三阶段对照实验 + 结果可视化
- [ ] **收尾：** 截图、录屏、数据文件整理完毕

---

## 附录 D：与旧版设计的关键差异

| 维度 | 旧版（4-node diamond） | 新版（Fat-Tree k=4） |
|------|----------------------|---------------------|
| 拓扑 | 4 交换机 + 4 主机 | 20 交换机 + 16 主机 |
| 路径数 | 2 条固定路径 | K 条动态计算路径（Yen's） |
| 路径算法 | Suurballe edge-disjoint | Yen's K-Shortest Path |
| 边权 | 无（纯跳数） | 动态 ML 加权 (α·hop + β·cur + γ·pred) |
| ML 模型 | 逐路径 (model_path_A/B.pkl) | 逐链路 (model_link_{safe_name}.pkl) |
| 流表匹配 | eth_dst only | 5-tuple (大象流) + eth_dst (回退) |
| ECMP | 无 | 有（老鼠流 hash 分流） |
| 大象流检测 | 无 | 有（1 Mbps 阈值） |
| 决策引擎 | DecisionEngine (EMA + 状态机) | DynamicWeightEngine + flow_table |
| 拓扑管理 | 硬编码端口映射 | TopologyManager (NetworkX) |
