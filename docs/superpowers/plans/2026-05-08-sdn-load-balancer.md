# SDN AI-Powered 动态负载均衡调度器 — 完整实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 基于 Ryu 控制器 + Mininet Fat-Tree k=4 数据中心拓扑，实现一个"AI 预测驱动的主动式动态负载均衡调度器"。通过 OpenFlow 1.3 Group Table（SELECT 类型）实现 ECMP 多路径转发，结合全局 MLP 神经网络预测链路拥塞趋势，在拥塞发生**之前**动态调整 Group Table 权重。通过三阶段对照实验（无负载均衡 → 阈值响应式 → AI 预测式）验证 AI 赋能的优势。

**Architecture:** Mininet 构建 Fat-Tree k=4 拓扑（20 交换机 + 16 主机，4 个 Pod），Ryu 控制器作为 SDN 控制平面。三个控制器均使用静态预安装的 `eth_dst` 流表规则 + OpenFlow Group Table（SELECT 类型，端口 3/4 作为 uplink bucket）实现多路径转发。`BaseBalancer` 基类提取公共逻辑（静态拓扑注入、Group Table 创建、流表安装、统计采集）。`ThresholdBalancer` 和 `PredictiveBalancer` 继承并实现差异化策略：阈值控制器使用迟滞阈值（70%/30%）响应式调整 Group 权重；预测控制器加载全局 MLP 模型，通过原生 NumPy 前向传播（零 sklearn 运行时依赖）推理，结合指数可用带宽分配 + 5% 死区防振荡机制动态调整 Group 权重。

**Tech Stack:** Python 3.12 / Ryu SDN Framework / Mininet / Open vSwitch / OpenFlow 1.3 / scikit-learn (MLPRegressor, 训练阶段) / numpy (推理阶段) / joblib / pandas / matplotlib

**项目定位：AI 赋能的 SDN 数据中心流量工程原型**

本项目解决一个问题：**Fat-Tree 数据中心多路径拥塞时的动态负载均衡**。与传统"阈值触发"方案不同，本项目引入：
1. **全局 MLP 链路利用率预测**：一个模型同时预测所有骨干链路的下一周期利用率
2. **指数可用带宽分配**：`weight ∝ exp(-3 × effective_util)`，effective_util = 0.4×current + 0.6×predicted
3. **主动预防式权重调整**：ML 预测拥塞趋势，提前调整 Group Table 权重引导流量
4. **原生 NumPy 推理**：推理阶段直接提取 MLP 权重矩阵做前向传播，零 sklearn 依赖，低延迟

核心创新在于：telemetry → MLP prediction → exponential weight allocation → preemptive group table modification。

**三个控制器的角色：**

| 控制器 | 角色 | 架构 | 对比意义 |
|--------|------|------|---------|
| `base_controller.py` | 基准对照（无负载均衡） | 静态 eth_dst 流表 + ECMP Group Table（50/50 固定权重） | 证明动态 LB 的必要性 |
| `threshold_balancer.py` | 对照组（阈值响应式） | 继承 BaseBalancer + DynamicWeightEngine（无 ML）+ 迟滞阈值决策 | 传统方法的延迟响应 |
| `predictive_balancer.py` | 实验组（AI 预测式） | 继承 BaseBalancer + DynamicWeightEngine（全局 MLP）+ 指数权重分配 | **核心创新** |

实验对比维度：`无 LB` vs `阈值 LB` vs `AI LB`，突出 AI 预测的**提前切换能力**和**高负载下的丢包率降低**。

---

## 评分标准对齐检查表

| 评分项 | 占比 | 本计划覆盖点 |
|--------|------|-------------|
| 报告（简介、原理、设计实现、结果分析、见解） | 60% | MLP 模型原理、指数可用带宽分配、动态 Group Table 权重、三阶段对照实验 |
| 附件（源代码、数据、演示视频/录屏、运行说明） | 30% | 完整 ML 流水线代码、全局 MLP 模型、Fat-Tree 拓扑、实验结果 CSV |
| 心得体会 | 10% | 不在本计划范围内，自行撰写 |

