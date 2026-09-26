"""查询条件：解析、逐行匹配、按块 min/max 剪枝。

条件用元组树表示：
    ("eq", v) ("ne", v) ("lt", v) ("le", v) ("gt", v) ("ge", v)
    ("between", lo, hi)
    ("and", (子条件, ...))   -- 子条件都是原子条件，不嵌套
"""

from . import ZonecutError

_ATOMIC_OPS = ("eq", "ne", "lt", "le", "gt", "ge", "between")
_TS_LIMIT = 10 ** 12


def _check_value(value, field):
    if isinstance(value, bool) or not isinstance(value, int):
        raise ZonecutError(f"条件的 {field} 不是整数: {value!r}")
    if value < 0 or value >= _TS_LIMIT:
        raise ZonecutError(f"条件的 {field} 超出 [0, 10^12): {value!r}")
    return value


def parse_condition(obj):
    """把查询 JSON 对象解析成条件元组树，非法形式抛 ZonecutError。"""
    if not isinstance(obj, dict):
        raise ZonecutError(f"条件不是对象: {obj!r}")
    op = obj.get("op")
    if op in ("eq", "ne", "lt", "le", "gt", "ge"):
        if "value" not in obj:
            raise ZonecutError(f"{op} 缺少 value")
        return (op, _check_value(obj["value"], "value"))
    if op == "between":
        if "lo" not in obj or "hi" not in obj:
            raise ZonecutError("between 缺少 lo/hi")
        lo = _check_value(obj["lo"], "lo")
        hi = _check_value(obj["hi"], "hi")
        if lo > hi:
            raise ZonecutError(f"between 要求 lo <= hi: {lo} > {hi}")
        return ("between", lo, hi)
    if op == "and":
        args = obj.get("args")
        if not isinstance(args, list) or len(args) < 2:
            raise ZonecutError("and 需要至少 2 个子条件")
        subs = []
        for arg in args:
            sub = parse_condition(arg)
            if sub[0] == "and":
                raise ZonecutError("and 不允许嵌套")
            subs.append(sub)
        return ("and", tuple(subs))
    raise ZonecutError(f"不支持的条件形式: {op!r}")


def compile_match(cond):
    """编译成逐行判定函数 f(ts) -> bool。"""
    op = cond[0]
    if op == "eq":
        v = cond[1]
        return lambda ts: ts == v
    if op == "ne":
        v = cond[1]
        return lambda ts: ts != v
    if op == "lt":
        v = cond[1]
        return lambda ts: ts < v
    if op == "le":
        v = cond[1]
        return lambda ts: ts <= v
    if op == "gt":
        v = cond[1]
        return lambda ts: ts > v
    if op == "ge":
        v = cond[1]
        return lambda ts: ts >= v
    if op == "between":
        lo, hi = cond[1], cond[2]
        return lambda ts: lo <= ts <= hi
    if op == "and":
        fs = [compile_match(c) for c in cond[1]]
        return lambda ts: all(f(ts) for f in fs)
    raise ZonecutError(f"不支持的条件形式: {op!r}")


def compile_prune(cond):
    """编译成块级剪枝函数 f(min_ts, max_ts) -> bool（True = 整块剪掉）。

    判据（README 2.2，边界算命中）：
        eq V        : V < m 或 V > M
        ne V        : m == M == V
        lt V        : m >= V
        le V        : m > V
        gt V        : M <= V
        ge V        : M < V
        between A B : B < m 或 A > M
        and [...]   : 任一项能剪掉这一块
    """
    op = cond[0]
    if op == "eq":
        v = cond[1]
        return lambda m, M: v < m or v > M
    if op == "ne":
        v = cond[1]
        return lambda m, M: m == M == v
    if op == "lt":
        v = cond[1]
        return lambda m, M: m >= v
    if op == "le":
        v = cond[1]
        return lambda m, M: m > v
    if op == "gt":
        v = cond[1]
        return lambda m, M: M <= v
    if op == "ge":
        v = cond[1]
        return lambda m, M: M < v
    if op == "between":
        lo, hi = cond[1], cond[2]
        return lambda m, M: hi < m or lo > M
    if op == "and":
        fs = [compile_prune(c) for c in cond[1]]
        return lambda m, M: any(f(m, M) for f in fs)
    raise ZonecutError(f"不支持的条件形式: {op!r}")
