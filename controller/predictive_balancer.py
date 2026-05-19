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
AGG_DPID_MIN = 9    # 0x09
AGG_DPID_MAX = 22   # 0x16


class PredictiveBalancer(BaseBalancer):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super(PredictiveBalancer, self).__init__(*args, **kwargs)
        self.active_sessions = {}  # (src_mac, dst_mac) -> {'switch_set', 'fwd_ports', 'last_seen'}
        self.path_installed = set()
        self.groups_installed = set()  # set of agg dpids with installed Group Tables
        # Broadcast storm cache: (dpid, src_mac, eth_type) -> timestamp
        self.broadcast_cache = {}

        self.init_stats(topo_manager=self.topo)

        model_path = os.path.join(os.path.dirname(__file__), "..", "models", "global_mlp_model.pkl")
        self.weight_engine = DynamicWeightEngine(model_path=model_path)

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
        # Group Table mode: reverse rules are pre-installed per-session
        return None

    def _invalidate_paths(self):
        for dpid in range(AGG_DPID_MIN, AGG_DPID_MAX + 1):
            self._delete_group_table(dpid)
        self.active_sessions.clear()
        self.path_installed.clear()
        self.logger.info("Paths and group tables invalidated due to topology change")

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
    # Group Table 管理
    # ──────────────────────────────────────────────
    def _safe_flood(self, datapath, in_port, msg):
        """安全无环洪泛：生成树骨干端口（防环）+ 边缘端口（达主机）"""
        dpid = datapath.id
        st_ports = self.topo.compute_spanning_tree_ports().get(dpid, set())
        
        # 使用 list() 获取当前端口键的静态快照
        # 避免 Ryu 拓扑发现模块在其他协程中异步增删端口导致字典大小改变引发崩溃
        active_ports = list(datapath.ports.keys())
        
        for port_no in active_ports:
            if port_no >= datapath.ofproto.OFPP_MAX or port_no == in_port:
                continue
            if self.topo.is_edge_port(dpid, port_no) or port_no in st_ports:
                self._send_packet(datapath, in_port, port_no, msg)

    @staticmethod
    def _is_agg_switch(dpid):
        return AGG_DPID_MIN <= dpid <= AGG_DPID_MAX

    def _is_core_facing_port(self, dpid, port_no):
        """判断指定端口是否为面向核心层的上行端口"""
        if not self._is_agg_switch(dpid):
            return False
        core_ports = self.topo.get_core_facing_ports(dpid)
        return any(p == port_no for p, _ in core_ports)

    def _ensure_group_table(self, dpid):
        """On-demand Group Table creation for an aggregation switch.

        OVS OFPGT_SELECT 的哈希行为由 bridge other_config 控制：
        fat_tree_topo.py 中已配置 dp_hash，保证同五元组同路径。
        """
        if dpid in self.groups_installed:
            return True
        if dpid not in self.datapaths:
            return False

        core_ports = self.topo.get_core_facing_ports(dpid)
        if len(core_ports) < 1:
            self.logger.warning("Agg switch s%d has no core-facing ports", dpid)
            return False

        dp = self.datapaths[dpid]
        ofproto = dp.ofproto
        parser = dp.ofproto_parser

        buckets = []
        for port_no, core_dpid in core_ports:
            actions = [parser.OFPActionOutput(port_no)]
            bucket = parser.OFPBucket(
                weight=1,
                watch_port=port_no,
                watch_group=ofproto.OFPG_ANY,
                actions=actions,
            )
            buckets.append(bucket)

        req = parser.OFPGroupMod(
            datapath=dp,
            command=ofproto.OFPGC_ADD,
            type_=ofproto.OFPGT_SELECT,
            group_id=dpid,
            buckets=buckets,
        )
        dp.send_msg(req)
        self.groups_installed.add(dpid)
        self.logger.info(
            "Group Table created: s%d, buckets=%s",
            dpid, [(p, c) for p, c in core_ports],
        )
        return True

    def _update_group_weights(self):
        """Update Group Table bucket weights based on MLP predictions."""
        group_weights = self.weight_engine.get_group_weights(self.topo)

        for agg_dpid, port_weight_pairs in group_weights.items():
            if agg_dpid not in self.groups_installed:
                continue
            if agg_dpid not in self.datapaths:
                continue

            dp = self.datapaths[agg_dpid]
            ofproto = dp.ofproto
            parser = dp.ofproto_parser

            buckets = []
            for port_no, weight in port_weight_pairs:
                actions = [parser.OFPActionOutput(port_no)]
                bucket = parser.OFPBucket(
                    weight=weight,
                    watch_port=port_no,
                    watch_group=ofproto.OFPG_ANY,
                    actions=actions,
                )
                buckets.append(bucket)

            req = parser.OFPGroupMod(
                datapath=dp,
                command=ofproto.OFPGC_MODIFY,
                type_=ofproto.OFPGT_SELECT,
                group_id=agg_dpid,
                buckets=buckets,
            )
            dp.send_msg(req)

        if group_weights:
            self.logger.info(
                "Group weights updated: %s",
                {d: w for d, w in group_weights.items() if d in self.groups_installed},
            )

    def _delete_group_table(self, dpid):
        """Remove Group Table from a switch."""
        if dpid not in self.datapaths:
            return
        dp = self.datapaths[dpid]
        ofproto = dp.ofproto
        parser = dp.ofproto_parser
        req = parser.OFPGroupMod(
            datapath=dp,
            command=ofproto.OFPGC_DELETE,
            group_id=ofproto.OFPG_ALL,
        )
        dp.send_msg(req)
        self.groups_installed.discard(dpid)

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
            cache_key = (dpid, src, eth.ethertype)
            now = time.time()
            if len(self.broadcast_cache) > 1000:
                self.broadcast_cache.clear()
            if cache_key in self.broadcast_cache:
                if now - self.broadcast_cache[cache_key] < 0.5:
                    return
            self.broadcast_cache[cache_key] = now

        # Learn host location via topology manager
        self.topo.learn_host(src, dpid, in_port)

        self.mac_to_port.setdefault(dpid, {})
        if src not in self.mac_to_port[dpid]:
            self.mac_to_port[dpid][src] = in_port

        # ARP proxy + loop-free flooding
        if eth.ethertype == ether_types.ETH_TYPE_ARP:
            # ====== Cold-start: safe flood (spanning tree + edge ports) ======
            if len(self.topo.G.nodes) < 20:
                self._safe_flood(datapath, in_port, msg)
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
                    if self._is_core_facing_port(dpid, out_port) and dpid in self.groups_installed:
                        actions = [parser.OFPActionGroup(group_id=dpid)]
                    else:
                        actions = [parser.OFPActionOutput(out_port)]
                    if buf_id is not None:
                        self.add_flow(datapath, PRIORITY_ACTIVE_PATH, match, actions, buf_id, idle_timeout=FLOW_IDLE_TIMEOUT)
                    else:
                        self.add_flow(datapath, PRIORITY_ACTIVE_PATH, match, actions, idle_timeout=FLOW_IDLE_TIMEOUT)
                        self._send_packet(datapath, in_port, out_port, msg)
                    return

        # Destination unknown or path not available: safe flood
        self._safe_flood(datapath, in_port, msg)

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

        # Flood ARP request via safe flood (spanning tree + edge ports)
        self._safe_flood(datapath, in_port, msg)

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
                    self._safe_flood(datapath, in_port, msg)
                    return
        else:
            self._safe_flood(datapath, in_port, msg)
            return
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
    # 路径安装（Group Table 模式）
    # ──────────────────────────────────────────────
    def _install_path_rules_for_all_paths(self, src_mac, dst_mac, all_paths):
        """Install flow rules for all ECMP paths, using Group Table on agg switches for upward traffic.

        Returns: set of all dpids where rules were installed.
        """
        switch_set = set()
        fwd_ports = {}

        for path_nodes in all_paths:
            fwd, rev = self.topo.path_to_ports(path_nodes)

            # Install forward rules
            for dpid, out_port in fwd.items():
                if dpid in self.datapaths:
                    dp = self.datapaths[dpid]
                    parser = dp.ofproto_parser
                    match = parser.OFPMatch(eth_src=src_mac, eth_dst=dst_mac)
                    if self._is_core_facing_port(dpid, out_port) and self._ensure_group_table(dpid):
                        actions = [parser.OFPActionGroup(group_id=dpid)]
                    else:
                        actions = [parser.OFPActionOutput(out_port)]
                    self.add_flow(dp, PRIORITY_ACTIVE_PATH, match, actions, idle_timeout=FLOW_IDLE_TIMEOUT)
                    switch_set.add(dpid)
                    if dpid not in fwd_ports:
                        fwd_ports[dpid] = out_port

            # Install reverse rules
            for dpid, out_port in rev.items():
                if dpid in self.datapaths:
                    dp = self.datapaths[dpid]
                    parser = dp.ofproto_parser
                    match = parser.OFPMatch(eth_src=dst_mac, eth_dst=src_mac)
                    if self._is_core_facing_port(dpid, out_port) and self._ensure_group_table(dpid):
                        actions = [parser.OFPActionGroup(group_id=dpid)]
                    else:
                        actions = [parser.OFPActionOutput(out_port)]
                    self.add_flow(dp, PRIORITY_ACTIVE_PATH, match, actions, idle_timeout=FLOW_IDLE_TIMEOUT)
                    switch_set.add(dpid)

        return switch_set, fwd_ports

    def _compute_and_install_path(self, src_mac, dst_mac):
        """Compute ECMP paths and install flow rules with Group Table on agg switches."""
        src_loc = self.topo.get_host_location(src_mac)
        dst_loc = self.topo.get_host_location(dst_mac)
        if not src_loc or not dst_loc:
            return

        src_dpid, dst_dpid = src_loc[0], dst_loc[0]

        # Find one ECMP path for edge-to-agg selection
        result = self.topo.compute_ecmp_path(src_dpid, dst_dpid, src_mac, dst_mac)
        if not result:
            return

        primary_path, cost = result

        # From the source agg switch, enumerate all paths to destination
        # This finds all core paths for the Group Table
        src_agg = None
        dst_agg = None
        for node in primary_path:
            if self._is_agg_switch(node):
                if src_agg is None:
                    src_agg = node
                dst_agg = node

        if src_agg and dst_agg and src_agg != dst_agg:
            # Cross-pod: enumerate all paths from src_agg to dst_agg
            agg_paths = self.topo.enumerate_all_shortest_paths(src_agg, dst_agg)
            # Build full paths: [edge_src, ...agg_src] + [agg_path] + [agg_dst, ...edge_dst]
            # Find prefix (edge -> src_agg) and suffix (dst_agg -> edge) from primary path
            prefix = []
            for node in primary_path:
                prefix.append(node)
                if node == src_agg:
                    break
            suffix = []
            capturing = False
            for node in primary_path:
                if node == dst_agg:
                    capturing = True
                if capturing:
                    suffix.append(node)
            # Combine: for each agg_path, build full path
            all_full_paths = []
            for agg_path in agg_paths:
                full_path = prefix[:-1] + agg_path + suffix[1:]
                all_full_paths.append(full_path)
        else:
            # Same-pod: only one path, no Group Table needed
            all_full_paths = [primary_path]

        session_key = (src_mac, dst_mac)
        switch_set, fwd_ports = self._install_path_rules_for_all_paths(
            src_mac, dst_mac, all_full_paths,
        )

        # 计算主路径的 util_keys，用于大象流带宽估算
        primary_fwd, primary_rev = self.topo.path_to_ports(primary_path)
        util_keys = self.topo.get_path_util_keys(primary_fwd, primary_rev)

        self.active_sessions[session_key] = {
            'src_mac': src_mac,
            'dst_mac': dst_mac,
            'switch_set': switch_set,
            'fwd_ports': fwd_ports,
            'util_keys': util_keys,
            'last_seen': time.time(),
        }

        self.logger.info(
            "Path installed: %s -> %s, primary=%s, paths=%d, cost=%.2f",
            src_mac, dst_mac, primary_path, len(all_full_paths), cost,
        )

    # ──────────────────────────────────────────────
    # 大象流迁移（精准隔离，防止群聚）
    # ──────────────────────────────────────────────
    ELEPHANT_THRESHOLD = 0.50  # 链路利用率超过 50% 视为拥塞

    def _install_explicit_path(self, src_mac, dst_mac, fwd_ports, rev_ports):
        """安装优先级更高的明确路径规则，绕过组表实现强制迁移"""
        for dpid, out_port in fwd_ports.items():
            if dpid in self.datapaths:
                dp = self.datapaths[dpid]
                parser = dp.ofproto_parser
                match = parser.OFPMatch(eth_src=src_mac, eth_dst=dst_mac)
                actions = [parser.OFPActionOutput(out_port)]
                self.add_flow(dp, PRIORITY_ACTIVE_PATH + 5, match, actions, idle_timeout=FLOW_IDLE_TIMEOUT)

        for dpid, out_port in rev_ports.items():
            if dpid in self.datapaths:
                dp = self.datapaths[dpid]
                parser = dp.ofproto_parser
                match = parser.OFPMatch(eth_src=dst_mac, eth_dst=src_mac)
                actions = [parser.OFPActionOutput(out_port)]
                self.add_flow(dp, PRIORITY_ACTIVE_PATH + 5, match, actions, idle_timeout=FLOW_IDLE_TIMEOUT)

    def _find_congested_sessions(self):
        """找出经过拥塞链路的会话（大象流候选）"""
        congested = []
        for session_key, session in self.active_sessions.items():
            for dpid in session.get('switch_set', set()):
                for (link_dpid, link_port), util in self.link_utilization.items():
                    if link_dpid == dpid and util > self.ELEPHANT_THRESHOLD:
                        congested.append(session_key)
                        break
                else:
                    continue
                break
        return congested

    # ──────────────────────────────────────────────
    # 决策循环：Group Table 权重更新 + 串行大象流迁移（容量占位）
    # ──────────────────────────────────────────────
    def _decision_loop(self):
        while True:
            hub.sleep(self.curr_poll_interval)
            if not self.datapaths:
                continue

            # 1. 更新 MLP 预测
            self.weight_engine.update_all_utilizations(self.link_utilization)
            self.weight_engine.predict_all()

            # 2. 更新拓扑链路权重 + Group Table 权重（新流自动按权重分配）
            self.weight_engine.apply_weights_to_topology(self.topo)
            if self.groups_installed:
                self._update_group_weights()

            # 3. 串行大象流迁移：容量占位防止群聚震荡
            max_util = max(self.link_utilization.values()) if self.link_utilization else 0
            if max_util > self.ELEPHANT_THRESHOLD:
                congested_keys = self._find_congested_sessions()
                # 虚拟利用率表：初始值 = 当前真实利用率
                virtual_util = dict(self.link_utilization)
                migrated = 0

                for session_key in congested_keys:
                    session = self.active_sessions.get(session_key)
                    if not session:
                        continue
                    src_mac, dst_mac = session['src_mac'], session['dst_mac']
                    src_loc = self.topo.get_host_location(src_mac)
                    dst_loc = self.topo.get_host_location(dst_mac)
                    if not (src_loc and dst_loc):
                        continue

                    # 用虚拟利用率更新图权重，使已迁移流的路径"变重"
                    self._apply_virtual_weights(virtual_util)
                    result = self.topo.compute_optimal_path(
                        src_loc[0], dst_loc[0], weight='weight',
                    )
                    if not result:
                        continue

                    path_nodes, _ = result
                    fwd, rev = self.topo.path_to_ports(path_nodes)

                    # 模拟占位：为新路径上的每条链路预留带宽
                    self._book_capacity(virtual_util, fwd, rev, session_key)

                    self._install_explicit_path(src_mac, dst_mac, fwd, rev)
                    migrated += 1
                    self.logger.info(
                        "Elephant migration #%d: %s -> %s, path=%s",
                        migrated, src_mac, dst_mac, path_nodes,
                    )

                # 恢复真实权重（虚拟占位仅用于本轮决策）
                self.weight_engine.apply_weights_to_topology(self.topo)

            # 4. 清理过期会话
            now = time.time()
            stale = [
                k for k, v in self.active_sessions.items()
                if now - v.get('last_seen', 0) > SESSION_TIMEOUT
            ]
            for k in stale:
                session = self.active_sessions.pop(k)
                self._cleanup_session_rules(session)
                self.logger.info("Session expired: %s -> %s", k[0], k[1])

            # 5. 日志摘要
            summary = self.weight_engine.get_state_summary()
            self.logger.info(
                "ML: %d links predicted, max_util=%.1f%%, groups=%d, sessions=%d",
                summary["links_monitored"],
                max_util * 100,
                len(self.groups_installed),
                len(self.active_sessions),
            )

    def _apply_virtual_weights(self, virtual_util):
        """用虚拟利用率更新拓扑图边权重（仅影响本轮决策的路径计算）"""
        for (src, dst), port in self.topo.link_ports.items():
            key = (src, port)
            current = virtual_util.get(key, 0.0)
            predicted = self.weight_engine.predicted_utils.get(key, current)
            weight = (
                self.weight_engine.ALPHA * 1.0
                + self.weight_engine.BETA * current
                + self.weight_engine.GAMMA * predicted
            )
            self.topo.set_edge_weight(src, dst, weight)

    def _book_capacity(self, virtual_util, fwd_ports, rev_ports, session_key):
        """在虚拟利用率表中为新路径预留带宽（容量占位）。

        估算该大象流的带宽贡献：取其当前拥塞链路利用率的均摊值，
        或回退到 ELEPHANT_THRESHOLD * BW_AGG_CORE 作为保守估计。
        """
        session = self.active_sessions.get(session_key)
        if not session:
            return

        # 尝试从当前路径链路估算该流的带宽贡献
        est_bw = 0.0
        old_keys = session.get('util_keys', set())
        if old_keys:
            utils_on_path = [self.link_utilization.get(k, 0) for k in old_keys]
            if utils_on_path:
                # 取路径上的最大利用率作为该流的带宽估计（保守）
                est_bw = max(utils_on_path)

        # 回退：用阈值 × 核心链路带宽
        if est_bw <= 0:
            est_bw = self.ELEPHANT_THRESHOLD * 2  # BW_AGG_CORE = 2 Mbps

        # 将估计带宽转化为利用率增量（占链路容量的比例）
        # BW_AGG_CORE = 2Mbps → 1Mbps 的流对应 util 增量 0.5
        util_increment = est_bw / 2  # 归一化到 [0,1]

        for dpid, out_port in fwd_ports.items():
            key = (dpid, out_port)
            virtual_util[key] = min(1.0, virtual_util.get(key, 0.0) + util_increment)

        for dpid, out_port in rev_ports.items():
            key = (dpid, out_port)
            virtual_util[key] = min(1.0, virtual_util.get(key, 0.0) + util_increment)

    def _cleanup_session_rules(self, session):
        """Remove all flow rules for an expired session."""
        src_mac = session.get('src_mac')
        dst_mac = session.get('dst_mac')
        for dpid in session.get('switch_set', set()):
            if dpid not in self.datapaths:
                continue
            dp = self.datapaths[dpid]
            parser = dp.ofproto_parser
            ofproto = dp.ofproto
            match = parser.OFPMatch(eth_src=src_mac, eth_dst=dst_mac)
            mod = parser.OFPFlowMod(
                datapath=dp,
                command=ofproto.OFPFC_DELETE,
                out_port=ofproto.OFPP_ANY,
                out_group=ofproto.OFPG_ANY,
                match=match,
            )
            dp.send_msg(mod)
            match_rev = parser.OFPMatch(eth_src=dst_mac, eth_dst=src_mac)
            mod_rev = parser.OFPFlowMod(
                datapath=dp,
                command=ofproto.OFPFC_DELETE,
                out_port=ofproto.OFPP_ANY,
                out_group=ofproto.OFPG_ANY,
                match=match_rev,
            )
            dp.send_msg(mod_rev)
