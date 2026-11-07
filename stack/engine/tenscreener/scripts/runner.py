#!/usr/bin/env python3
"""
runner · 右侧趋势中继识别 · tenscreener

在十倍股**上涨期间**识别它(用户核心需求)。与 L1 scout 互补:
  scout = 左侧错杀(业绩先行、股价未动,严格早期价格带)
  runner = 右侧中继(已创新高/主升/回调,静水趋势投资模式的落地)

方法论出处(全部静水原文,非自创条件):
  - 趋势投资(景气跟随)是静水本人主做模式:"看见>分析",承认市场资金合力比自己聪明
  - 核心α三特征:"创新高 + 持续超预期 + 板块效应"
  - "优质成长股越高越安全,恐高是90%的人赚不到大钱的根源" → 不设涨幅上限
  - "找到当下最景气行业的核心α,等它回调分仓分批介入(胜率+赔率最高的安全点)"
  - "常经历2次30-40%中期调整,单票可能腰斩,靠格局扛住"
  - 退出信号:"行业/标的增速跟不上"

五阶段(见 classify_phase):
  主升期 / 强势待确认 / 回调击球区 / 深调考验区 / 退潮
纯标准库。
"""

import sys
from datetime import date

from wencai import iwencai, _num, _pick, DEFAULT_FINANCE_CLI
from quarters import fetch_quarterly_yoy, analyze_quarterly, PASS_LINE
from monitor import market_gate, fetch_index_snapshot, DEFAULT_ZHISHU_CLI
import kline

# 阶段优先级(排序用):主升最优先,退潮/数据不足最后
PHASE_ORDER = ["主升期", "回调击球区", "强势待确认", "预告爆发",
               "深调考验区", "利润未兑现", "退潮", "数据不足"]
# 可导入 watchlist 的阶段(新进视角;"深调考验区"是持有纪律区、"退潮"不导入)
IMPORTABLE = ("主升期", "回调击球区", "强势待确认")

SYMBOL_PREFIX = {"SZ": "sz", "SH": "sh", "BJ": "bj"}


def to_symbol(code):
    """'300308.SZ' → 'sz300308'。"""
    c, _, suf = str(code).partition(".")
    if not suf:
        return ("sh" if str(code).startswith("6") else
                "bj" if str(code).startswith(("4", "8")) else "sz") + c
    return SYMBOL_PREFIX.get(suf.upper(), "sz") + c


# ─── Step1: 全市场条件粗筛(问财 1 次) ────────────────────────

def scan_step1(min_gain_1y=60, pass_line=40.0, max_mv=2000,
               cli_path=DEFAULT_FINANCE_CLI, limit=200):
    """条件(静水/CAN SLIM 字面):近1年涨幅>min_gain(动量,已走出来)
    + 营收同比&归母同比>pass_line(40=静水线) + 市值<max_mv亿 + 非ST上市满两年。
    申万行业 一并返回(板块效应计数用)。实测:条件值不随条件返回 →
    动量精确值由 Step2 腾讯K线离线算,不问问财要。"""
    q = (f"近一年涨跌幅大于{min_gain_1y}% "
         f"营业收入同比增长率大于{pass_line:.0f}% "
         f"归母净利润同比增长率大于{pass_line:.0f}% "
         f"总市值小于{max_mv}亿 非ST 上市满两年 "
         f"申万行业 总市值 近一年涨跌幅")
    datas = iwencai(q, limit=limit, cli_path=cli_path)
    rows = []
    for x in datas:
        code, name = x.get("股票代码"), x.get("股票简称")
        if not code or not name:
            continue
        ind = x.get("申万行业") or x.get("所属申万行业") or []
        if isinstance(ind, str):
            ind = [ind]
        rows.append({
            "code": code, "name": name,
            "rev_growth": _pick(x, "营业收入同比增长率"),
            "profit_growth": _pick(x, "归母净利润同比增长率"),
            "mv_yi": (_pick(x, "总市值") or 0) / 1e8 or None,
            "industry_l1": ind[0] if ind else None,      # 申万一级(板块效应)
        })
    return rows


def industry_resonance(rows):
    """板块效应(静水核心α第三特征):同一级行业在结果集内的数量。"""
    from collections import Counter
    c = Counter(r["industry_l1"] for r in rows if r.get("industry_l1"))
    return {r["code"]: c.get(r.get("industry_l1"), 0) for r in rows}, c


# ─── 阶段分类(纯函数,可离线单测) ────────────────────────────

