import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER, set_ev_cls
from ryu.ofproto import ofproto_v1_3
from controller.stats_mixin import StatsMixin
from controller.topology_manager import TopologyManager


class BaseBalancer(app_manager.RyuApp, StatsMixin):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super(BaseBalancer, self).__init__(*args, **kwargs)
        self.datapaths = {}
        self.configured_switches = set()
        self.topo = TopologyManager()
        self._build_static_topology()

    def _build_static_topology(self):
        """完全离线、常数级静态注入拓扑，杜绝运行期 LLDP 产生的 CPU 损耗"""
        for dpid in range(1, 21):
            self.topo.add_switch(dpid)
        # 建立 Edge (1-8) 与 Agg (9-16) 的静态边关联
        for pod in range(4):
            for e in range(2):
                e_dpid = pod * 2 + e + 1
                for a in range(2):
                    a_dpid = 8 + pod * 2 + a + 1
                    # 修正：Edge 的上行端口为 a + 3 (即 3, 4)，Agg 的下行端口为 e + 1 (即 1, 2)
                    self.topo.add_link(e_dpid, a + 3, a_dpid, e + 1)
        # 建立 Agg (9-16) 与 Core (17-20) 的静态边关联
        for pod in range(4):
            for a in range(2):
                a_dpid = 8 + pod * 2 + a + 1
                for c in range(2):
                    c_dpid = 16 + a * 2 + c + 1
                    self.topo.add_link(a_dpid, c + 3, c_dpid, pod + 1)

    def add_flow(self, datapath, priority, match, actions):
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        inst = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS, actions)]
        mod = parser.OFPFlowMod(
            datapath=datapath, priority=priority, match=match, instructions=inst
        )
        datapath.send_msg(mod)

    @set_ev_cls(ofp_event.EventOFPPortStatsReply, MAIN_DISPATCHER)
    def port_stats_reply_handler(self, ev):
        self.handle_port_stats_reply(ev)
