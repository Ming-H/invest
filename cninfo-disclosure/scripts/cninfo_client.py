#!/usr/bin/env python3
"""
巨潮资讯网（cninfo）公告查询与 PDF 下载 · 共享底座

本模块是 cninfo-disclosure skill 的底座，供 reports.py（财报）与
prospectus.py（招股书）复用：
  - query_announcements / query_with_stock : 巨潮公告全文查询 API
  - download_pdf                            : PDF 下载（带 %PDF- 头校验）
  - detect_column / detect_market           : 交易所 / 市场判断
  - extract_company_name                    : 从公告标题提取公司简称
  - parse_years                             : 年份参数解析（财报用）
  - DownloadError                           : 统一异常

纯 Python3 标准库，无第三方依赖。
数据来源：巨潮资讯网 http://www.cninfo.com.cn （证监会指定信息披露平台）
"""

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from typing import List, Optional


# ─── 常量 ───────────────────────────────────────────────────

SKILL_NAME = "cninfo-disclosure"
SKILL_VERSION = "3.1.0"

CNINFO_QUERY_URL = "http://www.cninfo.com.cn/new/hisAnnouncement/query"
CNINFO_PDF_BASE = "https://static.cninfo.com.cn/"

DEFAULT_TIMEOUT = 30
DEFAULT_PAGE_SIZE = 30  # 招股书 / 多年财报场景调大，单年报 15 足够

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/137.0.0.0 Safari/537.36"
)

# 公告查询用的请求头（巨潮要求 X-Requested-With + Referer，否则被反爬拦截）
QUERY_HEADERS = {
    "User-Agent": USER_AGENT,
    "Referer": "http://www.cninfo.com.cn/new/disclosure/stock",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "X-Requested-With": "XMLHttpRequest",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
}

# PDF 下载用的请求头
DOWNLOAD_HEADERS = {
    "User-Agent": USER_AGENT,
    "Referer": "http://www.cninfo.com.cn/",
}


class DownloadError(Exception):
    """巨潮查询 / 下载过程中的错误。"""
    def __init__(self, message: str, status_code: int = None):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


# ─── 通用工具 ───────────────────────────────────────────────

def detect_column(stock: str) -> Optional[str]:
    """
    根据股票代码判断交易所。
    6 开头 → sse（上交所），0/3 开头 → szse（深交所）
    非数字（公司名称）返回 None，后续会两个交易所都试。
    """
    code = stock.strip()
    if re.match(r"^\d{6}$", code):
        return "sse" if code.startswith("6") else "szse"
    return None


def detect_market(stock: str, market: str = "auto") -> str:
    """
    判断市场：A股(cninfo) 还是 港股(HKEX)。
    显式 --market a/hk 优先；自动：6位代码→A股，4-5位代码→港股，名称默认 A股。
    """
    code = stock.strip()
    if market == "hk":
        return "hk"
    if market == "a":
        return "a"
    if re.fullmatch(r"\d{6}", code):
        return "a"
    if re.fullmatch(r"\d{4,5}", code):
        return "hk"
    return "a"  # 名称默认按 A 股，港股请用代码或 -m hk


def extract_company_name(title: str) -> str:
    """
    从公告标题中提取公司简称。
    优先匹配"股份有限公司"，其次"有限公司"，最后尝试截取到"集团"。
    """
    patterns = [
        r"(.+?)股份有限公司",
        r"(.+?)有限公司",
        r"(.+?)集团",
    ]
    for pattern in patterns:
        m = re.match(pattern, title)
        if m:
            return m.group(1)

    m = re.match(r"(.+?)(?:\d{4}年|,|，)", title)
    if m:
        return m.group(1)

    return title[:6]


def parse_years(years_str: str) -> List[int]:
    """
    解析年份参数，支持：
    - 单年：2025
    - 范围：2023-2025
    - 枚举：2023,2024,2025
    """
    if "-" in years_str:
        parts = years_str.split("-")
        if len(parts) == 2:
            start, end = int(parts[0]), int(parts[1])
            if start > end:
                start, end = end, start
            return list(range(start, end + 1))
    elif "," in years_str:
        return [int(y.strip()) for y in years_str.split(",")]
    else:
        return [int(years_str)]


# ─── 巨潮公告查询 ───────────────────────────────────────────

