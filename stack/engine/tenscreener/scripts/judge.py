#!/usr/bin/env python3
"""
judge.py — tenscreener 单标的十倍股判定引擎（v3 判断权威, 2026-11-05）。

定位（族宪法 v3）：给一个代码，产「判定素材包」→ Claude 读包 + jingshui/can-slim/lynch
三方法论标准 → 写 verdict.json → finalize 校验并组装报告。判定内核复用现有零件:
  emoney(F10三线/预告) + quarters(序列分析) + runner(七阶段分类) + scorer(TPS数字因子)
新增的是「读财报」维度(仅厚报告下PDF抽章节; 薄报告全走API——PDF只为MD&A叙述层服务)与九类关注内容的免费取数(零问财额度):
  资产负债表先行(合同负债/存货/货币资金/有息负债/商誉) + 主营构成(分业务/地区)
  + 十大流通股东进退 + 股东户数趋势 + 经营评述 + 价格位置 + 大盘β(免费K线版)

用法(三段式, 经 cli.py judge 入口):
  python3 scripts/cli.py judge --code 603259            # ① 取数+素材包 pack.md/pack.json
  (Claude 读 pack.md → 写同目录 verdict.json)            # ② 判定(对话内)
  python3 scripts/cli.py judge --code 603259 --finalize # ③ 校验+组装 report.md

全免费源: 东财 F10/资产负债表/股东/估值 + emweb 主营构成/经营评述 + 腾讯K线 + 巨潮PDF。
"""
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import date, datetime

from emoney import (f10_periods, predicts, forecast_signal, three_line_qs)
from quarters import latest_report_period, last_n_quarters, q_label, q_cn, analyze_quarterly
import kline
import trend

HERE = os.path.dirname(os.path.abspath(__file__))
OUTROOT = os.path.join(os.path.dirname(HERE), "outputs", "judge")
SEC_HOST = "https://datacenter.eastmoney.com/securities/api/data/v1/get"
UA = {"User-Agent": "Mozilla/5.0", "Referer": "https://data.eastmoney.com"}

# cninfo-disclosure 底座 cli 的探测位置(同 flow.py 先例: 同级技能优先;
# 2026-11-07 加环境变量 CNINFO_CLI 与 TENSCREENER_SKILLS_DIR —— 仓库公开后他人路径各异)
CNINFO_CLI_CANDIDATES = [
    os.environ.get("CNINFO_CLI") or "",
    os.path.join(os.path.dirname(HERE), "cninfo-disclosure", "scripts", "cli.py"),
    os.path.join(os.environ.get("TENSCREENER_SKILLS_DIR") or "/nonexistent",
                 "cninfo-disclosure", "scripts", "cli.py"),
    os.path.expanduser("~/Documents/work/.claude/skills/cninfo-disclosure/scripts/cli.py"),
    os.path.expanduser("~/.claude/skills/cninfo-disclosure/scripts/cli.py"),
]

DEGRADED = []

# PDF 归档路径四级解析(用户定版 2026-11-05, 技能已公开他人路径各异):
#   --pdf-dir 单次指定(同时持久化为默认) > 环境变量 TENSCREENER_PDF_DIR
#   > cache/config.json 持久默认 > 兜底 outputs/pdf_library(零配置可用)
PDF_ENV = "TENSCREENER_PDF_DIR"
PDF_CFG = os.path.join(os.path.dirname(HERE), "cache", "config.json")
DEFAULT_PDF_LIB = os.path.join(OUTROOT, "pdf_library")


def resolve_pdf_dir(cli_val=None):
    """-> (dir, 来源)。cli_val 显式传入时持久化到 cache/config.json(首次指定即默认)。"""
    if cli_val:
        _save_cfg({"pdf_dir": cli_val})
        return cli_val, "本次指定(已记住为默认)"
    env = os.environ.get(PDF_ENV)
    if env:
        return env, "环境变量"
    v = _load_cfg().get("pdf_dir")
    if v:
        return v, "持久配置"
    return DEFAULT_PDF_LIB, "默认(首次使用可 --pdf-dir 指定并记住)"


def _load_cfg():
    try:
        return json.load(open(PDF_CFG, encoding="utf-8"))
    except Exception:
        return {}


