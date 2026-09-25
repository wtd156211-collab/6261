"""页面：必备元素与属性、数字来自引擎输出。"""

import os
import re
import shutil
import tempfile
import unittest

from .helpers import run_cli, sample


class TestPage(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.index = os.path.join(self.tmp, "cut.idx")
        # 块容量 32：cut 的 id 空洞能造出空块，覆盖页面全部状态形态
        code, _, err = run_cli(["build", sample("data", "cut.txt"), self.index,
                                "--block-size", "32"])
        self.assertEqual(code, 0, err)
        self.page = os.path.join(self.tmp, "page.html")
        code, _, err = run_cli(["page", sample("data", "cut.txt"), self.index,
                                sample("queries", "cut.jsonl"),
                                "--query", "cut-band-b", "--out", self.page])
        self.assertEqual(code, 0, err)
        with open(self.page, "r", encoding="utf-8") as fileobj:
            self.html = fileobj.read()

    def test_required_data_keys(self):
        for key in ("scanned", "skipped", "blocks", "prune-rate"):
            self.assertIn('data-key="%s"' % key, self.html)
        m = re.search(r'data-key="prune-rate"[^>]*><[^>]*>([\d.]+)%<', self.html)
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), "92.9")  # 1 - 1/14

    def test_table_structure(self):
        self.assertIn('<table id="blocks">', self.html)
        rows = re.findall(
            r'<tr data-block="(\d+)" data-state="(\w+)" data-rows="(\d+)" '
            r'data-min="([\d-]+)" data-max="([\d-]+)" data-empty="([01])">',
            self.html)
        self.assertEqual(len(rows), 14)  # 419 // 32 + 1
        self.assertEqual([int(r[0]) for r in rows], list(range(14)))  # 块号升序
        states = {r[1] for r in rows}
        self.assertTrue(states <= {"hit", "scanned", "skip"})
        for _, state, nrows, mn, mx, empty in rows:
            if empty == "1":
                self.assertEqual((nrows, mn, mx), ("0", "-", "-"))
                self.assertEqual(state, "skip")  # 空块任何条件都剪掉
            else:
                self.assertEqual(empty, "0")
        hit_rows = [r for r in rows if r[1] == "hit"]
        self.assertEqual([r[0] for r in hit_rows], ["12"])  # cut-band-b 只中块 12

    def test_numbers_come_from_engine(self):
        # 顶部四项与逐块状态一致：扫描 1 块、跳过 13 块、共 14 块
        self.assertRegex(self.html, r'data-key="scanned"><[^>]*>1<')
        self.assertRegex(self.html, r'data-key="skipped"><[^>]*>13<')
        self.assertRegex(self.html, r'data-key="blocks"><[^>]*>14<')

    def test_self_contained(self):
        self.assertIn('<script type="module">', self.html)
        self.assertNotIn("http://", self.html)
        self.assertNotIn("https://", self.html)
        self.assertNotIn("src=", self.html)
        self.assertNotIn("href=", self.html)

    def test_query_id_missing(self):
        code, _, _ = run_cli(["page", sample("data", "cut.txt"), self.index,
                              sample("queries", "cut.jsonl"),
                              "--query", "nope", "--out",
                              os.path.join(self.tmp, "x.html")])
        self.assertEqual(code, 1)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "x.html")))


if __name__ == "__main__":
    unittest.main()
