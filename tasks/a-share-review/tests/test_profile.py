#!/usr/bin/env python3
"""
离线单测：profile.py 的席位打标 / 大单形态判定（纯函数, 不联网）。
用法：python3 tests/test_profile.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import profile  # noqa: E402


def check(cond, msg):
    print(("  ✓ " if cond else "  ✗ FAIL: ") + msg)
    if not cond:
        raise AssertionError(msg)


print("== 席位打标 seat_tag ==")
check(profile.seat_tag("机构专用") == "机构", "机构专用→机构")
check(profile.seat_tag("沪股通专用") == "北向", "沪股通→北向")
check(profile.seat_tag("瑞银证券有限责任公司上海花园石桥路证券营业部") == "QFII通道", "瑞银花园石桥路→QFII通道")
check(profile.seat_tag("中信证券股份有限公司北京分公司") == "减持通道", "中信北京分公司→减持通道")
check(profile.seat_tag("国泰海通证券股份有限公司深圳深南大道京基一百证券营业部") == "游资",
      "京基一百优先于'深南大道'同为游资, 命中游资")
check(profile.seat_tag("国泰海通证券杭州延安路证券营业部") == "游资", "延安路→游资")
check(profile.seat_tag("东方财富证券股份有限公司拉萨东环路证券营业部") == "散户东财系", "拉萨东财系→散户东财系")
check(profile.seat_tag("某某证券某某大街证券营业部") == "游资/未知席位", "未知营业部→游资/未知席位")
check(profile.seat_tag("") == "—", "空席位→—")
# 优先级: 机构专用 命中在北向之前不应被"专用"干扰
check(profile.seat_tag("机构专用") != "北向", "机构专用不被误标北向")

print("== 大单形态 flow_pattern ==")
# 主力吸筹 + 大单主导(多路分散): 2026-11-12 松发实测形态
t1 = {"main": 20769, "xl": 2158, "large": 18611, "mid": -6522, "small": -14246, "date": "2026-11-12"}
p1 = profile.flow_pattern(t1)
check("主力吸筹·散户交筹" in p1, "主力流入+散户流出→吸筹形态")
check("大单主导(多路分散主力)" in p1, "xl占比<30%且大单占比>=60%→大单主导")
# 超大单主导
t2 = {"main": 30000, "xl": 20000, "large": 10000, "mid": -8000, "small": -20000, "date": "x"}
p2 = profile.flow_pattern(t2)
check("超大单主导" in p2, "xl占比>=60%且>=3000万→超大单主导")
# 出货
t3 = {"main": -8000, "xl": -5000, "large": -3000, "mid": 2000, "small": 6000, "date": "x"}
check("主力出货·散户接筹⚠" in profile.flow_pattern(t3), "主力流出+散户流入→出货形态")
# 边界: 空值
check(profile.flow_pattern(None) == "", "None→空")
check(profile.flow_pattern({"main": None}) == "", "main缺失→空")

print("== 打标摘要 _side_tags ==")
seats = [{"seat": "机构专用", "amt_wan": 1}, {"seat": "机构专用", "amt_wan": 2},
         {"seat": "沪股通专用", "amt_wan": 3}]
tags = profile._side_tags(seats)
check("机构×2" in tags and "北向×1" in tags, "两侧计数摘要正确: %s" % tags)
check(profile._side_tags([]) == "—", "空列表→—")

print("\n全部通过 ✅")
