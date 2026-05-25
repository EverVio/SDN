import os
import sys
import csv
import time
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from controller.base_balancer import BaseBalancer
from controller.weight_engine import DynamicWeightEngine

_weights_lock = threading.Lock()


class ThresholdBalancer(BaseBalancer):
    def __init__(self, *args, **kwargs):
        super(ThresholdBalancer, self).__init__(*args, **kwargs)
        self.weight_engine = DynamicWeightEngine(model_path=None)
        self._was_congested = False
        self._init_weights_csv()
        self.init_stats()

    def _init_weights_csv(self):
        os.makedirs("data", exist_ok=True)
        self._weights_file = open("data/group_weights.csv", "w", newline="")
        self._weights_writer = csv.writer(self._weights_file)
        self._weights_writer.writerow(["timestamp", "dpid", "port3_weight", "port4_weight"])
        self._weights_file.flush()

    def _write_weights(self, dpid, weights):
        w3 = dict(weights).get(3, 50)
        w4 = dict(weights).get(4, 50)
        with _weights_lock:
            self._weights_writer.writerow([time.time(), dpid, w3, w4])
            self._weights_file.flush()

    def on_telemetry_tick(self):
        """主遥测循环串行驱动的回调函数，实现精确无滞后的阈值响应"""
        CONGESTION_THRESHOLD = 0.70
        RECOVERY_THRESHOLD = 0.30

        self.weight_engine.update_all_utilizations(self.link_utilization)

        max_util = max(self.link_utilization.values(), default=0.0)
        group_weights = {}

        if max_util > CONGESTION_THRESHOLD:
            group_weights = self.weight_engine.get_group_weights()
            self._was_congested = True
        else:
            if self._was_congested and max_util < RECOVERY_THRESHOLD:
                group_weights = {dpid: [(3, 50), (4, 50)] for dpid in range(9, 17)}
                self._was_congested = False

        for dpid, weights in group_weights.items():
            if dpid in self.datapaths:
                self._modify_group_weights(
                    self.datapaths[dpid], group_id=1, weights=weights
                )
                self._write_weights(dpid, weights)
