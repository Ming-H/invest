#!/usr/bin/env python3
"""
离线解析测试：用 tests/fixtures/*.json（真实 API 采样）跑 flow.py 的解析 / 排序 / 日期提取逻辑。
不联网。用法：python3 tests/test_parser.py
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import flow  # noqa: E402

FIX = ROOT / "tests" / "fixtures"


def load(name):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def approx(a, b, eps=1e-6):
    # 相对容差：大金额（1e9 量级）浮点末位抖动不应判失败
    return a is not None and abs(a - b) <= eps * max(abs(a), abs(b), 1.0)


def check(cond, msg):
    print(("  ✓ " if cond else "  ✗ FAIL: ") + msg)
    if not cond:
        raise AssertionError(msg)


def test_industry_inflow():
    print("[industry-inflow]")
    payload = load("industry-inflow.json")  # fixture 以 --limit 5 采样
    rows = flow.parse_board(payload["datas"])
    rows = flow.sort_and_top(rows, "industry-inflow", 10)
    check(len(rows) == 5, f"fixture 5 条，得到 {len(rows)}")
    check(rows[0]["amount"] >= rows[-1]["amount"], "降序：首项 ≥ 末项")
    check(rows[0]["name"] == "银行", f"榜首=银行（实测 +33亿），得到 {rows[0]['name']}")
    check(approx(rows[0]["amount"], 3_302_760_900), f"银行金额≈3.3e9，得到 {rows[0]['amount']}")
    date = flow.extract_date(payload["datas"], flow.AMOUNT_PREFIXES)
    check(date == "2026-07-06", f"交易日=2026-07-06，得到 {date}")


def test_industry_outflow():
    print("[industry-outflow]")
    payload = load("industry-outflow.json")
    rows = flow.parse_board(payload["datas"])
    rows = flow.sort_and_top(rows, "industry-outflow", 10)
    check(len(rows) == 5, f"fixture 5 条，得到 {len(rows)}")
    check(rows[0]["amount"] <= rows[-1]["amount"], "升序：首项 ≤ 末项")
    check(rows[0]["amount"] < 0, "首项为负（流出）")
    check(rows[0]["name"] == "光学光电子", f"榜首=光学光电子（实测 -70亿），得到 {rows[0]['name']}")


def test_concept_inflow():
    print("[concept-inflow]")
    payload = load("concept-inflow.json")
    rows = flow.parse_board(payload["datas"])
    rows = flow.sort_and_top(rows, "concept-inflow", 10)
    check(len(rows) == 5, f"fixture 5 条，得到 {len(rows)}")
    check(rows[0]["amount"] > 0 and rows[0]["amount"] >= rows[-1]["amount"], "降序且首项为正")
    # 概念与行业共用 主力净买入额 字段（计划原假设的 资金净流入额 是错的，已实测纠正）
    check(rows[0]["amount"] is not None, "概念金额字段命中（主力净买入额）")


def test_orders():
    print("[orders]")
    payload = load("orders.json")
    rows = flow.parse_orders(payload["datas"])
    rows = flow.sort_and_top(rows, "orders", 10)
    check(len(rows) == 5, f"fixture 5 条，得到 {len(rows)}")
    r = rows[0]
    for k in flow.ORDERS_PREFIXES:
        check(k in r, f"含字段 {k}")
    expected_net = sum(r[k] for k in flow.ORDERS_PREFIXES)
    check(approx(r["net"], expected_net), "net = 特大+大+小 之和")
    check(rows[0]["net"] >= rows[-1]["net"], "按合计降序")


def test_stock():
    print("[stock]")
    payload = load("stock.json")
    rows = flow.parse_stock(payload["datas"])
    rows = flow.sort_and_top(rows, "stock", 10)
    check(len(rows) == 5, f"fixture 5 条，得到 {len(rows)}")
    check(rows[0]["name"] == "佰维存储", f"榜首=佰维存储，得到 {rows[0]['name']}")
    # stock 用的是 主力资金流向 字段（≠ 板块的 主力净买入额）
    check(approx(rows[0]["amount"], 1_016_229_569.01), f"金额取自 主力资金流向≈1.02e9，得到 {rows[0]['amount']}")
    check(rows[0]["pct"] is not None, "涨跌幅命中（stock 用 最新涨跌幅，无 :前复权 后缀）")
    date = flow.extract_date(payload["datas"], ["主力资金流向"])
    check(date == "2026-07-06", f"交易日=2026-07-06，得到 {date}")


def main():
    for t in [test_industry_inflow, test_industry_outflow, test_concept_inflow,
              test_orders, test_stock]:
        t()
    print("\n全部通过 ✓")


if __name__ == "__main__":
    main()
