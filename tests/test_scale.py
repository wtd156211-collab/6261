"""规模用例：按 samples/data/scale.md 的配方现场生成 100 万行，核对 README 第 5 节预算。

只读 samples/（配方），数据生成在临时目录。
"""

import json
import os
import random
import shutil
import tempfile
import time
import tracemalloc
import unittest

from .helpers import run_cli, sample

N_ROWS = 1000000
TS0 = 1700000000
MEM_LIMIT = N_ROWS * 2 + 4 * 1024 * 1024
INDEX_LIMIT = N_ROWS * 1 + 512 * 1024


def generate(tmp):
    """按 scale.md 配方生成数据、查询、变更三个文件。"""
    rng = random.Random(20260925)
    data = os.path.join(tmp, "scale.txt")
    ts_list = [TS0]
    with open(data, "w") as fileobj:
        fileobj.write("%012d %012d\n" % (0, TS0))
        ts = TS0
        for i in range(1, N_ROWS):
            ts += rng.randrange(4)
            ts_list.append(ts)
            fileobj.write("%012d %012d\n" % (i, ts))
    span = ts - TS0
    queries = os.path.join(tmp, "scale-queries.jsonl")
    with open(queries, "w") as fileobj:
        for k in range(200):
            length = (60, 300, 600)[k % 3]
            lo = TS0 + (k * 7919) % (span - length)
            fileobj.write(json.dumps({
                "id": "scale-%03d" % k, "op": "between",
                "lo": lo, "hi": lo + length}) + "\n")
    changes = os.path.join(tmp, "scale-changes.jsonl")
    max_ts = ts
    with open(changes, "w") as fileobj:
        for k in range(200):
            if k % 2 == 0:
                max_ts += 1
                op = {"op": "append", "id": 1000000 + k // 2, "ts": max_ts}
            else:
                rid = (k * 7919) % 1000000
                new = ts_list[rid] + 3600
                if new > max_ts:
                    new = ts_list[rid] - 3600
                    if new <= 0:
                        new = ts_list[rid]
                ts_list[rid] = new
                op = {"op": "set-ts", "id": rid, "ts": new}
            fileobj.write(json.dumps(op) + "\n")
    return data, queries, changes


class TestScale(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.data, cls.queries, cls.changes = generate(cls.tmp)
        cls.index = os.path.join(cls.tmp, "scale.idx")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp)

    def test_01_build_budget(self):
        tracemalloc.start()
        start = time.perf_counter()
        code, _, err = run_cli(["build", self.data, self.index])
        elapsed = time.perf_counter() - start
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        self.assertEqual(code, 0, err)
        self.assertLessEqual(elapsed, 5.0, "build 超时")
        self.assertLessEqual(os.path.getsize(self.index), INDEX_LIMIT, "索引文件超限")
        self.assertLessEqual(peak, MEM_LIMIT, "build 峰值分配超限")

    def test_02_query_budget_and_prune_rate(self):
        tracemalloc.start()
        report = os.path.join(self.tmp, "report.txt")
        start = time.perf_counter()
        code, out, err = run_cli(["query", self.data, self.index, self.queries,
                                  "--report", report])
        elapsed = time.perf_counter() - start
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        self.assertEqual(code, 0, err)
        self.assertLessEqual(elapsed, 3.0, "200 条查询总耗时超限")
        self.assertLessEqual(peak, MEM_LIMIT, "query 峰值分配超限")
        with open(report) as fileobj:
            lines = fileobj.read().splitlines()
        self.assertTrue(lines[0].startswith("# 块容量 "))
        self.assertEqual(len(lines), 201)
        rates = []
        for line in lines[1:]:
            _, _, scanned, _, total = line.split("\t")
            rates.append(1 - int(scanned) / int(total))
        self.assertGreaterEqual(sum(rates) / len(rates), 0.99, "平均剪枝率不达标")
        self.assertGreaterEqual(min(rates), 0.95, "单条最低剪枝率不达标")

    def test_03_single_query_latency(self):
        from zonecut.conditions import parse_queries
        from zonecut.engine import run_query
        from zonecut.index import BlockIndex
        index = BlockIndex.load(self.index)
        queries = parse_queries(self.queries)
        with open(self.data, "rb") as data_file:
            for query_id, cond in queries:
                start = time.perf_counter()
                run_query(data_file, index, query_id, cond)
                self.assertLessEqual(time.perf_counter() - start, 0.05,
                                     "%s 单条超时" % query_id)

    def test_04_update_budget_and_correctness(self):
        start = time.perf_counter()
        code, _, err = run_cli(["update", self.data, self.index, self.changes])
        elapsed = time.perf_counter() - start
        self.assertEqual(code, 0, err)
        self.assertLessEqual(elapsed, 5.0, "200 次变更超时")
        # 增量更新后的索引必须与全量重建一致
        from zonecut.index import BlockIndex
        updated = BlockIndex.load(self.index)
        rebuilt = BlockIndex.build(self.data, updated.block_size)
        self.assertEqual(list(updated.rows), list(rebuilt.rows))
        self.assertEqual(list(updated.mins), list(rebuilt.mins))
        self.assertEqual(list(updated.maxs), list(rebuilt.maxs))
        self.assertEqual(list(updated.offs), list(rebuilt.offs))

    def test_05_page_budget(self):
        page = os.path.join(self.tmp, "page.html")
        start = time.perf_counter()
        code, _, err = run_cli(["page", self.data, self.index, self.queries,
                                "--query", "scale-000", "--out", page])
        elapsed = time.perf_counter() - start
        self.assertEqual(code, 0, err)
        self.assertLessEqual(elapsed, 3.0, "page 超时")
        self.assertLessEqual(os.path.getsize(page), 8 * 1024 * 1024, "页面超限")


if __name__ == "__main__":
    unittest.main()
