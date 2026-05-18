"""
阈值响应式负载均衡控制器（对照组）
- ARP 单播转发（避免双路径环路广播风暴）
- 显式路径安装（动态计算，基于 TopologyManager）
- 阈值决策：util > 70% → 切换到另一条路径
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ryu.controller.handler import set_ev_cls
from ryu.ofproto import ofproto_v1_3
from ryu.lib import hub
from ryu.lib.packet import packet, ethernet, ether_types, arp
from ryu.controller import ofp_event
from ryu.controller.handler import MAIN_DISPATCHER

from controller.base_balancer import BaseBalancer

# 流表优先级
PRIORITY_ACTIVE_PATH = 20  # 新路径
PRIORITY_STANDBY_PATH = 10  # 旧路径


class ThresholdBalancer(BaseBalancer):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super(ThresholdBalancer, self).__init__(*args, **kwargs)
        self.host_location = {}
        self.current_path = "A"
        self.path_installed = False

        # Dual-path cache (computed dynamically)
        self.path_fwd = {"A": None, "B": None}
        self.path_rev = {"A": None, "B": None}
        self.path_util_keys = {"A": set(), "B": set()}

        self.init_stats(topo_manager=self.topo)
        self.decision_thread = hub.spawn(self._decision_loop)

    # ──────────────────────────────────────────────
    # BaseBalancer 抽象方法实现
    # ──────────────────────────────────────────────
    def _get_active_fwd_ports(self):
        return self.path_fwd.get(self.current_path)

    def _get_active_rev_ports(self):
        return self.path_rev.get(self.current_path)

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
    # 路径计算与安装
    # ──────────────────────────────────────────────
    def _compute_paths(self):
        """Compute two paths: shortest (A) and edge-disjoint alternative (B)."""
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

        result_a = self.topo.compute_optimal_path(src_dpid, dst_dpid)

        if not result_a:
            return

        path_a, _ = result_a
        fwd1, rev1 = self.topo.path_to_ports(path_a)
        self.path_fwd["A"] = fwd1
        self.path_rev["A"] = rev1
        self.path_util_keys["A"] = self.topo.get_path_util_keys(fwd1, rev1)

        result_b = self.topo.compute_alternative_path(src_dpid, dst_dpid, path_a)
        if result_b:
            path_b, _ = result_b
            fwd2, rev2 = self.topo.path_to_ports(path_b)
            self.path_fwd["B"] = fwd2
            self.path_rev["B"] = rev2
            self.path_util_keys["B"] = self.topo.get_path_util_keys(fwd2, rev2)
        else:
            self.path_fwd["B"] = fwd1
            self.path_rev["B"] = rev1
            self.path_util_keys["B"] = self.path_util_keys["A"]

        self.set_path_util_keys(self.path_util_keys)
        self._install_full_path("A", PRIORITY_STANDBY_PATH)
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

    # ──────────────────────────────────────────────
    # 路径切换
    # ──────────────────────────────────────────────
    def _switch_path(self, new_path):
        """切换路径：Make-Before-Break，先安装新路径再清理旧路径"""
        old_path = self.current_path
        self.logger.info(
            ">>> Make-Before-Break: switching from %s to %s", old_path, new_path
        )

        if self.path_installed:
            self._install_full_path(new_path, priority=PRIORITY_STANDBY_PATH)

        self.current_path = new_path
        hub.spawn(self._async_cleanup_old_path, old_path)

    def _async_cleanup_old_path(self, old_path):
        """精准删除旧路径的残留流表"""
        hub.sleep(0.2)

        ports = self.path_fwd.get(old_path, {})
        ports_rev = self.path_rev.get(old_path, {})

        for dpid in ports:
            if dpid not in self.datapaths:
                continue
            dp = self.datapaths[dpid]
            parser = dp.ofproto_parser
            ofproto = dp.ofproto

            match = parser.OFPMatch(eth_dst="00:00:00:00:00:03")
            mod = parser.OFPFlowMod(
                datapath=dp,
                command=ofproto.OFPFC_DELETE,
                out_port=ports[dpid],
                out_group=ofproto.OFPG_ANY,
                match=match,
            )
            dp.send_msg(mod)

            match_rev = parser.OFPMatch(eth_dst="00:00:00:00:00:01")
            mod_rev = parser.OFPFlowMod(
                datapath=dp,
                command=ofproto.OFPFC_DELETE,
                out_port=ports_rev[dpid],
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
