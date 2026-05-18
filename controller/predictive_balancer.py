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
from ryu.lib.packet import packet, ethernet, ether_types, arp
from ryu.topology import event as topo_event

from controller.stats_mixin import StatsMixin
from controller.topology_manager import TopologyManager
from controller.weight_engine import DynamicWeightEngine

# Constants
K_PATHS = 3  # number of candidate paths to compute
PRIORITY_ACTIVE_PATH = 20
PRIORITY_STANDBY_PATH = 10


class PredictiveBalancer(app_manager.RyuApp, StatsMixin):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super(PredictiveBalancer, self).__init__(*args, **kwargs)
        self.mac_to_port = {}
        self.ip_to_mac = {}
        self.datapaths = {}
        self.path_installed = False

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

        # Check if paths need to be installed (get all discovered host MACs)
        all_hosts = list(self.topo.host_table.keys())
        if not self.path_installed and len(all_hosts) >= 2:
            self._compute_and_install_paths()
            if self.path_installed:
                out_port = self._get_path_out_port(dpid)
                if out_port is not None:
                    self._send_packet(datapath, in_port, out_port, msg)
                    return

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
