# tests · tenscreener

```bash
python3 gen_fixtures.py   # 先生成离线 fixtures(若羽臣四时点真实数据)
python3 run_tests.py      # 82 用例,全部离线,0 问财消耗
```

> runner 回放另需 `tests/fixtures/{kline,yoy}/`(腾讯K线+三线同比表,生成脚本见
> gen_fixtures 内说明;重生成走 `kline.fetch_qfq` 生产路径,保证与线上同口径)

## 结构

| 文件 | 覆盖 |
|---|---|
| test_wencai.py | 缓存命中不调 subprocess / offline 缺 fixture 抛 FixtureMissing / 失败不缓存 / query 归一化同键 |
| test_quarters.py | 报告期日历边界(4/30、5/1、8/31、10/31、11/1…)、pass_streak/pass_lines/resonance/downshift/divergence(若羽臣真实数 + 昭衍式背离)、字段名漂移容忍 |
| test_scorer.py | F4 加速/持续性/单线打折/向后兼容、F7 PEG 分支与增速封顶 |
| test_l2.py | **回归 key 错名**(修复前 C/静水主营增速恒 0)、C 优先季度序列、L≠N、M gate、现金流、背离进 gaps、动态报告期 query 串 |
| test_monitor.py | confirm 三态矩阵 + 证伪 + 缺失季不算 fail;exit 矩阵(真实 26Q1→warn、合成→exit、背离→warn);market_gate 三态;状态机非法边 |
| test_e2e_replay.py | 若羽臣四时点端到端(见下) |

## 若羽臣(003010)四时点回放

fixtures 数值来自 2026-08-30 会话的双重验证数据(问财单季字段 × 累计倒算互验一致):

| 时点 | 命令 | 断言 |
|---|---|---|
| T1 2024-10-08 | `l1 --offline` | 若羽臣在候选池,共振=triple,streak_max=3;周期股被剔;profit_growth 有值(字段断链修复) |
| T2 2024-11-15 | `watch --run` | scouted→**confirmed**(三季报披露后两周,pos_1y=1.02,大盘 on),信号含"连续验证" |
| T3 2025-04-30 | `watch --run` | 年报+一季报连续过线 → "连续验证(资金合力)"注记 |
| T4 2026-04-30 | `track --run` | 26Q1 真实数(111→73 单季降档 + 回撤)→ **warn(中期调整裁定)**而非 exit;peak 经"近一年最高"补全 |

> ⚠️ T4 的 warn(而非 exit)是**有意的**:真实数据下三线仍全过线(73/164/168),
> 按静水"30-40% 中期调整扛住"纪律不应卖出;事后若羽臣 26Q3 反弹 +25% 佐证。
> exit 分支由 test_monitor 的合成数据覆盖。

## fixture 维护(哨兵机制)

- fixture 文件名 = md5(归一化 query + "|limit=N"),由 gen_fixtures.py 与 wencai.py 共用算法生成
- `--offline` 下 query 有一字之差 → FixtureMissing → 测试红:**改 query 模板必须同步改 gen_fixtures.py 并重新生成**
- 这是对"问财字段名/语法漂移"的主动哨兵,不是负担

## runner 双股票"上涨期间识别"验收(test_runner_replay.py)

| 股票 | 首次亮灯 | 当时涨幅 | 其后空间 | 当前(2026-08)判定 |
|---|---|---|---|---|
| 若羽臣 003010 | 2024-08 回调击球区 | 1.49x | +806% | 回调击球区(回撤37%+三线过线,大盘off暂缓) |
| 中际旭创 300308 | 2024-05 回调击球区 | 1.88x | +1179% | 回调击球区(回撤39%+三线过线) |

关键断言:披露滞后对齐(月份→报告期映射防未来函数)、2024-11 主升期=若羽臣真实启动月、
2025-02~05 中际旭创深调考验区(回撤>40%但三线仍过,"扛住"区)。

## AI 十倍股全样本验收(tests/ai10x_harness.py + test_ai10x.py)

```bash
python3 tests/ai10x_harness.py            # 免额度(腾讯+东财)筛 2024 以来 AI 主线 10x 并回放
python3 tests/ai10x_harness.py --only-failed   # 只重跑未 PASS 的
python3 tests/run_tests.py                # test_ai10x 断言全部 PASS/豁免合法
```

首轮结果(2026-08-30):19 只 ground-truth,18 PASS + 1 设计豁免(德科立,纯叙事型),
记分板 `docs/ai10x-scoreboard.md`。候选池可持续扩充——发现新 MISS 即为下一轮优化点。
