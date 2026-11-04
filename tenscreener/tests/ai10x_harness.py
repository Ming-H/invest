#!/usr/bin/env python3
"""
AI 十倍股 ground-truth 验收 harness · tenscreener loop

目标(用户设定):runner 能识别 2024 年至今 AI 泛科技板块【全部】十倍股。

流程:
  1) 候选池(静水 2024-2026 主线:光模块/PCB/算力/半导体/存储/铜连接/AIDC/液冷/电源)
  2) 腾讯K线筛 10x:max(2024-01以来最高)/min(2024-01以来最低) ≥ multiple_th(默认10)
  3) 对每只 ground-truth 跑 runner.backtest(东财三线+预告) → 首亮/其后涨幅
  4) 记分板 docs/ai10x-scoreboard.md + 断点 JSON outputs/ai10x/progress.json

免额度(腾讯+东财),不耗问财。断点续跑:重复执行只补未完成项。
用法:python3 tests/ai10x_harness.py [--multiple 10] [--only-failed]
"""

import argparse
import json
import os
import sys
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
SK = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(SK, "scripts"))

import kline
import emoney
import runner as R

PROG_DIR = os.path.join(SK, "outputs", "ai10x")
SCOREBOARD = os.path.join(SK, "docs", "ai10x-scoreboard.md")

# 候选池:AI 泛科技主线(静水"2024-2026 AI 泛科技:光模块、PCB、算力、半导体设备、存储")
# 按子板块分组;持续可补充(下轮 loop 发现漏的加进来)
CANDIDATES = {
    "光模块": [("300308", "中际旭创"), ("300502", "新易盛"), ("300394", "天孚通信"),
               ("603083", "剑桥科技"), ("300570", "太辰光"), ("688205", "德科立"),
               ("688498", "源杰科技"), ("301205", "联特科技"), ("300620", "光库科技"),
               ("688313", "仕佳光子"), ("002902", "铭普光磁"), ("000988", "华工科技")],
    "PCB": [("300476", "胜宏科技"), ("688183", "生益电子"), ("002463", "沪电股份"),
            ("002916", "深南电路"), ("002436", "兴森科技")],
    "算力/服务器": [("601138", "工业富联"), ("600498", "烽火通信"), ("000938", "紫光股份"),
                  ("000063", "中兴通讯"), ("000977", "浪潮信息"), ("000034", "神州数码"),
                  ("002261", "拓维信息")],
    "AI芯片": [("688256", "寒武纪"), ("688041", "海光信息"), ("300474", "景嘉微")],
    "存储": [("301308", "江波龙"), ("688525", "佰维存储"), ("001309", "德明利"),
             ("300475", "香农芯创")],
    "半导体设备/材料": [("002371", "北方华创"), ("688012", "中微公司"), ("688072", "拓荆科技")],
    "铜连接/连接器": [("002130", "沃尔核材"), ("600577", "精达股份"), ("300913", "兆龙互连"),
                    ("300979", "华丰科技"), ("688668", "鼎通科技")],
    "AIDC/液冷/电源": [("300442", "润泽科技"), ("002837", "英维克"), ("300383", "光环新网"),
                      ("300748", "奥飞数据"), ("002197", "欧陆通")],
    "PCB二线": [("002384", "东山精密"), ("300929", "奥士康"), ("603228", "景旺电子"),
              ("300814", "中富电路"), ("300852", "四会富仕"), ("300739", "明阳电路")],
    "算力租赁/边缘": [("603496", "恒为科技"), ("603220", "中贝通信"), ("603629", "利通电子"),
                  ("002229", "鸿博股份"), ("300846", "首都在线")],
    "半导体设计/材料": [("688521", "芯原股份"), ("688008", "澜起科技"), ("002409", "雅克科技"),
                    ("688300", "联瑞新材"), ("688535", "华海诚科"), ("688362", "甬矽电子"),
                    ("002156", "通富微电")],
    "散热/液冷/电源": [("300563", "神宇股份"), ("300499", "高澜股份"), ("301018", "申菱环境"),
                    ("300602", "飞荣达"), ("002335", "科华数据")],
    "光器件二线": [("300757", "罗博特科"), ("301183", "东田微"), ("688195", "腾景科技"),
                 ("002281", "光迅科技"), ("600105", "永鼎股份"), ("688048", "长光华芯")],
    "端侧AI/其他": [("603893", "瑞芯微"), ("688343", "云天励飞"), ("688559", "海目星"),
                  ("603881", "数据港"), ("603283", "赛腾股份")],
    "先进封装/设备": [("688403", "汇成股份"), ("688352", "颀中科技"), ("301200", "大族数控"),
                   ("688630", "芯碁微装"), ("688596", "正帆科技"), ("603690", "至纯科技"),
                   ("300260", "新莱应材"), ("688409", "富创精密")],
    "光学/激光": [("688025", "杰普特"), ("301421", "波长光电"), ("688502", "茂莱光学"),
                ("002222", "福晶科技")],
    "端侧SoC": [("688608", "恒玄科技"), ("688018", "乐鑫科技"), ("300458", "全志科技"),
               ("688099", "晶晨股份")],
    "存储二线": [("300302", "同有科技"), ("688110", "东芯股份")],
    "算力整机/租赁二线": [("603118", "共进股份"), ("600839", "四川长虹"), ("000628", "高新发展"),
                     ("600602", "云赛智联"), ("002929", "润建股份")],
    "液冷/电源二线": [("300684", "中石科技"), ("002518", "科士达"), ("002851", "麦格米特")],
    "连接器二线": [("002897", "意华股份")],
    "应用端探针": [("300418", "昆仑万维")],
    "PCB材料/设备三线": [("002636", "金安国纪"), ("301041", "金百泽"), ("002815", "崇达技术"),
                    ("688700", "东威科技")],
    "HVDC/液冷长尾": [("002364", "中恒电气"), ("300249", "依米康"), ("603912", "佳力图"),
                   ("300709", "精研科技")],
    "芯片长尾": [("688702", "盛科通信"), ("688515", "裕太微"), ("688332", "中科蓝讯"),
              ("300604", "长川科技"), ("688167", "炬光科技")],
    "IDC/封测长尾": [("002195", "岩山科技"), ("600589", "大位科技"), ("688158", "优刻得"),
                  ("002077", "大港股份"), ("000021", "深科技")],
    "主板查漏": [("300548", "博创科技"), ("688127", "蓝特光学"), ("000563", "汇源通信"),
               ("600183", "生益科技")],
    "北交所AI": [("839493", "并行科技"), ("430139", "华岭股份"), ("835207", "众诚科技")],
}

