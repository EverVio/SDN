# Fat-Tree K=4 + K-Shortest Path Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the 4-node diamond topology with a Fat-Tree (k=4) datacenter topology, and replace the Suurballe dual-path routing with Yen's K-Shortest Path algorithm using dynamic ML-weighted link costs.

**Architecture:** Fat-Tree k=4 provides 16 hosts, 8 edge switches, 8 aggregation switches, and 4 core switches with multipath connectivity. The routing layer uses Yen's algorithm to compute K shortest paths per flow, with edge weights derived from a `DynamicWeightEngine` that combines base hop cost, current utilization, and ML-predicted future utilization. Per-link Random Forest models replace the current per-path models.

**Tech Stack:** Python 3, Mininet (TCLink, OVSSwitch), Ryu (OpenFlow 1.3), NetworkX, scikit-learn (RandomForestRegressor), numpy, joblib, pandas

---

## File Map

| Action | File | Responsibility |
|--------|------|----------------|
| Create | `topo/fat_tree_topo.py` | Fat-Tree k=4 Mininet topology generator |
| Modify | `controller/topology_manager.py` | Add weighted graph, Yen's K-shortest paths |
| Create | `controller/weight_engine.py` | Dynamic link weight computation (current + predicted) |
| Modify | `controller/predictive_balancer.py` | K-path selection, per-link prediction integration |
| Modify | `controller/threshold_balancer.py` | Adapt to Fat-Tree, use TopologyManager for paths |
| Modify | `controller/stats_mixin.py` | Update link labeling for Fat-Tree |
| Modify | `scripts/collect_training_data.py` | Use Fat-Tree topology, multi-host traffic |
| Modify | `scripts/assemble_features.py` | Per-link feature assembly |
| Modify | `scripts/train_model.py` | Per-link model training |
| Modify | `scripts/traffic_gen.py` | Multi-pod traffic patterns |
| Modify | `controller/test_topology_manager.py` | Fat-Tree test topology, K-path tests |

---

## Task 1: Fat-Tree k=4 Topology Generator

**Files:**
- Create: `topo/fat_tree_topo.py`
- Modify: `topo/dual_path_topo.py` (keep for backward compatibility)

### Fat-Tree k=4 Structure

```
Pod 0:              Pod 1:              Pod 2:              Pod 3:
  e0    e1            e2    e3            e4    e5            e6    e7
 / \  / \           / \  / \           / \  / \           / \  / \
h0_0 h0_1 h1_0 h1_1 h2_0 h2_1 h3_0 h3_1 h4_0 h4_1 h5_0 h5_1 h6_0 h6_1 h7_0 h7_1
  |    |    |    |    |    |    |    |    |    |    |    |    |    |    |    |
  a0    a1            a2    a3            a4    a5            a6    a7
   \  /               \  /               \  /               \  /
    \/                 \/                 \/                 \/
   c0        c1        c2        c3       (core layer)
```

Switch DPID mapping: edge=1..8, aggregation=9..16, core=17..20

Port assignment per switch type:
- Edge switch: ports 1,2 = hosts; ports 3,4 = uplinks to aggregation
- Aggregation switch: ports 1,2 = downlinks to edge; ports 3,4 = uplinks to core
- Core switch: ports 1..4 = downlinks to aggregation (one per pod)

- [ ] **Step 1: Create `topo/fat_tree_topo.py`**

```python
import os
from mininet.net import Mininet
from mininet.node import RemoteController, OVSSwitch
from mininet.link import TCLink

K = 4  # Fat-Tree parameter
PODS = K
EDGE_PER_POD = K // 2   # 2
AGG_PER_POD = K // 2     # 2
HOST_PER_EDGE = K // 2   # 2

# Link bandwidths (Mbps)
BW_ACCESS = 0    # unlimited
BW_EDGE_AGG = 10
BW_AGG_CORE = 10


def _edge_dpid(pod, idx):
    """Edge switch DPID: 1..8"""
    return pod * EDGE_PER_POD + idx + 1


def _agg_dpid(pod, idx):
    """Aggregation switch DPID: 9..16"""
    return PODS * EDGE_PER_POD + pod * AGG_PER_POD + idx + 1


def _core_dpid(idx):
    """Core switch DPID: 17..20"""
    return PODS * EDGE_PER_POD + PODS * AGG_PER_POD + idx + 1


def create_topology(controller_ip="127.0.0.1", controller_port=6633):
    """Create Fat-Tree k=4 topology.

    Returns (net, controller) — caller is responsible for build/start/stop.
    """
    net = Mininet(
        controller=None,
        switch=OVSSwitch,
        link=TCLink,
        autoSetMacs=True,
    )

    c0 = net.addController(
        "c0", controller=RemoteController,
        ip=controller_ip, port=controller_port,
    )

    # Add switches
    for pod in range(PODS):
        for i in range(EDGE_PER_POD):
            dpid = _edge_dpid(pod, i)
            net.addSwitch(f"s{dpid}", dpid=dpid, protocols="OpenFlow13")
        for i in range(AGG_PER_POD):
            dpid = _agg_dpid(pod, i)
            net.addSwitch(f"s{dpid}", dpid=dpid, protocols="OpenFlow13")

    for i in range((K // 2) ** 2):
        dpid = _core_dpid(i)
        net.addSwitch(f"s{dpid}", dpid=dpid, protocols="OpenFlow13")

    # Add hosts and access links
    for pod in range(PODS):
        for e_idx in range(EDGE_PER_POD):
            edge_dpid = _edge_dpid(pod, e_idx)
            for h_idx in range(HOST_PER_EDGE):
                host_name = f"h{pod}_{e_idx * HOST_PER_EDGE + h_idx}"
                host = net.addHost(host_name)
                net.addLink(host, net.get(f"s{edge_dpid}"), bw=BW_ACCESS)

    # Edge <-> Aggregation links (within each pod)
    for pod in range(PODS):
        for e_idx in range(EDGE_PER_POD):
            edge_dpid = _edge_dpid(pod, e_idx)
            for a_idx in range(AGG_PER_POD):
                agg_dpid = _agg_dpid(pod, a_idx)
                net.addLink(
                    net.get(f"s{edge_dpid}"), net.get(f"s{agg_dpid}"),
                    bw=BW_EDGE_AGG,
                )

    # Aggregation <-> Core links
    for pod in range(PODS):
        for a_idx in range(AGG_PER_POD):
            agg_dpid = _agg_dpid(pod, a_idx)
            for c_local in range(K // 2):
                core_idx = a_idx * (K // 2) + c_local
                core_dpid = _core_dpid(core_idx)
                net.addLink(
                    net.get(f"s{agg_dpid}"), net.get(f"s{core_dpid}"),
                    bw=BW_AGG_CORE,
                )

    return net, c0


def cleanup():
    """Remove all Fat-Tree OVS bridges."""
    for pod in range(PODS):
        for i in range(EDGE_PER_POD):
            os.system(f"sudo ovs-vsctl --if-exists del-br s{_edge_dpid(pod, i)} 2>/dev/null")
        for i in range(AGG_PER_POD):
            os.system(f"sudo ovs-vsctl --if-exists del-br s{_agg_dpid(pod, i)} 2>/dev/null")
    for i in range((K // 2) ** 2):
        os.system(f"sudo ovs-vsctl --if-exists del-br s{_core_dpid(i)} 2>/dev/null")
    print("Fat-Tree cleanup completed.")
```

