import os
from mininet.net import Mininet
from mininet.node import RemoteController, OVSSwitch
from mininet.link import TCLink

K = 4
PODS = K
EDGE_PER_POD = K // 2   # 2
AGG_PER_POD = K // 2     # 2
HOST_PER_EDGE = K // 2   # 2

BW_ACCESS = 0    # unlimited
BW_EDGE_AGG = 10
BW_AGG_CORE = 10


def _edge_dpid(pod, idx):
    """Edge switch DPID: 1..8"""
    return pod * EDGE_PER_POD + idx + 1


def _agg_dpid(pod, idx):
    """Aggregation switch DPID: 9..16"""
    return PODS * EDGE_PER_POD + pod * AGG_PER_POD + idx + 1


def _core_dpid(idx):
    """Core switch DPID: 17..20"""
    return PODS * EDGE_PER_POD + PODS * AGG_PER_POD + idx + 1


def create_topology(controller_ip="127.0.0.1", controller_port=6633):
    """Create Fat-Tree k=4 topology.

    Returns (net, controller) — caller is responsible for build/start/stop.
    """
    net = Mininet(
        controller=None,
        switch=OVSSwitch,
        link=TCLink,
        autoSetMacs=True,
    )

    c0 = net.addController(
        "c0", controller=RemoteController,
        ip=controller_ip, port=controller_port,
    )

    # Add switches
    for pod in range(PODS):
        for i in range(EDGE_PER_POD):
            dpid = _edge_dpid(pod, i)
            net.addSwitch(f"s{dpid}", dpid=dpid, protocols="OpenFlow13")
        for i in range(AGG_PER_POD):
            dpid = _agg_dpid(pod, i)
            net.addSwitch(f"s{dpid}", dpid=dpid, protocols="OpenFlow13")

    for i in range((K // 2) ** 2):
        dpid = _core_dpid(i)
        net.addSwitch(f"s{dpid}", dpid=dpid, protocols="OpenFlow13")

    # Add hosts and access links
    for pod in range(PODS):
        for e_idx in range(EDGE_PER_POD):
            edge_dpid = _edge_dpid(pod, e_idx)
            for h_idx in range(HOST_PER_EDGE):
                host_name = f"h{pod}_{e_idx * HOST_PER_EDGE + h_idx}"
                host = net.addHost(host_name)
                net.addLink(host, net.get(f"s{edge_dpid}"), bw=BW_ACCESS)

    # Edge <-> Aggregation links (within each pod)
    for pod in range(PODS):
        for e_idx in range(EDGE_PER_POD):
            edge_dpid = _edge_dpid(pod, e_idx)
            for a_idx in range(AGG_PER_POD):
                agg_dpid = _agg_dpid(pod, a_idx)
                net.addLink(
                    net.get(f"s{edge_dpid}"), net.get(f"s{agg_dpid}"),
                    bw=BW_EDGE_AGG,
                )

    # Aggregation <-> Core links
    for pod in range(PODS):
        for a_idx in range(AGG_PER_POD):
            agg_dpid = _agg_dpid(pod, a_idx)
            for c_local in range(K // 2):
                core_idx = a_idx * (K // 2) + c_local
                core_dpid = _core_dpid(core_idx)
                net.addLink(
                    net.get(f"s{agg_dpid}"), net.get(f"s{core_dpid}"),
                    bw=BW_AGG_CORE,
                )

    return net, c0


def cleanup():
    """Remove all Fat-Tree OVS bridges."""
    for pod in range(PODS):
        for i in range(EDGE_PER_POD):
            os.system(f"sudo ovs-vsctl --if-exists del-br s{_edge_dpid(pod, i)} 2>/dev/null")
        for i in range(AGG_PER_POD):
            os.system(f"sudo ovs-vsctl --if-exists del-br s{_agg_dpid(pod, i)} 2>/dev/null")
    for i in range((K // 2) ** 2):
        os.system(f"sudo ovs-vsctl --if-exists del-br s{_core_dpid(i)} 2>/dev/null")
    print("Fat-Tree cleanup completed.")
