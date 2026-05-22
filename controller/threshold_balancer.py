import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ryu.lib import hub
from controller.base_balancer import BaseBalancer
from controller.weight_engine import DynamicWeightEngine


class ThresholdBalancer(BaseBalancer):
    def __init__(self, *args, **kwargs):
        super(ThresholdBalancer, self).__init__(*args, **kwargs)
        self.init_stats()
        self.weight_engine = DynamicWeightEngine(model_path=None)
        # 记录前一状态是否处于拥塞模式
        self._was_congested = False
        self.decision_thread = hub.spawn(self._decision_loop)

    def _decision_loop(self):
        CONGESTION_THRESHOLD = 0.70
        # 引入恢复低水位，防止路由在 70% 边缘频繁横跳（路由震荡）
        RECOVERY_THRESHOLD = 0.30

        while True:
            hub.sleep(self.POLL_INTERVAL)
            if not self.datapaths:
                continue

            self.weight_engine.update_all_utilizations(self.link_utilization)

            max_util = max(self.link_utilization.values(), default=0.0)
            group_weights = {}

            if max_util > CONGESTION_THRESHOLD:
                # 达到高水位，触发拥塞规避
                group_weights = self.weight_engine.get_group_weights()
                self._was_congested = True
            else:
                # 仅在利用率回落到低水位且前置状态为拥塞时，安全恢复对称路由
                if self._was_congested and max_util < RECOVERY_THRESHOLD:
                    group_weights = {dpid: [(3, 50), (4, 50)] for dpid in range(9, 17)}
                    self._was_congested = False

            for dpid, weights in group_weights.items():
                if dpid in self.datapaths:
                    self._modify_group_weights(
                        self.datapaths[dpid], group_id=1, weights=weights
                    )
