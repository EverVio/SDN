#!/usr/bin/env python3
"""
自动采集训练数据：启动 Ryu + Mininet，跑多种流量模式，批量保存 CSV。

用法：sudo python3 scripts/collect_training_data.py
"""

import os
import sys
import time
import random
import shutil
import signal
import socket
import subprocess

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(PROJECT_ROOT)
# Mininet 仅安装在系统 Python 中，conda 环境需要手动添加路径（追加到末尾，避免覆盖 conda 的 numpy）
sys.path.append("/usr/lib/python3/dist-packages")

from mininet.log import setLogLevel
from topo.fat_tree_topo import create_topology, cleanup

DATA_DIR = os.path.join(PROJECT_ROOT, "data")
SRC_CSV = os.path.join(DATA_DIR, "traffic_data.csv")
RYU_PORT = 6633
DURATION = 120
STP_WAIT = 30  # STP 收敛等待时间（秒）

# Fat-Tree k=4: 16 hosts across 4 pods
ALL_HOSTS = [f"h{pod}_{idx}" for pod in range(4) for idx in range(4)]

# Traffic permutation parameters
PERMUTATION_INTERVAL = 15  # seconds between reshuffles
NUM_PAIRS = 8              # concurrent communication pairs per round


def wait_for_port(port, timeout=30):
    """等待端口可连接"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return True
        except (ConnectionRefusedError, OSError):
            time.sleep(0.5)
    return False


def kill_ryu():
    """终止残留的 ryu-manager 进程"""
    os.system("pkill -f ryu-manager 2>/dev/null")
    time.sleep(1)


def start_ryu():
    """启动 Ryu 控制器子进程，返回 Popen 对象"""
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    controller_path = os.path.join(project_root, "controller", "threshold_balancer.py")
    proc = subprocess.Popen(
        ["ryu-manager", controller_path, "--observe-links"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        cwd=project_root,
    )
    if not wait_for_port(RYU_PORT, timeout=30):
        proc.kill()
        raise RuntimeError("Ryu 未能在 30 秒内启动")
    print("  Ryu 控制器已就绪")
    return proc


def run_single_experiment(net, duration):
    """Run dynamic random traffic permutation to exercise all ECMP paths.

    Every PERMUTATION_INTERVAL seconds, shuffle all 16 hosts into 8 random
    pairs and start new iperf UDP flows with random bandwidth (2-8 Mbps).
    This breaks deterministic hashing: different (src, dst) pairs map to
    different ECMP paths, forcing traffic across all aggregation and core
    switches within a single batch.
    """
    # Start iperf servers on all hosts (idempotent)
    for host_name in ALL_HOSTS:
        h = net.get(host_name)
        if h is not None:
            h.cmd("iperf -s -u &")
    time.sleep(1)

    num_rounds = duration // PERMUTATION_INTERVAL
    for round_idx in range(num_rounds):
        # Shuffle and pair
        shuffled = ALL_HOSTS[:]
        random.shuffle(shuffled)
        pairs = [(shuffled[i], shuffled[i + 1]) for i in range(0, len(shuffled), 2)]

        # Random bandwidth per pair: 2-8 Mbps
        for src_name, dst_name in pairs:
            src = net.get(src_name)
            dst = net.get(dst_name)
            if src is None or dst is None:
                continue
            bw = round(random.uniform(2.0, 8.0), 1)
            # iperf -t PERMUTATION_INTERVAL: flow lives for the full round
            src.cmd(
                f"iperf -c {dst.IP()} -u -b {bw}M "
                f"-t {PERMUTATION_INTERVAL} -i 1 &"
            )

        print(f"    Round {round_idx + 1}/{num_rounds}: "
              f"{len(pairs)} pairs, bandwidth 2-8 Mbps")
        time.sleep(PERMUTATION_INTERVAL)

    # Cleanup all iperf processes
    for host_name in ALL_HOSTS:
        h = net.get(host_name)
        if h is not None:
            h.cmd("killall -9 iperf 2>/dev/null")
    time.sleep(1)


def collect_batch(batch_idx, duration):
    """完整执行一轮实验：Ryu → Mininet → 动态随机流量 → 保存 CSV"""
    print(f"\n{'='*60}")
    print(f"批次 {batch_idx}: 动态随机置换, 时长={duration}s")
    print(f"{'='*60}")

    # 1. 清理 + 启动 Ryu
    cleanup()
    kill_ryu()
    ryu_proc = start_ryu()

    net = None
    try:
        # 2. 启动 Mininet
        print("  启动 Mininet 拓扑...")
        setLogLevel("warning")
        net, c0 = create_topology()
        net.build()
        c0.start()
        net.start()
        net.staticArp()
        print(f"  等待拓扑收敛 ({STP_WAIT}s)...")
        time.sleep(STP_WAIT)

        # 3. 执行动态随机流量
        print(f"  开始流量生成 (动态随机置换, {duration // PERMUTATION_INTERVAL} 轮)...")
        run_single_experiment(net, duration)
        print("  流量生成完成")

        # 4. 停止 Mininet（触发 CSV flush）
        print("  停止 Mininet...")
        net.stop()
        net = None
        time.sleep(2)

    finally:
        # 5. 终止 Ryu
        if net is not None:
            try:
                net.stop()
            except Exception:
                pass
        ryu_proc.send_signal(signal.SIGTERM)
        try:
            ryu_proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            ryu_proc.kill()
        print("  Ryu 已停止")

    # 6. 保存 CSV
    dst_csv = os.path.join(DATA_DIR, f"traffic_data_{batch_idx}.csv")
    if os.path.exists(SRC_CSV):
        shutil.copy2(SRC_CSV, dst_csv)
        size_kb = os.path.getsize(dst_csv) / 1024
        print(f"  已保存: {dst_csv} ({size_kb:.1f} KB)")
    else:
        print(f"  警告: {SRC_CSV} 不存在，跳过保存")

    cleanup()


def main():
    os.makedirs(DATA_DIR, exist_ok=True)

    num_batches = 10

    print(f"===== 训练数据采集 (动态随机置换) =====")
    print(f"共 {num_batches} 批次，每批 {DURATION} 秒")
    print(f"每批 {DURATION // PERMUTATION_INTERVAL} 轮置换，"
          f"每轮 {NUM_PAIRS} 对随机通信")
    print(f"预计总耗时: {num_batches * (DURATION + STP_WAIT + 20) / 60:.0f} 分钟")

    for idx in range(1, num_batches + 1):
        collect_batch(idx, DURATION)

    print(f"\n{'='*60}")
    print(f"全部采集完成！")
    csv_files = sorted([
        f for f in os.listdir(DATA_DIR)
        if f.startswith("traffic_data_") and f.endswith(".csv")
    ])
    print(f"共生成 {len(csv_files)} 个数据文件:")
    for f in csv_files:
        size_kb = os.path.getsize(os.path.join(DATA_DIR, f)) / 1024
        print(f"  {f} ({size_kb:.1f} KB)")
    print(f"\n下一步: cd scripts && python3 assemble_global_features.py && python3 train_global_mlp.py")


if __name__ == "__main__":
    main()
