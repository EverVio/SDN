import os
import sys

# 将父目录加入 sys.path 以便正确导入 controller 包
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER, set_ev_cls
from ryu.ofproto import ofproto_v1_3
from controller.stats_mixin import StatsMixin


class L2LearningSwitch(app_manager.RyuApp, StatsMixin):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super(L2LearningSwitch, self).__init__(*args, **kwargs)
        self.configured_switches = set()
        # 初始化 StatsMixin 的统计数据与定时监测线程
        self.init_stats()

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        self._setup_rules(ev.msg.datapath)

    @set_ev_cls(ofp_event.EventOFPStateChange, MAIN_DISPATCHER)
    def state_change_handler(self, ev):
        if ev.state == MAIN_DISPATCHER:
            self._setup_rules(ev.datapath)

    def _setup_rules(self, datapath):
        dpid = datapath.id
        # 将当前的 datapath 对象存入 StatsMixin 所需的字典中以供定时轮询
        self.datapaths[dpid] = datapath

        if dpid in self.configured_switches:
            return
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

        if dpid <= 16:
            buckets = []
            for port in [3, 4]:
                buckets.append(
                    parser.OFPBucket(
                        weight=50,
                        watch_port=port,
                        watch_group=ofproto.OFPG_ANY,
                        actions=[parser.OFPActionOutput(port)],
                    )
                )
            datapath.send_msg(
                parser.OFPGroupMod(
                    datapath=datapath,
                    command=ofproto.OFPGC_ADD,
                    type_=ofproto.OFPGT_SELECT,
                    group_id=1,
                    buckets=buckets,
                )
            )

        for i in range(16):
            match = parser.OFPMatch(eth_dst=f"00:00:00:00:00:{i+1:02x}")
            pod, e_idx = i // 4, (i % 4) // 2
            if dpid <= 8:
                actions = (
                    [parser.OFPActionOutput((i % 2) + 1)]
                    if (dpid - 1) // 2 == pod and (dpid - 1) % 2 == e_idx
                    else [parser.OFPActionGroup(group_id=1)]
                )
            elif dpid <= 16:
                actions = (
                    [parser.OFPActionOutput(e_idx + 1)]
                    if (dpid - 9) // 2 == pod
                    else [parser.OFPActionGroup(group_id=1)]
                )
            else:
                actions = [parser.OFPActionOutput(pod + 1)]

            inst = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS, actions)]
            datapath.send_msg(
                parser.OFPFlowMod(
                    datapath=datapath, priority=10, match=match, instructions=inst
                )
            )
        self.configured_switches.add(dpid)

    @set_ev_cls(ofp_event.EventOFPPortStatsReply, MAIN_DISPATCHER)
    def port_stats_reply_handler(self, ev):
        """接收交换机的端口流量统计响应，并交由 StatsMixin 处理与写入 CSV"""
        self.handle_port_stats_reply(ev)
