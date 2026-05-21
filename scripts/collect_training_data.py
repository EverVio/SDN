#!/usr/bin/env python3
"""
自动采集训练数据：单次启动 Ryu + Mininet，连续执行多轮动态流量置换，输出单一大文件。
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
sys.path.append("/usr/lib/python3/dist-packages")

from mininet.log import setLogLevel
from topo.fat_tree_topo import create_topology, cleanup

DATA_DIR = os.path.join(PROJECT_ROOT, "data")
SRC_CSV = os.path.join(DATA_DIR, "traffic_data.csv")
RYU_PORT = 6633
STP_WAIT = 5

ALL_HOSTS = [f"h{pod}_{idx}" for pod in range(4) for idx in range(4)]

# 优化采集效率：缩短轮次间隔，增加总轮数
PERMUTATION_INTERVAL = 8
TOTAL_ROUNDS = 200


def wait_for_port(port, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(1)
        result = s.connect_ex(("127.0.0.1", port))
        s.close()
        if result == 0:
            return True
        time.sleep(0.5)
    return False


def kill_ryu():
    os.system("pkill -f ryu-manager 2>/dev/null")
    time.sleep(1)


def start_ryu():
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    # 采用闭环策略：使用 threshold_balancer 采集数据，使模型学习路由切换后的状态转移
    controller_path = os.path.join(project_root, "controller", "threshold_balancer.py")
    proc = subprocess.Popen(
        ["ryu-manager", controller_path, "--observe-links"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        cwd=project_root,
    )
    wait_for_port(RYU_PORT, timeout=30)
    print("  Ryu 控制器已就绪")
    return proc


def main():
    os.makedirs(DATA_DIR, exist_ok=True)

    print("===== 快速训练数据采集 =====")
    print(f"共 {TOTAL_ROUNDS} 轮置换，每轮 {PERMUTATION_INTERVAL} 秒")
    print(
        f"预计总耗时: {(TOTAL_ROUNDS * PERMUTATION_INTERVAL + STP_WAIT + 20) / 60:.0f} 分钟"
    )

    cleanup()
    kill_ryu()
    ryu_proc = start_ryu()

    print("  启动 Mininet 拓扑...")
    setLogLevel("warning")
    net, c0 = create_topology()
    net.build()
    c0.start()
    net.start()
    net.staticArp()
    print(f"  等待拓扑收敛 ({STP_WAIT}s)...")
    time.sleep(STP_WAIT)

    print("  启动服务端守护进程...")
    for host_name in ALL_HOSTS:
        h = net.get(host_name)
        if h is not None:
            h.cmd("iperf -s -u &")
    time.sleep(1)

    print("  开始连续流量生成...")
    for round_idx in range(TOTAL_ROUNDS):
        shuffled = ALL_HOSTS[:]
        random.shuffle(shuffled)
        pairs = [(shuffled[i], shuffled[i + 1]) for i in range(0, len(shuffled), 2)]

        for idx, (src_name, dst_name) in enumerate(pairs):
            src = net.get(src_name)
            dst = net.get(dst_name)
            if src is None or dst is None:
                continue

            # 消除分布偏移：一半使用渐进流（模拟测试环境），一半使用随机恒定流
            if idx < len(pairs) // 2:
                src.cmd(f"iperf -c {dst.IP()} -u -b 0.5M -t {PERMUTATION_INTERVAL} &")
                src.cmd(
                    f"sh -c 'sleep 3 && iperf -c {dst.IP()} -u -b 0.5M -t {PERMUTATION_INTERVAL - 3}' &"
                )
            else:
                bw = round(random.uniform(0.2, 1.6), 2)
                src.cmd(f"iperf -c {dst.IP()} -u -b {bw}M -t {PERMUTATION_INTERVAL} &")

        print(f"    Round {round_idx + 1}/{TOTAL_ROUNDS}")
        time.sleep(PERMUTATION_INTERVAL)

    print("  清理流量进程...")
    for host_name in ALL_HOSTS:
        h = net.get(host_name)
        if h is not None:
            h.cmd("killall -9 iperf 2>/dev/null")
    time.sleep(1)

    print("  停止 Mininet...")
    net.stop()
    time.sleep(2)

    ryu_proc.send_signal(signal.SIGTERM)
    ryu_proc.wait()
    print("  Ryu 已停止")

    timestamp = int(time.time())
    dst_csv = os.path.join(DATA_DIR, f"traffic_data_continuous_{timestamp}.csv")
    if os.path.exists(SRC_CSV):
        shutil.copy2(SRC_CSV, dst_csv)
        size_kb = os.path.getsize(dst_csv) / 1024
        print(f"  已保存单一大文件: {dst_csv} ({size_kb:.1f} KB)")
    else:
        print(f"  警告: {SRC_CSV} 不存在")

    cleanup()


if __name__ == "__main__":
    main()
