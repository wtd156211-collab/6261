"""块级统计索引。

块按 id 切：块号 b = id // S，块 b 覆盖 id ∈ [b·S, (b+1)·S−1]。
每块维护 rows / min_ts / max_ts（空块 min/max 为 0，empty 即 rows == 0），
外加 pos —— 这一块第一行在数据文件里的行号（行留盘，整表不进内存）。

索引文件（二进制，字节序小端）：
    [0:4]   magic b"ZC01"
    [4:36]  4 × uint64：块容量 S、块数 B、行数 N、最大 id（空表为 0）
    其后    B × uint32 rows，B × uint64 pos，B × uint64 min_ts，B × uint64 max_ts
"""

import struct
from array import array

from . import ZonecutError
from .datafile import ROW_SIZE, parse_row

_MAGIC = b"ZC01"
_HEADER = struct.Struct("<4s4Q")

if array("I").itemsize != 4 or array("Q").itemsize != 8:  # pragma: no cover
    raise RuntimeError("需要 32 位 unsigned int 与 64 位 unsigned long long")


class Index:
    __slots__ = ("block_size", "rows", "pos", "min_ts", "max_ts",
                 "num_rows", "max_id")

    def __init__(self, block_size, rows, pos, min_ts, max_ts, num_rows, max_id):
        self.block_size = block_size
        self.rows = rows          # array('I')
        self.pos = pos            # array('Q')
        self.min_ts = min_ts      # array('Q')
        self.max_ts = max_ts      # array('Q')
        self.num_rows = num_rows
        self.max_id = max_id      # 空表为 None

    @property
    def num_blocks(self):
        return len(self.rows)

    # ---- 建索引：顺序读一遍数据文件 ----

    @classmethod
    def build(cls, data_path, block_size):
        if block_size < 1:
            raise ZonecutError(f"块容量必须 >= 1: {block_size}")
        rows = array("I")
        pos = array("Q")
        min_ts = array("Q")
        max_ts = array("Q")
        num_rows = 0
        max_id = None
        prev_id = -1
        cur_block = -1
        with open(data_path, "rb") as f:
            for lineno, line in enumerate(f, 1):
                row_id, ts = parse_row(line, lineno)
                if row_id <= prev_id:
                    raise ZonecutError(
                        f"数据文件第 {lineno} 行 id 不递增: {row_id}"
                    )
                prev_id = row_id
                b = row_id // block_size
                while cur_block < b:
                    # 新块（含空洞登记成的空块）从当前行号位置开始
                    cur_block += 1
                    rows.append(0)
                    pos.append(num_rows)
                    min_ts.append(0)
                    max_ts.append(0)
                if rows[cur_block] == 0:
                    min_ts[cur_block] = ts
                    max_ts[cur_block] = ts
                else:
                    if ts < min_ts[cur_block]:
                        min_ts[cur_block] = ts
                    if ts > max_ts[cur_block]:
                        max_ts[cur_block] = ts
                rows[cur_block] += 1
                num_rows += 1
                max_id = row_id
        return cls(block_size, rows, pos, min_ts, max_ts, num_rows, max_id)

    # ---- 读写索引文件 ----

    @classmethod
    def load(cls, index_path):
        with open(index_path, "rb") as f:
            buf = f.read()
        if len(buf) < _HEADER.size or buf[:4] != _MAGIC:
            raise ZonecutError(f"索引文件格式不合法: {index_path}")
        _, block_size, num_blocks, num_rows, max_id = _HEADER.unpack_from(buf)
        if block_size < 1:
            raise ZonecutError(f"索引块容量不合法: {block_size}")
        expect = _HEADER.size + num_blocks * (4 + 8 + 8 + 8)
        if len(buf) != expect:
            raise ZonecutError(f"索引文件长度不合法: {index_path}")
        off = _HEADER.size
        rows = array("I", buf[off:off + 4 * num_blocks])
        off += 4 * num_blocks
        pos = array("Q", buf[off:off + 8 * num_blocks])
        off += 8 * num_blocks
        min_ts = array("Q", buf[off:off + 8 * num_blocks])
        off += 8 * num_blocks
        max_ts = array("Q", buf[off:off + 8 * num_blocks])
        return cls(block_size, rows, pos, min_ts, max_ts,
                   num_rows, max_id if num_rows else None)

    def save(self, index_path):
        out = bytearray()
        out += _HEADER.pack(_MAGIC, self.block_size, self.num_blocks,
                            self.num_rows, self.max_id or 0)
        out += self.rows.tobytes()
        out += self.pos.tobytes()
        out += self.min_ts.tobytes()
        out += self.max_ts.tobytes()
        with open(index_path, "wb") as f:
            f.write(out)

    # ---- 增量更新（不重扫全表、不重建索引） ----

    def append(self, row_id, ts):
        """登记一行追加；调用方保证 id > 当前最大 id。"""
        b = row_id // self.block_size
        while len(self.rows) <= b:
            # 跨到新块号时，中间块号登记成空块
            self.rows.append(0)
            self.pos.append(self.num_rows)
            self.min_ts.append(0)
            self.max_ts.append(0)
        if self.rows[b] == 0:
            self.min_ts[b] = ts
            self.max_ts[b] = ts
        else:
            if ts < self.min_ts[b]:
                self.min_ts[b] = ts
            if ts > self.max_ts[b]:
                self.max_ts[b] = ts
        self.rows[b] += 1
        self.num_rows += 1
        self.max_id = row_id

    def set_ts(self, row_id, old_ts, new_ts, block_values):
        """按规则更新一块的统计。

        block_values: 无参回调，返回该块当前的全部 ts（含新值），
        只在边界被收回需要重算这一块时调用（≤ S 行）。
        """
        b = row_id // self.block_size
        if self.rows[b] == 1:
            self.min_ts[b] = new_ts
            self.max_ts[b] = new_ts
            return
        m = self.min_ts[b]
        M = self.max_ts[b]
        shrink = (old_ts == m and new_ts > old_ts) or \
                 (old_ts == M and new_ts < old_ts)
        if shrink:
            values = block_values()
            self.min_ts[b] = min(values)
            self.max_ts[b] = max(values)
        else:
            if new_ts < m:
                self.min_ts[b] = new_ts
            if new_ts > M:
                self.max_ts[b] = new_ts
