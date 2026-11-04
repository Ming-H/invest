#!/usr/bin/env python3
"""全市场 GT 枚举 · tenscreener loop

消除手工候选池的完整性疑问:对 2024 以来每个半年度报告期,取东财业绩排行
(营收同比/归母同比 各前400)的双线>40 并集 → outputs/ai10x/enum_universe.json。
ai10x_harness 会自动把它并入候选池做 10x 筛查(断点续跑,跨轮完成)。

免额度(东财 datacenter)。用法:python3 tests/ai10x_enumerate.py [--pages 4]
"""

import argparse
import json
import os
import sys
import urllib.request
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
SK = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(SK, "scripts"))
import emoney

OUT = os.path.join(SK, "outputs", "ai10x", "enum_universe.json")

# 2024 以来半年度报告期(双线>40 至少会落进一个半年报窗口;季度加密代价×2收益低)
DATES = ["2024-06-30", "2024-12-31", "2025-06-30", "2025-12-31", "2026-06-30"]


def fetch_page(sort_col, page, rd, cache_dir):
    cf = os.path.join(cache_dir, f"enum_{sort_col}_p{page}_{rd}.json")
    if os.path.exists(cf):
        try:
            return json.load(open(cf, encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    url = ("https://datacenter-web.eastmoney.com/api/data/v1/get"
           f"?reportName=RPT_LICO_FN_CPD&columns=ALL&pageSize=100&pageNumber={page}"
           f"&sortColumns={sort_col}&sortTypes=-1&filter=(REPORTDATE%3D%27{rd}%27)")
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0", "Referer": "https://data.eastmoney.com/"})
    d = json.load(urllib.request.urlopen(req, timeout=30))
    rows = (d.get("result") or {}).get("data") or []
    json.dump(rows, open(cf, "w", encoding="utf-8"), ensure_ascii=False)
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pages", type=int, default=4)
    args = ap.parse_args()
    cache_dir = os.path.join(SK, "outputs", "cache", "emoney",
                             date.today().strftime("%Y%m%d"))
    os.makedirs(cache_dir, exist_ok=True)

    uni = {}
    if os.path.exists(OUT):
        uni = {r["code"]: r for r in json.load(open(OUT, encoding="utf-8"))}
    for rd in DATES:
        for sort_col in ("YSTZ", "SJLTZ"):
            for page in range(1, args.pages + 1):
                for r in fetch_page(sort_col, page, rd, cache_dir):
                    code, rev, prof = r.get("SECURITY_CODE"), r.get("YSTZ"), r.get("SJLTZ")
                    if not code or rev is None or prof is None:
                        continue
                    if rev > 40 and prof > 40 and code not in uni:
                        uni[code] = {"code": code, "name": r.get("SECURITY_NAME_ABBR"),
                                     "rev_growth": rev, "profit_growth": prof,
                                     "report_date": rd}
        json.dump(list(uni.values()), open(OUT, "w", encoding="utf-8"),
                  ensure_ascii=False)
        print(f"{rd} 完成,累计 universe {len(uni)} 只", flush=True)
    print(f"\n枚举完成:{len(uni)} 只(2024 以来任一期双线>40)→ {OUT}")


if __name__ == "__main__":
    main()
