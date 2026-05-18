#!/usr/bin/env python3
"""
自动采集训练数据：启动 Ryu + Mininet，跑多种流量模式，批量保存 CSV。

用法：sudo python3 scripts/collect_training_data.py
"""

import os
import sys
import time
import shutil
import signal
import socket
import subprocess

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# Mininet 仅安装在系统 Python 中，conda 环境需要手动添加路径（追加到末尾，避免覆盖 conda 的 numpy）
sys.path.append("/usr/lib/python3/dist-packages")

from mininet.log import setLogLevel
from topo.fat_tree_topo import create_topology, cleanup
from scripts.traffic_gen import (
    generate_sawtooth_noise_commands,
    generate_step_commands,
    generate_sine_commands,
    generate_fat_tree_commands,
)

DATA_DIR = "data"
SRC_CSV = os.path.join(DATA_DIR, "traffic_data.csv")
RYU_PORT = 6633
DURATION = 120
STP_WAIT = 30  # STP 收敛等待时间（秒）


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
    proc = subprocess.Popen(
        [sys.executable, "-m", "ryu.manager", "controller/threshold_balancer.py",
         "--observe-links"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if not wait_for_port(RYU_PORT, timeout=30):
        proc.kill()
        raise RuntimeError("Ryu 未能在 30 秒内启动")
    print("  Ryu 控制器已就绪")
    return proc


def run_single_experiment(net, pattern, duration):
    """Run traffic between multiple host pairs in the Fat-Tree."""
    pairs = [
        ("h0_0", "h3_0"),  # cross-pod: pod 0 -> pod 1
        ("h0_1", "h6_0"),  # cross-pod: pod 0 -> pod 3
        ("h2_0", "h5_0"),  # cross-pod: pod 1 -> pod 2
    ]

    for src_name, dst_name in pairs:
        src = net.get(src_name)
        dst = net.get(dst_name)
        if src is None or dst is None:
            continue

        dst.cmd("iperf -s -u &")
        time.sleep(0.5)

        if pattern == "sawtooth":
            cmds = generate_sawtooth_noise_commands(duration)
        elif pattern == "step":
            cmds = generate_step_commands(duration)
        else:
            cmds = generate_sine_commands(duration)

        for t_start, bw in cmds:
            src.cmd(f"iperf -c {dst.IP()} -u -b {bw}M -t 3 -i 1 &")
            time.sleep(3)

        dst.cmd("killall -9 iperf 2>/dev/null")
        time.sleep(1)


def collect_batch(batch_idx, pattern, duration):
    """完整执行一轮实验：Ryu → Mininet → 流量 → 保存 CSV"""
    print(f"\n{'='*60}")
    print(f"批次 {batch_idx}: 模式={pattern}, 时长={duration}s")
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
        print(f"  等待 STP 收敛 ({STP_WAIT}s)...")
        time.sleep(STP_WAIT)

        # 3. 执行流量
        print(f"  开始流量生成 ({pattern})...")
        run_single_experiment(net, pattern, duration)
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

    # 定义实验批次：(批次号, 流量模式)
    batches = [
        (1, "sawtooth"),
        (2, "sawtooth"),
        (3, "step"),
        (4, "step"),
        (5, "sine"),
        (6, "sine"),
        (7, "sawtooth"),
        (8, "step"),
        (9, "sine"),
        (10, "sawtooth"),
    ]

    print(f"===== 训练数据采集 =====")
    print(f"共 {len(batches)} 批次，每批 {DURATION} 秒")
    print(f"预计总耗时: {len(batches) * (DURATION + STP_WAIT + 20) / 60:.0f} 分钟")

    for idx, pattern in batches:
        collect_batch(idx, pattern, DURATION)

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
    print(f"\n下一步: cd scripts && python3 assemble_features.py")


if __name__ == "__main__":
    main()