def classify_phase(m, ana, gate, pos_th=0.90, dd_lo=0.15, dd_hi=0.40,
                   line=PASS_LINE, forecast=None):
    """m=series_metrics(kline), ana=analyze_quarterly, gate=verdict str,
    forecast=emoney.forecast_signal 结果(可选,预告中枢倒推单季)。
    基本面:数据不足 → 退潮/利润未兑现;预告过线时:
      报告期已有≥1线过 → 松发式"营收实绩+利润预告"凑两线(静水预告倒推法);
      报告期0线过 → "预告爆发"(跟踪级:利润爆发但营收未验证,5不买#2字面不买,观察)。
    在线(≥2线)再看价格:创新高→主升/待确认;回撤15-40%→击球区;>40%→深调考验。"""
    pos = (m or {}).get("pos_1y")
    dd = (m or {}).get("drawdown")
    if ana is None:
        return "数据不足", "三线同比序列缺失,无法判定"
    pl = ana.get("pass_lines", {})
    n_pass = pl.get("count", 0)
    streak = ana.get("streak_max", 0)
    g = gate or "unknown"
    fc = forecast or ana.get("forecast")
    fc_pass = bool(fc and fc.get("pass"))

    if n_pass <= 1 and not fc_pass:
        # 扭亏识别:利润线同比无意义(基数≤0)但营收线在 → 不是"退潮",是"利润未兑现"
        # (寒武纪/赛力斯/江特电机型:静水5不买#1"亏损的公司不买"按设计等利润转正)
        lv = ana.get("latest_vals", {})
        if lv.get("parent_yoy") is None and lv.get("deduct_yoy") is None \
                and lv.get("rev_yoy") is not None:
            rv = lv.get("rev_yoy")
            return "利润未兑现", (f"营收同比{rv:+.0f}%但利润扭亏中(同比无意义) —— "
                               "静水『亏损不买』,等利润转正再判定")
        return "退潮", f"最新季仅{n_pass}/3线过线(增速跟不上,退出信号)"

    fc_note = ""
    if fc_pass:
        if n_pass == 0:
            yoy = fc.get("yoy")
            return "预告爆发", (f"预告中枢倒推单季{fc.get('single_yi')}亿"
                             f"(同比{yoy if yoy is None else f'{yoy:+.0f}%'}"
                             f"/扭亏环比{fc.get('qoq')})@{fc['period']} —— "
                             "利润已爆发但营收未验证(5不买#2字面不买),重点观察等财报")
        n_pass += 1
        fc_note = f"+预告中枢(单季{fc.get('single_yi')}亿@{fc['period']})"

    if pos is not None and pos >= pos_th:
        if streak >= 2 and g == "on":
            return "主升期", (f"pos_1y={pos:.2f}≥{pos_th}(创新高)+连续{streak}季过线"
                              f"+大盘on —— 核心α三特征齐备{fc_note}")
        why = "大盘" + g + "(α孤军,等β)" if g != "on" else f"streak={streak}<2(等连续验证)"
        return "强势待确认", f"pos_1y={pos:.2f}创新高+{n_pass}线过(含预告),但{why}{fc_note}"
    if dd is not None and dd_lo <= dd <= dd_hi:
        beta = "" if g != "off" else "(大盘off,介入暂缓,先等β)"
        return "回调击球区", (f"距峰回撤{dd*100:.0f}%∈[{dd_lo*100:.0f},{dd_hi*100:.0f}%]"
                            f"+{n_pass}线仍过(含预告) —— 静水『等回调分仓介入』安全点{beta}{fc_note}")
    if dd is not None and dd > dd_hi:
        return "深调考验区", (f"距峰回撤{dd*100:.0f}%>{dd_hi*100:.0f}%但{n_pass}线仍过(含预告)"
                            " —— 『扛住』持有纪律区,非新进推荐")
    return "强势待确认", f"pos_1y={pos if pos is None else round(pos, 2)},形态未明{fc_note}"


# ─── scan 主流程 ────────────────────────────────────────────

