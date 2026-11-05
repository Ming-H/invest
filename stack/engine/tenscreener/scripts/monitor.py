#!/usr/bin/env python3
"""
生命周期监测 · tenscreener

watch(右侧确认)与 track(持有期退出)的判定核心。四块:
  market_gate     大盘 β 三态 on/weak/off(CAN SLIM M"确认上升市再买";
                  静水"一年十倍=宏观放水+主线α+业绩爆发" —— 24/9/24 放水行情即此环)
  price_position  距一年新高 pos_1y(静水5不买#3"股价没创一年新高不买"的工程化;
                  避开问财"是否创一年新高"返回0条的坑:查近一年最高价自算)
  confirm_check   scouted→confirmed 确认三条件 R1持续超预期+R2创新高+R3大盘on,R4证伪
  exit_check      holding 退出监测 E1增速降档+E2扣非背离+E3回撤裁定(回撤触发裁定而非卖出:
                  静水"常经历2次30-40%中期调整,单票可能腰斩,靠格局扛住"vs"基本面转折才砍")

所有阈值为量化近似(无原文数值),默认值可经 CLI 参数调整。
纯标准库。
"""

from wencai import iwencai, _pick, _num, DEFAULT_ZHISHU_CLI, DEFAULT_FINANCE_CLI
from quarters import (pass_lines, pass_streak, downshift, divergence,
                      _label_sortkey as l_sortkey, PASS_LINE)


# ─── 大盘 β ─────────────────────────────────────────────────

def fetch_index_snapshot(cli_path=DEFAULT_ZHISHU_CLI):
    """上证指数一次查询(当日缓存)。实测返回 key 形态:
    最新价 / ma250[YYYYMMDD] / 涨跌幅[YYYYMMDD-YYYYMMDD](近一年区间),
    故用 _pick 模糊 + 区间涨跌幅正则解析,不依赖精确字段名。"""
    query = "上证指数 最新点位 250日均线 近一年涨跌幅"
    datas = iwencai(query, limit=5, cli_path=cli_path)
    if not datas:
        return {}
    x = datas[0]
    from l1_screener import _parse_range_gains
    gains = _parse_range_gains(x)
    return {
        "index_close": _pick(x, "最新价", "收盘价", "最新点位"),
        "ma250": _pick(x, "ma250", "250日均线", "年线"),
        "index_gain_1y": _pick(x, "近一年涨跌幅") or gains.get("gain_1yr"),
    }


def market_gate(snapshot=None, cli_path=DEFAULT_ZHISHU_CLI):
    """大盘 β 三态:on(可确认买点)/weak(α孤军,等待)/off(不确认)。
    优先 250日均线(close≥ma→on);均线不可得用 proxy:近一年涨幅>0→weak 以上,
    涨幅>0 视为 weak/on、≤0 为 off。method 字段如实标注,不伪造。"""
    s = snapshot if snapshot is not None else fetch_index_snapshot(cli_path)
    if not s:
        return {"verdict": "unknown", "method": "no_data",
                "note": "zhishu 查询无返回(额度或网络),R3 无法判定"}
    close, ma = s.get("index_close"), s.get("ma250")
    if close and ma and ma > 0:
        verdict = "on" if close >= ma else "off"
        return {"verdict": verdict, "method": "ma250", **s}
    gain = s.get("index_gain_1y")
    if gain is not None:
        verdict = "weak" if gain > 0 else "off"
        return {"verdict": verdict, "method": "proxy", **s}
    return {"verdict": "unknown", "method": "no_data", **s}


# ─── 价格位置 ───────────────────────────────────────────────

def price_position(names, cli_path=DEFAULT_FINANCE_CLI, batch=6):
    """批量查 最新价/近一年最高价(前复权) → {name: {last, high_1y, pos_1y}}。
    问财坑(2026-08-30 实测):「近一年最高价」对个股会返回**每日最高价序列**
    (最高价[YYYYMMDD]/最高价_前复权[YYYYMMDD] ×365 个字段)而非区间最高点
    → 解析兜底:序列取 max 即近一年最高。"""
    import re as _re
    result = {}
    for i in range(0, len(names), batch):
        chunk = names[i:i + batch]
        query = " ".join(chunk) + " 近一年最高价 前复权 最新价"
        datas = iwencai(query, limit=len(chunk) + 4, cli_path=cli_path)
        for x in datas:
            nm = x.get("股票简称")
            if not nm:
                continue
            high = _pick(x, "近一年最高价前复权", "近一年最高价", "近一年最高点")
            if high is None:                     # 兜底:每日最高价序列取 max
                nums = [_num(v) for k, v in x.items()
                        if _re.match(r"^最高价(_前复权)?\[", str(k))]
                nums = [n for n in nums if n]
                if nums:
                    high = max(nums)
            last = _pick(x, "最新价", "收盘价_前复权", "收盘价")
            result[nm] = {
                "last": last, "high_1y": high,
                "pos_1y": (last / high) if (last and high and high > 0) else None,
            }
    return result


