#!/usr/bin/env python3
"""
三组对比实验脚本：L2 基线 / 阈值均衡 / Global MLP 预测均衡。

实验设计 — 概率哈希碰撞 + 渐进突发 (Probabilistic Hash Collision + Ramp-up)：

  Fat-Tree k=4 在 Pod 0 ↔ Pod 3 之间有 4 条等价 Core 路径，
  总横截带宽 = 4 × 2Mbps = 8Mbps。

  阶段 1 (t=0s):  启动 3 条 1.5Mbps 背景流（4.5Mbps，4 路径各 ~1.125Mbps，无拥塞）
  阶段 2 (t=20s): 渐进启动突发流（3 条 0.5Mbps 子流，间隔 5s，模拟带宽爬升）
    - t=20s: 子流 A 0.5Mbps → 总 5.0Mbps
    - t=25s: 子流 B 0.5Mbps → 总 5.5Mbps
    - t=30s: 子流 C 0.5Mbps → 总 6.0Mbps

  哈希碰撞概率：3 背景流占 3 条路径，突发流有 25% 概率哈希到已占用路径。
  碰撞时该链路承载 2.625Mbps > 2Mbps → 丢包。

  - L2 基线组：静态哈希，碰撞后持续丢包
  - 阈值均衡组：碰撞后 2-3 秒检测到拥塞，响应式迁移
  - Global MLP 组：预测引擎在爬升阶段识别趋势，主动迁移，0 丢包

用法：sudo python3 scripts/run_experiment.py --group [l2|threshold|predictive|all] --iters 5
"""

import os
import sys
import time
import signal
import socket
import subprocess
import re
import csv

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(PROJECT_ROOT)
sys.path.append("/usr/lib/python3/dist-packages")

from mininet.log import setLogLevel
from topo.fat_tree_topo import create_topology, cleanup, configure_select_hash

RYU_PORT = 6633
TEST_DURATION = 60  # 测试持续时间（秒）
UDP_BANDWIDTH = 0.5  # 背景流每条带宽 (Mbps)
BURST_DELAY = 20  # 突发流启动延迟（秒）
BURST_STAGGER = 6  # 突发子流间隔（秒）
BURST_SUB_BW = 0.25  # 每条突发子流带宽 (Mbps)
BASE_PORT = 5000  # iperf 端口基准（每条流使用 BASE_PORT + flow_number）
CORE_LINK_BW = 2  # 每条核心链路带宽 (Mbps)，与 BW_AGG_CORE 一致

# 扩展为 9 条背景流（3对主机，每对并行3个端口，总计 4.5Mbps）
BACKGROUND_FLOWS = [
    ("h0_0", "h3_0"),
    ("h0_1", "h3_1"),
    ("h0_2", "h3_2"),
    ("h0_0", "h3_0"),
    ("h0_1", "h3_1"),
    ("h0_2", "h3_2"),
    ("h0_0", "h3_0"),
    ("h0_1", "h3_1"),
    ("h0_2", "h3_2"),
]

# 扩展为 6 条突发子流（逐步抬升，总计 1.5Mbps）
BURST_SUBFLOWS = [
    ("h0_3", "h3_3"),
    ("h0_0", "h3_3"),
    ("h0_1", "h3_3"),
    ("h0_3", "h3_3"),
    ("h0_0", "h3_3"),
    ("h0_1", "h3_3"),
]

CONTROLLERS = {
    "l2": "controller/base_controller.py",
    "threshold": "controller/threshold_balancer.py",
    "predictive": "controller/predictive_balancer.py",
}


