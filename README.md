# AI 预测驱动的 SDN 动态负载均衡调度系统

基于 Ryu 控制器与 Mininet Fat-Tree k=4 拓扑，实现了一个“AI 预测驱动的主动式动态负载均衡调度系统”。本系统通过 OpenFlow 1.3 Group Table（SELECT 类型）实现等价多路径（ECMP）转发，并集成全局多层感知机（MLP）时序网络，在链路上游预测网络拥塞趋势，通过指数可用带宽分配公式与 5% 变化死区防震荡机制，实现主动预防式的流分流调度。

---

## 🌟 主要特性

- **数据中心经典拓扑**：基于 Mininet 构建 Fat-Tree k=4 拓扑，包含 20 个 Open vSwitch 交换机和 16 台主机，具备 4 条跨 Pod 的等价骨干路径。
- **高效控制面转发**：全网部署静态 `eth_dst` 匹配规则，转发平面开销小；在汇聚层通过 OpenFlow 1.3 的 `OFPGT_SELECT` 组表和 OVS `dp_hash` 机制实现自适应多路径哈希分流。
- **精准高频遥测 (StatsMixin)**：以 0.5 秒为周期向全网交换机轮询端口统计信息，采用 OpenFlow 请求 `xid` 精确映射时钟快照，消除并发遥测中的时延抖动，并根据链路层级自适应计算瞬时利用率。
- **全局时序拥塞预测**：一个模型同时预测全网 24 条骨干上行链路在未来 3 个遥测周期内的最大利用率，相比传统单链路预测，空间时序关联性更高。
- **零外部库运行期依赖的前向推理 (DynamicWeightEngine)**：在推理阶段直接提取训练好的 MLP 权重与偏置，使用纯 NumPy 进行前向传播和标准化逆变换，避免了控制面运行期对 `scikit-learn` 等复杂库的动态依赖，保障纳秒级决策延迟。
- **抗震荡的主动权重决策**：有效利用率结合当前值与预测值（0.4 : 0.6），利用负指数分配公式 $W \propto \exp(-3.0 \times U_{eff})$ 动态产生分流组表权重。通过 5% 的变化死区过滤低振幅抖动，规避流表频繁下发的控制信道开销。
- **三策略自动化对照实验**：包含静态 ECMP 路由（base 基线）、迟滞阈值响应路由（Threshold 阈值响应）和 AI 预测主动式负载均衡（Predictive 预测式）的 5 轮自动化性能对比测试，自动分析并输出丢包率、抖动与吞吐量指标。
- **多维度评估可视化**：支持训练收敛性、预测精度残差、网络时空热力图、三策略多指标对比（分组柱状图、稳定性箱线图、CDF 累计分布函数、雷达图、帕累托最优权衡等）共 17 张分析图表的一键生成。

---

## 📂 项目结构

```
.
├── topo/
│   └── fat_tree_topo.py           # Fat-Tree 拓扑定义与 OVS 负载哈希配置
├── controller/
│   ├── stats_mixin.py             # 遥测端口统计采集模块
│   ├── base_balancer.py           # 负载均衡基类（组表与流表创建）
│   ├── base_controller.py         # base 静态 50/50 基线控制器
│   ├── weight_engine.py           # 权重计算与 NumPy MLP 前向推理引擎
│   ├── threshold_balancer.py      # 迟滞阈值响应式负载均衡器
│   └── predictive_balancer.py     # AI 预测式负载均衡器
├── scripts/
│   ├── collect_training_data.py   # 训练遥测数据采集脚本
│   ├── assemble_global_features.py # 全局时空特征 Pivoting 组装脚本
│   ├── train_global_mlp.py        # 全局 MLPRegressor 训练脚本
│   ├── run_experiment.py          # 自动化三阶段性能对照实验脚本
│   ├── plot_traffic_analysis.py   # 时空热力图与流量相关性绘图脚本
│   ├── plot_mlp_evaluation.py     # MLP 模型误差与拟合评估绘图脚本
│   └── plot_policy_comparison.py  # 核心三策略对比评估可视化脚本
├── data/
│   ├── traffic_data.csv           # 采集的原始时序流量遥测文件
│   ├── global_features.pkl        # 时空 Pivot 后滑动窗口特征文件
│   ├── group_weights.csv          # 控制器运行中组表权重变化日志
│   ├── base_average_results.csv     # 静态基线实验平均指标
│   ├── threshold_average_results.csv # 阈值响应式实验平均结果
│   └── predictive_average_results.csv # AI 预测式实验最终平均结果
├── models/
│   └── global_mlp_model.pkl       # 训练序列化的 MLP 模型文件
└── figures/                       # 可视化生成的 17 张分析图表
```

