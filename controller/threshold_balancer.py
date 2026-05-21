import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER, set_ev_cls
from ryu.controller import ofp_event
from ryu.ofproto import ofproto_v1_3
from ryu.lib import hub
from controller.base_balancer import BaseBalancer


class ThresholdBalancer(BaseBalancer):
    def __init__(self, *args, **kwargs):
        super(ThresholdBalancer, self).__init__(*args, **kwargs)
        self.init_stats()
        from controller.weight_engine import DynamicWeightEngine

        self.weight_engine = DynamicWeightEngine(model_path=None)
        # 记录前一状态是否处于拥塞模式
        self._was_congested = False
        self.decision_thread = hub.spawn(self._decision_loop)

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        self._setup_rules(ev.msg.datapath)

    @set_ev_cls(ofp_event.EventOFPStateChange, MAIN_DISPATCHER)
    def state_change_handler(self, ev):
        if ev.state == MAIN_DISPATCHER:
            self._setup_rules(ev.datapath)

    def _setup_rules(self, datapath):
        dpid = datapath.id
        if dpid in self.configured_switches:
            return
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        self.datapaths[dpid] = datapath

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
            self.add_flow(datapath, 10, match, actions)
        self.configured_switches.add(dpid)

    def _decision_loop(self):
        CONGESTION_THRESHOLD = 0.70

        while True:
            hub.sleep(self.POLL_INTERVAL)
            if not self.datapaths:
                continue

            self.weight_engine.update_all_utilizations(self.link_utilization)

            is_congested = any(
                util > CONGESTION_THRESHOLD for util in self.link_utilization.values()
            )
            group_weights = {}

            if is_congested:
                # 触发动态响应
                group_weights = self.weight_engine.get_group_weights(self.topo)
                self._was_congested = True
            else:
                # 仅在发生状态切换时，才恢复 50:50 对称分流
                if self._was_congested:
                    group_weights = {dpid: [(3, 50), (4, 50)] for dpid in range(9, 17)}
                    self._was_congested = False

            for dpid, weights in group_weights.items():
                if dpid in self.datapaths:
                    ofproto = self.datapaths[dpid].ofproto
                    parser = self.datapaths[dpid].ofproto_parser
                    buckets = [
                        parser.OFPBucket(
                            weight=w,
                            watch_port=p,
                            watch_group=ofproto.OFPG_ANY,
                            actions=[parser.OFPActionOutput(p)],
                        )
                        for p, w in weights
                    ]
                    msg = parser.OFPGroupMod(
                        datapath=self.datapaths[dpid],
                        command=ofproto.OFPGC_MODIFY,
                        type_=ofproto.OFPGT_SELECT,
                        group_id=1,
                        buckets=buckets,
                    )
                    self.datapaths[dpid].send_msg(msg)
