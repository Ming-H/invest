"""L2 打分修复回归:key 错名/动态期期/L≠N/背离/M gate。"""
import os
import sys
import unittest
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
import l2_analyzer
from l2_analyzer import score_canslim, score_jingshui, fetch_metrics


class TestKeyRegression(unittest.TestCase):
    """回归:历史 bug —— L1 输出 profit_growth/rev_growth,L2 曾读 *_2025 永远 0 分。"""

    def setUp(self):
        # op_cf 与 parent_profit_annual 均为年度累计口径(2026-11-07 修复)
        self.m = {"roe": 20.0, "gross_margin": 60.0, "pos_1y": 0.97,
                  "circ_mv": 80e8, "fund_hold": 5.0, "op_cf": 2.0e8,
                  "parent_profit_annual": 1.5e8,
                  "parent_profit": 0.4e8, "deduct_profit": 0.38e8}
        self.gate = {"verdict": "on", "method": "ma250", "index_gain_1y": 5.0}
        self.l1c = {"profit_growth": 68.1, "rev_growth": 54.6,
                    "price_history": {"gain_1yr": 25.0}}

    def test_c_positive_with_valid_l1_key(self):
        scores, _ = score_canslim(self.m, self.l1c, gate=self.gate)
        self.assertGreater(scores["C"], 0)             # 修复前恒 0

    def test_c_prefers_quarterly_latest(self):
        l1c = {**self.l1c, "quarterly": {
            "latest_period": "2026Q2", "periods": ["2026Q2"],
            "rev_yoy": {"2026Q2": 73.2}, "parent_yoy": {"2026Q2": 112.1},
            "deduct_yoy": {"2026Q2": 106.7}}}
        scores, _ = score_canslim(self.m, l1c, gate=self.gate)
        self.assertAlmostEqual(scores["C"], min(10, 112.1 / 15))

    def test_L_differs_from_N(self):
        scores, _ = score_canslim(self.m, self.l1c, gate=self.gate)
        # N=pos_1y*10≈9.7;L=5+(25-5)/4=10 → 不再重复
        self.assertAlmostEqual(scores["N"], 9.7, places=1)
        self.assertAlmostEqual(scores["L"], 10.0, places=1)

    def test_M_uses_gate(self):
        s_on, _ = score_canslim(self.m, self.l1c, gate={"verdict": "on"})
        s_off, _ = score_canslim(self.m, self.l1c, gate={"verdict": "off"})
        self.assertEqual(s_on["M"], 8)
        self.assertEqual(s_off["M"], 2)

    def test_jingshui_rev_growth_key_fixed(self):
        s, _ = score_jingshui(self.m, self.l1c)
        self.assertEqual(s["主营增速≥40%"], 2)          # 修复前恒 0

    def test_jingshui_pos_1y_tiers(self):
        s, _ = score_jingshui(self.m, self.l1c)
        self.assertEqual(s["创一年新高/强势"], 2)       # pos 0.97≥0.95
        m2 = {**self.m, "pos_1y": 0.85}
        s2, _ = score_jingshui(m2, self.l1c)
        self.assertEqual(s2["创一年新高/强势"], 1)

    def test_jingshui_cashflow(self):
        s, _ = score_jingshui(self.m, self.l1c)        # 年度CF 2.0 / 年度归母 1.5 = 1.33 ≥1 → 2分
        self.assertEqual(s["超级现金流"], 2)
        m2 = {**self.m, "op_cf": 0.9e8}                # 0.6 → 0分
        s2, _ = score_jingshui(m2, self.l1c)
        self.assertEqual(s2["超级现金流"], 0)

    def test_jingshui_cashflow_ignores_single_quarter_profit(self):
        """口径回归(2026-11-07): 比率只认年度累计归母, 不再误用单季归母。"""
        m = {**self.m, "op_cf": 2.0e8, "parent_profit_annual": 1.8e8,
             "parent_profit": 0.4e8}                   # 单季0.4亿(旧bug下 2.0/0.4=5 恒满分)
        s, _ = score_jingshui(m, self.l1c)             # 正确口径 2.0/1.8≈1.1 → 2分(而非靠错配)
        self.assertEqual(s["超级现金流"], 2)
        m2 = {**self.m, "op_cf": 1.3e8, "parent_profit_annual": 1.8e8,
              "parent_profit": 0.4e8}                  # 0.72 → 1分(旧口径 3.25 → 虚高2分)
        s2, _ = score_jingshui(m2, self.l1c)
        self.assertEqual(s2["超级现金流"], 1)

    def test_divergence_gap_recorded(self):
        l1c = {**self.l1c, "quarterly": {
            "latest_period": "2025Q2", "periods": ["2025Q2"],
            "rev_yoy": {"2025Q2": 50.0}, "parent_yoy": {"2025Q2": 885.0},
            "deduct_yoy": {"2025Q2": 2334.0},
            "divergence": {"gaps": {"2025Q2": 1449.0}, "flag": True, "at": "2025Q2"}}}
        s, gaps = score_jingshui(self.m, l1c)
        self.assertEqual(s["扣非归母一致性"], 0)
        self.assertTrue(any("需Claude拆解" in g for g in gaps))


class TestDynamicPeriods(unittest.TestCase):
    def test_query_contains_dynamic_periods(self):
        # 冻结日期 2026-08-30 → 最新季 2026半年报、最新年报 2025年报
        import tempfile
        import wencai
        d = tempfile.mkdtemp()
        wencai.set_cache_dir(d)
        try:
            import json as _json
            captured = {}

            def fake_iwencai(query, limit=80, cli_path=None, timeout=120):
                captured["query"] = query
                return [{"股票简称": "若羽臣"}]

            orig = l2_analyzer.iwencai
            l2_analyzer.iwencai = fake_iwencai
            try:
                m = fetch_metrics(["若羽臣"], today=date(2026, 8, 30))
            finally:
                l2_analyzer.iwencai = orig
            q = captured["query"]
            # 8月底日历保守语义:H1 至 8/31 才完整 → 最新季取一季报
            self.assertIn("2026年一季报单季归母净利润", q)
            self.assertIn("2025年年报归母净利润", q)
            self.assertIn("2025年年报经营活动产生的现金流量净额", q)
            self.assertIn("近一年最高价前复权", q)
            self.assertNotIn("换手率", q)               # 冗余字段已删(2026-11-07)
            self.assertEqual(m["若羽臣"]["report_period_used"], "20260331")
            self.assertEqual(m["若羽臣"]["annual_period_used"], "20251231")
        finally:
            wencai.set_cache_dir(None)


if __name__ == "__main__":
    unittest.main()
