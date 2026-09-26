"""页面：必备元素与属性、数字来自引擎输出、自包含。"""

import re
import shutil
import unittest

from tests.common import TempDirTestCase, run_cli, sample_path


class TestPage(TempDirTestCase):
    def setUp(self):
        super().setUp()
        # sync 组跑完变更之后再出页面（含命中/扫描/跳过/空块四种状态）
        self.data = self.path("sync.txt")
        shutil.copyfile(sample_path("data", "sync.txt"), self.data)
        self.idx = self.build(self.data, block_size=8)
        self.update(self.data, self.idx, sample_path("data", "sync-ops.jsonl"))
        self.html_path = self.path("page.html")
        code, _, err = run_cli("page", self.data, self.idx,
                               sample_path("queries", "sync.jsonl"),
                               "--query", "sync-mid",
                               "--out", self.html_path)
        self.assertEqual(code, 0, err.decode())
        with open(self.html_path, encoding="utf-8") as f:
            self.html = f.read()

    def test_required_data_key_elements(self):
        for key in ("scanned", "skipped", "blocks", "prune-rate"):
            self.assertIn(f'data-key="{key}"', self.html)
        m = re.search(r'data-key="prune-rate">([^<]+)<', self.html)
        self.assertRegex(m.group(1), r"^\d+\.\d%$")  # 一位小数百分数

    def test_numbers_match_engine_report(self):
        rep = self.path("r")
        self.query(self.data, self.idx, sample_path("queries", "sync.jsonl"),
                   report=rep)
        row = None
        total = None
        with open(rep, encoding="utf-8") as f:
            for line in f:
                if line.startswith("#"):
                    total = int(line.split()[4])
                elif line.startswith("sync-mid"):
                    row = line.rstrip("\n").split("\t")
        _, hits, scanned, skipped, blocks = row
        self.assertEqual(int(blocks), total)
        for key, expect in (("scanned", scanned), ("skipped", skipped),
                            ("blocks", blocks)):
            m = re.search(rf'data-key="{key}">(\d+)<', self.html)
            self.assertIsNotNone(m, key)
            self.assertEqual(m.group(1), expect, key)
        rate = 100.0 * (1 - int(scanned) / int(blocks))
        m = re.search(r'data-key="prune-rate">([^<]+)<', self.html)
        self.assertEqual(m.group(1), f"{rate:.1f}%")

    def test_block_rows(self):
        rows = re.findall(
            r'<tr data-block="(\d+)" data-state="(\w+)" data-rows="(\d+)" '
            r'data-min="([^"]+)" data-max="([^"]+)" data-empty="([01])">',
            self.html)
        self.assertTrue(rows)
        # 块号升序、连续
        self.assertEqual([int(r[0]) for r in rows], list(range(len(rows))))
        scanned = skipped = 0
        for _, state, n, mn, mx, empty in rows:
            self.assertIn(state, ("hit", "scanned", "skip"))
            if state == "skip":
                skipped += 1
            else:
                scanned += 1
            if empty == "1":
                self.assertEqual((mn, mx), ("-", "-"))  # 空块写 -
                self.assertEqual(n, "0")
                self.assertEqual(state, "skip")  # 空块任何条件都剪掉
            else:
                self.assertTrue(mn.isdigit() and mx.isdigit())
        m = re.search(r'data-key="scanned">(\d+)<', self.html)
        self.assertEqual(int(m.group(1)), scanned)
        m = re.search(r'data-key="skipped">(\d+)<', self.html)
        self.assertEqual(int(m.group(1)), skipped)
        m = re.search(r'data-key="blocks">(\d+)<', self.html)
        self.assertEqual(int(m.group(1)), len(rows))
        # 这份数据应当四种状态都出现
        states = {r[1] for r in rows}
        self.assertEqual(states, {"hit", "scanned", "skip"})
        self.assertIn("1", {r[5] for r in rows})

    def test_self_contained(self):
        self.assertNotIn("http://", self.html)
        self.assertNotIn("https://", self.html)
        self.assertNotIn("<link", self.html)
        self.assertNotIn("src=", self.html)
        self.assertIn('<script type="module">', self.html)
        self.assertIn('id="blocks"', self.html)
        self.assertTrue(self.html.startswith("<!DOCTYPE html>"))


if __name__ == "__main__":
    unittest.main()
