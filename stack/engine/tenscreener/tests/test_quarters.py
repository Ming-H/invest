"""报告期日历 + 三线序列纯函数 单测(若羽臣真实数)。"""
import os
import sys
import unittest
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
from quarters import (latest_report_period, latest_annual_period, disclosure_window,
                      pass_streak, pass_lines, resonance, yoy_accel, downshift,
                      divergence, _parse_yoy_row, PASS_LINE)

# 若羽臣真实三线单季同比(2026-08-30 会话双重验证)
RYC = {
    "2023Q4": (13.9, 22.5, 19.6), "2024Q1": (31.7, 86.3, 78.3),
    "2024Q2": (34.4, 66.5, 68.9), "2024Q3": (54.6, 68.1, 76.3),
    "2024Q4": (13.8, 133.2, 135.4), "2025Q1": (54.2, 113.9, 107.6),
    "2025Q2": (79.6, 71.7, 72.2), "2025Q3": (123.4, 73.3, 69.9),
    "2025Q4": (111.4, 87.0, 77.8), "2026Q1": (73.3, 163.8, 167.5),
    "2026Q2": (73.2, 112.1, 106.7),
}


def build_qs(periods):
    qs = {"periods": list(periods), "latest_period": periods[-1],
          "rev_yoy": {}, "parent_yoy": {}, "deduct_yoy": {}}
    for p in periods:
        r, pa, d = RYC[p]
        qs["rev_yoy"][p] = r
        qs["parent_yoy"][p] = pa
        qs["deduct_yoy"][p] = d
    return qs


class TestCalendar(unittest.TestCase):
    def test_latest_report_period_boundaries(self):
        cases = {
            "2024-04-30": (2023, 3), "2024-05-01": (2024, 1),
            "2024-08-31": (2024, 1), "2024-09-01": (2024, 2),
            "2024-10-31": (2024, 2), "2024-11-01": (2024, 3),
            "2024-12-31": (2024, 3), "2025-01-01": (2024, 3),
        }
        for d, expect in cases.items():
            self.assertEqual(latest_report_period(date.fromisoformat(d)), expect, d)

    def test_latest_annual_period(self):
        self.assertEqual(latest_annual_period(date(2026, 4, 30)), (2024, 4))
        self.assertEqual(latest_annual_period(date(2026, 5, 1)), (2025, 4))

    def test_disclosure_window(self):
        self.assertEqual(disclosure_window(date(2026, 8, 30))["period"], (2026, 2))
        self.assertEqual(disclosure_window(date(2026, 4, 15))["period"], (2025, 4))
        self.assertIsNone(disclosure_window(date(2026, 6, 15)))


class TestSeries(unittest.TestCase):
    def test_2024q3_triple(self):
        qs = build_qs(["2023Q4", "2024Q1", "2024Q2", "2024Q3"])
        pl = pass_lines(qs)
        self.assertEqual((pl["rev"], pl["parent"], pl["deduct"]), (True, True, True))
        self.assertEqual(resonance(qs), "triple")
        self.assertEqual(pass_streak(qs["rev_yoy"]), 1)          # 54.6过,34.4断
        self.assertEqual(pass_streak(qs["parent_yoy"]), 3)       # 86.3/66.5/68.1连过

    def test_2024q4_profit_led_not_dropped(self):
        qs = build_qs(["2024Q1", "2024Q2", "2024Q3", "2024Q4"])
        pl = pass_lines(qs)
        self.assertFalse(pl["rev"])                               # 13.8 掉线
        self.assertTrue(pl["parent"] and pl["deduct"])            # 133/135 过线
        self.assertEqual(resonance(qs), "profit_led")
        self.assertGreaterEqual(pl["count"], 2)                   # 不剔除的信息基础

    def test_downshift_2026q1(self):
        qs = build_qs(["2025Q2", "2025Q3", "2025Q4", "2026Q1"])
        ds = downshift(qs["rev_yoy"])                             # 111.4→73.3 = -38.1pp
        self.assertTrue(ds["flag"])
        self.assertAlmostEqual(ds["shift"], 38.1, places=1)
        self.assertEqual(ds["consecutive"], 1)                    # 前一降(123→111)仅12pp

    def test_downshift_consecutive(self):
        ds = downshift({"2025Q4": 110.0, "2026Q1": 75.0, "2026Q2": 40.0})
        self.assertEqual(ds["consecutive"], 2)

    def test_divergence_zhaoyan_style(self):
        dv = divergence({"2025Q2": 885.0}, {"2025Q2": 2334.0})    # 昭衍式
        self.assertTrue(dv["flag"])
        dv2 = divergence({"2026Q2": 112.1}, {"2026Q2": 106.7})    # 若羽臣26Q2:干净
        self.assertFalse(dv2["flag"])

    def test_yoy_accel(self):
        ok, info = yoy_accel({"2024Q1": 31.7, "2024Q2": 34.4, "2024Q3": 54.6, "2024Q4": 13.8})
        self.assertIsInstance(ok, bool)                            # 有数据即返回
        ok2, _ = yoy_accel({"2026Q1": 50.0})                       # 数据不足
        self.assertFalse(ok2)

    def test_parse_yoy_row_field_shapes(self):
        # 问财字段名漂移容忍:单季/单季度_ 前缀都能解析
        row = {"单季营业收入同比增长率[20240930]": 54.6,
               "单季度_归母净利润同比增长率[20240930]": 68.1,
               "单季扣非净利润同比增长率[20240930]": 76.3}
        parsed = _parse_yoy_row(row)
        self.assertEqual(parsed["rev_yoy"], {"2024Q3": 54.6})
        self.assertEqual(parsed["parent_yoy"], {"2024Q3": 68.1})
        self.assertEqual(parsed["deduct_yoy"], {"2024Q3": 76.3})

    def test_pass_streak_none_breaks(self):
        self.assertEqual(pass_streak({"2026Q1": None, "2025Q4": 80.0}), 0)


if __name__ == "__main__":
    unittest.main()
