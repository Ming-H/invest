# invest · 投资技能族架构

> 族宪法：分层、数据流、成员与接口。改动族结构前先读本文。
> 新成员/新联动改完后更新本文。2026-11-04 立。

## 定位

个人投资研究的一站式技能族：**发现 → 跟踪 → 决策** 全链路，方法论来自静水/欧奈尔/林奇三套体系，
工程哲学 = 确定性代码管数字与闸门，LLM（对话中的 Claude）只做受控判定与转述。

## 两平面 × 四层（2026-11-05 v4 定版：任务平面 / 功能平面分离）

```
┌──────────── 任务平面(编排器, 各自节奏跑流程) ────────────────┐
│ tasks/earnings-reader  季频任务: 闸门→四季表→调judge→入池→飞书  │
│ tasks/a-share-review   日频任务: 盯盘→公告雷达→回检→飞书        │
└────────────┬──────────────────────────────┬───────────────┘
             │ 编排调用(任务"用什么")          │
┌────────────▼─── 功能平面(四层栈, "是什么") ──▼───────────────┐
│ L4 判定引擎  stack/engine/tenscreener (judge+watch/track)      │
│ L3 方法论    stack/methodology/ jingshui·can-slim·lynch       │
│ L2 结构化数据【族外基础设施】东财F10/问财/同花顺官方16技能       │
│ L1 文档数据  stack/data/cninfo-disclosure (PDF底座)            │
└─────────────────────────────────────────────────────────────┘
```

- 任务与功能是"用什么/是什么"的关系: 任务平面站在栈旁使用整个栈, 不是第五层。
- L2 是官方技能生态(装在 work/.claude/skills, 族内只消费不收编)。
- 仓库目录即架构: `tasks/` 与 `stack/{data,methodology,engine}/`; 安装仍平铺(Claude Code 只认平铺)。

## 成员清单 · 职责矩阵（2026-11-05 v3：判断权威收拢版）

> 总纲：**一个任务（季频编排）+ 一个判断权威（十倍股判定）+ 一个哨兵（日频盯盘）+ 三部方法典 + 一个数据底座**——全市场入口唯一、判断逻辑唯一、日常监控唯一。

| 技能 | 层 | 定位 | 输入 | 输出 | 明确不做 |
|---|---|---|---|---|---|
| earnings-reader | 引擎·**季频任务**(编排器) | 对指定日期披露跑完整流程: ①40%+卫生闸门 ②近4季同比环比表 ③调 tenscreener judge 判定 ④汇总输出 | 指定日期全市场披露 | 标的池 Base(带阶段/跟踪标注)+财报文档 | 不自建第二套判断(framework.py 仅兜底); 价格不决定入池资格只影响阶段标注 |
| tenscreener | 引擎·**判断权威**(工具+生命周期) | judge: 代码→判定(是否十倍候选/七阶段/TPS/三线形态/信号); 外加 runner 右侧扫描 + watch/track | 单代码/右侧全市场 | 判定JSON/亮灯名单/confirm/exit信号 | 不做全市场基本面初筛(scout 二期改读池); 不给指令 |
| a-share-review | 引擎·**日频哨兵** | 池内标的价格/资金/事件 + 公告雷达解读(v2.3) + 回检 | 当日行情+watchlist+新公告 | 复盘文档/全息卡/E1预警/公告解读 | 不发现新标的; 不做判断(归 judge) |
| jingshui | 方法论·总典 | 静水宪法(40%线/5不买/纪律)+六段式深读 | 问题/财报PDF | 框架/六段式报告 | 存标准不执行(执行归 judge) |
| can-slim | 方法论·体检典 | 七要素清单(judge 打分骨架的知识源) | 个股 | 体检表 | 同上 |
| lynch | 方法论·透镜典 | 六分类/估值尺/避雷 | 个股 | 分类+独白 | 同上 |
| cninfo-disclosure | 数据·底座 | 财报/招股书/研报/HKEX 文档供给 | 公司+文档类型 | PDF/清单 | 不解读内容 |

