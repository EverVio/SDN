# SDN AI-Powered 动态负载均衡调度器 — 完整实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 基于 Ryu 控制器 + Mininet 仿真，实现一个"AI 预测驱动的主动式动态负载均衡调度器"，通过机器学习模型预测链路拥塞趋势，在拥塞发生**之前**提前重路由，并通过三阶段对照实验（无负载均衡 → 阈值响应式 → AI 预测式）验证 AI 赋能的优势。

**Architecture:** Mininet 构建双路径拓扑（4 交换机 + 4 主机），Ryu 控制器作为 SDN 控制平面。控制器周期性采集端口统计信息，喂入 Random Forest 回归模型预测未来链路利用率，当预测值超阈值时主动将流量从即将拥塞的路径重路由至轻载路径。

**Tech Stack:** Python 3.9 / Ryu SDN Framework 4.34 / Mininet / Open vSwitch 3.3.4 / OpenFlow 1.3 / scikit-learn / joblib / numpy / iperf / Conda (sdn)

**项目定位：AI 赋能的 SDN 动态流量工程原型**

本项目解决一个问题：**双路径拥塞时的动态 reroute**。与传统"阈值触发"方案不同，本项目引入机器学习预测模型，实现**主动预防式**路由切换。核心创新在于：telemetry → ML prediction → preemptive explicit flow install。

**三个控制器的角色：**

| 控制器 | 角色 | 架构 | 对比意义 |
|--------|------|------|---------|
| `base_controller.py` | 基准对照（无负载均衡） | L2 学习交换机：MAC 学习 + 泛洪 | 证明负载均衡的必要性 |
| `threshold_balancer.py` | 对照组（阈值响应式） | 显式路径 + if util>70% 则切换 | 传统方法的延迟响应 |
| `predictive_balancer.py` | 实验组（AI 预测式） | 显式路径 + RF 预测 + EMA + 冷启动 + 冷却锁 | **核心创新** |

实验对比维度：`无 LB` vs `阈值 LB` vs `AI LB`，突出 AI 预测的**提前切换能力**和**高负载下的吞吐量平稳度**。

---

## 评分标准对齐检查表

| 评分项 | 占比 | 本计划覆盖点 |
|--------|------|-------------|
| 报告（简介、原理、设计实现、结果分析、见解） | 60% | AI 模型原理、三阶段对照实验、预测 vs 响应式对比数据 |
| 附件（源代码、数据、演示视频/录屏、运行说明） | 30% | 完整 ML 流水线代码、训练数据、模型文件、可视化图表 |
| 心得体会 | 10% | 不在本计划范围内，自行撰写 |

**课程要求关键条款对照：**
- "能够实现基本的功能，允许不完善，但要可运行，能够通过自测用例验证" — 每个环节末尾给出验证方式
- "如果明确说明不完善地方，不会扣分；若分析到位，反而会考虑酌情加分" — 冷启动回退、冷却锁等工程权衡可在报告中深入分析
- "允许在已有框架下二次开发，但必须说明自己的开发工作体现在哪" — 基于 Ryu 框架开发，AI 模型为自研
- "切忌从网上直接拿一个软件交差" — 本计划仅指导思路，代码需自行编写
- 鼓励方向第 15 条："AI/GNN/DNN/Transformer/LLM 技术在通信网络中的应用" — 本项目直接命中

---

## 拓扑与流量模型总览

```
        ┌──────────────────────────────────┐
        │          Ryu Controller          │
        │  (OpenFlow 控制平面, TCP 6633)   │
        │  ┌────────────────────────────┐  │
        │  │  ML Model (Random Forest)  │  │
        │  │  predict U_{t+1} → reroute │  │
        │  └────────────────────────────┘  │
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

> **注意：** 实际端口号由 Mininet 的 `addLink()` 调用顺序决定。运行后需用 `ovs-ofctl show` 命令验证实际端口映射。

---

## 项目目录结构

```
/root/SDN/
├── topo/                        # Mininet 拓扑脚本
│   └── dual_path_topo.py        # Phase 1: 拓扑库函数（返回 net, c0）
├── controller/                  # Ryu 控制器代码
│   ├── base_controller.py       # Phase 2: L2 学习交换机（对照基准）✅
│   ├── stats_mixin.py           # Phase 3: 端口统计采集 Mixin ✅
│   ├── threshold_balancer.py    # Phase 3: 阈值响应式负载均衡（对照组）✅
│   └── predictive_balancer.py           # Phase 4: AI 预测式负载均衡（实验组）⬜
├── scripts/                     # 流量生成、数据处理、模型训练
│   ├── traffic_gen.py           # Phase 3: 动态流量生成器（含高斯噪声）✅
│   └── run_experiment.py        # Phase 3: 实验编排（拓扑 + 流量生成）✅
├── data/                        # 实验数据
│   ├── traffic_data.csv         # Phase 3: 原始端口统计数据 ✅ (490行)
│   └── screenshot/              # 阶段截图
├── models/                      # ML 模型文件（待训练）
├── figures/                     # 可视化图表（待生成）
├── docs/                        # 文档
│   ├── 遇到的问题.md
│   └── 配置环境.md
└── README.md

Phase 3 ML 训练完成后新增：
├── scripts/assemble_features.py # 特征组装脚本
├── scripts/train_model.py       # 模型训练脚本（RF + CV + GridSearchCV）✅
├── data/training_features.csv   # 训练特征（由 train_model.py 自动生成）✅
├── data/model_evaluation_summary.csv # 模型评估摘要 ✅
├── models/model_path_A.pkl      # 路径 A 预测模型 ✅
├── models/model_path_B.pkl      # 路径 B 预测模型 ✅
└── figures/                     # 可视化图表 ✅
    ├── cv_scores_path_A/B.png        # 交叉验证分数
    ├── learning_curve_path_A/B.png   # 学习曲线
    ├── pred_scatter_path_A/B.png     # 预测散点图（含置信区间）
    ├── feature_importance_path_A/B.png # 特征重要性
    ├── residuals_path_A/B.png        # 残差分析
    ├── error_distribution_path_A/B.png # 误差分布
    └── prediction_timeseries_path_A/B.png # 时间序列对比

Phase 4 完成后新增：
├── data/predictions.csv         # AI 预测值 vs 实际值

