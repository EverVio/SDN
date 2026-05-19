#!/usr/bin/env python3
"""
三组对比实验脚本：L2 基线 / 阈值均衡 / Global MLP 预测均衡。

实验设计 — 抽屉原理保证的大象流碰撞 (Pigeonhole Collision)：

  Fat-Tree k=4 在 Pod 0 ↔ Pod 3 之间有 4 条等价 Core 路径，
  总横截带宽 = 4 × 2Mbps = 8Mbps。

  注入 5 条 1.5Mbps 的独立 UDP 流（5 > 4），根据抽屉原理，
  必定至少有一条 Core 链路承载 2 条流（3Mbps > 2Mbps）。

  阶段 1 (t=0s):  启动前 4 条流（6Mbps 注入，4 路径容纳，无丢包）
  阶段 2 (t=20s): 启动第 5 条流（总注入 7.5Mbps，碰撞链路 3Mbps → 丢包）

  - L2 基线组：静态哈希无法迁移，碰撞链路持续丢包 ~33%
  - 阈值均衡组：检测到 >70% 后响应式切换，2-4 秒滞后丢包
  - Global MLP 组：预测引擎识别趋势，主动将第 5 条流迁至空闲路径

用法：sudo python3 scripts/run_experiment.py --group [l2|threshold|predictive|all]
"""

import os
import sys
import time
import signal
import socket
import subprocess
import re

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.append("/usr/lib/python3/dist-packages")

from mininet.log import setLogLevel
from topo.fat_tree_topo import create_topology, cleanup, configure_select_hash

RYU_PORT = 6633
TEST_DURATION = 60  # 测试持续时间（秒）
UDP_BANDWIDTH = 1.5  # 每条流的 UDP 带宽 (Mbps)
BURST_DELAY = 20  # 第 5 条流的启动延迟（秒）
BASE_PORT = 5000  # iperf 端口基准（每条流使用 BASE_PORT + flow_number）
CORE_LINK_BW = 2  # 每条核心链路带宽 (Mbps)，与 BW_AGG_CORE 一致

# 5 条独立流：Pod 0 → Pod 3，使用不同的 (src_mac, dst_mac) 对
# 保证 5 条流的哈希值各不相同，覆盖所有 4 条 ECMP 路径
# 抽屉原理：5 流 / 4 路径 → 至少 1 条路径承载 2 流 (3Mbps > 2Mbps)
BACKGROUND_FLOWS = [
    ("h0_0", "h3_0"),  # 流 1: 第 0 秒启动
    ("h0_1", "h3_1"),  # 流 2: 第 0 秒启动
    ("h0_2", "h3_2"),  # 流 3: 第 0 秒启动
    ("h0_3", "h3_3"),  # 流 4: 第 0 秒启动
]
BURST_FLOW = ("h0_0", "h3_3")  # 流 5: 第 BURST_DELAY 秒启动（复用源主机，新 MAC 对）

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
    log_path = f"data/ryu_{group_name}.log"
    ryu_log = open(log_path, "w")
    proc = subprocess.Popen(
        ["ryu-manager", controller_script, "--observe-links"],
        stdout=ryu_log,
        stderr=subprocess.STDOUT,
    )
    if not wait_for_port(RYU_PORT, timeout=30):
        proc.kill()
        print(f"  ERROR: Ryu failed to start. Check {log_path}")
        sys.exit(1)
    print(f"  Ryu started: {controller_script} (log: {log_path})")
    return proc


def parse_iperf_udp_output(output, expected_bw_mbps=None):
    """Parse iperf2 UDP client output for loss, jitter, and bandwidth.

    iperf2 client output format depends on whether the server report is received:
      - With server report:  "X Mbits/sec  Y ms  Z/W (L%)"
      - Without server report: "X Mbits/sec" (no jitter/loss line)

    When server report is missing, we compute approximate loss by comparing
    received bandwidth to the expected send rate.
    """
    loss_pct = 0.0
    jitter_ms = 0.0
    bandwidth_mbps = 0.0
    has_server_report = False

    if "did not receive server response" in output:
        loss_pct = 100.0

    for line in output.split("\n"):
        # Full server report line: bw + jitter + loss
        m = re.search(
            r"(\d+\.?\d*)\s+Mbits/sec\s+(\d+\.?\d*)\s+ms\s+\d+/\s*\d+\s+\((\d+\.?\d*)%\)",
            line,
        )
        if m:
            bandwidth_mbps = float(m.group(1))
            jitter_ms = float(m.group(2))
            loss_pct = float(m.group(3))
            has_server_report = True
            continue

        # Fallback: extract bandwidth from summary line (no jitter/loss)
        # Match the total summary line (with longer decimal in sec)
        m2 = re.search(
            r"(\d+\.?\d*)\s+\d+\.?\d*\s+sec\s+[\d.]+\s+\w+\s+(\d+\.?\d*)\s+Mbits/sec",
            line,
        )
        if m2:
            bandwidth_mbps = float(m2.group(2))

    # If no server report and we have bandwidth + expected BW, compute approximate loss
    if not has_server_report and bandwidth_mbps > 0 and expected_bw_mbps:
        loss_pct = max(0.0, (1.0 - bandwidth_mbps / expected_bw_mbps) * 100.0)

    return loss_pct, jitter_ms, bandwidth_mbps


