#!/usr/bin/env python3
"""
sector.py — 行业板块免费数据 · tenscreener v5(2026-11-07)

三层望远镜的行业β与发现度素材(全部免费源, 当日缓存, 慢速防限流):
  board_rank()             全部行业板块当日涨幅榜(1 次 clist) → 排名/热度
  board_members(bk)        板块成分清单(1 次 clist)
  board_kline_perf(bk)     板块指数 60/250 日表现(板块K线自算, 分段)
  industry_fundamentals()  行业景气实证: 成分∩东财业绩排行双线>40% 的公司数
                           (growth_universe 免费源 ∩ 板块成分 —— 无问财额度)

设计对应 docs/plans/2026-11-07-v5-three-layer-design.md §5:
行业β纯基本面(过线公司数=财报侧景气), 资金热度仅服务个股发现度表盘。
当日缓存镜像 emoney 模式; push2his 有间歇限流 → 重试+退避, 拉不到返回空由调用侧降级。
纯标准库。
"""
import json
import os
import time
import urllib.parse
import urllib.request
from datetime import date

SKILL_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SEC_CACHE_ROOT = os.path.join(SKILL_ROOT, "outputs", "cache", "sector")

_counter = {"api": 0, "cache_hit": 0}
_cache_dir = None


def set_cache_dir(path):
    global _cache_dir
    _cache_dir = path


def counter_report():
    return f"[板块] API 调用 {_counter['api']} 次 / 缓存命中 {_counter['cache_hit']} 次"


UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
      "Referer": "https://quote.eastmoney.com/"}


def _cache_file(name):
    d = _cache_dir or os.path.join(SEC_CACHE_ROOT, date.today().strftime("%Y%m%d"))
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, name + ".json")


