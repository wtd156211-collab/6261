"""查询执行与变更应用：剪枝、扫描、两阶段落盘。"""

import json

from .datafile import RECORD_SIZE, format_record, parse_record, read_block_rows
from .errors import ZonecutError
from .index import block_rows_reader, data_file_size

# 块状态：hit 块内有命中 / scanned 扫过没命中 / skip 被剪掉（含空块）
HIT = "hit"
SCANNED = "scanned"
SKIP = "skip"


class QueryResult:
    """单条查询的执行结果。

    hits 为 [(id, ts), ...]（按 id 升序）；states 为每块 HIT/SCANNED/SKIP，
    只有 want_states=True 时才收集（page 用），批量查询只留计数，
    避免每条查询都挂一份全块数的状态列表。
    """

    __slots__ = ("query_id", "hits", "states", "scanned", "nblocks", "hit_count")

    def __init__(self, query_id, hits, states, scanned, nblocks):
        self.query_id = query_id
        self.hits = hits
        self.states = states
        self.scanned = scanned    # 实际扫描的块数
        self.nblocks = nblocks
        self.hit_count = len(hits)

    @property
    def total(self):
        return self.nblocks

    @property
    def skipped(self):
        return self.nblocks - self.scanned


def run_query(data_file, index, query_id, cond, want_states=False):
    """对单个条件跑剪枝查询；data_file 为已打开的只读文件对象。"""
    hits = []
    states = [] if want_states else None
    scanned = 0
    rows_arr, mins, maxs, offs = index.rows, index.mins, index.maxs, index.offs
    for block in range(index.nblocks):
        if rows_arr[block] == 0 or cond.prune(mins[block], maxs[block]):
            if want_states:
                states.append(SKIP)
            continue
        scanned += 1
        data_file.seek(offs[block])
        buf = data_file.read(offs[block + 1] - offs[block])
        block_hit = False
        for pos in range(0, len(buf), RECORD_SIZE):
            ts = int(buf[pos + 13:pos + 25])
            if cond.match(ts):
                hits.append((int(buf[pos:pos + 12]), ts))
                block_hit = True
        if want_states:
            states.append(HIT if block_hit else SCANNED)
    return QueryResult(query_id, hits, states, scanned, index.nblocks)


def format_result(result):
    """单条查询的 stdout 段：# 头 + 命中行。"""
    out = ["# %s %d\n" % (result.query_id, len(result.hits))]
    for row_id, ts in result.hits:
        out.append("%d\t%d\n" % (row_id, ts))
    return "".join(out)


def format_report(index, results):
    """--report 报告：首行块容量/总块数/行数，其后一条查询一行五列。"""
    lines = ["# 块容量 %d 总块数 %d 行数 %d\n" % (
        index.block_size, index.nblocks, index.nrows)]
    for result in results:
        lines.append("%s\t%d\t%d\t%d\t%d\n" % (
            result.query_id, result.hit_count, result.scanned,
            result.skipped, result.total))
    return "".join(lines)


