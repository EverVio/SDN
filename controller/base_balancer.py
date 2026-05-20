"""
负载均衡控制器公共基类
提取 threshold_balancer 和 predictive_balancer 的重复代码
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from abc import abstractmethod

from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER
from ryu.controller.handler import set_ev_cls
from ryu.ofproto import ofproto_v1_3
from ryu.lib import hub
from ryu.lib.packet import packet, ethernet, ether_types, arp
from ryu.topology import event as topo_event

from controller.stats_mixin import StatsMixin
from controller.topology_manager import TopologyManager


class BaseBalancer(app_manager.RyuApp, StatsMixin):
    """负载均衡控制器公共基类，提供：
    - table-miss 规则安装
    - 流表/数据包发送辅助方法
    - ARP 学习与查找
    - 拓扑事件处理（LLDP）
    - 出端口计算（基于子类提供的路径数据）
    """

    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]
    TOPO_DEBOUNCE_SEC = 3.0  # 拓扑变更去抖时间（秒）

    # Valid Fat-Tree k=4 links: (src_dpid, dst_dpid) — bidirectional
    # Edge 1-8, Agg 9-16, Core 17-20
    # Pod 0: edge(1,2) ↔ agg(9,10), agg(9,10) ↔ core(17,18)
    # Pod 1: edge(3,4) ↔ agg(11,12), agg(11,12) ↔ core(17,18)
    # Pod 2: edge(5,6) ↔ agg(13,14), agg(13,14) ↔ core(19,20)
    # Pod 3: edge(7,8) ↔ agg(15,16), agg(15,16) ↔ core(19,20)
    _FAT_TREE_LINKS = {
        # Edge <-> Agg (within each pod)
        frozenset({1, 9}), frozenset({1, 10}), frozenset({2, 9}), frozenset({2, 10}),
        frozenset({3, 11}), frozenset({3, 12}), frozenset({4, 11}), frozenset({4, 12}),
        frozenset({5, 13}), frozenset({5, 14}), frozenset({6, 13}), frozenset({6, 14}),
        frozenset({7, 15}), frozenset({7, 16}), frozenset({8, 15}), frozenset({8, 16}),
        # Agg0 (per pod) <-> Core s17, s18
        frozenset({9, 17}), frozenset({9, 18}),
        frozenset({11, 17}), frozenset({11, 18}),
        frozenset({13, 17}), frozenset({13, 18}),
        frozenset({15, 17}), frozenset({15, 18}),
        # Agg1 (per pod) <-> Core s19, s20
        frozenset({10, 19}), frozenset({10, 20}),
        frozenset({12, 19}), frozenset({12, 20}),
        frozenset({14, 19}), frozenset({14, 20}),
        frozenset({16, 19}), frozenset({16, 20}),
    }

    def __init__(self, *args, **kwargs):
        super(BaseBalancer, self).__init__(*args, **kwargs)
        self.mac_to_port = {}
        self.ip_to_mac = {}
        self.datapaths = {}
        self.topo = TopologyManager()
        self._invalidate_timer = None
        self._ft_ports = {}  # (dpid, peer_dpid) -> port_no, populated by LLDP
        self._topo_built = False
        self._topo_build_timer = hub.spawn_after(15, self._ensure_topo_built)

    # ──────────────────────────────────────────────
    # 交换机连接：下发 table-miss 规则
    # ──────────────────────────────────────────────
    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        datapath = ev.msg.datapath
        parser = datapath.ofproto_parser
        ofproto = datapath.ofproto
        match = parser.OFPMatch()
        actions = [
            parser.OFPActionOutput(ofproto.OFPP_CONTROLLER, ofproto.OFPCML_NO_BUFFER)
        ]
        inst = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS, actions)]
        mod = parser.OFPFlowMod(
            datapath=datapath, priority=0, match=match, instructions=inst
        )
        datapath.send_msg(mod)
        self.datapaths[datapath.id] = datapath
        self.logger.info("Switch %s connected, table-miss installed", datapath.id)

    # ──────────────────────────────────────────────
    # 流表安装 / 数据包发送辅助
    # ──────────────────────────────────────────────
    def add_flow(self, datapath, priority, match, actions, buffer_id=None, idle_timeout=0):
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        inst = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS, actions)]
        kwargs = dict(
            datapath=datapath, priority=priority, match=match, instructions=inst,
            idle_timeout=idle_timeout,
        )
        if buffer_id is not None:
            kwargs["buffer_id"] = buffer_id
        mod = parser.OFPFlowMod(**kwargs)
        datapath.send_msg(mod)

    def add_flow_ovs(self, dpid, priority, match_str, actions_str, idle_timeout=0):
        """Fallback: install a flow rule via ovs-ofctl when datapath is unavailable."""
        import subprocess
        flow = f"priority={priority},{match_str}"
        if idle_timeout:
            flow += f",idle_timeout={idle_timeout}"
        flow += f",{actions_str}"
        subprocess.run(
            ["ovs-ofctl", "add-flow", f"s{dpid}", flow, "-O", "OpenFlow13"],
            capture_output=True,
        )

    def _send_packet(self, datapath, in_port, out_port, msg):
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        actions = [parser.OFPActionOutput(out_port)]
        data = msg.data if msg.buffer_id == ofproto.OFP_NO_BUFFER else None
        out = parser.OFPPacketOut(
            datapath=datapath,
            buffer_id=msg.buffer_id,
            in_port=in_port,
            actions=actions,
            data=data,
        )
        datapath.send_msg(out)

    def _install_reverse_rule(self, datapath, mac, in_port):
        """在当前交换机安装反向流表：eth_dst=mac -> in_port"""
        parser = datapath.ofproto_parser
        match = parser.OFPMatch(eth_dst=mac)
        actions = [parser.OFPActionOutput(in_port)]
        self.add_flow(datapath, 10, match, actions)

    # ──────────────────────────────────────────────
    # ARP 学习与查找
    # ──────────────────────────────────────────────
    def _arp_lookup(self, ip):
        return self.ip_to_mac.get(ip)

    def _learn_arp_binding(self, arp_pkt, eth_src):
        if arp_pkt.src_ip and arp_pkt.src_mac:
            self.ip_to_mac[arp_pkt.src_ip] = arp_pkt.src_mac
        if arp_pkt.opcode == arp.ARP_REPLY:
            if arp_pkt.dst_ip and arp_pkt.dst_mac:
                self.ip_to_mac[arp_pkt.dst_ip] = arp_pkt.dst_mac

    # ──────────────────────────────────────────────
    # 出端口计算（基于子类提供的路径数据）
    # ──────────────────────────────────────────────
    def _get_out_port(self, from_dpid, to_dpid):
        """计算从 from_dpid 到 to_dpid 的出端口"""
        fwd = self._get_active_fwd_ports()
        rev = self._get_active_rev_ports()

        if fwd and from_dpid in fwd:
            fwd_chain = list(fwd.keys())
            try:
                idx = fwd_chain.index(from_dpid)
                if idx + 1 < len(fwd_chain) and fwd_chain[idx + 1] == to_dpid:
                    return fwd[from_dpid]
            except ValueError:
                pass

        if rev and from_dpid in rev:
            rev_chain = list(rev.keys())
            try:
                idx = rev_chain.index(from_dpid)
                if idx + 1 < len(rev_chain) and rev_chain[idx + 1] == to_dpid:
                    return rev[from_dpid]
            except ValueError:
                pass

        return None

    @abstractmethod
    def _get_active_fwd_ports(self):
        """返回当前活跃路径的正向端口映射 dict {dpid: out_port}"""

    @abstractmethod
    def _get_active_rev_ports(self):
        """返回当前活跃路径的反向端口映射 dict {dpid: out_port}"""

    @abstractmethod
    def _invalidate_paths(self):
        """拓扑变化时清除路径缓存，触发重新计算"""

    # ──────────────────────────────────────────────
    # 拓扑发现事件处理（LLDP）
    # ──────────────────────────────────────────────
    FAT_TREE_MAX_DPID = 20

    @set_ev_cls(topo_event.EventSwitchEnter)
    def _switch_add_handler(self, ev):
        dpid = ev.switch.dp.id
        self.datapaths[dpid] = ev.switch.dp
        if dpid <= self.FAT_TREE_MAX_DPID:
            self.topo.add_switch(dpid)
            self.logger.info("Topology: switch s%d added", dpid)
        # Build Fat-Tree once all 20 expected switches are present
        if not self._topo_built:
            ft_count = sum(1 for d in self.datapaths if d <= self.FAT_TREE_MAX_DPID)
            if ft_count >= self.FAT_TREE_MAX_DPID:
                self._build_fat_tree_topo()

    @set_ev_cls(topo_event.EventSwitchLeave)
    def _switch_del_handler(self, ev):
        dpid = ev.switch.dp.id
        self.datapaths.pop(dpid, None)
        if dpid <= self.FAT_TREE_MAX_DPID:
            self.topo.remove_switch(dpid)
            self._topo_built = False
            self.logger.info("Topology: switch s%d removed", dpid)

    @set_ev_cls(topo_event.EventLinkAdd)
    def _link_add_handler(self, ev):
        src = ev.link.src
        dst = ev.link.dst
        key = frozenset({src.dpid, dst.dpid})
        if key in self._FAT_TREE_LINKS:
            self._ft_ports[(src.dpid, dst.dpid)] = src.port_no
            self._ft_ports[(dst.dpid, src.dpid)] = dst.port_no
            self.logger.info(
                "Topology: link s%d:p%d -> s%d:p%d",
                src.dpid, src.port_no, dst.dpid, dst.port_no,
            )
            if self._topo_built:
                self.topo.add_link(src.dpid, src.port_no, dst.dpid, dst.port_no)

    @set_ev_cls(topo_event.EventLinkDelete)
    def _link_del_handler(self, ev):
        src = ev.link.src
        dst = ev.link.dst
        key = frozenset({src.dpid, dst.dpid})
        if key in self._FAT_TREE_LINKS:
            self._ft_ports.pop((src.dpid, dst.dpid), None)
            self._ft_ports.pop((dst.dpid, src.dpid), None)
            self.logger.info("Topology: link s%d -> s%d removed", src.dpid, dst.dpid)
            if self._topo_built:
                self.topo.remove_link(src.dpid, dst.dpid)
                self._debounced_invalidate()

    # Static port assignments for Fat-Tree k=4
    # Port numbering follows Mininet's link-creation order in fat_tree_topo.py
    _FT_PORT_MAP = {
        # Edge switch host ports: port 1 = h{pod}_{e_idx*2}, port 2 = h{pod}_{e_idx*2+1}
        # Edge switch agg ports: port 3 = agg_idx_0, port 4 = agg_idx_1
        # Agg switch edge ports: port 1 = edge_idx_0, port 2 = edge_idx_1
        # Agg switch core ports: port 3 = core_idx_0, port 4 = core_idx_1
        # Core switch ports: port 1 = agg(pod0,idx0), port 2 = agg(pod0,idx1),
        #                    port 3 = agg(pod1,idx0), port 4 = agg(pod1,idx1)
        # (for core 17,18) or agg(pod2/3) (for core 19,20)

        # Pod 0: edge(1,2) <-> agg(9,10)
        (1, 9): 3, (9, 1): 1, (1, 10): 4, (10, 1): 1,
        (2, 9): 3, (9, 2): 2, (2, 10): 4, (10, 2): 2,
        # Pod 1: edge(3,4) <-> agg(11,12)
        (3, 11): 3, (11, 3): 1, (3, 12): 4, (12, 3): 1,
        (4, 11): 3, (11, 4): 2, (4, 12): 4, (12, 4): 2,
        # Pod 2: edge(5,6) <-> agg(13,14)
        (5, 13): 3, (13, 5): 1, (5, 14): 4, (14, 5): 1,
        (6, 13): 3, (13, 6): 2, (6, 14): 4, (14, 6): 2,
        # Pod 3: edge(7,8) <-> agg(15,16)
        (7, 15): 3, (15, 7): 1, (7, 16): 4, (16, 7): 1,
        (8, 15): 3, (15, 8): 2, (8, 16): 4, (16, 8): 2,

        # Agg <-> Core
        # agg0 of each pod connects to core s17, s18
        # agg1 of each pod connects to core s19, s20
        # Pod 0
        (9, 17): 3, (17, 9): 1, (9, 18): 4, (18, 9): 1,
        (10, 19): 3, (19, 10): 1, (10, 20): 4, (20, 10): 1,
        # Pod 1
        (11, 17): 3, (17, 11): 2, (11, 18): 4, (18, 11): 2,
        (12, 19): 3, (19, 12): 2, (12, 20): 4, (20, 12): 2,
        # Pod 2
        (13, 17): 3, (17, 13): 3, (13, 18): 4, (18, 13): 3,
        (14, 19): 3, (19, 14): 3, (14, 20): 4, (20, 14): 3,
        # Pod 3
        (15, 17): 3, (17, 15): 4, (15, 18): 4, (18, 15): 4,
        (16, 19): 3, (19, 16): 4, (16, 20): 4, (20, 16): 4,
    }

    def _ensure_topo_built(self):
        """Timer callback: build Fat-Tree from static port map."""
        if not self._topo_built:
            self._build_fat_tree_topo()

    def _build_fat_tree_topo(self):
        """Build Fat-Tree topology using static port assignments.

        Includes ALL 20 switches so path computation considers the full topology.
        Flow rules for switches not in self.datapaths are installed via ovs-ofctl.
        """
        for dpid in range(1, self.FAT_TREE_MAX_DPID + 1):
            if dpid not in self.topo.G.nodes:
                self.topo.add_switch(dpid)
        for (a, b), port_ab in self._FT_PORT_MAP.items():
            port_ba = self._FT_PORT_MAP.get((b, a))
            if port_ba is not None:
                if not self.topo.G.has_edge(a, b):
                    self.topo.add_link(a, port_ab, b, port_ba)
        self._topo_built = True
        connected = sum(1 for d in range(1, self.FAT_TREE_MAX_DPID + 1)
                        if d in self.datapaths)
        self.logger.info(
            "Topology: Fat-Tree built with %d nodes, %d edges (%d/%d switches connected)",
            len(self.topo.G.nodes), len(self.topo.G.edges),
            connected, self.FAT_TREE_MAX_DPID)

    def _debounced_invalidate(self):
        """Debounce topology change events to avoid clearing paths during LLDP discovery."""
        if self._invalidate_timer is not None:
            self._invalidate_timer.cancel()
        self._invalidate_timer = hub.spawn_after(
            self.TOPO_DEBOUNCE_SEC, self._do_invalidate
        )

    def _do_invalidate(self):
        self._invalidate_timer = None
        self._invalidate_paths()

    # ──────────────────────────────────────────────
    # 统计回复（子类需绑定事件）
    # ──────────────────────────────────────────────
    @set_ev_cls(ofp_event.EventOFPPortStatsReply, MAIN_DISPATCHER)
    def port_stats_reply_handler(self, ev):
        self.handle_port_stats_reply(ev)
