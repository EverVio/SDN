import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER, set_ev_cls
from ryu.controller import ofp_event
from ryu.ofproto import ofproto_v1_3
from ryu.lib import hub
from controller.base_balancer import BaseBalancer
from controller.weight_engine import DynamicWeightEngine


class PredictiveBalancer(BaseBalancer):
    def __init__(self, *args, **kwargs):
        super(PredictiveBalancer, self).__init__(*args, **kwargs)
        self.init_stats()
        model_path = os.path.join(
            os.path.dirname(__file__), "..", "models", "global_mlp_model.pkl"
        )
        self.weight_engine = DynamicWeightEngine(model_path=model_path)
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
        WEIGHT_DEADBAND = 0.15
        last_ratios = {}

        while True:
            hub.sleep(self.POLL_INTERVAL)
            if not self.datapaths:
                continue

            self.weight_engine.update_all_utilizations(self.link_utilization)
            self.weight_engine.predict_all()

            # 完美兼容原 weight_engine 的矩阵数学预测决策，直接映射回 Group Table Bucket 调权
            group_weights = self.weight_engine.get_group_weights(self.topo)
            for agg_dpid, port_weight_pairs in group_weights.items():
                if agg_dpid not in self.datapaths:
                    continue
                dp = self.datapaths[agg_dpid]
                buckets = []
                for port_no, weight in port_weight_pairs:
                    buckets.append(
                        dp.ofproto_parser.OFPBucket(
                            weight=weight,
                            watch_port=port_no,
                            watch_group=dp.ofproto.OFPG_ANY,
                            actions=[dp.ofproto_parser.OFPActionOutput(port_no)],
                        )
                    )
                dp.send_msg(
                    dp.ofproto_parser.OFPGroupMod(
                        datapath=dp,
                        command=dp.ofproto.OFPGC_MODIFY,
                        type_=dp.ofproto.OFPGT_SELECT,
                        group_id=1,
                        buckets=buckets,
                    )
                )
