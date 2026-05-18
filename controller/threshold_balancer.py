"""
阈值响应式负载均衡控制器（Phase 5 对照组）
- 拓扑发现（LLDP）
- ARP 单播转发（避免双路径环路广播风暴）
- 显式路径安装（动态计算，基于 TopologyManager）
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
from controller.topology_manager import TopologyManager

# 流表优先级
PRIORITY_ACTIVE_PATH = 20  # 新路径
PRIORITY_STANDBY_PATH = 10  # 旧路径


class ThresholdBalancer(app_manager.RyuApp, StatsMixin):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super(ThresholdBalancer, self).__init__(*args, **kwargs)
        self.mac_to_port = {}
        self.host_location = {}
        self.ip_to_mac = {}
        self.datapaths = {}
        self.current_path = "A"
        self.path_installed = False

        self.topo = TopologyManager()

        # Dual-path cache (computed dynamically)
        self.path_fwd = {"A": None, "B": None}
        self.path_rev = {"A": None, "B": None}
        self.path_util_keys = {"A": set(), "B": set()}

        self.init_stats(topo_manager=self.topo)
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
    # 拓扑发现事件处理
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
        self.logger.info("Topology: link s%d:p%d -> s%d:p%d",
                         src.dpid, src.port_no, dst.dpid, dst.port_no)
        self._invalidate_paths()

    @set_ev_cls(topo_event.EventLinkDelete)
    def _link_del_handler(self, ev):
        src = ev.link.src
        dst = ev.link.dst
        self.topo.remove_link(src.dpid, dst.dpid)
        self.logger.info("Topology: link s%d -> s%d removed", src.dpid, dst.dpid)
        self._invalidate_paths()

    def _invalidate_paths(self):
        self.path_fwd = {"A": None, "B": None}
        self.path_rev = {"A": None, "B": None}
        self.path_util_keys = {"A": set(), "B": set()}
        self.path_installed = False

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
        self.topo.learn_host(src, dpid, in_port)
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
        all_hosts = list(self.topo.host_table.keys())
        if not self.path_installed and len(all_hosts) >= 2:
            self._compute_paths()
            if self.path_installed:
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
    # 路径计算与安装
    # ──────────────────────────────────────────────
    def _compute_paths(self):
        """Compute two edge-disjoint paths via TopologyManager."""
        hosts = list(self.topo.host_table.keys())
        if len(hosts) < 2:
            return

        mac_a, mac_b = hosts[0], hosts[1]
        loc_a = self.topo.get_host_location(mac_a)
        loc_b = self.topo.get_host_location(mac_b)
        if not loc_a or not loc_b:
            return

        src_dpid = loc_a[0]
        dst_dpid = loc_b[0]

        paths = self.topo.compute_k_shortest_paths(src_dpid, dst_dpid, k=2, weight=None)

        if not paths:
            return

        fwd1, rev1 = self.topo.path_to_ports(paths[0][0])
        self.path_fwd["A"] = fwd1
        self.path_rev["A"] = rev1
        self.path_util_keys["A"] = self.topo.get_path_util_keys(fwd1, rev1)

        if len(paths) >= 2:
            fwd2, rev2 = self.topo.path_to_ports(paths[1][0])
            self.path_fwd["B"] = fwd2
            self.path_rev["B"] = rev2
            self.path_util_keys["B"] = self.topo.get_path_util_keys(fwd2, rev2)
        else:
            self.path_fwd["B"] = fwd1
            self.path_rev["B"] = rev1
            self.path_util_keys["B"] = self.path_util_keys["A"]

        self.set_path_util_keys(self.path_util_keys)
        self._install_full_path("A", PRIORITY_STANDBY_PATH)
        if len(paths) >= 2:
            self._install_full_path("B", PRIORITY_STANDBY_PATH)

        self.path_installed = True
        self.logger.info("Paths computed: fwd_A=%s, fwd_B=%s", fwd1, self.path_fwd["B"])

    def _install_full_path(self, path_name, priority):
        """Install flow rules for the named path."""
        fwd = self.path_fwd.get(path_name)
        rev = self.path_rev.get(path_name)
        if not fwd or not rev:
            return

        hosts = list(self.host_location.keys())
        if len(hosts) < 2:
            return

        mac_dst = hosts[1]
        mac_src = hosts[0]

        for dpid, out_port in fwd.items():
            if dpid in self.datapaths:
                dp = self.datapaths[dpid]
                parser = dp.ofproto_parser
                match = parser.OFPMatch(eth_dst=mac_dst)
                actions = [parser.OFPActionOutput(out_port)]
                self.add_flow(dp, priority, match, actions)

        for dpid, out_port in rev.items():
            if dpid in self.datapaths:
                dp = self.datapaths[dpid]
                parser = dp.ofproto_parser
                match = parser.OFPMatch(eth_dst=mac_src)
                actions = [parser.OFPActionOutput(out_port)]
                self.add_flow(dp, priority, match, actions)

    def _get_path_out_port(self, dpid):
        """获取当前路径下该交换机的出端口"""
        fwd = self.path_fwd.get(self.current_path)
        if fwd and dpid in fwd:
            return fwd[dpid]
        return None

    def _get_out_port(self, from_dpid, to_dpid):
        """计算从 from_dpid 到 to_dpid 的出端口（基于当前路径拓扑）"""
        curr = self.current_path
        fwd = self.path_fwd.get(curr)
        rev = self.path_rev.get(curr)

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

    # ──────────────────────────────────────────────
    # 路径切换
    # ──────────────────────────────────────────────
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
        ports = self.path_fwd.get(old_path, {})
        ports_rev = self.path_rev.get(old_path, {})

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
                out_port=ports[dpid],  # <--- 核心修复：只删除指向"旧出端口"的流表
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
        keys = self.path_util_keys.get(path_name, set())
        if not keys:
            return 0
        utils = [self.link_utilization.get(k, 0) for k in keys]
        return max(utils) if utils else 0

    # ──────────────────────────────────────────────
    # 统计回复
    # ──────────────────────────────────────────────
    @set_ev_cls(ofp_event.EventOFPPortStatsReply, MAIN_DISPATCHER)
    def port_stats_reply_handler(self, ev):
        self.handle_port_stats_reply(ev)
