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

WINDOW_SIZE = 3
IN_FILES = "../data/traffic_data_*.csv"
OUTPUT_FILE = "../data/global_features.pkl"

# Fat-Tree k=4 valid DPIDs: edge 1-8, aggregation 9-16, core 17-20
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

    # Filter backbone links:
    #  1. Remove host access ports (labels ending with '_edge')
    #  2. Remove old path_A/path_B labels from threshold_balancer
    #  3. Keep only DPIDs 1-20 (Fat-Tree k=4)
    df = df[~df['link_label'].str.endswith('_edge')]
    df = df[~df['link_label'].str.startswith('path_')]
    df = df[df['dpid'].isin(VALID_DPIDS)]

    print(f"\nBackbone records after filtering: {len(df)}")

    # Get sorted unique link keys — this defines the global feature vector order
    unique_links = df[['dpid', 'port_no']].drop_duplicates()
    link_keys = sorted([tuple(x) for x in unique_links.to_numpy()])
    num_links = len(link_keys)
    print(f"Backbone links: {num_links}")

    # Pivot: each row is a timestamp, columns are (dpid, port_no) utilization
    pivot_df = df.pivot_table(
        index='timestamp',
        columns=['dpid', 'port_no'],
        values='utilization',
        aggfunc='mean',  # average if duplicate records exist per timestamp
        fill_value=0.0,
    )

    # Ensure all link_keys are present (fill missing with 0)
    for key in link_keys:
        if key not in pivot_df.columns:
            pivot_df[key] = 0.0

    # Reorder columns to match sorted link_keys
    pivot_df = pivot_df[link_keys]

    time_series_data = pivot_df.to_numpy()
    print(f"Time series matrix: {time_series_data.shape[0]} timestamps x {num_links} links")

    # Build sliding window features
    # Input X: [T, T+1, T+2] flattened -> dimension = WINDOW_SIZE * num_links
    # Output Y: [T+3] -> dimension = num_links
    X_data = []
    Y_data = []

    for i in range(len(time_series_data) - WINDOW_SIZE):
        window = time_series_data[i: i + WINDOW_SIZE]
        target = time_series_data[i + WINDOW_SIZE]
        X_data.append(window.flatten())
        Y_data.append(target)

    X_array = np.array(X_data, dtype=np.float32)
    Y_array = np.array(Y_data, dtype=np.float32)

    print(f"\nAssembled features: X={X_array.shape}, Y={Y_array.shape}")
    print(f"Feature dimension: {WINDOW_SIZE} x {num_links} = {WINDOW_SIZE * num_links}")

    # Save
    os.makedirs(os.path.dirname(os.path.abspath(OUTPUT_FILE)), exist_ok=True)
    joblib.dump({
        'X': X_array,
        'Y': Y_array,
        'link_keys': link_keys,
        'window_size': WINDOW_SIZE,
    }, OUTPUT_FILE)
    print(f"Saved to {OUTPUT_FILE}")


if __name__ == "__main__":
    process_global_features()
