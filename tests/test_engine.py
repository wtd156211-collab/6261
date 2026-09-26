"""引擎正确性：剪枝结果与逐行全扫完全一致；剪枝判据边界口径。"""

import shutil
import unittest

from zonecut.conditions import (compile_match, compile_prune,
                                parse_condition)
from tests.common import (TempDirTestCase, load_query_jsons, naive_scan,
                          sample_path, stats_from_data)


def prune_of(op_obj, m, M):
    return compile_prune(parse_condition(op_obj))(m, M)


def match_of(op_obj, ts):
    return compile_match(parse_condition(op_obj))(ts)


class TestPrunePredicate(unittest.TestCase):
    """README 2.2 的判据表，边界算命中。"""

    def test_eq(self):
        self.assertTrue(prune_of({"op": "eq", "value": 9}, 10, 20))
        self.assertFalse(prune_of({"op": "eq", "value": 10}, 10, 20))
        self.assertFalse(prune_of({"op": "eq", "value": 20}, 10, 20))
        self.assertTrue(prune_of({"op": "eq", "value": 21}, 10, 20))

    def test_ne(self):
        # 只有整块都等于 V 才能剪；V 落在 [m, M] 之外不是剪枝理由
        self.assertTrue(prune_of({"op": "ne", "value": 7}, 7, 7))
        self.assertFalse(prune_of({"op": "ne", "value": 7}, 7, 8))
        self.assertFalse(prune_of({"op": "ne", "value": 9}, 10, 20))
        self.assertFalse(prune_of({"op": "ne", "value": 15}, 10, 20))

    def test_lt_le(self):
        self.assertTrue(prune_of({"op": "lt", "value": 10}, 10, 20))
        self.assertFalse(prune_of({"op": "lt", "value": 11}, 10, 20))
        self.assertTrue(prune_of({"op": "le", "value": 9}, 10, 20))
        self.assertFalse(prune_of({"op": "le", "value": 10}, 10, 20))

    def test_gt_ge(self):
        # M == V 时 ge 不能剪、gt 必须剪
        self.assertTrue(prune_of({"op": "gt", "value": 20}, 10, 20))
        self.assertFalse(prune_of({"op": "gt", "value": 19}, 10, 20))
        self.assertTrue(prune_of({"op": "ge", "value": 21}, 10, 20))
        self.assertFalse(prune_of({"op": "ge", "value": 20}, 10, 20))

    def test_between(self):
        self.assertTrue(prune_of({"op": "between", "lo": 1, "hi": 9}, 10, 20))
        self.assertFalse(prune_of({"op": "between", "lo": 1, "hi": 10}, 10, 20))
        self.assertFalse(prune_of({"op": "between", "lo": 20, "hi": 30}, 10, 20))
        self.assertTrue(prune_of({"op": "between", "lo": 21, "hi": 30}, 10, 20))

    def test_and(self):
        cond = {"op": "and", "args": [{"op": "ge", "value": 15},
                                      {"op": "le", "value": 25}]}
        self.assertTrue(prune_of(cond, 1, 14))    # ge 15 剪掉
        self.assertTrue(prune_of(cond, 26, 30))   # le 25 剪掉
        self.assertFalse(prune_of(cond, 14, 26))  # 两项都剪不掉

    def test_match_row_level(self):
        self.assertTrue(match_of({"op": "between", "lo": 3, "hi": 5}, 5))
        self.assertFalse(match_of({"op": "between", "lo": 3, "hi": 5}, 6))
        self.assertTrue(match_of(
            {"op": "and", "args": [{"op": "gt", "value": 1},
                                   {"op": "ne", "value": 3}]}, 2))
        self.assertFalse(match_of(
            {"op": "and", "args": [{"op": "gt", "value": 1},
                                   {"op": "ne", "value": 3}]}, 3))


class TestEngineVsFullScan(TempDirTestCase):
    """剪枝后的结果必须与逐行全扫一模一样（含空块的小容量块）。"""

    def _check_group(self, group, data, block_size):
        idx = self.build(data, name=f"{group}.zci", block_size=block_size)
        queries = load_query_jsons(sample_path("queries", f"{group}.jsonl"))
        qpath = sample_path("queries", f"{group}.jsonl")
        out = self.query(data, idx, qpath).decode()
        sections = {}
        cur = None
        for line in out.splitlines():
            if line.startswith("# "):
                _, qid, cnt = line.split()
                cur = []
                sections[qid] = cur
            else:
                row_id, ts = line.split("\t")
                cur.append((int(row_id), int(ts)))
        for q in queries:
            with self.subTest(group=group, query=q["id"], S=block_size):
                expect = naive_scan(data, q)
                got = sections[q["id"]]
                self.assertEqual(got, expect)
                self.assertEqual(len(got), len(expect))

    def test_static_groups_default_and_small_blocks(self):
        for group in ("cut", "part", "edge"):
            for block_size in (256, 8, 3, 1):
                self._check_group(group, sample_path("data", f"{group}.txt"),
                                  block_size)

    def test_sync_after_update(self):
        for block_size in (256, 8, 4):
            data = self.path(f"sync{block_size}.txt")
            shutil.copyfile(sample_path("data", "sync.txt"), data)
            idx = self.build(data, name=f"sync{block_size}.zci",
                             block_size=block_size)
            self.update(data, idx, sample_path("data", "sync-ops.jsonl"))
            self._check_group("sync", data, block_size)

    def test_index_stats_match_full_recompute(self):
        # 索引里的块统计必须等于对数据文件独立重算的统计
        for block_size in (256, 8, 1):
            idx = self.build(sample_path("data", "cut.txt"),
                             name=f"cut{block_size}.zci", block_size=block_size)
            from zonecut.index import Index
            index = Index.load(idx)
            stats = stats_from_data(sample_path("data", "cut.txt"), block_size)
            max_block = 419 // block_size
            self.assertEqual(index.num_blocks, max_block + 1)
            for b in range(index.num_blocks):
                with self.subTest(S=block_size, block=b):
                    if b in stats:
                        n, m, M = stats[b]
                        self.assertEqual(index.rows[b], n)
                        self.assertEqual(index.min_ts[b], m)
                        self.assertEqual(index.max_ts[b], M)
                    else:
                        self.assertEqual(index.rows[b], 0)  # 空块也占槽位


if __name__ == "__main__":
    unittest.main()
