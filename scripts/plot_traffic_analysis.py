"""
维度一：全网原始流量时空动态演进分析
- 图表 1: 全网骨干链路时空流量热力图
- 图表 2: 关键骨干链路利用率时序波动曲线图
"""

import os
import sys
import joblib
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import MaxNLocator, FuncFormatter
import datetime

FIGURES_DIR = os.path.join(os.path.dirname(__file__), "..", "figures")
DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")


def load_data():
    raw = joblib.load(os.path.join(DATA_DIR, "viz_raw_traffic_matrix.pkl"))
    return raw["matrix"], raw["link_keys"], raw["timestamps"]


def make_link_label(dpid, port_no):
    """S{dpid}-P{port_no}, 带拓扑层级前缀便于纵轴阅读"""
    if dpid <= 8:
        prefix = "E"  # Edge
    elif dpid <= 16:
        prefix = "A"  # Aggregation
    else:
        prefix = "C"  # Core
    return f"{prefix}{dpid}-P{port_no}"


def format_timestamp(ts, _=None):
    """Unix timestamp → HH:MM:SS"""
    return datetime.datetime.fromtimestamp(ts).strftime("%H:%M:%S")


# ─────────────────────────────────────────────────────
# 图表 1: 全网骨干链路时空流量热力图
# ─────────────────────────────────────────────────────
def plot_heatmap(matrix, link_keys, timestamps, downsample_time=4):
    """
    热力图: X=时间, Y=链路, 颜色=利用率
    downsample_time: 每 N 个时间步取 1 个，减少渲染负担
    """
    # 下采样时间维度
    ts_ds = timestamps[::downsample_time]
    mat_ds = matrix[::downsample_time, :]
    n_time, n_links = mat_ds.shape

    # Y 轴标签
    y_labels = [make_link_label(d, p) for d, p in link_keys]

    # 按拓扑层级排序: Core → Aggregation → Edge
    layer_order = []
    for target_prefix in ["C", "A", "E"]:
        for i, (d, p) in enumerate(link_keys):
            if make_link_label(d, p).startswith(target_prefix):
                layer_order.append(i)

    mat_sorted = mat_ds[:, layer_order]
    y_labels_sorted = [y_labels[i] for i in layer_order]

    # 绘图
    fig, ax = plt.subplots(figsize=(18, 10))

    # 时间轴用相对秒数（从 0 开始）
    t_rel = ts_ds - ts_ds[0]
    extent = [t_rel[0], t_rel[-1], -0.5, n_links - 0.5]

    im = ax.imshow(
        mat_sorted.T,
        aspect="auto",
        origin="lower",
        cmap="YlOrRd",
        vmin=0.0,
        vmax=1.0,
        extent=extent,
        interpolation="nearest",
    )

    # 在层级分界处画水平线
    n_core = sum(1 for d, p in link_keys if d > 16)
    n_agg = sum(1 for d, p in link_keys if 9 <= d <= 16)
    ax.axhline(y=n_core - 0.5, color="white", linewidth=1.5, linestyle="--")
    ax.axhline(y=n_core + n_agg - 0.5, color="white", linewidth=1.5, linestyle="--")
    ax.text(
        t_rel[-1] * 1.01, n_core / 2, "Core", va="center", ha="left",
        fontsize=9, fontweight="bold", color="white",
        bbox=dict(facecolor="black", alpha=0.6, pad=2),
    )
    ax.text(
        t_rel[-1] * 1.01, n_core + n_agg / 2, "Agg", va="center", ha="left",
        fontsize=9, fontweight="bold", color="white",
        bbox=dict(facecolor="black", alpha=0.6, pad=2),
    )
    ax.text(
        t_rel[-1] * 1.01, n_core + n_agg + (n_links - n_core - n_agg) / 2,
        "Edge", va="center", ha="left",
        fontsize=9, fontweight="bold", color="white",
        bbox=dict(facecolor="black", alpha=0.6, pad=2),
    )

    # Y 轴刻度：只显示部分（太密集会重叠）
    if n_links <= 80:
        ax.set_yticks(range(n_links))
        ax.set_yticklabels(y_labels_sorted, fontsize=6)
    else:
        step = max(1, n_links // 40)
        ticks = list(range(0, n_links, step))
        ax.set_yticks(ticks)
        ax.set_yticklabels([y_labels_sorted[i] for i in ticks], fontsize=6)

    # X 轴：用原始时间戳格式化
    n_x_ticks = 8
    tick_positions = np.linspace(t_rel[0], t_rel[-1], n_x_ticks)
    tick_timestamps = np.linspace(ts_ds[0], ts_ds[-1], n_x_ticks)
    ax.set_xticks(tick_positions)
    ax.set_xticklabels(
        [format_timestamp(t) for t in tick_timestamps], fontsize=8, rotation=30
    )

    # Colorbar
    cbar = fig.colorbar(im, ax=ax, pad=0.02, shrink=0.85)
    cbar.set_label("Link Utilization", fontsize=11)
    cbar.ax.tick_params(labelsize=9)

    ax.set_xlabel("Time", fontsize=12)
    ax.set_ylabel("Backbone Link", fontsize=12)
    ax.set_title(
        "Spatiotemporal Traffic Heatmap of Backbone Links\n"
        f"(Fat-Tree k=4, {n_links} links, {n_time} samples @ 0.5s, "
        f"span {t_rel[-1]:.0f}s)",
        fontsize=13,
        fontweight="bold",
    )

    fig.tight_layout()
    out_path = os.path.join(FIGURES_DIR, "1_spatiotemporal_heatmap.png")
    fig.savefig(out_path, dpi=180, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved: {out_path}")


# ─────────────────────────────────────────────────────
# 图表 2: 关键骨干链路利用率时序波动曲线图
# ─────────────────────────────────────────────────────
def plot_key_links(matrix, link_keys, timestamps):
    """
    选取 6 条代表性链路绘制时序曲线，标注拥塞/恢复阈值线
    """
    # 选取策略：Core 2 条 + Agg 2 条 + Edge 2 条（上行端口）
    candidates = {
        "Core": [(d, p) for d, p in link_keys if d > 16 and p in [1, 2]],
        "Agg-Up": [(d, p) for d, p in link_keys if 9 <= d <= 16 and p in [3, 4]],
        "Edge-Up": [(d, p) for d, p in link_keys if d <= 8 and p in [3, 4]],
    }
    selected = []
    for layer_name, links in candidates.items():
        if links:
            # 取方差最大的（最有变化的链路）
            variances = []
            for d, p in links:
                idx = link_keys.index((d, p))
                variances.append((np.var(matrix[:, idx]), d, p))
            variances.sort(reverse=True)
            for _, d, p in variances[:2]:
                selected.append((d, p))

    # 时间轴：转为相对分钟
    t_min = (timestamps - timestamps[0]) / 60.0

    # 对利用率做平滑（滑动窗口均值，窗口=240 即 2 分钟）消除毛刺
    def smooth(arr, w=240):
        kernel = np.ones(w) / w
        return np.convolve(arr, kernel, mode="same")

    fig, ax = plt.subplots(figsize=(16, 7))

    colors = ["#e74c3c", "#e67e22", "#2ecc71", "#3498db", "#9b59b6", "#1abc9c"]
    for i, (d, p) in enumerate(selected):
        idx = link_keys.index((d, p))
        raw_line = matrix[:, idx]
        smoothed = smooth(raw_line)
        label = make_link_label(d, p)
        ax.plot(t_min, smoothed, color=colors[i % len(colors)],
                linewidth=0.9, alpha=0.85, label=label)

    # 阈值线
    ax.axhline(y=0.70, color="red", linestyle="--", linewidth=1.2, alpha=0.7,
               label="Congestion Threshold (0.70)")
    ax.axhline(y=0.30, color="green", linestyle="--", linewidth=1.2, alpha=0.7,
               label="Recovery Threshold (0.30)")

    ax.set_xlabel("Time (minutes)", fontsize=12)
    ax.set_ylabel("Link Utilization", fontsize=12)
    ax.set_ylim(-0.05, 1.05)
    ax.set_xlim(t_min[0], t_min[-1])
    ax.legend(loc="upper right", fontsize=8, ncol=2, framealpha=0.9)
    ax.set_title(
        "Key Backbone Link Utilization Over Time\n"
        "(Smoothed w/ 2-minute window)",
        fontsize=13,
        fontweight="bold",
    )
    ax.grid(True, alpha=0.3)

    fig.tight_layout()
    out_path = os.path.join(FIGURES_DIR, "2_key_link_utilization.png")
    fig.savefig(out_path, dpi=180, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved: {out_path}")


# ─────────────────────────────────────────────────────
# 图表 3: 全网总吞吐负荷演趋图 (Total Network Load Timeline)
# ─────────────────────────────────────────────────────
def plot_total_network_load():
    traffic_path = os.path.join(DATA_DIR, "traffic_data.csv")
    if not os.path.exists(traffic_path):
        print(f"Warning: {traffic_path} not found. Skipping total load chart.")
        return
    df = pd.read_csv(traffic_path)
    
    # 按照时间戳分组计算平均利用率
    grouped = df.groupby("timestamp")["utilization"].mean().reset_index().sort_values("timestamp")
    
    min_ts = grouped["timestamp"].min()
    t_rel = grouped["timestamp"] - min_ts
    
    fig, ax = plt.subplots(figsize=(14, 6))
    
    ax.plot(t_rel, grouped["utilization"], color="#9b59b6", linewidth=2.0, label="All Ports Avg Utilization")
    ax.fill_between(t_rel, grouped["utilization"], color="#9b59b6", alpha=0.15)
    
    # 标注实验事件 (Use transform=ax.get_xaxis_transform() to fix height safely at 15% from the bottom axis)
    ax.axvline(x=0, color="gray", linestyle="--", linewidth=1.0)
    ax.text(0.5, 0.15, "t=0s: Background Flows Start", rotation=90, fontsize=8.5, color="gray", transform=ax.get_xaxis_transform())
    
    ax.axvline(x=20, color="red", linestyle=":", linewidth=1.2)
    ax.text(20.5, 0.15, "t=20s: Burst Subflow A", rotation=90, fontsize=8.5, color="red", transform=ax.get_xaxis_transform())
    
    ax.axvline(x=26, color="red", linestyle=":", linewidth=1.2)
    ax.text(26.5, 0.15, "t=26s: Burst Subflow B", rotation=90, fontsize=8.5, color="red", transform=ax.get_xaxis_transform())
    
    ax.axvline(x=32, color="red", linestyle=":", linewidth=1.2)
    ax.text(32.5, 0.15, "t=32s: Burst Subflow C", rotation=90, fontsize=8.5, color="red", transform=ax.get_xaxis_transform())

    ax.set_xlabel("Time (seconds)", fontsize=12)
    ax.set_ylabel("Average Telemetry Utilization", fontsize=12)
    ax.set_title(
        "Total Network Load Timeline\n"
        "(Arithmetic average of utilization across all ports over time)",
        fontsize=13, fontweight="bold"
    )
    ax.grid(True, alpha=0.3)
    ax.legend(loc="upper left")
    
    fig.tight_layout()
    out = os.path.join(FIGURES_DIR, "3_total_network_load.png")
    fig.savefig(out, dpi=180, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved: {out}")


# ─────────────────────────────────────────────────────
# 图表 4: 链路流量空间相关性热力图 (Traffic Correlation Matrix Heatmap)
# ─────────────────────────────────────────────────────
def plot_correlation_matrix(matrix, link_keys):
    labels = [make_link_label(d, p) for d, p in link_keys]
    df_mat = pd.DataFrame(matrix, columns=labels)
    corr_matrix = df_mat.corr(method="pearson").fillna(0.0)
    
    fig, ax = plt.subplots(figsize=(14, 12))
    
    im = ax.imshow(
        corr_matrix.values,
        cmap="coolwarm",
        vmin=-1.0,
        vmax=1.0,
        aspect="equal",
        origin="lower"
    )
    
    n_links = len(labels)
    ax.set_xticks(range(n_links))
    ax.set_xticklabels(labels, rotation=90, fontsize=4.5)  # Set slightly smaller fontsize (4.5pt) to avoid overlap
    ax.set_yticks(range(n_links))
    ax.set_yticklabels(labels, fontsize=4.5)
    
    ax.set_xticks(np.arange(n_links) - 0.5, minor=True)
    ax.set_yticks(np.arange(n_links) - 0.5, minor=True)
    ax.grid(which="minor", color="white", linestyle="-", linewidth=0.2)
    ax.tick_params(which="minor", bottom=False, left=False)
    
    cbar = fig.colorbar(im, ax=ax, pad=0.02, shrink=0.85)
    cbar.set_label("Pearson Correlation Coefficient", fontsize=11)
    cbar.ax.tick_params(labelsize=9)
    
    # Text annotation cell annotation is removed to offer clean, professional, high-density macroeconomic visual clarity.
    # Grid color intensities (RdBu/Coolwarm) are sufficient for demonstrating spatial correlation patterns.
                
    ax.set_title(
        "Traffic Correlation Matrix Heatmap of Backbone Links\n"
        "(Fat-Tree k=4 symmetrical topology, showing complementary/synchronous paths)",
        fontsize=13, fontweight="bold"
    )
    
    fig.tight_layout()
    out = os.path.join(FIGURES_DIR, "4_traffic_correlation_matrix.png")
    fig.savefig(out, dpi=180, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved: {out}")


if __name__ == "__main__":
    os.makedirs(FIGURES_DIR, exist_ok=True)

    print("Loading data...")
    matrix, link_keys, timestamps = load_data()
    print(f"  matrix shape: {matrix.shape}, links: {len(link_keys)}, "
          f"ts range: {timestamps[0]:.1f} - {timestamps[-1]:.1f}")

    print("\n[1/4] Plotting spatiotemporal heatmap...")
    plot_heatmap(matrix, link_keys, timestamps, downsample_time=4)

    print("\n[2/4] Plotting key link utilization curves...")
    plot_key_links(matrix, link_keys, timestamps)

    print("\n[3/4] Plotting total network load timeline...")
    plot_total_network_load()

    print("\n[4/4] Plotting traffic correlation matrix...")
    plot_correlation_matrix(matrix, link_keys)

    print("\nDone.")
