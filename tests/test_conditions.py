"""剪枝判据与条件解析：逐条对齐 README 2.2 的表。"""

import unittest

from .helpers import run_cli, sample  # noqa: F401  (保持 helpers 的 sys.path 副作用)
from zonecut.conditions import Condition, AndCondition, parse_condition
from zonecut.errors import ZonecutError


class TestPruneTable(unittest.TestCase):
    """块内 [m, M] = [100, 200]。"""

    def check(self, cond, m, M, expected):
        self.assertEqual(cond.prune(m, M), expected,
                         "%s on [%s, %s]" % (cond.describe(), m, M))

    def test_eq(self):
        self.check(Condition("eq", 99), 100, 200, True)   # V < m
        self.check(Condition("eq", 201), 100, 200, True)  # V > M
        self.check(Condition("eq", 100), 100, 200, False)
        self.check(Condition("eq", 200), 100, 200, False)
        self.check(Condition("eq", 150), 100, 200, False)

    def test_ne(self):
        self.check(Condition("ne", 150), 150, 150, True)   # 整块常量 == V
        self.check(Condition("ne", 150), 100, 200, False)  # 块内有别的值
        self.check(Condition("ne", 99), 100, 200, False)   # V 在区间外不是剪枝理由
        self.check(Condition("ne", 300), 100, 200, False)

    def test_lt_le(self):
        self.check(Condition("lt", 100), 100, 200, True)   # m >= V
        self.check(Condition("lt", 101), 100, 200, False)
        self.check(Condition("le", 99), 100, 200, True)    # m > V
        self.check(Condition("le", 100), 100, 200, False)

    def test_gt_ge(self):
        self.check(Condition("gt", 200), 100, 200, True)   # M <= V，边界算命中
        self.check(Condition("gt", 199), 100, 200, False)
        self.check(Condition("ge", 201), 100, 200, True)   # M < V
        self.check(Condition("ge", 200), 100, 200, False)  # M == V 不能剪

    def test_between(self):
        self.check(Condition("between", 0, 99), 100, 200, True)    # B < m
        self.check(Condition("between", 201, 300), 100, 200, True) # A > M
        self.check(Condition("between", 99, 100), 100, 200, False) # 闭区间
        self.check(Condition("between", 200, 201), 100, 200, False)
        self.check(Condition("between", 120, 180), 100, 200, False)

    def test_and(self):
        cond = AndCondition([Condition("gt", 250), Condition("lt", 300)])
        self.check(cond, 100, 200, True)   # gt 250 能剪
        cond = AndCondition([Condition("ge", 100), Condition("le", 200)])
        self.check(cond, 100, 200, False)  # 都剪不掉
        cond = AndCondition([Condition("ne", 7), Condition("between", 50, 60)])
        self.check(cond, 100, 200, True)   # between 能剪

    def test_match(self):
        self.assertTrue(Condition("eq", 5).match(5))
        self.assertFalse(Condition("ne", 5).match(5))
        self.assertTrue(Condition("between", 3, 5).match(5))
        self.assertFalse(Condition("between", 3, 5).match(6))
        cond = AndCondition([Condition("gt", 1), Condition("le", 4)])
        self.assertTrue(cond.match(4))
        self.assertFalse(cond.match(1))


class TestParse(unittest.TestCase):
    def test_ok(self):
        cond = parse_condition({"op": "between", "lo": 1, "hi": 2})
        self.assertIsInstance(cond, Condition)
        cond = parse_condition({"op": "and", "args": [
            {"op": "ge", "value": 1}, {"op": "ne", "value": 2}]})
        self.assertIsInstance(cond, AndCondition)

    def test_reject(self):
        bad = [
            {"op": "or", "args": []},
            {"op": "not", "args": [{"op": "eq", "value": 1}]},
            {"op": "eq"},                                   # 缺 value
            {"op": "between", "lo": 5, "hi": 1},            # lo > hi
            {"op": "and", "args": [{"op": "eq", "value": 1}]},  # 少于 2 项
            {"op": "and", "args": [{"op": "eq", "value": 1},
                                   {"op": "and", "args": []}]},  # 嵌套
            {"op": "eq", "value": -1},
            {"op": "eq", "value": 10**12},
            {"op": "eq", "value": "5"},
            {"op": "unknown", "value": 1},
        ]
        for obj in bad:
            with self.subTest(obj=obj):
                with self.assertRaises(ZonecutError):
                    parse_condition(obj)


if __name__ == "__main__":
    unittest.main()