# ─── 确认(scouted → confirmed) ──────────────────────────────

def confirm_check(item, qs, pos, gate, confirm_pos=0.95, line=PASS_LINE,
                  market_gate_on=True):
    """右侧确认三条件(全部满足→confirmed;任一缺→waiting+missing;R4→invalidated):
      R1 持续超预期:最新披露季三线≥2线 yoy≥line(静水核心α"持续超预期";若羽臣 24Q3 后一季即启启动)
          任一线 streak≥2 → 加注"连续验证(资金合力)"(若羽臣 25/4 年报+一季报连续过线→主升加速)
      R2 创一年新高:pos_1y≥confirm_pos(静水5不买#3;CAN SLIM N)
      R3 大盘 β:gate verdict=on(CAN SLIM M;静水"宏观放水")
      R4 证伪:最近两个披露期三线全 <line(静水"等财报验证成长仍在持续"之反向)
    返回 {result, signals, missing}。"""
    signals, missing = [], []
    if not qs or not qs.get("latest_period"):
        return {"result": "waiting", "signals": [], "missing": ["三线同比序列数据缺失"]}

    latest = qs["latest_period"]
    pl = pass_lines(qs, line)

    # R4 证伪(优先判定):最近两个【有数据】披露期三线全不过线(缺失季不算fail)
    def _period_all_fail(p):
        vals = [v for v in (qs.get(k, {}).get(p)
                            for k in ("rev_yoy", "parent_yoy", "deduct_yoy"))
                if v is not None]
        if not vals:
            return None                       # 该季无数据,不参与证伪判定
        return all(v < line for v in vals)

    data_periods = [p for p in qs.get("periods", [])
                    if any(qs.get(k, {}).get(p) is not None
                           for k in ("rev_yoy", "parent_yoy", "deduct_yoy"))][-2:]
    fails = [_period_all_fail(p) for p in data_periods]
    if len(fails) >= 2 and all(v is True for v in fails):
        return {"result": "invalidated",
                "signals": [f"证伪:近{len(data_periods)}个披露期({data_periods[0]}~"
                            f"{data_periods[-1]})三线同比全部<{line:.0f}%(成长未获财报验证)"],
                "missing": []}

    # R1 持续超预期
    if pl["count"] >= 2:
        sig = f"R1 持续超预期:{'+'.join(pl['labels'])}单季同比≥{line:.0f}% @{latest}"
        streaks = {k: pass_streak(qs.get(k, {}), line)
                   for k in ("rev_yoy", "parent_yoy", "deduct_yoy")}
        if max(streaks.values()) >= 2:
            best = max(streaks, key=streaks.get)
            sig += f",其中{ {'rev_yoy':'营收','parent_yoy':'归母','deduct_yoy':'扣非'}[best] }连续{streaks[best]}季过线(连续验证,资金合力)"
        signals.append(sig)
    else:
        missing.append(f"R1 持续超预期:最新季{latest}仅{pl['count']}/3线过线(需≥2)")

    # R2 创一年新高
    p = (pos or {}).get("pos_1y") if pos else None
    if p is not None and p >= confirm_pos:
        signals.append(f"R2 创一年新高:现价/一年最高={p:.2f}≥{confirm_pos}")
    else:
        missing.append(f"R2 创一年新高:pos_1y={'缺失' if p is None else f'{p:.2f}'}<{confirm_pos}(还没'看见')")

    # R3 大盘 β
    if market_gate_on:
        v = (gate or {}).get("verdict")
        if v == "on":
            signals.append(f"R3 大盘β:on({(gate or {}).get('method')})")
        elif v == "weak":
            missing.append("R3 大盘β:weak(α孤军,等大盘转on)")
        else:
            missing.append(f"R3 大盘β:{v or 'unknown'}(无法确认)")
    else:
        signals.append("R3 大盘β:已用 --no-market-gate 跳过")

    ok = not missing
    return {"result": "confirmed" if ok else "waiting",
            "signals": signals, "missing": missing}


