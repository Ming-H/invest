#!/usr/bin/env python3
"""
业绩预告模块 · 巨潮

下载 A 股上市公司的「业绩预告」（年度/半年度/季度/前三季 业绩预告，含预增/预减/预亏/扭亏等）。
复用 cninfo_client 底座。

与财报的差异：
  - 业绩预告是正式报告前的"预告性公告"（提前 1-2 个月），是早期景气信号
  - 巨潮以"公告"形式披露，标题含"业绩预告"（部分写成"业绩预增/预减/预亏公告"）
  - 一家公司每年可能有多份（年度预告 + 修正），按日期区分
  - 年份可选：不传 --year 取全部；传 --year 只下该年

用法（一般通过 cli.py 调用）：
  from forecast import search_forecast, download_forecast
  search_forecast("艾力斯")
  download_forecast("艾力斯", "/tmp/", year=2026)
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

FORECAST_START_DATE = "2000-01-01"
FORECAST_END_DATE = "2099-12-31"

# 业绩预告标题特征（"业绩预告"为主，兼容"业绩预增/预减/预亏/扭亏/续盈/续亏"等写法）
FORECAST_INCLUDE = ["业绩预告", "业绩预增", "业绩预减", "业绩预亏",
                    "业绩预盈", "业绩扭亏", "业绩续盈", "业绩续亏", "业绩修正"]
FORECAST_EXCLUDE = ["摘要", "英文", "English"]  # 不排除"修正/更正"（有意义）


def _is_forecast(title: str) -> bool:
    if any(kw in title for kw in FORECAST_EXCLUDE):
        return False
    return any(kw in title for kw in FORECAST_INCLUDE)


def _period_label(title: str):
    """从标题提取期间标签，如 '2026年半年度业绩预告' / '2024年年度业绩预告'。
    命名与财报一致（{全称}_{期间}.pdf）。"""
    m = re.search(r"(\d{4})年([^业]+?)业绩", title)
    if m:
        return f"{m.group(1)}年{m.group(2)}业绩预告"
    return None


def search_forecast(stock: str, year=None, timeout: int = DEFAULT_TIMEOUT) -> list:
    """搜索某公司的业绩预告公告。year 可选（按标题年份/公告日期过滤）。"""
    searchkey = f"{stock} 业绩预告"
    anns = query_with_stock(
        stock, searchkey,
        FORECAST_START_DATE, FORECAST_END_DATE,
        category="", timeout=timeout,
    )
    anns = [a for a in anns if _is_forecast(a["title"])]
    if year:
        y = str(year)
        anns = [a for a in anns if (y in a["title"]) or (a.get("date", "").startswith(y))]
    # 按日期降序（最新在前）
    anns.sort(key=lambda x: x.get("date", ""), reverse=True)
    return anns


def download_forecast(stock: str, output_dir: str, year=None,
                      timeout: int = DEFAULT_TIMEOUT) -> dict:
    """下载业绩预告（年份可选，不传取全部）。返回结果 dict。"""
    try:
        anns = search_forecast(stock, year=year, timeout=timeout)
        if not anns:
            return {
                "success": False, "stock": stock, "doc_type": "forecast",
                "year": year,
                "error": f"未在巨潮找到 {stock} 的业绩预告"
                         + (f"（{year}年）" if year else "")
                         + "。可能公司未披露或名称/年份有误。",
            }
        files = []
        for a in anns:
            # 命名与财报一致：{公司全称}_{YYYY年X业绩预告}.pdf
            stock_name = extract_company_name(a["title"]) or stock
            period = _period_label(a["title"]) or f"业绩预告_{a.get('date','').replace('-','')}"
            filename = safe_filename(f"{stock_name}_{period}.pdf")
            try:
                r = download_pdf(a["pdf_url"], output_dir, filename, timeout=timeout)
                files.append({"title": a["title"], "date": a["date"],
                              "path": r["filepath"], "size": r["size_display"]})
            except DownloadError as e:
                files.append({"title": a["title"], "date": a["date"], "error": e.message})
        ok = [f for f in files if "path" in f]
        return {
            "success": len(ok) > 0, "stock": stock, "doc_type": "forecast",
            "year": year, "total": len(anns), "downloaded": len(ok),
            "files": files,
        }
    except DownloadError as e:
        return {"success": False, "stock": stock, "doc_type": "forecast", "error": e.message}
