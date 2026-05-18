import os
import sys
import time
import atexit

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER
from ryu.controller.handler import set_ev_cls
from ryu.ofproto import ofproto_v1_3
from ryu.lib import hub
from ryu.lib.packet import packet, ethernet, ether_types, arp, ipv4, tcp, udp
from ryu.topology import event as topo_event

from controller.stats_mixin import StatsMixin
from controller.topology_manager import TopologyManager
from controller.weight_engine import DynamicWeightEngine

# Constants
K_PATHS = 3  # number of candidate paths to compute
PRIORITY_ACTIVE_PATH = 20
PRIORITY_STANDBY_PATH = 10

# Elephant/Mice flow separation
PRIORITY_MICE = 10
PRIORITY_ELEPHANT = 30
ELEPHANT_THRESHOLD = 1_000_000  # 1 Mbps in bytes/sec
FLOW_IDLE_TIMEOUT_MICE = 60
FLOW_IDLE_TIMEOUT_ELEPHANT = 300


class PredictiveBalancer(app_manager.RyuApp, StatsMixin):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super(PredictiveBalancer, self).__init__(*args, **kwargs)
        self.mac_to_port = {}
        self.ip_to_mac = {}
        self.datapaths = {}
        self.path_installed = False

        # Per-flow tracking for elephant/mice separation
        self.flow_table = {}  # flow_tuple -> {bytes, first_seen, last_seen, path_idx, is_elephant}
        self.flow_rules_installed = set()  # (flow_tuple, dpid) for elephant rule cleanup

        self.topo = TopologyManager()

        # K-path cache: list of (fwd_ports, rev_ports, path_nodes, cost)
        self.k_paths = []
        self.active_path_idx = 0

        # Per-path util keys for StatsMixin labeling
        self.path_util_keys = {}

        self.init_stats(topo_manager=self.topo)

        # Dynamic weight engine (ML predictions)
        model_dir = os.path.join(os.path.dirname(__file__), "..", "models")
        self.weight_engine = DynamicWeightEngine(model_dir=model_dir)

        atexit.register(self._cleanup)
        self.decision_thread = hub.spawn(self._decision_loop)

    # ──────────────────────────────────────────────
    # Switch connection: install table-miss rule
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
    # Packet-In handling
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

        # Ensure K paths are installed before flow-level routing
        all_hosts = list(self.topo.host_table.keys())
        if not self.path_installed and len(all_hosts) >= 2:
            self._compute_and_install_paths()

        # --- 5-tuple flow tracking for IPv4 data packets ---
        flow_tuple = self._parse_flow_tuple(pkt, eth)

        if flow_tuple is not None and self.path_installed:
            now = time.time()
            is_elephant = self._update_flow_state(flow_tuple, msg.total_len, now)

            if is_elephant:
                # Elephant flow: install dedicated high-priority rule
                src_loc = self.topo.get_host_location(src)
                dst_loc = self.topo.get_host_location(dst)

                if src_loc and dst_loc:
                    fwd, rev = self._select_elephant_path(src_loc[0], dst_loc[0])

                    if fwd is not None:
                        self._migrate_elephant_flow(flow_tuple, fwd, rev)

                        out_port = fwd.get(dpid)
                        if out_port is not None:
                            self._send_packet(datapath, in_port, out_port, msg)
                            return

            # Mice flow: use ECMP path selection
            n_paths = len(self.k_paths)
            if n_paths > 0:
                path_idx = self.topo.select_ecmp_path(flow_tuple, n_paths)
                fwd_ports = self.k_paths[path_idx][0]
                out_port = fwd_ports.get(dpid)

                if out_port is not None:
                    buf_id = msg.buffer_id if msg.buffer_id != ofproto.OFP_NO_BUFFER else None
                    self._install_flow_rule(
                        datapath, flow_tuple, out_port,
                        is_elephant=False, buffer_id=buf_id,
                    )
                    if buf_id is None:
                        self._send_packet(datapath, in_port, out_port, msg)
                    return

        # --- MAC-only fallback (non-IPv4 or paths not yet installed) ---
        if dst in self.topo.host_table:
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

        # Destination unknown: loop-free flooding
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
                # Known target: controller proxy ARP Reply
                self._send_arp_reply(datapath, in_port, arp_pkt, target_mac)
                return

        # Target unknown: loop-free flood along spanning tree
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
    # Flow table installation helpers
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
        """Install reverse flow rule on current switch: eth_dst=mac -> in_port"""
        parser = datapath.ofproto_parser
        match = parser.OFPMatch(eth_dst=mac)
        actions = [parser.OFPActionOutput(in_port)]
        self.add_flow(datapath, 10, match, actions)

    def _get_path_out_port(self, dpid):
        """Get output port for the currently active path."""
        if not self.k_paths:
            return None
        fwd = self.k_paths[self.active_path_idx][0]
        return fwd.get(dpid)

    def _get_out_port(self, from_dpid, to_dpid):
        """Compute output port from from_dpid to to_dpid (based on topology graph)."""
        # Check active path's forward direction
        if self.k_paths:
            fwd = self.k_paths[self.active_path_idx][0]
            rev = self.k_paths[self.active_path_idx][1]

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

    def _arp_lookup(self, ip):
        return self.ip_to_mac.get(ip)

    def _learn_arp_binding(self, arp_pkt, eth_src):
        if arp_pkt.src_ip and arp_pkt.src_mac:
            self.ip_to_mac[arp_pkt.src_ip] = arp_pkt.src_mac
        if arp_pkt.opcode == arp.ARP_REPLY:
            if arp_pkt.dst_ip and arp_pkt.dst_mac:
                self.ip_to_mac[arp_pkt.dst_ip] = arp_pkt.dst_mac

    # ──────────────────────────────────────────────
    # 5-tuple flow parsing and elephant/mice separation
    # ──────────────────────────────────────────────

    def _parse_flow_tuple(self, pkt, eth):
        """Extract 5-tuple from an IPv4 packet.

        Returns:
            (src_ip, dst_ip, proto, src_port, dst_port) or None if not IPv4.
        """
        if eth.ethertype != ether_types.ETH_TYPE_IP:
            return None

        ip_pkt = pkt.get_protocol(ipv4.ipv4)
        if ip_pkt is None:
            return None

        src_ip = ip_pkt.src
        dst_ip = ip_pkt.dst
        proto = ip_pkt.proto

        src_port = 0
        dst_port = 0

        if proto == 6:  # TCP
            tcp_pkt = pkt.get_protocol(tcp.tcp)
            if tcp_pkt:
                src_port = tcp_pkt.src_port
                dst_port = tcp_pkt.dst_port
        elif proto == 17:  # UDP
            udp_pkt = pkt.get_protocol(udp.udp)
            if udp_pkt:
                src_port = udp_pkt.src_port
                dst_port = udp_pkt.dst_port

        return (src_ip, dst_ip, proto, src_port, dst_port)

    def _update_flow_state(self, flow_tuple, byte_count, now):
        """Update per-flow byte tracking and detect elephant flows.

        Returns:
            True if the flow is currently classified as elephant.
        """
        if flow_tuple not in self.flow_table:
            self.flow_table[flow_tuple] = {
                'bytes': 0,
                'first_seen': now,
                'last_seen': now,
                'path_fwd': None,
                'is_elephant': False,
            }

        entry = self.flow_table[flow_tuple]
        entry['bytes'] += byte_count
        entry['last_seen'] = now

        elapsed = now - entry['first_seen']
        if elapsed < 0.5:
            return entry['is_elephant']

        rate = entry['bytes'] / elapsed  # bytes per second

        was_elephant = entry['is_elephant']
        entry['is_elephant'] = rate > ELEPHANT_THRESHOLD

        if entry['is_elephant'] and not was_elephant:
            self.logger.info(
                "Flow promoted to ELEPHANT: %s (rate=%.2f Mbps)",
                flow_tuple, rate * 8 / 1_000_000,
            )

        return entry['is_elephant']

    def _install_flow_rule(self, datapath, flow_tuple, out_port, is_elephant, buffer_id=None):
        """Install a 5-tuple flow rule on a switch."""
        parser = datapath.ofproto_parser
        ofproto = datapath.ofproto
        src_ip, dst_ip, proto, src_port, dst_port = flow_tuple

        priority = PRIORITY_ELEPHANT if is_elephant else PRIORITY_MICE
        idle_timeout = FLOW_IDLE_TIMEOUT_ELEPHANT if is_elephant else FLOW_IDLE_TIMEOUT_MICE

        match_kwargs = {
            'eth_type': ether_types.ETH_TYPE_IP,
            'ipv4_src': src_ip,
            'ipv4_dst': dst_ip,
            'ip_proto': proto,
        }

        if proto == 6:  # TCP
            match_kwargs['tcp_src'] = src_port
            match_kwargs['tcp_dst'] = dst_port
        elif proto == 17:  # UDP
            match_kwargs['udp_src'] = src_port
            match_kwargs['udp_dst'] = dst_port

        match = parser.OFPMatch(**match_kwargs)
        actions = [parser.OFPActionOutput(out_port)]
        inst = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS, actions)]

        kwargs = {
            'datapath': datapath,
            'priority': priority,
            'match': match,
            'instructions': inst,
            'idle_timeout': idle_timeout,
            'hard_timeout': 0,
            'flags': ofproto.OFPFF_SEND_FLOW_REM,
        }

        if buffer_id is not None:
            kwargs['buffer_id'] = buffer_id

        mod = parser.OFPFlowMod(**kwargs)
        datapath.send_msg(mod)

    def _select_elephant_path(self, src_dpid, dst_dpid):
        """Select the best path for an elephant flow using ML weights.

        Returns:
            (fwd_ports, rev_ports) or (None, None) if no path found.
        """
        self.weight_engine.apply_weights_to_topology(self.topo)

        paths_with_cost = self.topo.compute_k_shortest_paths(
            src_dpid, dst_dpid, k=K_PATHS, weight='weight'
        )

        if not paths_with_cost:
            return None, None

        best_idx = 0
        best_cost = float('inf')
        for idx, (path_nodes, cost) in enumerate(paths_with_cost):
            if cost < best_cost:
                best_cost = cost
                best_idx = idx

        best_path = paths_with_cost[best_idx][0]
        fwd, rev = self.topo.path_to_ports(best_path)

        self.logger.info(
            "Elephant path selected: ingress=s%d -> s%d, cost=%.2f",
            src_dpid, dst_dpid, best_cost,
        )

        return fwd, rev

    def _migrate_elephant_flow(self, flow_tuple, new_fwd, new_rev):
        """Install elephant flow rules along the new path, replacing old ones."""
        src_ip, dst_ip, proto, src_port, dst_port = flow_tuple

        def _build_match_kwargs(sip, dip, sp, dp, p):
            mk = {
                'eth_type': ether_types.ETH_TYPE_IP,
                'ipv4_src': sip,
                'ipv4_dst': dip,
                'ip_proto': p,
            }
            if p == 6:
                mk['tcp_src'] = sp
                mk['tcp_dst'] = dp
            elif p == 17:
                mk['udp_src'] = sp
                mk['udp_dst'] = dp
            return mk

        # Forward direction
        fwd_match = _build_match_kwargs(src_ip, dst_ip, src_port, dst_port, proto)
        for dpid, out_port in new_fwd.items():
            if dpid in self.datapaths:
                dp = self.datapaths[dpid]
                parser = dp.ofproto_parser
                ofproto = dp.ofproto

                old_key = (flow_tuple, dpid)
                if old_key in self.flow_rules_installed:
                    match = parser.OFPMatch(**fwd_match)
                    mod = parser.OFPFlowMod(
                        datapath=dp,
                        command=ofproto.OFPFC_DELETE_STRICT,
                        priority=PRIORITY_ELEPHANT,
                        out_port=ofproto.OFPP_ANY,
                        out_group=ofproto.OFPG_ANY,
                        match=match,
                    )
                    dp.send_msg(mod)
                    self.flow_rules_installed.discard(old_key)

                self._install_flow_rule(dp, flow_tuple, out_port, is_elephant=True)
                self.flow_rules_installed.add(old_key)

        # Reverse direction
        rev_tuple = (dst_ip, src_ip, proto, dst_port, src_port)
        rev_match = _build_match_kwargs(dst_ip, src_ip, dst_port, src_port, proto)
        for dpid, out_port in new_rev.items():
            if dpid in self.datapaths:
                dp = self.datapaths[dpid]
                parser = dp.ofproto_parser
                ofproto = dp.ofproto

                old_key = (rev_tuple, dpid)
                if old_key in self.flow_rules_installed:
                    match = parser.OFPMatch(**rev_match)
                    mod = parser.OFPFlowMod(
                        datapath=dp,
                        command=ofproto.OFPFC_DELETE_STRICT,
                        priority=PRIORITY_ELEPHANT,
                        out_port=ofproto.OFPP_ANY,
                        out_group=ofproto.OFPG_ANY,
                        match=match,
                    )
                    dp.send_msg(mod)
                    self.flow_rules_installed.discard(old_key)

                self._install_flow_rule(dp, rev_tuple, out_port, is_elephant=True)
                self.flow_rules_installed.add(old_key)

        entry = self.flow_table.get(flow_tuple)
        if entry:
            entry['path_fwd'] = new_fwd

        self.logger.info("Elephant flow %s migrated to new path", flow_tuple)

    def _check_elephant_flows(self):
        """Check all active elephant flows for better path availability."""
        now = time.time()
        stale_flows = []

        for flow_tuple, entry in self.flow_table.items():
            if not entry['is_elephant']:
                continue

            if now - entry['last_seen'] > FLOW_IDLE_TIMEOUT_ELEPHANT:
                stale_flows.append(flow_tuple)
                continue

            src_ip, dst_ip = flow_tuple[0], flow_tuple[1]
            src_mac = self.ip_to_mac.get(src_ip)
            dst_mac = self.ip_to_mac.get(dst_ip)
            if not src_mac or not dst_mac:
                continue

            src_loc = self.topo.get_host_location(src_mac)
            dst_loc = self.topo.get_host_location(dst_mac)
            if not src_loc or not dst_loc:
                continue

            fwd, rev = self._select_elephant_path(src_loc[0], dst_loc[0])

            if fwd is not None and entry.get('path_fwd') != fwd:
                self.logger.info(
                    "Elephant flow %s: better path found, migrating", flow_tuple
                )
                self._migrate_elephant_flow(flow_tuple, fwd, rev)

        for ft in stale_flows:
            self.flow_table.pop(ft, None)
            self.flow_rules_installed = {
                k for k in self.flow_rules_installed if k[0] != ft
            }
            self.logger.info("Elephant flow %s expired (idle timeout)", ft)

    # ──────────────────────────────────────────────
    # K-path computation and installation
    # ──────────────────────────────────────────────
    def _compute_and_install_paths(self):
        """Compute K shortest paths using dynamic weights and install flow rules."""
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

        # Apply current weights to topology graph
        self.weight_engine.apply_weights_to_topology(self.topo)

        # Compute K shortest paths
        paths_with_cost = self.topo.compute_k_shortest_paths(
            src_dpid, dst_dpid, k=K_PATHS, weight='weight'
        )

        if not paths_with_cost:
            self.logger.warning("No path found between s%d and s%d", src_dpid, dst_dpid)
            return

        self.k_paths = []
        self.path_util_keys = {}

        for idx, (path_nodes, cost) in enumerate(paths_with_cost):
            fwd, rev = self.topo.path_to_ports(path_nodes)
            util_keys = self.topo.get_path_util_keys(fwd, rev)
            self.k_paths.append((fwd, rev, path_nodes, cost))
            self.path_util_keys[str(idx)] = util_keys

        # Install all candidate paths with standby priority
        for idx, (fwd, rev, _, _) in enumerate(self.k_paths):
            self._install_full_path_dynamic(str(idx), PRIORITY_STANDBY_PATH, fwd, rev)

        self.active_path_idx = 0
        self.set_path_util_keys(self.path_util_keys)
        self.path_installed = True

        self.logger.info(
            "K=%d paths computed: ingress=s%d, costs=%s",
            len(self.k_paths), src_dpid,
            [f"{c:.2f}" for _, _, _, c in self.k_paths],
        )

    # ──────────────────────────────────────────────
    # Path installation and switching (dynamic port mapping)
    # ──────────────────────────────────────────────
    def _install_full_path_dynamic(self, path_name, priority, fwd_ports, rev_ports):
        """Install explicit flow rules on all switches along the path (dynamic port mapping)"""
        hosts = list(self.topo.host_table.keys())
        if len(hosts) < 2:
            return

        mac_dst = hosts[1]  # forward destination
        mac_src = hosts[0]  # reverse destination

        for dpid, out_port in fwd_ports.items():
            if dpid in self.datapaths:
                dp = self.datapaths[dpid]
                parser = dp.ofproto_parser
                # Forward: eth_dst=mac_dst -> path output port
                match = parser.OFPMatch(eth_dst=mac_dst)
                actions = [parser.OFPActionOutput(out_port)]
                self.add_flow(dp, priority, match, actions)

        for dpid, out_port in rev_ports.items():
            if dpid in self.datapaths:
                dp = self.datapaths[dpid]
                parser = dp.ofproto_parser
                match = parser.OFPMatch(eth_dst=mac_src)
                actions = [parser.OFPActionOutput(out_port)]
                self.add_flow(dp, priority, match, actions)

        self.logger.info("  Installed path %s (dynamic)", path_name)

    def _switch_path(self, new_idx):
        """Switch to path at new_idx using make-before-break."""
        old_idx = self.active_path_idx
        self.logger.info(
            ">>> Switching from path %d to path %d", old_idx, new_idx
        )

        if self.path_installed and new_idx < len(self.k_paths):
            fwd, rev, _, _ = self.k_paths[new_idx]
            self._install_full_path_dynamic(
                str(new_idx), PRIORITY_STANDBY_PATH, fwd, rev
            )

        self.active_path_idx = new_idx
        hub.spawn(self._async_cleanup_old_path, old_idx)

    def _async_cleanup_old_path(self, old_idx):
        """Remove flow rules for the old path."""
        hub.sleep(0.2)
        if old_idx >= len(self.k_paths):
            return

        fwd, rev, _, _ = self.k_paths[old_idx]
        hosts = list(self.topo.host_table.keys())
        if len(hosts) < 2:
            return

        mac_dst = hosts[1]
        mac_src = hosts[0]

        for dpid, out_port in fwd.items():
            if dpid not in self.datapaths:
                continue
            dp = self.datapaths[dpid]
            parser = dp.ofproto_parser
            ofproto = dp.ofproto
            match = parser.OFPMatch(eth_dst=mac_dst)
            mod = parser.OFPFlowMod(
                datapath=dp, command=ofproto.OFPFC_DELETE,
                out_port=out_port, out_group=ofproto.OFPG_ANY, match=match,
            )
            dp.send_msg(mod)

        for dpid, out_port in rev.items():
            if dpid not in self.datapaths:
                continue
            dp = self.datapaths[dpid]
            parser = dp.ofproto_parser
            ofproto = dp.ofproto
            match = parser.OFPMatch(eth_dst=mac_src)
            mod = parser.OFPFlowMod(
                datapath=dp, command=ofproto.OFPFC_DELETE,
                out_port=out_port, out_group=ofproto.OFPG_ANY, match=match,
            )
            dp.send_msg(mod)

        self.logger.info("  Cleaned up flows for path %d", old_idx)

    # ──────────────────────────────────────────────
    # Decision loop
    # ──────────────────────────────────────────────
    def _decision_loop(self):
        """Periodically update weights, predict, and select best path."""
        while True:
            hub.sleep(self.curr_poll_interval)
            if not self.datapaths or not self.k_paths:
                continue

            # Feed current utilizations to weight engine
            for (dpid, port_no), util in self.link_utilization.items():
                self.weight_engine.register_link(dpid, port_no)
                self.weight_engine.update_utilization(dpid, port_no, util)

            # Run ML predictions
            self.weight_engine.predict_all()

            # Recompute weights and find best path
            self.weight_engine.apply_weights_to_topology(self.topo)

            best_idx = self.active_path_idx
            best_cost = float('inf')
            for idx, (fwd, rev, path_nodes, _) in enumerate(self.k_paths):
                cost = self.topo._path_cost(path_nodes, weight='weight')
                if cost < best_cost:
                    best_cost = cost
                    best_idx = idx

            # Log state
            summary = self.weight_engine.get_state_summary()
            utils = [self._get_path_util(i) for i in range(len(self.k_paths))]
            self.logger.info(
                "Paths: %s, Active: %d, Best: %d (%.2f), ML: %d links predicted",
                [f"{u*100:.0f}%" for u in utils],
                self.active_path_idx, best_idx, best_cost,
                summary["links_with_prediction"],
            )

            # Switch if a different path is better
            if best_idx != self.active_path_idx:
                self._switch_path(best_idx)

            # Monitor elephant flows for migration
            self._check_elephant_flows()

    def _get_path_util(self, path_idx):
        """Get bottleneck utilization for path at given index."""
        key = str(path_idx)
        keys = self.path_util_keys.get(key, set())
        if not keys:
            return 0
        utils = [self.link_utilization.get(k, 0) for k in keys]
        return max(utils) if utils else 0

    def _cleanup(self):
        pass  # DynamicWeightEngine has no file handles to close

    # ──────────────────────────────────────────────
    # Stats reply
    # ──────────────────────────────────────────────
    @set_ev_cls(ofp_event.EventOFPPortStatsReply, MAIN_DISPATCHER)
    def port_stats_reply_handler(self, ev):
        self.handle_port_stats_reply(ev)

    # ──────────────────────────────────────────────
    # Topology discovery (LLDP -> dynamic graph maintenance)
    # ──────────────────────────────────────────────
    @set_ev_cls(topo_event.EventSwitchEnter)
    def _switch_add_handler(self, ev):
        dpid = ev.switch.dp.id
        self.datapaths[dpid] = ev.switch.dp
        self.topo.add_switch(dpid)
        self.logger.info("Topology: switch s%d added (graph node created)", dpid)

    @set_ev_cls(topo_event.EventSwitchLeave)
    def _switch_del_handler(self, ev):
        dpid = ev.switch.dp.id
        self.datapaths.pop(dpid, None)
        self.topo.remove_switch(dpid)
        self.logger.info("Topology: switch s%d removed (graph node deleted)", dpid)

    @set_ev_cls(topo_event.EventLinkAdd)
    def _link_add_handler(self, ev):
        src = ev.link.src
        dst = ev.link.dst
        self.topo.add_link(src.dpid, src.port_no, dst.dpid, dst.port_no)
        self.logger.info(
            "Topology: link s%d:p%d -> s%d:p%d (graph edges added)",
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

    def _invalidate_paths(self):
        """Clear K-path cache on topology change, triggering recomputation"""
        self.k_paths = []
        self.active_path_idx = 0
        self.path_util_keys = {}
        self.path_installed = False
        self.logger.info("Paths invalidated due to topology change")
