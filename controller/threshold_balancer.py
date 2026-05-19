"""
阈值响应式负载均衡控制器（对照组）
- ARP 代理 + 单播转发
- 每 (src_mac, dst_mac) 会话独立路径管理
- 阈值决策：路径瓶颈 util > 70% → 切换到替代路径
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ryu.controller.handler import set_ev_cls
from ryu.ofproto import ofproto_v1_3
from ryu.lib import hub
from ryu.lib.packet import packet, ethernet, ether_types, arp
from ryu.controller import ofp_event
from ryu.controller.handler import MAIN_DISPATCHER

from controller.base_balancer import BaseBalancer

PRIORITY_ACTIVE_PATH = 20
PRIORITY_STANDBY_PATH = 10
FLOW_IDLE_TIMEOUT = 30
SESSION_TIMEOUT = 60


class ThresholdBalancer(BaseBalancer):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super(ThresholdBalancer, self).__init__(*args, **kwargs)
        # session_key (src_mac, dst_mac) -> {
        #   'path_nodes', 'fwd_ports', 'rev_ports', 'util_keys',
        #   'alt_path_nodes', 'alt_fwd_ports', 'alt_rev_ports', 'alt_util_keys',
        #   'using_alt', 'last_seen'
        # }
        self.active_sessions = {}
        # Broadcast storm cache: (dpid, src_mac, eth_type) -> timestamp
        self.broadcast_cache = {}
        # Path switch cooldown
        self.last_switch_time = {}  # (src_mac, dst_mac) -> timestamp
        self.SWITCH_COOLDOWN = 10.0  # seconds between path switches

        self.init_stats(topo_manager=self.topo)
        self.decision_thread = hub.spawn(self._decision_loop)

    # ──────────────────────────────────────────────
    # BaseBalancer 抽象方法实现
    # ──────────────────────────────────────────────
    def _get_active_fwd_ports(self):
        if self.active_sessions:
            first = next(iter(self.active_sessions.values()))
            key = 'alt_fwd_ports' if first.get('using_alt') else 'fwd_ports'
            return first.get(key)
        return None

    def _get_active_rev_ports(self):
        if self.active_sessions:
            first = next(iter(self.active_sessions.values()))
            key = 'alt_rev_ports' if first.get('using_alt') else 'rev_ports'
            return first.get(key)
        return None

    def _invalidate_paths(self):
        self.active_sessions.clear()
        self.logger.info("Paths invalidated due to topology change")

    def _get_out_port(self, from_dpid, to_dpid):
        """Compute output port via Dijkstra."""
        result = self.topo.compute_optimal_path(from_dpid, to_dpid, weight=None)
        if result:
            path_nodes, _ = result
            if len(path_nodes) >= 2:
                fwd, _ = self.topo.path_to_ports(path_nodes)
                return fwd.get(from_dpid)
        return None

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

        # Broadcast storm suppression (same as L2 controller)
        if dst == "ff:ff:ff:ff:ff:ff":
            import time as _time
            cache_key = (dpid, src, eth.ethertype)
            now = _time.time()
            if len(self.broadcast_cache) > 1000:
                self.broadcast_cache.clear()
            if cache_key in self.broadcast_cache:
                if now - self.broadcast_cache[cache_key] < 0.5:
                    return
            self.broadcast_cache[cache_key] = now

        self.topo.learn_host(src, dpid, in_port)

        self.mac_to_port.setdefault(dpid, {})
        if src not in self.mac_to_port[dpid]:
            self.mac_to_port[dpid][src] = in_port

        # ARP proxy + loop-free flooding
        if eth.ethertype == ether_types.ETH_TYPE_ARP:
            # ====== Cold-start: force physical flood until full topology discovered ======
            if len(self.topo.G.nodes) < 20:
                self._send_packet(datapath, in_port, datapath.ofproto.OFPP_FLOOD, msg)
                return
            # ============================================================================

            arp_pkt = pkt.get_protocol(arp.arp)
            if arp_pkt and arp_pkt.opcode == arp.ARP_REQUEST:
                self._handle_arp_request(datapath, in_port, dpid, arp_pkt, src, msg)
                return
            if arp_pkt and arp_pkt.opcode == arp.ARP_REPLY:
                self._handle_arp_reply(datapath, in_port, dpid, dst, arp_pkt, src, msg)
                return

        # Data packet handling
        self._install_reverse_rule(datapath, src, in_port)

        if dst in self.topo.host_table and src in self.topo.host_table:
            session_key = (src, dst)
            if session_key not in self.active_sessions:
                self._compute_and_install_path(src, dst)

            if session_key in self.active_sessions:
                session = self.active_sessions[session_key]
                session['last_seen'] = time.time()
                fwd_ports = session['alt_fwd_ports'] if session.get('using_alt') else session['fwd_ports']
                out_port = fwd_ports.get(dpid)
                if out_port is not None:
                    buf_id = msg.buffer_id if msg.buffer_id != ofproto.OFP_NO_BUFFER else None
                    match = parser.OFPMatch(eth_src=src, eth_dst=dst)
                    actions = [parser.OFPActionOutput(out_port)]
                    if buf_id is not None:
                        self.add_flow(datapath, PRIORITY_ACTIVE_PATH, match, actions, buf_id, idle_timeout=FLOW_IDLE_TIMEOUT)
                    else:
                        self.add_flow(datapath, PRIORITY_ACTIVE_PATH, match, actions, idle_timeout=FLOW_IDLE_TIMEOUT)
                        self._send_packet(datapath, in_port, out_port, msg)
                    return

        # Destination unknown or path not available: loop-free flooding
        flood_ports = self.topo.get_flood_ports(dpid, in_port)
        if flood_ports:
            for port in flood_ports:
                self._send_packet(datapath, in_port, port, msg)
        else:
            self._send_packet(datapath, in_port, ofproto.OFPP_FLOOD, msg)

    # ──────────────────────────────────────────────
    # ARP proxy handling
    # ──────────────────────────────────────────────
    def _handle_arp_request(self, datapath, in_port, dpid, arp_pkt, src_mac, msg):
        self._learn_arp_binding(arp_pkt, src_mac)
        target_ip = arp_pkt.dst_ip
        target_mac = self._arp_lookup(target_ip)
        if target_mac:
            target_loc = self.topo.get_host_location(target_mac)
            if target_loc:
                self._send_arp_reply(datapath, in_port, arp_pkt, target_mac)
                return
        # Flood ARP request via all ports (same as L2 controller).
        # Broadcast storm is prevented by the broadcast_cache above.
        self._send_packet(datapath, in_port, datapath.ofproto.OFPP_FLOOD, msg)

    def _handle_arp_reply(self, datapath, in_port, dpid, dst_mac, arp_pkt, src_mac, msg):
        self._learn_arp_binding(arp_pkt, src_mac)
        self._install_reverse_rule(datapath, src_mac, in_port)
        target_loc = self.topo.get_host_location(dst_mac)
        if target_loc:
            target_dpid, target_port = target_loc
            if target_dpid == dpid:
                out_port = target_port
            else:
                out_port = self._get_out_port(dpid, target_dpid)
                if out_port is None:
                    out_port = datapath.ofproto.OFPP_FLOOD
        else:
            out_port = datapath.ofproto.OFPP_FLOOD
        self._send_packet(datapath, in_port, out_port, msg)

    def _send_arp_reply(self, datapath, in_port, req_arp_pkt, target_mac):
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        eth_pkt = packet.Packet()
        eth_pkt.add_protocol(ethernet.ethernet(
            ethertype=ether_types.ETH_TYPE_ARP,
            dst=req_arp_pkt.src_mac,
            src=target_mac,
        ))
        eth_pkt.add_protocol(arp.arp(
            opcode=arp.ARP_REPLY,
            src_mac=target_mac,
            src_ip=req_arp_pkt.dst_ip,
            dst_mac=req_arp_pkt.src_mac,
            dst_ip=req_arp_pkt.src_ip,
        ))
        eth_pkt.serialize()
        actions = [parser.OFPActionOutput(in_port)]
        out = parser.OFPPacketOut(
            datapath=datapath,
            buffer_id=ofproto.OFP_NO_BUFFER,
            in_port=ofproto.OFPP_CONTROLLER,
            actions=actions,
            data=eth_pkt.data,
        )
        datapath.send_msg(out)

    # ──────────────────────────────────────────────
    # 路径计算与安装（per-session）
    # ──────────────────────────────────────────────
    def _install_path_rules(self, src_mac, dst_mac, fwd_ports, priority=PRIORITY_ACTIVE_PATH):
        for dpid, out_port in fwd_ports.items():
            if dpid in self.datapaths:
                dp = self.datapaths[dpid]
                parser = dp.ofproto_parser
                match = parser.OFPMatch(eth_src=src_mac, eth_dst=dst_mac)
                actions = [parser.OFPActionOutput(out_port)]
                self.add_flow(dp, priority, match, actions, idle_timeout=FLOW_IDLE_TIMEOUT)

    def _compute_and_install_path(self, src_mac, dst_mac):
        src_loc = self.topo.get_host_location(src_mac)
        dst_loc = self.topo.get_host_location(dst_mac)
        if not src_loc or not dst_loc:
            return

        src_dpid, dst_dpid = src_loc[0], dst_loc[0]

        # Compute primary path (shortest)
        result = self.topo.compute_optimal_path(src_dpid, dst_dpid)
        if not result:
            return

        path_nodes, cost = result
        fwd, rev = self.topo.path_to_ports(path_nodes)
        util_keys = self.topo.get_path_util_keys(fwd, rev)

        # Compute alternative path (edge-disjoint)
        alt_result = self.topo.compute_alternative_path(src_dpid, dst_dpid, path_nodes)
        if alt_result:
            alt_path, alt_cost = alt_result
            alt_fwd, alt_rev = self.topo.path_to_ports(alt_path)
            alt_util_keys = self.topo.get_path_util_keys(alt_fwd, alt_rev)
        else:
            alt_path, alt_fwd, alt_rev, alt_util_keys = path_nodes, fwd, rev, util_keys

        session_key = (src_mac, dst_mac)
        self.active_sessions[session_key] = {
            'path_nodes': path_nodes,
            'fwd_ports': fwd,
            'rev_ports': rev,
            'util_keys': util_keys,
            'alt_path_nodes': alt_path,
            'alt_fwd_ports': alt_fwd,
            'alt_rev_ports': alt_rev,
            'alt_util_keys': alt_util_keys,
            'using_alt': False,
            'last_seen': time.time(),
        }

        # Install primary path rules
        self._install_path_rules(src_mac, dst_mac, fwd)
        self._install_path_rules(dst_mac, src_mac, rev)

        self.logger.info(
            "Path installed: %s -> %s, nodes=%s, alt=%s",
            src_mac, dst_mac, path_nodes, alt_path,
        )

    # ──────────────────────────────────────────────
    # 阈值决策循环
    # ──────────────────────────────────────────────
    def _decision_loop(self):
        while True:
            hub.sleep(self.curr_poll_interval)
            if not self.datapaths or not self.active_sessions:
                continue

            now = time.time()
            for (src_mac, dst_mac), session in list(self.active_sessions.items()):
                util_keys = session['util_keys']
                alt_util_keys = session['alt_util_keys']

                if not util_keys:
                    continue

                # Cooldown check
                time_since_last = now - self.last_switch_time.get((src_mac, dst_mac), 0)
                if time_since_last < self.SWITCH_COOLDOWN:
                    continue

                # Get bottleneck utilization for primary and alternative paths
                primary_util = max(
                    (self.link_utilization.get(k, 0) for k in util_keys),
                    default=0,
                )
                alt_util = max(
                    (self.link_utilization.get(k, 0) for k in alt_util_keys),
                    default=0,
                )

                using_alt = session.get('using_alt', False)

                # Threshold switching: primary congested AND alternative is better
                if not using_alt and primary_util > 0.70 and alt_util < 0.50:
                    self.last_switch_time[(src_mac, dst_mac)] = now
                    self.logger.info(
                        "Threshold: %s -> %s primary congested (%.1f%%), switching to alt (%.1f%%)",
                        src_mac, dst_mac, primary_util * 100, alt_util * 100,
                    )
                    self._install_path_rules(src_mac, dst_mac, session['alt_fwd_ports'])
                    self._install_path_rules(dst_mac, src_mac, session['alt_rev_ports'])
                    session['using_alt'] = True

                # Threshold switching back: alt congested AND primary is better
                elif using_alt and alt_util > 0.70 and primary_util < 0.50:
                    self.last_switch_time[(src_mac, dst_mac)] = now
                    self.logger.info(
                        "Threshold: %s -> %s alt congested (%.1f%%), switching back to primary (%.1f%%)",
                        src_mac, dst_mac, alt_util * 100, primary_util * 100,
                    )
                    self._install_path_rules(src_mac, dst_mac, session['fwd_ports'])
                    self._install_path_rules(dst_mac, src_mac, session['rev_ports'])
                    session['using_alt'] = False

            # Clean up stale sessions
            now = time.time()
            stale = [
                k for k, v in self.active_sessions.items()
                if now - v.get('last_seen', 0) > SESSION_TIMEOUT
            ]
            for k in stale:
                session = self.active_sessions.pop(k)
                self.logger.info("Session expired: %s -> %s", k[0], k[1])

            max_util = max(self.link_utilization.values()) if self.link_utilization else 0
            self.logger.info(
                "Threshold: max_util=%.1f%%, active sessions=%d",
                max_util * 100,
                len(self.active_sessions),
            )
