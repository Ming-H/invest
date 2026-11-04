#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""framework.py — earnings-reader L2 框架筛选层(纯计算, jingshui × CAN SLIM)。

移植自 h1-monitor/scripts/framework_filter.py(方案 framework-L2-方案-v2.1)。
唯一结构性改动: 删除 llm_judge_batch(headless GLM 子进程调用层) —— 枚举判定
由对话内 Claude 产出 judge_output.json, pipeline 校验后以 llm_results 传入。
取数函数(L1.5)移至 data.py。

三层判定:
  硬闸 H1晚期/H2假引擎(低基数+纯涨价+并表)/H3体量>=1000亿 -> 剔除
  五维评分 引擎30/空间25/阶段25/质地10/确认10 (python算数字维, Claude只出枚举)
  S级五维下限门 引擎>=20/空间>=16/阶段>=18/质地>=6/确认>=5
"""
import json
import os

# ---------------- 常量(方案 v2.1 §4/§5; 回测校准记录保留) ----------------
H3_MKTCAP_YI = 1000.0        # 体量硬闸: 总市值>=1000亿 -> 剔除
SIZE_BIG_YI = 200.0          # 200-1000亿 = 大
H2_LOWBASE_YOY = 300.0       # 低基数: 归母同比>300% 且 当季归母<0.5亿 且 ROE<5%
H2_LOWBASE_NP_YI = 0.5
H2_LOWBASE_ROE = 5.0
MOM_QOQ_ANCHOR = 15.0        # 环比证据锚: 归母当季环比<15%
LOSS_CUTOVER_QOQ = 50.0      # 扭亏类环比>50% -> 加速
S_LINE, A_LINE, B_LINE = 80.0, 75.0, 50.0  # A线=75: v2.2§13.6 校准(回测65->11只过宽, 75->5只)
S_CAP_PER_SEASON = 5

S_GATE = {"engine": 20, "space": 16, "stage": 18, "quality": 6, "confirm": 5}

DRIVE_ENUM = ["新品放量", "出海新市场", "量价齐升", "国产替代", "涨价周期", "供需趋紧", "其他"]
DRIVE_SCORE = {"新品放量": 12, "出海新市场": 12, "量价齐升": 10, "国产替代": 10,
               "供需趋紧": 8, "涨价周期": 4, "其他": 6}
ALPHA_ENUM = ["核心环节", "一般配套", "边缘"]
ALPHA_SCORE = {"核心环节": 8, "一般配套": 4, "边缘": 2}
STAGE_CALL_ENUM = ["早期", "中期", "晚期", "无意见"]

# ---------------- 纯逻辑: 动能判定 (方案 §4.1) ----------------

def _is_accel_series(yoy):
    """单调非降 或 当季为可算值中最大 -> True。yoy 含 None(基数<=0)。"""
    if not yoy or yoy[-1] is None:
        return False
    vals = [v for v in yoy if v is not None]
    monotonic = True
    for i in range(len(yoy) - 1):
        a, b = yoy[i], yoy[i + 1]
        if a is not None and b is not None and b < a - 1e-9:
            monotonic = False
            break
    return monotonic or yoy[-1] >= max(vals) - 1e-9


def _not_max(yoy):
    """同比证据: 当季可算 且 存在更早的更高值。"""
    if not yoy or yoy[-1] is None:
        return False
    return any(v is not None and v > yoy[-1] + 1e-9 for v in yoy[:-1])


def _qoq_down_two(qoq):
    """环比证据之二: 环比连续两季下行(需至少3个可算值)。"""
    vals = [v for v in qoq if v is not None]
    return len(vals) >= 3 and vals[-1] < vals[-2] < vals[-3]


def classify_momentum(rev_series, np_series):
    """-> (动能, 说明)。rev/np_series: [{val,yoy,qoq}x4], 字段可 None。
    顺序: 扭亏通道 -> 加速(含失配检查) -> 衰减(双条件) -> 高位持平/待确认。"""
    rev_yoy = [q.get("yoy") for q in rev_series]
    np_yoy = [q.get("yoy") for q in np_series]
    np_qoq = [q.get("qoq") for q in np_series]
    cur_n = np_yoy[-1]
    cur_rq, cur_nq = rev_series[-1].get("qoq"), np_series[-1].get("qoq")

    # 扭亏通道: 归母同比不可算(基数<=0)
    if cur_n is None:
        if cur_nq is not None and cur_nq > LOSS_CUTOVER_QOQ:
            return "加速", "扭亏+当季环比>50%"
        if cur_nq is not None and cur_nq > 0:
            return "高位持平", "扭亏+环比为正但未达锚"
        return "待确认", "扭亏+环比不可算"

    # 加速: 任一指标 单调升或当季最高, 且无失配(当季环比近乎停滞、同比高纯属基数残影)
    accel = _is_accel_series(rev_yoy) or _is_accel_series(np_yoy)
    mismatch = (cur_rq is not None and cur_rq < 5
                and cur_nq is not None and cur_nq < 10)
    if accel and not mismatch:
        return "加速", "同比单调升/当季最高且环比匹配"

    # 衰减: 同比证据(可算指标当季均非最高) AND 环比证据(归母环<15% 或 连续2季降) —— 双条件
    yoy_sides = [s for s in (rev_yoy, np_yoy) if s[-1] is not None]
    yoy_evid = bool(yoy_sides) and all(_not_max(s) for s in yoy_sides)
    qoq_evid = (cur_nq is not None
                and (cur_nq < MOM_QOQ_ANCHOR or _qoq_down_two(np_qoq)))
    if yoy_evid and qoq_evid:
        return "衰减", "当季同比非最高且环比动能弱"

    # 高位持平: 近两季可算同比均>40
    def last_two_gt40(yoy):
        pair = [v for v in yoy[-2:] if v is not None]
        return len(pair) >= 1 and all(v > 40 for v in pair)
    if last_two_gt40(rev_yoy) or last_two_gt40(np_yoy):
        return "高位持平", "同比均>40但当季非最高"
    return "待确认", "动能证据不足"


# ---------------- 纯逻辑: 兑现/体量/阶段 (§4.1-4.2) ----------------

def realized_bucket(realized_x):
    """近3年低点->现价倍数分档。None(数据缺失)按保守中档 3-5(7分), 防缺失变相加分。"""
    if realized_x is None:
        return "3-5", 7
    if realized_x < 3:
        return "<3", 9
    if realized_x < 5:
        return "3-5", 7
    if realized_x < 10:
        return "5-10", 4
    return ">=10", 0


def stage_cell(momentum, realized_x, mktcap_yi):
    """3x3 判定表 -> (stage, need_llm)。H3 体量闸在 gates 层独立触发。"""
    if mktcap_yi is not None and mktcap_yi >= H3_MKTCAP_YI:
        col = 3
    elif realized_x is not None and realized_x >= 10:
        col = 3
    elif (realized_x is None or realized_x < 3) and mktcap_yi is not None and mktcap_yi < SIZE_BIG_YI:
        col = 1
    else:
        col = 2
    if momentum == "加速":
        return ["早期启动", "中期加速", "中期·警惕"][col - 1], False
    if momentum == "高位持平":
        return ["早期·待确认", "中期", "晚期候选"][col - 1], col == 3
    # 衰减 / 待确认
    if col == 1:
        return "早期·待确认", False
    if col == 2:
        return "晚期候选", True
    return "晚期·出局", False


# ---------------- 纯逻辑: 五维评分 (§5) ----------------

def score_engine(card, drive_type):
    s = DRIVE_SCORE.get(drive_type, 6)
    rev_cur = card["quarterly"]["rev"][-1]
    np_cur = card["quarterly"]["np"][-1]
    dnp_cur = card["quarterly"]["dnp"][-1]
    if rev_cur.get("yoy") is not None and rev_cur["yoy"] >= 40:
        s += 6
    # 扣非匹配(单向): 扭亏通道记满分; 否则扣非>=40:4。仅"归母-扣非>100pp"(一次性抬轿方向)扣分;
    # 扣非>=归母(非经常为负贡献, 成色更好)不扣 —— 2026-10-28 南亚新材案例修正
    if np_cur.get("yoy") is None:
        s += 4
    elif (dnp_cur.get("yoy") is not None and dnp_cur["yoy"] >= 40
          and not ((np_cur.get("yoy") or 0) - dnp_cur["yoy"] > 100)):
        s += 4
    ncr = card.get("np_cash_ratio")
    if ncr is not None:
        if ncr >= 100:
            s += 4
        elif ncr >= 50:
            s += 2
        elif ncr >= 0:
            s += 1
    roe = card.get("roe")
    if roe is not None:
        if roe >= 17:
            s += 4
        elif roe >= 10:
            s += 2
    return s


def score_space(card, drive_type, alpha_role, pool_board_count, board_20d):
    beta = 0
    if pool_board_count >= 3:
        beta += 4
    if board_20d is not None and board_20d > 10:
        beta += 4
    s = beta + ALPHA_SCORE.get(alpha_role, 4)
    m = card.get("mktcap_yi")
    if m is not None:
        if m < 100:
            s += 9
        elif m < 200:
            s += 7
        elif m < 500:
            s += 4
    return s


def score_stage(momentum, realized_x, off_high_1y):
    m = {"加速": 12, "高位持平": 8, "待确认": 8, "衰减": 2}[momentum]
    m += realized_bucket(realized_x)[1]
    if off_high_1y is not None:
        if off_high_1y >= -25:
            m += 4
        elif off_high_1y >= -45:
            m += 2
        else:
            m += 1
    return m


def score_quality(card):
    gm = card.get("gm_trend") or []
    if len(gm) >= 2:
        rise = gm[-1] - gm[0]
        dips = sum(1 for i in range(len(gm) - 1) if gm[i + 1] < gm[i] - 0.1)
        if rise > 0.5 and dips == 0:
            gms = 4
        elif abs(rise) <= 0.5 and dips <= 1:
            gms = 2
        else:
            gms = 0
    else:
        gms = 2
    debt = card.get("debt_ratio")
    ds = 3 if debt is not None and debt < 40 else (2 if debt is not None and debt < 60 else 0)
    hs = 3 if card.get("holders_down") else 0  # P1: 股东户数下降, 缺失记0
    return gms + ds + hs


def score_confirm(card):
    inst = card.get("inst_pct")
    if inst is not None:
        cs = 5 if 5 <= inst <= 60 else (1 if inst < 5 else 3)
    else:
        cs = 2
    ytd = card.get("ytd_chg")
    if ytd is not None and ytd > 0:
        cs += 3
        if (card.get("limit_up_count") or 0) >= 3:
            cs += 2
    return cs


# ---------------- 纯逻辑: 硬闸 / 分级 / 标签 ----------------

def hard_gates(card, momentum, stage, llm=None):
    """命中闸列表 [{'gate':'H2','why':...}]。stage 为判定表原始输出。"""
    gates = []
    m = card.get("mktcap_yi")
    if m is not None and m >= H3_MKTCAP_YI:
        gates.append({"gate": "H3", "why": "体量出局(总市值%.0f亿>=1000亿)" % m})
    np_cur = card["quarterly"]["np"][-1]
    np_yoy = np_cur.get("yoy")
    np_val = np_cur.get("val")
    if (np_yoy is not None and np_yoy > H2_LOWBASE_YOY
            and np_val is not None and np_val < H2_LOWBASE_NP_YI
            and (card.get("roe") is not None and card["roe"] < H2_LOWBASE_ROE)):
        gates.append({"gate": "H2", "why": "低基数假高增(同比%.0f%%/当季%.2f亿/ROE%.1f%%)"
                      % (np_yoy, np_val, card["roe"])})
    if llm:
        if llm.get("is_pure_pricing_cycle"):
            gates.append({"gate": "H2", "why": "纯涨价周期(无量增)"})
        if llm.get("is_one_off_boost"):
            gates.append({"gate": "H2", "why": "并表/一次性收益为主"})
        if stage == "晚期候选" and llm.get("stage_call") == "晚期":
            gates.append({"gate": "H1", "why": "Claude终判晚期"})
    if stage == "晚期·出局":
        gates.append({"gate": "H1", "why": "判定表晚期(动能衰减x高度兑现)"})
    return gates


def classify(total, dims, gates):
    if gates:
        return "剔除"
    if total >= S_LINE and all(dims[k] >= v for k, v in S_GATE.items()):
        return "S"
    if total >= A_LINE:
        return "A"
    if total >= B_LINE:
        return "B"
    return "剔除"


def gen_tags(card, momentum, stage, llm, grade, gate_list):
    _ = grade, gate_list
    tags = {"stage": [stage], "drive": [llm.get("drive_type", "其他")] if llm else ["其他"],
            "quality": [], "confirm": [], "risk": [], "action": []}
    roe = card.get("roe")
    if roe is not None and roe >= 17:
        tags["quality"].append("高ROE%.1f" % roe)
    if (card.get("np_cash_ratio") or 0) >= 100:
        tags["quality"].append("现金流优")
    gm = card.get("gm_trend") or []
    if len(gm) >= 2 and gm[-1] - gm[0] > 0.5:
        tags["quality"].append("毛利改善")
    if (card.get("debt_ratio") or 100) < 40:
        tags["quality"].append("低杠杆")
    if card.get("np_cash_ratio") is not None and card["np_cash_ratio"] < 0:
        tags["quality"].append("净现比弱")
    if (card.get("debt_ratio") or 0) > 60:
        tags["quality"].append("高负债")
    inst = card.get("inst_pct")
    if inst is not None:
        if inst > 60:
            tags["confirm"].append("机构超配%.1f%%" % inst)
        elif inst >= 5:
            tags["confirm"].append("机构建仓%.1f%%" % inst)
        else:
            tags["confirm"].append("机构空白%.1f%%" % inst)
    ytd = card.get("ytd_chg")
    if ytd is not None and ytd > 50:
        tags["confirm"].append("强动量年内%+.0f%%" % ytd)
    elif ytd is not None and ytd < 0:
        tags["confirm"].append("动量弱年内%+.0f%%" % ytd)
    off = card.get("off_high_1y")
    if off is not None:
        if off >= -10:
            tags["confirm"].append("接近新高")
        elif off <= -40:
            tags["confirm"].append("深度回调%.0f%%·错杀观察" % off)
    ly, ty = card.get("listed_year"), card.get("this_year")
    if ly and ty and ty - ly < 1.5:
        tags["risk"].append("次新无年报")
    if llm is None:
        tags["risk"].append("LLM未判")
    if momentum in ("待确认",):
        tags["risk"].append("动能待验")
    if llm is not None and llm.get("drive_type") == "涨价周期":
        tags["risk"].append("周期属性")
    if card.get("mktcap_yi") is not None and 200 <= card["mktcap_yi"] < 500:
        tags["risk"].append("体量警示%.0f亿" % card["mktcap_yi"])
    return tags


# ---------------- 单只全流程 ----------------

def analyze_one(card, pool_board_count, llm=None, llm_failed=False):
    """card: 框架数据卡 dict -> 结果 dict(评分/阶段/闸/标签/分级)。
    llm = judge_output 中该码的判定(已过枚举校验); llm_failed=True 走保守路径。"""
    if llm_failed:
        llm = None
    q = card["quarterly"]
    momentum, mom_note = classify_momentum(q["rev"], q["np"])
    realized_x = card.get("realized_3y")
    stage_raw, need_llm = stage_cell(momentum, realized_x, card.get("mktcap_yi"))
    stage = stage_raw
    if need_llm:
        if llm is None:
            stage = "中期·警惕"  # LLM缺失保守路径(宁漏杀不错杀)
        elif llm.get("stage_call") in ("早期", "中期", "晚期"):
            stage = {"早期": "早期·待确认", "中期": "中期·警惕", "晚期": "晚期·出局"}[llm["stage_call"]]
    drive = (llm or {}).get("drive_type", "其他")
    alpha = (llm or {}).get("alpha_role", "一般配套")
    dims = {
        "engine": score_engine(card, drive),
        "space": score_space(card, drive, alpha, pool_board_count, card.get("board_20d")),
        "stage": score_stage(momentum, realized_x, card.get("off_high_1y")),
        "quality": score_quality(card),
        "confirm": score_confirm(card),
    }
    total = sum(dims.values())
    gates = hard_gates(card, momentum, stage, llm)
    grade = classify(total, dims, gates)
    # R组·骑乘观察(在途十倍,增长未降速): 已验证(近3年>=5倍)x仍在加速x引擎过硬xROE兜底。
    # 次优先级低于S/A; 有硬闸(晚期衰减/假引擎/体量)者不得入R。
    if (not gates and grade not in ("S", "A")
            and momentum == "加速"
            and (card.get("realized_3y") or 0) >= 5.0
            and dims["engine"] >= 20
            and (card.get("roe") or 0) >= 15):
        grade = "R"
    # α门槛(2026-10-28): S/A/R 须为"核心环节"(细分龙头/不可替代核心受益者, 静水: 最景气行业的王);
    # 一般配套/边缘最高B。判定缺席日(llm=None)不启用, 避免误杀。
    if llm is not None and grade in ("S", "A", "R") and alpha != "核心环节":
        grade = "B"
    return {
        "code": card["code"], "name": card.get("name", ""), "grade": grade,
        "total": total, "dims": dims, "momentum": momentum, "mom_note": mom_note,
        "stage": stage, "stage_raw": stage_raw, "gates": gates,
        "tags": gen_tags(card, momentum, stage, llm, grade, gates),
        "drive": drive, "alpha": alpha,
        "one_line": (llm or {}).get("one_line", ""), "final_note": (llm or {}).get("final_note", ""),
    }


# ---------------- 数据卡构建 ----------------

def build_card(entry, fetched, date_str=""):
    """entry: pipeline 的过闸条目(名片/单季表); fetched: L1.5 补全(市值/倍数等)。"""
    card = {
        "code": entry["code"], "name": entry.get("name", ""),
        "board": entry.get("board", ""), "l1_tier": entry.get("l1_tier", ""),
        "main_business": entry.get("main_business", ""),
        "catalyst": entry.get("catalyst", ""), "listed_year": entry.get("listed_year"),
        "quarterly": entry["quarterly"], "gm_trend": entry.get("gm_trend"),
        "roe": entry.get("roe"), "debt_ratio": entry.get("debt_ratio"),
        "np_cash_ratio": entry.get("np_cash_ratio"), "inst_pct": entry.get("inst_pct"),
        "ytd_chg": entry.get("ytd_chg"), "off_high_1y": entry.get("off_high_1y"),
        "limit_up_count": entry.get("limit_up_count"),
        "this_year": int(date_str[:4]) if date_str[:4].isdigit() else None,
    }
    card.update(fetched or {})
    return card


def stage_hint_of(card):
    """draft 用: 预计算阶段提示(judge_input 的 stage_hint, 供 Claude stage_call 判定)。"""
    q = card["quarterly"]
    mom, _ = classify_momentum(q["rev"], q["np"])
    st, need = stage_cell(mom, card.get("realized_3y"), card.get("mktcap_yi"))
    return st if need else "无意见"


# ---------------- 日报节渲染 (§7.1) ----------------

def render_section(results, market_status, s_full=False):
    lines = ["## 4. 框架精选",   # 编号承接: 1.筛选口径/2.正式财报/3.业绩预告
             "> %s | 研究分层, 非买点建议" % market_status, ""]
    kept = [r for r in results if r["grade"] in ("S", "A", "R")]
    kept.sort(key=lambda r: (-r["total"], r["name"]))
    for g in ("S", "A", "R"):
        grp = [r for r in kept if r["grade"] == g]
        if not grp:
            continue
        title = {"S": "S级·核心跟踪", "A": "A级·重点观察",
                 "R": "R级·骑乘观察(在途十倍·增长未降速)"}[g]
        if g == "S" and s_full:
            title += "(本季S已满)"
        lines.append("### %s(%d只)" % (title, len(grp)))
        lines.append("| 标的 | 总分·阶段·驱动 | 一句话 | 标签 | 五维(引/空/阶/质/确)｜行动 |")
        lines.append("|---|---|---|---|---|")
        for r in grp:
            tags = r["tags"]
            tag_txt = " ".join("#" + t for t in
                               (tags.get("quality", []) + tags.get("confirm", []) + tags.get("risk", [])))
            dims = r["dims"]
            lines.append("| **%s(%s)** | %d·%s·%s | %s | %s | %d/%d/%d/%d/%d｜%s |" % (
                r["name"], r["code"], r["total"], r["stage"], r["drive"],
                r["one_line"] or "—", tag_txt or "—",
                dims["engine"], dims["space"], dims["stage"], dims["quality"], dims["confirm"],
                (r.get("final_note") or "观察").replace("|", "/")))
        lines.append("")
    dropped = [r for r in results if r["grade"] in ("剔除", "B")]
    killed = [r for r in dropped if r["gates"]]
    bkept = [r for r in dropped if not r["gates"]]
    by_gate = {}
    for r in killed:
        key = r["gates"][0]["gate"]
        by_gate.setdefault(key, []).append(r)
    parts = []
    for key in ("H1", "H2", "H3"):
        if by_gate.get(key):
            parts.append("%s %d家(%s)" % ({"H1": "晚期", "H2": "假引擎", "H3": "体量"}[key],
                                          len(by_gate[key]),
                                          "、".join(r["name"] for r in by_gate[key][:6])))
    if bkept:
        parts.append("留档B %d只" % len(bkept))
    lines.append("### 剔除汇总")
    lines.append("今日过滤%d家: %s" % (len(dropped), " | ".join(parts) if parts else "无"))
    return "\n".join(lines)


def tr(vals):
    """近4季趋势文本: '+44.1→+120.8→+77.2→+129.6'; None(扭亏/基数≤0)->NA。"""
    return "→".join(("%+.1f" % v) if v is not None else "NA" for v in vals)


# ---------------- 编排(finalize 由 pipeline 调用) ----------------

def run(cards, llm_results, s_cap=S_CAP_PER_SEASON):
    """cards: [框架数据卡...]; llm_results: {code: 判定dict}(已校验, 缺码=判定缺席)。
    -> (results, s_full)。S上限按当批近似。"""
    boards = [c.get("board", "").split("-")[0] for c in cards]
    pool_board_count = {b: boards.count(b) for b in set(boards)}
    results = []
    for c in cards:
        cnt = pool_board_count.get(c.get("board", "").split("-")[0], 0)
        results.append(analyze_one(c, cnt, llm=llm_results.get(c["code"]),
                                   llm_failed=c["code"] not in llm_results))
    s_list = [r for r in results if r["grade"] == "S"]
    s_list.sort(key=lambda r: -r["total"])
    for r in s_list[s_cap:]:
        r["grade"] = "A"
        r["tags"]["risk"].append("S已满")
    return results, len(s_list) > s_cap


def load_judge_file(path, codes):
    """读 judge_output.json -> ({code: 判定}, 兜底计数)。
    枚举非法/缺字段 -> 该码走保守兜底(不阻塞); 多余码忽略。"""
    judged, fallback = {}, 0
    try:
        arr = json.load(open(path, encoding="utf-8"))
    except Exception:
        return judged, len(codes)
    for it in arr:
        if not isinstance(it, dict):
            continue
        code = str(it.get("code") or "").split(".")[0].strip()
        if code not in codes:
            continue
        ok = (it.get("drive_type") in DRIVE_ENUM
              and it.get("alpha_role") in ALPHA_ENUM
              and it.get("stage_call") in STAGE_CALL_ENUM
              and isinstance(it.get("is_pure_pricing_cycle"), bool)
              and isinstance(it.get("is_one_off_boost"), bool))
        if ok:
            judged[code] = {k: it.get(k) for k in
                            ("drive_type", "alpha_role", "stage_call",
                             "is_pure_pricing_cycle", "is_one_off_boost",
                             "one_line", "final_note", "catalyst_summary")}
        else:
            fallback += 1
    return judged, fallback


def dump_outputs(results, cards, out_dir, date_str):
    """L2 结果落盘(outputs/{date}/l2.json), 供 publish 与复盘联动读。"""
    try:
        if not os.path.isdir(out_dir):
            os.makedirs(out_dir)
        with open(os.path.join(out_dir, "l2.json"), "w", encoding="utf-8") as f:
            json.dump({"cards": cards, "results": results}, f, ensure_ascii=False, indent=1)
    except Exception:
        pass
