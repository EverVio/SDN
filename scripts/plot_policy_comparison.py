"""
多策略负载均衡性能对比实验评估可视化
- Chart 1: 多指标核心策略对比分组柱状图
- Chart 2: 30轮迭代实验鲁棒性与稳定性箱线图
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

BURST_LABEL = "Burst Flows"

POLICY_COLORS = {
    "base": "#95a5a6",
    "threshold": "#3498db",
    "predictive": "#2ecc71",
}
POLICY_LABELS = {
    "base": "base Static Hash",
    "threshold": "Threshold Reactive",
    "predictive": "Predictive Proactive",
}


def load_average(group):
    df = pd.read_csv(os.path.join(DATA_DIR, f"{group}_average_results.csv"))
    df["flow"] = df["flow"].replace({"Burst Flows": BURST_LABEL})
    return df


def load_iterations(group):
    df = pd.read_csv(os.path.join(DATA_DIR, f"{group}_iteration_results.csv"))
    df["flow"] = df["flow"].replace({"Burst Flows": BURST_LABEL})
    return df


# ─────────────────────────────────────────────────────
# Chart 1: Grouped Bar Chart for Core Metrics
# ─────────────────────────────────────────────────────
def plot_grouped_bar():
    groups = ["base", "threshold", "predictive"]
    dfs = {g: load_average(g) for g in groups}

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
                x + offsets[i],
                vals,
                bar_width,
                color=POLICY_COLORS[g],
                label=POLICY_LABELS[g],
                edgecolor="white",
                linewidth=0.5,
                alpha=0.9,
            )

            for bar, v in zip(bars, vals):
                if v > 0:
                    if col == "avg_jitter_ms":
                        y_offset = (0.01 + i * 0.06) * (
                            ax.get_ylim()[1] - ax.get_ylim()[0]
                        )
                    else:
                        y_offset = 0.005 * (ax.get_ylim()[1] - ax.get_ylim()[0])
                    ax.text(
                        bar.get_x() + bar.get_width() / 2,
                        bar.get_height() + y_offset,
                        f"{v:.2f}",
                        ha="center",
                        va="bottom",
                        fontsize=6.5,
                    )

        ax.set_xticks(x)
        ax.set_xticklabels(all_flows, fontsize=9)
        ax.set_ylabel(ylabel, fontsize=11)
        ax.set_title(title, fontsize=12, fontweight="bold")
        ax.legend(fontsize=9, loc="upper center", bbox_to_anchor=(0.5, 0.98), ncol=3)
        ax.grid(axis="y", alpha=0.3)

    if col == "avg_jitter_ms":
        y_max = ax.get_ylim()[1]
        ax.set_ylim(top=y_max * 1.2)

    fig.suptitle(
        "Multi-Policy Load Balancing: Core Metrics Comparison\n"
        "(base Static Hash vs. Threshold Reactive vs. Predictive Proactive)",
        fontsize=14,
        fontweight="bold",
        y=1.01,
    )
    fig.tight_layout()
    out = os.path.join(FIGURES_DIR, "policy_1_grouped_bar.png")
    fig.savefig(out, dpi=300, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved: {out}")


# ─────────────────────────────────────────────────────
# Chart 2: Box Plot for Robustness & Stability (Optimized with Arrows)
# ─────────────────────────────────────────────────────
def plot_box():
    groups = ["base", "threshold", "predictive"]
    burst_dfs = {}
    for g in groups:
        df = load_iterations(g)
        burst_dfs[g] = df[df["flow"] == BURST_LABEL]

    fig, axes = plt.subplots(1, 2, figsize=(14, 7))

    ax1 = axes[0]
    loss_data = [burst_dfs[g]["loss_pct"].values for g in groups]
    bp1 = ax1.boxplot(
        loss_data,
        tick_labels=[POLICY_LABELS[g] for g in groups],
        patch_artist=True,
        widths=0.5,
        medianprops=dict(color="black", linewidth=1.5),
        flierprops=dict(marker="o", markersize=5, alpha=0.6),
    )
    for patch, g in zip(bp1["boxes"], groups):
        patch.set_facecolor(POLICY_COLORS[g])
        patch.set_alpha(0.8)
    ax1.set_ylabel("Loss Rate (%)", fontsize=12)
    ax1.set_title(
        "Burst Flow Loss Rate Distribution\n(30 Independent Iterations)",
        fontsize=12,
        fontweight="bold",
    )
    ax1.grid(axis="y", alpha=0.3)
    ax1.set_axisbelow(True)
    ax1.set_xlim(0.5, 4.2)

    for i, g in enumerate(groups):
        data = burst_dfs[g]["loss_pct"]
        if len(data) == 0:
            continue
        med = data.median()
        q1, q3 = data.quantile(0.25), data.quantile(0.75)

        ax1.annotate(
            f"Med={med:.1f}%\nIQR={q1:.1f}–{q3:.1f}%",
            xy=(i + 1.25, med),
            xytext=(i + 1.45, med + 5),
            va="center",
            ha="left",
            fontsize=7.5,
            color="#333",
            fontweight="bold",
            bbox=dict(
                boxstyle="round,pad=0.2", facecolor="white", edgecolor="#ddd", alpha=0.8
            ),
            arrowprops=dict(
                arrowstyle="->",
                color="#666666",
                linewidth=0.8,
                connectionstyle="arc3",
            ),
        )

    ax2 = axes[1]
    bw_data = [burst_dfs[g]["bandwidth_mbps"].values for g in groups]
    bp2 = ax2.boxplot(
        bw_data,
        tick_labels=[POLICY_LABELS[g] for g in groups],
        patch_artist=True,
        widths=0.5,
        medianprops=dict(color="black", linewidth=1.5),
        flierprops=dict(marker="o", markersize=5, alpha=0.6),
    )
    for patch, g in zip(bp2["boxes"], groups):
        patch.set_facecolor(POLICY_COLORS[g])
        patch.set_alpha(0.8)
    ax2.set_ylabel("Throughput (Mbps)", fontsize=12)
    ax2.set_title(
        "Burst Flow Throughput Distribution\n(30 Independent Iterations)",
        fontsize=12,
        fontweight="bold",
    )
    ax2.grid(axis="y", alpha=0.3)
    ax2.set_axisbelow(True)
    ax2.set_xlim(0.5, 4.2)

    for i, g in enumerate(groups):
        data = burst_dfs[g]["bandwidth_mbps"]
        if len(data) == 0:
            continue
        med = data.median()
        q1, q3 = data.quantile(0.25), data.quantile(0.75)

        ax2.annotate(
            f"Med={med:.2f} Mbps\nIQR={q1:.2f}–{q3:.2f}",
            xy=(i + 1.25, med),
            xytext=(i + 1.45, med - 0.1),
            va="center",
            ha="left",
            fontsize=7.5,
            color="#333",
            fontweight="bold",
            bbox=dict(
                boxstyle="round,pad=0.2", facecolor="white", edgecolor="#ddd", alpha=0.8
            ),
            arrowprops=dict(
                arrowstyle="->", color="#666666", linewidth=0.8, connectionstyle="arc3"
            ),
        )

    fig.suptitle(
        "Policy Robustness & Stability: Burst Flow Performance Across 30 Iterations\n"
        "(Narrower boxes = more consistent control under hash collision uncertainty)",
        fontsize=14,
        fontweight="bold",
        y=0.93,
    )
    fig.tight_layout(rect=[0, 0, 1, 0.93])
    out = os.path.join(FIGURES_DIR, "policy_2_box_plot.png")
    fig.savefig(out, dpi=300, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved: {out}")


# ─────────────────────────────────────────────────────
# Chart 4: CDF for Network Impairments
# ─────────────────────────────────────────────────────
def plot_cdf():
    groups = ["base", "threshold", "predictive"]
    all_data = {}
    for g in groups:
        df = load_iterations(g)
        all_data[g] = df

    fig, axes = plt.subplots(1, 2, figsize=(16, 7))

    ax1 = axes[0]
    for g in groups:
        loss = all_data[g]["loss_pct"].values
        if len(loss) == 0:
            continue
        sorted_loss = np.sort(loss)
        cdf = np.arange(1, len(sorted_loss) + 1) / len(sorted_loss)
        ax1.plot(
            sorted_loss,
            cdf,
            color=POLICY_COLORS[g],
            linewidth=2,
            label=f"{POLICY_LABELS[g]} (n={len(sorted_loss)})",
        )

    for g in groups:
        loss = all_data[g]["loss_pct"].values
        if len(loss) == 0:
            continue
        p95 = np.percentile(loss, 95)
        ax1.axvline(
            x=p95, color=POLICY_COLORS[g], linestyle="--", alpha=1.0, linewidth=2.5
        )
        ax1.text(
            p95 + 3,
            0.5,
            f"P95={p95:.1f}%",
            fontsize=11,
            fontweight="bold",
            color=POLICY_COLORS[g],
            rotation=90,
            va="center",
            ha="center",
        )

    ax1.set_xlabel("Loss Rate (%)", fontsize=12)
    ax1.set_ylabel("Cumulative Probability", fontsize=12)
    ax1.set_title("Loss Rate CDF", fontsize=12, fontweight="bold")
    ax1.legend(fontsize=9, loc="lower right")
    ax1.set_xlim(left=-1)
    ax1.set_ylim(-0.02, 1.02)

    ax2 = axes[1]
    for g in groups:
        jitter = all_data[g]["jitter_ms"].values
        if len(jitter) == 0:
            continue
        sorted_jitter = np.sort(jitter)
        cdf = np.arange(1, len(sorted_jitter) + 1) / len(sorted_jitter)
        ax2.plot(
            sorted_jitter,
            cdf,
            color=POLICY_COLORS[g],
            linewidth=2,
            label=f"{POLICY_LABELS[g]} (n={len(sorted_jitter)})",
        )

    for g in groups:
        jitter = all_data[g]["jitter_ms"].values
        if len(jitter) == 0:
            continue
        p95 = np.percentile(jitter, 95)
        ax2.axvline(
            x=p95, color=POLICY_COLORS[g], linestyle="--", alpha=1.0, linewidth=2.5
        )
        ax2.text(
            p95 + 10,
            0.5,
            f"P95={p95:.1f}%",
            fontsize=11,
            fontweight="bold",
            color=POLICY_COLORS[g],
            rotation=90,
            va="center",
            ha="center",
        )

    ax2.set_xlabel("Jitter (ms)", fontsize=12)
    ax2.set_ylabel("Cumulative Probability", fontsize=12)
    ax2.set_title("Jitter CDF", fontsize=12, fontweight="bold")
    ax2.legend(fontsize=9, loc="lower right")
    # No grid
    ax2.set_xlim(left=-1)
    ax2.set_ylim(-0.02, 1.02)

    fig.suptitle(
        "CDF of Network Impairments Across All Flows & 30 Iterations\n"
        "(Steeper curve reaching Y=1.0 on the left = better QoS control)",
        fontsize=14,
        fontweight="bold",
        y=1.02,
    )
    fig.tight_layout()
    out = os.path.join(FIGURES_DIR, "policy_4_cdf.png")
    fig.savefig(out, dpi=300, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved: {out}")


def plot_weight_evolution():
    weights_path = os.path.join(DATA_DIR, "group_weights.csv")
    if not os.path.exists(weights_path):
        print(f"Warning: {weights_path} not found. Skipping weight evolution chart.")
        return
    df = pd.read_csv(weights_path)
    dpids = sorted(df["dpid"].unique())
    if not dpids:
        return

    fig, axes = plt.subplots(len(dpids), 1, figsize=(12, 2.5 * len(dpids)), sharex=True)
    if len(dpids) == 1:
        axes = [axes]

    min_ts = df["timestamp"].min()

    for i, (ax, dpid) in enumerate(zip(axes, dpids)):
        sub_df = df[df["dpid"] == dpid].sort_values("timestamp")
        t_rel = sub_df["timestamp"] - min_ts

        w_smooth = 5
        smoothed_w3 = (
            sub_df["port3_weight"].rolling(w_smooth, min_periods=1, center=True).mean()
        )
        smoothed_w4 = (
            sub_df["port4_weight"].rolling(w_smooth, min_periods=1, center=True).mean()
        )

        total_w = smoothed_w3 + smoothed_w4
        smoothed_w3 = (smoothed_w3 / total_w) * 100.0
        smoothed_w4 = (smoothed_w4 / total_w) * 100.0

        ax.stackplot(
            t_rel,
            smoothed_w3,
            smoothed_w4,
            labels=["Port 3", "Port 4"],
            colors=["#3498db", "#2ecc71"],
            alpha=0.85,
        )
        ax.set_ylabel(f"S{dpid} Weight (%)", fontsize=9)
        ax.set_ylim(0, 100)
        ax.grid(True, alpha=0.3)

    axes[-1].set_xlabel("Time (seconds)", fontsize=11)

    legend_elements = [
        Patch(facecolor="#3498db", alpha=0.85, label="Port 3"),
        Patch(facecolor="#2ecc71", alpha=0.85, label="Port 4"),
    ]
    fig.legend(
        handles=legend_elements,
        loc="upper center",
        bbox_to_anchor=(0.5, 0.95),
        ncol=2,
        fontsize=9,
        frameon=False,
    )

    fig.suptitle(
        "Dynamic Weight Evolution Stacked Area Chart\n"
        "(Port 3 & Port 4 dynamic splitting weights per Aggregation switch)",
        fontsize=13,
        fontweight="bold",
        y=0.99,
    )
    fig.tight_layout(rect=[0, 0, 1, 0.95])
    out = os.path.join(FIGURES_DIR, "policy_5_weight_evolution.png")
    fig.savefig(out, dpi=300, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved: {out}")


# ─────────────────────────────────────────────────────
# Chart 6: Control-Data Plane Co-evolution Plot (Dual-Axis Co-evolution)
# ─────────────────────────────────────────────────────
def plot_dual_axis_coevolution():
    weights_path = os.path.join(DATA_DIR, "group_weights.csv")
    traffic_path = os.path.join(DATA_DIR, "traffic_data.csv")
    if not (os.path.exists(weights_path) and os.path.exists(traffic_path)):
        print(
            "Warning: Missing weights or traffic files. Skipping dual-axis co-evolution."
        )
        return

    df_w = pd.read_csv(weights_path)
    df_t = pd.read_csv(traffic_path)

    dpid_target = 9
    df_w_sub = df_w[df_w["dpid"] == dpid_target].sort_values("timestamp")
    df_t_sub3 = df_t[
        (df_t["dpid"] == dpid_target) & (df_t["port_no"] == 3)
    ].sort_values("timestamp")
    df_t_sub4 = df_t[
        (df_t["dpid"] == dpid_target) & (df_t["port_no"] == 4)
    ].sort_values("timestamp")

    global_min_ts = min(df_w["timestamp"].min(), df_t["timestamp"].min())

    fig, axes = plt.subplots(1, 2, figsize=(16, 6.5))

    ax1 = axes[0]
    t_w = df_w_sub["timestamp"] - global_min_ts
    t_t3 = df_t_sub3["timestamp"] - global_min_ts

    def smooth(arr, w=5):
        if len(arr) < w:
            return arr
        return arr.rolling(w, min_periods=1, center=True).mean()

    color_w = "#3498db"
    color_u = "#e74c3c"

    ax1.plot(
        t_w,
        smooth(df_w_sub["port3_weight"]),
        color=color_w,
        linewidth=1.8,
        label="Port 3 Weight",
    )
    ax1.set_xlabel("Time (seconds)", fontsize=11)
    ax1.set_ylabel("Port Weight (%)", color=color_w, fontsize=11)
    ax1.tick_params(axis="y", labelcolor=color_w)
    ax1.set_ylim(-5, 105)

    ax1_twin = ax1.twinx()
    ax1_twin.plot(
        t_t3,
        smooth(df_t_sub3["utilization"]),
        color=color_u,
        linewidth=1.5,
        linestyle="--",
        label="Port 3 Utilization",
    )
    ax1_twin.set_ylabel("Link Utilization", color=color_u, fontsize=11)
    ax1_twin.tick_params(axis="y", labelcolor=color_u)
    ax1_twin.set_ylim(-0.05, 1.05)

    ax1.grid(True, alpha=0.3)
    ax1.set_title(
        f"Switch S{dpid_target} Port 3 Co-evolution", fontsize=12, fontweight="bold"
    )

    lines1, labels1 = ax1.get_legend_handles_labels()
    lines2, labels2 = ax1_twin.get_legend_handles_labels()
    ax1.legend(lines1 + lines2, labels1 + labels2, loc="upper left", fontsize=8.5)

    ax2 = axes[1]
    t_t4 = df_t_sub4["timestamp"] - global_min_ts

    ax2.plot(
        t_w,
        smooth(df_w_sub["port4_weight"]),
        color=color_w,
        linewidth=1.8,
        label="Port 4 Weight",
    )
    ax2.set_xlabel("Time (seconds)", fontsize=11)
    ax2.set_ylabel("Port Weight (%)", color=color_w, fontsize=11)
    ax2.tick_params(axis="y", labelcolor=color_w)
    ax2.set_ylim(-5, 105)

    ax2_twin = ax2.twinx()
    ax2_twin.plot(
        t_t4,
        smooth(df_t_sub4["utilization"]),
        color=color_u,
        linewidth=1.5,
        linestyle="--",
        label="Port 4 Utilization",
    )
    ax2_twin.set_ylabel("Link Utilization", color=color_u, fontsize=11)
    ax2_twin.tick_params(axis="y", labelcolor=color_u)
    ax2_twin.set_ylim(-0.05, 1.05)

    ax2.grid(True, alpha=0.3)
    ax2.set_title(
        f"Switch S{dpid_target} Port 4 Co-evolution", fontsize=12, fontweight="bold"
    )

    lines1, labels1 = ax2.get_legend_handles_labels()
    lines2, labels2 = ax2_twin.get_legend_handles_labels()
    ax2.legend(lines1 + lines2, labels1 + labels2, loc="upper left", fontsize=8.5)

    fig.suptitle(
        f"Control-Data Plane Co-evolution Plot for Switch S{dpid_target}\n"
        "(Port Weights vs. Actual Telemetry Link Utilization)",
        fontsize=14,
        fontweight="bold",
        y=1.01,
    )
    fig.tight_layout()
    out = os.path.join(FIGURES_DIR, "policy_6_dual_axis_coevolution.png")
    fig.savefig(out, dpi=300, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved: {out}")


# ─────────────────────────────────────────────────────
# Chart 7: Multi-Objective Pareto Trade-off Scatter Plot
# ─────────────────────────────────────────────────────
def plot_pareto_tradeoff():
    groups = ["base", "threshold", "predictive"]
    dfs = {}
    for g in groups:
        path = os.path.join(DATA_DIR, f"{g}_average_results.csv")
        if not os.path.exists(path):
            print(f"Warning: {path} not found. Skipping Pareto plot.")
            return
        dfs[g] = pd.read_csv(path)
        dfs[g]["flow"] = dfs[g]["flow"].replace({"Burst Flows": BURST_LABEL})

    fig, ax = plt.subplots(figsize=(10, 8))

    markers = {
        "base": "o",
        "threshold": "s",
        "predictive": "^",
    }

    for g in groups:
        df = dfs[g]
        ax.scatter(
            df["avg_loss_pct"],
            df["avg_bandwidth_mbps"],
            color=POLICY_COLORS[g],
            marker=markers[g],
            s=120,
            edgecolors="black",
            linewidths=0.8,
            alpha=0.85,
            label=POLICY_LABELS[g],
        )

        burst_row = df[df["flow"] == BURST_LABEL]
        if not burst_row.empty:
            ax.annotate(
                f"{POLICY_LABELS[g]} (Burst)",
                xy=(
                    burst_row["avg_loss_pct"].values[0],
                    burst_row["avg_bandwidth_mbps"].values[0],
                ),
                xytext=(
                    burst_row["avg_loss_pct"].values[0] + 1.2,
                    burst_row["avg_bandwidth_mbps"].values[0] - 0.03,
                ),
                fontsize=8.5,
                fontweight="bold",
                arrowprops=dict(arrowstyle="->", color=POLICY_COLORS[g], lw=1.2),
            )

    max_bw = max(dfs[g]["avg_bandwidth_mbps"].max() for g in groups)
    ideal_bw = max_bw

    ax.plot(
        0.0,
        ideal_bw,
        marker="*",
        color="gold",
        markersize=16,
        label="Ideal Point",
        markeredgecolor="black",
        zorder=5,
    )
    ax.text(
        1.2,
        ideal_bw - 0.02,
        f"Ideal Point\n(0% loss, max {ideal_bw:.2f} Mbps)",
        color="#b7950b",
        fontsize=9.5,
        fontweight="bold",
    )

    ax.set_xlabel("Average Loss Rate (%)", fontsize=12)
    ax.set_ylabel("Average Throughput (Mbps)", fontsize=12)
    ax.set_ylim(-0.05, ideal_bw * 1.1)
    ax.set_title(
        "Multi-Objective Pareto Trade-off Scatter Plot\n"
        "(Throughput vs. Loss Rate across all 10 flows under three policies)",
        fontsize=13,
        fontweight="bold",
    )
    ax.grid(True, alpha=0.3)
    ax.legend(loc="lower left", fontsize=10)

    fig.tight_layout()
    out = os.path.join(FIGURES_DIR, "policy_7_pareto_tradeoff.png")
    fig.savefig(out, dpi=300, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved: {out}")


def plot_flow_fairness_radar():
    groups = ["base", "threshold", "predictive"]
    dfs = {}
    for g in groups:
        path = os.path.join(DATA_DIR, f"{g}_average_results.csv")
        if not os.path.exists(path):
            print(f"Warning: {path} not found. Skipping radar chart.")
            return
        dfs[g] = pd.read_csv(path)
        dfs[g]["flow"] = dfs[g]["flow"].replace({"Burst Flows": BURST_LABEL})

    all_flows = [f"Flow {i}" for i in range(1, 10)]
    all_flows.append(BURST_LABEL)

    n_flows = len(all_flows)
    angles = np.linspace(0, 2 * np.pi, n_flows, endpoint=False).tolist()
    angles += angles[:1]

    fig, ax = plt.subplots(figsize=(9, 9), subplot_kw=dict(projection="polar"))

    for g in groups:
        df = dfs[g]
        values = []
        for f in all_flows:
            row = df[df["flow"] == f]
            raw_val = row["avg_bandwidth_mbps"].values[0] if not row.empty else 0

            norm_factor = 1.5 if f == BURST_LABEL else 0.5
            norm_val = raw_val / norm_factor
            values.append(norm_val)

        values += values[:1]

        ax.plot(
            angles, values, color=POLICY_COLORS[g], linewidth=2, label=POLICY_LABELS[g]
        )
        ax.fill(angles, values, color=POLICY_COLORS[g], alpha=0.15)

    ax.set_theta_offset(np.pi / 2)
    ax.set_theta_direction(-1)

    ax.set_xticks(angles[:-1])
    ax.set_xticklabels(all_flows, fontsize=10)

    ax.set_ylim(0.0, 1.05)
    ax.set_rlabel_position(180)

    ax.set_title(
        "Throughput Fairness Radar Chart (Normalized by per‑flow capacity)",
        fontsize=14,
        fontweight="bold",
        pad=20,
        y=1.10,
    )

    ax.legend(
        loc="upper center",
        bbox_to_anchor=(0.5, 1.10),
        ncol=3,
        fontsize=10,
        frameon=False,
    )

    fig.tight_layout()
    out = os.path.join(FIGURES_DIR, "policy_8_flow_fairness_radar.png")
    fig.savefig(out, dpi=300, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved: {out}")


if __name__ == "__main__":
    os.makedirs(FIGURES_DIR, exist_ok=True)

    print("[1/7] Plotting grouped bar chart (core metrics)...")
    plot_grouped_bar()

    print("[2/7] Plotting box plot (robustness & stability)...")
    plot_box()

    print("[3/7] Plotting CDF chart (network impairments)...")
    plot_cdf()

    print("[4/7] Plotting dynamic weight evolution stacks...")
    plot_weight_evolution()

    print("[5/7] Plotting control-data co-evolution curves...")
    plot_dual_axis_coevolution()

    print("[6/7] Plotting multi-objective pareto trade-offs...")
    plot_pareto_tradeoff()

    print("[7/7] Plotting flow fairness radar...")
    plot_flow_fairness_radar()

    print("\nAll policy comparison charts saved to figures/")
