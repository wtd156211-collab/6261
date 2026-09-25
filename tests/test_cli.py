"""CLI 退出码与错误处理。"""

import os
import shutil
import tempfile
import unittest

from .helpers import run_cli, sample, write_data


class TestExitCodes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)

    def test_usage_error_is_2(self):
        code, _, _ = run_cli([])
        self.assertEqual(code, 2)
        code, _, _ = run_cli(["build", "onlyone"])
        self.assertEqual(code, 2)
        code, _, _ = run_cli(["frobnicate"])
        self.assertEqual(code, 2)

    def test_missing_files_are_1(self):
        code, _, _ = run_cli(["build", "/nonexistent/data.txt",
                              os.path.join(self.tmp, "x.idx")])
        self.assertEqual(code, 1)
        code, _, _ = run_cli(["query", "/nonexistent/d", "/nonexistent/i",
                              "/nonexistent/q"])
        self.assertEqual(code, 1)

    def test_bad_data_format_is_1(self):
        bad = os.path.join(self.tmp, "bad.txt")
        with open(bad, "w") as fileobj:
            fileobj.write("1 2\n")  # 非定长
        code, _, _ = run_cli(["build", bad, os.path.join(self.tmp, "x.idx")])
        self.assertEqual(code, 1)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "x.idx")))

    def test_non_increasing_id_is_1(self):
        bad = os.path.join(self.tmp, "bad.txt")
        write_data(bad, [(5, 100), (5, 200)])
        code, _, _ = run_cli(["build", bad, os.path.join(self.tmp, "x.idx")])
        self.assertEqual(code, 1)
        write_data(bad, [(5, 100), (4, 200)])
        code, _, _ = run_cli(["build", bad, os.path.join(self.tmp, "x.idx")])
        self.assertEqual(code, 1)

    def test_bad_query_form_is_1(self):
        data = os.path.join(self.tmp, "d.txt")
        write_data(data, [(0, 100)])
        index = os.path.join(self.tmp, "d.idx")
        code, _, _ = run_cli(["build", data, index])
        self.assertEqual(code, 0)
        queries = os.path.join(self.tmp, "q.jsonl")
        with open(queries, "w") as fileobj:
            fileobj.write('{"id": "q", "op": "or", "args": []}\n')
        code, out, _ = run_cli(["query", data, index, queries])
        self.assertEqual(code, 1)
        self.assertEqual(out, "")  # 非 0 不写输出

    def test_block_size_option(self):
        data = os.path.join(self.tmp, "d.txt")
        write_data(data, [(i, i * 10) for i in range(10)])
        for size, blocks in ((1, 10), (3, 4), (100, 1)):
            index = os.path.join(self.tmp, "d%d.idx" % size)
            code, _, err = run_cli(["build", data, index, "--block-size", str(size)])
            self.assertEqual(code, 0, err)
            from zonecut.index import BlockIndex
            self.assertEqual(BlockIndex.load(index).nblocks, blocks)
        code, _, _ = run_cli(["build", data, os.path.join(self.tmp, "z.idx"),
                              "--block-size", "0"])
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
