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

    def compute_edge_disjoint_paths(self, src_dpid, dst_dpid):
        """计算两条边不相交路径（Suurballe 算法变体）。

        返回: (fwd1, rev1, fwd2, rev2)
              每个元素为 {dpid: out_port} 映射。
              若拓扑不支持两条不相交路径，fwd2/rev2 为 None。
        """
        if not (self.G.has_node(src_dpid) and self.G.has_node(dst_dpid)):
            return None, None, None, None

        # 步骤 1：计算第一条最短路径
        try:
            path1 = nx.shortest_path(self.G, src_dpid, dst_dpid)
        except nx.NetworkXNoPath:
            return None, None, None, None

        # 步骤 2：移除 path1 的边，构造残余图
        edges_in_path1 = set(zip(path1[:-1], path1[1:]))
        residual = self.G.copy()
        residual.remove_edges_from(edges_in_path1)

        # 步骤 3：在残余图上计算第二条路径
        try:
            path2 = nx.shortest_path(residual, src_dpid, dst_dpid)
        except nx.NetworkXNoPath:
            fwd, rev = self._path_to_ports(path1)
            return fwd, rev, None, None

        # 步骤 4：验证边不相交（双重保障）
        edges2 = set(zip(path2[:-1], path2[1:]))
        if edges_in_path1.isdisjoint(edges2):
            fwd1, rev1 = self._path_to_ports(path1)
            fwd2, rev2 = self._path_to_ports(path2)
            return fwd1, rev1, fwd2, rev2
        else:
            fwd, rev = self._path_to_ports(path1)
            return fwd, rev, None, None

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
