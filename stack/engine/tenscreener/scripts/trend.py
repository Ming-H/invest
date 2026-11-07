#!/usr/bin/env python3
"""
trend.py — 跨期趋势信号引擎(确定性代码, 2026-11-05)。

设计原则(用户定版): 单期阅读是体检, 跨期对比才是趋势判断; 凡能算的信号全部用
代码硬算并强制渲染进素材包与报告(记分卡机制), Claude 只做代码算不了的叙述对比。

七类信号(每条: 方向↑/→/↓ + 证据 + 出处):
  A 指引漂移   厚报告展望+经营评述抽"全年收入/利润指引"区间 → 时间线, 连续上调=强升
  B 股东身份   十大流通股东按身份分类(社保/汇金/证金=国家队, 香港中央结算=北向,
               保险=险资, 基金=公募), 新进/加仓/家数趋势 —— 社保出现或变多=国家队入场
  C 表述漂移   上升/风险关键词 × 报告期命中矩阵, 命中递增=叙述走强
  D 先行斜率   合同负债/存货vs收入/capex收入比/应收vs收入 —— 方向+加速度
  E 盈利质量   非经常占比(投资收益+公允)/归母 逐期 —— 水分扩大/收敛
  F 收现质量   经营净额/归母 逐期
  G 三线形态   复用 quarters.analyze_quarterly(streak/共振/加速/降档/背离)

纯标准库, 全函数可离线单测。
"""
import re

# ─── A. 指引漂移 ───────────────────────────────────────────

# 指引抽取(句子级, 实测教训: 药明原文"上调2026年全年业绩指引。预计2026年公司整体收入
# 由585-605亿元上调至…" —— "全年"与数字区间分居两个句子, 旧的整段正则被句号挡住):
#   先断句 → 句内须命中提示词(预计/指引/上调/全年…) + "指标 金额区间"(如 收入 585-605亿)
#   区间两种写法: "300-320亿元" 与 "300亿元至320亿元"; 百分比区间(35%-39%)因高位数后
#   强制单位而自然排除
_GUID_RANGE = re.compile(
    r"(收入|营收|净利|业绩|盈利)[^。;；\n]{0,40}?(\d[\d,，.]{0,12})\s*"
    r"(?:万亿元|亿元|万元|万亿|亿|万|元)?\s*[-—~至]\s*"
    r"(\d[\d,，.]{0,12})\s*(万亿元|亿元|万元|万亿|亿|万|元)")
_GUID_RAISE = re.compile(
    r"(?:上调至|调整至|升至|提高至)[^。;；\n]{0,20}?(\d[\d,，.]{0,12})\s*"
    r"(?:万亿元|亿元|万元|万亿|亿|万|元)?\s*[-—~至]\s*"
    r"(\d[\d,，.]{0,12})\s*(万亿元|亿元|万元|万亿|亿|万|元)")
_GUID_HINT = re.compile(r"预计|指引|上调|调整至|维持|有望|完成度|目标|全年|年度")


def _num(s):
    try:
        return float(str(s).replace(",", "").replace("，", "").replace("。", ""))
    except (ValueError, TypeError):
        return None


def _to_yi(v, unit):
    if v is None:
        return None
    u = (unit or "").replace("元", "")
    if "万亿" in (unit or ""):
        return round(v * 1e12 / 1e8, 1)
    if u == "亿" or unit == "亿元":
        return round(v, 1)
    if u == "万" or unit == "万元":
        return round(v * 1e4 / 1e8, 2)
    return round(v / 1e8, 2) if v > 1e6 else v   # 裸元


