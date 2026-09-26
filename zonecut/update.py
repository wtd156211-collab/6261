"""变更执行：append 与 set-ts。

一批变更先在内存里演算（只读被改写那一块的行），全部合法才落盘：
落盘 = 就地改写动过的行 + 文件末尾补追加行 + 写回索引。
任何一步报错都不写文件。
"""

import json
from bisect import bisect_left

from . import ZonecutError
from .datafile import ROW_SIZE, format_row

_TS_LIMIT = 10 ** 12


def _check_ts(ts, what):
    if isinstance(ts, bool) or not isinstance(ts, int) or not 0 <= ts < _TS_LIMIT:
        raise ZonecutError(f"{what} 的 ts 不合法: {ts!r}")
    return ts


def _check_id(row_id, what):
    if isinstance(row_id, bool) or not isinstance(row_id, int) \
            or not 0 <= row_id < _TS_LIMIT:
        raise ZonecutError(f"{what} 的 id 不合法: {row_id!r}")
    return row_id


def parse_ops(path):
    ops = []
    with open(path, "r", encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            try:
                obj = json.loads(line)
            except ValueError:
                raise ZonecutError(f"变更文件第 {lineno} 行不是合法 JSON") from None
            if not isinstance(obj, dict):
                raise ZonecutError(f"变更文件第 {lineno} 行不是对象")
            op = obj.get("op")
            if op == "append":
                ops.append(("append",
                            _check_id(obj.get("id"), "append"),
                            _check_ts(obj.get("ts"), "append")))
            elif op == "set-ts":
                ops.append(("set-ts",
                            _check_id(obj.get("id"), "set-ts"),
                            _check_ts(obj.get("ts"), "set-ts")))
            else:
                raise ZonecutError(f"变更文件第 {lineno} 行 op 不支持: {op!r}")
    return ops


class Updater:
    def __init__(self, data_path, index):
        self.data_path = data_path
        self.index = index
        self.overlay = {}      # 已存在行的改写：id -> 新 ts
        self.appended = []     # 追加行：[(id, ts)]，id 递增
        self.row_pos = {}      # 已定位的已存在行：id -> 行号
        self.base_rows = index.num_rows  # 本批开始前的行数（即磁盘上的行数）

    # 行定位与读取（只读涉及的那一块）

    def _disk_block_rows(self, b):
        """块 b 当前在磁盘上的行（本批追加尚未落盘）。"""
        idx = self.index
        start = idx.pos[b]
        stop = min(start + idx.rows[b], self.base_rows)
        if stop <= start:
            return []
        with open(self.data_path, "rb") as f:
            f.seek(start * ROW_SIZE)
            chunk = f.read((stop - start) * ROW_SIZE)
        out = []
        for off in range(0, len(chunk), ROW_SIZE):
            line = chunk[off:off + ROW_SIZE]
            out.append((int(line[:12]), int(line[13:25])))
        return out

    def _find_row(self, row_id):
        """返回 (当前 ts, 是否本批追加的行)；行不存在返回 None。"""
        for i in range(len(self.appended) - 1, -1, -1):
            aid, ats = self.appended[i]
            if aid == row_id:
                return ats, True
            if aid < row_id:
                break
        idx = self.index
        b = row_id // idx.block_size
        if b >= idx.num_blocks:
            return None
        rows = self._disk_block_rows(b)
        ids = [r[0] for r in rows]
        i = bisect_left(ids, row_id)
        if i < len(ids) and ids[i] == row_id:
            self.row_pos[row_id] = idx.pos[b] + i
            return self.overlay.get(row_id, rows[i][1]), False
        return None

    def _block_values(self, b):
        """块 b 当前全部 ts（含改写与本批追加），<= S 个。"""
        overlay = self.overlay
        values = [overlay.get(rid, ts) for rid, ts in self._disk_block_rows(b)]
        s = self.index.block_size
        for aid, ats in self.appended:
            if aid // s == b:
                values.append(ats)
        return values

    # 变更

    def apply_append(self, row_id, ts):
        idx = self.index
        if idx.num_rows or self.appended:
            cur_max = self.appended[-1][0] if self.appended else idx.max_id
            if row_id <= cur_max:
                raise ZonecutError(
                    f"append 的 id 必须大于当前最大 id: {row_id} <= {cur_max}"
                )
        idx.append(row_id, ts)
        self.appended.append((row_id, ts))

    def apply_set_ts(self, row_id, new_ts):
        found = self._find_row(row_id)
        if found is None:
            raise ZonecutError(f"set-ts 的 id 不存在: {row_id}")
        old_ts, is_appended = found
        if old_ts == new_ts:
            return
        if is_appended:
            i = len(self.appended) - 1
            while self.appended[i][0] != row_id:
                i -= 1
            self.appended[i] = (row_id, new_ts)
        else:
            self.overlay[row_id] = new_ts
        b = row_id // self.index.block_size
        self.index.set_ts(row_id, old_ts, new_ts,
                          lambda: self._block_values(b))

    def apply(self, ops):
        for op, row_id, ts in ops:
            if op == "append":
                self.apply_append(row_id, ts)
            else:
                self.apply_set_ts(row_id, ts)

    # 落盘（全部变更合法之后才会走到这里）

    def flush(self, index_path):
        with open(self.data_path, "r+b") as f:
            for row_id, ts in self.overlay.items():
                f.seek(self.row_pos[row_id] * ROW_SIZE)
                f.write(format_row(row_id, ts))
            if self.appended:
                f.seek(self.base_rows * ROW_SIZE)
                for row_id, ts in self.appended:
                    f.write(format_row(row_id, ts))
        self.index.save(index_path)
