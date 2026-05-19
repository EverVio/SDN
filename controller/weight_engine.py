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

    ALPHA = 1.0   # base hop cost coefficient
    BETA = 2.0    # current utilization coefficient
    GAMMA = 3.0   # predicted utilization coefficient (highest priority)

    def __init__(self, model_path=None):
        self.global_model = None
        self.scaler_X = None
        self.scaler_Y = None
        self.link_keys = []
        self.window_size = 3

        self.feature_history = []
        self.current_utils = {}
        self.predicted_utils = {}
        self.models_loaded = False

        if model_path and os.path.exists(model_path):
            self._load_global_model(model_path)

    def _load_global_model(self, model_path):
        """Load Global MLP model bundle (model + scalers + link_keys)."""
        data = joblib.load(model_path)
        self.global_model = data['model']
        self.scaler_X = data['scaler_X']
        self.scaler_Y = data['scaler_Y']
        self.link_keys = data['link_keys']
        self.window_size = data.get('window_size', 3)
        self.models_loaded = True

        # Cold-start: fill sliding window with zeros
        num_links = len(self.link_keys)
        self.feature_history = [[0.0] * num_links for _ in range(self.window_size)]

    def register_link(self, dpid, port_no):
        """No-op in global model mode — link set is fixed at model load time."""
        pass

    def update_utilization(self, dpid, port_no, utilization):
        """No-op in global model mode — use update_all_utilizations() instead."""
        pass

    def update_all_utilizations(self, link_util_dict):
        """Update the sliding window with a full snapshot of all link utilizations.

        Called once per polling cycle with the complete link_utilization dict
        from StatsMixin. Maintains a FIFO queue of WINDOW_SIZE snapshots.
        """
        self.current_utils = link_util_dict

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

        return (
            self.ALPHA * 1.0
            + self.BETA * current
            + self.GAMMA * predicted
        )

    def apply_weights_to_topology(self, topo_manager):
        """Update all edge weights in the TopologyManager graph."""
        for (src, dst), port in topo_manager.link_ports.items():
            weight = self.compute_weight(src, port, dst, None)
            topo_manager.set_edge_weight(src, dst, weight)

    def get_state_summary(self):
        """Return a dict summarizing current engine state."""
        return {
            "models_loaded": 1 if self.global_model else 0,
            "links_monitored": len(self.link_keys),
            "avg_predicted_util": (
                np.mean(list(self.predicted_utils.values()))
                if self.predicted_utils else 0.0
            ),
        }
