#!/usr/bin/env python3
"""
tenscreener 统一入口 · 十倍股生命周期筛选器

三级漏斗 + 生命周期监测:
  L1 问财初筛(左侧错杀:业绩过线、股价未动) → L2 深度体检 → L3 十倍潜力评估(TPS)
  watch 右侧确认(披露后:持续超预期+创一年新高+大盘β → scouted→confirmed)
  track 持有退出(增速降档/扣非背离/回撤裁定 → holding→exit_signal 告警)

用法:
  # 全流程: L1→L2→L3 + 归档
  python3 cli.py screen --report --archive
  # 生命周期
  python3 cli.py watch import --from-l1 outputs/20260830/l1.json --top 5
  python3 cli.py watch --run
  python3 cli.py track promote 003010 --cost 25.6
  python3 cli.py track --run
  # 离线回放(测试)
  python3 cli.py watch --run --offline --cache-dir ../tests/fixtures/cache/2024-11-15
"""

import argparse
import json
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import wencai
import kline
from wencai import DEFAULT_FINANCE_CLI, DEFAULT_ZHISHU_CLI
from l1_screener import run as l1_run
from l2_analyzer import analyze as l2_analyze
from l3_evaluator import evaluate as l3_eval, render_report
from quarters import fetch_quarterly_yoy, disclosure_window, latest_report_period, q_label
from monitor import market_gate, fetch_index_snapshot, price_position, confirm_check, exit_check
import runner as runner_mod
import watchlist as wl

SKILL_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_ROOT = os.environ.get("TENSCREENER_OUT_ROOT") or os.path.join(SKILL_ROOT, "outputs")


def _output(data, out):
    if out:
        with open(out, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"{len(data)} 条 → {out}", file=sys.stderr)
    else:
        print(json.dumps(data, ensure_ascii=False, indent=2))


def _archive_dir():
    d = os.path.join(OUT_ROOT, date.today().strftime("%Y%m%d"))
    os.makedirs(d, exist_ok=True)
    return d


