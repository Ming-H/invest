#!/usr/bin/env python3
"""overnight 模块离线单测: 解析/精度分母/缺符号跳过。用法: python3 tests/test_overnight.py"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import data  # noqa: E402


def check(cond, msg):
    print(("  ✓ " if cond else "  ✗ FAIL: ") + msg)
    if not cond:
        raise AssertionError(msg)


print("① 注册表完整性")
check(len(data.OVERNIGHT_REGISTRY) == 8, "8个符号")
check(len({x[1] for x in data.OVERNIGHT_REGISTRY}) == 8, "名称唯一")
check(all(len(x) == 4 for x in data.OVERNIGHT_REGISTRY), "四元组(secid,label,beta,div)")

print("② 解析: key匹配/精度/缺符号跳过")
diff = [
    {"f13": 100, "f12": "DJIA", "f2": 5019817, "f3": -111},
    {"f13": 133, "f12": "USDCNH", "f2": 66683, "f3": -1},      # 外汇×10000口径
    {"f13": 999, "f12": "NOTREG", "f2": 123, "f3": 45},          # 未注册符号→忽略
]
r = data._parse_overnight(diff)
check(len(r) == 2, "只保留注册符号(2)")
check(r[0]["label"] == "道琼斯" and r[0]["price"] == 50198.17 and r[0]["chg_pct"] == -1.11, "指数÷100")
check(r[1]["label"] == "美元/离岸人民币" and r[1]["price"] == 6.6683, "外汇÷10000")

print("③ 异常输入")
check(data._parse_overnight(None) == [], "None→[]")
check(data._parse_overnight([{"f13": 1, "f12": "X", "f2": None}]) == [], "价格缺失跳过")

print("\n全部通过 ✓")
