import os
from mininet.net import Mininet
from mininet.node import RemoteController, OVSSwitch
from mininet.link import TCLink

K = 4
PODS = K
EDGE_PER_POD = K // 2
AGG_PER_POD = K // 2
HOST_PER_EDGE = K // 2

BW_ACCESS = 10
BW_EDGE_AGG = 10
BW_AGG_CORE = 2


def _edge_dpid(pod, idx):
    return pod * EDGE_PER_POD + idx + 1


def _agg_dpid(pod, idx):
    return PODS * EDGE_PER_POD + pod * AGG_PER_POD + idx + 1


def _core_dpid(idx):
    return PODS * EDGE_PER_POD + PODS * AGG_PER_POD + idx + 1


def create_topology(controller_ip="127.0.0.1", controller_port=6633):
    net = Mininet(controller=None, switch=OVSSwitch, link=TCLink, autoSetMacs=True)
    c0 = net.addController(
        "c0", controller=RemoteController, ip=controller_ip, port=controller_port
    )

    for pod in range(PODS):
        for i in range(EDGE_PER_POD):
            dpid = _edge_dpid(pod, i)
            net.addSwitch(f"s{dpid}", dpid=f"{dpid:016x}", protocols="OpenFlow13")
        for i in range(AGG_PER_POD):
            dpid = _agg_dpid(pod, i)
            net.addSwitch(f"s{dpid}", dpid=f"{dpid:016x}", protocols="OpenFlow13")

    for i in range((K // 2) ** 2):
        dpid = _core_dpid(i)
        net.addSwitch(f"s{dpid}", dpid=f"{dpid:016x}", protocols="OpenFlow13")

    for pod in range(PODS):
        for e_idx in range(EDGE_PER_POD):
            edge_dpid = _edge_dpid(pod, e_idx)
            for h_idx in range(HOST_PER_EDGE):
                host = net.addHost(f"h{pod}_{e_idx * HOST_PER_EDGE + h_idx}")
                net.addLink(host, net.get(f"s{edge_dpid}"), bw=BW_ACCESS)

    for pod in range(PODS):
        for e_idx in range(EDGE_PER_POD):
            edge_dpid = _edge_dpid(pod, e_idx)
            for a_idx in range(AGG_PER_POD):
                net.addLink(
                    net.get(f"s{edge_dpid}"),
                    net.get(f"s{_agg_dpid(pod, a_idx)}"),
                    bw=BW_EDGE_AGG,
                )

    for pod in range(PODS):
        for a_idx in range(AGG_PER_POD):
            agg_dpid = _agg_dpid(pod, a_idx)
            for c_local in range(K // 2):
                core_dpid = _core_dpid(a_idx * (K // 2) + c_local)
                net.addLink(
                    net.get(f"s{agg_dpid}"),
                    net.get(f"s{core_dpid}"),
                    bw=BW_AGG_CORE,
                    max_queue_size=30,
                )

    return net, c0


def configure_select_hash():
    for pod in range(PODS):
        for i in range(EDGE_PER_POD):
            os.system(
                f"ovs-vsctl set bridge s{_edge_dpid(pod, i)} other_config:group-table-selection-method=dp_hash"
            )
    for pod in range(PODS):
        for i in range(AGG_PER_POD):
            os.system(
                f"ovs-vsctl set bridge s{_agg_dpid(pod, i)} other_config:group-table-selection-method=dp_hash"
            )
    for i in range((K // 2) ** 2):
        os.system(
            f"ovs-vsctl set bridge s{_core_dpid(i)} other_config:group-table-selection-method=dp_hash"
        )


def cleanup():
    os.system("mn -c 2>/dev/null")
    os.system("killall -9 iperf 2>/dev/null")
    output = os.popen("ovs-vsctl list-br 2>/dev/null").read()
    for br in output.strip().split("\n"):
        if br.strip():
            os.system(f"ovs-vsctl --if-exists del-br {br.strip()} 2>/dev/null")
    print("Fat-Tree cleanup completed.")