def _cached(name, fetch):
    cf = _cache_file(name)
    if os.path.exists(cf):
        try:
            _counter["cache_hit"] += 1
            return json.load(open(cf, encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    data = fetch()
    if data is not None:
        try:
            json.dump(data, open(cf, "w", encoding="utf-8"), ensure_ascii=False)
        except OSError:
            pass
    return data


def _get_json(url, timeout=20):
    """东财 push2/push2his 通用(间歇断连 → 2 次重试退避; 仍失败返回 None 由调用侧降级)。"""
    last = None
    for i in range(3):
        try:
            req = urllib.request.Request(url, headers=UA)
            return json.loads(urllib.request.urlopen(req, timeout=timeout).read().decode())
        except Exception as e:
            last = e
            time.sleep(1.2 * (i + 1))
    return {"_err": str(last)[:80]}


# ─── 行业板块当日涨幅榜 ───────────────────────────────────────

def board_rank():
    """全部行业板块(东财行业, m:90+t:2)当日涨跌幅排名。
    -> [{bk, name, pct, up_n, down_n, rank}] 按涨跌幅降序。失败返回 []。"""
    def fetch():
        p = {"pn": 1, "pz": 100, "po": 1, "np": 1, "fltt": 2, "invt": 2, "fid": "f3",
             "fs": "m:90 t:2", "fields": "f12,f14,f3,f104,f105"}
        u = "https://push2.eastmoney.com/api/qt/clist/get?" + urllib.parse.urlencode(p)
        d = _get_json(u)
        if not d or d.get("_err") or not d.get("data"):
            return None
        rows = (d.get("data") or {}).get("diff") or []
        out = []
        for r in rows:
            try:
                out.append({"bk": r.get("f12"), "name": r.get("f14"),
                            "pct": r.get("f3") if isinstance(r.get("f3"), (int, float)) else None,
                            "up_n": r.get("f104"), "down_n": r.get("f105")})
            except Exception:
                continue
        return out
    rows = _cached("board_rank_%s" % date.today().strftime("%Y%m%d"), fetch) or []
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    return rows


# ─── 个股 → 行业板块 ─────────────────────────────────────────

def stock_industry(code):
    """个股所属行业(qt/stock/get 单股详情: f127=东财行业, f129=概念/细分)。
    -> {"industry": "航海装备Ⅱ", "concepts": "船舶制造,..."} 或 None。
    (实测 ulist.np 的 f127 恒 -401 无效, 必须走 stock/get。)"""
    code6 = str(code).split(".")[0]
    secid = ("1." if code6[0] in "69" else "0.") + code6

    def fetch():
        p = {"secid": secid, "fields": "f57,f58,f127,f129"}
        u = "https://push2.eastmoney.com/api/qt/stock/get?" + urllib.parse.urlencode(p)
        d = _get_json(u)
        data = (d or {}).get("data") or {}
        if not data or d.get("_err"):
            return None
        ind = data.get("f127")
        return {"industry": ind, "concepts": data.get("f129")} if ind else None
    return _cached("ind_%s" % code6, fetch)


def find_board(industry_name, ranks=None, concepts=None):
    """行业名 → 板块信息。先精确/子串匹配榜单名; 未命中且给了 concepts
    (f129 细分概念, 如'船舶制造')时按概念名再匹配一轮。"""
    ranks = ranks if ranks is not None else board_rank()
    names = [industry_name] + ([concepts] if concepts else [])
    for want in names:
        if not want or not isinstance(want, str):
            continue
        for r in ranks:
            if want in str(r.get("name") or "") or str(r.get("name") or "") in want:
                return r
    return None


# ─── 板块成分 ────────────────────────────────────────────────

def board_members(bk):
    """板块成分清单(代码/名称/当日涨跌幅)。-> [{code, name, pct}] 失败返回 []。"""
    def fetch():
        p = {"pn": 1, "pz": 300, "po": 1, "np": 1, "fltt": 2, "invt": 2, "fid": "f3",
             "fs": "b:%s" % bk, "fields": "f12,f14,f3"}
        u = "https://push2.eastmoney.com/api/qt/clist/get?" + urllib.parse.urlencode(p)
        d = _get_json(u)
        if not d or d.get("_err") or not d.get("data"):
            return None
        rows = (d.get("data") or {}).get("diff") or []
        return [{"code": r.get("f12"), "name": r.get("f14"), "pct": r.get("f3")} for r in rows]
    return _cached("members_%s" % bk, fetch) or []


# ─── 板块指数区间表现 ────────────────────────────────────────

def board_kline_perf(bk):
    """板块指数(90.BKxxxx)近 60/250 交易日表现。-> {gain_60d, gain_250d, last_pct}
    push2his 限流风险 → 失败返回 {}(调用侧降级, 发现度表盘缺资金侧不阻塞)。"""
    def fetch():
        p = {"secid": "90.%s" % bk, "fields1": "f1,f2,f3", "fields2": "f51,f53",
             "klt": "101", "fqt": "1", "beg": "0", "end": "20500101", "lmt": "320"}
        u = ("https://push2his.eastmoney.com/api/qt/stock/kline/get?"
             + urllib.parse.urlencode(p))
        d = _get_json(u)
        if not d or d.get("_err") or not d.get("data"):
            return None
        kl = (d.get("data") or {}).get("klines") or []
        closes = [float(r.split(",")[1]) for r in kl]
        if len(closes) < 30:
            return None
        out = {}
        last = closes[-1]
        if len(closes) > 60:
            out["gain_60d"] = round((last / closes[-61] - 1) * 100, 1)
        if len(closes) > 250:
            out["gain_250d"] = round((last / closes[-251] - 1) * 100, 1)
        out["kdays"] = len(closes)
        return out
    return _cached("bkperf_%s" % bk, fetch) or {}


# ─── 行业景气实证(财报侧) ────────────────────────────────────

def industry_fundamentals(members, pass_line=40.0):
    """板块内『双线同比>pass_line』公司数(东财业绩排行 growth_universe ∩ 成分)。
    财报季最硬的行业景气证据; 空窗期(粗筛报告期旧)时数字偏保守, 如实呈现。
    -> {pass_n, pass_line, pass_names[:10], total}"""
    import emoney
    codes = {str(m.get("code")) for m in members if m.get("code")}
    if not codes:
        return {}
    try:
        uni = emoney.growth_universe(pass_line)
    except Exception:
        return {}
    hit = [u for u in uni if u.get("code") in codes]
    return {"pass_n": len(hit), "pass_line": pass_line,
            "report_date": hit[0]["report_date"] if hit else None,
            "pass_names": [u["name"] for u in hit][:10], "total": len(codes)}


# ─── 发现度三表盘聚合(纯函数) ────────────────────────────────

def discovery_dials(board_info, research_meta, holders_meta, pos_1y=None):
    """三表盘 → 发现度相位(纯函数, 可离线单测)。
    board_info: board_rank 行({rank,pct,up_n}) 或 None
    research_meta: {"reports_12m": n, "orgs": n} 或 None
    holders_meta: {"holders_wan": [(date, 万户)...新→旧]} 或 None
    pos_1y: 现价/一年最高(可选)
    -> {dials: {资金, 机构, 筹码}, phase, note}
    phase: 未发现(左侧黄金)/发现中/拥挤 —— 三表盘独立打分(0/1/2)取和:
      资金: 板块涨幅榜前5=2, 前15=1, 其余0
      机构: 覆盖机构≥20家=2(饱和), ≥8=1, 其余0   (≥20家同时是拥挤信号)
      筹码: 户数较一年前降>15%=1(集中), 升>15%=-1(发散)
    拥挤判定: 资金=2 且 机构=2;未发现: 合计≤1 且 pos_1y<0.8。"""
    s = {"资金": 0, "机构": 0, "筹码": 0}
    if board_info and isinstance(board_info.get("rank"), int):
        r = board_info["rank"]
        s["资金"] = 2 if r <= 5 else (1 if r <= 15 else 0)
    orgs = (research_meta or {}).get("orgs")
    if isinstance(orgs, int):
        s["机构"] = 2 if orgs >= 20 else (1 if orgs >= 8 else 0)
    hw = (holders_meta or {}).get("holders_wan") or []
    if len(hw) >= 4:
        newest, oldest = hw[0][1], hw[-1][1]
        if isinstance(newest, (int, float)) and isinstance(oldest, (int, float)) and oldest > 0:
            chg = (newest / oldest - 1) * 100
            s["筹码"] = -1 if chg < -15 else (1 if chg > 15 else 0)
    total = s["资金"] + s["机构"] + s["筹码"]
    if s["资金"] == 2 and s["机构"] == 2:
        phase = "拥挤"
    elif total <= 1 and (pos_1y is None or pos_1y < 0.8):
        phase = "未发现(左侧黄金)"
    elif total <= 1:
        phase = "未发现"
    else:
        phase = "发现中"
    note = "资金%s/机构%s/筹码%s → 合计%d" % (s["资金"], s["机构"], s["筹码"], total)
    if pos_1y is not None:
        note += " · pos_1y=%.2f" % pos_1y
    return {"dials": s, "phase": phase, "note": note}
