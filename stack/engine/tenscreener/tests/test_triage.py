#!/usr/bin/env python3
"""triage 生命周期硬判离线单测(用户规则: 已涨≥5x+回撤≥30%+反弹<90%前高=已兑现否决)。用法: python3 tests/test_triage.py"""
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import triage  # noqa: E402


def check(cond, msg):
    print(("  ✓ " if cond else "  ✗ FAIL: ") + msg)
    if not cond:
        raise AssertionError(msg)


def mk(path, base=10.0):
    """按 (天数, 收盘价) 序列造 rows: [[date, o, c, h, l, v]]"""
    out, d0 = [], date(2024, 1, 1)
    for i, c in enumerate(path):
        d = (d0 + timedelta(days=i)).isoformat()
        out.append([d, c, c, c, c, 1e6])
    return out


def ramp(n, a, b):
    return [a + (b - a) * i / max(n - 1, 1) for i in range(n)]

print("① 已兑现退潮(done): 10→60(6x)后跌到38(-37%), 反弹高点50(<54)")
rows = mk(ramp(200, 10, 60) + ramp(80, 60, 38) + [38] * 10 + ramp(30, 38, 50) + ramp(20, 50, 38))
r = triage.lifecycle(rows)
check(r["state"] == "done" and r["multiple"] == 6.0 and abs(r["drawdown"] - 36.7) < 0.5,
      "state=%s mult=%s dd=%s%%" % (r["state"], r["multiple"], r["drawdown"]))

print("② 未达否决线①: 倍数不足(4x)深回撤 → 不算done")
rows = mk(ramp(150, 10, 40) + ramp(100, 40, 26) + [26] * 10)   # 4x, -35%, 反弹=1.0前高?
r = triage.lifecycle(rows)
check(r["state"] != "done", "state=%s(4x回撤35%%但倍数<5 → 不否决)" % r["state"])

print("③ 未达否决线②: 回撤不足(16%)但5x → 回调观察")
rows = mk(ramp(200, 10, 50) + ramp(50, 50, 42) + [42] * 10)    # 5x, -16%
r = triage.lifecycle(rows)
check(r["state"] == "pullback", "state=%s" % r["state"])

print("④ 反弹摸回9折以上 → 不算done(强反弹)")
path = ramp(200, 10, 60) + ramp(80, 60, 38) + ramp(60, 38, 56) + [56] * 10   # 反弹56>54
r = triage.lifecycle(mk(path))
check(r["state"] != "done", "state=%s(反弹56≥90%%×60 → 高点递降不成立)" % r["state"])

print("⑤ 上升趋势: 现价≥85%×区间高")
rows = mk(ramp(200, 10, 100) + [88] * 5)
r = triage.lifecycle(rows)
check(r["state"] == "uptrend" and abs(r["near_high_ratio"] - 88.0) < 0.1, "state=%s" % r["state"])

print("⑥ 高点在低点之前(先跌后阴跌) → 不触发done")
rows = mk(ramp(50, 80, 20) + ramp(150, 20, 14) + [14] * 5)     # 高80在前, 低14在后
r = triage.lifecycle(rows)
check(r["state"] != "done", "state=%s(ordered=False)" % r["state"])

print("⑦ 左侧未启动: 窄幅无主升")
rows = mk([10, 11, 12, 14, 13, 11] * 30 + [10.8] * 5)   # 高14在前, 末值10.8(77%)
r = triage.lifecycle(rows)
check(r["state"] == "left", "state=%s" % r["state"])

print("\n全部通过 ✓")
