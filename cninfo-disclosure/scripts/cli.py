#!/usr/bin/env python3
"""
巨潮信息披露下载 · 统一入口（cninfo-disclosure）

支持三类文档：
  - 财报：年报 / 半年报 / 一季报 / 三季报   （按 股票 + 年份 定位，巨潮）
  - 招股书：首发招股说明书                 （按 股票 定位，无需年份，巨潮）
  - 券商研报：卖方研报清单                 （按 股票 定位，无需年份，东方财富）
港股财报走 HKEX 披露易（hkex.py，需 Playwright）。

子命令：
  search    搜索公告列表
  download  搜索并下载 PDF

用法：
  # 财报
  python3 scripts/cli.py download --stock "三一重工" --year 2025
  python3 scripts/cli.py download --stock "三一重工" --year 2025 --type semi -o /tmp/

  # 招股书（无需 --year）
  python3 scripts/cli.py download --stock "宁德时代" --type prospectus -o /tmp/

  # 券商研报清单（无需 --year）
  python3 scripts/cli.py download --stock 688578 --type research -o /tmp/

  # 港股财报
  python3 scripts/cli.py download --stock 01888 --year 2025 -m hk -o /tmp/
"""

import argparse
import importlib.util
import json
import os
import sys

from cninfo_client import DEFAULT_TIMEOUT, detect_market, parse_years
from reports import REPORT_TYPES, download_report, search_reports
from prospectus import download_prospectus, search_prospectus
from research import download_research, search_research


# ─── 子命令：search ──────────────────────────────────────────

def cmd_search(args):
    """搜索公告列表。"""
    if detect_market(args.stock, getattr(args, "market", "auto")) == "hk":
        print(json.dumps({
            "success": False, "market": "hk",
            "error": "港股请直接用 download 命令（HKEX 搜索由浏览器驱动，已集成在 download 流程中）",
        }, ensure_ascii=False, indent=2))
        return

    doc_type = args.type

    # 招股书：无需年份
    if doc_type == "prospectus":
        anns = search_prospectus(args.stock, timeout=args.timeout)
        out = {
            "success": True, "stock": args.stock, "doc_type": "prospectus",
            "total": len(anns),
            "announcements": [
                {"title": a["title"], "date": a["date"], "pdf_url": a["pdf_url"]}
                for a in anns
            ],
        }
        if not anns:
            out["tip"] = "未找到招股说明书。老公司可能无电子版；未上市公司请到证监会/交易所审核系统查。"
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return

    # 研报：无需年份，数据源为东方财富
    if doc_type == "research":
        reports = search_research(args.stock, timeout=args.timeout)
        if not reports:
            print(json.dumps({
                "success": False, "stock": args.stock, "doc_type": "research",
                "error": f"未在东方财富找到 {args.stock} 的券商研报，可能暂无券商覆盖。",
            }, ensure_ascii=False, indent=2))
            return
        print(json.dumps({
            "success": True, "stock": args.stock, "doc_type": "research",
            "total": len(reports), "reports": reports,
        }, ensure_ascii=False, indent=2))
        return

    if doc_type == "interim":
        print(json.dumps({"success": False, "error": "A股用 semi；interim 是港股中期报告，港股请加 -m hk"},
                         ensure_ascii=False, indent=2))
        return

    # 财报类：需要年份
    if not args.year:
        print(json.dumps({"success": False,
                          "error": "财报搜索需要 --year（招股书用 --type prospectus）"},
                         ensure_ascii=False, indent=2))
        return

    types_to_search = list(REPORT_TYPES.keys()) if doc_type == "all" else [doc_type]
    years = parse_years(args.year)

    all_announcements = []
    for rt_key in types_to_search:
        rt = REPORT_TYPES[rt_key]
        for y in years:
            try:
                anns = search_reports(args.stock, y, report_type=rt_key, timeout=args.timeout)
                for ann in anns:
                    ann["report_type"] = rt_key
                    ann["report_label"] = rt["label"]
                all_announcements.extend(anns)
            except Exception:
                continue

    output = {
        "success": True, "stock": args.stock,
        "total": len(all_announcements),
        "announcements": [
            {"title": a["title"], "date": a["date"], "pdf_url": a["pdf_url"],
             "report_type": a.get("report_type"), "report_label": a.get("report_label")}
            for a in all_announcements
        ],
    }
    if not all_announcements:
        output["tip"] = "未找到公告。可能该报告尚未发布，或公司名称/年份有误。"
    print(json.dumps(output, ensure_ascii=False, indent=2))


# ─── 子命令：download ───────────────────────────────────────

def cmd_download_hk(args):
    """港股下载：委托 hkex 模块(Playwright 驱动 HKEX 披露易)。"""
    here = os.path.dirname(os.path.abspath(__file__))
    spec = importlib.util.spec_from_file_location("hkex", os.path.join(here, "hkex.py"))
    hkex = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hkex)

    if args.type not in ("annual", "interim"):
        print(json.dumps({
            "success": False, "market": "hk", "stock": args.stock,
            "error": "港股只有年报(annual)和中期报告(interim)，不支持 semi/q1/q3/prospectus/all",
        }, ensure_ascii=False, indent=2))
        return

    years = parse_years(args.year)
    output_dir = args.output or "."
    results = [hkex.download_single(args.stock, y, args.type, output_dir) for y in years]
    succeeded = [r for r in results if r.get("success")]
    print(json.dumps({
        "success": len(succeeded) > 0, "stock": args.stock, "market": "hk",
        "total": len(results), "downloaded": len(succeeded),
        "failed": len(results) - len(succeeded), "results": results,
    }, ensure_ascii=False, indent=2))


