# invest · 族级共享约定

> 全族统一口径。改数字前想清楚牵动几个技能（40% 线至少牵三个）。

## 1. 增速线

**PASS_LINE = 40%（百分数，单季同比口径）**——营收/归母/扣非三线通用：

| 使用处 | 含义 |
|---|---|
| earnings-reader 初筛闸（R1） | 三指标可算侧须全 >40%，任一可算且≤40 出局 |
| a-share-review 财报三线 E1 | 三线全掉出 40 → ⚠E1 静水退出信号 |
| tenscreener 体检线 | 增速门槛同源 |

上年同期基数 ≤0 → 同比不可算 → **不否决**（扭亏通道，另由盈利规模闸把关）。

## 2. 数据源与额度

| 源 | 额度 | 使用方 |
|---|---|---|
| 东财 datacenter / push2 / F10 / emweb | 免费 | 全引擎 |
| 腾讯行情/K线、新浪 | 免费 | 全引擎（K线后备） |
| 巨潮公告 | 免费 | a-share-review、cninfo-disclosure |
| 华尔街见闻 | 免费 | a-share-review |
| **问财 openapi** | **50 次/日，全族共享** | 见下表 |

问财额度申报（改用量时更新）：

| 技能 | 单次跑批用量 | 频率 |
|---|---|---|
| a-share-review | 4-6 次 | 日频 |
| earnings-reader | ⌈过闸数/5⌉ 次（50 家=10 次）+1 备源 | 季频，披露期数日 |
| tenscreener | scout L1 1-2 次 | 按需 |

问财 key 走环境变量 `IWENCAI_API_KEY`（shell 已配），**任何技能不得把 key 写进文件**。
额度耗尽逃生门：各技能提供 `--no-wencai` 或等价开关，缺数据降级显示不阻塞。

## 3. 飞书落点

**约定（2026-11-04 用户定）：文档与 Base 一律放「我的空间根目录」，与历史分析文档（h1-monitor 三季报监测日报系列等）同级；不新建文件夹。**

| 资源 | 位置 |
|---|---|
| earnings-reader 财报阅读文档《YYYY年M月D日 财报阅读》 | 根目录 |
| 标的池 Base（跨引擎共享，见 §4） | 根目录，token 记 cache/pool_base.json |
| a-share-review 复盘文档 | 其既有「A股复盘」文件夹（历史约定，不新建设） |

通道：lark-cli（用户身份）。建 Base 用 `base +base-create --name --table-name --fields`（一次建库+首表+字段；`+app-create` 是 BaseApp 专用勿用）。

## 4. 标的池 Base

列结构（earnings-reader 首次运行自动建表，其他引擎只读/按约定写）：

| 列 | 类型 | 写入方 |
|---|---|---|
| 股票代码 | text（主键，upsert 依据） | 引擎 |
| 股票名称 | text | 引擎 |
| 纳入日期 | date | 引擎（首次） |
| 来源 | text | 引擎（如「财报阅读 26Q3」） |
| 营收/归母/扣非同比% | text（`+xx.x` 带符号格式，兼容"基数≤0"） | earnings-reader |
| L2分级 | single_select: S/A/R/B/剔除 | earnings-reader |
| 阶段 / 驱动 / 一句话 | text | earnings-reader（L2 判定） |
| 状态 | single_select: 观察/深研/已买/已剔 | **人**（默认观察） |
| 报告链接 | text | 引擎 |

## 5. 呈现与术语

- 红涨绿跌、红流入绿流出（与看盘软件一致）
- 增速一律 `+xx.x%` 带符号；金额亿元两位小数；「—」= 无数据，不写 N/A
- 术语统一用静水词汇：核心α / 主线 / 错杀 / 骑乘；CAN SLIM 七要素用《笑傲股市》中文版译名
- 所有报告页脚：*非投资建议，数据来源见正文标注*

## 6. 技能自包含原则

每个技能独立可用（SKILL.md + scripts 自足），**禁止跨技能 import 代码**；
需要复用的成熟函数 → 复制移植并在文件头注明出处（如「移植自 h1-monitor v7.3」）。
协作只走 ARCHITECTURE.md §接口约定的三种介质。
