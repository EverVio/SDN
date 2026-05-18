import networkx as nx
from collections import defaultdict


class TopologyManager:
    """基于 NetworkX 的动态拓扑管理器。

    职责：
    1. 维护有向图 G=(V, E)，节点=交换机 DPID，边=物理链路
    2. 管理主机位置表 Host_Table: MAC -> (dpid, port)
    3. 识别边缘接入端口（排除骨干端口）
    4. 计算边不相交路径（Suurballe 算法）
    5. 计算生成树（用于无环洪泛）
    """

    def __init__(self):
        self.G = nx.DiGraph()
        self.host_table = {}          # MAC -> (dpid, port)
        self.link_ports = {}          # (src_dpid, dst_dpid) -> out_port
        self._edge_ports_cache = None
        self._st_ports_cache = None

    # ──────────────────────────────────────────────
    # 拓扑动态维护
    # ──────────────────────────────────────────────

    def add_switch(self, dpid):
        if not self.G.has_node(dpid):
            self.G.add_node(dpid)
            self._invalidate_cache()

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
            self._invalidate_cache()

    def add_link(self, src_dpid, src_port, dst_dpid, dst_port):
        self.G.add_edge(src_dpid, dst_dpid)
        self.G.add_edge(dst_dpid, src_dpid)
        self.link_ports[(src_dpid, dst_dpid)] = src_port
        self.link_ports[(dst_dpid, src_dpid)] = dst_port
        self._invalidate_cache()

    def remove_link(self, src_dpid, dst_dpid):
        if self.G.has_edge(src_dpid, dst_dpid):
            self.G.remove_edge(src_dpid, dst_dpid)
        if self.G.has_edge(dst_dpid, src_dpid):
            self.G.remove_edge(dst_dpid, src_dpid)
        self.link_ports.pop((src_dpid, dst_dpid), None)
        self.link_ports.pop((dst_dpid, src_dpid), None)
        self._invalidate_cache()

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
    # 路径计算：边不相交路径（Suurballe）
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

    def compute_k_shortest_paths(self, src_dpid, dst_dpid, k=3, weight='weight'):
        """Compute up to K shortest paths using Yen's algorithm.

        Returns: List of (path, cost) tuples sorted by cost ascending.
        """
        import heapq

        if not (self.G.has_node(src_dpid) and self.G.has_node(dst_dpid)):
            return []

        if not nx.has_path(self.G, src_dpid, dst_dpid):
            return []
        first_path = nx.shortest_path(self.G, src_dpid, dst_dpid, weight=weight)
        first_cost = self._path_cost(first_path, weight)

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

                if nx.has_path(self.G, spur_node, dst_dpid):
                    spur_path = nx.shortest_path(
                        self.G, spur_node, dst_dpid, weight=weight
                    )
                    total_path = root_path[:-1] + spur_path
                    total_cost = root_cost + self._path_cost(spur_path, weight)

                    key = tuple(total_path)
                    if key not in seen:
                        seen.add(key)
                        heapq.heappush(B, (total_cost, len(seen), total_path))

                # Restore all removed edges
                for u, v, w in removed_edges:
                    if not self.G.has_edge(u, v):
                        self.G.add_edge(u, v, weight=w)

            if not B:
                break
            cost, _, path = heapq.heappop(B)
            A.append((path, cost))

        return A

    def select_ecmp_path(self, flow_tuple, k):
        """Select a path index [0, k) using a hash of the 5-tuple.

        Args:
            flow_tuple: (src_ip, dst_ip, proto, src_port, dst_port)
            k: number of available paths

        Returns:
            Integer index in [0, k), deterministic for a given flow_tuple.
        """
        if k <= 0:
            return 0
        return hash(flow_tuple) % k

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

    def compute_spanning_tree_ports(self):
        """计算生成树端口集合，用于无环洪泛。

        返回: {dpid: set(port_no, ...)}
        """
        if self._st_ports_cache is not None:
            return self._st_ports_cache

        undirected = self.G.to_undirected()
        if not undirected.nodes():
            return {}

        try:
            st = nx.minimum_spanning_tree(undirected)
        except nx.NetworkXError:
            return {}

        st_ports = defaultdict(set)
        for u, v in st.edges():
            port_uv = self.link_ports.get((u, v))
            port_vu = self.link_ports.get((v, u))
            if port_uv is not None:
                st_ports[u].add(port_uv)
            if port_vu is not None:
                st_ports[v].add(port_vu)

        self._st_ports_cache = dict(st_ports)
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

    def _invalidate_cache(self):
        self._edge_ports_cache = None
        self._st_ports_cache = None
