#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""profile.py — 买方画像(v3.2, 2026-11-12): 大单结构 + 龙虎榜/大宗席位打标 → 全息卡画像行。

设计原则:
- 纯确定性渲染(无 ⟦CLAUDE⟧ 占位), 数据函数在 data.py, 打标规则集中在本文件
- 五层识别金字塔中的三层落地: L0 大单结构(每日) / L2 席位(上榜日+大宗日) / L4 意图留给公告板块
- 席位≠身份: 营业部背后可多客户复用, 标签为概率性画像, 非事实指认
- 任一数据源失败自动降级(只渲染拿到的部分), 不阻塞复盘

边界(写死在代码里, 提醒未来维护者):
- "机构专用"是公募/社保/险资的匿名统称, 无法穿透到具体机构
- 龙虎榜仅上榜日可见; 大宗可场外协议不露面; 北向仅有日度持股(未接入本模块)
- 大单分类按单笔成交金额推断, 机构可拆单伪装
"""
import sys
from collections import Counter

sys.path.insert(0, __file__.rsplit("/", 1)[0])
import data  # noqa: E402

# 席位→资金类型打标表(概率性; 按圈内公认特征, 顺序即优先级, 可增量维护)。
# 键为 str=子串命中; 键为 tuple=全部子串同时命中(处理"中信证券股份有限公司北京分公司"这类全称)
SEAT_RULES = [
    # 交易所统一匿名席位
    ("机构专用", "机构"),
    ("沪股通", "北向"), ("深股通", "北向"),
    # QFII/外资通道(公开知名席位)
    ("瑞银证券", "QFII通道"), ("高盛", "QFII通道"), ("摩根大通", "QFII通道"),
    ("摩根士丹利", "QFII通道"), ("高华证券", "QFII通道"), ("野村", "QFII通道"),
    # 减持/配售常见通道
    (("中信证券", "北京分公司"), "减持通道"),
    # 知名游资聚集席位(部分, 可扩充)
    ("京基一百", "游资"), ("延安路", "游资"), ("深南大道", "游资"), ("南京太平南路", "游资"),
    ("银河证券绍兴", "游资"), ("华鑫上海分公司", "游资量化"),
    # 东财散户系
    ("拉萨", "散户东财系"), ("东方财富证券", "散户东财系"),
]


def seat_tag(name):
    """席位名→资金类型标签; 未命中且含'营业部'视为游资/未知, 其余标—。"""
    n = (name or "").strip()
    for key, tag in SEAT_RULES:
        keys = key if isinstance(key, tuple) else (key,)
        if all(k in n for k in keys):
            return tag
    return "游资/未知席位" if "营业部" in n else "—"


def flow_pattern(t):
    """当日大单结构→形态判定(确定性规则)。t=stock_fflow_breakdown 的一行, 单位万元。"""
    if not t or t.get("main") is None:
        return ""
    main, small, xl, lg = t["main"], t.get("small") or 0, t.get("xl") or 0, t.get("large") or 0
    bits = []
    if main > 0 and small < 0:
        bits.append("主力吸筹·散户交筹")
    elif main < 0 and small > 0:
        bits.append("主力出货·散户接筹⚠")
    elif main > 0:
        bits.append("主力净流入")
    else:
        bits.append("主力净流出")
    amain = abs(main) or 1
    if abs(xl) >= amain * 0.6 and abs(xl) >= 3000:
        bits.append("超大单主导(单一巨量机构嫌疑)")
    elif main > 0 and abs(xl) < amain * 0.3 and abs(lg) >= amain * 0.6:
        bits.append("大单主导(多路分散主力)")
    return "｜".join(bits)


def _side_tags(seats):
    """一侧席位列表 → '机构×2·北向×1' 摘要(过滤无法归类的'—')。"""
    c = Counter(seat_tag(s["seat"]) for s in seats if s.get("seat"))
    del c["—"]
    return "·".join("%s×%d" % (k, v) for k, v in c.most_common(4)) or "—"


def profile_lines(code, name="", lookback=30):
    """全息卡买方画像行。返回 (lines, summary): lines=markdown 行列表(可为空),
    summary=结构化摘要(进 machine.jsonl 时间序列)。全部降级返回 ([], {})。"""
    c = str(code).split(".")[0]
    lines, sm = [], {"code": c}

    # L0 大单结构(今日)
    ff = data.stock_fflow_breakdown(c, 6)
    if ff:
        t = ff[-1]
        sm.update({"date": t["date"], "main_wan": t["main"], "xl_wan": t["xl"], "large_wan": t["large"],
                   "small_wan": t["small"]})
        pat = flow_pattern(t)
        lines.append("买方画像: 主力 **%+.2f亿**(超大%+.2f/大%+.2f/中%+.2f/小%+.2f)%s" % (
            t["main"] / 1e4, t["xl"] / 1e4, t["large"] / 1e4, t["mid"] / 1e4, t["small"] / 1e4,
            (" → " + pat) if pat else ""))

    # L2 席位线索(近N日龙虎榜 + 大宗)
    parts = []
    try:
        lhb = data.lhb_records(c, lookback)
        sm["lhb_n"] = len(lhb)
        if lhb:
            last = lhb[0]
            seats = data.lhb_seats(c, last["date"])
            sm["lhb_last"] = {"date": last["date"],
                              "buy_tags": _side_tags(seats.get("buy", [])),
                              "sell_tags": _side_tags(seats.get("sell", []))}
            parts.append("上榜%d次(最近%s%s: 买 %s / 卖 %s)" % (
                len(lhb), last["date"][5:],
                (" %+.1f%%" % last["chg_pct"]) if last.get("chg_pct") is not None else "",
                _side_tags(seats.get("buy", [])), _side_tags(seats.get("sell", []))))
    except Exception:
        pass
    try:
        bt = data.block_trades(c, lookback)
        sm["block_n"] = len(bt)
        if bt:
            amt = round(sum(b["amt_yi"] for b in bt), 2)
            btags = Counter(seat_tag(b["buyer"]) for b in bt if b.get("buyer"))
            stags = Counter(seat_tag(b["seller"]) for b in bt if b.get("seller"))
            btags.pop("—", None)
            stags.pop("—", None)
            btxt = "·".join("%s×%d" % kv for kv in btags.most_common(3))
            stxt = "·".join("%s×%d" % kv for kv in stags.most_common(3))
            sm["block_last"] = {"date": bt[0]["date"], "amt_yi": amt,
                                "buy_tags": btxt, "sell_tags": stxt}
            parts.append("大宗%d笔合计%.2f亿(买 %s / 卖 %s)" % (len(bt), amt, btxt or "—", stxt or "—"))
    except Exception:
        pass
    if parts:
        lines.append("席位线索(近%d日): %s" % (lookback, " ｜ ".join(parts)))

    return lines, (sm if (lines or sm.get("lhb_n") or sm.get("block_n")) else {})