def _save_cfg(updates):
    try:
        os.makedirs(os.path.dirname(PDF_CFG), exist_ok=True)
        cfg = _load_cfg()
        cfg.update(updates)
        json.dump(cfg, open(PDF_CFG, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    except Exception:
        pass


LARK = os.path.join(os.path.expanduser("~"), ".npm-global", "bin", "lark-cli")


def resolve_folder(cli_val=None):
    """飞书目标文件夹(判定报告落点): --folder 指定(持久) > config.judge_folder > 根目录。"""
    if cli_val:
        _save_cfg({"judge_folder": cli_val})
        return cli_val, "本次指定(已记住为默认)"
    v = _load_cfg().get("judge_folder")
    if v:
        return v, "持久配置"
    return "", "我的空间根目录"


def _run_dir(code6):
    """该代码最新判定目录: 优先含 verdict.json 的最新目录(快速/完整模式并存时)。"""
    dirs = sorted((d for d in os.listdir(OUTROOT) if d.startswith(code6 + "_")), reverse=True)
    base = next((os.path.join(OUTROOT, d) for d in dirs
                 if os.path.exists(os.path.join(OUTROOT, d, "verdict.json"))), None)
    if base is None:
        base = os.path.join(OUTROOT, dirs[0]) if dirs else None
    return base


def _cleanup(base, keep=()):
    """清中间产物(素材包/判定/章节/PDF库副本); keep 之外的产物全清。"""
    import shutil
    for f in ("pack.md", "pack.json", "texts.json", "verdict.json", "report.md"):
        if f in keep:
            continue
        try:
            os.remove(os.path.join(base, f))
        except Exception:
            pass
    if "pdf" not in keep:
        shutil.rmtree(os.path.join(base, "pdf"), ignore_errors=True)


def _reject_summary_md(pack, v, errs):
    """判定为否的对话结论(不发飞书不落盘)。"""
    name = pack.get("name") or ""
    code = pack.get("code") or ""
    L = ["## ✗ 判定为否 · %s(%s)" % (name, code), "",
         "- **是否十倍股候选: 否**(置信 %s)" % v.get("confidence"),
         "- **所处阶段: %s** —— %s" % (v.get("stage"), v.get("stage_reason") or ""),
         "- **清单结论**: %s" % (v.get("conclusion") or "—"),
         "- **林奇分类**: %s" % v.get("lynch_class")]
    if v.get("risks"):
        L.append("- **主要风险**: %s" % "; ".join(v["risks"][:3]))
    if errs:
        L.append("- ⚠ verdict 校验: %s" % "; ".join(errs))
    L += ["", "(v4.1 发布策略: 判定为否不发飞书、不保留本地文件; 中间产物已清理)"]
    return "\n".join(L)


def publish(code, folder=None):
    """v4.1 条件发布: 仅 is_tenbagger_candidate=true → 飞书文档(全量)且本地不留 report.md
    (仅存 feishu_url.txt 指针); 判定为否 → 对话返回结论并清理全部产物, 不落盘不发文档。"""
    code6 = str(code).split(".")[0]
    base = _run_dir(code6)
    if not base or not os.path.exists(os.path.join(base, "verdict.json")):
        print("未找到 %s 的 verdict.json —— 先跑 judge --code 产素材包并完成三典判定" % code6)
        return False, None
    pack = {}
    pj = os.path.join(base, "pack.json")
    if os.path.exists(pj):
        pack = json.load(open(pj, encoding="utf-8"))
    v, errs = load_verdict(os.path.join(base, "verdict.json"))
    if v is None:
        return False, None
    if not v.get("is_tenbagger_candidate"):
        print(_reject_summary_md(pack, v, errs))
        _cleanup(base)
        return True, None
    ok, path = finalize(code)
    if not ok or not path:
        return False, None
    name, asof = pack.get("name") or code6, pack.get("asof") or date.today().isoformat()
    folder_tok, src = resolve_folder(folder)
    exp = "/tmp/ts_judge_publish_%s.md" % code6
    open(exp, "w", encoding="utf-8").write(open(path, encoding="utf-8").read())
    import subprocess
    # list 形式 + cwd(2026-11-07: 旧 shell=True 拼接, 股票名含引号/\$ 会破命令)
    cmd = [LARK, "drive", "+import", "--file", os.path.basename(exp),
           "--type", "docx", "--name", "%s(%s)十倍股判定 %s" % (name, code6, asof),
           "--as", "user"]
    if folder_tok:
        cmd += ["--folder-token", folder_tok]
    try:
        p = subprocess.run(cmd, cwd="/tmp", capture_output=True, timeout=300)
        out = (p.stdout or b"").decode("utf-8", "ignore")
    except Exception as e:
        print("飞书发布失败: %s" % str(e)[:120])
        return False, None
    url = ""
    for line in out.splitlines():
        if '"url"' in line:
            url = line.split('"url":')[1].strip().strip('",')
            break
    if url:
        # v4.1: 本地只留 feishu_url.txt 指针(用户定稿: 不保留 report.md)
        _cleanup(base, keep=("feishu_url.txt", ))
        open(os.path.join(base, "feishu_url.txt"), "w", encoding="utf-8").write(url)
        print("已发布(%s): %s" % (src, url))
        print("本地仅保留 feishu_url.txt: %s" % base)
    else:
        print("飞书发布失败: %s" % ((p.stderr or b"").decode("utf-8", "ignore")[:160] or out[:160]))
    return bool(url), url


def degrade(fn, why):
    DEGRADED.append((fn, why))


def _secu(code):
    code = str(code).split(".")[0]
    return code + (".SH" if code[0] in ("6", "9") else ".BJ" if code[0] in ("4", "8") else ".SZ")


def _http_json(url, referer="https://data.eastmoney.com", timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Referer": referer})
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read().decode())


def _sec_rows(report, code, sort="REPORT_DATE", extra_filter=None):
    """datacenter(securities) host 通用取数(FREEHOLDERS/HOLDERNUM/GBALANCE 实测可用)。
    坑: RPT_F10_BASIC_ORGINFO 不能带 sortColumns(返回空, earnings-reader 实测同款坑) → sort=None 时省略。
    当日落盘缓存(2026-11-07): 同一 report+code 二次调用零请求——修 build_pack 里
    FREEHOLDERS 被 holders_shift 与 trend.holders_timeline 各取一次的重复。"""
    from emoney import EM_CACHE_ROOT
    code6 = str(code).split(".")[0]
    cache_dir = os.path.join(EM_CACHE_ROOT, date.today().strftime("%Y%m%d"))
    cf = os.path.join(cache_dir, "sec_%s_%s_%s.json" % (
        report, code6, "f" if extra_filter else (sort or "nos")))
    if os.path.exists(cf):
        try:
            return json.load(open(cf, encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    p = {"reportName": report, "columns": "ALL", "pageSize": 40, "pageNumber": 1,
         "filter": '(SECUCODE="%s")' % _secu(code)}
    if sort:
        p["sortColumns"] = sort
        p["sortTypes"] = -1
    if extra_filter:
        p["filter"] = extra_filter
    u = SEC_HOST + "?" + urllib.parse.urlencode(p)
    try:
        d = _http_json(u)
        time.sleep(0.12)
        rows = (d.get("result") or {}).get("data") or []
        try:
            os.makedirs(cache_dir, exist_ok=True)
            json.dump(rows, open(cf, "w", encoding="utf-8"), ensure_ascii=False)
        except OSError:
            pass
        return rows
    except Exception as e:
        degrade("_sec_rows", "%s %s" % (report, str(e)[:60]))
        return []


def _f(v, div=None):
    if v is None:
        return None
    return round(v / div, 2) if div else round(v, 2)


def _yi(v):
    return _f(v, 1e8) if isinstance(v, (int, float)) else None


# ============ ① 报告期定位: 最近4份(两厚两薄) ============

def four_reports(code):
    """-> [(y,q,label,厚/薄)]。厚=半年报/年报(含MD&A/募投/风险), 薄=一季报/三季报。
    以 F10 实际披露过的报告期为准(未披露的不取)。"""
    periods = sorted(f10_periods(code), key=lambda s: (s[:4], s[-1]))
    latest = periods[-1] if periods else q_label(*latest_report_period())
    take = last_n_quarters((int(latest[:4]), int(latest[-1])), 4)
    out = []
    for y, q in take:
        lbl = q_label(y, q)
        out.append({"y": y, "q": q, "label": lbl, "cn": q_cn(y, q),
                    "thick": q in (2, 4), "has_data": lbl in periods})
    return out, periods


# ============ ② 三线序列(F10免费链) → quarters 分析结构 ============
# three_line_qs 已移入 emoney.py(2026-11-07: watch/track 免问财化共用入口)


def qoq_table(singles, n=4):
    """近 n 季单季金额 + 同比 + 环比(环比含季节噪声, 只呈现供参考)。
    首个显示季的环比取显示窗外上一季(singles 常有 8+ 季历史, 2026-11-05 用户反馈修正:
    此前 i==0 一律返回 None, 造成首季环比假缺失)。"""
    labels = sorted(singles)[-n:]

    def prev_label(lbl):
        if len(lbl) == 4:                       # 运行时格式 "26Q1"(qlabel 两位年)
            yy, q = 2000 + int(lbl[:2]), int(lbl[3])
            py, pq = (yy - 1, 4) if q == 1 else (yy, q - 1)
            return "%02dQ%d" % (py % 100, pq)
        y4, q = int(lbl[:4]), int(lbl[5])       # 兼容 "2026Q1" 四位年标签
        py, pq = (y4 - 1, 4) if q == 1 else (y4, q - 1)
        return "%dQ%d" % (py, pq)

    def qoq(lbl):
        p = prev_label(lbl)
        row = []
        for v, b in zip(singles.get(lbl, (None,) * 3), singles.get(p, (None,) * 3)):
            row.append(round((v / b - 1) * 100, 1) if (v is not None and b is not None and b > 0) else None)
        return tuple(row)

    out = []
    for lbl in labels:
        out.append({"label": lbl, "single_yi": tuple(_yi(v) for v in singles.get(lbl, (None,) * 3)),
                    "qoq": qoq(lbl)})
    return out, labels


# ============ ⓪ Stage1 三线40%硬闸(v4.1, 2026-11-05 用户定稿A案) ============
# 对齐 earnings-reader R1 家族标准: 当季三线可算侧同比全>40% + 不亏损 + 扭亏通道(归母≥0.5亿)。
# 不过 → 秒级否决(对话结论卡, 不下PDF不深读不落盘); 环比呈现不否决(静水字面为同比)。

GATE_TH = 40.0       # 静水字面增速线(与族 PASS_LINE 一致)
GATE_MIN_NP = 5e7    # 扭亏通道当季归母下限(0.5亿)


def _forecast_gate_ok(forecast, latest):
    """预告爆发通道:预告中枢倒推 pass 且预告报告期晚于最新已披露期。
    出处:静水财报分析框架§一.1"预告类取中枢测算,倒推最新单季环比/同比"
    (松发/源杰/寒武纪型 —— v4.1 硬闸曾把这类"最早捕获目标"秒否, 2026-11-07 补全)。"""
    return bool(forecast and forecast.get("pass") and forecast.get("period")
                and (not latest or forecast["period"] > latest))


def gate_verdict(qs, singles, forecast=None):
    """纯函数闸门(可离线单测) -> (passed, reason, latest, kind)。
    kind: pass / reject / nodata(数据源故障不否决)。
    通道优先级:三线40% > 扭亏通道(归母≥0.5亿) > 预告爆发通道(forecast 中枢倒推爆发,
    且预告期晚于已披露期)——豁免一切 reject。预告通道=允许深读, 不是买入判定
    (5不买#1/#2 仍由 Stage3 三典判定把关, 对应"利润未兑现/预告爆发"阶段)。"""
    latest = (qs or {}).get("latest_period")
    if not latest:
        return False, "F10 三线数据不可得", None, "nodata"
    passed, reason, kind = None, None, None
    rev, np_, ded = (singles or {}).get(latest) or (None, None, None)
    lbl = {"rev": "营收", "np": "归母", "ded": "扣非"}
    seg_all = " ".join("%s%s" % (lbl[k], ("%+.1f%%" % ((qs.get(k_map) or {}).get(latest)))
                                 if (qs.get(k_map) or {}).get(latest) is not None else "同比不可算")
                       for k, k_map in (("rev", "rev_yoy"), ("np", "parent_yoy"), ("ded", "deduct_yoy")))
    if (np_ is not None and np_ <= 0) or (ded is not None and ded <= 0):
        passed, kind = False, "reject"
        reason = ("当季亏损出局(归母%s/扣非%s)"
                  % tuple(("%.1f亿" % (v / 1e8)) if v is not None else "—" for v in (np_, ded)))
    else:
        yys = {"rev": (qs.get("rev_yoy") or {}).get(latest),
               "np": (qs.get("parent_yoy") or {}).get(latest),
               "ded": (qs.get("deduct_yoy") or {}).get(latest)}
        comp = {k: v for k, v in yys.items() if v is not None}
        if any(v <= GATE_TH for v in comp.values()):
            passed, kind = False, "reject"
            reason = "当季同比未全过40%%线(%s; 可算侧须全>40%%)" % seg_all
        elif not any(k in ("np", "ded") for k in comp):   # 归母+扣非同比全不可算=扭亏类
            if np_ is None or np_ < GATE_MIN_NP:
                passed, kind = False, "reject"
                reason = ("扭亏规模不足(归母/扣非同比全不可算, 当季归母%s < 0.5亿)"
                          % (("%.1f亿" % (np_ / 1e8)) if np_ is not None else "缺值"))
            else:
                passed = True
                reason = "扭亏通道过闸(当季归母%.1f亿≥0.5亿, %s)" % (np_ / 1e8, seg_all)
        else:
            passed = True
            reason = "当季三线同比全>40%%(%s)" % seg_all
    if not passed and _forecast_gate_ok(forecast, latest):
        return True, ("预告爆发通道过闸(%s @%s, 中枢单季%s亿 —— 静水预告倒推法; "
                      "实绩三线: %s)" % (forecast.get("basis"), forecast.get("period"),
                                          forecast.get("single_yi"), seg_all)), latest, "pass"
    return passed, reason, latest, (kind or ("pass" if passed else "reject"))


def stage1_gate(code):
    """Stage1 取数+闸门(F10免费源, 秒级)。-> (passed, qs, singles, reason, latest, kind)
    不过闸时补查业绩预告(免费)——预告中枢倒推爆发则走预告通道过闸。"""
    code6 = str(code).split(".")[0]
    try:
        qs, singles, periods = three_line_qs(code6)
    except Exception as e:
        return False, None, None, "三线取数失败: %s" % str(e)[:80], None, "nodata"
    fc = None
    try:
        pr = predicts(code6)
        if pr:
            fc = forecast_signal(periods, pr, date.today().isoformat())
    except Exception:
        fc = None
    passed, reason, latest, kind = gate_verdict(qs, singles, forecast=fc)
    return passed, qs, singles, reason, latest, kind


def quick_reject_md(name, code, qs, singles, reason):
    """Stage1 否决结论卡: 仅对话呈现 —— 不落盘、不发飞书、不做深读。"""
    qt, _ = qoq_table(singles or {}, 4)
    L = ["## ⛔ 快速否决 · %s(%s) 非十倍股候选" % (name or code, code), "",
         "> Stage1 三线40%硬闸未过(秒级判定, 未做报告深读) · 非投资建议", "",
         "**否决原因**: %s" % reason, "", "近4季三线(F10自算, 单季):", ""]
    rows = []
    for t in qt:
        yy = [(qs.get(k) or {}).get(t["label"]) for k in ("rev_yoy", "parent_yoy", "deduct_yoy")]
        rows.append([t["label"],
                     "%s/%s/%s" % tuple(("%.1f" % v) if v is not None else "—" for v in t["single_yi"]),
                     "/".join(("%+.1f" % v) if v is not None else "—" for v in yy),
                     "%s/%s/%s" % tuple(("%+.0f" % v) if v is not None else "—" for v in t["qoq"])])
    L += _tbl(["季", "单季亿(营/归/扣)", "同比%", "环比%"], rows)
    L += ["", "*依据: 静水核心因子#1「财报营收、利润增速≥40%」· 5不买#2「主营增速低于40%的不买」。*",
          "*如需绕闸深读(边缘案例人工指定): `judge --code %s --force-full`*" % code]
    return "\n".join(L)


def quality_rows(code):
    """F10 同表质地: 毛利率(累计)趋势 / ROE / 负债率 / 净现比。"""
    from emoney import _get
    rows = _get("RPT_F10_FINANCE_MAINFINADATA", code)
    ml, out = {}, []
    for r in rows:
        rd = (r.get("REPORT_DATE") or "")[:10]
        if not rd:
            continue
        y, m = int(rd[:4]), int(rd[5:7])
        q = {3: 1, 6: 2, 9: 3, 12: 4}.get(m)
        if not q:
            continue
        if r.get("XSMLL") is not None:
            ml["%dQ%d" % (y, q)] = r["XSMLL"]
        out.append({"label": "%dQ%d" % (y, q), "roe": r.get("ROEJQ"), "roic": r.get("ROIC"),
                    "debt": r.get("ZCFZL"),
                    "ncf_np": (r["NCO_NETPROFIT"] * 100) if isinstance(r.get("NCO_NETPROFIT"), (int, float)) else None})
    out.sort(key=lambda x: x["label"])
    return {"ml_trend": sorted(ml.items()), "latest": out[-1] if out else {}}


# ============ ③ 资产负债表先行信号 / 股东筹码 / 主营构成 ============

BAL_FIELDS = (("CONTRACT_LIAB", "合同负债"), ("CONTRACT_ASSET", "合同资产"),
              ("INVENTORY", "存货"), ("MONETARYFUNDS", "货币资金"),
              ("SHORT_LOAN", "短期借款"), ("LONG_LOAN", "长期借款"),
              ("ACCOUNTS_RECE", "应收账款"), ("GOODWILL", "商誉"))


def balance_leads(code, n=5):
    """近 n 报告期先行信号表。有息负债=短借+长借。"""
    rows = _sec_rows("RPT_F10_FINANCE_GBALANCE", code)[:n * 2]
    out = []
    for r in rows:
        rd = (r.get("REPORT_DATE") or "")[:10]
        y, m = int(rd[:4]), int(rd[5:7]) if len(rd) >= 7 else (None, None)
        q = {3: 1, 6: 2, 9: 3, 12: 4}.get(m)
        if not q:
            continue
        debt = sum(v for v in (r.get("SHORT_LOAN"), r.get("LONG_LOAN")) if isinstance(v, (int, float)))
        out.append({"label": "%dQ%d" % (y, q),
                    **{cn: _yi(r.get(k)) for k, cn in BAL_FIELDS},
                    "有息负债": _yi(debt) if debt else 0.0})
        if len(out) >= n:
            break
    return out


INC_FIELDS = (("FAIRVALUE_CHANGE_INCOME", "公允价值变动收益"), ("INVEST_INCOME", "投资收益"),
              ("INVEST_JOINT_INCOME", "其中联营合营"), ("ASSET_DISPOSAL_INCOME", "资产处置收益"),
              ("NONBUSINESS_INCOME", "营业外收入"), ("NONBUSINESS_EXPENSE", "营业外支出"))


def income_leads(code, n=4):
    """利润表非经常主干(近n报告期): 归母vs扣非背离的拆解依据(公允价值/投资收益/处置/营业外)。
    逐项附注明细(政府补助细项等)仍需 PDF, 但主干足以判断'利润水分来源'量级。"""
    rows = _sec_rows("RPT_F10_FINANCE_GINCOME", code)[:n * 2]
    out = []
    for r in rows:
        rd = (r.get("REPORT_DATE") or "")[:10]
        if len(rd) < 7:
            continue
        y, m = int(rd[:4]), int(rd[5:7])
        q = {3: 1, 6: 2, 9: 3, 12: 4}.get(m)
        if not q:
            continue
        out.append({"label": "%dQ%d" % (y, q), **{cn: _yi(r.get(k)) for k, cn in INC_FIELDS}})
        if len(out) >= n:
            break
    return out


def cashflow_leads(code, n=4):
    """现金流量表先行(近n报告期, 累计口径): 经营净额/投资净额/筹资净额/购建支付(capex)/
    自由现金流≈经营净额-capex。真实FCF是盈利质量最硬的尺(比净现比更直观)。"""
    rows = _sec_rows("RPT_F10_FINANCE_GCASHFLOW", code)[:n * 2]
    out = []
    for r in rows:
        rd = (r.get("REPORT_DATE") or "")[:10]
        if len(rd) < 7:
            continue
        y, m = int(rd[:4]), int(rd[5:7])
        q = {3: 1, 6: 2, 9: 3, 12: 4}.get(m)
        if not q:
            continue
        op, capex = r.get("NETCASH_OPERATE"), r.get("INVEST_PAY_CASH")
        out.append({"label": "%dQ%d" % (y, q), "经营净额": _yi(op), "投资净额": _yi(r.get("NETCASH_INVEST")),
                    "筹资净额": _yi(r.get("NETCASH_FINANCE")), "capex": _yi(capex),
                    "fcf": _yi(op - capex) if isinstance(op, (int, float)) and isinstance(capex, (int, float)) else None})
        if len(out) >= n:
            break
    return out


def holders_shift(code, freeholder_rows=None):
    """十大流通股东最新两期 diff(新进/退出/增减) + 股东户数近5期趋势。
    freeholder_rows 可注入(build_pack 已取过则复用, 防同一接口重复请求)。"""
    fh = (freeholder_rows or _sec_rows("RPT_F10_EH_FREEHOLDERS", code))[:12]
    by_end = {}
    for r in fh:
        by_end.setdefault((r.get("END_DATE") or "")[:10], []).append(r)
    ends = sorted(by_end, reverse=True)[:2]
    diff = None
    if len(ends) == 2:
        cur = {r.get("HOLDER_NAME"): r for r in by_end[ends[0]]}
        prev = {r.get("HOLDER_NAME"): r for r in by_end[ends[1]]}
        diff = {"period": [ends[1], ends[0]], "new": [], "exit": [], "change": []}
        for nm, r in cur.items():
            if nm not in prev:
                diff["new"].append(nm)
            else:
                a, b = r.get("HOLD_NUM"), prev[nm].get("HOLD_NUM")
                if isinstance(a, (int, float)) and isinstance(b, (int, float)) and b > 0:
                    chg = (a / b - 1) * 100
                    if abs(chg) >= 15:
                        diff["change"].append("%s %+.0f%%" % (nm[:16], chg))
        diff["exit"] = [nm for nm in prev if nm not in cur]
    hn = _sec_rows("RPT_F10_EH_HOLDERNUM", code)[:6]
    trend = [( (r.get("END_DATE") or "")[:10], _f(r.get("HOLDER_TOTAL_NUM"), 1e4)) for r in hn]
    return {"top10_diff": diff, "holders_wan": trend[:5]}


def main_composition(code, thick_labels):
    """emweb 主营构成: 取最近厚报告期 + 上年同期(分业务/分地区, 收入占比与毛利率)。"""
    try:
        d = _http_json("https://emweb.securities.eastmoney.com/PC_HSF10/BusinessAnalysis/PageAjax?code="
                       + ("SH" if str(code)[0] in "69" else ("BJ" if str(code)[0] in "48" else "SZ")) + str(code).split(".")[0],
                       referer="https://emweb.securities.eastmoney.com/")
    except Exception as e:
        degrade("main_composition", str(e)[:60])
        return {}
    rows = d.get("zygcfx") or []
    want = set()
    for lbl in thick_labels:
        y = int(lbl[:4])
        want.add("%d-12-31" % y if lbl[-1] == "4" else "%d-06-30" % y)
        want.add("%d-12-31" % (y - 1) if lbl[-1] == "4" else "%d-06-30" % (y - 1))
    out = []
    for r in rows:
        rd = (r.get("REPORT_DATE") or "")[:10]
        if rd in want:
            gm = r.get("GROSS_RPOFIT_RATIO")
            out.append({"period": rd, "dim": {1: "按业务", 2: "按产品", 3: "按地区"}.get(r.get("MAINOP_TYPE"), str(r.get("MAINOP_TYPE"))),
                        "item": r.get("ITEM_NAME"), "rev_yi": _yi(r.get("MAIN_BUSINESS_INCOME")),
                        "share_pct": _f(r.get("MBI_RATIO"), 1), "gm_pct": _f(gm, 0.01) if isinstance(gm, (int, float)) and gm <= 1 else gm})
    if not out:
        have = sorted(set((r.get("REPORT_DATE") or "")[:10] for r in rows))[-3:]
        degrade("main_composition", "无匹配期(取最近: %s)" % ", ".join(have))
    return {"rows": out, "jyps": [((r.get("REPORT_DATE") or "")[:10], r.get("BUSINESS_REVIEW"))
                                  for r in (d.get("jyps") or [])]}


# ============ ④ 行情快照 / 价格位置 / 大盘β(全免费) ============

def quote(code):
    secid = ("1." if str(code)[0] in "69" else "0.") + str(code).split(".")[0]
    for attempt in (1, 2):   # push2 间歇断连实测 → 重试一次
        try:
            d = _http_json("https://push2.eastmoney.com/api/qt/ulist.np/get?"
                           + urllib.parse.urlencode({"secids": secid, "fields": "f12,f14,f2,f3,f9,f20,f21",
                                                     "ut": "fa5fd1943c7b386f172d6893dbfba10b"}))
            it = ((d.get("data") or {}).get("diff") or [{}])[0]
            return {"name": it.get("f14"), "price": (it.get("f2") / 100) if isinstance(it.get("f2"), (int, float)) else None,
                    "mcap_yi": _yi(it.get("f20")), "pe": (it.get("f9") / 100) if isinstance(it.get("f9"), (int, float)) else None}
        except Exception as e:
            if attempt == 1:
                time.sleep(1)
                continue
            degrade("quote", str(e)[:60])
            return {}


def price_metrics(code):
    """腾讯日K(约4年) → runner 所需 m(series_metrics) + 3年低点倍数 + 年内涨幅。"""
    from runner import to_symbol
    sym = to_symbol(str(code).split(".")[0] + (".SH" if str(code)[0] in "69" else ".SZ"))
    end = date.today().isoformat()
    beg = "%d-01-01" % (date.today().year - 4)
    rows = kline.fetch_qfq(sym, beg, end)
    if not rows:
        degrade("price_metrics", "K线无数据")
        return {}, []
    m = kline.series_metrics(rows, window=250)
    closes = [(r[0], float(r[2])) for r in rows]
    if closes:
        last = closes[-1][1]
        three_y = "%d-01-01" % (date.today().year - 3)
        lows = [c for d, c in closes if d >= three_y]
        base = next((c for d, c in closes if d >= "%d-01-01" % date.today().year), None)
        m["realized_3y"] = round(last / min(lows), 2) if lows else None
        m["ytd_pct"] = round((last / base - 1) * 100, 1) if base else None
    return m, closes[-5:]


def market_gate_free():
    """大盘β(免费版, 2026-11-07 移入 monitor.py 供 watch/track 共用)。"""
    from monitor import market_gate_free as _mgf
    return _mgf()


# ============ ⑤ 报告正文(两厚两薄 PDF → 章节) ============

def _cninfo_cli():
    for p in CNINFO_CLI_CANDIDATES:
        if os.path.exists(p):
            return p
    return None


TYPE_BY_Q = {1: "q1", 2: "semi", 3: "q3", 4: "annual"}


def fetch_report_texts(code, name, reports, outdir, pdf_dir=None):
    """经 cninfo-disclosure 底座下载厚报告 PDF → pdftotext → 切章节。
    PDF **归档不删除**(沉淀到 pdf_dir/{名称}_{代码}/); 已存在同名报告跳过下载(幂等缓存)。"""
    cli = _cninfo_cli()
    if not cli:
        degrade("fetch_report_texts", "cninfo-disclosure cli 未找到(探测: %s)" % "; ".join(CNINFO_CLI_CANDIDATES[:2]))
        return []
    archive = os.path.join(pdf_dir or DEFAULT_PDF_LIB, "%s_%s" % (name, str(code).split(".")[0]))
    os.makedirs(archive, exist_ok=True)
    out = []
    for rp in reports:
        # 只下厚报告(年报/半年报): PDF 只为 MD&A 叙述层(募投/风险/展望)服务;
        # 薄报告(季报)的主要数据/股东信息/报表全走 API, 不再下载(2026-11-05 定版)
        if not rp.get("has_data") or not rp.get("thick"):
            continue
        # 归档命中则免下载(幂等: 二次判定同标的零下载零限速)
        key = "半年度报告" if rp["q"] == 2 else "年度报告"
        hit = [f for f in os.listdir(archive) if key in f and str(rp["y"]) in f and f.lower().endswith(".pdf")]
        path = os.path.join(archive, hit[0]) if hit else None
        if not path:
            ok = None
            for attempt in (1, 2):    # 巨潮限速间歇拦截 → 重试一次
                try:
                    before = set(os.listdir(archive))
                    # 坑(实测): cli 把 -o 当相对路径拼自身 cwd —— 可靠组合 = cwd=归档目录 + -o .
                    p = subprocess.run([sys.executable, cli, "download", "--stock", str(code).split(".")[0],
                                        "--year", str(rp["y"]), "--type", TYPE_BY_Q[rp["q"]],
                                        "-o", ".", "--timeout", "30"],
                                       capture_output=True, timeout=180, cwd=archive)
                    new = sorted((f for f in set(os.listdir(archive)) - before if f.lower().endswith(".pdf")),
                                 key=lambda f: -os.path.getmtime(os.path.join(archive, f)))
                    if not new:
                        if attempt == 1:
                            time.sleep(3)
                            continue
                        degrade("fetch_report_texts", "%s 下载失败: %s | %s"
                                % (rp["cn"], (p.stderr or b"").decode("utf-8", "ignore")[:60],
                                   (p.stdout or b"").decode("utf-8", "ignore").replace(chr(10), " ")[:100]))
                        break
                    ok = os.path.join(archive, new[0])
                    break
                except Exception as e:
                    if attempt == 2:
                        degrade("fetch_report_texts", "%s: %s" % (rp["cn"], str(e)[:70]))
            time.sleep(1.0)
            path = ok
        if not path:
            continue
        txt = _pdf_text(path)
        if not txt:
            degrade("fetch_report_texts", "%s 转文本失败(PDF 已归档保留: %s)" % (rp["cn"], path))
            continue
        out.append({"label": rp["label"], "cn": rp["cn"], "thick": rp["thick"],
                    "pdf": path, "chapters": _split_chapters(txt, thick=True)})
    return out


def _pdf_text(path, max_pages=120):
    try:
        t = path + ".txt"
        p = subprocess.run(["pdftotext", "-layout", "-l", str(max_pages), path, t],
                           capture_output=True, timeout=120)
        if p.returncode == 0 and os.path.exists(t) and os.path.getsize(t) > 500:
            return open(t, encoding="utf-8", errors="ignore").read()
    except Exception:
        pass
    finally:
        try:
            os.remove(path + ".txt")
        except Exception:
            pass
    return None


_SEC_RE = re.compile(r"第[一二三四五六七八九十]+节")
_CHAPTER_MARKS = {
    "mda": re.compile(r"管理层讨论与分析|经营情况讨论与分析"),
    "risk": re.compile(r"可能面对的风险|可能面临的风险|风险因素及对策"),
    "nonrec": re.compile(r"非经常性损益项目及金额|非经常性损益"),
    "holder": re.compile(r"股份变动及股东情况|股东信息|股东情况"),
}


def _slice(text, mark, cap, min_len=400):
    """切章节。年报目录页也有章节标题(后跟 .... 页码) —— 命中切片过短时跳过找下一个
    匹配位; 全部命中都短(如天然短小的股东章节)时回退【最长】命中而非第一个
    (实测: 药明年报 MD&A 首命中只有121字目录条目; 股东章节正文仅40字仍须取正文)。"""
    best = ""
    for m in mark.finditer(text):
        nxt = _SEC_RE.search(text, m.end())
        seg = text[m.start():nxt.start() if nxt else min(m.end() + cap * 3, len(text))]
        if len(seg) >= min_len:
            return seg[:cap]
        if len(seg) > len(best):
            best = seg
    return best[:cap]


def _split_chapters(txt, thick):
    """厚报告切 5 章; 薄报告全文截断(本就短) + 股东章。"""
    if not thick:
        holder = _slice(txt, _CHAPTER_MARKS["holder"], 2500)
        body = txt[:7000]
        return {"全文摘要": body, "股东章节": holder or "(未定位)"}
    mda = _slice(txt, _CHAPTER_MARKS["mda"], 12000)
    risk = _slice(txt, _CHAPTER_MARKS["risk"], 4000)
    nonrec = _slice(txt, _CHAPTER_MARKS["nonrec"], 2500)
    holder = _slice(txt, _CHAPTER_MARKS["holder"], 2500)
    # 募投: 全文定位(药明等公司放"重要事项"节而非 MD&A 内)
    m2 = re.search(r"募集资金使用情况|募集资金投向|募集资金的具体使用", txt)
    mipu = txt[m2.start():m2.start() + 5000] if m2 else ""
    return {"管理层讨论": mda or "(未定位)", "募投情况": mipu or "(未定位)",
            "风险章节": risk or "(未定位)", "非经常明细": nonrec or "(未定位)",
            "股东章节": holder or "(未定位)"}


def consensus(code, outdir):
    """券商一致预期(经 cninfo-disclosure 研报模块, 东财免费源): 近12月研报数/评级分布/
    最新盈利预测(EPS→预期PE)。jingshui 六段式第五段(机构一致预期与估值)的素材。"""
    import csv
    cli = _cninfo_cli()
    code6 = str(code).split(".")[0]
    if not cli:
        return {}
    try:
        p = subprocess.run([sys.executable, cli, "download", "--stock", code6,
                            "--type", "research", "-o", outdir, "--timeout", "30"],
                           capture_output=True, timeout=120)
        info = json.loads((p.stdout or b"").decode("utf-8", "ignore"))
    except Exception as e:
        degrade("consensus", str(e)[:60])
        return {}
    csv_path = info.get("csv") or (info.get("data") or {}).get("csv") if isinstance(info.get("data"), dict) else info.get("csv")
    if not csv_path or not os.path.exists(csv_path):
        degrade("consensus", "研报清单未生成")
        return {}
    try:
        rows = list(csv.DictReader(open(csv_path, encoding="utf-8-sig")))
    except Exception as e:
        degrade("consensus", "CSV解析: %s" % str(e)[:50])
        return {}
    if not rows:
        return {}
    def col(row, *names):
        for n in names:
            for k in row:
                if n in str(k):
                    return row.get(k)
        return None
    ratings = {}
    eps_recent = []
    for r in rows:
        rt = col(r, "评级") or ""
        for key in ("买入", "增持", "推荐", "中性", "持有", "卖出"):
            if key in rt:
                ratings[key] = ratings.get(key, 0) + 1
                break
        eps = col(r, "预测年每股收益", "预测EPS", "EPS")
        yr = col(r, "预测年份", "年度")
        if eps:
            try:
                eps_recent.append((str(yr or ""), float(eps)))
            except ValueError:
                pass
    out = {"reports_12m": len(rows),
           "orgs": len(set(col(r, "机构", "来源") for r in rows if col(r, "机构", "来源"))),
           "ratings": ratings,
           "eps_forecasts": eps_recent[-6:],
           "latest_date": max((str(col(r, "日期") or "") for r in rows), default="")}
    try:
        os.remove(csv_path)
    except Exception:
        pass
    return out


# ============ ⑥ 素材包渲染 ============

def build_pack(code, outdir, with_pdf=True, pdf_dir=None):
    """全量取数 → pack.json + pack.md。返回 (pack, outdir) 或 (None, errors)。"""
    os.makedirs(outdir, exist_ok=True)
    q = quote(code)
    code6 = str(code).split(".")[0]
    name = q.get("name")
    if not name:
        # 名称兜底: F10 公司简介的证券简称(防归档目录退化成 代码_代码; ORGINFO 不带 sort)
        rows = _sec_rows("RPT_F10_BASIC_ORGINFO", code6, sort=None)
        name = (rows[0] or {}).get("SECURITY_NAME_ABBR") if rows else None
    name = name or code6
    reports, all_periods = four_reports(code6)
    thick_labels = [r["label"] for r in reports if r["thick"]]

    qs, singles, periods = three_line_qs(code6)
    ana = analyze_quarterly(qs) if qs.get("latest_period") else None
    qt, qlabels = qoq_table(singles)
    qual = quality_rows(code6)
    bal = balance_leads(code6)
    fh_rows = _sec_rows("RPT_F10_EH_FREEHOLDERS", code6)[:30]   # 取一次, 两处共用
    hold = holders_shift(code6, freeholder_rows=fh_rows)
    comp = main_composition(code6, thick_labels)
    m, recent_closes = price_metrics(code6)
    gate = market_gate_free()
    fc = None
    try:
        pr = predicts(code6)
        if pr:
            fc = forecast_signal(periods, pr, date.today().isoformat())
    except Exception:
        pass

    # 阶段预判(runner.classify_phase, 纯函数)
    stage_pre = None
    if ana and m:
        from runner import classify_phase
        stage_pre = classify_phase(m, ana, gate.get("verdict"), forecast=fc)
    pdf_dir_resolved, pdf_src = resolve_pdf_dir(pdf_dir)
    texts = fetch_report_texts(code6, name, reports, outdir, pdf_dir=pdf_dir_resolved) if with_pdf else []
    inc = income_leads(code6)
    cf = cashflow_leads(code6)
    cons = consensus(code6, outdir)

    # ── 跨期趋势挖掘(确定性记分卡, 2026-11-05 用户定版: 单期是体检, 跨期才是趋势) ──
    jyps_list = comp.get("jyps") or []
    guidance = trend.extract_guidance(texts, jyps_list)
    gv = trend.guidance_verdict(guidance, "收入") or {}
    gv2 = trend.guidance_verdict(guidance, "净利") or {}
    holders_tl = trend.holders_timeline(fh_rows)
    kw = trend.keyword_matrix(texts, jyps_list)
    leads = trend.leads_verdict(bal, inc, cf, qs.get("periods") or [])
    card = trend.scorecard(gv, kw, holders_tl, leads, ana)

    pack = {"code": code6, "name": name, "asof": date.today().isoformat(),
            "quote": q, "reports": [{k: r[k] for k in ("label", "cn", "thick", "has_data")} for r in reports],
            "qs": qs, "analysis": ana, "quarterly_table": qt,
            "quality": qual, "balance_leads": bal, "income_leads": inc, "cashflow": cf, "holders": hold,
            "composition": comp, "price": m, "market_gate": gate,
            "forecast": fc, "stage_pre": stage_pre, "consensus": cons,
            "trend": {"guidance": guidance, "guidance_rev": gv, "guidance_np": gv2,
                      "holders_tl": holders_tl, "keywords": kw, "leads": leads,
                      "scorecard": card, "pdf_dir": [pdf_dir_resolved, pdf_src]},
            "report_texts_meta": [{k: t[k] for k in ("label", "cn", "thick")} for t in texts],
            "degraded": DEGRADED}
    json.dump(pack, open(os.path.join(outdir, "pack.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    md = _render_pack_md(pack, texts)
    open(os.path.join(outdir, "pack.md"), "w", encoding="utf-8").write(md)
    with open(os.path.join(outdir, "texts.json"), "w", encoding="utf-8") as f:
        json.dump(texts, f, ensure_ascii=False, indent=1)
    return pack, outdir


def _tbl(headers, rows):
    return ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)] + \
           ["| " + " | ".join(str(c) if c is not None else "—" for c in r) + " |" for r in rows]


def _fmt_yoy(v):
    return ("%+.1f%%" % v) if isinstance(v, (int, float)) else "扭亏/NA"


def _render_pack_md(pack, texts):
    p = pack
    L = ["# 判定素材包 · %s(%s) · %s" % (p["name"], p["code"], p["asof"]), ""]
    q = p.get("quote") or {}
    m = p.get("price") or {}
    g = p.get("market_gate") or {}
    L += ["> 现价 %s · 总市值 %s亿 · PE(动) %s · 距一年高点 %.0f%% · 3年低点 %.2f倍 · 年内 %+.1f%% · 大盘β %s(%s)"
          % (q.get("price") or "—", q.get("mcap_yi") or "—", q.get("pe") or "—",
             ((m.get("drawdown") or 0) * 100 if m.get("drawdown") is not None else None) or 0,
             m.get("realized_3y") or 0, m.get("ytd_pct") or 0,
             g.get("verdict", "—"), g.get("method", "")), ""]

    L += ["## 一、三线单季序列(近4季, F10自算)", ""]
    qt = p.get("quarterly_table") or []
    rows = []
    for t in qt:
        sy = t["single_yi"]
        qq = t["qoq"]
        rows.append([t["label"],
                     "%s/%s/%s" % tuple(("%.1f" % v) if v is not None else "—" for v in sy),
                     "%s/%s/%s" % tuple(_fmt_yoy(v).replace("%", "") for v in (p["qs"]["rev_yoy"].get(t["label"]), p["qs"]["parent_yoy"].get(t["label"]), p["qs"]["deduct_yoy"].get(t["label"]))),
                     "%s/%s/%s" % tuple(("%+.0f" % v) if v is not None else "—" for v in qq)])
    L += _tbl(["季", "单季亿(营/归/扣)", "同比%", "环比%"], rows) + [""]
    ana = p.get("analysis")
    if ana:
        L += ["- 过线: %s(%d/3) · 最长连续 %d 季 · 形态 %s · 加速 %s" % (
            "/".join(ana["pass_lines"]["labels"]) or "无", ana["pass_lines"]["count"],
            ana["streak_max"], ana["resonance"], ana["rev_accel"]),
              "- 降档: %s · 扣非背离: %s" % (ana["downshift"], ana["divergence"]["flag"]), ""]
    sp = p.get("stage_pre")
    if sp:
        L += ["**阶段预判(纯算法, 供参考)**: %s —— %s" % (sp[0], sp[1]), ""]
    fc = p.get("forecast")
    if fc:
        L += ["**业绩预告**: %s" % json.dumps(fc, ensure_ascii=False)[:200], ""]

    qual = p.get("quality") or {}
    if qual:
        L += ["## 二、质地", ""]
        ml = qual.get("ml_trend") or []
        if ml:
            L += ["- 毛利率(累计): " + " → ".join("%s %.1f%%" % (l, v) for l, v in ml[-6:])]
        lt = qual.get("latest") or {}
        L += ["- ROE %s · ROIC %s · 负债率 %s · 净现比 %s%%" % (
            lt.get("roe"), lt.get("roic"), lt.get("debt"), lt.get("ncf_np")), ""]

    bal = p.get("balance_leads") or []
    if bal:
        L += ["## 三、资产负债表先行信号(亿, 早期→近)", ""]
        keys = ["合同负债", "存货", "应收账款", "货币资金", "有息负债", "商誉"]
        rows = [[b["label"]] + [b.get(k) for k in keys] for b in reversed(bal)]
        L += _tbl(["期"] + keys, rows) + [""]
    inc = p.get("income_leads") or []
    if inc:
        L += ["## 三B、非经常主干(利润表, 背离拆解依据, 亿)", ""]
        ikeys = ["公允价值变动收益", "投资收益", "其中联营合营", "资产处置收益", "营业外收入", "营业外支出"]
        irows = [[b["label"]] + [b.get(k) for k in ikeys] for b in reversed(inc)]
        L += _tbl(["期"] + ikeys, irows) + [""]
    cf = p.get("cashflow") or []
    if cf:
        L += ["## 三C、现金流先行(累计口径, 亿)", ""]
        ckeys = ["经营净额", "投资净额", "筹资净额", "capex", "fcf(经营-capex)"]
        crows = [[b["label"]] + [b.get(k) for k in ("经营净额", "投资净额", "筹资净额", "capex", "fcf")] for b in reversed(cf)]
        L += _tbl(["期"] + ckeys, crows) + [""]

    hold = p.get("holders") or {}
    d = hold.get("top10_diff")
    if d:
        L += ["## 四、股东筹码", "",
              "- 十大流通股东(%s → %s): 新进 %s | 退出 %s" % (d["period"][0], d["period"][1],
                  "、".join(d["new"]) or "无", "、".join(d["exit"]) or "无")]
        if d["change"]:
            L += ["- 变动≥15%: " + "; ".join(d["change"])]
    hw = hold.get("holders_wan") or []
    if hw:
        L += ["- 股东户数(万户, 新→旧): " + " → ".join("%.2f" % v for _, v in hw if v), ""]

    comp = p.get("composition") or {}
    if comp.get("rows"):
        L += ["## 五、主营构成(最近厚报告期 vs 上年同期)", ""]
        rows = [[c["period"][:4] + ("中报" if c["period"][5:7] == "06" else "年报"), c["dim"], (c["item"] or "")[:22],
                 c["rev_yi"], (("%.1f%%" % c["share_pct"]) if c["share_pct"] is not None else "—"),
                 (("%.1f%%" % c["gm_pct"]) if isinstance(c["gm_pct"], (int, float)) else "—")]
                for c in comp["rows"] if c["dim"] in ("按业务", "按地区")]
        L += _tbl(["期", "维度", "构成", "收入亿", "占比", "毛利率"], rows[:24]) + [""]
    for rd, rv in (comp.get("jyps") or [])[:1]:
        if rv:
            L += ["## 六、经营评述(%s, API)" % rd, "", rv[:2600], ""]
    cons = p.get("consensus") or {}
    if cons.get("reports_12m"):
        L += ["## 六B、券商一致预期(近12月研报)", "",
              "- 研报 %s 篇 / 覆盖机构 %s 家 / 最近 %s" % (cons.get("reports_12m"), cons.get("orgs"), cons.get("latest_date")),
              "- 评级分布: %s" % (json.dumps(cons.get("ratings") or {}, ensure_ascii=False)),
              "- 盈利预测(EPS): %s" % (cons.get("eps_forecasts") or "无数据"), ""]

    if texts:
        L += ["## 七、报告章节原文(两厚两薄, 判定素材)", ""]
        for t in texts:
            L += ["### %s(%s)%s" % (t["cn"], t["label"], " 厚" if t["thick"] else " 薄"), ""]
            for k, v in t["chapters"].items():
                if v and v != "(未定位)":
                    vv = v[:3000] if k != "管理层讨论" else v[:6000]
                    L += ["**[%s]**" % k, "", vv, ""]
    if p.get("degraded"):
        L += ["## 降级项", ""] + ["- `%s` %s" % (f, w) for f, w in p["degraded"]] + [""]

    # ── 八、跨期趋势记分卡(确定性代码产出, 判定必须逐条回应 —— 用户定版的确保机制) ──
    tr = p.get("trend") or {}
    card = tr.get("scorecard") or {}
    if card:
        L += ["## 八、跨期趋势记分卡(确定性代码产出, 判定必须逐条回应)", "",
              "**综合: %s (记分 %d)**" % (card.get("verdict"), card.get("score")), "",
              "| 信号 | 方向 | 证据 |", "|---|---|---|"]
        for r in card.get("rows") or []:
            L.append("| %s | %s | %s |" % (r.get("信号"), r.get("方向"), r.get("证据")))
        L += [""]
        gtl = (tr.get("guidance_rev") or {}).get("steps") or []
        if gtl:
            L += ["**收入指引时间线**: " + " → ".join(gtl), ""]
        gtl2 = (tr.get("guidance_np") or {}).get("steps") or []
        if gtl2:
            L += ["**净利指引时间线**: " + " → ".join(gtl2), ""]
        htl = (tr.get("holders_tl") or {}).get("timeline") or []
        if htl:
            rows_h = []
            for t in reversed(htl):
                ids = t.get("by_id") or {}
                rows_h.append([t["period"], "%s家" % t.get("n_inst"),
                               "; ".join("%s×%d" % (k, len(v)) for k, v in ids.items()) or "无机构"])
            L += ["**十大流通股东身份时间线**(旧→新):", ""] + _tbl(["期", "机构家数", "身份构成"], rows_h) + [""]
    kwm = tr.get("keywords") or {}
    if kwm:
        L += ["**表述漂移**: %s | 上升词: %s | 风险词: %s" % (kwm.get("trend"),
              "; ".join("%s×%s" % (k, sum(v.values())) for k, v in (kwm.get("up") or {}).items()) or "无",
              "; ".join("%s×%s" % (k, sum(v.values())) for k, v in (kwm.get("risk") or {}).items()) or "无"), ""]
    pdir = (tr.get("pdf_dir") or ["", ""])
    L += ["*PDF 归档: %s (%s)*" % (pdir[0], pdir[1]), ""]

    L += ["## 九、判定任务(填 verdict.json)", "",
          "读上方素材 + jingshui(核心因子7条/5不买) + can-slim(七要素) + lynch(六分类)标准, 写同目录 verdict.json:",
          "```json",
          json.dumps({
              "code": p["code"], "name": p["name"],
              "is_tenbagger_candidate": True, "confidence": "高/中/低",
              "stage": "主升期/强势待确认/回调击球区/深调考验区/退潮/利润未兑现/预告爆发",
              "stage_reason": "≤60字, 需引用素材中的证据",
              "jingshui_core7": [{"no": 1, "item": "营收利润增速≥40%", "verdict": "✓/✗/部分", "evidence": "≤40字"}],
              "jingshui_5no": [{"no": 1, "item": "亏损公司?", "verdict": "✗/✓", "evidence": ""}],
              "canslim": {"C": "✓/✗/部分:证据", "A": "", "N": "", "S": "", "L": "", "I": "", "M": ""},
              "lynch_class": "快速增长/稳定增长/缓慢增长/周期/困境反转/隐蔽资产",
              "lynch_reason": "≤40字",
              "trend_signals": {"guidance": "连续上调/上调/维持/下调/无数据",
                                "national_team": "增强/在场/退出/无数据",
                                "narrative": "≤40字, 对比两期MD&A措辞变化(新出现/消失了什么)",
                                "leads": "≤40字, 先行指标综合方向"},
              "tps_qualitative": {"F1行业空间": "≤60字", "F5催化": "", "F6生意模式": ""},
              "pdf_summary": {"2025年报": "≤150字: 行业景气表述/经营要点增量/募投进展/主要风险/非经常",
                              "2026半年报": "同上(精读第七节该报告章节原文后总结)",
                              "跨期对比": "两厚报告对读≤150字: 景气措辞/经营要点/募投节奏/风险条目 的变化方向"},
              "risks": ["≤3条, 每条注明看什么指标跟踪"],
              "watch_next": ["≤3个可跟踪变量"],
              "conclusion": "过/不过/部分通过(差哪几条)"
          }, ensure_ascii=False, indent=1), "```", "",
          "**pdf_summary(2026-11-05 新增)**: 键=第七节两厚报告的 cn 标签 +『跨期对比』; "
          "每份厚报告总结一段(行业景气措辞/经营要点较上期增量/募投投产节奏/风险章节提示/非经常); "
          "『跨期对比』= 两厚**对读**找变化趋势: 什么表述新出现/消失了、景气定调升降、"
          "募投与产能节奏快慢、风险条目增减——与第八节 C表述漂移/叙述漂移互相印证。", ""]
    return "\n".join(L)


# ============ ⑦ verdict 校验 + 报告组装 ============

STAGES = ("主升期", "强势待确认", "回调击球区", "深调考验区", "退潮", "利润未兑现", "预告爆发")
CONF = ("高", "中", "低")
CORE7 = ["营收利润增速≥40%", "新产品/新服务/新市场量价齐升", "管理层对产业趋势强烈认同",
         "当下时代核心产业趋势中的超级α", "逻辑无法证伪、越推演越明朗", "符合机构选股偏好",
         "政策大力扶持的第一核心行业"]
NO5 = ["亏损公司?", "主营增速<40%?", "未创一年新高?", "无新产品/新市场?", "无强供求矛盾?"]
CS_KEYS = ("C", "A", "N", "S", "L", "I", "M")
LYNCH = ("快速增长", "稳定增长", "缓慢增长", "周期", "困境反转", "隐蔽资产")


def load_verdict(path):
    """读 verdict.json -> (verdict, 校验错误列表)。缺字段给出确定性占位, 不阻塞组装。"""
    errs = []
    try:
        v = json.load(open(path, encoding="utf-8"))
    except Exception as e:
        return None, ["verdict.json 解析失败: %s" % e]
    if v.get("stage") not in STAGES:
        errs.append("stage 非法或缺: %r" % v.get("stage"))
        v["stage"] = "强势待确认"
    if v.get("confidence") not in CONF:
        v.setdefault("confidence", "低")
    v.setdefault("is_tenbagger_candidate", False)
    c7 = v.get("jingshui_core7")
    if not isinstance(c7, list) or len(c7) != 7:
        errs.append("jingshui_core7 应为7条(现%d条) —— 已按缺省补齐" % (len(c7) if isinstance(c7, list) else 0))
        v["jingshui_core7"] = [{"no": i + 1, "item": it, "verdict": "部分", "evidence": "未判定"}
                               for i, it in enumerate(CORE7)]
    n5 = v.get("jingshui_5no")
    if not isinstance(n5, list) or len(n5) != 5:
        v["jingshui_5no"] = [{"no": i + 1, "item": it, "verdict": "未判定", "evidence": ""}
                             for i, it in enumerate(NO5)]
        errs.append("jingshui_5no 应为5条 —— 已按缺省补齐")
    cs = v.get("canslim") or {}
    v["canslim"] = {k: cs.get(k) or "未判定" for k in CS_KEYS}
    if v.get("lynch_class") not in LYNCH:
        v["lynch_class"] = v.get("lynch_class") or "未判定"
        errs.append("lynch_class 非法 —— 标记未判定")
    for k in ("stage_reason", "lynch_reason", "conclusion"):
        v.setdefault(k, "")
    v.setdefault("pdf_summary", {})          # 两厚报告精要(2026-11-05 新增, 缺省不阻塞)
    ts = v.get("trend_signals") or {}
    v["trend_signals"] = {k: ts.get(k) or "未判定" for k in ("guidance", "national_team", "narrative", "leads")}
    v.setdefault("tps_qualitative", {})
    v.setdefault("risks", [])
    v.setdefault("watch_next", [])
    return v, errs


def render_report(pack, verdict, errs):
    p = pack
    L = ["# %s(%s) 十倍股判定报告" % (p.get("name"), p.get("code")), "",
         "> 判定日 %s · 素材: F10八季三线/资产负债表/股东/主营构成/两厚两薄报告章节/价格与大盘β" % p.get("asof"),
         "> 方法论: 静水核心因子+5不买(主判) · CAN SLIM 七要素 · 林奇六分类 · 判定引擎 tenscreener judge",
         "> 声明: 非投资建议, 仅供参考学习。", ""]
    cand = "是" if verdict.get("is_tenbagger_candidate") else "否"
    L += ["## 结论",
          "",
          "- **是否十倍股候选: %s**(置信 %s)" % (cand, verdict.get("confidence")),
          "- **所处阶段: %s** —— %s" % (verdict.get("stage"), verdict.get("stage_reason")),
          "- **阶段预判(纯算法对照)**: %s" % (pack.get("stage_pre") or ("—",))[0],
          "- **清单结论: %s**" % verdict.get("conclusion", "—"),
          "- **林奇分类: %s** —— %s" % (verdict.get("lynch_class"), verdict.get("lynch_reason")), ""]
    qt = p.get("quarterly_table") or []
    qs = p.get("qs") or {}
    if qt:
        L += ["", "## 近4季财务三线(单季, F10自算)", "",
              "| 季 | 营收(亿) | 同比% | 环比% | 归母(亿) | 同比% | 环比% | 扣非(亿) | 同比% | 环比% |",
              "|---|---|---|---|---|---|---|---|---|---|"]

        def _n(v):
            return ("%.1f" % v) if isinstance(v, (int, float)) else "—"

        def _y(v):
            return _fmt_yoy(v).replace("%", "")   # None → 扭亏/NA

        def _q(v):
            return ("%+.1f" % v) if isinstance(v, (int, float)) else "—"

        for t in qt:
            sy, qq = t.get("single_yi") or (None,) * 3, t.get("qoq") or (None,) * 3
            yy = [(qs.get(k) or {}).get(t["label"]) for k in ("rev_yoy", "parent_yoy", "deduct_yoy")]
            L.append("| %s | %s | %s | %s | %s | %s | %s | %s | %s | %s |" % (
                t["label"], _n(sy[0]), _y(yy[0]), _q(qq[0]),
                _n(sy[1]), _y(yy[1]), _q(qq[1]), _n(sy[2]), _y(yy[2]), _q(qq[2])))
        ana = p.get("analysis")
        if ana:
            L += ["", "- 三线过线: %s(%d/3) · 最长连续 %d 季 · 形态 %s · 加速 %s · 扣非背离: %s" % (
                "/".join(ana["pass_lines"]["labels"]) or "无", ana["pass_lines"]["count"],
                ana["streak_max"], ana["resonance"], ana["rev_accel"], ana["divergence"]["flag"])]
    L += ["## 静水核心因子 7 条", "", "| # | 因子 | 判定 | 依据 |", "|---|---|---|---|"]
    for it in verdict.get("jingshui_core7", []):
        L.append("| %s | %s | %s | %s |" % (it.get("no"), it.get("item"), it.get("verdict"), it.get("evidence", "")))
    L += ["", "## 静水 5 不买(一票否决)", "", "| # | 项 | 判定 |", "|---|---|---|"]
    for it in verdict.get("jingshui_5no", []):
        L.append("| %s | %s | %s |" % (it.get("no"), it.get("item"), it.get("verdict")))
    L += ["", "## CAN SLIM 七要素", ""]
    for k in CS_KEYS:
        L.append("- **%s** %s" % (k, verdict["canslim"].get(k)))
    tq = verdict.get("tps_qualitative") or {}
    if tq:
        L += ["", "## TPS 定性因子(Claude 判)", ""]
        for k, vv in tq.items():
            if vv:
                L.append("- **%s**: %s" % (k, vv))
    tsig = verdict.get("trend_signals") or {}
    if tsig:
        L += ["", "## 跨期趋势判定",
              "",
              "- **指引漂移**: %s" % tsig.get("guidance"),
              "- **国家队/股东身份**: %s" % tsig.get("national_team"),
              "- **叙述漂移(两期MD&A对比)**: %s" % tsig.get("narrative"),
              "- **先行指标方向**: %s" % tsig.get("leads")]
    card = ((pack.get("trend") or {}).get("scorecard") or {})
    if card.get("rows"):
        L += ["", "**确定性记分卡: %s (记分 %d)**" % (card.get("verdict"), card.get("score")), "",
              "| 信号 | 方向 | 证据 |", "|---|---|---|"]
        for r in card["rows"]:
            L.append("| %s | %s | %s |" % (r.get("信号"), r.get("方向"), r.get("证据")))
    ps = verdict.get("pdf_summary") or {}
    if any(ps.values()):
        L += ["", "## 两厚报告精要(年报/半年报 PDF 总结)"]
        for k, s in ps.items():
            if s:
                L += ["", "- **%s**: %s" % (k, s)]
    if verdict.get("risks"):
        L += ["", "## 风险与跟踪"] + ["- %s" % r for r in verdict["risks"]]
    if verdict.get("watch_next"):
        L += ["", "## 下一步盯什么"] + ["- %s" % w for w in verdict["watch_next"]]
    if errs:
        L += ["", "> ⚠ verdict 校验: " + "; ".join(errs)]
    L += ["", "---", "*非投资建议 · tenscreener judge · %s*" % p.get("asof")]
    return "\n".join(L)


def finalize(code):
    """读 pack.json + verdict.json → report.md。-> (ok, path|None)"""
    code6 = str(code).split(".")[0]
    base = _run_dir(code6)
    if not base or not os.path.exists(os.path.join(base, "pack.json")):
        print("未找到 %s 的素材包, 先跑 judge --code" % code6)
        return False, None
    pack = json.load(open(os.path.join(base, "pack.json"), encoding="utf-8"))
    v, errs = load_verdict(os.path.join(base, "verdict.json"))
    if v is None:
        return False, None
    md = render_report(pack, v, errs)
    path = os.path.join(base, "report.md")
    open(path, "w", encoding="utf-8").write(md)
    return True, path
