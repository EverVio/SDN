"""
阈值响应式负载均衡控制器（Phase 5 对照组）
- 拓扑发现（LLDP + networkx）
- ARP 单播转发（避免双路径环路广播风暴）
- 显式路径安装（路径 A: s1→s2→s4, 路径 B: s1→s3→s4）
- 周期统计采集（StatsMixin）
- 阈值决策：util > 70% → 切换到另一条路径
"""

import os
import sys
import time
import networkx as nx

# ryu-manager 以文件方式加载模块，需要手动将项目根目录加入 sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER
from ryu.controller.handler import set_ev_cls
from ryu.ofproto import ofproto_v1_3
from ryu.lib import hub
from ryu.lib.packet import packet, ethernet, ether_types, arp
from ryu.topology import event as topo_event

from controller.stats_mixin import StatsMixin

# 路径端口映射：{路径名: {dpid: output_port}}
PATH_PORTS = {
    "A": {1: 3, 2: 2, 4: 1},  # s1:3→s2, s2:2→s4, s4:1→h3
    "B": {1: 4, 3: 2, 4: 1},  # s1:4→s3, s3:2→s4, s4:1→h3
}

# 反向路径端口映射：{路径名: {dpid: output_port}}（h3→h1 方向）
PATH_PORTS_REV = {
    "A": {4: 3, 2: 1, 1: 1},  # s4:3←s2, s2:1←s1, s1:1→h1
    "B": {4: 4, 3: 1, 1: 1},  # s4:4←s3, s3:1←s1, s1:1→h1
}