- [ ] **Step 2: Verify the topology loads without import errors**

Run:
```bash
cd /home/yang/SDN && python3 -c "from topo.fat_tree_topo import create_topology, cleanup; print('Import OK')"
```
Expected: `Import OK`

- [ ] **Step 3: Commit**

```bash
git add topo/fat_tree_topo.py
git commit -m "feat: add Fat-Tree k=4 Mininet topology generator"
```

---

## Task 2: TopologyManager — Weighted Graph + Yen's K-Shortest Paths

**Files:**
- Modify: `controller/topology_manager.py`

Replace `compute_edge_disjoint_paths()` with `compute_k_shortest_paths(src, dst, k, weight_fn)` using Yen's algorithm on a weighted graph. Add `set_edge_weight()` / `get_edge_weight()` for dynamic weights.

- [ ] **Step 1: Add weight support to TopologyManager**

Add these methods to the `TopologyManager` class in `controller/topology_manager.py`:

```python
def set_edge_weight(self, src_dpid, dst_dpid, weight):
    """Set weight on a directed edge (both directions)."""
    if self.G.has_edge(src_dpid, dst_dpid):
        self.G[src_dpid][dst_dpid]['weight'] = weight
    if self.G.has_edge(dst_dpid, src_dpid):
        self.G[dst_dpid][src_dpid]['weight'] = weight

def get_edge_weight(self, src_dpid, dst_dpid):
    """Get weight of a directed edge, default 1.0."""
    if self.G.has_edge(src_dpid, dst_dpid):
        return self.G[src_dpid][dst_dpid].get('weight', 1.0)
    return float('inf')
```

- [ ] **Step 2: Add Yen's K-Shortest Paths algorithm**

Add this method to the `TopologyManager` class:

```python
def compute_k_shortest_paths(self, src_dpid, dst_dpid, k=3, weight='weight'):
    """Compute up to K shortest paths using Yen's algorithm.

    Args:
        src_dpid: Source switch DPID
        dst_dpid: Destination switch DPID
        k: Maximum number of paths to return
        weight: Edge attribute to use as weight

    Returns:
        List of (path, cost) tuples sorted by cost ascending.
        Each path is a list of DPIDs. Empty list if no path exists.
    """
    if not (self.G.has_node(src_dpid) and self.G.has_node(dst_dpid)):
        return []

    # First shortest path
    try:
        first_path = nx.shortest_path(self.G, src_dpid, dst_dpid, weight=weight)
        first_cost = self._path_cost(first_path, weight)
    except nx.NetworkXNoPath:
        return []

    A = [(first_path, first_cost)]  # confirmed shortest paths
    B = []  # candidate paths (heap)

    for i in range(1, k):
        prev_path = A[-1][0]

        for j in range(len(prev_path) - 1):
            spur_node = prev_path[j]
            root_path = prev_path[:j + 1]
            root_cost = self._path_cost(root_path, weight)

            # Temporarily remove edges that share the same root path
            removed_edges = []
            for path, _ in A:
                if path[:j + 1] == root_path:
                    u, v = path[j], path[j + 1]
                    if self.G.has_edge(u, v):
                        w = self.G[u][v].get('weight', 1.0)
                        self.G.remove_edge(u, v)
                        removed_edges.append((u, v, w))

            # Remove root path nodes (except spur) to prevent loops
            removed_nodes = []
            for node in root_path[:-1]:
                if node != spur_node:
                    # Save and remove all edges incident to this node
                    for neighbor in list(self.G.neighbors(node)):
                        w = self.G[node][neighbor].get('weight', 1.0)
                        removed_edges.append((node, neighbor, w))
                        self.G.remove_edge(node, neighbor)

            # Find spur path from spur_node to dst
            try:
                spur_path = nx.shortest_path(
                    self.G, spur_node, dst_dpid, weight=weight
                )
                spur_cost = self._path_cost(spur_path, weight)

                # Total path = root + spur (without duplicating spur_node)
                total_path = root_path[:-1] + spur_path
                total_cost = root_cost + spur_cost

                # Add to candidates if not already present
                candidate = (total_path, total_cost)
                if candidate not in B:
                    import heapq
                    heapq.heappush(b, (total_cost, len(b), total_path))
            except nx.NetworkXNoPath:
                pass

            # Restore removed edges
            for u, v, w in removed_edges:
                if not self.G.has_edge(u, v):
                    self.G.add_edge(u, v, weight=w)

        # Pop best candidate
        import heapq
        if not b:
            break
        cost, _, path = heapq.heappop(b)
        A.append((path, cost))

    return A
```

