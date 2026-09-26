"""命令行：退出码口径与"非 0 不写输出"。"""

import os
import pathlib
import unittest

from tests.common import TempDirTestCase, run_cli, sample_path


class TestExitCodes(TempDirTestCase):
    def test_usage_error_is_2(self):
        code, _, _ = run_cli()
        self.assertEqual(code, 2)
        code, _, _ = run_cli("frobnicate")
        self.assertEqual(code, 2)
        code, _, _ = run_cli("build", "a", "b", "--block-size", "0")
        self.assertEqual(code, 2)
        code, _, _ = run_cli("build", "a", "b", "--block-size", "x")
        self.assertEqual(code, 2)

    def test_missing_files_are_1(self):
        idx = self.path("i.zci")
        code, _, _ = run_cli("build", self.path("nope.txt"), idx)
        self.assertEqual(code, 1)
        self.assertFalse(os.path.exists(idx))
        code, _, _ = run_cli("query", self.path("nope.txt"), idx,
                             self.path("q.jsonl"))
        self.assertEqual(code, 1)
        code, _, _ = run_cli("update", self.path("nope.txt"), idx,
                             self.path("o.jsonl"))
        self.assertEqual(code, 1)

    def test_bad_data_rejected(self):
        # id 不递增
        data = self.path("bad.txt")
        with open(data, "w") as f:
            f.write("000000000005 000000000100\n")
            f.write("000000000002 000000000101\n")
        idx = self.path("i.zci")
        code, _, _ = run_cli("build", data, idx)
        self.assertEqual(code, 1)
        self.assertFalse(os.path.exists(idx))
        # 行宽不对
        with open(data, "w") as f:
            f.write("000000000005 000000000100\n")
            f.write("000000000006 000000000101")  # 缺 LF
        code, _, _ = run_cli("build", data, idx)
        self.assertEqual(code, 1)
        self.assertFalse(os.path.exists(idx))

    def test_bad_query_rejected_before_output(self):
        idx = self.build(sample_path("data", "cut.txt"))
        q = self.path("bad.jsonl")
        with open(q, "w") as f:
            f.write('{"id": "ok", "op": "eq", "value": 1000}\n')
            f.write('{"id": "bad", "op": "contains", "value": 1}\n')
        rep = self.path("r")
        code, out, _ = run_cli("query", sample_path("data", "cut.txt"), idx,
                               q, "--report", rep)
        self.assertEqual(code, 1)
        self.assertEqual(out, b"")  # 非 0 不写输出
        self.assertFalse(os.path.exists(rep))

    def test_query_rejects_nested_and_and_bad_between(self):
        idx = self.build(sample_path("data", "cut.txt"))
        for obj in (
            '{"id": "x", "op": "and", "args": [{"op": "and", "args": '
            '[{"op": "eq", "value": 1}, {"op": "eq", "value": 2}]}, '
            '{"op": "eq", "value": 3}]}',
            '{"id": "x", "op": "and", "args": [{"op": "eq", "value": 1}]}',
            '{"id": "x", "op": "between", "lo": 9, "hi": 3}',
            '{"id": "x", "op": "eq"}',
            'not json',
        ):
            q = self.path("bad.jsonl")
            with open(q, "w") as f:
                f.write(obj + "\n")
            code, out, _ = run_cli("query", sample_path("data", "cut.txt"),
                                   idx, q)
            with self.subTest(obj=obj):
                self.assertEqual(code, 1)
                self.assertEqual(out, b"")

    def test_page_unknown_query_id(self):
        idx = self.build(sample_path("data", "cut.txt"))
        out_html = self.path("p.html")
        code, _, _ = run_cli("page", sample_path("data", "cut.txt"), idx,
                             sample_path("queries", "cut.jsonl"),
                             "--query", "nope", "--out", out_html)
        self.assertEqual(code, 1)
        self.assertFalse(os.path.exists(out_html))

    def test_update_bad_op_rejected_without_writes(self):
        import shutil
        data = self.path("t.txt")
        shutil.copyfile(sample_path("data", "sync.txt"), data)
        idx = self.build(data)
        before_data = pathlib.Path(data).read_bytes()
        before_idx = pathlib.Path(idx).read_bytes()
        ops = self.path("ops.jsonl")
        with open(ops, "w") as f:
            f.write('{"op": "append", "id": 40, "ts": 3500}\n')
            f.write('{"op": "delete", "id": 1}\n')
        code, _, _ = run_cli("update", data, idx, ops)
        self.assertEqual(code, 1)
        self.assertEqual(pathlib.Path(data).read_bytes(), before_data)
        self.assertEqual(pathlib.Path(idx).read_bytes(), before_idx)


if __name__ == "__main__":
    unittest.main()
