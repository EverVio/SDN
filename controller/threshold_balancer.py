"""
阈值响应式负载均衡控制器（Phase 5 对照组）
- 拓扑发现（LLDP）
- ARP 单播转发（避免双路径环路广播风暴）
- 显式路径安装（路径 A: s1→s2→s4, 路径 B: s1→s3→s4）
- 周期统计采集（StatsMixin）
- 阈值决策：util > 70% → 切换到另一条路径
"""

import os
import sys

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

# 流表优先级
PRIORITY_ACTIVE_PATH = 20  # 新路径
PRIORITY_STANDBY_PATH = 10  # 旧路径


class ThresholdBalancer(app_manager.RyuApp, StatsMixin):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super(ThresholdBalancer, self).__init__(*args, **kwargs)
        self.mac_to_port = {}  # {dpid: {mac: port}}
        self.host_location = {}  # {mac: (dpid, port)}
        self.ip_to_mac = {}  # {ip: mac}
        self.datapaths = {}  # {dpid: datapath} (StatsMixin 需要)
        self.current_path = "A"  # 默认路径 A
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
                self._learn_arp_binding(arp_pkt, src)
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
                self._learn_arp_binding(arp_pkt, src)
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
            self._install_full_path(self.current_path, PRIORITY_STANDBY_PATH)
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
        """通过 IP 查找 MAC（直接查询 ARP 学到的绑定表）"""
        return self.ip_to_mac.get(ip)

    def _learn_arp_binding(self, arp_pkt, eth_src):
        """从 ARP 包学习 IP-MAC 绑定"""
        if arp_pkt.src_ip and arp_pkt.src_mac:
            self.ip_to_mac[arp_pkt.src_ip] = arp_pkt.src_mac

        if arp_pkt.opcode == arp.ARP_REPLY:
            if arp_pkt.dst_ip and arp_pkt.dst_mac:
                self.ip_to_mac[arp_pkt.dst_ip] = arp_pkt.dst_mac

    # ──────────────────────────────────────────────
    # 路径安装与切换
    # ──────────────────────────────────────────────
    def _install_full_path(self, path_name, priority):
        """
        在路径上所有交换机安装显式流表（正向 h1→h3 + 反向 h3→h1）
        :param path_name: "A" 或 "B"
        :param priority: 流表优先级，默认 PRIORITY_STANDBY_PATH=10
        """
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
                self.add_flow(dp, priority, match, actions)
                # 反向：eth_dst=h1 → 反向路径出端口
                match_rev = parser.OFPMatch(eth_dst=h1_mac)
                actions_rev = [parser.OFPActionOutput(ports_rev[dpid])]
                self.add_flow(dp, priority, match_rev, actions_rev)
                self.logger.info(
                    "  Install: s%d fwd=p%d rev=p%d (path %s, prio=%d)",
                    dpid,
                    ports[dpid],
                    ports_rev[dpid],
                    path_name,
                    priority,
                )

    def _switch_path(self, new_path):
        """切换路径：利用 OpenFlow 原子覆盖特性实现先建后拆"""
        old_path = self.current_path
        self.logger.info(
            ">>> Make-Before-Break: switching from %s to %s", old_path, new_path
        )

        # 1. 统一使用相同的优先级 (例如 10) 安装新路径
        # 在公共节点 (s1, s4) 上，相同 match 和 priority 的 ADD 会原子性覆盖旧 Action，瞬间完成切换
        if self.path_installed:
            self._install_full_path(new_path, priority=PRIORITY_STANDBY_PATH)
            self.logger.info(
                "  Installed new path %s (Atomic Overwrite on shared nodes)", new_path
            )

        # 2. 更新当前路径记录
        self.current_path = new_path

        # 3. 异步清理非公共节点上的废弃流表
        hub.spawn(self._async_cleanup_old_path, old_path)

    def _async_cleanup_old_path(self, old_path):
        """精准删除旧路径的残留流表"""
        hub.sleep(0.2)

        # 获取旧路径涉及的 dpid 和端口映射
        ports = PATH_PORTS[old_path]
        ports_rev = PATH_PORTS_REV[old_path]

        for dpid in ports:
            if dpid not in self.datapaths:
                continue
            dp = self.datapaths[dpid]
            parser = dp.ofproto_parser
            ofproto = dp.ofproto

            # 删除正向流表：精准匹配 eth_dst 且 out_port 必须是旧端口
            match = parser.OFPMatch(eth_dst="00:00:00:00:00:03")
            mod = parser.OFPFlowMod(
                datapath=dp,
                command=ofproto.OFPFC_DELETE,
                out_port=ports[dpid],  # <--- 核心修复：只删除指向“旧出端口”的流表
                out_group=ofproto.OFPG_ANY,
                match=match,
            )
            dp.send_msg(mod)

            # 删除反向流表：精准匹配 eth_dst 且 out_port 必须是反向旧端口
            match_rev = parser.OFPMatch(eth_dst="00:00:00:00:00:01")
            mod_rev = parser.OFPFlowMod(
                datapath=dp,
                command=ofproto.OFPFC_DELETE,
                out_port=ports_rev[dpid],  # <--- 核心修复
                out_group=ofproto.OFPG_ANY,
                match=match_rev,
            )
            dp.send_msg(mod_rev)

        self.logger.info("  Cleaned up orphaned flows for old path %s", old_path)

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
        self.logger.info("Topology: switch s%d added", dpid)

    @set_ev_cls(topo_event.EventSwitchLeave)
    def _switch_del_handler(self, ev):
        dpid = ev.switch.dp.id
        self.datapaths.pop(dpid, None)
        self.logger.info("Topology: switch s%d removed", dpid)

    @set_ev_cls(topo_event.EventLinkAdd)
    def _link_add_handler(self, ev):
        src = ev.link.src
        dst = ev.link.dst
        self.logger.info(
            "Topology: link s%d:p%d → s%d:p%d",
            src.dpid,
            src.port_no,
            dst.dpid,
            dst.port_no,
        )

    @set_ev_cls(topo_event.EventLinkDelete)
    def _link_del_handler(self, ev):
        src = ev.link.src
        dst = ev.link.dst
        self.logger.info("Topology: link s%d → s%d removed", src.dpid, dst.dpid)
