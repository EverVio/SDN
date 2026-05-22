import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from controller.base_balancer import BaseBalancer


class BaseECMPController(BaseBalancer):
    def __init__(self, *args, **kwargs):
        super(BaseECMPController, self).__init__(*args, **kwargs)
        self.init_stats()
