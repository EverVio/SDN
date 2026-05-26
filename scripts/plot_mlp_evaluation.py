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

    # 自动寻找最具代表性（三链路平均方差最大）的 15 分钟微观片段 (每分钟约 120 个点)
    window_len = 15 * 120
    n_samples = len(t_min)
    if n_samples > window_len:
        max_var = -1
        best_start = 0
        for start_idx in range(0, n_samples - window_len, 60):
            end_idx = start_idx + window_len
            total_var = 0.0
            for name, idx in selected.items():
                if idx is not None:
                    total_var += np.var(Y_true[start_idx:end_idx, idx])
            if total_var > max_var:
                max_var = total_var
                best_start = start_idx
        best_end = best_start + window_len
        t_plot = t_min[best_start:best_end]
        Y_true_plot = Y_true[best_start:best_end, :]
        Y_pred_plot = Y_pred[best_start:best_end, :]
        span_str = f"Time window: {t_min[best_start]:.1f} - {t_min[best_end-1]:.1f} min"
    else:
        t_plot = t_min
        Y_true_plot = Y_true
        Y_pred_plot = Y_pred
        span_str = f"Test span: {t_min[-1]:.1f} min"

    fig, axes = plt.subplots(3, 1, figsize=(16, 12), sharex=False)
    colors = {"Core": "#e74c3c", "Agg": "#e67e22", "Edge": "#2ecc71"}

    for ax, (layer_name, idx) in zip(axes, selected.items()):
        if idx is None:
            ax.set_visible(False)
            continue
        dpid, port_no = link_keys[idx]
        label = make_link_label(dpid, port_no)

        ax.plot(t_plot, Y_true_plot[:, idx], color=colors[layer_name],
                linewidth=1.2, alpha=0.85, label=f"True ({label})")
        ax.plot(t_plot, Y_pred_plot[:, idx], color=colors[layer_name],
                linewidth=1.2, alpha=0.85, linestyle="--", label=f"Predicted ({label})")

        # 填充误差区域
        ax.fill_between(
            t_plot,
            Y_true_plot[:, idx], Y_pred_plot[:, idx],
            alpha=0.12, color=colors[layer_name],
        )

        # 计算该链路在该局部窗口的 RMSE
        rmse = np.sqrt(np.mean((Y_true_plot[:, idx] - Y_pred_plot[:, idx]) ** 2))
        ax.text(
            0.02, 0.92, f"{layer_name} Layer  |  {label}\nRMSE = {rmse:.4f}",
            transform=ax.transAxes, fontsize=10,
            bbox=dict(boxstyle="round,pad=0.3", facecolor="white", edgecolor="gray", alpha=0.9),
        )

        ax.set_ylabel("Utilization", fontsize=11)
        ax.set_ylim(-0.05, 1.05)
        ax.legend(loc="upper right", fontsize=9, framealpha=0.9)
        ax.grid(True, alpha=0.3)
        ax.set_xlabel("Time (minutes)", fontsize=11)

    fig.suptitle(
        "Micro-Window Predictive Tracking: True vs. Predicted Utilization\n"
        f"(15-Minute Dynamic Fragment, {span_str})",
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

    # 仅展示 Top-20 错误链路，其余归入 "Others (Mean)"
    top_n = 20
    if len(df_sorted) > top_n:
        df_top = df_sorted.iloc[:top_n].copy()
        df_others = df_sorted.iloc[top_n:]
        others_mean = df_others["RMSE"].mean()
        
        # 追加 Others 虚拟行
        others_row = pd.DataFrame([{
            "dpid": 0, "port_no": 0, "RMSE": others_mean, "MAE": 0.0, "R2": 0.0,
            "label": f"Others (n={len(df_others)})"
        }])
        df_plot = pd.concat([df_top, others_row], ignore_index=True)
        colors = [layer_color(int(row["dpid"])) if int(row["dpid"]) > 0 else "#95a5a6" for _, row in df_plot.iterrows()]
    else:
        df_plot = df_sorted
        colors = [layer_color(int(row["dpid"])) for _, row in df_plot.iterrows()]

    fig, ax = plt.subplots(figsize=(14, 7))

    x = np.arange(len(df_plot))
    bars = ax.bar(x, df_plot["RMSE"], color=colors, alpha=0.85, edgecolor="white", linewidth=0.3)

    # 在最高的几根柱子和 Others 柱子上面标注数值
    for i in range(len(df_plot)):
        if i < 8 or df_plot.iloc[i]["label"].startswith("Others"):
            ax.text(
                i, df_plot.iloc[i]["RMSE"] + 0.003,
                f'{df_plot.iloc[i]["RMSE"]:.3f}',
                ha="center", va="bottom", fontsize=7.5, color="#333", fontweight="bold"
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
        Patch(facecolor="#95a5a6", alpha=0.85, label="Others (Grouped Mean)"),
    ]
    ax.legend(handles=legend_elements, loc="upper right", fontsize=10)

    ax.set_xticks(x)
    ax.set_xticklabels(df_plot["label"], rotation=45, ha="right", fontsize=8.5)
    ax.set_ylabel("RMSE", fontsize=12)
    ax.set_xlabel("Backbone Link (sorted by RMSE descending)", fontsize=12)
    ax.set_title(
        "Per-Link Prediction Error Distribution (Spatial)\n"
        f"(Top-20 Highest Error Links individually + Grouped Mean, total {len(df_sorted)} links)",
        fontsize=13, fontweight="bold",
    )
    ax.grid(True, axis="y", alpha=0.3)

    fig.tight_layout()
    out_path = os.path.join(FIGURES_DIR, "7_spatial_error_distribution.png")
    fig.savefig(out_path, dpi=180, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved: {out_path}")


# ─────────────────────────────────────────────────────
# 图表 8: 拓扑层级预测误差分组小提琴图 (Hierarchical Prediction Error Distribution)
# ─────────────────────────────────────────────────────
def plot_hierarchical_error(metrics_csv):
    df = pd.read_csv(metrics_csv)
    
    # 划分层级
    def get_layer(dpid):
        if dpid > 16:
            return "Core"
        elif dpid >= 9:
            return "Aggregation"
        else:
            return "Edge"
            
    df["layer"] = df["dpid"].apply(get_layer)
    
    # 获取各个层级的 RMSE 数据
    layers = ["Core", "Aggregation", "Edge"]
    data_by_layer = [df[df["layer"] == l]["RMSE"].values for l in layers]
    
    # 检查是否有数据，防止空数组报错
    for i, l in enumerate(layers):
        if len(data_by_layer[i]) == 0:
            print(f"Warning: No data for layer {l}. Skipping violin plot.")
            return

    fig, ax = plt.subplots(figsize=(10, 7))
    
    # 绘制小提琴图
    parts = ax.violinplot(
        data_by_layer,
        showmeans=False,
        showmedians=False,
        showextrema=False,
        widths=0.6
    )
    
    # 设置小提琴的填充颜色和样式
    colors = ["#e74c3c", "#e67e22", "#2ecc71"]
    for pc, color in zip(parts["bodies"], colors):
        pc.set_facecolor(color)
        pc.set_edgecolor("black")
        pc.set_alpha(0.6)
        pc.set_linewidth(1.0)
        
    # 在小提琴内部绘制精美箱线图
    bp = ax.boxplot(
        data_by_layer,
        patch_artist=True,
        widths=0.15,
        medianprops=dict(color="black", linewidth=1.5),
        whiskerprops=dict(color="gray", linewidth=1.0),
        capprops=dict(color="gray", linewidth=1.0),
        flierprops=dict(marker="o", markerfacecolor="gray", markersize=4, alpha=0.5)
    )
    for patch, color in zip(bp["boxes"], colors):
        patch.set_facecolor(color)
        patch.set_alpha(0.85)
        
    # 添加带随机抖动的数据点（Jitter Scatter）
    for i, layer_data in enumerate(data_by_layer):
        x_pos = np.random.normal(i + 1, 0.04, size=len(layer_data))
        ax.scatter(
            x_pos,
            layer_data,
            color=colors[i],
            edgecolors="black",
            linewidths=0.5,
            s=25,
            alpha=0.6,
            zorder=3
        )
        
    ax.set_xticks([1, 2, 3])
    ax.set_xticklabels([f"{l}\n(n={len(d)})" for l, d in zip(layers, data_by_layer)], fontsize=11, fontweight="bold")
    ax.set_ylabel("RMSE Prediction Error", fontsize=12)
    ax.set_title(
        "Hierarchical Prediction Error Distribution (Violin Plot)\n"
        "(Comparison of model performance across Core, Aggregation, and Edge layers)",
        fontsize=13, fontweight="bold"
    )
    ax.grid(True, axis="y", alpha=0.3)
    
    # 标注各层级的平均 RMSE (Align horizontally at 90% of the maximum Y-limit to avoid outliers distortion)
    ymin, ymax = ax.get_ylim()
    y_pos = ymin + (ymax - ymin) * 0.9
    for i, layer_data in enumerate(data_by_layer):
        mean_val = np.mean(layer_data)
        ax.text(
            i + 1, y_pos,
            f"Mean: {mean_val:.4f}",
            ha="center", va="bottom", fontsize=9.5, fontweight="bold", color=colors[i]
        )
        
    fig.tight_layout()
    out = os.path.join(FIGURES_DIR, "8_hierarchical_error_distribution.png")
    fig.savefig(out, dpi=180, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved: {out}")


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

    print("\n[3/8] Plotting training convergence...")
    plot_convergence(history)

    print("\n[4/8] Plotting true vs predicted scatter...")
    plot_scatter(pred_data)

    print("\n[5/8] Plotting single-link tracking...")
    plot_tracking(pred_data)

    print("\n[6/8] Plotting residual distribution...")
    plot_residual_histogram(pred_data)

    print("\n[7/8] Plotting spatial error distribution...")
    plot_spatial_error(metrics_csv)

    print("\n[8/8] Plotting hierarchical error violin plot...")
    plot_hierarchical_error(metrics_csv)

    print("\nAll 6 evaluation charts saved to figures/")
