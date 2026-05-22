"""
Phase 1: Assemble global features for Global MLP model.

Reads all traffic_data_*.csv files, filters to Fat-Tree k=4 backbone links,
pivots to per-timestamp matrices, and builds sliding window features.
Output: data/global_features.pkl
"""

import os
import glob
import pandas as pd
import numpy as np
import joblib

WINDOW_SIZE = 6
PREDICTION_STEP = 2
# 新增：目标窗口大小（对应 1.5 秒）。模型将预测这段时间内的最大利用率，而非某一瞬间的值
TARGET_WINDOW = 3

IN_FILES = "../data/traffic_data.csv"
OUTPUT_FILE = "../data/global_features.pkl"

VALID_DPIDS = set(range(1, 21))


def process_global_features():
    csv_files = sorted(glob.glob(IN_FILES))
    if not csv_files:
        print("ERROR: No traffic_data_*.csv files found")
        return

    print(f"Loading {len(csv_files)} data files...")
    all_dfs = []
    for f in csv_files:
        df = pd.read_csv(f)
        all_dfs.append(df)
        print(f"  {f}: {len(df)} records")

    df = pd.concat(all_dfs, ignore_index=True)

    df = df[~((df["dpid"] <= 8) & (df["port_no"].isin([1, 2])))]
    df = df[~df["link_label"].str.startswith("path_")]
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
        fill_value=0.0,
    )

    for key in link_keys:
        if key not in pivot_df.columns:
            pivot_df[key] = 0.0

    pivot_df = pivot_df[pivot_df.sum(axis=1) > 0.01]

    time_series_data = pivot_df.to_numpy()

    print(
        f"Time series matrix: {time_series_data.shape[0]} timestamps x {num_links} links"
    )

    X_data = []
    Y_data = []

    # 修改：收缩循环右边界，预留出前瞻步长与目标窗口所需的数组空间
    for i in range(
        len(time_series_data) - WINDOW_SIZE - PREDICTION_STEP - TARGET_WINDOW + 1
    ):
        # 特征 X：当前滑动窗口内的历史状态
        window = time_series_data[i : i + WINDOW_SIZE]

        # 标签 Y：提取未来 [PREDICTION_STEP, PREDICTION_STEP + TARGET_WINDOW) 区间的切片
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
        # 算法原理：沿时间轴 (axis=0) 取最大值。对于控制平面，预测拥塞峰值比预测平均值更能保障无损防抖
        target = np.max(target_slice, axis=0)

        X_data.append(window.flatten())
        Y_data.append(target)

    X_array = np.array(X_data, dtype=np.float32)
    Y_array = np.array(Y_data, dtype=np.float32)

    print(f"\nAssembled features: X={X_array.shape}, Y={Y_array.shape}")
    print(f"Feature dimension: {WINDOW_SIZE} x {num_links} = {WINDOW_SIZE * num_links}")

    os.makedirs(os.path.dirname(os.path.abspath(OUTPUT_FILE)), exist_ok=True)
    joblib.dump(
        {
            "X": X_array,
            "Y": Y_array,
            "link_keys": link_keys,
            "window_size": WINDOW_SIZE,
        },
        OUTPUT_FILE,
    )
    print(f"Saved to {OUTPUT_FILE}")


if __name__ == "__main__":
    process_global_features()
