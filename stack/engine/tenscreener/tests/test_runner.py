"""runner 五阶段判定矩阵 + 辅助函数 单测。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
import runner
from runner import classify_phase, industry_resonance, to_symbol
from quarters import analyze_quarterly


def make_ana(periods_vals):
    """periods_vals: {qlabel: (rev,parent,deduct)} → analyze_quarterly 结果。"""
    qs = {"periods": sorted(periods_vals), "latest_period": sorted(periods_vals)[-1],
          "rev_yoy": {}, "parent_yoy": {}, "deduct_yoy": {}}
    for p, (r, pa, d) in periods_vals.items():
        qs["rev_yoy"][p] = r
        qs["parent_yoy"][p] = pa
        qs["deduct_yoy"][p] = d
    return analyze_quarterly(qs)


# 典型序列:三线连续过线(streak≥2)
ANA_STRONG = make_ana({"2025Q3": (60, 70, 65), "2025Q4": (80, 90, 85)})
# 单季过线(streak=1)
ANA_SINGLE = make_ana({"2025Q3": (10, 5, 5), "2025Q4": (80, 90, 85)})
# 最新季仅1线过(退潮)
ANA_FADE = make_ana({"2025Q3": (80, 90, 85), "2025Q4": (45, 20, 15)})

M_HIGH = {"pos_1y": 0.95, "drawdown": 0.05, "run_multiple": 2.0}
M_PULL = {"pos_1y": 0.75, "drawdown": 0.30, "run_multiple": 2.0}
M_DEEP = {"pos_1y": 0.50, "drawdown": 0.45, "run_multiple": 2.0}


class TestClassifyPhase(unittest.TestCase):
    def test_matrix(self):
        cases = [
            # (m, ana, gate, 期望阶段)
            (M_HIGH, ANA_STRONG, "on", "主升期"),
            (M_HIGH, ANA_STRONG, "off", "强势待确认"),     # 创新高但缺大盘β
            (M_HIGH, ANA_SINGLE, "on", "强势待确认"),      # streak<2 等连续验证
            (M_PULL, ANA_STRONG, "on", "回调击球区"),
            (M_PULL, ANA_STRONG, "off", "回调击球区"),     # 回调区不因β改标签
            (M_DEEP, ANA_STRONG, "on", "深调考验区"),      # 扛住纪律区
            (M_HIGH, ANA_FADE, "on", "退潮"),              # 增速断档优先
            (M_PULL, ANA_FADE, "on", "退潮"),
            (M_HIGH, None, "on", "数据不足"),
        ]
        for m, ana, gate, want in cases:
            phase, reason = classify_phase(m, ana, gate)
            self.assertEqual(phase, want, f"{m}/{gate}: {reason}")

    def test_pullback_with_gate_off_notes_beta(self):
        _, reason = classify_phase(M_PULL, ANA_STRONG, "off")
        self.assertIn("暂缓", reason)

    def test_boundaries(self):
        # dd=0.40 仍在击球区(闭区间);dd=0.41 深调;pos=0.90 走创新高分枝
        self.assertEqual(classify_phase({"pos_1y": 0.75, "drawdown": 0.40}, ANA_STRONG, "on")[0],
                         "回调击球区")
        self.assertEqual(classify_phase({"pos_1y": 0.75, "drawdown": 0.41}, ANA_STRONG, "on")[0],
                         "深调考验区")
        self.assertEqual(classify_phase({"pos_1y": 0.90, "drawdown": 0.10}, ANA_STRONG, "on")[0],
                         "主升期")

    def test_phase_order_covers_all_labels(self):
        for lbl in ("主升期", "回调击球区", "强势待确认", "深调考验区",
                    "利润未兑现", "退潮", "数据不足"):
            self.assertIn(lbl, runner.PHASE_ORDER)

    def test_turnaround_not_labeled_fade(self):
        # 扭亏型(赛力斯/寒武纪/江特):利润线同比无意义但营收在 → 利润未兑现,非退潮
        qs = {"periods": ["2023Q4", "2024Q1"], "latest_period": "2024Q1",
              "rev_yoy": {"2023Q4": 80, "2024Q1": 120},
              "parent_yoy": {"2023Q4": None, "2024Q1": None},
              "deduct_yoy": {"2023Q4": None, "2024Q1": None}}
        phase, reason = classify_phase(M_HIGH, analyze_quarterly(qs), "on")
        self.assertEqual(phase, "利润未兑现")
        self.assertIn("亏损不买", reason)
        # 利润线有数据且确实掉线 → 仍是退潮
        qs2 = {"periods": ["2023Q4", "2024Q1"], "latest_period": "2024Q1",
               "rev_yoy": {"2023Q4": 80, "2024Q1": 10},
               "parent_yoy": {"2023Q4": 50, "2024Q1": 5},
               "deduct_yoy": {"2023Q4": 45, "2024Q1": 3}}
        phase2, _ = classify_phase(M_HIGH, analyze_quarterly(qs2), "on")
        self.assertEqual(phase2, "退潮")


class TestHelpers(unittest.TestCase):
    def test_to_symbol(self):
        self.assertEqual(to_symbol("300308.SZ"), "sz300308")
        self.assertEqual(to_symbol("003010.SZ"), "sz003010")
        self.assertEqual(to_symbol("600519.SH"), "sh600519")
        self.assertEqual(to_symbol("600519"), "sh600519")
        self.assertEqual(to_symbol("300308"), "sz300308")

    def test_industry_resonance(self):
        rows = [{"code": "1", "industry_l1": "通信"}, {"code": "2", "industry_l1": "通信"},
                {"code": "3", "industry_l1": "有色"}, {"code": "4", "industry_l1": None}]
        res, counter = industry_resonance(rows)
        self.assertEqual(res["1"], 2)
        self.assertEqual(res["3"], 1)
        self.assertEqual(counter["通信"], 2)

    def test_qs_from_table_window(self):
        tbl = {"2025Q1": (1, 1, 1), "2025Q2": (2, 2, 2), "2025Q3": (3, 3, 3),
               "2025Q4": (4, 4, 4), "2026Q1": (5, 5, 5)}
        qs = runner._qs_from_table(tbl, "2026Q1", sorted(tbl))
        self.assertEqual(qs["periods"], ["2025Q2", "2025Q3", "2025Q4", "2026Q1"])
        self.assertEqual(qs["latest_period"], "2026Q1")
        self.assertIsNone(runner._qs_from_table(tbl, "2023Q1", sorted(tbl)))


class TestBacktestAlignment(unittest.TestCase):
    """披露滞后对齐:防未来数据泄露。"""

    def test_disclosure_calendar(self):
        # 20+ 根月线触发 backtest 内部月份→报告期映射(垫够 metrics_at 的最少根数)
        tbl = {"2024Q3": (50, 50, 50), "2024Q4": (60, 60, 60), "2025Q1": (70, 70, 70)}
        rows = [[f"{y}-{m:02d}-15", "1", str(10 + m), str(10 + m), "9", "0"]
                for y in (2023, 2024, 2025) for m in range(1, 13)]
        gates = [("2024-01-01", "on")]
        tl = runner.backtest("000000.XX", "测试", rows, tbl, gates,
                             "2025-01-01", "2025-08-31")
        by_month = {t["month"]: t["disclosed"] for t in tl}
        self.assertEqual(by_month["2025-04"], "2024Q3")   # 年报4月底才出
        self.assertEqual(by_month["2025-05"], "2025Q1")
        self.assertEqual(by_month["2025-08"], "2025Q1")   # 8月底前最新=Q1


if __name__ == "__main__":
    unittest.main()
