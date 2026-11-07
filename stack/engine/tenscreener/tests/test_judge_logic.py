#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_judge_logic.py — judge 纯逻辑单测(无网络)。

锚点: 2026-11-05 药明康德(603259) 首跑的真实行为(年报目录页先命中致 MD&A 只切到
121字 → _slice 增加"过短跳下一个命中"; cninfo cli downloaded 字段非路径 → 目录差集)。
运行: python3 tests/test_judge_logic.py
"""
import importlib.util
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("judge", os.path.join(HERE, "..", "scripts", "judge.py"))
judge = importlib.util.module_from_spec(spec)
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))
spec.loader.exec_module(judge)

# 模拟 A 股年报片段: 目录页有章节条目(后跟....页码), 正文有真章节
FILLER = "产能持续满载,在手订单充足,CRDMO一体化交付。" * 40
FAKE_ANNUAL = (
    "目 录\n"
    "第一节 释义......3\n"
    "第二节 公司简介和主要财务指标......8\n"
    "第三节 管理层讨论与分析......15\n"
    "第四节 公司治理......60\n"
    "第七节 股份变动及股东情况......90\n"
    "（正文开始）\n"
    "第一节 释义\n"
    "本公司 指 某某股份有限公司\n"
    "第二节 公司简介和主要财务指标\n"
    "非经常性损益项目及金额\n"
    "政府补助 200,000,000 元\n"
    "第三节 管理层讨论与分析\n"
    "一、报告期内公司从事的业务情况\n"
    "（一）主要业务 公司为全球客户提供一体化服务。\n" + FILLER + "\n"
    "第四节 公司治理\n"
    "公司治理健全。\n"
    "募集资金使用情况: 募投项目A已投入 80%,预计 2027 年达产。\n"
    "第七节 股份变动及股东情况\n"
    "普通股股东总数 180,000 户, 前十名流通股东含香港中央结算有限公司。\n"
    "第十节 财务报告\n（略）\n"
)


class TestSplitChapters(unittest.TestCase):
    def test_mda_skips_toc_entry(self):
        """MD&A 命中目录条目时须跳过, 切到正文章节(≥400字)。"""
        ch = judge._split_chapters(FAKE_ANNUAL, thick=True)
        self.assertIn("主要业务", ch["管理层讨论"])        # 切到正文章节
        self.assertNotIn("....", ch["管理层讨论"][:200])   # 不是目录条目
        self.assertIn("政府补助", ch["非经常明细"])
        self.assertIn("股东总数", ch["股东章节"])

    def test_slice_prefers_long_segment(self):
        text = ("目录 管理层讨论与分析....15\n" + "x" * 50 + "\n第四节\n"
                + "管理层讨论与分析\n" + "正文" * 400)
        seg = judge._slice(text, judge._CHAPTER_MARKS["mda"], 5000)
        self.assertIn("正文", seg)
        self.assertNotIn("....", seg)

    def test_holder_matches_thin_report_wording(self):
        """薄报告(季报)用'股东信息'而非'股份变动及股东情况'。"""
        text = "股东信息\n(一) 普通股股东总数和表决权恢复的优先股股东数量表\n" + "数据行 " * 200
        seg = judge._slice(text, judge._CHAPTER_MARKS["holder"], 2000)
        self.assertIn("股东总数", seg)

    def test_mipu_fulltext_search(self):
        m = judge.re.search(r"募集资金使用情况|募集资金投向|募集资金的具体使用", FAKE_ANNUAL)
        self.assertTrue(m)
        self.assertIn("募投项目A", FAKE_ANNUAL[m.start():m.start() + 5000])


class TestQoqTable(unittest.TestCase):
    def test_qoq(self):
        # singles 为原始元口径(_yi 会除 1e8), 用亿元量级数据
        E8 = 1e8
        singles = {"2025Q4": (100 * E8, 50 * E8, 40 * E8), "2026Q1": (110 * E8, 55 * E8, 44 * E8),
                   "2026Q2": (140 * E8, 70 * E8, 56 * E8), "2026Q3": (150 * E8, 90 * E8, 60 * E8)}
        qt, labels = judge.qoq_table(singles)
        self.assertEqual(labels[-1], "2026Q3")
        self.assertEqual(qt[-1]["single_yi"][0], 150.0)
        self.assertEqual(qt[1]["qoq"][0], 10.0)      # Q1 环比 +10%
        self.assertEqual(qt[0]["qoq"], (None, None, None))  # 只有4季 → 首季环比仍缺

    def test_qoq_first_quarter_uses_5th_back(self):
        """首季环比取显示窗外上一季(2026-11-05 用户反馈: 近4季须实际取5季算环比)。"""
        E8 = 1e8
        singles = {"2025Q3": (80 * E8, 40 * E8, 32 * E8),
                   "2025Q4": (100 * E8, 50 * E8, 40 * E8), "2026Q1": (110 * E8, 55 * E8, 44 * E8),
                   "2026Q2": (140 * E8, 70 * E8, 56 * E8), "2026Q3": (150 * E8, 90 * E8, 60 * E8)}
        qt, labels = judge.qoq_table(singles)
        self.assertEqual(labels[0], "2025Q4")               # 显示窗仍4季
        self.assertEqual(qt[0]["qoq"], (25.0, 25.0, 25.0))  # 25Q4环比=vs 25Q3(窗外), 不再是—
        self.assertEqual(qt[1]["qoq"][0], 10.0)
        # 跨年边界: 显示窗首季若是 Q1, 前一季取上年 Q4
        singles2 = {("2025Q%d" % q): (100 * E8, 50 * E8, 40 * E8) for q in (1, 2, 3, 4)}
        singles2.update({"2026Q1": (110 * E8, 55 * E8, 44 * E8)})
        qt2, _ = judge.qoq_table(singles2, n=1)             # 只显示 26Q1
        self.assertEqual(qt2[0]["qoq"], (10.0, 10.0, 10.0))  # vs 25Q4(窗外)


class TestStage1Gate(unittest.TestCase):
    """v4.1 三线40%硬闸(2026-11-05 用户定稿A案, 对齐 earnings-reader R1)。"""

    @staticmethod
    def mk_qs(latest="2026Q3", rev=None, np_=None, ded=None):
        return {"latest_period": latest,
                "rev_yoy": {latest: rev}, "parent_yoy": {latest: np_}, "deduct_yoy": {latest: ded}}

    def test_pass_all_lines_over_40(self):
        qs = self.mk_qs(rev=55.0, np_=120.0, ded=98.0)
        singles = {"2026Q3": (150e8, 25e8, 22e8)}
        ok, reason, latest, kind = judge.gate_verdict(qs, singles)
        self.assertTrue(ok); self.assertEqual(kind, "pass"); self.assertEqual(latest, "2026Q3")
        self.assertIn("全>40%", reason)

    def test_reject_line_under_40(self):
        qs = self.mk_qs(rev=38.0, np_=136.7, ded=49.9)      # 药明26Q3型: 营收贴线
        singles = {"2026Q3": (150e8, 25e8, 22e8)}
        ok, reason, latest, kind = judge.gate_verdict(qs, singles)
        self.assertFalse(ok); self.assertEqual(kind, "reject")
        self.assertIn("未全过40%", reason)

    def test_reject_current_quarter_loss(self):
        qs = self.mk_qs(rev=55.0, np_=None, ded=None)
        singles = {"2026Q3": (150e8, -3e8, None)}
        ok, reason, latest, kind = judge.gate_verdict(qs, singles)
        self.assertFalse(ok); self.assertEqual(kind, "reject")
        self.assertIn("当季亏损", reason)

    def test_turnaround_channel_needs_scale(self):
        # 归母/扣非同比不可算(基数≤0) + 归母 0.3亿 < 0.5亿 → 否
        qs = self.mk_qs(rev=55.0, np_=None, ded=None)
        singles = {"2026Q3": (150e8, 3e7, None)}
        ok, reason, latest, kind = judge.gate_verdict(qs, singles)
        self.assertFalse(ok); self.assertEqual(kind, "reject"); self.assertIn("扭亏规模不足", reason)

    def test_turnaround_channel_passes_with_scale(self):
        qs = self.mk_qs(rev=55.0, np_=None, ded=None)
        singles = {"2026Q3": (150e8, 6e7, None)}
        ok, reason, latest, kind = judge.gate_verdict(qs, singles)
        self.assertTrue(ok); self.assertEqual(kind, "pass"); self.assertIn("扭亏通道", reason)

    def test_nodata_when_no_periods(self):
        ok, reason, latest, kind = judge.gate_verdict({"latest_period": None}, {})
        self.assertFalse(ok); self.assertEqual(kind, "nodata"); self.assertIsNone(latest)


class TestStage1ForecastChannel(unittest.TestCase):
    """预告爆发通道(2026-11-07 补全): 预告中枢倒推爆发且预告期晚于已披露期 → 豁免一切 reject。
    实证锚点: 源杰(利润未兑现靠预告亮灯)/寒武纪(当季亏损+预告扭亏)/松发(Q1实绩+H1预告凑两线)。"""

    FC = {"period": "2026Q4", "pass": True, "basis": "单季同比+538%", "single_yi": 2.5}

    def test_loss_exempted_by_forecast(self):
        """当季亏损但预告期晚且中枢爆发(寒武纪型) → 预告通道过闸。"""
        qs = {"latest_period": "2026Q3", "rev_yoy": {"2026Q3": 55.0},
              "parent_yoy": {}, "deduct_yoy": {}}
        singles = {"2026Q3": (150e8, -3e8, None)}
        ok, reason, latest, kind = judge.gate_verdict(qs, singles, forecast=self.FC)
        self.assertTrue(ok); self.assertEqual(kind, "pass"); self.assertEqual(latest, "2026Q3")
        self.assertIn("预告爆发通道", reason)

    def test_under40_exempted_by_forecast(self):
        """三线未全过40但 H1 预告倒推单季爆发(松发/源杰型) → 预告通道过闸。"""
        qs = {"latest_period": "2026Q3", "rev_yoy": {"2026Q3": 55.0},
              "parent_yoy": {"2026Q3": 30.0}, "deduct_yoy": {"2026Q3": 35.0}}
        singles = {"2026Q3": (150e8, 20e8, 18e8)}
        ok, reason, _, kind = judge.gate_verdict(qs, singles, forecast=self.FC)
        self.assertTrue(ok); self.assertEqual(kind, "pass"); self.assertIn("预告爆发通道", reason)

    def test_forecast_for_disclosed_period_ignored(self):
        """预告期≤已披露期(实绩已出, 预告信号作废) → 不豁免, 维持 reject。"""
        fc = dict(self.FC, period="2026Q3")
        qs = {"latest_period": "2026Q3", "rev_yoy": {"2026Q3": 55.0},
              "parent_yoy": {"2026Q3": 30.0}, "deduct_yoy": {"2026Q3": 35.0}}
        singles = {"2026Q3": (150e8, 20e8, 18e8)}
        ok, _, _, kind = judge.gate_verdict(qs, singles, forecast=fc)
        self.assertFalse(ok); self.assertEqual(kind, "reject")

    def test_forecast_not_pass_ignored(self):
        """预告未达爆发口径 → 不豁免。"""
        fc = {**self.FC, "pass": False, "basis": None}
        qs = {"latest_period": "2026Q3", "rev_yoy": {"2026Q3": 55.0},
              "parent_yoy": {"2026Q3": 30.0}, "deduct_yoy": {"2026Q3": 35.0}}
        singles = {"2026Q3": (150e8, 20e8, 18e8)}
        ok, _, _, _ = judge.gate_verdict(qs, singles, forecast=fc)
        self.assertFalse(ok)

    def test_normal_pass_unaffected(self):
        """三线全过时 reason 仍为标准过闸(预告不干扰主通道)。"""
        qs = {"latest_period": "2026Q3", "rev_yoy": {"2026Q3": 55.0},
              "parent_yoy": {"2026Q3": 120.0}, "deduct_yoy": {"2026Q3": 98.0}}
        singles = {"2026Q3": (150e8, 25e8, 22e8)}
        ok, reason, _, _ = judge.gate_verdict(qs, singles, forecast=self.FC)
        self.assertTrue(ok); self.assertIn("全>40%", reason)

    def test_quick_reject_card(self):
        """否决结论卡: 含4季表与依据, 供对话呈现(不落盘不发飞书)。"""
        qs = {"latest_period": "2026Q3", "rev_yoy": {"2026Q3": 8.0},
              "parent_yoy": {"2026Q3": 5.0}, "deduct_yoy": {"2026Q3": 3.0}}
        singles = {"2026Q2": (100e8, 15e8, 14e8), "2026Q3": (108e8, 15.75e8, 14.42e8)}
        md = judge.quick_reject_md("长江电力", "600900", qs, singles, "当季同比未全过40%线(...)")
        self.assertIn("⛔ 快速否决 · 长江电力(600900)", md)
        self.assertIn("| 2026Q3 | 108.0/15.8/14.4 | +8.0/+5.0/+3.0 |", md)
        self.assertIn("--force-full", md)
        self.assertIn("静水核心因子#1", md)


class TestVerdict(unittest.TestCase):
    def _write(self, obj):
        f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
        json.dump(obj, f, ensure_ascii=False)
        f.close()
        return f.name

    def test_valid_verdict(self):
        v = {"stage": "主升期", "confidence": "高", "is_tenbagger_candidate": True,
             "jingshui_core7": [{"no": i + 1, "item": "x", "verdict": "✓", "evidence": ""} for i in range(7)],
             "jingshui_5no": [{"no": i + 1, "item": "y", "verdict": "否"} for i in range(5)],
             "canslim": {k: "✓" for k in ("C", "A", "N", "S", "L", "I", "M")},
             "lynch_class": "快速增长",
             "v5_three_layer": {"industry_beta": {"cycle_type": "强周期20年", "phase": "兑现段",
                                                  "evidence_up": ["船价高位"],
                                                  "evidence_down": ["订单增速回落"]}}}
        v2, errs = judge.load_verdict(self._write(v))
        self.assertEqual(errs, [])
        self.assertEqual(v2["stage"], "主升期")

    def test_v5_missing_and_no_counter_evidence_warn(self):
        """v5 三层判定校验: 缺失警告不阻塞; 行业反方证据缺失必警告(防追高合理化)。"""
        v = {"stage": "主升期", "confidence": "高",
             "v5_three_layer": {"industry_beta": {"cycle_type": "成长", "phase": "上行中",
                                                  "evidence_up": ["订单满"], "evidence_down": []}}}
        v2, errs = judge.load_verdict(self._write(v))
        self.assertTrue(any("evidence_down" in e for e in errs))
        v3, errs3 = judge.load_verdict(self._write({"stage": "主升期", "confidence": "低"}))
        self.assertTrue(any("v5_three_layer" in e for e in errs3))

    def test_invalid_stage_falls_back_with_error(self):
        v = {"stage": "起飞期"}
        v2, errs = judge.load_verdict(self._write(v))
        self.assertTrue(errs)
        self.assertEqual(v2["stage"], "强势待确认")      # 确定性兜底
        self.assertEqual(len(v2["jingshui_core7"]), 7)   # 缺省补齐
        self.assertEqual(len(v2["jingshui_5no"]), 5)

    def test_render_report_contains_core(self):
        pack = {"name": "测试", "code": "000000", "asof": "2026-11-05", "stage_pre": ["主升期", "x"]}
        v = {"stage": "主升期", "confidence": "高", "is_tenbagger_candidate": True,
             "stage_reason": "r", "conclusion": "过", "lynch_class": "快速增长", "lynch_reason": "l",
             "jingshui_core7": [{"no": 1, "item": "i", "verdict": "✓", "evidence": "e"}],
             "jingshui_5no": [{"no": 1, "item": "n", "verdict": "否"}],
             "canslim": {k: "✓" for k in ("C", "A", "N", "S", "L", "I", "M")}}
        md = judge.render_report(pack, v, [])
        for kw in ("十倍股判定报告", "是否十倍股候选: 是", "所处阶段: 主升期", "静水核心因子"):
            self.assertIn(kw, md)

    def test_render_report_contains_quarterly_table(self):
        """报告须含近4季三线明细表(2026-11-05 用户反馈补充)。"""
        pack = {"name": "测试", "code": "000000", "asof": "2026-11-05", "stage_pre": ["主升期", "x"],
                "quarterly_table": [
                    {"label": "2026Q2", "single_yi": (140.0, 70.0, 56.0), "qoq": (27.3, 27.3, 27.3)},
                    {"label": "2026Q3", "single_yi": (150.0, 90.0, 60.0), "qoq": (7.1, 28.6, 7.1)}],
                "qs": {"rev_yoy": {"2026Q3": 38.0}, "parent_yoy": {"2026Q3": 136.7}, "deduct_yoy": {"2026Q3": 49.9}},
                "analysis": {"pass_lines": {"labels": ["归母", "扣非"], "count": 2}, "streak_max": 3,
                             "resonance": "profit_led", "rev_accel": True,
                             "divergence": {"flag": "无"}}}
        v = {"stage": "主升期", "confidence": "高", "is_tenbagger_candidate": True,
             "stage_reason": "r", "conclusion": "过", "lynch_class": "快速增长", "lynch_reason": "l",
             "jingshui_core7": [{"no": 1, "item": "i", "verdict": "✓", "evidence": "e"}],
             "jingshui_5no": [{"no": 1, "item": "n", "verdict": "否"}],
             "canslim": {k: "✓" for k in ("C", "A", "N", "S", "L", "I", "M")}}
        md = judge.render_report(pack, v, [])
        self.assertIn("## 三、财务三线(近4季单季, F10自算)", md)
        self.assertIn("| 2026Q3 | 150.0 | +38.0 | +7.1 | 90.0 | +136.7 | +28.6 | 60.0 | +49.9 | +7.1 |", md)
        self.assertIn("三线过线: 归母/扣非(2/3)", md)
        # 无季度数据的旧 pack 不崩, 仅跳过该节
        md2 = judge.render_report({"name": "t", "code": "1", "asof": "d"}, dict(v), [])
        self.assertNotIn("近4季财务三线", md2)

    def test_render_report_pdf_summary(self):
        """两厚报告精要分节版(2026-11-08): 移至三线表后, 八小节逐节渲染。"""
        v = {"stage": "回调击球区", "confidence": "高", "is_tenbagger_candidate": True,
             "stage_reason": "r", "conclusion": "过", "lynch_class": "快速增长", "lynch_reason": "l",
             "jingshui_core7": [{"no": 1, "item": "i", "verdict": "✓", "evidence": "e"}],
             "jingshui_5no": [{"no": 1, "item": "n", "verdict": "否"}],
             "canslim": {k: "✓" for k in ("C", "A", "N", "S", "L", "I", "M")},
             "pdf_summary": {"在手订单与收入可见度": "交船计划排至2030, VLCC手持全球单体第一(2026半年报)",
                             "跨期对比": "定调由『峰回路转』升级为『创历史同期纪录』"}}
        md = judge.render_report({"name": "t", "code": "1", "asof": "d"}, v, [])
        self.assertIn("## 四、财报阅读(两厚报告精要, 分门别类对读)", md)
        self.assertIn("**在手订单与收入可见度**", md)
        self.assertIn("排至2030", md)
        # 精要节须在三线表之后、静水因子节之前
        self.assertLess(md.index("财报阅读"), md.index("### 静水核心因子"))
        # 未填 pdf_summary 的旧 verdict 不渲染该节
        md2 = judge.render_report({"name": "t", "code": "1", "asof": "d"},
                                  {k: vv for k, vv in v.items() if k != "pdf_summary"}, [])
        self.assertNotIn("财报阅读", md2)

    def test_render_report_core7_list_and_no_5no_table(self):
        """2026-11-08 用户定版: 核心因子7条用列表(同CAN SLIM式), 5不买不再渲染。"""
        v = {"stage": "主升期", "confidence": "高", "is_tenbagger_candidate": True,
             "stage_reason": "r", "conclusion": "过", "lynch_class": "快速增长", "lynch_reason": "l",
             "jingshui_core7": [{"no": 1, "item": "营收利润增速≥40%", "verdict": "✓", "evidence": "三线+181%"}],
             "jingshui_5no": [{"no": 3, "item": "未创一年新高?", "verdict": "✓(触发)", "evidence": ""}],
             "canslim": {k: "✓" for k in ("C", "A", "N", "S", "L", "I", "M")}}
        md = judge.render_report({"name": "t", "code": "1", "asof": "d"}, v, [])
        self.assertIn("- **1 营收利润增速≥40%**: ✓ —— 三线+181%", md)
        self.assertNotIn("静水 5 不买", md)          # 表格已删
        self.assertNotIn("亏损公司?", md)             # 条目不再出现在渲染


if __name__ == "__main__":
    unittest.main(verbosity=2)
