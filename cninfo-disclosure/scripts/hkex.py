#!/usr/bin/env python3
"""
港股财报下载 - 通过港交所披露易(HKEX)搜索并下载

HKEX 披露易是 JSF + 反爬站点，纯 HTTP 无法搜索，
因此本模块用 Playwright(浏览器自动化)驱动搜索页：
  输入股票代码 → 选自动补全 → 搜索 → 解析结果链接 → 按标题筛年报/中期报告 → 下 PDF。

依赖：pip install playwright   （用系统 Chrome，无需 playwright install）
港股只有「年报」和「中期报告」，没有一季报/三季报。

用法：
  python3 scripts/hkex.py download --stock 01888 --year 2025 --type annual -o /tmp/
  python3 scripts/hkex.py download --stock 01888 --year 2025 --type interim -o /tmp/
"""

import argparse
import os
import re
import sys
import urllib.request

HKEX_SEARCH_URL = "https://www1.hkexnews.hk/search/titlesearch.xhtml"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36")

# 港股报告类型：标题关键词 + 文件名后缀
HK_REPORT_TYPES = {
    "annual": {"label": "年报", "keywords": ["ANNUAL REPORT"], "suffix": "年年报"},
    "interim": {"label": "中期报告", "keywords": ["INTERIM REPORT", "INTERIM RESULTS"], "suffix": "年中期报告"},
}

# 标题里出现这些词的不是正本（年报/中期报告本身），排除
EXCLUDE_TITLE = ["ESG", "RESULTS ANNOUNCEMENT", "GENERAL MEETING", "PROXY",
                 "DIVIDEND", "NOTICE", "CIRCULAR", "MONTHLY RETURN", "DISCLOSURE",
                 "PLACING", "POLL", "BOARD MEETING", "PROFIT ALERT"]


def _clean_name(text: str) -> str:
    """从自动补全建议文本(如 '01888 KB LAMINATES')提取公司名。"""
    parts = re.split(r"\s+", text.strip())
    # 去掉开头的股票代码
    name_parts = [p for p in parts if not re.fullmatch(r"\d{4,6}", p)]
    name = " ".join(name_parts).strip()
    return name or (parts[0] if parts else "stock")


