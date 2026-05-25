"""
维度二：MLP 预测模型训练质量与泛化误差多维评估
- 图表 3: 模型训练与验证收敛曲线图
- 图表 4: 真实值 vs 预测值拟合优度散点图
- 图表 5: 单链路流量趋势预测追踪图
- 图表 6: 预测残差空间分布直方图
- 图表 7: 拓扑空间误差分布条形图
"""

import os
import sys
import joblib
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import MaxNLocator
from scipy.stats import gaussian_kde
import datetime

FIGURES_DIR = os.path.join(os.path.dirname(__file__), "..", "figures")
DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")


def make_link_label(dpid, port_no):
    if dpid <= 8:
        prefix = "E"
    elif dpid <= 16:
        prefix = "A"
    else:
        prefix = "C"
    return f"{prefix}{dpid}-P{port_no}"


def format_timestamp(ts, _=None):
    return datetime.datetime.fromtimestamp(ts).strftime("%H:%M:%S")


# ─────────────────────────────────────────────────────
# 图表 3: 模型训练与验证收敛曲线图
# ─────────────────────────────────────────────────────
def plot_convergence(history):
    loss_curve = np.array(history["loss_curve"])
    val_scores = np.array(history["validation_scores"])
    epochs = np.arange(1, len(loss_curve) + 1)

    fig, ax1 = plt.subplots(figsize=(12, 6))

    color_loss = "#e74c3c"
    color_val = "#2980b9"

    ax1.set_xlabel("Epoch", fontsize=12)
    ax1.set_ylabel("Training Loss (scaled MSE)", fontsize=12, color=color_loss)
    ax1.plot(epochs, loss_curve, color=color_loss, linewidth=1.5, alpha=0.85,
             label="Training Loss")
    ax1.tick_params(axis="y", labelcolor=color_loss)
    ax1.xaxis.set_major_locator(MaxNLocator(integer=True))

    ax2 = ax1.twinx()
    ax2.set_ylabel("Validation R² Score", fontsize=12, color=color_val)
    ax2.plot(epochs, val_scores, color=color_val, linewidth=1.5, alpha=0.85,
             linestyle="--", label="Validation Score")
    ax2.tick_params(axis="y", labelcolor=color_val)

    # 标记最佳验证得分位置
    best_epoch = np.argmax(val_scores) + 1
    best_score = val_scores.max()
    ax2.axvline(x=best_epoch, color=color_val, linestyle=":", alpha=0.5)
    ax2.annotate(
        f"Best R²={best_score:.4f}\n@ epoch {best_epoch}",
        xy=(best_epoch, best_score),
        xytext=(best_epoch + len(loss_curve) * 0.08, best_score - 0.02),
        fontsize=9, color=color_val,
        arrowprops=dict(arrowstyle="->", color=color_val, lw=1.2),
        bbox=dict(boxstyle="round,pad=0.3", facecolor="white", edgecolor=color_val, alpha=0.9),
    )

    # 合并图例
    lines1, labels1 = ax1.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax1.legend(lines1 + lines2, labels1 + labels2, loc="center right", fontsize=10)

    # 标注早停区域
    total_epochs = len(loss_curve)
    ax1.axvspan(best_epoch, total_epochs, alpha=0.06, color="gray")
    ax1.text(
        (best_epoch + total_epochs) / 2, loss_curve.max() * 0.95,
        "Post-best\n(no improvement)", ha="center", fontsize=8, color="gray",
    )

    ax1.set_title(
        "MLP Training Convergence Curve\n"
        f"(Adam optimizer, adaptive LR, early stopping @ {total_epochs} epochs)",
        fontsize=13, fontweight="bold",
    )
    ax1.grid(True, alpha=0.3)

    fig.tight_layout()
    out_path = os.path.join(FIGURES_DIR, "3_training_convergence.png")
    fig.savefig(out_path, dpi=180, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved: {out_path}")


# ─────────────────────────────────────────────────────
# 图表 4: 真实值 vs 预测值拟合优度散点图
# ─────────────────────────────────────────────────────
def plot_scatter(pred_data):
    Y_true = pred_data["Y_true"]
    Y_pred = pred_data["Y_pred"]

    # Flatten
    y_true_flat = Y_true.ravel()
    y_pred_flat = Y_pred.ravel()

    # 计算全局 R²
    ss_res = np.sum((y_true_flat - y_pred_flat) ** 2)
    ss_tot = np.sum((y_true_flat - np.mean(y_true_flat)) ** 2)
    r2 = 1 - ss_res / ss_tot

    fig, ax = plt.subplots(figsize=(9, 9))

    # Hexbin 防止过密
    hb = ax.hexbin(
        y_true_flat, y_pred_flat,
        gridsize=80, cmap="YlOrRd", mincnt=1,
        linewidths=0.2, edgecolors="none",
    )
    cb = fig.colorbar(hb, ax=ax, shrink=0.8, pad=0.02)
    cb.set_label("Sample Count per Hexbin", fontsize=10)

    # 对角线 y=x
    ax.plot([0, 1], [0, 1], "k--", linewidth=1.5, alpha=0.7, label="Perfect Prediction (y=x)")

    # 高亮高负载区域 [0.7, 1.0]
    ax.axvspan(0.7, 1.0, alpha=0.06, color="red")
    ax.text(0.85, 0.05, "High Load\nRegion", ha="center", fontsize=9, color="red", alpha=0.7)

    # 在图上标注 R²
    ax.text(
        0.05, 0.92, f"R² = {r2:.4f}\nn = {len(y_true_flat):,}",
        transform=ax.transAxes, fontsize=12,
        bbox=dict(boxstyle="round,pad=0.4", facecolor="white", edgecolor="gray", alpha=0.9),
    )

    ax.set_xlim(-0.02, 1.02)
    ax.set_ylim(-0.02, 1.02)
    ax.set_xlabel("True Link Utilization", fontsize=12)
    ax.set_ylabel("Predicted Link Utilization", fontsize=12)
    ax.set_aspect("equal")
    ax.legend(loc="lower right", fontsize=10)
    ax.set_title(
        "True vs. Predicted Link Utilization\n"
        f"(Hexbin Plot, {Y_true.shape[0]} test samples x {Y_true.shape[1]} links)",
        fontsize=13, fontweight="bold",
    )
    ax.grid(True, alpha=0.2)

    fig.tight_layout()
    out_path = os.path.join(FIGURES_DIR, "4_true_vs_predicted_scatter.png")
    fig.savefig(out_path, dpi=180, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved: {out_path}")


# ─────────────────────────────────────────────────────
# 图表 5: 单链路流量趋势预测追踪图
# ─────────────────────────────────────────────────────
def plot_tracking(pred_data):
    Y_true = pred_data["Y_true"]
    Y_pred = pred_data["Y_pred"]
    timestamps = pred_data["timestamps"]
    link_keys = pred_data["link_keys"]

    # 选取链路策略：找方差最大的 Core / Agg / Edge 链路各一条
    variances = [(np.var(Y_true[:, i]), i) for i in range(len(link_keys))]
    variances.sort(reverse=True)

    # 按拓扑层级分桶
    selected = {"Core": None, "Agg": None, "Edge": None}
    for _, idx in variances:
        dpid, port_no = link_keys[idx]
        if dpid > 16 and selected["Core"] is None:
            selected["Core"] = idx
        elif 9 <= dpid <= 16 and selected["Agg"] is None:
            selected["Agg"] = idx
        elif dpid <= 8 and selected["Edge"] is None:
            selected["Edge"] = idx
        if all(v is not None for v in selected.values()):
            break

    t_min = (timestamps - timestamps[0]) / 60.0

    fig, axes = plt.subplots(3, 1, figsize=(16, 12), sharex=True)
    colors = {"Core": "#e74c3c", "Agg": "#e67e22", "Edge": "#2ecc71"}

    for ax, (layer_name, idx) in zip(axes, selected.items()):
        if idx is None:
            ax.set_visible(False)
            continue
        dpid, port_no = link_keys[idx]
        label = make_link_label(dpid, port_no)

        ax.plot(t_min, Y_true[:, idx], color=colors[layer_name],
                linewidth=0.8, alpha=0.8, label=f"True ({label})")
        ax.plot(t_min, Y_pred[:, idx], color=colors[layer_name],
                linewidth=0.8, alpha=0.8, linestyle="--", label=f"Predicted ({label})")

        # 填充误差区域
        ax.fill_between(
            t_min,
            Y_true[:, idx], Y_pred[:, idx],
            alpha=0.12, color=colors[layer_name],
        )

        # 计算该链路 RMSE
        rmse = np.sqrt(np.mean((Y_true[:, idx] - Y_pred[:, idx]) ** 2))
        ax.text(
            0.02, 0.92, f"{layer_name} Layer  |  {label}\nRMSE = {rmse:.4f}",
            transform=ax.transAxes, fontsize=10,
            bbox=dict(boxstyle="round,pad=0.3", facecolor="white", edgecolor="gray", alpha=0.9),
        )

        ax.set_ylabel("Utilization", fontsize=11)
        ax.set_ylim(-0.05, 1.05)
        ax.legend(loc="upper right", fontsize=9, framealpha=0.9)
        ax.grid(True, alpha=0.3)

    axes[-1].set_xlabel("Time (minutes)", fontsize=12)
    fig.suptitle(
        "Single-Link Predictive Tracking: True vs. Predicted Utilization\n"
        f"(PREDICTION_STEP=2, TARGET_WINDOW=3, Test span: {t_min[-1]:.1f} min)",
        fontsize=13, fontweight="bold", y=0.98,
    )

    fig.tight_layout(rect=[0, 0, 1, 0.96])
    out_path = os.path.join(FIGURES_DIR, "5_single_link_tracking.png")
    fig.savefig(out_path, dpi=180, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved: {out_path}")


# ─────────────────────────────────────────────────────
# 图表 6: 预测残差空间分布直方图
# ─────────────────────────────────────────────────────
def plot_residual_histogram(pred_data):
    Y_true = pred_data["Y_true"]
    Y_pred = pred_data["Y_pred"]

    residuals = (Y_true - Y_pred).ravel()

    fig, ax = plt.subplots(figsize=(12, 6))

    # 直方图
    counts, bins, patches = ax.hist(
        residuals, bins=80, density=True,
        color="#3498db", alpha=0.6, edgecolor="white", linewidth=0.3,
        label="Residual Distribution",
    )

    # KDE 曲线
    kde = gaussian_kde(residuals, bw_method=0.15)
    x_kde = np.linspace(residuals.min(), residuals.max(), 500)
    ax.plot(x_kde, kde(x_kde), color="#e74c3c", linewidth=2, label="KDE Estimate")

    # 统计标注
    mu = np.mean(residuals)
    sigma = np.std(residuals)
    skew = float(pd.Series(residuals).skew())
    pct_extreme = np.mean(np.abs(residuals) > 0.3) * 100

    stats_text = (
        f"μ = {mu:+.4f}\n"
        f"σ = {sigma:.4f}\n"
        f"Skewness = {skew:+.4f}\n"
        f"|residual| > 0.3: {pct_extreme:.2f}%"
    )
    ax.text(
        0.97, 0.95, stats_text,
        transform=ax.transAxes, fontsize=10, va="top", ha="right",
        bbox=dict(boxstyle="round,pad=0.4", facecolor="white", edgecolor="gray", alpha=0.9),
    )

    # 零线与均值线
    ax.axvline(x=0, color="black", linestyle="--", linewidth=1, alpha=0.5, label="Zero Error")
    ax.axvline(x=mu, color="#e74c3c", linestyle=":", linewidth=1.5, alpha=0.7, label=f"Mean (μ={mu:+.4f})")

    # 标注极端残差区间
    ax.axvspan(0.3, residuals.max(), alpha=0.06, color="red")
    ax.axvspan(residuals.min(), -0.3, alpha=0.06, color="red")

    # 偏度方向标注
    if abs(skew) > 0.05:
        direction = "underestimate" if skew > 0 else "overestimate"
        ax.annotate(
            f"Model tends to\n{direction}\n(skew={skew:+.3f})",
            xy=(mu, kde(mu)[0]),
            xytext=(mu + (0.15 if skew > 0 else -0.15), kde(mu)[0] * 0.6),
            fontsize=9,
            arrowprops=dict(arrowstyle="->", color="#e74c3c", lw=1.2),
            bbox=dict(boxstyle="round,pad=0.3", facecolor="#fff5f5", edgecolor="#e74c3c", alpha=0.9),
        )

    ax.set_xlabel("Residual (True − Predicted)", fontsize=12)
    ax.set_ylabel("Probability Density", fontsize=12)
    ax.legend(loc="upper left", fontsize=9)
    ax.set_title(
        "Prediction Residual Distribution\n"
        f"({len(residuals):,} data points across all test samples and links)",
        fontsize=13, fontweight="bold",
    )
    ax.grid(True, alpha=0.2)

    fig.tight_layout()
    out_path = os.path.join(FIGURES_DIR, "6_residual_distribution.png")
    fig.savefig(out_path, dpi=180, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved: {out_path}")


# ─────────────────────────────────────────────────────
# 图表 7: 拓扑空间误差分布条形图
# ─────────────────────────────────────────────────────
def plot_spatial_error(metrics_csv):
    df = pd.read_csv(metrics_csv)
    df["label"] = df.apply(lambda r: make_link_label(int(r["dpid"]), int(r["port_no"])), axis=1)

    # 按 RMSE 降序排列
    df_sorted = df.sort_values("RMSE", ascending=False).reset_index(drop=True)

    # 拓扑层级颜色映射
    def layer_color(dpid):
        if dpid > 16:
            return "#e74c3c"   # Core - red
        elif dpid >= 9:
            return "#e67e22"   # Agg - orange
        else:
            return "#2ecc71"   # Edge - green

    colors = [layer_color(int(row["dpid"])) for _, row in df_sorted.iterrows()]

    fig, ax = plt.subplots(figsize=(18, 7))

    x = np.arange(len(df_sorted))
    bars = ax.bar(x, df_sorted["RMSE"], color=colors, alpha=0.85, edgecolor="white", linewidth=0.3)

    # 在最高的几根柱子上标注数值
    for i in range(min(8, len(df_sorted))):
        ax.text(
            i, df_sorted.iloc[i]["RMSE"] + 0.003,
            f'{df_sorted.iloc[i]["RMSE"]:.3f}',
            ha="center", va="bottom", fontsize=7, color="#333",
        )

    # 全局均值线
    global_mean_rmse = df_sorted["RMSE"].mean()
    ax.axhline(y=global_mean_rmse, color="#3498db", linestyle="--", linewidth=1.2, alpha=0.7,
               label=f"Mean RMSE = {global_mean_rmse:.4f}")

    # 拓扑层级图例
    from matplotlib.patches import Patch
    legend_elements = [
        Patch(facecolor="#e74c3c", alpha=0.85, label="Core (dpid>16)"),
        Patch(facecolor="#e67e22", alpha=0.85, label="Aggregation (dpid 9-16)"),
        Patch(facecolor="#2ecc71", alpha=0.85, label="Edge (dpid≤8)"),
    ]
    ax.legend(handles=legend_elements, loc="upper right", fontsize=10)

    ax.set_xticks(x)
    ax.set_xticklabels(df_sorted["label"], rotation=65, ha="right", fontsize=7)
    ax.set_ylabel("RMSE", fontsize=12)
    ax.set_xlabel("Backbone Link (sorted by RMSE descending)", fontsize=12)
    ax.set_title(
        "Per-Link Prediction Error Distribution (Spatial)\n"
        f"({len(df_sorted)} backbone links, Fat-Tree k=4)",
        fontsize=13, fontweight="bold",
    )
    ax.grid(True, axis="y", alpha=0.3)

    fig.tight_layout()
    out_path = os.path.join(FIGURES_DIR, "7_spatial_error_distribution.png")
    fig.savefig(out_path, dpi=180, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved: {out_path}")


if __name__ == "__main__":
    os.makedirs(FIGURES_DIR, exist_ok=True)

    print("Loading training history...")
    history = joblib.load(os.path.join(DATA_DIR, "viz_training_history.pkl"))
    print(f"  loss_curve: {len(history['loss_curve'])} epochs, "
          f"validation_scores: {len(history['validation_scores'])} epochs")

    print("Loading predictions...")
    pred_data = joblib.load(os.path.join(DATA_DIR, "viz_predictions.pkl"))
    print(f"  Y_true: {pred_data['Y_true'].shape}, Y_pred: {pred_data['Y_pred'].shape}")

    metrics_csv = os.path.join(DATA_DIR, "viz_per_link_metrics.csv")
    df_metrics = pd.read_csv(metrics_csv)
    print(f"  Per-link metrics: {len(df_metrics)} links")

    print("\n[3/7] Plotting training convergence...")
    plot_convergence(history)

    print("\n[4/7] Plotting true vs predicted scatter...")
    plot_scatter(pred_data)

    print("\n[5/7] Plotting single-link tracking...")
    plot_tracking(pred_data)

    print("\n[6/7] Plotting residual distribution...")
    plot_residual_histogram(pred_data)

    print("\n[7/7] Plotting spatial error distribution...")
    plot_spatial_error(metrics_csv)

    print("\nAll 5 evaluation charts saved to figures/")
