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

    def __init__(self, *args, **kwargs):
        super(BaseBalancer, self).__init__(*args, **kwargs)
        self.mac_to_port = {}
        self.ip_to_mac = {}
        self.datapaths = {}
        self.topo = TopologyManager()

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
    @set_ev_cls(topo_event.EventSwitchEnter)
    def _switch_add_handler(self, ev):
        dpid = ev.switch.dp.id
        self.datapaths[dpid] = ev.switch.dp
        self.topo.add_switch(dpid)
        self.logger.info("Topology: switch s%d added", dpid)

    @set_ev_cls(topo_event.EventSwitchLeave)
    def _switch_del_handler(self, ev):
        dpid = ev.switch.dp.id
        self.datapaths.pop(dpid, None)
        self.topo.remove_switch(dpid)
        self.logger.info("Topology: switch s%d removed", dpid)

    @set_ev_cls(topo_event.EventLinkAdd)
    def _link_add_handler(self, ev):
        src = ev.link.src
        dst = ev.link.dst
        self.topo.add_link(src.dpid, src.port_no, dst.dpid, dst.port_no)
        self.logger.info(
            "Topology: link s%d:p%d -> s%d:p%d",
            src.dpid, src.port_no, dst.dpid, dst.port_no,
        )
        self._invalidate_paths()

    @set_ev_cls(topo_event.EventLinkDelete)
    def _link_del_handler(self, ev):
        src = ev.link.src
        dst = ev.link.dst
        self.topo.remove_link(src.dpid, dst.dpid)
        self.logger.info("Topology: link s%d -> s%d removed", src.dpid, dst.dpid)
        self._invalidate_paths()

    # ──────────────────────────────────────────────
    # 统计回复（子类需绑定事件）
    # ──────────────────────────────────────────────
    @set_ev_cls(ofp_event.EventOFPPortStatsReply, MAIN_DISPATCHER)
    def port_stats_reply_handler(self, ev):
        self.handle_port_stats_reply(ev)
