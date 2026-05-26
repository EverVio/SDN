# SDN AI-Powered 动态负载均衡调度器 — 最终实现方案

## 1. 概述与设计目标
基于 Ryu 控制器与 Mininet Fat-Tree k=4 数据中心拓扑，本项目实现了一个“AI 预测驱动的主动式动态负载均衡调度器（Predictive Proactive Balancer）”。
该系统利用 OpenFlow 1.3 的 Group Table（SELECT 类型）实现等价多路径（ECMP）转发。同时，集成一个全局的多层感知机（MLP）神经网络预测骨干链路的拥塞趋势，在拥塞实际发生之前动态调整组表（Group Table）权重，达到预防性调整流量、缓解网络拥塞的目的。
本项目通过三阶段对照实验（无负载均衡的静态 ECMP、传统阈值响应式负载均衡、AI 预测式负载均衡）验证了 AI 预测驱动负载均衡的优势。

## 2. 拓扑与转发架构

### 2.1 Fat-Tree k=4 拓扑设计
网络拓扑采用 Fat-Tree k=4 架构，包含：
- 16 个主机：`h0_0` ~ `h3_3`（4 Pod × 2 Edge/Pod × 2 Host/Edge）。
- 8 个 Edge 交换机（DPID 1-8）。
- 8 个 Aggregation 交换机（DPID 9-16）。
- 4 个 Core 交换机（DPID 17-20）。
- 链路带宽配置：
  - Access 链路（主机与 Edge 交换机之间）：10 Mbps。
  - 下行汇聚链路（Edge 与 Aggregation 交换机之间）：10 Mbps。
  - 上行骨干链路（Aggregation 与 Core 交换机之间）：2 Mbps（瓶颈链路，总横截带宽 = 8 Mbps）。

### 2.2 转发与流表规则
所有控制器共享统一的静态转发面架构：
- **Group Table 方案**：
  - 交换机 DPID 1-16（Edge 与 Aggregation 层）在初始化时创建 `group_id=1` 的 SELECT 类型组表。
  - 组表包含两个 Bucket，分别指向端口 3 (uplink A) 和 4 (uplink B)，初始权重各为 50。通过 OVS 的 `dp_hash` 机制实现数据包哈希分流。
- **流表规则（Flow Table，优先级 10）**：
  - 基于 `eth_dst` 静态匹配规则实现跨 Pod 与 Pod 内路由，无需运行动态 ARP/MAC 学习，确保控制面开销最小。
  - **Edge 交换机 (dpid 1-8)**：目标为主机 `i`，若属于当前交换机直连，则直接 Output 到相应下行端口 `(i % 2) + 1`；否则，转发至 `group_id=1`。
  - **Aggregation 交换机 (dpid 9-16)**：若属于本 Pod 主机，Output 到相应下行 Edge 端口 `e_idx + 1`；否则，转发至 `group_id=1`。
  - **Core 交换机 (dpid 17-20)**：直接 Output 到目标 Pod 对应的端口 `pod + 1`。

