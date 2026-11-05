#!/usr/bin/env python3
"""
CAN SLIM 选股辅助脚本 — 轻量取数与汇总工具

职责：批量调用同花顺问财（hithink finance/market/zhishu）cli.py 取数，
按 C/A/N/S/L/I/M 七要素做字段归一化，输出 Markdown 体检表 + 可选 JSON。
不做最终打分 —— 打分由 Claude 按 SKILL.md 综合判断。

依赖：
  - 环境变量 IWENCAI_API_KEY（问财密钥）
  - hithink-finance-query / hithink-market-query / hithink-zhishu-query 三个 skill 已安装

用法：
  # 模式 A：用一个 CAN SLIM 初筛问句，问财返回候选并整理成表
  python3 canslim_screener.py --screen "最近一季净利润同比增长大于25%且股价创52周新高的股票"

  # 模式 B：对指定股票批量取七要素数据，整理成体检表
  python3 canslim_screener.py --tickers "宁德时代,比亚迪,中际旭创"
  python3 canslim_screener.py --tickers "宁德时代" --json

设计说明：
  问财是自然语言接口，返回字段名动态。本脚本用关键字做"容错归类"，
  把返回字段尽量映射到 C/A/N/S/L/I 六要素列；匹配不上的字段保留在"其他"列，
  并标注哪些要素存在"数据缺口"，供 Claude 阅读原始 datas 后人工补充。
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

DEFAULT_CLI_ROOT = os.path.expanduser("~/.claude/skills")
DEFAULT_LIMIT = "20"

# 三个 hithink skill 的 cli.py 模板路径
CLI_PATHS = {
    "finance": "{root}/hithink-finance-query/scripts/cli.py",
    "market": "{root}/hithink-market-query/scripts/cli.py",
    "zhishu": "{root}/hithink-zhishu-query/scripts/cli.py",
}

# 七要素 → 字段名容错匹配关键字（顺序即优先级；命中第一个即归属）
# 注意：N（新高/近1年）排在 L（近3/6/12月排名）之前，避免"近1年涨幅"误归 L
FIELD_KEYWORDS = {
    "C": ["最近一季", "单季", "当季", "净利润同比增长", "eps同比", "每股收益同比", "归母净利润同比"],
    "A": ["近三年", "近3年", "三年复合", "复合增长", "年度净利润", "年报净利润", "roe", "净资产收益"],
    "N": ["新高", "52周", "历史新高", "近一年", "近1年", "年涨幅", "年涨跌幅"],
    "S": ["量比", "换手", "成交量", "成交额", "流通市值", "流通股本", "自由流通"],
    "L": ["近三月", "近3月", "近六月", "近6月", "近十二月", "近12月", "阶段涨幅", "涨幅排名", "相对强度", "rs"],
    "I": ["主力", "机构", "基金持股", "基金持仓", "北向", "持仓比例", "净流入", "净买额", "增仓"],
}
ELEMENTS = ["C", "A", "N", "S", "L", "I"]


def cli_path(skill: str, cli_root: str) -> str:
    return CLI_PATHS[skill].format(root=cli_root)


def call_iwencai(skill: str, query: str, limit: str, cli_root: str) -> dict:
    """调用对应 hithink skill 的 cli.py，返回解析后的 dict。

    成功返回问财的标准输出 dict（含 datas）；失败返回 {"error": ..., "raw_stderr": ...}。
    """
    script = cli_path(skill, cli_root)
    if not os.path.exists(script):
        return {"error": f"cli.py 未找到：{script}（请确认 {skill} skill 已安装或用 --cli-dir 指定）"}
    try:
        proc = subprocess.run(
            ["python3", script, "--query", query, "--limit", limit],
            capture_output=True, text=True, timeout=60,
        )
    except subprocess.TimeoutExpired:
        return {"error": f"调用 {skill} 超时（query={query}）"}
    if proc.returncode != 0:
        return {"error": f"{skill} 调用失败（exit={proc.returncode}）", "raw_stderr": proc.stderr.strip()[:500]}
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"error": f"{skill} 返回非 JSON", "raw_stdout": proc.stdout.strip()[:500]}


def classify_field(field_name: str):
    """把问财字段名归类到 C/A/N/S/L/I，匹配不上返回 None。"""
    name = str(field_name).lower()
    # 特殊规则：带日期区间的涨跌幅（如 涨跌幅[20250619-20260618]）归 L（相对强度/阶段涨幅），
    # 排除当日"最新涨跌幅"。近 6 月/近 1 年涨幅都视为价格强度信号，
    # 长短周期由 Claude 看字段名里的日期区间判断。
    if "涨跌幅" in name and "[" in name and "最新" not in name:
        return "L"
    for elem in ELEMENTS:
        for kw in FIELD_KEYWORDS[elem]:
            if kw.lower() in name:
                return elem
    return None


def extract_stock_identity(row: dict):
    """从一行 datas 里提取 (代码, 简称)，找不到则用兜底。"""
    code, name = "", ""
    for k, v in row.items():
        kl = str(k).lower()
        if not code and ("代码" in str(k) or "code" in kl):
            code = str(v)
        if not name and ("简称" in str(k) or "名称" in str(k) or "股票名" in str(k)):
            name = str(v)
    return code, name


def build_row(row: dict, label: str = "") -> dict:
    """把一行 datas 归一化为 {code, name, C/A/N/S/L/I: [vals], other: [k=v], gaps: [...]}。"""
    code, name = extract_stock_identity(row)
    bucket = {e: [] for e in ELEMENTS}
    other = []
    for k, v in row.items():
        if str(k).strip() == "":
            continue
        # 跳过身份字段本身
        if any(t in str(k) for t in ("代码", "简称", "名称")) or "code" in str(k).lower():
            continue
        elem = classify_field(k)
        val = format_val(v)
        if elem:
            bucket[elem].append(f"{k}={val}")
        else:
            other.append(f"{k}={val}")
    gaps = [e for e in ELEMENTS if not bucket[e]]
    return {
        "code": code or label,
        "name": name or label,
        "buckets": bucket,
        "other": other,
        "gaps": gaps,
    }


def format_val(v) -> str:
    """把问财返回值格式化为可读字符串。"""
    if v is None:
        return "—"
    if isinstance(v, (int, float)):
        return f"{v}"
    s = str(v).strip()
    return s if s else "—"


def join_vals(vals) -> str:
    return "<br>".join(vals) if vals else "—"


def render_markdown(rows: list, market_ctx: str, raw_screen_query: str = None) -> str:
    """把归一化的行渲染成 Markdown 体检表。"""
    lines = []
    lines.append("# CAN SLIM 选股体检表\n")
    lines.append("> ⚠️ 仅数据汇总，非打分、非投资建议。打分与买卖判断由 Claude 按 SKILL.md 七要素表综合做出。\n")

    # M 大盘环境
    lines.append("## M — 大盘环境（总开关）\n")
    lines.append(market_ctx.strip() + "\n" if market_ctx.strip() else "（未取到大盘数据，请手工确认市场方向）\n")

    # 候选体检表
    lines.append("## 候选股七要素数据\n")
    header = "| 股票 | C 当季收益 | A 年度收益 | N 新高 | S 供需 | L 领涨强度 | I 机构 |"
    sep = "|------|-----------|-----------|--------|--------|-----------|--------|"
    lines.append(header)
    lines.append(sep)
    for r in rows:
        b = r["buckets"]
        title = r["name"] or r["code"]
        if r["code"] and r["name"] and r["code"] != r["name"]:
            title = f"{r['name']}（{r['code']}）"
        lines.append(
            f"| {title} | {join_vals(b['C'])} | {join_vals(b['A'])} | {join_vals(b['N'])} "
            f"| {join_vals(b['S'])} | {join_vals(b['L'])} | {join_vals(b['I'])} |"
        )

    # 数据缺口
    lines.append("\n## 数据缺口（需人工补充或放宽 query 重查）\n")
    any_gap = False
    for r in rows:
        title = r["name"] or r["code"]
        if r["gaps"]:
            any_gap = True
            lines.append(f"- **{title}**：缺 {', '.join(r['gaps'])}")
    if not any_gap:
        lines.append("- （七要素均有字段命中，但仍需 Claude 核对字段含义与催化剂真伪）")

    # 其他未归类字段（保留原始信息，避免丢失）
    has_other = any(r["other"] for r in rows)
    if has_other:
        lines.append("\n## 其他返回字段（未归入七要素，供参考）\n")
        for r in rows:
            if r["other"]:
                title = r["name"] or r["code"]
                lines.append(f"- **{title}**：{'；'.join(r['other'])}")

    if raw_screen_query:
        lines.append(f"\n---\n*初筛 query：`{raw_screen_query}`*")
    return "\n".join(lines) + "\n"


def get_market_context(cli_root: str, limit: str) -> str:
    """查 M 大盘方向（zhishu），返回文本。"""
    res = call_iwencai("zhishu", "上证指数 沪深300 创业板指 近20日涨跌幅", limit, cli_root)
    if "datas" in res and res["datas"]:
        parts = []
        for row in res["datas"][:3]:
            kv = "；".join(f"{k}={format_val(v)}" for k, v in row.items() if str(k).strip())
            parts.append(kv)
        return "\n".join(parts)
    if "error" in res:
        return f"（大盘数据获取失败：{res['error']}）"
    return "（未取到大盘数据）"


def run_screen(query: str, source: str, cli_root: str, limit: str) -> tuple:
    """模式 A：用一个初筛 query 拿候选，整理成表。返回 (rows, market_ctx, raw)。"""
    res = call_iwencai(source, query, limit, cli_root)
    if "error" in res:
        return [], f"（初筛失败：{res['error']}）", res
    datas = res.get("datas", [])
    rows = [build_row(d) for d in datas]
    market_ctx = get_market_context(cli_root, limit)
    # 保留原始返回供 Claude 读取动态字段
    raw = {"source": source, "query": query, "code_count": res.get("code_count"),
           "returned": len(datas), "datas": datas}
    return rows, market_ctx, raw


def run_tickers(tickers: list, cli_root: str, limit: str) -> tuple:
    """模式 B：对每只股票发 finance + market 富 query，合并归类。返回 (rows, market_ctx, raws)。"""
    market_ctx = get_market_context(cli_root, limit)
    rows = []
    raws = []
    for tk in tickers:
        tk = tk.strip()
        if not tk:
            continue
        finance_q = f"{tk} 最近一季净利润同比增长率 近三年净利润同比增长 ROE 机构持股比例"
        # market query 用实测稳定的写法：量比/换手→S，区间涨幅→L，主力资金流向→I
        # 注：个股查"是否创52周新高"问财返回空（需用 --screen 筛选式），故 N 在模式 B 以近一年涨幅近似
        market_q = f"{tk} 量比 换手率 近六月涨幅 近一年涨幅 主力资金流向"
        fin = call_iwencai("finance", finance_q, "5", cli_root)
        mkt = call_iwencai("market", market_q, "5", cli_root)

        merged = {}
        fin_row = fin.get("datas", [{}])[0] if "datas" in fin and fin["datas"] else {}
        mkt_row = mkt.get("datas", [{}])[0] if "datas" in mkt and mkt["datas"] else {}
        merged.update(fin_row)
        merged.update(mkt_row)

        if not merged:
            row = {"code": tk, "name": tk, "buckets": {e: [] for e in ELEMENTS},
                   "other": [], "gaps": ELEMENTS[:]}
            row["buckets"]["C"] = [f"取数失败：{fin.get('error', '无数据')}"]
            row["buckets"]["N"] = [f"取数失败：{mkt.get('error', '无数据')}"]
            rows.append(row)
        else:
            rows.append(build_row(merged, label=tk))
        raws.append({"ticker": tk, "finance": fin_row, "market": mkt_row})
    return rows, market_ctx, raws


def main():
    ap = argparse.ArgumentParser(
        description="CAN SLIM 选股辅助脚本（问财取数 + 汇总体检表，不打分）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="示例：\n"
               "  canslim_screener.py --screen '最近一季净利润同比增长大于25%的股票'\n"
               "  canslim_screener.py --tickers '宁德时代,比亚迪'\n"
               "  canslim_screener.py --tickers '宁德时代' --json\n",
    )
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--screen", help="模式 A：CAN SLIM 初筛问句（自然语言，问财解析）")
    g.add_argument("--tickers", help="模式 B：逗号分隔的股票名称/代码")
    ap.add_argument("--source", choices=["finance", "market", "zhishu"], default="finance",
                    help="--screen 模式调用的问财 skill（默认 finance）")
    ap.add_argument("--cli-dir", default=DEFAULT_CLI_ROOT, help=f"hithink cli.py 根目录（默认 {DEFAULT_CLI_ROOT}）")
    ap.add_argument("--limit", default=DEFAULT_LIMIT, help=f"问财每页条数（默认 {DEFAULT_LIMIT}）")
    ap.add_argument("--json", action="store_true", help="额外输出结构化 JSON 到 stderr")
    args = ap.parse_args()

    if not os.environ.get("IWENCAI_API_KEY"):
        print("⚠️ 未检测到环境变量 IWENCAI_API_KEY，问财调用会失败。"
              "获取：https://www.iwencai.com/skillhub", file=sys.stderr)

    if args.screen:
        rows, market_ctx, raw = run_screen(args.screen, args.source, args.cli_dir, args.limit)
        md = render_markdown(rows, market_ctx, raw_screen_query=args.screen)
        print(md)
        if args.json:
            print(json.dumps({"mode": "screen", "rows": rows, "raw": raw}, ensure_ascii=False, indent=2),
                  file=sys.stderr)
    else:
        tickers = [t.strip() for t in args.tickers.split(",") if t.strip()]
        rows, market_ctx, raws = run_tickers(tickers, args.cli_dir, args.limit)
        md = render_markdown(rows, market_ctx)
        print(md)
        if args.json:
            print(json.dumps({"mode": "tickers", "rows": rows, "raws": raws}, ensure_ascii=False, indent=2),
                  file=sys.stderr)


if __name__ == "__main__":
    main()
