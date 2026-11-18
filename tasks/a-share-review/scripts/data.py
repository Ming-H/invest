#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""data.py — a-share-review 取数层 v2 (全部免费源, 零密钥依赖, mac 本机运行)。

v2 变更(2026-11-02 设计升级):
- 行情批量化: stock_quotes_batch() 走 ulist.np/get 一次拉全部关注标的
  (单股 push2 限流日逐只请求大面积降级 → 批量实测稳定; f8换手/f9PE/f20市值全字段可用)
- K线多源: idx_kline/stock_kline 主链东财(多节点轮询) → 腾讯 → 新浪, 三级降级
  (2026-11-02 实测: push2his 间歇限流、腾讯下午遭 WAF 拦截, 单点依赖全部暴露)
- 北交所支持: _secid("0.92xxxx") / _sym("bj92xxxx") 实测可用
- 事件区: unlock_calendar() 东财解禁日历 + next_report_date() 预约披露日
- 财报三线: fundamentals_threeline() 问财单次批量(营收/归母/扣非同比)
- ma_status 增加 MA250 年线(与 tenscreener market_gate 大盘β口径统一)
- 两市成交口径修正: 上证指数 + 深证综指(399106)(旧版五大指数加总重复计算)
- jingxuan_members 返回 (成员, 错误信息), 降级不再静默

依赖:
- 东财 push2/push2his/datacenter (行情批量/K线/解禁/披露) — 免费
- 腾讯 fqkline / 新浪 CN_MarketDataService (K线后备) — 免费
- 问财 cli (涨幅榜/连板/三线, 经 hithink-market-query, 消耗问财额度)
- 板块资金流: 内置 flow.py 模块(2026-11-05 收编自 hithink-moneyflow 技能, 同样经
  hithink-market-query cli, 消耗问财额度; ~/.claude/skills 已废弃, 路径走 flow 的
  多位置探测)
