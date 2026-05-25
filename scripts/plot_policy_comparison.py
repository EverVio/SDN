"""
多策略负载均衡性能对比实验评估可视化
- Chart 1: 多指标核心策略对比分组柱状图
- Chart 2: 20轮迭代实验鲁棒性与稳定性箱线图
- Chart 4: 丢包与抖动性能指标累计分布函数图 (CDF)
"""

import os
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch

FIGURES_DIR = os.path.join(os.path.dirname(__file__), "..", "figures")
DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")

# Unified burst flow name across all groups
BURST_LABEL = "Burst Flows"

POLICY_COLORS = {
    "l2": "#95a5a6",
    "threshold": "#3498db",
    "predictive": "#2ecc71",
}
POLICY_LABELS = {
    "l2": "L2 Static Hash",
    "threshold": "Threshold Reactive",
    "predictive": "Predictive Proactive",
}


def load_average(group):
    df = pd.read_csv(os.path.join(DATA_DIR, f"{group}_average_results.csv"))
    df["flow"] = df["flow"].replace({"Flow 4 (burst)": BURST_LABEL})
    return df


def load_iterations(group):
    df = pd.read_csv(os.path.join(DATA_DIR, f"{group}_iteration_results.csv"))
    df["flow"] = df["flow"].replace({"Flow 4 (burst)": BURST_LABEL})
    return df