class ThresholdBalancer(app_manager.RyuApp, StatsMixin):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super(ThresholdBalancer, self).__init__(*args, **kwargs)
        self.mac_to_port = {}  # {dpid: {mac: port}}
        self.host_location = {}  # {mac: (dpid, port)}
        self.network = nx.Graph()
        self.datapaths = {}  # {dpid: datapath} (StatsMixin 需要)
        self.current_path = "A"  # 默认路径 A
        self.topo_ready = False
        self.path_installed = False  # 路径流表是否已安装

        self.init_stats()

        self.decision_thread = hub.spawn(self._decision_loop)

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
    # Packet-In 处理
    # ──────────────────────────────────────────────
    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def packet_in_handler(self, ev):
        msg = ev.msg
        datapath = msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        in_port = msg.match["in_port"]
        dpid = datapath.id

        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocol(ethernet.ethernet)
        if eth.ethertype in (ether_types.ETH_TYPE_LLDP, ether_types.ETH_TYPE_IPV6):
            return

        src = eth.src
        dst = eth.dst

        # 学习源 MAC 位置（仅首次）
        if src not in self.host_location:
            self.host_location[src] = (dpid, in_port)
            self.logger.info("Learn host: %s at s%d port %d", src, dpid, in_port)

        self.mac_to_port.setdefault(dpid, {})
        if src not in self.mac_to_port[dpid]:
            self.mac_to_port[dpid][src] = in_port

        # ── ARP 处理：单播转发（避免环路广播风暴）──
        if eth.ethertype == ether_types.ETH_TYPE_ARP:
            arp_pkt = pkt.get_protocol(arp.arp)
            if arp_pkt and arp_pkt.opcode == arp.ARP_REQUEST:
                target_mac = self._arp_lookup(arp_pkt.dst_ip)
                if target_mac and target_mac in self.host_location:
                    target_dpid, target_port = self.host_location[target_mac]
                    if target_dpid == dpid:
                        out_port = target_port
                    else:
                        out_port = self._get_out_port(dpid, target_dpid)
                        if out_port is None:
                            out_port = ofproto.OFPP_FLOOD
                else:
                    out_port = ofproto.OFPP_FLOOD
                self._send_packet(datapath, in_port, out_port, msg)
                return

            # ARP 回复：安装反向流表 + 转发
            if arp_pkt and arp_pkt.opcode == arp.ARP_REPLY:
                self._install_reverse_rule(datapath, src, in_port)
                if dst in self.host_location:
                    dst_dpid, dst_port = self.host_location[dst]
                    if dst_dpid == dpid:
                        out_port = dst_port
                    else:
                        out_port = self._get_out_port(dpid, dst_dpid)
                        if out_port is None:
                            out_port = ofproto.OFPP_FLOOD
                else:
                    out_port = ofproto.OFPP_FLOOD
                self._send_packet(datapath, in_port, out_port, msg)
                return

        # ── 数据包处理 ──
        # 学习源 MAC（用于 ARP 单播）
        self._install_reverse_rule(datapath, src, in_port)

        # 当两端 host 都已知时，首次在路径所有交换机上安装显式流表
        h1_mac = "00:00:00:00:00:01"
        h3_mac = "00:00:00:00:00:03"
        if (
            not self.path_installed
            and h1_mac in self.host_location
            and h3_mac in self.host_location
        ):
            self._install_full_path(self.current_path)
            self.path_installed = True
            # 处理当前数据包（沿已安装路径转发）
            out_port = self._get_path_out_port(dpid)
            if out_port is not None:
                self._send_packet(datapath, in_port, out_port, msg)
                return

        if dst in self.host_location:
            out_port = self._get_path_out_port(dpid)
            if out_port is not None:
                match = parser.OFPMatch(eth_dst=dst)
                actions = [parser.OFPActionOutput(out_port)]
                if msg.buffer_id != ofproto.OFP_NO_BUFFER:
                    self.add_flow(datapath, 10, match, actions, msg.buffer_id)
                else:
                    self.add_flow(datapath, 10, match, actions)
                    self._send_packet(datapath, in_port, out_port, msg)
                return

        # 目的地未知：泛洪
        self._send_packet(datapath, in_port, ofproto.OFPP_FLOOD, msg)

    # ──────────────────────────────────────────────
    # 流表安装辅助
    # ──────────────────────────────────────────────
    def add_flow(self, datapath, priority, match, actions, buffer_id=None):
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        inst = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS, actions)]
        kwargs = dict(
            datapath=datapath, priority=priority, match=match, instructions=inst
        )
        if buffer_id is not None:
            kwargs["buffer_id"] = buffer_id
        mod = parser.OFPFlowMod(**kwargs)
        datapath.send_msg(mod)

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
        """在当前交换机安装反向流表：eth_dst=mac → in_port"""
        if mac not in self.host_location:
            self.host_location[mac] = (datapath.id, in_port)
        parser = datapath.ofproto_parser
        match = parser.OFPMatch(eth_dst=mac)
        actions = [parser.OFPActionOutput(in_port)]
        self.add_flow(datapath, 10, match, actions)

    def _get_path_out_port(self, dpid):
        """获取当前路径下该交换机的出端口"""
        path = PATH_PORTS.get(self.current_path, {})
        return path.get(dpid)

    def _get_out_port(self, from_dpid, to_dpid):
        """计算从 from_dpid 到 to_dpid 的出端口（基于当前路径拓扑）"""
        path = self.current_path
        if path == "A":
            chain = [1, 2, 4]
        else:
            chain = [1, 3, 4]
        try:
            idx = chain.index(from_dpid)
            if idx + 1 < len(chain) and chain[idx + 1] == to_dpid:
                return PATH_PORTS[path][from_dpid]
            if idx - 1 >= 0 and chain[idx - 1] == to_dpid:
                return PATH_PORTS_REV[path][from_dpid]
        except ValueError:
            pass
        return None

    def _arp_lookup(self, ip):
        """通过 IP 查找 MAC（遍历 host_location，基于 host 编号推断）"""
        for mac in self.host_location:
            try:
                host_num = int(mac.split(":")[-1])
                if f"10.0.0.{host_num}" == ip:
                    return mac
            except ValueError:
                continue
        return None

    # ──────────────────────────────────────────────
    # 路径安装与切换
    # ──────────────────────────────────────────────
    def _install_full_path(self, path_name):
        """在路径上所有交换机安装显式流表（正向 h1→h3 + 反向 h3→h1）"""
        h3_mac = "00:00:00:00:00:03"
        h1_mac = "00:00:00:00:00:01"
        ports = PATH_PORTS[path_name]
        ports_rev = PATH_PORTS_REV[path_name]
        for dpid in ports:
            if dpid in self.datapaths:
                dp = self.datapaths[dpid]
                parser = dp.ofproto_parser
                # 正向：eth_dst=h3 → 路径出端口
                match = parser.OFPMatch(eth_dst=h3_mac)
                actions = [parser.OFPActionOutput(ports[dpid])]
                self.add_flow(dp, 10, match, actions)
                # 反向：eth_dst=h1 → 反向路径出端口
                match_rev = parser.OFPMatch(eth_dst=h1_mac)
                actions_rev = [parser.OFPActionOutput(ports_rev[dpid])]
                self.add_flow(dp, 10, match_rev, actions_rev)
                self.logger.info(
                    "  Install: s%d fwd=p%d rev=p%d (path %s)",
                    dpid,
                    ports[dpid],
                    ports_rev[dpid],
                    path_name,
                )

    def _switch_path(self, new_path):
        """切换路径：删除 s1 旧流表 + 在所有交换机安装新流表"""
        self.logger.info(
            ">>> Switching from path %s to path %s", self.current_path, new_path
        )
        self._clear_path_flows()
        self.current_path = new_path
        if self.path_installed:
            self._install_full_path(new_path)

    def _clear_path_flows(self):
        """删除所有交换机上优先级=10 的流表"""
        for dpid, dp in self.datapaths.items():
            parser = dp.ofproto_parser
            ofproto = dp.ofproto
            match = parser.OFPMatch()
            mod = parser.OFPFlowMod(
                datapath=dp,
                command=ofproto.OFPFC_DELETE,
                priority=10,
                out_port=ofproto.OFPP_ANY,
                out_group=ofproto.OFPG_ANY,
                match=match,
            )
            dp.send_msg(mod)
        self.logger.info("  Cleared all priority=10 flows")

    # ──────────────────────────────────────────────
    # 阈值决策循环
    # ──────────────────────────────────────────────
    def _decision_loop(self):
        """每 POLL_INTERVAL 秒检查链路利用率，超阈值则切换路径"""
        while True:
            hub.sleep(self.curr_poll_interval)
            if not self.datapaths:
                continue

            util_a = self._get_path_util("A")
            util_b = self._get_path_util("B")

            self.logger.info(
                "Path A: %.1f%%, Path B: %.1f%%, current: %s",
                util_a * 100,
                util_b * 100,
                self.current_path,
            )

            if self.current_path == "A" and util_a > 0.70 and util_b < 0.50:
                self.logger.info(
                    "Path A congested (%.1f%%), rerouting to B", util_a * 100
                )
                self._switch_path("B")
            elif self.current_path == "B" and util_b > 0.70 and util_a < 0.50:
                self.logger.info(
                    "Path B congested (%.1f%%), rerouting to A", util_b * 100
                )
                self._switch_path("A")

    def _get_path_util(self, path_name):
        """获取路径瓶颈利用率（所有核心链路的最大值）"""
        if path_name == "A":
            keys = [(1, 3), (2, 2), (4, 3)]
        else:
            keys = [(1, 4), (3, 2), (4, 4)]
        utils = [self.link_utilization.get(k, 0) for k in keys]
        return max(utils) if utils else 0

    # ──────────────────────────────────────────────
    # 统计回复
    # ──────────────────────────────────────────────
    @set_ev_cls(ofp_event.EventOFPPortStatsReply, MAIN_DISPATCHER)
    def port_stats_reply_handler(self, ev):
        self.handle_port_stats_reply(ev)

    # ──────────────────────────────────────────────
    # 拓扑发现（LLDP）
    # ──────────────────────────────────────────────
    @set_ev_cls(topo_event.EventSwitchEnter)
    def _switch_add_handler(self, ev):
        dpid = ev.switch.dp.id
        self.datapaths[dpid] = ev.switch.dp
        self.network.add_node(dpid)
        self.logger.info("Topology: switch s%d added", dpid)

    @set_ev_cls(topo_event.EventSwitchLeave)
    def _switch_del_handler(self, ev):
        dpid = ev.switch.dp.id
        self.datapaths.pop(dpid, None)
        if self.network.has_node(dpid):
            self.network.remove_node(dpid)
        self.logger.info("Topology: switch s%d removed", dpid)

    @set_ev_cls(topo_event.EventLinkAdd)
    def _link_add_handler(self, ev):
        src = ev.link.src
        dst = ev.link.dst
        self.network.add_edge(src.dpid, dst.dpid, port_no=src.port_no)
        self.logger.info(
            "Topology: link s%d:p%d → s%d:p%d",
            src.dpid,
            src.port_no,
            dst.dpid,
            dst.port_no,
        )
        if not self.topo_ready and self.network.number_of_edges() >= 4:
            self.topo_ready = True
            self.logger.info(
                "Topology ready: %d switches, %d links",
                self.network.number_of_nodes(),
                self.network.number_of_edges(),
            )

    @set_ev_cls(topo_event.EventLinkDelete)
    def _link_del_handler(self, ev):
        src = ev.link.src
        dst = ev.link.dst
        if self.network.has_edge(src.dpid, dst.dpid):
            self.network.remove_edge(src.dpid, dst.dpid)
        self.logger.info("Topology: link s%d → s%d removed", src.dpid, dst.dpid)
