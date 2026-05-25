"""
Assemble global features for Global MLP model and export visualization data.

Reads traffic_data.csv, filters to Fat-Tree k=4 backbone links,
pivots to per-timestamp matrices, builds sliding window features,
and saves comprehensive datasets for visualization.
Output: data/global_features.pkl, data/viz_raw_traffic_matrix.pkl
"""

import os
import joblib
import numpy as np
import pandas as pd

WINDOW_SIZE = 6
PREDICTION_STEP = 2
TARGET_WINDOW = 3

IN_FILE = "../data/traffic_data.csv"
OUTPUT_FILE = "../data/global_features.pkl"
VIZ_MATRIX_FILE = "../data/viz_raw_traffic_matrix.pkl"

VALID_DPIDS = set(range(1, 21))


def process_global_features():
    if not os.path.exists(IN_FILE):
        print(f"ERROR: {IN_FILE} not found")
        return

    df = pd.read_csv(IN_FILE)
    print(f"Loaded {IN_FILE}: {len(df)} records")

    df = df[~((df["dpid"] <= 8) & (df["port_no"].isin([1, 2])))]
    df = df[df["dpid"].isin(VALID_DPIDS)]

    print(f"\nBackbone records after filtering: {len(df)}")

    unique_links = df[["dpid", "port_no"]].drop_duplicates()
    link_keys = sorted([tuple(x) for x in unique_links.to_numpy()])
    num_links = len(link_keys)
    print(f"Backbone links: {num_links}")

    pivot_df = df.pivot_table(
        index="timestamp",
        columns=["dpid", "port_no"],
        values="utilization",
        aggfunc="mean",
    )

    min_ts = pivot_df.index.min()
    max_ts = pivot_df.index.max()
    expected_index = np.arange(min_ts, max_ts + 0.5, 0.5)

    pivot_df = (
        pivot_df.reindex(index=expected_index)
        .interpolate(method="linear", limit_direction="both")
        .fillna(0.0)
    )
    pivot_df = pivot_df.reindex(columns=link_keys, fill_value=0.0)

    # 导出完整的时序矩阵供后续绘制全网时空流量热力图
    os.makedirs(os.path.dirname(os.path.abspath(VIZ_MATRIX_FILE)), exist_ok=True)
    joblib.dump(
        {
            "matrix": pivot_df.to_numpy(),
            "link_keys": link_keys,
            "timestamps": pivot_df.index.to_numpy(),
        },
        VIZ_MATRIX_FILE,
    )
    print(f"Saved raw traffic matrix for visualization to {VIZ_MATRIX_FILE}")

    time_series_data = pivot_df.to_numpy()
    timestamps_raw = pivot_df.index.to_numpy()

    print(
        f"Time series matrix: {time_series_data.shape[0]} timestamps x {num_links} links"
    )

    X_data = []
    Y_data = []
    Y_timestamps = []  # 追踪每个训练样本对应的目标预测时间戳，供时序图对齐使用

    for i in range(
        len(time_series_data) - WINDOW_SIZE - PREDICTION_STEP - TARGET_WINDOW + 1
    ):
        window = time_series_data[i : i + WINDOW_SIZE]
        target_slice = time_series_data[
            i
            + WINDOW_SIZE
            + PREDICTION_STEP
            - 1 : i
            + WINDOW_SIZE
            + PREDICTION_STEP
            - 1
            + TARGET_WINDOW
        ]
        target = np.max(target_slice, axis=0)

        X_data.append(window.flatten())
        Y_data.append(target)
        # 记录目标时段起点的绝对时间戳
        Y_timestamps.append(timestamps_raw[i + WINDOW_SIZE + PREDICTION_STEP - 1])

    X_array = np.array(X_data, dtype=np.float32)
    Y_array = np.array(Y_data, dtype=np.float32)
    Y_ts_array = np.array(Y_timestamps, dtype=np.float64)

    print(f"\nAssembled features: X={X_array.shape}, Y={Y_array.shape}")
    print(f"Feature dimension: {WINDOW_SIZE} x {num_links} = {WINDOW_SIZE * num_links}")

    os.makedirs(os.path.dirname(os.path.abspath(OUTPUT_FILE)), exist_ok=True)
    joblib.dump(
        {
            "X": X_array,
            "Y": Y_array,
            "timestamps": Y_ts_array,
            "link_keys": link_keys,
            "window_size": WINDOW_SIZE,
        },
        OUTPUT_FILE,
    )
    print(f"Saved to {OUTPUT_FILE}")


if __name__ == "__main__":
    process_global_features()
