#!/usr/bin/env python3
"""
L2 深度体检 · tenscreener

对 L1 候选池逐只做 CAN SLIM 七要素 + 静水清单的【可量化部分】打分,
并标注【定性缺口】(催化剂/供求/机构明细 → 交 Claude 读财报研报补)。

全部报告期动态计算(修复历史"硬编码 2025 年"随时间失效的 bug):
  季度字段 ← 最新已披露报告期(latest_report_period)
  年度字段 ← 最新完整年报(latest_annual_period)
数据源:同花顺问财(经 wencai.py 缓存/计数)。
"""

import argparse
import json
import sys

from wencai import iwencai, _num, _pick, DEFAULT_FINANCE_CLI, DEFAULT_ZHISHU_CLI
from l1_screener import _get_by_period, _parse_range_gains
from quarters import (latest_report_period, latest_annual_period,
                      q_cn, q_enddate, divergence, PASS_LINE)
from monitor import market_gate, fetch_index_snapshot


def fetch_metrics(names, cli_path=DEFAULT_FINANCE_CLI, batch=6,
                  zhishu_cli=DEFAULT_ZHISHU_CLI, today=None):
    """对候选批量取 L2 深度指标(分批,问财一次处理 ~6 只较稳)。
    季度字段用最新季、年度字段用最新年报;chg_1y 不再单独请求
    (优先复用 L1 的 price_history.gain_1y)。today 可注入(测试冻结日期)。"""
    today_q = latest_report_period(today)
    annual = latest_annual_period(today)
    qd, ad = q_enddate(*today_q), q_enddate(*annual)
    result = {}
    for i in range(0, len(names), batch):
        b = names[i:i + batch]
        query = (
            " ".join(b) +
            f" {q_cn(*today_q)}单季归母净利润 单季扣非净利润"
            f" 归母净利润同比增长率 加权净资产收益率"
            f" {q_cn(*annual)}经营活动产生的现金流量净额 {q_cn(*annual)}销售毛利率"
            f" {q_cn(*annual)}销售净利率 {q_cn(*annual)}基金持股占流通股比例"
            " 最新市盈率ttm 市净率 流通市值 换手率 量比 近一年最高价前复权 最新价"
        )
        datas = iwencai(query, limit=len(b) + 4, cli_path=cli_path)
        for x in datas:
            nm = x.get("股票简称")
            if not nm:
                continue
            result[nm] = {
                # 季度字段(最新报告期;缺则容错模糊)
                "parent_profit": _pick(x, f"单季归母净利润[{qd}]", "单季归母净利润",
                                       f"归母净利润[{qd}]", "归母净利润"),
                "deduct_profit": _pick(x, f"单季扣非净利润[{qd}]", "单季扣非净利润",
                                       f"扣非归母净利润[{qd}]", "扣非归母净利润"),
                "roe": _pick(x, f"加权净资产收益率[{qd}]", "加权净资产收益率",
                             f"净资产收益率[{qd}]", "净资产收益率"),
                # 年度字段(最新完整年报)
                "op_cf": _pick(x, f"经营活动产生的现金流量净额[{ad}]",
                               "经营活动产生的现金流量净额"),
                "gross_margin": _pick(x, f"销售毛利率[{ad}]", "销售毛利率"),
                "net_margin": _pick(x, f"销售净利率[{ad}]", "销售净利率"),
                "fund_hold": _pick(x, f"基金持股占流通股比例[{ad}]",
                                   "基金持股占流通股比例"),
                # 行情/估值(时点值)
                "turnover": _pick(x, "换手率"),
                "volume_ratio": _pick(x, "量比"),
                "pe": _pick(x, "最新市盈率ttm", "市盈率"),
                "pb": _pick(x, "最新市净率", "市净率"),
                "circ_mv": _pick(x, "流通市值", "最新a股流通市值"),
                # 距一年新高(静水5不买#3的工程化:查近一年最高价自算,避开"是否创新高"返回0条)
                "high_1y": _pick(x, "近一年最高价前复权", "近一年最高价"),
                "last": _pick(x, "最新价"),
                "report_period_used": qd,
                "annual_period_used": ad,
            }
            p = result[nm]
            p["pos_1y"] = (p["last"] / p["high_1y"]) if (
                p.get("last") and p.get("high_1y") and p["high_1y"] > 0) else None
    # chg_1y 不花查询:调用侧注入(analyze 里优先取 L1 price_history,fallback 解析)
    return result


