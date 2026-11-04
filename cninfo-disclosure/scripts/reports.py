#!/usr/bin/env python3
"""
财报（定期报告）模块 · 巨潮

支持年报 / 半年报 / 一季报 / 三季报的搜索与下载。
复用 cninfo_client 底座（巨潮公告查询 + PDF 下载）。

用法（一般通过 cli.py 调用）：
  from reports import search_reports, download_report
  search_reports("三一重工", 2025, "annual")
  download_report("三一重工", 2025, "annual", "/tmp/")
"""

import re

from cninfo_client import (
    DEFAULT_TIMEOUT,
    DownloadError,
    download_pdf,
    extract_company_name,
    query_with_stock,
    safe_filename,
)


# 报告类型配置
# category 是巨潮的公告类别代码；date_range 给出该报告的披露时间窗（用于缩小搜索范围）
REPORT_TYPES = {
    "annual": {
        "label": "年报",
        "category": "category_ndbg_szsh",
        "search_key": "年度报告",
        "filename_suffix": "年年度报告",
        "include_keyword": "年度报告",
        "date_range": lambda y: (f"{y + 1}-01-01", f"{y + 1}-06-30"),
    },
    "semi": {
        "label": "半年报",
        "category": "category_bndbg_szsh",
        "search_key": "半年度报告",
        "filename_suffix": "年半年度报告",
        "include_keyword": "半年度报告",
        "date_range": lambda y: (f"{y}-07-01", f"{y}-12-31"),
    },
    "q1": {
        "label": "一季报",
        "category": "category_yjdbg_szsh",
        # 用"一季度"而非"第一季度报告"："一季度"是"第一季度"的子串，
        # 可同时命中"2026年第一季度报告"与"2026年一季度报告"两种标题写法
        "search_key": "一季度",
        "filename_suffix": "年第一季度报告",
        "include_keyword": "一季度",
        "date_range": lambda y: (f"{y}-03-01", f"{y}-06-30"),
    },
    "q3": {
        "label": "三季报",
        "category": "category_sjdbg_szsh",
        "search_key": "第三季度报告",
        "filename_suffix": "年第三季度报告",
        "include_keyword": "第三季度",
        "date_range": lambda y: (f"{y}-09-01", f"{y}-12-31"),
    },
}

# 排除关键词（摘要、更正、补充等）
EXCLUDE_KEYWORDS = ["摘要", "更正", "补充", "修改", "英文", "已取消"]


def search_reports(
    stock: str,
    year: int,
    report_type: str = "annual",
    timeout: int = DEFAULT_TIMEOUT,
) -> list:
    """搜索指定公司的某类定期报告公告。"""
    rt = REPORT_TYPES[report_type]
    start_date, end_date = rt["date_range"](year)
    searchkey = f"{stock} {rt['search_key']}"
    announcements = query_with_stock(
        stock, searchkey, start_date, end_date,
        category=rt["category"], timeout=timeout,
    )
    # 客户端按年份过滤：标题中必须包含目标年份
    return [a for a in announcements if str(year) in a["title"]]


def filter_report(announcements: list, report_type: str = "annual"):
    """从公告列表中筛选出报告正本（排除摘要、更正等）。"""
    rt = REPORT_TYPES[report_type]
    include_kw = rt["include_keyword"]

    candidates = []
    for ann in announcements:
        title = ann["title"]
        if include_kw not in title:
            continue
        if any(kw in title for kw in EXCLUDE_KEYWORDS):
            continue
        candidates.append(ann)

    if not candidates:
        # 放宽条件：只排除"摘要"
        for ann in announcements:
            title = ann["title"]
            if include_kw in title and "摘要" not in title:
                candidates.append(ann)

    if not candidates:
        return None

    # 优先选标题最短的（正本标题通常最短）
    candidates.sort(key=lambda x: len(x["title"]))
    return candidates[0]


def download_report(
    stock: str,
    year: int,
    report_type: str,
    output_dir: str,
    timeout: int = DEFAULT_TIMEOUT,
) -> dict:
    """
    下载单个报告。返回结果 dict（含 success 字段），不抛异常。
    """
    rt = REPORT_TYPES[report_type]

    try:
        announcements = search_reports(stock, year, report_type=report_type, timeout=timeout)

        if not announcements:
            return {
                "success": False,
                "year": year,
                "report_type": report_type,
                "report_label": rt["label"],
                "error": f"未找到 {stock} {year} {rt['label']}",
            }

        target = filter_report(announcements, report_type=report_type)
        if not target:
            return {
                "success": False,
                "year": year,
                "report_type": report_type,
                "report_label": rt["label"],
                "error": "未找到正本（只有摘要或更正版）",
            }

        # 生成文件名：标题含公司名则提取；否则回退到 --stock
        stock_name = extract_company_name(target["title"])
        if (not stock_name) or stock_name[0].isdigit():
            if not re.match(r"^\d{6}$", stock):
                stock_name = stock
        filename = safe_filename(f"{stock_name}_{year}{rt['filename_suffix']}.pdf")

        result = download_pdf(
            pdf_url=target["pdf_url"],
            output_dir=output_dir,
            filename=filename,
            timeout=timeout,
        )

        return {
            "success": True,
            "year": year,
            "report_type": report_type,
            "report_label": rt["label"],
            "title": target["title"],
            "date": target["date"],
            "pdf_url": target["pdf_url"],
            "downloaded": result["filepath"],
            "file_size": result["size_display"],
        }

    except DownloadError as e:
        return {
            "success": False,
            "year": year,
            "report_type": report_type,
            "report_label": rt["label"],
            "error": e.message,
        }