## 3. 项目目录结构
项目代码已完全实现，其最终目录结构如下：
```
/home/yang/SDN/
├── topo/                          # Mininet 拓扑脚本
│   └── fat_tree_topo.py           # Fat-Tree k=4 拓扑生成与 OVS dp_hash 配置
├── controller/                    # Ryu 控制器与核心控制逻辑
│   ├── stats_mixin.py             # 遥测模块（固定 0.5s 周期采集端口字节数并精确计算利用率）
│   ├── base_balancer.py           # 控制器基类（封装流表下发、Group 组表创建与修改）
│   ├── base_controller.py         # 静态 ECMP 基线控制器（使用固定 50/50 权重）
│   ├── weight_engine.py           # 动态权重计算引擎（MLP 推理、指数带宽分配与 5% 死区防振荡）
│   ├── threshold_balancer.py      # 阈值响应式负载均衡器（无 ML 依赖，基于当前利用率与迟滞阈值控制）
│   └── predictive_balancer.py     # AI 预测式负载均衡器（核心模块，基于全局 MLP 的前向推理与预测）
├── scripts/                       # 数据采集、训练与性能可视化脚本
│   ├── collect_training_data.py   # 使用基线控制器，注入随机配对流量采集遥测数据（2000轮）
│   ├── assemble_global_features.py # 特征组装（时空 Pivot、滑动窗口为 6，预测步长为 2）
│   ├── train_global_mlp.py        # 全局 MLP 模型训练（输出 global_mlp_model.pkl）
│   ├── run_experiment.py          # 对照实验自动化运行脚本
│   ├── plot_traffic_analysis.py   # 流量时空特性热力图及关联分析可视化
│   ├── plot_mlp_evaluation.py     # 全局 MLP 模型预测误差与收敛情况分析可视化
│   └── plot_policy_comparison.py  # 三策略对比（丢包率、抖动、吞吐量）可视化分析
├── data/                          # 实验 CSV 数据与序列化文件
│   ├── traffic_data.csv           # 采集的原始链路利用率数据
│   ├── global_features.pkl        # 滑动窗口特征矩阵
│   ├── group_weights.csv          # 运行中记录的组权重变化数据
│   ├── l2_average_results.csv     # 静态 ECMP 基线平均结果
│   ├── threshold_average_results.csv # 阈值响应式平均结果
│   └── predictive_average_results.csv # AI 预测式最终平均结果
├── models/                        # 序列化模型
│   └── global_mlp_model.pkl       # 训练完毕的 MLP 权重与标准化参数
└── figures/                       # 可视化生成的 17 张分析图表
```

## 4. 控制策略实现细节

### 4.1 遥测与利用率计算 (StatsMixin)
遥测模块 `StatsMixin` 每 0.5 秒（`POLL_INTERVAL = 0.5`）向所有活跃交换机发送 `OFPPortStatsRequest`。
- **瞬时利用率计算**：
  使用以下公式计算每条链路的瞬时利用率：
  $$U = \frac{\Delta \text{Bytes} \times 8}{\Delta t \times \text{Link\_BW}}$$
- **精确时序匹配**：利用 OpenFlow 请求的 `xid` 字段映射发出请求时的基准时间戳 `xid_to_ts`，消除多协程并发遥测响应时的采样时延扰动。
- **层级自适应带宽**：Edge 层所有端口及 Aggregation 层下行端口带宽设为 10 Mbps，Aggregation 层上行端口与 Core 层所有端口带宽设为 2 Mbps。

### 4.2 权重计算与防振荡引擎 (DynamicWeightEngine)
核心权重决策组件：
1. **全局特征滑动窗口**：通过 `feature_history` 维护大小为 6（覆盖 3 秒历史）的时序特征。
2. **免依赖前向传播推理**：为了避免 Ryu 控制器运行时对 `scikit-learn` 复杂库的调用依赖，推理阶段将训练好的 MLP 权重 `coefs_` 与偏置 `intercepts_` 提取为 NumPy 矩阵。利用纯 NumPy 实现多层 ReLU 激活的前向计算：
   $$z^{(l)} = a^{(l-1)} W^{(l)} + b^{(l)}$$
   $$a^{(l)} = \max(0, z^{(l)})$$
   最终层输出反向标准化（Inverse Transform）并剪裁（Clip）在 $[0.0, 1.0]$ 区间内，更新预测利用率 $U_{pred}$。
3. **混合有效利用率与指数带宽分配**：
   结合当前瞬时利用率与预测利用率（配置比例为 0.4 : 0.6）计算有效利用率 $U_{eff}$：
   $$U_{eff} = 0.4 \times U_{curr} + 0.6 \times U_{pred}$$
   使用指数分配公式来计算组表 Bucket 权重：
   $$W(p) \propto \exp(-3.0 \times U_{eff}(p))$$
   该公式保证在高利用率下权重成指数级衰减，使流量被迅速引导至相对空闲的备用链路上。
