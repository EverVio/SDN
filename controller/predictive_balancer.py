import os
import sys
import time
import csv
import networkx as nx
import numpy as np
from sklearn.ensemble import RandomForestRegressor
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

PREDICTIVE_CSV_PATH = "./data/predictions.csv"


class DecisionEngine:
    COLD_START_PERIODS = 5  # 冷启动阶段持续的统计周期数
    COOLDOWN_PERIODS = 3  # 路径切换后的冷却周期数（期间不再切换）
    CONGESTION_THRESHOLD = 0.7
    PREDICT_MAE = 0.06
    EMA_ALPHA = 0.6  # 指数移动平均的平滑因子
    WINDOW_SIZE = 3

    def __init__(self, model_dir, pred_csv_path, poll_interval):
        model_path_a = os.path.join(model_dir, "model_path_A.pkl")
        model_path_b = os.path.join(model_dir, "model_path_B.pkl")

        self.curr_path = "A"
        self.smoothed_a = None
        self.smoothed_b = None
        self.stats_counts = 0
        self.feature_queue = []  # 存储最近 N 次的特征值
        self.cooldown_remaining = 0  # 路径切换冷却时间剩余秒数
        self.model_a = joblib.load(model_path_a)
        self.model_b = joblib.load(model_path_b)
        self.poll_interval = poll_interval

        self.pred_csv = open(pred_csv_path, "w", newline="")
        self.pred_writer = csv.writer(self.pred_csv)
        self.pred_writer.writerow(["timestamp", "link", "predicted", "smoothed"])

    def on_stats_collected(self, util_a, util_b):
        self.stats_counts += 1

        # 阶段一：冷启动阶段——仅收集数据，不做决策
        if self.stats_counts < self.COLD_START_PERIODS:
            if len(self.feature_queue) < self.WINDOW_SIZE:
                self.feature_queue.append((util_a, util_b))
            return None

        # 阶段二：特征积累阶段——继续收集数据，直到达到窗口大小
        if len(self.feature_queue) < self.WINDOW_SIZE:
            self.feature_queue.append((util_a, util_b))
            return None

        # 阶段三：冷却期——不做切换，但继续更新平滑值
        if self.cooldown_remaining > 0:
            self.cooldown_remaining -= 1
            if self.cooldown_remaining == 0:
                # 冷却期结束，重置统计计数以重新进入冷启动阶段
                self.feature_queue.clear()
                self.smoothed_a = None
                self.smoothed_b = None
            return None

        # 阶段四：预测模式
        self.feature_queue.pop(0)  # 移除最旧的特征
        self.feature_queue.append((util_a, util_b))  # 添加最新的特征

        # 构建六维特征向量
        combined = []
        for ua, ub in self.feature_queue:
            combined.extend([ua, ub])

        # 分别预测 path A 和 path B 的拥塞概率
        pred_a = self._predict(combined, "A")
        pred_b = self._predict(combined, "B")

        # EMA 平滑
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

        # 记录预测结果
        timestamp = (int(time.time()) // self.poll_interval) * self.poll_interval
        self.pred_writer.writerow([timestamp, "path_A", pred_a, self.smoothed_a])
        self.pred_writer.writerow([timestamp, "path_B", pred_b, self.smoothed_b])
        self.pred_csv.flush()

        # 决策：MAE 感知切换
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
        self.mac_to_port = {}  # {dpid: {mac: port}}
        self.host_location = {}  # {mac: (dpid, port)}
        self.ip_to_mac = {}  # {ip: mac}
        self.network = nx.Graph()
        self.datapaths = {}  # {dpid: datapath} (StatsMixin 需要)
        self.topo_ready = False
        self.path_installed = False  # 路径流表是否已安装

        self.init_stats()

        model_dir = os.path.join(os.path.dirname(__file__), "..", "models")
        self.engine = DecisionEngine(
            model_dir=model_dir,
            pred_csv_path=PREDICTIVE_CSV_PATH,  # 使用顶部定义的路径
            poll_interval=self.curr_poll_interval,  # StatsMixin 提供的轮询间隔
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
            self._install_full_path(self.engine.curr_path)
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
        path = PATH_PORTS.get(self.engine.curr_path, {})
        return path.get(dpid)

    def _get_out_port(self, from_dpid, to_dpid):
        """计算从 from_dpid 到 to_dpid 的出端口（基于当前路径拓扑）"""
        path = self.engine.curr_path
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
    def _install_full_path(self, path_name):
        """在路径上所有交换机安装显式流表（正向 h1→h3 + 反向 h3→h1）"""
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
                self.add_flow(dp, 10, match, actions)
                # 反向：eth_dst=h1 → 反向路径出端口
                match_rev = parser.OFPMatch(eth_dst=h1_mac)
                actions_rev = [parser.OFPActionOutput(ports_rev[dpid])]
                self.add_flow(dp, 10, match_rev, actions_rev)
                self.logger.info(
                    "  Install: s%d fwd=p%d rev=p%d (path %s)",
                    dpid,
                    ports[dpid],
                    ports_rev[dpid],
                    path_name,
                )

    def _switch_path(self, new_path):
        """切换路径：删除 s1 旧流表 + 在所有交换机安装新流表"""
        old_path = "A" if new_path == "B" else "B"
        self.logger.info(">>> Switching from path %s to path %s", old_path, new_path)
        self._clear_path_flows()
        if self.path_installed:
            self._install_full_path(new_path)

    def _clear_path_flows(self):
        """删除所有交换机上优先级=10 的流表"""
        for dpid, dp in self.datapaths.items():
            parser = dp.ofproto_parser
            ofproto = dp.ofproto
            match = parser.OFPMatch()
            mod = parser.OFPFlowMod(
                datapath=dp,
                command=ofproto.OFPFC_DELETE,
                priority=10,
                out_port=ofproto.OFPP_ANY,
                out_group=ofproto.OFPG_ANY,
                match=match,
            )
            dp.send_msg(mod)
        self.logger.info("  Cleared all priority=10 flows")

    # ──────────────────────────────────────────────
    # 阈值决策循环
    # ──────────────────────────────────────────────
    def _decision_loop(self):
        """每 POLL_INTERVAL 秒采集利用率，交给模型决策"""
        while True:
            hub.sleep(self.curr_poll_interval)
            if not self.datapaths:
                continue

            util_a = self._get_path_util("A")
            util_b = self._get_path_util("B")

            # 调用决策引擎
            decision = self.engine.on_stats_collected(util_a, util_b)

            # 打印状态
            state = (
                self.engine.get_state_name()
                if hasattr(self.engine, "get_state_name")
                else "?"
            )
            self.logger.info(
                "Path A: %.1f%%, Path B: %.1f%%, Engine state: %s, Current path: %s, Decision: %s",
                util_a * 100,
                util_b * 100,
                state,
                self.engine.curr_path,
                decision if decision else "None",
            )

            # 如果引擎要求切换，则执行切换
            if decision is not None:
                self._switch_path(decision)

    def _get_path_util(self, path_name):
        """获取路径瓶颈利用率（所有核心链路的最大值）"""
        if path_name == "A":
            keys = [(1, 3), (2, 2), (4, 3)]
        else:
            keys = [(1, 4), (3, 2), (4, 4)]
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
    # 拓扑发现（LLDP）
    # ──────────────────────────────────────────────
    @set_ev_cls(topo_event.EventSwitchEnter)
    def _switch_add_handler(self, ev):
        dpid = ev.switch.dp.id
        self.datapaths[dpid] = ev.switch.dp
        self.network.add_node(dpid)
        self.logger.info("Topology: switch s%d added", dpid)

    @set_ev_cls(topo_event.EventSwitchLeave)
    def _switch_del_handler(self, ev):
        dpid = ev.switch.dp.id
        self.datapaths.pop(dpid, None)
        if self.network.has_node(dpid):
            self.network.remove_node(dpid)
        self.logger.info("Topology: switch s%d removed", dpid)

    @set_ev_cls(topo_event.EventLinkAdd)
    def _link_add_handler(self, ev):
        src = ev.link.src
        dst = ev.link.dst
        self.network.add_edge(src.dpid, dst.dpid, port_no=src.port_no)
        self.logger.info(
            "Topology: link s%d:p%d → s%d:p%d",
            src.dpid,
            src.port_no,
            dst.dpid,
            dst.port_no,
        )
        if not self.topo_ready and self.network.number_of_edges() >= 4:
            self.topo_ready = True
            self.logger.info(
                "Topology ready: %d switches, %d links",
                self.network.number_of_nodes(),
                self.network.number_of_edges(),
            )

    @set_ev_cls(topo_event.EventLinkDelete)
    def _link_del_handler(self, ev):
        src = ev.link.src
        dst = ev.link.dst
        if self.network.has_edge(src.dpid, dst.dpid):
            self.network.remove_edge(src.dpid, dst.dpid)
        self.logger.info("Topology: link s%d → s%d removed", src.dpid, dst.dpid)