def _stock_gain_1y(m, l1c):
    """个股近一年涨幅:复用 L1 已取的 price_history.gain_1y(零额外查询)。"""
    return (l1c.get("price_history") or {}).get("gain_1yr")


def score_canslim(m, l1c, gate=None):
    """CAN SLIM 可量化部分打分(每要素 0-10)。返回 (scores, gaps)。
    gate: monitor.market_gate() 的结果(M 要素与 L 相对强度共用,一次查询)。"""
    scores = {}
    gaps = []

    # C 当季收益:优先三线序列最新季(最贴 CAN SLIM 字面"当季"),fallback L1 年度值
    q = l1c.get("quarterly") or {}
    latest = q.get("latest_period")
    pg = q.get("parent_yoy", {}).get(latest) if latest else None
    if pg is None:
        pg = l1c.get("profit_growth")
    scores["C"] = min(10, (pg or 0) / 15) if pg else 0
    if pg is None:
        gaps.append("C 当季净利同比")

    # A 年度收益:ROE + 毛利率
    roe, gm = m.get("roe"), m.get("gross_margin")
    a = 0.0
    if roe:
        a += min(5, roe / 5)        # ROE≥25 满分 5
    if gm:
        a += min(5, gm / 18)        # 毛利≥90 满分 5
    scores["A"] = a
    if roe is None or gm is None:
        gaps.append("A 年度盈利质量(ROE/毛利)")

    # N 新高代理:距一年新高比例(CAN SLIM N"新高";创新高=10,95%→9.5)
    pos = m.get("pos_1y")
    scores["N"] = max(0.0, min(10, pos * 10)) if pos is not None else 0
    if pos is None:
        gaps.append("N 距一年新高(最新价/近一年最高价)")
    gaps.append("N 催化剂(定性,需研报核实)")

    # S 供需:流通市值分档
    mv = m.get("circ_mv")
    if mv:
        mvy = mv / 1e8
        if 50 <= mvy <= 500:
            scores["S"] = 8
        elif mvy < 2000:
            scores["S"] = 6
        else:
            scores["S"] = 4
    else:
        scores["S"] = 0
        gaps.append("S 流通市值")

    # L 领涨强度:个股近一年涨幅 - 上证近一年涨幅(相对强度;CAN SLIM"RS Rating,无则用涨幅替代")
    # 修复历史 bug:此前 L 与 N 完全重复(都用 chg_1y/15)
    stock_g = _stock_gain_1y(m, l1c)
    idx_g = (gate or {}).get("index_gain_1y")
    if stock_g is not None and idx_g is not None:
        scores["L"] = max(0.0, min(10, 5 + (stock_g - idx_g) / 4))  # 跑赢20pp→10,跑输20pp→0
    elif stock_g is not None:       # 指数缺失:只看绝对涨幅(弱化档)
        scores["L"] = min(10, max(0, stock_g / 15))
        gaps.append("L 相对强度(指数涨幅缺失,退化为绝对涨幅)")
    else:
        scores["L"] = 0
        gaps.append("L 领涨强度(个股近一年涨幅缺失)")

    # I 机构持仓
    fh = m.get("fund_hold")
    scores["I"] = min(10, (fh or 0) * 0.4) if fh is not None else 0
    gaps.append("I 外资/基金增减持(需东财)")

    # M 大盘方向:market_gate 三态(量化近似,Claude 可覆盖)
    v = (gate or {}).get("verdict")
    scores["M"] = {"on": 8, "weak": 5, "off": 2}.get(v, 5)
    if v is None:
        gaps.append("M 大盘方向(快照缺失)")
    else:
        gaps.append(f"M 大盘={v}({(gate or {}).get('method')},量化近似,Claude可覆盖)")

    return scores, gaps