BEG, END = "2024-01-01", "2026-08-30"


def symbol_of(code):
    c = str(code)
    if c.startswith(("4", "8")):
        return "bj" + c            # 北交所(新浪源)
    return ("sh" if c.startswith(("6", "9")) else "sz") + c


def screen_multiple(rows, beg=BEG):
    """2024-01 以来的 最高/最低 倍数(用盘中高低点)。"""
    sub = [r for r in rows if r[0] >= beg]
    if len(sub) < 50:
        return None
    hi = max(float(r[3]) for r in sub)
    lo = min(float(r[4]) for r in sub)
    return hi / lo if lo > 0 else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--multiple", type=float, default=10.0)
    ap.add_argument("--only-failed", action="store_true")
    args = ap.parse_args()

    os.makedirs(PROG_DIR, exist_ok=True)
    prog_path = os.path.join(PROG_DIR, "progress.json")
    prog = {"stocks": {}}
    if os.path.exists(prog_path):
        prog = json.load(open(prog_path))

    flat = [(c, n, sec) for sec, lst in CANDIDATES.items() for c, n in lst]
    # 全市场枚举并集(2024以来任一期双线>40,tests/ai10x_enumerate.py 生成)
    enum_path = os.path.join(PROG_DIR, "enum_universe.json")
    if os.path.exists(enum_path):
        seen = {c for c, _, _ in flat}
        for r in json.load(open(enum_path, encoding="utf-8")):
            if r["code"] not in seen:
                flat.append((r["code"], r["name"], "全市场枚举"))
    print(f"候选池 {len(flat)} 只(含枚举) | 阈值 {args.multiple}x | 断点 {len(prog['stocks'])} 只已处理")

    idx = kline.fetch_qfq("sh000001", "2023-01-01", END)
    gates = kline.index_gate_series(idx)

    gt = []          # ground truth
    for code, name, sec in flat:
        key = code
        done = prog["stocks"].get(key, {})
        try:
            if not done.get("rows"):
                rows = kline.fetch_qfq(symbol_of(code), "2023-01-01", END)
                mult = screen_multiple(rows)
                done["rows"], done["mult"] = True, round(mult, 2) if mult else None
                prog["stocks"][key] = done
                json.dump(prog, open(prog_path, "w"), ensure_ascii=False)
            rows = kline.fetch_qfq(symbol_of(code), "2023-01-01", END)  # 缓存命中
            if (done.get("mult") or 0) >= args.multiple:
                gt.append((code, name, sec, done["mult"]))
                prev_v = (done.get("backtest") or {}).get("verdict")
                if not done.get("backtest") or (args.only_failed and prev_v not in ("PASS",)):
                    pack = {"yoy": None, "periods": emoney.f10_periods(code),
                            "predicts": emoney.predicts(code)}
                    pack["yoy"] = emoney.yoy_from_periods(pack["periods"])
                    tl = R.backtest(code, name, rows, pack["yoy"], gates, BEG, END,
                                    periods=pack["periods"], predicts=pack["predicts"])
                    lit = [t for t in tl if t["phase"] in
                           ("主升期", "强势待确认", "回调击球区", "预告爆发")]
                    # 叙事先行自证:亮灯时点(或全程)之前从无"营收+归母双过40"季
                    # → 主升发生在业绩可验证之前,框架字面(5不买#1/#2)不可跟 → NARRATIVE
                    light_q = (lit[0]["disclosed"] if lit else None)
                    double_pass = [q for q, (r, pp, _) in pack["yoy"].items()
                                   if q >= BEG[:4]                       # 只看本次行情窗口
                                   and (r or 0) >= 40 and (pp or 0) >= 40
                                   and (light_q is None or q <= light_q)]
                    if lit:
                        f = lit[0]
                        entry = float(next(r[2] for r in rows if r[0] == f["asof"]))
                        hi = max(float(r[3]) for r in rows if r[0] >= f["asof"])
                        up = (hi / entry - 1) * 100
                        # 达标口径:首亮在主升中前段(涨幅进度≤70% of peak mult)且其后≥50%
                        progress = f["run_multiple"] / done["mult"]
                        verdict = "PASS" if (progress <= 0.7 and up >= 50) else \
                                  ("LATE" if up >= 50 else
                                   ("NARRATIVE" if not double_pass else "MISS"))
                        done["backtest"] = {"first": f["month"], "phase": f["phase"],
                                            "at": f["run_multiple"], "up_after": round(up),
                                            "progress": round(progress, 2), "verdict": verdict}
                    else:
                        done["backtest"] = {"first": None,
                                            "verdict": "NARRATIVE" if not double_pass else "MISS"}
                    prog["stocks"][key] = done
                    json.dump(prog, open(prog_path, "w"), ensure_ascii=False)
        except Exception as e:
            done["error"] = f"{type(e).__name__}: {e}"
            prog["stocks"][key] = done
            print(f"  [err] {name}: {e}")

    # 记分板
    lines = [f"# AI 十倍股识别记分板(2024-01~{END})\n",
             f"> 阈值:2024 以来最高/最低 ≥{args.multiple}x | 生成:{date.today().isoformat()}",
             f"> 达标=首亮时进度≤70%(没等到尾段)且其后≥+50% | LATE=亮了但只剩<50% | MISS=未亮\n"]
    n_pass = n_late = n_miss = 0
    for code, name, sec, mult in sorted(gt, key=lambda x: -x[3]):
        b = prog["stocks"][code].get("backtest") or {}
        v = b.get("verdict", "?")
        n_pass += v == "PASS"; n_late += v == "LATE"; n_miss += v == "MISS"
        first = b.get("first") or "-"
        lines.append(f"| {name}({code}) | {sec} | {mult}x | {first} | "
                     f"{b.get('phase', '-') or '-'} | {b.get('at', '-')}x | "
                     f"{b.get('progress', '-')} | +{b.get('up_after', '-')}% | **{v}** |")
    lines.insert(3, f"\n**{len(gt)} 只 ground-truth | PASS {n_pass} / LATE {n_late} / MISS {n_miss}"
                    f" | 达标率 {n_pass}/{len(gt)}**\n")
    lines.insert(4, "| 股票 | 板块 | 实际倍数 | 首亮 | 阶段 | 当时 | 进度 | 其后 | 判定 |")
    lines.insert(5, "|---|---|---|---|---|---|---|---|---|")
    with open(SCOREBOARD, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"\nground-truth {len(gt)} 只 | PASS {n_pass} / LATE {n_late} / MISS {n_miss}")
    print(f"记分板 → {SCOREBOARD}")
    for code, name, sec, mult in sorted(gt, key=lambda x: -x[3]):
        b = prog["stocks"][code].get("backtest") or {}
        print(f"  {b.get('verdict','?'):5} {name:8} {mult:6.1f}x 首亮{b.get('first') or '-'}")


if __name__ == "__main__":
    main()