- [ ] **Step 3: Fix the implementation (the B variable needs to be a heap)**

Replace the method above with this corrected version that properly uses a heap:

```python
def compute_k_shortest_paths(self, src_dpid, dst_dpid, k=3, weight='weight'):
    """Compute up to K shortest paths using Yen's algorithm.

    Returns: List of (path, cost) tuples sorted by cost ascending.
    """
    import heapq

    if not (self.G.has_node(src_dpid) and self.G.has_node(dst_dpid)):
        return []

    try:
        first_path = nx.shortest_path(self.G, src_dpid, dst_dpid, weight=weight)
        first_cost = self._path_cost(first_path, weight)
    except nx.NetworkXNoPath:
        return []

    A = [(first_path, first_cost)]
    B = []  # min-heap of (cost, tiebreaker, path)
    seen = {tuple(first_path)}

    for i in range(1, k):
        prev_path = A[-1][0]

        for j in range(len(prev_path) - 1):
            spur_node = prev_path[j]
            root_path = prev_path[:j + 1]
            root_cost = self._path_cost(root_path, weight)

            # Remove edges used by confirmed paths with same root
            removed_edges = []
            for path, _ in A:
                if len(path) > j and path[:j + 1] == root_path:
                    u, v = path[j], path[j + 1]
                    if self.G.has_edge(u, v):
                        w = self.G[u][v].get('weight', 1.0)
                        self.G.remove_edge(u, v)
                        removed_edges.append((u, v, w))

            # Remove root path internal nodes to prevent revisiting
            for node in root_path[:-1]:
                for neighbor in list(self.G.neighbors(node)):
                    w = self.G[node][neighbor].get('weight', 1.0)
                    removed_edges.append((node, neighbor, w))
                    self.G.remove_edge(node, neighbor)

            try:
                spur_path = nx.shortest_path(
                    self.G, spur_node, dst_dpid, weight=weight
                )
                total_path = root_path[:-1] + spur_path
                total_cost = root_cost + self._path_cost(spur_path, weight)

                key = tuple(total_path)
                if key not in seen:
                    seen.add(key)
                    heapq.heappush(B, (total_cost, len(seen), total_path))
            except nx.NetworkXNoPath:
                pass

            # Restore all removed edges
            for u, v, w in removed_edges:
                if not self.G.has_edge(u, v):
                    self.G.add_edge(u, v, weight=w)

        if not B:
            break
        cost, _, path = heapq.heappop(B)
        A.append((path, cost))

    return A

def _path_cost(self, path, weight='weight'):
    """Compute total weight of a path (list of nodes)."""
    cost = 0.0
    for i in range(len(path) - 1):
        edge_data = self.G.get_edge_data(path[i], path[i + 1], default={})
        cost += edge_data.get(weight, 1.0)
    return cost
```

- [ ] **Step 4: Keep backward compatibility**

Rename the old method to keep it available:

```python
# Keep old method as alias for tests / backward compatibility
def compute_edge_disjoint_paths(self, src_dpid, dst_dpid):
    """Legacy: compute two edge-disjoint paths (Suurballe variant)."""
    paths = self.compute_k_shortest_paths(src_dpid, dst_dpid, k=2, weight=None)
    if len(paths) >= 2:
        fwd1, rev1 = self._path_to_ports(paths[0][0])
        fwd2, rev2 = self._path_to_ports(paths[1][0])
        return fwd1, rev1, fwd2, rev2
    elif len(paths) == 1:
        fwd, rev = self._path_to_ports(paths[0][0])
        return fwd, rev, None, None
    return None, None, None, None
```

- [ ] **Step 5: Add path-to-ports conversion for a single path**

```python
def path_to_ports(self, path):
    """Convert a node list to (fwd_ports, rev_ports) dicts.

    fwd_ports: {dpid: out_port} for forward direction
    rev_ports: {dpid: out_port} for reverse direction
    """
    return self._path_to_ports(path)
```

- [ ] **Step 6: Run existing tests to verify backward compatibility**

Run:
```bash
cd /home/yang/SDN && python3 controller/test_topology_manager.py
```
Expected: All 10 tests pass (compute_edge_disjoint_paths still works via the legacy wrapper).

- [ ] **Step 7: Commit**

```bash
git add controller/topology_manager.py
git commit -m "feat: add weighted graph and Yen's K-shortest paths to TopologyManager"
```

---

## Task 3: Dynamic Weight Engine

**Files:**
- Create: `controller/weight_engine.py`

Computes per-link edge weights combining: base hop cost, current utilization, and ML-predicted utilization.

- [ ] **Step 1: Create `controller/weight_engine.py`**

```python
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
            # Filename: model_link_{dpid}_{port}.pkl
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
        """Compute the dynamic weight for a directed edge.

        Args:
            src_dpid, src_port: source switch and output port
            dst_dpid, dst_port: destination switch and input port

        Returns:
            float weight value
        """
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
            # Use the output port of src as the key for utilization
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
```

- [ ] **Step 2: Verify import**

Run:
```bash
cd /home/yang/SDN && python3 -c "from controller.weight_engine import DynamicWeightEngine; e = DynamicWeightEngine(); print('OK, state:', e.get_state_summary())"
```
Expected: `OK, state: {'monitored_links': 0, ...}`

- [ ] **Step 3: Commit**

```bash
git add controller/weight_engine.py
git commit -m "feat: add DynamicWeightEngine for ML-weighted link costs"
```

---

## Task 4: Update StatsMixin for Fat-Tree Link Labeling

**Files:**
- Modify: `controller/stats_mixin.py`

The current `_get_link_label` uses `_path_util_keys` which has only "A" and "B". For Fat-Tree, labels should reflect the link's role in the topology (edge_to_agg, agg_to_core).

- [ ] **Step 1: Update `_get_link_label` method**

Replace the `_get_link_label` method in `StatsMixin`:

