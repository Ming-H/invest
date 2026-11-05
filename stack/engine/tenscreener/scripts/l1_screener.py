#!/usr/bin/env python3
"""
L1 问财初筛 · tenscreener

全 A 股量化硬筛,把 ~5000 只砍到几十只"营收在加速、无硬伤"的候选池。
  Step1 问财粗筛:营收同比≥50% + 严格早期价格带 + 质量底线(字面法则)
  Step2 历史股价过滤:剔周期/剔已涨高 + 区间涨幅二次验证
  Step3 三线多季序列(排序加权,不加硬过滤):
      近四个季度单季 营收/归母/扣非 同比 —— 过线条数/连续季数/共振形态
      【若羽臣教训】单季掉线不剔除(24Q4 营收+14%掉线但利润+133%过线,随后照涨):
      只作信息披露与排序权重,忠于两框架字面法则,不自行衍生苛刻条件。

数据源:同花顺问财(hithink-finance-query,经 wencai.py 缓存/计数)。
纯标准库。
"""

import argparse
import json
import re as _re
import sys
from datetime import date

from wencai import iwencai, _num, _pick, DEFAULT_FINANCE_CLI          # noqa: F401 (re-export)
from quarters import (latest_report_period, fetch_quarterly_yoy,      # noqa: F401
                      analyze_quarterly)


def _get_by_period(item, field, period):
    """从问财返回项取 '字段[报告期]' 的值,容错模糊匹配。period 如 20251231。"""
    key = f"{field}[{period}]"
    if key in item:
        return _num(item[key])
    for k, v in item.items():
        if field in k and str(period) in k:
            return _num(v)
    return None


# ─── Step1: 问财粗筛 ───────────────────────────────────────

def screen_step1(roe_min=5, cli_path=DEFAULT_FINANCE_CLI, limit=150,
                 max_1yr_gain=30, max_3yr_gain=100):
    """
    问财硬筛 · CAN SLIM × 静水【字面法则】+ 严格早期价格带(问财侧能条件化的部分):
      营收同比 ≥50%        成长已启动(业绩趋势)
      总市值 <300亿        小中市值(十倍空间)
      近1年涨幅 <max_1yr   未大涨(严格早期,挡掉已涨很高的)
      近3年涨幅 <max_3yr   挡多倍股高位横盘(近1年没涨但3年已翻几倍的不算早期)
      ROE >roe_min         质量底线(早期投入期容忍低 ROE,故默认 5 而非 17)
      上市满两年 非ST 非亏损
    query 末尾的「归母净利润同比增长率/扣除非经常性损益后的净利润」是名词性输出字段
    (不带条件)——修复历史 bug:此前 query 未请求这两个字段,导致 C 要素与扣非过滤断链。
    """
    query = (
        f"营业收入同比增长率大于50% "                # 营收暴增(成长已启动)
        f"总市值小于300亿 "                          # 小中市值(十倍空间)
        f"近一年涨跌幅小于{max_1yr_gain}% "          # 严格早期:近1年未大涨
        f"近三年涨跌幅小于{max_3yr_gain}% "          # 挡多倍股高位横盘
        f"ROE大于{roe_min}% "                        # 质量底线(早期容忍低ROE)
        f"上市满两年 非ST 非亏损 "
        f"归母净利润同比增长率 扣除非经常性损益后的净利润"   # 输出字段(修复断链)
    )
    datas = iwencai(query, limit=limit, cli_path=cli_path)
    candidates = []
    for x in datas:
        candidates.append({
            "code": x.get("股票代码"),
            "name": x.get("股票简称"),
            "profit_growth": _pick(x, "归母净利润同比增长率"),        # C
            "rev_growth": _pick(x, "营业收入同比增长率"),            # 静水主营
            "roe": _pick(x, "净资产收益率", "加权净资产收益率"),      # A
            "deduct_profit": _pick(x, "扣非归母净利润", "扣除非经常性损益后的净利润"),
        })
    return candidates


def detect_traps(c):
    """排雷:周期反转 / 一次性注水。返回陷阱标记列表。"""
    traps = []
    pg = c.get("profit_growth")
    if pg and pg > 300:
        traps.append("周期反转嫌疑(净利同比>300%)")
    return traps


