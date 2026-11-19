# a-share-review v3.8 周复盘设计

日期：2026-11-19 ｜ 状态：定案 ｜ 作者：Claude（用户批准范围：分析→规划→实施→提交→安装）
前置：v3.7.3（晨跑模式/隔夜全球市场/腾讯兜底）

## 一、定位

**日线看波动，周线看结构。** 日报回答"今天发生了什么、明天盯什么"；
周报回答"这一周的结构是什么、下周准备什么"——静水框架"趋势判定看周线"
的执行层落地。周报不重复日报：不复述每日公告细节，只汇总"本周发生了
什么"；不做逐日回检，只做周际回检。

## 二、现有功能盘点（复用决策）

| 现有能力 | 周报复用方式 |
|---|---|
| `data.index_closes(300日)` | 直接取窗口首尾算周涨跌；聚合周MA |
| `data.market_total_amount(10日)` | 5日合计 vs 前5日 = 周量能对比 |
| `machine.jsonl` 日频时间序列 | **核心复用**：板块资金流按日快照 → 周累计+上榜次数聚合（"真主线 vs 一日游"判别，日报做不到） |
| `data.stock_kline(250日)` | 日K按ISO周聚合成周K（开/收/高/低/量） |
| `data.stock_moneyflow_daily(20日)` | 窗口内求和 = 周主力净额 |
| `data.announcements_recent(7日)`/互动易 | 本周事件时间线（只列不解读——解读已由当周日报完成） |
| `data.next_report_date`/`unlock_calendar` | 下周日历（披露预约+解禁窗口） |
| `data.tenscreener_triage` | 阶段快照，与上周周报快照对比标迁移 |
| `data.earnings_momentum` | 高增榜本周 vs 上周 diff（新进/退出/连续在榜） |
| `review.py` 的 `_recheck`/`publish`/`md_to_xml_segments`/`⟦CLAUDE⟧` 体系 | 回检改为周际机检；发布管线原样复用 |
| `charts.py` | v1 一张图：上证近6周走势+本周窗口标注（能力内） |

**新增仅三块**：周窗口与聚合纯函数、周报渲染、`machine_weekly.jsonl` 时间序列。

## 三、设计决策（含权衡）

### D-W1 滚动 5 交易日窗，不硬对齐自然周

窗口 = **最近 5 个完整交易日**（不含未收盘当日）。周五盘后/周末运行时
恰好等于自然周；周中运行得到滚动周（跨自然周边界）。理由：
- 任何一天都能跑（晨跑模式同理），不引入"周几才能跑"的状态
- 避开节假日空洞（自然周对齐在长假周无解）
- 标题带区间「(M.D-M.D)」，窗口永远显式，无歧义

### D-W2 资金主线聚合从 machine.jsonl 读，不重打问财

板块资金流日频快照已在 machine.jsonl（含 asof 口径修复）。周累计 =
逐日相加；上榜次数 = 出现天数。零额外额度、零新数据源。代价：依赖
日报连续运行（缺日则在周报"数据降级项"如实标注覆盖 N/5 日）。

### D-W3 周K线由日K聚合，不自建周K数据源

`stock_kline` 日K按 ISO 周分组聚合（开=首日开、收=末日收、高/低=极值、
量=求和）。免费源无现成周K接口，自聚合口径透明且可测试。

### D-W4 回检机制周际化（复用不改日报逻辑）

上周周报的「下周观察」由本周周报机检（触发位突破/上证周MA5站守），
机检函数参数化自 `_recheck_yesterday` 的思路重写为
`_recheck_last_week(prev_weekly, klines)`——不共用函数体（口径不同：
日线trig vs 周线收盘），共用判定阈值风格（0.5%容差）。

### D-W5 观察与小结必须可机检（延续日报铁律）

「下周观察」写明价位/触发位/数据口径，下周周报自动回检——判断积累
信用，与日报回检同一哲学。

## 四、模块设计

```
《YYYY年M月D日A股周复盘(M.D-M.D)》
├── 一、本周市场结构
│   ├── 1.1 周度指数与量能（周涨跌幅表 / 周K位置：周MA5·10·20 / 5日合计量 vs 前5日）
│   ├── 1.2 周度资金主线（板块5日累计净额 Top8 + 上榜N/5天 + 一日游标注）
│   ├── 1.3 周度情绪（本周最高连板梯队 / 断板 / 周涨幅Top20板块分布）
│   └── 1.4 高增榜周度变化（earnings_momentum 新进/退出/连续在榜）
├── 二、关注标的周全息
│   ├── 周涨跌 / 周K位置 / 周主力净额 / 本周事件时间线（公告+互动易汇总，不解读）
│   ├── 持仓组：距成本 / 本周损益贡献 / 十倍股阶段 vs 上周（迁移标注⇡⇣）
│   └── 观察组：触发位距 / 距一年新高
├── 三、上周观察回检（周际机检 + ⟦CLAUDE⟧一句话补判）
├── 四、周度小结与下周观察
│   ├── ⟦CLAUDE⟧ 本周结构定性（延续/切换/转折）
│   ├── 下周日历（预约披露 / 解禁窗口 / 事件）
│   └── ⟦CLAUDE⟧ 下周观察（必须可机检）
└── 〖图1〗上证近6周走势（本周窗口高亮）
```

`notes_for_claude.md` 素材包：本周 vs 上周主线 diff、高增榜 diff、
阶段迁移明细、量能对比——润色必读，不进正稿。

## 五、时间序列 machine_weekly.jsonl

一周一行（week_end 为键，同周重跑幂等覆盖）：
`{week_start, week_end, idx_weekly:{上证:{pct,close,above_ma5w}}, amt_5d,
 amt_prev_5d, top_flows:[{name,total,days}], sentiment:{max_lb,lb_break},
 gainers_top5, earnings_momentum:[…], watchlist:{code:{week_pct,main_5d,
 triage,cost_dist}}, next_watch:[…], degraded:[…]}`

日报 `machine.jsonl` 不动；周报读日频写周频，两层分离。

## 六、CLI 与工作流

```bash
python3 scripts/weekly.py --no-publish   # 草稿 outputs/weekly/{week_end}/weekly.md
python3 scripts/weekly.py --publish {YYYY-MM-DD}   # 发布（同复盘文件夹）
```

触发词（SKILL.md）：「周复盘」「本周复盘」「weekly」。推荐周五盘后
或周末运行（晨跑模式用户可在周六早跑，隔夜板块自然并入上周）。
`--publish` 后 URL 报给用户；同日重跑幂等。

## 七、不做（YAGNI）

- 不做自然周硬对齐与"周报补跑历史"（滚动窗已覆盖）
- 不做月线/季线（先看周线使用频率）
- 不重打问财取周资金（D-W2）
- 周K图表只做上证一张（watchlist 周K以表格呈现，图表二期看需求）
- 不自动定时（周报手动触发，与日报一致的哲学）

## 八、验收

1. 纯函数单测：周窗口切分 / 日K→周K聚合 / 资金周累计+上榜次数 /
   周际回检（tests/test_weekly.py，全绿）
2. 实跑 `--no-publish`：窗口=最近5完整交易日，模块齐全或降级如实标注，
   ⟦CLAUDE⟧ 占位完整，machine_weekly.jsonl 落一行
3. 发布一次真实周报到飞书（标题区间正确、图1锚点插图成功）
4. SKILL.md v3.8 触发词与工作流更新；invest monorepo commit+push；
   install.sh 平铺安装到 work/.claude/skills 后实跑一遍安装副本
