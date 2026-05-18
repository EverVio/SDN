import os
import numpy as np
import joblib


class DynamicWeightEngine:
    """Computes dynamic edge weights for routing decisions.

    Weight formula:
        w = alpha * hop_cost + beta * current_util + gamma * predicted_util

    Where:
        hop_cost = 1.0 (constant per hop)
        current_util = current link utilization [0, 1]
        predicted_util = ML-predicted next-period utilization [0, 1]
    """

    ALPHA = 1.0   # base hop cost coefficient
    BETA = 2.0    # current utilization coefficient
    GAMMA = 3.0   # predicted utilization coefficient (highest priority)
    WINDOW_SIZE = 3  # sliding window for ML features

    def __init__(self, model_dir=None):
        self.link_models = {}       # (dpid, port_no) -> sklearn model
        self.feature_queues = {}    # (dpid, port_no) -> deque of util values
        self.current_utils = {}     # (dpid, port_no) -> current utilization
        self.predicted_utils = {}   # (dpid, port_no) -> predicted utilization
        self.monitored_links = set()
        self.models_loaded = False

        if model_dir:
            self.load_models(model_dir)

    def load_models(self, model_dir):
        """Load per-link ML models from directory."""
        import glob
        model_files = glob.glob(os.path.join(model_dir, "model_link_*.pkl"))
        for mf in model_files:
            basename = os.path.basename(mf)
            parts = basename.replace("model_link_", "").replace(".pkl", "").split("_")
            if len(parts) == 2:
                key = (int(parts[0]), int(parts[1]))
                self.link_models[key] = joblib.load(mf)
        self.models_loaded = len(self.link_models) > 0

    def register_link(self, dpid, port_no):
        """Register a link for monitoring."""
        key = (dpid, port_no)
        self.monitored_links.add(key)
        if key not in self.feature_queues:
            from collections import deque
            self.feature_queues[key] = deque(maxlen=self.WINDOW_SIZE)

    def update_utilization(self, dpid, port_no, utilization):
        """Update current utilization for a link and feed the feature queue."""
        key = (dpid, port_no)
        self.current_utils[key] = utilization
        if key in self.feature_queues:
            self.feature_queues[key].append(utilization)

    def predict_all(self):
        """Run ML prediction for all monitored links with enough data."""
        self.predicted_utils.clear()
        for key in self.monitored_links:
            queue = self.feature_queues.get(key)
            if queue and len(queue) >= self.WINDOW_SIZE:
                features = list(queue)
                if key in self.link_models:
                    X = np.array(features).reshape(1, -1)
                    pred = float(self.link_models[key].predict(X)[0])
                    self.predicted_utils[key] = max(0.0, min(1.0, pred))

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
            "monitored_links": len(self.monitored_links),
            "models_loaded": len(self.link_models),
            "links_with_prediction": len(self.predicted_utils),
            "avg_current_util": (
                np.mean(list(self.current_utils.values()))
                if self.current_utils else 0.0
            ),
            "avg_predicted_util": (
                np.mean(list(self.predicted_utils.values()))
                if self.predicted_utils else 0.0
            ),
        }
