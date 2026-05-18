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
from controller.weight_engine import DynamicWeightEngine

# 流表优先级
PRIORITY_ACTIVE_PATH = 20
PRIORITY_STANDBY_PATH = 10
FLOW_IDLE_TIMEOUT = 30  # 流表空闲超时（秒）
SESSION_TIMEOUT = 60    # 会话清理超时（秒）


class PredictiveBalancer(BaseBalancer):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super(PredictiveBalancer, self).__init__(*args, **kwargs)
        self.active_sessions = {}  # (src_mac, dst_mac) -> {'path_nodes', 'fwd_ports', 'rev_ports', 'util_keys'}
        self.path_installed = set()

        self.init_stats(topo_manager=self.topo)

        model_dir = os.path.join(os.path.dirname(__file__), "..", "models")
        self.weight_engine = DynamicWeightEngine(model_dir=model_dir)

        self.decision_thread = hub.spawn(self._decision_loop)

    # ──────────────────────────────────────────────
    # BaseBalancer 抽象方法实现
    # ──────────────────────────────────────────────
    def _get_active_fwd_ports(self):
        if self.active_sessions:
            first = next(iter(self.active_sessions.values()))
            return first.get('fwd_ports')
        return None

    def _get_active_rev_ports(self):
        if self.active_sessions:
            first = next(iter(self.active_sessions.values()))
            return first.get('rev_ports')
        return None

    def _invalidate_paths(self):
        self.active_sessions.clear()
        self.path_installed.clear()
        self.logger.info("Paths invalidated due to topology change")

    def _get_out_port(self, from_dpid, to_dpid):
        """Override base class: compute output port via direct Dijkstra instead of session cache."""
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

        # Learn host location via topology manager
        self.topo.learn_host(src, dpid, in_port)

        self.mac_to_port.setdefault(dpid, {})
        if src not in self.mac_to_port[dpid]:
            self.mac_to_port[dpid][src] = in_port

        # ARP proxy + loop-free flooding
        if eth.ethertype == ether_types.ETH_TYPE_ARP:
            arp_pkt = pkt.get_protocol(arp.arp)
            if arp_pkt and arp_pkt.opcode == arp.ARP_REQUEST:
                self._handle_arp_request(datapath, in_port, dpid, arp_pkt, src, msg)
                return

            if arp_pkt and arp_pkt.opcode == arp.ARP_REPLY:
                self._handle_arp_reply(datapath, in_port, dpid, dst, arp_pkt, src, msg)
                return

        # Data packet handling
        self._install_reverse_rule(datapath, src, in_port)

        # Compute and install path when both hosts are known
        if dst in self.topo.host_table and src in self.topo.host_table:
            session_key = (src, dst)
            if session_key not in self.active_sessions:
                self._compute_and_install_path(src, dst)

            if session_key in self.active_sessions:
                self.active_sessions[session_key]['last_seen'] = time.time()
                fwd = self.active_sessions[session_key]['fwd_ports']
                out_port = fwd.get(dpid)
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
        """ARP request: proxy reply or loop-free flood"""
        self._learn_arp_binding(arp_pkt, src_mac)

        target_ip = arp_pkt.dst_ip
        target_mac = self._arp_lookup(target_ip)

        if target_mac:
            target_loc = self.topo.get_host_location(target_mac)
            if target_loc:
                self._send_arp_reply(datapath, in_port, arp_pkt, target_mac)
                return

        flood_ports = self.topo.get_flood_ports(dpid, in_port)
        if flood_ports:
            for port in flood_ports:
                self._send_packet(datapath, in_port, port, msg)
        else:
            self._send_packet(datapath, in_port, datapath.ofproto.OFPP_FLOOD, msg)

    def _handle_arp_reply(self, datapath, in_port, dpid, dst_mac, arp_pkt, src_mac, msg):
        """ARP reply: learn binding + forward"""
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
        """Construct and send ARP Reply (controller proxy)"""
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
    # 路径安装
    # ──────────────────────────────────────────────
    def _install_path_rules(self, src_mac, dst_mac, fwd_ports):
        """Install eth_src+eth_dst rules along path to avoid multi-session collision."""
        for dpid, out_port in fwd_ports.items():
            if dpid in self.datapaths:
                dp = self.datapaths[dpid]
                parser = dp.ofproto_parser
                match = parser.OFPMatch(eth_src=src_mac, eth_dst=dst_mac)
                actions = [parser.OFPActionOutput(out_port)]
                self.add_flow(dp, PRIORITY_ACTIVE_PATH, match, actions, idle_timeout=FLOW_IDLE_TIMEOUT)

    def _compute_and_install_path(self, src_mac, dst_mac):
        """Compute optimal path using current ML weights and install flow rules."""
        src_loc = self.topo.get_host_location(src_mac)
        dst_loc = self.topo.get_host_location(dst_mac)
        if not src_loc or not dst_loc:
            return

        src_dpid, dst_dpid = src_loc[0], dst_loc[0]

        self.weight_engine.apply_weights_to_topology(self.topo)
        result = self.topo.compute_ecmp_path(src_dpid, dst_dpid, src_mac, dst_mac)

        if result:
            path_nodes, cost = result
            fwd, rev = self.topo.path_to_ports(path_nodes)
            util_keys = self.topo.get_path_util_keys(fwd, rev)

            session_key = (src_mac, dst_mac)
            self.active_sessions[session_key] = {
                'path_nodes': path_nodes,
                'fwd_ports': fwd,
                'rev_ports': rev,
                'util_keys': util_keys,
                'last_seen': time.time(),
            }

            self._install_path_rules(src_mac, dst_mac, fwd)
            self._install_path_rules(dst_mac, src_mac, rev)

            self.logger.info(
                "Path installed: %s -> %s, nodes=%s, cost=%.2f",
                src_mac, dst_mac, path_nodes, cost,
            )

    # ──────────────────────────────────────────────
    # 决策循环：周期性预测并触发动态重路由
    # ──────────────────────────────────────────────
    def _decision_loop(self):
        while True:
            hub.sleep(self.curr_poll_interval)
            if not self.datapaths or not self.active_sessions:
                continue

            for (dpid, port_no), util in self.link_utilization.items():
                self.weight_engine.register_link(dpid, port_no)
                self.weight_engine.update_utilization(dpid, port_no, util)

            self.weight_engine.predict_all()
            self.weight_engine.apply_weights_to_topology(self.topo)

            for (src_mac, dst_mac), session in list(self.active_sessions.items()):
                src_loc = self.topo.get_host_location(src_mac)
                dst_loc = self.topo.get_host_location(dst_mac)
                if not src_loc or not dst_loc:
                    continue

                src_dpid, dst_dpid = src_loc[0], dst_loc[0]
                result = self.topo.compute_ecmp_path(src_dpid, dst_dpid, src_mac, dst_mac)

                if result:
                    new_path, new_cost = result
                    old_path = session['path_nodes']

                    if new_path != old_path:
                        self.logger.info(
                            "Rerouting %s -> %s: cost %.2f", src_mac, dst_mac, new_cost,
                        )
                        new_fwd, new_rev = self.topo.path_to_ports(new_path)
                        new_util_keys = self.topo.get_path_util_keys(new_fwd, new_rev)

                        self._install_path_rules(src_mac, dst_mac, new_fwd)
                        self._install_path_rules(dst_mac, src_mac, new_rev)

                        self._cleanup_stale_rules(src_mac, dst_mac, session['fwd_ports'], new_fwd)
                        self._cleanup_stale_rules(dst_mac, src_mac, session['rev_ports'], new_rev)

                        session['path_nodes'] = new_path
                        session['fwd_ports'] = new_fwd
                        session['rev_ports'] = new_rev
                        session['util_keys'] = new_util_keys

            # Clean up stale sessions
            now = time.time()
            stale = [
                k for k, v in self.active_sessions.items()
                if now - v.get('last_seen', 0) > SESSION_TIMEOUT
            ]
            for k in stale:
                src_mac, dst_mac = k
                session = self.active_sessions.pop(k)
                self._cleanup_stale_rules(src_mac, dst_mac, session['fwd_ports'], {})
                self._cleanup_stale_rules(dst_mac, src_mac, session['rev_ports'], {})
                self.logger.info("Session expired: %s -> %s", src_mac, dst_mac)

            summary = self.weight_engine.get_state_summary()
            max_util = max(self.link_utilization.values()) if self.link_utilization else 0
            self.logger.info(
                "ML: %d links predicted, max_util=%.1f%%, active sessions=%d",
                summary["links_with_prediction"],
                max_util * 100,
                len(self.active_sessions),
            )

    def _cleanup_stale_rules(self, src_mac, dst_mac, old_ports, new_ports):
        """Remove flow rules on switches that are in old path but not new path."""
        for dpid, out_port in old_ports.items():
            if dpid in new_ports:
                continue
            if dpid not in self.datapaths:
                continue
            dp = self.datapaths[dpid]
            parser = dp.ofproto_parser
            ofproto = dp.ofproto
            match = parser.OFPMatch(eth_src=src_mac, eth_dst=dst_mac)
            mod = parser.OFPFlowMod(
                datapath=dp,
                command=ofproto.OFPFC_DELETE,
                out_port=out_port,
                out_group=ofproto.OFPG_ANY,
                match=match,
            )
            dp.send_msg(mod)
