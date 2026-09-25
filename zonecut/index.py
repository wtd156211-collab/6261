"""块级统计索引：每块 rows/min_ts/max_ts 加块内首行在数据文件中的字节位置。

文件格式（小端、定长、无填充，写盘结果确定）：

    魔数 8 字节 "ZCUTIDX1"
    S u32 | 块数 B u32 | 行数 N u64 | 最大 id u64
    rows[B] u32 | mins[B] u64 | maxs[B] u64 | offs[B+1] u64

块 b 覆盖 id ∈ [b*S, (b+1)*S-1]，其数据行 = 数据文件 [offs[b], offs[b+1])；
空块 rows == 0 且 offs[b] == offs[b+1]。offs[B] 为数据文件长度。
"""

import os
import struct
from array import array

from .datafile import RECORD_SIZE, iter_records, read_block_rows
from .errors import ZonecutError

MAGIC = b"ZCUTIDX1"
_HEADER = struct.Struct("<8sIIQQ")
_RECORD_ARRAYS = (("rows", "I"), ("mins", "Q"), ("maxs", "Q"))


def _u32():
    return array("I") if array("I").itemsize == 4 else array("L")


class BlockIndex:
    """内存中的块统计。offs 长度恒为 nblocks + 1，offs[-1] 为数据文件长度。"""

    __slots__ = ("block_size", "nrows", "max_id", "rows", "mins", "maxs", "offs")

    def __init__(self, block_size):
        if not isinstance(block_size, int) or block_size < 1:
            raise ZonecutError("块容量必须是 >= 1 的整数")
        self.block_size = block_size
        self.nrows = 0
        self.max_id = -1
        self.rows = _u32()
        self.mins = array("Q")
        self.maxs = array("Q")
        self.offs = array("Q", [0])

    @property
    def nblocks(self):
        return len(self.rows)

    # ---- 建索引 ----------------------------------------------------------

    @classmethod
    def build(cls, data_path, block_size):
        """顺序读一遍数据文件，逐行累计所在块的统计，空洞块号登记为空块。"""
        index = cls(block_size)
        last_id = -1
        try:
            with open(data_path, "rb") as fileobj:
                for offset, row_id, ts in iter_records(fileobj, validate=True):
                    if row_id <= last_id:
                        raise ZonecutError(
                            "数据文件 id 未严格递增: %d 出现在 %d 之后" % (row_id, last_id))
                    last_id = row_id
                    index._append_row(row_id, ts, offset)
        except OSError as exc:
            raise ZonecutError("数据文件不可读: %s" % exc) from exc
        return index

    def _append_row(self, row_id, ts, offset):
        """登记一行（id 严格递增、位于文件 offset 处）。建索引与追加共用。"""
        block = row_id // self.block_size
        if block >= self.nblocks:
            # 跨到新块号：中间块号登记成空块，offs 指向当前文件末尾。
            for _ in range(block - self.nblocks):
                self.rows.append(0)
                self.mins.append(0)
                self.maxs.append(0)
                self.offs.insert(len(self.offs) - 1, offset)
            self.rows.append(1)
            self.mins.append(ts)
            self.maxs.append(ts)
            self.offs.insert(len(self.offs) - 1, offset)
        else:
            self.rows[block] += 1
            if ts < self.mins[block]:
                self.mins[block] = ts
            if ts > self.maxs[block]:
                self.maxs[block] = ts
        self.offs[-1] = offset + RECORD_SIZE
        self.nrows += 1
        self.max_id = row_id

    # ---- 变更 ------------------------------------------------------------

    def append(self, row_id, ts):
        """追加一行：id 必须大于当前最大 id。返回该行的文件 offset。"""
        if row_id <= self.max_id:
            raise ZonecutError(
                "追加的 id %d 不大于当前最大 id %d" % (row_id, self.max_id))
        offset = self.offs[-1]
        self._append_row(row_id, ts, offset)
        return offset

    # ---- 读写索引文件 ----------------------------------------------------

    def save(self, index_path):
        body = [self.rows.tobytes(), self.mins.tobytes(),
                self.maxs.tobytes(), self.offs.tobytes()]
        header = _HEADER.pack(MAGIC, self.block_size, self.nblocks,
                              self.nrows, max(self.max_id, 0))
        try:
            with open(index_path, "wb") as fileobj:
                fileobj.write(header)
                for part in body:
                    fileobj.write(part)
        except OSError as exc:
            raise ZonecutError("索引文件不可写: %s" % exc) from exc

    @classmethod
    def load(cls, index_path):
        try:
            with open(index_path, "rb") as fileobj:
                blob = fileobj.read()
        except OSError as exc:
            raise ZonecutError("索引文件不可读: %s" % exc) from exc
        if len(blob) < _HEADER.size:
            raise ZonecutError("索引文件格式不合法：文件过短")
        magic, block_size, nblocks, nrows, max_id = _HEADER.unpack_from(blob)
        if magic != MAGIC:
            raise ZonecutError("索引文件格式不合法：魔数不匹配")
        index = cls.__new__(cls)
        index.block_size = block_size
        index.nrows = nrows
        index.max_id = max_id if nblocks else -1
        pos = _HEADER.size
        for name, typecode in _RECORD_ARRAYS:
            arr = array(typecode)
            size = nblocks * arr.itemsize
            if pos + size > len(blob):
                raise ZonecutError("索引文件格式不合法：%s 段截断" % name)
            arr.frombytes(blob[pos:pos + size])
            setattr(index, name, arr)
            pos += size
        offs = array("Q")
        size = (nblocks + 1) * offs.itemsize
        if pos + size != len(blob):
            raise ZonecutError("索引文件格式不合法：offs 段长度不符")
        offs.frombytes(blob[pos:pos + size])
        index.offs = offs
        if block_size < 1:
            raise ZonecutError("索引文件格式不合法：块容量为 0")
        return index


def block_rows_reader(data_path, index, overlay=None, disk_size=None):
    """返回 read_rows(block)：只读指定块的数据行，overlay 可叠加未落盘的改动。

    overlay 为 {id: ts}，用于 update 两阶段提交期间读取"当前"块内容；
    disk_size 把读取区间钳在已落盘范围内（追加行可能还没写入文件）。
    """
    if overlay is None:
        overlay = {}

    def read_rows(block):
        start = index.offs[block]
        end = index.offs[block + 1]
        if disk_size is not None:
            if start >= disk_size:
                return []
            end = min(end, disk_size)
        try:
            with open(data_path, "rb") as fileobj:
                rows = read_block_rows(fileobj, start, end)
        except OSError as exc:
            raise ZonecutError("数据文件不可读: %s" % exc) from exc
        if overlay:
            rows = [(rid, overlay.get(rid, rts)) for rid, rts in rows]
        return rows

    return read_rows


def data_file_size(data_path):
    try:
        return os.path.getsize(data_path)
    except OSError as exc:
        raise ZonecutError("数据文件不可读: %s" % exc) from exc
