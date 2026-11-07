#!/usr/bin/env python3
"""
东方财富免额度数据源 · tenscreener

问财额度耗尽日的备份 + 业绩预告/披露日的唯一来源(问财无此数据):
  f10_periods(code)     F10 主要指标(按报告期累计三线 + NOTICE_DATE 披露日,全历史)
  predicts(code)        业绩预告(RPT_PUBLIC_OP_NEWPREDICT):归母净利润区间/类型/披露日
  forecast_signal(...)  预告中枢倒推单季 → 同比/环比/扭亏判定(静水"预告类取中枢倒推单季")

出处:静水财报分析框架§一.1 —— "预告类:取中枢测算;倒推最新单季环比/同比,判断利润
释放节奏"(松发股份例:H1 预告+Q1 实绩 → 倒推 Q2 单季归母环比+129%、同比+538%)。
缓存 outputs/cache/emoney/YYYYMMDD/,set_cache_dir/set_offline 镜像 kline 模式。
纯标准库。
"""

import json
import os
import urllib.request
from datetime import date

SKILL_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EM_CACHE_ROOT = os.path.join(SKILL_ROOT, "outputs", "cache", "emoney")

_counter = {"api": 0, "cache_hit": 0}
_cache_dir = None
_offline = False

Q_BY_MONTH = {3: 1, 6: 2, 9: 3, 12: 4}
QCN = {1: "一季报", 2: "中报", 3: "三季报", 4: "年报"}


class FixtureMissing(Exception):
    pass


def set_cache_dir(path):
    global _cache_dir
    _cache_dir = path


def set_offline(flag=True):
    global _offline
    _offline = flag


def counter_report():
    return f"[东财] API 调用 {_counter['api']} 次 / 缓存命中 {_counter['cache_hit']} 次"


def secucode(code):
    code = str(code).split(".")[0]
    return f"{code}.SH" if code.startswith(("6", "9")) else f"{code}.SZ"


def _get(report_name, code, page_size=100):
    cf = os.path.join(_cache_dir or os.path.join(EM_CACHE_ROOT, date.today().strftime("%Y%m%d")),
                      f"{report_name}_{str(code).split('.')[0]}.json")
    os.makedirs(os.path.dirname(cf), exist_ok=True)
    if os.path.exists(cf):
        try:
            with open(cf, encoding="utf-8") as f:
                _counter["cache_hit"] += 1
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    if _offline:
        raise FixtureMissing(f"emoney fixture 未命中: {report_name} {code}")
    url = (f"https://datacenter-web.eastmoney.com/api/data/v1/get"
           f"?reportName={report_name}&columns=ALL&pageSize={page_size}"
           f"&filter=(SECUCODE%3D%22{secucode(code)}%22)")
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0", "Referer": "https://data.eastmoney.com/"})
    d = json.load(urllib.request.urlopen(req, timeout=30))
    rows = (d.get("result") or {}).get("data") or []
    with open(cf, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False)
    _counter["api"] += 1
    return rows


# ─── F10 主要指标(累计三线 + 披露日) ────────────────────────

def f10_periods(code):
    """→ {"periods": {qlabel: {"cum": (营收,归母,扣非 累计), "notice": "YYYY-MM-DD"}}}"""
    rows = _get("RPT_F10_FINANCE_MAINFINADATA", code)
    out = {}
    for r in rows:
        rd = (r.get("REPORT_DATE") or "")[:10]
        if not rd:
            continue
        y, m = int(rd[:4]), int(rd[5:7])
        q = Q_BY_MONTH.get(m)
        if not q:
            continue
        out[f"{y}Q{q}"] = {
            "cum": (r.get("TOTALOPERATEREVE"), r.get("PARENTNETPROFIT"),
                    r.get("KCFJCXSYJLR")),
            "notice": (r.get("NOTICE_DATE") or "")[:10] or None,
        }
    return out


