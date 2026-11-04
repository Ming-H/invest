#!/usr/bin/env python3
"""
fixtures 生成器 · tenscreener tests

把若羽臣(003010)四个历史时点的【真实已验证数据】写成本地问财缓存 fixture,
供 `--offline --cache-dir tests/fixtures/cache/<时点>` 回放。

数据来源:2026-08-30 会话中经问财单季口径+累计倒算双重交叉验证的真实值
(2023Q4~2026Q2 三线单季同比;股价为前复权近似值)。

⚠️ query 字符串必须与 scripts/ 里的实现【逐字一致】(含空格归一化后)。
   改 query 模板后必须重跑本脚本重新生成,否则 offline 回放会抛 FixtureMissing
   ——这正是防"问财语法漂移"的哨兵设计。
"""

import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))


def fixture_path(cache_dir, query, limit):
    norm = " ".join(str(query).split())
    key = hashlib.md5(f"{norm}|limit={limit}".encode("utf-8")).hexdigest()
    return os.path.join(cache_dir, f"{key}.json")


def write(cache_dir, query, limit, datas):
    os.makedirs(cache_dir, exist_ok=True)
    with open(fixture_path(cache_dir, query, limit), "w", encoding="utf-8") as f:
        json.dump({"query": " ".join(str(query).split()), "limit": limit,
                   "ts": "fixture", "datas": datas}, f, ensure_ascii=False, indent=1)


def q_yoy(rev, parent, deduct):
    """构造一行『近四个季度单季三线同比』返回(key 用问财单季字段形态)。"""
    row = {"股票代码": "003010.SZ", "股票简称": "若羽臣"}
    for (lbl, r) in rev:
        row[f"单季营业收入同比增长率[{lbl}]"] = r
    for (lbl, p) in parent:
        row[f"单季归母净利润同比增长率[{lbl}]"] = p
    for (lbl, d) in deduct:
        row[f"单季扣非净利润同比增长率[{lbl}]"] = d
    return row


def e(y, q):
    return f"{y}{'0331' if q == 1 else '0630' if q == 2 else '0930' if q == 3 else '1231'}"


# ── 真实三线单季同比(2023Q4~2026Q2,已双重验证) ──────────────
YOY = {
    "2023Q4": (13.9, 22.5, 19.6),
    "2024Q1": (31.7, 86.3, 78.3),
    "2024Q2": (34.4, 66.5, 68.9),
    "2024Q3": (54.6, 68.1, 76.3),
    "2024Q4": (13.8, 133.2, 135.4),
    "2025Q1": (54.2, 113.9, 107.6),
    "2025Q2": (79.6, 71.7, 72.2),
    "2025Q3": (123.4, 73.3, 69.9),
    "2025Q4": (111.4, 87.0, 77.8),
    "2026Q1": (73.3, 163.8, 167.5),
    "2026Q2": (73.2, 112.1, 106.7),
}


def yoy_row(periods):
    rev = [(e(int(p[:4]), int(p[-1])), YOY[p][0]) for p in periods]
    par = [(e(int(p[:4]), int(p[-1])), YOY[p][1]) for p in periods]
    ded = [(e(int(p[:4]), int(p[-1])), YOY[p][2]) for p in periods]
    return q_yoy(rev, par, ded)


YQ_QUERY = ("若羽臣 近四个季度单季营业收入同比增长率"
            " 近四个季度单季归母净利润同比增长率 近四个季度单季扣非净利润同比增长率")
POS_QUERY = "若羽臣 近一年最高价 前复权 最新价"
ZHISHU_QUERY = "上证指数 最新点位 250日均线 近一年涨跌幅"
L1_QUERY = ("营业收入同比增长率大于50% 总市值小于300亿 近一年涨跌幅小于30%"
            " 近三年涨跌幅小于100% ROE大于15% 上市满两年 非ST 非亏损"
            " 归母净利润同比增长率 扣除非经常性损益后的净利润")
L1_PRICE_QUERY = ("若羽臣 测试周期股 历史最高价前复权 历史最高价日 最新价"
                  " 近六个月涨跌幅 近一年涨跌幅 近三年涨跌幅")


