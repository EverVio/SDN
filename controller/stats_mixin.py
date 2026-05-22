import time
import os
import csv
from ryu.lib import hub


class StatsMixin:
    POLL_INTERVAL = 0.5

    def init_stats(self):
        self.datapaths = {}
        self.prev_port_stats = {}
        self.prev_time = {}
        self.link_utilization = {}

        os.makedirs("data", exist_ok=True)
        self.csv_file = open("data/traffic_data.csv", "w", newline="")
        self.csv_writer = csv.writer(self.csv_file)
        self.csv_writer.writerow(
            ["timestamp", "dpid", "port_no", "utilization", "link_label"]
        )
        self.csv_file.flush()
        self.monitor_thread = hub.spawn(self._monitor)

    def _monitor(self):
        while True:
            for dp in list(self.datapaths.values()):
                dp.send_msg(
                    dp.ofproto_parser.OFPPortStatsRequest(dp, 0, dp.ofproto.OFPP_ANY)
                )
            hub.sleep(self.POLL_INTERVAL)

    def _get_port_bandwidth(self, dpid, port_no):
        """根据 Fat-Tree k=4 拓扑架构动态返回端口的物理带宽(bps)"""
        # 边缘交换机上行端口(3,4)和汇聚交换机下行端口(1,2)为 10Mbps
        if dpid <= 8:
            return 10_000_000
        elif dpid <= 16:
            if port_no in [1, 2]:
                return 10_000_000
            return 2_000_000  # 汇聚上行去往核心为 2Mbps
        return 2_000_000  # 核心交换机端口为 2Mbps

    def handle_port_stats_reply(self, ev):
        msg = ev.msg
        dpid = msg.datapath.id
        now = time.time()
        bucket_ts = (int(now) // self.POLL_INTERVAL) * self.POLL_INTERVAL

        if dpid not in self.prev_time:
            for stat in msg.body:
                if stat.port_no < 0xFFFFFF00:
                    self.prev_port_stats[(dpid, stat.port_no)] = stat.tx_bytes
            self.prev_time[dpid] = now
            return

        delta_time = now - self.prev_time[dpid]
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
                if delta_bytes >= 0:
                    # 动态获取对应的物理通道带宽
                    link_bw = self._get_port_bandwidth(dpid, port_no)
                    util = (delta_bytes * 8) / (delta_time * link_bw)
                    self.link_utilization[key] = min(util, 1.0)
                    self.csv_writer.writerow(
                        [
                            bucket_ts,
                            dpid,
                            port_no,
                            f"{self.link_utilization[key]:.6f}",
                            f"s{dpid}_p{port_no}",
                        ]
                    )
            self.prev_port_stats[key] = tx_bytes
        self.csv_file.flush()
        self.prev_time[dpid] = now
