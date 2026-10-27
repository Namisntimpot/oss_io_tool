"""Compatibility alias sharing the oss_io_tool module and IO session."""
import sys
import oss_io_tool
from oss_io_tool import index, publisher
sys.modules[__name__ + ".index"] = index
sys.modules[__name__ + ".publisher"] = publisher
sys.modules[__name__] = oss_io_tool
