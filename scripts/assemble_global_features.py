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

# 修改：扩展时序窗口至 6 步（对应 0.5s 采样率下的 3.0 秒历史特征）
WINDOW_SIZE = 6
# 新增：定义预测前瞻步长为 2 步（提前 1.0 秒预测未来拓扑状态，为控制器留出下发流表裕度）
PREDICTION_STEP = 2

IN_FILES = "../data/traffic_data_*.csv"
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

    # 修改：调整循环边界，留出窗口空间和前瞻预测步长的空间
    for i in range(len(time_series_data) - WINDOW_SIZE - PREDICTION_STEP + 1):
        window = time_series_data[i : i + WINDOW_SIZE]
        # 修改：标签 Y 对应当前窗口结束后的第 PREDICTION_STEP 步（即未来的第 2 步，+1.0s）
        target = time_series_data[i + WINDOW_SIZE + PREDICTION_STEP - 1]
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