---

## ⚙️ 准备工作

### 1. 系统要求
- **OS**：Ubuntu 20.04/22.04/24.04 (支持 WSbase 运行环境)。
- VS Code + Remote - WSL 扩展（推荐）。
- 系统已预先安装 Mininet、Open vSwitch (OVS) 以及 iperf 流量测试工具。

### 2. 安装 Python 依赖环境
```bash
# 升级 pip 并安装 ML 训练、推理与数据分析依赖
pip3 install --break-system-packages scikit-learn joblib networkx numpy pandas matplotlib

# Ryu 控制器框架安装（Ryu 需要较低版本的 setuptools）
pip3 install --break-system-packages setuptools==67.8.0
pip3 install --break-system-packages ryu

# 验证运行库是否成功加载
python3 -c "import ryu, sklearn, networkx, joblib, numpy, pandas, matplotlib; print('All environment dependencies OK')"
```

---

## 🚀 快速开始与运行指南

要重现完整的 AI 预测式负载均衡控制流程，请依次运行以下步骤：

### 第一步：采集训练数据集
利用静态 ECMP 控制器驱动拓扑，在网络中随机对 16 台主机注入并发随机 TCP 流，构建 2000 轮（每轮持续 8 秒，总时长约 4.5 小时）的数据集：
```bash
sudo python3 scripts/collect_training_data.py
```
这将在 `data/traffic_data.csv` 下生成数十万行的原始遥测数据。

### 第二步：特征 Pivoting 组装
对采集的链路利用率数据进行过滤（仅保留 24 条核心骨干链路）、时空对齐、以及滑动窗口（过去 6 个周期，即 3 秒历史）的切分特征工程：
```bash
python3 scripts/assemble_global_features.py
```

### 第三步：训练全局 MLP 模型
基于特征矩阵，训练隐层结构为 `(256, 128, 64)` 的全局多输出 MLP 神经网络，预测未来 3 个遥测周期（1.5 秒）内各骨干链路的预测最大利用率：
```bash
python3 scripts/train_global_mlp.py
```
模型文件包含权重参数与标准化算子，将自动保存为 `models/global_mlp_model.pkl`。

### 第四步：运行三策略对照实验
在“概率路径碰撞 + 渐进突发”的恶劣网络工况下，依次启动并自动化运行三组控制器（base 静态哈希、阈值响应式、AI 预测式）各 5 轮迭代的系统评测：
```bash
sudo python3 scripts/run_experiment.py --group all --iters 5
```
运行结束后，所有详细实验日志及最终的吞吐量、丢包率、抖动等数据会持久化保存在 `data/` 下的各策略 CSV 结果文件中。

### 第五步：生成评估可视化图表
运行三个性能评估可视化脚本，从流量特性、模型误差以及控制策略对比等维度，一键生成 17 张分析图表：
```bash
python3 scripts/plot_traffic_analysis.py
python3 scripts/plot_mlp_evaluation.py
python3 scripts/plot_policy_comparison.py
```
所有生成的图片均会自动保存在 `figures/` 目录中。

---

## 📊 策略性能对比结论

在突发流负荷逐步提升至 6.0 Mbps 的极端拥塞压测实验下，五轮平均的核心统计结果如下：

| 控制策略 | 突发流聚合丢包率 (%) | 突发流平均抖动 (ms) | 突发流平均吞吐量 (Mbps) | 所有流平均丢包率范围 (%) |
| :--- | :---: | :---: | :---: | :---: |
| **base Static Hash (基线)** | 28.77% | 23.98 ms | 1.06 Mbps | 17.78% ~ 46.35% |
| **Threshold Reactive (阈值)** | 16.76% | 33.16 ms | 1.27 Mbps | 9.33% ~ 27.48% |
| **Predictive Proactive (AI预测)** | **12.47%** | **12.82 ms** | **1.38 Mbps** | **6.80% ~ 36.00%** |

### 核心结论与工程洞察：
1. **显著降低丢包率**：AI 预测式算法通过主动拥塞规避，将突发流丢包率从 base 基线的 28.77% 降至 **12.47%**，优于滞后调整的阈值响应式（16.76%）。
2. **极佳的抗抖动性能**：阈值响应式由于在拥塞发生后才被动进行流表修改，存在较长的响应时延，造成流量在多路径间频繁切换并引发短暂的剧烈排队，使抖动恶化到 33.16 ms；而 AI 预测式提前平滑过渡，将抖动大幅降低至 **12.82 ms**。
3. **保障突发流量吞吐**：突发流量的平均接收带宽提升到了 **1.38 Mbps**，显著释放了数据中心多路径瓶颈链路的承载能力。
