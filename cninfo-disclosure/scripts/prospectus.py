#!/usr/bin/env python3
"""
招股说明书模块 · 巨潮

支持 A 股上市公司「首次公开发行（IPO）招股说明书」的搜索与下载。
复用 cninfo_client 底座。

与财报的关键差异：
  - 招股书是上市时的一次性文件，无"年份"概念 → 全时段宽日期范围搜索
  - 筛选规则聚焦"首发招股书"：必含"招股说明书"+"首次公开发行"，
    排除 H 股 / 确认意见 / 意向书 / 摘要 / 过程稿等干扰项
  - 标题通常含板块信息（科创板/创业板/主板/北交所），可解析返回

v1 范围：巨潮已上市公司首发招股书（再融资招股书/配股/增发/可转债 → v2）。

用法（一般通过 cli.py 调用）：
  from prospectus import search_prospectus, download_prospectus
  search_prospectus("宁德时代")
  download_prospectus("宁德时代", "/tmp/")
"""

from cninfo_client import (
    DEFAULT_TIMEOUT,
    DownloadError,
    download_pdf,
    query_with_stock,
    safe_filename,
)


# 招股书用全时段宽日期范围（上市时点因公司而异，可能很早，如 2001 年的茅台）
PROSPECTUS_START_DATE = "1990-01-01"
PROSPECTUS_END_DATE = "2099-12-31"

# 首发招股书标题特征：A股IPO标准标题形如
#   "首次公开发行股票并在创业板上市招股说明书"
PROSPECTUS_INCLUDE = "招股说明书"
IPO_KEYWORDS = ("首次公开发行", "首次公开")

# 排除关键词：非首发 / 非A股 / 过程稿 / 干扰公告
PROSPECTUS_EXCLUDE = [
    "H股", "港股", "境外", "全球发售",          # 非A股
    "确认意见",                                  # 控股股东/实控人确认意见
    "意向书",                                    # 增发招股意向书
    "摘要", "英文", "English",                   # 摘要 / 英文版
    "撤销", "中止", "终止", "失效",              # 撤回 / 中止发行
    "附录", "附件",                              # 附录附件
    "申报稿", "上会稿", "注册稿", "报送稿",      # 过程稿（巨潮一般仅正式版，保险排除）
    "反馈意见",                                  # 审核反馈
    "配股", "增发", "可转换", "可转债", "公司债",  # 再融资（v2 单独支持）
]


def detect_board(title: str):
    """从标题识别上市板块。"""
    for board in ["科创板", "创业板", "北交所", "主板", "中小企业板"]:
        if board in title:
            return board
    return None


def search_prospectus(stock: str, timeout: int = DEFAULT_TIMEOUT) -> list:
    """搜索某公司的招股说明书公告（全时段）。"""
    searchkey = f"{stock} 招股说明书"
    return query_with_stock(
        stock, searchkey,
        PROSPECTUS_START_DATE, PROSPECTUS_END_DATE,
        category="", timeout=timeout,
    )


def filter_prospectus(announcements: list):
    """从公告列表中筛选首发招股书正本。"""
    # 第一轮：严格首发特征
    candidates = []
    for ann in announcements:
        title = ann["title"]
        if PROSPECTUS_INCLUDE not in title:
            continue
        if not any(kw in title for kw in IPO_KEYWORDS):
            continue
        if any(kw in title for kw in PROSPECTUS_EXCLUDE):
            continue
        candidates.append(ann)

    # 第二轮放宽：只要"招股说明书"且非明显干扰项（兼容老式标题/不同写法）
    if not candidates:
        loose_exclude = ["H股", "港股", "境外", "全球发售", "确认意见",
                         "意向书", "摘要", "英文", "English"]
        for ann in announcements:
            title = ann["title"]
            if PROSPECTUS_INCLUDE not in title:
                continue
            if any(kw in title for kw in loose_exclude):
                continue
            candidates.append(ann)

    if not candidates:
        return None

    # 多份时优先日期最早（首发正式版最早刊登），标题最短次之
    candidates.sort(key=lambda x: (x.get("date") or "9999", len(x["title"])))
    return candidates[0]


def download_prospectus(
    stock: str,
    output_dir: str,
    timeout: int = DEFAULT_TIMEOUT,
) -> dict:
    """
    下载首发招股说明书。返回结果 dict（含 success 字段），不抛异常。
    """
    try:
        announcements = search_prospectus(stock, timeout=timeout)

        if not announcements:
            return {
                "success": False,
                "stock": stock,
                "doc_type": "prospectus",
                "error": (
                    f"未在巨潮找到 {stock} 的招股说明书。"
                    "老公司（2008年前上市）可能无电子版，建议到上交所/深交所官网查询；"
                    "未上市公司（IPO审核中）的预披露招股书请到证监会/交易所审核系统查询。"
                ),
            }

        target = filter_prospectus(announcements)
        if not target:
            # 找到相关公告但无首发正本 → 把可用公告列给用户参考
            available = [
                {"title": a["title"], "date": a["date"], "pdf_url": a["pdf_url"]}
                for a in announcements[:10]
            ]
            return {
                "success": False,
                "stock": stock,
                "doc_type": "prospectus",
                "error": "找到相关公告但无首发招股书正本（可能仅有确认意见/摘要/H股等）",
                "available": available,
            }

        # 招股书标题（如"首次公开发行股票并在创业板上市招股说明书"）不含公司名，
        # 文件名直接用用户传入的 stock（名称或代码）
        stock_name = stock
        board = detect_board(target["title"])
        filename = safe_filename(f"{stock_name}_招股说明书.pdf")

        result = download_pdf(
            pdf_url=target["pdf_url"],
            output_dir=output_dir,
            filename=filename,
            timeout=timeout,
        )

        return {
            "success": True,
            "stock": stock_name,
            "doc_type": "prospectus",
            "report_label": "招股说明书",
            "board": board,
            "title": target["title"],
            "date": target["date"],
            "pdf_url": target["pdf_url"],
            "downloaded": result["filepath"],
            "file_size": result["size_display"],
        }

    except DownloadError as e:
        return {
            "success": False,
            "stock": stock,
            "doc_type": "prospectus",
            "error": e.message,
        }
