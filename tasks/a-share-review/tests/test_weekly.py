"""v3.8 周复盘纯函数测试（docs/plans/2026-11-19-weekly-review-design.md §八-1）。

只测聚合与判定逻辑——数据源与渲染走实跑验收。全部离线确定性。
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))

from weekly import (  # noqa: E402
    aggregate_flows_week,
    aggregate_gainers_week,
    aggregate_lianban_week,
    daily_to_weekly,
    recheck_last_week,
    week_label,
    weekly_ma,
    weekly_window,
)


# ── D-W1 滚动 5 交易日窗 ─────────────────────────────────


class TestWeeklyWindow:
    def test_takes_last_five_completed(self):
        dates = ["2026-11-10", "2026-11-11", "2026-11-12", "2026-11-13",
                 "2026-11-16", "2026-11-17", "2026-11-18"]
        win, prev = weekly_window(dates, today="2026-11-19")
        assert win == ["2026-11-12", "2026-11-13", "2026-11-16",
                       "2026-11-17", "2026-11-18"]
        assert prev == ["2026-11-05", "2026-11-06", "2026-11-10", "2026-11-11",
                        "2026-11-12"][:4] or prev == ["2026-11-10", "2026-11-11"]

    def test_intraday_today_bar_dropped(self):
        """盘中运行: 末根=今日未收盘K线必须剔除(D-W1)。"""
        dates = ["2026-11-12", "2026-11-13", "2026-11-16", "2026-11-17",
                 "2026-11-18", "2026-11-19"]
        win, _ = weekly_window(dates, today="2026-11-19")
        assert win[-1] == "2026-11-18"
        assert len(win) == 5

    def test_today_bar_kept_when_closed(self):
        """收盘后运行: 末根=今日完整K线保留。"""
        dates = ["2026-11-13", "2026-11-16", "2026-11-17", "2026-11-18",
                 "2026-11-19"]
        win, _ = weekly_window(dates, today="2026-11-19", after_close=True)
        assert win[-1] == "2026-11-19"

    def test_short_history_degrades(self):
        win, prev = weekly_window(["2026-11-17", "2026-11-18"], today="2026-11-19")
        assert win == ["2026-11-17", "2026-11-18"]
        assert prev == []


# ── D-W3 日K→周K聚合 ─────────────────────────────────────


class TestDailyToWeekly:
    def _days(self):
        # 两根周K: 11-09(一)~11-13(五), 11-16(一)~11-18(三)
        return [
            {"date": "2026-11-09", "open": 10, "close": 11, "high": 12, "low": 9.5, "volume": 100},
            {"date": "2026-11-10", "open": 11, "close": 10.5, "high": 11.5, "low": 10, "volume": 120},
            {"date": "2026-11-13", "open": 10.5, "close": 12, "high": 12.5, "low": 10.2, "volume": 80},
            {"date": "2026-11-16", "open": 12, "close": 13, "high": 13.2, "low": 11.8, "volume": 90},
            {"date": "2026-11-18", "open": 13, "close": 12.6, "high": 13.5, "low": 12.4, "volume": 70},
        ]

    def test_group_by_iso_week(self):
        weeks = daily_to_weekly(self._days())
        assert len(weeks) == 2
        w1, w2 = weeks
        assert w1["week"] == "2026-W46" and w1["open"] == 10 and w1["close"] == 12
        assert w1["high"] == 12.5 and w1["low"] == 9.5 and w1["volume"] == 300
        assert w2["close"] == 12.6 and w2["volume"] == 160

    def test_weekly_pct(self):
        weeks = daily_to_weekly(self._days())
        # 周涨跌 = 周内首日开盘 → 末日收盘
        assert abs(weeks[0]["pct"] - (12 / 10 - 1) * 100) < 1e-9


class TestWeeklyMA:
    def test_ma5_over_weekly_closes(self):
        weeks = [{"close": float(i)} for i in range(1, 8)]  # 1..7
        assert weekly_ma(weeks, 5, at=-1) == 5.0            # (3+4+5+6+7)/5
        assert weekly_ma(weeks, 5, at=-2) == 4.0
        assert weekly_ma(weeks[:3], 5) is None              # 样本不足→None


# ── D-W2 machine.jsonl 周聚合 ────────────────────────────


class TestAggregateFlows:
    def _rows(self):
        return [
            {"date": "2026-11-16", "flows": {"inflow": [
                {"name": "元件", "amount": 10}, {"name": "白酒", "amount": 5}]}},
            {"date": "2026-11-17", "flows": {"inflow": [
                {"name": "元件", "amount": 20}, {"name": "军工", "amount": 8}]}},
            {"date": "2026-11-18", "flows": {"inflow": [
                {"name": "元件", "amount": 15}]}},
        ]

    def test_total_and_days(self):
        agg = aggregate_flows_week(self._rows(), side="inflow", days=3)
        assert agg[0]["name"] == "元件"
        assert agg[0]["total"] == 45 and agg[0]["days"] == 3
        jun = next(a for a in agg if a["name"] == "军工")
        assert jun["total"] == 8 and jun["days"] == 1

    def test_one_day_wonder_flag(self):
        agg = aggregate_flows_week(self._rows(), side="inflow", days=3)
        jun = next(a for a in agg if a["name"] == "军工")
        assert jun["flag"] == "一日游"

    def test_missing_flow_rows_counted_as_absent(self):
        """machine 缺日 → 上榜天数<窗口, 覆盖率如实(D-W2)。"""
        rows = self._rows()[:2] + [{"date": "2026-11-18"}]   # 18日无flows
        agg = aggregate_flows_week(rows, side="inflow", days=3)
        yuanjian = next(a for a in agg if a["name"] == "元件")
        assert yuanjian["days"] == 2


class TestAggregateLianban:
    def test_max_streak_and_break(self):
        rows = [
            {"date": "2026-11-16", "lianban": [{"name": "华东重机", "days": 4},
                                               {"name": "甲股", "days": 2}]},
            {"date": "2026-11-17", "lianban": [{"name": "华东重机", "days": 5},
                                               {"name": "乙股", "days": 2}]},
            {"date": "2026-11-18", "lianban": [{"name": "乙股", "days": 3}]},
        ]
        out = aggregate_lianban_week(rows)
        assert out["max"] == {"name": "华东重机", "days": 5}
        assert {b["name"] for b in out["broken"]} == {"华东重机", "甲股"}

    def test_empty_week(self):
        assert aggregate_lianban_week([{"date": "x"}])["max"] is None


class TestAggregateGainers:
    def test_repeat_appearance_ranking(self):
        rows = [
            {"date": "d1", "gainers_top5": [{"name": "甲", "pct": 10},
                                            {"name": "乙", "pct": 9}]},
            {"date": "d2", "gainers_top5": [{"name": "甲", "pct": 12},
                                            {"name": "丙", "pct": 8}]},
        ]
        out = aggregate_gainers_week(rows)
        assert out[0] == {"name": "甲", "days": 2, "best_pct": 12}
        assert out[1]["name"] == "乙"


# ── D-W4 周际回检 ────────────────────────────────────────


class TestRecheckLastWeek:
    def _prev(self):
        return {"week_end": "2026-11-18",
                "watchlist": {"603268": {"week_close": 170.0, "trig": 175.0}},
                "idx_weekly": {"sh": {"close": 3580.0, "ma5w": 3600.0}},
                "next_watch": []}

    def test_trigger_hit_and_miss(self):
        klines = {"603268": [{"date": "2026-11-18", "close": 176.0}]}
        out = recheck_last_week(self._prev(), klines, sh_close=3590.0)
        by = {r["desc"]: r for r in out}
        assert "已突破✅" in by["603268 触发位175.00"]["result"]

        klines2 = {"603268": [{"date": "2026-11-18", "close": 171.0}]}
        out2 = recheck_last_week(self._prev(), klines2, sh_close=3590.0)
        assert "未突破" in {r["desc"]: r for r in out2}["603268 触发位175.00"]["result"]

    def test_week_ma5_guard(self):
        out = recheck_last_week(self._prev(), {}, sh_close=3610.0)
        ma_row = next(r for r in out if "周MA5" in r["desc"])
        assert "站上✅" in ma_row["result"]

    def test_no_prev_returns_empty(self):
        assert recheck_last_week(None, {}, sh_close=3600.0) == []


# ── 标题区间 ─────────────────────────────────────────────


class TestWeekLabel:
    def test_label(self):
        assert week_label("2026-11-12", "2026-11-18") == "11.12-11.18"