**课程要求关键条款对照：**
- "能够实现基本的功能，允许不完善，但要可运行，能够通过自测用例验证" — 每个环节末尾给出验证方式
- "如果明确说明不完善地方，不会扣分；若分析到位，反而会考虑酌情加分" — 冷启动回退、死区防振荡、权重分配策略等工程权衡可在报告中深入分析
- "允许在已有框架下二次开发，但必须说明自己的开发工作体现在哪" — 基于 Ryu 框架开发，DynamicWeightEngine、全局 MLP 推理、指数权重分配为自研
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

链路带宽: access = 10 Mbps, edge↔agg = 10 Mbps, agg↔core = 2 Mbps
交换机 DPID: edge=1..8, aggregation=9..16, core=17..20
主机命名: h{pod}_{idx} (h0_0 ~ h3_3)
```

### 转发架构

```
┌─────────────────────────────────────────────────────────┐
│                  Ryu Controller                          │
│  ┌───────────────────────────────────────────────────┐  │
│  │     BaseBalancer (公共基类)                        │  │
│  │  - Group Table 创建 (SELECT, port 3/4 buckets)    │  │
│  │  - eth_dst 流表安装 (priority 10)                 │  │
│  │  - StatsMixin 端口统计采集                        │  │
│  └───────────────────────────────────────────────────┘  │
│  ┌──────────────────┐  ┌─────────────────────────────┐  │
│  │ DynamicWeightEngine│  │  Global MLP Model          │  │
│  │ exp(-3×eff_util)  │  │  global_mlp_model.pkl       │  │
│  │ 5% deadband       │  │  NumPy 前向传播 (推理)      │  │
│  └──────────────────┘  └─────────────────────────────┘  │
│  ┌───────────────────────────────────────────────────┐  │
│  │     Group Table 权重动态调整                       │  │
│  │  - OFPGT_SELECT: port 3 (uplink A) + port 4 (B)  │  │
│  │  - OFPGC_MODIFY: 按需更新 bucket weights          │  │
│  └───────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────┘
```

### 流表与 Group Table 方案

| 组件 | 用途 | 匹配/类型 |
|------|------|----------|
| Flow Table (priority 10) | eth_dst → output port / group | eth_dst only |
| Group Table (id=1, SELECT) | ECMP uplink 选择 | port 3 + port 4, weight 动态 |
| dp_hash 选择方法 | OVS 哈希分流 | `group-table-selection-method=dp_hash` |

---

## 项目目录结构

```
/home/yang/SDN/
├── topo/                          # Mininet 拓扑脚本
│   └── fat_tree_topo.py           # Fat-Tree k=4 拓扑生成器 ✅
│                                    - create_topology() → (net, c0)
│                                    - configure_select_hash() → OVS dp_hash
│                                    - cleanup()
├── controller/                    # Ryu 控制器代码
│   ├── stats_mixin.py             # 端口统计采集 Mixin ✅
│   │                                - 固定 0.5s 轮询
│   │                                - 按层自适应带宽 (edge=10M, agg_down=10M, agg_up/core=2M)
│   │                                - 写入 data/traffic_data.csv
│   ├── base_balancer.py           # 负载均衡控制器公共基类 ✅
│   │                                - Group Table 创建 (SELECT, port 3/4)
│   │                                - eth_dst 流表安装 (priority 10)
│   │                                - add_flow() 辅助
│   ├── base_controller.py         # L2 基线控制器（对照基准）✅
│   │                                - 静态 eth_dst 流表 + ECMP Group Table (50/50)
│   ├── weight_engine.py           # DynamicWeightEngine ✅
│   │                                - 全局 MLP 模型加载
│   │                                - 原生 NumPy 前向传播推理
│   │                                - 指数可用带宽分配 + 5% 死区
│   ├── threshold_balancer.py      # 阈值响应式负载均衡（对照组）✅
│   │                                - 继承 BaseBalancer
│   │                                - DynamicWeightEngine (model_path=None, 无 ML)
│   │                                - 迟滞阈值: CONGESTION=0.70, RECOVERY=0.30
│   └── predictive_balancer.py     # AI 预测式负载均衡（实验组）✅
│                                    - 继承 BaseBalancer
│                                    - DynamicWeightEngine (加载 global_mlp_model.pkl)
│                                    - 每轮: update → predict → get_group_weights → modify
├── scripts/                       # 流量生成、数据采集、模型训练、实验
│   ├── collect_training_data.py   # 训练数据采集 ✅
│   │                                - 2000 轮 × 8s，随机配对 16 主机
│   │                                - 使用 threshold_balancer 控制器
│   ├── assemble_global_features.py # 全局特征组装 ✅
│   │                                - 滑动窗口 WINDOW=6, PRED_STEP=2, TARGET_WIN=3
│   │                                - 过滤 edge 端口，pivot → 时序矩阵
│   │                                - 输出 data/global_features.pkl
│   ├── train_global_mlp.py        # 全局 MLP 训练 ✅
│   │                                - MLPRegressor (128, 64), StandardScaler
│   │                                - 输出 models/global_mlp_model.pkl
│   └── run_experiment.py          # 三组对照实验 ✅
│                                    - L2 / Threshold / Predictive
│                                    - 概率哈希碰撞 + 渐进突发
│                                    - 多轮迭代 + CSV 输出
├── data/                          # 实验数据
│   ├── traffic_data.csv           # 采集的原始遥测数据
│   ├── global_features.pkl        # 组装后的训练特征
│   ├── l2_average_results.csv     # L2 基线平均结果
│   ├── l2_iteration_results.csv   # L2 基线逐轮结果
│   ├── threshold_average_results.csv
│   ├── threshold_iteration_results.csv
│   ├── predictive_average_results.csv
│   ├── predictive_iteration_results.csv
│   ├── ryu_l2.log / ryu_threshold.log / ryu_predictive.log
│   └── screenshot/                # 环境截图
├── models/                        # ML 模型文件
│   └── global_mlp_model.pkl       # 全局 MLP 模型 ✅ (~745KB)
├── figures/                       # 可视化图表（待生成）
├── docs/
│   └── superpowers/plans/
│       └── 2026-05-08-sdn-load-balancer.md  # 本文件
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
pip3 install --break-system-packages scikit-learn joblib networkx numpy pandas