def _emit_guidance_from_blob(out, blob, period, source):
    """断句 → 句内提示词 + 指标区间(含"上调至"新区间)。"""
    for sent in re.split(r"[。;；\n]", blob):
        sent = sent.strip()
        if len(sent) < 10 or not _GUID_HINT.search(sent):
            continue
        for m in _GUID_RANGE.finditer(sent):
            lo, hi = _num(m.group(2)), _num(m.group(3))
            if lo is None or hi is None or hi < lo or hi <= 0:
                continue
            # metric 取「最靠近数字」的指标词(实测: "全年业绩指引,…整体收入由585-605亿"
            # 左扫会先命中句首的'业绩', 而语义上是收入指引)
            raw_metric = None
            for km in re.finditer(r"收入|营收|净利|业绩|盈利", sent[:m.start(2)]):
                raw_metric = km.group(0)
            raw_metric = raw_metric or m.group(1)
            metric = "净利" if raw_metric == "净利" else ("收入" if raw_metric in ("收入", "营收") else "业绩")
            out.append({"period": period, "source": source, "metric": metric,
                        "lo_yi": _to_yi(lo, m.group(4)), "hi_yi": _to_yi(hi, m.group(4)),
                        "sentence": sent[:120]})
            # "由X上调至Y": 追加新区间(上调后的目标 —— 趋势信号的主体)
            r = _GUID_RAISE.search(sent[m.end():])
            if r:
                rlo, rhi = _num(r.group(1)), _num(r.group(2))
                if rlo and rhi and rhi >= rlo:
                    out.append({"period": period, "source": source, "metric": metric,
                                "lo_yi": _to_yi(rlo, r.group(3)), "hi_yi": _to_yi(rhi, r.group(3)),
                                "sentence": ("上调至: " + sent)[:120]})


def extract_guidance(texts, jyps_list):
    """从 报告章节(展望/MD&A/全文) + 经营评述 抽指引区间(含"上调至"新区间)。
    texts: [{label, cn, thick, chapters:{...}}]; jyps_list: [(date_str, review_text)]
    -> [{period, source, lo_yi, hi_yi, metric, sentence}]。"""
    out = []
    for t in texts or []:
        blob = "\n".join(v for v in (t.get("chapters") or {}).values() if isinstance(v, str))
        _emit_guidance_from_blob(out, blob, t["label"], t["cn"])
    for dt, rv in jyps_list or []:
        if rv:
            _emit_guidance_from_blob(out, rv, dt[:7].replace("-", "M"), "经营评述")
    return out


def _period_key(p):
    """期次标签 → 可排序时间键。'2025Q4'→(2025,12) '2026Q2'→(2026,6) '2026M09'→(2026,9)。
    实测教训: 字符串排序会把 '2026M09' 排在 '2026Q2' 前(M<Q), 指引时间线被倒置, 涨判成跌。"""
    p = str(p)
    m = re.match(r"(\d{4})Q([1-4])", p)
    if m:
        return (int(m.group(1)), int(m.group(2)) * 3)
    m = re.match(r"(\d{4})M(\d{2})", p)
    if m:
        return (int(m.group(1)), int(m.group(2)))
    return (9999, 0)


def guidance_verdict(guidance, metric="收入"):
    """同一 metric 的指引时间线 -> {dir, steps, note}。dir: 连续上调/上调/维持/下调/无数据。
    判定: 相邻期中枢(mid)比较; 同期多源取最晚。"""
    rows = [g for g in guidance if g.get("metric") == metric]
    if not rows:
        return {"dir": "无数据", "steps": [], "note": "未抽到%s指引区间" % metric}
    # 同期取一条(后出现的覆盖); 同句"由X上调至Y"的 Y 是该期现行指引
    by_p = {}
    for g in rows:
        by_p[g["period"]] = g
    tl = sorted(by_p.values(), key=lambda g: _period_key(g["period"]))
    mids = [(g["period"], round((g["lo_yi"] + g["hi_yi"]) / 2, 1)) for g in tl]
    ups = downs = 0
    for i in range(1, len(mids)):
        chg = mids[i][1] - mids[i - 1][1]
        if chg > 0.5:
            ups += 1
        elif chg < -0.5:
            downs += 1
    # 期内上调: "由585-605上调至610-619" 单期即一次上调(实测药明 26Q3 句式)
    intra_ups = sum(1 for g in rows if str(g.get("sentence", "")).startswith("上调至:"))
    ups += intra_ups
    steps = ["%s: %s~%s亿(中枢%.0f)" % (g["period"], g["lo_yi"], g["hi_yi"],
                                          (g["lo_yi"] + g["hi_yi"]) / 2) for g in tl]
    if intra_ups:
        steps.append("(期内上调×%d: 由旧区间上调至现行)" % intra_ups)
    if ups >= 2 and downs == 0:
        d = "连续上调"
    elif ups > downs:
        d = "上调"
    elif downs > ups:
        d = "下调"
    else:
        d = "维持"
    return {"dir": d, "steps": steps,
            "note": "%s指引时间线: %s" % (metric, " → ".join(steps))}