- 巨潮 hisAnnouncement (公告) + 华尔街见闻 (新闻) — 免费
所有函数失败返回 None/[]/(err) 并带 degraded 标记, 不阻塞整体复盘。
"""
import datetime
import json
import os
import re
import subprocess
import time
import urllib.parse
import urllib.request

import flow

UA = {"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com"}
SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WENCAI_CLI = str(flow.wencai_cli_path()) if flow.wencai_cli_path() else ""   # 同一探测逻辑
ENV = dict(os.environ)
for envf in ("~/.hithink_env", "~/.claude/.hithink_env"):
    p = os.path.expanduser(envf)
    if os.path.exists(p):
        for line in open(p):
            if "=" in line and not line.startswith("#"):
                k, v = line.strip().split("=", 1)
                ENV[k] = v
        break

# 东财 K 线节点(限流时轮询, 2026-11-02 实测主站间歇失败)
EM_KLINE_HOSTS = ["push2his.eastmoney.com", "91.push2his.eastmoney.com",
                  "32.push2his.eastmoney.com"]


def _get(url, headers=None, timeout=12, retries=3):
    last = None
    for i in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=headers or UA)
            return json.loads(urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", "ignore"))
        except Exception as ex:
            last = ex
            time.sleep(min(2 * (i + 1), 8))
    raise last


def _post(url, data, headers=None, timeout=15):
    body = urllib.parse.urlencode(data).encode("utf-8")
    req = urllib.request.Request(url, data=body,
                                 headers=headers or {"User-Agent": "Mozilla/5.0"})
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", "ignore"))


def _secid(code):
    code = str(code).split(".")[0]
    if code.startswith(("6", "9")):
        return "1." + code          # 沪(含科创板 68x)
    return "0." + code              # 深 + 北交所(92x/43x/83x, 实测 0. 前缀可用)


def _sym(code):
    code = str(code).split(".")[0]
    if code.startswith(("92", "43", "83", "87", "88")):
        return "bj" + code          # 北交所(新浪 bj 前缀; 注意须在 9 开头沪B股判断之前)
    if code.startswith(("6", "9")):
        return "sh" + code
    return "sz" + code


# ---------------- 模块一: 市场总览 ----------------

# 展示五大指数; total 口径 = 上证指数(全沪) + 深证综指(全深), 不含北交所
INDICES = [("1.000001", "上证指数"), ("0.399001", "深证成指"), ("0.399006", "创业板指"),
           ("1.000688", "科创50"), ("0.899050", "北证50")]
TOTAL_PAIR = [("1.000001", "上证指数"), ("0.399106", "深证综指")]


def index_overview():
    """五大指数收盘/涨跌幅/成交额。主链: push2his K线推算; 限流后备: 腾讯批量行情。"""
    tq = None
    out = []
    for secid, name in INDICES:
        row = {"name": name, "close": None, "chg": None, "amount_yi": None}
        try:
            rows = idx_kline(secid, 5)
            last, prev = rows[-1], rows[-2]
            row = {"name": name, "close": last["close"],
                   "chg": round((last["close"] / prev["close"] - 1) * 100, 2),
                   "amount_yi": last["amount_yi"]}
        except Exception:
            if tq is None:
                tq = tencent_index_quotes()
            if name in tq:
                row = {"name": name, **tq[name]}
        out.append(row)
    return out


def market_total_amount(days=10):
    """两市(沪+深综)近N日成交额序列: [{date, amount_yi}]。
    东财K线间歇断连 → 双轮多节点重试; 全败返回[]由调用方降级(当日值走腾讯快照)。"""
    for attempt in range(2):
        try:
            sh = idx_kline("1.000001", days)
            sz = idx_kline("0.399106", days)
            n = min(len(sh), len(sz), days)
            if n:
                return [{"date": sh[-i]["date"],
                         "amount_yi": round(sh[-i]["amount_yi"] + sz[-i]["amount_yi"], 0)}
                        for i in range(n, 0, -1)]
        except Exception:
            time.sleep(1)
    return []


def breadth_today():
    """全市场涨跌家数(东财涨跌分布)。失败 None。"""
    try:
        d = _get("https://push2ex.eastmoney.com/getTopicZDFenBu?ut=7eea3edcaed734bea9cbfc24409ed989&dpt=wz.ztzt", retries=1)
        up = down = flat = 0
        for item in (d.get("data") or {}).get("fenbu") or []:
            for k, v in item.items():
                try:
                    ki = int(k)
                except ValueError:
                    continue
                if ki > 0:
                    up += v
                elif ki < 0:
                    down += v
                else:
                    flat += v
        if up + down + flat == 0:
            return None
        return {"up": up, "down": down, "flat": flat}
    except Exception:
        return None


def tencent_index_quotes():
    """腾讯批量指数行情(push2限流后备): {名称: {close, chg, amount_yi}}。"""
    syms = "sh000001,sz399001,sz399006,sh000688,bj899050"
    try:
        req = urllib.request.Request("https://qt.gtimg.cn/q=" + syms,
                                     headers={"User-Agent": "Mozilla/5.0"})
        raw = urllib.request.urlopen(req, timeout=10).read().decode("gbk", "ignore")
        out = {}
        for m in re.finditer(r'v_(\w+)="([^"]+)"', raw):
            p = m.group(2).split("~")
            if len(p) < 38:
                continue
            name = p[1]
            out[name] = {"close": float(p[3]), "chg": float(p[32]),
                         "amount_yi": round(float(p[37]) / 1e4, 0)}
        return out
    except Exception:
        return {}


def idx_kline(secid, days=70):
    """东财日K(多节点轮询): [{date, close, amount_yi}]。全节点失败抛异常(调用方降级)。"""
    last = None
    for host in EM_KLINE_HOSTS:
        try:
            d = _get("https://%s/api/qt/stock/kline/get?secid=%s&fields1=f1,f2,f3"
                     "&fields2=f51,f53,f57&klt=101&fqt=1&end=20500101&lmt=%d" % (host, secid, days),
                     retries=0)
            rows = []
            for k in (d.get("data") or {}).get("klines") or []:
                p = k.split(",")
                rows.append({"date": p[0], "close": float(p[1]), "amount_yi": round(float(p[2]) / 1e8, 0)})
            if rows:
                return rows
            last = RuntimeError("%s 空 klines" % host)
        except Exception as ex:
            last = ex
            time.sleep(0.5)
    raise last or RuntimeError("idx_kline 全节点失败: " + secid)


def idx_closes_tx(sym, days=70):
    """腾讯指数日K(东财限流后备, 仅收盘价)。"""
    d = _get("https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=%s,day,,,%d,qfq"
             % (sym, days), {"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com"})
    node = (d.get("data") or {}).get(sym) or {}
    k = node.get("day") or node.get("qfqday") or []
    return [{"date": x[0], "close": float(x[2])} for x in k]


def idx_closes_sina(sym, days=70):
    """新浪指数日K(腾讯 WAF 拦截时的末级后备, 仅收盘价)。sym 如 sh000001。"""
    url = ("https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData"
           "?symbol=%s&scale=240&ma=no&datalen=%d" % (sym, days))
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0",
                                               "Referer": "https://finance.sina.com.cn/"})
    d = json.load(urllib.request.urlopen(req, timeout=15))
    return [{"date": r["day"], "close": float(r["close"])} for r in d]


def index_closes(sym, days=300):
    """指数收盘序列(东财多节点→腾讯→新浪 三级降级)。用于 MA5/20/250。"""
    secid = ("1." if sym.startswith("sh") else "0.") + sym[2:]
    for fn in (lambda: idx_kline(secid, days),
               lambda: idx_closes_tx(sym, days),
               lambda: idx_closes_sina(sym, days)):
        try:
            rows = fn()
            if len(rows) >= 20:
                return rows
        except Exception:
            continue
    return []


def ma_status(rows, n1=5, n2=20, n3=250):
    """收盘序列 → (MA5, MA20, MA250年线, 短线多空, 年线β)。rows 需含 {close}。"""
    closes = [r["close"] for r in rows]
    if len(closes) < n2:
        return None
    ma1 = sum(closes[-n1:]) / n1
    ma2 = sum(closes[-n2:]) / n2
    ma3 = (sum(closes[-n3:]) / n3) if len(closes) >= n3 else None
    chg20 = (closes[-1] / closes[-n2] - 1) * 100 if len(closes) >= n2 else None
    return {"ma5": round(ma1, 1), "ma20": round(ma2, 1),
            "ma250": round(ma3, 1) if ma3 else None,
            "trend": "多头" if ma1 > ma2 else "空头",
            "beta": ("on" if closes[-1] >= ma3 else "off") if ma3 else None,
            "chg20": round(chg20, 1)}


# ---------------- 模块二: 大涨标的 ----------------

def _wencai(query, limit=20):
    """问财 cli 封装: 返回 datas(list[dict]) 或 []。"""
    if not os.path.exists(WENCAI_CLI):
        return []
    try:
        out = subprocess.check_output(
            ["python3", WENCAI_CLI, "--query", query, "--limit", str(limit)],
            env=ENV, timeout=60, stderr=subprocess.DEVNULL).decode("utf-8", "ignore")
        m = re.search(r"\{.*\}", out, re.S)
        if not m:
            return []
        d = json.loads(m.group(0))
        return d.get("datas") or []
    except Exception:
        return []


def _pick(row, *prefixes):
    """问财字段带日期后缀, 前缀匹配取值。"""
    for p in prefixes:
        for k, v in row.items():
            if k.startswith(p) and v not in (None, ""):
                return v
    return None


def top_gainers_20d(n=15):
    """近20日涨幅榜 Top N: [{name, code, pct, industry, mcap_yi}]。
    注: 问财行业字段名为「所属同花顺行业」, 前缀须精确匹配。"""
    rows = _wencai("近20日涨幅前%d的股票 所属同花顺行业 总市值" % (n * 2), n * 2)
    out = []
    for r in rows:
        name = _pick(r, "股票简称")
        code = str(_pick(r, "股票代码") or "").split(".")[0]
        pct = None
        for k, v in r.items():
            if k.startswith("涨跌幅[") and isinstance(v, (int, float)):
                pct = v
        ind = _pick(r, "所属同花顺行业", "所属申万行业", "所属行业", "行业")
        mcap = _pick(r, "总市值")
        if pct is None or not name:
            continue
        if isinstance(ind, (list, tuple)):
            ind = ind[0] if ind else None
        out.append({"name": name, "code": code, "pct": round(float(pct), 1),
                    "industry": str(ind) if ind else "—",
                    "mcap_yi": round(float(mcap) / 1e8, 0) if mcap else None})
        if len(out) >= n:
            break
    return out


def lianban_ladder(min_days=2):
    """连板天梯: [{name, code, days}]。"""
    rows = _wencai("连板天梯", 20)
    out = []
    for r in rows:
        name = _pick(r, "股票简称")
        code = str(_pick(r, "股票代码") or "").split(".")[0]
        days = _pick(r, "连续涨停天数", "连板数")
        try:
            days = int(float(days))
        except (TypeError, ValueError):
            continue
        if days >= min_days:
            out.append({"name": name, "code": code, "days": days})
    return out


def jingxuan_members():
    """L2 精选表(Base)成员: ({code: name}, err_or_None)。失败不再静默, err 写入降级清单。"""
    try:
        for _attempt in range(2):
            try:
                subprocess.check_output(
                    "cd /tmp && lark-cli base +record-list --base-token J8RGbNZobaXu9VsqtFWcKUemnQe "
                    "--table-id tblHhkqqSWcpGD7Y --limit 100 --format ndjson "
                    "--overwrite --output ./asr_jx.ndjson --as user",
                    shell=True, timeout=60, stderr=subprocess.DEVNULL)
                break
            except Exception:
                if _attempt:
                    raise
                time.sleep(2)
        out = {}
        for line in open("/tmp/asr_jx.ndjson"):
            try:
                r = json.loads(line)
                c = str(r.get("代码", "")).split(".")[0].strip()
                if c:
                    out[c] = {"name": r.get("股票名称", ""), "score": r.get("综合分", ""),
                              "stage": r.get("阶段", ""), "action": r.get("行动", "")}
            except Exception:
                pass
        if not out:
            return {}, "精选表Base为空或解析0条"
        return out, None
    except Exception as ex:
        return {}, "lark-cli拉取精选表失败: %s" % str(ex)[:80]


# ---------------- 模块三: 板块资金流(内置 flow 模块, 2026-11-05 收编) ----------------

def sector_flows(top_in=8, top_out=6):
    """行业板块主力净流入/流出: {inflow: [...], outflow: [...]}。"""
    res = {"inflow": [], "outflow": []}
    if flow.wencai_cli_path() is None:
        return res
    for view, key, n in (("industry-inflow", "inflow", top_in), ("industry-outflow", "outflow", top_out)):
        try:
            rows, _meta = flow.fetch_rows(view, top=n, timeout=90)
            res[key] = [{"name": r.get("name"),
                         "amount": round((r.get("amount") or 0) / 1e8, 2),
                         "pct": r.get("pct")} for r in rows]
        except flow.FlowError:
            res[key] = []
    return res


# ---------------- 模块四: 个股全景 ----------------

def stock_quotes_batch(codes):
    """批量行情(主链, 2026-11-02 实测稳定): {code: {price, chg, turnover, pe, mcap_yi, float_mcap_yi, main_inflow_wan, name}}。
    ulist.np/get: f2价格x100 f3涨跌x100 f8换手x100 f9PE(动)x100 f20总市值 f21流通市值 f62主力净流入(元)。"""
    if not codes:
        return {}
    secids = ",".join(_secid(c) for c in codes)
    try:
        d = _get("https://push2.eastmoney.com/api/qt/ulist.np/get?secids=%s"
                 "&fields=f2,f3,f8,f9,f12,f14,f20,f21,f62&ut=fa5fd1943c7b386f172d6893dbfba10b" % secids)
        out = {}
        for it in (d.get("data") or {}).get("diff") or []:
            code = it.get("f12")
            if not code:
                continue
            out[str(code)] = {
                "name": it.get("f14"),
                "price": it["f2"] / 100.0 if isinstance(it.get("f2"), (int, float)) else None,
                "chg": it["f3"] / 100.0 if isinstance(it.get("f3"), (int, float)) else None,
                "turnover_pct": it["f8"] / 100.0 if isinstance(it.get("f8"), (int, float)) else None,
                "pe_ttm": it["f9"] / 100.0 if isinstance(it.get("f9"), (int, float)) else None,
                "mktcap_yi": round(it["f20"] / 1e8, 0) if it.get("f20") else None,
                "float_mcap_yi": round(it["f21"] / 1e8, 0) if it.get("f21") else None,
                "main_inflow_wan": round(it["f62"] / 1e4, 0) if isinstance(it.get("f62"), (int, float)) else None,
            }
        return out
    except Exception:
        return {}


def stock_quote(code):
    """单股快照(后备: 批量接口未覆盖时)。东财单股(含f170修复) → 腾讯(仅价/涨跌)。"""
    try:
        d = _get("https://push2.eastmoney.com/api/qt/stock/get?secid=%s"
                 "&fields=f43,f57,f58,f60,f62,f116,f162,f168,f170&ut=fa5fd1943c7b386f172d6893dbfba10b" % _secid(code))["data"]
        return {"code": d.get("f57"), "name": d.get("f58"),
                "price": d["f43"] / 100.0 if d.get("f43") else None,
                "chg": (d.get("f170") / 100.0) if d.get("f170") is not None else None,
                "main_inflow_wan": round(d["f62"] / 1e4, 0) if d.get("f62") is not None else None,
                "mktcap_yi": round(d["f116"] / 1e8, 0) if d.get("f116") else None,
                "pe_ttm": d["f162"] / 100.0 if d.get("f162") else None,
                "turnover_pct": d["f168"] / 100.0 if d.get("f168") else None}
    except Exception:
        try:
            req = urllib.request.Request("https://qt.gtimg.cn/q=" + _sym(code),
                                         headers={"User-Agent": "Mozilla/5.0"})
            raw = urllib.request.urlopen(req, timeout=10).read().decode("gbk", "ignore")
            p = raw.split('"')[1].split("~")
            return {"code": code, "name": p[1], "price": float(p[3]), "chg": float(p[32]),
                    "main_inflow_wan": None, "mktcap_yi": None,
                    "pe_ttm": None, "turnover_pct": None}
        except Exception:
            raise


def stock_kline(code, days=250):
    """个股日K(前复权, 三级降级): [{date, close, volume}]。
    东财多节点(含成交额) → 腾讯fqkline(含volume) → 新浪(含volume)。"""
    # ① 东财
    try:
        d = None
        for host in EM_KLINE_HOSTS:
            try:
                d = _get("https://%s/api/qt/stock/kline/get?secid=%s&fields1=f1,f2,f3"
                         "&fields2=f51,f53,f56,f57&klt=101&fqt=1&end=20500101&lmt=%d"
                         % (host, _secid(code), days), retries=0)
                if (d.get("data") or {}).get("klines"):
                    break
            except Exception:
                time.sleep(0.4)
        rows = []
        for k in ((d or {}).get("data") or {}).get("klines") or []:
            p = k.split(",")
            rows.append({"date": p[0], "close": float(p[1]), "volume": float(p[2])})
        if rows:
            return rows
    except Exception:
        pass
    sym = _sym(code)
    # ② 腾讯(不支持北交所; 返回空时继续降级新浪)
    try:
        d = _get("https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=%s,day,,,%d,qfq"
                 % (sym, days), {"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com"})
        node = (d.get("data") or {}).get(sym) or {}
        k = node.get("qfqday") or node.get("day") or []
        if k:
            return [{"date": x[0], "close": float(x[2]), "volume": float(x[5])} for x in k]
    except Exception:
        pass
    # ③ 新浪(末级, 支持北交所 bj 前缀)
    url = ("https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData"
           "?symbol=%s&scale=240&ma=no&datalen=%d" % (sym, days))
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0",
                                               "Referer": "https://finance.sina.com.cn/"})
    d = json.load(urllib.request.urlopen(req, timeout=15))
    return [{"date": r["day"], "close": float(r["close"]), "volume": float(r["volume"])} for r in (d or [])]


def stock_news(name, limit=5):
    """华尔街见闻搜索: [{title, uri, time}]。"""
    try:
        d = _get("https://api-one-wscn.awtmt.com/apiv1/search/article?query=%s&limit=%d"
                 % (urllib.parse.quote(name), limit),
                 {"User-Agent": "Mozilla/5.0"}, timeout=10)
        out = []
        for it in (d.get("data") or {}).get("items") or []:
            a = (it.get("resource") or {}).get("data") or {}
            if a.get("title"):
                out.append({"title": a["title"], "uri": a.get("uri"),
                            "time": (a.get("display_time") or "")})
        return out
    except Exception:
        return []


_ORGID_CACHE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "outputs", "orgids.json")


def _cninfo_orgids():
    """代码 -> orgId 映射(巨潮官方 szse_stock.json, 全市场)。本地缓存 outputs/orgids.json。
    实测坑(2026-11-05): hisAnnouncement 的 stock 参数必须是 "代码,orgId" —— 旧版传
    "代码,sh/sz" 且 column 恒 szse, 沪市标的(60x/68x)永远返回 0 条, 全息卡"近7日公告"
    对沪市持仓一直静默空。"""
    if os.path.exists(_ORGID_CACHE):
        try:
            return json.load(open(_ORGID_CACHE, encoding="utf-8"))
        except Exception:
            pass
    try:
        req = urllib.request.Request("http://www.cninfo.com.cn/new/data/szse_stock.json",
                                     headers={"User-Agent": "Mozilla/5.0",
                                              "Referer": "http://www.cninfo.com.cn/"})
        m = {s["code"]: s["orgId"] for s in
             json.loads(urllib.request.urlopen(req, timeout=20).read().decode())["stockList"]}
        os.makedirs(os.path.dirname(_ORGID_CACHE), exist_ok=True)
        json.dump(m, open(_ORGID_CACHE, "w", encoding="utf-8"), ensure_ascii=False)
        return m
    except Exception:
        return {}


def announcements_recent(code, days=7, limit=6):
    """巨潮公告(日期区间版): [{title, date, pdf_url}]。
    正确姿势(2026-11-05 实测): column 按市场(sse/bjse/szse) + stock="代码,orgId"。
    v3.5.1: 窗口右端扩到明天——晚间披露的公告巨潮标记为次日日期, 当晚复盘就要看到。"""
    end = datetime.date.today() + datetime.timedelta(days=1)
    start = datetime.date.today() - datetime.timedelta(days=days)
    c = str(code).split(".")[0]
    column = "bjse" if c.startswith(("92", "43", "83", "87", "88")) else ("sse" if c.startswith(("6", "9")) else "szse")
    org = _cninfo_orgids().get(c)
    if not org:
        return []
    try:
        d = _post("http://www.cninfo.com.cn/new/hisAnnouncement/query", {
            "pageNum": 1, "pageSize": limit,
            "column": column, "tabName": "fulltext",
            "stock": "%s,%s" % (c, org),
            "seDate": "%s~%s" % (start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")),
            "searchkey": "", "secid": "", "plate": "", "category": "", "isHLtitle": "true",
        })
        anns = (d.get("announcements") or [])
        out = []
        for a in anns:
            if not a.get("announcementTitle"):
                continue
            pdf = ""
            adj = (a.get("adjunctUrl") or "").strip()
            if adj:
                pdf = "https://static.cninfo.com.cn/" + adj.lstrip("/")   # adjunctUrl 带不带前导/都归一
            out.append({"title": (a.get("announcementTitle") or "").replace("<em>", "").replace("</em>", ""),
                        "date": time.strftime("%Y-%m-%d", time.localtime((a.get("announcementTime") or 0) / 1000)),
                        "pdf_url": pdf})
        return out
    except Exception:
        return []


def ann_pdf_text(pdf_url, max_chars=3000):
    """公告 PDF -> 文本(pdftotext, 免费): 下载到 /tmp(ASCII名) -> 前30页转文本 -> 截断 -> 清理。
    公告雷达解读素材用(移植自 h1-monitor watch_radar 的 pdftotext 经验: >30页读前30页
    主体足够; 大多数公告 <10 页)。失败返回 None。"""
    import subprocess as _sp
    if not pdf_url:
        return None
    tmp = "/tmp/asr_ann_%d.pdf" % int(time.time() * 1000)
    try:
        req = urllib.request.Request(pdf_url, headers=UA)
        open(tmp, "wb").write(urllib.request.urlopen(req, timeout=30).read())
        txt = tmp + ".txt"
        p = _sp.run(["pdftotext", "-layout", "-l", "30", tmp, txt],
                    capture_output=True, timeout=120)
        if p.returncode == 0 and os.path.exists(txt) and os.path.getsize(txt) > 200:
            return open(txt, encoding="utf-8", errors="ignore").read()[:max_chars]
    except Exception:
        return None
    finally:
        for f in (tmp, tmp + ".txt"):
            try:
                os.remove(f)
            except Exception:
                pass
    return None


# ---------------- 模块四新增: 事件区 + 财报三线 ----------------

def unlock_calendar(code, within_days=90):
    """未来解禁日历(东财 RPT_LIFT_STAGE, 免费): [{date, shares_wan, mcap_yi, ratio_pct, type}]。"""
    try:
        end = (datetime.date.today() + datetime.timedelta(days=within_days)).strftime("%Y-%m-%d")
        today = datetime.date.today().strftime("%Y-%m-%d")
        c = str(code).split(".")[0]
        flt = '(SECURITY_CODE="%s")(FREE_DATE>=\'%s\')(FREE_DATE<=\'%s\')' % (c, today, end)
        url = ("https://datacenter-web.eastmoney.com/api/data/v1/get?reportName=RPT_LIFT_STAGE"
               "&columns=ALL&filter=" + urllib.parse.quote(flt, safe="()")
               + "&sortColumns=FREE_DATE&sortTypes=1&pageSize=5")
        d = _get(url)
        out = []
        for r in (d.get("result") or {}).get("data") or []:
            out.append({"date": (r.get("FREE_DATE") or "")[:10],
                        "shares_wan": round((r.get("ABLE_FREE_SHARES") or 0), 0),
                        "mcap_yi": round((r.get("ALIFT_MARKET_CAP") or 0) / 1e4, 1),
                        "ratio_pct": round((r.get("TOTAL_RATIO") or 0) * 100, 2),
                        "type": r.get("FREE_SHARES_TYPE") or ""})
        return out
    except Exception:
        return []


def next_report_date(code):
    """下一报告期预约披露日(东财 RPT_PUBLIC_BS_APPOIN, 免费): {year, type, date} 或 None。"""
    try:
        today = datetime.date.today().strftime("%Y-%m-%d")
        c = str(code).split(".")[0]
        flt = '(SECURITY_CODE="%s")' % c
        url = ("https://datacenter-web.eastmoney.com/api/data/v1/get?reportName=RPT_PUBLIC_BS_APPOIN"
               "&columns=ALL&filter=" + urllib.parse.quote(flt, safe="()")
               + "&sortColumns=REPORT_DATE&sortTypes=-1&pageSize=16")
        d = _get(url)
        rows = (d.get("result") or {}).get("data") or []
        cands = []
        for r in rows:
            dt = (r.get("FIRST_APPOINT_DATE") or r.get("REPORT_DATE") or "")[:10]
            if dt >= today:
                cands.append({"year": r.get("REPORT_YEAR"), "type": r.get("REPORT_TYPE"), "date": dt})
        cands.sort(key=lambda x: x["date"])
        return cands[0] if cands else None
    except Exception:
        return None


# ---------------- 模块四新增: 个股主力资金流日频序列 ----------------

def stock_moneyflow_daily(code, days=20):
    """个股主力资金日频序列(东财 fflow daykline, 免费):
    [{date, main_yi, super_yi, big_yi, mid_yi, small_yi}]。
    字段: f51日期 f52主力 f53小单 f54中单 f55大单 f56超大单(元)。
    2026-11-03 实测与当日 ulist f62 口径交叉一致。失败返回 []。"""
    try:
        d = _get("https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get"
                 "?secid=%s&fields1=f1,f2,f3,f7&fields2=f51,f52,f53,f54,f55,f56"
                 "&klt=101&lmt=%d" % (_secid(code), days), retries=2)
        out = []
        for k in (d.get("data") or {}).get("klines") or []:
            p = k.split(",")
            out.append({"date": p[0],
                        "main_yi": round(float(p[1]) / 1e8, 2),
                        "small_yi": round(float(p[2]) / 1e8, 2),
                        "mid_yi": round(float(p[3]) / 1e8, 2),
                        "big_yi": round(float(p[4]) / 1e8, 2),
                        "super_yi": round(float(p[5]) / 1e8, 2)})
        return out
    except Exception:
        return []


def fundamentals_threeline(codes):
    """财报三线同比(问财单次批量, 1次额度): {code: {rev_yoy, np_yoy, ded_yoy, period}}。
    取最新已披露报告期(累计口径), 供 E1 增速预警与全息卡业绩呈现。"""
    if not codes:
        return {}
    rows = _wencai("%s 最新报告期营业收入同比增长率 最新报告期归属母公司股东的净利润同比增长率 最新报告期扣非净利润同比增长率"
                   % " ".join(str(c).split(".")[0] for c in codes), len(codes) + 4)
    out = {}
    for r in rows:
        code = str(_pick(r, "股票代码") or "").split(".")[0]
        if not code:
            continue
        def _yoy(*prefixes):
            for p in prefixes:
                for k, v in r.items():
                    if k.startswith(p) and isinstance(v, (int, float)):
                        return round(v, 1)
            return None
        out[code] = {"rev_yoy": _yoy("营业收入同比增长率"),
                     "np_yoy": _yoy("归母净利润同比增长率", "归属母公司股东的净利润同比增长率",
                                    "归属于母公司股东的净利润同比增长率"),
                     "ded_yoy": _yoy("扣非净利润同比增长率")}
    return out


# ---------------- 买方画像(v3.2, 2026-11-12): 大单结构/龙虎榜/大宗, 全免费源 ----------------

DC_WEB = "https://datacenter-web.eastmoney.com/api/data/v1/get"
DC_HDR = {"User-Agent": "Mozilla/5.0", "Referer": "https://data.eastmoney.com/"}


def stock_fflow_breakdown(code, days=6):
    """近N日大单结构净额(单位:万元): 主力=超大+大, 超大单/大单/中单/小单。
    东财 push2 资金流K线(免费)。失败返回[](画像降级, 不阻塞)。"""
    url = ("https://push2.eastmoney.com/api/qt/stock/fflow/kline/get?secid=%s&klt=101"
           "&fields1=f1,f2,f3&fields2=f51,f52,f53,f54,f55,f56&lmt=%d"
           % (_secid(code), days))
    try:
        d = (_get(url, timeout=10) or {}).get("data") or {}
        out = []
        for k in (d.get("klines") or [])[-days:]:
            f = k.split(",")
            if len(f) < 6:
                continue
            try:
                out.append({"date": f[0][:10],
                            "main": round(float(f[1]) / 1e4, 0),   # 万元
                            "small": round(float(f[2]) / 1e4, 0),
                            "mid": round(float(f[3]) / 1e4, 0),
                            "large": round(float(f[4]) / 1e4, 0),
                            "xl": round(float(f[5]) / 1e4, 0)})
            except ValueError:
                continue
        return out
    except Exception:
        return []


def lhb_records(code, days=30):
    """近N日龙虎榜上榜记录(东财 datacenter, 免费)。[{date, reason, chg_pct}] 失败返回[]。"""
    c = str(code).split(".")[0]
    url = (DC_WEB + "?reportName=RPT_DAILYBILLBOARD_DETAILSNEW"
           "&filter=(SECURITY_CODE%3D%22" + c + "%22)"
           "&columns=TRADE_DATE,EXPLANATION,CHANGE_RATE&pageSize=10&sortColumns=TRADE_DATE&sortTypes=-1")
    try:
        d = (_get(url, headers=DC_HDR) or {}).get("result") or {}
        cutoff = (datetime.date.today() - datetime.timedelta(days=days)).isoformat()
        out = []
        for r in (d.get("data") or []):
            dt = str(r.get("TRADE_DATE") or "")[:10]
            if dt >= cutoff:
                out.append({"date": dt, "reason": (r.get("EXPLANATION") or "").strip(),
                            "chg_pct": r.get("CHANGE_RATE")})
        return out
    except Exception:
        return []


def lhb_seats(code, date):
    """某日龙虎榜买卖前五席位(单位:万元)。{"buy":[{seat,amt_wan}], "sell":[...]} 无记录返回{}。"""
    c = str(code).split(".")[0]
    out = {}
    for rpt, side, col in (("RPT_BILLBOARD_DAILYDETAILSBUY", "buy", "BUY"),
                           ("RPT_BILLBOARD_DAILYDETAILSSELL", "sell", "SELL")):
        url = (DC_WEB + "?reportName=" + rpt + "&filter=(SECURITY_CODE%3D%22" + c
               + "%22)(TRADE_DATE%3D%27" + date + "%27)"
               "&columns=ALL&pageSize=6&sortColumns=" + col + "&sortTypes=-1")
        try:
            rows = ((_get(url, headers=DC_HDR) or {}).get("result") or {}).get("data") or []
            out[side] = [{"seat": (r.get("OPERATEDEPT_NAME") or "").strip(),
                          "amt_wan": round((r.get(col) or 0) / 1e4)} for r in rows[:5]]
        except Exception:
            out[side] = []
    return out if (out.get("buy") or out.get("sell")) else {}


def block_trades(code, days=30):
    """近N日大宗交易(东财 datacenter, 免费)。[{date, price, vol_wan, amt_yi, buyer, seller}]
    失败返回[]。amt 由 价格×数量 计算(接口金额字段口径不稳)。"""
    c = str(code).split(".")[0]
    url = (DC_WEB + "?reportName=RPT_DATA_BLOCKTRADE&filter=(SECURITY_CODE%3D%22" + c
           + "%22)&columns=ALL&pageSize=10&sortColumns=TRADE_DATE&sortTypes=-1")
    try:
        rows = ((_get(url, headers=DC_HDR) or {}).get("result") or {}).get("data") or []
        cutoff = (datetime.date.today() - datetime.timedelta(days=days)).isoformat()
        out = []
        for r in rows:
            dt = str(r.get("TRADE_DATE") or "")[:10]
            if dt < cutoff:
                continue
            price, vol = r.get("DEAL_PRICE"), r.get("DEAL_VOLUME")
            if not (price and vol):
                continue
            out.append({"date": dt, "price": price, "vol_wan": round(vol / 1e4, 2),
                        "amt_yi": round(price * vol / 1e8, 2),
                        "buyer": (r.get("BUYER_NAME") or "").strip(),
                        "seller": (r.get("SELLER_NAME") or "").strip()})
        return out
    except Exception:
        return []


def contract_liab(code, periods=4):
    """合同负债序列(东财 F10 资产负债表, 免费不耗问财)。v3.3(2026-11-14)新增。

    订单先行指标(预收款模式公司: 造船/军工/地产/装修/部分设备): 签单即收首付,
    余额先涨、1.5-3年后转收入; 环比净增连续萎缩 = 新签退潮的最早警报(领先收入
    拐点 1-2 年)。注意交付旺季余额天然承压(确认收入消耗大), 跨期对比须剔季节性
    (松发 2025Q4 仅+5.5% 即交付冲刺所致, 非订单退潮)。

    → [{"date","cl_yi","d_yi","pct"}] 最新在前, 共 periods 期; 失败/无数据返回 []。
    非预收模式公司余额≈0, 调用方自行决定是否展示。"""
    c = str(code).split(".")[0]
    url = (DC_WEB + "?reportName=RPT_F10_FINANCE_GBALANCE"
           "&filter=(SECUCODE%3D%22" + c + (".SH%22)" if c.startswith(("6", "9")) else ".SZ%22)")
           + "&columns=SECUCODE,REPORT_DATE,CONTRACT_LIAB&pageSize=10"
           "&sortColumns=REPORT_DATE&sortTypes=-1")
    try:
        rows = ((_get(url, headers=DC_HDR) or {}).get("result") or {}).get("data") or []
        seq = [(str(r.get("REPORT_DATE") or "")[:10], r.get("CONTRACT_LIAB"))
               for r in rows if r.get("CONTRACT_LIAB") is not None]
        seq.sort(key=lambda x: x[0], reverse=True)
        out = []
        for i, (dt, v) in enumerate(seq[:periods]):
            d_yi = pct = None
            if i + 1 < len(seq) and seq[i + 1][1]:
                d_yi = round((v - seq[i + 1][1]) / 1e8, 2)
                pct = round((v / seq[i + 1][1] - 1) * 100, 1)
            out.append({"date": dt, "cl_yi": round(v / 1e8, 2), "d_yi": d_yi, "pct": pct})
        return out
    except Exception:
        return []



# ---------------- 模块1.4: 财报高增榜(单季归母>40% × 披露后涨幅, v3.4 2026-11-16) ----------------
# 每日1次问财筛「最新报告期单季归母同比>40%且≥1亿」(候选=单季归母Top100),
# 基准价=披露日当日收盘(前复权, ≤披露日的最后一根), 现价=批量行情; 按披露后涨幅排Top20。
# 基准价按报告期缓存 cache/earnings_momentum.json, 次日起仅刷行情; 同日重跑幂等。

EMOM_CACHE = os.path.join(SKILL_DIR, "cache", "earnings_momentum.json")

TENSCREENER_TRIAGE = os.path.join(SKILL_DIR, "..", "tenscreener", "scripts", "triage.py")


def tenscreener_triage(codes, names=None):
    """批量十倍股分诊(tenscreener triage, 腾讯K线+东财F10 全免费)。→ {code: row} 或 None(降级)。
    v3.5(2026-11-17): 生命周期硬判(用户规则: 已涨≥5x+回撤≥30%+反弹<90%前高=已兑现否决)
    + Stage1三线闸门 → A趋势候选/B左侧回调/C闸门未过/done已兑现。"""
    if not codes or not os.path.exists(TENSCREENER_TRIAGE):
        return None
    args = ["python3", TENSCREENER_TRIAGE, "--codes", ",".join(codes), "--json"]
    if names:
        args += ["--names", ",".join(str(names.get(c) or "") for c in codes)]
    try:
        out = subprocess.check_output(args, timeout=420,
                                      stderr=subprocess.DEVNULL).decode("utf-8", "ignore")
        m = re.search(r"\[.*\]", out, re.S)
        rows = json.loads(m.group(0)) if m else []
        return {r.get("code"): r for r in rows if r.get("code")}
    except Exception:
        return None


def latest_report_period(today=None):
    """季节自适应: 最近一个已披露完毕的报告期 → (问财表述, 报告期标签)。
    11-1月→本年三季报; 2-4月→上年年报; 5-7月→本年一季报; 8-10月→本年中报。"""
    t = today or datetime.date.today()
    y, m = t.year, t.month
    if m >= 11:
        return ("%d年三季报" % y, "%dQ3" % y)
    if m <= 1:
        return ("%d年三季报" % (y - 1), "%dQ3" % (y - 1))
    if m <= 4:
        return ("%d年年报" % (y - 1), "%dFY" % (y - 1))
    if m <= 7:
        return ("%d年一季报" % y, "%dQ1" % y)
    return ("%d年中报" % y, "%dQ2" % y)


def _norm_ymd(s):
    """问财公告日期"20261031" → "2026-10-31"; 异常返回 None。"""
    s = str(s).replace("-", "").replace("/", "")[:8]
    return "%s-%s-%s" % (s[:4], s[4:6], s[6:8]) if len(s) == 8 and s.isdigit() else None


def earnings_high_growth_screen(period_label, limit=100):
    """问财筛选(每日1次, 占额度): 单季归母同比>40% 且单季归母≥1亿, 按单季归母降序。
    → [{code, name, np_yi, yoy_pct, disc_date}] 或 []。"""
    # 2026-11-18 问财语法漂移修复: "单季"措辞被映射回累计口径, 须用官方术语"单季度";
    # 字段前缀随之变为 "单季度.归属于母公司所有者的净利润..." (试金石: 中远海控单季+81须入榜)
    q = ("%s单季度归母净利润同比增长率大于40%%且单季度归母净利润大于1亿元"
         "且%s披露日期在2020年1月1日至2030年12月31日之间的股票,"
         "按单季度归母净利润从高到低排序" % (period_label, period_label))
    out = []
    for r in _wencai(q, limit):
        name = _pick(r, "股票简称")
        if not name or "ST" in name.upper() or name.startswith("退"):   # ST/退市警示出局(对齐earnings-reader闸门)
            continue
        dd = _norm_ymd(_pick(r, "公告日期[", "定期报告预计披露日期["))
        try:
            out.append({"code": str(_pick(r, "股票代码") or "").split(".")[0],
                        "name": name,
                        "np_yi": round(float(_pick(r, "单季度.归属于母公司所有者的净利润[",
                                                   "单季度_归母净利润[")) / 1e8, 1),
                        "yoy_pct": round(float(_pick(r, "单季度.归属于母公司所有者的净利润同比增长率[",
                                                     "单季度_归母净利润同比增长率[",
                                                     "归属于母公司所有者的净利润同比增长率[")) , 1),
                        "disc_date": dd})
        except (TypeError, ValueError):
            continue
    return out


def _disc_base_close(code, disc_date):
    """披露日基准收盘: K线(前复权)中 ≤披露日 的最后一根。失败返回 None。"""
    base = None
    try:
        for row in stock_kline(code, days=60):
            if row["date"] <= disc_date:
                base = row["close"]
    except Exception:
        return None
    return base


def earnings_momentum(n=20, cand_limit=100):
    """财报高增Top20 主入口。
    → ({period_label, period, updated, rows:[{code,name,np_yi,yoy_pct,disc_date,
        base,price,chg_pct,streak}]}, err_or_None)。问财空返回时 rows=[] + err。"""
    label, period = latest_report_period()
    cands = earnings_high_growth_screen(label, cand_limit)
    if not cands:
        return {"period_label": label, "period": period,
                "rows": []}, "问财筛选无返回"
    cache = {}
    try:
        cache = json.load(open(EMOM_CACHE))
    except Exception:
        pass
    if cache.get("period") != period:          # 报告期滚动 → 缓存重建(基准价/连榜清零)
        cache = {"period": period, "base": {}, "streak": {}, "updated": None}
    quotes = stock_quotes_batch([c["code"] for c in cands])
    rows = []
    for c in cands:
        if not c["disc_date"]:
            continue
        b = cache["base"].get(c["code"])
        if not b:
            b = _disc_base_close(c["code"], c["disc_date"])
            if b:
                cache["base"][c["code"]] = b
        price = (quotes.get(c["code"]) or {}).get("price")
        if not b or not price:
            continue
        rows.append(dict(c, base=round(b, 2), price=price,
                         chg_pct=round((price / b - 1) * 100, 1)))
    rows.sort(key=lambda x: -x["chg_pct"])
    top = rows[:n]
    today_str = datetime.date.today().isoformat()
    if cache.get("updated") != today_str:       # 连榜计数: 一日一涨, 同日重跑幂等
        today_codes = {t["code"] for t in top}
        for code in list(cache["streak"]):
            if code not in today_codes:
                cache["streak"][code] = 0
        for t in top:
            cache["streak"][t["code"]] = cache["streak"].get(t["code"], 0) + 1
        cache["updated"] = today_str
    try:                                        # 落盘始终执行: 同日重跑也要持久化新拉的基准价
        os.makedirs(os.path.dirname(EMOM_CACHE), exist_ok=True)
        json.dump(cache, open(EMOM_CACHE, "w"), ensure_ascii=False)
    except Exception:
        pass
    for t in top:
        t["streak"] = cache["streak"].get(t["code"], 1)
    return {"period_label": label, "period": period,
            "updated": today_str, "rows": top}, None


# ---------------- v3.6: 通用渠道扩展(调研纪要走公告分级/互动问答/研报/指标注册表) ----------------

def irm_qa_recent(name, code, days=7, limit=5):
    """互动易(深市)近N日新问答: [{q, a, date}]。keyWord=公司名搜索 + stockCode
    客户端过滤 + updateDate 窗口过滤。沪市走上证e互动(会话墙, 2026-11-18 实测
    不可编程访问)——沪市代码直接返回 []。失败/无新问答返回 [](降级不阻塞)。
    mainContent=投资者提问, attachedContent=公司回复(空=未回复)。"""
    c = str(code).split(".")[0]
    if not c.startswith(("0", "2", "3")):
        return []
    try:
        d = _post("http://irm.cninfo.com.cn/newircs/index/search",
                  {"pageNo": 1, "pageSize": 30, "searchTypes": 11, "keyWord": name},
                  headers={"User-Agent": "Mozilla/5.0"})
        cutoff = time.time() * 1000 - days * 86400 * 1000
        out = []
        for r in d.get("results") or []:
            if str(r.get("stockCode") or "").split(".")[0] != c:
                continue
            ts = max(int(r.get("updateDate") or 0), int(r.get("pubDate") or 0))
            if ts < cutoff:
                continue
            out.append({"q": (r.get("mainContent") or "").strip(),
                        "a": (r.get("attachedContent") or "").strip(),
                        "date": time.strftime("%Y-%m-%d", time.localtime(ts / 1000))})
            if len(out) >= limit:
                break
        return out
    except Exception:
        return []


def stock_reports_recent(code, days=14, limit=5):
    """东财研报中心近N日研报(免费): [{title, org, rating, date}]。2026-11-18 实测
    松发股份返回东吴/国金/开源等真实研报。失败返回 []。"""
    c = str(code).split(".")[0]
    end = datetime.date.today()
    begin = end - datetime.timedelta(days=days)
    try:
        d = _get("https://reportapi.eastmoney.com/report/list?industryCode=*&pageSize=%d"
                 "&industry=*&rating=*&ratingChange=*&beginTime=%s&endTime=%s"
                 "&pageNo=1&qType=0&code=%s" % (limit, begin.isoformat(), end.isoformat(), c),
                 headers=DC_HDR)
        out = []
        for r in d.get("data") or []:
            out.append({"title": (r.get("title") or "").strip(),
                        "org": r.get("orgSName") or "",
                        "rating": r.get("sRatingName") or "",
                        "date": (r.get("publishDate") or "")[:10]})
        return out
    except Exception:
        return []


IND_EXAMPLE = os.path.join(SKILL_DIR, "indicators.example.json")
IND_JSON = os.path.join(SKILL_DIR, "indicators.json")


def industry_indicators():
    """通用行业高频指标注册表 -> [{name, value, chg_pct, freq, note}]。
    配置: indicators.json(优先, gitignored 个人层) 否则 indicators.example.json。
    provider 两种: eastmoney_index(东财全球指数, 日频自动, BDI 实测可用) /
    manual(月度手更——value/date/chg_pct 写在配置里)。单条失败静默跳过。"""
    cfg = {}
    for fp in (IND_JSON, IND_EXAMPLE):
        if os.path.exists(fp):
            try:
                cfg = json.load(open(fp, encoding="utf-8"))
            except Exception:
                cfg = {}
            break
    out = []
    for it in cfg.get("indicators") or []:
        try:
            if it.get("provider") == "eastmoney_index":
                v = (_get("https://push2.eastmoney.com/api/qt/stock/get?secid=%s"
                          "&fields=f43,f58,f170" % it["secid"]).get("data") or {})
                if v.get("f43") is not None:
                    out.append({"name": it["name"], "value": v["f43"] / 100.0,
                                "chg_pct": (v["f170"] / 100.0
                                            if v.get("f170") is not None else None),
                                "freq": it.get("freq", "daily"), "note": it.get("note", "")})
            elif it.get("provider") == "manual" and it.get("value") is not None:
                out.append({"name": it["name"], "value": it.get("value"),
                            "chg_pct": it.get("chg_pct"),
                            "freq": it.get("freq", "monthly"),
                            "note": it.get("date", "")})
        except Exception:
            continue
    return out
