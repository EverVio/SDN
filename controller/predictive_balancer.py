import os
import sys
import time
import csv
import numpy as np
import joblib
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

# 流表优先级
PRIORITY_ACTIVE_PATH = 20  # 新路径
PRIORITY_STANDBY_PATH = 10  # 旧路径

PREDICTIVE_CSV_PATH = "./data/predictions.csv"


class DecisionEngine:
    COLD_START_PERIODS = 5
    COOLDOWN_PERIODS = 3
    CONGESTION_THRESHOLD = 0.7
    PREDICT_MAE = 0.06
    EMA_ALPHA = 0.6
    WINDOW_SIZE = 3

    def __init__(self, model_dir, pred_csv_path, poll_interval):
        model_path_a = os.path.join(model_dir, "model_path_A.pkl")
        model_path_b = os.path.join(model_dir, "model_path_B.pkl")

        self.curr_path = "A"
        self.smoothed_a = None
        self.smoothed_b = None
        self.stats_counts = 0
        self.feature_queue = []
        self.cooldown_remaining = 0
        self.model_a = joblib.load(model_path_a)
        self.model_b = joblib.load(model_path_b)

        self.pred_csv = open(pred_csv_path, "w", newline="")
        self.pred_writer = csv.writer(self.pred_csv)
        self.pred_writer.writerow(["timestamp", "link", "predicted", "smoothed"])

    def on_stats_collected(self, util_a, util_b, current_poll_interval=3):
        self.stats_counts += 1

        if self.stats_counts < self.COLD_START_PERIODS:
            if len(self.feature_queue) < self.WINDOW_SIZE:
                self.feature_queue.append((util_a, util_b))
            return None

        if len(self.feature_queue) < self.WINDOW_SIZE:
            self.feature_queue.append((util_a, util_b))
            return None

        if self.cooldown_remaining > 0:
            self.cooldown_remaining -= 1
            if self.cooldown_remaining == 0:
                self.feature_queue.clear()
                self.smoothed_a = None
                self.smoothed_b = None
            return None

        self.feature_queue.pop(0)
        self.feature_queue.append((util_a, util_b))

        combined = []
        for ua, ub in self.feature_queue:
            combined.extend([ua, ub])

        pred_a = self._predict(combined, "A")
        pred_b = self._predict(combined, "B")

        now = time.time()
        if self.smoothed_a is None:
            self.smoothed_a = pred_a
            self.smoothed_b = pred_b
        else:
            self.smoothed_a = (
                self.EMA_ALPHA * pred_a + (1 - self.EMA_ALPHA) * self.smoothed_a
            )
            self.smoothed_b = (
                self.EMA_ALPHA * pred_b + (1 - self.EMA_ALPHA) * self.smoothed_b
            )

        timestamp = (int(now) // current_poll_interval) * current_poll_interval
        self.pred_writer.writerow([timestamp, "path_A", pred_a, self.smoothed_a])
        self.pred_writer.writerow([timestamp, "path_B", pred_b, self.smoothed_b])
        self.pred_csv.flush()

        if self.curr_path == "A":
            if (self.smoothed_a + self.PREDICT_MAE > self.CONGESTION_THRESHOLD) and (
                self.smoothed_b + self.PREDICT_MAE < self.CONGESTION_THRESHOLD
            ):
                self.curr_path = "B"
                self.cooldown_remaining = self.COOLDOWN_PERIODS
                return "B"
        else:
            if (self.smoothed_b + self.PREDICT_MAE > self.CONGESTION_THRESHOLD) and (
                self.smoothed_a + self.PREDICT_MAE < self.CONGESTION_THRESHOLD
            ):
                self.curr_path = "A"
                self.cooldown_remaining = self.COOLDOWN_PERIODS
                return "A"

        return None

    def _predict(self, features, target):
        X = np.array(features).reshape(1, -1)
        model = self.model_a if target == "A" else self.model_b
        return float(model.predict(X)[0])

    def get_state_name(self):
        if self.stats_counts < self.COLD_START_PERIODS:
            return f"ColdStart[{self.stats_counts}/{self.COLD_START_PERIODS}]"
        if len(self.feature_queue) < self.WINDOW_SIZE:
            return "Filling_Queue"
        if self.cooldown_remaining > 0:
            return f"Cooldown[{self.cooldown_remaining}/{self.COOLDOWN_PERIODS}]"
        return "Model_Predict"

    def close(self):
        if self.pred_csv and not self.pred_csv.closed:
            self.pred_csv.close()


class PredictiveBalancer(app_manager.RyuApp, StatsMixin):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super(PredictiveBalancer, self).__init__(*args, **kwargs)
        self.mac_to_port = {}       # {dpid: {mac: port}}
        self.ip_to_mac = {}         # {ip: mac}
        self.datapaths = {}         # {dpid: datapath}
        self.path_installed = False

        # 模块一：拓扑管理器（动态图 + 主机表）
        self.topo = TopologyManager()

        # 路径端口缓存（由拓扑管理器计算，避免重复计算）
        self.path_fwd = {"A": None, "B": None}  # {path_name: {dpid: out_port}}
        self.path_rev = {"A": None, "B": None}
        self.path_util_keys = {"A": set(), "B": set()}  # {(dpid, port_no)}

        self.init_stats(topo_manager=self.topo)

        model_dir = os.path.join(os.path.dirname(__file__), "..", "models")
        self.engine = DecisionEngine(
            model_dir=model_dir,
            pred_csv_path=PREDICTIVE_CSV_PATH,
            poll_interval=self.curr_poll_interval,
        )

        atexit.register(self._cleanup)
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

        # 模块一：通过拓扑管理器学习主机位置
        self.topo.learn_host(src, dpid, in_port)

        self.mac_to_port.setdefault(dpid, {})
        if src not in self.mac_to_port[dpid]:
            self.mac_to_port[dpid][src] = in_port

        # ── 模块二：ARP 代理 + 无环洪泛 ──
        if eth.ethertype == ether_types.ETH_TYPE_ARP:
            arp_pkt = pkt.get_protocol(arp.arp)
            if arp_pkt and arp_pkt.opcode == arp.ARP_REQUEST:
                self._handle_arp_request(datapath, in_port, dpid, arp_pkt, src, msg)
                return

            if arp_pkt and arp_pkt.opcode == arp.ARP_REPLY:
                self._handle_arp_reply(datapath, in_port, dpid, dst, arp_pkt, src, msg)
                return

        # ── 数据包处理 ──
        self._install_reverse_rule(datapath, src, in_port)

        # 检查是否需要首次安装路径（动态获取所有主机 MAC）
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

        # 目的地未知：无环洪泛
        flood_ports = self.topo.get_flood_ports(dpid, in_port)
        if flood_ports:
            for port in flood_ports:
                self._send_packet(datapath, in_port, port, msg)
        else:
            self._send_packet(datapath, in_port, ofproto.OFPP_FLOOD, msg)

    # ──────────────────────────────────────────────
    # 模块二：ARP 代理处理
    # ──────────────────────────────────────────────
    def _handle_arp_request(self, datapath, in_port, dpid, arp_pkt, src_mac, msg):
        """ARP 请求处理：代答或无环洪泛"""
        self._learn_arp_binding(arp_pkt, src_mac)

        target_ip = arp_pkt.dst_ip
        target_mac = self._arp_lookup(target_ip)

        if target_mac:
            target_loc = self.topo.get_host_location(target_mac)
            if target_loc:
                # 已知目标：控制器代答 ARP Reply
                self._send_arp_reply(datapath, in_port, arp_pkt, target_mac)
                return

        # 目标未知：沿生成树无环洪泛
        flood_ports = self.topo.get_flood_ports(dpid, in_port)
        if flood_ports:
            for port in flood_ports:
                self._send_packet(datapath, in_port, port, msg)
        else:
            self._send_packet(datapath, in_port, datapath.ofproto.OFPP_FLOOD, msg)

    def _handle_arp_reply(self, datapath, in_port, dpid, dst_mac, arp_pkt, src_mac, msg):
        """ARP 回复处理：学习绑定 + 转发"""
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
        """构造并发送 ARP Reply（控制器代答）"""
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

        # 构造以太网帧
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
        parser = datapath.ofproto_parser
        match = parser.OFPMatch(eth_dst=mac)
        actions = [parser.OFPActionOutput(in_port)]
        self.add_flow(datapath, 10, match, actions)

    def _get_path_out_port(self, dpid):
        """获取当前路径下该交换机的出端口（动态计算）"""
        curr = self.engine.curr_path
        fwd = self.path_fwd.get(curr)
        if fwd and dpid in fwd:
            return fwd[dpid]
        return None

    def _get_out_port(self, from_dpid, to_dpid):
        """计算从 from_dpid 到 to_dpid 的出端口（基于拓扑图）"""
        # 查找正向路径
        curr = self.engine.curr_path
        fwd = self.path_fwd.get(curr)
        rev = self.path_rev.get(curr)

        if fwd and from_dpid in fwd:
            # 检查正向路径的下一跳
            fwd_chain = list(fwd.keys())
            try:
                idx = fwd_chain.index(from_dpid)
                if idx + 1 < len(fwd_chain) and fwd_chain[idx + 1] == to_dpid:
                    return fwd[from_dpid]
            except ValueError:
                pass

        if rev and from_dpid in rev:
            # 检查反向路径
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
    # 模块一 & 三：路径计算（Suurballe 边不相交）
    # ──────────────────────────────────────────────
    def _compute_and_install_paths(self):
        """动态计算边不相交路径并安装流表"""
        hosts = list(self.topo.host_table.keys())
        if len(hosts) < 2:
            return

        # 取前两个发现的主机作为端点
        mac_a, mac_b = hosts[0], hosts[1]
        loc_a = self.topo.get_host_location(mac_a)
        loc_b = self.topo.get_host_location(mac_b)
        if not loc_a or not loc_b:
            return

        src_dpid = loc_a[0]
        dst_dpid = loc_b[0]

        # 使用拓扑管理器计算边不相交路径
        fwd1, rev1, fwd2, rev2 = self.topo.compute_edge_disjoint_paths(src_dpid, dst_dpid)

        if fwd1 is None:
            self.logger.warning("No path found between s%d and s%d", src_dpid, dst_dpid)
            return

        # 存储路径映射
        self.path_fwd["A"] = fwd1
        self.path_rev["A"] = rev1
        self.path_fwd["B"] = fwd2 if fwd2 else fwd1
        self.path_rev["B"] = rev2 if rev2 else rev1

        # 计算路径利用率键
        self.path_util_keys["A"] = self.topo.get_path_util_keys(fwd1, rev1)
        self.path_util_keys["B"] = self.topo.get_path_util_keys(
            fwd2 if fwd2 else fwd1,
            rev2 if rev2 else rev1
        )

        # 更新 StatsMixin 的路径标签
        self.set_path_util_keys(self.path_util_keys)

        if fwd2 is None:
            self.logger.info("Single path mode (topology does not support edge-disjoint)")
        else:
            self.logger.info("Edge-disjoint paths computed via Suurballe algorithm")

        # 安装正向和反向流表（ML 引擎决定当前路径，入口交换机也由 ML 控制）
        self._install_full_path_dynamic("A", PRIORITY_STANDBY_PATH, fwd1, rev1)
        if fwd2 is not None:
            self._install_full_path_dynamic("B", PRIORITY_STANDBY_PATH, fwd2, rev2)

        self.path_installed = True
        self.logger.info(
            "Paths installed: ingress=s%d, fwd_A=%s, fwd_B=%s",
            src_dpid, fwd1, fwd2
        )

    # ──────────────────────────────────────────────
    # 路径安装与切换（ML 引擎主导 Active/Standby）
    # ──────────────────────────────────────────────
    def _install_full_path_dynamic(self, path_name, priority, fwd_ports, rev_ports):
        """在路径上所有交换机安装显式流表（动态端口映射）"""
        hosts = list(self.topo.host_table.keys())
        if len(hosts) < 2:
            return

        mac_dst = hosts[1]  # 正向目标
        mac_src = hosts[0]  # 反向目标

        for dpid, out_port in fwd_ports.items():
            if dpid in self.datapaths:
                dp = self.datapaths[dpid]
                parser = dp.ofproto_parser
                # 正向：eth_dst=mac_dst → 路径出端口（包括入口交换机，由 ML 引擎控制）
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

    def _switch_path(self, new_path):
        """切换路径：利用 OpenFlow 原子覆盖特性实现先建后拆"""
        old_path = self.engine.curr_path
        self.logger.info(
            ">>> Make-Before-Break: switching from %s to %s", old_path, new_path
        )

        if self.path_installed:
            fwd = self.path_fwd.get(new_path)
            rev = self.path_rev.get(new_path)
            if fwd and rev:
                self._install_full_path_dynamic(new_path, PRIORITY_STANDBY_PATH, fwd, rev)

        # 异步清理旧路径
        hub.spawn(self._async_cleanup_old_path, old_path)

    def _async_cleanup_old_path(self, old_path):
        """精准删除旧路径的残留流表"""
        hub.sleep(0.2)

        fwd = self.path_fwd.get(old_path)
        rev = self.path_rev.get(old_path)
        if not fwd or not rev:
            return

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
                datapath=dp,
                command=ofproto.OFPFC_DELETE,
                out_port=out_port,
                out_group=ofproto.OFPG_ANY,
                match=match,
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
                datapath=dp,
                command=ofproto.OFPFC_DELETE,
                out_port=out_port,
                out_group=ofproto.OFPG_ANY,
                match=match,
            )
            dp.send_msg(mod)

        self.logger.info("  Cleaned up orphaned flows for old path %s", old_path)

    # ──────────────────────────────────────────────
    # 决策循环
    # ──────────────────────────────────────────────
    def _decision_loop(self):
        while True:
            hub.sleep(self.curr_poll_interval)
            if not self.datapaths:
                continue

            util_a = self._get_path_util("A")
            util_b = self._get_path_util("B")

            state = (
                self.engine.get_state_name()
                if hasattr(self.engine, "get_state_name")
                else "?"
            )
            self.logger.info(
                "Path A: %.1f%%, Path B: %.1f%%, Engine: %s, Current: %s, Decision: %s",
                util_a * 100,
                util_b * 100,
                state,
                self.engine.curr_path,
                "pending",
            )

            decision = self.engine.on_stats_collected(
                util_a, util_b, self.curr_poll_interval
            )

            if decision is not None:
                self._switch_path(decision)

    def _get_path_util(self, path_name):
        """获取路径瓶颈利用率（动态键集合）"""
        keys = self.path_util_keys.get(path_name, set())
        if not keys:
            return 0
        utils = [self.link_utilization.get(k, 0) for k in keys]
        return max(utils) if utils else 0

    def _cleanup(self):
        if hasattr(self, "engine"):
            self.engine.close()

    # ──────────────────────────────────────────────
    # 统计回复
    # ──────────────────────────────────────────────
    @set_ev_cls(ofp_event.EventOFPPortStatsReply, MAIN_DISPATCHER)
    def port_stats_reply_handler(self, ev):
        self.handle_port_stats_reply(ev)

    # ──────────────────────────────────────────────
    # 模块一：拓扑发现（LLDP → 动态图维护）
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
            "Topology: link s%d:p%d → s%d:p%d (graph edges added)",
            src.dpid, src.port_no, dst.dpid, dst.port_no,
        )
        # 链路变更后清除路径缓存，下次需要时重新计算
        self._invalidate_paths()

    @set_ev_cls(topo_event.EventLinkDelete)
    def _link_del_handler(self, ev):
        src = ev.link.src
        dst = ev.link.dst
        self.topo.remove_link(src.dpid, dst.dpid)
        self.logger.info("Topology: link s%d → s%d removed", src.dpid, dst.dpid)
        self._invalidate_paths()

    def _invalidate_paths(self):
        """拓扑变更时清除路径缓存，触发重新计算"""
        self.path_fwd = {"A": None, "B": None}
        self.path_rev = {"A": None, "B": None}
        self.path_util_keys = {"A": set(), "B": set()}
        self.path_installed = False
        self.logger.info("Paths invalidated due to topology change")