**关键变化(v2→v3)**: 判断逻辑从两套并行(earnings-reader framework.py / tenscreener TPS)收拢为 tenscreener judge 唯一权威; 全市场基本面扫描从两台收拢为 earnings-reader 一台; 两技能关系从"经池松耦合"变为"任务调功能"(CLI shell-out, 同 flow.py 调问财 cli 先例)。

**标的的一生**: 财报季任务(闸门→judge判定→入池带标注) → 平时 judge 单点咨询/runner 右侧扫描 → 人工建仓 → a-share-review 每日盯+公告解读 → 每季 judge 复判(watch/track) → exit_signal → 人工清仓 → 回检积累信用。

**边界裁决**: 全市场闸门与四季表=earnings-reader; 十倍判定(候选/阶段/TPS/信号)=tenscreener judge; 价格位置=judge 内嵌; 日常异动+公告=a-share-review; 单股深读=jingshui; 方法标准=三部典存而不执行; 买卖=永远人工。

**待接线(v3 落地四步+原有两项)**: ① tenscreener judge 子命令(组装 quarters/scorer/monitor/runner) ② earnings-reader 接 judge, framework.py 转兜底 ③ 池加〔阶段/TPS/跟踪〕列, 状态机对齐 ④ scout 改读池 ⑤ a-share-review ★标注切新池 ⑥ lynch 分类入池标签。

## 接口约定（引擎间只通过这三种介质协作，禁止跨技能 import 代码）

1. **标的池 Base**（single source of truth）：列结构见 CONVENTIONS.md。谁入池谁 upsert；
   下游引擎按 股票代码 读取。状态列由人流转（观察→深研→已买→已剔）。
2. **飞书文档**：各引擎产出到各自文件夹（见 CONVENTIONS.md），报告互相以 URL 引用。
3. **本地 JSON 快照**：各技能 outputs/ 下自有，不跨技能读。

## 路线图（族完备性）

1. ✅ 季频引擎（2026-11-04 建成）
2. h1-monitor 服务器 cron 退役（earnings-reader 稳定一个披露季后）
3. 持仓/仓位管理层（静水仓位纪律的执行层，tenscreener track 覆盖一半）
4. 交易决策日志（判断沉淀+事后回检，a-share-review 昨日回检为雏形）
5. a-share-review 旧 h1-monitor Base ★标注切到新标的池

## 安装与更新（2026-11-04 定案）

**开发/仓库 = 嵌套结构；安装 = 平铺真实目录**（Claude Code 只发现 `skills/<名>/SKILL.md` 平铺一层，嵌套与软链方案已评估并放弃，见本节历史讨论）。

```bash
./install.sh                    # 克隆/更新 ~/.claude/skill-repos/invest → rsync 平铺到 work/.claude/skills/
./install.sh <目标skills目录>    # 自定义目标
```

- 安装副本是**拷贝**不是引用：改代码走「开发目录改 → push → 重跑 install.sh（rsync 增量秒级）」，勿直接改安装副本
- `outputs/ cache/ watchlist.json` 在安装副本侧受保护，更新永不覆盖（运行数据与代码分离）
- 仓库布局(v4 两平面): `tasks/{earnings-reader,a-share-review}` + `stack/{data/cninfo-disclosure, methodology/{jingshui,can-slim,lynch}, engine/tenscreener}`; install.sh 按 basename 平铺安装
- 各技能 git 历史: subtree 全量并入本仓主线，GitHub 散仓已归档

## 飞书三目录规约（2026-11-05 定版，三条流三个家）

| 流 | 飞书目录 | token 配置位置 |
|---|---|---|
| 日频复盘(a-share-review) | 「A股复盘」 | 各自 outputs/feishu_folder.json(历史机制) |
| 季报阅读(earnings-reader) | 「财报阅读」 | cache/pool_base.json 的 doc_folder |
| 十倍股判定(tenscreener judge) | 「十倍股判定」 | cache/config.json 的 judge_folder |
| 标的池 Base | 我的空间根目录 | cache/pool_base.json(跨引擎唯一真源) |

「投资相关」「财报记录」为历史存档目录(h1-monitor 时代监测日报), 不再新增。新建文件夹需用户明示。