def parse_changes(path):
    """解析变更文件，返回 [(op, id, ts), ...]。"""
    changes = []
    try:
        with open(path, "r", encoding="utf-8") as fileobj:
            for lineno, line in enumerate(fileobj, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ZonecutError("变更文件第 %d 行不是合法 JSON" % lineno) from exc
                if not isinstance(obj, dict):
                    raise ZonecutError("变更文件第 %d 行不是 JSON 对象" % lineno)
                op = obj.get("op")
                row_id, ts = obj.get("id"), obj.get("ts")
                for name, value in (("id", row_id), ("ts", ts)):
                    if not isinstance(value, int) or isinstance(value, bool) \
                            or not 0 <= value < 10**12:
                        raise ZonecutError(
                            "变更文件第 %d 行字段 %s 缺失或越界" % (lineno, name))
                if op not in ("append", "set-ts"):
                    raise ZonecutError("变更文件第 %d 行 op 不支持: %r" % (lineno, op))
                changes.append((op, row_id, ts))
    except OSError as exc:
        raise ZonecutError("变更文件不可读: %s" % exc) from exc
    return changes


def apply_changes(data_path, index, changes):
    """按行序执行变更，全部校验通过后一次性落盘（数据文件 + 索引由调用方保存）。

    第一阶段只在内存中模拟：统计按规则增量更新，数据改动记入待写列表；
    任何一步不合法都抛 ZonecutError，此时磁盘原封不动。
    """
    overlay = {}          # id -> 新 ts（已存在行的待写改动）
    appends = []          # [(id, ts), ...] 待追加行
    appended = {}         # id -> ts（本次变更文件内追加的行）
    disk_size = data_file_size(data_path)  # 追加行尚未落盘，读取以此为界
    read_rows = block_rows_reader(data_path, index, overlay, disk_size)

    def read_current(block):
        rows = read_rows(block)
        if appends:
            lo = block * index.block_size
            hi = lo + index.block_size
            extra = [(rid, ts) for rid, ts in appends if lo <= rid < hi]
            if extra:
                rows = rows + extra
                rows.sort(key=lambda row: row[0])
        return rows

    for op, row_id, ts in changes:
        if op == "append":
            if row_id in appended:
                raise ZonecutError("追加的 id 不大于当前最大 id: %d" % row_id)
            index.append(row_id, ts)
            appends.append((row_id, ts))
            appended[row_id] = ts
        else:
            if row_id in appended:
                old_ts = appended[row_id]
                if old_ts == ts:
                    continue
                appended[row_id] = ts
                appends = [(rid, ts if rid == row_id else rts) for rid, rts in appends]
                block = row_id // index.block_size
                _update_stats_set_ts(index, block, row_id, old_ts, ts, read_current)
            else:
                # set-ts：先按当前 overlay 读旧值并更新统计，再登记新改动。
                _set_ts_existing(index, row_id, ts, overlay, read_current)

    # 第二阶段：落盘。追加行写在文件末尾，改写行就地覆盖。
    writes = []
    if appends:
        base = data_file_size(data_path)
        for i, (row_id, ts) in enumerate(appends):
            writes.append((base + i * RECORD_SIZE, format_record(row_id, ts)))
    if overlay:
        with open(data_path, "rb") as data_file:
            for row_id, ts in overlay.items():
                offset = _locate_row(data_file, index, row_id)
                writes.append((offset, format_record(row_id, ts)))
    try:
        with open(data_path, "r+b") as data_file:
            for offset, record in writes:
                data_file.seek(offset)
                data_file.write(record)
    except OSError as exc:
        raise ZonecutError("数据文件不可写: %s" % exc) from exc


def _set_ts_existing(index, row_id, new_ts, overlay, read_current):
    block = row_id // index.block_size
    if block >= index.nblocks or index.rows[block] == 0:
        raise ZonecutError("set-ts 的 id 不存在: %d" % row_id)
    rows = read_current(block)
    old_ts = None
    for rid, rts in rows:
        if rid == row_id:
            old_ts = rts
            break
    if old_ts is None:
        raise ZonecutError("set-ts 的 id 不存在: %d" % row_id)
    if old_ts == new_ts:
        return
    _update_stats_set_ts(index, block, row_id, old_ts, new_ts, read_current)
    overlay[row_id] = new_ts


def _update_stats_set_ts(index, block, row_id, old_ts, new_ts, read_current):
    """统计更新三规则：区间内不动；撑大改一侧；边界收回只重算这一块。"""
    mn, mx = index.mins[block], index.maxs[block]
    shrink = (old_ts == mn and new_ts > old_ts) or (old_ts == mx and new_ts < old_ts)
    if shrink:
        rows = read_current(block)
        values = [new_ts if rid == row_id else rts for rid, rts in rows]
        if len(values) <= 1:
            index.mins[block] = index.maxs[block] = new_ts
        else:
            index.mins[block] = min(values)
            index.maxs[block] = max(values)
    else:
        if new_ts < mn:
            index.mins[block] = new_ts
        if new_ts > mx:
            index.maxs[block] = new_ts


def _locate_row(data_file, index, row_id):
    """在 row_id 所在块内二分查找其字节位置（块内 id 严格递增）。"""
    block = row_id // index.block_size
    start, end = index.offs[block], index.offs[block + 1]
    data_file.seek(start)
    buf = data_file.read(end - start)
    lo, hi = 0, len(buf) // RECORD_SIZE
    while lo < hi:
        mid = (lo + hi) // 2
        pos = mid * RECORD_SIZE
        mid_id = int(buf[pos:pos + 12])
        if mid_id < row_id:
            lo = mid + 1
        elif mid_id > row_id:
            hi = mid
        else:
            return start + pos
    raise ZonecutError("set-ts 的 id 不存在: %d" % row_id)