def scan(min_gain_1y=60, pass_line=40.0, max_mv=2000, top_kline=30, top=15,
         cli_path=DEFAULT_FINANCE_CLI, zhishu_cli=DEFAULT_ZHISHU_CLI,
         beg=None, verbose=True, use_forecast=True, source="auto"):
    """四步:粗筛 → 腾讯K线动量(免额度) → 三线序列 → 阶段分类(含业绩预告信号)。
    source: wencai(问财,含市值/行业) | emoney(东财免额度,与回放同源同口径,无市值/行业)
            | auto(默认:问财无返回时自动降级东财)。
    返回 (cands, meta);cands 已按(阶段,行业共振,streak,归母同比)排序。"""
    beg = beg or f"{date.today().year - 1 if date.today().month > 6 else date.today().year - 2}-01-01"
    use_em = (source == "emoney")
    if not use_em:
        rows = scan_step1(min_gain_1y, pass_line, max_mv, cli_path)
        if verbose:
            print(f"► runner Step1 粗筛[问财]:近1年>{min_gain_1y}% & 双线同比>{pass_line:.0f}% "
                  f"& 市值<{max_mv}亿 → {len(rows)} 只", file=sys.stderr)
        if not rows and source == "auto":
            print("  问财无返回(额度?),自动降级东财免额度源", file=sys.stderr)
            use_em = True
    if use_em:
        import emoney
        uni = emoney.growth_universe(pass_line, pages=4)
        rows = [{"code": u["code"], "name": u["name"], "rev_growth": u["rev_growth"],
                 "profit_growth": u["profit_growth"], "mv_yi": None,
                 "industry_l1": None} for u in uni]
        if verbose:
            print(f"► runner Step1 粗筛[东财]:双线同比>{pass_line:.0f}% → {len(rows)} 只"
                  f"(动量/市值过滤在 Step2 按 K线做;无行业共振)", file=sys.stderr)
    if not rows:
        return [], {"step1_count": 0, "source": source}
    res_map, ind_count = industry_resonance(rows)

    # Step2: 动量TopN 拉K线(免额度);K线自算 g1y 做动量闸——问财"近一年涨跌幅大于X%"
    # 条件值不随条件返回(实测), 故 2026-11-07 起两种模式统一用 K 线兜底验证;
    # 东财模式另按 g1y 重排(其粗筛无价格条件)。
    # 并发拉取(2026-11-07): 东财模式 240+ 只串行约 2-4 分钟 → 8 线程压到 ~30 秒
    # (腾讯接口可扛; kline 缓存按 symbol 分文件, 线程安全)。
    # (截断放大且下限240:2026中报季增速分布极端,+242%只排223/372,小截断会丢主线票)
    cap = max(top_kline * 4, 240) if use_em else top_kline
    picked = sorted(rows, key=lambda r: r.get("profit_growth") or 0,
                    reverse=True)[:cap]
    end = date.today().isoformat()

    def _kline_one(r):
        try:
            kl = kline.fetch_qfq(to_symbol(r["code"]), beg, end)
        except Exception as e:
            r["_kline_err"] = str(e)[:60]
            return None
        m = kline.series_metrics(kl)
        if len(kl) > 250:                           # K线自算近1年涨幅做动量闸(统一兜底)
            g1y = (float(kl[-1][2]) / float(kl[-250][2]) - 1) * 100
            r["g1y"] = round(g1y, 1)
            if g1y < min_gain_1y:
                return None
        r["m"] = m
        return r

    from concurrent.futures import ThreadPoolExecutor
    survivors = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        for r, res in zip(picked, ex.map(_kline_one, picked)):
            if res is not None:
                survivors.append(res)
                continue
            err = r.pop("_kline_err", None)
            if verbose and err:
                print(f"  [K线跳过] {r['name']}: {err}", file=sys.stderr)
            elif verbose and r.get("g1y") is not None:
                print(f"  [动量未过] {r['name']} 近1年{r['g1y']}%<{min_gain_1y}%", file=sys.stderr)
    if use_em:
        survivors.sort(key=lambda r: -(r.get("g1y") or 0))
    picked = survivors[:top_kline]

    # Step3: 三线序列 + 业绩预告(东财免额度,静水松发式"营收实绩+利润预告")
    import emoney
    qs_map = {}
    if not use_em:
        names = [r["name"] for r in picked if r.get("m")]
        qs_map = fetch_quarterly_yoy(names, cli_path=cli_path) if names else {}
    for r in picked:
        if use_em:
            try:
                periods = emoney.f10_periods(r["code"])
                yoy = emoney.yoy_from_periods(periods)
                window = sorted(yoy)[-4:]
                qs = {"periods": window, "latest_period": window[-1] if window else None,
                      "rev_yoy": {q: yoy[q][0] for q in window},
                      "parent_yoy": {q: yoy[q][1] for q in window},
                      "deduct_yoy": {q: yoy[q][2] for q in window}} if window else None
                r["quarterly"] = qs
                r["ana"] = analyze_quarterly(qs) if qs else None
                if use_forecast and r["ana"]:
                    preds = emoney.predicts(r["code"])
                    fc = emoney.forecast_signal(periods, preds, None, pass_line)
                    if fc and fc.get("period") != (qs or {}).get("latest_period"):
                        r["ana"]["forecast"] = fc
                continue
            except Exception as e:
                if verbose:
                    print(f"  [东财三线跳过] {r['name']}: {e}", file=sys.stderr)
                r["quarterly"], r["ana"] = None, None
                continue
        qs = qs_map.get(r["name"])
        r["quarterly"] = qs or None
        r["ana"] = analyze_quarterly(qs) if qs else None
        if use_forecast and r["ana"]:
            try:
                periods = emoney.f10_periods(r["code"])
                preds = emoney.predicts(r["code"])
                fc = emoney.forecast_signal(periods, preds, None, pass_line)
                if fc and fc.get("period") != qs.get("latest_period"):
                    r["ana"]["forecast"] = fc
            except Exception as e:
                if verbose:
                    print(f"  [预告跳过] {r['name']}: {e}", file=sys.stderr)

    # Step4: 大盘 gate + 阶段
    gate = market_gate(fetch_index_snapshot(cli_path=zhishu_cli))
    for r in picked:
        phase, reason = classify_phase(r.get("m"), r.get("ana"), gate.get("verdict"))
        r["phase"], r["phase_reason"] = phase, reason
        r["industry_n"] = res_map.get(r["code"], 0)
        rm = (r.get("m") or {}).get("run_multiple")
        r["run_multiple"] = round(rm, 2) if rm else None
        r["x10_left"] = round(10 / rm, 1) if rm else None       # 距10x剩余倍数(按52周低点起算)
    cands = [r for r in picked if r.get("m")]
    cands.sort(key=lambda r: (PHASE_ORDER.index(r["phase"]) if r["phase"] in PHASE_ORDER else 99,
                              -r.get("industry_n", 0),
                              -(r.get("ana") or {}).get("streak_max", 0),
                              -(r.get("profit_growth") or 0)))
    meta = {"step1_count": len(rows), "gate": gate, "source": "emoney" if use_em else "wencai",
            "industry_counter": {k: v for k, v in
                                 sorted(ind_count.items(), key=lambda kv: -kv[1])[:8]}}
    return cands[:top], meta


