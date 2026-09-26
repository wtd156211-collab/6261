"""样例对齐：stdout 逐字节一致、剪枝上界、确定性。"""

import math
import os
import shutil
import unittest

from tests.common import (GROUPS, TempDirTestCase, run_cli, sample_path)

BLOCK_SIZE = 256  # 默认块容量（README：块容量不给就自己定）


class TestSamplesStdout(TempDirTestCase):
    def test_static_groups_match_expected(self):
        for group in ("cut", "part", "edge"):
            with self.subTest(group=group):
                idx = self.build(sample_path("data", f"{group}.txt"))
                out = self.query(sample_path("data", f"{group}.txt"), idx,
                                 sample_path("queries", f"{group}.jsonl"))
                with open(sample_path("expected", f"{group}.txt"), "rb") as f:
                    self.assertEqual(out, f.read())

    def test_sync_group_after_update(self):
        data = self.path("sync.txt")
        shutil.copyfile(sample_path("data", "sync.txt"), data)
        idx = self.build(data)
        self.update(data, idx, sample_path("data", "sync-ops.jsonl"))
        out = self.query(data, idx, sample_path("queries", "sync.jsonl"))
        with open(sample_path("expected", "sync.txt"), "rb") as f:
            self.assertEqual(out, f.read())


class TestPruningBounds(TempDirTestCase):
    def _report_scanned(self):
        scanned = {}
        for group in GROUPS:
            if group == "sync":
                data = self.path("sync.txt")
                shutil.copyfile(sample_path("data", "sync.txt"), data)
                idx = self.build(data, name="sync.zci")
                self.update(data, idx, sample_path("data", "sync-ops.jsonl"))
            else:
                data = sample_path("data", f"{group}.txt")
                idx = self.build(data, name=f"{group}.zci")
            rep = self.path(f"{group}.rep")
            self.query(data, idx, sample_path("queries", f"{group}.jsonl"),
                       report=rep)
            with open(rep, encoding="utf-8") as f:
                for line in f:
                    if line.startswith("#"):
                        head = line.split()
                        self.assertEqual(head[1], "块容量")
                        self.assertEqual(int(head[2]), BLOCK_SIZE)
                        continue
                    qid, hits, sc, sk, total = line.rstrip("\n").split("\t")
                    scanned[qid] = (int(hits), int(sc), int(sk), int(total))
        return scanned

    def test_scanned_blocks_within_bounds(self):
        scanned = self._report_scanned()
        with open(sample_path("expected-pruning.txt"), encoding="utf-8") as f:
            lines = [ln for ln in f.read().splitlines()
                     if ln and not ln.startswith("#")]
        self.assertEqual(len(lines), len(scanned))
        for line in lines:
            qid, h, bound, _ = line.split("\t")
            h = int(h)
            bound_n = 0 if bound == "0" else math.ceil(h / BLOCK_SIZE) + 2
            with self.subTest(query=qid):
                hits, sc, sk, total = scanned[qid]
                self.assertEqual(hits, h)  # 命中行数与记录一致
                self.assertLessEqual(sc, bound_n)
                self.assertEqual(sk, total - sc)  # 跳过 = 总块数 − 扫描


class TestDeterminism(TempDirTestCase):
    def test_query_and_report_are_byte_deterministic(self):
        idx = self.build(sample_path("data", "part.txt"))
        args = (sample_path("data", "part.txt"), idx,
                sample_path("queries", "part.jsonl"))
        out1 = self.query(*args, report=self.path("r1"))
        out2 = self.query(*args, report=self.path("r2"))
        self.assertEqual(out1, out2)
        with open(self.path("r1"), "rb") as f:
            r1 = f.read()
        with open(self.path("r2"), "rb") as f:
            self.assertEqual(r1, f.read())

    def test_page_is_byte_deterministic(self):
        idx = self.build(sample_path("data", "edge.txt"))
        for name in ("p1.html", "p2.html"):
            code, _, err = run_cli(
                "page", sample_path("data", "edge.txt"), idx,
                sample_path("queries", "edge.jsonl"),
                "--query", "edge-pair", "--out", self.path(name))
            self.assertEqual(code, 0, err.decode())
        with open(self.path("p1.html"), "rb") as f:
            p1 = f.read()
        with open(self.path("p2.html"), "rb") as f:
            self.assertEqual(p1, f.read())


if __name__ == "__main__":
    unittest.main()
