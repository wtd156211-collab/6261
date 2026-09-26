"""变更规则：统计跟着追加/改写走，不许漏行也不许多扫。"""

import json
import pathlib
import random
import unittest

from zonecut.index import Index
from tests.common import (TempDirTestCase, naive_scan, read_rows, run_cli,
                          stats_from_data)


def write_data(path, rows):
    with open(path, "w", encoding="ascii", newline="") as f:
        for row_id, ts in rows:
            f.write(f"{row_id:012d} {ts:012d}\n")


def write_ops(path, ops):
    with open(path, "w", encoding="utf-8", newline="") as f:
        for op in ops:
            f.write(json.dumps(op) + "\n")


class UpdateBase(TempDirTestCase):
    BLOCK = 8

    def make_table(self, rows):
        data = self.path("t.txt")
        write_data(data, rows)
        idx = self.build(data, block_size=self.BLOCK)
        return data, idx

    def apply_ops(self, data, idx, ops, name="ops.jsonl"):
        ops_path = self.path(name)
        write_ops(ops_path, ops)
        code, _, err = run_cli("update", data, idx, ops_path)
        self.assertEqual(code, 0, err.decode())

    def assert_stats_fresh(self, data, idx):
        """索引统计 == 对数据文件独立重算的统计（防漏行、防不收缩）。"""
        index = Index.load(idx)
        rows = read_rows(data)
        self.assertEqual(index.num_rows, len(rows))
        self.assertEqual(index.max_id, rows[-1][0] if rows else None)
        stats = stats_from_data(data, index.block_size)
        max_block = (rows[-1][0] // index.block_size) if rows else -1
        self.assertEqual(index.num_blocks, max_block + 1)
        for b in range(index.num_blocks):
            if b in stats:
                n, m, M = stats[b]
                self.assertEqual((index.rows[b], index.min_ts[b],
                                  index.max_ts[b]), (n, m, M),
                                 f"块 {b} 统计过期")
            else:
                self.assertEqual(index.rows[b], 0, f"空块 {b} 未登记")


class TestAppendRules(UpdateBase):
    def test_append_same_block(self):
        data, idx = self.make_table([(i, 100 + i) for i in range(8)])
        self.apply_ops(data, idx, [{"op": "append", "id": 8, "ts": 50}])
        index = Index.load(idx)
        self.assertEqual((index.rows[1], index.min_ts[1], index.max_ts[1]),
                         (1, 50, 50))
        self.assert_stats_fresh(data, idx)

    def test_append_registers_empty_blocks_in_gap(self):
        data, idx = self.make_table([(i, 100 + i) for i in range(8)])
        # id 25 落在块 3，中间的块 1、2 必须登记成空块
        self.apply_ops(data, idx, [{"op": "append", "id": 25, "ts": 700}])
        index = Index.load(idx)
        self.assertEqual(index.num_blocks, 4)
        self.assertEqual((index.rows[1], index.rows[2]), (0, 0))
        self.assertEqual((index.rows[3], index.min_ts[3], index.max_ts[3]),
                         (1, 700, 700))
        self.assert_stats_fresh(data, idx)

    def test_append_into_existing_empty_block(self):
        # id 有空洞：块 1 原本就是空块，追加落进去要从空变非空
        data, idx = self.make_table([(i, 100 + i) for i in range(8)]
                                    + [(16, 500), (17, 510)])
        self.apply_ops(data, idx, [{"op": "append", "id": 18, "ts": 90}])
        index = Index.load(idx)
        self.assertEqual((index.rows[2], index.min_ts[2], index.max_ts[2]),
                         (3, 90, 510))
        self.assert_stats_fresh(data, idx)

    def test_append_rejects_non_increasing_id(self):
        data, idx = self.make_table([(i, 100 + i) for i in range(8)])
        before_data = pathlib.Path(data).read_bytes()
        before_idx = pathlib.Path(idx).read_bytes()
        ops = self.path("bad.jsonl")
        write_ops(ops, [{"op": "append", "id": 7, "ts": 1}])
        code, _, _ = run_cli("update", data, idx, ops)
        self.assertEqual(code, 1)
        # 失败不许写文件
        self.assertEqual(pathlib.Path(data).read_bytes(), before_data)
        self.assertEqual(pathlib.Path(idx).read_bytes(), before_idx)


class TestSetTsRules(UpdateBase):
    def test_change_inside_range_keeps_stats(self):
        data, idx = self.make_table([(i, 100 + i * 10) for i in range(8)])
        self.apply_ops(data, idx, [{"op": "set-ts", "id": 3, "ts": 135}])
        index = Index.load(idx)
        self.assertEqual((index.min_ts[0], index.max_ts[0]), (100, 170))
        self.assert_stats_fresh(data, idx)

    def test_extend_max_side(self):
        data, idx = self.make_table([(i, 100 + i * 10) for i in range(8)])
        self.apply_ops(data, idx, [{"op": "set-ts", "id": 5, "ts": 999}])
        index = Index.load(idx)
        self.assertEqual((index.min_ts[0], index.max_ts[0]), (100, 999))
        self.assert_stats_fresh(data, idx)

    def test_extend_min_side(self):
        data, idx = self.make_table([(i, 100 + i * 10) for i in range(8)])
        self.apply_ops(data, idx, [{"op": "set-ts", "id": 2, "ts": 3}])
        index = Index.load(idx)
        self.assertEqual((index.min_ts[0], index.max_ts[0]), (3, 170))
        self.assert_stats_fresh(data, idx)

    def test_shrink_min_boundary_recomputes(self):
        # 旧值 = min 且新值更大：必须重算这一块，否则统计不收缩
        data, idx = self.make_table([(i, 100 + i * 10) for i in range(8)])
        self.apply_ops(data, idx, [{"op": "set-ts", "id": 0, "ts": 155}])
        index = Index.load(idx)
        self.assertEqual((index.min_ts[0], index.max_ts[0]), (110, 170))
        self.assert_stats_fresh(data, idx)

    def test_shrink_max_boundary_recomputes(self):
        data, idx = self.make_table([(i, 100 + i * 10) for i in range(8)])
        self.apply_ops(data, idx, [{"op": "set-ts", "id": 7, "ts": 105}])
        index = Index.load(idx)
        self.assertEqual((index.min_ts[0], index.max_ts[0]), (100, 160))
        self.assert_stats_fresh(data, idx)

    def test_single_row_block_takes_new_value(self):
        data, idx = self.make_table([(0, 100), (8, 200)])
        self.apply_ops(data, idx, [{"op": "set-ts", "id": 8, "ts": 50}])
        index = Index.load(idx)
        self.assertEqual((index.min_ts[1], index.max_ts[1]), (50, 50))
        self.assert_stats_fresh(data, idx)

    def test_set_ts_on_appended_row_same_batch(self):
        data, idx = self.make_table([(i, 100 + i) for i in range(8)])
        self.apply_ops(data, idx, [
            {"op": "append", "id": 9, "ts": 500},
            {"op": "set-ts", "id": 9, "ts": 42},
        ])
        index = Index.load(idx)
        self.assertEqual((index.min_ts[1], index.max_ts[1]), (42, 42))
        self.assert_stats_fresh(data, idx)
        self.assertEqual(dict(read_rows(data))[9], 42)

    def test_set_ts_missing_id_rejected(self):
        data, idx = self.make_table([(0, 100), (1, 101)])
        before = pathlib.Path(data).read_bytes()
        ops = self.path("bad.jsonl")
        write_ops(ops, [{"op": "set-ts", "id": 5, "ts": 1}])  # 空洞里的 id
        code, _, _ = run_cli("update", data, idx, ops)
        self.assertEqual(code, 1)
        self.assertEqual(pathlib.Path(data).read_bytes(), before)


class TestRandomizedUpdates(UpdateBase):
    """随机追加/改写交错，每批之后核对统计与查询结果。"""

    def test_random_ops_keep_stats_and_results_exact(self):
        rng = random.Random(20260925)
        rows = [(i, rng.randrange(1000)) for i in range(40)]
        data, idx = self.make_table(rows)
        next_id = 40
        query_file = self.path("q.jsonl")
        queries = []
        for k in range(30):
            lo = rng.randrange(1200)
            hi = lo + rng.randrange(400)
            queries.append({"id": f"rnd-{k:02d}", "op": "between",
                            "lo": lo, "hi": hi})
        queries.append({"id": "rnd-and", "op": "and",
                        "args": [{"op": "ge", "value": 300},
                                 {"op": "le", "value": 700}]})
        queries.append({"id": "rnd-ne", "op": "ne", "value": 555})
        with open(query_file, "w", encoding="utf-8", newline="") as f:
            for q in queries:
                f.write(json.dumps(q) + "\n")

        for batch in range(15):
            ops = []
            for _ in range(rng.randrange(1, 6)):
                if rng.random() < 0.4:
                    next_id += rng.randrange(1, 20)  # 允许跨块空洞
                    ops.append({"op": "append", "id": next_id,
                                "ts": rng.randrange(1200)})
                else:
                    victim = rng.randrange(next_id)
                    ops.append({"op": "set-ts", "id": victim,
                                "ts": rng.randrange(1200)})
            # 只保留 id 存在的 set-ts（空洞里的 id 是非法输入，单独测过）
            existing = {r[0] for r in read_rows(data)}
            ops = [op for op in ops
                   if op["op"] == "append" or op["id"] in existing]
            if not ops:
                continue
            self.apply_ops(data, idx, ops, name=f"ops{batch}.jsonl")
            with self.subTest(batch=batch):
                self.assert_stats_fresh(data, idx)
                # 每条查询：剪枝结果 == 逐行全扫
                out = self.query(data, idx, query_file).decode()
                sections = {}
                cur = None
                for line in out.splitlines():
                    if line.startswith("# "):
                        _, qid, _ = line.split()
                        cur = sections.setdefault(qid, [])
                    else:
                        row_id, ts = line.split("\t")
                        cur.append((int(row_id), int(ts)))
                for q in queries:
                    self.assertEqual(sections[q["id"]], naive_scan(data, q),
                                     f"批次 {batch} 查询 {q['id']} 不一致")


if __name__ == "__main__":
    unittest.main()
