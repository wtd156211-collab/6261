"""变更规则：追加与改写后统计必须跟上，且不许全量重建。

核心对照：增量更新后的索引 == 对更新后数据重新 build 的索引；
剪枝查询结果 == 暴力全表扫。
"""

import json
import os
import random
import shutil
import tempfile
import unittest

from .helpers import brute_force, read_data_rows, run_cli, write_data
from zonecut.conditions import parse_condition
from zonecut.index import BlockIndex


class UpdateCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.data = os.path.join(self.tmp, "data.txt")
        self.index = os.path.join(self.tmp, "data.idx")

    def _build(self, rows, block_size=4):
        write_data(self.data, rows)
        code, _, err = run_cli(["build", self.data, self.index,
                                "--block-size", str(block_size)])
        self.assertEqual(code, 0, err)

    def _update(self, ops):
        changes = os.path.join(self.tmp, "changes.jsonl")
        with open(changes, "w") as fileobj:
            for op in ops:
                fileobj.write(json.dumps(op) + "\n")
        return run_cli(["update", self.data, self.index, changes])

    def _assert_index_matches_rebuild(self):
        updated = BlockIndex.load(self.index)
        rebuilt = BlockIndex.build(self.data, updated.block_size)
        self.assertEqual(list(updated.rows), list(rebuilt.rows))
        self.assertEqual(list(updated.mins), list(rebuilt.mins))
        self.assertEqual(list(updated.maxs), list(rebuilt.maxs))
        self.assertEqual(list(updated.offs), list(rebuilt.offs))
        self.assertEqual(updated.nrows, rebuilt.nrows)
        self.assertEqual(updated.max_id, rebuilt.max_id)

    def _assert_queries_match_brute_force(self, queries):
        qfile = os.path.join(self.tmp, "q.jsonl")
        with open(qfile, "w") as fileobj:
            for i, cond in enumerate(queries):
                obj = {"id": "q%d" % i}
                obj.update(cond)
                fileobj.write(json.dumps(obj) + "\n")
        code, out, err = run_cli(["query", self.data, self.index, qfile])
        self.assertEqual(code, 0, err)
        rows = read_data_rows(self.data)
        sections = {}
        current = None
        for line in out.splitlines():
            if line.startswith("# "):
                _, qid, hits = line.split(" ")
                current = []
                sections[qid] = (int(hits), current)
            else:
                rid, ts = line.split("\t")
                current.append((int(rid), int(ts)))
        for i, cond in enumerate(queries):
            expected = brute_force(rows, parse_condition(cond))
            hits, got = sections["q%d" % i]
            self.assertEqual(hits, len(expected), cond)
            self.assertEqual(got, expected, cond)


class TestAppendRules(UpdateCase):
    def test_append_registers_empty_blocks(self):
        self._build([(0, 100), (1, 200)])
        code, _, err = self._update([{"op": "append", "id": 9, "ts": 50}])
        self.assertEqual(code, 0, err)
        index = BlockIndex.load(self.index)
        # 块容量 4：块 0 有 id 0/1，块 1 空，块 2 有 id 9
        self.assertEqual(index.nblocks, 3)
        self.assertEqual(list(index.rows), [2, 0, 1])
        self.assertEqual(index.mins[2], 50)
        self.assertEqual(index.maxs[2], 50)
        self._assert_index_matches_rebuild()

    def test_append_into_existing_tail_block(self):
        self._build([(0, 100), (1, 200)])
        code, _, err = self._update([{"op": "append", "id": 2, "ts": 150}])
        self.assertEqual(code, 0, err)
        index = BlockIndex.load(self.index)
        self.assertEqual(list(index.rows), [3])
        self.assertEqual(index.mins[0], 100)
        self.assertEqual(index.maxs[0], 200)
        self._assert_index_matches_rebuild()

    def test_append_extends_stats(self):
        self._build([(0, 100), (1, 200)])
        code, _, err = self._update([{"op": "append", "id": 2, "ts": 300},
                                     {"op": "append", "id": 3, "ts": 50}])
        self.assertEqual(code, 0, err)
        index = BlockIndex.load(self.index)
        self.assertEqual(index.mins[0], 50)
        self.assertEqual(index.maxs[0], 300)
        self._assert_index_matches_rebuild()

    def test_append_id_must_increase(self):
        self._build([(0, 100), (1, 200)])
        code, _, _ = self._update([{"op": "append", "id": 1, "ts": 1}])
        self.assertEqual(code, 1)
        code, _, _ = self._update([{"op": "append", "id": 0, "ts": 1}])
        self.assertEqual(code, 1)
        # 失败不落盘：数据与索引保持原样
        self.assertEqual(read_data_rows(self.data), [(0, 100), (1, 200)])
        self._assert_index_matches_rebuild()

    def test_failed_update_writes_nothing(self):
        self._build([(0, 100), (1, 200)])
        with open(self.data, "rb") as fileobj:
            before = fileobj.read()
        code, _, _ = self._update([{"op": "append", "id": 2, "ts": 1},
                                   {"op": "set-ts", "id": 99, "ts": 2}])
        self.assertEqual(code, 1)
        with open(self.data, "rb") as fileobj:
            self.assertEqual(fileobj.read(), before)
        self._assert_index_matches_rebuild()


