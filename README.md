# invest · 个人投研技能族

> 三引擎 + 一池 + 四层 —— 把「发现 → 跟踪 → 决策」的完整投研闭环装进 Claude Code 技能体系。
> 数据与事实由确定性代码计算，判断与叙述由 LLM 受控承担；宁可错过，不可编造。

## 是什么

一个按**节奏**组织的 Claude Code 技能族：财报披露期批量扫描（季频）、每日盘后复盘（日频）、
十倍股全生命周期管理（持续），共享一个飞书「标的池」多维表格。方法论沉淀自静水2008、
欧奈尔《笑傲股市》、彼得·林奇三套体系，并已代码化为可执行的闸门与评分。

```
                ┌────────────────────────────────────┐
                │      飞书 = 决策记录层（一池）        │
                │  标的池 Base + 分析文档（根目录）      │
                └──────▲──────────▲────────▲─────────┘
                       │写入       │读取回检 │入池
    ┌──────────────────┴──┐  ┌────┴────────┴────────┐
    │ 季频：earnings-reader │  │ 日频：a-share-review  │
    │ 披露日→闸门→L2分级     │  │ 盘后六模块+全息卡+回检 │
    └──────────▲──────────┘  └─────────▲───────────┘
               │ 发现候选                │ 深研请求
    ┌──────────┴───────────────────────┴──────────┐
    │ 持续：tenscreener                              │
    │ scout 左侧错杀 → runner 右侧主升 → track 退出   │
    └──────────────────▲───────────────────────────┘
                       │ 判定标准（知识库）
    ┌──────────────────┴──────────┐
    │ 方法论: jingshui / can-slim / lynch │
    ├─────────────────────────────┤
    │ 数据: cninfo-disclosure(巨潮披露底座) │
    └─────────────────────────────┘
```

## 成员

| 技能 | 层 | 节奏 | 触发词示例 | 一句话 |
|---|---|---|---|---|
| **earnings-reader** | 引擎 | 季频 | 「读 10月30日的财报」 | 披露日全市场扫描：同比闸门 → 名片+四期表 → 静水×CAN SLIM 五维分级 → 入池+飞书文档 |
| **a-share-review** | 引擎 | 日频 | 「复盘」 | 盘后六模块复盘 + 持仓/观察全息卡 + 昨日观察自动回检 |
| **tenscreener** | 引擎 | 持续 | 「筛十倍股」 | 三级漏斗找左侧错杀 + 五阶段识别右侧主升 + 退出监测 |
| **jingshui** | 方法论 | 被引用 | 「用静水的方法分析药明康德中报」 | 静水体系：选股方法论 + 财报六段式框架 |
| **can-slim** | 方法论 | 被引用 | 「XX 符合 CAN SLIM 吗」 | 欧奈尔七要素体检 |
| **lynch** | 方法论 | 被引用 | 「帮我给 XX 做六分类」 | 林奇六分类与关键财务数字 |
| **cninfo-disclosure** | 数据 | 被调用 | 「下载宁德时代招股书」 | 巨潮/HKEX 披露下载底座（财报/招股书/研报） |

## 安装与更新

```bash
git clone git@github.com:Ming-H/invest.git
cd invest && ./install.sh          # 默认装到 ~/Documents/work/.claude/skills
./install.sh /path/to/skills       # 或指定目标目录
```

安装形态：**开发/仓库为嵌套结构，安装为平铺的独立技能目录**（Claude Code 只发现
`skills/<技能名>/SKILL.md` 平铺一层）。`install.sh` 用 rsync 增量同步并排除
`outputs/ cache/ watchlist.json` 等运行数据——更新重跑即可，不丢持仓与台账。

## 使用示例

```
读 10 月 30 日的财报                        → earnings-reader 全流程（约 15-20 分钟）
复盘                                        → a-share-review 当日复盘
用静水的方法分析 药明康德 26Q3              → jingshui 六段式单股精读
筛一下现在的十倍股候选                      → tenscreener scout
```

## 族口径（全文见 CONVENTIONS.md）

- **增速线 40%**：营收/归母/扣非单季同比，全族唯一标准（闸门/三线预警/漏斗同源）
- **问财额度 50 次/日**：全族共享，各技能文档申报用量，`--no-wencai` 逃生门
- **飞书落位**：分析文档与标的池 Base 一律放「我的空间根目录」，不新建文件夹
- **呈现**：红涨绿跌；金额亿元两位小数；所有报告页脚「非投资建议」
- **可靠性哲学**：闸门与算术是确定性代码；LLM 只做枚举判定与受控转述，缺漏走保守兜底

## 目录结构

```
invest/
├── README.md · ARCHITECTURE.md(族宪法) · CONVENTIONS.md(族口径) · install.sh
├── earnings-reader/        # SKILL.md + scripts/{data,framework,pipeline}.py + tests/
├── a-share-review/         # SKILL.md + scripts/{review,data,charts}.py
├── tenscreener/            # scout/runner/watch/track
├── jingshui/  can-slim/  lynch/          # 方法论(references + 范例)
└── cninfo-disclosure/      # 巨潮/HKEX 下载底座
```

各技能的详细用法见其目录内 `SKILL.md`；架构决策记录见 `ARCHITECTURE.md`。

## 路线图

- [x] 三引擎齐备（2026-11-04 earnings-reader 上线）
- [ ] h1-monitor 服务器 cron 退役（earnings-reader 稳定一个披露季后）
- [ ] 持仓/仓位管理层（静水仓位纪律执行层）
- [ ] 交易决策日志（判断沉淀 + 事后回检）
- [ ] a-share-review 旧 h1-monitor Base ★标注切到新标的池

## 免责声明

本仓库全部产出为个人研究用途的**非投资建议**；数据来自东方财富、腾讯、问财、巨潮等公开接口，
以报告内标注为准。
