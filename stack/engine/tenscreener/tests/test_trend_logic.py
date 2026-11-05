#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_trend_logic.py — 跨期趋势信号引擎单测(无网络)。

锚点案例: 药明康德 26Q3 经营评述原文"全年收入由人民币585-605亿元上调至610-619亿元"
(指引连续上调正例); 社保基金新进 = 国家队入场(用户点名的信号)。
"""
import importlib.util
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))
import trend  # noqa: E402


YAO = ("2026年前三季度实现收入455.4亿元。公司进一步上调2026年全年业绩指引, "
       "预计2026年公司整体收入由人民币585-605亿元上调至人民币610-619亿元, "
       "持续经营业务收入同比增速由35%-39%上调至40%-42%。")


class TestGuidance(unittest.TestCase):
    def test_extract_from_jyps(self):
        g = trend.extract_guidance([], [("2026-09-30", YAO)])
        self.assertTrue(g)
        vals = [(x["lo_yi"], x["hi_yi"]) for x in g if x["metric"] == "收入"]
        self.assertIn((585.0, 605.0), vals)
        self.assertIn((610.0, 619.0), vals)

    def test_extract_from_report_text(self):
        texts = [{"label": "2025Q4", "cn": "2025年年报", "thick": True,
                  "chapters": {"管理层讨论": "展望: 预计2026年全年营业收入达到300亿元至320亿元。"}}]
        g = trend.extract_guidance(texts, [])
        self.assertTrue(any(x["lo_yi"] == 300.0 and x["hi_yi"] == 320.0 for x in g))

    def test_verdict_up(self):
        gv = trend.guidance_verdict([
            {"period": "2025Q4", "metric": "收入", "lo_yi": 585, "hi_yi": 605},
            {"period": "2026M06", "metric": "收入", "lo_yi": 600, "hi_yi": 615},
            {"period": "2026M09", "metric": "收入", "lo_yi": 610, "hi_yi": 619}])
        self.assertEqual(gv["dir"], "连续上调")

    def test_verdict_down(self):
        gv = trend.guidance_verdict([
            {"period": "a", "metric": "收入", "lo_yi": 100, "hi_yi": 110},
            {"period": "b", "metric": "收入", "lo_yi": 80, "hi_yi": 90}])
        self.assertEqual(gv["dir"], "下调")

    def test_no_data(self):
        self.assertEqual(trend.guidance_verdict([])["dir"], "无数据")


class TestHolderIdentity(unittest.TestCase):
    def test_identity(self):
        self.assertEqual(trend.holder_identity("全国社保基金一一六组合"), "国家队-社保")
        self.assertEqual(trend.holder_identity("基本养老保险基金八零二组合"), "国家队-社保")
        self.assertEqual(trend.holder_identity("中央汇金资产管理有限责任公司"), "国家队-汇金证金")
        self.assertEqual(trend.holder_identity("香港中央结算有限公司"), "北向")
        self.assertEqual(trend.holder_identity("中国人寿保险股份有限公司-传统险"), "险资")
        self.assertEqual(trend.holder_identity("易方达优质精选混合型证券投资基金"), "公募基金")
        self.assertEqual(trend.holder_identity("某某投资公司"), "其他机构")

    def test_national_team_appears(self):
        rows = [
            {"END_DATE": "2026-03-31", "HOLDER_NAME": "香港中央结算有限公司"},
            {"END_DATE": "2026-06-30", "HOLDER_NAME": "香港中央结算有限公司"},
            {"END_DATE": "2026-06-30", "HOLDER_NAME": "全国社保基金四一八组合"},
            {"END_DATE": "2026-09-30", "HOLDER_NAME": "全国社保基金四一八组合"},
            {"END_DATE": "2026-09-30", "HOLDER_NAME": "全国社保基金一一二组合"},
        ]
        tl = trend.holders_timeline(rows)
        self.assertEqual(tl["national_team"]["dir"], "↑增强")
        self.assertIn("社保", "".join(tl["national_team"]["evidence"]))


class TestSlope(unittest.TestCase):
    def test_accel_up(self):
        s = trend.slope([("a", 10), ("b", 12), ("c", 16), ("d", 24)])
        self.assertEqual(s["dir"], "加速升")

    def test_down(self):
        s = trend.slope([("a", 24), ("b", 16), ("c", 12), ("d", 10)])
        self.assertEqual(s["dir"], "降")

    def test_flat_and_insufficient(self):
        self.assertEqual(trend.slope([("a", 10), ("b", 10.1)])["dir"], "数据不足")


class TestKeywords(unittest.TestCase):
    def test_matrix(self):
        texts = [{"label": "x", "cn": "2025年年报", "thick": True,
                  "chapters": {"管理层讨论": "产能满产满销, 供不应求, 订单饱满, 需求旺盛" * 3}},
                 {"label": "y", "cn": "2026年半年报", "thick": True,
                  "chapters": {"管理层讨论": "供不应求加剧, 满产, 涨价, 出海提速" * 3}}]
        kw = trend.keyword_matrix(texts, [])
        self.assertEqual(kw["up_total"] > 0 and kw["risk_total"] == 0, True)
        self.assertIn("上升词占优", kw["trend"])


class TestScorecard(unittest.TestCase):
    def test_score_positive(self):
        gv = {"dir": "连续上调", "steps": ["a: 10~11亿"]}
        kw = {"trend": "上升词占优(10:0)", "up_total": 10, "risk_total": 0}
        ht = {"national_team": {"dir": "↑增强", "evidence": ["社保"]}}
        leads = [{"信号": "合同负债", "方向": "加速升", "证据": "x", "释义": ""}]
        card = trend.scorecard(gv, kw, ht, leads, {"streak_max": 3, "resonance": "triple"})
        self.assertGreaterEqual(card["score"], 4)
        self.assertEqual(card["verdict"], "上升态势")


if __name__ == "__main__":
    unittest.main(verbosity=2)
