#!/usr/bin/env python3
"""
TPS 十倍潜力评分模型 · tenscreener 核心

十倍潜力分(Tenbag Potential Score, 0-100) = 七因子加权:
  F1 行业空间    30%   TAM / 渗透率 / 景气开端      (Claude 定性输入 0-100)
  F2 终点可达性  20%   市值×10 的行业对标           (脚本可算)
  F3 市值(非线性)15%   100·exp(-市值/800),减分不否决  (脚本算)
  F4 成长性      15%   营收/净利增速及加速           (脚本算)
  F5 催化        10%   新品/供不求/出海/周期开端     (Claude 定性输入 0-100)
  F6 生意模式     5%   可扩张/护城河/定价权          (Claude 定性输入 0-100)
  F7 估值         5%   PEG/PS(10倍股早期允许偏高)   (脚本算)

设计哲学:大市值不被硬卡,而是被 F3 自然压低;
只有 F1(空间)极高(时代级浪潮)时,大市值才能靠 F1 翻盘。
纯 Python3 标准库,无第三方依赖。
"""

import math

# 七因子权重(合计 1.0)
WEIGHTS = {
    "F1": 0.30,  # 行业空间(最重要,10倍根因)
    "F2": 0.20,  # 终点可达性
    "F3": 0.15,  # 市值(非线性减分)
    "F4": 0.15,  # 成长性
    "F5": 0.10,  # 催化
    "F6": 0.05,  # 生意模式
    "F7": 0.05,  # 估值
}

# 市值衰减常数(亿元):exp(-市值/800),约 800 亿处腰斩、2400 亿处降到 5%
MARKET_CAP_DECAY = 800.0


def clamp(v, lo=0.0, hi=100.0):
    if v is None:
        return 50.0  # 缺口给中位,不偏不倚
    try:
        return max(lo, min(hi, float(v)))
    except (TypeError, ValueError):
        return 50.0


# ─── 各因子打分函数 ─────────────────────────────────────────

def f3_market_cap(market_cap_yi):
    """
    F3 市值非线性减分(0-100)。market_cap_yi 单位:亿元。
    ≤100亿≈95, 300亿≈69, 800亿≈37, 1500亿≈15, 3000亿≈2。
    减分而非否决:大市值仍有分,只是低。
    """
    if market_cap_yi is None or market_cap_yi <= 0:
        return 50.0
    return clamp(100.0 * math.exp(-market_cap_yi / MARKET_CAP_DECAY))


def f4_growth(rev_growth_latest, rev_growth_prev=None, profit_growth_latest=None,
              streak=0, lines_passed=0):
    """
    F4 成长性(0-100):
      增速水平(0-60)  取 max(营收,净利)同比分档(≥80→60/≥50→50/≥40→42/≥30→32/≥15→20/>0→10)
                      —— 40 档即静水核心因子#1"营收、利润增速≥40%"
      加速(0-15)      最新 > 上季/上年 → 15;prev 缺失 → 8(中位,不白给);减速 → 4
                      —— 十倍股画像"业绩拐点加速:从低速→高速增长,且增速提升"
      持续性(0-15)    连续过线季数 streak≥3→15/==2→12/==1→8/0→3;
                      lines_passed<2(最新季三线仅0-1线过)时该项×0.6
                      —— 静水核心α三特征之"持续超预期";单线孤立打折(若羽臣24Q4教训:
                         营收+14%掉线但利润+133%过线 → 序列共振比单点更可信,孤立单点要打折)
      净利同步(0-10)  净利>0 且 ≥营收增速一半 → 10;净利>0 → 6
    streak/lines_passed 由 quarters.analyze_quarterly 提供;缺序列时退化为旧两参数行为。
    """
    score = 0.0
    rev_g = rev_growth_latest or 0
    pf = profit_growth_latest or 0
    g = max(rev_g, pf)
    # 增速水平(0-60)
    if g >= 80:
        score += 60
    elif g >= 50:
        score += 50
    elif g >= 40:
        score += 42
    elif g >= 30:
        score += 32
    elif g >= 15:
        score += 20
    elif g > 0:
        score += 10
    # 加速(0-15)
    if rev_growth_prev is not None:
        score += 15 if rev_g > rev_growth_prev else 4
    else:
        score += 8                            # prev 缺失给中位(修复旧代码恒5)
    # 持续性(0-15)
    if streak >= 3:
        persist = 15
    elif streak == 2:
        persist = 12
    elif streak == 1:
        persist = 8
    else:
        persist = 3
    if lines_passed is not None and lines_passed < 2:
        persist *= 0.6                        # 单线孤立打折
    score += persist
    # 净利同步(0-10)
    if pf > 0:
        score += 10 if pf >= rev_g / 2 else 6
    return clamp(score)