# Ryu (需要降级 setuptools)
pip3 install --break-system-packages setuptools==67.8.0
pip3 install --break-system-packages ryu

# 验证
python3 -c "import ryu, sklearn, networkx, joblib, numpy, pandas; print('All imports OK')"
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
- `create_topology(controller_ip, controller_port)` — 返回 `(net, c0)` 元组
- `configure_select_hash()` — 设置 OVS Group Table 的 dp_hash 选择方法
- `cleanup()` — 清理所有 OVS 交换机和 iperf 进程

**链路带宽：**
- Access（主机↔Edge）：10 Mbps
- Edge↔Aggregation：10 Mbps
- Aggregation↔Core：2 Mbps（瓶颈链路，总横截带宽 = 4 × 2 = 8 Mbps）

### 1.2 验证

```bash
python3 -c "from topo.fat_tree_topo import create_topology, cleanup; print('Import OK')"
```

---

## Phase 2：StatsMixin — 端口统计采集 ✅

**状态：已完成**

**文件：** `controller/stats_mixin.py`

### 2.1 核心功能

- 每 0.5s 向所有交换机发送 `OFPPortStatsRequest`
- 计算每条链路的瞬时利用率：`util = delta_bytes * 8 / (delta_time * LINK_BW)`
- 写入 `data/traffic_data.csv`（timestamp, dpid, port_no, utilization, link_label）
- 固定轮询间隔 0.5s（无自适应调整）