def _write_archive(parts, report_md=None):
    """parts: {文件名: json对象}; report_md: 字符串。写 outputs/YYYYMMDD/ + manifest。"""
    d = _archive_dir()
    for name, obj in parts.items():
        with open(os.path.join(d, name), "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
    if report_md:
        with open(os.path.join(d, "report.md"), "w", encoding="utf-8") as f:
            f.write(report_md)
    c = wencai.counter()
    manifest = {"date": date.today().isoformat(),
                "api_calls": c["api"], "cache_hits": c["cache_hit"],
                "queries": c["queries"]}
    with open(os.path.join(d, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    print(f"[归档] {d} (api={c['api']} cache={c['cache_hit']})", file=sys.stderr)


# ─── judge: 单标的十倍股完整判定(v3 判断权威, 2026-11-05) ──

def cmd_judge(args):
    """judge 三段式入口: 默认取数产素材包; --finalize 校验 verdict.json 并组装报告。
    取数全免费源(东财F10/资产负债表/股东/主营构成/emweb + 腾讯K线 + 巨潮PDF), 零问财额度。"""
    import judge as _j
    code6 = str(args.code).split(".")[0]
    outdir = os.path.join(OUT_ROOT, "judge", "%s_%s" % (code6, date.today().strftime("%Y%m%d")))
    if getattr(args, "publish", False):
        ok, url = _j.publish(args.code, folder=getattr(args, "folder", None))
        return
    if args.finalize:
        ok, path = _j.finalize(code6)
        if ok:
            print("报告已组装: %s" % path)
        return
    pack, d = _j.build_pack(args.code, outdir, with_pdf=not getattr(args, "no_pdf", False),
                            pdf_dir=getattr(args, "pdf_dir", None))
    print("素材包完成: %s/pack.md | 报告%d份 | 降级%d项"
          % (d, len(pack.get("report_texts_meta") or []), len(_j.DEGRADED)), file=sys.stderr)
    print("下一步: Claude 读 pack.md(结合 jingshui/can-slim/lynch 标准) → 写 %s/verdict.json → 再跑 --finalize" % d)


# ─── L1/L2/L3 ───────────────────────────────────────────────

def cmd_screen(args):
    cands = l1_run(roe_min=args.roe_min, cli_path=args.cli_path,
                   max_1yr_gain=args.max_1yr_gain, max_3yr_gain=args.max_3yr_gain,
                   min_6mo_gain=args.min_6mo_gain, max_position=args.max_position,
                   max_quarterly=args.max_quarterly)
    if not cands:
        print("L1 无候选(全市场无营收加速+高质量+强势的股票),结束。", file=sys.stderr)
        if args.archive:
            _write_archive({"l1.json": cands})
        return
    l2 = l2_analyze(cands, cli_path=args.cli_path,
                    zhishu_cli=args.zhishu_cli)[:args.top_l2]
    l3 = l3_eval(l2, sector_max_cap=args.sector_max_cap)[:args.top_l3]
    md = render_report(l3, sector_max_cap=args.sector_max_cap)
    if args.report:
        print(md)
    else:
        _output(l3, args.out)
    if args.archive:
        _write_archive({"l1.json": cands, "l2.json": l2, "l3.json": l3}, md)


def cmd_l1(args):
    cands = l1_run(roe_min=args.roe_min, cli_path=args.cli_path,
                   max_1yr_gain=args.max_1yr_gain, max_3yr_gain=args.max_3yr_gain,
                   min_6mo_gain=args.min_6mo_gain, max_position=args.max_position,
                   max_quarterly=args.max_quarterly)
    if args.top > 0:
        cands = cands[:args.top]
    if args.archive:
        _write_archive({"l1.json": cands})
    _output(cands, args.out)


def cmd_l2(args):
    with open(args.candidates, encoding="utf-8") as f:
        cands = json.load(f)
    l2 = l2_analyze(cands, cli_path=args.cli_path, zhishu_cli=args.zhishu_cli)
    if args.top > 0:
        l2 = l2[:args.top]
    if args.archive:
        _write_archive({"l2.json": l2})
    _output(l2, args.out)


def cmd_l3(args):
    with open(args.l2, encoding="utf-8") as f:
        l2 = json.load(f)
    l3 = l3_eval(l2, sector_max_cap=args.sector_max_cap)
    if args.top > 0:
        l3 = l3[:args.top]
    if args.report:
        print(render_report(l3, sector_max_cap=args.sector_max_cap))
    else:
        _output(l3, args.out)


# ─── runner(右侧趋势中继:上涨期间识别) ──────────────────────

YOY_FIXTURE_DIR = os.path.join(SKILL_ROOT, "tests", "fixtures", "yoy")


def cmd_runner(args):
    if getattr(args, "runner_cmd", None) == "backtest":
        _runner_backtest(args)
        return
    cands, meta = runner_mod.scan(
        min_gain_1y=args.min_gain_1y, pass_line=args.pass_line,
        max_mv=args.max_mv, top_kline=args.top_kline, top=args.top,
        cli_path=args.cli_path, zhishu_cli=args.zhishu_cli,
        use_forecast=not args.no_forecast, source=args.source)
    md = runner_mod.render_report(cands, meta)
    if args.archive:
        _write_archive({"runner.json": cands})
        with open(os.path.join(_archive_dir(), "runner.md"), "w", encoding="utf-8") as f:
            f.write(md)
    print(md)


def _runner_backtest(args):
    """单票阶段时间线回放(K线+大盘走腾讯免额度接口,三线同比用已验证 fixtures)。"""
    code = args.code.split(".")[0]
    yoy_path = os.path.join(YOY_FIXTURE_DIR, f"{code}.json")
    if not os.path.exists(yoy_path):
        print(f"缺少三线同比 fixture:{yoy_path}(先运行 python3 tests/gen_fixtures.py)",
              file=sys.stderr)
        sys.exit(2)
    with open(yoy_path, encoding="utf-8") as f:
        yoy_pack = json.load(f)
    sym = runner_mod.to_symbol(args.code)
    y = int(args.beg[:4])
    beg_adj = f"{y-1}-01-01"                      # 多拉一年,垫 250 日窗口
    rows = kline.fetch_qfq(sym, beg_adj, args.end)
    idx = kline.fetch_qfq("sh000001", beg_adj, args.end)
    gates = kline.index_gate_series(idx)
    tl = runner_mod.backtest(args.code, yoy_pack["name"], rows,
                             yoy_pack["yoy"], gates, args.beg, args.end,
                             periods=yoy_pack.get("periods"),
                             predicts=yoy_pack.get("predicts"))
    print(runner_mod.render_backtest(yoy_pack["name"], code, tl))
    # 首次亮灯摘要(上涨期间识别验收)
    lit = [t for t in tl if t["phase"] in ("主升期", "强势待确认", "回调击球区", "预告爆发")]
    if not lit:
        print("\n区间内无亮灯月(全部 退潮/深调考验区)。")
        return
    first = lit[0]
    entry_close = float(next(r[2] for r in rows if r[0] == first["asof"]))
    hi_after = max(float(r[3]) for r in rows if r[0] >= first["asof"])
    print(f"\n**首次亮灯 {first['month']}({first['phase']})**:当时收盘 {entry_close:.2f},"
          f"对应52周低点 {first['run_multiple']}x;其后区间最高 {hi_after:.2f},"
          f"自亮灯日起最大 +{(hi_after/entry_close-1)*100:.0f}%")


# ─── watch(右侧确认) ────────────────────────────────────────

def _entry_context_from_l1(c):
    q = c.get("quarterly") or {}
    ph = c.get("price_history") or {}
    return {
        "price": ph.get("last"), "mv_yi": None,
        "rev_growth_l1": c.get("rev_growth"), "profit_growth_l1": c.get("profit_growth"),
        "latest_period": q.get("latest_period"),
        "rev_yoy": (q.get("rev_yoy") or {}).get(q.get("latest_period")),
        "parent_yoy": (q.get("parent_yoy") or {}).get(q.get("latest_period")),
        "deduct_yoy": (q.get("deduct_yoy") or {}).get(q.get("latest_period")),
        "resonance": q.get("resonance"),
        "price_band": {"gain_1y": ph.get("gain_1yr"), "gain_3y": ph.get("gain_3yr"),
                       "pos_ath": ph.get("position_ratio")},
    }


def _entry_context_from_runner(c):
    q = c.get("quarterly") or {}
    m = c.get("m") or {}
    return {
        "price": m.get("last"), "mv_yi": c.get("mv_yi"),
        "phase": c.get("phase"), "run_multiple": c.get("run_multiple"),
        "x10_left": c.get("x10_left"), "industry": c.get("industry_l1"),
        "industry_n": c.get("industry_n"),
        "latest_period": q.get("latest_period"),
        "rev_yoy": (q.get("rev_yoy") or {}).get(q.get("latest_period")),
        "parent_yoy": (q.get("parent_yoy") or {}).get(q.get("latest_period")),
        "deduct_yoy": (q.get("deduct_yoy") or {}).get(q.get("latest_period")),
        "pos_1y": m.get("pos_1y"), "drawdown": m.get("drawdown"),
    }


def cmd_watch(args):
    wlpath = args.watchlist
    if args.watch_cmd == "add":
        w = wl.load(wlpath)
        item = wl.new_item(args.code, args.name, note=args.note, added_from="manual")
        wl.add(w, item)
        wl.save(w, wlpath)
        print(f"已加入 watchlist(scouted): {args.name}({args.code})")
        return
    if args.watch_cmd == "import":
        if args.from_runner:
            with open(args.from_runner, encoding="utf-8") as f:
                cands = json.load(f)
            cands = [c for c in cands if c.get("phase") in runner_mod.IMPORTABLE]
            ctx = _entry_context_from_runner
            src = f"runner-{date.today()}"
        else:
            with open(args.from_l1, encoding="utf-8") as f:
                cands = json.load(f)
            ctx = _entry_context_from_l1
            src = f"l1-{date.today()}"
        w = wl.load(wlpath)
        n = 0
        for c in cands[:args.top or 5]:
            if not c.get("name") or not c.get("code"):
                continue
            if wl.find(w, c["code"]):
                continue
            wl.add(w, wl.new_item(c["code"], c["name"], added_from=src,
                                  context=ctx(c)))
            n += 1
        wl.save(w, wlpath)
        print(f"导入 {n} 只 → scouted(来源:{'runner' if args.from_runner else 'L1'})")
        return
    if args.watch_cmd == "remove":
        w = wl.load(wlpath)
        it = wl.remove(w, args.code)
        wl.save(w, wlpath)
        print(f"已移除: {it['name']}({it['code']})")
        return
    if args.watch_cmd == "reopen":
        w = wl.load(wlpath)
        wl.transition(w, args.code, "scouted", note=args.note or "人工重开观察")
        wl.save(w, wlpath)
        print(f"已重开为 scouted: {args.code}")
        return

    # 默认: 列表;--run 则对 scouted 执行确认检查
    w = wl.load(wlpath)
    if not args.run:
        _print_watchlist(w)
        return
    _watch_run(args, w, wlpath)


def _watch_run(args, w, wlpath):
    scouted = [it for it in w["items"] if it["state"] == "scouted" and it.get("name")]
    gate = None
    qs_map, pos_map = {}, {}
    if scouted:
        names = [it["name"] for it in scouted]
        qs_map = fetch_quarterly_yoy(names, cli_path=args.cli_path)
        pos_map = price_position(names, cli_path=args.cli_path)
        if not args.no_market_gate:
            gate = market_gate(fetch_index_snapshot(cli_path=args.zhishu_cli))
    lines, rows = _render_watch_header(gate), []
    for it in scouted:
        qs = qs_map.get(it["name"])
        pos = pos_map.get(it["name"], {})
        chk = confirm_check(it, qs, pos, gate, confirm_pos=args.confirm_pos,
                            market_gate_on=not args.no_market_gate)
        it["last_checked"] = date.today().isoformat()
        it["last_period_seen"] = (qs or {}).get("latest_period") or it.get("last_period_seen")
        wl.record_price(it, pos.get("last"), (qs or {}).get("latest_period"))
        if chk["result"] == "confirmed":
            it["confirm"] = {"result": "confirmed", "date": date.today().isoformat(),
                             "signals": chk["signals"], "missing": []}
            wl.transition(w, it["code"], "confirmed",
                          note=";".join(chk["signals"]))
        elif chk["result"] == "invalidated":
            it["confirm"] = {"result": "invalidated", "date": date.today().isoformat(),
                             "signals": chk["signals"], "missing": []}
            wl.transition(w, it["code"], "closed", note=";".join(chk["signals"]))
        else:
            it["confirm"] = {"result": "waiting", "date": date.today().isoformat(),
                             "signals": chk["signals"], "missing": chk["missing"]}
        rows.append((it, qs, pos, chk))
    wl.save(w, wlpath)
    md = _render_watch_md(lines, rows, gate)
    d = _archive_dir()
    with open(os.path.join(d, "watch.md"), "w", encoding="utf-8") as f:
        f.write(md)
    print(md)
    print(f"\n[归档] {os.path.join(d, 'watch.md')}", file=sys.stderr)


def _render_watch_header(gate):
    win = disclosure_window()
    latest = latest_report_period()
    head = [f"# tenscreener · watch 右侧确认报告 ({date.today().isoformat()})\n"]
    if win:
        head.append(f"> 披露窗口:**{win['label']}**(至 {win['deadline']})——财报季,新数据陆续落地\n")
    else:
        head.append(f"> 当前非披露窗口(最新完整报告期:{q_label(*latest)});确认信号主要看价格/大盘\n")
    if gate:
        head.append(f"> 大盘β:**{gate.get('verdict')}**(method={gate.get('method')})\n")
    return head


def _render_watch_md(head, rows, gate):
    lines = list(head)
    lines.append("| 股票 | 状态 | 最新季三线(营收/归母/扣非 同比%) | pos_1y | 结论 | 缺失/信号 |")
    lines.append("|---|---|---|---|---|---|")
    for it, qs, pos, chk in rows:
        latest = (qs or {}).get("latest_period")
        if latest:
            tri = "/".join(_p((qs.get(k) or {}).get(latest))
                           for k in ("rev_yoy", "parent_yoy", "deduct_yoy"))
            tri += f" @{latest}"
        else:
            tri = "数据缺失"
        p = (pos or {}).get("pos_1y")
        detail = ";".join(chk["missing"] or chk["signals"])
        lines.append(f"| {it['name']}({it['code']}) | {it['state']} | {tri} | "
                     f"{'-' if p is None else f'{p:.2f}'} | **{chk['result']}** | {detail} |")
    return "\n".join(lines)


def _p(v):
    return f"{v:+.0f}" if isinstance(v, (int, float)) else "-"


def _print_watchlist(w):
    if not w["items"]:
        print("watchlist 为空。用法: watch add/import 加入标的。")
        return
    print(f"{'代码':8} {'名称':8} {'状态':12} {'最新确认/告警':40} 加入日")
    for it in w["items"]:
        c = it.get("confirm", {})
        t = it.get("track", {})
        badge = c.get("result") or (f"level={t.get('last_level')}" if t.get("last_level") else "")
        print(f"{it['code']:8} {it['name']:8} {it['state']:12} {badge:40} {it['added_at']}")


# ─── track(持有退出) ────────────────────────────────────────

def cmd_track(args):
    wlpath = args.watchlist
    if args.track_cmd == "promote":
        w = wl.load(wlpath)
        wl.transition(w, args.code, "holding",
                      note=f"人工建仓 cost={args.cost}" + (f" date={args.date}" if args.date else ""))
        it = wl.find(w, args.code)
        pp = price_position([it["name"]], cli_path=args.cli_path).get(it["name"], {})
        peak = max([args.cost] + ([pp["high_1y"]] if pp.get("high_1y") else []))
        it["track"] = {"cost": args.cost,
                       "promoted_at": args.date or date.today().isoformat(),
                       "peak_price": peak, "last_level": None, "last_reasons": []}
        wl.save(w, wlpath)
        print(f"confirmed→holding: {it['name']}({it['code']}) cost={args.cost} peak种子={peak}")
        return
    if args.track_cmd == "keep":
        w = wl.load(wlpath)
        wl.transition(w, args.code, "holding", note=args.note or "人工否决exit告警,继续持有")
        wl.save(w, wlpath)
        print(f"exit_signal→holding(继续持有): {args.code}")
        return
    if args.track_cmd == "close":
        w = wl.load(wlpath)
        wl.transition(w, args.code, "closed", note=args.note or args.reason or "人工清仓")
        wl.save(w, wlpath)
        print(f"→closed: {args.code}")
        return

    # 默认: 列表;--run 对 holding/exit_signal 执行退出检查
    w = wl.load(wlpath)
    if not args.run:
        _print_watchlist(w)
        return
    targets = [it for it in w["items"]
               if it["state"] in ("holding", "exit_signal")
               or (args.include_confirmed and it["state"] == "confirmed")]
    if not targets:
        print("无 holding/exit_signal 标的(先 track promote)。")
        return
    names = [it["name"] for it in targets if it.get("name")]
    qs_map = fetch_quarterly_yoy(names, cli_path=args.cli_path)
    pos_map = price_position(names, cli_path=args.cli_path)
    rows = []
    for it in targets:
        qs = qs_map.get(it.get("name"), {})
        pos = pos_map.get(it.get("name"), {})
        tr = it.setdefault("track", {})
        # peak 维护:历史峰值/现价/近一年最高 三者取大
        # (近一年最高覆盖"上次 track 至今之间创出的高点",漏跑也不丢峰值)
        peak = max([tr.get("peak_price") or 0, pos.get("last") or 0,
                    pos.get("high_1y") or 0]) or None
        res = exit_check(it, qs, pos, peak,
                         downshift_pp=args.downshift_pp)
        tr["peak_price"] = peak
        tr["last_level"] = res["level"]
        tr["last_reasons"] = res["reasons"]
        it["last_checked"] = date.today().isoformat()
        it["last_period_seen"] = qs.get("latest_period") or it.get("last_period_seen")
        wl.record_price(it, pos.get("last"), qs.get("latest_period"))
        if res["level"] == "exit" and it["state"] == "holding":
            wl.transition(w, it["code"], "exit_signal",
                          note=";".join(res["reasons"]) or "增速降档")
        elif res["level"] != "exit" and it["state"] == "exit_signal":
            wl.transition(w, it["code"], "holding",
                          note="告警条件不再满足,自动回落holding")
        rows.append((it, qs, pos, res))
    wl.save(w, wlpath)
    md = _render_track_md(rows)
    d = _archive_dir()
    with open(os.path.join(d, "track.md"), "w", encoding="utf-8") as f:
        f.write(md)
    print(md)
    print(f"\n[归档] {os.path.join(d, 'track.md')}", file=sys.stderr)


def _render_track_md(rows):
    lines = [f"# tenscreener · track 持有退出监测 ({date.today().isoformat()})\n"]
    lines.append("> 纪律:exit_signal 是告警不是指令;建仓/清仓永远人工裁定"
                "(静水:错了坚决砍,但中期调整要扛住)。\n")
    lines.append("| 股票 | 状态 | 成本 | 峰值 | 现价 | 回撤 | 最新季三线 | level | 理由 |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for it, qs, pos, res in rows:
        tr = it.get("track", {})
        latest = qs.get("latest_period")
        tri = ("数据缺失" if not latest else
               "/".join(_p((qs.get(k) or {}).get(latest))
                        for k in ("rev_yoy", "parent_yoy", "deduct_yoy")) + f" @{latest}")
        dd = res.get("drawdown")
        lines.append(
            f"| {it['name']}({it['code']}) | {it['state']} | {tr.get('cost') or '-'} | "
            f"{tr.get('peak_price') or '-'} | {pos.get('last') or '-'} | "
            f"{'-' if dd is None else f'{dd*100:.0f}%'} | {tri} | **{res['level']}** | "
            f"{';'.join(res['reasons']) or '基本面持续,持有'} |")
    return "\n".join(lines)


# ─── 参数 ───────────────────────────────────────────────────

def build_parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--cli-path", default=DEFAULT_FINANCE_CLI, help="hithink finance cli.py 路径")
    common.add_argument("--zhishu-cli", default=DEFAULT_ZHISHU_CLI, help="hithink zhishu cli.py 路径")
    common.add_argument("--out", help="输出 JSON 文件(默认 stdout)")
    common.add_argument("--cache-dir", help="问财缓存目录(默认 outputs/cache/YYYYMMDD;测试指向 fixtures)")
    common.add_argument("--no-cache", action="store_true", help="强制重新取数并刷新缓存")
    common.add_argument("--offline", action="store_true", help="只读缓存,缺失即报错(回放测试)")
    common.add_argument("--dump-raw", action="store_true", help="打印问财返回原始 key(调试字段漂移)")
    common.add_argument("--archive", action="store_true", help="结果归档到 outputs/YYYYMMDD/")

    p = argparse.ArgumentParser(
        description="tenscreener 十倍股筛选器 · CAN SLIM×静水 三级漏斗+生命周期监测",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = p.add_subparsers(dest="cmd")

    sp = sub.add_parser("screen", parents=[common], help="全流程 L1→L2→L3")
    sp.add_argument("--roe-min", type=float, default=15, help="L1 ROE 下限%%(默认 15;l1 单跑默认 5,见 docs)")
    _add_l1_params(sp)
    sp.add_argument("--top-l2", type=int, default=10, help="L2 保留数(默认 10)")
    sp.add_argument("--top-l3", type=int, default=5, help="L3 保留数(默认 5)")
    sp.add_argument("--sector-max-cap", type=float, help="行业最大市值(亿),用于 F2 终点对标")
    sp.add_argument("--report", action="store_true", help="输出 Markdown 报告")
    sp.set_defaults(func=cmd_screen)

    l1p = sub.add_parser("l1", parents=[common], help="只跑 L1 问财初筛")
    l1p.add_argument("--roe-min", type=float, default=15, help="L1 ROE 下限%%(默认 15;模块直跑默认 5)")
    _add_l1_params(l1p)
    l1p.add_argument("--top", type=int, default=0, help="保留前 N(0=全部)")
    l1p.set_defaults(func=cmd_l1)

    l2p = sub.add_parser("l2", parents=[common], help="只跑 L2 深度体检")
    l2p.add_argument("--candidates", required=True, help="L1 候选 JSON")
    l2p.add_argument("--top", type=int, default=10)
    l2p.set_defaults(func=cmd_l2)

    l3p = sub.add_parser("l3", parents=[common], help="只跑 L3 十倍潜力评估")
    l3p.add_argument("--l2", required=True, help="L2 体检 JSON")
    l3p.add_argument("--top", type=int, default=5)
    l3p.add_argument("--sector-max-cap", type=float)
    l3p.add_argument("--report", action="store_true")
    l3p.set_defaults(func=cmd_l3)

    # watch / track / runner
    rp = sub.add_parser("runner", parents=[common], help="右侧趋势中继(上涨期间识别十倍股)")
    rsub = rp.add_subparsers(dest="runner_cmd")
    rbt = rsub.add_parser("backtest", help="单票阶段时间线回放(上涨期间识别验收)")
    rbt.add_argument("code", help="股票代码,如 003010 / 300308")
    rbt.add_argument("--beg", default="2024-01-01")
    rbt.add_argument("--end", default=date.today().isoformat())
    rp.add_argument("--min-gain-1y", type=float, default=60, help="近1年涨幅下限%%(默认 60,已走出来)")
    rp.add_argument("--pass-line", type=float, default=40.0, help="营收/归母同比下限(默认 40=静水线)")
    rp.add_argument("--max-mv", type=float, default=2000, help="市值上限亿(默认 2000;大市值由已涨倍数/剩余空间与TPS的F3自然压制,不做硬卡)")
    rp.add_argument("--top-kline", type=int, default=30, help="拉K线细算的TopN(默认 30)")
    rp.add_argument("--top", type=int, default=15, help="输出前 N(默认 15)")
    rp.add_argument("--no-forecast", action="store_true", help="不接入业绩预告信号(东财免额度)")
    rp.add_argument("--source", choices=["auto", "wencai", "emoney"], default="auto",
                    help="Step1数据源:auto=问财失败自动降级东财(默认) | emoney=东财免额度(与回放同口径)")
    rp.set_defaults(func=cmd_runner)

    wp = sub.add_parser("watch", parents=[common], help="右侧确认(scouted→confirmed)")
    wp.add_argument("--watchlist", default=wl.DEFAULT_PATH, help="watchlist.json 路径")
    wsub = wp.add_subparsers(dest="watch_cmd")
    wadd = wsub.add_parser("add", help="手工加入 scouted")
    wadd.add_argument("code"); wadd.add_argument("name")
    wadd.add_argument("--note")
    wimp = wsub.add_parser("import", help="从 L1/runner 候选 JSON 批量导入")
    wimp.add_argument("--from-l1")
    wimp.add_argument("--from-runner")
    wimp.add_argument("--top", type=int, default=5)
    wrm = wsub.add_parser("remove", help="移除")
    wrm.add_argument("code")
    wro = wsub.add_parser("reopen", help="closed→scouted 人工重开")
    wro.add_argument("code"); wro.add_argument("--note")
    wp.add_argument("--run", action="store_true", help="对 scouted 执行确认检查")
    wp.add_argument("--confirm-pos", type=float, default=0.95, help="R2 创一年新高阈值(默认 0.95)")
    wp.add_argument("--no-market-gate", action="store_true", help="跳过 R3 大盘β")
    wp.set_defaults(func=cmd_watch)

    tp = sub.add_parser("track", parents=[common], help="持有退出监测(holding→exit_signal)")
    tp.add_argument("--watchlist", default=wl.DEFAULT_PATH, help="watchlist.json 路径")
    tsub = tp.add_subparsers(dest="track_cmd")
    tpr = tsub.add_parser("promote", help="confirmed→holding 人工建仓")
    tpr.add_argument("code"); tpr.add_argument("--cost", type=float, required=True)
    tpr.add_argument("--date", help="建仓日 YYYY-MM-DD(默认今天)")
    tke = tsub.add_parser("keep", help="exit_signal→holding 人工否决告警")
    tke.add_argument("code"); tke.add_argument("--note")
    tcl = tsub.add_parser("close", help="→closed 人工清仓/移除")
    tcl.add_argument("code"); tcl.add_argument("--reason")
    tp.add_argument("--run", action="store_true", help="对 holding/exit_signal 执行退出检查")
    tp.add_argument("--downshift-pp", type=float, default=30.0, help="E1 增速降档阈值pp(默认 30)")
    tp.add_argument("--include-confirmed", action="store_true", help="连带检查 confirmed(预演)")
    tp.set_defaults(func=cmd_track)

    # ── judge: 单标的十倍股完整判定(v3 判断权威, 2026-11-05) ──
    jp = sub.add_parser("judge", parents=[common], help="单标的十倍股判定(取数→素材包→三方法论判定→报告)")
    jp.add_argument("--code", "-c", required=True, help="股票代码(6位, 可带后缀)")
    jp.add_argument("--finalize", action="store_true",
                    help="校验 verdict.json 并组装 report.md(默认=取数产素材包)")
    jp.add_argument("--no-pdf", action="store_true",
                    help="快速模式: 跳过财报PDF下载与章节抽取, 纯API素材包(MD&A/募投/风险全文将缺席)")
    jp.add_argument("--pdf-dir", default=None,
                    help="PDF 归档目录(首次指定后持久为默认; 默认 outputs/pdf_library; 环境变量 TENSCREENER_PDF_DIR 亦可)")
    jp.add_argument("--publish", action="store_true",
                    help="发布: finalize + 上传飞书文档 + 清理中间产物(本地只留 report.md; 需已写 verdict.json)")
    jp.add_argument("--folder", default=None,
                    help="飞书目标文件夹 token(首次指定后持久为默认; 不指定用 config.judge_folder 或根目录)")
    jp.set_defaults(func=cmd_judge)

    return p


def _add_l1_params(sp):
    sp.add_argument("--max-1yr-gain", type=float, default=30, help="近1年涨幅上限%%(默认 30)")
    sp.add_argument("--max-3yr-gain", type=float, default=100, help="近3年涨幅上限%%(默认 100)")
    sp.add_argument("--min-6mo-gain", type=float, default=10, help="近6月涨幅下限%%(默认 10)")
    sp.add_argument("--max-position", type=float, default=0.75, help="当前价/历史最高上限(默认 0.75)")
    sp.add_argument("--max-quarterly", type=int, default=20, help="前 N 只查三线多季序列(默认 20)")


def main():
    # 预提取全局参数:允许出现在子命令之后(如 `watch add X Y --offline --watchlist P`)
    gp = argparse.ArgumentParser(add_help=False)
    gp.add_argument("--cache-dir")
    gp.add_argument("--offline", action="store_true")
    gp.add_argument("--no-cache", action="store_true")
    gp.add_argument("--dump-raw", action="store_true")
    gp.add_argument("--cli-path", dest="cli_path")
    gp.add_argument("--zhishu-cli", dest="zhishu_cli")
    gp.add_argument("--watchlist")
    given, remaining = gp.parse_known_args(sys.argv[1:])
    sys.argv = [sys.argv[0]] + remaining

    p = build_parser()
    args = p.parse_args()
    if not args.cmd:
        p.print_help()
        sys.exit(0)
    # 用户显式给过的全局参数覆盖子解析器默认值
    for k, v in vars(given).items():
        if v not in (None, False):
            setattr(args, k, v)

    # 全局缓存/离线配置(问财与K线同步;K线 fixtures 约定放 <cache-dir>/kline/)
    if args.cache_dir:
        wencai.set_cache_dir(args.cache_dir)
        kline.set_cache_dir(os.path.join(args.cache_dir, "kline"))
    if args.offline:
        if not args.cache_dir:
            p.error("--offline 需配合 --cache-dir 指向 fixtures 目录")
        wencai.set_offline(True)
        kline.set_offline(True)
    if args.no_cache:
        # 强制重取:用带随机后缀的新缓存目录,不误删共享当日缓存
        import uuid
        wencai.set_cache_dir(os.path.join(
            wencai.CACHE_ROOT,
            date.today().strftime("%Y%m%d") + "_refresh_" + uuid.uuid4().hex[:6]))
    if args.dump_raw:
        wencai.set_debug_keys(True)
    args.func(args)
    print(wencai.counter_report(), file=sys.stderr)


if __name__ == "__main__":
    main()
