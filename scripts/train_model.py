"""
随机森林模型训练（Fat-Tree per-link）
功能：
  1. 读取 data/training_features.csv（由 collect_training_data.py + assemble_features.py 生成）
  2. 按链路标签分别建模（edge_s*, agg_s*, core_s* 等 Fat-Tree 链路）
  3. 使用 GridSearchCV + TimeSeriesSplit 进行超参数调优
  4. 导出模型至 models/model_link_*.pkl
  5. 输出模型评估摘要 CSV

前置步骤：
  1. sudo python3 scripts/collect_training_data.py   # 采集真实 Mininet 数据
  2. cd scripts && python3 assemble_features.py       # 组装特征
  3. cd scripts && python3 train_model.py             # 训练模型（本脚本）
"""

import os
import warnings
import numpy as np
import pandas as pd
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from sklearn.ensemble import RandomForestRegressor
from sklearn.model_selection import TimeSeriesSplit, GridSearchCV, learning_curve
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
import joblib

warnings.filterwarnings("ignore")

# ------------------------------ 配置参数 ------------------------------
OUTPUT_MODEL_DIR = "../models"
FIGURES_DIR = "../figures"
SUMMARY_CSV = "../data/model_evaluation_summary.csv"
TRAINING_CSV = "../data/training_features.csv"
RANDOM_STATE = 42

# 超参数搜索空间
PARAM_GRID = {
    "n_estimators": [30, 50, 100],
    "max_depth": [3, 5, 8, None],
    "min_samples_leaf": [1, 3, 5],
}

os.makedirs(OUTPUT_MODEL_DIR, exist_ok=True)
os.makedirs(FIGURES_DIR, exist_ok=True)


# ======================== 数据加载 ========================


def load_data(csv_path):
    """Load feature file, return DataFrame"""
    df = pd.read_csv(csv_path)
    feat_cols = [c for c in df.columns if c.startswith("feat_")]
    return df, feat_cols


def cross_validate_rf(X, y, n_splits=5, link_name=""):
    """使用 TimeSeriesSplit 交叉验证评估 RF 稳定性"""
    tscv = TimeSeriesSplit(n_splits=n_splits)
    rf = RandomForestRegressor(
        n_estimators=50, max_depth=5, random_state=RANDOM_STATE, n_jobs=-1
    )

    fold_metrics = []
    for fold, (train_idx, val_idx) in enumerate(tscv.split(X), 1):
        rf.fit(X[train_idx], y[train_idx])
        y_pred = rf.predict(X[val_idx])
        mae = mean_absolute_error(y[val_idx], y_pred)
        rmse = np.sqrt(mean_squared_error(y[val_idx], y_pred))
        r2 = r2_score(y[val_idx], y_pred)
        fold_metrics.append({"fold": fold, "MAE": mae, "RMSE": rmse, "R²": r2})

    metrics_df = pd.DataFrame(fold_metrics)
    print(f"\n  {link_name} — {n_splits}-Fold TimeSeriesSplit CV:")
    print(f"    MAE:  {metrics_df['MAE'].mean():.4f} ± {metrics_df['MAE'].std():.4f}")
    print(f"    RMSE: {metrics_df['RMSE'].mean():.4f} ± {metrics_df['RMSE'].std():.4f}")
    print(f"    R²:   {metrics_df['R²'].mean():.4f} ± {metrics_df['R²'].std():.4f}")
    return metrics_df


# ======================== 可视化函数 ========================


def plot_cv_scores(cv_df, link_name):
    """绘制交叉验证分数柱状图"""
    _, axes = plt.subplots(1, 3, figsize=(15, 4))

    for ax, metric, color in zip(
        axes, ["MAE", "RMSE", "R²"], ["steelblue", "coral", "seagreen"]
    ):
        ax.bar(cv_df["fold"], cv_df[metric], alpha=0.7, color=color)
        ax.axhline(y=cv_df[metric].mean(), color="r", linestyle="--", alpha=0.7)
        ax.set_xlabel("Fold")
        ax.set_ylabel(metric)
        ax.set_title(f"{link_name} — CV {metric}")
        ax.set_xticks(cv_df["fold"])

    plt.tight_layout()
    filepath = os.path.join(FIGURES_DIR, f"cv_scores_{link_name}.png")
    plt.savefig(filepath, dpi=150)
    plt.close()
    print(f"  CV 分数图表已保存: {filepath}")