def search_filings(stock: str, year: int = None, report_type: str = None, headless: bool = True):
    """
    用 Playwright 在 HKEX 搜索某股票的公告，返回 [{title, url}, ...]。
    传 year+report_type 时会限定日期范围（年报次年发布、中期报告当年发布），
    确保目标报告落在返回结果内（HKEX 默认只返回最近约 100 条）。
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise RuntimeError(
            "港股下载需要 Playwright。请安装：pip install playwright "
            "（使用系统 Chrome，无需 playwright install chromium）"
        )

    filings = []
    stock_name = stock
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(channel="chrome", headless=headless)
        except Exception:
            browser = p.chromium.launch(headless=headless)
        ctx = browser.new_context(user_agent=UA)
        page = ctx.new_page()
        page.goto(HKEX_SEARCH_URL, timeout=60000)
        page.wait_for_timeout(2500)

        # 输入股票代码/名称，触发自动补全(prefix.do)
        si = page.locator("#searchStockCode")
        si.click()
        si.type(stock, delay=100)
        page.wait_for_timeout(2500)

        # 点第一个补全建议
        sug = page.locator(".autocomplete-suggestion").first
        try:
            stock_name = sug.inner_text()
            sug.click()
        except Exception as e:
            browser.close()
            raise RuntimeError(f"未在 HKEX 找到股票 {stock}（自动补全无结果）: {e}")
        page.wait_for_timeout(1000)

        # 限定日期范围：年报次年发布、中期报告当年发布
        if year:
            if report_type == "interim":
                s, e = f"{year}-01-01", f"{year}-12-31"
            else:  # annual 次年发布
                s, e = f"{year + 1}-01-01", f"{year + 1}-12-31"
            try:
                page.evaluate(
                    """([s, e]) => {
                        const setHid = (id, v) => { const el = document.getElementById(id); if (el) el.value = v; };
                        setHid('startDate', s.replace(/-/g, ''));
                        setHid('endDate', e.replace(/-/g, ''));
                        document.querySelectorAll('.searchDateFrom').forEach(el => el.value = s);
                        document.querySelectorAll('.searchDateTo').forEach(el => el.value = e);
                    }""",
                    [s, e],
                )
                page.wait_for_timeout(500)
            except Exception:
                pass

        # 搜索（不限文档类型，拿该日期范围内全部公告）
        page.locator(".filter__btn-applyFilters-js").first.click(timeout=8000)
        page.wait_for_timeout(8000)

        # 解析结果：取所有 listconews PDF 链接
        links = page.eval_on_selector_all(
            "a",
            "els=>els.map(e=>({t:(e.innerText||'').trim(),h:e.href}))"
            ".filter(x=>x.h.includes('listconews') && x.h.toLowerCase().endsWith('.pdf'))",
        )
        browser.close()

    filings = [{"title": x["t"], "url": x["h"]} for x in links]
    return filings, _clean_name(stock_name)


def pick_report(filings, report_type, year):
    """从公告列表里筛出指定类型+年份的报告正本。"""
    rt = HK_REPORT_TYPES[report_type]
    kws = rt["keywords"]
    year_str = str(year)

    def is_excluded(title):
        tu = title.upper()
        return any(k in tu for k in EXCLUDE_TITLE)

    cands = []
    for f in filings:
        tu = f["title"].upper()
        if not any(k in tu for k in kws):
            continue
        if year_str not in f["url"] and year_str not in f["title"]:
            continue
        if is_excluded(f["title"]):
            continue
        cands.append(f)

    if not cands:
        # 放宽：只要标题含关键词 + 年份
        cands = [f for f in filings
                 if any(k in f["title"].upper() for k in kws)
                 and (year_str in f["url"] or year_str in f["title"])]

    if not cands:
        return None
    # 优先标题最短（正本通常最短，如 "ANNUAL REPORT 2025"）
    cands.sort(key=lambda f: len(f["title"]))
    return cands[0]


def download_pdf(pdf_url, output_dir, filename, timeout=60):
    os.makedirs(output_dir, exist_ok=True)
    filepath = os.path.join(output_dir, filename)
    req = urllib.request.Request(pdf_url, headers={"User-Agent": UA, "Referer": "https://www1.hkexnews.hk/"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read()
    if data[:5] != b"%PDF-":
        raise RuntimeError(f"下载的不是合法 PDF（文件头 {data[:20]!r}）")
    with open(filepath, "wb") as f:
        f.write(data)
    size = len(data)
    size_disp = f"{size/1024/1024:.1f}MB" if size >= 1024*1024 else f"{size/1024:.1f}KB"
    return {"filepath": filepath, "size_display": size_disp}


def download_single(stock, year, report_type, output_dir, headless=True):
    rt = HK_REPORT_TYPES[report_type]
    try:
        filings, stock_name = search_filings(stock, year=year, report_type=report_type, headless=headless)
    except RuntimeError as e:
        return {"success": False, "stock": stock, "year": year,
                "report_type": report_type, "report_label": rt["label"], "error": str(e)}

    if not filings:
        return {"success": False, "stock": stock, "year": year,
                "report_type": report_type, "report_label": rt["label"],
                "error": f"HKEX 未返回 {stock} 的公告"}

    target = pick_report(filings, report_type, year)
    if not target:
        return {"success": False, "stock": stock, "year": year,
                "report_type": report_type, "report_label": rt["label"],
                "error": f"未找到 {stock_name} {year} {rt['label']}"}

    filename = f"{stock_name}_{year}{rt['suffix']}.pdf"
    filename = re.sub(r'[\\/:*?"<>|]', "_", filename)
    try:
        r = download_pdf(target["url"], output_dir, filename)
    except Exception as e:
        return {"success": False, "stock": stock, "year": year,
                "report_type": report_type, "report_label": rt["label"], "error": str(e)}

    return {"success": True, "stock": stock_name, "market": "hk", "year": year,
            "report_type": report_type, "report_label": rt["label"],
            "title": target["title"], "pdf_url": target["url"],
            "downloaded": r["filepath"], "file_size": r["size_display"]}


def main():
    ap = argparse.ArgumentParser(description="港股财报下载(HKEX 披露易)")
    sub = ap.add_subparsers(dest="command")
    dp = sub.add_parser("download", help="搜索并下载港股财报 PDF")
    dp.add_argument("--stock", "-s", required=True, help="港股代码或名称(如 01888)")
    dp.add_argument("--year", "-y", required=True, help="年份")
    dp.add_argument("--type", "-t", choices=list(HK_REPORT_TYPES.keys()), default="annual",
                    help="annual(年报) / interim(中期报告)，默认 annual")
    dp.add_argument("--output", "-o", default=".", help="下载目录")
    dp.add_argument("--headed", action="store_true", help="显示浏览器(调试用)")
    args = ap.parse_args()

    if not args.command:
        ap.print_help(); sys.exit(0)

    import json
    res = download_single(args.stock, int(args.year), args.type, args.output, headless=not args.headed)
    print(json.dumps(res, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
