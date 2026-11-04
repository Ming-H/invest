#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_gates.py — 闸门与框架纯逻辑单测(无网络)。

锚点案例来自 h1-monitor v7.3 规则注释(*ST联翔/诺德股份/时代万恒等)与
分档严格边界(>100/>50 为严格大于, 压线落二档)。运行: python3 tests/test_gates.py
"""
import importlib.util
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))
import framework as fw   # noqa: E402
import pipeline as P     # noqa: E402


def ser(yoy, qoq, vals=None):
    return [{"val": v, "yoy": y, "qoq": q} for v, y, q in
            zip(vals or [None] * len(yoy), yoy, qoq)]


class TestQuarterCalendar(unittest.TestCase):
    def test_labels(self):
        self.assertEqual(P.quarter_of("2026-09-30"), (2026, 3))
        self.assertEqual(P.quarter_of("2026-03-31"), (2026, 1))
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
        """营收+50% 但归母同比+10% -> R1 假成长出局(诺德股份型)。"""
        q = {"26Q3": {"rev": 150, "np": 11, "ded": 10},
             "25Q3": {"rev": 100, "np": 10, "ded": 9},
             "26Q2": {"rev": 100, "np": 20, "ded": 18}}
        self.assertEqual(self.run_gate(q), [])

    def test_r3_st(self):
        q = {"26Q3": {"rev": 150, "np": 25, "ded": 22},
             "25Q3": {"rev": 100, "np": 10, "ded": 9},
             "26Q2": {"rev": 100, "np": 15, "ded": 14}}
        self.assertEqual(self.run_gate(q, name="*ST联翔"), [])
        self.assertEqual(self.run_gate(q, name="XX退"), [])

    def test_r2_loss(self):
        """当季归母亏损 -> R2 出局, 即便营收高增。"""
        q = {"26Q3": {"rev": 150, "np": -5, "ded": -6},
             "25Q3": {"rev": 100, "np": 10, "ded": 9},
             "26Q2": {"rev": 100, "np": 15, "ded": 14}}
        self.assertEqual(self.run_gate(q), [])

    def test_r25_tiny_turnaround(self):
        """扭亏类(基数≤0 同比不可算) 当季归母 300 万 < 0.5 亿 -> R2.5 拦(时代万恒型)。"""
        q = {"26Q3": {"rev": 150, "np": 3e6, "ded": None},
             "25Q3": {"rev": 100, "np": -5, "ded": -5},
             "26Q2": {"rev": 100, "np": 1e6, "ded": None}}
        self.assertEqual(self.run_gate(q), [])

    def test_turnaround_with_scale_passes(self):
        """扭亏类但当季归母 ≥0.5 亿 -> 过闸。"""
        q = {"26Q3": {"rev": 150, "np": 6e7, "ded": None},
             "25Q3": {"rev": 100, "np": -5, "ded": -5},
             "26Q2": {"rev": 100, "np": 2e7, "ded": None}}
        out = self.run_gate(q)
        self.assertEqual(len(out), 1)

    def test_tier_strict_boundary(self):
        """分档严格大于: 同比恰=100 且环比恰=50 -> 二档; 严格超 -> 特档。"""
        base = {"25Q3": {"rev": 100, "np": 10, "ded": 9},
                "26Q2": {"rev": 100, "np": 15, "ded": 14}}
        edge = dict(base, **{"26Q3": {"rev": 150, "np": 20, "ded": 18}})
        self.assertEqual(self.run_gate(edge)[0]["tier"], "二档")
        over = dict(base, **{"26Q3": {"rev": 150, "np": 25, "ded": 22}})
        self.assertEqual(self.run_gate(over)[0]["tier"], "特档")


class TestForecastGates(unittest.TestCase):
    def _run(self, rows, hist, names=None):
        return P.process_forecast(rows, hist, names or {}, 2026, 3)

    def test_lower_bound_deduction_and_r1(self):
        """预告当季=区间下限-H1累计; 下限同比≤40 -> R1 出局。"""
        cum = {"2026-06-30": {"rev": 200, "np": 30, "ded": 28}}
        qq = P.quarter_amounts(cum, 2026, 3, n_back=1)  # 仅演示 hist 结构
        hist = {"X": (cum, {"25Q3": {"rev": 100, "np": 10, "ded": 9},
                            "26Q2": {"rev": 100, "np": 15, "ded": 14}} | qq)}
        rows = [{"SECURITY_CODE": "X", "NOTICE_DATE": "2026-10-15",
                 "PREDICT_FINANCE": "净利润", "PREDICT_AMT_LOWER": 5.2e8, "PREDICT_AMT_UPPER": 6e8}]
        out = self._run(rows, hist, {"X": "预告股"})
        # 下限倒推当季归母=5.2-0.3=4.9亿 vs 25Q3 0.1亿 -> 同比远超40; 环比 vs 26Q2 0.15亿 -> 高
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["spans"]["np"][0], 5.2e8 - 30)
        # 下限倒推当季≤0 -> R2 出局(精确用例: 25Q3 基数为负使同比不可算, 只剩 R2 拦)
        cum2 = {"2026-06-30": {"rev": 200, "np": 30, "ded": 28}}
        hist2 = {"X": (cum2, {"25Q3": {"rev": 100, "np": -5, "ded": -5},
                             "26Q2": {"rev": 100, "np": 15, "ded": 14}})}
        rows_bad = [{"SECURITY_CODE": "X", "NOTICE_DATE": "2026-10-15",
                     "PREDICT_FINANCE": "净利润", "PREDICT_AMT_LOWER": 20, "PREDICT_AMT_UPPER": 60}]
        self.assertEqual(self._run(rows_bad, hist2, {"X": "预告股"}), [])


class TestCatalystClean(unittest.TestCase):
    def test_numbered_segments(self):
        t = "业绩变动原因如下 1、销量增长30%，2、非经常性损益影响小，3、新品放量"
        clean, n = P.clean_catalyst(t, is_review=False)
        self.assertGreaterEqual(n, 2)
        self.assertIn("销量增长", clean)
        self.assertNotIn("非经常性损益影响", clean)  # 非归因条目剔除

    def test_anchor_cut_for_review(self):
        t = "报告期经营情况稳定。主要原因为：1、海外订单增长，2、产能释放。综上，公司整体向好。"
        clean, n = P.clean_catalyst(t, is_review=True)
        self.assertIn("海外订单", clean)
        self.assertNotIn("综上", clean)

    def test_count_items(self):
        self.assertEqual(P.count_items("1、a；2、b；3、c"), 3)
        self.assertEqual(P.count_items("只有一句话"), 1)


class TestFramework(unittest.TestCase):
    def mk_card(self, rev_yoy, np_yoy, np_qoq=None, rev_qoq=None, **kw):
        np_qoq = np_qoq or [None] * len(rev_yoy)
        rev_qoq = rev_qoq or [None] * len(rev_yoy)
        card = {"code": "T", "name": "T",
                "quarterly": {"rev": ser(rev_yoy, rev_qoq), "np": ser(np_yoy, np_qoq),
                              "dnp": ser([None] * len(rev_yoy), [None] * len(rev_yoy))}}
        card.update(kw)
        return card

    def test_momentum_accel(self):
        m, _ = fw.classify_momentum(ser([10, 20, 30, 45], [5, 5, 5, 8]),
                                    ser([10, 20, 30, 50], [5, 5, 5, 9]))
        self.assertEqual(m, "加速")

    def test_momentum_loss_crossover(self):
        """扭亏通道: 归母同比不可算, 环比>50% -> 加速。"""
        m, _ = fw.classify_momentum(ser([10, 20, 30, 45], [5, 5, 5, 8]),
                                    ser([None, None, None, None], [None, None, None, 80]))
        self.assertEqual(m, "加速")

    def test_momentum_decay_dual_condition(self):
        """衰减=同比证据+环比证据双条件。"""
        m, _ = fw.classify_momentum(ser([80, 60, 50, 45], [10, 5, 2, 1]),
                                    ser([200, 100, 60, 50], [10, 5, 2, 1]))
        self.assertEqual(m, "衰减")

    def test_h2_lowbase_gate(self):
        """低基数假高增: 同比>300% + 当季<0.5亿 + ROE<5% -> H2。"""
        card = self.mk_card([50, 50, 50, 50], [100, 200, 300, 400], roe=3.0)
        card["quarterly"]["np"][-1]["val"] = 0.2
        gates = fw.hard_gates(card, "加速", "早期启动")
        self.assertTrue(any(g["gate"] == "H2" and "低基数" in g["why"] for g in gates))

    def test_h3_size_gate(self):
        card = self.mk_card([50, 50, 50, 50], [50, 50, 50, 50], mktcap_yi=1200)
        gates = fw.hard_gates(card, "加速", "早期启动")
        self.assertTrue(any(g["gate"] == "H3" for g in gates))

    def test_alpha_gate_downgrades(self):
        """α门槛: S/A/R 须核心环节, 一般配套最高 B。"""
        card = self.mk_card([60, 60, 60, 60], [80, 90, 100, 120], roe=20.0,
                            np_cash_ratio=120, mktcap_yi=50,
                            inst_pct=30, ytd_chg=60, limit_up_count=5,
                            gm_trend=[20.0, 21.0, 22.0, 23.0], debt_ratio=30,
                            realized_3y=1.2, off_high_1y=-10)
        r_core = fw.analyze_one(card, 3, llm={"drive_type": "新品放量", "alpha_role": "核心环节",
                                              "stage_call": "无意见",
                                              "is_pure_pricing_cycle": False, "is_one_off_boost": False})
        r_supp = fw.analyze_one(card, 3, llm={"drive_type": "新品放量", "alpha_role": "一般配套",
                                              "stage_call": "无意见",
                                              "is_pure_pricing_cycle": False, "is_one_off_boost": False})
        self.assertGreaterEqual(r_core["grade"], "A")
        self.assertLessEqual(r_supp["grade"], "B")

    def test_judge_file_validation(self):
        """judge_output 枚举校验: 非法枚举走兜底, 合法保留, 多余码忽略。"""
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump([
                {"code": "A", "drive_type": "新品放量", "alpha_role": "核心环节",
                 "stage_call": "无意见", "is_pure_pricing_cycle": False, "is_one_off_boost": False},
                {"code": "B", "drive_type": "编造的驱动", "alpha_role": "核心环节",
                 "stage_call": "无意见", "is_pure_pricing_cycle": False, "is_one_off_boost": False},
                {"code": "ZZZ", "drive_type": "新品放量", "alpha_role": "核心环节",
                 "stage_call": "无意见", "is_pure_pricing_cycle": True, "is_one_off_boost": False},
            ], f)
            path = f.name
        judged, fallback = fw.load_judge_file(path, {"A", "B"})
        os.unlink(path)
        self.assertIn("A", judged)
        self.assertNotIn("B", judged)      # 枚举非法 -> 兜底
        self.assertNotIn("ZZZ", judged)    # 多余码忽略
        self.assertEqual(fallback, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
