import pandas as pd
import glob

IN_FILES = "../data/traffic_data_*.csv"
OUT_FILE = "../data/training_features.csv"
WINDOW_SIZE = 3


def process_single_csv(input_csv):
    """处理单一批次的原始 CSV，返回组装好的特征 DataFrame"""
    df = pd.read_csv(input_csv)
    df = df[df["link_label"].isin(["path_A", "path_B"])]

    pivot = df.pivot_table(
        index="timestamp", columns="link_label", values="utilization", aggfunc="max"
    ).sort_index()

    util_a = pivot["path_A"].values
    util_b = pivot["path_B"].values

    samples = []
    for i in range(WINDOW_SIZE, len(util_a)):
        features = []
        for t in range(i - WINDOW_SIZE, i):
            features.extend([util_a[t], util_b[t]])

        for label, target in [("path_A", util_a[i]), ("path_B", util_b[i])]:
            samples.append(
                {
                    **{f"feat_{j}": features[j] for j in range(6)},
                    "target_label": label,
                    "U_next": target,
                }
            )
    return pd.DataFrame(samples)


def main():
    # 获取所有分批次的原始数据文件
    csv_files = glob.glob(IN_FILES)
    csv_files.sort()
    all_features = []

    for f in csv_files:
        feat_df = process_single_csv(f)
        all_features.append(feat_df)
        print(f"提取 {f} -> 获得 {len(feat_df)} 个特征样本")

    # 在特征层面进行合并
    merged_df = pd.concat(all_features, ignore_index=True)
    merged_df.to_csv(OUT_FILE, index=False, float_format="%.6f")

    print(f"\n合并完成。总计合并 {len(csv_files)} 个文件。")
    print(f"总特征样本数: {len(merged_df)} (已保存至 {OUT_FILE})")
    print(f"  path_A 样本数: {len(merged_df[merged_df['target_label'] == 'path_A'])}")
    print(f"  path_B 样本数: {len(merged_df[merged_df['target_label'] == 'path_B'])}")


if __name__ == "__main__":
    main()