# ─── 回测:单票阶段时间线(验收"上涨期间识别") ─────────────────

def backtest(code, name, kline_rows, yoy_table, gates, beg, end,
             pos_th=0.90, dd_lo=0.15, dd_hi=0.40, line=PASS_LINE,
             periods=None, predicts=None):
    """逐月回放阶段。yoy_table: {qlabel: (rev, parent, deduct)}(已验证同比);
    gates: kline.index_gate_series 输出;periods: f10_periods(NOTICE_DATE 精确披露对齐);
    predicts: emoney.predicts(业绩预告中枢倒推,静水松发式)。
    披露滞后对齐:优先用 NOTICE_DATE(某股年报2月出,保守日历会晚看一季);
    无 periods 时回退保守日历(1-4月看上年Q3)。防未来数据泄露。"""
    from quarters import _label_sortkey
    import emoney
    b, e = beg, end
    months = sorted({r[0][:7] for r in kline_rows if b <= r[0] <= e})
    qlabels = sorted(yoy_table, key=_label_sortkey)

    def _calendar(mth):
        y, m = int(mth[:4]), int(mth[5:7])
        if 1 <= m <= 4:
            return f"{y-1}Q3"
        return f"{y}Q1" if m <= 8 else f"{y}Q2" if m <= 10 else f"{y}Q3"

    timeline = []
    for mth in months:
        asof = next((r[0] for r in reversed(kline_rows) if r[0][:7] == mth), None)
        if not asof:
            continue
        m = kline.metrics_at(kline_rows, asof)
        if not m:
            continue
        # 披露对齐:NOTICE_DATE ≤ 月末的最新已披露报告期(在 yoy 表内),回退保守日历
        if periods:
            known = [lbl for lbl, v in periods.items()
                     if lbl in yoy_table and v.get("notice") and v["notice"] <= asof]
            q_lbl = max(known, key=_label_sortkey) if known else _calendar(mth)
        else:
            q_lbl = _calendar(mth)
        qs = _qs_from_table(yoy_table, q_lbl, qlabels)
        ana = analyze_quarterly(qs) if qs else None
        fc = emoney.forecast_signal(periods or {}, predicts or [], asof, line) \
            if (periods and predicts) else None
        phase, reason = classify_phase(m, ana, kline.gate_at(gates, asof),
                                       pos_th, dd_lo, dd_hi, line, forecast=fc)
        timeline.append({"month": mth, "asof": asof, "phase": phase,
                         "reason": reason, "pos_1y": round(m["pos_1y"], 2),
                         "run_multiple": round(m["run_multiple"], 2),
                         "drawdown": round(m["drawdown"], 2),
                         "disclosed": q_lbl,
                         **({"forecast": fc} if fc else {})})
    return timeline


