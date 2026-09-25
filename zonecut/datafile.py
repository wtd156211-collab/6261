"""数据文件的读写：26 字节定长文本行，12 位 id + 空格 + 12 位 ts + LF。

行按 id 严格递增，id 允许空洞；第 n 行的字节位置固定为 n * RECORD_SIZE。
读取一律按 RECORD_SIZE 的整数倍分块流式进行，整表不进内存。
"""

import re

from .errors import ZonecutError

RECORD_SIZE = 26
MAX_VALUE = 10**12  # id 与 ts 的取值上界（开区间）

_CHUNK_RECORDS = 8192
_RECORDS_RE = re.compile(rb"(?:\d{12} \d{12}\n)+")


def format_record(row_id, ts):
    """把一行编码成 26 字节。"""
    return b"%012d %012d\n" % (row_id, ts)


def parse_record(buf, pos):
    """从 buf 的 pos 处解析一行，返回 (id, ts)。调用方保证格式已校验。"""
    return int(buf[pos:pos + 12]), int(buf[pos + 13:pos + 25])


def validate_chunk(chunk):
    """校验一个按 RECORD_SIZE 对齐的数据块，格式不合法抛 ZonecutError。"""
    if not _RECORDS_RE.fullmatch(chunk):
        raise ZonecutError("数据文件格式不合法：存在非 26 字节定长记录的行")


def iter_records(fileobj, validate=True):
    """流式产出 (offset, id, ts)，offset 为该行在文件中的字节位置。"""
    offset = 0
    while True:
        chunk = fileobj.read(_CHUNK_RECORDS * RECORD_SIZE)
        if not chunk:
            return
        if len(chunk) % RECORD_SIZE:
            raise ZonecutError("数据文件长度不是 26 的整数倍")
        if validate:
            validate_chunk(chunk)
        for pos in range(0, len(chunk), RECORD_SIZE):
            row_id, ts = parse_record(chunk, pos)
            yield offset + pos, row_id, ts
        offset += len(chunk)


def read_block_rows(fileobj, start, end):
    """读出 [start, end) 字节区间内的全部行，返回 [(id, ts), ...]。"""
    fileobj.seek(start)
    buf = fileobj.read(end - start)
    if len(buf) != end - start or len(buf) % RECORD_SIZE:
        raise ZonecutError("数据文件与索引不一致：块区间越界")
    return [parse_record(buf, pos) for pos in range(0, len(buf), RECORD_SIZE)]