```python
def _get_link_label(self, dpid, port_no):
    """Dynamic link labeling for Fat-Tree topology."""
    if self.topo_manager is None:
        return f"s{dpid}_p{port_no}"

    # Edge port (host access)
    if self.topo_manager.is_edge_port(dpid, port_no):
        return f"s{dpid}_p{port_no}_edge"

    # Check active path util keys (K paths)
    if hasattr(self, '_path_util_keys'):
        for path_name, keys in self._path_util_keys.items():
            if (dpid, port_no) in keys:
                return f"path_{path_name}"

    # Classify by switch tier in Fat-Tree
    if dpid <= 8:
        return f"edge_s{dpid}_p{port_no}"
    elif dpid <= 16:
        return f"agg_s{dpid}_p{port_no}"
    else:
        return f"core_s{dpid}_p{port_no}"
```

- [ ] **Step 2: Update `set_path_util_keys` to support arbitrary path names**

The current method already accepts any dict, so no change needed. Verify:

```python
# This already works with {"0": set(), "1": set(), ...} for K paths
def set_path_util_keys(self, path_util_keys):
    self._path_util_keys = path_util_keys
```

- [ ] **Step 3: Commit**

```bash
git add controller/stats_mixin.py
git commit -m "feat: update StatsMixin link labeling for Fat-Tree tiers"
```

---

## Task 5: Update PredictiveBalancer for K-Path Routing

**Files:**
- Modify: `controller/predictive_balancer.py`

Major changes:
1. Import `DynamicWeightEngine`
2. Replace dual-path path cache with K-path cache
3. Replace `DecisionEngine` with `DynamicWeightEngine` + simple best-path selection
4. Update `_compute_and_install_paths` to use K-shortest paths
5. Update `_decision_loop` to recompute weights and select best path

- [ ] **Step 1: Update imports and constants**

At the top of `predictive_balancer.py`, add:

```python
from controller.weight_engine import DynamicWeightEngine
```

Update constants:

```python
K_PATHS = 3  # number of candidate paths to compute
PRIORITY_ACTIVE_PATH = 20
PRIORITY_STANDBY_PATH = 10
```

- [ ] **Step 2: Replace DecisionEngine usage in `__init__`**

Replace the `__init__` method of `PredictiveBalancer`:

```python
def __init__(self, *args, **kwargs):
    super(PredictiveBalancer, self).__init__(*args, **kwargs)
    self.mac_to_port = {}
    self.ip_to_mac = {}
    self.datapaths = {}
    self.path_installed = False

    self.topo = TopologyManager()

    # K-path cache: list of (fwd_ports, rev_ports, path_nodes, cost)
    self.k_paths = []
    self.active_path_idx = 0  # index into k_paths

    # Per-path util keys for StatsMixin labeling
    self.path_util_keys = {}

    self.init_stats(topo_manager=self.topo)

    # Dynamic weight engine (ML predictions)
    model_dir = os.path.join(os.path.dirname(__file__), "..", "models")
    self.weight_engine = DynamicWeightEngine(model_dir=model_dir)

    atexit.register(self._cleanup)
    self.decision_thread = hub.spawn(self._decision_loop)
```

- [ ] **Step 3: Replace `_compute_and_install_paths`**

```python
def _compute_and_install_paths(self):
    """Compute K shortest paths using dynamic weights and install flow rules."""
    hosts = list(self.topo.host_table.keys())
    if len(hosts) < 2:
        return

    mac_a, mac_b = hosts[0], hosts[1]
    loc_a = self.topo.get_host_location(mac_a)
    loc_b = self.topo.get_host_location(mac_b)
    if not loc_a or not loc_b:
        return

    src_dpid = loc_a[0]
    dst_dpid = loc_b[0]

    # Apply current weights to topology graph
    self.weight_engine.apply_weights_to_topology(self.topo)

    # Compute K shortest paths
    paths_with_cost = self.topo.compute_k_shortest_paths(
        src_dpid, dst_dpid, k=K_PATHS, weight='weight'
    )

    if not paths_with_cost:
        self.logger.warning("No path found between s%d and s%d", src_dpid, dst_dpid)
        return

    self.k_paths = []
    self.path_util_keys = {}

    for idx, (path_nodes, cost) in enumerate(paths_with_cost):
        fwd, rev = self.topo.path_to_ports(path_nodes)
        util_keys = self.topo.get_path_util_keys(fwd, rev)
        self.k_paths.append((fwd, rev, path_nodes, cost))
        self.path_util_keys[str(idx)] = util_keys

    # Install all candidate paths with standby priority
    for idx, (fwd, rev, _, _) in enumerate(self.k_paths):
        self._install_full_path_dynamic(str(idx), PRIORITY_STANDBY_PATH, fwd, rev)

    self.active_path_idx = 0
    self.set_path_util_keys(self.path_util_keys)
    self.path_installed = True

    self.logger.info(
        "K=%d paths computed: ingress=s%d, costs=%s",
        len(self.k_paths), src_dpid,
        [f"{c:.2f}" for _, _, _, c in self.k_paths],
    )
```

- [ ] **Step 4: Replace `_get_path_out_port`**

```python
def _get_path_out_port(self, dpid):
    """Get output port for the currently active path."""
    if not self.k_paths:
        return None
    fwd = self.k_paths[self.active_path_idx][0]
    return fwd.get(dpid)
```

- [ ] **Step 5: Replace `_get_path_util`**

```python
def _get_path_util(self, path_idx):
    """Get bottleneck utilization for path at given index."""
    key = str(path_idx)
    keys = self.path_util_keys.get(key, set())
    if not keys:
        return 0
    utils = [self.link_utilization.get(k, 0) for k in keys]
    return max(utils) if utils else 0
```

- [ ] **Step 6: Replace `_decision_loop`**

