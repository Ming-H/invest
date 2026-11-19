#!/usr/bin/env python3
"""周复盘 v3.8 —— docs/plans/2026-11-19-weekly-review-design.md

日线看波动, 周线看结构: 周报回答「本周结构是什么、下周准备什么」。
滚动 5 交易日窗(D-W1, 任何一天可跑), 资金主线从 machine.jsonl 日频
聚合(D-W2, 零问财额度), 周K由日K自聚合(D-W3), 回检周际化(D-W4)。

用法:
  python3 scripts/weekly.py --no-publish            # 草稿 outputs/weekly/{week_end}/
  python3 scripts/weekly.py --publish YYYY-MM-DD    # 发布周报(同复盘飞书文件夹)
"""

import argparse
import datetime as dt
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import data  # noqa: E402
from review import (OUTROOT, cn_date, folder_token, fmt, load_machine,  # noqa: E402
                    load_watchlist, md_to_xml_segments, sh)

WROOT = os.path.join(OUTROOT, "weekly")
WMACHINE = os.path.join(OUTROOT, "machine_weekly.jsonl")


# ═══════════════ 纯函数(测试覆盖: tests/test_weekly.py) ═══════════════

def weekly_window(dates, today=None, after_close=None, n=5):
    """滚动 n 交易日窗(D-W1): 末根=今日且未收盘(默认 now<15:00)则剔除。

    → (win_dates, prev_win_dates); 历史不足时相应短/空, 调用方如实降级。"""
    if not dates:
        return [], []
    today = today or dt.date.today().isoformat()
    if after_close is None:
        after_close = dt.datetime.now().hour >= 15
    ds = list(dates)
    if ds[-1] == today and not after_close:
        ds = ds[:-1]
    if len(ds) > n:
        win, prev = ds[-n:], ds[-2 * n:-n]
    else:
        win, prev = ds, []
    return win, prev


def _iso_week(iso_date):
    d = dt.date.fromisoformat(iso_date)
    return "%04d-W%02d" % (d.isocalendar()[0], d.isocalendar()[1])


def daily_to_weekly(rows):
    """日K→周K(D-W3, 按 ISO 周分组)。

    输入 [{date, close, volume?, open?, high?, low?}] —— 数据层只保证
    close+volume(东财/腾讯/新浪三级口径), OHLC 缺省回退 close(高点/低点
    为收盘价包络, 语义在 docstring 声明, 不假装有盘中极值)。
    → [{week, open, close, high, low, volume, pct}], pct=周内首日开盘→
    末日收盘(close-only 输入时=首日收盘→末日收盘)。"""
    out = []
    for r in sorted(rows, key=lambda x: x["date"]):
        wk = _iso_week(r["date"])
        close = float(r["close"])
        if out and out[-1]["week"] == wk:
            b = out[-1]
            b["close"] = close
            b["high"] = max(b["high"], float(r.get("high", close)))
            b["low"] = min(b["low"], float(r.get("low", close)))
            b["volume"] = b.get("volume", 0) + float(r.get("volume") or 0)
        else:
            out.append({"week": wk,
                        "open": float(r.get("open", close)),
                        "close": close,
                        "high": float(r.get("high", close)),
                        "low": float(r.get("low", close)),
                        "volume": float(r.get("volume") or 0)})
    for b in out:
        b["pct"] = round((b["close"] / b["open"] - 1) * 100, 2)
    return out


def weekly_ma(weeks, n=5, at=-1):
    """周收盘 MA_n; 样本不足返回 None。at=-1 取最新一周。"""
    closes = [float(w["close"]) for w in weeks]
    if not closes:
        return None
    end = len(closes) + at + 1 if at < 0 else at + 1
    start = end - n
    if start < 0 or end <= 0:
        return None
    return round(sum(closes[start:end]) / n, 2)


