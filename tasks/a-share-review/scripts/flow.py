#!/usr/bin/env python3
"""
flow.py — 当天 A 股板块/个股资金流向（a-share-review 内置模块）。

前身: 独立技能 hithink-moneyflow(2026-07), 2026-11-05 收编为本模块并优化:
  - CLI 路径解析改为「同级技能优先 + 多位置探测」(实测教训: ~/.claude/skills
    被清空后原硬编码路径静默失效, 复盘模块三随之降级)
  - 库函数化: fetch_rows() 抛 FlowError 而非 sys.exit, 供 data.py 直接 import;
    命令行入口(main)保留, 三种输出(table/html/json)不变
  - 解析/渲染逻辑原样保留(前缀匹配 [YYYYMMDD] 后缀字段 + 发散条形图)

不直接发 HTTP, 而是 shell-out 调用 hithink-market-query 的 cli(复用其鉴权/分页/重试),
固化已验证的 query 模板, 客户端按带符号金额重排。
数据来源: 同花顺问财(经 hithink-market-query), 消耗问财额度。
"""

import argparse
import html
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# hithink-market-query cli 的探测位置(顺序即优先级):
#   ① 同级技能 —— 安装布局 work/.claude/skills/{a-share-review, hithink-market-query} 并排
#   ② work 目录技能位置(本机唯一安装地, ~/.claude/skills 已废弃)
#   ③ 用户级 ~/.claude/skills(他人克隆本仓时的通用兜底)
_CLI_CANDIDATES = [
    Path(__file__).resolve().parents[1].parent / "hithink-market-query" / "scripts" / "cli.py",
    Path.home() / "Documents" / "work" / ".claude" / "skills" / "hithink-market-query" / "scripts" / "cli.py",
    Path.home() / ".claude" / "skills" / "hithink-market-query" / "scripts" / "cli.py",
]
FALLBACK_URL = "https://www.iwencai.com/unifiedwap/chat"
SOURCE = "同花顺问财（经 hithink-market-query）"
DEFAULT_TOP = 10
DEFAULT_TIMEOUT = 60


class FlowError(Exception):
    """取数失败(cli 缺失/超时/非JSON/非零退出)。"""


def wencai_cli_path():
    """-> Path 或 None: 探测 hithink-market-query cli。data.py 的问财调用共用此解析。"""
    for p in _CLI_CANDIDATES:
        if p.exists():
            return p
    return None


# view → (固化 query 模板, 排序方向, 视图类型)
#   排序方向：desc = 净额降序（流入/个股，大数在前）；asc = 净额升序（流出，最负在前）；None = 按合计降序
#   ⚠️ query 措辞决定问财返回的排序与口径，勿随意改写
VIEWS = {
    "industry-inflow":  ("今日行业板块主力资金净流入排名",                         "desc", "board"),
    "industry-outflow": ("今日主力资金净流出最多的行业板块",                       "asc",  "board"),
    "concept-inflow":   ("今日概念板块主力资金净流入排名",                         "desc", "board"),
    "concept-outflow":  ("今日概念板块主力资金净流出排名",                         "asc",  "board"),
    "orders":           ("今日行业板块超大单净流入额、大单净流入额、小单净流入额",  None,   "orders"),
    "stock":            ("今日{sector}板块主力资金净流入最多的个股",               "desc", "stock"),
}

VIEW_TITLES = {
    "industry-inflow":  "今日行业板块主力资金净流入",
    "industry-outflow": "今日行业板块主力资金净流出",
    "concept-inflow":   "今日概念板块主力资金净流入",
    "concept-outflow":  "今日概念板块主力资金净流出",
    "orders":           "今日行业板块资金类型分解（特大单/大单/小单）",
    "stock":            "今日{sector}板块主力资金净流入个股",
}

