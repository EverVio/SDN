import os
import numpy as np
import joblib


class DynamicWeightEngine:
    """Computes dynamic edge weights using a single Global MLP model.

    Weight formula:
        w = alpha * hop_cost + beta * current_util + gamma * predicted_util

    The global model takes a sliding window of all backbone link utilizations
    as input and predicts the next-timestep utilization for all links at once.
    This eliminates the per-link model I/O bottleneck of the old approach.
    """

    ALPHA = 1.0  # base hop cost coefficient
    BETA = 2.0  # current utilization coefficient
    GAMMA = 3.0  # predicted utilization coefficient (highest priority)

    def __init__(self, model_path=None):
        self.global_model = None
        self.scaler_X = None
        self.scaler_Y = None
        self.link_keys = []
        self.window_size = 6

        self.feature_history = []
        self.current_utils = {}
        self.predicted_utils = {}
        self.models_loaded = False
        self._last_group_ratios = {}  # dpid -> [ratio_per_port, ...]，用于权重变化死区

        if model_path and os.path.exists(model_path):
            self._load_global_model(model_path)

    def _load_global_model(self, model_path):
        """Load Global MLP model bundle (model + scalers + link_keys)."""
        data = joblib.load(model_path)
        self.global_model = data["model"]
        self.scaler_X = data["scaler_X"]
        self.scaler_Y = data["scaler_Y"]
        self.link_keys = data["link_keys"]
        self.window_size = data.get("window_size", 3)
        self.models_loaded = True

        # Cold-start: fill sliding window with zeros
        num_links = len(self.link_keys)
        self.feature_history = [[0.0] * num_links for _ in range(self.window_size)]

    def update_all_utilizations(self, link_util_dict):
        """Update the sliding window with a full snapshot of all link utilizations."""
        self.current_utils = link_util_dict

        # 修复措施：若未加载全局模型，则无需维护时序历史窗口，直接返回避免 pop(0) 触发 IndexError
        if not self.global_model:
            return

        # Build feature vector in the exact order of link_keys
        current_vector = []
        for key in self.link_keys:
            current_vector.append(link_util_dict.get(key, 0.0))

        self.feature_history.pop(0)
        self.feature_history.append(current_vector)

    def predict_all(self):
        """Run O(1) global matrix inference — single forward pass for all links."""
        if not self.global_model:
            return

        # Flatten sliding window into (1, num_links * window_size)
        X = np.array(self.feature_history, dtype=np.float32).flatten().reshape(1, -1)

        # Scale -> predict -> inverse scale -> clip
        X_scaled = self.scaler_X.transform(X)
        pred_scaled = self.global_model.predict(X_scaled)
        pred = self.scaler_Y.inverse_transform(pred_scaled.reshape(1, -1))[0]
        pred = np.clip(pred, 0.0, 1.0)

        for i, key in enumerate(self.link_keys):
            self.predicted_utils[key] = float(pred[i])

    def compute_weight(self, src_dpid, src_port, dst_dpid, dst_port):
        """Compute the dynamic weight for a directed edge."""
        key = (src_dpid, src_port)
        current = self.current_utils.get(key, 0.0)
        predicted = self.predicted_utils.get(key, current)

        return self.ALPHA * 1.0 + self.BETA * current + self.GAMMA * predicted

    def get_group_weights(self, topo_manager):
        WEIGHT_DEADBAND = 0.10
        # 汇聚交换机 (9-16)
        SWITCH_MIN, SWITCH_MAX = 1, 16
        result = {}

        for dpid in range(SWITCH_MIN, SWITCH_MAX + 1):
            uplink_ports = [3, 4]
            available_list = []
            for port_no in uplink_ports:
                key = (dpid, port_no)
                util = self.predicted_utils.get(key)
                if util is None:
                    util = self.current_utils.get(key, 0.0)

                available_list.append(np.exp(-3.0 * util))

            total = sum(available_list)
            if total > 0:
                ratios = [a / total for a in available_list]
            else:
                ratios = [1.0 / len(uplink_ports)] * len(uplink_ports)

            # 死区检查：防止边缘/汇聚层流表频繁下发导致全网路由震荡
            last_ratios = self._last_group_ratios.get(dpid)
            if last_ratios is not None and len(last_ratios) == len(ratios):
                max_delta = max(abs(r - lr) for r, lr in zip(ratios, last_ratios))
                if max_delta < WEIGHT_DEADBAND:
                    continue

            # 计算整数权重值
            weights = []
            for i, port_no in enumerate(uplink_ports):
                if total > 0:
                    w = max(1, int(available_list[i] / total * 100))
                else:
                    w = 50
                weights.append((port_no, w))

            result[dpid] = weights
            self._last_group_ratios[dpid] = ratios

        return result
