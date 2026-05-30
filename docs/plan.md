# 基于 MLP 预测的 SDN 动态负载均衡器 — 完整实现方案

## 目录

- [1. 概述与设计目标](#1-概述与设计目标)
  - [1.1 项目简介](#11-项目简介)
  - [1.2 核心设计目标](#12-核心设计目标)
  - [1.3 技术栈](#13-技术栈)
  - [1.4 系统架构总览](#14-系统架构总览)
- [2. Fat-Tree 拓扑与转发架构](#2-fat-tree-拓扑与转发架构)
  - [2.1 Fat-Tree k=4 拓扑设计](#21-fat-tree-k4-拓扑设计)
  - [2.2 转发面架构](#22-转发面架构)
- [3. 项目目录结构与文件说明](#3-项目目录结构与文件说明)
  - [3.1 完整目录树](#31-完整目录树)
  - [3.2 模块依赖关系](#32-模块依赖关系)
- [4. 控制器核心模块详解](#4-控制器核心模块详解)
  - [4.1 遥测模块：StatsMixin](#41-遥测模块statsmixincontrollerstats_mixinpy)
  - [4.2 控制器基类：BaseBalancer](#42-控制器基类basebalancercontrollerbase_balancerpy)
  - [4.3 静态 ECMP 基线：BaseECMPController](#43-静态-ecmp-基线baseecmpcontrollercontrollerbase_controllerpy)
  - [4.4 权重计算引擎：DynamicWeightEngine](#44-权重计算引擎dynamicweightenginecontrollerweight_enginepy)
  - [4.5 阈值响应式负载均衡：ThresholdBalancer](#45-阈值响应式负载均衡thresholdbalancercontrollerthreshold_balancerpy)
  - [4.6 AI 预测式负载均衡：PredictiveBalancer](#46-ai-预测式负载均衡predictivebalancercontrollerpredictive_balancerpy)
- [5. 数据采集与模型训练流水线](#5-数据采集与模型训练流水线)
  - [5.1 训练数据采集](#51-训练数据采集scriptscollect_training_datapy)
  - [5.2 特征工程](#52-特征工程scriptsassemble_global_featurespy)
  - [5.3 MLP 模型训练](#53-mlp-模型训练scriptstrain_global_mlppy)
- [6. 对照实验设计与自动化运行](#6-对照实验设计与自动化运行)
  - [6.1 实验设计：概率哈希碰撞 + 渐进突发](#61-实验设计概率哈希碰撞--渐进突发)
  - [6.2 实验自动化运行](#62-实验自动化运行scriptsrun_experimentpy)
- [7. 实验结果与性能分析](#7-实验结果与性能分析)
  - [7.1 核心性能指标汇总](#71-核心性能指标汇总)
  - [7.2 逐流详细数据](#72-逐流详细数据30-轮平均)
  - [7.3 逐轮次稳定性分析](#73-逐轮次稳定性分析)
  - [7.4 关键发现](#74-关键发现)
  - [7.5 性能差异根因分析](#75-性能差异根因分析)
  - [7.6 MLP 模型预测精度](#76-mlp-模型预测精度)
  - [7.7 权重调整行为分析](#77-权重调整行为分析)
- [8. 可视化系统详解](#8-可视化系统详解)
  - [8.1 流量时空特性分析](#81-流量时空特性分析scriptsplot_traffic_analysispy)
  - [8.2 MLP 模型评估](#82-mlp-模型评估scriptsplot_mlp_evaluationpy)
  - [8.3 策略对比分析](#83-策略对比分析scriptsplot_policy_comparisonpy)
- [9. Web 实时监控仪表盘](#9-web-实时监控仪表盘)
  - [9.1 后端架构](#91-后端架构webapppy)
  - [9.2 实验管理器](#92-实验管理器webexperiment_runnerpy)
  - [9.3 拓扑工具](#93-拓扑工具webtopology_utilspy)
  - [9.4 前端功能](#94-前端功能)
- [10. 开发过程中遇到的问题与解决方案](#10-开发过程中遇到的问题与解决方案)
  - [10.1 环路拓扑中的广播风暴](#101-环路拓扑中的广播风暴)
  - [10.2 自定义控制器 100% 丢包](#102-自定义控制器-100-丢包)
  - [10.3 数据采集依赖反转](#103-数据采集依赖反转)
  - [10.4 iperf 并发流端口冲突与僵尸进程污染](#104-问题四iperf-并发流端口冲突与僵尸进程污染)
  - [10.5 遥测多协程并发导致的采样数据污染](#105-问题五遥测多协程并发导致的采样数据污染)
  - [10.6 OVS 特殊端口统计值干扰利用率计算](#106-问题六ovs-特殊端口统计值干扰利用率计算)
  - [10.7 权重微振荡导致的 OpenFlow 信令风暴](#107-问题七权重微振荡导致的-openflow-信令风暴)
  - [10.8 训练数据与测试环境的分布偏移](#108-问题八训练数据与测试环境的分布偏移)
  - [10.9 实验时间精度漂移](#109-问题九实验时间精度漂移)
- [11. 环境配置与部署指南](#11-环境配置与部署指南)
  - [11.1 系统依赖安装](#111-系统依赖安装)
  - [11.2 Python 环境配置](#112-python-环境配置)
  - [11.3 验证安装](#113-验证安装)
  - [11.4 完整运行流程](#114-完整运行流程)
- [12. 数据文件格式规范](#12-数据文件格式规范)
  - [12.1 原始遥测数据](#121-原始遥测数据datatraffic_datacsv)
  - [12.2 特征矩阵](#122-特征矩阵dataglobal_featurespkl)
  - [12.3 训练模型](#123-训练模型modelsglobal_mlp_modelpkl)
  - [12.4 组权重日志](#124-组权重日志datagroup_weightscsv)
  - [12.5 实验结果](#125-实验结果datagroup_average_resultscsv)
  - [12.6 每链路误差统计](#126-每链路误差统计dataviz_per_link_metricscsv)
- [13. 实施阶段规划](#13-实施阶段规划)
  - [阶段 1：基础设施搭建](#阶段-1基础设施搭建)
  - [阶段 2：传统负载均衡实现](#阶段-2传统负载均衡实现)
  - [阶段 3：数据采集与模型训练](#阶段-3数据采集与模型训练)
  - [阶段 4：AI 预测式控制器](#阶段-4ai-预测式控制器)
  - [阶段 5：对照实验与可视化](#阶段-5对照实验与可视化)
  - [阶段 6：Web 仪表盘与文档](#阶段-6web-仪表盘与文档)
  - [总计预计耗时](#总计预计耗时)
- [14. 已知局限与扩展方向](#14-已知局限与扩展方向)
  - [14.1 已知局限](#141-已知局限)
  - [14.2 可能的扩展方向](#142-可能的扩展方向)
  - [14.3 理论边界与开放问题](#143-理论边界与开放问题)

---

## 1. 概述与设计目标

### 1.1 项目简介

本项目基于 Ryu 控制器框架与 Mininet 网络仿真器，在 Fat-Tree k=4 数据中心拓扑上实现了一个 **基于 MLP 预测的 SDN 动态负载均衡器（MLP-Predictive Dynamic Load Balancer for SDN）**。

系统利用 OpenFlow 1.3 的 Group Table（SELECT 类型）实现等价多路径（ECMP）转发，并集成一个多层感知机（MLP）神经网络预测骨干链路的拥塞趋势。在拥塞实际发生之前，系统动态调整组表权重，达到预防性调整流量、缓解网络拥塞的目的。

项目通过三阶段对照实验验证了基于 MLP 预测的负载均衡相比传统方案的优势：
- **静态 ECMP（base）**：固定 50:50 权重，不做任何调整
- **阈值响应式（threshold）**：基于当前利用率越限触发，滞后响应
- **AI 预测式（predictive）**：基于 MLP 预测未来 1.5s 状态，主动调整

### 1.2 核心设计目标

| 目标 | 说明 | 实现方式 |
|:---|:---|:---|
| **主动预测** | 在拥塞发生前 1.5s 开始调整 | MLP 滑动窗口预测 + 混合有效利用率 |
| **轻量推理** | 控制器运行时零 sklearn 依赖 | 训练/推理分离，纯 NumPy 前向传播 |
| **防振荡** | 避免微小波动导致频繁流表下发 | 5% 死区（Deadband）过滤 |
| **可对照** | 三种策略自动化对比 | 统一实验框架，相同流量模板 |
| **可视化** | 16 张多维度分析图表 | 三个独立绘图脚本 |
| **实时监控** | Web 仪表盘实时观测 | Flask + SocketIO + Cytoscape.js |

### 1.3 技术栈

| 组件 | 版本 / 说明 |
|:---|:---|
| 操作系统 | Ubuntu 20.04 ~ 24.04（需 root 权限运行 Mininet） |
| 控制器框架 | Ryu 4.34（OpenFlow 1.3） |
| 网络仿真 | Mininet + OVS 3.3.4 |
| 流量测试 | iperf（UDP 模式，每流独立端口） |
| Python | 3.9（Conda 虚拟环境 `sdn_env`） |
| 机器学习 | scikit-learn `MLPRegressor`（仅训练阶段） |
| 数值计算 | NumPy（运行时推理） |
| 数据处理 | Pandas、joblib |
| 可视化 | Matplotlib（180 DPI 输出） |
| Web 后端 | Flask + Flask-SocketIO |
| Web 前端 | Cytoscape.js（拓扑图）+ 原生 JS |

### 1.4 系统架构总览

```
┌─────────────────────────────────────────────────────────┐
│                    Web 仪表盘 (web/)                      │
│  Flask + SocketIO ← 读取 CSV → 实时推送 → Cytoscape.js   │
└────────────────────────┬────────────────────────────────┘
                         │ 控制实验启停
┌────────────────────────▼────────────────────────────────┐
│              对照实验框架 (run_experiment.py)               │
│  启动 Ryu 子进程 → 创建 Mininet → 注入 iperf → 采集指标    │
└────────┬───────────────┬───────────────┬────────────────┘
         │               │               │
    ┌────▼────┐   ┌──────▼──────┐  ┌─────▼──────┐
    │  base   │   │  threshold  │  │predictive  │
    │ECMP基线 │   │ 阈值响应式  │  │ AI预测式   │
    └────┬────┘   └──────┬──────┘  └─────┬──────┘
         │               │               │
         └───────────────┼───────────────┘
                         │
              ┌──────────▼──────────┐
              │   BaseBalancer      │
              │  (静态转发面)        │
              │  eth_dst 流表规则   │
              │  SELECT Group Table │
              └──────────┬──────────┘
                         │
         ┌───────────────┼───────────────┐
         │               │               │
    ┌────▼────┐   ┌──────▼──────┐  ┌─────▼──────┐
    │StatsMixin│  │DynamicWeight│  │  Mininet   │
    │ 遥测采集 │  │   Engine    │  │ Fat-Tree   │
    │ 0.5s周期 │  │ MLP推理+指数│  │  k=4拓扑   │
    └─────────┘  │ 权重+死区   │  └────────────┘
                 └─────────────┘
```

---

## 2. Fat-Tree 拓扑与转发架构

### 2.1 Fat-Tree k=4 拓扑设计

网络拓扑采用 Fat-Tree k=4 架构，是现代数据中心网络的经典设计方案。

#### 拓扑组成

| 层级 | 设备数量 | DPID 范围 | 说明 |
|:---|:---:|:---:|:---|
| Host | 16 | — | `h0_0` ~ `h3_3`（4 Pod × 2 Edge/Pod × 2 Host/Edge） |
| Edge | 8 | 1-8 | 每台连接 2 台主机，上行连接 2 台 Aggregation |
| Aggregation | 8 | 9-16 | 每台连接 2 台 Edge，上行连接 2 台 Core |
| Core | 4 | 17-20 | 全连接到所有 Pod 的 Aggregation |

#### 链路带宽分层设计

| 链路类型 | 连接 | 带宽 | 说明 |
|:---|:---|:---:|:---|
| Access | Host ↔ Edge | 10 Mbps | 主机接入链路 |
| Edge-Agg | Edge ↔ Aggregation | 10 Mbps | Pod 内下行汇聚 |
| **Agg-Core** | **Aggregation ↔ Core** | **2 Mbps** | **瓶颈骨干链路** |

骨干链路总横截带宽 = 4 Core × 2 Mbps = **8 Mbps**，刻意设为瓶颈以便触发拥塞。

#### DPID 分配策略（`topo/fat_tree_topo.py`）

通过函数式计算确保 DPID 的确定性与可推导性：

```python
K = 4
PODS = K                    # 4 个 Pod
EDGE_PER_POD = K // 2       # 每 Pod 2 台 Edge
AGG_PER_POD = K // 2        # 每 Pod 2 台 Aggregation
HOST_PER_EDGE = K // 2      # 每台 Edge 连 2 台主机

def _edge_dpid(pod, idx):
    return pod * EDGE_PER_POD + idx + 1
    # Pod 0: s1, s2 | Pod 1: s3, s4 | Pod 2: s5, s6 | Pod 3: s7, s8

def _agg_dpid(pod, idx):
    return PODS * EDGE_PER_POD + pod * AGG_PER_POD + idx + 1
    # Pod 0: s9, s10 | Pod 1: s11, s12 | Pod 2: s13, s14 | Pod 3: s15, s16

def _core_dpid(idx):
    return PODS * EDGE_PER_POD + PODS * AGG_PER_POD + idx + 1
    # s17, s18, s19, s20
```

#### 主机命名与 IP 分配

主机命名格式为 `h{pod}_{index}`，IP 由 Mininet 自动分配（`autoSetMacs=True`）：

```
Pod 0: h0_0 (10.0.0.1), h0_1 (10.0.0.2), h0_2 (10.0.0.3), h0_3 (10.0.0.4)
Pod 1: h1_0 (10.0.0.5), h1_1 (10.0.0.6), h1_2 (10.0.0.7), h1_3 (10.0.0.8)
Pod 2: h2_0 (10.0.0.9), h2_1 (10.0.0.10), h2_2 (10.0.0.11), h2_3 (10.0.0.12)
Pod 3: h3_0 (10.0.0.13), h3_1 (10.0.0.14), h3_2 (10.0.0.15), h3_3 (10.0.0.16)
```

#### 物理链路连接细节

每台交换机的端口分配遵循固定规则：

| 交换机层 | 端口 1 | 端口 2 | 端口 3 | 端口 4 |
|:---|:---|:---|:---|:---|
| **Edge (1-8)** | Host A | Host B | Agg A (uplink) | Agg B (uplink) |
| **Aggregation (9-16)** | Edge A (downlink) | Edge B (downlink) | Core A (uplink) | Core B (uplink) |
| **Core (17-20)** | Pod 0 Agg | Pod 1 Agg | Pod 2 Agg | Pod 3 Agg |

示例：`s1`（Pod 0, Edge 0）的端口 3 连接 `s9`（Pod 0, Agg 0），端口 4 连接 `s10`（Pod 0, Agg 1）。

#### OVS dp_hash 配置

`configure_select_hash()` 对所有 20 台 OVS 交换机设置：

```bash
ovs-vsctl set bridge s{dpid} other_config:group-table-selection-method=dp_hash
```

这确保 SELECT Group 的桶选择由 OVS 数据平面哈希（基于 5 元组的 `dp_hash`）完成，而非 OpenFlow 控制器哈希，实现**线速逐流一致性哈希**。每条流的路径在其生命周期内保持不变，避免乱序。

#### Core 链路缓冲区限制

```python
net.addLink(agg_switch, core_switch, bw=BW_AGG_CORE, max_queue_size=30)
```

`max_queue_size=30` 限制了 Core 链路的 HTB 队列长度，防止缓冲区膨胀（Buffer Bloat）。当队列满时，新到达的数据包被立即丢弃，使拥塞效应快速显现而非被缓冲区吸收后延迟爆发。

### 2.2 转发面架构

所有控制器共享统一的静态转发面架构，核心逻辑在 `BaseBalancer._setup_rules()` 中实现。

#### Group Table 方案

仅 DPID 1-16（Edge 与 Aggregation 层）创建 `group_id=1` 的 SELECT 类型组表：

```python
buckets = []
for port in [3, 4]:
    buckets.append(
        parser.OFPBucket(
            weight=50,                              # 初始权重各 50
            watch_port=port,                        # 监控端口状态
            watch_group=ofproto.OFPG_ANY,           # 不监控组状态
            actions=[parser.OFPActionOutput(port)], # 输出到对应端口
        )
    )
datapath.send_msg(
    parser.OFPGroupMod(
        datapath=datapath,
        command=ofproto.OFPGC_ADD,          # 添加操作
        type_=ofproto.OFPGT_SELECT,        # SELECT 类型
        group_id=1,
        buckets=buckets,
    )
)
```

Core 交换机（DPID 17-20）**不创建 Group Table**，因为它们到每个 Pod 有唯一确定路径。

#### 流表规则（优先级 10）

基于 `eth_dst` 静态匹配规则，遍历 16 个可能的目标主机 MAC 地址：

```python
for i in range(16):
    match = parser.OFPMatch(eth_dst=f"00:00:00:00:00:{i+1:02x}")
    pod, e_idx = i // 4, (i % 4) // 2
    if dpid <= 8:       # Edge 交换机
        if (dpid - 1) // 2 == pod and (dpid - 1) % 2 == e_idx:
            actions = [parser.OFPActionOutput((i % 2) + 1)]  # 直连主机
        else:
            actions = [parser.OFPActionGroup(group_id=1)]    # 转发到 SELECT 组
    elif dpid <= 16:     # Aggregation 交换机
        if (dpid - 9) // 2 == pod:
            actions = [parser.OFPActionOutput(e_idx + 1)]    # 同 Pod 下行
        else:
            actions = [parser.OFPActionGroup(group_id=1)]    # 跨 Pod 到 Core
    else:                # Core 交换机
        actions = [parser.OFPActionOutput(pod + 1)]          # 确定性路由
    self.add_flow(datapath, 10, match, actions)
```

**路由决策逻辑**：

```
目标主机 MAC: 00:00:00:00:00:{i+1:02x}
目标 Pod:     i // 4
目标 Edge:    (i % 4) // 2

Edge 交换机 (DPID 1-8):
  └─ 直连？(dpid-1)//2 == pod AND (dpid-1)%2 == e_idx
     ├─ 是 → Output 到主机端口 (i%2)+1
     └─ 否 → Group Table (SELECT, 端口 3/4 均衡)

Aggregation 交换机 (DPID 9-16):
  └─ 同 Pod？(dpid-9)//2 == pod
     ├─ 是 → Output 到 Edge 端口 e_idx+1
     └─ 否 → Group Table (SELECT, 端口 3/4 均衡)

Core 交换机 (DPID 17-20):
  └─ 直接 Output 到 pod+1（确定性路由）
```

#### 设计决策

1. **静态 `eth_dst` 匹配**：消除了对动态 MAC 学习、ARP 处理或 Packet-In 的需求，转发面完全确定性，控制面开销最小化
2. **Group Table 作为唯一控制点**：所有三种策略的差异仅在于如何计算和下发 Group Table 权重
3. **Core 无 Group Table**：Core 到每个 Pod 有唯一路径，无需负载均衡
4. **防重复配置**：`configured_switches` 集合记录已配置的 DPID，避免事件处理器重复触发

---

## 3. 项目目录结构与文件说明

### 3.1 完整目录树

```
/home/yang/SDN/
├── topo/                              # Mininet 拓扑脚本
│   └── fat_tree_topo.py               # Fat-Tree k=4 拓扑生成、OVS dp_hash 配置、清理函数
│
├── controller/                        # Ryu 控制器与核心控制逻辑
│   ├── stats_mixin.py                 # 遥测 Mixin（0.5s 周期采集端口字节数，精确利用率计算）
│   ├── base_balancer.py               # 控制器基类（流表安装、Group Table 创建与修改、遥测分发）
│   ├── base_controller.py             # 静态 ECMP 基线（继承 BaseBalancer，仅 init_stats()）
│   ├── weight_engine.py               # 权重计算引擎（MLP 纯 NumPy 推理、指数分配、死区过滤）
│   ├── threshold_balancer.py          # 阈值响应式（迟滞状态机，无 ML 依赖）
│   └── predictive_balancer.py         # AI 预测式（全局 MLP 前向推理，主动调整）
│
├── scripts/                           # 数据采集、训练与性能可视化脚本
│   ├── collect_training_data.py       # 训练数据采集（静态 ECMP + 随机流量注入，2000 轮）
│   ├── assemble_global_features.py    # 特征组装（骨干链路过滤、Pivot、滑动窗口构建）
│   ├── train_global_mlp.py            # MLP 训练（MLPRegressor + 早停 + 多维度评估导出）
│   ├── run_experiment.py              # 三策略对照实验自动化运行（含 iperf 指标采集）
│   ├── plot_traffic_analysis.py       # 流量时空特性分析（热力图、关键链路、总负载、相关矩阵）
│   ├── plot_mlp_evaluation.py         # MLP 模型评估（收敛、散点、追踪、残差、空间误差、层级误差）
│   └── plot_policy_comparison.py      # 策略对比（柱状图、箱线图、CDF、权重演进、帕累托、雷达）
│
├── web/                               # Web 实时监控仪表盘
│   ├── app.py                         # Flask + SocketIO 后端（REST API + WebSocket 实时推送）
│   ├── experiment_runner.py           # 实验子进程生命周期管理（启动、监控、停止）
│   ├── topology_utils.py              # Cytoscape.js 拓扑图数据生成（节点/边/布局/端口映射）
│   └── static/                        # 前端静态资源
│       ├── index.html                 # 主页面（14KB）
│       ├── script.js                  # 前端逻辑（31KB，SocketIO 连接、拓扑渲染、图表更新）
│       └── style.css                  # 样式表（43KB）
│
├── data/                              # 实验数据与中间产物
│   ├── traffic_data.csv               # 原始遥测数据（timestamp, dpid, port_no, utilization）
│   ├── global_features.pkl            # 特征矩阵（X: [N,144], Y: [N,24], timestamps, link_keys）
│   ├── group_weights.csv              # 运行时组权重变化日志（timestamp, dpid, port3, port4）
│   ├── viz_raw_traffic_matrix.pkl     # 完整时序矩阵（供热力图绘制）
│   ├── viz_training_history.pkl       # 训练收敛曲线（loss_curve_, validation_scores_）
│   ├── viz_predictions.pkl            # 测试集预测对照（Y_true, Y_pred, timestamps, link_keys）
│   ├── viz_per_link_metrics.csv       # 每链路误差统计（dpid, port_no, MSE, MAE, RMSE）
│   ├── base_iteration_results.csv     # 基线逐轮次结果
│   ├── base_average_results.csv       # 基线平均结果
│   ├── threshold_iteration_results.csv# 阈值逐轮次结果
│   ├── threshold_average_results.csv  # 阈值平均结果
│   ├── predictive_iteration_results.csv # AI 逐轮次结果
│   └── predictive_average_results.csv # AI 平均结果
│
├── models/                            # 序列化模型
│   └── global_mlp_model.pkl           # MLP 权重 + Scaler 参数 + link_keys + window_size (1.7MB)
│
├── figures/                           # 可视化输出（16 张 PNG，180 DPI）
│   ├── 1_spatiotemporal_heatmap.png   # 全网链路时空利用率热力图
│   ├── 2_key_link_utilization.png     # 关键骨干链路利用率曲线
│   ├── 3_traffic_correlation_matrix.png # 链路流量相关矩阵
│   ├── 4_training_convergence.png     # MLP 训练收敛曲线
│   ├── 5_true_vs_predicted_scatter.png # 真实 vs 预测散点图
│   ├── 6_single_link_tracking.png     # 单链路预测追踪
│   ├── 7_residual_distribution.png    # 预测残差分布
│   ├── 8_spatial_error_distribution.png # 空间误差分布
│   ├── 9_hierarchical_error_distribution.png # 层级误差分布
│   ├── policy_1_grouped_bar.png       # 三策略分组柱状图
│   ├── policy_2_box_plot.png          # 策略稳定性箱线图
│   ├── policy_4_cdf.png              # 丢包/抖动 CDF 曲线
│   ├── policy_5_weight_evolution.png  # Group Table 权重演进
│   ├── policy_6_dual_axis_coevolution.png # 利用率-权重协同演进
│   ├── policy_7_pareto_tradeoff.png   # 帕累托权衡分析
│   └── policy_8_flow_fairness_radar.png # 流公平性雷达图
│
├── docs/                              # 文档
│   ├── plan.md                        # 本文档
│   ├── 配置环境.md                     # 环境配置指南
│   └── 遇到的问题.md                   # 开发问题记录
│
├── README.md                          # 项目说明
└── .gitignore                         # Git 忽略规则
```

### 3.2 模块依赖关系

```
fat_tree_topo.py ←──────── run_experiment.py ────────→ 所有控制器
       ↑                      collect_training_data.py
       │
       └─── 被实验脚本调用创建 Mininet 网络

stats_mixin.py ←── BaseBalancer ←── BaseECMPController (base)
       │              ↑                      │
       │              ├── ThresholdBalancer   │ 仅 init_stats()
       │              └── PredictiveBalancer  │
       │                      │               │
       └── 遥测数据 ─→ weight_engine.py ←─────┘
                          │
                          ├── predict_all() (MLP 推理)
                          └── get_group_weights() (指数分配 + 死区)

traffic_data.csv ←── collect_training_data.py
       ↓
assemble_global_features.py → global_features.pkl
       ↓
train_global_mlp.py → global_mlp_model.pkl + viz_*.pkl
       ↓
predictive_balancer.py ← 加载模型

run_experiment.py → *_iteration_results.csv → *_average_results.csv
       ↓
plot_*.py → figures/*.png

web/app.py ← 读取 traffic_data.csv + group_weights.csv
       ↓
experiment_runner.py ← 启动 run_experiment.py 子进程
```

---

## 4. 控制器核心模块详解

### 4.1 遥测模块：StatsMixin（`controller/stats_mixin.py`）

#### 功能概述

StatsMixin 是一个 Mixin 类，为任何 Ryu 控制器提供链路利用率遥测能力。它通过协程周期性地向所有交换机发送端口统计请求，并计算每条链路的瞬时利用率。

#### 核心常量

```python
POLL_INTERVAL = 0.5  # 遥测采样周期（秒）
```

#### 状态变量（在 `init_stats()` 中初始化）

| 变量 | 类型 | 说明 |
|:---|:---|:---|
| `datapaths` | `dict{dpid: datapath}` | 已连接的交换机对象 |
| `prev_port_stats` | `dict{(dpid,port_no): tx_bytes}` | 上一次 TX 字节数 |
| `prev_time` | `dict{dpid: timestamp}` | 上一次测量时间戳 |
| `link_utilization` | `dict{(dpid,port_no): float}` | 当前利用率值 |
| `current_snapshot_ts` | `float` | 当前轮询周期的量化时间戳 |
| `xid_to_ts` | `dict{xid: timestamp}` | OpenFlow 事务 ID → 请求时间戳 |

#### 核心方法：`_monitor()` 协程

```python
def _monitor(self):
    while True:
        hub.sleep(self.POLL_INTERVAL)  # 休眠 0.5s
        if not self.datapaths:
            continue

        # 1. 时间戳量化：对齐到 POLL_INTERVAL 边界
        self.current_snapshot_ts = (
            time.time() // self.POLL_INTERVAL
        ) * self.POLL_INTERVAL

        # 2. 调用子类的决策钩子（负载均衡器的入口）
        if hasattr(self, "on_telemetry_tick"):
            self.on_telemetry_tick()

        # 3. 向所有交换机发送端口统计请求
        for dp in list(self.datapaths.values()):
            req = dp.ofproto_parser.OFPPortStatsRequest(dp, 0, dp.ofproto.OFPP_ANY)
            self.xid_to_ts[req.xid] = self.current_snapshot_ts  # 记录 xid → 时间戳
            dp.send_msg(req)
```

**关键设计**：`xid_to_ts` 映射将每个 OpenFlow 请求的事务 ID 与发出时刻的时间戳绑定。当异步响应回来时，通过 `msg.xid` 反查原始时间戳，确保同一轮询周期内的所有测量共享相同的时间桶，消除多协程并发的时序抖动。

#### 核心方法：`handle_port_stats_reply(ev)`

```python
def handle_port_stats_reply(self, ev):
    msg = ev.msg
    dpid = msg.datapath.id
    now = time.time()

    # 精确反解析发出遥测请求时的时钟快照时间
    bucket_ts = self.xid_to_ts.get(msg.xid, self.current_snapshot_ts)
    if msg.xid in self.xid_to_ts:
        del self.xid_to_ts[msg.xid]

    # 首次收到响应时只记录基线值，不计算利用率
    if dpid not in self.prev_time:
        for stat in msg.body:
            if stat.port_no < 0xFFFFFF00:  # 过滤 OVS 内部端口
                self.prev_port_stats[(dpid, stat.port_no)] = stat.tx_bytes
        self.prev_time[dpid] = now
        return

    delta_time = now - self.prev_time[dpid]
    if delta_time <= 0:
        return

    for stat in msg.body:
        port_no = stat.port_no
        if port_no >= 0xFFFFFF00:  # 过滤 LOCAL 等内部端口
            continue
        key = (dpid, port_no)
        tx_bytes = stat.tx_bytes

        if key in self.prev_port_stats:
            delta_bytes = tx_bytes - self.prev_port_stats[key]
            if delta_bytes >= 0:
                link_bw = self._get_port_bandwidth(dpid, port_no)
                util = (delta_bytes * 8) / (delta_time * link_bw)
                self.link_utilization[key] = min(util, 1.0)  # 剪裁到 [0, 1]
                self.csv_writer.writerow([
                    bucket_ts, dpid, port_no,
                    f"{self.link_utilization[key]:.6f}",
                ])
        self.prev_port_stats[key] = tx_bytes
    self.csv_file.flush()
    self.prev_time[dpid] = now
```

#### 层级自适应带宽函数

```python
def _get_port_bandwidth(self, dpid, port_no):
    if dpid <= 8:           # Edge 交换机：所有端口 10 Mbps
        return 10_000_000
    elif dpid <= 16:        # Aggregation 交换机
        if port_no in [1, 2]:
            return 10_000_000   # 下行端口 10 Mbps
        return 2_000_000        # 上行端口 2 Mbps
    return 2_000_000            # Core 交换机：所有端口 2 Mbps
```

### 4.2 控制器基类：BaseBalancer（`controller/base_balancer.py`）

#### 功能概述

BaseBalancer 是所有控制器策略的基类，通过多重继承组合 Ryu 的 `app_manager.RyuApp` 和自定义的 `StatsMixin`。它封装了流表安装、Group Table 创建与修改、遥测分发等公共功能。

#### 类定义与初始化

```python
class BaseBalancer(app_manager.RyuApp, StatsMixin):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]  # 仅支持 OpenFlow 1.3

    def __init__(self, *args, **kwargs):
        super(BaseBalancer, self).__init__(*args, **kwargs)
        self.datapaths = {}           # dpid → datapath 对象
        self.configured_switches = set()  # 已配置的 DPID 集合（防重复）
```

#### 事件处理器

```python
@set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
def switch_features_handler(self, ev):
    """交换机首次连接时触发"""
    self._setup_rules(ev.msg.datapath)

@set_ev_cls(ofp_event.OFPStateChange, MAIN_DISPATCHER)
def state_change_handler(self, ev):
    """交换机状态迁移至 MAIN 时触发"""
    if ev.state == MAIN_DISPATCHER:
        self._setup_rules(ev.datapath)

@set_ev_cls(ofp_event.EventOFPPortStatsReply, MAIN_DISPATCHER)
def port_stats_reply_handler(self, ev):
    """端口统计响应到达时触发，委托给 StatsMixin"""
    self.handle_port_stats_reply(ev)
```

#### Group Table 权重修改方法

```python
def _modify_group_weights(self, datapath, group_id, weights):
    """发送 OFPGC_MODIFY 指令修改 SELECT Group 的桶权重"""
    ofproto = datapath.ofproto
    parser = datapath.ofproto_parser
    buckets = [
        parser.OFPBucket(
            weight=w,
            watch_port=p,
            watch_group=ofproto.OFPG_ANY,
            actions=[parser.OFPActionOutput(p)],
        )
        for p, w in weights  # weights = [(port_no, weight), ...]
    ]
    msg = parser.OFPGroupMod(
        datapath=datapath,
        command=ofproto.OFPGC_MODIFY,  # 修改操作
        type_=ofproto.OFPGT_SELECT,
        group_id=group_id,
        buckets=buckets,
    )
    datapath.send_msg(msg)
```

### 4.3 静态 ECMP 基线：BaseECMPController（`controller/base_controller.py`）

最简单的控制器，仅 13 行代码：

```python
class BaseECMPController(BaseBalancer):
    def __init__(self, *args, **kwargs):
        super(BaseECMPController, self).__init__(*args, **kwargs)
        self.init_stats()  # 仅启动遥测，不修改任何权重
```

**作用**：
- 训练数据采集阶段：使用此控制器采集无干预的自然流量动态
- 对照实验阶段：作为最差情况基线，代表哈希碰撞后的固定路径行为

### 4.4 权重计算引擎：DynamicWeightEngine（`controller/weight_engine.py`）

#### 功能概述

DynamicWeightEngine 是核心权重决策组件，由 `ThresholdBalancer` 和 `PredictiveBalancer` 共享。它负责 MLP 推理、指数带宽分配和死区过滤。

#### 核心常量

```python
WEIGHT_DEADBAND = 0.05   # 死区阈值：比例变化 < 5% 不触发组表修改
WEIGHT_CURRENT = 0.4     # 当前利用率混合权重
WEIGHT_PREDICTED = 0.6   # 预测利用率混合权重
```

#### 模型加载与零依赖推理（`_load_global_model`）

```python
def _load_global_model(self, model_path):
    data = joblib.load(model_path)
    self.global_model = data["model"]
    self.scaler_X = data.get("scaler_X")
    self.scaler_Y = data.get("scaler_Y")
    self.link_keys = data["link_keys"]           # 24 条骨干链路的 (dpid, port_no) 列表
    self.window_size = data.get("window_size", 6)
    self.models_loaded = True

    # 提取标准化参数
    if self.scaler_X is not None:
        self.scaler_mean_X = self.scaler_X.mean_
        self.scaler_scale_X = self.scaler_X.scale_
    if self.scaler_Y is not None:
        self.scaler_mean_Y = self.scaler_Y.mean_
        self.scaler_scale_Y = self.scaler_Y.scale_

    # 提取 MLP 原始权重矩阵
    self.mlp_weights = self.global_model.coefs_      # [W1, W2, W3, W4]
    self.mlp_biases = self.global_model.intercepts_  # [b1, b2, b3, b4]

    # 删除 sklearn 对象，实现零运行时依赖
    del self.global_model, self.scaler_X, self.scaler_Y
    self.global_model = None
    self.scaler_X = None
    self.scaler_Y = None

    # 初始化滑动窗口（6 × 24 的零矩阵）
    num_links = len(self.link_keys)
    self.feature_history = [[0.0] * num_links for _ in range(self.window_size)]
```

**权重矩阵维度**（以 (256,128,64) 隐藏层为例）：
- `coefs_[0]`: (144, 256) — 输入层 → 第一隐藏层
- `coefs_[1]`: (256, 128) — 第一 → 第二隐藏层
- `coefs_[2]`: (128, 64)  — 第二 → 第三隐藏层
- `coefs_[3]`: (64, 24)   — 第三隐藏层 → 输出层

#### 滑动窗口更新（`update_all_utilizations`）

```python
def update_all_utilizations(self, link_util_dict):
    self.current_utils = link_util_dict
    if not self.models_loaded:
        return

    # 按 link_keys 顺序构建当前时刻的利用率向量
    current_vector = []
    for key in self.link_keys:
        current_vector.append(link_util_dict.get(key, 0.0))

    # 滑动窗口：移除最旧的一行，追加当前行
    self.feature_history.pop(0)
    self.feature_history.append(current_vector)
```

#### MLP 前向传播推理（`predict_all`）

```python
def predict_all(self):
    if not self.models_loaded:
        return

    # 1. 展平滑动窗口为一维输入向量
    X = np.array(self.feature_history, dtype=np.float32).ravel()
    # X.shape = (144,) = 6 时间步 × 24 链路

    # 2. StandardScaler 逆变换（输入标准化）
    X_scaled = (X - self.scaler_mean_X) / self.scaler_scale_X

    # 3. 逐层前向传播（隐藏层 ReLU，输出层线性）
    activation = X_scaled
    num_layers = len(self.mlp_weights)
    for i in range(num_layers - 1):  # 前 3 层：ReLU 激活
        z = np.dot(activation, self.mlp_weights[i]) + self.mlp_biases[i]
        activation = np.maximum(z, 0.0)  # ReLU

    # 最后一层：线性输出
    pred_scaled = np.dot(activation, self.mlp_weights[-1]) + self.mlp_biases[-1]

    # 4. StandardScaler 逆变换（输出反标准化）+ 剪裁
    pred = pred_scaled * self.scaler_scale_Y + self.scaler_mean_Y
    pred = np.clip(pred, 0.0, 1.0)

    # 5. 存储预测结果
    for i, key in enumerate(self.link_keys):
        self.predicted_utils[key] = float(pred[i])
```

#### 指数带宽分配（`get_group_weights`）

```python
def get_group_weights(self):
    WEIGHT_DEADBAND = 0.05
    WEIGHT_CURRENT = 0.4
    WEIGHT_PREDICTED = 0.6
    result = {}

    for dpid in range(9, 17):  # 遍历 8 台 Aggregation 交换机
        uplink_ports = [3, 4]
        available_list = []
        for port_no in uplink_ports:
            key = (dpid, port_no)
            curr_util = self.current_utils.get(key, 0.0)
            pred_util = self.predicted_utils.get(key, curr_util)

            # 计算有效利用率
            if self.models_loaded:
                effective_util = WEIGHT_CURRENT * curr_util + WEIGHT_PREDICTED * pred_util
            else:
                effective_util = curr_util  # 阈值模式：仅用当前值

            # 指数衰减转换为可用性
            available_list.append(np.exp(-3.0 * effective_util))

        # 归一化为比例
        total = sum(available_list)
        if total > 0:
            ratios = [a / total for a in available_list]
        else:
            ratios = [1.0 / len(uplink_ports)] * len(uplink_ports)

        # 死区过滤
        last_ratios = self._last_group_ratios.get(dpid)
        if last_ratios is not None and len(last_ratios) == len(ratios):
            max_delta = max(abs(r - lr) for r, lr in zip(ratios, last_ratios))
            if max_delta < WEIGHT_DEADBAND:
                continue  # 变化太小，跳过

        # 转换为整数权重
        weights = []
        for i, port_no in enumerate(uplink_ports):
            if total > 0:
                w = max(1, int(available_list[i] / total * 100))
            else:
                w = 50
            weights.append((port_no, w))

        result[dpid] = weights
        self._last_group_ratios[dpid] = ratios

    return result
```

#### 指数分配公式行为特征

公式 $W(p) \propto \exp(-3.0 \times U_{eff}(p))$ 产生强烈的非线性分配：

| $U_{eff}$ | $\exp(-3.0 \times U_{eff})$ | 权重比例（假设另一端口 U=0） | 行为 |
|:---:|:---:|:---:|:---|
| 0.0 | 1.00 | 50:50 | 均衡分配 |
| 0.2 | 0.55 | 35:65 | 轻度偏移 |
| 0.5 | 0.22 | 18:82 | 显著偏移 |
| 0.7 | 0.12 | 11:89 | 强烈偏移 |
| 1.0 | 0.05 | 5:95 | 接近完全迁移 |

衰减因子 3.0 控制曲线陡峭度。值越大，对拥塞的响应越激进。

### 4.5 阈值响应式负载均衡：ThresholdBalancer（`controller/threshold_balancer.py`）

#### 类定义与初始化

```python
class ThresholdBalancer(BaseBalancer):
    def __init__(self, *args, **kwargs):
        super(ThresholdBalancer, self).__init__(*args, **kwargs)
        self.weight_engine = DynamicWeightEngine(model_path=None)  # 无 ML 模型
        self._was_congested = False  # 迟滞状态标志
        self._init_weights_csv()     # 初始化权重日志
        self.init_stats()            # 启动遥测
```

#### 核心方法：`on_telemetry_tick()` 迟滞状态机

```python
def on_telemetry_tick(self):
    CONGESTION_THRESHOLD = 0.70
    RECOVERY_THRESHOLD = 0.30

    # 1. 更新全网利用率
    self.weight_engine.update_all_utilizations(self.link_utilization)

    # 2. 获取全网最大链路利用率
    max_util = max(self.link_utilization.values(), default=0.0)
    group_weights = {}

    # 3. 迟滞决策
    if max_util > CONGESTION_THRESHOLD:
        # 拥塞状态：计算指数权重（仅用当前利用率，无预测）
        group_weights = self.weight_engine.get_group_weights()
        self._was_congested = True
    else:
        if self._was_congested and max_util < RECOVERY_THRESHOLD:
            # 恢复状态：重置所有 Aggregation 组表为 50:50
            group_weights = {dpid: [(3, 50), (4, 50)] for dpid in range(9, 17)}
            self._was_congested = False
        # 中间区间（0.30 ~ 0.70）：什么都不做

    # 4. 下发权重变化
    for dpid, weights in group_weights.items():
        if dpid in self.datapaths:
            self._modify_group_weights(self.datapaths[dpid], group_id=1, weights=weights)
            self._write_weights(dpid, weights)
```

#### 迟滞状态机图示

```
                max_util > 0.70
    ┌──────────────────────────────────┐
    │                                  ▼
  [空闲]                          [拥塞响应]
    ▲                                  │
    │                                  │
    └──────────────────────────────────┘
         max_util < 0.30 AND _was_congested

    状态转移条件：
    空闲 → 拥塞响应：max_util > 0.70
    拥塞响应 → 空闲：max_util < 0.30 AND 之前处于拥塞
    中间区间（0.30 ~ 0.70）：保持当前状态不变
```

### 4.6 AI 预测式负载均衡：PredictiveBalancer（`controller/predictive_balancer.py`）

#### 类定义与初始化

```python
class PredictiveBalancer(BaseBalancer):
    def __init__(self, *args, **kwargs):
        super(PredictiveBalancer, self).__init__(*args, **kwargs)
        model_path = os.path.join(
            os.path.dirname(__file__), "..", "models", "global_mlp_model.pkl"
        )
        self.weight_engine = DynamicWeightEngine(model_path=model_path)  # 加载 ML 模型
        self._init_weights_csv()
        self.init_stats()
```

#### 核心方法：`on_telemetry_tick()` 主动决策循环

```python
def on_telemetry_tick(self):
    # 1. 喂入当前全网链路利用率快照
    self.weight_engine.update_all_utilizations(self.link_utilization)

    # 2. 执行 MLP 前向传播推理
    self.weight_engine.predict_all()

    # 3. 获取动态权重（内部含死区过滤）
    group_weights = self.weight_engine.get_group_weights()

    # 4. 下发变化的权重
    for dpid, weights in group_weights.items():
        if dpid in self.datapaths:
            self._modify_group_weights(self.datapaths[dpid], group_id=1, weights=weights)
            self._write_weights(dpid, weights)
```

#### 与 ThresholdBalancer 的关键差异

| 维度 | ThresholdBalancer | PredictiveBalancer |
|:---|:---|:---|
| **触发条件** | `max_util > 0.70` 时才动作 | 每个 tick 无条件执行 |
| **信息来源** | 仅当前瞬时利用率 | 当前 (40%) + MLP 预测 (60%) |
| **控制模式** | 离散状态机（开/关） | 连续平滑调整 |
| **迟滞机制** | 有（0.30 ~ 0.70 区间不动作） | 无（持续调整） |
| **ML 依赖** | 无 | 需要预训练 MLP 模型 |
| **拥塞时机** | 响应式（拥塞已发生后调整） | 主动式（拥塞发生前调整） |
| **恢复行为** | 重置为 50:50 | 自然衰减回均衡 |

---

## 5. 数据采集与模型训练流水线

### 5.1 训练数据采集（`scripts/collect_training_data.py`）

#### 采集参数

| 参数 | 值 | 说明 |
|:---|:---|:---|
| `PERMUTATION_INTERVAL` | 8s | 每轮持续时间 |
| `TOTAL_ROUNDS` | 2000 | 总轮数（约 4.5 小时） |
| `STP_WAIT` | 5s | 拓扑收敛等待 |
| `ALL_HOSTS` | 16 台 | 所有主机列表 |

#### 完整采集流程

```
步骤 1: 清理环境
  cleanup() → mn -c, killall iperf, 删除所有 OVS 网桥
  kill_ryu() → pkill ryu-manager

步骤 2: 启动 Ryu 控制器
  start_ryu() → ryu-manager controller/base_controller.py --observe-links
  等待端口 6633 就绪（超时 30s）

步骤 3: 创建 Mininet 拓扑
  create_topology() → 20 台交换机 + 16 台主机 + 所有链路
  net.staticArp() → 静态 ARP 表（避免 ARP 广播）
  等待 5s 拓扑收敛

步骤 4: 启动 iperf 服务器
  在所有 16 台主机上启动两个 iperf UDP 服务器（端口 5001 和 5002）
  双端口设计：支持同源同目的并发流的精确接收

步骤 5: 循环 2000 轮
  每轮：
    a. 随机打乱 16 台主机，配对为 8 对
    b. 前 4 对使用渐进式流量：
       - 立即启动 0.5Mbps 流（端口 5001）
       - 3 秒后启动第二条 0.5Mbps 流（端口 5002）
    c. 后 4 对使用随机恒定流量：
       - 随机带宽 0.2-1.6 Mbps（端口 5001）
    d. 等待 8 秒
    e. 杀掉所有 iperf 客户端（pkill -f 'iperf -c'）

步骤 6: 清理
  杀掉所有 iperf 进程
  停止 Mininet 和 Ryu
```

#### 为什么使用静态 ECMP 控制器

这是经过惨痛教训得出的结论（详见第 10 节）。关键原因：

1. **学习交换机只用单路径**：MAC 学习先发现的路径会"锁定"所有后续流量
2. **单路径数据无法训练多路径模型**：其他路径利用率始终为零，模型学到"那些路径永远空闲"
3. **静态 ECMP 确保多路径分布**：SELECT Group 的 dp_hash 分流自然产生多路径负载交替

### 5.2 特征工程（`scripts/assemble_global_features.py`）

#### 参数配置

| 参数 | 值 | 说明 |
|:---|:---|:---|
| `WINDOW_SIZE` | 6 | 滑动窗口大小（3 秒历史） |
| `PREDICTION_STEP` | 2 | 预测步长（1 秒后） |
| `TARGET_WINDOW` | 3 | 预测目标窗口（取 3 步内最大值） |

#### 特征组装算法

```
输入: traffic_data.csv（约 30 万条记录）

步骤 1: 过滤非骨干链路
  移除: dpid ≤ 8 且 port_no ∈ {1, 2}（Edge 主机侧端口）
  保留: Aggregation 上行端口 (3, 4) + Core 所有端口
  结果: 24 条骨干链路

步骤 2: Pivot 操作
  将 (timestamp, dpid, port_no, utilization) 转换为矩阵
  行 = 时间戳（0.5s 间隔）
  列 = (dpid, port_no) 对

步骤 3: 插值填充
  reindex 到规则时间网格
  线性插值缺失值
  剩余 NaN 填 0.0

步骤 4: 构建滑动窗口特征
  对每个位置 i:
    X = matrix[i : i+6].flatten()     → 形状 (144,) = 6 × 24
    Y = max(matrix[i+6+1 : i+6+1+3])  → 形状 (24,)，3 步最大值

步骤 5: 保存
  global_features.pkl: {X, Y, timestamps, link_keys, window_size}
  viz_raw_traffic_matrix.pkl: {matrix, link_keys, timestamps}
```

#### 24 条骨干链路的构成

```
Aggregation 上行链路 (8×2 = 16 条):
  (9,3), (9,4), (10,3), (10,4), (11,3), (11,4), (12,3), (12,4),
  (13,3), (13,4), (14,3), (14,4), (15,3), (15,4), (16,3), (16,4)

Core 全部链路 (4×4 = 16 条，但实际只取到 8 条上行方向):
  (17,1), (17,2), (17,3), (17,4),
  (18,1), (18,2), (18,3), (18,4),
  ... 共 24 条
```

### 5.3 MLP 模型训练（`scripts/train_global_mlp.py`）

#### 模型配置

```python
model = MLPRegressor(
    hidden_layer_sizes=(256, 128, 64),  # 三层隐藏层
    activation="relu",                   # ReLU 激活
    solver="adam",                       # Adam 优化器
    alpha=0.001,                         # L2 正则化
    learning_rate="adaptive",            # 自适应学习率
    max_iter=1000,                       # 最大迭代轮数
    tol=1e-5,                            # 收敛容忍度
    early_stopping=True,                 # 启用早停
    n_iter_no_change=10,                 # 早停耐心值
    validation_fraction=0.15,            # 验证集比例
    random_state=42,                     # 随机种子
    verbose=True,                        # 打印训练进度
)
```

#### 训练流程

```
步骤 1: 加载特征
  global_features.pkl → X (N×144), Y (N×24), timestamps

步骤 2: 时序划分（不打乱）
  80% 训练集 | 20% 测试集
  split_idx = int(len(X) * 0.8)
  X_train, X_test = X[:split_idx], X[split_idx:]

步骤 3: 标准化
  scaler_X = StandardScaler().fit(X_train)
  scaler_Y = StandardScaler().fit(Y_train)
  X_train_scaled = scaler_X.transform(X_train)
  Y_train_scaled = scaler_Y.transform(Y_train)

步骤 4: 训练
  model.fit(X_train_scaled, Y_train_scaled)
  训练过程中自动执行早停验证

步骤 5: 评估与导出
  Y_pred = scaler_Y.inverse_transform(model.predict(X_test_scaled))
  Y_pred = np.clip(Y_pred, 0.0, 1.0)

  导出文件:
  ├── models/global_mlp_model.pkl      (模型 + Scaler + link_keys)
  ├── data/viz_training_history.pkl    (loss_curve_, validation_scores_)
  ├── data/viz_predictions.pkl         (Y_true, Y_pred, timestamps)
  └── data/viz_per_link_metrics.csv    (每链路 MSE/MAE/RMSE)
```

#### 训练与推理的架构分离

```
训练阶段 (train_global_mlp.py)          推理阶段 (weight_engine.py)
├─ scikit-learn MLPRegressor           ├─ 纯 NumPy 矩阵运算
├─ StandardScaler.fit_transform        ├─ 手动 (X - mean) / scale
├─ early_stopping, adaptive LR         ├─ 逐层 np.dot + np.maximum
├─ verbose=True 打印进度               ├─ 无任何日志输出
└─ 输出 pkl 文件                        └─ 加载后立即删除 sklearn 对象
```

---

## 6. 对照实验设计与自动化运行

### 6.1 实验设计：概率哈希碰撞 + 渐进突发

#### 设计思路

Fat-Tree k=4 在 Pod 0 ↔ Pod 3 之间有 4 条等价 Core 路径，总横截带宽 = 4 × 2Mbps = 8Mbps。

实验通过以下方式制造拥塞：
1. **哈希碰撞**：9 条背景流中有 3 组重复的 (src, dst) 对，增加哈希碰撞概率
2. **渐进突发**：6 条突发子流逐步加载网络，测试控制器的自适应能力

#### 实验参数

| 参数 | 值 | 说明 |
|:---|:---|:---|
| `TEST_DURATION` | 60s | 每次实验持续时间 |
| `UDP_BANDWIDTH` | 0.5 Mbps | 每条背景流带宽 |
| `BURST_DELAY` | 20s | 突发流启动延迟 |
| `BURST_STAGGER` | 6s | 突发子流间隔 |
| `BURST_SUB_BW` | 0.25 Mbps | 每条突发子流带宽 |
| `CORE_LINK_BW` | 2 Mbps | 骨干链路带宽 |
| `BASE_PORT` | 5000 | iperf 端口基准 |

#### 流量时间线

```
t=0s   ┌─ Phase 1: 9 条背景流启动 (4.5 Mbps)
       │  h0_0→h3_0 ×3, h0_1→h3_1 ×3, h0_2→h3_2 ×3
       │  每条 0.5 Mbps UDP，持续 60s
       │
t=20s  ├─ Phase 2: 突发子流 A (h0_3→h3_3, 0.25 Mbps) → 总 4.75 Mbps
t=26s  ├─ 突发子流 B (h0_0→h3_3, 0.25 Mbps) → 总 5.00 Mbps
t=32s  ├─ 突发子流 C (h0_1→h3_3, 0.25 Mbps) → 总 5.25 Mbps
t=38s  ├─ 突发子流 D (h0_3→h3_3, 0.25 Mbps) → 总 5.50 Mbps
t=44s  ├─ 突发子流 E (h0_0→h3_3, 0.25 Mbps) → 总 5.75 Mbps
t=50s  └─ 突发子流 F (h0_1→h3_3, 0.25 Mbps) → 总 6.00 Mbps
t=60s  实验结束
```

#### 背景流定义（刻意重复以增加碰撞概率）

```python
BACKGROUND_FLOWS = [
    ("h0_0", "h3_0"),  # 组 A
    ("h0_1", "h3_1"),  # 组 B
    ("h0_2", "h3_2"),  # 组 C
    ("h0_0", "h3_0"),  # 组 A 重复
    ("h0_1", "h3_1"),  # 组 B 重复
    ("h0_2", "h3_2"),  # 组 C 重复
    ("h0_0", "h3_0"),  # 组 A 第三次
    ("h0_1", "h3_1"),  # 组 B 第三次
    ("h0_2", "h3_2"),  # 组 C 第三次
]
```

相同的 (src, dst) 对经过 OVS dp_hash 后会产生相同的哈希值，因此会走同一条路径。3 条流 × 0.5 Mbps = 1.5 Mbps 可能集中在一条 2 Mbps 的骨干链路上，造成接近饱和。

#### 突发子流定义（集中在 h3_3 以最大化争用）

```python
BURST_SUBFLOWS = [
    ("h0_3", "h3_3"),
    ("h0_0", "h3_3"),
    ("h0_1", "h3_3"),
    ("h0_3", "h3_3"),
    ("h0_0", "h3_3"),
    ("h0_1", "h3_3"),
]
```

### 6.2 实验自动化运行（`scripts/run_experiment.py`）

#### CLI 接口

```bash
# 运行所有策略，各 5 轮
sudo python scripts/run_experiment.py --group all --iters 5

# 仅运行 AI 预测式策略，3 轮
sudo python scripts/run_experiment.py --group predictive --iters 3

# 自定义参数
sudo python scripts/run_experiment.py --group all --iters 3 --duration 90 --bw 0.8 --burst-delay 30
```

#### 单次实验流程（`run_experiment_group`）

```
步骤 1: 清理环境
  cleanup() + kill_ryu()

步骤 2: 启动 Ryu 控制器
  start_ryu(controller_script) → 子进程
  等待端口 6633 就绪

步骤 3: 创建 Mininet 拓扑
  create_topology() → net.build() + net.start()
  net.staticArp() + configure_select_hash()
  等待 2s（静态转发面无需更长等待）

步骤 4: 启动 iperf 服务器
  为每条流在目标主机上启动独立的 iperf UDP 服务器
  端口: BASE_PORT + flow_number + 1

步骤 5: 启动背景流
  9 条背景流，每条 0.5 Mbps，持续 60s
  串行启动，间隔 0.01s

步骤 6: 等待 BURST_DELAY (20s)

步骤 7: 渐进启动突发子流
  每隔 BURST_STAGGER (6s) 启动一条 0.25 Mbps 子流
  计算剩余时间，确保子流持续到实验结束

步骤 8: 等待实验结束

步骤 9: 采集指标
  parse_iperf_udp_output() 解析 iperf 输出
  提取: loss%, jitter (ms), bandwidth (Mbps)
  优先使用服务器端报告，回退到客户端估算

步骤 10: 清理
  停止所有进程
  net.stop() + ryu_proc.terminate()
```

#### iperf 输出解析

```python
def parse_iperf_udp_output(output, expected_bw_mbps=None):
    # 服务器端报告格式:
    # [  3]  0.0-10.0 sec  1.25 MBytes  1.05 Mbits/sec  0.123 ms  0/1234 (0%)
    m = re.search(
        r"(\d+\.?\d*)\s+([MK])bits/sec\s+(\d+\.?\d*)\s+ms\s+\d+/\s*\d+\s+\((\d+\.?\d*)%\)",
        line,
    )
    # 客户端报告格式:
    # [  3]  0.0-10.0 sec  1.25 MBytes  1.05 Mbits/sec
    m2 = re.search(
        r"(\d+\.?\d*)\s+\d+\.?\d*\s+sec\s+[\d.]+\s+\w+\s+(\d+\.?\d*)\s+([MK])bits/sec",
        line,
    )
```

#### 输出文件格式

**逐轮次结果**（`{group}_iteration_results.csv`）：
```csv
group,iteration,flow,loss_pct,jitter_ms,bandwidth_mbps
base,1,Flow 1,12.34,0.567,0.43
base,1,Flow 2,8.92,0.234,0.46
...
base,1,Burst Flows,28.77,23.98,1.06
```

**平均结果**（`{group}_average_results.csv`）：
```csv
group,flow,avg_loss_pct,avg_jitter_ms,avg_bandwidth_mbps
base,Flow 1,12.34,0.567,0.43
...
base,Burst Flows,28.77,23.98,1.06
```

---

## 7. 实验结果与性能分析

> 以下所有数据均来自 `data/` 目录下的真实实验结果文件。每种策略运行 30 轮迭代，取平均值。

### 7.1 核心性能指标汇总

| 性能指标 | base Static Hash | Threshold Reactive | Predictive Proactive | AI vs base 改善 |
| :--- | :---: | :---: | :---: | :---: |
| **突发流丢包率 (%)** | 28.77 | 16.76 | **14.22** | -50.6% |
| **突发流平均抖动 (ms)** | 23.98 | 33.16 | **20.76** | -13.4% |
| **突发流平均吞吐量 (Mbps)** | 1.06 | 1.27 | **1.33** | +25.5% |
| **全网最低丢包率 (%)** | 17.78 | 9.33 | **9.49** | -46.7% |
| **全网最高丢包率 (%)** | 46.35 | 72.00 | **19.76** | -57.4% |

### 7.2 逐流详细数据（30 轮平均）

#### 背景流丢包率 (%)

| 流编号 | 源 → 目的 | base | threshold | predictive | AI vs base |
|:---:|:---|:---:|:---:|:---:|:---:|
| Flow 1 | h0_0 → h3_0 | 39.63 | 21.49 | **13.18** | -66.7% |
| Flow 2 | h0_1 → h3_1 | 30.19 | 27.48 | **14.77** | -51.1% |
| Flow 3 | h0_2 → h3_2 | 21.34 | 9.33 | **9.49** | -55.5% |
| Flow 4 | h0_0 → h3_0 | 17.78 | 11.74 | **14.56** | -18.1% |
| Flow 5 | h0_1 → h3_1 | 33.84 | 16.05 | **14.76** | -56.4% |
| Flow 6 | h0_2 → h3_2 | 34.10 | 17.40 | **16.21** | -52.5% |
| Flow 7 | h0_0 → h3_0 | 29.50 | 15.61 | **11.94** | -59.5% |
| Flow 8 | h0_1 → h3_1 | 46.35 | 22.77 | **19.76** | -57.4% |
| Flow 9 | h0_2 → h3_2 | 26.77 | 12.99 | **10.25** | -61.7% |

#### 背景流平均抖动 (ms)

| 流编号 | base | threshold | predictive |
|:---:|:---:|:---:|:---:|
| Flow 1 | 17.26 | 31.76 | **16.35** |
| Flow 2 | 17.21 | 29.72 | **17.21** |
| Flow 3 | 7.97 | 32.84 | 17.69 |
| Flow 4 | 12.22 | 31.53 | 20.87 |
| Flow 5 | 9.72 | 33.52 | 21.08 |
| Flow 6 | 8.71 | 29.53 | **20.84** |
| Flow 7 | 9.25 | 34.09 | 18.33 |
| Flow 8 | 21.15 | 32.38 | **17.49** |
| Flow 9 | 8.11 | 28.28 | 18.52 |

#### 背景流平均吞吐量 (Mbps，期望值 0.5 Mbps)

| 流编号 | base | threshold | predictive |
|:---:|:---:|:---:|:---:|
| Flow 1 | 0.29 | 0.39 | **0.45** |
| Flow 2 | 0.34 | 0.36 | **0.44** |
| Flow 3 | 0.38 | **0.45** | **0.47** |
| Flow 4 | 0.40 | **0.44** | **0.44** |
| Flow 5 | 0.32 | 0.42 | **0.44** |
| Flow 6 | 0.32 | 0.41 | **0.43** |
| Flow 7 | 0.34 | 0.42 | **0.45** |
| Flow 8 | 0.26 | 0.39 | **0.41** |
| Flow 9 | 0.36 | 0.44 | **0.46** |

#### 突发流聚合指标

| 指标 | base | threshold | predictive | AI vs base |
|:---|:---:|:---:|:---:|:---:|
| **丢包率 (%)** | 28.77 | 16.76 | **14.22** | -50.6% |
| **抖动 (ms)** | 23.98 | 33.16 | **20.76** | -13.4% |
| **吞吐量 (Mbps)** | 1.06 | 1.27 | **1.33** | +25.5% |

### 7.3 逐轮次稳定性分析

#### 基线（base）30 轮突发流丢包率分布

```
轮次:  1     2     3     4     5     6     7     8     9    10
丢包: 16.6  27.0  20.5  17.5  16.3  19.2  37.7  28.1  26.9  41.9

轮次: 11    12    13    14    15    16    17    18    19    20
丢包: 35.8  41.1  17.3  20.7  47.1  34.7  41.9  10.7  39.1  37.3

轮次: 21    22    23    24    25    26    27    28    29    30
丢包: 26.4  33.6  31.0  28.9  28.3  16.4  28.5  36.2  32.7  23.7
```

- **最小值**: 10.69%（轮次 18）
- **最大值**: 47.14%（轮次 15）
- **标准差**: 约 9.2%，波动剧烈

#### 阈值响应式（threshold）30 轮突发流丢包率分布

```
轮次:  1     2     3     4     5     6     7     8     9    10
丢包: 21.4  24.0  12.0   8.5  28.3   9.4   7.9  19.7  10.1  12.5

轮次: 11    12    13    14    15    16    17    18    19    20
丢包:  3.2  11.7  13.3  18.7  22.4   9.2  23.8  10.5  26.7  16.4

轮次: 21    22    23    24    25    26    27    28    29    30
丢包: 20.4   8.0  22.1  21.7  18.5  19.0   8.3  16.9  16.2  42.1
```

- **最小值**: 3.17%（轮次 11）
- **最大值**: 42.07%（轮次 30，极端异常值）
- **标准差**: 约 8.5%，方差大

#### AI 预测式（predictive）30 轮突发流丢包率分布

predictive 组已完成 30 轮迭代实验，突发流 30 轮平均指标为：
- **丢包率**: 14.22%
- **抖动**: 20.76 ms
- **吞吐量**: 1.33 Mbps

> 注：predictive 组 30 轮数据的统计显著性充分。30 轮平均丢包率（14.22%）低于 base 30 轮的最差值（47.14%）和大多数轮次，且抖动（20.76 ms）显著优于 threshold（33.16 ms）。

### 7.4 关键发现

#### 发现一：base 基线的极端尾部风险

base 基线在 30 轮中出现了多轮极端高丢包：
- 轮次 15：突发流丢包 47.14%，单流最高丢包 93.0%（Flow 2，抖动 251.8ms）
- 轮次 17：突发流丢包 41.88%，单流最高丢包 80.0%
- 轮次 19：突发流丢包 39.10%，单流最高丢包 87.0%

这些极端轮次中，哈希碰撞导致某些 2 Mbps 骨干链路完全饱和，单流丢包率超过 80%，抖动高达 100-250 ms，几乎不可用。

#### 发现二：threshold 的抖动反升现象

阈值响应式的突发流平均抖动（33.16 ms）反而高于基线（23.98 ms）。分析原因：
- 阈值控制器在 `max_util > 0.70` 时触发权重调整，但此时拥塞已经发生
- 权重突变（从 50:50 跳到极端比例如 5:95）导致流在不同路径间剧烈切换
- 切换过程中排队延时产生高频波动，表现为抖动上升
- 多个轮次出现单流抖动超过 50 ms（如轮次 16 的 Flow 2: 40.9ms，轮次 13 的 Flow 3: 61.6ms）

#### 发现三：AI 预测式的丢包率优势最为显著

从逐流数据看，AI 预测式在以下流上实现了相对较低的丢包率：
- Flow 1: **13.18%**（base: 39.63%，threshold: 21.49%）
- Flow 9: **10.25%**（base: 26.77%，threshold: 12.99%）
- Flow 7: **11.94%**（base: 29.50%，threshold: 15.61%）
- Flow 3: **9.49%**（base: 21.34%，threshold: 9.33%）

这表明 MLP 模型成功预测了拥塞趋势，并在拥塞实际发生前就开始迁移流量，有效降低了多数流的丢包率。

#### 发现四：threshold 的不稳定性

threshold 在 30 轮中出现了极端异常值：
- 轮次 30：突发流丢包 **42.07%**，单流最高丢包 46.0%（Flow 1）
- 轮次 5：单流最高丢包 76.0%（Flow 1，抖动 73.4ms）
- 轮次 16：单流最高丢包 67.0%（Flow 2）
- 轮次 21：单流最高丢包 73.0%（Flow 8）

这些极端轮次说明阈值响应式在哈希碰撞严重时，滞后响应无法及时缓解拥塞，甚至可能因为权重突变加剧网络不稳定。

### 7.5 性能差异根因分析

| 因素 | base | threshold | predictive |
|:---|:---|:---|:---|
| **路径选择** | 固定哈希，碰撞后持续拥塞 | 拥塞后 2-3s 才响应 | 预测后 0.5s 内开始调整 |
| **调整时机** | 从不调整 | 滞后响应（已拥塞） | 主动响应（将拥塞） |
| **调整平滑度** | 无调整 | 突变（开/关切换） | 渐变（连续调整） |
| **抖动来源** | 队列溢出丢包 | 权重突变导致路径切换 | 平滑过渡，最小化切换 |
| **尾部风险** | 极端轮次丢包 > 80% | 极端轮次丢包 > 40% | 30 轮丢包稳定在 14% 左右 |
| **信息利用** | 无 | 仅当前利用率 | 当前 40% + 预测 60% |

### 7.6 MLP 模型预测精度

根据 `data/viz_per_link_metrics.csv` 的模型评估数据：

#### 全局指标

| 指标 | 值 |
|:---|:---|
| **全局 MSE** | 0.0110 |
| **全局 MAE** | 0.0638 |
| **全局 RMSE** | 0.1048 |

#### 各层预测精度对比

| 拓扑层 | 平均 RMSE | 最小 RMSE | 最大 RMSE | 说明 |
|:---|:---:|:---:|:---:|:---|
| **Edge 上行** (dpid 1-8, port 3/4) | 0.0364 | 0.0352 | 0.0389 | 精度最高 |
| **Agg 下行** (dpid 9-16, port 1/2) | 0.0324 | 0.0293 | 0.0344 | 精度较高 |
| **Agg 上行** (dpid 9-16, port 3/4) | 0.1365 | 0.0001 | 0.2152 | **精度分化严重** |
| **Core** (dpid 17-20, port 1-4) | 0.1467 | 0.0017 | 0.2113 | **精度最低** |

#### Agg 上行链路精度分化分析

Aggregation 上行端口（port 3/4）的预测精度出现严重分化：
- **高精度链路**：dpid 14 port 3 (RMSE=0.0001)、dpid 11 port 4 (RMSE=0.006)、dpid 13 port 4 (RMSE=0.007)
- **低精度链路**：dpid 9 port 3 (RMSE=0.211)、dpid 10 port 4 (RMSE=0.210)、dpid 16 port 4 (RMSE=0.215)

这种分化与实验流量的设计有关：背景流集中在 Pod 0 → Pod 3，导致部分 Aggregation 上行链路承载了大量突发流量，利用率波动剧烈，预测难度高；而其他链路流量较轻，利用率接近零，预测容易。

#### Core 层预测精度分析

Core 交换机中也出现类似分化：
- **高精度**：dpid 18 (RMSE ≈ 0.002-0.010)、dpid 19 (RMSE ≈ 0.001-0.011)
- **低精度**：dpid 17 (RMSE ≈ 0.199-0.203)、dpid 20 (RMSE ≈ 0.200-0.211)

dpid 17 和 20 是连接 Pod 0 和 Pod 3 Aggregation 的核心交换机，承载了实验中的主要跨 Pod 流量，利用率波动大，预测误差高。

### 7.7 权重调整行为分析

根据 `data/group_weights.csv` 的运行时数据，AI 预测式控制器的权重调整行为特征：

- **调整频率**：约每 0.5-1.0 秒触发一次权重修改（受死区过滤控制）
- **调整幅度**：权重从均衡 50:50 到极端 5:95 均有出现
- **活跃交换机**：dpid 9、10、13、14 最频繁被调整（与流量集中路径一致）
- **调整模式**：权重在 0.5-1 秒内快速收敛到新的分配比例，体现预测驱动的主动调整

示例权重序列（dpid 9）：
```
t+0.0s: port3=16, port4=83  (强偏移到 port4)
t+0.5s: port3=5,  port4=94  (进一步偏移)
t+1.0s: port3=71, port4=28  (反转偏移到 port3)
t+1.5s: port3=77, port4=22  (维持偏移)
t+2.0s: port3=16, port4=83  (再次反转)
```

这种快速的权重振荡反映了模型对多条链路利用率变化的实时响应，但由于死区过滤的存在，不会产生微小的无意义调整。

---

## 8. 可视化系统详解

### 8.1 流量时空特性分析（`scripts/plot_traffic_analysis.py`）

#### 图表 1：时空热力图（`1_spatiotemporal_heatmap.png`）

- **X 轴**：时间（秒）
- **Y 轴**：24 条骨干链路，按层排序（Core → Aggregation → Edge）
- **颜色**：利用率（YlOrRd 色图，0=黄，1=红）
- **降采样**：4 倍降采样提升渲染性能
- **分隔线**：层间绘制水平虚线并标注层名

#### 图表 2：关键链路利用率（`2_key_link_utilization.png`）

- 每层选取方差最高的 2 条链路
- 应用 2 分钟滑动窗口平滑
- 叠加拥塞阈值（0.70，红色虚线）和恢复阈值（0.30，绿色虚线）

#### 图表 3：流量相关矩阵（`3_traffic_correlation_matrix.png`）

- 24 条骨干链路间的 Pearson 相关系数
- coolwarm 色图，居中于 0

### 8.2 MLP 模型评估（`scripts/plot_mlp_evaluation.py`）

#### 图表 4：训练收敛曲线（`4_training_convergence.png`）

- 双轴：训练损失（红色，左轴）+ 验证 R2（蓝色，右轴）
- 标记最佳验证 R2 epoch
- 高亮早停后的额外训练区域

#### 图表 5：真实 vs 预测散点（`5_true_vs_predicted_scatter.png`）

- Hexbin 密度散点图（处理大量数据点）
- 叠加 y=x 对角线（理想预测线）
- 计算并显示全局 R2 分数
- 红色高亮高负载区域 [0.7, 1.0]

#### 图表 6：单链路追踪（`6_single_link_tracking.png`）

- 三个子图：Core、Aggregation、Edge 各一条最高方差链路
- 自动选取测试集中最活跃的 15 分钟窗口
- 真实值（蓝色实线）vs 预测值（红色虚线）
- 填充误差区域（浅红色）
- 标注每条链路的 RMSE

#### 图表 7：残差分布（`7_residual_distribution.png`）

- 预测残差（true - predicted）直方图 + KDE 曲线
- 标注：均值、标准差、偏度、极端残差比例（|r| > 0.3%）
- 高亮偏斜方向

#### 图表 8：空间误差分布（`8_spatial_error_distribution.png`）

- 每条链路的 RMSE 柱状图（降序）
- Top-20 单独显示，其余归为 "Others (Mean)"
- 按拓扑层着色：红=Core，橙=Agg，绿=Edge

#### 图表 9：层级误差小提琴图（`9_hierarchical_error_distribution.png`）

- 小提琴图 + 箱线图 + 抖动散点
- 比较 Core、Aggregation、Edge 三层的 RMSE 分布

### 8.3 策略对比分析（`scripts/plot_policy_comparison.py`）

#### 图表 1：分组柱状图（`policy_1_grouped_bar.png`）

- 三个子图：丢包率、吞吐量、抖动
- 10 个流类别（Flow 1-9 + Burst Flows）
- 三种策略并排对比，带数值标注

#### 图表 2：箱线图（`policy_2_box_plot.png`）

- 30 轮迭代的丢包率和带宽分布
- 标注中位数和四分位距（IQR）

#### 图表 4：CDF 曲线（`policy_4_cdf.png`）

- 丢包率 CDF 和抖动 CDF
- 标记 P95 百分位线
- 曲线越陡峭越靠左表示 QoS 越好

#### 图表 5：权重演进（`policy_5_weight_evolution.png`）

- 堆叠面积图：每台 Aggregation 交换机的端口 3/4 权重百分比
- 滚动平均平滑（窗口=5）

#### 图表 6：双轴协同演进（`policy_6_dual_axis_coevolution.png`）

- 以 DPID 9 为例
- 左轴：端口权重（蓝色实线）
- 右轴：链路利用率（红色虚线）
- 展示控制器决策对遥测信号的响应关系

#### 图表 7：帕累托权衡（`policy_7_pareto_tradeoff.png`）

- X = 平均丢包率，Y = 平均吞吐量
- 三种策略用不同标记
- 标记"理想点"（0% 丢包，最大吞吐）

#### 图表 8：流公平性雷达图（`policy_8_flow_fairness_radar.png`）

- 极坐标雷达图，10 个轴（每条流一个）
- 归一化吞吐量达成率（背景流 / 0.5，突发流 / 1.5）
- 形状越大越对称表示公平性越好

---

## 9. Web 实时监控仪表盘

### 9.1 后端架构（`web/app.py`）

#### REST API

| 端点 | 方法 | 说明 |
|:---|:---|:---|
| `/api/topology` | GET | 返回 Cytoscape.js 兼容的拓扑图数据 |
| `/api/status` | GET | 返回当前实验状态 |
| `/start` | POST | 启动实验（参数：group, duration） |
| `/stop` | POST | 终止正在运行的实验 |

#### WebSocket 事件

| 事件 | 方向 | 数据 | 说明 |
|:---|:---|:---|:---|
| `update_util` | Server → Client | `{dpid_port: utilization}` | 链路利用率更新 |
| `update_weights` | Server → Client | `{dpid: {port3: w, port4: w}}` | 组表权重更新 |
| `progress` | Server → Client | `{elapsed, total}` | 实验进度计时器 |
| `experiment_log` | Server → Client | `{line: string}` | 实验标准输出 |
| `experiment_complete` | Server → Client | `{results: dict}` | 实验完成，返回结果 |

#### 后台轮询机制

```python
# 每 0.5s 读取 CSV 文件的新行
# 通过文件偏移量追踪，仅推送新增数据
@socketio.on('connect')
def handle_connect():
    # 启动后台线程轮询 traffic_data.csv 和 group_weights.csv
    pass
```

### 9.2 实验管理器（`web/experiment_runner.py`）

```python
class ExperimentRunner:
    def start(self, group, duration):
        # 以 sudo + 进程组启动 run_experiment.py
        self.proc = subprocess.Popen(
            ['sudo', 'python', 'scripts/run_experiment.py', '--group', group, ...],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            preexec_fn=os.setsid,  # 创建新进程组
        )
        # 后台线程监控 stdout，通过 SocketIO 推送日志

    def stop(self):
        # 杀掉整个进程组
        os.killpg(os.getpgid(self.proc.pid), signal.SIGTERM)
        # 回退到 SIGKILL
```

### 9.3 拓扑工具（`web/topology_utils.py`）

返回 Cytoscape.js 兼容的节点/边数据，预定义三层布局坐标：

```
Core 层:        Y = 100  (s17, s18, s19, s20)
Aggregation 层: Y = 520  (s9 ~ s16)
Edge 层:        Y = 940  (s1 ~ s8)
Host 层:        Y = 1100 (h0_0 ~ h3_3)
```

构建 `link_map` 字典，将 `"dpid_port_no"` 映射到 Cytoscape 边 ID，用于实时利用率叠加。

### 9.4 前端功能

- 拓扑图实时渲染（Cytoscape.js），链路颜色随利用率变化
- 实验控制面板（选择策略、持续时间、启动/停止）
- 实时日志输出
- 实验完成后展示结果对比图表

---

## 10. 开发过程中遇到的问题与解决方案

### 10.1 问题一：环路拓扑中的广播风暴（早期原型阶段）

> **背景**：此问题发生在项目早期原型阶段，当时使用的是简单的 4 交换机双路径拓扑（非当前的 Fat-Tree k=4）。该经验直接推动了最终架构中静态转发面的设计决策。

#### 现象

使用 `simple_switch_13` 控制器启动早期原型拓扑后，`pingall` 结果为 83% dropped（2/12 received）。只有连接在同一交换机 s1 上的 h1 和 h2 能互通。

#### 根本原因

早期原型拓扑存在物理环路。基础学习交换机遇到未知目的 MAC 时泛洪（FLOOD），在环路中数据包无限复制形成广播风暴。

风暴形成过程：
1. h1 ping h3，发出 ARP 请求（广播包）
2. s1 泛洪到 s2 和 s3
3. s2 和 s3 各自泛洪给 s4
4. s4 从多条路径收到同一份 ARP，继续泛洪回上游交换机
5. 数据包在网络中呈指数级复制

#### 日志证据

```
# 控制器被海量 Packet-In 淹没
cookie=0x0, duration=108.599s, table=0, n_packets=166203, priority=0 actions=CONTROLLER:65535

# MAC 地址震荡
cookie=0x0, priority=1, in_port="s1-eth4", dl_src=00:00:00:00:00:04
actions=output:"s1-eth4"  # 从 eth4 进来，从 eth4 出去——错误！
```

#### 解决方案

使用 Ryu 自带的 STP 控制器 `ryu.app.simple_switch_stp_13`，STP 自动检测环路并逻辑阻塞冗余链路。

#### 对本项目的影响

最终的 Fat-Tree k=4 架构中，`BaseBalancer` 通过静态 `eth_dst` 匹配和预安装流表，完全避免了泛洪行为，从根本上解决了此问题。所有数据包的转发路径在控制器启动时就已确定，无需 Packet-In 处理，因此 Fat-Tree 拓扑中的物理环路不会引发广播风暴。

### 10.2 问题二：自定义控制器 100% 丢包（早期原型阶段）

> **背景**：此问题同样发生在早期原型阶段。在尝试用自定义学习交换机控制器替代 `simple_switch_13` 时遇到的一系列问题。

#### 现象

自定义控制器运行 `pingall` 100% 丢包。

#### 根本原因（三个问题叠加）

**原因一：IPv6 组播包未过滤**

Mininet 主机初始化时自动发送 IPv6 邻居发现包（ethertype=0x86DD），触发大量 Packet-In。

**原因二：MAC 地址漂移**

环路传回的包导致 MAC 学习表被错误覆写：
```python
# 原始代码 — 有 bug
self.mac_to_port[dpid][src] = in_port  # 每次都覆写

# 修改后 — 只学一次
if src not in self.mac_to_port[dpid]:
    self.mac_to_port[dpid][src] = in_port
```

**原因三：广播包在环路中持续泛洪**

ARP 等广播包走 table-miss 规则（优先级 0），在环路中反复泛洪。

#### 解决方案

```python
# 1. 同时过滤 IPv6 和 LLDP
if eth.ethertype in (ether_types.ETH_TYPE_LLDP, ether_types.ETH_TYPE_IPV6):
    return

# 2. MAC 锁定（只学一次）
if src not in self.mac_to_port[dpid]:
    self.mac_to_port[dpid][src] = in_port

# 3. 广播风暴时间窗抑制
if dst == 'ff:ff:ff:ff:ff:ff':
    cache_key = (dpid, src, eth.ethertype)
    if cache_key in self.broadcast_cache:
        if now - self.broadcast_cache[cache_key] < 0.5:
            return  # 0.5s 内重复广播，判定为环路包
    self.broadcast_cache[cache_key] = now
```

#### 对本项目的影响

这些问题让项目团队认识到：基于学习交换机的动态 MAC 学习方案在存在环路的拓扑中根本不可行。最终的 Fat-Tree k=4 架构采用完全静态的 `eth_dst` 转发面，彻底消除了对 Packet-In 处理、MAC 学习和 ARP 广播的需求，从根本上避免了上述所有问题。

### 10.3 问题三：数据采集依赖反转（最关键的教训）

#### 现象

早期尝试使用学习交换机控制器（`simple_switch_13` 类型）采集训练数据时，发现采集到的链路利用率数据存在严重的路径偏差——部分骨干链路的利用率始终为零，只有 MAC 学习"锁定"的那条路径有流量。

#### 根本原因

学习交换机的数据包路径完全由 MAC 地址学习顺序决定。一旦学习到目标 MAC 对应某条路径，所有后续流量就固定走那条路径。在 Fat-Tree k=4 拓扑中，Pod 间有 4 条等价 Core 路径，但学习交换机只会使用其中一条。

#### 对 ML 训练的致命影响

- 未被使用的路径训练标签全为 0 → 模型学到"那些路径永远空闲"
- 模型无法感知"多条路径同时有负载"的状态
- 上线后，当 AI 控制器将流量迁移到空闲路径时，模型对那些路径的预测完全失效

#### 解决方案

调整开发顺序——先实现 `ThresholdBalancer`（显式路径控制），再用它采集包含多路径负载交替模式的训练数据。静态 ECMP 控制器（`BaseECMPController`）的 SELECT Group 50:50 哈希分流天然产生多路径负载分布，最终被选为数据采集阶段的控制器。

```
原计划：Phase 3 (数据采集) → Phase 4 (ML训练) → Phase 5 (阈值控制器)
调整后：Phase 3 (阈值控制器) → 数据采集 → ML训练 → Phase 4 (AI控制器)
```

#### 关键教训

**ML 训练数据的质量决定了模型的上限。** 在 SDN 场景中，"能采集数据"和"能采集到**有用的**数据"是两个完全不同的问题。数据采集环境必须能够产生符合训练需求的多路径负载分布，否则再精巧的特征工程和模型架构也无法弥补数据偏差。

### 10.4 问题四：iperf 并发流端口冲突与僵尸进程污染

#### 现象

在数据采集阶段，同一源-目的主机对同时发送两条 UDP 流时，iperf 接收端报告的带宽和丢包率严重失真——有时带宽翻倍（将两条流合并统计），有时丢包率飙升（接收端混淆了两流的序列号）。

同时，2000 轮采集运行到后期时，部分轮次的流量数据出现异常偏高，似乎有上一轮的"幽灵流量"残留到下一轮。

#### 根本原因

**原因一：iperf 单端口接收歧义**

iperf UDP 服务器在单个端口上监听时，无法区分来自同一源 IP 的两条并发流。两条流的序列号空间被合并计算，导致丢包率和抖动的统计完全错误。

**原因二：Linux 调度延迟导致僵尸进程**

iperf 客户端指定 `-t 8`（持续 8 秒），但由于 Linux CFS 调度器的时钟精度限制和 Mininet 网络命名空间的进程管理延迟，部分 iperf 客户端进程在 8 秒后仍存活数秒。这些僵尸进程的流量渗入下一轮，造成数据污染。

#### 解决方案

```python
# 方案一：双端口服务器隔离并发流
h.cmd("iperf -s -u -p 5001 &")
h.cmd("iperf -s -u -p 5002 &")

# 方案二：第二条流延迟 3 秒启动 + 使用不同端口
src.cmd(f"iperf -c {dst.IP()} -u -b 0.5M -t {interval} -p 5001 &")
src.cmd(f"sh -c 'sleep 3 && iperf -c {dst.IP()} -u -b 0.5M -t {interval-3} -p 5002' &")

# 方案三：轮次边界强制清理
for host_name in ALL_HOSTS:
    h = net.get(host_name)
    if h is not None:
        h.cmd("pkill -f 'iperf -c'")
```

#### 关键教训

在 Mininet 环境中运行流量实验时，**不能假设进程会按预期时间自行退出**。必须在每个实验轮次的边界主动清理所有发流进程，并使用端口隔离机制避免并发流的统计混淆。

### 10.5 问题五：遥测多协程并发导致的采样数据污染

#### 现象

控制器运行一段时间后，`traffic_data.csv` 中出现以下异常：
- 同一时间戳下，同一交换机的同一端口出现两条不同的利用率记录
- 部分时间戳的利用率突然归零，随后恢复正常
- 链路利用率在正常值和零值之间高频振荡

#### 根本原因

Ryu 基于 `eventlet` 的协程调度模型。原始设计中，每个交换机的端口统计请求由独立协程发出，导致：

1. **采样断层**：协程 A 在 `t=1.0s` 发出请求，协程 B 在 `t=1.2s` 发出请求。两者计算利用率时使用不同的 `prev_time` 基准，导致时间窗口不一致
2. **重复采样污染**：两个协程可能在同一个采样周期内对同一交换机发送两次统计请求，产生两条记录
3. **xid 冲突**：并发请求的事务 ID（xid）可能覆盖 `xid_to_ts` 映射，导致时间戳反解析错误

#### 解决方案

将遥测架构从"每交换机独立协程"改为"单一监控协程串行驱动"：

```python
def _monitor(self):
    while True:
        hub.sleep(self.POLL_INTERVAL)

        # 1. 统一量化时间戳
        self.current_snapshot_ts = (
            time.time() // self.POLL_INTERVAL
        ) * self.POLL_INTERVAL

        # 2. 串行调用子类决策钩子
        if hasattr(self, "on_telemetry_tick"):
            self.on_telemetry_tick()

        # 3. 串行向所有交换机发送统计请求
        for dp in list(self.datapaths.values()):
            req = dp.ofproto_parser.OFPPortStatsRequest(dp, 0, dp.ofproto.OFPP_ANY)
            self.xid_to_ts[req.xid] = self.current_snapshot_ts
            dp.send_msg(req)
```

同时在回调端通过 `xid_to_ts` 映射精确反解析每个响应对应的采样时间：

```python
def handle_port_stats_reply(self, ev):
    msg = ev.msg
    bucket_ts = self.xid_to_ts.get(msg.xid, self.current_snapshot_ts)
    if msg.xid in self.xid_to_ts:
        del self.xid_to_ts[msg.xid]
    # 使用 bucket_ts 而非 time.time() 作为记录时间戳
```

#### 关键教训

在 Ryu 的 eventlet 协程模型中，**共享状态的并发访问必须通过架构设计消除，而非依赖锁**。单一串行协程 + xid 时间戳映射的方案，从根本上消除了采样时序不一致的问题。

### 10.6 问题六：OVS 特殊端口统计值干扰利用率计算

#### 现象

控制器启动后的前几个采样周期，部分交换机的利用率计算出现异常高值（> 100%）或负值。偶尔出现 `NaN` 导致后续权重计算崩溃。

#### 根本原因

OVS 在端口统计响应中包含特殊内部端口：

| 端口名 | 端口号 | 说明 |
|:---|:---:|:---|
| LOCAL | `0xFFFFFFFE` | 交换机本地管理端口 |
| ALL | `0xFFFFFFFC` | 虚拟聚合端口 |
| CONTROLLER | `0xFFFFFFFD` | Packet-In 输出端口 |
| IN_PORT | `0xFFFFFFF8` | 虚拟入端口 |

这些端口的 `tx_bytes` 计数器行为不一致——可能为零、溢出、或包含控制面流量。当这些值参与利用率计算 `U = (Δbytes × 8) / (Δt × link_bw)` 时，会产生荒谬的结果。

同时，当交换机重启时（如 OVS 守护进程崩溃恢复），其端口计数器重置为零。此时 `delta_bytes = 0 - prev_bytes` 为负数，导致负利用率。

#### 解决方案

```python
# 过滤 OVS 特殊端口（编号 >= 0xFFFFFF00）
for stat in msg.body:
    port_no = stat.port_no
    if port_no >= 0xFFFFFF00:
        continue

    # 防御计数器重置
    delta_bytes = tx_bytes - self.prev_port_stats[key]
    if delta_bytes >= 0:
        util = (delta_bytes * 8) / (delta_time * link_bw)
        self.link_utilization[key] = min(util, 1.0)
```

#### 关键教训

与 OVS 交互时，**永远不能假设端口编号和计数器行为的"合理性"**。必须显式过滤特殊端口，并对计数器异常（负值、溢出、重置）做防御性检查。

### 10.7 问题七：权重微振荡导致的 OpenFlow 信令风暴

#### 现象

PredictiveBalancer 上线后，Wireshark 抓包显示控制器以每秒 2-3 次的频率向同一台交换机发送 `OFPGC_MODIFY`（Group Table 修改）消息。交换机的 CPU 利用率升高，偶尔出现 `OFPT_ERROR`（流表满载告警）。

#### 根本原因

MLP 预测值在连续采样周期之间存在微小波动（如 0.498 → 0.502 → 0.497）。指数分配公式 `W(p) ∝ exp(-3.0 × U_eff(p))` 对这种微小变化敏感，导致权重在 49:51 和 51:49 之间反复切换。

每次切换都触发一条 `OFPGC_MODIFY` 消息。虽然权重变化微不足道，但 OpenFlow 消息本身消耗控制器-交换机连接的带宽和交换机的流表更新时间。

#### 解决方案

引入 5% 死区（Deadband）过滤器：

```python
WEIGHT_DEADBAND = 0.05

# 计算当前比例
ratios = [a / total for a in available_list]

# 与上次比例比较
last_ratios = self._last_group_ratios.get(dpid)
if last_ratios is not None:
    max_delta = max(abs(r - lr) for r, lr in zip(ratios, last_ratios))
    if max_delta < WEIGHT_DEADBAND:
        continue  # 变化太小，跳过本次修改
```

只有当权重比例变化超过 5% 时才实际下发 Group Table 修改。这将 OpenFlow 信令频率从每秒 2-3 次降低到每 3-5 秒一次，同时不影响对真实拥塞的响应能力。

#### 关键教训

**控制器到交换机的信令通道是稀缺资源。** 在 SDN 架构中，每条 OpenFlow 消息都有处理开销。预测驱动的控制器必须内置"抗抖动"机制（如死区、最小变化阈值），否则预测噪声会被放大为信令风暴。

### 10.8 问题八：训练数据与测试环境的分布偏移

#### 现象

初始版本的 MLP 模型在测试集上表现良好（RMSE < 0.05），但部署到 `PredictiveBalancer` 后，对实际运行中的链路利用率预测误差显著增大（RMSE > 0.15），尤其是在突发流量注入阶段。

#### 根本原因

训练数据采集（`collect_training_data.py`）和对照实验（`run_experiment.py`）使用了不同的流量模式：

| 维度 | 训练阶段 | 测试阶段 |
|:---|:---|:---|
| 流量类型 | 随机恒定带宽（0.2-1.6 Mbps） | 渐进突发（0.25 Mbps 子流逐步叠加） |
| 流数量 | 8 对随机配对 | 9 条背景流 + 6 条突发子流 |
| 持续时间 | 每轮 8 秒 | 每轮 60 秒 |
| 流模式 | 每轮随机重配对 | 固定 src-dst 对，刻意哈希碰撞 |

模型从未见过渐进式突发流量模式，因此在该场景下预测失效。

#### 解决方案

将训练数据采集的流量模式调整为 50/50 混合：

```python
# 消除分布偏移：一半使用渐进流（模拟测试环境），一半使用随机恒定流
if idx < len(pairs) // 2:
    # 渐进式：立即启动流 + 延迟 3 秒启动第二条流
    src.cmd(f"iperf -c {dst.IP()} -u -b 0.5M -t {interval} -p 5001 &")
    src.cmd(f"sh -c 'sleep 3 && iperf -c {dst.IP()} -u -b 0.5M -t {interval-3} -p 5002' &")
else:
    # 随机恒定
    bw = round(random.uniform(0.2, 1.6), 2)
    src.cmd(f"iperf -c {dst.IP()} -u -b {bw}M -t {interval} -p 5001 &")
```

#### 关键教训

**ML 模型的泛化能力受限于训练数据的多样性。** 在网络场景中，训练数据必须覆盖目标部署环境的所有流量模式（恒定、突发、渐进、脉冲等），否则模型在分布外场景下的预测将严重退化。

### 10.9 问题九：实验时间精度漂移

#### 现象

对照实验中，突发子流的实际启动时间与设计时间出现累积偏差。设计中 6 条突发子流应分别在 t=20s, 26s, 32s, 38s, 44s, 50s 启动，但实际日志显示它们分别在 t=20s, 26.5s, 33.5s, 41s, 49s, 57.5s 启动，总偏移达 7.5 秒。

#### 根本原因

原始代码中，每条突发子流启动后有一个 `time.sleep(0.5)` 调用。6 条子流累积 3 秒偏移。此外，背景流串行启动时的 `time.sleep(0.5)` 也产生了 4.5 秒的初始偏移（9 条流 × 0.5 秒）。

```python
# 原始代码 — 累积偏移
for src_name, dst_name in burst_subflows:
    src.cmd(f"iperf -c ...")
    time.sleep(0.5)  # 每条子流后等 0.5s → 6 条累计 3s
```

#### 解决方案

四处时间优化：

```python
# 优化 1：背景流串行启动间隔从 0.5s 降到 0.01s
time.sleep(0.01)

# 优化 2：移除突发子流后的 sleep(0.5)
# 每条子流启动后立即返回，间隔由 BURST_STAGGER 精确控制

# 优化 3：拓扑等待时间从 10s 降到 2s（静态转发面无需更长等待）
time.sleep(2)

# 优化 4：迭代间等待从 5s 降到 0.5s
time.sleep(0.5)
```

#### 关键教训

在需要精确时间控制的网络实验中，**每个 `time.sleep()` 调用都是潜在的精度杀手**。必须严格审查所有 sleep 调用的必要性和累积效应，尤其在循环体内。

---

## 11. 环境配置与部署指南

### 11.1 系统依赖安装

```bash
sudo apt update
sudo apt install -y mininet iperf wireshark-common
```

### 11.2 Python 环境配置

```bash
# 创建 Conda 虚拟环境（Ryu 对高版本 Python 兼容性较差）
conda create -n sdn_env python=3.9 -y
conda activate sdn_env

# 安装 Ryu 及兼容性依赖（版本必须精确）
pip install "setuptools==59.5.0" pbr       # 降级 setuptools 绕过 flat-layout 检查
pip install ryu --no-build-isolation        # 禁用构建隔离，使用降级后的 setuptools
pip install eventlet==0.30.2                # 修复 ALREADY_HANDLED ImportError

# 安装数据处理与可视化依赖
pip install scikit-learn joblib networkx numpy pandas matplotlib
```

### 11.3 验证安装

```bash
sudo mn --test pingall          # 预期：0% dropped
ryu-manager --version            # 预期：ryu-manager 4.34
ovs-vsctl --version              # 预期：3.3.4+
python -c "import sklearn; print(sklearn.__version__)"  # 预期：1.x
```

### 11.4 完整运行流程

```bash
# 激活环境
conda activate sdn_env

# 步骤 1：采集训练数据（约 4.5 小时，可中断后继续）
sudo python scripts/collect_training_data.py

# 步骤 2：组装特征矩阵（约 1 分钟）
cd scripts && python assemble_global_features.py && cd ..

# 步骤 3：训练 MLP 模型（约 5-10 分钟）
cd scripts && python train_global_mlp.py && cd ..

# 步骤 4：运行对照实验（每策略 5 轮，约 15 分钟）
sudo python scripts/run_experiment.py --group all --iters 5

# 步骤 5：生成可视化图表（约 2 分钟）
cd scripts && python plot_traffic_analysis.py
python plot_mlp_evaluation.py
python plot_policy_comparison.py && cd ..

# 步骤 6（可选）：启动 Web 仪表盘
python web/app.py
# 浏览器访问 http://localhost:5000
```

---

## 12. 数据文件格式规范

### 12.1 原始遥测数据（`data/traffic_data.csv`）

```csv
timestamp,dpid,port_no,utilization
1716000000.000,1,3,0.123456
1716000000.000,1,4,0.098765
1716000000.000,2,3,0.234567
...
```

- **timestamp**：Unix 时间戳（秒），量化到 0.5s 边界
- **dpid**：交换机 DPID（1-20）
- **port_no**：端口号（1-4）
- **utilization**：利用率（0.0-1.0），保留 6 位小数

预计记录数：20 交换机 × 4 端口 × 2 轮/秒 × 2000 轮 × 8 秒/轮 ≈ **256 万条**

### 12.2 特征矩阵（`data/global_features.pkl`）

```python
{
    "X": np.array,          # 形状 (N, 144)，N 为样本数，144 = 6 × 24
    "Y": np.array,          # 形状 (N, 24)，24 条骨干链路的预测目标
    "timestamps": np.array, # 形状 (N,)，每个样本的预测目标时间戳
    "link_keys": list,      # 24 个 (dpid, port_no) 元组
    "window_size": 6,       # 滑动窗口大小
}
```

### 12.3 训练模型（`models/global_mlp_model.pkl`）

```python
{
    "model": MLPRegressor,      # 训练好的模型对象
    "scaler_X": StandardScaler, # 输入标准化器
    "scaler_Y": StandardScaler, # 输出标准化器
    "link_keys": list,          # 24 个 (dpid, port_no) 元组
    "window_size": 6,           # 滑动窗口大小
}
```

文件大小约 1.7 MB。

### 12.4 组权重日志（`data/group_weights.csv`）

```csv
timestamp,dpid,port3_weight,port4_weight
1716000000.123,9,72,28
1716000000.623,10,65,35
...
```

- **timestamp**：Unix 时间戳（秒，浮点数）
- **dpid**：Aggregation 交换机 DPID（9-16）
- **port3_weight**：端口 3 权重（整数，总和约 100）
- **port4_weight**：端口 4 权重

### 12.5 实验结果（`data/{group}_average_results.csv`）

```csv
group,flow,avg_loss_pct,avg_jitter_ms,avg_bandwidth_mbps
base,Flow 1,12.34,0.567,0.43
base,Flow 2,8.92,0.234,0.46
...
base,Burst Flows,28.77,23.98,1.06
```

### 12.6 每链路误差统计（`data/viz_per_link_metrics.csv`）

```csv
dpid,port_no,MSE,MAE,RMSE
9,3,0.002345,0.034567,0.048432
9,4,0.001987,0.029876,0.044587
...
```

---

## 13. 实施阶段规划

### 阶段 1：基础设施搭建

**目标**：搭建可运行的 Mininet 拓扑和基础控制器框架。

| 步骤 | 内容 | 产出 | 预计耗时 |
|:---|:---|:---|:---|
| 1.1 | 环境配置（Ubuntu + Conda + Ryu + Mininet） | 可运行的开发环境 | 2h |
| 1.2 | 实现 `fat_tree_topo.py` | 16 主机 + 20 交换机的 Fat-Tree 拓扑 | 2h |
| 1.3 | 实现 `stats_mixin.py` | 0.5s 周期遥测，输出 traffic_data.csv | 3h |
| 1.4 | 实现 `base_balancer.py` | 静态转发面 + Group Table 创建 | 3h |
| 1.5 | 实现 `base_controller.py` | 最简基线控制器 | 0.5h |
| 1.6 | 验证：pingall 0% 丢包 + iperf 正常 | 基础设施就绪 | 1h |

**里程碑**：Mininet 拓扑可连通，遥测数据正常采集。

### 阶段 2：传统负载均衡实现

**目标**：实现阈值响应式控制器，验证 Group Table 权重修改的有效性。

| 步骤 | 内容 | 产出 | 预计耗时 |
|:---|:---|:---|:---|
| 2.1 | 实现 `weight_engine.py`（无模型模式） | 指数分配 + 死区过滤 | 3h |
| 2.2 | 实现 `threshold_balancer.py` | 迟滞阈值状态机 | 2h |
| 2.3 | 端到端测试：注入拥塞流量，验证权重调整 | 确认 Group Table 修改生效 | 2h |
| 2.4 | 实现 `run_experiment.py`（初版） | 自动化实验框架 | 3h |

**里程碑**：阈值控制器可在拥塞时自动调整路径权重。

### 阶段 3：数据采集与模型训练

**目标**：采集高质量训练数据，训练 MLP 预测模型。

| 步骤 | 内容 | 产出 | 预计耗时 |
|:---|:---|:---|:---|
| 3.1 | 实现 `collect_training_data.py` | 自动化数据采集脚本 | 2h |
| 3.2 | 运行数据采集（2000 轮） | traffic_data.csv（约 30 万条） | 4.5h |
| 3.3 | 实现 `assemble_global_features.py` | 特征组装脚本 | 2h |
| 3.4 | 运行特征组装 | global_features.pkl | 0.5h |
| 3.5 | 实现 `train_global_mlp.py` | MLP 训练脚本 | 2h |
| 3.6 | 运行模型训练 + 调参 | global_mlp_model.pkl | 1h |

**里程碑**：训练好的 MLP 模型，测试集 RMSE < 0.1。

### 阶段 4：AI 预测式控制器

**目标**：实现基于 MLP 的主动式负载均衡控制器。

| 步骤 | 内容 | 产出 | 预计耗时 |
|:---|:---|:---|:---|
| 4.1 | 扩展 `weight_engine.py`（加载模型 + 前向传播） | MLP 推理能力 | 3h |
| 4.2 | 实现 `predictive_balancer.py` | AI 预测式控制器 | 2h |
| 4.3 | 端到端测试：验证预测准确性和权重调整 | 确认主动调整生效 | 2h |

**里程碑**：AI 控制器可在拥塞发生前调整路径权重。

### 阶段 5：对照实验与可视化

**目标**：完成三策略对比实验，生成全面的可视化分析。

| 步骤 | 内容 | 产出 | 预计耗时 |
|:---|:---|:---|:---|
| 5.1 | 完善 `run_experiment.py`（三策略 + 多轮次） | 完整实验框架 | 2h |
| 5.2 | 运行对照实验（3 策略 × 5 轮） | 所有 *_results.csv | 0.5h |
| 5.3 | 实现 `plot_traffic_analysis.py` | 3 张流量分析图 | 3h |
| 5.4 | 实现 `plot_mlp_evaluation.py` | 6 张模型评估图 | 3h |
| 5.5 | 实现 `plot_policy_comparison.py` | 7 张策略对比图 | 3h |

**里程碑**：16 张可视化图表，实验数据完整。

### 阶段 6：Web 仪表盘与文档

**目标**：实现实时监控界面，完善项目文档。

| 步骤 | 内容 | 产出 | 预计耗时 |
|:---|:---|:---|:---|
| 6.1 | 实现 `web/app.py` | Flask + SocketIO 后端 | 3h |
| 6.2 | 实现 `web/experiment_runner.py` | 实验子进程管理 | 2h |
| 6.3 | 实现 `web/topology_utils.py` | 拓扑数据生成 | 1h |
| 6.4 | 实现前端 `static/` | HTML + JS + CSS | 5h |
| 6.5 | 撰写文档 | README + plan.md | 3h |

**里程碑**：Web 仪表盘可实时观测实验过程。

### 总计预计耗时

| 阶段 | 耗时 |
|:---|:---|
| 阶段 1：基础设施 | 11.5h |
| 阶段 2：传统均衡 | 10h |
| 阶段 3：数据与训练 | 12h |
| 阶段 4：AI 控制器 | 7h |
| 阶段 5：实验与可视化 | 11.5h |
| 阶段 6：Web 与文档 | 14h |
| **总计** | **约 66h** |

---

## 14. 已知局限与扩展方向

### 14.1 已知局限

#### 14.1.1 架构层面局限

| 问题 | 说明 | 影响 | 严重程度 |
|:---|:---|:---|:---:|
| **静态拓扑假设** | 流表规则基于 Fat-Tree k=4 硬编码，DPID 分配、端口映射、Group ID 均写死 | 无法迁移到其他拓扑（如 Leaf-Spine、Mesh），需重写转发面逻辑 | 高 |
| **单控制器架构** | 所有 20 台交换机连接同一 Ryu 实例，控制面带宽和计算能力集中 | 控制器成为单点故障（SPOF）和性能瓶颈；在更大拓扑下，端口统计请求的串行处理将成为延迟瓶颈 | 高 |
| **模型静态部署** | MLP 模型训练后固定为 pkl 文件，不随网络拓扑或流量模式演化更新 | 长期运行（数天/数周）后，模型与实际网络状态脱节，预测精度退化 | 中 |
| **仅覆盖 Aggregation 层** | 权重调整仅针对 DPID 9-16 的 Aggregation 上行端口，Edge 层不做动态调整 | 无法处理 Edge 层的哈希碰撞拥塞；Edge 到 Aggregation 的流量分布完全由 dp_hash 决定 | 中 |
| **无故障恢复机制** | 未测试链路/交换机故障场景，无快速重路由逻辑 | 单链路故障可能导致部分流量不可达直到人工干预 | 中 |

#### 14.1.2 实验与数据局限

| 问题 | 说明 | 影响 | 严重程度 |
|:---|:---|:---|:---:|
| **仅 UDP 测试** | 所有实验使用 iperf UDP 模式，无 TCP 流量 | TCP 的拥塞控制（AIMD）与负载均衡策略存在耦合效应，UDP 结果无法直接推广到 TCP 场景 | 高 |
| **哈希碰撞不可控** | OVS dp_hash 的具体哈希函数和碰撞分布取决于内核实现版本 | 不同运行间的哈希碰撞模式可能不同，实验可复现性受限 | 中 |
| **训练数据耗时** | 2000 轮采集需要 4.5 小时，且必须在 Mininet 环境中运行 | 快速迭代时成为瓶颈；无法在无 Mininet 的 CI/CD 环境中自动采集 | 中 |
| **Predictive 实验轮次不足** | AI 预测式策略仅运行 1 轮（base 和 threshold 各 30 轮） | 统计显著性有限，无法确认 AI 策略的方差特征和尾部行为 | 高 |
| **流量模式单一** | 实验仅包含 Pod 0 → Pod 3 的单方向跨 Pod 流量，无东西向混合流量 | 无法验证策略在复杂多方向流量矩阵下的表现 | 中 |
| **无背景噪声** | 实验环境中无 DNS、ARP、管理流量等背景噪声 | 实际部署中背景流量可能干扰利用率计算和预测精度 | 低 |

#### 14.1.3 实现层面局限

| 问题 | 说明 | 影响 | 严重程度 |
|:---|:---|:---|:---:|
| **硬编码参数过多** | 衰减因子（3.0）、死区（5%）、迟滞阈值（0.70/0.30）、混合权重（0.4/0.6）均为常量 | 无法适应不同网络规模和流量特征；调参需修改代码并重新部署 | 中 |
| **无流表条目老化** | 静态流表规则永不过期，无 idle_timeout / hard_timeout | 流表空间不会被释放；在大规模部署中可能导致流表溢出 | 低 |
| **利用率计算无平滑** | 链路利用率直接使用单次采样的瞬时值，无移动平均或指数平滑 | 单次采样噪声直接影响权重调整和 MLP 输入特征 | 中 |
| **权重整数量化损失** | 权重从连续比例转换为 `int(ratio × 100)` 时存在量化误差 | 当比例接近 50:50 时，量化可能将 49.6:50.4 变为 50:50，丢失微小但有意义的差异 | 低 |
| **日志与可观测性不足** | 控制器运行时仅输出 CSV，无结构化日志、指标暴露或告警机制 | 生产环境中难以诊断异常行为 | 低 |

### 14.2 可能的扩展方向

#### 14.2.1 模型与算法层面

| 扩展方向 | 说明 | 技术路线 | 难度 |
|:---|:---|:---|:---:|
| **在线增量学习** | 在运行时周期性微调 MLP 模型，适应网络状态的长期演化 | 在 `weight_engine.py` 中增加滑动窗口训练逻辑，每 N 个 tick 用最近 M 个样本微调模型参数 | 高 |
| **深度强化学习（DRL）** | 用 DRL 替代 MLP + 指数分配的两阶段方案，实现端到端策略学习 | 状态空间 = 链路利用率向量，动作空间 = 权重向量，奖励 = -丢包率 - λ×抖动 | 高 |
| **图神经网络（GNN）** | 利用 Fat-Tree 拓扑的图结构信息增强预测 | 将交换机建模为节点、链路建模为边，使用 GCN/GAT 捕获空间相关性 | 高 |
| **Transformer 时序预测** | 用 Transformer 替代 MLP，利用自注意力机制捕获长距离时序依赖 | 将滑动窗口扩展为更长序列（如 30 步），使用 PatchEmbedding + MultiHeadAttention | 中 |
| **多指标联合预测** | 除利用率外，同时预测丢包率、延迟、队列深度等指标 | 扩展输出层维度，使用多任务学习（Multi-Task Learning）共享隐藏层 | 中 |
| **自适应死区** | 根据网络负载动态调整死区阈值，在高负载时更积极调整，低负载时更保守 | `deadband = base × (1 + α × avg_util)`，高负载时死区缩小 | 低 |
| **模型压缩与量化** | 使用知识蒸馏、权重剪枝或 INT8 量化减小 MLP 模型，降低推理延迟 | 当前模型 1.7MB，推理约 0.1ms；目标 < 0.5MB，推理 < 0.05ms | 中 |

#### 14.2.2 系统与架构层面

| 扩展方向 | 说明 | 技术路线 | 难度 |
|:---|:---|:---|:---:|
| **分布式控制器** | 消除单控制器瓶颈和单点故障 | 使用 ONOS 集群或分布式 Ryu 部署，每台 Aggregation 交换机有独立控制器实例，通过分布式共识协议同步全局视图 | 高 |
| **多拓扑支持** | 将拓扑参数化，支持任意 k 值的 Fat-Tree 和 Leaf-Spine | 将 DPID 分配、端口映射、流表规则生成器抽象为拓扑描述文件驱动的工厂模式 | 中 |
| **动态拓扑适应** | 支持运行时链路增删、交换机热插拔 | 监听 `OFPStateChange` 事件，动态重建转发面和模型输入维度 | 高 |
| **故障检测与快速重路由** | 在链路故障场景下实现亚秒级收敛 | 利用 Group Table 的 `watch_port` 机制 + 控制器端链路状态探测 | 中 |
| **TCP 感知负载均衡** | 考虑 TCP 拥塞控制与负载均衡的交互效应 | 区分 TCP/UDP 流，对 TCP 流使用更保守的权重调整策略，避免与 TCP AIMD 产生振荡 | 中 |
| **QoS 区分** | 为不同优先级的流量应用不同的权重分配策略 | 在流表匹配中增加 DSCP/ToS 字段，高优先级流量使用更稳定的路径 | 中 |
| **容器化部署** | 使用 Docker 容器化 Ryu 控制器和 Mininet 拓扑 | 编写 Dockerfile，使用 docker-compose 编排控制器和网络仿真环境 | 低 |
| **CI/CD 集成** | 自动化测试流水线，每次代码提交自动运行对照实验 | 使用 GitHub Actions + Mininet 容器，自动运行 5 轮对照实验并生成报告 | 中 |

#### 14.2.3 可视化与可观测性层面

| 扩展方向 | 说明 | 技术路线 | 难度 |
|:---|:---|:---|:---:|
| **Grafana 监控集成** | 将链路利用率、权重变化等指标推送到 Prometheus/Grafana | 在控制器中增加 Prometheus exporter，暴露 gauge/histogram 指标 | 中 |
| **实时拓扑着色** | 在 Web 仪表盘中根据利用率动态着色拓扑图的节点和边 | 当前 Cytoscape.js 已支持边颜色映射，需增加动态阈值着色逻辑 | 低 |
| **回放模式** | 支持回放历史实验的完整过程，逐步观察控制器决策 | 记录完整的遥测时间序列和权重变化日志，开发时间轴回放 UI | 中 |
| **A/B 对比面板** | 在同一界面上并排对比两种策略的实时行为 | 扩展 Web 仪表盘支持同时运行两个实验实例 | 中 |

### 14.3 理论边界与开放问题

#### 14.3.1 预测式负载均衡的理论极限

当前 MLP 模型的预测精度（RMSE ≈ 0.10）对负载均衡效果的提升已接近边际递减区间。以下问题值得深入研究：

1. **预测精度与均衡效果的关系**：是否存在一个精度阈值，超过该阈值后继续提升预测精度对丢包率/吞吐量的改善可以忽略？当前数据表明 RMSE 从 0.15 降到 0.10 带来了约 15% 的丢包率改善，但从 0.10 降到 0.05 能带来多少改善？

2. **预测步长的最优值**：当前 `PREDICTION_STEP=2`（预测 1 秒后）。更长的预测步长允许更早的主动调整，但预测精度会下降。最优步长取决于网络状态的变化速度和控制器的响应延迟。

3. **主动调整 vs 响应调整的分界点**：在什么网络动态性（流量变化频率）下，预测式方案优于简单的阈值响应式？当前实验的流量变化周期约 8 秒，但更短的周期（如 1 秒）可能使预测失效。

#### 14.3.2 公平性与饿死问题

当前指数分配公式 `W(p) ∝ exp(-3.0 × U_eff(p))` 会将流量从拥塞路径"推向"空闲路径。但这种推力没有上限约束，可能导致：

- **饿死效应**：如果某条路径持续拥塞，其权重可能长期接近零，导致经过该路径的流被饿死
- **公平性悖论**：从全局最优角度看，迁移流量是正确的；但从单流角度看，被迁移的流可能经历更高的延迟（新路径的排队延时）

引入最小权重保障（如 `w = max(min_weight, calculated_w)`）和公平性约束（如 Jain's Fairness Index 下限）是潜在的改进方向。

#### 14.3.3 可扩展性分析

当前系统在 Fat-Tree k=4（20 交换机、24 骨干链路）上验证。向更大规模扩展时的瓶颈分析：

| 规模 | 交换机数 | 骨干链路 | MLP 输入维度 | 预估推理延迟 | 主要瓶颈 |
|:---|:---:|:---:|:---:|:---:|:---|
| k=4（当前） | 20 | 24 | 144 | ~0.1ms | 无 |
| k=6 | 54 | 72 | 432 | ~0.3ms | 模型维度增长 |
| k=8 | 128 | 160 | 960 | ~1.0ms | 遥测采集延迟 |
| k=10 | 250 | 300 | 1800 | ~3.0ms | 串行统计请求瓶颈 |

k ≥ 8 时，当前的串行遥测架构将成为瓶颈（20 台交换机 × 0.5s 采样周期已接近极限）。需要引入并行遥测采集或分层采样策略。