# 前缀匹配用的字段族——不同 view 返回字段名不同，但金额字段都带 [YYYYMMDD] 后缀。
NAME_PREFIXES = ["指数简称", "股票简称"]
CODE_PREFIXES = ["指数代码", "股票代码"]
AMOUNT_PREFIXES = ["主力净买入额", "主力资金流向", "资金净流入额"]
ORDERS_PREFIXES = ["特大单净买入额", "大单净买入额", "小单净买入额"]
ORDERS_LABELS = {"特大单净买入额": "特大单", "大单净买入额": "大单", "小单净买入额": "小单"}
PCT_PREFIXES = ["最新涨跌幅"]
DATE_RE = re.compile(r"\[(\d{8})\]")


# ---------------- 前缀匹配解析 ----------------

def find_key(row, prefix):
    """返回 row 中第一个以 prefix 开头的 key（忽略 [date] 后缀），无则 None。"""
    for k in row:
        if k.startswith(prefix):
            return k
    return None


def pick(row, prefixes):
    """返回第一个命中前缀的 (value, key)；全不中则 (None, None)。"""
    for p in prefixes:
        k = find_key(row, p)
        if k is not None:
            return row[k], k
    return None, None


def to_float(v):
    if v is None:
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def extract_date(rows, amount_prefixes):
    """从任一金额字段后缀 [YYYYMMDD] 提取交易日，返回 'YYYY-MM-DD' 或 None。"""
    for row in rows:
        for p in amount_prefixes:
            k = find_key(row, p)
            if k:
                m = DATE_RE.search(k)
                if m:
                    d = m.group(1)
                    return f"{d[:4]}-{d[4:6]}-{d[6:]}"
    return None


def parse_board(rows):
    out = []
    for r in rows:
        name, _ = pick(r, NAME_PREFIXES)
        amt, _ = pick(r, AMOUNT_PREFIXES)
        pct, _ = pick(r, PCT_PREFIXES)
        out.append({"name": name, "amount": to_float(amt), "pct": to_float(pct)})
    return out


def parse_stock(rows):
    out = []
    for r in rows:
        name, _ = pick(r, ["股票简称"])
        amt, _ = pick(r, ["主力资金流向"])
        pct, _ = pick(r, PCT_PREFIXES)
        industry, _ = pick(r, ["所属同花顺行业"])
        out.append({"name": name, "amount": to_float(amt), "pct": to_float(pct),
                    "industry": industry})
    return out


def parse_orders(rows):
    out = []
    for r in rows:
        name, _ = pick(r, ["指数简称"])
        item = {"name": name}
        for p in ORDERS_PREFIXES:
            v, _ = pick(r, [p])
            item[p] = to_float(v)
        pct, _ = pick(r, PCT_PREFIXES)
        item["pct"] = to_float(pct)
        item["net"] = sum(v for v in (item[p] for p in ORDERS_PREFIXES) if v is not None)
        out.append(item)
    return out


# ---------------- 格式化 ----------------

def fmt_yi(amount):
    """金额按 1e8 折算成「亿」，带正负号，2 位小数。None → '-'。"""
    if amount is None:
        return "-"
    return f"{amount / 1e8:+,.2f}"


def fmt_pct(p):
    if p is None:
        return "-"
    return f"{p:+.2f}%"


def sort_and_top(items, view, top):
    direction, vtype = VIEWS[view][1], VIEWS[view][2]
    if vtype == "orders":
        items = sorted(items, key=lambda x: x["net"], reverse=True)
    else:
        none_sentinel = float("-inf") if direction == "desc" else float("inf")
        items = sorted(
            items,
            key=lambda x: (x["amount"] if x["amount"] is not None else none_sentinel),
            reverse=(direction == "desc"),
        )
    return items[:top]


# ---------------- 取数(库入口, 抛 FlowError) ----------------

