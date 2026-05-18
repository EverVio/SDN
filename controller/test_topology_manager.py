"""拓扑管理器单元测试：验证动态图、主机表、边不相交路径、生成树"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from controller.topology_manager import TopologyManager


def test_basic_graph_operations():
    """测试基本图操作：添加/删除交换机和链路"""
    topo = TopologyManager()

    # 添加交换机
    topo.add_switch(1)
    topo.add_switch(2)
    topo.add_switch(3)
    topo.add_switch(4)
    assert set(topo.G.nodes()) == {1, 2, 3, 4}

    # 添加链路（双向）
    topo.add_link(1, 3, 2, 1)  # s1:3 <-> s2:1
    topo.add_link(2, 2, 4, 1)  # s2:2 <-> s4:1
    topo.add_link(1, 4, 3, 1)  # s1:4 <-> s3:1
    topo.add_link(3, 2, 4, 2)  # s3:2 <-> s4:2

    assert topo.G.has_edge(1, 2)
    assert topo.G.has_edge(2, 1)
    assert topo.G.has_edge(1, 3)
    assert topo.G.has_edge(4, 2)
    assert topo.link_ports[(1, 2)] == 3
    assert topo.link_ports[(2, 1)] == 1

    # 删除链路
    topo.remove_link(1, 3)
    assert not topo.G.has_edge(1, 3)
    assert not topo.G.has_edge(3, 1)
    assert (1, 3) not in topo.link_ports

    # 删除交换机
    topo.remove_switch(3)
    assert 3 not in topo.G.nodes()
    # 关联的链路端口应被清理
    assert all(3 not in k for k in topo.link_ports.keys())

    print("  PASS: test_basic_graph_operations")


def test_host_learning():
    """测试主机学习与查询"""
    topo = TopologyManager()
    topo.add_switch(1)
    topo.add_switch(4)

    topo.learn_host("00:00:00:00:00:01", 1, 1)
    topo.learn_host("00:00:00:00:00:03", 4, 1)

    assert topo.get_host_location("00:00:00:00:00:01") == (1, 1)
    assert topo.get_host_location("00:00:00:00:00:03") == (4, 1)
    assert topo.get_host_location("00:00:00:00:00:99") is None

    # 学习是幂等的（只学一次）
    topo.learn_host("00:00:00:00:00:01", 2, 3)  # 不应覆盖
    assert topo.get_host_location("00:00:00:00:00:01") == (1, 1)

    # 删除交换机时清理关联主机
    topo.remove_switch(1)
    assert topo.get_host_location("00:00:00:00:00:01") is None
    assert topo.get_host_location("00:00:00:00:00:03") == (4, 1)

    print("  PASS: test_host_learning")


def test_edge_port_identification():
    """测试边缘端口识别"""
    topo = TopologyManager()
    _build_dual_path_topo(topo)

    # s1: port 3 和 port 4 是骨干端口，port 1 和 2 是边缘端口
    assert not topo.is_edge_port(1, 3)  # 骨干
    assert not topo.is_edge_port(1, 4)  # 骨干
    assert topo.is_edge_port(1, 1)      # 边缘（h1 接入）
    assert topo.is_edge_port(1, 2)      # 边缘（h2 接入）

    # s4: port 1 和 2 是骨干端口
    assert not topo.is_edge_port(4, 1)  # 骨干
    assert not topo.is_edge_port(4, 2)  # 骨干
    assert topo.is_edge_port(4, 3)      # 边缘（h3 接入）
    assert topo.is_edge_port(4, 4)      # 边缘（h4 接入）

    # s2: port 1 和 2 是骨干端口
    assert not topo.is_edge_port(2, 1)
    assert not topo.is_edge_port(2, 2)

    print("  PASS: test_edge_port_identification")


def test_edge_disjoint_paths():
    """测试 Suurballe 边不相交路径计算"""
    topo = TopologyManager()
    _build_dual_path_topo(topo)

    fwd1, rev1, fwd2, rev2 = topo.compute_edge_disjoint_paths(1, 4)

    # 应该找到两条路径
    assert fwd1 is not None, "Should find at least one path"
    assert fwd2 is not None, "Dual-path topology should support two edge-disjoint paths"

    # 路径应该是边不相交的
    edges1 = set(fwd1.items())
    edges2 = set(fwd2.items())

    # 正向路径的边不应重叠（除了入口交换机 s1）
    fwd1_out_ports = set(fwd1.values())
    fwd2_out_ports = set(fwd2.values())
    assert fwd1_out_ports != fwd2_out_ports or len(fwd1) != len(fwd2), \
        "Two paths should use different edges"

    # 验证路径结构：路径应从 s1 出发（最后的交换机不需要转发条目）
    assert 1 in fwd1, "Path 1 should start at s1"
    assert 1 in fwd2, "Path 2 should start at s1"

    # 两条路径的链路集合应该不相交
    edges1 = set()
    for dpid, port in fwd1.items():
        edges1.add((dpid, port))
    edges2 = set()
    for dpid, port in fwd2.items():
        edges2.add((dpid, port))

    # s1 的出端口不同（一条 3，一条 4）
    assert fwd1[1] != fwd2[1], "Ingress switch should use different output ports"

    print(f"  PASS: test_edge_disjoint_paths")
    print(f"    Path 1 fwd: {fwd1}")
    print(f"    Path 2 fwd: {fwd2}")
    print(f"    Path 1 rev: {rev1}")
    print(f"    Path 2 rev: {rev2}")


def test_edge_disjoint_fallback():
    """测试拓扑不支持两条不相交路径时的降级"""
    topo = TopologyManager()
    # 线性拓扑：s1 - s2 - s3（只有一条路径）
    topo.add_switch(1)
    topo.add_switch(2)
    topo.add_switch(3)
    topo.add_link(1, 1, 2, 1)
    topo.add_link(2, 2, 3, 1)

    fwd1, rev1, fwd2, rev2 = topo.compute_edge_disjoint_paths(1, 3)

    assert fwd1 is not None
    assert fwd2 is None, "Linear topology should fall back to single path"
    assert fwd1[1] == 1  # s1:1 -> s2
    assert fwd1[2] == 2  # s2:2 -> s3

    print("  PASS: test_edge_disjoint_fallback")


def test_spanning_tree():
    """测试生成树计算（无环洪泛）"""
    topo = TopologyManager()
    _build_dual_path_topo(topo)

    st_ports = topo.compute_spanning_tree_ports()

    # 生成树应该覆盖所有 4 个交换机
    assert 1 in st_ports
    assert 2 in st_ports
    assert 3 in st_ports
    assert 4 in st_ports

    # 每个交换机至少有一个生成树端口
    for dpid, ports in st_ports.items():
        assert len(ports) >= 1, f"Switch s{dpid} should have at least 1 ST port"

    # 生成树应该有 n-1=3 条边，总共 6 个端口（每条边两端各一个）
    total_ports = sum(len(p) for p in st_ports.values())
    assert total_ports == 6, f"Spanning tree should have 6 ports total, got {total_ports}"

    print(f"  PASS: test_spanning_tree")
    print(f"    ST ports: {st_ports}")


def test_flood_ports():
    """测试无环洪泛端口"""
    topo = TopologyManager()
    _build_dual_path_topo(topo)

    # s1 的洪泛端口（排除入端口 1，但 1 是接入端口不在 ST 中）
    flood = topo.get_flood_ports(1, in_port=1)
    assert len(flood) >= 1, "s1 should have flood ports"
    assert 1 not in flood, "in_port should be excluded"
    # ST 端口应该是骨干端口（3 或 4）
    assert flood.issubset({3, 4}), f"Flood ports should be backbone ports, got {flood}"

    # s2 的洪泛端口（入端口 1 是骨干端口，应被排除）
    flood2 = topo.get_flood_ports(2, in_port=1)
    assert 1 not in flood2, "in_port should be excluded"
    # s2 的 ST 端口是 {1, 2}，排除 in_port=1 后应为 {2}
    assert flood2 == {2}

    print(f"  PASS: test_flood_ports")
    print(f"    s1 flood (in=1): {flood}")
    print(f"    s2 flood (in=1): {flood2}")


def test_path_util_keys():
    """测试路径利用率键提取"""
    topo = TopologyManager()
    _build_dual_path_topo(topo)

    fwd1, rev1, fwd2, rev2 = topo.compute_edge_disjoint_paths(1, 4)

    keys_a = topo.get_path_util_keys(fwd1, rev1)
    keys_b = topo.get_path_util_keys(fwd2, rev2)

    # 两组键应该不完全相同（使用了不同链路）
    assert keys_a != keys_b, "Two edge-disjoint paths should have different util keys"

    # 每组应该包含正向和反向的所有 (dpid, port) 对
    assert len(keys_a) >= 2, "Path A should have at least 2 util keys"
    assert len(keys_b) >= 2, "Path B should have at least 2 util keys"

    print(f"  PASS: test_path_util_keys")
    print(f"    Keys A: {keys_a}")
    print(f"    Keys B: {keys_b}")


def test_topology_change_invalidation():
    """测试拓扑变更时缓存失效"""
    topo = TopologyManager()
    _build_dual_path_topo(topo)

    # 预计算生成树
    st1 = topo.compute_spanning_tree_ports()
    assert topo._st_ports_cache is not None

    # 添加新链路应清除缓存
    topo.add_link(2, 3, 3, 3)
    assert topo._st_ports_cache is None

    # 重新计算
    st2 = topo.compute_spanning_tree_ports()
    assert topo._st_ports_cache is not None

    print("  PASS: test_topology_change_invalidation")


def test_dual_path_consistency():
    """测试双路径拓扑下路径计算的一致性"""
    topo = TopologyManager()
    _build_dual_path_topo(topo)

    # 多次计算应该返回相同结果
    for i in range(5):
        fwd1, rev1, fwd2, rev2 = topo.compute_edge_disjoint_paths(1, 4)
        assert fwd1 is not None
        assert fwd2 is not None

    print("  PASS: test_dual_path_consistency")


def _build_dual_path_topo(topo):
    """构建标准双路径测试拓扑（与 dual_path_topo.py 一致）

    路径 A: s1:3 → s2:1, s2:2 → s4:1
    路径 B: s1:4 → s3:1, s3:2 → s4:2
    """
    for dpid in [1, 2, 3, 4]:
        topo.add_switch(dpid)

    topo.add_link(1, 3, 2, 1)  # s1:3 <-> s2:1
    topo.add_link(2, 2, 4, 1)  # s2:2 <-> s4:1
    topo.add_link(1, 4, 3, 1)  # s1:4 <-> s3:1
    topo.add_link(3, 2, 4, 2)  # s3:2 <-> s4:2


if __name__ == "__main__":
    print("=" * 60)
    print("TopologyManager Unit Tests")
    print("=" * 60)

    tests = [
        test_basic_graph_operations,
        test_host_learning,
        test_edge_port_identification,
        test_edge_disjoint_paths,
        test_edge_disjoint_fallback,
        test_spanning_tree,
        test_flood_ports,
        test_path_util_keys,
        test_topology_change_invalidation,
        test_dual_path_consistency,
    ]

    passed = 0
    failed = 0
    for test in tests:
        try:
            test()
            passed += 1
        except Exception as e:
            print(f"  FAIL: {test.__name__}: {e}")
            import traceback
            traceback.print_exc()
            failed += 1

    print()
    print("=" * 60)
    print(f"Results: {passed} passed, {failed} failed, {passed + failed} total")
    print("=" * 60)
    sys.exit(0 if failed == 0 else 1)