# ─── B. 股东身份信号 ────────────────────────────────────────

IDENTITY_RULES = [
    ("国家队-社保", r"社保基金|全国社保|社保[0-9零一二三四五六七八九十]{1,3}组|基本养老保险|养老基金"),
    ("国家队-汇金证金", r"中央汇金|中国证券金融|证金"),
    ("北向", r"香港中央结算"),
    ("险资", r"保险|人寿|财险|养老保险股份"),
    ("公募基金", r"基金"),
    ("券商资管", r"证券股份|证券资管|证券有限责任公司"),
]


def holder_identity(name):
    name = str(name or "")
    for label, pat in IDENTITY_RULES:
        if re.search(pat, name):
            return label
    return "其他机构"


def holders_timeline(freeholder_rows, n_periods=4):
    """十大流通股东按期×身份聚合。freeholder_rows: RPT_F10_EH_FREEHOLDERS 原始行。
    -> {"timeline": [{period, by_id: {身份: [股东名...]}, n_inst}],
        "national_team": {dir, evidence}, "north_diff": ...}"""
    by_end = {}
    for r in freeholder_rows or []:
        by_end.setdefault((r.get("END_DATE") or "")[:10], []).append(r)
    ends = sorted(by_end, reverse=True)[:n_periods]
    tl = []
    for e in ends:
        ids = {}
        for r in by_end[e]:
            ids.setdefault(holder_identity(r.get("HOLDER_NAME")), []).append(r.get("HOLDER_NAME"))
        tl.append({"period": e, "by_id": {k: v for k, v in ids.items() if k != "其他机构"},
                   "n_inst": sum(len(v) for k, v in ids.items() if k != "其他机构")})
    # 国家队信号(社保+汇金证金): 家数/出现 跨期对比
    nt_counts = []
    for t in tl:
        c = len(t["by_id"].get("国家队-社保", [])) + len(t["by_id"].get("国家队-汇金证金", []))
        nt_counts.append((t["period"], c, t["by_id"].get("国家队-社保", []) + t["by_id"].get("国家队-汇金证金", [])))
    nt = {"dir": "无数据", "evidence": []}
    if nt_counts:
        newest, oldest = nt_counts[0], nt_counts[-1]   # ends 降序: [0]=最新
        if newest[1] > oldest[1]:
            nt = {"dir": "↑增强", "evidence": ["%s %d家 → %s %d家%s" % (
                oldest[0], oldest[1], newest[0], newest[1],
                "(含社保)" if any("社保" in str(x) for x in newest[2]) else "")]}
        elif newest[1] == 0 and oldest[1] > 0:
            nt = {"dir": "↓退出", "evidence": ["%s %d家 → %s 0家" % (oldest[0], oldest[1], newest[0])]}
        elif newest[1] == oldest[1] and newest[1] > 0:
            nt = {"dir": "→在场", "evidence": ["%s~%s 持续%d家" % (oldest[0], newest[0], newest[1])]}
    return {"timeline": tl, "national_team": nt}


# ─── C. 表述漂移: 关键词命中矩阵 ────────────────────────────

UP_WORDS = ["供不应求", "满产", "满销", "排队", "涨价", "提价", "量价齐升", "出海",
            "订单饱满", "订单充足", "产能瓶颈", "超预期", "创历史新高", "创新高",
            "需求旺盛", "上调", "扩产", "产能扩张", "高景气", "供小于求", "翻番"]