def aggregate_flows_week(rows, side="inflow", days=5):
    """machine.jsonl 日频资金快照 → 周累计(D-W2)。

    → [{name, total(亿), days(上榜天数), flag}]; days==1 标「一日游」。
    缺 flows 的日期按缺席计(不虚增分母)。"""
    agg = {}
    for r in rows:
        for item in ((r.get("flows") or {}).get(side) or []):
            name = item.get("name")
            if not name:
                continue
            a = agg.setdefault(name, {"name": name, "total": 0.0, "days": 0})
            a["total"] = round(a["total"] + (item.get("amount") or 0), 2)
            a["days"] += 1
    out = list(agg.values())
    for a in out:
        a["flag"] = "一日游" if a["days"] == 1 else "持续%d日" % a["days"]
    return sorted(out, key=lambda x: -x["total"])


def aggregate_lianban_week(rows):
    """连板周结构: 本周最高梯队 + 断板名单(周内出现但末日已消失)。"""
    nonempty = [r for r in rows if r.get("lianban")]
    if not nonempty:
        return {"max": None, "broken": []}
    seen, last_names = {}, set()
    for r in nonempty:
        for item in r["lianban"]:
            n = item.get("name")
            if not n:
                continue
            seen[n] = max(seen.get(n, 0), item.get("days") or 0)
        last_names = {i.get("name") for i in r["lianban"]}
    broken = [{"name": n, "days": d} for n, d in seen.items() if n not in last_names]
    broken.sort(key=lambda x: -x["days"])
    top = max(seen.items(), key=lambda kv: kv[1])
    return {"max": {"name": top[0], "days": top[1]}, "broken": broken}


def aggregate_gainers_week(rows):
    """日榜涨幅Top5快照 → 反复上榜聚合(主线股代理, 零额度)。

    → [{name, days, best_pct}] 按上榜次数降序。"""
    agg = {}
    for r in rows:
        for item in (r.get("gainers_top5") or []):
            n = item.get("name")
            if not n:
                continue
            a = agg.setdefault(n, {"name": n, "days": 0, "best_pct": item.get("pct") or 0})
            a["days"] += 1
            a["best_pct"] = max(a["best_pct"], item.get("pct") or 0)
    return sorted(agg.values(), key=lambda x: (-x["days"], -x["best_pct"]))


def recheck_last_week(prev, klines_by_code, sh_close):
    """周际回检(D-W4): 上周周报「下周观察」机检——触发位突破/上证周MA5。

    判定阈值与日报一致(0.5% 容差); prev=None(首期周报)返回空。"""
    out = []
    if not prev:
        return out
    for code, w in (prev.get("watchlist") or {}).items():
        trig = w.get("trig")
        bars = klines_by_code.get(code) or []
        price = bars[-1]["close"] if bars else None
        if trig and price:
            hit = price >= trig * 0.995
            out.append({"desc": "%s 触发位%.2f" % (code, trig),
                        "result": "已突破✅(周收%.2f)" % price if hit
                        else "未突破(周收%.2f)" % price,
                        "quote": "上周周收盘%.2f" % (w.get("week_close") or 0)})
    ma5w = ((prev.get("idx_weekly") or {}).get("sh") or {}).get("ma5w")
    if ma5w and sh_close:
        out.append({"desc": "上证周MA5(%.2f)" % ma5w,
                    "result": "站上✅(本周收%.2f)" % sh_close if sh_close >= ma5w
                    else "失守(本周收%.2f)" % sh_close,
                    "quote": "MA5W=%s" % ma5w})
    return out


def week_label(start, end):
    """(M.D-M.D) 标题区间。"""
    s, e = dt.date.fromisoformat(start), dt.date.fromisoformat(end)
    return "%d.%d-%d.%d" % (s.month, s.day, e.month, e.day)


# ═══════════════ 数据装配与渲染 ═══════════════

def load_machine_weekly(n=8):
    if not os.path.exists(WMACHINE):
        return []
    out = []
    for line in open(WMACHINE):
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out[-n:]


def _week_close_series(rows, win_dates):
    """窗口内首尾收盘 + 窗口前最后收盘(周涨幅基准=上周五收)。"""
    idx = {r["date"]: r for r in rows}
    closes = [idx[d]["close"] for d in win_dates if d in idx]
    prev = None
    for r in rows:
        if r["date"] < win_dates[0]:
            prev = r["close"]
    return (closes[-1] if closes else None), prev