def score_jingshui(m, l1c):
    """静水可量化清单(每项 0-2)。返回 (scores, gaps)。"""
    s = {}
    gaps = []
    q = l1c.get("quarterly") or {}
    latest = q.get("latest_period")

    s["不亏损"] = 2 if (m.get("deduct_profit") or 0) > 0 else 0

    # 主营增速≥40%:优先三线序列最新季,fallback L1 年度值(修复历史 key 错名断链)
    rv = q.get("rev_yoy", {}).get(latest) if latest else None
    if rv is None:
        rv = l1c.get("rev_growth")
    s["主营增速≥40%"] = 2 if (rv or 0) >= 40 else (1 if (rv or 0) > 0 else 0)

    # 创一年新高/强势:pos_1y(修复历史"chg_1y>0"过弱代理)
    pos = m.get("pos_1y")
    s["创一年新高/强势"] = 2 if (pos is not None and pos >= 0.95) else \
                          (1 if (pos is not None and pos >= 0.8) else 0)

    # 超级现金流:经营现金流/归母净利(静水"5超级"之超级现金流;药明康德范例"现金流与利润匹配")
    opcf, pp = m.get("op_cf"), m.get("parent_profit")
    if opcf is not None and pp and pp > 0:
        ratio = opcf / pp
        s["超级现金流"] = 2 if ratio >= 1 else (1 if ratio >= 0.7 else 0)
    else:
        s["超级现金流"] = None
        gaps.append("超级现金流(现金流或归母净利缺失)")

    # 扣非归母一致性(静水财报分析框架§一.2:背离时必须拆非经营性贡献,昭衍新药例)
    dv = q.get("divergence")
    if dv is not None:
        if dv.get("flag"):
            s["扣非归母一致性"] = 0
            gaps.append(f"扣非/归母背离 gap={list(dv['gaps'].values())[-1] if dv['gaps'] else '?'}pp"
                        f"@{dv.get('at')},需Claude拆解非经营性贡献")
        else:
            s["扣非归母一致性"] = 2
    else:
        s["扣非归母一致性"] = None
        gaps.append("扣非归母一致性(三线序列缺失)")

    s["新品"] = None
    gaps.append("新品/新市场(定性)")
    s["供不应求"] = None
    gaps.append("供求信号(需财报:合同负债/产能/订单)")
    return s, gaps


def analyze(l1_candidates, cli_path=DEFAULT_FINANCE_CLI,
            zhishu_cli=DEFAULT_ZHISHU_CLI, gate=None, today=None):
    """对 L1 候选池逐只体检,按 CAN SLIM 综合分降序返回。
    gate:可注入的 market_gate 结果(测试/复用);默认现查一次(当日缓存)。"""
    names = [c["name"] for c in l1_candidates if c.get("name")]
    metrics = fetch_metrics(names, cli_path=cli_path, today=today)
    if gate is None:
        gate = market_gate(fetch_index_snapshot(cli_path=zhishu_cli))
    results = []
    for c in l1_candidates:
        nm = c.get("name")
        m = metrics.get(nm, {})
        cscores, cgaps = score_canslim(m, c, gate=gate)
        jscores, jgaps = score_jingshui(m, c)
        cvals = [v for v in cscores.values() if isinstance(v, (int, float))]
        canslim_total = round(sum(cvals) / len(cvals) * 10, 1) if cvals else 0
        results.append({
            "code": c.get("code"), "name": nm,
            "l1": c, "metrics": m,
            "canslim_scores": cscores, "canslim_total": canslim_total,
            "jingshui_scores": jscores,
            "market_gate": gate,
            "gaps": cgaps + jgaps,
        })
    results.sort(key=lambda x: x["canslim_total"], reverse=True)
    return results


def main():
    ap = argparse.ArgumentParser(description="tenscreener L2 深度体检")
    ap.add_argument("--candidates", required=True, help="L1 候选池 JSON 文件")
    ap.add_argument("--cli-path", default=DEFAULT_FINANCE_CLI)
    ap.add_argument("--zhishu-cli", default=DEFAULT_ZHISHU_CLI)
    ap.add_argument("--out", help="输出 JSON 文件")
    ap.add_argument("--top", type=int, default=10, help="保留前 N 只(0=全部)")
    args = ap.parse_args()

    with open(args.candidates, encoding="utf-8") as f:
        cands = json.load(f)
    results = analyze(cands, cli_path=args.cli_path, zhishu_cli=args.zhishu_cli)
    if args.top > 0:
        results = results[:args.top]

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        print(f"L2 体检 {len(results)} 只 → {args.out}", file=sys.stderr)
    else:
        print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
