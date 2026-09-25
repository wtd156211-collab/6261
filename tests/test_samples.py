"""样本组端到端：stdout 逐字节对齐、剪枝上界、确定性。"""

import math
import os
import shutil
import tempfile
import unittest

from zonecut import DEFAULT_BLOCK_SIZE

from .helpers import run_cli, sample

GROUPS = ("cut", "part", "edge")


class TestSamples(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)

    def _build(self, data, name="idx"):
        index = os.path.join(self.tmp, name + ".idx")
        code, _, err = run_cli(["build", data, index])
        self.assertEqual(code, 0, err)
        return index

    def _query(self, data, index, queries, report=None):
        argv = ["query", data, index, queries]
        if report:
            argv += ["--report", report]
        code, out, err = run_cli(argv)
        self.assertEqual(code, 0, err)
        return out

    def test_expected_stdout(self):
        for group in GROUPS:
            with self.subTest(group=group):
                index = self._build(sample("data", group + ".txt"), group)
                out = self._query(sample("data", group + ".txt"), index,
                                  sample("queries", group + ".jsonl"))
                with open(sample("expected", group + ".txt"), "r") as fileobj:
                    self.assertEqual(out, fileobj.read())

    def test_sync_group(self):
        data = os.path.join(self.tmp, "sync.txt")
        shutil.copyfile(sample("data", "sync.txt"), data)
        index = self._build(data, "sync")
        code, _, err = run_cli(["update", data, index, sample("data", "sync-ops.jsonl")])
        self.assertEqual(code, 0, err)
        out = self._query(data, index, sample("queries", "sync.jsonl"))
        with open(sample("expected", "sync.txt"), "r") as fileobj:
            self.assertEqual(out, fileobj.read())

    def test_pruning_bounds(self):
        bounds = {}
        with open(sample("expected-pruning.txt"), "r") as fileobj:
            for line in fileobj:
                if line.startswith("#") or not line.strip():
                    continue
                qid, hits, bound, _ = line.rstrip("\n").split("\t")
                bounds[qid] = (int(hits), bound)
        reports = {}
        for group in GROUPS:
            index = self._build(sample("data", group + ".txt"), group)
            report = os.path.join(self.tmp, group + ".rep")
            self._query(sample("data", group + ".txt"), index,
                        sample("queries", group + ".jsonl"), report)
            reports[group] = report
        data = os.path.join(self.tmp, "sync.txt")
        shutil.copyfile(sample("data", "sync.txt"), data)
        index = self._build(data, "sync")
        run_cli(["update", data, index, sample("data", "sync-ops.jsonl")])
        report = os.path.join(self.tmp, "sync.rep")
        self._query(data, index, sample("queries", "sync.jsonl"), report)
        reports["sync"] = report
        block_size = DEFAULT_BLOCK_SIZE
        for report in reports.values():
            with open(report, "r") as fileobj:
                lines = fileobj.read().splitlines()
            self.assertTrue(lines[0].startswith("# 块容量 %d " % block_size))
            for line in lines[1:]:
                qid, hits, scanned, skipped, total = line.split("\t")
                hits_expected, bound = bounds[qid]
                self.assertEqual(int(hits), hits_expected, qid)
                if bound == "0":
                    limit = 0
                else:
                    limit = math.ceil(hits_expected / block_size) + 2
                self.assertLessEqual(int(scanned), limit, qid)
                self.assertEqual(int(scanned) + int(skipped), int(total), qid)

    def test_deterministic(self):
        index = self._build(sample("data", "part.txt"), "part")
        outs = [self._query(sample("data", "part.txt"), index,
                            sample("queries", "part.jsonl")) for _ in range(2)]
        self.assertEqual(outs[0], outs[1])
        pages = []
        for i in range(2):
            page = os.path.join(self.tmp, "p%d.html" % i)
            code, _, err = run_cli(["page", sample("data", "part.txt"), index,
                                    sample("queries", "part.jsonl"),
                                    "--query", "part-with-hole", "--out", page])
            self.assertEqual(code, 0, err)
            with open(page, "rb") as fileobj:
                pages.append(fileobj.read())
        self.assertEqual(pages[0], pages[1])
        reports = []
        for i in range(2):
            report = os.path.join(self.tmp, "r%d.txt" % i)
            self._query(sample("data", "part.txt"), index,
                        sample("queries", "part.jsonl"), report)
            with open(report, "rb") as fileobj:
                reports.append(fileobj.read())
        self.assertEqual(reports[0], reports[1])


if __name__ == "__main__":
    unittest.main()
