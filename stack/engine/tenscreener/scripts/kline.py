#!/usr/bin/env python3
"""
腾讯前复权日K封装 · tenscreener

免额度数据源(问财额度留给财务查询),runner 通道的价格动量全靠这里:
  fetch_qfq(symbol, beg, end)   640根上限自动分段合并
  series_metrics(rows)          pos_1y/52周低点倍数/距峰回撤
  index_gate_series(idx_rows)   上证日收盘 → 每日 ma250 on/off 序列(回测用)

缓存 outputs/cache/kline/YYYYMMDD/,set_cache_dir/set_offline 镜像 wencai 模式
(offline 缺失抛 FixtureMissing,回放测试的哨兵)。
纯标准库。
"""

import json
import os
import urllib.request
from datetime import date, datetime

SKILL_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KLINE_CACHE_ROOT = os.path.join(SKILL_ROOT, "outputs", "cache", "kline")

_counter = {"api": 0, "cache_hit": 0}
_cache_dir = None
_offline = False


class FixtureMissing(Exception):
    """回放模式下 kline 未命中 —— fixture 与请求参数不再匹配。"""


def set_cache_dir(path):
    global _cache_dir
    _cache_dir = path


def set_offline(flag=True):
    global _offline
    _offline = flag


def counter():
    return dict(_counter)


def counter_report():
    return f"[K线] API 调用 {_counter['api']} 次 / 缓存命中 {_counter['cache_hit']} 次"


def _cache_file(symbol, beg, end):
    d = _cache_dir or os.path.join(KLINE_CACHE_ROOT, date.today().strftime("%Y%m%d"))
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, f"{symbol}_{beg}_{end}.json")


def _fetch_chunk(symbol, beg, end):
    """单段拉取。沪深走腾讯(前复权,单次上限640根);北交所(bj前缀)腾讯不支持,
    走新浪日K(不复权口径,北交所标的分红少影响有限)——行格式统一为
    [date, open, close, high, low, volume](注意新浪字段顺序不同,这里做了映射)。"""
    if symbol.startswith("bj"):
        from datetime import datetime
        days = (datetime.strptime(end, "%Y-%m-%d") -
                datetime.strptime(beg, "%Y-%m-%d")).days
        datalen = min(1023, max(60, int(days * 5 / 7) + 30))
        url = ("https://quotes.sina.cn/cn/api/json_v2.php/"
               f"CN_MarketDataService.getKLineData?symbol={symbol}"
               f"&scale=240&ma=no&datalen={datalen}")
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://finance.sina.com.cn/"})
        d = json.load(urllib.request.urlopen(req, timeout=20))
        if not isinstance(d, list):                 # 新浪异常返回(非北交所/下市)
            return []
        return [[r["day"], r["open"], r["close"], r["high"], r["low"], r["volume"]]
                for r in d if beg <= r["day"] <= end]
    url = (f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
           f"?param={symbol},day,{beg},{end},640,qfq")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    d = json.load(urllib.request.urlopen(req, timeout=20))
    data = d["data"][symbol]
    return data.get("qfqday") or data.get("day") or []


def _spans(beg, end):
    """把区间切成 ≤2 个日历年的段(每段 ≈487 交易日 < 腾讯 640 根上限)。"""
    b, e = int(beg[:4]), int(end[:4])
    pts = [beg] + [f"{y}-01-01" for y in range(b + 2, e + 1, 2)] + [end]
    pts = sorted({p for p in pts if beg <= p <= end})
    if pts[0] != beg:
        pts.insert(0, beg)
    return [(pts[i], pts[i + 1]) for i in range(len(pts) - 1)]


def fetch_qfq(symbol, beg, end):
    """区间前复权日K,自动分段(避开腾讯单次 640 根截断)合并去重。
    返回 [[YYYY-MM-DD, open, close, high, low, volume], ...] 升序。"""
    cf = _cache_file(symbol, beg, end)
    if os.path.exists(cf):
        try:
            with open(cf, encoding="utf-8") as f:
                cached = json.load(f)
            _counter["cache_hit"] += 1
            return cached["rows"]
        except (json.JSONDecodeError, OSError, KeyError):
            pass
    if _offline:
        raise FixtureMissing(f"kline fixture 未命中: {symbol} {beg}~{end}")

    rows = {}
    for s, e_ in _spans(beg, end):
        for r in _fetch_chunk(symbol, s, e_):
            rows[r[0]] = r
    out = [rows[k] for k in sorted(rows)]
    with open(cf, "w", encoding="utf-8") as f:
        json.dump({"symbol": symbol, "beg": beg, "end": end, "rows": out},
                  f, ensure_ascii=False)
    _counter["api"] += 1
    return out


# ─── 指标计算(纯函数,可离线单测) ────────────────────────────

def series_metrics(rows, window=250):
    """rows 升序。→ {last, high_52w, low_52w, pos_1y, run_multiple, drawdown}
    pos_1y=现价/一年最高;run_multiple=现价/一年最低(十倍进度);
    drawdown=1-现价/一年最高(距峰值回撤)。"""
    if len(rows) < 20:
        return {}
    w = rows[-window:]
    last = float(rows[-1][2])
    high_52w = max(float(r[3]) for r in w)
    low_52w = min(float(r[4]) for r in w)
    return {
        "last": last,
        "high_52w": high_52w,
        "low_52w": low_52w,
        "pos_1y": last / high_52w if high_52w > 0 else None,
        "run_multiple": last / low_52w if low_52w > 0 else None,
        "drawdown": 1 - last / high_52w if high_52w > 0 else None,
    }


def metrics_at(rows, asof, window=250):
    """历史时点指标(回测用):asof 'YYYY-MM-DD' 之前的 window 根内计算。"""
    sub = [r for r in rows if r[0] <= asof]
    return series_metrics(sub, window) if len(sub) >= 20 else {}


def index_gate_series(idx_rows, window=250):
    """上证日K → [(date, verdict)] 每日 ma250 on/off(回测的大盘β时间线)。
    现实时扫描用 monitor.market_gate,这里只服务回放。"""
    out = []
    for i, r in enumerate(idx_rows):
        if i + 1 < window:
            continue
        ma = sum(float(x[2]) for x in idx_rows[i + 1 - window:i + 1]) / window
        out.append((r[0], "on" if float(r[2]) >= ma else "off"))
    return out


def gate_at(gates, asof, default="off"):
    """asof 当日(或之前最近一日)的大盘 verdict。"""
    prior = [g for g in gates if g[0] <= asof]
    return prior[-1][1] if prior else default