```python
def _decision_loop(self):
    """Periodically update weights, predict, and select best path."""
    while True:
        hub.sleep(self.curr_poll_interval)
        if not self.datapaths or not self.k_paths:
            continue

        # Feed current utilizations to weight engine
        for (dpid, port_no), util in self.link_utilization.items():
            self.weight_engine.register_link(dpid, port_no)
            self.weight_engine.update_utilization(dpid, port_no, util)

        # Run ML predictions
        self.weight_engine.predict_all()

        # Recompute weights and find best path
        self.weight_engine.apply_weights_to_topology(self.topo)

        best_idx = self.active_path_idx
        best_cost = float('inf')
        for idx, (fwd, rev, path_nodes, _) in enumerate(self.k_paths):
            # Recompute cost with updated weights
            cost = self.topo._path_cost(path_nodes, weight='weight')
            if cost < best_cost:
                best_cost = cost
                best_idx = idx

        # Log state
        summary = self.weight_engine.get_state_summary()
        utils = [self._get_path_util(i) for i in range(len(self.k_paths))]
        self.logger.info(
            "Paths: %s, Active: %d, Best: %d (%.2f), ML: %d links predicted",
            [f"{u*100:.0f}%" for u in utils],
            self.active_path_idx, best_idx, best_cost,
            summary["links_with_prediction"],
        )

        # Switch if a different path is better
        if best_idx != self.active_path_idx:
            self._switch_path(best_idx)
```

- [ ] **Step 7: Replace `_switch_path`**

```python
def _switch_path(self, new_idx):
    """Switch to path at new_idx using make-before-break."""
    old_idx = self.active_path_idx
    self.logger.info(
        ">>> Switching from path %d to path %d", old_idx, new_idx
    )

    if self.path_installed and new_idx < len(self.k_paths):
        fwd, rev, _, _ = self.k_paths[new_idx]
        self._install_full_path_dynamic(
            str(new_idx), PRIORITY_STANDBY_PATH, fwd, rev
        )

    self.active_path_idx = new_idx
    hub.spawn(self._async_cleanup_old_path, old_idx)
```

- [ ] **Step 8: Update `_async_cleanup_old_path`**

```python
def _async_cleanup_old_path(self, old_idx):
    """Remove flow rules for the old path."""
    hub.sleep(0.2)
    if old_idx >= len(self.k_paths):
        return

    fwd, rev, _, _ = self.k_paths[old_idx]
    hosts = list(self.topo.host_table.keys())
    if len(hosts) < 2:
        return

    mac_dst = hosts[1]
    mac_src = hosts[0]

    for dpid, out_port in fwd.items():
        if dpid not in self.datapaths:
            continue
        dp = self.datapaths[dpid]
        parser = dp.ofproto_parser
        ofproto = dp.ofproto
        match = parser.OFPMatch(eth_dst=mac_dst)
        mod = parser.OFPFlowMod(
            datapath=dp, command=ofproto.OFPFC_DELETE,
            out_port=out_port, out_group=ofproto.OFPG_ANY, match=match,
        )
        dp.send_msg(mod)

    for dpid, out_port in rev.items():
        if dpid not in self.datapaths:
            continue
        dp = self.datapaths[dpid]
        parser = dp.ofproto_parser
        ofproto = dp.ofproto
        match = parser.OFPMatch(eth_dst=mac_src)
        mod = parser.OFPFlowMod(
            datapath=dp, command=ofproto.OFPFC_DELETE,
            out_port=out_port, out_group=ofproto.OFPG_ANY, match=match,
        )
        dp.send_msg(mod)

    self.logger.info("  Cleaned up flows for path %d", old_idx)
```

- [ ] **Step 9: Update `_cleanup` to remove DecisionEngine reference**

```python
def _cleanup(self):
    pass  # DynamicWeightEngine has no file handles to close
```

- [ ] **Step 10: Remove the `DecisionEngine` class entirely**

Delete the `DecisionEngine` class (lines 30-135 in the original file) since it's replaced by `DynamicWeightEngine`.

- [ ] **Step 11: Commit**

```bash
git add controller/predictive_balancer.py
git commit -m "feat: replace dual-path with K-path routing using DynamicWeightEngine"
```

---

## Task 6: Update ThresholdBalancer for Fat-Tree

**Files:**
- Modify: `controller/threshold_balancer.py`

The threshold balancer needs to use `TopologyManager` for dynamic path computation in the Fat-Tree, instead of hardcoded `PATH_PORTS`.

- [ ] **Step 1: Replace hardcoded path constants with dynamic computation**

Remove `PATH_PORTS` and `PATH_PORTS_REV` constants. Add import:

```python
from controller.topology_manager import TopologyManager
```

Update `__init__`:

```python
def __init__(self, *args, **kwargs):
    super(ThresholdBalancer, self).__init__(*args, **kwargs)
    self.mac_to_port = {}
    self.host_location = {}
    self.ip_to_mac = {}
    self.datapaths = {}
    self.current_path = "A"
    self.path_installed = False

    self.topo = TopologyManager()

    # Dual-path cache (computed dynamically)
    self.path_fwd = {"A": None, "B": None}
    self.path_rev = {"A": None, "B": None}
    self.path_util_keys = {"A": set(), "B": set()}

    self.init_stats(topo_manager=self.topo)
    self.decision_thread = hub.spawn(self._decision_loop)
```

- [ ] **Step 2: Add topology discovery event handlers**