# ─── Step2: 历史股价过滤(用股价形态区分成长 vs 周期 + 区间涨幅)─────
# 用户洞察:周期股历史上一定经历过完整大涨大跌(最高价在很久以前且当前远离高位);
#          成长股要么还在低位蓄势,要么在创新高的路上(最高价在近期)。

def _parse_range_gains(item, today=None):
    """从问财项解析 '涨跌幅[YYYYMMDD-YYYYMMDD]' 字段,按区间长度归类为 6mo/1yr/3yr。
    问财不能把"近6月涨幅"当筛选条件,但能当字段返回(形如 涨跌幅[20260109-20260710])。
    返回 {gain_6mo, gain_1yr, gain_3yr}(任一缺失为 None)。"""
    today = today or date.today()
    found = []  # [(区间天数, 涨跌幅值)]
    for k, v in item.items():
        m = _re.match(r"^涨跌幅\[(\d{8})-(\d{8})\]$", str(k).strip())
        if not m:
            continue
        val = _num(v)
        if val is None:
            continue
        try:
            s = date(int(m.group(1)[:4]), int(m.group(1)[4:6]), int(m.group(1)[6:8]))
            found.append(((today - s).days, val))
        except (ValueError, TypeError):
            continue
    out = {"gain_6mo": None, "gain_1yr": None, "gain_3yr": None}
    if not found:
        return out
    found.sort(key=lambda x: x[0])                      # 按区间天数升序
    bands = {"gain_6mo": 182, "gain_1yr": 365, "gain_3yr": 1095}
    used = set()
    for key, target in bands.items():                   # 每档取最接近的未用区间
        best_i, best_d = None, 1e9
        for i, (d, _v) in enumerate(found):
            if i in used:
                continue
            if abs(d - target) < best_d:
                best_d, best_i = abs(d - target), i
        if best_i is not None and best_d <= 120:        # 容差 120 天
            used.add(best_i)
            out[key] = found[best_i][1]
    return out


def fetch_price_history(names, cli_path=DEFAULT_FINANCE_CLI, batch=8):
    """
    批量查候选的「历史最高价、历史最高价日、当前价、近6月/近1年/近3年涨跌幅」。
    返回 {name: {high, high_date, last, days_since_high, position_ratio, gain_6mo, gain_1yr, gain_3yr}}。
    """
    today = date.today()
    result = {}
    for i in range(0, len(names), batch):
        chunk = names[i:i + batch]
        query = (" ".join(chunk)
                 + " 历史最高价前复权 历史最高价日 最新价"
                   " 近六个月涨跌幅 近一年涨跌幅 近三年涨跌幅")
        datas = iwencai(query, limit=len(chunk) + 2, cli_path=cli_path)
        for x in datas:
            nm = x.get("股票简称")
            if not nm:
                continue
            high = _pick(x, "历史最高价前复权", "历史最高价")
            high_date_raw = x.get("历史最高价日")
            last = _pick(x, "最新价")
            days_since_high = None
            if high_date_raw:
                try:
                    s = str(high_date_raw)
                    hd = date(int(s[:4]), int(s[4:6]), int(s[6:8]))
                    days_since_high = (today - hd).days
                except (ValueError, TypeError):
                    pass
            entry = {
                "high": high,
                "high_date": high_date_raw,
                "last": last,
                "days_since_high": days_since_high,
                "position_ratio": (last / high) if (high and last and high > 0) else None,
            }
            entry.update(_parse_range_gains(x, today=today))
            result[nm] = entry
    return result


