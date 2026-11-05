#!/usr/bin/env python3
"""
报告期工具 + 单季同比三线序列 · tenscreener

两部分:
  1. 报告期日历:今天 → 最新已披露报告期/最新完整年报/当前披露窗口
     (披露窗口:Q1@4月底、H1@8月底、Q3@10月底、FY@次年4月底 —— 静水"每年1/4/7-8/10月是财报季")
  2. 三线单季同比序列:一次问财批量取「近四个季度单季 营收/归母/扣非 同比增长率」,
     加纯函数分析:过线季数/三线共振/加速/降档/扣非归母背离。

所有阈值默认 40(静水核心因子#1"财报营收、利润增速≥40%"、5不买#2"主营增速低于40%不买"),
均作为参数暴露,不在调用侧硬编码新条件。

出处映射:
  - 三线分别看序列:静水核心因子#1 + 若羽臣复盘特征3(营收与利润会错位,24Q4 营收+14%掉线但利润过线)
  - 连续多季过线=持续超预期:静水核心α三特征"创新高+持续超预期+板块效应"
  - 扣非/归母背离必须拆解:静水财报分析框架"归母增速与扣非增速明显背离时,
    必须拆出非经营性贡献"(昭衍新药例:扣非+2334%里 7~7.8 亿来自实验猴生物资产)
纯标准库。
"""

import re
from datetime import date

from wencai import iwencai, _num, DEFAULT_FINANCE_CLI

PASS_LINE = 40.0   # 静水增速线(参数默认,勿在调用侧加码)


# ─── 报告期日历 ─────────────────────────────────────────────

def latest_report_period(today=None):
    """当前日期 → 最新已披露报告期 (year, quarter)。
    披露窗口:Q1@4月底、H1@8月底、Q3@10月底、FY@次年4月底。"""
    today = today or date.today()
    y, m = today.year, today.month
    if 5 <= m <= 8:
        return (y, 1)       # 5-8月:当年一季报
    if 9 <= m <= 10:
        return (y, 2)       # 9-10月:当年半年报
    if m >= 11:
        return (y, 3)       # 11-12月:当年三季报
    return (y - 1, 3)       # 1-4月:上年三季报(年报要4月底才出)


def latest_annual_period(today=None):
    """最新【完整披露】年报期 (year, 4)。年报 Y 在 Y+1 年 4/30 前披露完。"""
    today = today or date.today()
    return (today.year - 1, 4) if today.month >= 5 else (today.year - 2, 4)


def disclosure_window(today=None):
    """当前是否处于某个披露窗口内。返回 dict 或 None。
    窗口:1-4月=上年年报(至4/30)、7-8月=半年报(至8/31)、10月=三季报(至10/31)。"""
    today = today or date.today()
    y, m = today.year, today.month
    if 1 <= m <= 4:
        return {"period": (y - 1, 4), "label": f"{y-1}年年报", "deadline": f"{y}-04-30"}
    if m in (7, 8):
        return {"period": (y, 2), "label": f"{y}年半年报", "deadline": f"{y}-08-31"}
    if m == 10:
        return {"period": (y, 3), "label": f"{y}年三季报", "deadline": f"{y}-10-31"}
    return None


def prev_quarter(y, q):
    return (y - 1, 4) if q == 1 else (y, q - 1)


def q_label(y, q):
    return f"{y}Q{q}"


def q_enddate(y, q):
    return f"{y}{'0331' if q == 1 else '0630' if q == 2 else '0930' if q == 3 else '1231'}"


def q_cn(y, q):
    return {1: f"{y}年一季报", 2: f"{y}年半年报", 3: f"{y}年三季报", 4: f"{y}年年报"}[q]


def last_n_quarters(latest, n):
    """最近 n 个季度(含 latest),早 → 晚。"""
    res, cur = [], latest
    for _ in range(n):
        res.append(cur)
        cur = prev_quarter(*cur)
    return list(reversed(res))


# 兼容旧名(l1_screener 历史私有函数名)
_latest_report_period = latest_report_period
_prev_quarter = prev_quarter
_q_label = q_label
_q_enddate = q_enddate
_q_cn = q_cn
_last_n_quarters = last_n_quarters


# ─── 三线单季同比序列抓取 ───────────────────────────────────
# 问财语法(实测):「近四个季度单季xxx同比增长率」一次展开 4 个季度的逐季同比。
# 返回 key 形如 单季营业收入同比增长率[20240930] / 单季度_归母净利润同比增长率[...] 等,
# 用正则宽松解析,不依赖精确字段名(防漂移)。

_YOY_PATTERNS = {
    "rev_yoy": re.compile(r"单季度?_?营业收入同比增长率\[(\d{8})\]"),
    "parent_yoy": re.compile(r"单季度?_?归母净利润同比增长率\[(\d{8})\]"),
    "deduct_yoy": re.compile(r"单季度?_?扣非净利润同比增长率\[(\d{8})\]"),
}