### 2.2 按层带宽计算

```python
def _get_port_bandwidth(self, dpid, port_no):
    if dpid <= 8:                    # Edge 层
        return 10_000_000            # 所有端口 10 Mbps
    elif dpid <= 16:                 # Aggregation 层
        if port_no in [1, 2]:
            return 10_000_000        # 下行端口 10 Mbps
        return 2_000_000             # 上行端口 2 Mbps
    return 2_000_000                 # Core 层 2 Mbps
```

### 2.3 链路标签

简化为 `s{dpid}_p{port_no}` 格式，用于训练数据中的链路标识。

---

## Phase 3：BaseBalancer — 负载均衡控制器公共基类 ✅

**状态：已完成**

**文件：** `controller/base_balancer.py`

从 `threshold_balancer.py` 和 `predictive_balancer.py` 中提取的公共代码，消除重复。

### 3.1 公共方法

| 方法 | 功能 |
|------|------|
| `add_flow(datapath, priority, match, actions)` | 流表安装辅助 |
| `port_stats_reply_handler(ev)` | 统计回复处理（代理 StatsMixin） |

### 3.2 三个控制器共享的初始化流程

所有控制器的 `_setup_rules(datapath)` 遵循相同模式：
1. 创建 Group Table（SELECT 类型，port 3 + port 4 作为 bucket，初始 weight=50/50）
2. 安装 16 条 eth_dst 流表规则（priority 10），匹配逻辑：
   - Edge 层（dpid 1-8）：本 Pod 主机 → 直接 output，其他 → Group
   - Agg 层（dpid 9-16）：本 Pod 主机 → 直接 output，其他 → Group
   - Core 层（dpid 17-20）：按 Pod 号 output 到对应端口

---

## Phase 4：DynamicWeightEngine — ML 加权链路代价 ✅

**状态：已完成**

**文件：** `controller/weight_engine.py`

### 4.1 权重公式

```
effective_util = 0.4 × current_util + 0.6 × predicted_util
weight ∝ exp(-3.0 × effective_util)   （指数可用带宽分配）
```

- `current_util` = 当前链路利用率 [0, 1]
- `predicted_util` = MLP 预测的下一周期链路利用率 [0, 1]
- 无 ML 模型时，`effective_util = current_util`（退化为纯当前利用率）

### 4.2 全局 MLP 模型加载与推理

```python
def _load_global_model(self, model_path):
    data = joblib.load(model_path)
    self.global_model = data["model"]        # sklearn MLPRegressor
    self.scaler_X = data["scaler_X"]         # StandardScaler (输入)
    self.scaler_Y = data["scaler_Y"]         # StandardScaler (输出)
    self.link_keys = data["link_keys"]       # [(dpid, port), ...] 骨干链路列表
    self.window_size = data.get("window_size", 6)

    # 提取权重矩阵用于原生 NumPy 推理（零 sklearn 运行时依赖）
    self.mlp_weights = self.global_model.coefs_
    self.mlp_biases = self.global_model.intercepts_
    self.scaler_mean_X = self.scaler_X.mean_
    self.scaler_scale_X = self.scaler_X.scale_
    self.scaler_mean_Y = self.scaler_Y.mean_
    self.scaler_scale_Y = self.scaler_Y.scale_
```

### 4.3 原生 NumPy 前向传播推理

```python
def predict_all(self):
    X = np.array(self.feature_history, dtype=np.float32).ravel()
    X_scaled = (X - self.scaler_mean_X) / self.scaler_scale_X

    activation = X_scaled
    for i in range(len(self.mlp_weights) - 1):    # ReLU 隐藏层
        z = np.dot(activation, self.mlp_weights[i]) + self.mlp_biases[i]
        activation = np.maximum(z, 0.0)

    pred_scaled = np.dot(activation, self.mlp_weights[-1]) + self.mlp_biases[-1]
    pred = pred_scaled * self.scaler_scale_Y + self.scaler_mean_Y
    pred = np.clip(pred, 0.0, 1.0)

    for i, key in enumerate(self.link_keys):
        self.predicted_utils[key] = float(pred[i])
```