4. **死区防振荡过滤**：
   配置 `WEIGHT_DEADBAND = 0.05`。仅当上行端口分流比例与上次下发的差值最大值大于 5% 时才触发 OpenFlow 组表修改指令（`OFPGC_MODIFY`）。这极大地缓解了网络控制信道的开销，避免了微小噪声导致的频繁流表下发与链路震荡。

### 4.3 阈值响应式负载均衡 (ThresholdBalancer)
- 未使用机器学习模型（`model_path=None`），其有效利用率直接采用瞬时利用率（$U_{eff} = U_{curr}$）。
- 引入迟滞阈值决策机制：
  - 当全网最大链路利用率超过拥塞阈值 `CONGESTION_THRESHOLD = 0.70` 时，启动动态组表权重调整。
  - 当最大利用率降至恢复阈值 `RECOVERY_THRESHOLD = 0.30` 以下，且当前处于拥塞响应状态时，将所有汇聚层上行组表重置为 50/50，并解除拥塞响应状态。
- 该机制模拟传统基于网络遥测越限触发的应急调度。

### 4.4 AI 预测式负载均衡 (PredictiveBalancer)
- 核心主动控制模块。在每次遥测 tick（0.5s）中，它顺序执行以下流程：
  1. 更新当前全网骨干链路的瞬时利用率至滑动窗口特征矩阵。
  2. 通过原生 NumPy 前向传播，推理出未来 3 个步长（1.5s）内所有骨干链路的预测最大利用率。
  3. 通过 `DynamicWeightEngine` 计算汇聚层上行端口的指数权重。
  4. 如果算出的权重超出死区限制，直接下发组表修改指令，并在 `data/group_weights.csv` 中持久化记录本次权重的变化。

## 5. 数据采集与模型训练

### 5.1 数据采集流水线 (`collect_training_data.py`)
使用静态 ECMP 控制器，通过 Mininet 在网络中注入大量随机流量。
- 随机从 16 台主机中配对，每轮持续 8 秒，共执行 2000 轮（约 4.5 小时）。
- 该过程能够模拟极其丰富的端口状态组合，记录超过 30 万条时序遥测记录。

### 5.2 特征工程与全局矩阵组装 (`assemble_global_features.py`)
- 过滤非骨干链路（仅保留交换机 1-20，端口 3 和 4 的记录）。
- 通过 Pivot 操作将原始数据转换为行代表时间戳、列代表具体端口的利用率矩阵。
- 配置参数：滑动窗口 `WINDOW_SIZE=6`，预测步长 `PREDICTION_STEP=2`，预测目标窗口 `TARGET_WINDOW=3`。
- 构建的输入特征 $X$ 为展平的 $6 \times 24$ 矩阵，目标 $Y$ 为未来 3 步内 24 条骨干链路的最大利用率。

### 5.3 全局 MLP 训练 (`train_global_mlp.py`)
- 使用 `MLPRegressor` 构建深度神经网络模型。
- 网络隐层结构为 `(256, 128, 64)`，激活函数为 ReLU，优化器为 Adam。
- 配置早停机制（Early Stopping），验证集比例为 15%，`random_state=42`。
- 训练完成后，将训练好的 MLP 权重参数、偏置以及两套 `StandardScaler` 标准化参数序列化保存至 `models/global_mlp_model.pkl`。

## 6. 对照实验与结果分析

### 6.1 对照实验设计 (`run_experiment.py`)
为了验证 AI 预测式、阈值响应式和静态 ECMP（L2 基线）的差异，设计了“概率哈希碰撞与渐进突发流”的极端测试场景：
- **阶段 1 (t=0s)**：引入 9 条跨 Pod 的背景 TCP 流（每条带宽 0.5 Mbps，总流量 4.5 Mbps），由于路径重叠，发生哈希碰撞，使某些 2 Mbps 的骨干链路产生初始拥塞。
- **阶段 2 (t=20s - 50s)**：每隔 6 秒逐步引入 1 条 0.25 Mbps 的突发流（共 6 条），将网络总负荷逐步提升至 6.0 Mbps，验证算法在恶劣网络条件下的自适应调度能力。
- 对每个控制算法，实验自动化运行 5 轮迭代，求取平均统计指标。