def plot_learning_curve(estimator, X, y, link_name):
    """绘制学习曲线：训练集大小 vs 模型性能"""
    train_sizes, train_scores, val_scores = learning_curve(
        estimator,
        X,
        y,
        cv=TimeSeriesSplit(n_splits=3),
        scoring="neg_mean_absolute_error",
        train_sizes=np.linspace(0.3, 1.0, 6),
        n_jobs=-1,
    )

    train_mean = -train_scores.mean(axis=1)
    train_std = train_scores.std(axis=1)
    val_mean = -val_scores.mean(axis=1)
    val_std = val_scores.std(axis=1)

    plt.figure(figsize=(8, 5))
    plt.fill_between(
        train_sizes, train_mean - train_std, train_mean + train_std, alpha=0.1, color="b"
    )
    plt.fill_between(
        train_sizes, val_mean - val_std, val_mean + val_std, alpha=0.1, color="r"
    )
    plt.plot(train_sizes, train_mean, "o-", color="b", label="Training MAE")
    plt.plot(train_sizes, val_mean, "o-", color="r", label="Validation MAE")
    plt.xlabel("Training set size")
    plt.ylabel("MAE")
    plt.title(f"{link_name} — Learning Curve")
    plt.legend()
    plt.grid(alpha=0.3)
    plt.tight_layout()
    filepath = os.path.join(FIGURES_DIR, f"learning_curve_{link_name}.png")
    plt.savefig(filepath, dpi=150)
    plt.close()
    print(f"  学习曲线已保存: {filepath}")


def plot_predictions(y_true, y_pred, link_name, y_pred_std=None):
    """绘制预测值 vs 真实值散点图（可选置信区间）"""
    plt.figure(figsize=(6, 6))
    plt.scatter(y_true, y_pred, alpha=0.7, edgecolors="k", linewidth=0.3, s=30)
    min_val = min(y_true.min(), y_pred.min())
    max_val = max(y_true.max(), y_pred.max())
    plt.plot([min_val, max_val], [min_val, max_val], "r--", label="Perfect prediction")

    if y_pred_std is not None:
        sorted_idx = np.argsort(y_true)
        plt.fill_between(
            y_true[sorted_idx],
            y_pred[sorted_idx] - y_pred_std[sorted_idx],
            y_pred[sorted_idx] + y_pred_std[sorted_idx],
            alpha=0.15,
            color="steelblue",
            label="±1 std (tree variance)",
        )

    plt.xlabel("True utilization")
    plt.ylabel("Predicted utilization")
    plt.title(f"{link_name} — RandomForest Prediction")
    plt.legend()
    plt.tight_layout()
    filepath = os.path.join(FIGURES_DIR, f"pred_scatter_{link_name}.png")
    plt.savefig(filepath, dpi=150)
    plt.close()
    print(f"  预测散点图已保存: {filepath}")


def plot_feature_importance(rf_model, feat_cols, link_name):
    """绘制特征重要性柱状图"""
    importances = rf_model.feature_importances_
    indices = np.argsort(importances)[::-1]

    plt.figure(figsize=(8, 5))
    bars = plt.bar(
        range(len(importances)), importances[indices], alpha=0.7, color="steelblue"
    )
    plt.xlabel("Feature")
    plt.ylabel("Importance")
    plt.title(f"{link_name} — Feature Importance")
    plt.xticks(
        range(len(importances)),
        [feat_cols[i] for i in indices],
        rotation=45,
        ha="right",
    )
    for bar, val in zip(bars, importances[indices]):
        plt.text(
            bar.get_x() + bar.get_width() / 2,
            bar.get_height() + 0.005,
            f"{val:.3f}",
            ha="center",
            va="bottom",
            fontsize=8,
        )
    plt.tight_layout()
    filepath = os.path.join(FIGURES_DIR, f"feature_importance_{link_name}.png")
    plt.savefig(filepath, dpi=150)
    plt.close()
    print(f"  特征重要性图表已保存: {filepath}")


def plot_residuals(y_true, y_pred, link_name):
    """绘制残差分析图"""
    residuals = y_true - y_pred

    _, axes = plt.subplots(1, 2, figsize=(12, 5))

    axes[0].scatter(y_pred, residuals, alpha=0.7, edgecolors="k", linewidth=0.3)
    axes[0].axhline(y=0, color="r", linestyle="--")
    axes[0].set_xlabel("Predicted")
    axes[0].set_ylabel("Residual")
    axes[0].set_title(f"{link_name} — Residuals vs Predicted")
    axes[0].grid(alpha=0.3)

    axes[1].hist(residuals, bins=30, alpha=0.7, edgecolor="k", color="skyblue")
    axes[1].set_xlabel("Residual")
    axes[1].set_ylabel("Count")
    axes[1].set_title(f"{link_name} — Residual Distribution")
    axes[1].axvline(x=0, color="r", linestyle="--")
    axes[1].grid(alpha=0.3, axis="y")

    plt.tight_layout()
    filepath = os.path.join(FIGURES_DIR, f"residuals_{link_name}.png")
    plt.savefig(filepath, dpi=150)
    plt.close()
    print(f"  残差分析图表已保存: {filepath}")