### 4.4 指数可用带宽分配 + 死区防振荡

```python
def get_group_weights(self):
    WEIGHT_DEADBAND = 0.05

    for dpid in range(9, 17):          # 仅 Agg 交换机
        for port_no in [3, 4]:         # 上行端口
            effective_util = 0.4 * curr_util + 0.6 * pred_util  # 有 ML 时
            available_list.append(np.exp(-3.0 * effective_util))

        ratios = [a / total for a in available_list]

        # 死区防振荡：变化 < 5% 则跳过
        if last_ratios and max(abs(r - lr) ...) < WEIGHT_DEADBAND:
            continue

        weights = [(port, max(1, int(ratio * 100))) for port, ratio in ...]
```

---

## Phase 5：L2 基线控制器（对照基准）✅

**状态：已完成**

**文件：** `controller/base_controller.py`

### 5.1 架构

```
base_controller.py (继承 StatsMixin)
├── _setup_rules(datapath)
│   ├── 创建 Group Table (SELECT, port 3/4, weight 50/50)
│   └── 安装 16 条 eth_dst 流表 (priority 10)
│       ├── Edge: 本 Pod → output, 其他 → Group
│       ├── Agg:  本 Pod → output, 其他 → Group
│       └── Core: 按 Pod 号 output
└── port_stats_reply_handler → StatsMixin
```

### 5.2 与负载均衡控制器的区别

基线控制器使用固定 50/50 Group Table 权重，不做任何动态调整。流量通过 OVS dp_hash 在两条上行链路间随机分配，无法感知拥塞。

---

## Phase 6：阈值响应式负载均衡控制器（对照组）✅

**状态：已完成**

**文件：** `controller/threshold_balancer.py`

### 6.1 架构

```
threshold_balancer.py (继承 BaseBalancer)
├── BaseBalancer                 # 公共基类：Group Table + 流表
├── DynamicWeightEngine          # 无 ML 模型 (model_path=None)
├── __init__                     # _was_congested = False
├── _setup_rules(datapath)       # 与 BaseBalancer 相同的 Group + 流表
├── _decision_loop()             # 迟滞阈值决策
│   ├── CONGESTION_THRESHOLD = 0.70
│   ├── RECOVERY_THRESHOLD = 0.30
│   ├── max_util > 0.70 → get_group_weights() + MODIFY Group
│   └── max_util < 0.30 (恢复) → 重置为 50/50
└── port_stats_reply_handler → StatsMixin
```

### 6.2 决策循环

```
每 0.5s:
  1. update_all_utilizations(link_utilization)
  2. max_util = max(link_utilization.values())
  3. if max_util > 0.70:
       group_weights = weight_engine.get_group_weights()  # 无 ML
       MODIFY Group Table
       _was_congested = True
  4. elif _was_congested and max_util < 0.30:
       重置所有 Agg 交换机 Group Table 为 50/50
       _was_congested = False
```

---

## Phase 7：AI 预测式负载均衡控制器（核心创新）✅

**状态：已完成**

**文件：** `controller/predictive_balancer.py`

### 7.1 架构

```
predictive_balancer.py (继承 BaseBalancer)
├── BaseBalancer                 # 公共基类
├── DynamicWeightEngine          # 加载 global_mlp_model.pkl
├── __init__                     # 加载 MLP 模型
├── _setup_rules(datapath)       # Group + 流表
├── _decision_loop()             # ML 驱动决策
│   ├── update_all_utilizations → 喂入当前利用率
│   ├── predict_all → NumPy 前向传播
│   ├── get_group_weights → 指数权重分配
│   └── _modify_group_weights → MODIFY Group Table
└── _modify_group_weights(datapath, group_id, weights)
```

### 7.2 决策循环

