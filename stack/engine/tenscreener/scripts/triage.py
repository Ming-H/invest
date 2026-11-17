#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""triage.py — 批量十倍股分诊(v5.3, 2026-11-17): 每日复盘 1.4 财报高增Top20 联动入口。

用户规则(2026-11-17 定稿, 硬判一票否决):
  一个十倍股一定处于上升趋势; 若主升段已走完、创过新高后未再创新高 → 不是候选。
  已兑现退潮 = 区间低→高已涨 ≥ DONE_MULT(5x) 且 现价距高点回撤 ≥ DONE_DD(30%)
               且 近60日最高 < 区间最高×REBOUND_CAP(90%)   # 反弹结构: 连9折都摸不到
  上升趋势候选 = 现价 ≥ 区间最高×NEAR_HIGH(85%) 或 近60日刷新区间最高
  左侧未启动 = 其余

分级(生命周期优先于闸门):
  done = 已兑现退潮(一票否决, 输出「已走完X.Xx·回撤Y%」)
  A    = 上升趋势候选 + Stage1 三线闸门过
  B    = 左侧未启动 + 闸门过(标"左侧", 亿纬型)
  C    = 闸门未过(标注卡线指标与缺口)

全免费源: 腾讯前复权K线(kline.fetch_qfq, 自带当日缓存) + 东财F10(judge.stage1_gate,
emoney 缓存)。同日重跑幂等。非投资建议。
"""
import json
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kline  # noqa: E402
import judge  # noqa: E402

# 判据阈值(CLI 可调)
DONE_MULT = 5.0      # 低→高已涨倍数 ≥ 此值视为"主升段已走完一段行情"
DONE_DD = 0.30       # 现价距区间高点回撤 ≥ 30%
REBOUND_CAP = 0.90   # 近60日最高 < 区间最高×90% = 反弹连9折都未摸到(高点递降结构)
NEAR_HIGH = 0.85     # 现价 ≥ 区间最高×85% = 贴新高(上升趋势)
RECENT = 60          # 反弹结构观察窗(交易日)


def sym_of(code):
    c = str(code).split(".")[0]
    if c.startswith(("92", "43", "83", "87", "88")):
        return "bj" + c
    return ("sh" if c.startswith(("6", "9")) else "sz") + c


def lifecycle(rows, done_mult=DONE_MULT, done_dd=DONE_DD,
              rebound_cap=REBOUND_CAP, near_high=NEAR_HIGH, recent=RECENT):
    """生命周期硬判(纯函数, 可离线单测)。
    rows = [[date, open, close, high, low, vol], ...] 升序(收盘口径)。
    → dict(state, low, low_date, high, high_date, last, multiple, drawdown,
           rebound_ratio, near_high_ratio)"""
    if not rows or len(rows) < 20:
        return {"state": "nodata"}
    closes = [(r[0], float(r[2])) for r in rows]
    lo_d, lo = min(closes, key=lambda x: x[1])
    hi_d, hi = max(closes, key=lambda x: x[1])
    # 高点必须在低点之后才算"主升段已走完"; 高点在前=下跌后的低位区, 走左侧逻辑
    ordered = lo_d <= hi_d
    last_d, last = closes[-1]
    recent_hi = max(c for _, c in closes[-recent:])
    multiple = hi / lo if lo > 0 else None
    drawdown = 1 - last / hi if hi > 0 else None
    rebound_ratio = recent_hi / hi if hi > 0 else None
    near_ratio = last / hi if hi > 0 else None
    if ordered and multiple and multiple >= done_mult and drawdown and drawdown >= done_dd \
            and rebound_ratio and rebound_ratio < rebound_cap:
        state = "done"          # 已兑现退潮: 用户规则一票否决
    elif near_ratio and near_ratio >= near_high:
        state = "uptrend"       # 贴新高/上升趋势
    elif last / lo >= 2.0 if lo > 0 else False:
        state = "pullback"      # 已有主升(低点起≥2x)但回调未达done否决线 — 回调观察
    else:
        state = "left"          # 左侧未启动(尚未走出主升)
    return {"state": state, "low": round(lo, 2), "low_date": lo_d, "high": round(hi, 2),
            "high_date": hi_d, "last": round(last, 2), "multiple": round(multiple, 1) if multiple else None,
            "drawdown": round(drawdown * 100, 1) if drawdown is not None else None,
            "rebound_ratio": round(rebound_ratio * 100, 1) if rebound_ratio else None,
            "near_high_ratio": round(near_ratio * 100, 1) if near_ratio else None}


def triage_one(code, name=None, lookback_years=3):
    """单标的分诊: 生命周期 + Stage1闸门 → {code,name,grade,state,...,conclusion}"""
    code6 = str(code).split(".")[0]
    name = name or code6
    beg = (date.today() - timedelta(days=int(365.25 * lookback_years))).isoformat()
    end = date.today().isoformat()
    try:
        rows = kline.fetch_qfq(sym_of(code6), beg, end)
    except Exception as e:
        return {"code": code6, "name": name, "grade": "err", "state": "nodata",
                "conclusion": "K线获取失败: %s" % str(e)[:40]}
    life = lifecycle(rows)
    if life.get("state") == "nodata":
        return {"code": code6, "name": name, "grade": "err", "state": "nodata",
                "conclusion": "K线数据不足"}
    out = dict(life, code=code6, name=name)
    try:
        passed, _qs, _sg, reason, latest, kind = judge.stage1_gate(code6)
        out["gate"] = bool(passed)
        out["gate_kind"] = kind          # standard/turnaround/forecast
        out["gate_reason"] = reason
        out["latest_period"] = latest
    except Exception as e:
        passed, out["gate"], out["gate_reason"] = None, None, "闸门取数失败: %s" % str(e)[:40]

    if life["state"] == "done":
        out["grade"] = "done"
        out["conclusion"] = "已兑现退潮: %s→%s走完%.1fx主升, 现回撤%.0f%%且反弹未过前高9折 — 非候选" % (
            life["low_date"], life["high_date"], life["multiple"], life["drawdown"])
    elif passed:
        out["grade"] = "A" if life["state"] == "uptrend" else "B"
        zh = {"uptrend": "上升趋势", "pullback": "回调观察", "left": "左侧未启动"}
        tag = "%s·距高点%.0f%%" % (zh.get(life["state"], life["state"]), 100 - life["near_high_ratio"])
        out["conclusion"] = "%s + 三线闸门过 — 候选" % tag
    else:
        out["grade"] = "C"
        out["conclusion"] = "闸门未过: %s" % (out.get("gate_reason") or "?")[:60]
    return out


def triage(codes, names=None, lookback_years=3):
    names = names or {}
    return [triage_one(c, names.get(str(c).split(".")[0]), lookback_years)
            for c in codes]


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="批量十倍股分诊(免费源)")
    ap.add_argument("--codes", required=True, help="逗号分隔代码")
    ap.add_argument("--names", default="", help="可选: 逗号分隔名称(与codes对齐)")
    ap.add_argument("--lookback", type=int, default=3, help="回看年数(默认3)")
    ap.add_argument("--json", action="store_true", help="输出JSON")
    a = ap.parse_args()
    cs = [c.strip() for c in a.codes.split(",") if c.strip()]
    ns = [n.strip() for n in a.names.split(",") if n.strip()] if a.names else []
    res = triage(cs, dict(zip(cs, ns)), a.lookback)
    if a.json:
        print(json.dumps(res, ensure_ascii=False))
    else:
        for r in res:
            print("%-6s %s %-8s %s" % (r.get("name"), r.get("code"), r.get("grade"), r.get("conclusion")))
