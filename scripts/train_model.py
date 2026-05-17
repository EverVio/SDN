"""
随机森林模型训练与可视化分析
功能：
  1. 读取 data/training_features.csv（由 collect_training_data.py + assemble_features.py 生成）
  2. 按链路（path_A / path_B）分别建模
  3. 使用 TimeSeriesSplit 交叉验证评估模型稳定性
  4. 使用 GridSearchCV 进行超参数调优
  5. 按时间顺序划分训练/测试集（前80%训练，后20%测试）
  6. 使用 Random Forest 进行回归，记录 MAE / RMSE / R² / 相关系数
  7. 生成多种可视化图表：预测值散点图、特征重要性、残差分析、误差分布、CV分数、学习曲线
  8. 全量数据训练并导出最终模型至 models/model_path_A.pkl 和 models/model_path_B.pkl
  9. 输出模型评估摘要 CSV

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
TEST_SPLIT = 0.2
CV_FOLDS = 5
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
    """加载特征文件，返回 DataFrame"""
    df = pd.read_csv(csv_path)
    feat_cols = [c for c in df.columns if c.startswith("feat_")]
    if len(feat_cols) != 6:
        raise ValueError(f"期望6个特征列，实际找到 {len(feat_cols)} 个")
    return df, feat_cols


def split_time_series(X, y, test_size=0.2):
    """按时间顺序划分训练/测试集（不打乱）"""
    split_idx = int(len(X) * (1 - test_size))
    return X[:split_idx], X[split_idx:], y[:split_idx], y[split_idx:]


def evaluate_model(y_true, y_pred, model_name=""):
    """计算并打印常用回归指标，返回指标字典"""
    mae = mean_absolute_error(y_true, y_pred)
    rmse = np.sqrt(mean_squared_error(y_true, y_pred))
    r2 = r2_score(y_true, y_pred)
    corr = np.corrcoef(y_true, y_pred)[0, 1]

    print(
        f"  {model_name} — MAE: {mae:.4f}, RMSE: {rmse:.4f}, "
        f"R²: {r2:.4f}, Corr: {corr:.4f}"
    )
    return {"MAE": mae, "RMSE": rmse, "R²": r2, "Correlation": corr}


# ======================== 交叉验证与超参数调优 ========================


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


def tune_hyperparameters(X, y, n_splits=5, link_name=""):
    """GridSearchCV + TimeSeriesSplit 超参数调优"""
    tscv = TimeSeriesSplit(n_splits=n_splits)
    rf = RandomForestRegressor(random_state=RANDOM_STATE, n_jobs=-1)

    grid = GridSearchCV(
        rf,
        PARAM_GRID,
        cv=tscv,
        scoring="neg_mean_absolute_error",
        n_jobs=-1,
        verbose=0,
    )
    grid.fit(X, y)

    print(f"\n  {link_name} — 最优超参数: {grid.best_params_}")
    print(f"    最优 CV MAE: {-grid.best_score_:.4f}")
    return grid.best_estimator_, grid.best_params_


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


# ======================== 核心训练流程 ========================


def get_tree_predictions(X, rf_model):
    """获取每棵树的预测值，用于计算置信区间"""
    tree_preds = np.array([tree.predict(X) for tree in rf_model.estimators_])
    return tree_preds.mean(axis=0), tree_preds.std(axis=0)


def train_and_analyze(df, feat_cols, link_name):
    """对指定链路进行完整的 RF 训练与分析流水线"""
    print(f"\n{'='*60}")
    print(f"分析链路: {link_name}")
    print(f"{'='*60}")

    df_link = df[df["target_label"] == link_name].copy()
    X = df_link[feat_cols].values
    y = df_link["U_next"].values

    print(f"样本总数: {len(X)}")
    print(
        f"目标值统计: mean={y.mean():.4f}, std={y.std():.4f}, "
        f"min={y.min():.4f}, max={y.max():.4f}"
    )

    # ---- 1. 交叉验证 ----
    print("\n--- 交叉验证 (TimeSeriesSplit) ---")
    cv_df = cross_validate_rf(X, y, n_splits=CV_FOLDS, link_name=link_name)

    # ---- 2. 超参数调优 ----
    print("\n--- 超参数调优 (GridSearchCV) ---")
    best_model, best_params = tune_hyperparameters(
        X, y, n_splits=CV_FOLDS, link_name=link_name
    )

    # ---- 3. 训练/测试集评估 ----
    X_train, X_test, y_train, y_test = split_time_series(X, y, TEST_SPLIT)
    print(f"\n训练集: {len(X_train)}, 测试集: {len(X_test)}")

    best_model.fit(X_train, y_train)
    y_pred = best_model.predict(X_test)
    test_metrics = evaluate_model(y_test, y_pred, model_name="RF (tuned)")

    # 树方差置信区间
    _, y_pred_std = get_tree_predictions(X_test, best_model)
    print(f"  平均预测标准差: {y_pred_std.mean():.4f}")

    # OOB 分数（如果支持）
    if hasattr(best_model, "oob_score_") and best_model.oob_score_:
        print(f"  OOB R²: {best_model.oob_score_:.4f}")

    # 特征重要性
    importances = best_model.feature_importances_
    print(
        f"  特征重要性: {np.array2string(importances, precision=4, suppress_small=True)}"
    )

    # ---- 4. 可视化 ----
    print("\n--- 生成可视化图表 ---")
    plot_cv_scores(cv_df, link_name)
    plot_learning_curve(best_model, X, y, link_name)
    plot_predictions(y_test, y_pred, link_name, y_pred_std)
    plot_feature_importance(best_model, feat_cols, link_name)
    plot_residuals(y_test, y_pred, link_name)
    plot_error_distribution(y_test, y_pred, link_name)
    plot_prediction_time_series(y_test, y_pred, link_name)

    # ---- 5. 全量训练并导出 ----
    print("\n--- 全量训练 & 导出模型 ---")
    final_model = RandomForestRegressor(**best_params, random_state=RANDOM_STATE, n_jobs=-1)
    final_model.fit(X, y)

    model_path = os.path.join(OUTPUT_MODEL_DIR, f"model_{link_name}.pkl")
    joblib.dump(final_model, model_path)
    file_size_kb = os.path.getsize(model_path) / 1024
    print(f"  模型已保存: {model_path} ({file_size_kb:.1f} KB)")

    y_all_pred = final_model.predict(X)
    evaluate_model(y, y_all_pred, model_name="Full data")

    return final_model, test_metrics, best_params, cv_df


# ======================== 主函数 ========================


def main():
    print("===== SDN 负载均衡 — Random Forest 模型训练流水线 =====")

    # Step 1: 加载训练数据
    print(f"\n--- 加载训练数据: {TRAINING_CSV} ---")
    if not os.path.exists(TRAINING_CSV):
        print(f"错误: {TRAINING_CSV} 不存在！")
        print("请先执行:")
        print("  1. sudo python3 scripts/collect_training_data.py")
        print("  2. cd scripts && python3 assemble_features.py")
        return

    df, feat_cols = load_data(TRAINING_CSV)
    print(f"特征列: {feat_cols}")
    print(f"总样本数: {len(df)}")
    print(f"链路分布:\n{df['target_label'].value_counts()}")

    # Step 2: 训练与评估
    all_results = []

    for link in ["path_A", "path_B"]:
        _, metrics, params, cv_df = train_and_analyze(df, feat_cols, link)
        all_results.append({
            "link": link,
            **metrics,
            "best_params": str(params),
            "cv_MAE_mean": cv_df["MAE"].mean(),
            "cv_MAE_std": cv_df["MAE"].std(),
            "cv_R²_mean": cv_df["R²"].mean(),
            "cv_R²_std": cv_df["R²"].std(),
        })

    # 输出摘要
    print(f"\n{'='*60}")
    print("模型评估摘要")
    print(f"{'='*60}")
    summary_df = pd.DataFrame(all_results)
    print(summary_df.to_string(index=False))
    summary_df.to_csv(SUMMARY_CSV, index=False)
    print(f"\n摘要已保存: {SUMMARY_CSV}")

    print(f"\n===== 训练流水线完成 =====")
    print(f"模型文件: {OUTPUT_MODEL_DIR}/")
    print(f"分析图表: {FIGURES_DIR}/")


if __name__ == "__main__":
    main()
