"""
Train Global MLP model and export multi-perspective evaluation data for visualization.

Loads features, performs split, scales data, trains MLPRegressor,
and outputs model artifacts along with precise analysis results (loss curves,
true vs pred arrays, per-link errors) into the data/ directory.
"""

import os
import joblib
import numpy as np
import pandas as pd
from sklearn.neural_network import MLPRegressor
from sklearn.metrics import mean_squared_error, mean_absolute_error
from sklearn.preprocessing import StandardScaler

DATA_FILE = "../data/global_features.pkl"
MODEL_DIR = "../models"
MODEL_FILE = os.path.join(MODEL_DIR, "global_mlp_model.pkl")

# 定义可视化分析输出路径
VIZ_HISTORY_FILE = "../data/viz_training_history.pkl"
VIZ_PRED_FILE = "../data/viz_predictions.pkl"
VIZ_METRICS_CSV = "../data/viz_per_link_metrics.csv"


def train_model():
    data = joblib.load(DATA_FILE)
    X = data["X"]
    Y = data["Y"]
    timestamps = data["timestamps"]
    link_keys = data["link_keys"]
    window_size = data["window_size"]

    split_idx = int(len(X) * 0.8)
    X_train, X_test = X[:split_idx], X[split_idx:]
    Y_train, Y_test = Y[:split_idx], Y[split_idx:]
    ts_test = timestamps[split_idx:]  # 提取测试集对应的时间戳

    scaler_X = StandardScaler()
    X_train_scaled = scaler_X.fit_transform(X_train)
    X_test_scaled = scaler_X.transform(X_test)

    scaler_Y = StandardScaler()
    Y_train_scaled = scaler_Y.fit_transform(Y_train)
    Y_test_scaled = scaler_Y.transform(Y_test)

    model = MLPRegressor(
        hidden_layer_sizes=(256, 128, 64),
        activation="relu",
        solver="adam",
        alpha=0.001,
        learning_rate="adaptive",
        max_iter=1000,
        tol=1e-5,
        early_stopping=True,
        n_iter_no_change=10,
        validation_fraction=0.15,
        random_state=42,
        verbose=True,
    )

    model.fit(X_train_scaled, Y_train_scaled)

    # 1. 导出模型收敛曲线数据（训练集 Loss 与验证集 Score 演变）
    joblib.dump(
        {
            "loss_curve": model.loss_curve_,
            "validation_scores": model.validation_scores_,
        },
        VIZ_HISTORY_FILE,
    )
    print(f"Saved training history curves to {VIZ_HISTORY_FILE}")

    Y_pred_scaled = model.predict(X_test_scaled)
    Y_pred = scaler_Y.inverse_transform(Y_pred_scaled)
    Y_pred = np.clip(Y_pred, 0.0, 1.0)

    # 2. 导出全量测试集真实值与预测值对照矩阵（含拓扑键值与精确时间戳）
    # 用于拟合优度散点图、单链路时序逼近图以及残差分布直方图的绘制
    joblib.dump(
        {
            "Y_true": Y_test,
            "Y_pred": Y_pred,
            "timestamps": ts_test,
            "link_keys": link_keys,
        },
        VIZ_PRED_FILE,
    )
    print(f"Saved test predictions comparison to {VIZ_PRED_FILE}")

    # 3. 计算并导出单链路维度的精细化误差统计（Spatial Error Distribution）
    # 用于后续结合拓扑结构绘制链路误差条形图或拓扑着色图
    per_link_records = []
    for i, key in enumerate(link_keys):
        dpid, port_no = key
        link_true = Y_test[:, i]
        link_pred = Y_pred[:, i]

        link_mse = mean_squared_error(link_true, link_pred)
        link_mae = mean_absolute_error(link_true, link_pred)
        link_rmse = np.sqrt(link_mse)

        per_link_records.append(
            {
                "dpid": dpid,
                "port_no": port_no,
                "MSE": link_mse,
                "MAE": link_mae,
                "RMSE": link_rmse,
            }
        )

    df_metrics = pd.DataFrame(per_link_records)
    df_metrics.to_csv(VIZ_METRICS_CSV, index=False)
    print(f"Saved per-link spatial metrics to {VIZ_METRICS_CSV}")

    # 全局指标打印
    global_mse = mean_squared_error(Y_test, Y_pred)
    global_mae = mean_absolute_error(Y_test, Y_pred)
    print(
        f"\nGlobal Evaluation -> MSE: {global_mse:.6f}, MAE: {global_mae:.6f}, RMSE: {np.sqrt(global_mse):.6f}"
    )

    os.makedirs(MODEL_DIR, exist_ok=True)
    joblib.dump(
        {
            "model": model,
            "scaler_X": scaler_X,
            "scaler_Y": scaler_Y,
            "link_keys": link_keys,
            "window_size": window_size,
        },
        MODEL_FILE,
    )
    print(f"Saved model configuration to {MODEL_FILE}")


if __name__ == "__main__":
    train_model()