def zhishu_row(close, ma, gain):
    """实测 zhishu 返回 key 形态:最新价/ma250[日期]/涨跌幅[区间]。"""
    return {"指数代码": "000001.SH", "指数简称": "上证指数",
            "最新价": close, "ma250[20241115]": ma,
            "涨跌幅[20231115-20241115]": gain}


def pos_row(high, last):
    return {"股票代码": "003010.SZ", "股票简称": "若羽臣",
            "近一年最高价前复权": high, "最新价": last}


def gen_2024_10_08():
    """T1:L1 初筛。若羽臣(24Q3 三线首次全过线,股价未动) + 一只周期股(应被剔)。"""
    d = os.path.join(HERE, "cache", "2024-10-08")
    write(d, L1_QUERY, 150, [
        {"股票代码": "003010.SZ", "股票简称": "若羽臣",
         "归母净利润同比增长率": 68.1, "营业收入同比增长率": 54.6,
         "净资产收益率": 8.2, "扣除非经常性损益后的净利润[20240930]": 56_770_000},
        {"股票代码": "999999.SZ", "股票简称": "测试周期股",
         "归母净利润同比增长率": 60.0, "营业收入同比增长率": 55.0,
         "净资产收益率": 9.0, "扣除非经常性损益后的净利润[20240930]": 30_000_000},
    ])
    write(d, L1_PRICE_QUERY, 4, [
        {"股票代码": "003010.SZ", "股票简称": "若羽臣",
         "历史最高价前复权": 11.5, "历史最高价日": "20200729", "最新价": 6.92,
         "近六个月涨跌幅": 12.0, "近一年涨跌幅": -18.0, "近三年涨跌幅": -55.0,
         "涨跌幅[20240408-20241008]": -18.0, "涨跌幅[20240101-20240701]": 12.0,
         "涨跌幅[20211008-20241008]": -55.0},
        {"股票代码": "999999.SZ", "股票简称": "测试周期股",
         "历史最高价前复权": 30.0, "历史最高价日": "20210630", "最新价": 9.0,
         "近六个月涨跌幅": 5.0, "近一年涨跌幅": -10.0, "近三年涨跌幅": -70.0},
    ])
    write(d, YQ_QUERY, 5, [yoy_row(["2023Q4", "2024Q1", "2024Q2", "2024Q3"])])
    # pos/zhishu 供 watch 用,同目录附带(2024-11-15 用独立目录)
    return d


def gen_2024_11_15():
    """T2:watch --run。三季报(10/30)披露后两周,股价 9.2 突破一年新高,大盘 on。"""
    d = os.path.join(HERE, "cache", "2024-11-15")
    write(d, YQ_QUERY, 5, [yoy_row(["2023Q4", "2024Q1", "2024Q2", "2024Q3"])])
    write(d, POS_QUERY, 5, [pos_row(9.0, 9.2)])          # pos_1y = 1.022
    write(d, ZHISHU_QUERY, 5, [zhishu_row(3320.0, 3010.0, 12.0)])
    return d


def gen_2025_04_30():
    """T3:年报+一季报连续过线(streak≥2 → 连续验证注记)。"""
    d = os.path.join(HERE, "cache", "2025-04-30")
    write(d, YQ_QUERY, 5, [yoy_row(["2024Q2", "2024Q3", "2024Q4", "2025Q1"])])
    write(d, POS_QUERY, 5, [pos_row(13.6, 21.89)])       # 已大幅新高
    write(d, ZHISHU_QUERY, 5, [zhishu_row(3350.0, 3150.0, 10.0)])
    return d


def gen_2026_04_30():
    """T4:track --run。26Q1 披露:增速降档(123→111→73)但三线仍全过,回撤49% →
    按规则应 warn(中期调整裁定:扛住)而非 exit —— 与真实后续(26Q3 反弹+25%)一致。"""
    d = os.path.join(HERE, "cache", "2026-04-30")
    write(d, YQ_QUERY, 5, [yoy_row(["2025Q2", "2025Q3", "2025Q4", "2026Q1"])])
    write(d, POS_QUERY, 5, [pos_row(44.0, 24.30)])
    return d


if __name__ == "__main__":
    for gen in (gen_2024_10_08, gen_2024_11_15, gen_2025_04_30, gen_2026_04_30):
        print("wrote", gen())