RISK_WORDS = ["降价", "价格战", "下滑", "库存积压", "需求疲软", "不及预期", "下调",
              "制裁", "贸易摩擦", "亏损扩大", "产能闲置", "去库存"]


def keyword_matrix(texts, jyps_list=None):
    """上升/风险词 × 报告源 命中计数矩阵。-> {up: {word: {源: n}}, risk: {...},
    trend: '上升词占优/风险词占优/均衡/无信号'}"""
    sources = []
    for t in texts or []:
        blob = "\n".join(v for v in (t.get("chapters") or {}).values() if isinstance(v, str))
        sources.append((t["cn"], blob))
    for dt, rv in (jyps_list or []):
        if rv:
            sources.append(("评述" + dt[5:7], rv))
    up, risk = {}, {}
    for word in UP_WORDS:
        up[word] = {src: blob.count(word) for src, blob in sources if blob.count(word)}
    for word in RISK_WORDS:
        risk[word] = {src: blob.count(word) for src, blob in sources if blob.count(word)}
    up_total = sum(sum(v.values()) for v in up.values())
    risk_total = sum(sum(v.values()) for v in risk.values())
    if up_total + risk_total == 0:
        trend = "无信号"
    elif up_total >= risk_total * 2:
        trend = "上升词占优(%d:%d)" % (up_total, risk_total)
    elif risk_total >= up_total * 2:
        trend = "风险词占优(%d:%d)" % (risk_total, up_total)
    else:
        trend = "均衡(up%d/risk%d)" % (up_total, risk_total)
    return {"up": {k: v for k, v in up.items() if v}, "risk": {k: v for k, v in risk.items() if v},
            "up_total": up_total, "risk_total": risk_total, "trend": trend}


# ─── D/E/F. 数字趋势判定(斜率) ──────────────────────────────

def slope(series):
    """series: [(label, value)] 早期→近。-> {dir, chg_pct, note}
    dir: 加速升/升/平/降/数据不足。升=末值>首值*1.05; 加速升=后半段涨幅>前半段。"""
    vals = [(l, v) for l, v in series if isinstance(v, (int, float))]
    if len(vals) < 3:
        return {"dir": "数据不足", "chg_pct": None, "note": "%d期" % len(vals)}
    first, last = vals[0][1], vals[-1][1]
    chg = round((last / first - 1) * 100, 1) if first and first > 0 else None
    half = len(vals) // 2
    h1 = vals[half][1] - vals[0][1]
    h2 = vals[-1][1] - vals[half][1]
    if chg is None:
        d = "数据异常"
    elif chg >= 5 and h2 >= h1:
        d = "加速升" if h2 > h1 * 1.2 else "升"
    elif chg >= 5:
        d = "升(放缓)"
    elif chg <= -5:
        d = "降"
    else:
        d = "平"
    return {"dir": d, "chg_pct": chg, "note": " → ".join("%s%.1f" % (l, v) for l, v in vals)}


