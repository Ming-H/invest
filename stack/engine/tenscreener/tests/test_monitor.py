"""monitor confirm/exit 矩阵 + watchlist 状态机 单测。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
from monitor import confirm_check, exit_check, market_gate
import sector
import watchlist as wl

# 若羽臣真实三线(同 test_quarters)
QS_T2 = {
    "periods": ["2023Q4", "2024Q1", "2024Q2", "2024Q3"], "latest_period": "2024Q3",
    "rev_yoy": {"2023Q4": 13.9, "2024Q1": 31.7, "2024Q2": 34.4, "2024Q3": 54.6},
    "parent_yoy": {"2023Q4": 22.5, "2024Q1": 86.3, "2024Q2": 66.5, "2024Q3": 68.1},
    "deduct_yoy": {"2023Q4": 19.6, "2024Q1": 78.3, "2024Q2": 68.9, "2024Q3": 76.3}}
QS_25Q1 = {
    "periods": ["2024Q2", "2024Q3", "2024Q4", "2025Q1"], "latest_period": "2025Q1",
    "rev_yoy": {"2024Q2": 34.4, "2024Q3": 54.6, "2024Q4": 13.8, "2025Q1": 54.2},
    "parent_yoy": {"2024Q2": 66.5, "2024Q3": 68.1, "2024Q4": 133.2, "2025Q1": 113.9},
    "deduct_yoy": {"2024Q2": 68.9, "2024Q3": 76.3, "2024Q4": 135.4, "2025Q1": 107.6}}
QS_26Q1 = {
    "periods": ["2025Q2", "2025Q3", "2025Q4", "2026Q1"], "latest_period": "2026Q1",
    "rev_yoy": {"2025Q2": 79.6, "2025Q3": 123.4, "2025Q4": 111.4, "2026Q1": 73.3},
    "parent_yoy": {"2025Q2": 71.7, "2025Q3": 73.3, "2025Q4": 87.0, "2026Q1": 163.8},
    "deduct_yoy": {"2025Q2": 72.2, "2025Q3": 69.9, "2025Q4": 77.8, "2026Q1": 167.5}}


class TestConfirm(unittest.TestCase):
    def test_confirmed_when_all_three(self):
        r = confirm_check({}, QS_T2, {"pos_1y": 1.02},
                          {"verdict": "on", "method": "ma250"})
        self.assertEqual(r["result"], "confirmed")
        self.assertEqual(len(r["signals"]), 3)
        self.assertIn("连续验证", "".join(r["signals"]))      # 归母/扣非连续3季

    def test_waiting_when_pos_low(self):
        r = confirm_check({}, QS_T2, {"pos_1y": 0.70}, {"verdict": "on"})
        self.assertEqual(r["result"], "waiting")
        self.assertTrue(any("R2" in m for m in r["missing"]))

    def test_waiting_when_gate_weak_or_off(self):
        for v in ("weak", "off"):
            r = confirm_check({}, QS_T2, {"pos_1y": 1.02}, {"verdict": v})
            self.assertEqual(r["result"], "waiting")

    def test_no_market_gate_skips_r3(self):
        r = confirm_check({}, QS_T2, {"pos_1y": 1.02}, None, market_gate_on=False)
        self.assertEqual(r["result"], "confirmed")

    def test_invalidated_two_periods_all_fail(self):
        qs = {"periods": ["2025Q1", "2025Q2"], "latest_period": "2025Q2",
              "rev_yoy": {"2025Q1": 5, "2025Q2": 8},
              "parent_yoy": {"2025Q1": 3, "2025Q2": 6},
              "deduct_yoy": {"2025Q1": 4, "2025Q2": 7}}
        r = confirm_check({}, qs, {"pos_1y": 0.5}, {"verdict": "on"})
        self.assertEqual(r["result"], "invalidated")

    def test_missing_period_not_counted_as_fail(self):
        # 一期数据缺失 → 不触发证伪
        qs = {"periods": ["2024Q3", "2024Q4"], "latest_period": "2024Q4",
              "rev_yoy": {"2024Q4": 13.8}, "parent_yoy": {}, "deduct_yoy": {}}
        r = confirm_check({}, qs, {"pos_1y": 0.5}, {"verdict": "on"})
        self.assertNotEqual(r["result"], "invalidated")

    def test_streak_note_2025q1(self):
        r = confirm_check({}, QS_25Q1, {"pos_1y": 1.2}, {"verdict": "on"})
        self.assertEqual(r["result"], "confirmed")
        self.assertIn("连续验证", "".join(r["signals"]))


class TestExit(unittest.TestCase):
    def test_2026q1_realdata_is_warn_not_exit(self):
        """真实回放:26Q1 增速降档(单季38pp)+回撤49% 但三线仍全过 →
        中期调整裁定 warn(静水:扛住)。事后若羽臣 26Q3 反弹+25%,验证阈值不过拟合。"""
        res = exit_check({}, QS_26Q1, {"last": 24.30}, 47.83)
        self.assertEqual(res["level"], "warn")
        self.assertTrue(any("中期调整" in x for x in res["reasons"]))
        self.assertAlmostEqual(res["drawdown"], 0.492, places=2)

    def test_exit_when_fundamentals_fail_with_drawdown(self):
        qs = {"periods": ["2025Q3", "2025Q4", "2026Q1", "2026Q2"],
              "latest_period": "2026Q2",
              "rev_yoy": {"2025Q3": 123.4, "2025Q4": 80.0, "2026Q1": 45.0, "2026Q2": 30.0},
              "parent_yoy": {"2025Q3": 73.3, "2025Q4": 60.0, "2026Q1": 44.0, "2026Q2": 35.0},
              "deduct_yoy": {"2025Q3": 69.9, "2025Q4": 55.0, "2026Q1": 40.0, "2026Q2": 32.0}}
        res = exit_check({}, qs, {"last": 20.0}, 47.83)
        self.assertEqual(res["level"], "exit")
        self.assertFalse(res["fundamentals_pass"])

    def test_exit_consecutive_downshift(self):
        qs = {"periods": ["2025Q4", "2026Q1", "2026Q2"], "latest_period": "2026Q2",
              "rev_yoy": {"2025Q4": 110.0, "2026Q1": 75.0, "2026Q2": 40.0},
              "parent_yoy": {"2025Q4": 90.0, "2026Q1": 60.0, "2026Q2": 41.0},
              "deduct_yoy": {"2025Q4": 85.0, "2026Q1": 58.0, "2026Q2": 42.0}}
        res = exit_check({}, qs, {"last": 30.0}, 45.0)     # 回撤仅33%
        self.assertEqual(res["level"], "exit")             # 靠连续降档触发

    def test_ok_when_steady(self):
        qs = {"periods": ["2025Q4", "2026Q1"], "latest_period": "2026Q1",
              "rev_yoy": {"2025Q4": 60.0, "2026Q1": 65.0},
              "parent_yoy": {"2025Q4": 50.0, "2026Q1": 55.0},
              "deduct_yoy": {"2025Q4": 48.0, "2026Q1": 52.0}}
        res = exit_check({}, qs, {"last": 40.0}, 45.0)
        self.assertEqual(res["level"], "ok")

    def test_divergence_warn(self):
        qs = {"periods": ["2026Q1", "2026Q2"], "latest_period": "2026Q2",
              "rev_yoy": {"2026Q1": 60.0, "2026Q2": 65.0},
              "parent_yoy": {"2026Q1": 60.0, "2026Q2": 885.0},
              "deduct_yoy": {"2026Q1": 55.0, "2026Q2": 2334.0}}
        res = exit_check({}, qs, {"last": 40.0}, 45.0)
        self.assertEqual(res["level"], "warn")
        self.assertTrue(any("背离" in x for x in res["reasons"]))


class TestMarketGate(unittest.TestCase):
    def test_ma250_method(self):
        g = market_gate({"index_close": 3320.0, "ma250": 3010.0,
                         "index_gain_1y": 12.0, "high_1y": 3400.0})
        self.assertEqual((g["verdict"], g["method"]), ("on", "ma250"))

    def test_proxy_method(self):
        g = market_gate({"index_gain_1y": 8.0, "index_close": 3300.0})
        self.assertEqual(g["verdict"], "weak")            # 无年线时涨>0只给weak
        self.assertEqual(g["method"], "proxy")
        g2 = market_gate({"index_gain_1y": -5.0, "index_close": 3300.0})
        self.assertEqual(g2["verdict"], "off")

    def test_no_data(self):
        g = market_gate({})
        self.assertEqual(g["verdict"], "unknown")


class TestWatchlistStateMachine(unittest.TestCase):
    def setUp(self):
        self.w = {"version": 1, "updated": None, "items": []}
        wl.add(self.w, wl.new_item("003010", "若羽臣"))

    def test_legal_path(self):
        wl.transition(self.w, "003010", "confirmed", note="测试")
        wl.transition(self.w, "003010", "holding", note="promote")
        wl.transition(self.w, "003010", "exit_signal", note="auto")
        wl.transition(self.w, "003010", "holding", note="keep")
        wl.transition(self.w, "003010", "closed", note="清仓")
        wl.transition(self.w, "003010", "scouted", note="reopen")
        self.assertEqual(self.w["items"][0]["state"], "scouted")

    def test_illegal_transition_raises(self):
        with self.assertRaises(wl.IllegalTransition):
            wl.transition(self.w, "003010", "holding")       # scouted→holding 非法
        with self.assertRaises(wl.IllegalTransition):
            wl.transition(self.w, "003010", "exit_signal")   # scouted→exit_signal 非法

    def test_confirmed_can_reopen_to_scouted(self):
        """人工降级重观察(2026-11-07 补边): confirmed→scouted 合法。"""
        wl.transition(self.w, "003010", "confirmed", note="R123")
        wl.transition(self.w, "003010", "scouted", note="人工降级重观察")
        self.assertEqual(self.w["items"][0]["state"], "scouted")

    def test_save_and_load_roundtrip_with_bak(self):
        import tempfile
        d = tempfile.mkdtemp()
        p = os.path.join(d, "watchlist.json")
        wl.save(self.w, p)
        wl.save(self.w, p)                                    # 第二次写产生 .bak
        self.assertTrue(os.path.exists(p + ".bak"))
        w2 = wl.load(p)
        self.assertEqual(w2["items"][0]["code"], "003010")


if __name__ == "__main__":
    unittest.main()


class TestDiscoveryDials(unittest.TestCase):
    """v5 发现度三表盘(2026-11-07): 纯函数, 资金/机构/筹码 → 相位。"""

    def test_left_gold_undiscovered(self):
        """行业β好但资金未发现+机构未覆盖+股价低位 = 左侧黄金。"""
        d = sector.discovery_dials(
            board_info={"rank": 30, "pct": 0.5},
            research_meta={"orgs": 3},
            holders_meta={"holders_wan": [("2026-09-30", 3.0), ("2025-09-30", 3.1),
                                          ("2024-09-30", 3.2), ("2023-09-30", 3.3)]},
            pos_1y=0.55)
        self.assertEqual(d["dials"]["资金"], 0)
        self.assertEqual(d["phase"], "未发现(左侧黄金)")

    def test_crowded(self):
        """板块榜首+研报覆盖饱和 = 拥挤(风险相位)。"""
        d = sector.discovery_dials(
            board_info={"rank": 2, "pct": 5.0},
            research_meta={"orgs": 25},
            holders_meta={"holders_wan": [("2026-09-30", 2.0), ("2025-09-30", 2.4)]},
            pos_1y=0.98)
        self.assertEqual(d["phase"], "拥挤")

    def test_discovering_middle(self):
        d = sector.discovery_dials(
            board_info={"rank": 10}, research_meta={"orgs": 10}, holders_meta={}, pos_1y=0.9)
        self.assertEqual(d["phase"], "发现中")