def _qs_from_table(yoy_table, latest, qlabels):
    """从已验证同比表构造 quarters 结构(截至 latest 的近四季)。"""
    idx = qlabels.index(latest) if latest in qlabels else -1
    if idx < 0:
        return None
    window = qlabels[max(0, idx - 3):idx + 1]
    qs = {"periods": window, "latest_period": latest,
          "rev_yoy": {}, "parent_yoy": {}, "deduct_yoy": {}}
    for p in window:
        r, pa, d = yoy_table[p]
        qs["rev_yoy"][p] = r
        qs["parent_yoy"][p] = pa
        qs["deduct_yoy"][p] = d
    return qs


def render_report(cands, meta):
    lines = [f"# tenscreener · runner 趋势中继报告 ({date.today().isoformat()})\n"]
    g = meta.get("gate", {})
    lines.append(f"> 大盘β:**{g.get('verdict')}**(method={g.get('method')}) | "
                 f"Step1 粗筛 {meta.get('step1_count')} 只\n")
    ic = meta.get("industry_counter") or {}
    if ic:
        lines.append("> 板块效应(申万一级·结果集内计数):"
                     + " ".join(f"{k}×{v}" for k, v in list(ic.items())[:6]) + "\n")
    lines.append("| 股票 | 阶段 | 行业(共振) | pos_1y | 回撤 | 已涨倍数 | 距10x | streak | 三线最新季 |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for r in cands:
        ana = r.get("ana") or {}
        pl = ana.get("pass_lines", {})
        latest = (r.get("quarterly") or {}).get("latest_period")

        def _f(v):
            return f"{v:+.0f}" if isinstance(v, (int, float)) else "—"   # None=扭亏/缺基数

        tri = "-" if not latest else "/".join(
            _f((r["quarterly"].get(k) or {}).get(latest))
            for k in ("rev_yoy", "parent_yoy", "deduct_yoy")) + f" @{latest}"
        dd = (r.get("m") or {}).get("drawdown")
        lines.append(
            f"| {r['name']}({r['code']}) | **{r['phase']}** | "
            f"{r.get('industry_l1') or '-'}(×{r.get('industry_n', 0)}) | "
            f"{(r.get('m') or {}).get('pos_1y', 0):.2f} | "
            f"{'-' if dd is None else f'{dd*100:.0f}%'} | "
            f"{r.get('run_multiple') or '-'}x | {r.get('x10_left') or '-'}x | "
            f"{ana.get('streak_max', '-')} | {tri} |")
    lines.append("\n> 阶段说明:主升期=创新高+连续过线+大盘on;回调击球区=回撤15-40%且三线≥2线过"
                 "(静水『等回调分仓介入』);深调考验区=回撤>40%但基本面在线(『扛住』纪律区,非新进);"
                 "退潮=增速跟不上。已涨倍数=现价/52周低点。")
    lines.append("\n> 纪律:不恐高、不设涨幅上限(静水『越高越安全』);报告非投资建议。")
    return "\n".join(lines)


def render_backtest(name, code, timeline):
    lines = [f"# runner backtest · {name}({code}) 阶段时间线\n"]
    lines.append("| 月份 | 阶段 | pos_1y | 回撤 | 已涨倍数 | 当时已披露 | 理由 |")
    lines.append("|---|---|---|---|---|---|---|")
    for t in timeline:
        lines.append(f"| {t['month']} | **{t['phase']}** | {t['pos_1y']} | "
                     f"{t['drawdown']*100:.0f}% | {t['run_multiple']}x | "
                     f"{t['disclosed']} | {t['reason'][:44]} |")
    return "\n".join(lines)