```
每 0.5s:
  1. weight_engine.update_all_utilizations(link_utilization)
     → 维护滑动窗口 feature_history (window=6)
  2. weight_engine.predict_all()
     → NumPy 前向传播：X → scale → ReLU layers → output → inverse scale → clip
     → 更新 predicted_utils[(dpid, port)]
  3. weight_engine.get_group_weights()
     → 对每个 Agg 交换机 (dpid 9-16):
       effective = 0.4×current + 0.6×predicted
       weight ∝ exp(-3 × effective)
     → 5% 死区防振荡
  4. 对每个需要更新的交换机:
     _modify_group_weights(datapath, group_id=1, weights)
     → OFPGC_MODIFY: 更新 SELECT Group 的 bucket weights
```

### 7.3 与 ThresholdBalancer 的区别

| | threshold_balancer | predictive_balancer |
|---|---|---|
| DynamicWeightEngine | `model_path=None`（无 ML） | 加载 `global_mlp_model.pkl` |
| 利用率计算 | 仅当前 `current_util` | `0.4×current + 0.6×predicted` |
| 决策触发 | `max_util > 0.70` 才行动 | 每轮都调整（指数权重平滑过渡） |
| 恢复机制 | `max_util < 0.30` 重置 50/50 | 权重自然恢复（预测值降低时） |
| 响应延迟 | 拥塞后 2-3s 检测 | 趋势提前识别 |

---

## Phase 8：数据采集与模型训练 ✅

**状态：已完成**

### 8.1 数据采集

**文件：** `scripts/collect_training_data.py`

```bash
sudo python3 scripts/collect_training_data.py
# 2000 轮 × 8s，约 267 分钟（~4.5 小时）
# 使用 Fat-Tree 拓扑 + threshold_balancer 控制器
# 每轮随机配对 16 个主机，产生多样化流量模式
```

### 8.2 全局特征组装

**文件：** `scripts/assemble_global_features.py`

```bash
cd scripts && python3 assemble_global_features.py
```

**特征工程：**
- 过滤 edge 端口（只保留骨干链路：dpid 1-20, port 3/4）
- Pivot: timestamp × (dpid, port_no) → utilization 矩阵
- 滑动窗口：WINDOW_SIZE=6, PREDICTION_STEP=2, TARGET_WINDOW=3
- 输入 X: 6 个时间步 × N 条链路 = 展平向量
- 目标 Y: 未来 3 个时间步内每条链路的最大利用率

### 8.3 全局 MLP 模型训练

**文件：** `scripts/train_global_mlp.py`

```bash
cd scripts && python3 train_global_mlp.py
```

**模型配置：**
- `MLPRegressor(hidden_layer_sizes=(128, 64), activation='relu', solver='adam')`
- `alpha=0.01, max_iter=1000, early_stopping=True, validation_fraction=0.15`
- StandardScaler 对输入 X 和输出 Y 分别标准化
- 80/20 时间序列切分（无 shuffle）
- 输出：`models/global_mlp_model.pkl`（包含 model, scaler_X, scaler_Y, link_keys, window_size）

### 8.4 完整训练流程

```bash
# Step 1: 采集训练数据（约 4.5 小时）
sudo python3 scripts/collect_training_data.py

# Step 2: 组装全局特征
cd scripts && python3 assemble_global_features.py

# Step 3: 训练全局 MLP 模型
cd scripts && python3 train_global_mlp.py

# Step 4: 验证模型文件
ls -la models/global_mlp_model.pkl
```

---

## Phase 9：对照实验与结果分析 ✅

**状态：已完成**

### 9.1 三阶段对照实验设计

| 实验 | 控制器 | 预期行为 | 对比意义 |
|------|--------|---------|---------|
| **Exp A: L2 基线** | `base_controller.py` | 静态 ECMP 50/50，哈希碰撞持续丢包 | 证明动态 LB 的必要性 |
| **Exp B: 阈值响应式** | `threshold_balancer.py` | 拥塞后才切换 Group 权重 | 传统方法的延迟响应 |
| **Exp C: AI 预测式** | `predictive_balancer.py` | MLP 预测 + 指数权重持续调整 | **核心创新** |