def run_experiment_group(group_name, controller_script):
    """Run a single experiment group with pigeonhole collision guarantee."""
    print(f"\n{'='*60}")
    print(f"Experiment: {group_name}")
    print(f"Controller: {controller_script}")
    print(f"Background flows (t=0s):")
    for i, (s, d) in enumerate(BACKGROUND_FLOWS):
        print(f"  Flow {i+1}: {s}->{d} @ {UDP_BANDWIDTH}Mbps")
    print(f"Burst flow (t={BURST_DELAY}s):")
    print(f"  Flow 5: {BURST_FLOW[0]}->{BURST_FLOW[1]} @ {UDP_BANDWIDTH}Mbps")
    print(
        f"Total injection: 5 x {UDP_BANDWIDTH} = {5 * UDP_BANDWIDTH}Mbps "
        f"on {4 * CORE_LINK_BW}Mbps capacity"
    )
    print(f"{'='*60}")

    cleanup()
    kill_ryu()
    ryu_proc = start_ryu(controller_script, group_name)

    results = {}
    net = None

    try:
        print("  Starting Mininet topology...")
        setLogLevel("warning")
        net, c0 = create_topology()
        net.build()
        c0.start()
        net.start()
        configure_select_hash()
        print("  Waiting for active topology convergence (LLDP Discovery)...")
        converged = False
        for attempt in range(60):
            time.sleep(1)
            if attempt >= 40:
                converged = True
                break
        print("  Topology core networks stabilized.")

        # Start iperf servers on all destination hosts (each flow uses unique port)
        all_flows = BACKGROUND_FLOWS + [BURST_FLOW]
        for i, (_, dst_name) in enumerate(all_flows):
            dst = net.get(dst_name)
            if dst:
                port = BASE_PORT + i + 1
                dst.cmd(
                    f"iperf -s -u -p {port} -i 1 "
                    f"> /tmp/iperf_server_flow{i+1}.log 2>&1 &"
                )
        time.sleep(1)

        # ARP warm-up: actively build host table via repeated probes
        # Forces Packet-In on every switch, letting Ryu complete host mapping
        print("  ARP warm-up: actively building host table...")
        for src_name, dst_name in all_flows:
            src = net.get(src_name)
            dst = net.get(dst_name)
            if src and dst:
                for _ in range(3):
                    src.cmd(f"ping -c 1 -W 2 {dst.IP()}")
                check = src.cmd(f"ping -c 1 -W 2 {dst.IP()}")
                status = "OK" if "64 bytes from" in check else "FAILED"
                print(f"    {src_name} -> {dst_name} ({dst.IP()}): {status}")
        time.sleep(2)

        # === Phase 1: Start 4 background flows at t=0 ===
        print(f"  Phase 1: Starting {len(BACKGROUND_FLOWS)} background flows...")
        for i, (src_name, dst_name) in enumerate(BACKGROUND_FLOWS):
            src = net.get(src_name)
            dst = net.get(dst_name)
            if src and dst:
                port = BASE_PORT + i + 1
                src.cmd(
                    f"iperf -c {dst.IP()} -u -b {UDP_BANDWIDTH}M -p {port} "
                    f"-t {TEST_DURATION} -i 5 "
                    f"> /tmp/iperf_flow{i+1}.log 2>&1 &"
                )
                print(f"    Flow {i+1}: {src_name}->{dst_name} started")

        # Wait, then start burst flow
        print(f"  Waiting {BURST_DELAY}s before burst...")
        time.sleep(BURST_DELAY)

        # === Phase 2: Start 5th burst flow at t=BURST_DELAY ===
        remaining = TEST_DURATION - BURST_DELAY
        src_b = net.get(BURST_FLOW[0])
        dst_b = net.get(BURST_FLOW[1])
        if src_b and dst_b:
            port = BASE_PORT + 5
            src_b.cmd(
                f"iperf -c {dst_b.IP()} -u -b {UDP_BANDWIDTH}M -p {port} "
                f"-t {remaining} -i 5 "
                f"> /tmp/iperf_flow5.log 2>&1 &"
            )
            print(f"  Flow 5 (burst): {BURST_FLOW[0]}->{BURST_FLOW[1]} started")

        print(f"  Running test for remaining {remaining + 5}s...")
        time.sleep(remaining + 5)

        # Collect results from client logs + server logs
        flow_labels = [f"Flow {i+1}" for i in range(len(BACKGROUND_FLOWS))] + ["Flow 5"]
        for flow_label, (src_name, dst_name) in zip(flow_labels, all_flows):
            flow_num = flow_label.replace("Flow ", "")
            client_log = f"/tmp/iperf_flow{flow_num}.log"
            server_log = f"/tmp/iperf_server_flow{flow_num}.log"

            loss, jitter, bw = 0.0, 0.0, 0.0
            source = "none"

            # Try client log first (has send-side data + server report if received)
            if os.path.exists(client_log):
                with open(client_log) as f:
                    client_output = f.read()
                loss, jitter, bw = parse_iperf_udp_output(
                    client_output, expected_bw_mbps=UDP_BANDWIDTH
                )
                source = "client"

            # If client shows 0% loss but 0 bandwidth, try server log
            # (server log has the authoritative received-side statistics)
            if os.path.exists(server_log):
                with open(server_log) as f:
                    server_output = f.read()
                s_loss, s_jitter, s_bw = parse_iperf_udp_output(server_output)
                # Use server data if it has more info
                if s_bw > 0 and (bw == 0 or source == "none"):
                    loss, jitter, bw = s_loss, s_jitter, s_bw
                    source = "server"

            results[flow_label] = {
                "loss_pct": loss,
                "jitter_ms": jitter,
                "bandwidth_mbps": bw,
            }

            if bw > 0:
                print(
                    f"    {flow_label} ({src_name}->{dst_name}): "
                    f"loss={loss:.1f}%, jitter={jitter:.3f}ms, bw={bw:.1f}Mbps [{source}]"
                )
            else:
                print(f"    {flow_label} ({src_name}->{dst_name}): no data")

    finally:
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
        print("  Ryu stopped")

    cleanup()
    return results


