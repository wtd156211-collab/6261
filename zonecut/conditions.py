"""查询条件：解析、逐行匹配、按块统计剪枝。

支持 eq/ne/lt/le/gt/ge/between/and（and 的参数为 >= 2 个原子条件、不嵌套）。
剪枝判据见 README 2.2：边界算命中，ne 只在整块常量时剪，空块一律剪。
"""

import json

from .errors import ZonecutError

_ATOMIC_OPS = ("eq", "ne", "lt", "le", "gt", "ge", "between")


class Condition:
    """原子条件：op + 至多两个界值。"""

    __slots__ = ("op", "lo", "hi")

    def __init__(self, op, lo, hi=None):
        self.op = op
        self.lo = lo
        self.hi = hi

    def match(self, ts):
        op = self.op
        if op == "eq":
            return ts == self.lo
        if op == "ne":
            return ts != self.lo
        if op == "lt":
            return ts < self.lo
        if op == "le":
            return ts <= self.lo
        if op == "gt":
            return ts > self.lo
        if op == "ge":
            return ts >= self.lo
        return self.lo <= ts <= self.hi  # between

    def prune(self, mn, mx):
        """块内 [mn, mx] 是否不可能有命中（True = 整块剪掉）。"""
        op = self.op
        if op == "eq":
            return self.lo < mn or self.lo > mx
        if op == "ne":
            return mn == mx == self.lo
        if op == "lt":
            return mn >= self.lo
        if op == "le":
            return mn > self.lo
        if op == "gt":
            return mx <= self.lo
        if op == "ge":
            return mx < self.lo
        return self.hi < mn or self.lo > mx  # between

    def describe(self):
        if self.op == "between":
            return "between %d %d" % (self.lo, self.hi)
        return "%s %d" % (self.op, self.lo)


class AndCondition:
    """and：全部子条件都命中才算命中；任一子条件能剪则整块剪掉。"""

    __slots__ = ("args",)

    def __init__(self, args):
        self.args = args

    def match(self, ts):
        return all(arg.match(ts) for arg in self.args)

    def prune(self, mn, mx):
        return any(arg.prune(mn, mx) for arg in self.args)

    def describe(self):
        return "and [%s]" % ", ".join(arg.describe() for arg in self.args)


def _check_value(obj, key):
    value = obj.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ZonecutError("条件字段 %r 缺失或不是整数" % key)
    if not 0 <= value < 10**12:
        raise ZonecutError("条件字段 %r 超出取值范围 [0, 10^12)" % key)
    return value


def parse_condition(obj):
    """把查询 JSON 对象解析成条件树，形式不支持抛 ZonecutError。"""
    if not isinstance(obj, dict):
        raise ZonecutError("条件必须是 JSON 对象")
    op = obj.get("op")
    if op in _ATOMIC_OPS:
        if op == "between":
            lo = _check_value(obj, "lo")
            hi = _check_value(obj, "hi")
            if lo > hi:
                raise ZonecutError("between 要求 lo <= hi")
            return Condition(op, lo, hi)
        return Condition(op, _check_value(obj, "value"))
    if op == "and":
        args = obj.get("args")
        if not isinstance(args, list) or len(args) < 2:
            raise ZonecutError("and 需要至少 2 个子条件")
        parsed = []
        for arg in args:
            cond = parse_condition(arg)
            if not isinstance(cond, Condition):
                raise ZonecutError("and 的子条件必须是原子条件，不允许嵌套")
            parsed.append(cond)
        return AndCondition(parsed)
    raise ZonecutError("不支持的条件形式: %r" % (op,))


def parse_queries(path):
    """解析查询文件，返回 [(查询 id, 条件), ...]，保持文件行序。"""
    queries = []
    seen = set()
    try:
        with open(path, "r", encoding="utf-8") as fileobj:
            for lineno, line in enumerate(fileobj, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ZonecutError("查询文件第 %d 行不是合法 JSON" % lineno) from exc
                if not isinstance(obj, dict) or not isinstance(obj.get("id"), str):
                    raise ZonecutError("查询文件第 %d 行缺少字符串类型的 id" % lineno)
                query_id = obj["id"]
                if query_id in seen:
                    raise ZonecutError("查询 id 重复: %r" % query_id)
                seen.add(query_id)
                queries.append((query_id, parse_condition(obj)))
    except OSError as exc:
        raise ZonecutError("查询文件不可读: %s" % exc) from exc
    return queries