def wait_for_port(port, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return True
        except (ConnectionRefusedError, OSError):
            time.sleep(0.5)
    return False


def kill_ryu():
    os.system("pkill -f ryu-manager 2>/dev/null")
    time.sleep(1)


def start_ryu(controller_script, group_name="run"):
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    controller_path = os.path.join(project_root, controller_script)
    log_path = os.path.join(project_root, "data", f"ryu_{group_name}.log")
    ryu_log = open(log_path, "w")
    proc = subprocess.Popen(
        ["ryu-manager", controller_path],
        stdout=ryu_log,
        stderr=subprocess.STDOUT,
        cwd=project_root,
    )
    if not wait_for_port(RYU_PORT, timeout=30):
        proc.kill()
        print(f"  ERROR: Ryu failed to start. Check {log_path}")
        sys.exit(1)
    print(f"  Ryu started: {controller_script} (log: {log_path})")
    return proc


def _to_mbps(bw_value, unit):
    """Convert bandwidth value to Mbits/sec."""
    if "Kbits" in unit:
        return bw_value / 1000.0
    return bw_value


def parse_iperf_udp_output(output, expected_bw_mbps=None):
    """Parse iperf2 UDP client output for loss, jitter, and bandwidth.

    Handles both Mbits/sec and Kbits/sec units from iperf2 output.
    """
    loss_pct = 0.0
    jitter_ms = 0.0
    bandwidth_mbps = 0.0
    has_server_report = False

    if "did not receive server response" in output:
        loss_pct = 100.0

    for line in output.split("\n"):
        m = re.search(
            r"(\d+\.?\d*)\s+([MK])bits/sec\s+(\d+\.?\d*)\s+ms\s+\d+/\s*\d+\s+\((\d+\.?\d*)%\)",
            line,
        )
        if m:
            bandwidth_mbps = _to_mbps(float(m.group(1)), m.group(2) + "bits/sec")
            jitter_ms = float(m.group(3))
            loss_pct = float(m.group(4))
            has_server_report = True
            continue

        m2 = re.search(
            r"(\d+\.?\d*)\s+\d+\.?\d*\s+sec\s+[\d.]+\s+\w+\s+(\d+\.?\d*)\s+([MK])bits/sec",
            line,
        )
        if m2:
            bandwidth_mbps = _to_mbps(float(m2.group(2)), m2.group(3) + "bits/sec")

    if not has_server_report and bandwidth_mbps > 0 and expected_bw_mbps:
        loss_pct = max(0.0, (1.0 - bandwidth_mbps / expected_bw_mbps) * 100.0)

    return loss_pct, jitter_ms, bandwidth_mbps


def _collect_flow_result(client_log, server_log, expected_bw):
    """Collect iperf results from client and server logs."""
    loss, jitter, bw = 0.0, 0.0, 0.0
    source = "none"

    if os.path.exists(client_log):
        with open(client_log) as f:
            output = f.read()
        loss, jitter, bw = parse_iperf_udp_output(output, expected_bw_mbps=expected_bw)
        source = "client"

    if os.path.exists(server_log):
        with open(server_log) as f:
            output = f.read()
        s_loss, s_jitter, s_bw = parse_iperf_udp_output(output)
        if s_bw > 0 and (bw == 0 or source == "none"):
            loss, jitter, bw = s_loss, s_jitter, s_bw
            source = "server"

    return loss, jitter, bw


def run_experiment_group(group_name, controller_script):
    n_bg = len(BACKGROUND_FLOWS)
    n_burst = len(BURST_SUBFLOWS)
    total_bw = n_bg * UDP_BANDWIDTH + n_burst * BURST_SUB_BW
    print(
        f"\n{'='*60}\nExperiment: {group_name}\nController: {controller_script}\n{'='*60}"
    )

    cleanup()
    kill_ryu()
    ryu_proc = start_ryu(controller_script, group_name)
    results = {}

    print("  Starting Mininet topology...")
    setLogLevel("warning")
    net, c0 = create_topology()
    net.build()
    c0.start()
    net.start()

    net.staticArp()
    configure_select_hash()

    print("  Waiting for active topology convergence (LLDP Discovery)...")
    time.sleep(10)
    print("  Topology core networks stabilized.")

    all_flows = BACKGROUND_FLOWS + BURST_SUBFLOWS
    server_procs = []
    for i, (_, dst_name) in enumerate(all_flows):
        dst = net.get(dst_name)
        if dst:
            port = BASE_PORT + i + 1
            log_path = f"/tmp/iperf_server_flow{i+1}.log"
            if os.path.exists(log_path):
                os.remove(log_path)
            srv_f = open(log_path, "w")
            srv_proc = dst.popen(
                f"iperf -s -u -p {port} -i 1", stdout=srv_f, stderr=subprocess.STDOUT
            )
            server_procs.append((srv_proc, srv_f))
            time.sleep(0.1)
    time.sleep(1)

    print(f"  Phase 1: Starting {len(BACKGROUND_FLOWS)} background flows...")
    client_procs = []
    for i, (src_name, dst_name) in enumerate(BACKGROUND_FLOWS):
        src = net.get(src_name)
        dst = net.get(dst_name)
        if src and dst:
            port = BASE_PORT + i + 1
            log_path = f"/tmp/iperf_flow{i+1}.log"
            if os.path.exists(log_path):
                os.remove(log_path)
            f = open(log_path, "w")
            proc = src.popen(
                f"iperf -c {dst.IP()} -u -b {UDP_BANDWIDTH}M -p {port} -t {TEST_DURATION} -i 5",
                stdout=f,
                stderr=subprocess.STDOUT,
            )
            client_procs.append((proc, f))
            time.sleep(0.5)
            print(f"    Flow {i+1}: {src_name}->{dst_name} started (pid={proc.pid})")

    print(f"  Waiting {BURST_DELAY}s before burst ramp-up...")
    time.sleep(BURST_DELAY)

    for j, (src_name, dst_name) in enumerate(BURST_SUBFLOWS):
        if j > 0:
            time.sleep(BURST_STAGGER)
        remaining = TEST_DURATION - BURST_DELAY - j * BURST_STAGGER
        if remaining <= 0:
            continue
        src = net.get(src_name)
        dst = net.get(dst_name)
        if src and dst:
            port = BASE_PORT + n_bg + j + 1
            log_path = f"/tmp/iperf_burst{j}.log"
            if os.path.exists(log_path):
                os.remove(log_path)
            f = open(log_path, "w")
            proc = src.popen(
                f"iperf -c {dst.IP()} -u -b {BURST_SUB_BW}M -p {port} -t {remaining} -i 5",
                stdout=f,
                stderr=subprocess.STDOUT,
            )
            client_procs.append((proc, f))
            time.sleep(0.5)
            print(
                f"  Sub-flow {chr(65+j)}: {src_name}->{dst_name} @ {BURST_SUB_BW}Mbps started (pid={proc.pid})"
            )

    last_burst_start = BURST_DELAY + (len(BURST_SUBFLOWS) - 1) * BURST_STAGGER
    wait_time = TEST_DURATION - last_burst_start + 5
    print(f"  Running test for remaining {wait_time}s...")
    time.sleep(wait_time)

    for proc, f in client_procs:
        proc.terminate()
        proc.wait()
        f.close()
    for proc, f in server_procs:
        proc.terminate()
        proc.wait()
        f.close()

    for i, (src_name, dst_name) in enumerate(BACKGROUND_FLOWS):
        flow_label = f"Flow {i+1}"
        loss, jitter, bw = _collect_flow_result(
            f"/tmp/iperf_flow{i+1}.log",
            f"/tmp/iperf_server_flow{i+1}.log",
            UDP_BANDWIDTH,
        )
        results[flow_label] = {
            "loss_pct": loss,
            "jitter_ms": jitter,
            "bandwidth_mbps": bw,
        }

    burst_losses, burst_jitters, burst_bws = [], [], []
    for j, (src_name, dst_name) in enumerate(BURST_SUBFLOWS):
        loss, jitter, bw = _collect_flow_result(
            f"/tmp/iperf_burst{j}.log",
            f"/tmp/iperf_server_flow{n_bg + j + 1}.log",
            BURST_SUB_BW,
        )
        if bw > 0:
            burst_losses.append(loss)
            burst_jitters.append(jitter)
            burst_bws.append(bw)

    if burst_bws:
        results["Flow 4 (burst)"] = {
            "loss_pct": sum(burst_losses) / len(burst_losses),
            "jitter_ms": sum(burst_jitters) / len(burst_jitters),
            "bandwidth_mbps": sum(burst_bws),
        }
    else:
        results["Flow 4 (burst)"] = {"loss_pct": 0, "jitter_ms": 0, "bandwidth_mbps": 0}

    net.stop()
    ryu_proc.terminate()
    ryu_proc.wait()
    print("  Ryu stopped")
    cleanup()
    return results


def print_summary(all_results):
    """Print comparative summary of all experiment groups."""
    n_bg = len(BACKGROUND_FLOWS)
    n_burst = len(BURST_SUBFLOWS)
    total_bw = n_bg * UDP_BANDWIDTH + n_burst * BURST_SUB_BW
    print(f"\n{'='*60}")
    print(f"EXPERIMENT SUMMARY: Probabilistic Hash Collision + Ramp-up")
    print(f"{'='*60}")
    print(f"Topology: Fat-Tree k=4, 4 equal-cost Core paths (Pod 0 <-> Pod 3)")
    print(
        f"Link capacity: {CORE_LINK_BW}Mbps per link, {4 * CORE_LINK_BW}Mbps total cross-section"
    )
    print(
        f"Background: {n_bg} x {UDP_BANDWIDTH}Mbps = {n_bg * UDP_BANDWIDTH}Mbps (Phase 1)"
    )
    print(
        f"Burst:      {n_burst} x {BURST_SUB_BW}Mbps = {n_burst * BURST_SUB_BW}Mbps "
        f"(ramp-up, t={BURST_DELAY}s, stagger={BURST_STAGGER}s)"
    )
    print(f"Total:      {total_bw}Mbps on {4 * CORE_LINK_BW}Mbps capacity")
    print()

    flow_names = [f"Flow {i+1}" for i in range(n_bg)] + ["Flow 4 (burst)"]
    header = f"{'Group':<15} {'Flow':<16} {'Loss%':>8} {'Jitter':>10} {'BW':>10}"
    print(header)
    print("-" * len(header))

    for group, results in all_results.items():
        if not results:
            print(f"{group:<15} {'':16} {'N/A':>8} {'N/A':>10} {'N/A':>10}")
            continue

        for flow_name in flow_names:
            if flow_name in results:
                r = results[flow_name]
                loss_str = f"{r['loss_pct']:.1f}%" if r["loss_pct"] >= 0 else "N/A"
                jitter_str = f"{r['jitter_ms']:.3f}ms" if r["jitter_ms"] >= 0 else "N/A"
                bw_str = (
                    f"{r['bandwidth_mbps']:.1f}Mbps"
                    if r["bandwidth_mbps"] >= 0
                    else "N/A"
                )
                print(
                    f"{group:<15} {flow_name:<16} {loss_str:>8} {jitter_str:>10} {bw_str:>10}"
                )
        print()


def main():
    global TEST_DURATION, UDP_BANDWIDTH, BURST_DELAY

    import argparse

    parser = argparse.ArgumentParser(
        description="Fat-Tree pigeonhole collision experiment"
    )
    parser.add_argument(
        "--group",
        choices=["l2", "threshold", "predictive", "all"],
        default="all",
        help="Which group to test",
    )
    parser.add_argument("--duration", type=int, default=TEST_DURATION)
    parser.add_argument("--bw", type=float, default=UDP_BANDWIDTH)
    parser.add_argument("--burst-delay", type=int, default=BURST_DELAY)
    parser.add_argument(
        "--iters", type=int, default=5, help="Number of iterations per test group"
    )
    args = parser.parse_args()

    TEST_DURATION = args.duration
    UDP_BANDWIDTH = args.bw
    BURST_DELAY = args.burst_delay
    iters = args.iters

    data_dir = os.path.join(PROJECT_ROOT, "data")
    os.makedirs(data_dir, exist_ok=True)

    os.system("pkill -f ryu-manager 2>/dev/null")
    time.sleep(1)
    cleanup()
    time.sleep(2)

    if args.group == "all":
        groups = ["l2", "threshold", "predictive"]
    else:
        groups = [args.group]

    # 初始化单轮详细结果 CSV 文件的表头
    iteration_csv_path = os.path.join(data_dir, "iteration_results.csv")
    with open(iteration_csv_path, "w", newline="") as f_iter:
        writer = csv.writer(f_iter)
        writer.writerow(
            ["group", "iteration", "flow", "loss_pct", "jitter_ms", "bandwidth_mbps"]
        )

    all_groups_avg = {}

    for group in groups:
        # 用于缓存该组内所有轮次的流数据以计算均值
        group_history = {}

        for it in range(1, iters + 1):
            print(f"\n--- Group: {group} | Iteration {it}/{iters} ---")
            results = run_experiment_group(group, CONTROLLERS[group])

            if not results:
                continue

            # 实时写入当前轮次的数据至 iteration_results.csv
            with open(iteration_csv_path, "a", newline="") as f_iter:
                writer = csv.writer(f_iter)
                for flow_name, r in results.items():
                    writer.writerow(
                        [
                            group,
                            it,
                            flow_name,
                            r["loss_pct"],
                            r["jitter_ms"],
                            r["bandwidth_mbps"],
                        ]
                    )

                    if flow_name not in group_history:
                        group_history[flow_name] = {
                            "loss_pct": [],
                            "jitter_ms": [],
                            "bandwidth_mbps": [],
                        }
                    group_history[flow_name]["loss_pct"].append(r["loss_pct"])
                    group_history[flow_name]["jitter_ms"].append(r["jitter_ms"])
                    group_history[flow_name]["bandwidth_mbps"].append(
                        r["bandwidth_mbps"]
                    )

            time.sleep(2)

        # 计算并保存该组所有轮次的平均值
        group_avg = {}
        for flow_name, metrics in group_history.items():
            avg_loss = (
                sum(metrics["loss_pct"]) / len(metrics["loss_pct"])
                if metrics["loss_pct"]
                else 0.0
            )
            avg_jitter = (
                sum(metrics["jitter_ms"]) / len(metrics["jitter_ms"])
                if metrics["jitter_ms"]
                else 0.0
            )
            avg_bw = (
                sum(metrics["bandwidth_mbps"]) / len(metrics["bandwidth_mbps"])
                if metrics["bandwidth_mbps"]
                else 0.0
            )

            group_avg[flow_name] = {
                "loss_pct": avg_loss,
                "jitter_ms": avg_jitter,
                "bandwidth_mbps": avg_bw,
            }
        all_groups_avg[group] = group_avg

    # 保存计算好的所有组流平均数据至文件 average_results.csv
    average_csv_path = os.path.join(data_dir, "average_results.csv")
    with open(average_csv_path, "w", newline="") as f_avg:
        writer = csv.writer(f_avg)
        writer.writerow(
            ["group", "flow", "avg_loss_pct", "avg_jitter_ms", "avg_bandwidth_mbps"]
        )
        for group, flows in all_groups_avg.items():
            for flow_name, metrics in flows.items():
                writer.writerow(
                    [
                        group,
                        flow_name,
                        f"{metrics['loss_pct']:.2f}",
                        f"{metrics['jitter_ms']:.3f}",
                        f"{metrics['bandwidth_mbps']:.2f}",
                    ]
                )

    # 打印最终的聚合平均值总结界面
    print_summary(all_groups_avg)


if __name__ == "__main__":
    main()
