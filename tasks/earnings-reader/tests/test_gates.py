#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_gates.py — 闸门与渲染纯逻辑单测(无网络)。

锚点案例来自 h1-monitor v7.3 规则注释(*ST联翔/诺德股份/时代万恒等)与
分档严格边界(>100/>50 为严格大于, 压线落二档)。运行: python3 tests/test_gates.py
v3(2026-11-05): process_formal 改双返回(passed, rejected); 判定/L2/预告测试随功能删除。
"""
import importlib.util
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))
import pipeline as P     # noqa: E402


class TestQuarterCalendar(unittest.TestCase):
    def test_labels(self):
        self.assertEqual(P.quarter_of("2026-09-30"), (2026, 3))
        self.assertEqual(P.quarter_of("2026-03-31"), (2026, 1))
        self.assertEqual(P.quarter_of("2026-06-30"), (2026, 2))
        self.assertEqual(P.qlabel(*P.prev_quarter(2026, 1)), "25Q4")
        self.assertEqual(P.display_quarters(2026, 3), ["25Q4", "26Q1", "26Q2", "26Q3"])
        self.assertEqual(P.display_quarters(2027, 1), ["26Q2", "26Q3", "26Q4", "27Q1"])
        self.assertEqual(P.yoy_of("26Q3"), "25Q3")
        self.assertEqual(P.qoq_of("26Q1"), "25Q4")

    def test_amounts_q1_identity(self):
        """Q1 单季 = 一季报累计本身(不是 Q1−FY, 泛化最易错处)。"""
        cum = {"2026-03-31": {"rev": 100, "np": 10, "ded": 9},
               "2025-12-31": {"rev": 400, "np": 40, "ded": 38}}
        qa = P.quarter_amounts(cum, 2026, 1, n_back=2)
        self.assertEqual(qa["26Q1"]["rev"], 100)

    def test_amounts_diff(self):
        cum = {"2026-03-31": {"rev": 100, "np": 10, "ded": 9},
               "2026-06-30": {"rev": 250, "np": 30, "ded": 28},
               "2026-09-30": {"rev": 400, "np": 50, "ded": 45}}
        qa = P.quarter_amounts(cum, 2026, 3)
        self.assertEqual(qa["26Q2"]["rev"], 150)
        self.assertEqual(qa["26Q3"]["rev"], 150)


class TestFormalGates(unittest.TestCase):
    ROW = [{"SECURITY_CODE": "X", "SECURITY_NAME_ABBR": "正常股",
            "NOTICE_DATE": "2026-10-30 00:00:00",
            "TOTAL_OPERATE_INCOME": 400, "PARENT_NETPROFIT": 50}]

    def run_gate(self, q, name="正常股"):
        return P.process_formal(self.ROW, {"X": ({}, q)}, {"X": name}, 2026, 3)

    def test_r1_fake_growth(self):
        """营收+50% 但归母同比+10% -> R1 假成长出局(诺德股份型), 带同比数值入 rejected。"""
        q = {"26Q3": {"rev": 150, "np": 11, "ded": 10},
             "25Q3": {"rev": 100, "np": 10, "ded": 9},
             "26Q2": {"rev": 100, "np": 20, "ded": 18}}
        ok, rej = self.run_gate(q)
        self.assertEqual(ok, [])
        self.assertEqual(rej[0]["why"], "同比未达40%")
        self.assertAlmostEqual(rej[0]["yoy"]["np"], 10.0)

    def test_r3_st(self):
        q = {"26Q3": {"rev": 150, "np": 25, "ded": 22},
             "25Q3": {"rev": 100, "np": 10, "ded": 9},
             "26Q2": {"rev": 100, "np": 15, "ded": 14}}
        ok, rej = self.run_gate(q, name="*ST联翔")
        self.assertEqual(ok, [])
        self.assertEqual(rej[0]["why"], "ST/退市警示出局")
        ok2, _ = self.run_gate(q, name="XX退")
        self.assertEqual(ok2, [])

    def test_r2_loss(self):
        """当季归母亏损 -> R2 出局, 即便营收高增。"""
        q = {"26Q3": {"rev": 150, "np": -5, "ded": -6},
             "25Q3": {"rev": 100, "np": 10, "ded": 9},
             "26Q2": {"rev": 100, "np": 15, "ded": 14}}
        ok, rej = self.run_gate(q)
        self.assertEqual(ok, [])
        self.assertEqual(rej[0]["why"], "当季亏损出局")

    def test_r25_tiny_turnaround(self):
        """扭亏类(基数≤0 同比不可算) 当季归母 300 万 < 0.5 亿 -> R2.5 拦(时代万恒型)。"""
        q = {"26Q3": {"rev": 150, "np": 3e6, "ded": None},
             "25Q3": {"rev": 100, "np": -5, "ded": -5},
             "26Q2": {"rev": 100, "np": 1e6, "ded": None}}
        ok, rej = self.run_gate(q)
        self.assertEqual(ok, [])
        self.assertTrue(rej[0]["why"].startswith("扭亏规模不足"))

    def test_turnaround_with_scale_passes(self):
        """扭亏类但当季归母 ≥0.5 亿 -> 过闸。"""
        q = {"26Q3": {"rev": 150, "np": 6e7, "ded": None},
             "25Q3": {"rev": 100, "np": -5, "ded": -5},
             "26Q2": {"rev": 100, "np": 2e7, "ded": None}}
        ok, rej = self.run_gate(q)
        self.assertEqual(len(ok), 1)
        self.assertEqual(rej, [])

    def test_tier_strict_boundary(self):
        """分档严格大于: 同比恰=100 且环比恰=50 -> 二档; 严格超 -> 特档。"""
        base = {"25Q3": {"rev": 100, "np": 10, "ded": 9},
                "26Q2": {"rev": 100, "np": 15, "ded": 14}}
        edge = dict(base, **{"26Q3": {"rev": 150, "np": 20, "ded": 18}})
        self.assertEqual(self.run_gate(edge)[0][0]["tier"], "二档")
        over = dict(base, **{"26Q3": {"rev": 150, "np": 25, "ded": 22}})
        self.assertEqual(self.run_gate(over)[0][0]["tier"], "特档")


class TestRender(unittest.TestCase):
    """v3 监测日报格式(2026-11-05): 标题系列名 + 过闸卡片(摘要行+近4季三线表) + 未过闸一句话。"""

    Q = {"26Q3": {"rev": 150e8, "np": 25e8, "ded": 22e8},
         "25Q3": {"rev": 100e8, "np": 10e8, "ded": 9e8},
         "26Q2": {"rev": 100e8, "np": 15e8, "ded": 14e8},
         "25Q4": {"rev": 120e8, "np": 18e8, "ded": 16e8},
         "26Q1": {"rev": 90e8, "np": 12e8, "ded": 11e8}}

    def _passed(self):
        rows = [{"SECURITY_CODE": "688380", "SECURITY_NAME_ABBR": "中微半导",
                 "NOTICE_DATE": "2026-10-03", "TOTAL_OPERATE_INCOME": 400e8,
                 "PARENT_NETPROFIT": 50e8}]
        ok, _ = P.process_formal(rows, {"688380": ({}, self.Q)}, {"688380": "中微半导"}, 2026, 3)
        return ok

    def test_doc_title(self):
        self.assertEqual(P.doc_title("三季报", "2026-10-03", "2026-10-03", 33),
                         "三季报监测日报 2026-10-03(33家)")
        self.assertEqual(P.doc_title("中报", "2026-08-28", "2026-08-30", 9),
                         "中报监测日报 08-28~08-30(9家)")

    def test_zero_disclosure_doc(self):
        """零披露窗口(节假日)出 0 家报告, 与'数据源挂了'区分。"""
        md = P.gen_report([], [], "东财", 0, "2026-10-03", "2026-10-03", "三季报", 2026, 3)
        self.assertIn("# 三季报监测日报 2026-10-03(0家)", md)
        self.assertIn("过闸 **0 家**", md)
        self.assertNotIn("## ⏰", md)
        self.assertNotIn("## 未过闸", md)

    def test_gen_report_sections(self):
        passed = self._passed()
        md = P.gen_report(passed, [{"code": "688676", "name": "金盘科技",
                                    "why": "同比未达40%",
                                    "yoy": {"rev": 17.5, "ded": 13.8}}],
                          "东财", 2, "2026-10-03", "2026-10-03", "三季报", 2026, 3)
        self.assertIn("# 三季报监测日报 2026-10-03(2家)", md)
        self.assertIn("过闸 **1 家**", md)
        self.assertIn("- **中微半导(688380.SH)**【", md)          # 代码带后缀(对齐台账惯例)
        self.assertIn("入台账跟踪", md)
        self.assertIn("| 营收(亿) | 120.00 | 90.00 | 100.00 | 150.00 |", md)  # 近4季三线
        self.assertIn("| 营收同比 | — | — | — | +50.0% |", md)   # 基数缺失→—, 当季可算
        self.assertIn("## 未过闸(1家, 一句话带过)", md)
        self.assertIn("**金盘科技(688676.SH)** 营收+17.5% 扣非+13.8% → 同比未达40%。", md)
        self.assertIn("非投资建议", md)

    def test_base_rows(self):
        """Base 行: 代码带后缀, 同比数值化, 新建时档位/状态/首次纳入(人流转字段只在新建给)。"""
        passed = self._passed()
        rows = P.base_rows(passed, "2026-10-03 三季报监测日报", "https://x", ["2026-10-03", "2026-10-03"])
        d = rows[0]
        self.assertEqual(d["股票代码"], "688380.SH")
        self.assertEqual(d["股票名称"], "中微半导")
        self.assertEqual(d["营收同比%"], 50.0)
        self.assertEqual(d["来源报告"], "2026-10-03 三季报监测日报")
        self.assertEqual(d["报告链接"], "https://x")
        self.assertNotIn("状态", d)          # 状态/首次纳入由 base_upsert 只在新建时补
        self.assertEqual(d["归母同比%"], 150.0)  # 可算同比全写


if __name__ == "__main__":
    unittest.main(verbosity=2)