def leads_verdict(balance, income, cashflow, qs_labels, rev_cum_yi_by_period=None):
    """先行指标趋势判定。balance/cashflow: judge 的 balance_leads/cashflow_leads(早期→近
    排序前的原序, 此处统一 reverse 成早期→近)。income: income_leads。
    -> [{信号, 方向, 证据, 释义}]"""
    out = []
    if balance:
        b = list(reversed(balance))
        cl = [(r["label"], r.get("合同负债")) for r in b]
        s = slope(cl)
        out.append({"信号": "合同负债(订单先行)", "方向": s["dir"], "证据": s["note"],
                    "释义": "客户预付款持续上升=未来收入可见度增强" if "升" in s["dir"] else "观察"})
        inv = [(r["label"], r.get("存货")) for r in b]
        s2 = slope(inv)
        out.append({"信号": "存货", "方向": s2["dir"], "证据": s2["note"],
                    "释义": "结合收入增速判断主动备货或滞销(见对比行)"})
        rec = [(r["label"], r.get("应收账款")) for r in b]
        s3 = slope(rec)
        out.append({"信号": "应收账款", "方向": s3["dir"], "证据": s3["note"],
                    "释义": "应收增速显著超收入=压货风险"})
    if cashflow:
        c = list(reversed(cashflow))
        fcf = [(r["label"], r.get("fcf")) for r in c]
        out.append({"信号": "自由现金流(经营-capex)", "方向": slope(fcf)["dir"],
                    "证据": slope(fcf)["note"], "释义": "扩张期常为负, 转正是成熟信号"})
        op = [(r["label"], r.get("经营净额")) for r in c]
        out.append({"信号": "经营现金流净额", "方向": slope(op)["dir"], "证据": slope(op)["note"],
                    "释义": "利润的现金含量"})
    if income:
        i = list(reversed(income))
        nonop = [(r["label"], (r.get("投资收益") or 0) + (r.get("公允价值变动收益") or 0)) for r in i]
        s5 = slope(nonop)
        out.append({"信号": "非经常量级(投资收益+公允)", "方向": s5["dir"], "证据": s5["note"],
                    "释义": "占归母比重升=利润水分扩大, 需拆解"})
    # 存货增速 vs 收入增速(最新期, 简版: 用 balance 最新两期 YoY vs qs 最新营收 YoY)
    try:
        if balance and qs_labels:
            b = list(reversed(balance))
            if len(b) >= 2 and b[-1].get("存货") and b[0].get("存货") and b[0]["存货"] > 0:
                inv_yoy = round((b[-1]["存货"] / b[0]["存货"] - 1) * 100, 1)
                out.append({"信号": "存货增速(近%d期)" % len(b), "方向": "参考",
                            "证据": "%+.1f%%" % inv_yoy, "释义": "与营收增速对比: 高于营收增速+10pp以上需警惕滞销"})
    except Exception:
        pass
    return out


# ─── 汇总记分卡 ─────────────────────────────────────────────

def scorecard(guidance_v, kw, holders_tl, leads, ana):
    """趋势记分卡: 每条 {信号, 方向, 证据}。方向含↑/升/上调/增强 计1分, ↓计-1。"""
    rows = []
    rows.append({"信号": "A 全年收入指引", "方向": guidance_v.get("dir"), "证据": "; ".join(guidance_v.get("steps") or [])[:120]})
    nt = holders_tl.get("national_team") or {}
    rows.append({"信号": "B 国家队(社保/汇金证金)", "方向": nt.get("dir"), "证据": "; ".join(nt.get("evidence") or [])[:100]})
    rows.append({"信号": "C 报告表述(关键词)", "方向": kw.get("trend"), "证据": "up%d/risk%d" % (kw.get("up_total", 0), kw.get("risk_total", 0))})
    for item in leads or []:
        rows.append({"信号": item["信号"], "方向": item["方向"], "证据": item["证据"][:90]})
    if ana:
        rows.append({"信号": "G 三线形态", "方向": ("连续过线%d季/加速" % ana.get("streak_max", 0)) if ana.get("streak_max") else "弱",
                     "证据": "%s 共振" % ana.get("resonance", "—")})
    score = 0
    for r in rows:
        d = str(r.get("方向"))
        if "放缓" in d or "数据不足" in d or "参考" in d or "无数据" in d or "无信号" in d:
            continue                                   # 中性/缺数据不计分
        if any(k in d for k in ("连续上调", "上调", "增强", "加速升", "↑", "上升词占优", "升")) and "降" not in d and "退出" not in d:
            score += 1
        elif any(k in d for k in ("下调", "退出", "降", "风险词占优", "弱")):
            score -= 1
    return {"rows": rows, "score": score,
            "verdict": "上升态势" if score >= 3 else ("偏上行" if score >= 1 else ("中性/混合" if score >= -1 else "下行态势"))}