### 6.2 实验结果分析
根据 `data` 目录下真实实验结果文件统计得出如下数据：

| 性能指标 | L2 Static Hash (基线) | Threshold Reactive (阈值) | Predictive Proactive (AI预测) |
| :--- | :---: | :---: | :---: |
| **突发流聚合丢包率 (%)** | 28.77% | 16.76% | **12.47%** |
| **突发流平均抖动 (ms)** | 23.98 ms | 33.16 ms | **12.82 ms** |
| **突发流平均吞吐量 (Mbps)** | 1.06 Mbps | 1.27 Mbps | **1.38 Mbps** |
| **全网各流平均最低丢包 (%)** | 17.78% | 9.33% | **6.80%** |
| **全网各流平均最高丢包 (%)** | 46.35% | 27.48% | **36.00%** |

#### 结果分析：
1. **拥塞重分配能力**：AI 预测式算法取得了最佳的总体网络性能。其突发流丢包率从 L2 基线的 28.77% 降至 **12.47%**，较阈值响应式（16.76%）也减少了约 4.3 个百分点。
2. **抗抖动性能**：阈值响应式由于基于当前越限触发，在网络高负荷时会产生滞后的权重下发，使得流在不同路径间频繁剧烈切换，排队延时产生高频波动，导致突发流抖动上升至 33.16 ms。而 AI 预测式在拥塞实际发生前已完成了平滑的分流过渡，将抖动大幅缩减至 **12.82 ms**，甚至远优于静态基线的 23.98 ms。
3. **吞吐量保障**：突发流吞吐量从 L2 基线 1.06 Mbps 上升至 **1.38 Mbps**，有效保障了高优先级突发流量的传输带宽。

## 7. 评估可视化图表列表
可视化组件生成了多角度的图形分析，全部保存在 `figures/` 目录中：
- **`1_spatiotemporal_heatmap.png`**：展现全网链路在时空维度的瞬时利用率分布。
- **`2_key_link_utilization.png`**：跟踪展现核心骨干瓶颈链路的随时间变化曲线。
- **`3_total_network_load.png`**：全网网络总流量负载分布曲线。
- **`3_training_convergence.png`**：全局 MLP 神经网络的训练损失收敛图。
- **`4_traffic_correlation_matrix.png`**：不同骨干链路之间的流量相关性热力图。
- **`4_true_vs_predicted_scatter.png`**：MLP 预测值与真实链路状态的散点对比及拟合分析。
- **`5_single_link_tracking.png`**：单条骨干链路上预测曲线与实际轨迹的拟合追踪图.
- **`6_residual_distribution.png`**：MLP 预测残差分布直方图，验证模型预测的稳定性。
- **`7_spatial_error_distribution.png`**：不同交换机空间位置的预测误差分布图。
- **`8_hierarchical_error_distribution.png`**：各层（Aggregation 层与 Core 层）的预测误差分布图。
- **`policy_1_grouped_bar.png`**：三类控制器在所有流量上的丢包率、抖动和吞吐量对比直方图。
- **`policy_2_box_plot.png`**：三类策略在多轮迭代中稳定性的箱线图对比。
- **`policy_4_cdf.png`**：丢包与抖动性能指标的累计分布函数 (CDF) 曲线。
- **`policy_5_weight_evolution.png`**：运行过程中汇聚交换机 Group Table 权重的演动轨迹。
- **`policy_6_dual_axis_coevolution.png`**：链路利用率变化与 Group Table 权重调整的双轴协同演进曲线。
- **`policy_7_pareto_tradeoff.png`**：吞吐量与丢包率之间的帕累托最优权衡对比分析。
- **`policy_8_flow_fairness_radar.png`**：三种策略下流之间带宽分配公平性的雷达图。
