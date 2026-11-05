"""scorer F4/F7 单测。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
from scorer import f4_growth, f7_valuation, compute_tps, clamp


class TestF4(unittest.TestCase):
    def test_prev_missing_gets_median_not_five(self):
        # 修复历史 bug:prev 恒 None 时旧代码恒给 5 分
        with_prev = f4_growth(50.0, 40.0, 60.0)      # 加速 15
        without = f4_growth(50.0, None, 60.0)        # prev 缺失 8
        decelerate = f4_growth(50.0, 80.0, 60.0)     # 减速 4
        self.assertEqual(with_prev - without, 7)     # 15-8
        self.assertEqual(without - decelerate, 4)    # 8-4

    def test_persistence_and_single_line_discount(self):
        base = f4_growth(55.0, 50.0, 70.0, streak=2, lines_passed=3)
        single = f4_growth(55.0, 50.0, 70.0, streak=2, lines_passed=1)
        self.assertGreater(base, single)             # 单线孤立打折(若羽臣24Q4教训)
        streak3 = f4_growth(55.0, 50.0, 70.0, streak=3, lines_passed=3)
        self.assertGreater(streak3, base)

    def test_level_buckets(self):
        self.assertGreater(f4_growth(90.0, 50.0, 100.0), f4_growth(45.0, 50.0, 40.0))

    def test_backward_compatible_signature(self):
        v = f4_growth(45.8, 76.3, 53.1)              # 旧调用姿势仍可用
        self.assertTrue(0 <= v <= 100)


class TestF7(unittest.TestCase):
    def test_peg_branch_alive(self):
        self.assertEqual(f7_valuation(pe_ttm=50, peg=0.7), 90.0)
        self.assertEqual(f7_valuation(pe_ttm=50, peg=1.0), 70.0)
        self.assertEqual(f7_valuation(pe_ttm=50, peg=1.5), 50.0)
        self.assertEqual(f7_valuation(pe_ttm=50, peg=3.0), 25.0)

    def test_peg_priority_over_pe(self):
        # PEG 1.5 → 50;若无 PEG,PE=50 → 35(≤80档)。优先 PEG 分支
        self.assertEqual(f7_valuation(pe_ttm=50, peg=1.5), 50.0)
        self.assertEqual(f7_valuation(pe_ttm=50), 35.0)

    def test_growth_cap_100_for_peg(self):
        # 增速封顶 100:PE=50/增速500 的 PEG 应按 50/100=0.5 → 90,而非 0.1
        from l3_evaluator import evaluate
        # 直接验证公式语义在 evaluate 内:构造 l2 单只
        l2 = [{"metrics": {"pe": 50.0, "circ_mv": 100 * 1e8},
               "l1": {"rev_growth": 500.0, "profit_growth": 500.0,
                      "quarterly": {"latest_period": "2026Q2", "periods": ["2026Q2"],
                                    "rev_yoy": {"2026Q2": 500.0},
                                    "parent_yoy": {"2026Q2": 500.0},
                                    "deduct_yoy": {}, "streak_max": 1,
                                    "pass_lines": {"count": 1}}}}]
        out = evaluate(l2)
        self.assertEqual(out[0]["tps_detail"]["F7"], 90.0)   # PEG=50/100=0.5


class TestTps(unittest.TestCase):
    def test_clamp_none_mid(self):
        self.assertEqual(clamp(None), 50.0)

    def test_weights_sum(self):
        from scorer import WEIGHTS
        self.assertAlmostEqual(sum(WEIGHTS.values()), 1.0)


if __name__ == "__main__":
    unittest.main()
