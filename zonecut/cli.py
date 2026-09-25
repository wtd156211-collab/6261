"""zonecut 命令行：build / query / update / page。

退出码：0 成功；1 输入不可用；2 用法错误。非 0 不写任何输出。
"""

import argparse
import sys

from . import DEFAULT_BLOCK_SIZE
from .conditions import parse_queries
from .engine import (apply_changes, format_report, format_result,
                     parse_changes, run_query)
from .errors import ZonecutError
from .index import BlockIndex
from .page import render_page


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="zonecut", description="块级统计与查询剪枝（单列 ts）")
    sub = parser.add_subparsers(dest="command", required=True)

    p_build = sub.add_parser("build", help="建索引：顺序读一遍数据文件")
    p_build.add_argument("data")
    p_build.add_argument("index")
    p_build.add_argument("--block-size", type=int, default=DEFAULT_BLOCK_SIZE)

    p_query = sub.add_parser("query", help="按索引剪枝后执行查询文件")
    p_query.add_argument("data")
    p_query.add_argument("index")
    p_query.add_argument("queries")
    p_query.add_argument("--report", default=None)

    p_update = sub.add_parser("update", help="按变更文件就地更新数据与索引")
    p_update.add_argument("data")
    p_update.add_argument("index")
    p_update.add_argument("changes")

    p_page = sub.add_parser("page", help="把一条查询的块级结果渲染成 HTML")
    p_page.add_argument("data")
    p_page.add_argument("index")
    p_page.add_argument("queries")
    p_page.add_argument("--query", required=True, help="查询 id")
    p_page.add_argument("--out", required=True)

    args = parser.parse_args(argv)
    try:
        if args.command == "build":
            _cmd_build(args)
        elif args.command == "query":
            _cmd_query(args)
        elif args.command == "update":
            _cmd_update(args)
        else:
            _cmd_page(args)
    except ZonecutError as exc:
        print("zonecut: %s" % exc, file=sys.stderr)
        return 1
    return 0


def _cmd_build(args):
    if args.block_size < 1:
        raise ZonecutError("块容量必须是 >= 1 的整数")
    index = BlockIndex.build(args.data, args.block_size)
    index.save(args.index)


def _cmd_query(args):
    index = BlockIndex.load(args.index)
    queries = parse_queries(args.queries)
    results = []
    out_parts = []
    try:
        with open(args.data, "rb") as data_file:
            for query_id, cond in queries:
                result = run_query(data_file, index, query_id, cond)
                out_parts.append(format_result(result))
                result.hits = ()  # 命中行已落入文本，释放元组控制峰值内存
                results.append(result)
    except OSError as exc:
        raise ZonecutError("数据文件不可读: %s" % exc) from exc
    output = "".join(out_parts)
    report = format_report(index, results) if args.report else None
    if args.report:
        try:
            with open(args.report, "w", encoding="utf-8", newline="") as fileobj:
                fileobj.write(report)
        except OSError as exc:
            raise ZonecutError("报告文件不可写: %s" % exc) from exc
    sys.stdout.write(output)


def _cmd_update(args):
    index = BlockIndex.load(args.index)
    changes = parse_changes(args.changes)
    apply_changes(args.data, index, changes)
    index.save(args.index)


def _cmd_page(args):
    index = BlockIndex.load(args.index)
    queries = parse_queries(args.queries)
    cond = None
    for query_id, candidate in queries:
        if query_id == args.query:
            cond = candidate
            break
    if cond is None:
        raise ZonecutError("查询 id 不存在于查询文件: %r" % args.query)
    try:
        with open(args.data, "rb") as data_file:
            result = run_query(data_file, index, args.query, cond, want_states=True)
    except OSError as exc:
        raise ZonecutError("数据文件不可读: %s" % exc) from exc
    html = render_page(index, result, cond)
    try:
        with open(args.out, "w", encoding="utf-8", newline="") as fileobj:
            fileobj.write(html)
    except OSError as exc:
        raise ZonecutError("HTML 文件不可写: %s" % exc) from exc
