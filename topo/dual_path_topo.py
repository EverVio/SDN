"""
双路径拓扑：4 交换机 + 4 主机
路径 A: s1 -> s2 -> s4
路径 B: s1 -> s3 -> s4
所有核心链路带宽 10 Mbps
"""

from mininet.net import Mininet
from mininet.node import RemoteController, OVSSwitch
from mininet.cli import CLI
from mininet.link import TCLink
from mininet.log import setLogLevel


def create_topology():
    # 创建网络对象
    net = Mininet(
        controller=None,  # 不用内置控制器
        switch=OVSSwitch,  # 使用 OVS
        link=TCLink,  # 支持带宽限制
        autoSetMacs=True,  # 自动设置 MAC 地址
    )

    # 添加远程控制器（连接到 Ryu）
    c0 = net.addController(
        "c0",
        controller=RemoteController,
        ip="127.0.0.1",
        port=6633,
    )

    # 添加交换机（protocols 指定 OpenFlow 版本）
    s1 = net.addSwitch("s1", protocols="OpenFlow13")
    s2 = net.addSwitch("s2", protocols="OpenFlow13")
    s3 = net.addSwitch("s3", protocols="OpenFlow13")
    s4 = net.addSwitch("s4", protocols="OpenFlow13")

    # 添加主机
    h1 = net.addHost("h1")
    h2 = net.addHost("h2")
    h3 = net.addHost("h3")
    h4 = net.addHost("h4")

    # 添加接入链路（不设带宽限制，默认无限带宽）
    net.addLink(h1, s1)
    net.addLink(h2, s1)
    net.addLink(h3, s4)
    net.addLink(h4, s4)

    # 添加核心链路 — 双路径，带宽 10 Mbps
    net.addLink(s1, s2, bw=10)  # 路径 A 第一段
    net.addLink(s2, s4, bw=10)  # 路径 A 第二段
    net.addLink(s1, s3, bw=10)  # 路径 B 第一段
    net.addLink(s3, s4, bw=10)  # 路径 B 第二段

    # 构建网络、启动控制器和交换机
    net.build()
    c0.start()
    net.start()

    # 等待交换机连接控制器
    import time

    time.sleep(2)

    print("\n=== 拓扑已启动 ===")
    print("路径 A: h1/h2 -> s1 -> s2 -> s4 -> h3/h4")
    print("路径 B: h1/h2 -> s1 -> s3 -> s4 -> h3/h4")
    print("链路带宽: 10 Mbps (核心链路)")
    print("进入 Mininet CLI...\n")

    # 进入交互式命令行
    CLI(net)

    # 退出时清理
    net.stop()


if __name__ == "__main__":
    setLogLevel("info")
    create_topology()
