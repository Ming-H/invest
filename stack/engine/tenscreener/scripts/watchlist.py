#!/usr/bin/env python3
"""
watchlist 状态存储 + 生命周期状态机 · tenscreener

状态机(非法迁移抛错;save 前自动 .bak 备份):
    scouted ──confirm(脚本自动)──► confirmed ──promote(人工)──► holding
       ▲  │                          │ │                        │ ▲
       │  └──invalidated(脚本自动)──►closed◄─invalidated/清仓──┘ │
       │            (confirmed→scouted=人工降级重观察)│            │
       └──────────── reopen(人工) ◄──────────────┘               │
                                  ▲                               │
                                  └──exit_signal(自动告警)──人工否决keep──┘

纪律:脚本只自动迁移 confirm(右侧确认)与 invalidated(证伪);
     holding 的进入(promote)与退出(close)永远人工执行 —— 不荐股、不给买卖点。
"""

import json
import os
import shutil
from datetime import date

SKILL_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_PATH = os.path.join(SKILL_ROOT, "outputs", "watchlist.json")

STATES = ("scouted", "confirmed", "holding", "exit_signal", "closed")

# 合法边:(from, to)
TRANSITIONS = {
    ("scouted", "confirmed"),      # confirm_check 全过(脚本自动)
    ("scouted", "closed"),         # 证伪 R4(脚本自动)
    ("confirmed", "holding"),      # 人工建仓(track promote)
    ("confirmed", "closed"),       # 人工移除/证伪
    ("confirmed", "scouted"),      # 人工降级重观察(watch reopen, 2026-11-07 补)
    ("holding", "exit_signal"),    # exit_check level=exit(自动告警,非指令)
    ("exit_signal", "holding"),    # 人工否决告警,继续持有(track keep)
    ("exit_signal", "closed"),     # 人工清仓
    ("holding", "closed"),         # 人工清仓
    ("closed", "scouted"),         # 人工重开观察(watch reopen)
}


class IllegalTransition(Exception):
    pass


def assert_transition(cur, nxt):
    if cur == nxt:
        return
    if (cur, nxt) not in TRANSITIONS:
        raise IllegalTransition(f"非法状态迁移:{cur} → {nxt}(合法边见 TRANSITIONS)")


def new_item(code, name, note=None, added_from=None, context=None):
    return {
        "code": str(code), "name": name, "state": "scouted",
        "added_at": date.today().isoformat(),
        "added_from": added_from or "manual",
        "entry_context": context or {},
        "last_checked": None, "last_period_seen": None,
        "confirm": {}, "track": {},
        "price_log": [], "history": [], "notes": [note] if note else [],
    }


def load(path=DEFAULT_PATH):
    if not os.path.exists(path):
        return {"version": 1, "updated": None, "items": []}
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if data.get("version") != 1:
        raise ValueError(f"watchlist 版本不支持:{data.get('version')}(期望 1)")
    for it in data.get("items", []):
        if it.get("state") not in STATES:
            raise ValueError(f"watchlist 含未知状态:{it.get('state')} @ {it.get('code')}")
    return data


def save(wl, path=DEFAULT_PATH):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.exists(path):               # 备份后再写
        shutil.copy2(path, path + ".bak")
    wl["updated"] = date.today().isoformat()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(wl, f, ensure_ascii=False, indent=2)


def find(wl, code):
    for it in wl["items"]:
        if it["code"] == str(code):
            return it
    return None


def add(wl, item):
    if find(wl, item["code"]):
        raise ValueError(f"{item['code']} 已在 watchlist")
    wl["items"].append(item)
    return item


def remove(wl, code):
    it = find(wl, code)
    if not it:
        raise ValueError(f"{code} 不在 watchlist")
    wl["items"].remove(it)
    return it


def transition(wl, code, nxt, note=None):
    """状态迁移(校验合法边)+写 history。"""
    it = find(wl, code)
    if not it:
        raise ValueError(f"{code} 不在 watchlist")
    cur = it["state"]
    assert_transition(cur, nxt)
    it["state"] = nxt
    it["history"].append({"date": date.today().isoformat(),
                          "event": f"{cur}→{nxt}", "note": note or ""})
    return it


def log_event(wl, code, event, note=""):
    it = find(wl, code)
    if it:
        it["history"].append({"date": date.today().isoformat(),
                              "event": event, "note": note})


def record_price(it, price, period=None):
    if price is not None:
        it["price_log"].append({"date": date.today().isoformat(),
                                "period": period, "price": price})
        it["price_log"] = it["price_log"][-40:]     # 只留最近40条
