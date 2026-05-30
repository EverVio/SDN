"""
Fat-Tree k=4 topology description for frontend visualization.
Generates nodes and edges with predefined layout coordinates.
"""

# DPID ranges: edge 1-8, aggregation 9-16, core 17-20
# Hosts: h{pod}_{idx} where pod=0..3, idx=0..3


def get_topology_json():
    """Return (nodes, edges, link_map) for the Fat-Tree k=4 topology."""

    nodes = []
    edges = []

    # --- Core switches (dpid 17-20) ---
    core_positions = [
        (300, 100),
        (500, 100),
        (1100, 100),
        (1300, 100),
    ]
    for i, (x, y) in enumerate(core_positions):
        dpid = 17 + i
        nodes.append(
            {
                "data": {
                    "id": f"s{dpid}",
                    "label": f"s{dpid}",
                    "dpid": dpid,
                    "type": "core",
                },
                "position": {"x": x, "y": y},
            }
        )

    # --- Aggregation switches (dpid 9-16) ---
    agg_positions = [
        (200, 520), (400, 520),  # Pod 0
        (600, 520), (800, 520),  # Pod 1
        (1000, 520), (1200, 520),  # Pod 2
        (1400, 520), (1600, 520),  # Pod 3
    ]
    for i, (x, y) in enumerate(agg_positions):
        dpid = 9 + i
        nodes.append(
            {
                "data": {
                    "id": f"s{dpid}",
                    "label": f"s{dpid}",
                    "dpid": dpid,
                    "type": "aggregation",
                    "weightText": "50:50",
                },
                "position": {"x": x, "y": y},
            }
        )

    # --- Edge switches (dpid 1-8) ---
    edge_positions = [
        (200, 940), (400, 940),  # Pod 0
        (600, 940), (800, 940),  # Pod 1
        (1000, 940), (1200, 940),  # Pod 2
        (1400, 940), (1600, 940),  # Pod 3
    ]
    for i, (x, y) in enumerate(edge_positions):
        dpid = 1 + i
        nodes.append(
            {
                "data": {
                    "id": f"s{dpid}",
                    "label": f"s{dpid}",
                    "dpid": dpid,
                    "type": "edge",
                },
                "position": {"x": x, "y": y},
            }
        )

    # --- Hosts (h{pod}_{idx}) ---
    for pod in range(4):
        for e_idx in range(2):
            edge_dpid = pod * 2 + e_idx + 1
            ex, ey = edge_positions[pod * 2 + e_idx]
            for h_idx in range(2):
                host_name = f"h{pod}_{e_idx * 2 + h_idx}"
                hx = ex + (h_idx * 2 - 1) * 45  
                hy = 1100
                nodes.append(
                    {
                        "data": {
                            "id": host_name,
                            "label": f"{host_name}",
                            "type": "host",
                            "edgeDpid": edge_dpid,
                        },
                        "position": {"x": hx, "y": hy},
                    }
                )

    # --- Edges ---
    # Aggregation-to-Core links
    # Each agg switch (pod p, idx a) connects to K/2 core switches
    # core_dpid = a * (K//2) + c_local + 17  (where c_local = 0,1)
    for pod in range(4):
        for a_idx in range(2):
            agg_dpid = 9 + pod * 2 + a_idx
            for c_local in range(2):
                core_dpid = 17 + a_idx * 2 + c_local
                link_id = f"e-{min(agg_dpid, core_dpid)}-{max(agg_dpid, core_dpid)}"
                edges.append(
                    {
                        "data": {
                            "id": link_id,
                            "source": f"s{agg_dpid}",
                            "target": f"s{core_dpid}",
                            "type": "agg-core",
                            "bandwidth": 2,  # Mbps
                        },
                    }
                )

    # Edge-to-Aggregation links
    for pod in range(4):
        for e_idx in range(2):
            edge_dpid = pod * 2 + e_idx + 1
            for a_idx in range(2):
                agg_dpid = 9 + pod * 2 + a_idx
                link_id = f"e-{min(edge_dpid, agg_dpid)}-{max(edge_dpid, agg_dpid)}"
                edges.append(
                    {
                        "data": {
                            "id": link_id,
                            "source": f"s{edge_dpid}",
                            "target": f"s{agg_dpid}",
                            "type": "edge-agg",
                            "bandwidth": 10,  # Mbps
                        },
                    }
                )

    # Host-to-Edge links
    for pod in range(4):
        for e_idx in range(2):
            edge_dpid = pod * 2 + e_idx + 1
            for h_idx in range(2):
                host_name = f"h{pod}_{e_idx * 2 + h_idx}"
                link_id = f"e-{host_name}-s{edge_dpid}"
                edges.append(
                    {
                        "data": {
                            "id": link_id,
                            "source": host_name,
                            "target": f"s{edge_dpid}",
                            "type": "host-edge",
                            "bandwidth": 10,  # Mbps
                        },
                    }
                )

    # Build link_map: maps "dpid_port" -> edge_id for utilization lookup
    # Key format matches traffic_data.csv: "dpid_port_no"
    # Port assignments based on topology wiring in fat_tree_topo.py:
    #   Edge switches: port 1,2 = host-facing; port 3,4 = uplink to agg
    #   Agg switches: port 1,2 = downlink to edge; port 3,4 = uplink to core
    #   Core switches: port 1,2,3,4 = downlink to agg
    link_map = {}

    # Agg-to-Core port mapping
    for pod in range(4):
        for a_idx in range(2):
            agg_dpid = 9 + pod * 2 + a_idx
            for c_local in range(2):
                core_dpid = 17 + a_idx * 2 + c_local
                agg_port = 3 + c_local  # ports 3,4
                core_port = pod + 1
                link_id = f"e-{min(agg_dpid, core_dpid)}-{max(agg_dpid, core_dpid)}"
                link_map[f"{agg_dpid}_{agg_port}"] = link_id
                link_map[f"{core_dpid}_{core_port}"] = link_id

    # Edge-to-Agg port mapping
    for pod in range(4):
        for e_idx in range(2):
            edge_dpid = pod * 2 + e_idx + 1
            for a_idx in range(2):
                agg_dpid = 9 + pod * 2 + a_idx
                edge_port = 3 + a_idx  # ports 3,4
                agg_port = e_idx + 1
                link_id = f"e-{min(edge_dpid, agg_dpid)}-{max(edge_dpid, agg_dpid)}"
                link_map[f"{edge_dpid}_{edge_port}"] = link_id
                link_map[f"{agg_dpid}_{agg_port}"] = link_id

    # Host-facing ports on edge switches
    for pod in range(4):
        for e_idx in range(2):
            edge_dpid = pod * 2 + e_idx + 1
            for h_idx in range(2):
                host_name = f"h{pod}_{e_idx * 2 + h_idx}"
                host_port = h_idx + 1  # ports 1,2
                link_id = f"e-{host_name}-s{edge_dpid}"
                link_map[f"{edge_dpid}_{host_port}"] = link_id

    return nodes, edges, link_map
