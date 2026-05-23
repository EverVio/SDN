import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from controller.base_balancer import BaseBalancer
from controller.weight_engine import DynamicWeightEngine


class PredictiveBalancer(BaseBalancer):
    def __init__(self, *args, **kwargs):
        super(PredictiveBalancer, self).__init__(*args, **kwargs)
        model_path = os.path.join(
            os.path.dirname(__file__), "..", "models", "global_mlp_model.pkl"
        )
        self.weight_engine = DynamicWeightEngine(model_path=model_path)
        # 确保基础引擎加载完模型后再初始化遥测监控主循环
        self.init_stats()

    def on_telemetry_tick(self):
        """主遥测循环串行驱动的回调函数，彻底解决了多协程并发读取时的采样断层和重复采样污染"""
        # 1. 喂入当前全网链路利用率快照并执行全局矩阵预测
        self.weight_engine.update_all_utilizations(self.link_utilization)
        self.weight_engine.predict_all()

        # 2. 获取动态计算出的汇聚层交换机组表整数权重（内部包含死区控制）
        group_weights = self.weight_engine.get_group_weights()

        # 3. 遍历所有发生权重显著变化的汇聚交换机，在线修正组表 Bucket 比例
        for dpid, weights in group_weights.items():
            if dpid in self.datapaths:
                self._modify_group_weights(
                    self.datapaths[dpid], group_id=1, weights=weights
                )
