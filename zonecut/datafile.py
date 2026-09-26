"""定长数据文件：12 位 id + 空格 + 12 位 ts + LF，共 26 字节一行。"""

from . import ZonecutError

ROW_SIZE = 26


def format_row(row_id, ts):
    return f"{row_id:012d} {ts:012d}\n".encode("ascii")


def parse_row(line, lineno):
    """解析并校验一行，返回 (id, ts)。"""
    if (
        len(line) != ROW_SIZE
        or line[12] != 0x20
        or line[25] != 0x0A
        or not line[:12].isdigit()
        or not line[13:25].isdigit()
    ):
        raise ZonecutError(f"数据文件第 {lineno} 行格式不合法")
    return int(line[:12]), int(line[13:25])


def check_data_size(path, num_rows):
    """廉价完整性检查：文件大小必须等于行数 × 26。"""
    import os

    size = os.path.getsize(path)
    if size != num_rows * ROW_SIZE:
        raise ZonecutError(
            f"数据文件大小 {size} 与索引行数 {num_rows} 不一致"
        )