# ─────────────────────────────────────────────────────
# Chart 1: Grouped Bar Chart for Core Metrics
# ─────────────────────────────────────────────────────
def plot_grouped_bar():
    groups = ["l2", "threshold", "predictive"]
    dfs = {g: load_average(g) for g in groups}

    # Determine flow order: Flow 1-9 sorted, then Burst Flows last
    all_flows = []
    for i in range(1, 10):
        all_flows.append(f"Flow {i}")
    all_flows.append(BURST_LABEL)

    fig, axes = plt.subplots(3, 1, figsize=(16, 14))

    metrics = [
        ("avg_loss_pct", "Average Loss Rate (%)", "Loss Rate (%)"),
        ("avg_bandwidth_mbps", "Average Throughput (Mbps)", "Throughput (Mbps)"),
        ("avg_jitter_ms", "Average Jitter (ms)", "Jitter (ms)"),
    ]

    x = np.arange(len(all_flows))
    bar_width = 0.25
    offsets = [-bar_width, 0, bar_width]

    for ax, (col, title, ylabel) in zip(axes, metrics):
        for i, g in enumerate(groups):
            df = dfs[g]
            vals = []
            for f in all_flows:
                row = df[df["flow"] == f]
                vals.append(row[col].values[0] if len(row) > 0 else 0)
            bars = ax.bar(
                x + offsets[i], vals, bar_width,
                color=POLICY_COLORS[g], label=POLICY_LABELS[g],
                edgecolor="white", linewidth=0.5, alpha=0.9,
            )
            # Value labels on bars
            for bar, v in zip(bars, vals):
                if v > 0:
                    ax.text(
                        bar.get_x() + bar.get_width() / 2, bar.get_height(),
                        f"{v:.2f}", ha="center", va="bottom", fontsize=6.5,
                    )

        ax.set_xticks(x)
        ax.set_xticklabels(all_flows, fontsize=9)
        ax.set_ylabel(ylabel, fontsize=11)
        ax.set_title(title, fontsize=12, fontweight="bold")
        ax.legend(fontsize=9, loc="upper left")
        ax.grid(axis="y", alpha=0.3)

    fig.suptitle(
        "Multi-Policy Load Balancing: Core Metrics Comparison\n"
        "(L2 Static Hash vs. Threshold Reactive vs. Predictive Proactive)",
        fontsize=14, fontweight="bold", y=1.01,
    )
    fig.tight_layout()
    out = os.path.join(FIGURES_DIR, "policy_1_grouped_bar.png")
    fig.savefig(out, dpi=180, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved: {out}")


# ─────────────────────────────────────────────────────
# Chart 2: Box Plot for Robustness & Stability
# ─────────────────────────────────────────────────────
def plot_box():
    groups = ["l2", "threshold", "predictive"]
    burst_dfs = {}
    for g in groups:
        df = load_iterations(g)
        burst_dfs[g] = df[df["flow"] == BURST_LABEL]

    fig, axes = plt.subplots(1, 2, figsize=(14, 7))

    # Subplot 1: Loss rate distribution
    ax1 = axes[0]
    loss_data = [burst_dfs[g]["loss_pct"].values for g in groups]
    bp1 = ax1.boxplot(
        loss_data, tick_labels=[POLICY_LABELS[g] for g in groups],
        patch_artist=True, widths=0.5,
        medianprops=dict(color="black", linewidth=1.5),
        flierprops=dict(marker="o", markersize=5, alpha=0.6),
    )
    for patch, g in zip(bp1["boxes"], groups):
        patch.set_facecolor(POLICY_COLORS[g])
        patch.set_alpha(0.8)
    ax1.set_ylabel("Loss Rate (%)", fontsize=12)
    ax1.set_title(
        "Burst Flow Loss Rate Distribution\n(20 Independent Iterations)",
        fontsize=12, fontweight="bold",
    )
    ax1.grid(axis="y", alpha=0.3)
    ax1.tick_params(axis="x", labelsize=10)

    # Annotate median and IQR
    for i, g in enumerate(groups):
        data = burst_dfs[g]["loss_pct"]
        med = data.median()
        q1, q3 = data.quantile(0.25), data.quantile(0.75)
        ax1.text(
            i + 1, med, f"  Med={med:.1f}%\n  IQR={q1:.1f}–{q3:.1f}%",
            va="center", fontsize=8, color="#333",
        )

    # Subplot 2: Bandwidth distribution
    ax2 = axes[1]
    bw_data = [burst_dfs[g]["bandwidth_mbps"].values for g in groups]
    bp2 = ax2.boxplot(
        bw_data, tick_labels=[POLICY_LABELS[g] for g in groups],
        patch_artist=True, widths=0.5,
        medianprops=dict(color="black", linewidth=1.5),
        flierprops=dict(marker="o", markersize=5, alpha=0.6),
    )
    for patch, g in zip(bp2["boxes"], groups):
        patch.set_facecolor(POLICY_COLORS[g])
        patch.set_alpha(0.8)
    ax2.set_ylabel("Throughput (Mbps)", fontsize=12)
    ax2.set_title(
        "Burst Flow Throughput Distribution\n(20 Independent Iterations)",
        fontsize=12, fontweight="bold",
    )
    ax2.grid(axis="y", alpha=0.3)
    ax2.tick_params(axis="x", labelsize=10)

    for i, g in enumerate(groups):
        data = burst_dfs[g]["bandwidth_mbps"]
        med = data.median()
        q1, q3 = data.quantile(0.25), data.quantile(0.75)
        ax2.text(
            i + 1, med, f"  Med={med:.2f} Mbps\n  IQR={q1:.2f}–{q3:.2f}",
            va="center", fontsize=8, color="#333",
        )

    fig.suptitle(
        "Policy Robustness & Stability: Burst Flow Performance Across 20 Iterations\n"
        "(Narrower boxes = more consistent control under hash collision uncertainty)",
        fontsize=14, fontweight="bold", y=1.02,
    )
    fig.tight_layout()
    out = os.path.join(FIGURES_DIR, "policy_2_box_plot.png")
    fig.savefig(out, dpi=180, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved: {out}")


# ─────────────────────────────────────────────────────
# Chart 4: CDF for Network Impairments
# ─────────────────────────────────────────────────────
def plot_cdf():
    groups = ["l2", "threshold", "predictive"]
    all_data = {}
    for g in groups:
        df = load_iterations(g)
        all_data[g] = df

    fig, axes = plt.subplots(1, 2, figsize=(16, 7))

    # Subplot 1: Loss rate CDF
    ax1 = axes[0]
    for g in groups:
        loss = all_data[g]["loss_pct"].values
        sorted_loss = np.sort(loss)
        cdf = np.arange(1, len(sorted_loss) + 1) / len(sorted_loss)
        ax1.plot(sorted_loss, cdf, color=POLICY_COLORS[g], linewidth=2,
                 label=f"{POLICY_LABELS[g]} (n={len(sorted_loss)})")

    # Mark P95 and P99
    for g in groups:
        loss = all_data[g]["loss_pct"].values
        p95 = np.percentile(loss, 95)
        p99 = np.percentile(loss, 99)
        ax1.axvline(x=p95, color=POLICY_COLORS[g], linestyle=":", alpha=0.5, linewidth=1)
        ax1.text(p95, 0.50, f"P95={p95:.1f}%", fontsize=7, color=POLICY_COLORS[g],
                 rotation=90, va="center")

    ax1.set_xlabel("Loss Rate (%)", fontsize=12)
    ax1.set_ylabel("Cumulative Probability", fontsize=12)
    ax1.set_title("Loss Rate CDF", fontsize=12, fontweight="bold")
    ax1.legend(fontsize=9, loc="lower right")
    ax1.grid(True, alpha=0.3)
    ax1.set_xlim(left=-1)
    ax1.set_ylim(-0.02, 1.02)

    # Subplot 2: Jitter CDF
    ax2 = axes[1]
    for g in groups:
        jitter = all_data[g]["jitter_ms"].values
        sorted_jitter = np.sort(jitter)
        cdf = np.arange(1, len(sorted_jitter) + 1) / len(sorted_jitter)
        ax2.plot(sorted_jitter, cdf, color=POLICY_COLORS[g], linewidth=2,
                 label=f"{POLICY_LABELS[g]} (n={len(sorted_jitter)})")

    for g in groups:
        jitter = all_data[g]["jitter_ms"].values
        p95 = np.percentile(jitter, 95)
        ax2.axvline(x=p95, color=POLICY_COLORS[g], linestyle=":", alpha=0.5, linewidth=1)
        ax2.text(p95, 0.50, f"P95={p95:.1f}ms", fontsize=7, color=POLICY_COLORS[g],
                 rotation=90, va="center")

    ax2.set_xlabel("Jitter (ms)", fontsize=12)
    ax2.set_ylabel("Cumulative Probability", fontsize=12)
    ax2.set_title("Jitter CDF", fontsize=12, fontweight="bold")
    ax2.legend(fontsize=9, loc="lower right")
    ax2.grid(True, alpha=0.3)
    ax2.set_xlim(left=-1)
    ax2.set_ylim(-0.02, 1.02)

    fig.suptitle(
        "CDF of Network Impairments Across All Flows & 20 Iterations\n"
        "(Steeper curve reaching Y=1.0 on the left = better QoS control)",
        fontsize=14, fontweight="bold", y=1.02,
    )
    fig.tight_layout()
    out = os.path.join(FIGURES_DIR, "policy_4_cdf.png")
    fig.savefig(out, dpi=180, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved: {out}")


if __name__ == "__main__":
    os.makedirs(FIGURES_DIR, exist_ok=True)

    print("[1/3] Plotting grouped bar chart (core metrics)...")
    plot_grouped_bar()

    print("[2/3] Plotting box plot (robustness & stability)...")
    plot_box()

    print("[3/3] Plotting CDF chart (network impairments)...")
    plot_cdf()

    print("\nAll 3 policy comparison charts saved to figures/")