def cmd_download(args):
    """搜索并下载 PDF。"""
    market = detect_market(args.stock, getattr(args, "market", "auto"))
    if market == "hk":
        return cmd_download_hk(args)

    output_dir = args.output or "."
    doc_type = args.type

    # 招股书：无需年份，单次下载
    if doc_type == "prospectus":
        res = download_prospectus(args.stock, output_dir, timeout=args.timeout)
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return

    # 研报：无需年份，落地 CSV + Markdown 清单（东方财富）
    if doc_type == "research":
        res = download_research(args.stock, output_dir, timeout=args.timeout)
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return

    if doc_type == "interim":
        print(json.dumps({
            "success": False, "market": "a", "stock": args.stock,
            "error": "A股用 semi(半年报)；interim 是港股中期报告，港股请加 -m hk 或用港股代码",
        }, ensure_ascii=False, indent=2))
        return

    # 财报类：需要年份
    if not args.year:
        print(json.dumps({
            "success": False, "stock": args.stock,
            "error": "财报下载需要 --year（招股书用 --type prospectus，无需年份）",
        }, ensure_ascii=False, indent=2))
        return

    years = parse_years(args.year)
    type_keys = list(REPORT_TYPES.keys()) if doc_type == "all" else [doc_type]

    tasks = [{"year": y, "type": t} for y in years for t in type_keys]
    results = [
        download_report(args.stock, t["year"], t["type"], output_dir, args.timeout)
        for t in tasks
    ]

    succeeded = [r for r in results if r["success"]]
    failed = [r for r in results if not r["success"]]
    output = {
        "success": len(succeeded) > 0,
        "stock": args.stock,
        "total": len(tasks),
        "downloaded": len(succeeded),
        "failed": len(failed),
        "results": results,
    }
    if failed:
        output["failed_details"] = [
            {"year": r.get("year"), "type": r.get("report_type", ""), "error": r.get("error", "")}
            for r in failed
        ]
    print(json.dumps(output, ensure_ascii=False, indent=2))


# ─── 参数解析 ────────────────────────────────────────────────

def _add_common_args(parser):
    parser.add_argument("--stock", "-s", type=str, required=True,
                        help="股票名称或代码（如 '三一重工' / '600031' / '01888'）")
    parser.add_argument("--year", "-y", type=str, default=None,
                        help="年份：单年(2025)/范围(2023-2025)/枚举(2023,2025)。财报必填，招股书无需")
    parser.add_argument("--type", "-t", type=str,
                        choices=list(REPORT_TYPES.keys()) + ["prospectus", "research", "all", "interim"],
                        default="annual",
                        help="文档类型: annual/semi/q1/q3（财报）/ prospectus（招股书）/ research（券商研报，无需年份）/ all / interim(港股中期)，默认 annual")
    parser.add_argument("--market", "-m", type=str, choices=["auto", "a", "hk"], default="auto",
                        help="市场: auto(自动判断) / a(A股) / hk(港股)，默认 auto")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT,
                        help=f"请求超时秒数（默认: {DEFAULT_TIMEOUT}）")


def build_parser():
    parser = argparse.ArgumentParser(
        description="信息披露与研报下载 - 财报 + 招股书（巨潮/HKEX）+ 券商研报清单（东方财富）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用示例:
  # 下载年报（默认）
  python3 scripts/cli.py download --stock "三一重工" --year 2025 -o /tmp/

  # 下载半年报 / 一季报 / 三季报
  python3 scripts/cli.py download --stock "三一重工" --year 2025 --type semi -o /tmp/
  python3 scripts/cli.py download --stock "徐工机械" --year 2025 --type q1 -o /tmp/

  # 下载招股说明书（无需 --year）
  python3 scripts/cli.py download --stock "宁德时代" --type prospectus -o /tmp/
  python3 scripts/cli.py download --stock 300750 --type prospectus -o /tmp/

  # 下载券商研报清单（无需 --year，东方财富）
  python3 scripts/cli.py download --stock "艾力斯" --type research -o /tmp/
  python3 scripts/cli.py download --stock 688578 --type research -o /tmp/

  # 搜索招股书公告
  python3 scripts/cli.py search --stock "宁德时代" --type prospectus

  # 批量下载多年财报
  python3 scripts/cli.py download --stock "三一重工" --year 2023-2025 -o /tmp/

  # 港股财报
  python3 scripts/cli.py download --stock 01888 --year 2025 -m hk -o /tmp/

文档类型:
  annual      年报（默认）
  semi        半年报
  q1          一季报
  q3          三季报
  prospectus  首发招股说明书（无需年份）
  research    券商研报清单（无需年份，东方财富）
  all         全部财报类型
  interim     港股中期报告

数据来源: 巨潮资讯网 (cninfo.com.cn) / HKEX 披露易 (hkexnews.hk)
        """,
    )
    subparsers = parser.add_subparsers(dest="command", help="子命令")

    sp = subparsers.add_parser("search", help="搜索公告列表")
    _add_common_args(sp)

    dp = subparsers.add_parser("download", help="搜索并下载 PDF")
    _add_common_args(dp)
    dp.add_argument("--output", "-o", type=str, default=".", help="下载目录（默认: 当前目录）")

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(0)

    if args.command == "search":
        cmd_search(args)
    elif args.command == "download":
        cmd_download(args)


if __name__ == "__main__":
    main()
