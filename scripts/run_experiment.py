#!/usr/bin/env python3
"""
三组对比实验脚本：base 基线 / 阈值均衡 / Global MLP 预测均衡。

实验设计 — 概率哈希碰撞 + 渐进突发 (Probabilistic Hash Collision + Ramp-up)：

  Fat-Tree k=4 在 Pod 0 ↔ Pod 3 之间有 4 条等价 Core 路径，
  总横截带宽 = 4 × 2Mbps = 8Mbps。

  阶段 1 (t=0s):  启动 9 条 0.5Mbps 背景流（4.5Mbps，4 路径各 ~1.125Mbps，无拥塞）
  阶段 2 (t=20s): 渐进启动突发流（6 条 0.25Mbps 子流，间隔 6s，模拟带宽爬升）
    - t=20s: 子流 A 0.25Mbps → 总 4.75Mbps
    - t=26s: 子流 B 0.25Mbps → 总 5.0Mbps
    - t=32s: 子流 C 0.25Mbps → 总 5.25Mbps

  哈希碰撞概率：9 背景流占多条路径，突发流有概率哈希到已占用路径。
  碰撞时该链路超载 > 2Mbps → 丢包。

  - base 基线组：静态哈希，碰撞后持续丢包
  - 阈值均衡组：碰撞后 2-3 秒检测到拥塞，响应式迁移
  - Global MLP 组：预测引擎在爬升阶段识别趋势，主动迁移，0 丢包

用法：sudo python3 scripts/run_experiment.py --group [base|threshold|predictive|all] --iters 5
"""

import os
import sys
import time
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

BURST_SUBFLOWS = [
    ("h0_3", "h3_3"),
    ("h0_0", "h3_3"),
    ("h0_1", "h3_3"),
    ("h0_3", "h3_3"),
    ("h0_0", "h3_3"),
    ("h0_1", "h3_3"),
]

CONTROLLERS = {
    "base": "controller/base_controller.py",
    "threshold": "controller/threshold_balancer.py",
    "predictive": "controller/predictive_balancer.py",
}


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
    time.sleep(0.5)


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
    if "Kbits" in unit:
        return bw_value / 1000.0
    return bw_value


def parse_iperf_udp_output(output, expected_bw_mbps=None):
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

    # 优化点 1：因架构采用完全离线常数级静态拓扑注入，无需冗长的主动发现等待，下调至 2 秒供连接建立
    print("  Waiting for switch connections...")
    time.sleep(2)
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
    time.sleep(0.5)

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
            # 优化点 2：缩短串行启动背景流时带来的累计硬件阻塞等待时间
            time.sleep(0.01)

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
            print(
                f"  Sub-flow {chr(65+j)}: {src_name}->{dst_name} @ {BURST_SUB_BW}Mbps started (pid={proc.pid})"
            )
            # 优化点 3：移除此处产生额外 0.5s 累积偏差的 time.sleep(0.5)，确保子流间隔严格受 BURST_STAGGER 控制

    last_burst_start = BURST_DELAY + (len(BURST_SUBFLOWS) - 1) * BURST_STAGGER
    wait_time = TEST_DURATION - last_burst_start + 2
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
        results["Burst Flows"] = {
            "loss_pct": sum(burst_losses) / len(burst_losses),
            "jitter_ms": sum(burst_jitters) / len(burst_jitters),
            "bandwidth_mbps": sum(burst_bws),
        }
    else:
        results["Burst Flows"] = {"loss_pct": 0, "jitter_ms": 0, "bandwidth_mbps": 0}

    net.stop()
    ryu_proc.terminate()
    ryu_proc.wait()
    print("  Ryu stopped")
    cleanup()
    return results


def print_summary(all_results):
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

    flow_names = [f"Flow {i+1}" for i in range(n_bg)] + ["Burst Flows"]
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
        choices=["base", "threshold", "predictive", "all"],
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
    time.sleep(0.5)
    cleanup()
    time.sleep(1)

    if args.group == "all":
        groups = ["base", "threshold", "predictive"]
    else:
        groups = [args.group]

    all_groups_avg = {}

    for group in groups:
        group_history = {}

        # 为当前 group 创建迭代结果 CSV（覆盖写入表头）
        iteration_csv = os.path.join(data_dir, f"{group}_iteration_results.csv")
        with open(iteration_csv, "w", newline="") as f_iter:
            writer = csv.writer(f_iter)
            writer.writerow(
                [
                    "group",
                    "iteration",
                    "flow",
                    "loss_pct",
                    "jitter_ms",
                    "bandwidth_mbps",
                ]
            )

        for it in range(1, iters + 1):
            print(f"\n--- Group: {group} | Iteration {it}/{iters} ---")
            results = run_experiment_group(group, CONTROLLERS[group])

            if not results:
                continue

            # 追加当前迭代的数据到该 group 的迭代 CSV
            with open(iteration_csv, "a", newline="") as f_iter:
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

            # 优化点 4：缩短多轮迭代之间的闲置等待时间至 0.5s
            time.sleep(0.5)

        # 计算该 group 的平均结果并写入单独的 average CSV
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

        # 保存该 group 的平均结果到 CSV
        avg_csv = os.path.join(data_dir, f"{group}_average_results.csv")
        with open(avg_csv, "w", newline="") as f_avg:
            writer = csv.writer(f_avg)
            writer.writerow(
                ["group", "flow", "avg_loss_pct", "avg_jitter_ms", "avg_bandwidth_mbps"]
            )
            for flow_name, metrics in group_avg.items():
                writer.writerow(
                    [
                        group,
                        flow_name,
                        f"{metrics['loss_pct']:.2f}",
                        f"{metrics['jitter_ms']:.3f}",
                        f"{metrics['bandwidth_mbps']:.2f}",
                    ]
                )

        all_groups_avg[group] = group_avg

    print_summary(all_groups_avg)


if __name__ == "__main__":
    main()
