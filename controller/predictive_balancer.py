import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ryu.lib import hub
from controller.base_balancer import BaseBalancer
from controller.weight_engine import DynamicWeightEngine


class PredictiveBalancer(BaseBalancer):
    def __init__(self, *args, **kwargs):
        super(PredictiveBalancer, self).__init__(*args, **kwargs)
        self.init_stats()
        model_path = os.path.join(
            os.path.dirname(__file__), "..", "models", "global_mlp_model.pkl"
        )
        self.weight_engine = DynamicWeightEngine(model_path=model_path)
        self.decision_thread = hub.spawn(self._decision_loop)

    def _decision_loop(self):
        while True:
            hub.sleep(self.POLL_INTERVAL)
            if not self.datapaths:
                continue

            # 1. 喂入当前全网链路利用率快照并执行全局矩阵预测
            self.weight_engine.update_all_utilizations(self.link_utilization)
            self.weight_engine.predict_all()

            # 2. 获取动态计算出的汇聚层交换机组表整数权重（内部已包含 5% 变化死区控制）
            group_weights = self.weight_engine.get_group_weights()

            # 3. 遍历所有发生权重显著变化的汇聚交换机，在线修正组表 Bucket 比例
            for dpid, weights in group_weights.items():
                if dpid in self.datapaths:
                    self._modify_group_weights(
                        self.datapaths[dpid], group_id=1, weights=weights
                    )
