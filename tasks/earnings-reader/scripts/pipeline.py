#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pipeline.py — earnings-reader 编排器(v3 单段式, 2026-11-05 用户重定流程)。

流程(全确定性, 无模型判定, 不读财报正文):
  指定日期披露清单 -> 营收/归母/扣非 单季同比>40% 闸门 -> 近4季三线表(金额/同比/环比)
  -> 监测日报 markdown -> --publish: 飞书文档(「财报记录」文件夹, 每天一份) + 台账 Base
     按季分表 upsert(26三季报跟踪/27一季报跟踪..., 缺表自动按模板新建)

历史: h1-monitor v7.3 -> v2 三段式(draft/Claude判定/finalize, 2026-11-04, 已退役)
  -> v3 单段式(2026-11-05: 判定/L2/业绩预告/催化全删; 文档对齐「{季}报监测日报」系列;
     v3.2 台账按季分表, 字段规范沿用 26三季报跟踪: 代码/三线同比4季链/当季环比/L1档位)。

用法:
  pipeline.py --date 2026-10-03                 # 出 final.md(不发飞书)
  pipeline.py --since 2026-10-28 --until 2026-10-30
  pipeline.py --date 2026-10-03 --publish       # 文档 + Base
  可选: --force(忽略幂等台账)
"""
import argparse
import json
import os
import subprocess
import sys
import time
from datetime import date as _date, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import data          # noqa: E402

LARK = os.path.join(os.path.expanduser("~"), ".npm-global", "bin", "lark-cli")
OUTROOT = os.path.join(ROOT, "outputs")
CACHE = os.path.join(ROOT, "cache")
DONE = os.path.join(CACHE, "processed.json")           # 幂等台账

# 飞书落点(2026-11-05 用户定版: 按季分表; cache/feishu.json 可覆盖)
FEISHU = {"track_base": "J8RGbNZobaXu9VsqtFWcKUemnQe",   # 台账 Base(每季一张表)
          "doc_folder": "AXJlff1ogl42bLdUarccrWGFn8g"}   # 财报记录
CFG_FILE = os.path.join(CACHE, "feishu.json")
# 台账表字段模板(字段命名沿用 26三季报跟踪 既有规范; 新季表按此自动创建)
TRACK_FIELDS = ["代码", "股票名称", "营收同比4季", "归母同比4季", "扣非同比4季",
                "当季环比", "L1档位", "首次纳入", "报告链接"]

TH, HI_Y, HI_Q = 40.0, 100.0, 50.0
MIN_PROFIT = 5e7            # R2.5: 扭亏类当季归母下限(0.5亿)
MAX_PICKS = 120             # 上限保护: 超出按分档排序截断并在报告标注
INDS = (("rev", "营收"), ("np", "归母"), ("ded", "扣非"))


def log(msg):
    print("[%s] %s" % (time.strftime("%H:%M:%S"), msg), flush=True)


def run(cmd, timeout=300):
    p = subprocess.run(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    return (p.returncode,
            p.stdout.decode("utf-8", "replace").strip(),
            p.stderr.decode("utf-8", "replace").strip())


def _cfg():
    cfg = dict(FEISHU)
    try:
        cfg.update({k: v for k, v in json.load(open(CFG_FILE, encoding="utf-8")).items() if v})
    except Exception:
        pass
    return cfg


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
    """累计口径 -> 单季 {qlabel: {rev,np,ded}}。Q1 单季=一季报累计本身; 其余=本季累计-上季累计。"""
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
    """显示窗: 当季往前共 4 期(如 25Q4/26Q1/26Q2/26Q3)。"""
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


# ============ 纯计算: 闸门(规则出处 h1-monitor v7.3) ============

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
    """正式财报闸门 -> (passed, rejected)。
    passed: [{code,name,nd,tier,checks,q}]; rejected: [{code,name,why,yoy}](一句话呈现用)。
    闸序: R3名称 -> R2盈利 -> R1同比硬线 -> R2.5规模 -> R4锚+分档。
    当季=F10本季累计-上季累计(F10未更新则披露行兜底)。"""
    cur_l, py_l, pq_l = qlabel(y, q), qlabel(y - 1, q), qlabel(*prev_quarter(y, q))
    latest = {}
    for r in rows:
        c = str(r.get("SECURITY_CODE") or "").split(".")[0]
        nd = str(r.get("NOTICE_DATE") or "")
        if not c:
            continue
        if c not in latest or nd > latest[c]["nd"]:
            latest[c] = {"nd": nd, "rev": r.get("TOTAL_OPERATE_INCOME"), "np": r.get("PARENT_NETPROFIT")}
    passed, rejected = [], []
    for c, row in latest.items():
        nm = names.get(c) or c
        if is_risk_name(nm):
            rejected.append({"code": c, "name": nm, "why": "ST/退市警示出局"})
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
            rejected.append({"code": c, "name": nm, "why": "当季亏损出局"})
            continue
        yoys, qoqs = {}, {}
        for k, _ in INDS:
            yoys[k] = pct(cur.get(k), (qq.get(py_l) or {}).get(k))
            qoqs[k] = pct(cur.get(k), (qq.get(pq_l) or {}).get(k))
        # R1 同比硬线: 可算且≤40% -> 假成长出局(环比无否决权, v7.3)
        if any(v is not None and not v > TH for v in yoys.values()):
            rejected.append({"code": c, "name": nm, "why": "同比未达40%", "yoy": yoys})
            continue
        checks = [(k, yoys[k], qoqs[k]) for k, _ in INDS
                  if yoys[k] is not None or qoqs[k] is not None]
        if not checks:
            rejected.append({"code": c, "name": nm, "why": "同比环比均不可算(基数≤0)"})
            continue
        # R2.5: 归母+扣非同比全不可算(扭亏类)时, 当季归母须达规模(0.5亿)
        if not any(k in ("np", "ded") for k, yv, _ in checks if yv is not None) and \
           (cur.get("np") is None or cur["np"] < MIN_PROFIT):
            rejected.append({"code": c, "name": nm, "why": "扭亏规模不足(当季归母<0.5亿)"})
            continue
        t = tier_of(checks)
        if not t:
            rejected.append({"code": c, "name": nm, "why": "无锚(同比≤100%且环比≤50%)"})
            continue
        passed.append({"code": c, "name": nm, "nd": row["nd"][:10],
                       "tier": t, "checks": checks, "q": qq})
    return passed, rejected


# ============ 渲染 ============

def _f(v):
    return "%.2f" % (v / 1e8) if v is not None else "—"


def _pp(v, base):
    if v is None or base is None:
        return "—"
    if base <= 0:
        return "基数≤0"
    return "%+.1f%%" % ((v / base - 1) * 100)


def qt(e, y, q):
    """近4季三线表: 季度做列; 每指标 金额/同比/环比 三行。"""
    qq = e.get("q") or {}
    cols = display_quarters(y, q)
    cur_l = cols[-1]
    headtxt = " | ".join(("%s(当季)" % cur_l) if k == cur_l else k for k in cols)
    rows = ["| 单季 | %s |" % headtxt, "|" + "---|" * (len(cols) + 1)]

    def amt(k, ind):
        return _f((qq.get(k) or {}).get(ind))

    def rate(k, ind, yoy):
        base = (qq.get(yoy_of(k) if yoy else qoq_of(k)) or {}).get(ind)
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


TIER_LABEL = {"特档": "特档·双高", "一档": "第一档·单高", "二档": "第二档·稳增"}


def doc_title(season, start, end, codes_n):
    d = start if start == end else "%s~%s" % (start[5:], end[5:])
    return "%s监测日报 %s(%d家)" % (season, d, codes_n)


def gen_report(passed, rejected, src, codes_n, start, end, season, y, q, truncated=0):
    title = doc_title(season, start, end, codes_n)
    win = start if start == end else "%s~%s" % (start, end)
    L = ["# " + title, "",
         "> 分析范围: %s 披露的%s **%d 家**; 三线40%%闸门过闸 **%d 家**。数据源: %s。"
         % (win, season, codes_n, len(passed), src), "",
         "> 闸门: 营收/归母/扣非 单季同比可算侧全>40%; 当季亏损出局; 扭亏类当季归母≥0.5亿; "
         "ST/退市警示出局; 环比不否决仅呈现。分析引擎: earnings-reader(确定性)。", ""]
    if truncated:
        L.append("> ⚠ 过闸超上限, 按分档+强度截断 %d 家。" % truncated)
        L.append("")
    if passed:
        order = {"特档": 0, "一档": 1, "二档": 2}

        def strength(e):
            ys = [yv for _, yv, _ in e["checks"] if yv is not None]
            qs = [qv for _, _, qv in e["checks"] if qv is not None]
            return (max(ys + qs) if (ys or qs) else 0)
        es = sorted(passed, key=lambda e: (order[e["tier"]], -strength(e)))
        L += ["## ⏰ 过闸标的 ×%d" % len(es), ""]
        for e in es:
            cur = (e.get("q") or {}).get(qlabel(y, q)) or {}
            L.append("- **%s(%s)**【%s】%s · 当季营收%s亿/归母%s亿/扣非%s亿 → 入台账跟踪。"
                     % (e["name"], data._secu(e["code"]), TIER_LABEL[e["tier"]], highlight(e),
                        _f(cur.get("rev")), _f(cur.get("np")), _f(cur.get("ded"))))
            L += ["", qt(e, y, q), ""]
    if rejected:
        L += ["## 未过闸(%d家, 一句话带过)" % len(rejected), ""]
        lbl = dict(INDS)
        for r in sorted(rejected, key=lambda r: r["code"]):
            vals = ""
            if r.get("yoy"):
                seg = ["%s%+.1f%%" % (lbl[k], v) for k, v in r["yoy"].items() if v is not None]
                vals = (" ".join(seg) + " → ") if seg else ""
            L.append("- **%s(%s)** %s%s。" % (r["name"], data._secu(r["code"]), vals, r["why"]))
    L += ["", "---", "", "*非投资建议 · earnings-reader · %s*" % start]
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


# ============ 发布: 飞书文档 + 重点跟踪标的 Base ============

def publish_doc(final_path, title):
    """final.md -> 飞书文档(「财报记录」文件夹)。-> doc_url"""
    cfg = _cfg()
    exp = os.path.join(CACHE, "publish_%d.md" % int(time.time()))
    with open(exp, "w", encoding="utf-8") as f:
        f.write(open(final_path, encoding="utf-8").read())
    rc, out, err = run('cd %s && %s drive +import --file %s --type docx --name "%s" '
                       '--folder-token %s --as user'
                       % (CACHE, LARK, os.path.basename(exp), title, cfg["doc_folder"]), timeout=300)
    os.unlink(exp)
    for line in (out or "").splitlines():
        if '"url"' in line:
            return line.split('"url":')[1].strip().strip('",')
    log("  ⚠ 文档发布失败: " + (err or out or "")[:200])
    return ""


def track_table_name(season, y):
    """按季分表名: 26三季报跟踪 / 27一季报跟踪 ...(对齐既有命名)。y=报告期年份。"""
    return "%d%s跟踪" % (y % 100, season)


def resolve_track_table(season, y):
    """季表解析: 按名找, 缺则按 TRACK_FIELDS 模板新建。-> (base_token, table_id)"""
    cfg = _cfg()
    base, name = cfg["track_base"], track_table_name(season, y)
    rc, out, _ = run("%s base +table-list --base-token %s --as user --format json"
                     % (LARK, base), timeout=60)
    try:
        d = json.loads(out[out.index("{"):])
        for t in (d.get("data", {}).get("tables") or []):
            if t.get("name") == name:
                return base, t.get("id") or ""
    except Exception:
        pass
    fields = "'" + json.dumps([{"field_name": f, "type": "text"} for f in TRACK_FIELDS],
                              ensure_ascii=False).replace("'", "'\\''") + "'"
    rc, out, err = run("%s base +table-create --base-token %s --name '%s' --fields %s "
                       "--as user --format json" % (LARK, base, name, fields), timeout=120)
    tid = ""
    try:
        d = json.loads(out[out.index("{"):])
        tid = (d.get("data") or {}).get("table_id") or ""
    except Exception:
        pass
    if tid:
        log("⑥b 新建季表「%s」: %s" % (name, tid))
    else:
        log("  ⚠ 季表「%s」新建失败: %s" % (name, (err or out or "")[:150]))
    return base, tid


def _series4(qq, cols, ind):
    """4季同比链 '+33.4→+18.5→+25.7→+57.8'(旧→新, 收于当季); 不可算→'—'。"""
    parts = []
    for k in cols:
        yv = pct((qq.get(k) or {}).get(ind), (qq.get(yoy_of(k)) or {}).get(ind))
        parts.append(("%+.1f" % yv) if yv is not None else "—")
    return "→".join(parts)


def base_rows(passed, doc_label, doc_url, y, q):
    """passed -> 季表行(字段命名沿用 26三季报跟踪 既有规范)。
    首次纳入只在新build时由 base_upsert 补(人流转字段, 更新不覆盖)。"""
    cols = display_quarters(y, q)
    cur_l = cols[-1]
    rows = []
    for e in passed:
        qq = e.get("q") or {}
        cur = qq.get(cur_l) or {}
        qoqs = []
        for k, _ in INDS:                       # 当季环比: 营收/归母/扣非
            qv = pct(cur.get(k), (qq.get(qoq_of(cur_l)) or {}).get(k))
            qoqs.append(("%+.1f" % qv) if qv is not None else "—")
        d = {"代码": e["code"], "股票名称": e["name"],
             "营收同比4季": _series4(qq, cols, "rev"),
             "归母同比4季": _series4(qq, cols, "np"),
             "扣非同比4季": _series4(qq, cols, "ded"),
             "当季环比": " / ".join(qoqs),
             "L1档位": TIER_LABEL[e["tier"]]}
        if doc_url:
            d["报告链接"] = "[%s](%s)" % (doc_label, doc_url)
        rows.append(d)
    return rows


def base_upsert(passed, doc_label, doc_url, window, season, y, q):
    """upsert by 代码: record-list 拉码->rid, 新建缺失/更新已有。-> 写入条数"""
    cfg = _cfg()
    base, table = resolve_track_table(season, y)
    if not table:
        log("  ⚠ 季表不可用, 跳过 Base 写入")
        return 0
    nd = os.path.join(CACHE, "track_list.ndjson")
    run('cd %s && %s base +record-list --base-token %s --table-id %s '
        '--field-id 代码 --format ndjson --output %s --overwrite --as user'
        % (CACHE, LARK, base, table, os.path.basename(nd)), timeout=60)
    have = {}
    try:
        for ln in open(nd, encoding="utf-8"):
            r = json.loads(ln)
            c = str(r.get("代码") or "").strip()
            if c and r.get("record_id"):
                have[c] = r["record_id"]
    except Exception:
        pass
    rows = base_rows(passed, doc_label, doc_url, y, q)
    creates, updates = [], {}
    for d in rows:
        if d["代码"] in have:
            updates[have[d["代码"]]] = d
        else:
            d2 = dict(d)
            d2["首次纳入"] = window[1]
            creates.append(d2)

    def _sh(payload):
        return "'" + json.dumps(payload, ensure_ascii=False).replace("'", "'\\''") + "'"
    written = 0
    for i in range(0, len(creates), 100):     # 单批≤200, 保守 100
        rc, out, err = run('%s base +record-batch-create --base-token %s --table-id %s '
                           '--json %s --as user'
                           % (LARK, base, table, _sh({"create_records": creates[i:i + 100]})),
                           timeout=120)
        written += len(creates[i:i + 100]) if rc == 0 else 0
        if rc != 0:
            log("  ⚠ Base 新建批次失败: " + (err or out or "")[:200])
    if updates:
        rc, out, err = run('%s base +record-batch-update --base-token %s --table-id %s '
                           '--json %s --as user'
                           % (LARK, base, table, _sh({"update_records": updates})),
                           timeout=120)
        written += len(updates) if rc == 0 else 0
        if rc != 0:
            log("  ⚠ Base 更新失败: " + (err or out or "")[:200])
    return written


# ============ 主流程 ============

def cmd_run(a):
    if a.since:
        start, end = a.since, (a.until or a.since)
    else:
        d = a.date or (_date.today() - timedelta(days=1)).isoformat()
        start = end = d
    tag = start if start == end else "%s_%s" % (start, end)
    outdir = os.path.join(OUTROOT, tag)
    os.makedirs(outdir, exist_ok=True)
    log("run: 窗口[%s~%s]%s" % (start, end, " (publish)" if a.publish else ""))

    # 1) 清单 + 报告期识别(只取正式财报)
    f_rows, w_rows, src = data.disclosure_lists(start, end)
    if not src:
        log("全部数据源不可用, 退出(重试或检查网络)")
        return 1
    rpt, season = data.detect_period(f_rows, w_rows)
    if not rpt:
        # 零披露窗口(如节假日): 按窗口月份推断季节, 出 0 家报告(对齐历史监测日报系列)
        rpt, season = data._season_tag(start, end)
        log("① 清单: %s源 窗口零披露 | 报告期按月推断=%s(%s)" % (src, rpt, season))
    f_rows = [r for r in f_rows if str(r.get("REPORTDATE") or "")[:10] == rpt]
    y, q = quarter_of(rpt)
    log("① 清单: %s源 正式%d行 | 报告期=%s(%s)" % (src, len(f_rows), rpt, season))

    # 2) 名称
    names, codes = {}, set()
    for r in f_rows:
        c = str(r.get("SECURITY_CODE") or "").split(".")[0]
        if c:
            codes.add(c)
            n = r.get("SECURITY_NAME_ABBR") or ""
            if n:
                names[c] = n
    missing = [c for c in sorted(codes) if not names.get(c)]
    if missing:
        names.update(data.tx_names(missing))
    codes_n = len(codes)
    log("② 名称: %d/%d (腾讯兜底%d)" % (len([c for c in codes if names.get(c)]), codes_n, len(missing)))

    # 3) 幂等过滤
    done = load_done()
    if a.force:
        log("②b --force: 幂等台账忽略")
        fresh_f = f_rows
    else:
        fresh_f = [r for r in f_rows if (str(r.get("SECURITY_CODE") or "").split(".")[0],
                                         str(r.get("NOTICE_DATE") or "")[:10]) not in done]

    # 4) 历史三指标(F10 看门狗批量)
    todo = sorted(set([str(r.get("SECURITY_CODE") or "").split(".")[0] for r in fresh_f if r.get("SECURITY_CODE")]))
    hist = {}

    def _f10_prog(nd, total):
        if nd % 500 < 50 or nd == total:
            log("③ 历史F10(看门狗): %d/%d" % (nd, total))
    got = data.f10_batch(todo, progress=_f10_prog)
    for c, (cum, _qual) in got.items():
        hist[c] = (cum, quarter_amounts(cum, y, q))
    log("③ 历史F10: %d/%d" % (len(hist), len(todo)))

    # 5) 闸门
    passed, rejected = process_formal(fresh_f, hist, names, y, q)
    truncated = 0
    if len(passed) > MAX_PICKS:
        order = {"特档": 0, "一档": 1, "二档": 2}

        def strength(e):
            ys = [yv for _, yv, _ in e["checks"] if yv is not None]
            qs = [qv for _, _, qv in e["checks"] if qv is not None]
            return (max(ys + qs) if (ys or qs) else 0)
        passed.sort(key=lambda e: (order[e["tier"]], -strength(e)))
        truncated = len(passed) - MAX_PICKS
        passed = passed[:MAX_PICKS]
        log("④ 截断保护: ->%d (按分档+强度)" % MAX_PICKS)
    log("④ 闸门: 过闸%d 未过闸%d (披露%d家)" % (len(passed), len(rejected), codes_n))

    # 6) 报告 + 快照
    title = doc_title(season, start, end, codes_n)
    md = gen_report(passed, rejected, src, codes_n, start, end, season, y, q, truncated=truncated)
    final = os.path.join(outdir, "final.md")
    with open(final, "w", encoding="utf-8") as f:
        f.write(md)
    machine = {"tag": tag, "window": [start, end], "src": src, "rpt": rpt, "season": season,
               "y": y, "q": q, "disclosed": codes_n, "title": title,
               "entries": [{k: e.get(k) for k in ("code", "name", "nd", "tier", "checks")} for e in passed],
               "rejected": rejected, "degraded": data.DEGRADED}
    with open(os.path.join(outdir, "machine.json"), "w", encoding="utf-8") as f:
        json.dump(machine, f, ensure_ascii=False, indent=1)
    log("⑤ final.md: %s | 过闸%d 未过闸%d | 降级%d项"
        % (final, len(passed), len(rejected), len(data.DEGRADED)))

    # 7) 发布
    if not a.publish:
        log("完成(未发布) | 下一步: --publish 发文档+Base")
        return 0
    doc_url = publish_doc(final, title)
    log("⑥ 飞书文档: " + (doc_url or "失败"))
    doc_label = title.rsplit("(", 1)[0]        # 「三季报监测日报 2026-10-15」
    written = base_upsert(passed, doc_label, doc_url, machine["window"], season, y, q) if passed else 0
    log("⑦ 季表[%s]: 写入%d" % (track_table_name(season, y), written))
    for e in passed:                          # 幂等台账: 发布成功才落账
        done.add((e["code"], e.get("nd") or start))
    save_done(done)
    log("完成 | %s | Base写入%d" % (doc_url or "(文档失败)", written))
    return 0 if (doc_url or written) else 1


def main():
    ap = argparse.ArgumentParser(description="earnings-reader 编排器(v3 单段式)")
    ap.add_argument("--date", help="单日 YYYY-MM-DD(默认昨日)")
    ap.add_argument("--since", help="区间起点(与--until连用)")
    ap.add_argument("--until", help="区间终点")
    ap.add_argument("--publish", action="store_true", help="发布飞书文档+重点跟踪标的 Base")
    ap.add_argument("--force", action="store_true", help="忽略幂等台账")
    a = ap.parse_args()
    return cmd_run(a)


if __name__ == "__main__":
    sys.exit(main())
