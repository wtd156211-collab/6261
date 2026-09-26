"""zonecut: 单表单列的块级统计与查询剪枝。"""

__version__ = "1.0.0"

DEFAULT_BLOCK_SIZE = 256


class ZonecutError(Exception):
    """输入不可用（对应退出码 1）。"""