class TestSetTsRules(UpdateCase):
    def test_inside_range_no_change(self):
        self._build([(0, 100), (1, 200), (2, 300)])
        code, _, err = self._update([{"op": "set-ts", "id": 1, "ts": 250}])
        self.assertEqual(code, 0, err)
        index = BlockIndex.load(self.index)
        self.assertEqual(index.mins[0], 100)
        self.assertEqual(index.maxs[0], 300)
        self._assert_index_matches_rebuild()

    def test_extend_either_side(self):
        self._build([(0, 100), (1, 200), (2, 300)])
        code, _, err = self._update([{"op": "set-ts", "id": 1, "ts": 400},
                                     {"op": "set-ts", "id": 0, "ts": 50}])
        self.assertEqual(code, 0, err)
        index = BlockIndex.load(self.index)
        self.assertEqual(index.mins[0], 50)
        self.assertEqual(index.maxs[0], 400)
        self._assert_index_matches_rebuild()

    def test_shrink_min_boundary_recomputes_block(self):
        self._build([(0, 100), (1, 200), (2, 300)])
        code, _, err = self._update([{"op": "set-ts", "id": 0, "ts": 250}])
        self.assertEqual(code, 0, err)
        index = BlockIndex.load(self.index)
        self.assertEqual(index.mins[0], 200)  # 100 被挪走，min 重算
        self.assertEqual(index.maxs[0], 300)
        self._assert_index_matches_rebuild()

    def test_shrink_max_boundary_recomputes_block(self):
        self._build([(0, 100), (1, 200), (2, 300)])
        code, _, err = self._update([{"op": "set-ts", "id": 2, "ts": 150}])
        self.assertEqual(code, 0, err)
        index = BlockIndex.load(self.index)
        self.assertEqual(index.mins[0], 100)
        self.assertEqual(index.maxs[0], 200)
        self._assert_index_matches_rebuild()

    def test_shrink_single_row_block(self):
        self._build([(0, 100), (5, 500)], block_size=4)
        code, _, err = self._update([{"op": "set-ts", "id": 5, "ts": 700}])
        self.assertEqual(code, 0, err)
        index = BlockIndex.load(self.index)
        self.assertEqual(index.mins[1], 700)
        self.assertEqual(index.maxs[1], 700)
        self._assert_index_matches_rebuild()

    def test_set_ts_missing_id(self):
        self._build([(0, 100)])
        code, _, _ = self._update([{"op": "set-ts", "id": 7, "ts": 1}])
        self.assertEqual(code, 1)
        code, _, _ = self._update([{"op": "set-ts", "id": 3, "ts": 1}])  # 空洞 id
        self.assertEqual(code, 1)

    def test_set_ts_after_append_same_file(self):
        self._build([(0, 100), (1, 200)])
        code, _, err = self._update([{"op": "append", "id": 2, "ts": 300},
                                     {"op": "set-ts", "id": 2, "ts": 50},
                                     {"op": "set-ts", "id": 0, "ts": 250}])
        self.assertEqual(code, 0, err)
        self._assert_index_matches_rebuild()
        self.assertEqual(read_data_rows(self.data),
                         [(0, 250), (1, 200), (2, 50)])


class TestRandomizedConsistency(UpdateCase):
    def test_random_ops_match_rebuild_and_brute_force(self):
        rng = random.Random(20260926)
        rows = [(i, rng.randrange(0, 1000)) for i in range(0, 60, 2)]
        self._build(rows, block_size=8)
        ops = []
        next_id = 60
        for _ in range(120):
            if rng.random() < 0.4:
                ops.append({"op": "append", "id": next_id,
                            "ts": rng.randrange(0, 1200)})
                next_id += rng.randrange(1, 4)
            else:
                rid = rng.randrange(0, next_id)
                ops.append({"op": "set-ts", "id": rid,
                            "ts": rng.randrange(0, 1200)})
        # 过滤掉会失败的 set-ts（id 不存在）：按数据文件逐步模拟存在性
        exists = {rid for rid, _ in rows}
        valid = []
        for op in ops:
            if op["op"] == "append":
                exists.add(op["id"])
                valid.append(op)
            elif op["id"] in exists:
                valid.append(op)
        code, _, err = self._update(valid)
        self.assertEqual(code, 0, err)
        self._assert_index_matches_rebuild()
        queries = [
            {"op": "eq", "value": 500},
            {"op": "ne", "value": 500},
            {"op": "lt", "value": 100},
            {"op": "le", "value": 999},
            {"op": "gt", "value": 900},
            {"op": "ge", "value": 0},
            {"op": "between", "lo": 200, "hi": 800},
            {"op": "and", "args": [{"op": "ge", "value": 100},
                                   {"op": "le", "value": 900},
                                   {"op": "ne", "value": 500}]},
        ]
        self._assert_queries_match_brute_force(queries)


if __name__ == "__main__":
    unittest.main()
