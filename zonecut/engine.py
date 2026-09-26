"""查询引擎：先按块统计剪枝，只扫剩下的块。"""

import json

from . import ZonecutError
from .conditions import compile_match, compile_prune, parse_condition
from .datafile import ROW_SIZE


def load_queries(path):
    """读查询文件，返回 [(查询 id, 条件树)]，保持文件行序。"""
    queries = []
    with open(path, "r", encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            try:
                obj = json.loads(line)
            except ValueError:
                raise ZonecutError(f"查询文件第 {lineno} 行不是合法 JSON") from None
            if not isinstance(obj, dict):
                raise ZonecutError(f"查询文件第 {lineno} 行不是对象")
            qid = obj.get("id")
            if not isinstance(qid, str) or not qid:
                raise ZonecutError(f"查询文件第 {lineno} 行 id 不合法")
            try:
                cond = parse_condition(obj)
            except ZonecutError as e:
                raise ZonecutError(f"查询 {qid}: {e}") from None
            queries.append((qid, cond))
    return queries


def run_query(data_file, index, cond, collect_states=False):
    """跑一条查询。

    返回 (hits, scanned, states)：
        hits    —— [(id, ts)]，按 id 升序（块号升序 + 块内行序天然保证）
        scanned —— 扫描块数（剪不掉的块，逐行判定）
        states  —— collect_states 时每块一项
                   (state, rows, min_ts, max_ts, empty)，否则为 None
    """
    prune = compile_prune(cond)
    match = compile_match(cond)
    hits = []
    scanned = 0
    states = [] if collect_states else None
    rows_a = index.rows
    pos_a = index.pos
    min_a = index.min_ts
    max_a = index.max_ts
    for b in range(len(rows_a)):
        n = rows_a[b]
        if n == 0:
            if collect_states:
                states.append(("skip", 0, 0, 0, 1))
            continue
        m = min_a[b]
        M = max_a[b]
        if prune(m, M):
            if collect_states:
                states.append(("skip", n, m, M, 0))
            continue
        scanned += 1
        data_file.seek(pos_a[b] * ROW_SIZE)
        chunk = data_file.read(n * ROW_SIZE)
        block_hits = 0
        for off in range(0, len(chunk), ROW_SIZE):
            ts = int(chunk[off + 13:off + 25])
            if match(ts):
                hits.append((int(chunk[off:off + 12]), ts))
                block_hits += 1
        if collect_states:
            states.append(("hit" if block_hits else "scanned", n, m, M, 0))
    return hits, scanned, states


def format_result(qid, hits):
    """stdout 的一段：先 `# <查询 id> <命中行数>`，再逐行 `<id>\t<ts>`。"""
    parts = [f"# {qid} {len(hits)}\n"]
    parts.extend(f"{row_id}\t{ts}\n" for row_id, ts in hits)
    return "".join(parts)


def report_header(index):
    return (f"# 块容量 {index.block_size} 总块数 {index.num_blocks} "
            f"行数 {index.num_rows}\n")


def report_line(qid, hits_count, scanned, total_blocks):
    return f"{qid}\t{hits_count}\t{scanned}\t{total_blocks - scanned}\t{total_blocks}\n"
