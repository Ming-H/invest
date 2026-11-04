"""AI 十倍股 ground-truth 验收(循环目标:识别 2024 至今全部 AI 十倍股)。

数据:tests/ai10x_harness.py 生成的 outputs/ai10x/progress.json(免额度腾讯+东财,
断点续跑)。达标口径:首亮进度≤70% 且其后≥+50%。
NARRATIVE(叙事先行型,harness 数据自证 + 本测试反向复核):主升发生在业绩可验证
之前——2024 窗口内从无"营收+归母双过40"季(德科立/芯原/麦格米特),5不买#1/#2 字面
不可跟;业绩兑现时框架及时亮灯(麦格米特 2026-08 击球区)。
"""
import json
import os
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SK = os.path.dirname(HERE)
PROG = os.path.join(SK, "outputs", "ai10x", "progress.json")
BEG_YEAR = "2024"


@unittest.skipUnless(os.path.exists(PROG), "先运行 python3 tests/ai10x_harness.py")
class TestAI10x(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        with open(PROG, encoding="utf-8") as f:
            cls.prog = json.load(f)

    def _gt(self):
        return {c: d for c, d in self.prog["stocks"].items()
                if (d.get("mult") or 0) >= 10}

    def test_coverage(self):
        gt = self._gt()
        self.assertGreaterEqual(len(gt), 15, "ground-truth 数量异常(候选池或数据缺失)")
        for c, d in gt.items():
            self.assertIn("backtest", d, f"{c} 缺回放结果")

    def test_no_miss_no_late(self):
        """框架可见集合全 PASS:不允许 MISS(漏检)与 LATE(亮太晚)。"""
        bad = {c: (d.get("backtest") or {}).get("verdict")
               for c, d in self._gt().items()
               if (d.get("backtest") or {}).get("verdict") not in ("PASS", "NARRATIVE")}
        self.assertEqual(bad, {}, f"存在 MISS/LATE: {bad}")

    def test_narrative_legitimacy(self):
        """NARRATIVE 反向复核:必须真的'2024 以来无双过线季'(防止豁免被滥用)。
        用东财数据独立重算,不信任 harness 自身结论。"""
        import sys
        sys.path.insert(0, os.path.join(SK, "scripts"))
        import emoney
        narr = [c for c, d in self._gt().items()
                if (d.get("backtest") or {}).get("verdict") == "NARRATIVE"]
        for c in narr:
            yoy = emoney.yoy_from_periods(emoney.f10_periods(c))
            lit = [q for q, (r, p, _) in yoy.items()
                   if q >= BEG_YEAR and (r or 0) >= 40 and (p or 0) >= 40]
            self.assertEqual(lit, [], f"{c} 存在双过线季 {lit},不属叙事型,豁免非法")


if __name__ == "__main__":
    unittest.main()