Phase 5 完成后新增：
├── scripts/plot_results.py      # 结果可视化脚本
└── figures/*.png                # 对比图表
```

---

## 环境准备

### 已验证的环境

- Windows 11 + WSL2 (Ubuntu 24.04)
- VS Code + Remote - WSL 扩展
- Conda 虚拟环境 `sdn` (Python 3.9)
- 已安装：Mininet、Ryu 4.34、OVS 3.3.4、iperf、networkx、matplotlib、numpy

### 新增依赖安装

```bash
conda activate sdn

# ML 训练与推理
pip install scikit-learn joblib

# 验证
python3 -c "import sklearn, joblib; print('ML deps OK')"
```

### 环境验证

```bash
conda activate sdn

# 验证 Mininet
sudo mn --test pingall
# 预期：0% dropped

# 验证 Ryu
ryu-manager --version
# 预期：ryu-manager 4.34

# 验证 Python 依赖
python3 -c "import ryu, networkx, matplotlib, numpy, sklearn, joblib; print('All imports OK')"
```

> **提醒：** 截图保存所有验证输出，作为环境搭建成功的证据。

---

## Phase 1：Mininet 拓扑设计与构建

**复杂度：** 中 | **难度：** 中 | **预估工程量：** 3-4 小时

### 1.1 拓扑脚本编写

在 `/root/SDN/topo/` 下创建 `dual_path_topo.py`。

**设计说明：** 拓扑脚本设计为**库函数**（返回 `net, c0`），而非独立可执行脚本。调用方（如 `run_experiment.py`）负责 `net.build()`、`net.start()`、`net.stop()`。这样拓扑创建与流量生成可以在同一进程中编排。

**实现要点：**
- `create_topology()` 返回 `(net, c0)` 元组
- 使用 `OVSSwitch` + `TCLink` + `RemoteController`
- 核心链路 `bw=10`（Mbps），接入链路不限速
- `cleanup()` 函数清理 OVS 交换机

**实际代码：** 参见 `/root/SDN/topo/dual_path_topo.py`

### 1.2 运行与验证

**方式 A — 使用 run_experiment.py 自动编排（推荐）：**
```bash
conda activate sdn
cd /root/SDN
# 终端 1：启动 Ryu 控制器
ryu-manager controller/base_controller.py --observe-links

# 终端 2：运行实验编排脚本（自动创建拓扑 + 生成流量）
sudo python3 scripts/run_experiment.py
```

**方式 B — 手动启动拓扑（调试用）：**

需要将 `dual_path_topo.py` 改为独立脚本模式（添加 `if __name__` 入口和 `CLI(net)`），或在 Python 交互式环境中调用。

**验证步骤：**

```bash
mininet> pingall
# 预期：0% dropped (12/12 received)

mininet> net
# 确认端口映射：s1-eth3→s2(路径A), s1-eth4→s3(路径B)

mininet> sh ovs-ofctl show s1 -O OpenFlow13
# 确认 s1 有 4 个端口

mininet> iperf h1 h3
# 预期：TCP 吞吐量接近 10Mbps
```

> **保存证据：** 截图 pingall、net、iperf 结果。

---

## Phase 2：基础控制器（已完成）

`base_controller.py` 已实现并验证通过。作为"无负载均衡"的基准对照。

**当前位置：** `/root/SDN/controller/base_controller.py`

**关键特性：**
- L2 学习交换机（MAC 学习 + 泛洪转发）
- IPv6/LLDP 过滤
- MAC 地址锁定（防止环路导致的 MAC Flapping）
- 广播风暴时间窗去重（0.5 秒窗口）
- table-miss 规则（priority=0 → CONTROLLER）

**已验证：** `pingall` 0% dropped，iperf ~10Mbps。

> **后续对比实验中，此控制器的实验数据将证明：无负载均衡时，所有流量只能走单一路径（由 MAC 学习顺序决定），另一条路径完全空闲。**

---

## Phase 3：基础设施与数据基础

**复杂度：** 中-高 | **难度：** 中-高 | **预估工程量：** 8-10 小时

**本节目标：** 构建完整的数据采集与路径控制基础设施。包括：(1) StatsMixin 端口统计采集器；(2) 动态流量生成器；(3) 阈值响应式负载均衡控制器（threshold_balancer.py）；(4) 使用 threshold_balancer 作为控制器采集双路径训练数据；(5) 特征组装与 Random Forest 模型训练。

**本节产出：**
- ✅ `controller/stats_mixin.py`（StatsMixin，120 行）
- ✅ `controller/threshold_balancer.py`（396 行）
- ✅ `scripts/traffic_gen.py`（67 行）
- ✅ `scripts/run_experiment.py`（62 行）
- ✅ `data/traffic_data.csv`（490 行双路径训练数据）
- ✅ `scripts/assemble_features.py`（特征组装）
- ✅ `scripts/train_model.py`（模型训练，RF + CV + GridSearchCV）
- ✅ `models/model_path_A.pkl` + `models/model_path_B.pkl`（已训练，RF 超参数调优）

---

### 3.1 端口统计采集器

#### 你要做什么

在 `threshold_balancer.py` 和 `predictive_balancer.py` 中实现一个共享的统计采集模块（设计为 Mixin 类）。每 3 秒向所有已连接的交换机发送 OpenFlow 端口统计请求，收到回复后计算每条核心链路的瞬时利用率，写入 CSV 文件。

#### 关键概念

**OpenFlow 端口统计查询机制：**
- 控制器发送 `OFPPortStatsRequest` → 交换机回复 `OFPPortStatsReply`
- 回复中包含每个端口的 `tx_bytes`（发送字节数，累计值）
- 利用率 = (本次 tx_bytes - 上次 tx_bytes) × 8 / (时间间隔 × 链路带宽)
- 乘 8 是因为 bytes → bits

**为什么用 Mixin 模式？**
两个负载均衡控制器都需要统计采集功能，但它们继承自不同的基类。Mixin 是 Python 中实现"多继承功能复用"的惯用模式——定义一个独立的类包含采集逻辑，控制器通过继承 Mixin 获得这些方法。

**Ryu 的协程调度：**
Ryu 基于 eventlet（协程库）。`hub.spawn` 创建的是协程而非线程，它和 Ryu 的事件循环在同一线程中运行。`hub.sleep(3)` 会把控制权交还给事件循环，等时间到了再继续——这意味着周期采集不会阻塞 Packet-In 处理。

#### 实现步骤

**Step 1：设计数据结构**

你需要维护以下状态：
- `datapaths`：dict，记录所有已连接交换机的 datapath 对象，key 是 dpid
- `prev_port_stats`：dict，记录每个端口上一次查询时的 tx_bytes，key 是 (dpid, port_no)
- `prev_time`：dict（key 是 dpid），每个交换机上一次收到统计回复的时间戳
- `link_utilization`：dict，每个核心链路的最新利用率值

思考：为什么需要 `prev_port_stats` 和 `prev_time`？因为 OpenFlow 返回的是累计字节数，你需要差值计算瞬时速率。为什么 `prev_time` 用 per-datapath 字典？因为每个交换机首次回复时只有累计值，没有上一次的基线，需要单独记录。

**Step 2：实现周期采集循环**

用 `hub.spawn` 启动一个后台协程，死循环中先调用 `request_port_stats()`，再 `hub.sleep()`。`request_port_stats()` 遍历所有 datapath，构造 `OFPPortStatsRequest` 消息并发送。

**Step 3：处理统计回复**

注册 `EventOFPPortStatsReply` 事件处理函数。收到回复后：
1. 遍历 `msg.body` 中的每个端口统计
2. 跳过特殊端口（port_no >= 0xffffff00，如 LOCAL、FLOOD 等）
3. 计算 delta_bytes 和 delta_time，得出 utilization
4. clamp 到 [0, 1.0]（防止负值或超界）
5. 更新 `prev_port_stats` 和 `prev_time`
6. 写入 CSV

**Step 4：实现链路标签**

`_get_link_label(dpid, port_no)` 根据端口映射返回链路类型。关键点：必须覆盖**所有核心链路**（不仅是 s1 的端口），否则 `get_path_utilization()` 无法正确计算整条路径的瓶颈利用率。

端口映射表（运行前用 `ovs-ofctl show` 验证）：
- 路径 A：s1 port3 → s2, s2 port2 → s4, s4 port3 ← s2
- 路径 B：s1 port4 → s3, s3 port2 → s4, s4 port4 ← s3

**Step 5：时间桶对齐**

Ryu 向多个交换机发送请求并接收回复是异步过程，s1 和 s2 的回复时间戳可能差几十到几百毫秒。如果直接用精确时间戳，后续特征组装时同一轮询周期的数据无法对齐。

解决方案：将时间戳向下取整到轮询间隔的倍数：
```python
poll_int = self.curr_poll_interval
bucket_ts = (int(now) // poll_int) * poll_int
```

这样同一轮询周期内所有交换机的回复都拥有完全相同的 `timestamp`。

**Step 6：自适应轮询**

固定 3 秒轮询在空闲时浪费资源，在突发时又不够敏捷。实现了三个状态：
- 空闲态（所有核心链路 < 30%）：POLL_INTERVAL = 5 秒
- 常规态：POLL_INTERVAL = 3 秒
- 警戒态（任意核心链路 > 50%）：POLL_INTERVAL = 1 秒

**实现代码：** 在 `_monitor` 循环中，每次统计完成后检查 `self.link_utilization` 的最大值，动态调整 `self.curr_poll_interval`。

```python
def _monitor(self):
    """后台轮询循环：自适应轮询 → 发请求 → 睡眠 → 重复"""
    while True:
        if self.link_utilization:
            u_max = max(self.link_utilization.values())
            if u_max < self.IDLE_THRESHOLD:      # < 0.3
                self.curr_poll_interval = self.POLL_IDLE      # 5s
            elif u_max > self.WARNING_THRESHOLD:  # > 0.5
                self.curr_poll_interval = self.POLL_WARNING   # 1s
        self._request_port_stats()
        hub.sleep(self.curr_poll_interval)
```

**训练数据与采样策略的匹配性：** 自适应轮询导致采样间隔在 1s/3s/5s 之间跳变，训练数据必须使用相同的采样策略生成，否则模型输入特征的时间分辨率与训练时不一致，预测精度会大幅退化。`train_model.py` 内部使用自适应采样仿真生成训练数据，确保模型与运行时的采样行为完全匹配。

#### 常见陷阱

1. **忘记 clamp utilization**：差值计算可能因计数器溢出或时间抖动产生负值或超过 100%。
2. **只标记 s1 端口**：如果你只给 s1 的 port3/port4 打标签，`get_path_utilization('A')` 只看到 s1→s2 的利用率，看不到 s2→s4 这条真正的瓶颈。
3. **CSV 不 flush**：Python 的 csv.writer 有缓冲，如果不 `flush()`，控制器异常退出时最后几行数据会丢失。
4. **特殊端口未跳过**：OVS 的 LOCAL、FLOOD 等特殊端口号很大（≥ 0xffffff00），不跳过会导致计算错误。

#### 验证检查点

- [x] 启动控制器和 Mininet 后，Ryu 日志显示 "Switch X connected"
- [x] 产生流量后，`data/traffic_data.csv` 有数据写入
- [x] CSV 中同一 timestamp 下有多个交换机的记录
- [x] 无流量时 utilization ≈ 0，有 iperf 流量时 utilization > 0
- [x] iperf TCP 打满时 utilization 接近 1.0
- [x] `_get_link_label` 覆盖了所有 6 个核心端口

#### 实现说明

**实际代码：** 参见 `/root/SDN/controller/stats_mixin.py`（120 行）

**与初版设计的关键差异：**

1. **`prev_time` 使用 per-datapath 字典**：`self.prev_time = {}`（key 是 dpid），而非单一浮点数。这样正确处理了每个交换机首次回复统计时的基线记录——首次收到某交换机的回复时只记录 `tx_bytes` 和时间戳，不做利用率计算（因为没有上一次的基线）。

2. **自适应轮询**：在 `_monitor` 循环中根据 `max(self.link_utilization.values())` 动态调整 `curr_poll_interval`。U_max < 0.3 → 5s，U_max > 0.5 → 1s，中间保持 3s。`train_model.py` 使用相同的自适应采样策略生成训练数据，确保模型与运行时的采样行为匹配。

3. **时间桶对齐使用动态间隔**：`bucket_ts = (int(now) // poll_int) * poll_int`，其中 `poll_int = self.curr_poll_interval`。

4. **`delta_bytes > 0` 检查**：只在字节差值为正时才计算利用率，避免计数器溢出导致的负值。

---

### 3.2 动态流量生成器

#### 你要做什么

创建 `/root/SDN/scripts/traffic_gen.py`，一个独立的 Python 脚本，能在 Mininet CLI 中通过 `sh python3 scripts/traffic_gen.py` 调用。它生成 iperf UDP 打流命令序列，模拟三种流量模式：阶跃、正弦、锯齿+噪声。

#### 关键概念

**为什么必须用 UDP 模式？**
iperf TCP 有慢启动（Slow Start）机制——连接建立后带宽从 0 逐步增长。如果你每 3 秒发一条 iperf 命令，TCP 还没爬出慢启动窗口就结束了，实际吞吐量远低于目标值。UDP 模式通过 `-b` 参数直接指定发送速率，3 秒内就能精确达到目标带宽。

**为什么需要高斯噪声？**
如果你用完美的正弦波或锯齿波训练模型，模型只会学到"周期性规律"，对真实网络中随机波动的泛化能力极差。叠加 σ=0.5 Mbps 的高斯噪声模拟真实网络的微突发（Microburst），迫使模型学习趋势而非死记波形。

**iperf 命令格式解析：**
```bash
iperf -c 10.0.0.1 -u -b 5M -t 3 -i 1
#     ───┬────   ─  ───┬─  ─┬─  ─┬─
#        │         │     │    │    └─ 每秒输出一次统计
#        │         │     │    └─ 持续 3 秒
#        │         │     └─ 目标带宽 5 Mbps
#        │         └─ UDP 模式
#        └─ 目的 IP（iperf 服务端）
```

#### 实现步骤

**Step 1：设计命令生成函数**

每个生成函数返回一个列表：`[(start_time, bandwidth_mbps), ...]`。每 3 秒一条命令，对应一个 iperf 进程。

**Step 2：实现三种波形**
- 阶跃波（step）：低带宽→高带宽→低带宽，周期性切换。验证阈值触发。
- 正弦波（sine）：`bw = center + amplitude * sin(2πt/period)`。验证连续预测。
- 锯齿波+噪声（sawtooth）：线性叠加高斯噪声。验证泛化能力。

**Step 3：实现执行函数**

在 Mininet 环境中，可以通过 `net.get('h3')` 获取主机对象，然后 `h3.cmd(iperf_command)` 执行命令。但如果脚本是独立运行的（不导入 Mininet），你需要用 `subprocess` 或直接打印命令让用户手动执行。

推荐方案：脚本只生成命令列表并打印，用户在 Mininet CLI 中手动执行或通过 `h3.cmd()` 批量执行。

#### 常见陷阱

1. **用 TCP 模式**：3 秒 TCP 流无法达到目标带宽，采集到的利用率数据严重失真。
2. **噪声 σ 太大**：σ > 2 时锯齿波的上升趋势被噪声淹没，模型学不到规律。推荐 σ=0.5。
3. **iperf 服务端未启动**：客户端发包但服务端没监听，iperf 直接退出，无流量产生。
4. **duration 不是 3 的倍数**：最后一条命令可能不足 3 秒，导致流量突然中断。

#### 验证检查点

- [x] `python3 scripts/traffic_gen.py --pattern step` 能打印命令列表
- [x] 命令列表中带宽值符合预期（step: 交替高低，sine: 平滑振荡，sawtooth: 有随机抖动）
- [x] 在 Mininet 中手动执行一条命令，Ryu 日志显示对应链路利用率上升

#### 实现说明

**实际代码：** 参见 `/root/SDN/scripts/traffic_gen.py`（67 行）

**实际参数（与初版设计的差异）：**

| 参数 | 初版设计 | 实际值 | 说明 |
|------|---------|--------|------|
| sine center | 5.0 | 4.5 | 降低中心带宽 |
| sine amplitude | 4.0 | 3.5 | 减小振幅 |
| sine clamp | 10.0 | 9.5 | 留出 0.5 Mbps 余量 |
| sawtooth base_max | 10 | 8 | 降低峰值避免持续拥塞 |
| sawtooth clamp | 12.0 | 9.5 | 与链路带宽 10 Mbps 对齐 |

实际参数使带宽范围为 0.5-9.5 Mbps，比初版更保守，避免链路长时间满载导致实验难以观察切换行为。

---

### 3.3 数据采集完整流程

**Step 1：启动控制器**
```bash
# 终端 1
conda activate sdn
cd /root/SDN
ryu-manager controller/threshold_balancer.py --observe-links 2>&1 | tee data/collect_ryu.log
```

> 必须使用 `threshold_balancer.py` 作为控制器来采集数据。base_controller（L2 学习交换机）无法控制路径选择，采集到的数据只有单一路径有流量，无法训练双路径预测模型。详见下方 3.4 节。

**Step 2：运行实验编排脚本**

`run_experiment.py` 自动完成拓扑创建、流量生成、清理的完整流程：

```bash
# 终端 2
sudo python3 scripts/run_experiment.py
```

**实现要点：**
- `run_experiment.py` 调用 `dual_path_topo.create_topology()` 获取 `(net, c0)`
- 自动启动 iperf 服务端（h3）和客户端（h1）
- 流量方向：**h1 → h3**（h1 是 iperf 客户端，h3 是服务端）
- 使用锯齿波 + 高斯噪声模式，120 秒，每 3 秒一条 iperf UDP 命令
- 实验结束后自动清理 OVS 交换机

**实际代码：** 参见 `/root/SDN/scripts/run_experiment.py`

**Step 3：检查 CSV**
```bash
head -20 data/traffic_data.csv
wc -l data/traffic_data.csv
```

**实际结果：**
- CSV 有 header + 490 行数据
- 每行格式：`timestamp, dpid, port_no, utilization, link_label`
- 同一 timestamp 下有多个交换机的记录
- path_A 和 path_B 都有非零利用率数据

> **保存证据：** 截图 CSV 前 20 行和行数统计。

---

### 3.4 阈值响应式负载均衡控制器（对照组 + 数据采集基础设施）

**本节目标：** 实现 `threshold_balancer.py`，它既是三阶段对照实验中的"阈值响应式"对照组，也是为 ML 训练提供双路径数据的采集基础设施。

**本节产出：** `controller/threshold_balancer.py`（396 行，已实现）

**实际代码：** 参见 `/root/SDN/controller/threshold_balancer.py`

#### 已实现的架构

```
threshold_balancer.py
├── PATH_PORTS / PATH_PORTS_REV     # 路径端口映射常量
├── ThresholdBalancer(RyuApp, StatsMixin)
│   ├── __init__                    # MAC表、host_location、networkx图、当前路径
│   ├── switch_features_handler()   # table-miss 规则
│   ├── packet_in_handler()         # ARP 单播 + host 学习 + 数据包转发
│   ├── add_flow / _send_packet     # 流表/Packet-Out 辅助
│   ├── _install_reverse_rule()     # 反向流表安装
│   ├── _get_path_out_port()        # 当前路径出端口查询
│   ├── _get_out_port()             # 跨交换机出端口计算
│   ├── _arp_lookup()               # IP→MAC 查找
│   ├── _install_full_path()        # 在所有路径交换机安装正向+反向流表
│   ├── _switch_path()              # 清除旧流表 + 安装新流表
│   ├── _clear_path_flows()         # 删除 priority=10 流表
│   ├── _decision_loop()            # 每 3 秒检查利用率，超阈值切换
│   ├── _get_path_util()            # 路径瓶颈利用率（max of 核心链路）
│   ├── port_stats_reply_handler()  # 委托给 StatsMixin
│   └── topology event handlers     # LLDP 邻居发现
```

#### 与 base_controller.py 的本质区别

| | base_controller.py | threshold_balancer.py |
|---|---|---|
| 转发方式 | MAC 学习 + 泛洪（FLOOD） | 显式路径安装（每跳预装流表） |
| 路径控制 | 无法控制（取决于 MAC 学习顺序） | 精确控制（控制器决定走 A 还是 B） |
| 环路防护 | 需要广播风暴时间窗补丁 | 不需要（无泛洪 = 无环路） |
| 路径切换 | 不可能 | 删除旧流表 + 安装新流表 |
| ARP 处理 | 泛洪 | 单播转发（控制器查 host_location 表） |

#### 关键设计决策

1. **路径端口映射使用字典常量**：`PATH_PORTS = {"A": {1: 3, 2: 2, 4: 1}, "B": {1: 4, 3: 2, 4: 1}}`，包含正向和反向两个映射。

2. **显式路径安装同时装正向和反向流表**：`_install_full_path()` 在路径上所有交换机同时安装 `eth_dst=h3`（正向）和 `eth_dst=h1`（反向）的流表规则。

3. **路径切换先清后装**：`_switch_path()` 先调用 `_clear_path_flows()` 删除所有 `priority=10` 的流表，再安装新路径。

4. **阈值决策**：`util > 0.70` 且另一条路径 `util < 0.50` 时切换，防止"跳入火坑"。

5. **首次路径安装延迟到两端 host 都已知时**：`path_installed` 标志确保只有在 h1 和 h3 的 MAC 都被学习到后才安装路径流表。

#### 验证检查点

- [x] `pingall` 0% dropped（显式路径安装正确）
- [x] `sh ovs-ofctl dump-flows s1 -O OpenFlow13` 显示 priority=10 的路径规则
- [x] 产生流量后，Ryu 日志显示 "Path A: xx.x%, Path B: x.x%, current: A"
- [x] 当 util_a > 70% 时，日志显示 ">>> Path A congested, rerouting to B"
- [x] 切换后 `dump-flows s1` 显示出端口从 port3 变为 port4

#### 运行与验证

```bash
# 终端 1
ryu-manager controller/threshold_balancer.py --observe-links 2>&1 | tee data/threshold_ryu.log

# 终端 2
sudo python3 scripts/run_experiment.py

# 或手动测试：
# sudo python3 topo/dual_path_topo.py → Mininet CLI → pingall → iperf
```

> **保存证据：** 截图 pingall 结果、重路由日志、流表切换前后的 dump-flows 对比。

---

### 3.5 使用 threshold_balancer 采集双路径训练数据

**为什么要用 threshold_balancer 而不是 base_controller 采集数据？**

base_controller 是 L2 学习交换机，路径选择由 MAC 学习顺序随机决定——所有流量只会走一条路径，另一条路径利用率始终为 0。用这样的数据训练 ML 模型，模型永远学不到"两条路径同时有负载"的模式，无法做出有意义的路径切换预测。

threshold_balancer 通过显式路径安装控制流量走向，并在拥塞时切换路径。用它采集的数据天然包含两条路径的负载交替模式，是训练双路径预测模型的必要条件。

**采集流程：**

```bash
# 终端 1：启动 threshold_balancer
ryu-manager controller/threshold_balancer.py --observe-links 2>&1 | tee data/collect_ryu.log

# 终端 2：运行实验编排（自动创建拓扑 + 生成流量）
sudo python3 scripts/run_experiment.py
```

**实际结果：**
- `data/traffic_data.csv` 包含 490 行数据（header + 490）
- 时间跨度 117 秒（timestamp 1778498118 → 1778498235）
- path_A 和 path_B 都有非零利用率数据
- 阈值切换机制在 util > 70% 时触发路径切换

**数据质量验证：**
```bash
# 检查数据行数
wc -l data/traffic_data.csv

# 检查 path_A 和 path_B 的数据分布
python3 -c "
import pandas as pd
df = pd.read_csv('data/traffic_data.csv')
df = df[df['link_label'].isin(['path_A', 'path_B'])]
print(df.groupby('link_label')['utilization'].describe())
"
```

> **保存证据：** 截图数据分布统计，确认两条路径都有非零利用率数据。

---

## Phase 3（续）：离线 ML 训练流水线

**复杂度：** 中 | **难度：** 中 | **预估工程量：** 3-4 小时

**本节目标：** 将 Phase 3 采集的原始 CSV 数据，经过特征工程组装为训练数据集，训练 Random Forest 回归模型（含交叉验证与超参数调优），导出为 `.pkl` 文件供控制器在线推理使用。

**本节产出：** `data/training_features.csv`（训练数据集），`data/model_evaluation_summary.csv`（评估摘要），`models/model_path_A.pkl` + `models/model_path_B.pkl`（模型文件），`figures/` 下 14 张可视化图表

---

### 3.6 特征组装脚本

#### 你要做什么

创建 `/root/SDN/scripts/assemble_features.py`，读取 `data/traffic_data.csv`，将原始的逐端口统计数据转换为滑动窗口训练特征。

#### 关键概念

**多变量时间序列预测：**
路径 A 和路径 B 的流量高度负相关——当 A 拥塞时，流量会切换到 B，反之亦然。如果每条链路独立预测，模型看不到另一条链路的状态。将两条链路的历史数据拼接为一个特征向量，让模型同时感知整个网络核心切面的状态。

**特征向量设计：**
- 输入 X = [U_A(t-2), U_B(t-2), U_A(t-1), U_B(t-1), U_A(t), U_B(t)]（6 维）
- 标签 Y = U_{t+1}（path_A 或 path_B 的下一周期实际利用率）
- 窗口大小 = 3 个时间步（9 秒历史），比 5 个时间步（15 秒）更敏捷

**为什么每个时间步生成两个样本？**
同一组特征 [U_A(t-2), U_B(t-2), ...] 既可用于预测 U_A(t+1)，也可用于预测 U_B(t+1)。所以每个滑动窗口位置生成两行训练数据，一行 target_label='path_A'，一行='path_B'。训练时按 target_label 分组，各训练一个模型。

#### 实现步骤

**Step 1：读取并过滤数据**

用 pandas 读取 CSV，只保留 `link_label` 为 `path_A` 或 `path_B` 的行。其他链路（如 `s2_p1`）的数据不需要。

**Step 2：Pivot 为宽表**

原始 CSV 是长表格式（每个端口一行）。需要 pivot 为宽表，使每个 timestamp 对应一行，列分别是 `path_A` 和 `path_B` 的利用率。

提示：`df.pivot_table(index='timestamp', columns='link_label', values='utilization', aggfunc='max')`

为什么用 `aggfunc='max'`？因为 `_get_link_label` 给同一路径的多个端口都打了 `path_A` 标签（如 s1 port3、s2 port2、s4 port3），同一个 timestamp 下可能有多个 path_A 记录。取最大值而非平均值——路径的瓶颈利用率由最忙的那段链路决定，取平均会低估拥塞程度。例如 s1→s2 利用率 90%、s2→s4 利用率 10%，平均 50% 会掩盖 s1→s2 的拥塞，而 max 90% 才是真实的路径瓶颈。

**Step 3：构建滑动窗口**

遍历 pivot 后的时间序列，对每个位置 i（从 WINDOW_SIZE 开始）：
1. 取 i-3 到 i-1 的 3 个时间步的双链路数据
2. 交替排列为 [U_A(t-2), U_B(t-2), U_A(t-1), U_B(t-1), U_A(t), U_B(t)]
3. 为 path_A 和 path_B 各生成一行样本

**Step 4：输出 CSV**

列名使用 `feat_0` 到 `feat_5`（6 个特征列），加上 `target_label` 和 `U_next`。

#### 常见陷阱

1. **忘记 pivot**：如果你直接用原始长表做滑动窗口，每个 timestamp 有多行（不同端口），窗口逻辑会完全混乱。
2. **aggfunc 用 sum 或 mean**：同一路径多个端口的利用率不应该求和（那会超过 100%），也不应该取平均（会低估瓶颈）。应该取最大值（max），因为路径瓶颈由最忙的链路段决定。
3. **窗口中特征顺序错误**：必须严格交替 [U_A, U_B, U_A, U_B, ...]，否则模型无法正确学到双链路的时序关系。
4. **数据量不足**：单次 120 秒采集约 40 个时间点，减去 3 的窗口大小，只有 ~37 个位置 × 2 = ~74 个样本。数据量偏少，需多次采集叠加。实际使用了 5 批次数据，共 376 个样本（path_A: 188, path_B: 188）。

#### 验证检查点

- [ ] `python3 scripts/assemble_features.py` 运行无报错
- [ ] 输出 CSV 有 `feat_0` 到 `feat_5` 六列特征 + `target_label` + `U_next`
- [ ] path_A 和 path_B 的样本数相等
- [ ] `feat_0` 和 `feat_1` 的值分别对应某个时间点的 path_A 和 path_B 利用率

#### 参考实现

<details>
<summary>点击展开 assemble_features.py 完整代码</summary>

```python
#!/usr/bin/env python3
"""多变量特征组装：双链路状态 → 6 维滑动窗口训练特征"""
import pandas as pd
import argparse

WINDOW_SIZE = 3  # 3 个时间步 × 2 条链路 = 6 维特征


def assemble_features(input_csv, output_csv):
    df = pd.read_csv(input_csv)
    df = df[df['link_label'].isin(['path_A', 'path_B'])]

    # Pivot: 每行一个 timestamp，列是 path_A/path_B 的利用率
    pivot = df.pivot_table(
        index='timestamp', columns='link_label',
        values='utilization', aggfunc='max'
    ).sort_index()

    util_a = pivot['path_A'].values
    util_b = pivot['path_B'].values

    all_samples = []
    for i in range(WINDOW_SIZE, len(util_a)):
        features = []
        for t in range(i - WINDOW_SIZE, i):
            features.extend([util_a[t], util_b[t]])

        for label, target in [('path_A', util_a[i]), ('path_B', util_b[i])]:
            all_samples.append({
                **{f'feat_{j}': features[j] for j in range(6)},
                'target_label': label,
                'U_next': target,
            })

    out_df = pd.DataFrame(all_samples)
    out_df.to_csv(output_csv, index=False)
    print(f"Assembled {len(out_df)} samples → {output_csv}")
    print(f"  Feature dim: {WINDOW_SIZE * 2} (3 timesteps × 2 links)")
    print(f"  path_A: {len(out_df[out_df['target_label'] == 'path_A'])}")
    print(f"  path_B: {len(out_df[out_df['target_label'] == 'path_B'])}")


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', default='data/traffic_data.csv')
    parser.add_argument('--output', default='data/training_features.csv')
    args = parser.parse_args()
    assemble_features(args.input, args.output)
```

</details>

---

### 3.7 模型训练脚本

#### 你要做什么

创建 `/root/SDN/scripts/train_model.py`，读取 `training_features.csv`，使用 Random Forest 回归模型进行训练。采用 TimeSeriesSplit 交叉验证评估模型稳定性，GridSearchCV 自动调优超参数，按链路分别导出两个 `.pkl` 文件，并生成丰富的可视化分析图表。

#### 关键概念

**为什么选择 Random Forest？**
- 集成多棵决策树，能学到非线性关系（链路利用率存在非线性动态）
- 内置特征重要性分析，可解释哪些历史时刻对预测影响最大
- 通过树方差可估计预测置信区间
- OOB（Out-of-Bag）分数提供免费的交叉验证评估
- 推理速度 ~1ms，完全满足 3 秒轮询间隔

**为什么不能 shuffle 时间序列数据？**
`sklearn.model_selection.train_test_split(X, y, shuffle=True)` 会随机打乱样本。对于时间序列，这意味着"未来的数据可能出现在训练集中"——模型在离线评估时准确率奇高（因为偷看了未来），上线实盘就崩溃。这叫 Data Leakage，是算法面试的高频陷阱。

正确做法：前 80% 样本训练，后 20% 测试，严格保持时间顺序。交叉验证同样使用 `TimeSeriesSplit` 而非标准 KFold。

**TimeSeriesSplit 交叉验证：**
标准 KFold 交叉验证会随机划分数据，对时间序列同样会造成数据泄漏。`TimeSeriesSplit` 保证每个验证折叠的训练集都在验证集之前——模拟真实的"用过去预测未来"场景。5 折 CV 提供比单次 80/20 划分更稳健的性能估计（均值 ± 标准差）。

**GridSearchCV 超参数调优：**
对 `n_estimators`（树数量）、`max_depth`（树深度）、`min_samples_leaf`（叶节点最小样本数）进行网格搜索，配合 TimeSeriesSplit 找到最优超参数组合。这比手动调参更系统、更可靠。

**为什么按链路分别训练？**
虽然输入特征相同（6 维全局状态），但 path_A 和 path_B 的目标值分布不同。分别训练让每个模型专注于预测特定链路，避免互相干扰。

#### 实现步骤

**Step 1：读取数据，提取特征列**

特征列是所有以 `feat_` 开头的列（6 个）。标签列是 `U_next`。

**Step 2：TimeSeriesSplit 交叉验证**

使用 `TimeSeriesSplit(n_splits=5)` 对默认 RF 配置进行交叉验证，记录每折的 MAE、RMSE、R²。输出均值 ± 标准差，评估模型稳定性。

**Step 3：GridSearchCV 超参数调优**

搜索空间：
- `n_estimators`: [30, 50, 100]
- `max_depth`: [3, 5, 8, None]
- `min_samples_leaf`: [1, 3, 5]

使用 `TimeSeriesSplit(n_splits=5)` 作为 CV 策略，`scoring='neg_mean_absolute_error'`。

**Step 4：训练/测试集评估**

用最优超参数的模型在 80/20 时间序列划分上评估。计算测试集 MAE、RMSE、R²、相关系数。通过树方差计算预测置信区间。

**Step 5：生成可视化图表**

- 交叉验证分数柱状图（每折 MAE/RMSE/R²）
- 学习曲线（训练集大小 vs 模型性能）
- 预测散点图（含 ±1 标准差置信区间）
- 特征重要性柱状图（含数值标注）
- 残差分析图（残差 vs 预测值 + 残差分布）
- 误差分布直方图
- 预测值时间序列对比图

**Step 6：全量训练并导出**

用最优超参数在全部数据上重新训练，导出为 `models/model_path_A.pkl` 和 `models/model_path_B.pkl`。输出模型评估摘要 CSV。

#### 常见陷阱

1. **shuffle=True**：最致命的错误。时间序列数据一旦随机打乱，离线指标全部作废。
2. **用 KFold 而非 TimeSeriesSplit**：标准 KFold 同样会引入数据泄漏，交叉验证结果不可信。
3. **忘记按链路分组**：如果用混合数据训练单个模型，path_A 和 path_B 的预测会互相干扰。
4. **max_depth=None 无限制**：小数据集上容易过拟合，GridSearchCV 会自动选择合适的深度。
5. **忽视树方差**：RF 的树方差是天然的不确定性估计，在散点图中展示置信区间能增强报告说服力。

#### 验证检查点

- [x] `python3 scripts/train_model.py` 运行无报错
- [x] `models/model_path_A.pkl` 和 `models/model_path_B.pkl` 存在
- [x] 14 张可视化图表生成至 `figures/` 目录
- [x] `data/model_evaluation_summary.csv` 包含完整的评估指标

#### 实际实现

**实际代码：** 参见 `/root/SDN/scripts/train_model.py`

**训练数据：** `train_model.py` 使用自适应轮询策略（1s/3s/5s）仿真生成训练数据，与运行时的采样行为匹配。具体超参数和性能指标见运行后输出的 `data/model_evaluation_summary.csv`。

---

### 3.8 完整训练流程

```bash
# Step 1: 训练模型（自动生成训练数据 + 交叉验证 + 超参数调优）
python3 scripts/train_model.py
# 内部流程：
#   1. 使用自适应轮询策略仿真生成多批次训练数据
#   2. 按链路分别训练 RF 模型（TimeSeriesSplit CV + GridSearchCV）
#   3. 生成 14 张可视化图表 → figures/
#   4. 输出模型评估摘要 → data/model_evaluation_summary.csv
#   5. 导出 → models/model_path_A.pkl, models/model_path_B.pkl

# Step 2: 验证
ls -la models/
ls -la figures/
cat data/model_evaluation_summary.csv
```

**实际模型性能（Random Forest，6 维输入，自适应采样训练数据，超参数调优后）：**

模型使用自适应轮询策略（1s/3s/5s）仿真生成的训练数据训练，与运行时的采样行为完全匹配。

| 指标 | path_A | path_B | 说明 |
|------|--------|--------|------|
| 推理时间 | < 1ms | < 1ms | 满足轮询间隔要求 |

> 具体 MAE / R² 等指标取决于训练数据的随机种子和流量模式，运行 `train_model.py` 后查看 `data/model_evaluation_summary.csv` 获取实际数值。

> **保存证据：** 截图训练输出（MAE、R²、CV 分数），保存模型文件和图表，记录特征重要性用于报告分析。

---

## Phase 4：AI 预测式负载均衡控制器（核心创新）

**复杂度：** 高 | **难度：** 高 | **预估工程量：** 8-10 小时

**本节目标：** 在 Phase 3 的 `threshold_balancer.py` 基础上，将决策层从"当前超阈值才切换"替换为"ML 预测下一周期将拥塞，提前切换"。使用 Phase 3 训练好的 Random Forest 模型进行在线推理。这是本项目的核心创新——从被动响应变为主动预防。

**本节产出：** `controller/predictive_balancer.py`

---

### 4.1 你需要做什么

创建 `/root/SDN/controller/predictive_balancer.py`，它与 `threshold_balancer.py` 的**唯一区别**在决策层。拓扑发现、Host 学习、ARP 单播转发、显式路径安装、StatsMixin 这些模块完全相同，直接复制即可。

你需要新增一个 `DecisionEngine` 类，实现：

1. **状态机**：Cold Start → AI Prediction → Cooldown 三态循环
2. **ML 推理**：加载 Phase 3 训练好的 Random Forest 模型，输入 6 维特征，输出下一周期利用率预测
3. **EMA 平滑**：对预测值做指数移动平均，消除单次预测的抖动
4. **MAE 感知阈值**：`predicted + model_error > 0.7` 才判定拥塞，而非简单的 `predicted > 0.7`
5. **预测日志**：每次预测写入 `data/predictions.csv`，用于后续可视化（预测 vs 实际）

#### 与 threshold_balancer.py 的本质区别

| | threshold_balancer.py | predictive_balancer.py |
|---|---|---|
| 决策依据 | **当前**利用率 > 70% | **预测**下一周期利用率 + MAE > 65% |
| 切换时机 | 拥塞**已经发生**后 | 拥塞**即将发生**前 |
| 决策函数 | `check_and_reroute()` | `DecisionEngine.on_stats_collected()` |
| 平滑处理 | 无 | EMA α=0.3 |
| 误差容忍 | 无 | MAE-aware 阈值修正 |
| 状态管理 | 无 | 冷启动 / AI / 冷却三态机 |

### 4.2 关键概念

#### DecisionEngine 是什么？

`DecisionEngine` 是一个独立的决策类，与 Ryu 控制器解耦。控制器每个轮询周期调用一次 `engine.on_stats_collected(util_a, util_b, current_poll_interval)`，引擎返回 `None`（不切换）或 `'A'`/`'B'`（切换到指定路径）。

这种设计的好处：
- **可测试**：可以在不启动 Ryu 的情况下单独测试决策逻辑
- **可替换**：想换决策算法？只改 Engine，控制器其他代码不动
- **可对比**：threshold_balancer 和 ai_balancer 的区别仅在 Engine

#### 状态机设计

```
                    ┌──────────────────────────┐
                    │       COLD START         │
                    │  (first 5 poll periods)  │
                    │  features.len() < WINDOW │
                    │  fallback: static path A │
                    └───────────┬──────────────┘
                                │ 5 periods collected
                                ▼
                    ┌──────────────────────────┐
                    │      AI PREDICTION       │
                    │  predict U_{t+1}         │
                    │  EMA smooth              │
                    │  if predicted + MAE > 65%│
                    │    → reroute + cooldown  │
                    └───────────┬──────────────┘
                                │ reroute triggered
                                ▼
                    ┌──────────────────────────┐
                    │       COOLDOWN           │
                    │  (3 periods = 9 seconds) │
                    │  reject all switch reqs  │
                    │  keep collecting stats   │
                    └───────────┬──────────────┘
                                │ cooldown expires
                                ▼
                          back to AI PREDICTION
```

**为什么需要冷启动？** 模型需要至少 WINDOW_SIZE=3 个时间步的历史数据才能做预测。前 5 个采样周期（15 秒）没有足够数据，此时保持静态路径 A。

**为什么需要冷却锁？** 假设路径 A 拥塞，切换到 B。如果立刻重新评估，B 的利用率刚刚因为流量涌入而升高，模型可能又切回 A——形成乒乓效应。冷却 3 个周期（9 秒）让流量稳定。

**冷却结束时为什么要重置？** 冷却结束时清空特征队列和 EMA 状态。原因：切换前积累的特征反映的是旧路径的高负载，携带这些历史会导致模型对新路径产生误判。

#### EMA 平滑（指数移动平均）

单次预测可能有噪声。EMA 对连续预测做加权平均，近期权重更高：

```
smoothed = α × predicted + (1-α) × smoothed_prev
```

使用固定 α=0.6，新值权重 60%，历史 40%。

**种子初始化问题：** 第一次预测时没有 `smoothed_prev`。如果设为 0，第一次 smoothed = alpha × predicted，会严重低估。解决方案：第一次预测时直接用原始值作为种子。

#### MAE 感知阈值

模型有误差（MAE ≈ 0.6）。如果阈值是 65%，模型预测 62% 就认为安全，但实际可能是 70%——已经拥塞了。

修正公式：`predicted + PREDICT_MAE > threshold`。这样当模型预测 57% + 8% = 65% 时就触发切换，留出误差缓冲。

**双路拥塞边界：** 如果两条路径的 smoothed 值都超过阈值，说明网络整体过载，切换没有意义。此时不切换。

#### 预测日志

每次预测将 `(timestamp, link, predicted, smoothed)` 写入 `data/predictions.csv`。这个文件用于 Phase 5 的可视化——绘制预测值 vs 实际值的对比图，展示 AI 的预测准确度。

#### 关键参数

| 参数 | 值 | 说明 |
|------|-----|------|
| `COLD_START_PERIODS` | 5 | 冷启动阶段收集数据点（≥ WINDOW_SIZE） |
| `COOLDOWN_PERIODS` | 3 | 重路由后锁定 9 秒 |
| `CONGESTION_PREDICT_THRESHOLD` | 0.7 | 预测拥塞阈值（低于实际 70%，给 MAE 留空间） |
| `EMA_ALPHA` | 0.6 | EMA 平滑系数（新值权重 60%） |
| `PREDICT_MAE` | 0.06 | 模型 MAE，用于阈值修正 |
| `WINDOW_SIZE` | 3 | 滑动窗口（3 时间步 × 2 链路 = 6 维特征） |

### 4.3 实现步骤

#### Step 1：复制 threshold_balancer.py

从 `threshold_balancer.py` 复制一份作为 `predictive_balancer.py` 的起点。修改类名（如 `AIPredictiveBalancer`）。

#### Step 2：删除旧决策逻辑

删除 `check_and_reroute()` 方法和 `_decision_loop()` 中的阈值判断逻辑。这些将被 DecisionEngine 替代。

#### Step 3：实现 DecisionEngine 类

在 `predictive_balancer.py` 中定义一个独立的 `DecisionEngine` 类（不继承 RyuApp）。核心方法：

- `__init__(self, model_dir, predict_mae, pred_csv_path)`：加载两个 pkl 模型，初始化状态变量
- `on_stats_collected(self, util_a, util_b, current_poll_interval=3)`：决策主逻辑，返回 `None` 或 `'A'`/`'B'`
- `_predict(self, combined_features, target)`：调用模型推理

`on_stats_collected` 的内部流程：
1. `stats_count += 1`
2. 如果 `stats_count < COLD_START_PERIODS`：追加特征到队列，返回 None
3. 如果 `feature_queue` 长度不足 WINDOW_SIZE：继续填充，返回 None
4. 如果 `cooldown_remaining > 0`：递减计数器，到期时清空队列和 EMA，返回 None
5. 滑动窗口更新：`pop(0)` + `append((util_a, util_b))`
6. 构建 6 维特征向量：`[U_A(t-2), U_B(t-2), U_A(t-1), U_B(t-1), U_A(t), U_B(t)]`
7. 对 path_A 和 path_B 各做一次推理
8. EMA 平滑（含种子初始化）
9. 写入 predictions.csv
10. MAE 感知阈值判断 + 双路拥塞边界检查
11. 触发切换时设置 `cooldown_remaining` 和 `current_path`

#### Step 4：修改 _decision_loop

将 `_decision_loop` 中的决策逻辑替换为调用 `DecisionEngine`：

```python
decision = self.engine.on_stats_collected(util_a, util_b, self.curr_poll_interval)
if decision:
    self.install_path(decision)
```

同时添加状态日志：每轮打印当前处于哪个阶段（Cold Start / AI Prediction / Cooldown）。

#### Step 5：添加预测 CSV 初始化

在 `__init__` 中创建 `data/predictions.csv` 文件和 writer。注意在控制器退出时关闭文件（可以在 `__del__` 或用 `atexit`）。

#### Step 6：处理 POLL_INTERVAL 常量

`on_stats_collected` 中写 predictions.csv 时需要 `POLL_INTERVAL` 做时间对齐。确保 DecisionEngine 能访问到这个常量（作为参数传入或在 Engine 内定义）。

### 4.4 常见陷阱

1. **EMA 种子设为 0**：第一次 smoothed = 0.3 × predicted，严重低估。必须在 `smoothed is None` 时直接用原始预测值。
2. **冷却结束不清空队列**：切换前的高负载历史会污染新路径的预测，导致误判。必须在 `cooldown_remaining` 归零时 `feature_queue.clear()` + `smoothed = None`。
3. **双路同时拥塞时仍切换**：如果两条路径都超阈值，切换是无效操作。必须检查目标路径 `+ MAE < threshold` 才允许切换。
4. **特征向量顺序错误**：模型期望 `[U_A(t-2), U_B(t-2), U_A(t-1), U_B(t-1), U_A(t), U_B(t)]`，不是 `[U_A(t-2), U_A(t-1), U_A(t), U_B(t-2), ...]`。必须交替排列。
5. **忘记 predictions.csv 的 flush**：Python 的 CSV writer 有缓冲，不 flush 的话数据可能丢失。
6. **模型文件路径错误**：`joblib.load('models/model_path_A.pkl')` 用的是相对路径，取决于启动 ryu-manager 时的工作目录。建议用绝对路径或 `os.path.dirname(__file__)` 定位。

### 4.5 验证检查点

- [ ] `pingall` 0% dropped（显式路径安装正确）
- [ ] Ryu 日志显示冷启动阶段：`Cold start [1/5], path A static`
- [ ] 冷启动结束后进入 AI 模式：`AI prediction mode, smoothed_a=0.xx`
- [ ] 产生高流量后，日志显示 `>>> reroute to B`（在拥塞发生前）
- [ ] 切换后进入冷却：`Cooldown [3/3], locked to path B`
- [ ] 冷却结束后恢复 AI 预测
- [ ] `data/predictions.csv` 有数据写入
- [ ] `dump-flows` 显示出端口从 port3 变为 port4（或反之）

### 4.6 参考实现

<details>
<summary>点击展开 DecisionEngine 完整代码</summary>

```python
import time
import csv
import numpy as np
import joblib


class DecisionEngine:
    """AI 预测式决策引擎 — 独立于 Ryu，可单独测试"""

    COLD_START_PERIODS = 5
    COOLDOWN_PERIODS = 3
    CONGESTION_PREDICT_THRESHOLD = 0.7
    EMA_ALPHA = 0.6  # EMA 平滑系数
    WINDOW_SIZE = 3  # 3 个时间步 × 2 条链路 = 6 维

    def __init__(self, model_dir='models/', predict_mae=0.06,
                 pred_csv_path='data/predictions.csv'):
        self.model_a = joblib.load(f'{model_dir}/model_path_A.pkl')
        self.model_b = joblib.load(f'{model_dir}/model_path_B.pkl')

        self.stats_count = 0
        self.feature_queue = []          # [(util_a, util_b), ...]
        self.cooldown_remaining = 0
        self.current_path = 'A'

        self.smoothed_a = None           # EMA seed: None → first prediction
        self.smoothed_b = None
        self.PREDICT_MAE = predict_mae

        # 预测值记录 CSV
        self.pred_csv = open(pred_csv_path, 'w', newline='')
        self.pred_writer = csv.writer(self.pred_csv)
        self.pred_writer.writerow(['timestamp', 'link', 'predicted', 'smoothed'])

    def close(self):
        """关闭 CSV 文件"""
        self.pred_csv.close()

    def on_stats_collected(self, util_a, util_b, current_poll_interval=3):
        """
        每个轮询周期调用一次。
        current_poll_interval: 当前动态轮询间隔，由 StatsMixin 提供
        返回: None (不切换) 或 'A' / 'B' (切换到指定路径)
        """
        self.stats_count += 1

        # === Phase 1: Cold Start ===
        if self.stats_count < self.COLD_START_PERIODS:
            self.feature_queue.append((util_a, util_b))
            return None

        # 特征队列填充（冷启动→AI 过渡）
        if len(self.feature_queue) < self.WINDOW_SIZE:
            self.feature_queue.append((util_a, util_b))
            return None

        # === Phase 2: Cooldown ===
        if self.cooldown_remaining > 0:
            self.cooldown_remaining -= 1
            if self.cooldown_remaining == 0:
                # 冷却结束：重置预测状态
                self.feature_queue.clear()
                self.smoothed_a = None
                self.smoothed_b = None
            return None

        # === Phase 3: AI Prediction ===
        # 滑动窗口更新
        self.feature_queue.pop(0)
        self.feature_queue.append((util_a, util_b))

        # 构建 6 维特征向量
        combined_features = []
        for (ua, ub) in self.feature_queue:
            combined_features.extend([ua, ub])

        # 推理
        predicted_a = self._predict(combined_features, target='A')
        predicted_b = self._predict(combined_features, target='B')

        # EMA 平滑（含种子初始化）
        now = time.time()
        if self.smoothed_a is None:
            self.smoothed_a = predicted_a
            self.smoothed_b = predicted_b
        else:
            self.smoothed_a = self.EMA_ALPHA * predicted_a + (1 - self.EMA_ALPHA) * self.smoothed_a
            self.smoothed_b = self.EMA_ALPHA * predicted_b + (1 - self.EMA_ALPHA) * self.smoothed_b

        # 记录预测值（使用当前动态轮询间隔对齐时间桶）
        bucket_ts = (int(now) // current_poll_interval) * current_poll_interval
        self.pred_writer.writerow([
            bucket_ts, 'path_A', f'{predicted_a:.4f}', f'{self.smoothed_a:.4f}'
        ])
        self.pred_writer.writerow([
            bucket_ts, 'path_B', f'{predicted_b:.4f}', f'{self.smoothed_b:.4f}'
        ])
        self.pred_csv.flush()

        # MAE 感知阈值判断 + 双路拥塞边界
        if (self.current_path == 'A' and
            self.smoothed_a + self.PREDICT_MAE > self.CONGESTION_PREDICT_THRESHOLD and
            self.smoothed_b + self.PREDICT_MAE < self.CONGESTION_PREDICT_THRESHOLD):
            self.current_path = 'B'
            self.cooldown_remaining = self.COOLDOWN_PERIODS
            return 'B'

        if (self.current_path == 'B' and
            self.smoothed_b + self.PREDICT_MAE > self.CONGESTION_PREDICT_THRESHOLD and
            self.smoothed_a + self.PREDICT_MAE < self.CONGESTION_PREDICT_THRESHOLD):
            self.current_path = 'A'
            self.cooldown_remaining = self.COOLDOWN_PERIODS
            return 'A'

        return None

    def _predict(self, combined_features, target='A'):
        """多变量模型推理：6 维全局特征 → 预测指定链路的利用率"""
        X = np.array(combined_features).reshape(1, -1)  # (1, 6)
        model = self.model_a if target == 'A' else self.model_b
        return model.predict(X)[0]

    def get_state_name(self):
        """返回当前状态名称，用于日志"""
        if self.stats_count < self.COLD_START_PERIODS:
            return f"Cold Start [{self.stats_count}/{self.COLD_START_PERIODS}]"
        if len(self.feature_queue) < self.WINDOW_SIZE:
            return "Filling Queue"
        if self.cooldown_remaining > 0:
            return f"Cooldown [{self.cooldown_remaining}/{self.COOLDOWN_PERIODS}]"
        return "AI Prediction"
```

</details>

<details>
<summary>点击展开 predictive_balancer.py 模块结构参考</summary>

```
predictive_balancer.py
├── __init__
│   ├── load DecisionEngine (model_dir, predict_mae, poll_interval)
│   ├── init mac_to_port, host_location
│   ├── init topo graph (networkx)
│   ├── init StatsMixin (stats collector)
│   └── spawn _monitor() + _decision_loop()
│
├── switch_features_handler()     # table-miss rule（同 threshold_balancer）
├── packet_in_handler()           # ARP unicast + host learning（同 threshold_balancer）
├── _handle_port_stats_reply()    # StatsMixin callback → CSV + feature update
│
├── _decision_loop()              # 每 POLL_INTERVAL 秒调用
│   ├── get util_a, util_b from link_utilization
│   ├── decision = engine.on_stats_collected(util_a, util_b, self.curr_poll_interval)
│   ├── if decision: install_path(decision)
│   └── log state (engine.get_state_name())
│
├── DecisionEngine               # （见上方参考代码）
│
├── install_path(path_name)       # 显式路径流表安装（同 threshold_balancer）
├── get_path_utilization(path)    # 同 threshold_balancer
│
└── topology event handlers       # LLDP 邻居发现（同 threshold_balancer）
```

</details>

### 4.7 运行与验证

```bash
# 确保 models/model_path_A.pkl 和 model_path_B.pkl 存在（Phase 3 训练好的）

# 终端 1
ryu-manager controller/predictive_balancer.py --observe-links 2>&1 | tee data/ai_ryu.log

# 终端 2
sudo python3 scripts/run_experiment.py

# 或手动测试：
# sudo python3 scripts/run_experiment.py → 验证 pingall 0% dropped
```

**验证 AI 决策流程：**

```bash
# 在 Mininet CLI 中产生流量
mininet> h1 iperf -s -u &
mininet> h3 iperf -c 10.0.0.1 -u -b 7M -t 60

# 观察 Ryu 日志，预期看到：
# [t=0-15s]  Cold start [1/5], path A static
# [t=15s]    Cold start complete, entering AI prediction mode
# [t=18s]    AI predict: smoothed_a=0.42, smoothed_b=0.05, path A
# [t=30s]    AI predict: smoothed_a=0.68, smoothed_b=0.04, >>> reroute to B
# [t=30-39s] Cooldown [3], locked to path B
# [t=39s]    Cooldown expired, resuming AI prediction
```

**验证预测日志：**

```bash
# 检查 predictions.csv 是否有数据
wc -l data/predictions.csv
# 预期：20+ 行（每个采样周期 2 行：path_A 和 path_B）
head data/predictions.csv
# 预期：timestamp,link,predicted,smoothed
```

> **保存证据：** 截图完整决策日志（冷启动→AI 预测→触发切换→冷却→恢复），保存 `data/predictions.csv`。

---

## Phase 5：对照实验与结果分析

**复杂度：** 中 | **难度：** 中 | **预估工程量：** 4-5 小时

**本节目标：** 运行三组对照实验（无 LB / 阈值 / AI），采集数据，生成可视化图表，证明 AI 预测式负载均衡的优势。这是将前面所有工作串联为完整故事的最终环节。

**本节产出：** `scripts/plot_results.py`、`figures/` 目录下的对比图表、实验数据 CSV

---

### 5.1 你需要做什么

1. 依次运行三个控制器（base / threshold / ai），每个都用相同的流量模型跑 120 秒
2. 每次运行保存 Ryu 日志、iperf 输出、流量数据 CSV
3. 编写 `plot_results.py` 读取三组数据，生成对比图表
4. 编写 `plot_results.py` 的预测准确度图，对比 AI 的预测值 vs 实际值

#### 三阶段对照实验设计

| 实验 | 控制器 | 预期行为 | 对比意义 |
|------|--------|---------|---------|
| **Exp A: 无负载均衡** | `base_controller.py` | 流量全走单一路径，另一条空闲 | 证明 LB 的必要性 |
| **Exp B: 阈值响应式** | `threshold_balancer.py` | 拥塞后才切换，有延迟 | 传统方法的瓶颈 |
| **Exp C: AI 预测式** | `predictive_balancer.py` | 拥塞前主动切换，无感知 | **AI 的核心优势** |

### 5.2 关键概念

#### 统一流量模型

三组实验必须使用**完全相同的流量模型**，否则对比没有意义。`traffic_gen.py` 生成的锯齿波 + 高斯噪声模式：

- 带宽从 2 Mbps 线性增长到 8 Mbps（30 秒周期）
- 叠加 σ=0.5 Mbps 的高斯噪声（模拟微突发）
- 每 3 秒一个 iperf 命令（UDP 模式）
- 总时长 120 秒，覆盖 4 个锯齿周期

#### 度量指标

| 指标 | Exp A (无 LB) | Exp B (阈值) | Exp C (AI) | 说明 |
|------|--------------|-------------|------------|------|
| 路径利用率分布 | A: ~100%, B: ~0% | A/B 交替 | A/B 交替 | 负载是否均衡 |
| 首次切换时间 | N/A | ~25s (拥塞后) | ~18s (拥塞前) | **AI 提前量** |
| 重路由切换次数 | 0 | 3-5 次 | 2-3 次 | 频率（越少越稳定） |
| 平均吞吐量 | ~9 Mbps | ~8 Mbps | ~9 Mbps | 高负载下平稳度 |
| 丢包率 | 高（拥塞时） | 中（切换瞬间） | 低 | **核心对比指标** |

**核心故事线：** Exp A 证明"需要 LB"，Exp B 证明"传统 LB 有延迟"，Exp C 证明"AI 能提前切换"。首次切换时间是报告中最关键的数字。

### 5.3 实现步骤

#### Step 1：运行实验 A（基线）

```bash
# 终端 1：启动 base_controller
ryu-manager controller/base_controller.py --observe-links 2>&1 | tee data/expA_ryu.log

# 终端 2：运行实验编排
sudo python3 scripts/run_experiment.py
# 退出后停止 Ryu
```

base_controller 没有流量数据 CSV 输出，你需要从 Ryu 日志或手动 dump-flows 中记录路径利用率。

#### Step 2：运行实验 B（阈值）

```bash
# 终端 1
ryu-manager controller/threshold_balancer.py --observe-links 2>&1 | tee data/expB_ryu.log

# 终端 2
sudo python3 scripts/run_experiment.py
# 保存 → data/traffic_data.csv（自动由 StatsMixin 生成）
```

#### Step 3：运行实验 C（AI）

```bash
# 终端 1
ryu-manager controller/predictive_balancer.py --observe-links 2>&1 | tee data/expC_ryu.log

# 终端 2
sudo python3 scripts/run_experiment.py
# 保存 → data/traffic_data.csv, data/predictions.csv
```

#### Step 4：编写 plot_results.py

创建 `/root/SDN/scripts/plot_results.py`，生成两类图表：

**图表 1：链路利用率三实验对比**（3 子图，共享 X 轴时间）
- 每个子图：path_A 和 path_B 的利用率曲线 + 70% 阈值线
- 子图标题：Exp A / Exp B / Exp C
- 用途：一眼看出 Exp A 单路径满载、Exp B 有切换延迟、Exp C 提前切换

**图表 2：AI 预测准确度**（单图）
- 蓝实线：Exp C 的 path_A 实际利用率（从 `expC_traffic_data.csv` 读取）
- 红虚线：DecisionEngine 的 EMA 预测值（从 `predictions.csv` 读取）
- 橙色虚线：65% 预测阈值
- 用途：展示模型预测 vs 实际值的吻合度

读取数据的注意事项：
- `traffic_data.csv` 是长表格式，需要按 `link_label` 过滤 `path_A` / `path_B`
- `predictions.csv` 的 timestamp 是对齐后的时间戳，需要与 `traffic_data.csv` 的 timestamp 对齐
- 利用率是 0-1 范围，绘图时乘 100 转为百分比

#### Step 5：生成图表

```bash
python3 scripts/plot_results.py
# 预期：Saved figures/utilization_comparison.png
#       Saved figures/prediction_accuracy.png
```

确保 `figures/` 目录存在。

### 5.4 常见陷阱

1. **三次实验没有用相同的流量模型**：必须用相同的 `traffic_gen.py` 参数（pattern、duration、noise-sigma），否则对比不公平。
2. **Exp A 没有 traffic_data.csv**：base_controller 不输出流量数据。可以从 Ryu 日志中提取，或给 base_controller 也加上 StatsMixin 的 CSV 输出。
3. **predictions.csv 和 traffic_data.csv 的 timestamp 不对齐**：两者都使用了时间桶对齐（`bucket_ts = (int(now) // POLL_INTERVAL) * POLL_INTERVAL`），但 POLL_INTERVAL 可能不同。确保一致。
4. **matplotlib 中文显示乱码**：如果图表标题用了中文，需要设置字体：`plt.rcParams['font.sans-serif'] = ['SimHei']`。建议全部用英文。
5. **图表 Y 轴范围不统一**：三张子图必须用相同的 Y 轴范围（0-120%），否则无法直观对比。

### 5.5 验证检查点

- [ ] 三组实验均完成，日志文件存在于 `data/` 目录
- [ ] `data/expB_traffic_data.csv` 和 `data/expC_traffic_data.csv` 有数据
- [ ] `data/predictions.csv` 有数据（Exp C 生成）
- [ ] `figures/utilization_comparison.png` 生成，三张子图清晰可辨
- [ ] `figures/prediction_accuracy.png` 生成，预测曲线与实际曲线趋势一致
- [ ] Exp A 中 path_B 利用率接近 0%（证明无 LB 时单路径过载）
- [ ] Exp C 的首次切换时间早于 Exp B（证明 AI 提前量）

### 5.6 预期实验结果分析

**Exp A（无负载均衡）：**
- 所有流量走路径 A（由 MAC 学习顺序决定）
- 路径 A 利用率 ~100%，路径 B 利用率 ~0%
- 当流量超过 10 Mbps 时产生拥塞和丢包
- **结论：** 证明负载均衡的必要性

**Exp B（阈值响应式）：**
- 当路径 A 利用率超过 70% 时触发切换
- 切换前已有拥塞发生（被动响应）
- 切换瞬间有短暂丢包
- **结论：** 阈值方法有效但有延迟

**Exp C（AI 预测式）：**
- 冷启动阶段（0-15s）：静态路由，与 Exp A 相同
- AI 预测阶段（15s+）：模型预测将在下周期拥塞，提前切换
- 切换发生在拥塞**之前**，无感知
- 冷却锁防止乒乓效应
- **结论：** AI 预测比阈值响应更快，有效避免拥塞

### 5.7 参考实现

<details>
<summary>点击展开 plot_results.py 完整代码</summary>

```python
#!/usr/bin/env python3
"""实验结果可视化：生成对比图表"""
import os
import pandas as pd
import matplotlib.pyplot as plt
import numpy as np


def plot_utilization_comparison():
    """链路利用率对比图：三实验叠加"""
    fig, axes = plt.subplots(3, 1, figsize=(12, 10), sharex=True)

    for ax, (label, csv_path) in zip(axes, [
        ('Exp A: No LB', 'data/expA_traffic_data.csv'),
        ('Exp B: Threshold LB', 'data/expB_traffic_data.csv'),
        ('Exp C: AI-Predictive LB', 'data/expC_traffic_data.csv'),
    ]):
        if not os.path.exists(csv_path):
            ax.set_title(f'{label} (no data)')
            continue

        df = pd.read_csv(csv_path)
        path_a = df[df['link_label'] == 'path_A']
        path_b = df[df['link_label'] == 'path_B']

        ax.plot(path_a['timestamp'], path_a['utilization'] * 100,
                'b-', label='Path A', alpha=0.8)
        ax.plot(path_b['timestamp'], path_b['utilization'] * 100,
                'r-', label='Path B', alpha=0.8)
        ax.axhline(y=70, color='gray', linestyle='--', alpha=0.5, label='70% threshold')
        ax.set_ylabel('Utilization (%)')
        ax.set_title(label)
        ax.legend(loc='upper right')
        ax.set_ylim(0, 120)

    axes[-1].set_xlabel('Time (s)')
    plt.tight_layout()
    os.makedirs('figures', exist_ok=True)
    plt.savefig('figures/utilization_comparison.png', dpi=150)
    print("Saved figures/utilization_comparison.png")


def plot_prediction_accuracy():
    """AI 预测准确度：预测值 vs 实际值"""
    actual_path = 'data/expC_traffic_data.csv'
    pred_path = 'data/predictions.csv'

    if not os.path.exists(actual_path) or not os.path.exists(pred_path):
        print("Skipping prediction accuracy plot: missing data files")
        return

    actual_df = pd.read_csv(actual_path)
    actual_a = actual_df[actual_df['link_label'] == 'path_A'].sort_values('timestamp')

    pred_df = pd.read_csv(pred_path)
    pred_a = pred_df[pred_df['link'] == 'path_A'].sort_values('timestamp')

    fig, ax = plt.subplots(figsize=(12, 5))
    ax.plot(actual_a['timestamp'], actual_a['utilization'] * 100,
            'b-', label='Actual', alpha=0.8)
    ax.plot(pred_a['timestamp'], pred_a['smoothed'].astype(float) * 100,
            'r--', label='Predicted (EMA)', alpha=0.8)
    ax.axhline(y=65, color='orange', linestyle='--', alpha=0.5,
               label='65% prediction threshold')
    ax.set_xlabel('Time (s)')
    ax.set_ylabel('Utilization (%)')
    ax.set_title('AI Prediction vs Actual (Path A)')
    ax.legend()
    plt.tight_layout()
    os.makedirs('figures', exist_ok=True)
    plt.savefig('figures/prediction_accuracy.png', dpi=150)
    print("Saved figures/prediction_accuracy.png")


if __name__ == '__main__':
    plot_utilization_comparison()
    plot_prediction_accuracy()
```

</details>

**生成的图表：**

| 图表 | 文件名 | 用途 |
|------|--------|------|
| 链路利用率三实验对比 | `figures/utilization_comparison.png` | 核心对比图，展示 AI 提前切换 |
| AI 预测准确度 | `figures/prediction_accuracy.png` | 展示模型预测 vs 实际值 |

---

## 附录 A：常见问题排查

### Q: pingall 丢包率高达 83%？

**原因：** 使用了无环路防护的控制器（如 `simple_switch_13`）。双路径拓扑存在物理环路。

**解决：** 使用我们的 `base_controller.py`（有广播风暴抑制）或负载均衡控制器（显式路径安装，无泛洪）。

### Q: 交换机连接不上控制器？

```bash
# 确认 Ryu 在监听
ss -tlnp | grep 6633

# 确认交换机配置
mininet> sh ovs-vsctl get-controller s1

# 手动设置
mininet> sh ovs-vsctl set-controller s1 tcp:127.0.0.1:6633
```

### Q: model 加载失败？

```bash
# 确认模型文件存在
ls -la models/

# 确认 sklearn 版本一致
python3 -c "import sklearn; print(sklearn.__version__)"
```

### Q: iperf 吞吐量为 0？

```bash
# 先确认 ping 通
mininet> h1 ping -c 1 h3

# 确认 iperf 服务端在运行
mininet> h1 ps aux | grep iperf

# 确认使用 UDP 模式
mininet> h3 iperf -c 10.0.0.1 -u -b 5M -t 3 -i 1
```

### Q: CSV 数据为空？

**排查：**
1. 确认交换机已连接控制器（Ryu 日志有 "Switch X connected"）
2. 确认有流量产生（iperf 正在运行）
3. 确认 CSV 文件路径正确（`data/traffic_data.csv`）
4. 检查文件权限

### Q: 控制器代码修改后不生效？

```bash
mininet> exit
# Ctrl+C 停止 Ryu
# 重新启动
ryu-manager controller/predictive_balancer.py --observe-links
sudo python3 scripts/run_experiment.py
```

---

## 附录 B：命令速查表

### Mininet 命令

| 命令 | 说明 |
|------|------|
| `sudo python3 scripts/run_experiment.py` | 自动运行实验（拓扑 + 流量 + 清理） |
| `pingall` | 测试全网连通性 |
| `iperf h1 h3` | TCP 吞吐量测试 |
| `sh ovs-ofctl show s1 -O OpenFlow13` | 查看 s1 端口信息 |
| `sh ovs-ofctl dump-flows s1 -O OpenFlow13` | 查看 s1 流表 |
| `sh ovs-ofctl dump-ports s1 -O OpenFlow13` | 查看 s1 端口统计 |
| `exit` | 退出 Mininet |

### Ryu 命令

| 命令 | 说明 |
|------|------|
| `ryu-manager controller/base_controller.py --observe-links` | 基础控制器 |
| `ryu-manager controller/threshold_balancer.py --observe-links` | 阈值负载均衡 |
| `ryu-manager controller/predictive_balancer.py --observe-links` | AI 负载均衡 |
| `ryu-manager --verbose controller/predictive_balancer.py` | 详细日志模式 |

### Python 脚本

| 命令 | 说明 |
|------|------|
| `python3 scripts/traffic_gen.py --pattern sawtooth` | 生成锯齿波流量 |
| `python3 scripts/traffic_gen.py --pattern sine` | 生成正弦波流量 |
| `python3 scripts/traffic_gen.py --pattern step` | 生成阶跃流量 |
| `python3 scripts/assemble_features.py` | 组装训练特征 |
| `python3 scripts/train_model.py` | 训练 ML 模型（自适应采样） |
| `python3 scripts/plot_results.py` | 生成对比图表 |

---

## 附录 C：开发检查点清单

- [x] **Phase 1：** Mininet 拓扑运行成功，双路径建立，pingall 0% dropped
- [x] **Phase 2：** base_controller.py 验证通过
- [x] **Phase 3：** StatsMixin（含自适应轮询）+ 流量生成器 + threshold_balancer.py 实现 ✅，特征组装 + RF 模型训练完成 ✅
- [ ] **Phase 4：** predictive_balancer.py 实现，冷启动→AI→冷却状态机验证通过
- [ ] **Phase 5：** 三阶段对照实验完成，有完整数据和图表
- [ ] **收尾：** 截图、录屏、数据文件整理完毕

---

## 附录 D：可选扩展（加分项）

### D.1 模型在线更新

每隔 N 个周期用新数据微调模型（在线学习），适应流量模式变化。

### D.2 多模型对比

可考虑 Ridge 回归作为轻量级替代（模型 ~1KB，推理 <0.1ms），与当前 RF 模型（~350KB，推理 ~1ms）对比。在 3 秒轮询间隔下两者都足够快，但 RF 的非线性拟合能力更强（R² 0.94 vs Ridge ~0.88）。

### D.3 可视化仪表盘

用 Flask + WebSocket 实时展示链路利用率和 AI 预测值。

### D.4 IPv6 支持

课程鼓励"底层代码涉及 IPv6 协议"。在控制器中添加 IPv6 包处理。
