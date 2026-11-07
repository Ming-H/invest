#!/usr/bin/env python3
"""
问财调用统一封装 · tenscreener

所有对问财(hithink-finance-query / hithink-zhishu-query)的 subprocess 调用收口到这里:
  - 当日缓存:同一天同 query 直接读本地,不耗 API 额度(outputs/cache/YYYYMMDD/)
  - API 计数:每次真实调用计数并记录 query,结束时 counter_report() 打印
  - 回放模式:set_cache_dir(fixtures目录)+set_offline(True) 后只读缓存,
    缺失即抛 FixtureMissing —— 用于离线测试,同时是"query 语法漂移"的哨兵
    (query 模板一改,fixture 不匹配,测试立刻红)。

纯标准库。
"""

import hashlib
import json
import os
import subprocess
import sys
from datetime import date

def _probe_cli(skill):
    """问财 CLI 多位置探测(2026-11-05: ~/.claude/skills 已废弃, 官方技能装在
    work/.claude/skills; 不修则 L1/runner/watch 全部静默降级 —— flow.py 同款雷)。
    2026-11-07: 仓库已公开, 他人安装路径各异 → 优先环境变量
    TENSCREENER_SKILLS_DIR(指向 skills 根目录), 再试默认两处。"""
    env_root = os.environ.get("TENSCREENER_SKILLS_DIR")
    cands = ([os.path.join(env_root, skill, "scripts", "cli.py")] if env_root else []) + \
        [os.path.expanduser(p) for p in (
            "~/Documents/work/.claude/skills/%s/scripts/cli.py" % skill,
            "~/.claude/skills/%s/scripts/cli.py" % skill)]
    for p in cands:
        if os.path.exists(p):
            return p
    return cands[-1]


DEFAULT_FINANCE_CLI = _probe_cli("hithink-finance-query")
DEFAULT_ZHISHU_CLI = _probe_cli("hithink-zhishu-query")

SKILL_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_ROOT = os.path.join(SKILL_ROOT, "outputs", "cache")

_counter = {"api": 0, "cache_hit": 0, "queries": []}
_cache_dir = None      # None=按天自动(CACHE_ROOT/YYYYMMDD); 指定目录=固定缓存/回放
_offline = False       # True=只读缓存,缺失抛错(测试用)
_debug_keys = False    # True=每次取数打印返回项的原始 key(调试问财字段漂移用)


class FixtureMissing(Exception):
    """回放模式下 cache 未命中 —— query 模板与 fixture 不再匹配。"""


def set_cache_dir(path):
    global _cache_dir
    _cache_dir = path


def set_offline(flag=True):
    global _offline
    _offline = flag


def set_debug_keys(flag=True):
    global _debug_keys
    _debug_keys = flag


def counter():
    return dict(_counter)


def counter_report():
    return f"[问财] API 调用 {_counter['api']} 次 / 缓存命中 {_counter['cache_hit']} 次"


def _cache_file(query, limit):
    d = _cache_dir or os.path.join(CACHE_ROOT, date.today().strftime("%Y%m%d"))
    os.makedirs(d, exist_ok=True)
    key = hashlib.md5(f"{query}|limit={limit}".encode("utf-8")).hexdigest()
    return os.path.join(d, f"{key}.json")


def _num(v):
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _pick(item, *candidates):
    """从问财项里取第一个存在的候选字段值(精确 → 模糊子串),转 float。"""
    for c in candidates:                    # 精确匹配
        if c in item:
            return _num(item[c])
    for c in candidates:                    # 模糊子串匹配
        for k, v in item.items():
            if c in k:
                return _num(v)
    return None


def iwencai(query, limit=80, cli_path=DEFAULT_FINANCE_CLI, timeout=120):
    """调用问财 cli(finance/zhishu 通用),返回 datas 列表。失败返回空列表。
    同日同 query 走缓存;offline 模式下缓存缺失抛 FixtureMissing。"""
    norm = " ".join(str(query).split())
    cf = _cache_file(norm, limit)
    if os.path.exists(cf):
        try:
            with open(cf, encoding="utf-8") as f:
                cached = json.load(f)
            if cached.get("query") == norm:
                _counter["cache_hit"] += 1
                return cached.get("datas", []) or []
        except (json.JSONDecodeError, OSError):
            pass                            # 缓存损坏 → 重新取
    if _offline:
        raise FixtureMissing(f"fixture 未命中: {norm!r} (limit={limit})")

    try:
        proc = subprocess.run(
            [sys.executable, cli_path, "--query", norm, "--limit", str(limit)],
            capture_output=True, text=True, timeout=timeout)
        data = json.loads(proc.stdout)
    except Exception as e:
        print(f"[问财查询失败] {e}", file=sys.stderr)
        return []                           # 失败不写缓存(下次重试)

    datas = data.get("datas", []) or []
    if _debug_keys and datas:
        print(f"[wencai raw keys] {norm[:60]}... → {list(datas[0].keys())}",
              file=sys.stderr)
    with open(cf, "w", encoding="utf-8") as f:
        json.dump({"query": norm, "limit": limit, "ts": date.today().isoformat(),
                   "datas": datas}, f, ensure_ascii=False)
    _counter["api"] += 1
    _counter["queries"].append(norm)
    return datas