def query_announcements(
    searchkey: str,
    column: str,
    start_date: str,
    end_date: str,
    category: str = "",
    stock: str = "",
    page_size: int = DEFAULT_PAGE_SIZE,
    timeout: int = DEFAULT_TIMEOUT,
) -> list:
    """
    调用巨潮公告查询 API，搜索公告。

    参数：
      searchkey  : 全文搜索关键词（如 "三一重工 年度报告"）
      column     : 交易所（sse / szse）
      start_date : 起始日期 YYYY-MM-DD
      end_date   : 结束日期 YYYY-MM-DD
      category   : 公告类别代码（如年报 category_ndbg_szsh）；空表示不限
      stock      : 巨潮 stock 参数（代码,orgId 形式），一般留空用 searchkey 即可
      page_size  : 每页条数

    返回：[{title, date, adjunct_url, pdf_url}, ...]
    """
    data = urllib.parse.urlencode({
        "pageNum": "1",
        "pageSize": str(page_size),
        "column": column,
        "tabName": "fulltext",
        "plate": "",
        "stock": stock,
        "searchkey": searchkey,
        "secid": "",
        "category": category,
        "trade": "",
        "seach": "",
        "startDate": start_date,
        "endDate": end_date,
        "isFuzzy": "true",
        "sortName": "",
        "sortOrder": "",
    }).encode("utf-8")

    req = urllib.request.Request(
        CNINFO_QUERY_URL, data=data, headers=QUERY_HEADERS, method="POST"
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            result = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise DownloadError(f"巨潮 API 请求失败: HTTP {e.code} {e.reason}", e.code)
    except urllib.error.URLError as e:
        raise DownloadError(f"网络错误: {e.reason}")
    except json.JSONDecodeError:
        raise DownloadError("巨潮 API 返回了非 JSON 格式的响应")

    announcements = result.get("announcements") or []

    parsed = []
    for ann in announcements:
        title = ann.get("announcementTitle", "")
        ts = ann.get("announcementTime", 0)
        adjunct_url = ann.get("adjunctUrl", "")

        if not title or not adjunct_url:
            continue

        date_str = ""
        if ts:
            try:
                date_str = datetime.fromtimestamp(ts / 1000).strftime("%Y-%m-%d")
            except (ValueError, OSError):
                date_str = str(ts)

        parsed.append({
            "title": title,
            "date": date_str,
            "adjunct_url": adjunct_url,
            "pdf_url": CNINFO_PDF_BASE + adjunct_url,
        })

    return parsed


def query_with_stock(
    stock: str,
    searchkey: str,
    start_date: str,
    end_date: str,
    category: str = "",
    timeout: int = DEFAULT_TIMEOUT,
) -> list:
    """
    搜索公告。能确定交易所（6位代码）则只查该所；
    否则（公司名称）sse 与 szse 都查，按 pdf_url 去重合并。
    """
    column = detect_column(stock)

    if column:
        return query_announcements(
            searchkey, column, start_date, end_date,
            category=category, timeout=timeout,
        )

    seen = set()
    merged = []
    for col in ["sse", "szse"]:
        try:
            results = query_announcements(
                searchkey, col, start_date, end_date,
                category=category, timeout=timeout,
            )
        except DownloadError:
            continue
        for r in results:
            if r["pdf_url"] not in seen:
                seen.add(r["pdf_url"])
                merged.append(r)
    return merged


# ─── PDF 下载 ───────────────────────────────────────────────

def download_pdf(
    pdf_url: str,
    output_dir: str,
    filename: str,
    timeout: int = DEFAULT_TIMEOUT,
) -> dict:
    """下载 PDF 文件到本地，校验 %PDF- 头。"""
    import os
    os.makedirs(output_dir, exist_ok=True)
    filepath = os.path.join(output_dir, filename)

    req = urllib.request.Request(pdf_url, headers=DOWNLOAD_HEADERS, method="GET")

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
    except urllib.error.HTTPError as e:
        raise DownloadError(f"PDF 下载失败: HTTP {e.code} {e.reason}", e.code)
    except urllib.error.URLError as e:
        raise DownloadError(f"PDF 下载网络错误: {e.reason}")

    if not data[:5] == b"%PDF-":
        raise DownloadError(
            f"下载的文件不是合法 PDF（文件头: {data[:20]}），可能是反爬拦截。"
        )

    with open(filepath, "wb") as f:
        f.write(data)

    size_bytes = len(data)
    if size_bytes >= 1024 * 1024:
        size_display = f"{size_bytes / (1024 * 1024):.1f}MB"
    else:
        size_display = f"{size_bytes / 1024:.1f}KB"

    return {
        "filepath": filepath,
        "size_bytes": size_bytes,
        "size_display": size_display,
    }


def safe_filename(filename: str) -> str:
    """把文件名中的非法字符替换为下划线。"""
    return re.sub(r'[\\/:*?"<>|]', "_", filename)
