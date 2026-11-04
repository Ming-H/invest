#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pipeline.py — earnings-reader 编排器（draft / finalize / publish 三段式）。

移植自 h1-monitor v7.3 pipeline.py（闸门/单季差分/名片/渲染原样照搬, 规则出处见
各函数 docstring 与 docs/DESIGN.md §5）。结构性改动:
  - 报告期不再手改常量: detect_period() 从披露行众数自动识别(支持任意季)
  - headless GLM 全删: 催化压缩与 L2 枚举判定由对话内 Claude 产出 judge_output.json,
    本脚本只做校验(枚举合法/催化恰好n条)与确定性兜底 —— 缺漏不阻塞发布
  - 输出: 我的空间根目录的 标的池 Base(upsert by 股票代码) + 财报阅读文档(不建文件夹, 用户约定)

用法:
  pipeline.py --date 2026-10-30 --draft                 # ① 草稿+判定输入
  pipeline.py --since 2026-10-28 --until 2026-10-30 --draft   # 区间补跑
  pipeline.py --date 2026-10-30 --finalize              # ③a 终稿(读 judge_output.json)
  pipeline.py --date 2026-10-30 --finalize --publish    # ③b 终稿+Base+飞书
  可选: --no-wencai(跳过问财筹码) --force(忽略幂等) --judge-file 路径
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import date as _date, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import data          # noqa: E402
import framework as fw   # noqa: E402

LARK = os.path.join(os.path.expanduser("~"), ".npm-global", "bin", "lark-cli")
OUTROOT = os.path.join(ROOT, "outputs")
CACHE = os.path.join(ROOT, "cache")
PROCESSED = os.path.join(CACHE, "pool_base.json")      # Base/Folder token 缓存
DONE = os.path.join(CACHE, "processed.json")           # 幂等台账

TH, HI_Y, HI_Q = 40.0, 100.0, 50.0
MIN_PROFIT = 5e7            # R2.5: 扭亏类当季归母下限(0.5亿)
MAX_PICKS = 120             # 上限保护: 超出按分档排序截断并在报告标注
INDS = (("rev", "营收"), ("np", "归母"), ("ded", "扣非"))
TIER_LABEL = {"特档": "特档·双高", "一档": "第一档·单高", "二档": "第二档·稳增"}


def log(msg):
    print("[%s] %s" % (time.strftime("%H:%M:%S"), msg), flush=True)


