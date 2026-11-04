#!/usr/bin/env python3
"""
券商研报模块 · 东方财富

支持 A 股个股「券商研究报告（卖方研报）」清单的拉取与落地。
数据源：东方财富研报中心 reportapi.eastmoney.com（非巨潮，独立实现 HTTP 调用）。
复用 cninfo_client 底座的 safe_filename / DEFAULT_TIMEOUT（纯标准库，零依赖）。

与财报 / 招股书的关键差异：
  - 数据源是东方财富研报中心，而非巨潮公告全文检索
  - 研报是券商持续产出的研报流，无"年份"概念 → 全时段分页拉取
  - 输出是「研报清单」（CSV + Markdown），含评级 / 分析师 / 盈利预测 / PDF 直链，
    而非单个 PDF。每篇研报的 PDF 直链（https://pdf.dfcfw.com/pdf/H3_{infoCode}_1.pdf）
    已列入清单，可按需手动下载。

用法（一般通过 cli.py 调用）：
  from research import search_research, download_research
  search_research("688578")           # 返回研报列表
  download_research("688578", "/tmp/") # 落地 CSV + Markdown
"""

import csv
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter

from cninfo_client import DEFAULT_TIMEOUT, DownloadError, safe_filename


# ─── 常量 ───────────────────────────────────────────────────

EM_REPORT_URL = "https://reportapi.eastmoney.com/report/list"

EM_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/137.0.0.0 Safari/537.36"
    ),
    "Referer": "https://data.eastmoney.com/",
    "Accept": "application/json, text/javascript, */*; q=0.01",
}

EM_PAGE_SIZE = 5000  # 单页拉满；个股研报通常不足 5000 篇，一般一页搞定

# 清单列（CSV 表头 + Markdown 表头）
COLUMNS = [
    ("date", "日期"),
    ("org", "机构"),
    ("analyst", "分析师"),
    ("rating", "评级"),
    ("title", "报告标题"),
    ("industry", "行业"),
    ("eps_this", "今年EPS"),
    ("pe_this", "今年PE"),
    ("eps_next", "次年EPS"),
    ("pe_next", "次年PE"),
    ("pdf_url", "PDF"),
]


# ─── 东方财富 API 调用 ──────────────────────────────────────

def _fetch_page(stock, page, page_size=EM_PAGE_SIZE, timeout=DEFAULT_TIMEOUT):
    """调用东方财富研报 API 拉取一页，返回原始 JSON dict。"""
    params = {
        "industryCode": "*",
        "pageSize": str(page_size),
        "industry": "*",
        "rating": "*",
        "ratingChange": "*",
        "beginTime": "2000-01-01",
        "endTime": "2099-12-31",
        "pageNo": str(page),
        "fields": "",
        "qType": "0",
        "orgCode": "",
        "code": stock,
        "rcode": "",
        "p": str(page),
        "pageNum": str(page),
        "pageNumber": str(page),
    }
    qs = urllib.parse.urlencode(params)
    req = urllib.request.Request(f"{EM_REPORT_URL}?{qs}", headers=EM_HEADERS, method="GET")

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise DownloadError(f"东方财富研报 API 请求失败: HTTP {e.code} {e.reason}", e.code)
    except urllib.error.URLError as e:
        raise DownloadError(f"网络错误: {e.reason}")
    except json.JSONDecodeError:
        raise DownloadError("东方财富 API 返回了非 JSON 格式的响应")


def _normalize(rec):
    """把原始记录规整为清单字段 dict。"""
    info_code = rec.get("infoCode") or ""
    analyst = rec.get("researcher") or rec.get("author") or ""
    return {
        "date": (rec.get("publishDate") or "")[:10],
        "org": rec.get("orgSName") or "",
        "analyst": analyst,
        "rating": rec.get("emRatingName") or "",
        "title": (rec.get("title") or "").strip(),
        "industry": rec.get("indvInduName") or "",
        "eps_this": rec.get("predictThisYearEps") or "",
        "pe_this": rec.get("predictThisYearPe") or "",
        "eps_next": rec.get("predictNextYearEps") or "",
        "pe_next": rec.get("predictNextYearPe") or "",
        "pdf_url": f"https://pdf.dfcfw.com/pdf/H3_{info_code}_1.pdf" if info_code else "",
        "stock_name": rec.get("stockName") or "",
        "stock_code": rec.get("stockCode") or "",
    }


def fetch_research(stock, timeout=DEFAULT_TIMEOUT):
    """
    拉取某股票的全部券商研报（全时段，自动分页）。

    返回 (rows, current_year)：
      rows         : 规整后的研报列表，按日期降序
      current_year : 东方财富返回的"当前年"（用于盈利预测列含义，可忽略）
    """
    first = _fetch_page(stock, 1, timeout=timeout)
    total_page = first.get("TotalPage", 0) or 0
    records = list(first.get("data") or [])

    for page in range(2, total_page + 1):
        try:
            records.extend(_fetch_page(stock, page, timeout=timeout).get("data") or [])
        except DownloadError:
            continue  # 单页失败不中断整体

    rows = [_normalize(rec) for rec in records]
    rows.sort(key=lambda x: x["date"], reverse=True)
    return rows, first.get("currentYear")


