#!/usr/bin/env python3
"""
离线单测: v3.4 财报高增榜 — 报告期季节判定 / 问财解析 / 涨幅排序 / 连榜幂等 / 报告期滚动。
不联网(_wencai/stock_quotes_batch/_disc_base_close 全部 monkeypatch)。用法: python3 tests/test_earnings.py
"""
import datetime
import json
import sys
import tempfile
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import data  # noqa: E402


def check(cond, msg):
    print(("  ✓ " if cond else "  ✗ FAIL: ") + msg)
    if not cond:
        raise AssertionError(msg)


def approx(a, b, eps=1e-6):
    return a is not None and abs(a - b) <= eps * max(abs(a), abs(b), 1.0)


# ---- ① 报告期季节自适应 ----
print("① latest_report_period 边界")
D = datetime.date
cases = [(D(2026, 11, 16), ("2026年三季报", "2026Q3")),
         (D(2026, 12, 31), ("2026年三季报", "2026Q3")),
         (D(2027, 1, 31), ("2026年三季报", "2026Q3")),
         (D(2027, 2, 1), ("2026年年报", "2026FY")),
         (D(2027, 4, 30), ("2026年年报", "2026FY")),
         (D(2027, 5, 1), ("2027年一季报", "2027Q1")),
         (D(2027, 7, 31), ("2027年一季报", "2027Q1")),
         (D(2027, 8, 1), ("2027年中报", "2027Q2"))]
for d, want in cases:
    got = data.latest_report_period(d)
    check(got == want, "%s → %s" % (d, want[1]))

# ---- ② 公告日期归一 ----
print("② _norm_ymd")
check(data._norm_ymd("20261031") == "2026-10-31", "紧凑格式")
check(data._norm_ymd("2026-10-31 00:00:00") == "2026-10-31", "带时间截断")
check(data._norm_ymd(None) is None and data._norm_ymd("abc") is None, "异常返回None")

# ---- ③ 问财筛选解析(真实字段形状) ----
print("③ earnings_high_growth_screen 解析")
FAKE = [{"股票代码": "601919.SH", "股票简称": "中远海控",
         "单季度_归母净利润[20260930]": 17230012400.17,
         "单季度_归母净利润同比增长率[20260930]": 80.73,
         "公告日期[20260930]": "20261030"},
        {"股票代码": "300308.SZ", "股票简称": "中际旭创",
         "单季度_归母净利润[20260930]": 8586789313.48,
         "单季度_归母净利润同比增长率[20260930]": 173.74,
         "公告日期[20260930]": "20261030"},
        {"股票代码": "000000.SZ", "股票简称": "坏行", "公告日期[20260930]": ""},
        {"股票代码": "600079.SH", "股票简称": "ST人福",
         "单季度_归母净利润[20260930]": 1.09e9, "单季度_归母净利润同比增长率[20260930]": 103.0,
         "公告日期[20260930]": "20261030"}]  # 坏行/ST 均应跳过
data._wencai = lambda q, limit=20: FAKE
rows = data.earnings_high_growth_screen("2026年三季报")
check(len(rows) == 2, "坏行/ST跳过, 解析2条")
check(rows[0]["code"] == "601919" and rows[0]["np_yi"] == 172.3
      and rows[0]["yoy_pct"] == 80.7 and rows[0]["disc_date"] == "2026-10-30",
      "字段归一(代码去后缀/亿元/日期)")

# ---- ④ earnings_momentum: 排序/TopN/连榜/幂等/报告期滚动 ----
print("④ earnings_momentum 全链(离线)")
tmpdir = tempfile.mkdtemp()
data.EMOM_CACHE = os.path.join(tmpdir, "emom.json")
data.latest_report_period = lambda today=None: ("2026年三季报", "2026Q3")
KL = {"601919": 10.0, "300308": 20.0}          # 披露日基准收盘


def fake_quotes(codes):
    return {"601919": {"price": 11.0}, "300308": {"price": 18.0}}  # +10% / -10%


data._disc_base_close = lambda code, dd: KL.get(code)
data.stock_quotes_batch = fake_quotes
r, err = data.earnings_momentum(n=2)
check(err is None and len(r["rows"]) == 2, "返回2条无错")
check(r["rows"][0]["code"] == "601919" and approx(r["rows"][0]["chg_pct"], 10.0),
      "按披露后涨幅降序(+10%在前)")
check(r["rows"][1]["chg_pct"] == -10.0, "负涨幅在后")
check(r["rows"][0]["streak"] == 1 and r["rows"][1]["streak"] == 1, "首日连榜=1")

r2, _ = data.earnings_momentum(n=2)              # 同日重跑 → 幂等
check(r2["rows"][0]["streak"] == 1, "同日重跑连榜不重复累计(幂等)")

cache = json.load(open(data.EMOM_CACHE))
cache["updated"] = "2026-11-15"                   # 伪装成昨天, 模拟次日重跑
json.dump(cache, open(data.EMOM_CACHE, "w"))
data.stock_quotes_batch = lambda codes: {"601919": {"price": 11.0}}  # 300308掉出榜单
r3, _ = data.earnings_momentum(n=1)
check(r3["rows"][0]["streak"] == 2, "次日仍在榜 → 连榜+1")
check(json.load(open(data.EMOM_CACHE))["streak"]["300308"] == 0, "掉出榜单 → 清零")

data.latest_report_period = lambda today=None: ("2026年年报", "2026FY")  # 报告期滚动
r4, _ = data.earnings_momentum(n=2)
c4 = json.load(open(data.EMOM_CACHE))
check(c4["period"] == "2026FY" and c4["streak"]["601919"] == 1, "报告期滚动 → 缓存重建连榜重计")

print("\n全部通过 ✓")
