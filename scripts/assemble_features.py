import pandas as pd
import glob

IN_FILES = "../data/traffic_data_*.csv"
OUT_FILE = "../data/training_features.csv"
WINDOW_SIZE = 3


def process_single_csv(input_csv):
    """Process a single batch CSV into per-link sliding window features."""
    df = pd.read_csv(input_csv)

    # Filter out edge ports (only backbone links)
    df = df[~df["link_label"].str.endswith("_edge")]

    samples = []
    for label, group in df.groupby("link_label"):
        group = group.sort_values("timestamp")
        utils = group["utilization"].values

        if len(utils) <= WINDOW_SIZE:
            continue

        for i in range(WINDOW_SIZE, len(utils)):
            features = list(utils[i - WINDOW_SIZE:i])
            samples.append({
                **{f"feat_{j}": features[j] for j in range(WINDOW_SIZE)},
                "target_label": label,
                "U_next": utils[i],
            })

    return pd.DataFrame(samples)


def main():
    csv_files = sorted(glob.glob(IN_FILES))
    all_features = []

    for f in csv_files:
        feat_df = process_single_csv(f)
        all_features.append(feat_df)
        print(f"Processed {f} -> {len(feat_df)} samples")

    merged_df = pd.concat(all_features, ignore_index=True)
    merged_df.to_csv(OUT_FILE, index=False, float_format="%.6f")

    n_links = merged_df["target_label"].nunique()
    print(f"\nTotal samples: {len(merged_df)}, Links: {n_links}")
    print(f"Saved to {OUT_FILE}")


if __name__ == "__main__":
    main()
