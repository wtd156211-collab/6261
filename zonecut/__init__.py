"""zonecut：单表单列（ts）的块级统计与查询剪枝。

只依赖标准库。数据文件为 26 字节定长文本行（12 位 id + 空格 + 12 位 ts + LF），
索引按块记录 rows/min_ts/max_ts 与块内首行在数据文件中的字节位置。
"""

__version__ = "1.0.0"

DEFAULT_BLOCK_SIZE = 256
