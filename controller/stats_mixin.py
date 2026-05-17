import time
import os
import csv

from ryu.lib import hub


class StatsMixin:
    """端口统计采集模块，可被任何 RyuApp 多重继承"""

    # 轮询参数
    LINK_BW = 10_000_000  # 10 Mbps
    POLL_NORMAL = 3  # 正常轮询间隔（秒）
    POLL_IDLE = 5  # 空闲时轮询间隔
    POLL_WARNING = 1  # 高负载时轮询间隔
    IDLE_THRESHOLD = 0.30
    WARNING_THRESHOLD = 0.50

    def init_stats(self):
        """初始化统计相关数据结构，并由子类在 __init__ 中调用"""
        self.datapaths = {}  # dpid → datapath 对象
        self.prev_port_stats = {}  # (dpid, port_no) → 上次tx_bytes
        self.prev_time = {}  # dpid → 上次收到该交换机统计回复的时间戳（per-datapath）
        self.link_utilization = {}  # (dpid, port_no) → 当前利用率
        self.curr_poll_interval = self.POLL_NORMAL

        os.makedirs("data", exist_ok=True)

        self.csv_file = open("data/traffic_data.csv", "w", newline="")
        self.csv_writer = csv.writer(self.csv_file)
        self.csv_writer.writerow(
            ["timestamp", "dpid", "port_no", "utilization", "link_label"]
        )
        self.csv_file.flush()

        self.monitor_thread = hub.spawn(self._monitor)

    def _monitor(self):
        """后台轮询循环：自适应轮询 → 发请求 → 睡眠 → 重复"""
        while True:
            # 自适应轮询：根据当前最大链路利用率调整轮询间隔
            if self.link_utilization:
                u_max = max(self.link_utilization.values())
                if u_max < self.IDLE_THRESHOLD:
                    self.curr_poll_interval = self.POLL_IDLE
                elif u_max > self.WARNING_THRESHOLD:
                    self.curr_poll_interval = self.POLL_WARNING
            self._request_port_stats()
            hub.sleep(self.curr_poll_interval)

    def _request_port_stats(self):
        """向所有已知交换机发送端口统计请求"""
        for dp in self.datapaths.values():
            parser = dp.ofproto_parser
            req = parser.OFPPortStatsRequest(dp, 0, dp.ofproto.OFPP_ANY)
            dp.send_msg(req)

    def handle_port_stats_reply(self, ev):
        """处理交换机的统计回复（需在控制器中绑定事件）"""
        msg = ev.msg
        dpid = msg.datapath.id
        now = time.time()

        poll_int = self.curr_poll_interval
        bucket_ts = (int(now) // poll_int) * poll_int

        # 每个 datapath 首次收到统计回复时，只记录基线，不计算利用率。
        # 避免将交换机启动以来的累积字节误算为瞬时速率。
        if dpid not in self.prev_time:
            for stat in msg.body:
                port_no = stat.port_no
                if port_no >= 0xFFFFFF00:
                    continue
                self.prev_port_stats[(dpid, port_no)] = stat.tx_bytes
            self.prev_time[dpid] = now
            return

        prev_ts = self.prev_time[dpid]
        delta_time = now - prev_ts
        if delta_time <= 0:
            return

        for stat in msg.body:
            port_no = stat.port_no
            if port_no >= 0xFFFFFF00:
                continue

            key = (dpid, port_no)
            tx_bytes = stat.tx_bytes

            if key in self.prev_port_stats:
                delta_bytes = tx_bytes - self.prev_port_stats[key]

                if delta_bytes > 0:
                    utilization = (delta_bytes * 8) / (delta_time * self.LINK_BW)
                    utilization = min(utilization, 1.0)
                    self.link_utilization[key] = utilization

                    link_label = self._get_link_label(dpid, port_no)
                    self.csv_writer.writerow(
                        [bucket_ts, dpid, port_no, f"{utilization:.6f}", link_label]
                    )
                    self.csv_file.flush()

            self.prev_port_stats[key] = tx_bytes

        self.prev_time[dpid] = now

    def _get_link_label(self, dpid, port_no):
        """根据交换机号和端口号返回链路标签（需与实际拓扑端口一致）"""
        # 路径 A：s1 port3 → s2 port2 → s4 port3
        # 路径 B：s1 port4 → s3 port2 → s4 port4
        if dpid == 1 and port_no == 3:
            return "path_A"
        if dpid == 2 and port_no == 2:
            return "path_A"
        if dpid == 4 and port_no == 3:
            return "path_A"
        if dpid == 1 and port_no == 4:
            return "path_B"
        if dpid == 3 and port_no == 2:
            return "path_B"
        if dpid == 4 and port_no == 4:
            return "path_B"
        # 其他端口（接入端口等）用通用标签
        return f"s{dpid}_p{port_no}"
