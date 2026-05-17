import time
from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER
from ryu.controller.handler import set_ev_cls
from ryu.ofproto import ofproto_v1_3
from ryu.lib.packet import packet, ethernet, ether_types

"""
app_manager: Ryu 应用管理器，提供 RyuApp 基类
ofp_event: OpenFlow 事件类的集合
CONFIG_DISPATCHER, MAIN_DISPATCHER: 处理器状态常量
set_ev_cls: 事件处理装饰器
ofproto_v1_3: OpenFlow 1.3 协议常量
packet, ethernet, ether_types: 数据包解析库
"""


class L2LearningSwitch(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # MAC 地址表: {dpid: {mac_addr: port_no}}
        # 例如: {1: {'00:00:00:00:00:01': 1, '00:00:00:00:00:03': 3}}
        self.mac_to_port = {}

        # 广播风暴缓存：{(dpid, src_mac, eth_type): timestamp}
        self.broadcast_cache = {}
        # 缓存最大容量，防止内存泄漏
        self.cache_limit = 1000

    # @装饰器：让方法变成一个自动调用的事件处理器
    # set_ev_cls 接收两个参数： 1.事件类型  2.调度器状态
    # EventOFPSwitchFeatures 事件：有一台交换机把基本信息（ID、端口数等）发送给了 Ryu
    # CONFIG_DISPATCHER	状态：握手完毕，刚收到交换机信息，但还没开始正常转发
    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        # ev：Ryu 传递给这个函数的事件对象
        # ev.msg 是原始的 OpenFlow 消息对象

        datapath = ev.msg.datapath  # 代表这台刚连上来的交换机
        ofproto = datapath.ofproto  # 协议常量模块
        parser = datapath.ofproto_parser  # 消息构造器模块

        match = parser.OFPMatch()  # 创建空匹配（匹配所有数据包）

        # 把匹配到的包完整的送到 Ryu 控制器那里去（不进行缓存）
        actions = [
            parser.OFPActionOutput(ofproto.OFPP_CONTROLLER, ofproto.OFPCML_NO_BUFFER)
        ]

        # 下发一条优先级为 0 （最低）的流表规则
        self.add_flow(datapath, 0, match, actions)
        self.logger.info("Switch %s connected, table-miss installed", datapath.id)

    def add_flow(self, datapath, priority, match, actions, buffer_id=None):
        """向交换机添加一条流表规则"""

        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

        # 创建指令：APPLY_ACTIONS 表示立即执行动作
        inst = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS, actions)]

        kwargs = dict(
            datapath=datapath,
            priority=priority,
            match=match,
            instructions=inst,
        )

        # 如果有 buffer_id，加上它（避免重传已缓存的包）
        if buffer_id is not None:
            kwargs["buffer_id"] = buffer_id

        mod = parser.OFPFlowMod(**kwargs)
        datapath.send_msg(mod)

    # MAIN_DISPATCHER 状态：交换机处于正常转发状态
    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def packet_in_handler(self, ev):
        """收到 Packet-In 时：学习 MAC 地址，转发数据包"""
        msg = ev.msg
        datapath = msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        in_port = msg.match["in_port"]  # 消息进入交换机的物理端口

        # 解析数据包
        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocol(ethernet.ethernet)

        # 过滤 LLDP（SDN 控制器用来拓扑发现的，不需要学习 MAC）
        # 和 IPv6（消除 Mininet 主机初始化时的 IPv6 组播风暴）
        if eth.ethertype in (ether_types.ETH_TYPE_LLDP, ether_types.ETH_TYPE_IPV6):
            return

        dst = eth.dst  # 目的 MAC 地址
        src = eth.src  # 源 MAC 地址
        dpid = datapath.id  # 交换机 ID

        # ====== 泛洪风暴抑制 ======
        # 拦截目标 MAC 为全 F 的广播包
        if dst == "ff:ff:ff:ff:ff:ff":
            cache_key = (dpid, src, eth.ethertype)
            now = time.time()

            # 定期清理缓存，防止内存泄漏
            if len(self.broadcast_cache) > self.cache_limit:
                self.broadcast_cache.clear()

            if cache_key in self.broadcast_cache:
                # 0.5 秒时间窗拦截重复广播包
                if now - self.broadcast_cache[cache_key] < 0.5:
                    return

            self.broadcast_cache[cache_key] = now

        # 初始化该交换机的 MAC 表
        self.mac_to_port.setdefault(dpid, {})

        # MAC 锁定：防止由于环路传回的包导致端口映射漂移
        if src not in self.mac_to_port[dpid]:
            # ====== MAC 地址学习 ======
            # 记录：从 in_port 进来的包，源地址是 src
            # 以后要发往 src 的包，从 in_port 出去就行了
            self.mac_to_port[dpid][src] = in_port
            self.logger.info("Switch %s: learn %s on port %d", dpid, src, in_port)

        # ====== 查找目的端口 ======
        if dst in self.mac_to_port[dpid]:
            # 已知目的端口
            out_port = self.mac_to_port[dpid][dst]
        else:
            # 未知目的，泛洪
            # OFPP_FLOOD 在 OpenFlow 中代表：
            # 从所有正常端口（除了入端口）发送出去，类似传统交换机的泛洪行为
            out_port = ofproto.OFPP_FLOOD

        # 构造输出动作
        actions = [parser.OFPActionOutput(out_port)]

        # ====== 下发流表规则（避免后续同类型包再触发 Packet-In）======
        if out_port != ofproto.OFPP_FLOOD:
            match = parser.OFPMatch(eth_dst=dst, eth_src=src)

            # 如果交换机缓存了这个包，用 buffer_id 下发规则
            # 交换机会自动处理缓存的包，不需要 Packet-Out
            if msg.buffer_id != ofproto.OFP_NO_BUFFER:
                self.add_flow(datapath, 1, match, actions, msg.buffer_id)
                return  # 包已经被交换机处理，直接返回
            else:
                self.add_flow(datapath, 1, match, actions)

        # ====== Packet-Out：发送当前数据包 ======
        # 如果是泛洪，或者没有 buffer_id，需要用 Packet-Out 手动发送

        # 如果交换机没缓存包，我们必须把原包数据完整的通过 Packet-Out 发回去，否则这个包就真的丢了。
        # 如果交换机已缓存（buffer_id 有效），我们传 data=None 即可，交换机知道去缓冲区拿。
        data = msg.data if msg.buffer_id == ofproto.OFP_NO_BUFFER else None
        out = parser.OFPPacketOut(
            datapath=datapath,
            buffer_id=msg.buffer_id,
            in_port=in_port,
            actions=actions,
            data=data,
        )

        datapath.send_msg(out)