def cum_singles(periods):
    """累计 → 单季。periods: f10_periods 的 dict → {qlabel: (rev,gm,kf) 单季}"""
    def prev_ql(lbl):
        y, q = int(lbl[:4]), int(lbl[-1])
        return f"{y-1}Q4" if q == 1 else f"{y}Q{q-1}"
    single = {}
    for lbl in sorted(periods):
        cum = periods[lbl]["cum"]
        p = prev_ql(lbl)
        if lbl.endswith("Q1") or p not in periods:
            single[lbl] = cum
        else:
            single[lbl] = tuple(
                (a - b) if (a is not None and b is not None) else None
                for a, b in zip(cum, periods[p]["cum"]))
    return single


def yoy_from_periods(periods):
    """→ {qlabel: (rev,gm,kf 单季同比%)}(基数≤0/缺数 → None;扭亏同比无意义)"""
    single = cum_singles(periods)

    def prev_y_ql(lbl):
        return f"{int(lbl[:4]) - 1}Q{lbl[-1]}"
    table = {}
    for lbl in sorted(single):
        base = single.get(prev_y_ql(lbl))
        row = []
        for v, b in zip(single[lbl], base or (None, None, None)):
            if v is None or b is None or b <= 0 or v <= 0:
                row.append(None)
            else:
                row.append(round((v / b - 1) * 100, 1))
        if any(x is not None for x in row):
            table[lbl] = tuple(row)
    return table


def three_line_qs(code):
    """F10 累计 → 单季同比 → quarters.analyze_quarterly 的 qs 结构。
    -> (qs, singles, periods)。全免费源, watch/track/judge/runner(东财) 共用入口
    (2026-11-07 从 judge.py 移入: 生命周期监测免问财化)。"""
    periods = f10_periods(code)
    yoy = yoy_from_periods(periods)
    singles = cum_singles(periods)
    qs = {"rev_yoy": {}, "parent_yoy": {}, "deduct_yoy": {}, "periods": sorted(yoy), "latest_period": None}
    for lbl, (r, p, d) in yoy.items():
        qs["rev_yoy"][lbl] = r
        qs["parent_yoy"][lbl] = p
        qs["deduct_yoy"][lbl] = d
    qs["latest_period"] = qs["periods"][-1] if qs["periods"] else None
    return qs, singles, periods


# ─── 业绩预告 ───────────────────────────────────────────────

def predicts(code):
    """→ [{period, notice, lo, hi, yoy_lo, yoy_hi, type, finance}] 只留归母净利润预告。"""
    rows = _get("RPT_PUBLIC_OP_NEWPREDICT", code)
    out = []
    for r in rows:
        rd = (r.get("REPORT_DATE") or "")[:10]
        if not rd or r.get("PREDICT_FINANCE_CODE") != "004":   # 004=归母净利润
            continue
        y, m = int(rd[:4]), int(rd[5:7])
        q = Q_BY_MONTH.get(m)
        if not q:
            continue
        out.append({
            "period": f"{y}Q{q}", "notice": (r.get("NOTICE_DATE") or "")[:10] or None,
            "lo": r.get("PREDICT_AMT_LOWER"), "hi": r.get("PREDICT_AMT_UPPER"),
            "yoy_lo": r.get("ADD_AMP_LOWER"), "yoy_hi": r.get("ADD_AMP_UPPER"),
            "type": r.get("PREDICT_TYPE"),
        })
    return sorted(out, key=lambda p: p["notice"] or "")


