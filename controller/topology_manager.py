import networkx as nx
from collections import defaultdict


class TopologyManager:
    """基于 NetworkX 的动态拓扑管理器。

    职责：
    1. 维护有向图 G=(V, E)，节点=交换机 DPID，边=物理链路
    2. 管理主机位置表 Host_Table: MAC -> (dpid, port)
    3. 识别边缘接入端口（排除骨干端口）
    4. 计算加权最短路径（Dijkstra）
    5. 计算生成树（用于无环洪泛）
    """

    def __init__(self):
        self.G = nx.DiGraph()
        self.host_table = {}          # MAC -> (dpid, port)
        self.link_ports = {}          # (src_dpid, dst_dpid) -> out_port
        self._st_ports_cache = None

    # ──────────────────────────────────────────────
    # 拓扑动态维护
    # ──────────────────────────────────────────────

    def add_switch(self, dpid):
        if not self.G.has_node(dpid):
            self.G.add_node(dpid)
            self._recompute_spanning_tree()

    def remove_switch(self, dpid):
        if self.G.has_node(dpid):
            self.G.remove_node(dpid)
            self.link_ports = {
                k: v for k, v in self.link_ports.items()
                if dpid not in k
            }
            self.host_table = {
                mac: loc for mac, loc in self.host_table.items()
                if loc[0] != dpid
            }
            self._recompute_spanning_tree()

    def add_link(self, src_dpid, src_port, dst_dpid, dst_port):
        self.G.add_edge(src_dpid, dst_dpid)
        self.G.add_edge(dst_dpid, src_dpid)
        self.link_ports[(src_dpid, dst_dpid)] = src_port
        self.link_ports[(dst_dpid, src_dpid)] = dst_port
        self._recompute_spanning_tree()

    def remove_link(self, src_dpid, dst_dpid):
        if self.G.has_edge(src_dpid, dst_dpid):
            self.G.remove_edge(src_dpid, dst_dpid)
        if self.G.has_edge(dst_dpid, src_dpid):
            self.G.remove_edge(dst_dpid, src_dpid)
        self.link_ports.pop((src_dpid, dst_dpid), None)
        self.link_ports.pop((dst_dpid, src_dpid), None)
        self._recompute_spanning_tree()

    def learn_host(self, mac, dpid, port):
        if mac not in self.host_table:
            self.host_table[mac] = (dpid, port)

    def get_host_location(self, mac):
        return self.host_table.get(mac)

    def has_path(self, src_dpid, dst_dpid):
        """Check whether a directed path exists between two switches."""
        if not (self.G.has_node(src_dpid) and self.G.has_node(dst_dpid)):
            return False
        return nx.has_path(self.G, src_dpid, dst_dpid)

    # ──────────────────────────────────────────────
    # 边缘端口识别
    # ──────────────────────────────────────────────

    def get_backbone_ports(self, dpid):
        """获取连接其他交换机的骨干端口集合"""
        if not self.G.has_node(dpid):
            return set()
        ports = set()
        for neighbor in self.G[dpid]:
            out_port = self.link_ports.get((dpid, neighbor))
            if out_port is not None:
                ports.add(out_port)
        return ports

    def is_edge_port(self, dpid, port_no):
        """判断某个端口是否为边缘接入端口（不连接其他交换机）"""
        backbone = self.get_backbone_ports(dpid)
        return port_no not in backbone

    # ──────────────────────────────────────────────
    # 最短路径计算
    # ──────────────────────────────────────────────

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

    def _path_cost(self, path, weight='weight'):
        """Compute total weight of a path (list of nodes)."""
        cost = 0.0
        for i in range(len(path) - 1):
            edge_data = self.G.get_edge_data(path[i], path[i + 1], default={})
            cost += edge_data.get(weight, 1.0)
        return cost

    def compute_optimal_path(self, src_dpid, dst_dpid, weight='weight'):
        """Compute the single optimal (shortest) path using Dijkstra.

        Returns:
            (path_nodes, cost) tuple, or None if no path exists.
        """
        if not (self.G.has_node(src_dpid) and self.G.has_node(dst_dpid)):
            return None

        if not nx.has_path(self.G, src_dpid, dst_dpid):
            return None

        path_nodes = nx.shortest_path(self.G, src_dpid, dst_dpid, weight=weight)
        cost = self._path_cost(path_nodes, weight)
        return path_nodes, cost

    def compute_ecmp_path(self, src_dpid, dst_dpid, src_mac, dst_mac, weight='weight'):
        """Compute shortest path with ECMP-aware selection for Fat-Tree.

        When multiple equal-cost shortest paths exist, selects one based on
        MAC pair hash to distribute traffic across different core switches.

        Returns:
            (path_nodes, cost) tuple, or None if no path exists.
        """
        if not (self.G.has_node(src_dpid) and self.G.has_node(dst_dpid)):
            return None

        if not nx.has_path(self.G, src_dpid, dst_dpid):
            return None

        all_paths = list(nx.all_shortest_paths(self.G, src_dpid, dst_dpid, weight=weight))

        if not all_paths:
            return None

        if len(all_paths) == 1:
            path_nodes = all_paths[0]
        else:
            mac_lo, mac_hi = sorted([src_mac, dst_mac])
            idx = hash((mac_lo, mac_hi)) % len(all_paths)
            path_nodes = all_paths[idx]

        cost = self._path_cost(path_nodes, weight)
        return path_nodes, cost

    def compute_alternative_path(self, src_dpid, dst_dpid, primary_path, weight='weight'):
        """Compute an alternative shortest path by temporarily removing primary path edges.

        Returns:
            (path_nodes, cost) tuple, or None if no alternative exists.
        """
        if not primary_path or len(primary_path) < 2:
            return None

        removed = []
        for i in range(len(primary_path) - 1):
            u, v = primary_path[i], primary_path[i + 1]
            if self.G.has_edge(u, v):
                w = self.G[u][v].get('weight', 1.0)
                self.G.remove_edge(u, v)
                removed.append((u, v, w))
            if self.G.has_edge(v, u):
                w = self.G[v][u].get('weight', 1.0)
                self.G.remove_edge(v, u)
                removed.append((v, u, w))

        result = None
        if nx.has_path(self.G, src_dpid, dst_dpid):
            path_nodes = nx.shortest_path(self.G, src_dpid, dst_dpid, weight=weight)
            cost = self._path_cost(path_nodes, weight)
            result = (path_nodes, cost)

        for u, v, w in removed:
            self.G.add_edge(u, v, weight=w)

        return result

    def enumerate_all_shortest_paths(self, src_dpid, dst_dpid, weight='weight'):
        """Enumerate all equal-cost shortest paths between two switches.

        Returns:
            list of path node lists, or empty list if no path exists.
        """
        if not (self.G.has_node(src_dpid) and self.G.has_node(dst_dpid)):
            return []
        if not nx.has_path(self.G, src_dpid, dst_dpid):
            return []
        return list(nx.all_shortest_paths(self.G, src_dpid, dst_dpid, weight=weight))

    def get_core_facing_ports(self, dpid):
        """Return [(port_no, core_dpid), ...] for an aggregation switch's core-facing ports.

        DPID ranges (Mininet passes dpid as hex to OVS):
          Edge switches:  0x01-0x08 → decimal 1-8
          Agg switches:   0x09-0x16 → decimal 9-22
          Core switches:  0x17-0x20 → decimal 23-32
        """
        CORE_DPID_MIN = 23   # 0x17
        CORE_DPID_MAX = 32   # 0x20
        ports = []
        if not self.G.has_node(dpid):
            return ports
        for neighbor in self.G[dpid]:
            if CORE_DPID_MIN <= neighbor <= CORE_DPID_MAX:
                port_no = self.link_ports.get((dpid, neighbor))
                if port_no is not None:
                    ports.append((port_no, neighbor))
        return ports

    def compute_spanning_tree_ports(self):
        """返回生成树端口集合（由拓扑变更事件预计算）。

        返回: {dpid: set(port_no, ...)}
        """
        if self._st_ports_cache is None:
            self._recompute_spanning_tree()
        return self._st_ports_cache

    def get_flood_ports(self, dpid, in_port=None):
        """获取无环洪泛端口列表（仅生成树端口，排除入端口）"""
        st_ports = self.compute_spanning_tree_ports()
        ports = set(st_ports.get(dpid, set()))
        if in_port is not None:
            ports.discard(in_port)
        return ports

    def get_path_util_keys(self, fwd_ports, rev_ports):
        """从路径端口映射中提取 (dpid, port_no) 集合，用于利用率统计。"""
        keys = set()
        if fwd_ports:
            for dpid, port in fwd_ports.items():
                keys.add((dpid, port))
        if rev_ports:
            for dpid, port in rev_ports.items():
                keys.add((dpid, port))
        return keys

    # ──────────────────────────────────────────────
    # 内部辅助
    # ──────────────────────────────────────────────

    def path_to_ports(self, path):
        """Convert a node list to (fwd_ports, rev_ports) dicts."""
        return self._path_to_ports(path)

    def _path_to_ports(self, path):
        """将节点路径转换为端口映射 (fwd, rev)"""
        fwd_ports = {}
        for i in range(len(path) - 1):
            fwd_ports[path[i]] = self.link_ports[(path[i], path[i + 1])]

        rev_ports = {}
        for i in range(len(path) - 1, 0, -1):
            rev_ports[path[i]] = self.link_ports[(path[i], path[i - 1])]

        return fwd_ports, rev_ports

    def _recompute_spanning_tree(self):
        """Eagerly recompute spanning tree ports on topology change."""
        undirected = self.G.to_undirected()
        if not undirected.nodes():
            self._st_ports_cache = {}
            return

        if not nx.is_connected(undirected):
            self._st_ports_cache = {}
            return

        st = nx.minimum_spanning_tree(undirected)
        st_ports = defaultdict(set)
        for u, v in st.edges():
            port_uv = self.link_ports.get((u, v))
            port_vu = self.link_ports.get((v, u))
            if port_uv is not None:
                st_ports[u].add(port_uv)
            if port_vu is not None:
                st_ports[v].add(port_vu)
        self._st_ports_cache = dict(st_ports)
