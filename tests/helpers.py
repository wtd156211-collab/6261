"""测试公共工具：只读 samples/，可写临时目录。"""

import contextlib
import io
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from zonecut.cli import main  # noqa: E402

SAMPLES = os.path.join(os.path.dirname(__file__), "..", "samples")
SAMPLES = os.path.abspath(SAMPLES)


def sample(*parts):
    return os.path.join(SAMPLES, *parts)


def run_cli(argv):
    """进程内跑 CLI，返回 (退出码, stdout, stderr)。"""
    out, err = io.StringIO(), io.StringIO()
    code = 0
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = main(argv)
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
    return code, out.getvalue(), err.getvalue()


def read_data_rows(path):
    """暴力读全表，返回 [(id, ts), ...]。"""
    rows = []
    with open(path, "rb") as fileobj:
        blob = fileobj.read()
    for pos in range(0, len(blob), 26):
        rows.append((int(blob[pos:pos + 12]), int(blob[pos + 13:pos + 25])))
    return rows


def match_row(cond, ts):
    return cond.match(ts)


def brute_force(rows, cond):
    return [(rid, ts) for rid, ts in rows if cond.match(ts)]


def write_data(path, rows):
    with open(path, "wb") as fileobj:
        for row_id, ts in rows:
            fileobj.write(b"%012d %012d\n" % (row_id, ts))