def forecast_signal(periods, predicts, asof, line=40.0, qoq_th=50.0):
    """取 asof 前最新一条归母预告,若其报告期晚于已披露最新报告期 → 倒推单季中枢。
    判定(静水松发式"预告取中枢倒推单季"),三口径任一爆发即 pass:
      ① 累计同比:中枢 / 上年同期累计 - 1 ≥ line(基数>0)
      ② 预告自带:ADD_AMP 中值 ≥ line(东财预告给的累计同比区间)
      ③ 单季爆发:单季中枢>0 且(单季同比≥line 或 上季单季≤0 由亏转盈
         —— 由亏转盈时环比无界,不算失败;九安 2021Q3 单季亏损→Q4 预告 10 亿即此形态)
    返回 dict 或 None。"""
    if asof:
        preds = [p for p in predicts if p["notice"] and p["notice"] <= asof
                 and p["lo"] is not None and p["hi"] is not None]
    else:
        preds = [p for p in predicts if p["lo"] is not None and p["hi"] is not None]
    if not preds:
        return None
    pred = preds[-1]
    disclosed = {lbl: v for lbl, v in periods.items()
                 if not asof or (v.get("notice") and v["notice"] <= asof)}
    if not disclosed or pred["period"] in disclosed:
        return None                              # 预告期已披露实绩 → 不再算预告信号

    def prev_ql(lbl):
        y, q = int(lbl[:4]), int(lbl[-1])
        return f"{y-1}Q4" if q == 1 else f"{y}Q{q-1}"

    def cum_gm(lbl):
        return (periods.get(lbl, {}).get("cum") or (None, None, None))[1]

    mid_cum = (pred["lo"] + pred["hi"]) / 2
    P = pred["period"]
    prev_gm = cum_gm(prev_ql(P))                # 同年上一报告期累计
    py_gm = cum_gm(f"{int(P[:4]) - 1}Q{P[-1]}")  # 上年同期累计
    single_mid = mid_cum - prev_gm if prev_gm is not None else None
    res = {"period": P, "notice": pred["notice"], "type": pred["type"],
           "mid_cum": round(mid_cum / 1e8, 2), "single_yi": None,
           "cum_yoy": None, "yoy": None, "qoq": None,
           "turned": False, "basis": None, "pass": False}

    # ① 累计同比(中枢/上年同期累计)
    if py_gm is not None and py_gm > 0 and mid_cum > 0:
        res["cum_yoy"] = round((mid_cum / py_gm - 1) * 100, 1)
    # ② 预告自带同比区间中值
    pred_yoy = None
    if pred.get("yoy_lo") is not None and pred.get("yoy_hi") is not None:
        pred_yoy = (pred["yoy_lo"] + pred["yoy_hi"]) / 2

    if single_mid is not None and single_mid > 0:
        res["single_yi"] = round(single_mid / 1e8, 2)
        # 单季同比(上年同期单季为正时)
        py_single = None
        if py_gm is not None:
            py_prev_gm = cum_gm(prev_ql(f"{int(P[:4]) - 1}Q{P[-1]}"))
            if py_prev_gm is not None:
                py_single = py_gm - py_prev_gm
        if py_single is not None and py_single > 0:
            res["yoy"] = round((single_mid / py_single - 1) * 100, 1)
        # 上季单季(环比;由亏转盈时无界)
        prev_single = None
        if prev_gm is not None:
            pp_gm = cum_gm(prev_ql(prev_ql(P)))
            if pp_gm is not None:
                prev_single = prev_gm - pp_gm
                if prev_single > 0:
                    res["qoq"] = round((single_mid / prev_single - 1) * 100, 1)

    # 三口径判定
    if res["cum_yoy"] is not None and res["cum_yoy"] >= line:
        res["basis"] = f"累计同比{res['cum_yoy']:+.0f}%"
    elif pred_yoy is not None and pred_yoy >= line:
        res["basis"] = f"预告自带同比{pred_yoy:+.0f}%" \
                       if pred["type"] != "扭亏" else "预告扭亏(幅度大)"
    elif res["single_yi"] and res["yoy"] is not None and res["yoy"] >= line:
        res["basis"] = f"单季同比{res['yoy']:+.0f}%"
    elif res["single_yi"] and res["qoq"] is not None and res["qoq"] >= qoq_th:
        res["basis"] = f"单季环比{res['qoq']:+.0f}%"
    elif res["single_yi"]:
        # 单季为正且(上年同期或上季由亏转盈)→ 由亏转盈(环比无界)
        res["turned"] = True
        res["basis"] = "单季由亏转盈"
    res["pass"] = res["basis"] is not None
    return res