```python
@set_ev_cls(topo_event.EventSwitchEnter)
def _switch_add_handler(self, ev):
    dpid = ev.switch.dp.id
    self.datapaths[dpid] = ev.switch.dp
    self.topo.add_switch(dpid)
    self.logger.info("Topology: switch s%d added", dpid)

@set_ev_cls(topo_event.EventSwitchLeave)
def _switch_del_handler(self, ev):
    dpid = ev.switch.dp.id
    self.datapaths.pop(dpid, None)
    self.topo.remove_switch(dpid)
    self.logger.info("Topology: switch s%d removed", dpid)

@set_ev_cls(topo_event.EventLinkAdd)
def _link_add_handler(self, ev):
    src = ev.link.src
    dst = ev.link.dst
    self.topo.add_link(src.dpid, src.port_no, dst.dpid, dst.port_no)
    self.logger.info("Topology: link s%d:p%d -> s%d:p%d",
                     src.dpid, src.port_no, dst.dpid, dst.port_no)
    self._invalidate_paths()

@set_ev_cls(topo_event.EventLinkDelete)
def _link_del_handler(self, ev):
    src = ev.link.src
    dst = ev.link.dst
    self.topo.remove_link(src.dpid, dst.dpid)
    self.logger.info("Topology: link s%d -> s%d removed", src.dpid, dst.dpid)
    self._invalidate_paths()

def _invalidate_paths(self):
    self.path_fwd = {"A": None, "B": None}
    self.path_rev = {"A": None, "B": None}
    self.path_util_keys = {"A": set(), "B": set()}
    self.path_installed = False
```

- [ ] **Step 3: Update host learning to use TopologyManager**

Replace the host learning block in `packet_in_handler`:

```python
# Replace: if src not in self.host_location: ...
self.topo.learn_host(src, dpid, in_port)
if src not in self.host_location:
    self.host_location[src] = (dpid, in_port)
    self.logger.info("Learn host: %s at s%d port %d", src, dpid, in_port)
```

- [ ] **Step 4: Replace `_install_full_path` with dynamic version**

```python
def _install_full_path(self, path_name, priority):
    """Install flow rules for the named path."""
    fwd = self.path_fwd.get(path_name)
    rev = self.path_rev.get(path_name)
    if not fwd or not rev:
        return

    hosts = list(self.host_location.keys())
    if len(hosts) < 2:
        return

    mac_dst = hosts[1]
    mac_src = hosts[0]

    for dpid, out_port in fwd.items():
        if dpid in self.datapaths:
            dp = self.datapaths[dpid]
            parser = dp.ofproto_parser
            match = parser.OFPMatch(eth_dst=mac_dst)
            actions = [parser.OFPActionOutput(out_port)]
            self.add_flow(dp, priority, match, actions)

    for dpid, out_port in rev.items():
        if dpid in self.datapaths:
            dp = self.datapaths[dpid]
            parser = dp.ofproto_parser
            match = parser.OFPMatch(eth_dst=mac_src)
            actions = [parser.OFPActionOutput(out_port)]
            self.add_flow(dp, priority, match, actions)
```

- [ ] **Step 5: Add dynamic path computation trigger**

Add to `packet_in_handler`, replacing the hardcoded MAC check:

```python
# Replace: if not self.path_installed and h1_mac in ... and h3_mac in ...
all_hosts = list(self.topo.host_table.keys())
if not self.path_installed and len(all_hosts) >= 2:
    self._compute_paths()
    if self.path_installed:
        out_port = self._get_path_out_port(dpid)
        if out_port is not None:
            self._send_packet(datapath, in_port, out_port, msg)
            return
```

Add the computation method:

```python
def _compute_paths(self):
    """Compute two edge-disjoint paths via TopologyManager."""
    hosts = list(self.topo.host_table.keys())
    if len(hosts) < 2:
        return

    mac_a, mac_b = hosts[0], hosts[1]
    loc_a = self.topo.get_host_location(mac_a)
    loc_b = self.topo.get_host_location(mac_b)
    if not loc_a or not loc_b:
        return

    src_dpid = loc_a[0]
    dst_dpid = loc_b[0]

    paths = self.topo.compute_k_shortest_paths(src_dpid, dst_dpid, k=2, weight=None)

    if not paths:
        return

    fwd1, rev1 = self.topo.path_to_ports(paths[0][0])
    self.path_fwd["A"] = fwd1
    self.path_rev["A"] = rev1
    self.path_util_keys["A"] = self.topo.get_path_util_keys(fwd1, rev1)

    if len(paths) >= 2:
        fwd2, rev2 = self.topo.path_to_ports(paths[1][0])
        self.path_fwd["B"] = fwd2
        self.path_rev["B"] = rev2
        self.path_util_keys["B"] = self.topo.get_path_util_keys(fwd2, rev2)
    else:
        self.path_fwd["B"] = fwd1
        self.path_rev["B"] = rev1
        self.path_util_keys["B"] = self.path_util_keys["A"]

    self.set_path_util_keys(self.path_util_keys)
    self._install_full_path("A", PRIORITY_STANDBY_PATH)
    if len(paths) >= 2:
        self._install_full_path("B", PRIORITY_STANDBY_PATH)

    self.path_installed = True
    self.logger.info("Paths computed: fwd_A=%s, fwd_B=%s", fwd1, self.path_fwd["B"])
```

- [ ] **Step 6: Update `_get_path_out_port` and `_get_out_port`**

```python
def _get_path_out_port(self, dpid):
    fwd = self.path_fwd.get(self.current_path)
    if fwd and dpid in fwd:
        return fwd[dpid]
    return None

def _get_out_port(self, from_dpid, to_dpid):
    curr = self.current_path
    fwd = self.path_fwd.get(curr)
    rev = self.path_rev.get(curr)

    if fwd and from_dpid in fwd:
        fwd_chain = list(fwd.keys())
        try:
            idx = fwd_chain.index(from_dpid)
            if idx + 1 < len(fwd_chain) and fwd_chain[idx + 1] == to_dpid:
                return fwd[from_dpid]
        except ValueError:
            pass

    if rev and from_dpid in rev:
        rev_chain = list(rev.keys())
        try:
            idx = rev_chain.index(from_dpid)
            if idx + 1 < len(rev_chain) and rev_chain[idx + 1] == to_dpid:
                return rev[from_dpid]
        except ValueError:
            pass

    return None
```

- [ ] **Step 7: Update `_get_path_util` to use dynamic keys**

```python
def _get_path_util(self, path_name):
    keys = self.path_util_keys.get(path_name, set())
    if not keys:
        return 0
    utils = [self.link_utilization.get(k, 0) for k in keys]
    return max(utils) if utils else 0
```