### 9.2 实验场景：概率哈希碰撞 + 渐进突发

```
Fat-Tree k=4: Pod 0 ↔ Pod 3 有 4 条等价 Core 路径
总横截带宽 = 4 × 2Mbps = 8Mbps

阶段 1 (t=0s):   启动 9 条 0.5Mbps 背景流 (4.5Mbps, 无拥塞)
阶段 2 (t=20s):  渐进启动突发子流 (3 条 0.25Mbps, 间隔 6s)
  - t=20s: +0.25Mbps → 4.75Mbps
  - t=26s: +0.25Mbps → 5.0Mbps
  - t=32s: +0.25Mbps → 5.25Mbps

哈希碰撞: 背景流占路径，突发流有概率哈希到已占用路径
碰撞时该链路 > 2Mbps → 丢包
```

### 9.3 运行实验

```bash
# 运行全部三组（默认每组 5 轮迭代）
sudo python3 scripts/run_experiment.py --group all --iters 5

# 单独运行某组
sudo python3 scripts/run_experiment.py --group l2 --iters 5
sudo python3 scripts/run_experiment.py --group threshold --iters 5
sudo python3 scripts/run_experiment.py --group predictive --iters 5
```

### 9.4 实验结果

**L2 基线（静态 ECMP 50/50）：**

| 流 | 平均丢包率 | 平均抖动 | 平均带宽 |
|----|-----------|---------|---------|
| Flow 1-9 | 5.3% ~ 49.3% | 4.1 ~ 13.7 ms | 0.27 ~ 0.50 Mbps |
| 突发流 | 30.1% | 16.9 ms | 1.11 Mbps |

**阈值响应式：**

| 流 | 平均丢包率 | 平均抖动 | 平均带宽 |
|----|-----------|---------|---------|
| Flow 1-9 | 7.4% ~ 21.5% | 25.2 ~ 34.2 ms | 0.41 ~ 0.49 Mbps |
| 突发流 | 15.6% | 34.6 ms | 1.34 Mbps |

**AI 预测式（全局 MLP）：**

| 流 | 平均丢包率 | 平均抖动 | 平均带宽 |
|----|-----------|---------|---------|
| Flow 1-9 | 4.1% ~ 14.3% | 20.9 ~ 26.0 ms | 0.45 ~ 0.50 Mbps |
| 突发流 | 11.2% | 28.6 ms | 1.41 Mbps |

**关键对比：**
- 丢包率：AI (4-14%) < 阈值 (7-21%) < L2 (5-49%)，AI 降低约 30-50%
- 突发流丢包：AI 11.2% vs 阈值 15.6% vs L2 30.1%
- 带宽利用率：AI 1.41 Mbps > 阈值 1.34 Mbps > L2 1.11 Mbps

### 9.5 结果可视化

待实现：需要编写 `scripts/plot_results.py` 对比三个控制器的实验结果。

```bash
python3 scripts/plot_results.py
# 生成 figures/ 下的对比图表
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
/usr/local/bin/ryu-manager controller/predictive_balancer.py
```

### Q: networkx 缺失？

```bash
sudo pip3 install --break-system-packages networkx
```

### Q: pingall 丢包率高？

确认使用我们的控制器（有显式 Group Table + 流表规则），而非 Ryu 自带的 simple_switch。

### Q: 模型文件不存在？

```bash
# 确认训练流程已完成
ls -la models/global_mlp_model.pkl

# 如果没有，先执行训练
sudo python3 scripts/collect_training_data.py
cd scripts && python3 assemble_global_features.py
cd scripts && python3 train_global_mlp.py
```

### Q: OVS Group Table 不生效？