def plot_error_distribution(y_true, y_pred, link_name):
    """绘制预测误差分布"""
    errors = np.abs(y_true - y_pred)

    plt.figure(figsize=(8, 5))
    plt.hist(errors, bins=30, alpha=0.7, edgecolor="k", color="coral")
    plt.xlabel("Absolute Error")
    plt.ylabel("Count")
    plt.title(f"{link_name} — Error Distribution")
    plt.axvline(
        x=np.mean(errors), color="b", linestyle="--",
        label=f"Mean: {np.mean(errors):.4f}",
    )
    plt.axvline(
        x=np.median(errors), color="g", linestyle="--",
        label=f"Median: {np.median(errors):.4f}",
    )
    plt.legend()
    plt.grid(alpha=0.3, axis="y")
    plt.tight_layout()
    filepath = os.path.join(FIGURES_DIR, f"error_distribution_{link_name}.png")
    plt.savefig(filepath, dpi=150)
    plt.close()
    print(f"  误差分布图表已保存: {filepath}")


def plot_prediction_time_series(y_true, y_pred, link_name):
    """绘制预测值与真实值的时间序列对比"""
    plt.figure(figsize=(12, 5))
    time_indices = np.arange(len(y_true))
    plt.plot(time_indices, y_true, "b-", label="True", alpha=0.7, linewidth=1.5)
    plt.plot(time_indices, y_pred, "r--", label="Predicted", alpha=0.7, linewidth=1.5)
    plt.xlabel("Time index")
    plt.ylabel("Utilization")
    plt.title(f"{link_name} — Prediction Time Series")
    plt.legend()
    plt.grid(alpha=0.3)
    plt.tight_layout()
    filepath = os.path.join(FIGURES_DIR, f"prediction_timeseries_{link_name}.png")
    plt.savefig(filepath, dpi=150)
    plt.close()
    print(f"  时间序列对比图表已保存: {filepath}")


# ======================== 主函数 ========================


def main():
    print("===== Fat-Tree Per-Link Model Training =====")

    if not os.path.exists(TRAINING_CSV):
        print(f"Error: {TRAINING_CSV} not found!")
        print("Please run first:")
        print("  1. sudo python3 scripts/collect_training_data.py")
        print("  2. cd scripts && python3 assemble_features.py")
        return

    df, feat_cols = load_data(TRAINING_CSV)
    links = df["target_label"].unique()
    print(f"Features: {feat_cols}, Links: {len(links)}, Samples: {len(df)}")

    all_results = []
    for link in sorted(links):
        df_link = df[df["target_label"] == link]
        if len(df_link) < 20:
            print(f"  Skipping {link}: only {len(df_link)} samples")
            continue

        X = df_link[feat_cols].values
        y = df_link["U_next"].values

        # Train/test split (temporal)
        split_idx = int(len(X) * 0.8)
        X_train, X_test = X[:split_idx], X[split_idx:]
        y_train, y_test = y[:split_idx], y[split_idx:]

        # Quick hyperparameter search
        tscv = TimeSeriesSplit(n_splits=3)
        rf = RandomForestRegressor(random_state=RANDOM_STATE, n_jobs=-1)
        grid = GridSearchCV(
            rf, PARAM_GRID, cv=tscv,
            scoring="neg_mean_absolute_error", n_jobs=-1, verbose=0,
        )
        grid.fit(X_train, y_train)
        best = grid.best_estimator_

        y_pred = best.predict(X_test)
        mae = mean_absolute_error(y_test, y_pred)

        # Save model
        safe_name = link.replace(" ", "_").replace("/", "_")
        model_path = os.path.join(OUTPUT_MODEL_DIR, f"model_link_{safe_name}.pkl")
        joblib.dump(best, model_path)

        all_results.append({"link": link, "MAE": mae, "samples": len(df_link)})
        print(f"  {link}: MAE={mae:.4f}, n={len(df_link)}")

    summary = pd.DataFrame(all_results)
    summary.to_csv(SUMMARY_CSV, index=False)
    print(f"\nTrained {len(all_results)} models, summary saved to {SUMMARY_CSV}")


if __name__ == "__main__":
    main()