- [ ] **Step 8: Commit**

```bash
git add controller/threshold_balancer.py
git commit -m "feat: update ThresholdBalancer for Fat-Tree with dynamic path computation"
```

---

## Task 7: Update Data Collection for Fat-Tree

**Files:**
- Modify: `scripts/collect_training_data.py`
- Modify: `scripts/traffic_gen.py`

- [ ] **Step 1: Update imports in `collect_training_data.py`**

```python
from topo.fat_tree_topo import create_topology, cleanup
```

- [ ] **Step 2: Add multi-pod traffic generation**

Update `run_single_experiment` to generate traffic between multiple host pairs:

```python
def run_single_experiment(net, pattern, duration):
    """Run traffic between multiple host pairs in the Fat-Tree."""
    pairs = [
        ("h0_0", "h3_0"),  # cross-pod: pod 0 -> pod 1
        ("h0_1", "h6_0"),  # cross-pod: pod 0 -> pod 3
        ("h2_0", "h5_0"),  # cross-pod: pod 1 -> pod 2
    ]

    for src_name, dst_name in pairs:
        src = net.get(src_name)
        dst = net.get(dst_name)
        if src is None or dst is None:
            continue

        dst.cmd("iperf -s -u &")
        time.sleep(0.5)

        if pattern == "sawtooth":
            cmds = generate_sawtooth_noise_commands(duration)
        elif pattern == "step":
            cmds = generate_step_commands(duration)
        else:
            cmds = generate_sine_commands(duration)

        for t_start, bw in cmds:
            src.cmd(f"iperf -c {dst.IP()} -u -b {bw}M -t 3 -i 1 &")
            time.sleep(3)

        dst.cmd("killall -9 iperf 2>/dev/null")
        time.sleep(1)
```

- [ ] **Step 3: Update `cleanup()` call**

```python
# In collect_batch, the cleanup() call now uses fat_tree_topo.cleanup()
```

- [ ] **Step 4: Update STP wait time (larger topology needs more time)**

```python
STP_WAIT = 30  # Fat-Tree has more switches, needs longer STP convergence
```

- [ ] **Step 5: Update `traffic_gen.py` with wider bandwidth range**

Add a Fat-Tree traffic generator that uses lower per-flow bandwidth (more flows share links):

```python
def generate_fat_tree_commands(duration=120, center=2.0, amplitude=1.5, period=30):
    """Lower-bandwidth traffic for Fat-Tree (more concurrent flows)."""
    commands = []
    t = 0
    while t < duration:
        bw = center + amplitude * np.sin(2 * np.pi * t / period)
        bw = max(0.3, min(4.0, bw))
        commands.append((t, round(bw, 2)))
        t += 3
    return commands
```

- [ ] **Step 6: Commit**

```bash
git add scripts/collect_training_data.py scripts/traffic_gen.py
git commit -m "feat: update data collection for Fat-Tree multi-pod traffic"
```

---

## Task 8: Update Feature Assembly for Per-Link Modeling

**Files:**
- Modify: `scripts/assemble_features.py`

Change from per-path (path_A, path_B) to per-link feature assembly.

- [ ] **Step 1: Rewrite `process_single_csv` for per-link features**

```python
WINDOW_SIZE = 3


def process_single_csv(input_csv):
    """Process a single batch CSV into per-link sliding window features."""
    df = pd.read_csv(input_csv)

    # Filter out edge ports (only backbone links)
    df = df[~df["link_label"].str.endswith("_edge")]

    samples = []
    for label, group in df.groupby("link_label"):
        group = group.sort_values("timestamp")
        utils = group["utilization"].values

        if len(utils) <= WINDOW_SIZE:
            continue

        for i in range(WINDOW_SIZE, len(utils)):
            features = list(utils[i - WINDOW_SIZE:i])
            samples.append({
                **{f"feat_{j}": features[j] for j in range(WINDOW_SIZE)},
                "target_label": label,
                "U_next": utils[i],
            })

    return pd.DataFrame(samples)
```

- [ ] **Step 2: Update `main()` to reflect per-link output**

```python
def main():
    csv_files = sorted(glob.glob(IN_FILES))
    all_features = []

    for f in csv_files:
        feat_df = process_single_csv(f)
        all_features.append(feat_df)
        print(f"Processed {f} -> {len(feat_df)} samples")

    merged_df = pd.concat(all_features, ignore_index=True)
    merged_df.to_csv(OUT_FILE, index=False, float_format="%.6f")

    n_links = merged_df["target_label"].nunique()
    print(f"\nTotal samples: {len(merged_df)}, Links: {n_links}")
    print(f"Saved to {OUT_FILE}")
```

- [ ] **Step 3: Commit**

```bash
git add scripts/assemble_features.py
git commit -m "feat: per-link feature assembly for Fat-Tree ML models"
```

---

## Task 9: Update Model Training for Per-Link Models

**Files:**
- Modify: `scripts/train_model.py`

Train one RF model per backbone link instead of per-path.

- [ ] **Step 1: Update `load_data` to accept variable feature count**

```python
def load_data(csv_path):
    df = pd.read_csv(csv_path)
    feat_cols = [c for c in df.columns if c.startswith("feat_")]
    return df, feat_cols
```

- [ ] **Step 2: Add per-link training loop**

Replace the main training loop:

```python
def main():
    print("===== Fat-Tree Per-Link Model Training =====")

    if not os.path.exists(TRAINING_CSV):
        print(f"Error: {TRAINING_CSV} not found!")
        return

    df, feat_cols = load_data(TRAINING_CSV)
    links = df["target_label"].unique()
    print(f"Features: {feat_cols}, Links: {len(links)}, Samples: {len(df)}")

    all_results = []
    for link in sorted(links):
        df_link = df[df["target_label"] == link]
        if len(df_link) < 20:
            print(f"  Skipping {link}: only {len(df_link)} samples")
            continue

        X = df_link[feat_cols].values
        y = df_link["U_next"].values

        # Train/test split (temporal)
        split_idx = int(len(X) * 0.8)
        X_train, X_test = X[:split_idx], X[split_idx:]
        y_train, y_test = y[:split_idx], y[split_idx:]

        # Quick hyperparameter search
        tscv = TimeSeriesSplit(n_splits=3)
        rf = RandomForestRegressor(random_state=RANDOM_STATE, n_jobs=-1)
        grid = GridSearchCV(
            rf, PARAM_GRID, cv=tscv,
            scoring="neg_mean_absolute_error", n_jobs=-1, verbose=0,
        )
        grid.fit(X_train, y_train)
        best = grid.best_estimator_

        y_pred = best.predict(X_test)
        mae = mean_absolute_error(y_test, y_pred)

        # Save model
        safe_name = link.replace(" ", "_").replace("/", "_")
        model_path = os.path.join(OUTPUT_MODEL_DIR, f"model_link_{safe_name}.pkl")
        joblib.dump(best, model_path)

        all_results.append({"link": link, "MAE": mae, "samples": len(df_link)})
        print(f"  {link}: MAE={mae:.4f}, n={len(df_link)}")

    summary = pd.DataFrame(all_results)
    summary.to_csv(SUMMARY_CSV, index=False)
    print(f"\nTrained {len(all_results)} models, summary saved to {SUMMARY_CSV}")
```

- [ ] **Step 3: Commit**

```bash
git add scripts/train_model.py
git commit -m "feat: per-link model training for Fat-Tree"
```

---

## Task 10: Update Unit Tests for Fat-Tree

**Files:**
- Modify: `controller/test_topology_manager.py`

- [ ] **Step 1: Add Fat-Tree test topology builder**

```python
def _build_fat_tree_topo(topo):
    """Build a minimal Fat-Tree k=4 test topology (one pod + core)."""
    # Pod 0: e1, e2 (edge), a1, a2 (agg)
    # Core: c1
    for dpid in range(1, 21):
        topo.add_switch(dpid)

    # Pod 0 edge <-> agg
    topo.add_link(1, 3, 9, 1)   # e1 <-> a1
    topo.add_link(1, 4, 10, 1)  # e1 <-> a2
    topo.add_link(2, 3, 9, 2)   # e2 <-> a1
    topo.add_link(2, 4, 10, 2)  # e2 <-> a2

    # Pod 0 agg <-> core
    topo.add_link(9, 3, 17, 1)  # a1 <-> c1
    topo.add_link(10, 3, 17, 2) # a2 <-> c1

    # Pod 1 edge <-> agg
    topo.add_link(3, 3, 11, 1)
    topo.add_link(3, 4, 12, 1)
    topo.add_link(4, 3, 11, 2)
    topo.add_link(4, 4, 12, 2)

    # Pod 1 agg <-> core
    topo.add_link(11, 3, 17, 3)
    topo.add_link(12, 3, 17, 4)

    # Learn hosts
    topo.learn_host("00:00:00:00:00:01", 1, 1)
    topo.learn_host("00:00:00:00:00:03", 3, 1)
```

- [ ] **Step 2: Add K-shortest paths test**

```python
def test_k_shortest_paths():
    """Test Yen's K-shortest paths on Fat-Tree topology."""
    topo = TopologyManager()
    _build_fat_tree_topo(topo)

    paths = topo.compute_k_shortest_paths(1, 3, k=3, weight=None)

    assert len(paths) >= 1, "Should find at least one path"
    # Fat-Tree should support multiple paths between edge switches in different pods
    for path, cost in paths:
        assert path[0] == 1
        assert path[-1] == 3

    print(f"  PASS: test_k_shortest_paths")
    print(f"    Found {len(paths)} paths: {[(p, f'{c:.1f}') for p, c in paths]}")
```

- [ ] **Step 3: Add weighted paths test**

```python
def test_weighted_k_paths():
    """Test that weights affect path ordering."""
    topo = TopologyManager()
    _build_fat_tree_topo(topo)

    # Set high weight on one link to make it less preferred
    topo.set_edge_weight(9, 3, 100.0)  # make a1->c1 expensive

    paths = topo.compute_k_shortest_paths(1, 3, k=3, weight='weight')

    assert len(paths) >= 1
    # The expensive link should not be in the first path
    first_path = paths[0][0]
    # Path through a2 should be preferred over a1
    assert 9 not in first_path or len(paths) == 1, \
        "High-weight link should be avoided"

    print(f"  PASS: test_weighted_k_paths")
```

- [ ] **Step 4: Update existing tests for compatibility**

The existing `_build_dual_path_topo` helper and all its tests should still pass since `compute_edge_disjoint_paths` is preserved as a legacy wrapper. Run to verify:

```bash
cd /home/yang/SDN && python3 controller/test_topology_manager.py
```

- [ ] **Step 5: Commit**

```bash
git add controller/test_topology_manager.py
git commit -m "test: add Fat-Tree and K-shortest paths unit tests"
```

---

## Task 11: End-to-End Verification

- [ ] **Step 1: Run all unit tests**

```bash
cd /home/yang/SDN && python3 controller/test_topology_manager.py
```
Expected: All tests pass (original 10 + new 2).

- [ ] **Step 2: Verify Fat-Tree topology import**

```bash
cd /home/yang/SDN && python3 -c "
from topo.fat_tree_topo import create_topology
# Don't actually start Mininet, just verify the function exists
print('Fat-Tree topology module OK')
"
```

- [ ] **Step 3: Verify controller imports**

```bash
cd /home/yang/SDN && python3 -c "
from controller.weight_engine import DynamicWeightEngine
from controller.topology_manager import TopologyManager
print('All controller modules OK')
"
```

- [ ] **Step 4: Final commit with all changes**

```bash
git add -A && git status
```

Review the status, then:
```bash
git commit -m "feat: complete Fat-Tree k=4 upgrade with K-shortest path routing"
```
