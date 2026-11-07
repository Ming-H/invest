#!/usr/bin/env python3
"""
L3 十倍潜力评估 · tenscreener

基于 L2 体检结果,算十倍潜力分 TPS:
  F2/F3/F4/F7 由 scorer 函数自动算(终点可达/市值衰减/成长性/估值)
  F1/F5/F6 占位 50(行业空间/催化/生意模式 → 交 Claude 读研报财报定性后回填)

输出 Top 名单 + Markdown 报告框架。
"""

import argparse
import json
import sys

from scorer import (
    compute_tps, tps_grade, f2_endpoint_reach,
    f3_market_cap, f4_growth, f7_valuation,
)


def _fmt_pct(v):
    return f"{v:+.0f}" if isinstance(v, (int, float)) else "-"


def evaluate(l2_results, sector_max_cap=None):
    """对 L2 结果逐只算 TPS,降序返回。sector_max_cap:行业最大市值(亿)。
    F4 从三线序列取最新/上季营收同比+连续过线(无序列退化为 L1 年度值);
    F7 PEG 接通:PE/增速(增速封顶100,林奇惯例防高增速算出荒谬低PEG)。"""
    out = []
    for r in l2_results:
        m = r.get("metrics", {})
        l1 = r.get("l1", {})
        q = l1.get("quarterly") or {}
        latest = q.get("latest_period")
        rev_latest = q.get("rev_yoy", {}).get(latest) if latest else None
        rev_prev = None
        if latest:
            prevs = [p for p in q.get("periods", []) if p != latest]
            if prevs:
                rev_prev = q.get("rev_yoy", {}).get(prevs[-1])
        pg_latest = q.get("parent_yoy", {}).get(latest) if latest else None
        streak = q.get("streak_max") or 0
        lines_n = (q.get("pass_lines") or {}).get("count")
        if rev_latest is None:                # 无三线序列 → 退化为 L1 年度值
            rev_latest = l1.get("rev_growth")
        if pg_latest is None:
            pg_latest = l1.get("profit_growth")

        pe = m.get("pe")
        # PEG 增速口径(2026-11-07 修正): 优先 L1 年度累计归母同比(最新报告期累计,
        # 更贴林奇 PEG 的年度增速语义), 单季同比仅兜底——Q4/低基数单季会把 PEG 算得
        # 过低、F7 虚高。增速封顶100(林奇惯例防高增速算出荒谬低PEG)。
        peg_growth = l1.get("profit_growth") or pg_latest
        peg = None
        if pe and peg_growth and peg_growth > 0 and pe > 0:
            peg = pe / min(peg_growth, 100)

        mv = m.get("circ_mv")
        mvy = mv / 1e8 if mv else None

        factors = {
            "F1": 50,   # 行业空间 → Claude 定性(读研报: TAM/渗透率/景气开端)
            "F2": f2_endpoint_reach(mvy, sector_max_cap_yi=sector_max_cap),
            "F3": f3_market_cap(mvy),
            "F4": f4_growth(rev_latest, rev_prev, pg_latest,
                            streak=streak, lines_passed=lines_n),
            "F5": 50,   # 催化 → Claude 定性(新品/供不求/出海/周期开端)
            "F6": 50,   # 生意模式 → Claude 定性(可扩张/护城河/定价权)
            "F7": f7_valuation(pe_ttm=pe, peg=peg),
        }
        tps, detail = compute_tps(factors)
        out.append({
            **r,
            "market_cap_yi": round(mvy, 1) if mvy else None,
            "tps": tps,
            "tps_detail": detail,
            "grade": tps_grade(tps),
            "factors_need_claude": ["F1 行业空间", "F5 催化", "F6 生意模式"],
        })
    out.sort(key=lambda x: x["tps"], reverse=True)
    return out


def render_report(top, sector_max_cap=None):
    """渲染 Markdown 报告。"""
    lines = ["# tenscreener · 十倍潜力评估报告\n"]
    lines.append(f"> 行业最大市值对标:{sector_max_cap or '未指定(用 A 股分档估)'} 亿\n")
    lines.append("| # | 股票 | TPS | 分级 | 市值亿 | F1空间 | F2终点 | F3市值 | F4成长 | F5催化 | F6模式 | F7估值 |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for i, r in enumerate(top, 1):
        d = r["tps_detail"]
        lines.append(
            f"| {i} | {r['name']}({r['code']}) | **{r['tps']}** | {r['grade']} | "
            f"{r.get('market_cap_yi')} | {d['F1']} | {d['F2']} | {d['F3']} | {d['F4']} | "
            f"{d['F5']} | {d['F6']} | {d['F7']} |"
        )
    lines.append("\n> ⚠️ F1(行业空间)/F5(催化)/F6(生意模式)为占位分 50,")
    lines.append("> 需 Claude 读研报/财报做定性评估后回填,TPS 才会最终定稿。")

    # 三线近四季同比表(看见>分析:单季掉线不剔除,共振形态一目了然)
    rows_q = [(r, (r.get("l1", {}).get("quarterly") or {})) for r in top]
    rows_q = [(r, q) for r, q in rows_q if q.get("periods")]
    if rows_q:
        periods = [p for p in rows_q[0][1]["periods"]][-4:]
        lines.append("\n## 三线近四季单季同比(%)· 共振形态\n")
        lines.append("| 股票 | 线 | " + " | ".join(periods) + " | 共振 | 连续过线 |")
        lines.append("|" + "---|" * (len(periods) + 3))
        for r, q in rows_q:
            for label, key in (("营收", "rev_yoy"), ("归母", "parent_yoy"), ("扣非", "deduct_yoy")):
                vals = " | ".join(_fmt_pct(q.get(key, {}).get(p)) for p in periods)
                lines.append(f"| {r['name']} | {label} | {vals} | "
                             f"{q.get('resonance', '-')} | {q.get('streak_max', '-')} |")

    lines.append("\n## 待 Claude 定性补全的因子(每只 Top)")
    for r in top:
        lines.append(f"\n### {r['name']}({r['code']})")
        lines.append(f"- **F1 行业空间**:所处行业 TAM/渗透率/增速?是否景气开端?")
        lines.append(f"- **F5 催化**:有何新品/新市场/出海/政策催化?是否在周期开端(非已被炒过)?")
        lines.append(f"- **F6 生意模式**:模式可复制扩张吗?有无护城河/定价权(高毛利)?")
        lines.append(f"- **L2 缺口**:{', '.join(r.get('gaps', [])) or '无'}")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="tenscreener L3 十倍潜力评估")
    ap.add_argument("--l2", required=True, help="L2 体检 JSON")
    ap.add_argument("--sector-max-cap", type=float, help="行业最大市值(亿),用于 F2 对标")
    ap.add_argument("--top", type=int, default=5)
    ap.add_argument("--out", help="输出 JSON")
    ap.add_argument("--report", action="store_true", help="输出 Markdown 报告到 stdout")
    args = ap.parse_args()

    with open(args.l2, encoding="utf-8") as f:
        l2 = json.load(f)
    results = evaluate(l2, sector_max_cap=args.sector_max_cap)[:args.top]

    if args.report:
        print(render_report(results, sector_max_cap=args.sector_max_cap))
    elif args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        print(f"L3 评估 Top{len(results)} → {args.out}", file=sys.stderr)
    else:
        print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