def _universe_report_dates(today=None):
    """全市场粗筛报告期候选(主选, 次选)——只选【已全部披露完】的报告期,
    防止按未披露完的 REPORTDATE 过滤时漏掉尚未披露的公司:
      1-4月→上年三季报 | 5-6月→当年一季报 | 7-8月→中报披露窗口, 主选中报次选一季报
      | 9-10月→当年中报 | 11-12月→当年三季报(2026-11-07 修复: 旧逻辑 month>=7 一律
      用中报, Q4 整整落后一季; 且"fallback 一季报"注释从未实现, candidates[1] 是死代码)。"""
    from datetime import date as _d
    today = today or _d.today()
    y, m = today.year, today.month
    if m >= 11:
        return [f"{y}-09-30", f"{y}-06-30"]
    if m >= 9:
        return [f"{y}-06-30", f"{y}-09-30"]     # 9-10月: 三季报在披露窗口, 主选已齐的中报
    if m >= 7:
        return [f"{y}-06-30", f"{y}-03-31"]     # 7-8月: 中报陆续披露, 主选中报(排序表只含已披露)
    if m >= 5:
        return [f"{y}-03-31", f"{y - 1}-12-31"]
    return [f"{y - 1}-09-30", f"{y - 1}-06-30"]


def growth_universe(pass_line=40.0, pages=4, report_date=None):
    """全市场成长股粗筛(免额度):RPT_LICO_FN_CPD 按营收同比(YSTZ)/归母同比(SJLTZ)
    各取前 pages×100,保留双线>pass_line 的并集。问财耗尽日的 runner Step1 替代。
    报告期:默认按披露日历自动选(见 _universe_report_dates);主选无数据时真正落一档。
    返回 [{code,name,rev_growth,profit_growth,report_date}]。"""
    from datetime import date as _d
    dates = [report_date] if report_date else _universe_report_dates()

    def _fetch(sort_col, page, rd):
        cf = os.path.join(_cache_dir or os.path.join(
            EM_CACHE_ROOT, _d.today().strftime("%Y%m%d")),
            f"univ_{sort_col}_p{page}_{rd}.json")
        os.makedirs(os.path.dirname(cf), exist_ok=True)
        if os.path.exists(cf):
            try:
                _counter["cache_hit"] += 1
                return json.load(open(cf, encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        url = ("https://datacenter-web.eastmoney.com/api/data/v1/get"
               f"?reportName=RPT_LICO_FN_CPD&columns=ALL&pageSize=100&pageNumber={page}"
               f"&sortColumns={sort_col}&sortTypes=-1"
               f"&filter=(REPORTDATE%3D%27{rd}%27)")
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0", "Referer": "https://data.eastmoney.com/"})
        d = json.load(urllib.request.urlopen(req, timeout=30))
        rows = (d.get("result") or {}).get("data") or []
        json.dump(rows, open(cf, "w", encoding="utf-8"), ensure_ascii=False)
        _counter["api"] += 1
        return rows

    for rd in dates:
        out = {}
        any_row = False
        for sort_col in ("YSTZ", "SJLTZ"):
            for page in range(1, pages + 1):
                rows = _fetch(sort_col, page, rd)
                any_row = any_row or bool(rows)
                for r in rows:
                    code = r.get("SECURITY_CODE")
                    rev, prof = r.get("YSTZ"), r.get("SJLTZ")
                    if not code or rev is None or prof is None:
                        continue
                    if rev > pass_line and prof > pass_line and code not in out:
                        out[code] = {"code": code, "name": r.get("SECURITY_NAME_ABBR"),
                                     "rev_growth": rev, "profit_growth": prof,
                                     "report_date": rd}
        if any_row:
            return list(out.values())
    return []
