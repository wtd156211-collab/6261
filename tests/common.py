"""测试公共工具：只读 samples/，一切写入都在临时目录。"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLES = os.path.join(REPO_ROOT, "samples")
GROUPS = ("cut", "part", "edge", "sync")


def sample_path(*parts):
    return os.path.join(SAMPLES, *parts)


def run_cli(*args, cwd=REPO_ROOT):
    """跑 python -m zonecut，返回 (退出码, stdout bytes, stderr bytes)。"""
    proc = subprocess.run(
        [sys.executable, "-m", "zonecut", *args],
        cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return proc.returncode, proc.stdout, proc.stderr


class TempDirTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def path(self, name):
        return os.path.join(self.tmp, name)

    def build(self, data, name="index.zci", block_size=None):
        idx = self.path(name)
        args = ["build", data, idx]
        if block_size is not None:
            args += ["--block-size", str(block_size)]
        code, out, err = run_cli(*args)
        self.assertEqual(code, 0, err.decode())
        return idx

    def query(self, data, idx, queries, report=None):
        args = ["query", data, idx, queries]
        if report:
            args += ["--report", report]
        code, out, err = run_cli(*args)
        self.assertEqual(code, 0, err.decode())
        return out

    def update(self, data, idx, ops):
        code, out, err = run_cli("update", data, idx, ops)
        self.assertEqual(code, 0, err.decode())


def read_rows(data_path):
    """测试侧独立的全表逐行扫描。"""
    rows = []
    with open(data_path, "rb") as f:
        for line in f:
            rows.append((int(line[:12]), int(line[13:25])))
    return rows


def naive_match(query, ts):
    """测试侧独立的逐行判定（直接读查询 JSON，不经过引擎）。"""
    op = query["op"]
    if op == "eq":
        return ts == query["value"]
    if op == "ne":
        return ts != query["value"]
    if op == "lt":
        return ts < query["value"]
    if op == "le":
        return ts <= query["value"]
    if op == "gt":
        return ts > query["value"]
    if op == "ge":
        return ts >= query["value"]
    if op == "between":
        return query["lo"] <= ts <= query["hi"]
    if op == "and":
        return all(naive_match(arg, ts) for arg in query["args"])
    raise AssertionError(f"未知 op: {op}")


def naive_scan(data_path, query):
    return [(i, t) for i, t in read_rows(data_path) if naive_match(query, t)]


def load_query_jsons(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f]


def stats_from_data(data_path, block_size):
    """测试侧独立的全量统计重算：{块号: [rows, min, max]}。"""
    stats = {}
    for row_id, ts in read_rows(data_path):
        b = row_id // block_size
        slot = stats.setdefault(b, [0, ts, ts])
        slot[0] += 1
        slot[1] = min(slot[1], ts)
        slot[2] = max(slot[2], ts)
    return stats