def _call_cli(query, limit, timeout):
    cli = wencai_cli_path()
    if cli is None:
        raise FlowError(
            "hithink-market-query cli 未找到, 探测过: %s\n  安装指引: https://www.iwencai.com/skillhub"
            % "; ".join(str(p) for p in _CLI_CANDIDATES))
    cmd = [sys.executable, str(cli), "--query", query, "--limit", str(limit)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise FlowError(f"调用 cli 超时（{timeout}s）")
    if proc.returncode != 0:
        # cli 失败时把它的 JSON 错误透传，不改写不淹没
        raise FlowError((proc.stdout or proc.stderr or "cli 未知错误")[:500])
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        raise FlowError(f"cli 输出非 JSON：{proc.stdout[:300]}")


def fetch_rows(view, top=DEFAULT_TOP, sector=None, timeout=DEFAULT_TIMEOUT):
    """库入口: -> (rows, meta)。无数据返回 ([], meta); 失败抛 FlowError。
    data.py 板块资金流直接调用本函数, 不再 shell-out 独立技能。"""
    if view not in VIEWS:
        raise FlowError(f"未知 view: {view}")
    query_tmpl, _, vtype = VIEWS[view]
    if "{sector}" in query_tmpl:
        if not sector:
            raise FlowError(f"view={view} 需要 sector（如 半导体）")
        query = query_tmpl.format(sector=sector)
    else:
        query = query_tmpl

    limit = max(top, 50)  # 多取一些，客户端按带符号金额重排兜底
    payload = _call_cli(query, limit, timeout)
    datas = payload.get("datas") or []
    meta = {
        "view": view, "query": query,
        "trade_date": None, "code_count": payload.get("code_count"),
        "shown": 0, "top": top, "source": SOURCE,
    }
    if not datas:
        return [], meta

    if vtype == "orders":
        rows = parse_orders(datas)
        date_prefixes = ORDERS_PREFIXES
    elif vtype == "stock":
        rows = parse_stock(datas)
        date_prefixes = ["主力资金流向"]
    else:
        rows = parse_board(datas)
        date_prefixes = AMOUNT_PREFIXES

    rows = sort_and_top(rows, view, top)
    meta["trade_date"] = extract_date(datas, date_prefixes)
    meta["shown"] = len(rows)
    return rows, meta


# ---------------- 渲染：表格 ----------------

def render_table(rows, meta, title, vtype):
    date = meta.get("trade_date") or "?"
    lines = [f"## {title} Top {meta['top']}（{date}）", f"数据来源：{SOURCE}", ""]
    if vtype == "orders":
        lines.append("| 排名 | 板块 | 特大单（亿） | 大单（亿） | 小单（亿） | 合计（亿） | 涨跌幅 |")
        lines.append("|---:|:---|---:|---:|---:|---:|---:|")
        for i, r in enumerate(rows, 1):
            lines.append(
                f"| {i} | {r['name'] or '-'} | {fmt_yi(r['特大单净买入额'])} | "
                f"{fmt_yi(r['大单净买入额'])} | {fmt_yi(r['小单净买入额'])} | "
                f"{fmt_yi(r['net'])} | {fmt_pct(r['pct'])} |"
            )
    else:
        subject = "个股" if vtype == "stock" else "板块"
        lines.append(f"| 排名 | {subject} | 主力净额（亿） | 涨跌幅 |")
        lines.append("|---:|:---|---:|---:|")
        for i, r in enumerate(rows, 1):
            lines.append(
                f"| {i} | {r['name'] or '-'} | {fmt_yi(r['amount'])} | {fmt_pct(r['pct'])} |"
            )
    if meta.get("code_count") is not None:
        lines.append("")
        lines.append(f"_共命中 {meta['code_count']} 条，展示前 {len(rows)} 条 · {SOURCE}_")
    return "\n".join(lines)


# ---------------- 渲染：自包含 HTML 发散条形图 ----------------

# CSS 用纯字符串（含大量花括号），不能用 f-string / .format()。
HTML_CSS = """
:root {
  --bg: #fafafa; --fg: #1a1a1a; --muted: #6b7280; --card: #ffffff;
  --grid: #e5e7eb; --pos: #16a34a; --neg: #dc2626; --zero: #9ca3af;
}
html.dark {
  --bg: #0f172a; --fg: #e5e7eb; --muted: #94a3b8; --card: #1e293b;
  --grid: #334155; --pos: #22c55e; --neg: #ef4444; --zero: #64748b;
}
body { font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", "Microsoft YaHei", sans-serif;
       background: var(--bg); color: var(--fg); margin: 0; padding: 24px; }
.container { max-width: 940px; margin: 0 auto; }
h1 { font-size: 20px; margin: 0 0 4px; }
.meta { color: var(--muted); font-size: 13px; margin-bottom: 18px; }
.toggle { float: right; background: var(--card); border: 1px solid var(--grid);
          color: var(--fg); padding: 4px 12px; border-radius: 6px; cursor: pointer; font-size: 13px; }
.toggle:hover { border-color: var(--muted); }
.chart { clear: both; }
.row { display: grid; grid-template-columns: 130px 1fr 96px; align-items: center;
       gap: 10px; padding: 4px 0; }
.label { text-align: right; font-size: 13px; overflow: hidden; text-overflow: ellipsis;
         white-space: nowrap; }
.track { position: relative; height: 22px; background: var(--card);
         border-radius: 4px; border: 1px solid var(--grid); }
.track::before { content: ""; position: absolute; left: 50%; top: 0; bottom: 0;
                 width: 1px; background: var(--zero); }
.bar { position: absolute; top: 2px; bottom: 2px; border-radius: 2px; min-width: 0; }
.bar.pos { background: var(--pos); left: 50%; }
.bar.neg { background: var(--neg); }
.value { font-size: 12px; font-variant-numeric: tabular-nums; color: var(--muted); }
.group { margin-bottom: 14px; padding-bottom: 6px; border-bottom: 1px dashed var(--grid); }
.group:last-child { border-bottom: none; }
.group-label { font-size: 13px; font-weight: 600; margin: 4px 0 2px; }
.sub { display: grid; grid-template-columns: 130px 1fr 96px; align-items: center;
       gap: 10px; padding: 2px 0; }
.sub .label { font-size: 12px; color: var(--muted); }
.sub .track { height: 16px; }
.footer { margin-top: 24px; color: var(--muted); font-size: 12px;
          border-top: 1px solid var(--grid); padding-top: 10px; }
"""


def _bar_html(amt, max_abs):
    """单根发散条形：正→从中线向右绿，负→从中线向左红。"""
    if amt is None or amt == 0:
        return '<div class="bar" style="left:50%;width:0"></div>'
    ratio = abs(amt) / max_abs * 50  # 占 track 一半的百分比
    if amt > 0:
        return f'<div class="bar pos" style="width:{ratio:.2f}%"></div>'
    return f'<div class="bar neg" style="left:{50 - ratio:.2f}%;width:{ratio:.2f}%"></div>'


def _max_abs(values):
    vals = [abs(v) for v in values if v is not None]
    return max(vals, default=1) or 1


def render_bars_single(rows):
    max_abs = _max_abs(r.get("amount") for r in rows)
    parts = []
    for r in rows:
        name = html.escape(str(r.get("name") or "-"))
        parts.append(
            f'<div class="row"><div class="label" title="{name}">{name}</div>'
            f'<div class="track">{_bar_html(r.get("amount"), max_abs)}</div>'
            f'<div class="value">{fmt_yi(r.get("amount"))} 亿</div></div>'
        )
    return "\n".join(parts)


def render_bars_orders(rows):
    all_vals = [r[k] for r in rows for k in ORDERS_PREFIXES if r.get(k) is not None]
    max_abs = _max_abs(all_vals)
    parts = []
    for r in rows:
        name = html.escape(str(r.get("name") or "-"))
        parts.append(f'<div class="group"><div class="group-label">{name}</div>')
        for k in ORDERS_PREFIXES:
            lab = ORDERS_LABELS[k]
            parts.append(
                f'<div class="sub"><div class="label">{lab}</div>'
                f'<div class="track">{_bar_html(r.get(k), max_abs)}</div>'
                f'<div class="value">{fmt_yi(r.get(k))} 亿</div></div>'
            )
        parts.append("</div>")
    return "\n".join(parts)


def render_html(rows, meta, title, vtype, out_dir, view):
    date = meta.get("trade_date") or "?"
    full_title = f"{title} Top {meta['top']}（{date}）"
    meta_line = (
        f"交易日 {date} · 共命中 {meta.get('code_count', '?')} 条 · "
        f"展示前 {len(rows)} 条 · 排序：{('降序' if VIEWS[view][1] == 'desc' else '升序') if VIEWS[view][1] else '按合计降序'}"
    )
    body = render_bars_orders(rows) if vtype == "orders" else render_bars_single(rows)

    # 组装：CSS 段直接拼接，动态段用 f-string，避免 .format() 撞 CSS 花括号
    doc = (
        "<!DOCTYPE html>\n"
        '<html lang="zh-CN">\n<head>\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{html.escape(full_title)}</title>\n"
        f"<style>{HTML_CSS}</style>\n"
        "</head>\n<body>\n<div class=\"container\">\n"
        '<button class="toggle" onclick="document.documentElement.classList.toggle(\'dark\')">明 / 暗</button>\n'
        f"<h1>{html.escape(full_title)}</h1>\n"
        f'<div class="meta">{html.escape(meta_line)}</div>\n'
        f'<div class="chart">\n{body}\n</div>\n'
        f'<div class="footer">数据来源：{SOURCE} · 生成于 {html.escape(meta["generated_at"])}</div>\n'
        "</div>\n"
        "<script>"
        "if(window.matchMedia&&matchMedia('(prefers-color-scheme: dark)').matches)"
        "document.documentElement.classList.add('dark');"
        "</script>\n"
        "</body>\n</html>\n"
    )

    fname = f"capital_flow_{view}_{date.replace('-', '')}.html"
    target_dir = Path(out_dir) if out_dir else Path.cwd()
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / fname
    target.write_text(doc, encoding="utf-8")
    return target


# ---------------- 命令行入口(手动用法保留) ----------------

def main():
    ap = argparse.ArgumentParser(
        prog="flow",
        description="当天 A 股板块/个股资金流向（行业/概念 流入流出榜、大小单分解、个股主力资金）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "固化 view 与示例：\n"
            "  python3 scripts/flow.py --view industry-inflow\n"
            "  python3 scripts/flow.py --view concept-outflow --top 20\n"
            "  python3 scripts/flow.py --view orders --format html\n"
            "  python3 scripts/flow.py --view stock --sector 半导体\n\n"
            f"数据来源：{SOURCE}"
        ),
    )
    ap.add_argument("--view", "-v", required=True, choices=list(VIEWS.keys()),
                    help="资金流向视图")
    ap.add_argument("--top", "-n", type=int, default=DEFAULT_TOP,
                    help=f"取前 N 条（默认 {DEFAULT_TOP}）")
    ap.add_argument("--sector", "-s", default=None,
                    help="板块名，仅 view=stock 必填（如 半导体、银行、光伏）")
    ap.add_argument("--format", "-f", choices=["table", "html", "json"], default="table",
                    help="输出格式（默认 table）")
    ap.add_argument("--out", "-o", default=None,
                    help="输出目录（仅 --format html 生效，默认当前目录）")
    ap.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT,
                    help=f"调用 cli 超时秒数（默认 {DEFAULT_TIMEOUT}）")
    args = ap.parse_args()

    try:
        rows, meta = fetch_rows(args.view, args.top, args.sector, args.timeout)
    except FlowError as e:
        sys.stderr.write(f"[flow] {e}\n")
        sys.exit(1)
    if not rows:
        sys.stdout.write(
            f"[flow] 未查询到数据（view={args.view}）。\n"
            f"  可能非交易时段或查询未命中。可访问同花顺问财 web 端核实：{FALLBACK_URL}\n"
        )
        return

    meta["generated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    title = VIEW_TITLES[args.view].format(sector=args.sector or "")
    vtype = VIEWS[args.view][2]
    if args.format == "json":
        print(json.dumps({"meta": meta, "rows": rows}, ensure_ascii=False, indent=2))
    elif args.format == "html":
        print(str(render_html(rows, meta, title, vtype, args.out, args.view)))
    else:
        print(render_table(rows, meta, title, vtype))


if __name__ == "__main__":
    main()