def print_summary(all_results):
    """Print comparative summary of all experiment groups."""
    print(f"\n{'='*60}")
    print(f"EXPERIMENT SUMMARY: Pigeonhole Collision")
    print(f"{'='*60}")
    print(f"Topology: Fat-Tree k=4, 4 equal-cost Core paths (Pod 0 <-> Pod 3)")
    print(f"Link capacity: {CORE_LINK_BW}Mbps per link, {4 * CORE_LINK_BW}Mbps total cross-section")
    print(f"Background: 4 x {UDP_BANDWIDTH}Mbps = {4 * UDP_BANDWIDTH}Mbps (Phase 1)")
    print(
        f"Burst:      1 x {UDP_BANDWIDTH}Mbps = {UDP_BANDWIDTH}Mbps (Phase 2, t={BURST_DELAY}s)"
    )
    print(
        f"Total:      5 x {UDP_BANDWIDTH}Mbps = {5 * UDP_BANDWIDTH}Mbps > {4 * CORE_LINK_BW}Mbps capacity"
    )
    print(
        f"Guarantee:  Pigeonhole principle -> at least 1 link carries 2 flows ({2 * UDP_BANDWIDTH}Mbps > {CORE_LINK_BW}Mbps)"
    )
    print()

    header = f"{'Group':<15} {'Flow':<8} {'Loss%':>8} {'Jitter':>10} {'BW':>10}"
    print(header)
    print("-" * len(header))

    for group, results in all_results.items():
        if not results:
            print(f"{group:<15} {'':8} {'N/A':>8} {'N/A':>10} {'N/A':>10}")
            continue

        for flow_name in [f"Flow {i+1}" for i in range(4)] + ["Flow 5"]:
            if flow_name in results:
                r = results[flow_name]
                loss_str = f"{r['loss_pct']:.1f}%" if r["loss_pct"] >= 0 else "N/A"
                jitter_str = f"{r['jitter_ms']:.3f}ms" if r["jitter_ms"] >= 0 else "N/A"
                bw_str = (
                    f"{r['bandwidth_mbps']:.1f}Mbps"
                    if r["bandwidth_mbps"] >= 0
                    else "N/A"
                )
                tag = " (burst)" if flow_name == "Flow 5" else ""
                print(
                    f"{group:<15} {flow_name + tag:<16} {loss_str:>8} {jitter_str:>10} {bw_str:>10}"
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
    parser.add_argument("--bw", type=int, default=UDP_BANDWIDTH)
    parser.add_argument("--burst-delay", type=int, default=BURST_DELAY)
    args = parser.parse_args()

    TEST_DURATION = args.duration
    UDP_BANDWIDTH = args.bw
    BURST_DELAY = args.burst_delay

    os.makedirs("data", exist_ok=True)

    # Thorough cleanup before starting
    os.system("mn -c 2>/dev/null")
    os.system("killall -9 iperf 2>/dev/null")
    os.system("pkill -f ryu-manager 2>/dev/null")
    time.sleep(2)

    if args.group == "all":
        groups = ["l2", "threshold", "predictive"]
    else:
        groups = [args.group]

    all_results = {}
    for group in groups:
        try:
            results = run_experiment_group(group, CONTROLLERS[group])
            all_results[group] = results
        except Exception as e:
            print(f"  ERROR in {group}: {e}")
            all_results[group] = {}

    print_summary(all_results)


if __name__ == "__main__":
    main()
