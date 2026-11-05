"""两只真实十倍股的"上涨期间识别"验收回放。

fixtures:tests/fixtures/kline/{sz003010,sz300308,sh000001}.json(腾讯前复权,2023-01~2026-08)
         tests/fixtures/yoy/{003010,300308}.json(本会话双重验证的三线单季同比)
运行前先:python3 tests/gen_fixtures.py(已含 fixtures 生成逻辑)
验收口径:首次亮灯在上涨早期(≤2x)、主升段持续亮灯、当前阶段与真实一致。
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
import kline
import runner as runner_mod

HERE = os.path.dirname(os.path.abspath(__file__))
FIX_K = os.path.join(HERE, "fixtures", "kline")
FIX_Y = os.path.join(HERE, "fixtures", "yoy")
LIT = ("主升期", "强势待确认", "回调击球区")

REQUIRE = os.path.exists(os.path.join(FIX_K, "sz003010.json"))


def load(sym):
    with open(os.path.join(FIX_K, f"{sym}.json"), encoding="utf-8") as f:
        return json.load(f)["rows"]


def load_yoy(code):
    with open(os.path.join(FIX_Y, f"{code}.json"), encoding="utf-8") as f:
        return json.load(f)


def run_backtest(code, sym):
    rows = load(sym)
    idx = load("sh000001")
    gates = kline.index_gate_series(idx)
    pack = load_yoy(code)
    tl = runner_mod.backtest(code, pack["name"], rows, pack["yoy"], gates,
                             "2024-01-01", "2026-08-30")
    return tl, rows


@unittest.skipUnless(REQUIRE, "fixtures 未生成")
class TestRuoyuchenReplay(unittest.TestCase):
    """若羽臣 003010:低点 3.92(24/02)→高点 47.83(25/10),+1121%。"""

    @classmethod
    def setUpClass(cls):
        cls.tl, cls.rows = run_backtest("003010", "sz003010")
        cls.by_month = {t["month"]: t for t in cls.tl}

    def test_first_light_early_with_big_upside(self):
        lit = [t for t in self.tl if t["phase"] in LIT]
        self.assertEqual(lit[0]["month"], "2024-08")
        self.assertLessEqual(lit[0]["run_multiple"], 1.6)          # 上涨早期(1.49x)
        entry = float(next(r[2] for r in self.rows if r[0] == lit[0]["asof"]))
        hi = max(float(r[3]) for r in self.rows if r[0] >= lit[0]["asof"])
        self.assertGreaterEqual(hi / entry, 8.0)                   # 其后仍有 ≥8x

    def test_real_launch_month_is_uptrend(self):
        self.assertEqual(self.by_month["2024-11"]["phase"], "主升期")  # 真实启动月

    def test_mid_run_stays_lit(self):
        up = [m for m in ("2025-02", "2025-03", "2025-04", "2025-05", "2025-06",
                          "2025-08", "2025-09")
              if self.by_month[m]["phase"] == "主升期"]
        self.assertGreaterEqual(len(up), 5)                        # 主升段持续亮灯
        self.assertGreaterEqual(max(t["run_multiple"] for t in self.tl), 9.0)

    def test_deep_test_zone_after_peak(self):
        for m in ("2026-05", "2026-06", "2026-07"):
            self.assertEqual(self.by_month[m]["phase"], "深调考验区")  # 回撤>40%但三线仍过

    def test_current_state_pullback_zone(self):
        t = self.by_month["2026-08"]
        self.assertEqual(t["phase"], "回调击球区")                  # 回撤37%+三线过线
        self.assertIn("大盘off", t["reason"])                      # β缺失暂缓注记


@unittest.skipUnless(REQUIRE, "fixtures 未生成")
class TestZhongjiReplay(unittest.TestCase):
    """中际旭创 300308:窗口内 +1011%;自52周低点最大 ~13x。"""

    @classmethod
    def setUpClass(cls):
        cls.tl, cls.rows = run_backtest("300308", "sz300308")
        cls.by_month = {t["month"]: t for t in cls.tl}

    def test_first_light_early_with_big_upside(self):
        lit = [t for t in self.tl if t["phase"] in LIT]
        # 2024-02:2023年报预告(1月预增)+2023Q3实绩 → 松发式两线,较无预告版再提前3个月
        self.assertEqual(lit[0]["month"], "2024-02")
        self.assertLessEqual(lit[0]["run_multiple"], 5.5)          # 5.23x(预告级提前)
        entry = float(next(r[2] for r in self.rows if r[0] == lit[0]["asof"]))
        hi = max(float(r[3]) for r in self.rows if r[0] >= lit[0]["asof"])
        self.assertGreaterEqual(hi / entry, 11.0)                  # +1197% 实测

    def test_2024_06_waiting_for_beta(self):
        # 创新高+三线过但大盘off → 强势待确认(2024中 A股熊市尾端,α孤军)
        t = self.by_month["2024-06"]
        self.assertEqual(t["phase"], "强势待确认")

    def test_uptrend_after_924(self):
        self.assertEqual(self.by_month["2024-09"]["phase"], "主升期")

    def test_deep_correction_2025_q1(self):
        # 24Q3高基数+关税冲击回撤46-56%,但三线仍过 → 扛住纪律区(非退潮)
        deep = [m for m in ("2025-02", "2025-03", "2025-04", "2025-05")
                if self.by_month[m]["phase"] == "深调考验区"]
        self.assertGreaterEqual(len(deep), 3)

    def test_second_leg_uptrend(self):
        for m in ("2025-07", "2025-08"):
            self.assertEqual(self.by_month[m]["phase"], "主升期")
        self.assertGreaterEqual(self.by_month["2026-05"]["run_multiple"], 12.0)

    def test_current_state_pullback_zone(self):
        self.assertEqual(self.by_month["2026-08"]["phase"], "回调击球区")


if __name__ == "__main__":
    unittest.main()