def classify_growth_cyclic(ph, max_position=0.75):
    """
    用历史股价形态判断 成长 vs 周期 vs 已涨很高(需「最高价距今 d」+「当前位置 pos」联合判断):
      周期股(涨过又跌回):最高价>3年 且 当前<50%
      见顶回落(周期嫌疑):最高价<1年 但 当前已大跌<50%(刚见顶下跌)
      已涨很高(非早期):  当前≥max_position(默认75%历史最高)→已在高位,严格早期剔除
      成长股(刚突破早期):最高价<1年 且 当前60%~75%(创新高但累计涨幅还小)
      中性:其他 → 交 L2/L3 定性
    返回 (label, reason)。label: 'growth' / 'cyclic' / 'high' / 'neutral'。
    'cyclic' 与 'high' 在 run() 中剔除。
    """
    d = ph.get("days_since_high")
    pos = ph.get("position_ratio")
    if d is None or pos is None:
        return "neutral", "历史价数据不全"
    if d > 1095 and pos < 0.5:
        return "cyclic", f"最高价{d}天前且当前{pos*100:.0f}%→周期股(涨过又跌回)"
    if d < 365 and pos < 0.5:
        return "cyclic", f"最高价{d}天前但当前仅{pos*100:.0f}%→见顶回落,周期嫌疑"
    if pos >= max_position:
        return "high", f"当前{pos*100:.0f}%≥历史最高{max_position*100:.0f}%→已涨很高,严格早期剔除"
    if d < 365 and pos >= 0.6:
        return "growth", f"最高价{d}天前且当前{pos*100:.0f}%→刚突破创新高、累计涨幅还小,成长早期"
    return "neutral", f"最高价{d}天前,当前{pos*100:.0f}%,需定性"


# ─── Step3: 三线多季序列(排序加权,不剔除) ────────────────────

def attach_quarterly(survivors, cli_path=DEFAULT_FINANCE_CLI, max_quarterly=20,
                     verbose=True):
    """对前 max_quarterly 只幸存者取三线单季同比序列并嵌入 quarterly 指标。
    出处:静水核心因子#1(增速≥40%)+核心α三特征之"持续超预期";
    单季掉线不剔除(若羽臣 24Q4 教训),只进排序权重。"""
    names = [c["name"] for c in survivors[:max_quarterly] if c.get("name")]
    if not names:
        return survivors
    if verbose:
        print(f"► 三线多季序列(前{len(names)}只:近四季单季 营收/归母/扣非 同比)...", file=sys.stderr)
    qs_map = fetch_quarterly_yoy(names, cli_path=cli_path)
    for c in survivors:
        qs = qs_map.get(c.get("name"))
        ana = analyze_quarterly(qs) if qs else None
        if qs:
            c["quarterly"] = {**qs, **ana}
        else:
            c["quarterly_missing"] = True
    return survivors


# ─── 主流程 ─────────────────────────────────────────────────