```bash
# 确认已设置 dp_hash 选择方法
sudo python3 -c "from topo.fat_tree_topo import configure_select_hash; configure_select_hash()"

# 验证 Group Table
sh ovs-ofctl dump-groups s9 -O OpenFlow13
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
| `sh ovs-ofctl dump-groups s1 -O OpenFlow13` | 查看 s1 Group Table |
| `sh ovs-ofctl dump-ports s1 -O OpenFlow13` | 查看 s1 端口统计 |

### Ryu 命令

| 命令 | 说明 |
|------|------|
| `ryu-manager controller/base_controller.py` | L2 基线控制器 |
| `ryu-manager controller/threshold_balancer.py` | 阈值负载均衡 |
| `ryu-manager controller/predictive_balancer.py` | AI 负载均衡 |

### Python 脚本

| 命令 | 说明 |
|------|------|
| `sudo python3 scripts/collect_training_data.py` | 训练数据采集（2000 轮） |
| `cd scripts && python3 assemble_global_features.py` | 组装全局训练特征 |
| `cd scripts && python3 train_global_mlp.py` | 训练全局 MLP 模型 |
| `sudo python3 scripts/run_experiment.py --group all --iters 5` | 三组对照实验 |

---

## 附录 C：开发检查点清单

- [x] **Phase 1：** Fat-Tree k=4 拓扑生成器 + configure_select_hash
- [x] **Phase 2：** StatsMixin — 固定 0.5s 轮询 + 按层带宽计算
- [x] **Phase 3：** BaseBalancer — Group Table 创建 + 流表安装
- [x] **Phase 4：** DynamicWeightEngine — 全局 MLP 加载 + NumPy 推理 + 指数权重分配
- [x] **Phase 5：** base_controller — L2 基线（静态 ECMP 50/50）
- [x] **Phase 6：** threshold_balancer — 迟滞阈值 + DynamicWeightEngine（无 ML）
- [x] **Phase 7：** predictive_balancer — 全局 MLP 预测 + 指数权重 + 死区防振荡
- [x] **Phase 8：** 数据采集 + 全局特征组装 + 全局 MLP 训练
- [x] **Phase 9：** 三阶段对照实验 + CSV 结果输出
- [ ] **收尾：** 结果可视化 (plot_results.py) + 截图 + 录屏 + 数据文件整理

---

## 附录 D：与旧版设计的关键差异

| 维度 | 旧版（计划） | 当前实现 |
|------|-------------|---------|
| ML 模型 | 逐链路 RandomForestRegressor | 全局 MLPRegressor (128, 64) |
| 模型文件 | `model_link_{name}.pkl` (多个) | `global_mlp_model.pkl` (单个) |
| 推理方式 | sklearn predict() | 原生 NumPy 前向传播（零 sklearn 依赖） |
| 路由机制 | Dijkstra 最短路径 + per-flow 流表 | OpenFlow Group Table (SELECT) + eth_dst 流表 |
| 拓扑管理 | TopologyManager (NetworkX DiGraph) | 已删除，静态 DPID 数学规律直接计算 |
| 会话管理 | active_sessions 动态跟踪 | 无（Group Table 统一处理） |
| 权重计算 | `w = α·hop + β·cur + γ·pred` | `weight ∝ exp(-3 × (0.4×cur + 0.6×pred))` |
| 防振荡 | 无 | 5% 死区 (WEIGHT_DEADBAND) |
| 特征窗口 | WINDOW_SIZE=3 | WINDOW_SIZE=6 |
| 预测目标 | 下一周期利用率 | 未来 3 步最大利用率 (TARGET_WINDOW=3) |
| 数据采集轮次 | 10 批次 × 120s | 2000 轮 × 8s |
| 链路带宽 | 全层统一 10 Mbps | access/edge-agg=10M, agg-core=2M |
| 基线控制器 | L2 学习交换机（MAC 学习+泛洪） | 静态 eth_dst + ECMP Group Table |
| 实验脚本 | 手动运行各控制器 | `run_experiment.py` 自动化三组对比 |
