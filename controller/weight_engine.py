import os
import numpy as np
import joblib


class DynamicWeightEngine:
    ALPHA = 1.0
    BETA = 2.0
    GAMMA = 3.0

    def __init__(self, model_path=None):
        self.global_model = None
        self.scaler_X = None
        self.scaler_Y = None
        self.link_keys = []
        self.window_size = 6

        # 预提取的 NumPy 矩阵与参数
        self.mlp_weights = []
        self.mlp_biases = []
        self.scaler_mean_X = None
        self.scaler_scale_X = None
        self.scaler_mean_Y = None
        self.scaler_scale_Y = None

        self.feature_history = []
        self.current_utils = {}
        self.predicted_utils = {}
        self.models_loaded = False
        self._last_group_ratios = {}

        if model_path and os.path.exists(model_path):
            self._load_global_model(model_path)

    def _load_global_model(self, model_path):
        data = joblib.load(model_path)
        self.global_model = data["model"]
        self.scaler_X = data["scaler_X"]
        self.scaler_Y = data["scaler_Y"]
        self.link_keys = data["link_keys"]
        self.window_size = data.get("window_size", 3)
        self.models_loaded = True

        # 提取 Scaler 参数以实现原生归一化
        self.scaler_mean_X = self.scaler_X.mean_
        self.scaler_scale_X = self.scaler_X.scale_
        self.scaler_mean_Y = self.scaler_Y.mean_
        self.scaler_scale_Y = self.scaler_Y.scale_

        # 提取 MLP 权重和偏置系数
        self.mlp_weights = self.global_model.coefs_
        self.mlp_biases = self.global_model.intercepts_

        num_links = len(self.link_keys)
        self.feature_history = [[0.0] * num_links for _ in range(self.window_size)]

    def update_all_utilizations(self, link_util_dict):
        self.current_utils = link_util_dict

        if not self.models_loaded:
            return

        current_vector = []
        for key in self.link_keys:
            current_vector.append(link_util_dict.get(key, 0.0))

        self.feature_history.pop(0)
        self.feature_history.append(current_vector)

    def predict_all(self):
        if not self.models_loaded:
            return

        # 1. 扁平化特征输入
        X = np.array(self.feature_history, dtype=np.float32).ravel()

        # 2. 原生 Standard Scaler 变换: (X - mean) / scale
        X_scaled = (X - self.scaler_mean_X) / self.scaler_scale_X

        # 3. 原生 NumPy 矩阵乘法前向传播 (支持 ReLU 激活函数)
        activation = X_scaled
        num_layers = len(self.mlp_weights)
        for i in range(num_layers - 1):
            # 隐藏层：Dot(W, X) + b -> ReLU
            z = np.dot(activation, self.mlp_weights[i]) + self.mlp_biases[i]
            activation = np.maximum(z, 0.0)

        # 输出层：线性变换，不经过 ReLU
        pred_scaled = np.dot(activation, self.mlp_weights[-1]) + self.mlp_biases[-1]

        # 4. 原生 逆标准化变换: (pred * scale) + mean
        pred = pred_scaled * self.scaler_scale_Y + self.scaler_mean_Y
        pred = np.clip(pred, 0.0, 1.0)

        for i, key in enumerate(self.link_keys):
            self.predicted_utils[key] = float(pred[i])

    def compute_weight(self, src_dpid, src_port, dst_dpid, dst_port):
        key = (src_dpid, src_port)
        current = self.current_utils.get(key, 0.0)
        predicted = self.predicted_utils.get(key, current)

        return self.ALPHA * 1.0 + self.BETA * current + self.GAMMA * predicted

    def get_group_weights(self, topo_manager):
        WEIGHT_DEADBAND = 0.05
        SWITCH_MIN, SWITCH_MAX = 1, 16
        result = {}

        WEIGHT_CURRENT = 0.4
        WEIGHT_PREDICTED = 0.6

        for dpid in range(SWITCH_MIN, SWITCH_MAX + 1):
            uplink_ports = [3, 4]
            available_list = []
            for port_no in uplink_ports:
                key = (dpid, port_no)
                curr_util = self.current_utils.get(key, 0.0)
                pred_util = self.predicted_utils.get(key, curr_util)

                if self.models_loaded:
                    effective_util = (
                        WEIGHT_CURRENT * curr_util + WEIGHT_PREDICTED * pred_util
                    )
                else:
                    effective_util = curr_util

                available_list.append(np.exp(-3.0 * effective_util))

            total = sum(available_list)
            if total > 0:
                ratios = [a / total for a in available_list]
            else:
                ratios = [1.0 / len(uplink_ports)] * len(uplink_ports)

            last_ratios = self._last_group_ratios.get(dpid)
            if last_ratios is not None and len(last_ratios) == len(ratios):
                max_delta = max(abs(r - lr) for r, lr in zip(ratios, last_ratios))
                if max_delta < WEIGHT_DEADBAND:
                    continue

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