def run(roe_min=5, cli_path=DEFAULT_FINANCE_CLI, verbose=True,
        max_1yr_gain=30, max_3yr_gain=100, min_6mo_gain=10, max_position=0.75,
        max_quarterly=20):
    """L1 硬筛:CAN SLIM×静水字面法则(query)+ 严格早期价格带 + 扣非自过滤 + 排雷标记
    + 三线多季序列(排序加权不剔除)。"""
    if verbose:
        print(f"► 问财硬筛(严格早期:近1年<{max_1yr_gain}% 近3年<{max_3yr_gain}% "
              f"近6月>{min_6mo_gain}% 距ATH<{max_position*100:.0f}%)...", file=sys.stderr)
    raw = screen_step1(roe_min=roe_min, cli_path=cli_path,
                       max_1yr_gain=max_1yr_gain, max_3yr_gain=max_3yr_gain)
    if verbose:
        print(f"  硬筛命中 {len(raw)} 只", file=sys.stderr)

    survivors = []
    for c in raw:
        dp = c.get("deduct_profit")          # 静水·不亏损(扣非为正);数据缺失不误删
        if dp is not None and dp <= 0:
            continue
        if c.get("profit_growth") is None:   # C 要素数据缺失:标注不剔除,交 L2 记 gap
            c["c_data_missing"] = True
        # 注:抓"潜在10倍早期"——低基数暴增(净利>500%)是早期成长正常特征,不再剔除;
        # 周期/重组由 L2(行业)和 L3(F1 行业空间)甄别。
        c["traps"] = detect_traps(c)
        survivors.append(c)

    if verbose:
        print(f"  扣非为正 + 排雷后 {len(survivors)} 只", file=sys.stderr)

    # 历史股价过滤:剔周期 + 剔已涨高(距ATH) + 严格早期区间涨幅二次验证
    names = [c["name"] for c in survivors if c["name"]]
    if names:
        if verbose:
            print("► 历史股价过滤(剔周期/剔已涨高 + 近6月趋势 + 近1/3年涨幅二次验证)...", file=sys.stderr)
        ph_map = fetch_price_history(names, cli_path=cli_path)
        filtered = []
        for c in survivors:
            ph = ph_map.get(c["name"], {})
            label, reason = classify_growth_cyclic(ph, max_position=max_position)
            c["price_history"] = ph
            c["growth_cyclic"] = label
            c["growth_cyclic_reason"] = reason
            if label in ("cyclic", "high"):       # 周期股 + 已涨很高(非早期)一并剔除
                continue
            # 严格早期价格带·脚本二次验证(问财条件不可靠,这里兜底)
            g6 = ph.get("gain_6mo"); g1 = ph.get("gain_1yr"); g3 = ph.get("gain_3yr")
            drop = None
            if g6 is not None and g6 < min_6mo_gain:
                drop = f"近6月 {g6:.0f}%<{min_6mo_gain}%→趋势未起"
            elif g1 is not None and g1 > max_1yr_gain:
                drop = f"近1年 {g1:.0f}%>{max_1yr_gain}%→已大涨"
            elif g3 is not None and g3 > max_3yr_gain:
                drop = f"近3年 {g3:.0f}%>{max_3yr_gain}%→多倍股横盘"
            if drop:
                c["growth_cyclic"] = "dropped"
                c["growth_cyclic_reason"] = drop
                continue
            # 区间涨幅数据缺失 → 不误剔(问财批量返回不稳定),但标注提醒 L2/L3 人工核趋势
            missing = [k for k, v in [("近6月", g6), ("近1年", g1), ("近3年", g3)] if v is None]
            if missing:
                c["growth_cyclic_reason"] = reason + f" | 注:{'/'.join(missing)}涨幅数据缺失,趋势待 L2 人工核"
            filtered.append(c)
        survivors = filtered
        if verbose:
            growth_n = sum(1 for c in survivors if c.get("growth_cyclic") == "growth")
            print(f"  历史价过滤后 {len(survivors)} 只(成长早期 {growth_n} 只,已剔周期/已涨高/趋势未起)",
                  file=sys.stderr)

    # 三线多季序列(批2):排序加权(过线条数, 连续季数, 营收同比),不剔除
    survivors = attach_quarterly(survivors, cli_path=cli_path,
                                 max_quarterly=max_quarterly, verbose=verbose)

    survivors.sort(key=lambda c: (
        c.get("quarterly", {}).get("pass_lines", {}).get("count", 0),
        c.get("quarterly", {}).get("streak_max", 0),
        c.get("rev_growth") or 0,
    ), reverse=True)
    return survivors


def main():
    ap = argparse.ArgumentParser(description="tenscreener L1 问财初筛(严格早期十倍股)")
    ap.add_argument("--roe-min", type=float, default=5, help="ROE 下限%%(默认 5,早期容忍低 ROE)")
    ap.add_argument("--max-1yr-gain", type=float, default=30, help="近1年涨幅上限%%(默认 30,挡已大涨)")
    ap.add_argument("--max-3yr-gain", type=float, default=100, help="近3年涨幅上限%%(默认 100,挡多倍股高位横盘)")
    ap.add_argument("--min-6mo-gain", type=float, default=10, help="近6月涨幅下限%%(默认 10,要求趋势已起、走出底部)")
    ap.add_argument("--max-position", type=float, default=0.75, help="当前价/历史最高 上限(默认 0.75,挡追 ATH)")
    ap.add_argument("--max-quarterly", type=int, default=20, help="取前 N 只查三线多季序列(默认 20)")
    ap.add_argument("--cli-path", default=DEFAULT_FINANCE_CLI, help="hithink finance cli.py 路径")
    ap.add_argument("--out", help="输出 JSON 文件路径(默认打印 stdout)")
    ap.add_argument("--top", type=int, default=0, help="只保留前 N 只(0=全部)")
    args = ap.parse_args()

    survivors = run(roe_min=args.roe_min, cli_path=args.cli_path,
                    max_1yr_gain=args.max_1yr_gain, max_3yr_gain=args.max_3yr_gain,
                    min_6mo_gain=args.min_6mo_gain, max_position=args.max_position,
                    max_quarterly=args.max_quarterly)
    if args.top > 0:
        survivors = survivors[:args.top]

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(survivors, f, ensure_ascii=False, indent=2)
        print(f"候选池 {len(survivors)} 只 → {args.out}", file=sys.stderr)
    else:
        print(json.dumps(survivors, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
