import csv
import os
import time
from ryu.lib import hub


class StatsMixin:
    POLL_INTERVAL = 0.5

    def init_stats(self):
        self.datapaths = {}
        self.prev_port_stats = {}
        self.prev_time = {}
        self.link_utilization = {}
        self.current_snapshot_ts = 0.0
        self.xid_to_ts = {}

        os.makedirs("data", exist_ok=True)
        self.csv_file = open("data/traffic_data.csv", "w", newline="")
        self.csv_writer = csv.writer(self.csv_file)
        self.csv_writer.writerow(["timestamp", "dpid", "port_no", "utilization"])
        self.csv_file.flush()
        self.monitor_thread = hub.spawn(self._monitor)

    def _monitor(self):
        while True:
            hub.sleep(self.POLL_INTERVAL)
            if not self.datapaths:
                continue

            self.current_snapshot_ts = (
                time.time() // self.POLL_INTERVAL
            ) * self.POLL_INTERVAL

            if hasattr(self, "on_telemetry_tick"):
                self.on_telemetry_tick()

            for dp in list(self.datapaths.values()):
                req = dp.ofproto_parser.OFPPortStatsRequest(dp, 0, dp.ofproto.OFPP_ANY)
                self.xid_to_ts[req.xid] = self.current_snapshot_ts
                dp.send_msg(req)

    def _get_port_bandwidth(self, dpid, port_no):
        if dpid <= 8:
            return 10_000_000
        elif dpid <= 16:
            if port_no in [1, 2]:
                return 10_000_000
            return 2_000_000
        return 2_000_000

    def handle_port_stats_reply(self, ev):
        msg = ev.msg
        dpid = msg.datapath.id
        now = time.time()

        # 精确反解析发出遥测请求时的时钟快照时间
        bucket_ts = self.xid_to_ts.get(msg.xid, self.current_snapshot_ts)

        if msg.xid in self.xid_to_ts:
            del self.xid_to_ts[msg.xid]

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
                    link_bw = self._get_port_bandwidth(dpid, port_no)
                    util = (delta_bytes * 8) / (delta_time * link_bw)
                    self.link_utilization[key] = min(util, 1.0)
                    self.csv_writer.writerow(
                        [
                            bucket_ts,
                            dpid,
                            port_no,
                            f"{self.link_utilization[key]:.6f}",
                        ]
                    )
            self.prev_port_stats[key] = tx_bytes
        self.csv_file.flush()
        self.prev_time[dpid] = now