def _enddate_to_label(e):
    try:
        return q_label(int(e[:4]), int(e[4:6]) // 3)
    except (ValueError, IndexError):
        return None


def _parse_yoy_row(item):
    """一行问财返回 → {line: {qlabel: yoy}}。"""
    out = {"rev_yoy": {}, "parent_yoy": {}, "deduct_yoy": {}}
    for line, pat in _YOY_PATTERNS.items():
        for k, v in item.items():
            m = pat.search(str(k))
            if m:
                lbl = _enddate_to_label(m.group(1))
                val = _num(v)
                if lbl and val is not None:
                    out[line][lbl] = val
    return out


def fetch_quarterly_yoy(names, cli_path=DEFAULT_FINANCE_CLI, batch=6):
    """批量取近四个季度单季三线同比。返回 {name: quarterly结构}。
    quarterly结构: {periods:[早→晚], rev_yoy:{}, parent_yoy:{}, deduct_yoy:{}, latest_period}
    Q4 单季问财覆盖不稳 → 缺失标 None 不造数;整批解析失败 → 显式逐期 fallback(仅此路径多花查询)。"""
    result = {}
    for i in range(0, len(names), batch):
        chunk = names[i:i + batch]
        query = (" ".join(chunk)
                 + " 近四个季度单季营业收入同比增长率 近四个季度单季归母净利润同比增长率"
                   " 近四个季度单季扣非净利润同比增长率")
        datas = iwencai(query, limit=len(chunk) + 4, cli_path=cli_path)
        parsed_any = False
        for x in datas:
            nm = x.get("股票简称")
            if not nm:
                continue
            rows = _parse_yoy_row(x)
            if any(rows[l] for l in rows):
                parsed_any = True
                result[nm] = _normalize_quarterly(rows)
        if not parsed_any and datas:        # 字段名漂移 → 显式逐期 fallback
            fb = _fetch_quarterly_yoy_explicit(chunk, cli_path)
            for nm, rows in fb.items():
                result[nm] = _normalize_quarterly(rows)
    return result


def _fetch_quarterly_yoy_explicit(names, cli_path, latest=None, n=4):
    """fallback:逐报告期显式查「{y}年{季}报单季xxx同比增长率」。"""
    latest = latest or latest_report_period()
    out = {nm: {"rev_yoy": {}, "parent_yoy": {}, "deduct_yoy": {}} for nm in names}
    for y, q in last_n_quarters(latest, n):
        query = (" ".join(names) + f" {q_cn(y, q)}单季营业收入同比增长率"
                 f" {q_cn(y, q)}单季归母净利润同比增长率 {q_cn(y, q)}单季扣非净利润同比增长率")
        datas = iwencai(query, limit=len(names) + 4, cli_path=cli_path)
        for x in datas:
            nm = x.get("股票简称")
            if nm not in out:
                continue
            lbl = q_label(y, q)
            for line, field in (("rev_yoy", "单季营业收入同比增长率"),
                                ("parent_yoy", "单季归母净利润同比增长率"),
                                ("deduct_yoy", "单季扣非净利润同比增长率")):
                for k, v in x.items():
                    if field in str(k) and q_enddate(y, q) in str(k):
                        val = _num(v)
                        if val is not None:
                            out[nm][line][lbl] = val
                        break
    return out


def _normalize_quarterly(rows):
    periods = sorted({p for l in rows for p in rows[l]}, key=_label_sortkey)
    latest = periods[-1] if periods else None
    return {**rows, "periods": periods, "latest_period": latest}


_LABEL_RE = re.compile(r"^(\d{4})Q([1-4])$")


def _label_sortkey(lbl):
    m = _LABEL_RE.match(lbl)
    return (int(m.group(1)), int(m.group(2))) if m else (0, 0)


# ─── 纯函数序列分析(全部可离线单测) ─────────────────────────

def pass_streak(series, line=PASS_LINE):
    """从最新季往回数连续 yoy≥line 的季数。series: {qlabel: yoy}。
    缺失值 → 中断(连续性无法确认)。"""
    streak = 0
    for lbl in sorted(series, key=_label_sortkey, reverse=True):
        v = series.get(lbl)
        if v is None or v < line:
            break
        streak += 1
    return streak


def pass_lines(qs, line=PASS_LINE):
    """最新披露季三线各自是否过线。返回 {rev,parent,deduct: bool, count, labels}。"""
    latest = qs.get("latest_period")
    if not latest:
        return {"rev": False, "parent": False, "deduct": False,
                "count": 0, "labels": []}
    flags, labels = {}, []
    for key, boolk in (("rev_yoy", "rev"), ("parent_yoy", "parent"), ("deduct_yoy", "deduct")):
        v = qs.get(key, {}).get(latest)
        ok = v is not None and v >= line
        flags[boolk] = ok
        if ok:
            labels.append({"rev": "营收", "parent": "归母", "deduct": "扣非"}[boolk])
    flags["count"] = sum(1 for k in ("rev", "parent", "deduct") if flags[k])
    flags["labels"] = labels
    return flags


def resonance(qs, line=PASS_LINE):
    """三线共振形态(看最新披露季):
    triple=三线全过 | profit_led=仅利润线过(若羽臣24Q4:营收+14%掉线,归母/扣非过)
    rev_led=仅营收过 | single=仅一线过 | none=全不过。营收与利润错位是常态,不剔除只标注。"""
    pl = pass_lines(qs, line)
    profit = pl["parent"] or pl["deduct"]
    if pl["count"] == 3:
        return "triple"
    if profit:
        return "profit_led"
    if pl["rev"]:
        return "rev_led"
    return "single" if pl["count"] == 1 else "none"


def yoy_accel(series):
    """最近4个有值季:后2均值 > 前2均值 → 加速。返回 (ok, info)。"""
    vals = [(lbl, v) for lbl, v in sorted(series.items(), key=lambda kv: _label_sortkey(kv[0]))
            if v is not None]
    if len(vals) < 4:
        return False, {"reason": f"单季同比数据不足({len(vals)}/4)"}
    recent = vals[-4:]
    first = sum(v for _, v in recent[:2]) / 2
    second = sum(v for _, v in recent[2:]) / 2
    return second > first, {"window": f"{recent[0][0]}~{recent[-1][0]}",
                            "first_avg": round(first, 1), "second_avg": round(second, 1)}


def downshift(series, pp=30.0):
    """增速降档检测:最新有值季较上一有值季 yoy 降幅 ≥pp → flag。
    返回 {shift, consecutive, flag}。consecutive=从最新往回连续降幅≥pp 的季数。
    出处:静水"退出信号:行业/标的增速跟不上""直接用眼睛看增速还在不在"。"""
    vals = [(lbl, v) for lbl, v in sorted(series.items(), key=lambda kv: _label_sortkey(kv[0]))
            if v is not None]
    if len(vals) < 2:
        return {"shift": None, "consecutive": 0, "flag": False}
    shifts = [vals[i - 1][1] - vals[i][1] for i in range(len(vals) - 1, 0, -1)]  # 新→旧
    consecutive = 0
    for s in shifts:
        if s >= pp:
            consecutive += 1
        else:
            break
    return {"shift": round(shifts[0], 1), "consecutive": consecutive,
            "flag": shifts[0] >= pp}


def divergence(parent_yoy, deduct_yoy, gap_pp=30.0):
    """扣非/归母同比背离检测:共有的最新一季 |parent-deduct| ≥ gap_pp → flag。
    出处:静水财报分析框架§一.2(昭衍新药例:扣非+2334%里 7~7.8 亿来自实验猴生物资产)。"""
    common = sorted(set(parent_yoy) & set(deduct_yoy), key=_label_sortkey)
    gaps = {}
    for lbl in common:
        p, d = parent_yoy[lbl], deduct_yoy[lbl]
        if p is not None and d is not None:
            gaps[lbl] = round(abs(p - d), 1)
    if not gaps:
        return {"gaps": {}, "flag": False, "at": None}
    latest = common[-1] if common else None
    # 最新一季或近两季任一背离即 flag(背离常在确认季显形)
    recent = common[-2:] if len(common) >= 2 else common
    flag = any(gaps.get(l, 0) >= gap_pp for l in recent if gaps.get(l) is not None)
    return {"gaps": gaps, "flag": flag, "at": latest}


def analyze_quarterly(qs, line=PASS_LINE):
    """一次算全所有序列指标(嵌入 L1 候选用)。"""
    if not qs or not qs.get("latest_period"):
        return None
    latest = qs["latest_period"]
    pl = pass_lines(qs, line)
    streaks = {k: pass_streak(qs.get(k, {}), line)
               for k in ("rev_yoy", "parent_yoy", "deduct_yoy")}
    return {
        "pass_lines": pl,
        "resonance": resonance(qs, line),
        "streak_max": max(streaks.values()),
        "streaks": streaks,
        "rev_accel": yoy_accel(qs.get("rev_yoy", {}))[0],
        "downshift": downshift(qs.get("rev_yoy", {})),
        "divergence": divergence(qs.get("parent_yoy", {}), qs.get("deduct_yoy", {})),
        # 最新季三线原始值(扭亏识别用:利润线 None=基数≤0,同比无意义)
        "latest_vals": {k: qs.get(k, {}).get(latest)
                        for k in ("rev_yoy", "parent_yoy", "deduct_yoy")},
    }
