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


# ── 免费路径 fixtures(2026-11-07 watch/track 免问财化) ─────────
# emoney F10(三线) + 腾讯K线(价格/大盘) —— 全部由 YOY 表确定性构造, 无网络。

# 单季基数(亿元, 任意正值, 同比由此推出): 2022Q4 供 2023Q4 同比, 2023 各季供 2024 同比
_SINGLE_BASE = {
    "2022Q4": (100.0, 50.0, 40.0),
    "2023Q1": (100.0, 60.0, 50.0), "2023Q2": (100.0, 60.0, 50.0),
    "2023Q3": (100.0, 60.0, 50.0),
}


def _singles():
    """{qlabel: (rev, parent, deduct) 单季亿元} —— YOY 表 × 基数, 2022Q4~2026Q1。"""
    out = dict(_SINGLE_BASE)
    for lbl, (r, p, d) in YOY.items():
        base_lbl = "%dQ%s" % (int(lbl[:4]) - 1, lbl[-1])
        b = out[base_lbl]
        out[lbl] = tuple(round(v * (1 + y / 100), 4)
                         for v, y in zip(b, (r, p, d)))
    return out


_NOTICE = {  # 披露日(与真实节奏一致即可, 回放截断用; 基数季也须有——同比计算需要)
    "2022Q4": "2023-04-20", "2023Q1": "2023-04-25", "2023Q2": "2023-08-28",
    "2023Q3": "2023-10-30",
    "2023Q4": "2024-04-20", "2024Q1": "2024-04-25", "2024Q2": "2024-08-28",
    "2024Q3": "2024-10-30", "2024Q4": "2025-04-25", "2025Q1": "2025-04-28",
    "2025Q2": "2025-08-27", "2025Q3": "2025-10-29", "2025Q4": "2026-04-24",
    "2026Q1": "2026-04-28",
}


def _q_end(y, q):
    return "%d-%s" % (y, {1: "03-31", 2: "06-30", 3: "09-30", 4: "12-31"}[q])


def write_f10(cache_dir, asof, code="003010"):
    """emoney F10 fixture: 单季→累计, 仅写 notice≤asof 的报告期(防未来泄露)。"""
    singles = _singles()
    rows, cum = [], (0.0, 0.0, 0.0)
    for lbl in sorted(singles):
        if lbl not in _NOTICE or _NOTICE[lbl] > asof:
            # 未到披露日的季, 累计清零重置(该季不会出现在行里; cum 沿用上年同季断链无碍,
            # 因下一写入季必为 Q1 或 notice 已过的季, Q1 的 cum=single 与历史无关)
            y, q = int(lbl[:4]), int(lbl[-1])
            if q == 1:
                cum = singles[lbl]
            continue
        y, q = int(lbl[:4]), int(lbl[-1])
        s = singles[lbl]
        cum = s if q == 1 else tuple(a + b for a, b in zip(cum, s))
        rows.append({"REPORT_DATE": _q_end(y, q),
                     "TOTALOPERATEREVE": round(cum[0] * 1e8),
                     "PARENTNETPROFIT": round(cum[1] * 1e8),
                     "KCFJCXSYJLR": round(cum[2] * 1e8),
                     "NOTICE_DATE": _NOTICE[lbl]})
    d = os.path.join(cache_dir, "emoney")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "RPT_F10_FINANCE_MAINFINADATA_%s.json" % code),
              "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False)
    return d


def _calendar_days(beg, end):
    from datetime import date as _d, timedelta as _td
    b, e = _d.fromisoformat(beg), _d.fromisoformat(end)
    out, cur = [], b
    while cur <= e:
        if cur.weekday() < 5:
            out.append(cur.isoformat())
        cur += _td(days=1)
    return out


def write_kline(cache_dir, symbol, beg, end, closes, high_pad=0.0):
    """腾讯K线 fixture: closes 为关键锚点(线性插值到每根), high=close+pad
    (pad 默认 0: 锚点高价即区间高, pos_1y/drawdown 断言才能精确)。"""
    days = _calendar_days(beg, end)
    anchors = {d: v for d, v in closes}
    xs = list(anchors.items())

    def price_at(dstr):
        for i in range(len(xs) - 1):
            if xs[i][0] <= dstr <= xs[i + 1][0]:
                import datetime as _dt
                d0 = _dt.date.fromisoformat(xs[i][0]).toordinal()
                d1 = _dt.date.fromisoformat(xs[i + 1][0]).toordinal()
                dd = _dt.date.fromisoformat(dstr).toordinal()
                t = (dd - d0) / max(d1 - d0, 1)
                return round(xs[i][1] + (xs[i + 1][1] - xs[i][1]) * t, 3)
        return xs[-1][1]

    rows = [[d, str(price_at(d)), str(price_at(d)),
             str(round(price_at(d) + high_pad, 3)), str(round(price_at(d) - 0.3, 3)), "1"]
            for d in days]
    # 锚点日精确落值(插值端点已含, 保险覆写)
    d = os.path.join(cache_dir, "kline")
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, "%s_%s_%s.json" % (symbol, beg, end))
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"symbol": symbol, "beg": beg, "end": end, "rows": rows},
                  f, ensure_ascii=False)
    return path


def gen_free_fixtures():
    """T2/T3/T4 的 emoney F10 + K线 fixtures(watch/track 免费路径, TENSCREENER_ASOF 冻结)。"""
    root = HERE
    # T2 2024-11-15: 股价 9.2 破一年新高; 大盘 on(上证 3000→3320 升势)
    d2 = os.path.join(root, "cache", "2024-11-15")
    write_f10(d2, "2024-11-15")
    write_kline(d2, "sz003010", "2022-01-01", "2024-11-15",
                [("2022-01-04", 8.0), ("2024-09-30", 8.0), ("2024-11-15", 9.2)])
    write_kline(d2, "sh000001", "2022-01-01", "2024-11-15",
                [("2022-01-04", 3000), ("2024-11-15", 3320)])
    # T3 2025-04-30: 已大幅新高(21.89); 大盘 on
    d3 = os.path.join(root, "cache", "2025-04-30")
    write_f10(d3, "2025-04-30")
    write_kline(d3, "sz003010", "2023-01-01", "2025-04-30",
                [("2023-01-03", 20.0), ("2025-02-28", 20.0), ("2025-04-30", 21.89)])
    write_kline(d3, "sh000001", "2023-01-01", "2025-04-30",
                [("2023-01-03", 3050), ("2025-04-30", 3280)])
    # T4 2026-04-30: 250日窗内高点 44 → 回撤45%(warn 裁定)
    d4 = os.path.join(root, "cache", "2026-04-30")
    write_f10(d4, "2026-04-30")
    write_kline(d4, "sz003010", "2024-01-01", "2026-04-30",
                [("2024-01-02", 30.0), ("2025-09-30", 44.0), ("2026-04-30", 24.3)])
    return d2, d3, d4


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
    print("wrote free-path fixtures:", *gen_free_fixtures())
