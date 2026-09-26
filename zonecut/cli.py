"""命令行：build / query / update / page。

退出码：0 成功；1 输入不可用；2 用法错误（argparse 默认）。
非 0 不写输出：所有产物都在输入全部校验通过之后才落盘。
"""

import argparse
import sys

from . import DEFAULT_BLOCK_SIZE, ZonecutError
from .datafile import check_data_size
from .engine import (format_result, load_queries, report_header,
                     report_line, run_query)
from .index import Index
from .page import render_page
from .update import Updater, parse_ops


def _block_size(value):
    try:
        n = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"块容量不是整数: {value!r}")
    if n < 1:
        raise argparse.ArgumentTypeError(f"块容量必须 >= 1: {value!r}")
    return n


def build_parser():
    parser = argparse.ArgumentParser(
        prog="zonecut", description="块级统计与查询剪枝")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("build", help="建索引")
    p.add_argument("data")
    p.add_argument("index")
    p.add_argument("--block-size", type=_block_size,
                   default=DEFAULT_BLOCK_SIZE)

    p = sub.add_parser("query", help="按索引剪枝跑查询")
    p.add_argument("data")
    p.add_argument("index")
    p.add_argument("queries")
    p.add_argument("--report")

    p = sub.add_parser("update", help="执行追加与改写")
    p.add_argument("data")
    p.add_argument("index")
    p.add_argument("ops")

    p = sub.add_parser("page", help="生成单条查询的块状态页面")
    p.add_argument("data")
    p.add_argument("index")
    p.add_argument("queries")
    p.add_argument("--query", required=True)
    p.add_argument("--out", required=True)
    return parser


def _cmd_build(args):
    index = Index.build(args.data, args.block_size)
    index.save(args.index)


def _cmd_query(args):
    index = Index.load(args.index)
    check_data_size(args.data, index.num_rows)
    queries = load_queries(args.queries)
    out = sys.stdout
    report_lines = [report_header(index)] if args.report else None
    with open(args.data, "rb") as data_file:
        for qid, cond in queries:
            hits, scanned, _ = run_query(data_file, index, cond)
            out.write(format_result(qid, hits))
            if report_lines is not None:
                report_lines.append(
                    report_line(qid, len(hits), scanned, index.num_blocks))
    if report_lines is not None:
        with open(args.report, "w", encoding="utf-8", newline="") as f:
            f.write("".join(report_lines))


def _cmd_update(args):
    index = Index.load(args.index)
    check_data_size(args.data, index.num_rows)
    ops = parse_ops(args.ops)
    updater = Updater(args.data, index)
    updater.apply(ops)
    updater.flush(args.index)


def _cmd_page(args):
    index = Index.load(args.index)
    check_data_size(args.data, index.num_rows)
    queries = load_queries(args.queries)
    cond = None
    for qid, c in queries:
        if qid == args.query:
            cond = c
            break
    if cond is None:
        raise ZonecutError(f"查询 id 不在查询文件里: {args.query}")
    with open(args.data, "rb") as data_file:
        hits, scanned, states = run_query(data_file, index, cond,
                                          collect_states=True)
    html = render_page(args.query, len(hits), scanned, states)
    with open(args.out, "w", encoding="utf-8", newline="") as f:
        f.write(html)


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        {"build": _cmd_build, "query": _cmd_query,
         "update": _cmd_update, "page": _cmd_page}[args.cmd](args)
    except (ZonecutError, OSError) as e:
        print(f"zonecut: {e}", file=sys.stderr)
        return 1
    return 0