# ─── 退出(holding → exit_signal) ────────────────────────────

def exit_check(item, qs, pos, peak, downshift_pp=30.0, drawdown_th=0.40,
               line=PASS_LINE):
    """持有期退出监测,聚合 level: ok/warn/exit。
      E1 增速降档:最新季较上季 yoy 降≥pp→warn;连续2季降 或 连续2季破线→exit
         (静水"退出信号:行业/标的增速跟不上""直接用眼睛看增速还在不在";
          若羽臣 25Q3 +123%→26Q1 +73% 连续降档,股价先行腰斩)
      E2 扣非/归母背离:flag→warn+需Claude拆解非经营性贡献(昭衍新药例)
      E3 回撤裁定:drawdown≥th 不独立卖出 —— 基本面仍过线→warn(静水中期调整纪律:扛住);
         基本面 fail→exit(基本面转折才砍)。
         (若羽臣 25/10→26/6 回撤49%且增速降档→exit)
    返回 {level, reasons, fundamentals_pass, drawdown}。"""
    reasons = []
    level = "ok"
    pl = pass_lines(qs, line) if qs and qs.get("latest_period") else {"count": 0}
    latest = qs.get("latest_period") if qs else None

    # E1 增速降档(基准线:营收+扣非,归母受非经常性扰动)
    e1_exit = False
    for label, key in (("营收", "rev_yoy"), ("扣非", "deduct_yoy")):
        ds = downshift(qs.get(key, {}) if qs else {}, downshift_pp)
        if ds["flag"]:
            if ds["consecutive"] >= 2:
                e1_exit = True
                reasons.append(f"E1 增速降档:{label}同比最新降{ds['shift']}pp,连续{ds['consecutive']}季降≥{downshift_pp:.0f}pp→exit")
            else:
                reasons.append(f"E1 增速降档:{label}同比最新降{ds['shift']}pp≥{downshift_pp:.0f}pp@{latest}→warn")
    # 连续2季跌破40线(降档的另一形态)
    if qs and not e1_exit:
        for label, key in (("营收", "rev_yoy"), ("扣非", "deduct_yoy")):
            series = qs.get(key, {})
            vals = [(l, v) for l, v in sorted(series.items(), key=lambda kv: l_sortkey(kv[0]))
                    if v is not None]
            if len(vals) >= 2 and all(v < line for _, v in vals[-2:]):
                e1_exit = True
                reasons.append(f"E1 增速降档:{label}同比连续2季<{line:.0f}%({vals[-2][0]}:{vals[-2][1]:.0f}%,{vals[-1][0]}:{vals[-1][1]:.0f}%)→exit")

    # E2 扣非/归母背离
    if qs:
        dv = divergence(qs.get("parent_yoy", {}), qs.get("deduct_yoy", {}))
        if dv["flag"]:
            reasons.append(f"E2 扣非/归母背离 gap={dv['gaps'].get(dv['at'])}pp@{dv['at']}→warn,需Claude拆解非经营性贡献")

    # E3 回撤裁定
    last = (pos or {}).get("last") if pos else None
    drawdown = (1 - last / peak) if (last and peak and peak > 0) else None
    fundamentals_pass = (not e1_exit) and pl["count"] >= 2
    e3_exit = False
    if drawdown is not None and drawdown >= drawdown_th:
        if fundamentals_pass:
            reasons.append(f"E3 回撤{drawdown*100:.0f}%≥{drawdown_th*100:.0f}%但三线{pl['count']}/3仍过线→中期调整裁定:静水纪律为扛住(30-40%调整常见),warn观察")
        else:
            e3_exit = True
            reasons.append(f"E3 回撤{drawdown*100:.0f}%≥{drawdown_th*100:.0f}%且基本面已不过线(过线{pl['count']}/3)→基本面转折,exit")

    level = "exit" if (e1_exit or e3_exit) else ("warn" if reasons else "ok")
    return {"level": level, "reasons": reasons,
            "fundamentals_pass": fundamentals_pass,
            "drawdown": round(drawdown, 3) if drawdown is not None else None}