def f2_endpoint_reach(market_cap_yi, sector_max_cap_yi=None):
    """
    F2 终点可达性(0-100):市值×10 的终点,行业/市场有无公司达到过。
    sector_max_cap_yi: 同行业最大市值(亿元),用于精确对标;None 则用 A 股分档粗估。
    """
    if market_cap_yi is None or market_cap_yi <= 0:
        return 50.0
    target = market_cap_yi * 10  # 10 倍后的终点市值

    if sector_max_cap_yi and sector_max_cap_yi > 0:
        ratio = sector_max_cap_yi / target  # 行业龙头市值 / 目标终点
        if ratio >= 1.0:
            return 90.0   # 行业内已有公司达到过 → 终点合理可达
        elif ratio >= 0.5:
            return 70.0
        elif ratio >= 0.2:
            return 40.0
        else:
            return 15.0   # 远超行业天花板

    # 无行业对标,A 股分档粗估
    if target <= 500:
        return 85.0     # 终点 <500 亿,极易达
    elif target <= 2000:
        return 65.0
    elif target <= 5000:
        return 45.0
    elif target <= 10000:
        return 25.0
    else:
        return 10.0     # 终点 >万亿,仅茅台/工行级,极难


def f7_valuation(pe_ttm=None, peg=None):
    """
    F7 估值(0-100):PEG 优先,否则 PE。10 倍股早期估值偏高可接受,故权重仅 5%。
    """
    if peg is not None and peg > 0:
        if peg <= 0.8:
            return 90.0
        elif peg <= 1.2:
            return 70.0
        elif peg <= 2.0:
            return 50.0
        else:
            return 25.0
    if pe_ttm and pe_ttm > 0:
        if pe_ttm <= 20:
            return 75.0
        elif pe_ttm <= 40:
            return 55.0
        elif pe_ttm <= 80:
            return 35.0
        else:
            return 20.0
    return 40.0  # 数据缺口


# ─── 综合 TPS ───────────────────────────────────────────────

def compute_tps(factors):
    """
    factors: dict,键 F1-F7,值 0-100。
      F1/F5/F6 由 Claude 定性给出;F2/F3/F4/F7 由上面函数算后传入(或直接给值)。
    返回 (tps_total, detail_dict)。
    """
    detail = {}
    tps = 0.0
    for k, w in WEIGHTS.items():
        v = clamp(factors.get(k))
        detail[k] = round(v, 1)
        tps += w * v
    return round(tps, 1), detail


def tps_grade(tps):
    """TPS 分档。"""
    if tps >= 80:
        return "S · 极具 10 倍潜力"
    elif tps >= 65:
        return "A · 高潜力"
    elif tps >= 50:
        return "B · 中等潜力"
    elif tps >= 35:
        return "C · 潜力有限"
    else:
        return "D · 10 倍空间不足"


FACTOR_LABELS = {
    "F1": "行业空间(30%)",
    "F2": "终点可达(20%)",
    "F3": "市值衰减(15%)",
    "F4": "成长性(15%)",
    "F5": "催化(10%)",
    "F6": "生意模式(5%)",
    "F7": "估值(5%)",
}


if __name__ == "__main__":
    # 自测:模拟一只小盘高成长创新药
    demo = {
        "F1": 85,  # 行业空间大(创新药出海)
        "F2": f2_endpoint_reach(184, sector_max_cap_yi=2200),  # 艾力斯式:184亿×10=1840,医药龙头2200亿
        "F3": f3_market_cap(184),
        "F4": f4_growth(45.8, 76.3, 53.1),  # 艾力斯式增速(注意是减速,但绝对高)
        "F5": 80,  # 强催化
        "F6": 75,  # 好生意
        "F7": f7_valuation(pe_ttm=49),
    }
    tps, detail = compute_tps(demo)
    print(f"TPS = {tps}  [{tps_grade(tps)}]")
    for k, v in detail.items():
        print(f"  {FACTOR_LABELS[k]:18} {v:5.1f}")
