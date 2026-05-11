import os
import sys
import time
import atexit
from mininet.log import setLogLevel

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from topo.dual_path_topo import create_topology
from scripts.traffic_gen import generate_sawtooth_noise_commands, generate_step_commands


def cleanup():
    """清理遗留的 OVS 网桥"""
    switches = ["s1", "s2", "s3", "s4"]
    for sw in switches:
        os.system(f"sudo ovs-vsctl --if-exists del-br {sw} 2>/dev/null")
    print("Cleanup OVS completed.")


def run_traffic(net, pattern="sawtooth", duration=120):
    """在 Mininet 环境中执行流量生成"""
    if pattern == "sawtooth":
        cmds = generate_sawtooth_noise_commands(duration)
    else:
        cmds = generate_step_commands(duration)

    h1 = net.get("h1")
    h3 = net.get("h3")

    h3.cmd("iperf -s -u &")
    time.sleep(1)

    for t_start, bw in cmds:
        h1.cmd(f"iperf -c {h3.IP()} -u -b {bw}M -t 3 -i 1 &")
        time.sleep(3)

    print("Traffic generation finished.")
    h3.cmd("killall -9 iperf 2>/dev/null")


def main():
    atexit.register(cleanup)
    cleanup()
    setLogLevel("info")

    net, c0 = create_topology()
    net.build()
    c0.start()
    net.start()
    time.sleep(2)
    print("\n=== 拓扑已启动 ===")

    print("开始自动生成流量...")
    run_traffic(net, pattern="sawtooth", duration=120)

    print("\n=== 流量生成结束，清理拓扑 ===")
    net.stop()


if __name__ == "__main__":
    main()
