#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""data.py — earnings-reader 取数层。

移植自 h1-monitor v7.3(阿里云版 pipeline.py) + framework_filter.py L1.5 取数,
Mac 本机运行(python3.9+), 原 py3.6 兼容枷锁解除。接口实测坑注释一并移植。

设计:
- 全免费源优先(东财/腾讯); 问耗额度仅在 iw_cards(筹码)与备源, 见族 CONVENTIONS.md
- 每个函数独立降级: 失败返回 None/[]/{} 并记入 DEGRADED, pipeline 落 manifest.json,
  永不因单点失败阻塞整体(抄 a-share-review 模式)
- 问财 key 只从环境变量 IWENCAI_API_KEY 读, 严禁写进任何文件
"""
import json
import os
import sys
import time
import urllib.parse
import urllib.request

EM = "https://datacenter-web.eastmoney.com/api/data/v1/get"
F10 = "https://datacenter.eastmoney.com/securities/api/data/v1/get"
PUSH2 = "https://push2.eastmoney.com/api/qt/stock/get"
IW_URL = "https://openapi.iwencai.com/v1/query2data"
UA = {"User-Agent": "Mozilla/5.0"}

# 本次运行的降级清单 [(函数, 细节)], pipeline 读走后落 manifest
DEGRADED = []
# 东财本次运行是否至少成功一页(HTTP 可达)。可达+0行=真零披露(如节假日), 与"挂了"区分。
EM_OK = False


def degrade(fn, why):
    DEGRADED.append((fn, why))


def _get(url, params, timeout=30, referer=None, alarm=None):
    """alarm: 秒级硬超时(signal.alarm), 免疫"滴流挂死"(服务器每 25s 滴 1 字节时
    socket timeout 永不触发, 实测 2026-11-04 晚 F10 段整进程挂死于 1800/2355)。"""
    qs = urllib.parse.urlencode(params)
    h = dict(UA)
    if referer:
        h["Referer"] = referer
    req = urllib.request.Request(url + "?" + qs, headers=h)
    if alarm:
        import signal

        def _to(*_a):
            raise TimeoutError("alarm %ss" % alarm)
        old = signal.signal(signal.SIGALRM, _to)
        signal.alarm(alarm)
        try:
            return json.loads(urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8"))
        finally:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, old)
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8"))


def _secu(code):
    """东财 SECUCODE 后缀: 6/9 沪, 4/8 北, 其余深。"""
    return code + (".SH" if code[0] in ("6", "9") else (".BJ" if code[0] in ("4", "8") else ".SZ"))


def _sym(code):
    """腾讯符号(北交所无日线接口, 调用方自弃)。"""
    return ("sh" if code[0] in ("6", "9") else "sz") + code


# ============ ① 披露清单(东财主源, 问财备源) ============

def em_pages(report, date_field, start, end):
    """东财披露清单按公告日窗口翻页读到底。

    实测坑(2026-11-04 本机):
    1. 只按 NOTICE_DATE 窗口过滤, 不带报告期过滤 —— 报告期由 detect_period() 从返回行
       REPORTDATE 众数自动识别(替代旧版换季手改 RPT_DATE 常量)。教训: 若写
       REPORTDATE>=窗口起点, 季末报告期(09-30)全被滤掉, 实测 10-30 只剩 3 条年报预告。
    2. 排序主键必须用 SECURITY_CODE —— 用 NOTICE_DATE 排序时宽窗口(≥2日)分页会大量
       重复(实测 2 日 2408 行仅 970 唯一键, 60% 重复; h1-monitor 旧版"披露983家"即此
       欠采样, 真值 2359 家)。SECURITY_CODE 主键实测 13 页 0 重复。
    3. 行键去重 + 固定点停页(某页零新键即停)双保险; 30 页保险丝。
    date_field 参数保留给调用方记账, 过滤条件不用它。"""
    _ = date_field
    global EM_OK
    seen, pn = {}, 1
    while pn <= 30:
        try:
            d = _get(EM, {"reportName": report, "columns": "ALL", "pageSize": 500, "pageNumber": pn,
                          "sortColumns": "SECURITY_CODE", "sortTypes": 1,
                          "filter": "(NOTICE_DATE>='%s')(NOTICE_DATE<='%s 23:59:59')" % (start, end)})
        except Exception:
            break
        EM_OK = True
        r = (d.get("result") or {}).get("data") or []
        if not r:
            break
        new = 0
        for x in r:
            # 行键: 代码+报告期+公告日+指标字段 —— 预告表一司多指标行(归母/扣非/营收各一行),
            # 三键会把多指标压成一行(实测 3->1), 必须带 PREDICT_FINANCE
            k = (str(x.get("SECURITY_CODE")), str(x.get("REPORTDATE") or x.get("REPORT_DATE") or "")[:10],
                 str(x.get("NOTICE_DATE") or "")[:19], str(x.get("PREDICT_FINANCE") or ""))
            if k not in seen:
                seen[k] = x
                new += 1
        if new == 0 and pn > 1:
            break
        if len(r) < 500:
            break
        pn += 1
        time.sleep(0.2)
    return list(seen.values())


SEASON_LABEL = {"03-31": "一季报", "06-30": "中报", "09-30": "三季报", "12-31": "年报"}


def detect_period(f_rows, w_rows):
    """披露行 REPORTDATE 众数 -> 本季报告期(如 2026-09-30, '三季报')。识别失败返回 (None, '')。"""
    cnt = {}
    for r in f_rows + w_rows:
        rd = str(r.get("REPORTDATE") or r.get("REPORT_DATE") or "")[:10]
        if rd:
            cnt[rd] = cnt.get(rd, 0) + 1
    if not cnt:
        return None, ""
    rpt = max(cnt, key=cnt.get)
    return rpt, SEASON_LABEL.get(rpt[5:], "报告期")


def _iw(query):
    """问财直调(备源/筹码用)。key 只从环境变量取。"""
    key = os.environ.get("IWENCAI_API_KEY", "")
    if not key:
        degrade("iw", "IWENCAI_API_KEY 未配置")
        return []
    import secrets
    body = json.dumps({"query": query, "page": 1, "limit": 200}).encode()
    req = urllib.request.Request(IW_URL, data=body, headers={
        "Authorization": "Bearer " + key, "Content-Type": "application/json",
        "X-Claw-Call-Type": "normal", "X-Claw-Skill-Id": "hithink-finance-query",
        "X-Claw-Skill-Version": "1.0.0", "X-Claw-Plugin-Id": "none",
        "X-Claw-Plugin-Version": "none", "X-Claw-Trace-Id": secrets.token_hex(32)})
    try:
        d = json.loads(urllib.request.urlopen(req, timeout=60).read().decode("utf-8"))
        return d.get("datas") or d.get("data") or []
    except Exception as e:
        degrade("iw", str(e)[:80])
        return []


def _season_tag(start, end):
    """备源用: 按窗口中点月份推断当季报告期 YYYY-MM-DD。"""
    mid = start[:7]
    y, m = int(mid[:4]), int(mid[5:7])
    if m in (1, 2):
        return "%d-12-31" % (y - 1), "年报"
    if m in (3, 4):
        return "%d-03-31" % y, "一季报"
    if m in (7, 8):
        return "%d-06-30" % y, "中报"
    if m in (10, 11):
        return "%d-09-30" % y, "三季报"
    return ("%d-12-31" % y, "年报") if m == 12 else ("%d-06-30" % y, "中报")


def iw_lists(start, end):
    """问财备源清单(仅东财全挂时用, 消耗 2 次额度): 归一成东财行结构。
    问财日期过滤不生效 -> 客户端按公告日期过滤; 报告期由窗口月份推断。"""
    tag, _ = _season_tag(start, end)
    t8 = tag.replace("-", "")
    f_rows = _iw("公布%s的股票 股票代码 股票简称 公告日期 营业收入[%s] 归母净利润[%s]"
                 % (_season_tag(start, end)[1], t8, t8))
    w_rows = _iw("公布%s业绩预告的股票 股票代码 股票简称 公告日期 预告净利润下限[%s] 预告净利润上限[%s]"
                 % (_season_tag(start, end)[1], t8, t8))

    def pick(rows, with_amounts):
        out = []
        for r in rows:
            nd = str(r.get("公告日期") or "")[:10]
            if start <= nd <= end:
                row = {"SECURITY_CODE": r.get("股票代码"), "SECURITY_NAME_ABBR": r.get("股票简称"),
                       "NOTICE_DATE": nd, "_iw": r, "REPORTDATE": tag}
                if with_amounts:
                    row["TOTAL_OPERATE_INCOME"] = r.get("营业收入[%s]" % t8)
                    row["PARENT_NETPROFIT"] = r.get("归母净利润[%s]" % t8)
                out.append(row)
        return out
    return pick(f_rows, True), pick(w_rows, False)


def disclosure_lists(start, end):
    """-> (formal_rows, forecast_rows, src)。东财可达即用(0行=真零披露如节假日, 不耗
    问财额度); 完全不可达才走问财备源。src None = 全挂。"""
    global EM_OK
    f_rows = em_pages("RPT_LICO_FN_CPD", "REPORTDATE", start, end)
    w_rows = em_pages("RPT_PUBLIC_OP_NEWPREDICT", "REPORT_DATE", start, end)
    if EM_OK:
        return f_rows, w_rows, "东财"
    f2, w2 = iw_lists(start, end)
    if f2 or w2:
        return f2, w2, "问财备源"
    return [], [], None


# ============ ② 历史三指标 + 质地(F10, 一司一次全报告期) ============

def f10_amounts(code):
    """-> (cum{报告期:{rev,np,ded}}, qual{质地})。
    坑(实测): F10 host 的 filter 必须双引号; 一次拉全部类型可行, 不必按 REPORT_TYPE 过滤请求。"""
    cum, qual = {}, {}
    try:
        d = _get(F10, {"reportName": "RPT_F10_FINANCE_MAINFINADATA", "columns": "ALL",
                       "pageSize": 16, "pageNumber": 1, "sortColumns": "REPORT_DATE", "sortTypes": -1,
                       "filter": '(SECUCODE="%s")' % _secu(code)}, timeout=15, alarm=12)
    except Exception as e:
        degrade("f10_amounts", "%s %s" % (code, str(e)[:60]))
        return cum, qual
    rows = (d.get("result") or {}).get("data") or []
    ml = {}
    for r in rows:
        if (r.get("REPORT_TYPE") or "") not in ("一季报", "中报", "三季报", "年报"):
            continue
        rd = (r.get("REPORT_DATE") or "")[:10]
        if not rd:
            continue
        cell = cum.setdefault(rd, {})
        for k, col in (("rev", "TOTALOPERATEREVE"), ("np", "PARENTNETPROFIT"), ("ded", "KCFJCXSYJLR")):
            if cell.get(k) is None and r.get(col) is not None:
                cell[k] = r[col]
        if r.get("XSMLL") is not None:
            ml[rd] = r["XSMLL"]
    r0 = rows[0] if rows else {}
    qual = {"ml_trend": sorted(ml.items()),
            "roe": r0.get("ROEJQ"), "roic": r0.get("ROIC"),
            "debt": r0.get("ZCFZL"),
            "ncf_np": (r0["NCO_NETPROFIT"] * 100) if isinstance(r0.get("NCO_NETPROFIT"), (int, float)) else None}
    return cum, qual


# ============ ③ 名称兜底(腾讯) ============

def tx_names(codes):
    """行情接口兜底名称(逐批重试一次)。"""
    out = {}
    for i in range(0, len(codes), 50):
        pre = [("sh" if c[0] in ("6", "9") else "sz") + c for c in codes[i:i + 50]]
        for attempt in (1, 2):
            try:
                req = urllib.request.Request("https://qt.gtimg.cn/q=" + ",".join(pre), headers=UA)
                d = urllib.request.urlopen(req, timeout=15).read().decode("gbk")
                for line in d.strip().split(";"):
                    if "~" not in line:
                        continue
                    p = line.split("~")
                    raw = p[0].split("=")[0].replace("v_", "")
                    c = raw[2:] if raw[:2] in ("sh", "sz") else raw
                    if len(p) > 1 and p[1] and c:
                        out[c] = p[1]
                break
            except Exception:
                if attempt == 2:
                    degrade("tx_names", pre[0] + "...")
                time.sleep(1)
        time.sleep(0.3)
    return out


# ============ ④ 名片数据(只给过闸标的拉) ============

def org_info(code):
    """F10 公司基本信息。坑: 该接口不能带 sortColumns(会返回空)。"""
    out = {}
    try:
        d = _get(F10, {"reportName": "RPT_F10_BASIC_ORGINFO", "columns": "ALL",
                       "pageSize": 1, "pageNumber": 1, "filter": '(SECUCODE="%s")' % _secu(code)})
        r = ((d.get("result") or {}).get("data") or [{}])[0]
        out = {"board": r.get("BOARD_NAME_LEVEL") or "", "main": r.get("MAIN_BUSINESS") or "",
               "holder": r.get("ACTUAL_HOLDER") or "", "listing": (r.get("LISTING_DATE") or "")[:10],
               "emp": r.get("EMP_NUM"), "province": (r.get("ADDRESS") or "")[:2]}
    except Exception as e:
        degrade("org_info", "%s %s" % (code, str(e)[:60]))
    time.sleep(0.1)
    return out


def quotes_batch(codes):
    """push2 ulist 批量行情 -> {code: {mcap, fmcap, pe(动)}}。
    实测教训(2026-11-04 本机): 单股 stock/get 端点 117/117 断连不可用; ulist 批量稳定
    (配方抄 a-share-review: 带 ut 令牌、不带 fltt; f20 总市值(元) f21 流通 f9 PE动÷100)。
    PB/概念在 ulist 无对应字段号 -> 舍弃; 行业展示由 org_info 板块字段覆盖。"""
    if not codes:
        return {}
    out = {}
    for i in range(0, len(codes), 50):
        secids = ",".join((("1." if c[0] in ("6", "9") else "0.") + c) for c in codes[i:i + 50])
        try:
            d = _get("https://push2.eastmoney.com/api/qt/ulist.np/get",
                     {"secids": secids, "fields": "f12,f14,f20,f21,f9",
                      "ut": "fa5fd1943c7b386f172d6893dbfba10b"})
            for it in (d.get("data") or {}).get("diff") or []:
                c = str(it.get("f12") or "")
                if c:
                    out[c] = {"mcap": it.get("f20"), "fmcap": it.get("f21"),
                              "pe": (it["f9"] / 100.0) if isinstance(it.get("f9"), (int, float)) else None}
        except Exception as e:
            degrade("quotes_batch", "%s... %s" % (codes[i][:6], str(e)[:60]))
        time.sleep(0.15)
    return out


def kline_stat(code, year):
    """腾讯日K(320根前复权)算 距一年新高/年初至今。北交所(4/8开头)无此接口, 返回空。
    (东财 push2his 在 h1-monitor 服务器被限流故历史性弃用; 本机如遇腾讯限流可后续加东财备链。)"""
    if code[0] in ("4", "8"):
        return {}
    try:
        u = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=%s,day,,,320,qfq" % _sym(code)
        req = urllib.request.Request(u, headers=UA)
        d = json.loads(urllib.request.urlopen(req, timeout=15).read().decode("utf-8"))
        k = (d.get("data") or {}).get(_sym(code)) or {}
        days = k.get("qfqday") or k.get("day") or []
        pts = [(x[0], float(x[2])) for x in days if len(x) >= 3]
        if pts:
            last, hi = pts[-1][1], max(p[1] for p in pts)
            base = next((p[1] for p in pts if p[0] >= "%d-01-01" % year), None)
            time.sleep(0.15)
            return {"drawdown": (last / hi - 1) * 100 if hi > 0 else None,
                    "ytd": (last / base - 1) * 100 if base else None}
    except Exception as e:
        degrade("kline_stat", "%s %s" % (code, str(e)[:60]))
    time.sleep(0.15)
    return {}


def lift_next(code, today_s):
    """下次解禁(RPT_LIFT_STAGE 日期过滤不生效 -> 客户端过滤); 单位万股/万元/%;
    个别行单位异常 -> 按占流通比 0.05%~100% 闸门过滤。"""
    try:
        d = _get(EM, {"reportName": "RPT_LIFT_STAGE", "columns": "ALL", "pageSize": 60, "pageNumber": 1,
                      "sortColumns": "FREE_DATE", "sortTypes": 1,
                      "filter": '(SECURITY_CODE="%s")' % code})
        for r in (d.get("result") or {}).get("data") or []:
            fd = (r.get("FREE_DATE") or "")[:10]
            ratio = r.get("FREE_RATIO")
            if fd >= today_s and isinstance(ratio, (int, float)) and 0.05 <= ratio <= 100:
                return {"lift_date": fd, "lift_shares": r.get("FREE_SHARES"),
                        "lift_ratio": ratio, "lift_mcap": r.get("LIFT_MARKET_CAP")}
    except Exception as e:
        degrade("lift_next", "%s %s" % (code, str(e)[:60]))
    time.sleep(0.1)
    return {}


def iw_cards(codes, names):
    """问财批量(5家/批, 消耗额度): 机构持股占流通% / 股东户数 / 近一年涨停次数。
    字段名带日期后缀 -> 前缀匹配; 问财数值是字符串须 float。"""
    if not codes:
        return {}
    if not os.environ.get("IWENCAI_API_KEY"):
        degrade("iw_cards", "key 未配置, 筹码行缺省")
        return {}
    out = {}
    for i in range(0, len(codes), 5):
        batch_c, batch_n = codes[i:i + 5], names[i:i + 5]
        rows = _iw("%s 机构持股占流通股比例 股东户数 近一年涨停次数" % " ".join(batch_n))
        for r in rows:
            c = str(r.get("股票代码") or "").split(".")[0]
            if c not in batch_c:
                continue
            cell = out.setdefault(c, {})
            for k, v in r.items():
                if v is None:
                    continue
                try:
                    v = float(v)
                except (TypeError, ValueError):
                    continue
                if k.startswith("机构持股占流通股比例"):
                    cell["inst"] = v
                elif k.startswith("股东户数") or k.startswith("总户数"):
                    cell["holders"] = v
                elif k.startswith("涨停次数"):
                    cell["limits"] = v
        time.sleep(0.5)
    if not out:
        degrade("iw_cards", "问财返回空(额度/网络)")
    return out


def biz_review(code, rpt_date):
    """emweb F10 经营评述(jyps): 只取 REPORT_DATE==本季报告期 的条目, 本期没写就没有。"""
    sym = ("SH" if code[0] in ("6", "9") else "SZ") + code
    try:
        req = urllib.request.Request(
            "https://emweb.securities.eastmoney.com/PC_HSF10/BusinessAnalysis/PageAjax?code=" + sym,
            headers=UA)
        d = json.loads(urllib.request.urlopen(req, timeout=15).read().decode("utf-8"))
        for r in d.get("jyps") or []:
            if (r.get("REPORT_DATE") or "")[:10] == rpt_date and r.get("BUSINESS_REVIEW"):
                time.sleep(0.1)
                return r["BUSINESS_REVIEW"]
    except Exception as e:
        degrade("biz_review", "%s %s" % (code, str(e)[:60]))
    time.sleep(0.1)
    return ""


# ============ ⑤ L1.5 框架补数(移植自 framework_filter.py) ============

def _http_json(url, referer, retries=2, waits=(2, 5)):
    """带重试 GET-JSON(腾讯/东财批量拉取间歇限流)。"""
    last = None
    for i in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Referer": referer})
            return json.loads(urllib.request.urlopen(req, timeout=12).read().decode())
        except Exception as ex:
            last = ex
            if i < retries:
                time.sleep(waits[i] if i < len(waits) else 5)
    raise last


def fetch_quote_em(code):
    """总市值(亿)/PE-TTM。主链东财 datacenter 估值表; 备 push2(f115=TTM)。失败 None。"""
    try:
        url = ("https://datacenter-web.eastmoney.com/api/data/v1/get?reportName=RPT_VALUEANALYSIS_DET"
               "&columns=SECURITY_CODE,TRADE_DATE,TOTAL_MARKET_CAP,PE_TTM"
               "&filter=(SECURITY_CODE%%3D%%22%s%%22)&pageSize=1"
               "&sortColumns=TRADE_DATE&sortTypes=-1" % code)
        row = _http_json(url, "https://data.eastmoney.com")["result"]["data"][0]
        return {"mktcap_yi": round(row["TOTAL_MARKET_CAP"] / 1e8, 1),
                "pe_ttm": round(row["PE_TTM"], 1) if row.get("PE_TTM") else None}
    except Exception:
        pass
    try:
        mid = "1" if code.startswith(("6", "9")) else "0"
        url = ("https://push2.eastmoney.com/api/qt/stock/get?secid=%s.%s"
               "&fields=f43,f116,f115&ut=fa5fd1943c7b386f172d6893dbfba10b" % (mid, code))
        d = _http_json(url, "https://quote.eastmoney.com")["data"]
        return {"mktcap_yi": round(d["f116"] / 1e8, 1),
                "pe_ttm": (d["f115"] / 100) if isinstance(d.get("f115"), (int, float)) else None}
    except Exception:
        degrade("fetch_quote_em", code)
        return None


_MT_FAIL_STREAK = 0   # 腾讯月K连续失败计数(熔断器)


def fetch_monthly_tx(code, months=200):
    """腾讯月K(前复权, 主月K失败降级周K): 近3年低点倍数/上市以来倍数/近3月近6月涨幅/是否历史新高。
    实测教训(2026-11-04 晚): 腾讯 ifzq 间歇性整体拦截(WAF), 每家烧满重试阶梯可达 ~90s,
    106 家要 2.6h -> 熔断器: 连续 8 家全失败即跳过剩余(realized_3y=None, 框架按保守中档记分)。"""
    global _MT_FAIL_STREAK
    if _MT_FAIL_STREAK >= 8:
        return None
    sym = _sym(code)
    k, kind = None, None
    for _kind, _bars3y, _b3, _b6 in (("month", 36, 4, 7), ("week", 156, 13, 26)):
        try:
            d = _http_json(
                "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=%s,%s,,,%d,qfq"
                % (sym, _kind, months), "https://gu.qq.com", retries=1)
            _k = ((d.get("data") or {}).get(sym) or {}).get("qfq" + _kind)
            if _k:
                k, kind, bars3y, b3, b6 = _k, _kind, _bars3y, _b3, _b6
                break
        except Exception:
            continue
    _ = kind
    if not k:
        _MT_FAIL_STREAK += 1
        if _MT_FAIL_STREAK == 8:
            degrade("fetch_monthly_tx", "熔断: 连续8家失败, 剩余跳过(腾讯晚间拦截)")
        return None
    _MT_FAIL_STREAK = 0
    try:
        closes = [float(x[2]) for x in k]
        cur = closes[-1]
        w3y = closes[-(bars3y + 1):-1] if len(closes) > bars3y else closes[:-1]
        lows_all = [float(x[4] if len(x) > 4 else x[2]) for x in k]
        return {
            "realized_3y": round(cur / min(w3y), 2) if w3y else None,
            "realized_ipo": round(cur / min(lows_all), 2) if lows_all else None,
            "near_3m": round(cur / closes[-b3] - 1, 4) if len(closes) >= b3 else None,
            "near_6m": round(cur / closes[-b6] - 1, 4) if len(closes) >= b6 else None,
            "hist_high": abs(cur - max(closes)) < 1e-9,
        }
    except Exception:
        degrade("fetch_monthly_tx", code)
        return None


def fetch_annual_np_em(code, years=3):
    """东财 RPT_LICO_FN_CPD: 近N年年度归母+营收(亿元)。失败 None。"""
    url = ("https://datacenter-web.eastmoney.com/api/data/v1/get?reportName=RPT_LICO_FN_CPD"
           "&columns=SECURITY_CODE,REPORTDATE,TOTAL_OPERATE_INCOME,PARENT_NETPROFIT"
           "&filter=(SECURITY_CODE%%3D%%22%s%%22)&pageSize=24"
           "&sortColumns=REPORTDATE&sortTypes=-1" % code)
    try:
        req = urllib.request.Request(url, headers={"Referer": "https://data.eastmoney.com", **UA})
        d = json.loads(urllib.request.urlopen(req, timeout=10).read().decode())
        rows = [r for r in d["result"]["data"] if r["REPORTDATE"][:10].endswith("-12-31")][:years]
        return [{"year": int(r["REPORTDATE"][:4]),
                 "rev_yi": round(r["TOTAL_OPERATE_INCOME"] / 1e8, 2),
                 "np_yi": round(r["PARENT_NETPROFIT"] / 1e8, 3)} for r in rows]
    except Exception:
        degrade("fetch_annual_np_em", code)
        return None


def fetch_all(code):
    """L1.5 聚合(市值/PE、月K倍数、近3年年度)。"""
    out = {}
    for fn in (fetch_quote_em, fetch_monthly_tx):
        r = fn(code)
        if r:
            out.update(r)
    annual = fetch_annual_np_em(code)
    if annual:
        out["annual_np3"] = annual
    return out or None


def fetch_market_status():
    """上证+创业板: 20日涨跌+MA5/MA20 -> 一句话(L2 报告头用); 失败返回 ''。"""
    try:
        parts = []
        for sym, name in (("sh000001", "上证"), ("sz399006", "创业板")):
            d = _http_json(
                "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=%s,day,,,60,qfq" % sym,
                "https://gu.qq.com", retries=2)
            k = (d.get("data") or {}).get(sym) or {}
            days = k.get("qfqday") or k.get("day") or []
            closes = [float(x[2]) for x in days]
            if len(closes) < 21:
                continue
            chg20 = (closes[-1] / closes[-21] - 1) * 100
            ma5 = sum(closes[-5:]) / 5.0
            ma20 = sum(closes[-20:]) / 20.0
            parts.append("%s20日%+.1f%%·%s" % (name, chg20, "多头" if ma5 > ma20 else "空头"))
        return ("大盘: " + " / ".join(parts)) if parts else ""
    except Exception:
        return ""


# ============ F10 批量子进程模式(看门狗) ============
# 实测教训(2026-11-04 深夜): F10 host 间歇性把 SSL 握手/DNS 解析黑洞化,
# socket timeout 与 signal.alarm 均拦不住(macOS getaddrinfo 不受信号中断),
# 单线程直调会整进程挂死。解法: 批量跑在子进程里, 外层看门狗超时强杀重试一次。
#
# 子进程模式: python3 data.py f10batch 代码1,代码2,...  -> stdout 一行 JSON {code: [cum, qual]}

def f10_batch_worker(codes):
    import json as _j
    out = {}
    for c in codes:
        cum, qual = f10_amounts(c)
        if cum:
            out[c] = [cum, qual]
    print(_j.dumps(out, ensure_ascii=False))


def f10_batch(codes, chunk=50, timeout=150, progress=None):
    """看门狗批量 F10 -> {code: (cum, qual)}。挂死块强杀重试一次, 二次失败降级跳过。
    progress(done, total) 每块回调。"""
    import subprocess as _sp
    import sys as _sys
    here = os.path.dirname(os.path.abspath(__file__))
    out = {}
    for i in range(0, len(codes), chunk):
        batch = codes[i:i + chunk]
        arg = ",".join(batch)
        for attempt in (1, 2):
            try:
                p = _sp.run([_sys.executable, os.path.abspath(__file__), "f10batch", arg],
                            stdout=_sp.PIPE, stderr=_sp.PIPE, timeout=timeout, cwd=here)
                got = json.loads((p.stdout or b"").decode("utf-8", "replace").strip().splitlines()[-1])
                for c, (cum, qual) in got.items():
                    out[c] = (cum, qual)
                break
            except Exception as e:
                if attempt == 2:
                    degrade("f10_batch", "块%d看门狗超时跳过(%s): %s" % (i // chunk, batch[0], str(e)[:40]))
                else:
                    time.sleep(2)
        time.sleep(0.1)
        if progress:
            progress(min(i + chunk, len(codes)), len(codes))
    return out


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "f10batch":
        f10_batch_worker(sys.argv[2].split(","))