# ─── 对外接口 ───────────────────────────────────────────────

def search_research(stock, timeout=DEFAULT_TIMEOUT):
    """
    搜索某股票的券商研报，返回精简列表（供 cli search 输出 JSON）。
    每项含 title / date / org / rating / analyst / pdf_url。
    """
    rows, _ = fetch_research(stock, timeout=timeout)
    return [
        {
            "title": r["title"],
            "date": r["date"],
            "org": r["org"],
            "rating": r["rating"],
            "analyst": r["analyst"],
            "pdf_url": r["pdf_url"],
        }
        for r in rows
    ]


def download_research(stock, output_dir, timeout=DEFAULT_TIMEOUT):
    """
    拉取全部研报并落地 CSV + Markdown 清单。返回结果 dict（含 success 字段），不抛异常。
    """
    try:
        rows, _ = fetch_research(stock, timeout=timeout)

        if not rows:
            return {
                "success": False,
                "stock": stock,
                "doc_type": "research",
                "error": (
                    f"未在东方财富找到 {stock} 的券商研报。"
                    "可能是冷门标的暂无券商覆盖，建议到 data.eastmoney.com/report 核实。"
                ),
            }

        os.makedirs(output_dir, exist_ok=True)

        # 文件名优先用 API 返回的规范股票简称（如传入代码 688578 → "艾力斯"）
        stock_name = rows[0]["stock_name"] or stock
        stock_code = rows[0]["stock_code"] or stock
        base = safe_filename(stock_name)

        csv_path = os.path.join(output_dir, f"{base}_券商研报清单.csv")
        md_path = os.path.join(output_dir, f"{base}_券商研报清单.md")

        summary = _summarize(rows)
        _write_csv(rows, csv_path)
        _write_markdown(rows, md_path, stock_name, stock_code, summary)

        return {
            "success": True,
            "stock": stock,
            "doc_type": "research",
            "report_label": "券商研报",
            "stock_name": stock_name,
            "stock_code": stock_code,
            "total": len(rows),
            "date_span": summary["date_span"],
            "org_count": summary["org_count"],
            "analyst_count": summary["analyst_count"],
            "csv": csv_path,
            "markdown": md_path,
        }

    except DownloadError as e:
        return {
            "success": False,
            "stock": stock,
            "doc_type": "research",
            "error": e.message,
        }


# ─── 清单生成 ───────────────────────────────────────────────

def _summarize(rows):
    """统计：时间跨度 / 机构数 / 分析师数 / 评级分布 / 机构 TOP。"""
    orgs = Counter(r["org"] for r in rows if r["org"])
    ratings = Counter(r["rating"] for r in rows if r["rating"])
    analysts = Counter(r["analyst"] for r in rows if r["analyst"])
    dates = [r["date"] for r in rows if r["date"]]
    span = f"{min(dates)} ~ {max(dates)}" if dates else ""
    return {
        "date_span": span,
        "org_count": len(orgs),
        "analyst_count": len(analysts),
        "ratings": ratings,
        "orgs": orgs,
    }


def _write_csv(rows, path):
    """落地 CSV（utf-8-sig，Excel 友好）。"""
    headers = [label for _, label in COLUMNS]
    keys = [key for key, _ in COLUMNS]
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        for r in rows:
            writer.writerow([r.get(k, "") for k in keys])


def _write_markdown(rows, md_path, stock_name, stock_code, summary):
    """落地 Markdown 清单（含统计 + 最新 N 篇表格）。"""
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(f"# {stock_name}（{stock_code}）券商研报清单\n\n")
        f.write("- 数据来源：东方财富 [reportapi.eastmoney.com](https://data.eastmoney.com/report/stock.jshtml)\n")
        f.write(f"- 研报总数：**{len(rows)}** 篇（{summary['date_span']}）\n")
        f.write(f"- 覆盖机构：**{summary['org_count']}** 家　分析师：**{summary['analyst_count']}** 位\n\n")

        f.write("## 评级分布\n\n")
        f.write("| 评级 | 篇数 |\n|---|---|\n")
        for k, v in summary["ratings"].most_common():
            f.write(f"| {k} | {v} |\n")

        f.write("\n## 覆盖机构 TOP 15\n\n")
        f.write("| 机构 | 篇数 |\n|---|---|\n")
        for k, v in summary["orgs"].most_common(15):
            f.write(f"| {k} | {v} |\n")

        f.write("\n## 最新 30 篇研报\n\n")
        f.write("| 日期 | 机构 | 评级 | 分析师 | 报告标题 | PDF |\n|---|---|---|---|---|---|\n")
        for r in rows[:30]:
            pdf = f"[PDF]({r['pdf_url']})" if r["pdf_url"] else ""
            title = r["title"].replace("|", "\\|")
            analyst = r["analyst"].replace("|", "\\|")
            f.write(f"| {r['date']} | {r['org']} | {r['rating']} | {analyst} | {title} | {pdf} |\n")

        f.write(f"\n> 完整 {len(rows)} 篇清单见同目录 CSV 文件。\n")