def _chart_sh(sh_rows, win, outdir):
    """图1: 上证近6周走势(周分界+本周窗口高亮)。失败返回 None。"""
    try:
        import matplotlib
        matplotlib.use("Agg")
        from matplotlib import font_manager
        import matplotlib.pyplot as plt
        for _f in ("PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", "Noto Sans CJK SC"):
            if any(_f.lower() in f.name.lower() for f in font_manager.fontManager.ttflist):
                plt.rcParams["font.family"] = _f
                break
        plt.rcParams["axes.unicode_minus"] = False

        rows = sh_rows[-32:]
        xs = list(range(len(rows)))
        closes = [r["close"] for r in rows]
        fig, ax = plt.subplots(figsize=(9.6, 3.6), dpi=130)
        ax.plot(xs, closes, lw=1.4, color="#1f6feb")
        # 周分界竖线(ISO 周切换处)
        for i in range(1, len(rows)):
            if _iso_week(rows[i]["date"]) != _iso_week(rows[i - 1]["date"]):
                ax.axvline(i - 0.5, color="#d0d7de", lw=0.8, ls="--")
        # 本周窗口高亮
        wi = [i for i, r in enumerate(rows) if win[0] <= r["date"] <= win[-1]]
        if wi:
            ax.axvspan(wi[0] - 0.5, wi[-1] + 0.5, color="#1f6feb", alpha=0.08)
        step = max(1, len(rows) // 8)
        ax.set_xticks(xs[::step])
        ax.set_xticklabels([rows[i]["date"][5:] for i in xs[::step]], fontsize=8)
        ax.set_title("上证指数近6周（虚线=周边界，底色=本周窗口）", fontsize=10)
        for sp in ("top", "right"):
            ax.spines[sp].set_visible(False)
        path = os.path.join(outdir, "chart_week_sh.png")
        fig.tight_layout()
        fig.savefig(path)
        plt.close(fig)
        return path
    except Exception:
        return None


def build(args):
    degraded = []
    hist = load_machine(30)
    sh_rows = data.index_closes("sh000001", 300)
    all_dates = [r["date"] for r in sh_rows]
    win, prev_win = weekly_window(all_dates)
    if len(win) < 2:
        print("周复盘: 交易日序列不足(指数K线获取失败?)")
        return
    wk_start, wk_end = win[0], win[-1]
    label = week_label(wk_start, wk_end)
    outdir = os.path.join(WROOT, wk_end)
    os.makedirs(outdir, exist_ok=True)
    for old in os.listdir(outdir):
        if old.startswith("chart"):
            os.remove(os.path.join(outdir, old))

    win_rows = [r for r in hist if r["date"] in set(win)]
    coverage = "%d/%d" % (len(win_rows), len(win))
    if len(win_rows) < len(win):
        degraded.append("machine日频覆盖%s(%s)" % (coverage, "日报缺日→周聚合不完整"))
    prev_weekly = next((r for r in reversed(load_machine_weekly())
                        if r.get("week_end") != wk_end), None)

    lines = ["# %sA股周复盘（%s）" % (cn_date(wk_end), label), "",
             "> 窗口=最近%d个完整交易日(%s~%s) · 机器快照覆盖%s · 数据源全免费" % (
                 len(win), wk_start, wk_end, coverage), ""]

    # ===== 一、本周市场结构 =====
    lines += ["## 一、本周市场结构", ""]

    # --- 1.1 指数与量能(周) ---
    lines += ["### 1.1 周度指数与量能", "",
              "| 指数 | 周收盘 | 周涨跌 | 周MA5 | 周MA10 | 位置 |",
              "|---|---:|---:|---:|---:|---|"]
    idx_weekly = {}
    idx_series = {}
    for secid, name in data.INDICES:
        sym = ("sh" if secid.startswith("1.") else "sz") + secid.split(".")[1]
        try:
            rows = sh_rows if sym == "sh000001" else data.index_closes(sym, 300)
            weeks = daily_to_weekly(rows)
            last_close, prev_close = _week_close_series(rows, win)
            pct = (round((last_close / prev_close - 1) * 100, 2)
                   if (last_close and prev_close) else None)
            ma5w, ma10w = weekly_ma(weeks, 5), weekly_ma(weeks, 10)
            if last_close and ma5w:
                pos = "周MA5上方" if last_close >= ma5w else "周MA5下方"
                if ma10w:
                    pos += "/MA10%s" % ("上" if last_close >= ma10w else "下")
            else:
                pos = "—"
            lines.append("| %s | %s | %s | %s | %s | %s |" % (
                name, fmt(last_close, "%.2f"),
                ("%+.2f%%" % pct) if pct is not None else "—",
                fmt(ma5w, "%.0f"), fmt(ma10w, "%.0f"), pos))
            if sym == "sh000001":
                idx_weekly["sh"] = {"close": last_close, "pct": pct, "ma5w": ma5w}
            idx_series[sym] = rows
        except Exception:
            lines.append("| %s | — | — | — | — | — |" % name)
            degraded.append("周指数(%s)" % name)
    try:
        amt = data.market_total_amount(14)
        amt_by = {r["date"]: r["amount_yi"] for r in amt}
        amt_5d = sum(v for k, v in amt_by.items() if k in set(win))
        prev_keys = [d for d in all_dates if d < wk_start][-len(win):]
        amt_prev = sum(amt_by.get(d) or 0 for d in prev_keys)
        amt_state = ("周放量" if amt_prev and amt_5d > amt_prev * 1.05
                     else "周缩量" if amt_prev and amt_5d < amt_prev * 0.95 else "周常量")
        lines += ["", "**本周两市合计成交 %s 亿**（前一周 %s 亿，%s）" % (
            "{:,.0f}".format(amt_5d) if amt_5d else "—",
            "{:,.0f}".format(amt_prev) if amt_prev else "—", amt_state)]
    except Exception:
        amt_5d = amt_prev = None
        degraded.append("周量能")

    chart = _chart_sh(sh_rows, win, outdir)
    imgs = []
    if chart:
        lines += ["", "〖图1〗", ""]
        imgs.append((chart, "图1"))

    # --- 1.2 周度资金主线 ---
    lines += ["", "### 1.2 周度资金主线（5日累计 × 上榜天数）", ""]
    try:
        agg_in = aggregate_flows_week(win_rows, "inflow", len(win))
        agg_out = aggregate_flows_week(win_rows, "outflow", len(win))
        if agg_in:
            lines += ["| 净流入板块 | 周累计(亿) | 上榜 | 形态 |", "|---|---:|---:|---|"]
            for a in agg_in[:8]:
                lines.append("| %s | %s | %s/%d | %s |" % (
                    a["name"], "%+.1f" % a["total"], a["days"], len(win), a["flag"]))
            lines.append("")
        if agg_out:
            lines += ["| 净流出板块 | 周累计(亿) | 上榜 |", "|---|---:|---:|"]
            for a in agg_out[:4]:
                lines.append("| %s | %s | %s/%d |" % (
                    a["name"], "%+.1f" % a["total"], a["days"], len(win)))
            lines.append("")
        lines.append("⟦CLAUDE: 本周真主线 vs 一日游——上榜≥3/5的净流入板块是真主线候选, 结合上周主线(见素材包diff)判断延续/切换/新起, 2-3句⟧")
    except Exception:
        degraded.append("周资金主线")

    # --- 1.3 周度情绪结构 ---
    lines += ["", "### 1.3 周度情绪结构", ""]
    try:
        lb = aggregate_lianban_week(win_rows)
        if lb["max"]:
            broken = "、".join("%s(%d板断)" % (b["name"], b["days"])
                               for b in lb["broken"][:4]) or "无"
            lines += ["本周最高连板: **%s %d板**；周内断板: %s" % (
                lb["max"]["name"], lb["max"]["days"], broken), ""]
        rep = aggregate_gainers_week(win_rows)
        if rep:
            lines += ["| 反复上榜(日榜Top5) | 次数 | 最高涨幅 |", "|---|---:|---:|"]
            for g in rep[:8]:
                lines.append("| %s | %d | %+.1f%% |" % (g["name"], g["days"], g["best_pct"]))
            lines += [""]
    except Exception:
        degraded.append("周情绪结构")

    # --- 1.4 高增榜周度变化 ---
    lines += ["### 1.4 财报高增榜·周度变化", ""]
    em_now = (win_rows[-1].get("earnings_momentum") or []) if win_rows else []
    em_prev = (prev_weekly or {}).get("earnings_momentum") or []
    if em_now:
        names_now = {e.get("name") for e in em_now}
        names_prev = {e.get("name") for e in em_prev}
        enter = [e for e in em_now if e.get("name") not in names_prev]
        exit_ = [e.get("name") for e in em_prev if e.get("name") not in names_now]
        stay = len(names_now & names_prev)
        lines += ["本周Top20: 新进 **%d** 只 / 连续在榜 **%d** 只 / 退出 %d 只（上周快照对照）"
                  % (len(enter), stay, len(exit_))]
        if enter:
            lines += ["", "新进: " + "、".join(
                "%s(披露后%+.1f%%)" % (e.get("name"), e.get("chg_pct") or 0)
                for e in enter[:6])]
        if exit_:
            lines += ["", "退出: " + "、".join(exit_[:6])]
        lines += [""]
    else:
        lines += ["本周无高增榜快照(日报缺日或问财降级)", ""]
        degraded.append("高增榜周度变化")

    # ===== 二、关注标的周全息 =====
    stocks = load_watchlist()
    codes = [s.get("code") for s in stocks]
    lines += ["## 二、关注标的（周全息）", ""]
    quotes = {}
    try:
        quotes = data.stock_quotes_batch(codes) or {}
    except Exception:
        degraded.append("周批量行情")
    tri = data.tenscreener_triage(codes, {s.get("code"): s.get("name") for s in stocks}) or {}
    wl_machine = {}
    hold_lines, obs_lines = [], []
    for s in stocks:
        code, name = str(s.get("code")), s.get("name") or s.get("code")
        cost = s.get("cost")
        try:
            k = data.stock_kline(code, 250)
            k_win = [r for r in k if win[0] <= r["date"] <= win[-1]]
            wk_close, wk_prev = _week_close_series(k, win)
            weeks = daily_to_weekly(k)
            wpct = (round((wk_close / wk_prev - 1) * 100, 2)
                    if (wk_close and wk_prev) else None)
            ma5w = weekly_ma(weeks, 5)
            hi20 = max((r["close"] for r in k[-20:]), default=None)
            trig = round(hi20, 2) if hi20 else None
            mf = [r for r in data.stock_moneyflow_daily(code, 12)
                  if r["date"] in set(win)]
            if not mf:  # push2his 间歇限流: 一次重试(净额缺失≠0)
                import time as _t
                _t.sleep(1.2)
                mf = [r for r in data.stock_moneyflow_daily(code, 12)
                      if r["date"] in set(win)]
            # 缺失≠零: 限流返回空列表时如实标缺, 不把 0 当净额(诚实性)
            main5 = round(sum(r["main_yi"] for r in mf), 2) if mf else None
            trow = tri.get(code) or {}
            tri_now = ("%s·%s" % (trow.get("grade"), trow.get("state"))
                       if trow.get("grade") else "—")
            tri_prev = ((prev_weekly or {}).get("watchlist") or {}).get(code, {}).get("tri")
            tri_txt = tri_now + ("（%s→⇡）" % tri_prev if tri_prev and tri_prev != tri_now
                                 else "（维持）" if tri_prev else "")
            events = [a for a in data.announcements_recent(code, days=9, limit=8)
                      if win[0] <= (a.get("date") or "") <= win[-1]]
            wl_machine[code] = {"week_close": wk_close, "week_pct": wpct,
                                "main_5d": main5, "trig": trig, "tri": tri_now,
                                "events": len(events)}
            cells = ["%s(%s)" % (name, code), fmt(wk_close),
                     ("%+.2f%%" % wpct) if wpct is not None else "—",
                     ("%+.2f亿" % main5) if main5 is not None else "—",
                     trig or "—", "%d条" % len(events), tri_txt]
            if cost and wk_close:
                cells.append("%+.1f%%" % ((wk_close / cost - 1) * 100))
                hold_lines.append("| " + " | ".join(str(c) for c in cells) + " |")
            else:
                obs_lines.append("| " + " | ".join(str(c) for c in cells) + " |")
        except Exception:
            degraded.append("周全息(%s)" % code)
    if hold_lines:
        lines += ["### 持仓组", "",
                  "| 标的 | 周收盘 | 周涨跌 | 周主力净额 | 20日高(触发位) | 本周公告 | 十倍股分诊 | 距成本 |",
                  "|---|---|---:|---:|---:|---:|---|---:|"] + hold_lines + [""]
    if obs_lines:
        lines += ["### 观察组", "",
                  "| 标的 | 周收盘 | 周涨跌 | 周主力净额 | 20日高(触发位) | 本周公告 | 十倍股分诊 |",
                  "|---|---|---:|---:|---:|---:|---|"] + obs_lines + [""]
    lines.append("⟦CLAUDE: 持仓周检——周主力净额方向×连续性、阶段迁移含义、事件时间线该警觉什么, 每标的1-2句⟧")

    # ===== 三、上周观察回检 =====
    lines += ["", "## 三、上周观察回检", ""]
    checks = recheck_last_week(
        prev_weekly, {c: [r for r in (data.stock_kline(c, 15) or [])
                          if r["date"] <= wk_end] for c in codes},
        (idx_weekly.get("sh") or {}).get("close"))
    if checks:
        lines += ["| 上周观察 | 本周结果 | 依据 |", "|---|---|---|"]
        for c in checks:
            lines.append("| %s | %s | %s |" % (c["desc"], c["result"], c["quote"]))
        lines += ["", "⟦CLAUDE: 一句话——哪条兑现/落空, 判断信用记账⟧"]
    else:
        lines += ["（首期周报，无上周观察可回检）"]

    # ===== 四、周度小结与下周观察 =====
    lines += ["", "## 四、周度小结与下周观察", "",
              "⟦CLAUDE: 本周结构定性——延续/切换/转折, 结合1.2主线diff与1.3情绪, 2-3句⟧", ""]
    cal = []
    for s in stocks:
        code, name = str(s.get("code")), s.get("name") or s.get("code")
        nr = data.next_report_date(code)
        if nr:
            cal.append("%s 预约披露%s(%s)" % (name, nr.get("date"), nr.get("type") or ""))
        for u in data.unlock_calendar(code, within_days=14):
            if wk_end < u.get("date", "") <= (dt.date.fromisoformat(wk_end)
                                              + dt.timedelta(days=9)).isoformat():
                cal.append("%s 解禁%s(占比%.1f%%)" % (name, u.get("date"), u.get("ratio_pct") or 0))
    lines += ["**下周日历**：" + ("；".join(cal) if cal else "无预约事项"), "",
              "⟦CLAUDE: 下周观察——必须可机检(价位/触发位/数据口径), 下周周报自动回检, 2-3条⟧"]

    if degraded:
        lines += ["", "---", "本次数据降级项: " + "、".join(degraded)]

    md = os.path.join(outdir, "weekly.md")
    open(md, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    json.dump({"imgs": [(p, a) for p, a in imgs], "win": win,
               "degraded": degraded, "label": label, "week_end": wk_end},
              open(os.path.join(outdir, "manifest.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)

    # machine_weekly.jsonl(同周幂等覆盖)
    rows = [r for r in load_machine_weekly(100) if r.get("week_end") != wk_end]
    rows.append({"week_start": wk_start, "week_end": wk_end, "label": label,
                 "idx_weekly": idx_weekly, "amt_5d": amt_5d, "amt_prev_5d": amt_prev,
                 "top_flows": agg_in[:5] if agg_in else [],
                 "sentiment": lb if win_rows else {},
                 "gainers": rep[:5] if win_rows else [],
                 "earnings_momentum": em_now, "watchlist": wl_machine,
                 "next_watch": [], "degraded": degraded})
    with open(WMACHINE, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    _write_notes(outdir, prev_weekly, agg_in if win_rows else [], lb if win_rows else {},
                 em_now, em_prev, wl_machine, checks)
    print("周复盘草稿: %s (窗口 %s~%s, 覆盖%s)%s" % (
        md, wk_start, wk_end, coverage,
        " · 降级%d项" % len(degraded) if degraded else ""))


def _write_notes(outdir, prev_weekly, agg_in, lb, em_now, em_prev, wl, checks):
    """素材包(Claude润色用, 不进正稿): 周际 diff 与回检结果。"""
    L = ["# 周报复盘素材包（不进正稿）", ""]
    prev_flows = (prev_weekly or {}).get("top_flows") or []
    if prev_flows and agg_in:
        pin = {a["name"] for a in prev_flows}
        now = {a["name"] for a in agg_in}
        L += ["## 主线diff（上周Top5 → 本周Top8）", "",
              "延续: " + ("、".join(sorted(pin & now)) or "无"),
              "新起: " + ("、".join(sorted(now - pin)) or "无"),
              "退潮: " + ("、".join(sorted(pin - now)) or "无"), ""]
    L += ["## 情绪", "", "最高梯队: %s；断板: %s" % (
        lb.get("max") or "—",
        "、".join("%s(%d)" % (b["name"], b["days"]) for b in lb.get("broken", [])[:6]) or "无"), ""]
    if em_prev:
        n_now = {e.get("name") for e in em_now}
        n_prev = {e.get("name") for e in em_prev}
        L += ["## 高增榜diff", "", "新进: " + ("、".join(sorted(n_now - n_prev)) or "无"),
              "退出: " + ("、".join(sorted(n_prev - n_now)) or "无"), ""]
    if wl:
        L += ["## watchlist周快照", ""]
        for c, w in wl.items():
            L.append("- %s: 周收%s(%s%%) 主力%+.2f亿 分诊%s" % (
                c, w.get("week_close"), w.get("week_pct"), w.get("main_5d") or 0,
                w.get("tri")))
    if checks:
        L += ["", "## 上周观察回检(机检结果)", ""]
        for c in checks:
            L.append("- %s → %s" % (c["desc"], c["result"]))
    open(os.path.join(outdir, "notes_for_claude.md"), "w", encoding="utf-8").write(
        "\n".join(L) + "\n")


def publish_weekly(md_path):
    man = json.load(open(os.path.join(os.path.dirname(os.path.abspath(md_path)),
                                      "manifest.json")))
    outdir = os.path.dirname(os.path.abspath(md_path))
    tok = folder_token()
    title = "%sA股周复盘（%s）" % (cn_date(man["week_end"]), man["label"])
    img_by_anchor = dict((a, os.path.basename(p)) for p, a in man.get("imgs", []))
    open(os.path.join(outdir, "stub.md"), "w").write("　")
    out = sh('lark-cli drive +import --file stub.md --type docx --name "%s" '
             '--folder-token %s --as user' % (title, tok), cwd=outdir)
    m = re.search(r'"token":\s*"([A-Za-z0-9]+)"', out)
    if not m:
        raise RuntimeError("创建文档失败: " + out[:300])
    doc = m.group(1)
    segs = md_to_xml_segments(open(md_path, encoding="utf-8").read(), img_by_anchor)
    ok_img = total_img = 0
    for kind, val in segs:
        if kind == "xml":
            open(os.path.join(outdir, "seg.xml"), "w").write(val)
            sh('lark-cli docs +update --doc %s --command append --content "$(cat seg.xml)" '
               '--as user' % doc, cwd=outdir, timeout=120)
        else:
            total_img += 1
            r = sh("lark-cli docs +update --doc %s --command append "
                   "--content '<img path=\"@./%s\" width=\"820\"/>' --as user" % (doc, val),
                   cwd=outdir, timeout=180)
            ok_img += 1 if ('"result": "success"' in r or '"ok": true' in r) else 0
    return doc, "https://my.feishu.cn/docx/" + doc, ok_img, total_img


def main():
    ap = argparse.ArgumentParser(description="A股周复盘 v3.8")
    ap.add_argument("--no-publish", action="store_true", help="只生成草稿不发飞书")
    ap.add_argument("--publish", help="发布指定周(week_end=YYYY-MM-DD)的已生成周报")
    args = ap.parse_args()
    if args.publish:
        md = os.path.join(WROOT, args.publish, "weekly.md")
        if not os.path.exists(md):
            print("未找到周报草稿: %s (先跑 --no-publish)" % md)
            sys.exit(1)
        _doc, url, ok, total = publish_weekly(md)
        print("已发布: %s (插图 %d/%d)" % (url, ok, total))
        return
    build(args)


if __name__ == "__main__":
    main()