def run(cmd, timeout=300):
    p = subprocess.run(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    return (p.returncode,
            p.stdout.decode("utf-8", "replace").strip(),
            p.stderr.decode("utf-8", "replace").strip())


# ============ 季度日历(泛化: 任意报告期) ============

QEND = {1: "03-31", 2: "06-30", 3: "09-30", 4: "12-31"}


def quarter_of(rpt):
    """'2026-09-30' -> (2026, 3)。"""
    inv = {v: k for k, v in QEND.items()}
    return int(rpt[:4]), inv[rpt[5:]]


def prev_quarter(y, q):
    return (y - 1, 4) if q == 1 else (y, q - 1)


def qlabel(y, q):
    return "%02dQ%d" % (y % 100, q)


def cum_end(y, q):
    return "%d-%s" % (y, QEND[q])


def quarter_amounts(cum, y, q, n_back=8):
    """累计口径 -> 单季 {qlabel: {rev,np,ded}}。Q1 单季=一季报累计本身; 其余=本季累计-上季累计。
    (旧版 quarters() 的硬编码 26Q3/26Q2/... 泛化为任意报告期。)"""
    def sub(a, b):
        x, z = cum.get(cum_end(*a)) or {}, cum.get(cum_end(*b)) or {}
        return {k: x[k] - z[k] for k in ("rev", "np", "ded")
                if x.get(k) is not None and z.get(k) is not None}
    out = {}
    yq = (y, q)
    for _ in range(n_back):
        ay, aq = yq
        if aq == 1:
            c = cum.get(cum_end(ay, 1)) or {}
            out[qlabel(ay, 1)] = {k: v for k, v in c.items() if v is not None}
        else:
            out[qlabel(ay, aq)] = sub(yq, prev_quarter(ay, aq))
        yq = prev_quarter(*yq)
    return out


def display_quarters(y, q):
    """显示窗: 当季往前共 4 期(旧版 25Q4/26Q1/26Q2/26Q3)。"""
    out, yq = [], (y, q)
    for _ in range(4):
        out.append(qlabel(*yq))
        yq = prev_quarter(*yq)
    return list(reversed(out))


def yoy_of(lbl):
    yy, q = (2000 + int(lbl[:2])), int(lbl[3])
    return qlabel(yy - 1, q)


def qoq_of(lbl):
    yy, q = (2000 + int(lbl[:2])), int(lbl[3])
    return qlabel(*prev_quarter(yy, q))


# ============ 纯计算: 闸门(照搬 v7.3, 出处标注) ============

def pct(a, b):
    return (a / b - 1) * 100 if (a is not None and b is not None and b > 0) else None


def is_risk_name(name):
    """R3: 退市风险警示/退市整理(名称含 ST 或 退)直接出局。"""
    return ("ST" in (name or "")) or ("退" in (name or ""))


def tier_of(checks):
    """分档(呈现用): 锚(R4)=存在(同比>TH 或 环比>HI_Q), 无锚->None;
    特档=最大同比>100且最大环比>50, 其一->一档, 其余->二档。"""
    if not checks:
        return None
    if not any((y is not None and y > TH) or (q is not None and q > HI_Q)
               for _, y, q in checks):
        return None
    ys = [y for _, y, _ in checks if y is not None]
    qs = [q for _, _, q in checks if q is not None]
    by = max(ys) if ys else 0.0
    bq = max(qs) if qs else 0.0
    if by > HI_Y and bq > HI_Q:
        return "特档"
    if by > HI_Y or bq > HI_Q:
        return "一档"
    return "二档"


def process_formal(rows, hist, names, y, q):
    """正式财报: 按code取最新公告; 当季=F10本季累计-上季累计(F10未更新则披露行兜底)。
    闸序: R3名称 -> R2盈利 -> R1同比硬线 -> R2.5规模 -> R4锚+分档。"""
    cur_l, py_l, pq_l = qlabel(y, q), qlabel(y - 1, q), qlabel(*prev_quarter(y, q))
    latest = {}
    for r in rows:
        c = str(r.get("SECURITY_CODE") or "").split(".")[0]
        nd = str(r.get("NOTICE_DATE") or "")
        if not c:
            continue
        if c not in latest or nd > latest[c]["nd"]:
            latest[c] = {"nd": nd, "rev": r.get("TOTAL_OPERATE_INCOME"), "np": r.get("PARENT_NETPROFIT")}
    out = []
    for c, row in latest.items():
        if is_risk_name(names.get(c)):
            continue
        cum, qq = hist.get(c) or ({}, {})
        cur = dict(qq.get(cur_l) or {})
        if not cur and cum.get(cum_end(*prev_quarter(y, q))):
            h1 = cum[cum_end(*prev_quarter(y, q))]
            cur = {k: row[k] - h1[k] for k in ("rev", "np")
                   if row.get(k) is not None and h1.get(k) is not None}
        # R2: 当季亏损出局(扣非有值也拦; 缺值不拦)
        if (cur.get("np") is not None and cur["np"] <= 0) or \
           (cur.get("ded") is not None and cur["ded"] <= 0):
            continue
        checks = []
        for k, _ in INDS:
            yv = pct(cur.get(k), (qq.get(py_l) or {}).get(k))
            qo = pct(cur.get(k), (qq.get(pq_l) or {}).get(k))
            # R1 同比硬线: 可算且<=40% -> 假成长出局(环比无否决权, v7.3)
            if yv is not None and not yv > TH:
                checks = []
                break
            if yv is not None or qo is not None:
                checks.append((k, yv, qo))
        if not checks:
            continue
        # R2.5: 归母+扣非同比全不可算(扭亏类)时, 当季归母须达规模(0.5亿)
        if not any(k in ("np", "ded") for k, yv, _ in checks if yv is not None) and \
           (cur.get("np") is None or cur["np"] < MIN_PROFIT):
            continue
        t = tier_of(checks)
        if t:
            out.append({"code": c, "name": names.get(c) or c, "nd": row["nd"][:10],
                        "tier": t, "checks": checks, "q": qq, "is_fc": False})
    return out


def process_forecast(rows, hist, names, y, q):
    """业绩预告: 一司多指标行(归母/扣非/营收), 各自倒推当季下限算(同比,环比), 全丢 tier_of。"""
    cur_l, py_l, pq_l = qlabel(y, q), qlabel(y - 1, q), qlabel(*prev_quarter(y, q))
    prev_cum_end = cum_end(*prev_quarter(y, q))
    per = {}
    for r in rows:
        c = str(r.get("SECURITY_CODE") or "").split(".")[0]
        fin = r.get("PREDICT_FINANCE") or ""
        if not c or "每股" in fin:
            continue
        if r.get("_iw") is not None:                      # 问财备源行: 只有净利区间
            ind = "np"
            lo, hi = r["_iw"].get("预告净利润下限"), r["_iw"].get("预告净利润上限")
            # 问财字段带日期后缀, 由 data.iw_lists 预先归一成无后缀键
            if lo is None:
                lo = r["_iw"].get("预告净利润下限[%s]" % (r.get("REPORTDATE") or "").replace("-", ""))
            if hi is None:
                hi = r["_iw"].get("预告净利润上限[%s]" % (r.get("REPORTDATE") or "").replace("-", ""))
        else:
            ind = "ded" if "扣除非经常" in fin else ("rev" if "营业" in fin else ("np" if ("净利" in fin or "归属" in fin) else None))
            if ind is None:
                continue
            lo, hi = r.get("PREDICT_AMT_LOWER"), r.get("PREDICT_AMT_UPPER")
        nd = str(r.get("NOTICE_DATE") or "")
        key = (c, ind)
        if key not in per or nd > per[key]["nd"]:
            per[key] = {"nd": nd, "lo": lo, "hi": hi,
                        "amp_lo": r.get("ADD_AMP_LOWER"), "amp_hi": r.get("ADD_AMP_UPPER"),
                        "reason": (r.get("CHANGE_REASON_EXPLAIN") or "").strip()}
    byc = {}
    for (c, ind), r in per.items():
        byc.setdefault(c, {})[ind] = r
    out = []
    for c, inds in byc.items():
        if is_risk_name(names.get(c)):
            continue
        cum, qq = hist.get(c) or ({}, {})
        h1 = cum.get(prev_cum_end) or {}
        checks, spans = [], {}
        for ind, r in inds.items():
            if r.get("lo") is None or h1.get(ind) is None:
                continue
            cur_lo = r["lo"] - h1[ind]
            hi = r["hi"] if r["hi"] is not None else r["lo"]
            yv = pct(cur_lo, (qq.get(py_l) or {}).get(ind))
            qo = pct(cur_lo, (qq.get(pq_l) or {}).get(ind))
            # R1 同比硬线(同正式)
            if yv is not None and not yv > TH:
                checks = []
                break
            if yv is not None or qo is not None:
                checks.append((ind, yv, qo))
            spans[ind] = (cur_lo, hi - h1[ind])
        if not checks:
            continue
        # R2: 下限倒推当季亏损出局
        if ("np" in spans and spans["np"][0] <= 0) or \
           ("ded" in spans and spans["ded"][0] <= 0):
            continue
        # R2.5: 扭亏类净利区间倒推当季下限须达规模
        if not any(k in ("np", "ded") for k, yv, _ in checks if yv is not None) and \
           ("np" not in spans or spans["np"][0] < MIN_PROFIT):
            continue
        t = tier_of(checks)
        if not t:
            continue
        np_row = inds.get("np") or {}
        amp_lo, amp_hi = np_row.get("amp_lo"), np_row.get("amp_hi")
        cum_np = (cum.get(cum_end(y - 1, q)) or {}).get("np")
        if amp_lo is None:
            amp_lo = pct(np_row.get("lo"), cum_np)
        if amp_hi is None:
            amp_hi = pct(np_row.get("hi"), cum_np)
        out.append({"code": c, "name": names.get(c) or c,
                    "nd": max(r["nd"] for r in inds.values())[:10],
                    "tier": t, "checks": checks, "q": qq, "spans": spans, "is_fc": True,
                    "np_lo": np_row.get("lo"), "np_hi": np_row.get("hi"),
                    "amp_lo": amp_lo, "amp_hi": amp_hi,
                    "reason": (np_row.get("reason") or "").strip()})
    return out


# ============ 催化预清洗(确定性, 不靠模型; 照搬) ============

_NUM_MARK = re.compile(r"(?:^|[\s,，;；:：.。、(（])(?:[0-9]{1,2}[、.．)）]|[①②③④⑤⑥⑦⑧⑨⑩])")
_ANCHOR = re.compile(r"(?:主要原因为|主要原因[是为]?|变动原因|驱动因素)[：:]?")
_DROP_KW = ("非经常性损益", "无重大影响", "不存在其他")   # 非归因条目: 用户定稿剔除(2026-10-18)


def count_items(s):
    """分项计数: 数编号标记(1、/1./（1）/1)); 无标记按1条。"""
    n = len(_NUM_MARK.findall(s or ""))
    return n if n >= 2 else 1


def clean_catalyst(text, is_review):
    """正式评述只取'原因'锚点之后并砍'综上'尾句; 按编号切段剔非归因条目。
    -> (清洗文本, 条数)。"""
    t = (text or "").strip()
    if is_review:
        m = _ANCHOR.search(t)
        if m:
            t = t[m.end():].strip()
        i = t.find("综上")
        if i > len(t) // 3:
            t = t[:i].strip()
    marks = list(_NUM_MARK.finditer(t))
    if len(marks) < 2:
        if any(kw in t for kw in _DROP_KW) and len(t) < 60:
            return "", 0
        return (t, 1) if t else ("", 0)
    bounds = [mm.start() for mm in marks] + [len(t)]
    pre = t[:bounds[0]].strip().rstrip("：:，,;")
    lead = pre if len(pre) >= 12 else ""
    kept = []
    for k in range(len(marks)):
        seg = t[bounds[k]:bounds[k + 1]].strip()
        if seg and not any(kw in seg for kw in _DROP_KW):
            kept.append(seg)
    parts = ([lead] if lead else []) + kept
    if not parts:
        return "", 0
    return (lead + "。" if (lead and not lead.endswith(("。", "；", ";", "：", ":"))) else (lead or "")) + "".join(kept), len(parts)


# ============ 渲染 ============

def _f(v):
    return "%.2f" % (v / 1e8) if v is not None else "—"


def _r(s):
    return "%.2f~%.2f" % (s[0] / 1e8, s[1] / 1e8) if s else "—"


def _pp(v, base):
    if v is None or base is None:
        return "—"
    if base <= 0:
        return "基数≤0"
    return "%+.1f%%" % ((v / base - 1) * 100)


def _pp2(lo, hi, base):
    if base is None:
        return "—"
    if base <= 0:
        return "基数≤0"
    a, b = _pp(lo, base), _pp(hi, base)
    return a if a == b else "%s~%s" % (a, b)


def qt(e, y, q, fc=False):
    """季度做列; 每指标 金额/同比/环比 三行; 预告当季为区间。"""
    qq = e.get("q") or {}
    sp = e.get("spans") or {}
    cols = display_quarters(y, q)
    cur_l = cols[-1]
    headtxt = " | ".join(("%s(区间)" if fc else "%s(当季)") % cur_l if k == cur_l else k for k in cols)
    rows = ["| 单季 | %s |" % headtxt, "|" + "---|" * (len(cols) + 1)]

    def amt(k, ind):
        if fc and k == cur_l:
            return _r(sp.get(ind))
        return _f((qq.get(k) or {}).get(ind))

    def rate(k, ind, yoy):
        base = (qq.get(yoy_of(k) if yoy else qoq_of(k)) or {}).get(ind)
        if fc and k == cur_l:
            return _pp2(sp[ind][0], sp[ind][1], base) if ind in sp else "—"
        return _pp((qq.get(k) or {}).get(ind), base)

    for ind, lbl in INDS:
        rows.append("| %s(亿) | %s |" % (lbl, " | ".join(amt(k, ind) for k in cols)))
        rows.append("| %s同比 | %s |" % (lbl, " | ".join(rate(k, ind, True) for k in cols)))
        rows.append("| %s环比 | %s |" % (lbl, " | ".join(rate(k, ind, False) for k in cols)))
    return "\n".join(rows)


def highlight(e):
    lbl = dict(INDS)
    parts = []
    for k, yv, q in e["checks"]:
        seg = (["同%+.0f%%" % yv] if yv is not None else []) + (["环%+.0f%%" % q] if q is not None else [])
        if seg:
            parts.append(lbl[k] + "/".join(seg))
    return " · ".join(parts)


def _p(v):
    return "%.1f%%" % v if isinstance(v, (int, float)) else "—"


def _n(v):
    return "%.0f" % v if isinstance(v, (int, float)) else "—"


def _yi(v):
    return "%.0f亿" % (v / 1e8) if isinstance(v, (int, float)) else "—"


def board_tag(code):
    """涨跌幅属性: 300/301创业板/科创板20cm, 北交所30cm, 其余10cm。"""
    if code[:3] in ("688", "689") or code[0] == "3":
        return "20cm"
    if code[0] in ("8", "4"):
        return "30cm"
    return "10cm"


def card_md(e, board_cnt):
    """名片块: 板块/主营/快照/质地/筹码/解禁/催化。"""
    cd = e.get("card") or {}
    ql = e.get("qual") or {}
    out = []
    board = cd.get("board") or cd.get("ind") or "—"
    b1 = board.split("-")[0]
    same = " | 本期%s板块入池%d家" % (b1, board_cnt[b1]) if b1 != "—" and board_cnt.get(b1, 0) >= 2 else ""
    out.append("> **板块**: %s%s%s" % (board, (" · " + cd["province"]) if cd.get("province") else "", same))
    if cd.get("main") or cd.get("holder"):
        out.append("> **主营**: %s%s%s" % (cd.get("main") or "—",
                   (" | 实控人: " + cd["holder"]) if cd.get("holder") else "",
                   (" | 员工%d人" % cd["emp"]) if cd.get("emp") else ""))
    out.append("> **快照**: 总市值%s(流通%s) · PE%s · 距一年新高%s · 年内%s" % (
        _yi(cd.get("mcap")), _yi(cd.get("fmcap")),
        "%.1fx" % cd["pe"] if isinstance(cd.get("pe"), (int, float)) else "—",
        "%+.1f%%" % cd["drawdown"] if isinstance(cd.get("drawdown"), (int, float)) else "—",
        "%+.0f%%" % cd["ytd"] if isinstance(cd.get("ytd"), (int, float)) else "—"))
    if ql.get("ml_trend"):
        out.append("> **质地**: 毛利率(累计) %s · ROE %s · ROIC %s · 负债率 %s · 净利现金含量 %s" % (
            "→".join("%.1f%%" % v for _, v in ql["ml_trend"][-4:]),
            _p(ql.get("roe")), _p(ql.get("roic")), _p(ql.get("debt")), _p(ql.get("ncf_np"))))
    if cd.get("inst") is not None or cd.get("holders") is not None or cd.get("limits") is not None:
        out.append("> **筹码**: 机构占流通%s · 股东%s · 近一年涨停%s" % (
            _p(cd.get("inst")),
            "%.1f万" % (cd["holders"] / 1e4) if isinstance(cd.get("holders"), (int, float)) else "—",
            ("%d次" % cd["limits"]) if isinstance(cd.get("limits"), (int, float)) else "—"))
    if cd.get("lift_date"):
        out.append("> **解禁**: %s · %s万股 · 占流通%s · 市值%s" % (
            cd["lift_date"], _n(cd.get("lift_shares")), _p(cd.get("lift_ratio")),
            _yi((cd.get("lift_mcap") or 0) * 1e4)))
    cat = (e.get("catalyst_sum") or e.get("catalyst_clean") or e.get("reason") or e.get("review") or "")
    if cat:
        if len(cat) > 600:
            cat = cat[:600] + "…(原文见公告)"
        out.append("> **催化**: %s" % cat)
    return out


def gen_report(formal, forecast, src, codes_n, start, end, board_cnt, y, q,
               truncated=0, cat_note=""):
    L = ["## 1. 筛选口径", "",
         "> 规则: 三指标同比可算须>%.0f%%(基数≤0忽略); 环比不否决仅参与分档(单季环比含季节噪声); "
         "入池须锚(同>%.0f%%或环>%.0f%%); 当季归母/扣非(有值)>0; 扭亏类当季归母≥0.5亿; ST/退市警示出局; "
         "预告按区间下限 | 特档·双高=最大同>%.0f%%且最大环>%.0f%% · 第一档·单高=其一 · 第二档·稳增=其余"
         % (TH, TH, HI_Q, HI_Y, HI_Q), "",
         "> 范围: %s源 窗口[%s~%s] 披露%d家, 本次入池%d家(正式%d 预告%d); 未入池零呈现%s%s"
         % (src, start, end, codes_n, len(formal) + len(forecast), len(formal), len(forecast),
            ("; 超上限截断%d家(按分档)" % truncated) if truncated else "", cat_note), ""]
    for title, picks in (("2. 正式财报", formal), ("3. 业绩预告", forecast)):
        es = ([e for e in picks if e["tier"] == "特档"] +
              [e for e in picks if e["tier"] == "一档"] +
              [e for e in picks if e["tier"] == "二档"])
        if not es and not picks:
            continue
        L.extend(["## %s" % title, ""])
        for t in ("特档", "一档", "二档"):
            g = [e for e in picks if e["tier"] == t]
            if not g:
                continue
            L.extend(["### ◆ %s ×%d" % (TIER_LABEL[t], len(g)), ""])
            for e in g:
                cd0 = e.get("card") or {}
                head = "- **%s(%s)** %s · %s" % (e["name"], e["code"], highlight(e), board_tag(e["code"]))
                if cd0.get("listing"):
                    head += " · %s年上市" % cd0["listing"][:4]
                if title == "业绩预告" and e.get("np_lo") is not None:
                    head += " · 净利%.2f~%.2f亿 → 待正式财报" % (e["np_lo"] / 1e8, (e["np_hi"] or e["np_lo"]) / 1e8)
                L.append(head)
                L.extend(card_md(e, board_cnt))
                L.extend(["", qt(e, y, q, fc=(title == "业绩预告")), ""])
        L.append("")
    return "\n".join(L)


# ============ 幂等台账 ============

def load_done():
    try:
        return set(tuple(x) for x in json.load(open(DONE, encoding="utf-8")))
    except Exception:
        return set()


def save_done(done):
    os.makedirs(CACHE, exist_ok=True)
    json.dump([list(x) for x in sorted(done)], open(DONE, "w", encoding="utf-8"), ensure_ascii=False)


# ============ ① draft ============

def cmd_draft(a):
    if a.since:
        start, end = a.since, (a.until or a.since)
    else:
        d = a.date or (_date.today() - timedelta(days=1)).isoformat()
        start = end = d
    tag = start if start == end else "%s_%s" % (start, end)
    outdir = os.path.join(OUTROOT, tag)
    os.makedirs(outdir, exist_ok=True)
    log("draft: 窗口[%s~%s]%s" % (start, end, " (no-wencai)" if a.no_wencai else ""))

    # 1) 清单 + 报告期识别
    f_rows, w_rows, src = data.disclosure_lists(start, end)
    if not src:
        log("全部数据源不可用, 退出(重试或检查网络)")
        return 1
    rpt, label = data.detect_period(f_rows, w_rows)
    if not rpt:
        log("报告期识别失败, 退出")
        return 1
    f_rows = [r for r in f_rows if str(r.get("REPORTDATE") or "")[:10] == rpt]
    w_rows = [r for r in w_rows if str(r.get("REPORT_DATE") or r.get("REPORTDATE") or "")[:10] == rpt]
    y, q = quarter_of(rpt)
    log("① 清单: %s源 正式%d 预告%d | 报告期=%s(%s)" % (src, len(f_rows), len(w_rows), rpt, label))

    # 2) 名称
    names, codes = {}, set()
    for r in f_rows + w_rows:
        c = str(r.get("SECURITY_CODE") or "").split(".")[0]
        if c:
            codes.add(c)
            n = r.get("SECURITY_NAME_ABBR") or ""
            if n:
                names[c] = n
    missing = [c for c in sorted(codes) if not names.get(c)]
    if missing:
        names.update(data.tx_names(missing))
    log("② 名称: %d/%d (腾讯兜底%d)" % (len([c for c in codes if names.get(c)]), len(codes), len(missing)))

    # 3) 幂等过滤
    done = load_done()
    fresh_f = [r for r in f_rows if (str(r.get("SECURITY_CODE") or "").split(".")[0],
                                     str(r.get("NOTICE_DATE") or "")[:10]) not in done] if not a.force else f_rows
    fresh_w = [r for r in w_rows if (str(r.get("SECURITY_CODE") or "").split(".")[0],
                                     str(r.get("NOTICE_DATE") or "")[:10]) not in done] if not a.force else w_rows
    if a.force:
        log("②b --force: 幂等台账忽略")

    # 4) 历史三指标(F10): 看门狗批量(深夜实测 F10 host 会黑洞化 SSL 握手/DNS,
    #    socket 超时拦不住 -> 子进程块+强杀重试, 流程保证不挂死)
    todo = sorted(set([str(r.get("SECURITY_CODE") or "").split(".")[0] for r in fresh_f + fresh_w if r.get("SECURITY_CODE")]))
    hist, quals = {}, {}

    def _f10_prog(done, total):
        if done % 500 < 50 or done == total:
            log("③ 历史F10(看门狗): %d/%d" % (done, total))
    got = data.f10_batch(todo, progress=_f10_prog)
    for c, (cum, qual) in got.items():
        hist[c] = (cum, quarter_amounts(cum, y, q))
        quals[c] = qual
    log("③ 历史F10: %d/%d" % (len(hist), len(todo)))

    # 5) 闸门 + 分档
    formal = process_formal(fresh_f, hist, names, y, q)
    forecast = process_forecast(fresh_w, hist, names, y, q)
    picks = formal + forecast
    truncated = 0
    if len(picks) > MAX_PICKS:
        def strength(e):
            ys = [yv for _, yv, _ in e["checks"] if yv is not None]
            qs = [qv for _, _, qv in e["checks"] if qv is not None]
            return (max(ys + qs) if (ys or qs) else 0)
        order = {"特档": 0, "一档": 1, "二档": 2}
        picks.sort(key=lambda e: (order[e["tier"]], -strength(e)))
        keep = set((e["code"], e["is_fc"]) for e in picks[:MAX_PICKS])
        truncated = len(picks) - MAX_PICKS
        formal = [e for e in formal if (e["code"], e["is_fc"]) in keep]
        forecast = [e for e in forecast if (e["code"], e["is_fc"]) in keep]
        picks = formal + forecast
        log("④ 截断保护: %d -> %d (按分档+强度)" % (len(picks) + truncated, MAX_PICKS))
    log("④ 闸门: 正式%d 预告%d (披露%d家)" % (len(formal), len(forecast), len(codes)))
    if not picks:
        log("无过闸标的。0 家也是正常结果(非披露高峰日), outputs 已写 machine.json 供核查")
        _dump_machine(outdir, start, end, tag, src, rpt, label, y, q, len(codes), [], data.DEGRADED)
        with open(os.path.join(outdir, "draft.md"), "w", encoding="utf-8") as f:
            f.write("## 1. 筛选口径\n\n> 窗口[%s~%s] 报告期%s(%s) 披露%d家, 入池0家\n" % (start, end, rpt, label, len(codes)))
        return 0

    # 6) 名片(只拉过闸者): 行情批量一次拉齐, 其余逐只
    uniq_codes, seen_c = [], set()
    for e in picks:
        if e["code"] not in seen_c:
            seen_c.add(e["code"])
            uniq_codes.append(e["code"])
    qb = data.quotes_batch(uniq_codes)
    log("⑤ 行情批量(ulist %d批): %d家有市值" % ((len(uniq_codes) + 49) // 50, len(qb)))
    n = 0
    for e in picks:
        card = {}
        card.update(data.org_info(e["code"]))
        card.update(qb.get(e["code"], {}))
        card.update(data.kline_stat(e["code"], int(end[:4])))
        card.update(data.lift_next(e["code"], end))
        e["card"] = card
        e["qual"] = quals.get(e["code"], {})
        n += 1
        if n % 20 == 0:
            log("⑤ 名片: %d/%d" % (n, len(picks)))
    if a.no_wencai:
        log("⑤ 筹码: --no-wencai 跳过")
    else:
        iwc = data.iw_cards([e["code"] for e in picks], [e["name"] for e in picks])
        for e in picks:
            e["card"].update(iwc.get(e["code"], {}))
        log("⑤ 筹码(问财%d批): %d家有数据" % ((len(picks) + 4) // 5, len(iwc)))
    board_cnt = {}
    for e in picks:
        b = ((e["card"].get("board") or e["card"].get("ind") or "").split("-")[0])
        if b:
            board_cnt[b] = board_cnt.get(b, 0) + 1

    # 7) 催化: 正式=经营评述, 预告=变动原因; 确定性预清洗
    for e in formal:
        rv = data.biz_review(e["code"], rpt)
        if rv:
            e["review"] = rv
    for e in picks:
        raw = e.get("reason") or e.get("review") or ""
        is_rev = bool(e.get("review")) and not e.get("reason")
        clean, n = clean_catalyst(raw, is_rev)
        e["catalyst_clean"], e["cat_n"] = clean, n
    log("⑤c 催化预清洗: 有源%d家" % len([e for e in picks if e.get("catalyst_clean")]))

    # 8) L1.5 + 框架卡 + stage_hint(行情缺失时以 datacenter 估值表补市值/PE)
    for i, e in enumerate(picks, 1):
        fe = data.fetch_all(e["code"]) or {}
        cd = e.get("card") or {}
        if not cd.get("mcap") and fe.get("mktcap_yi"):
            cd["mcap"] = fe["mktcap_yi"] * 1e8
        if fe.get("pe_ttm"):
            cd["pe"] = fe["pe_ttm"]          # 优先 TTM 口径(ulist 的 f9 是动 PE)
        if cd.get("mcap"):
            fe["mktcap_yi"] = round(cd["mcap"] / 1e8, 1)
        _ly = (cd.get("listing") or "")[:4]
        fe["listed_year"] = int(_ly) if _ly.isdigit() else None
        e["fetched"] = fe
        e["quarterly"] = _series_of(e, y, q)
        card = fw.build_card(_fw_entry(e), e["fetched"], end)
        e["fw_card"] = card
        e["stage_hint"] = fw.stage_hint_of(card)
        if i % 20 == 0:
            log("⑥ L1.5: %d/%d" % (i, len(picks)))
    log("⑥ L1.5+框架卡: %d家" % len(picks))

    # 9) 落盘
    cat_note = " | 催化为清洗原文, 待 Claude 压缩(judge_output.catalyst_summary)"
    md = gen_report(formal, forecast, src, len(codes), start, end, board_cnt, y, q,
                    truncated=truncated, cat_note=cat_note)
    # machine.json 先落盘(防晚段写盘异常丢全量数据 —— 实测教训: 曾在 judge_input 构建处
    # 崩掉, 20 分钟取数全损), 再写其余产物
    _dump_machine(outdir, start, end, tag, src, rpt, label, y, q, len(codes),
                  _slim_entries(picks), data.DEGRADED)
    with open(os.path.join(outdir, "draft.md"), "w", encoding="utf-8") as f:
        f.write(md)
    judge = []
    for e in picks:
        cd, ql, fe = e.get("card") or {}, e.get("qual") or {}, e.get("fetched") or {}
        judge.append({
            "code": e["code"], "name": e["name"],
            "board": cd.get("board") or cd.get("ind") or "",
            "main_business": cd.get("main", ""),
            "catalyst": e.get("catalyst_clean") or "", "n": e.get("cat_n", 0),
            "gm_trend": [round(v, 1) for _, v in (ql.get("ml_trend") or [])[-4:]] or None,
            "roe": ql.get("roe"), "np_cash_ratio": ql.get("ncf_np"),
            "mktcap_yi": fe.get("mktcap_yi"),
            "quarterly": {"rev": e["quarterly"]["rev"], "np": e["quarterly"]["np"],
                          "dnp": e["quarterly"]["dnp"]},
            "realized_3y": fe.get("realized_3y"), "off_high_1y": cd.get("drawdown"),
            "ytd_chg": cd.get("ytd"), "stage_hint": e.get("stage_hint", "无意见"),
        })
    with open(os.path.join(outdir, "judge_input.json"), "w", encoding="utf-8") as f:
        json.dump(judge, f, ensure_ascii=False, indent=1)
    with open(os.path.join(outdir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump({"degraded": data.DEGRADED}, f, ensure_ascii=False, indent=1)
    log("⑧ draft 完成: %s | 过闸%d 正式%d 预告%d | 判定输入%d卡 | 降级%d项"
        % (outdir, len(picks), len(formal), len(forecast), len(judge), len(data.DEGRADED)))
    log("下一步: Claude 读 judge_input.json 逐批判定 -> 写 %s/judge_output.json -> --finalize"
        % os.path.join("outputs", tag))
    return 0


def _fw_entry(e):
    """pipeline 条目 -> framework.build_card 所需 entry 形状。"""
    cd, ql = e.get("card") or {}, e.get("qual") or {}
    return {"code": e["code"], "name": e["name"],
            "board": cd.get("board") or cd.get("ind") or "", "l1_tier": TIER_LABEL.get(e.get("tier"), ""),
            "main_business": cd.get("main", ""),
            "catalyst": e.get("catalyst_clean") or e.get("reason") or e.get("review") or "",
            "listed_year": (lambda s: int(s) if s.isdigit() else None)((cd.get("listing") or "")[:4]),
            "quarterly": e["quarterly"], "gm_trend": [round(v, 1) for _, v in (ql.get("ml_trend") or [])[-4:]] or None,
            "roe": ql.get("roe"), "debt_ratio": ql.get("debt"),
            "np_cash_ratio": ql.get("ncf_np"), "inst_pct": cd.get("inst"),
            "ytd_chg": cd.get("ytd"), "off_high_1y": cd.get("drawdown"),
            "limit_up_count": cd.get("limits")}


def _series_of(e, y, q):
    """framework 所需 4 期序列 [{val(亿),yoy,qoq}](显示窗顺序)。"""
    qq = e.get("q") or {}
    sp = e.get("spans") or {}
    cols = display_quarters(y, q)
    cur_l = cols[-1]

    def ser(ind):
        out = []
        for k in cols:
            if e.get("is_fc") and k == cur_l:
                lo, hi = sp.get(ind, (None, None))
                yb = (qq.get(yoy_of(k)) or {}).get(ind)
                qb = (qq.get(qoq_of(k)) or {}).get(ind)
                out.append({"val": (lo / 1e8) if lo is not None else None,
                            "yoy": ((lo / yb - 1) * 100) if (lo is not None and yb and yb > 0) else None,
                            "qoq": ((lo / qb - 1) * 100) if (lo is not None and qb and qb > 0) else None})
                continue
            v = (qq.get(k) or {}).get(ind)
            yb = (qq.get(yoy_of(k)) or {}).get(ind)
            qb = (qq.get(qoq_of(k)) or {}).get(ind)
            out.append({"val": (v / 1e8) if v is not None else None,
                        "yoy": ((v / yb - 1) * 100) if (v is not None and yb and yb > 0) else None,
                        "qoq": ((v / qb - 1) * 100) if (v is not None and qb and qb > 0) else None})
        return out
    # framework 沿用旧版键名: rev/np/dnp(dnp=扣非)
    return {"rev": ser("rev"), "np": ser("np"), "dnp": ser("ded")}


def _slim_entries(picks):
    """machine.json 存的条目(去掉不可序列化的冗余, 保留 finalize/publish 所需)。"""
    out = []
    for e in picks:
        s = {k: e.get(k) for k in ("code", "name", "nd", "tier", "is_fc", "checks", "spans",
                                   "np_lo", "np_hi", "amp_lo", "amp_hi", "review", "reason",
                                   "catalyst_clean", "cat_n", "catalyst_sum", "stage_hint",
                                   "card", "qual", "fetched", "quarterly")}
        s["q"] = e.get("q")
        out.append(s)
    return out


def _dump_machine(outdir, start, end, tag, src, rpt, label, y, q, codes_n, entries, degraded):
    with open(os.path.join(outdir, "machine.json"), "w", encoding="utf-8") as f:
        json.dump({"tag": tag, "window": [start, end], "src": src,
                   "rpt": rpt, "rpt_label": label, "y": y, "q": q,
                   "disclosed": codes_n, "entries": entries, "degraded": degraded},
                  f, ensure_ascii=False, indent=1)


# ============ ③ finalize / publish ============

def _load_dir(a):
    if a.since:
        start = a.since
        end = a.until or a.since
    else:
        d = a.date or (_date.today() - timedelta(days=1)).isoformat()
        start = end = d
    tag = start if start == end else "%s_%s" % (start, end)
    outdir = os.path.join(OUTROOT, tag)
    mfile = os.path.join(outdir, "machine.json")
    if not os.path.exists(mfile):
        log("未找到 %s —— 先跑 --draft" % mfile)
        return None, None
    return outdir, json.load(open(mfile, encoding="utf-8"))


def cmd_finalize(a):
    outdir, m = _load_dir(a)
    if not m:
        return 1
    y, q = m["y"], m["q"]
    entries = m["entries"]
    judge_file = a.judge_file or os.path.join(outdir, "judge_output.json")
    codes = [e["code"] for e in entries]
    llm, fallback = fw.load_judge_file(judge_file, set(codes)) if os.path.exists(judge_file) else ({}, len(codes))
    # 催化摘要回填: judge 的 catalyst_summary 须"恰好n条"(count_items>=n), 否则兜底清洗原文
    cat_ok = cat_fb = 0
    for e in entries:
        j = llm.get(e["code"])
        s = (j or {}).get("catalyst_summary")
        if isinstance(s, str) and len(s) >= 10 and count_items(s) >= (e.get("cat_n") or 1):
            e["catalyst_sum"] = s
            cat_ok += 1
        else:
            cat_fb += 1
    log("judge: 判定%d/%d 兜底%d | 催化 成功%d 兜底清洗原文%d"
        % (len(llm), len(codes), fallback + (len(codes) - len(llm)), cat_ok, cat_fb))
    cards, results = _run_l2(entries, llm, m, outdir)
    formal = [e for e in entries if not e.get("is_fc")]
    forecast = [e for e in entries if e.get("is_fc")]
    board_cnt = {}
    for e in entries:
        b = ((e.get("card") or {}).get("board") or (e.get("card") or {}).get("ind") or "").split("-")[0]
        if b:
            board_cnt[b] = board_cnt.get(b, 0) + 1
    md = gen_report(formal, forecast, m["src"], m["disclosed"],
                    m["window"][0], m["window"][1], board_cnt, y, q)
    market = data.fetch_market_status() or "大盘: 状态未获取"
    sec = fw.render_section(results, market, s_full=False)
    final = md + "\n" + sec + "\n\n---\n*非投资建议 | 数据源: %s(F10/行情/估值), 腾讯(K线), 问财(筹码) | 引擎: earnings-reader*\n" % m["src"]
    if m.get("degraded"):
        final += "\n### 本次数据降级项\n" + "\n".join("- `%s` %s" % (f, w) for f, w in m["degraded"][:20]) + "\n"
    with open(os.path.join(outdir, "final.md"), "w", encoding="utf-8") as f:
        f.write(final)
    log("finalize 完成: %s/final.md" % outdir)
    return 0


def _run_l2(entries, llm, m, outdir):
    cards = []
    for e in entries:
        card = fw.build_card(_fw_entry(e), e.get("fetched"), m["window"][1])
        cards.append(card)
    results, s_full = fw.run(cards, llm)
    fw.dump_outputs(results, cards, outdir, m["window"][1])
    _ = s_full
    return cards, results


# ---------- 飞书: 文件夹 / 文档 / 标的池 Base ----------

def _pool_cfg():
    try:
        return json.load(open(PROCESSED, encoding="utf-8"))
    except Exception:
        return {}


def _pool_save(cfg):
    os.makedirs(CACHE, exist_ok=True)
    json.dump(cfg, open(PROCESSED, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


POOL_FIELDS = ('[{"name":"股票代码","type":"text"},'
               '{"name":"股票名称","type":"text"},'
               '{"name":"纳入日期","type":"date"},'
               '{"name":"来源","type":"text"},'
               '{"name":"营收同比%","type":"text"},{"name":"归母同比%","type":"text"},{"name":"扣非同比%","type":"text"},'
               '{"name":"L2分级","type":"select","multiple":false,"options":[{"name":"S"},{"name":"A"},{"name":"R"},{"name":"B"},{"name":"剔除"}]},'
               '{"name":"阶段","type":"text"},{"name":"驱动","type":"text"},{"name":"一句话","type":"text"},'
               '{"name":"状态","type":"select","multiple":false,"options":[{"name":"观察"},{"name":"深研"},{"name":"已买"},{"name":"已剔"}]},'
               '{"name":"报告链接","type":"text"}]')


def ensure_pool_table():
    """标的池 Base/表: cache 记 (base_token, table_id); 首次用 +base-create 一次建库+首表+字段
    (官方姿势, 见 lark-base skill; +app-create 是 BaseApp 专用需 workspace-token, 别用)。
    落位: 我的空间根目录(与历史分析文档同级, 用户约定不新建文件夹)。"""
    cfg = _pool_cfg()
    if cfg.get("pool_base") and cfg.get("pool_table"):
        return cfg["pool_base"], cfg["pool_table"]
    rc, out, _ = run("%s base +base-create --name 标的池 --table-name 标的池 "
                     "--fields '%s' --as user --format json" % (LARK, POOL_FIELDS), timeout=120)
    base_token, table_id = "", ""
    try:
        d = json.loads(out[out.index("{"):])
        data = d.get("data") or {}
        base_token = data.get("base_token") or data.get("app_token") or ""
        table_id = data.get("table_id") or ""
    except Exception:
        pass
    if not base_token or not table_id:
        log("  ⚠ Base 建库失败 —— 可手工建『标的池』Base 后把 base_token/table_id 写入 cache/pool_base.json")
        return "", ""
    cfg["pool_base"], cfg["pool_table"] = base_token, table_id
    _pool_save(cfg)
    return base_token, table_id


def pool_upsert(entries, results, doc_url, m):
    """upsert by 股票代码: record-list 拉码->rid, 新建缺失/更新已有。状态列只在新建时给默认'观察',
    已有记录不覆盖状态与纳入日期(人流转字段)。"""
    base_token, table_id = ensure_pool_table()
    if not base_token or not table_id:
        log("  ⚠ 标的池表不可用, 跳过 Base 写入")
        return 0
    l2 = {r["code"]: r for r in results}
    nd = os.path.join(CACHE, "pool_list.ndjson")
    run('%s base +record-list --base-token %s --table-id %s '
        '--field-id 股票代码 --format ndjson --output %s --overwrite --as user'
        % (LARK, base_token, table_id, nd), timeout=60)
    have = {}
    try:
        for ln in open(nd, encoding="utf-8"):
            r = json.loads(ln)
            c = str(r.get("股票代码") or "").split(".")[0].strip()
            if c and r.get("record_id"):
                have[c] = r["record_id"]
    except Exception:
        pass
    src_label = "财报阅读 %s" % m.get("rpt", "")
    creates, updates = [], {}
    for e in entries:
        r = l2.get(e["code"], {})
        yoy = dict((k, yv) for k, yv, _ in e.get("checks", []) if yv is not None)

        def fmt(v):
            return ("%+.1f" % v) if isinstance(v, (int, float)) else "—"
        d = {"股票代码": e["code"], "股票名称": e["name"],
             "来源": src_label,
             "营收同比%": fmt(yoy.get("rev")), "归母同比%": fmt(yoy.get("np")), "扣非同比%": fmt(yoy.get("ded")),
             "L2分级": [r.get("grade") or "B"],
             "阶段": r.get("stage") or "", "驱动": r.get("drive") or "", "一句话": r.get("one_line") or ""}
        if doc_url:
            d["报告链接"] = "[财报阅读 %s](%s)" % (m["window"][1], doc_url)
        if e["code"] in have:
            updates[have[e["code"]]] = d
        else:
            d2 = dict(d)
            d2["纳入日期"] = e.get("nd") or m["window"][1]
            d2["状态"] = ["观察"]
            creates.append(d2)
    for i in range(0, len(creates), 100):
        pf = "pool_c%d.json" % i
        json.dump({"create_records": creates[i:i + 100]},
                  open(os.path.join(CACHE, pf), "w", encoding="utf-8"), ensure_ascii=False)
        rc, out, err = run('cd %s && %s base +record-batch-create --base-token %s --table-id %s '
                           '--json @%s --as user' % (CACHE, LARK, base_token, table_id, pf), timeout=120)
        if rc != 0:
            log("  ⚠ Base 新建失败: %s" % (err or out)[:160])
    if updates:
        json.dump({"update_records": updates},
                  open(os.path.join(CACHE, "pool_u.json"), "w", encoding="utf-8"), ensure_ascii=False)
        rc, out, err = run('cd %s && %s base +record-batch-update --base-token %s --table-id %s '
                           '--json @pool_u.json --as user' % (CACHE, LARK, base_token, table_id), timeout=120)
        if rc != 0:
            log("  ⚠ Base 更新失败: %s" % (err or out)[:160])
    log("⑦ 标的池: 新建%d 更新%d" % (len(creates), len(updates)))
    return len(creates) + len(updates)


def cmd_publish(a):
    outdir, m = _load_dir(a)
    if not m:
        return 1
    final = os.path.join(outdir, "final.md")
    if not os.path.exists(final):
        log("未找到 final.md —— 先 --finalize")
        return 1
    # 文档 -> 我的空间根目录(与历史分析文档同级; 用户约定: 不新建文件夹)
    s, e_ = m["window"]
    name = "%s年%s月%s日 财报阅读" % (s[:4], str(int(s[5:7])), str(int(s[8:10]))) if s == e_ else \
        "%s年%s月%s日~%s月%s日 财报阅读" % (s[:4], str(int(s[5:7])), str(int(s[8:10])),
                                          str(int(e_[5:7])), str(int(e_[8:10])))
    exp = os.path.join(CACHE, "publish_%s.md" % m["tag"])
    with open(exp, "w", encoding="utf-8") as f:
        f.write(open(final, encoding="utf-8").read())
    rc, out, err = run('cd %s && %s drive +import --file %s --type docx --name "%s" --as user'
                       % (CACHE, LARK, os.path.basename(exp), name), timeout=300)
    doc_url = ""
    for line in (out or "").splitlines():
        if '"url"' in line:
            doc_url = line.split('"url":')[1].strip().strip('",')
            break
    log("⑤ 飞书文档: " + (doc_url or "失败 " + (err or "")[:150]))
    # Base
    entries = m["entries"]
    l2file = os.path.join(outdir, "l2.json")
    results = json.load(open(l2file, encoding="utf-8"))["results"] if os.path.exists(l2file) else []
    written = pool_upsert(entries, results, doc_url, m)
    # 幂等台账落账
    done = load_done()
    for en in entries:
        done.add((en["code"], en.get("nd") or m["window"][1]))
    save_done(done)
    log("完成 | %s | Base写入%d" % (doc_url or "(文档失败)", written))
    return 0 if (doc_url or written) else 1


def main():
    ap = argparse.ArgumentParser(description="earnings-reader 编排器")
    ap.add_argument("--date", help="单日 YYYY-MM-DD(默认昨日)")
    ap.add_argument("--since", help="区间起点(与--until连用)")
    ap.add_argument("--until", help="区间终点")
    ap.add_argument("--draft", action="store_true", help="① 草稿+判定输入")
    ap.add_argument("--finalize", action="store_true", help="③a 终稿")
    ap.add_argument("--publish", action="store_true", help="③b Base+飞书(需先finalize)")
    ap.add_argument("--judge-file", help="judge_output.json 路径(默认 outputs/{tag}/)")
    ap.add_argument("--no-wencai", action="store_true", help="跳过问财筹码(不耗额度)")
    ap.add_argument("--force", action="store_true", help="忽略幂等台账")
    a = ap.parse_args()
    if a.draft:
        return cmd_draft(a)
    if a.finalize:
        return cmd_finalize(a)
    if a.publish:
        return cmd_publish(a)
    ap.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
