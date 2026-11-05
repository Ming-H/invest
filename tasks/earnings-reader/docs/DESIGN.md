# earnings-reader（财报阅读）· 设计定案 v1

状态：待用户审 → 审过即实施
日期：2026-11-04
前身：h1-monitor v7.3（阿里云 cron 日报），精神续作；流程沿用，形态重构。

---

## 0. 一句话定位

**季频引擎**：财报披露期在 Claude 会话里手动触发，读取指定日期披露的财报，
以「营收单季同比 > 40%」为主闸筛出高增长标的，批量产出名片式分析 + L2 框架分级，
写入标的池 Base + 飞书文档。族内位置见 `invest/ARCHITECTURE.md`（三引擎之一）。

与 jingshui 功能二的分工：**jingshui = 单家公司深度六段式精读**（"用静水的方法分析药明康德中报"）；
**earnings-reader = 全市场批量扫描阅读**（"读 10 月 30 日的财报"）。前者是后者的下游深研接口——
批量扫描发现值得深读的标的 → 交给 jingshui 六段式。

## 1. 需求定案

| 项 | 定案 | 备注 |
|---|---|---|
| 触发 | Claude 会话手动：读财报 / 读 X月X日的财报 | 无 cron |
| 日期 | 默认昨日；支持单日与区间（补跑） | |
| **筛选 = 三步漏斗** | ①初筛闸（同比硬线）→ ②近4季同比环比表 → ③框架分析过滤假象 | 用户 2026-11-04 定，等价于旧 v7.3 全套闸门 + L2 |
| 初筛闸 | 旧 R1~R4 **全套照搬**（见 §5）：三指标可算侧同比全 >40% + 卫生闸 + 规模闸 + 锚定闸 | 环比只呈现与分档，无否决权（v7.3 教训） |
| 假象过滤 | 旧 L2 硬闸 H1/H2/H3 照搬（低基数/纯涨价/并表/晚期/体量） | H2 低基数公式见 §6 |
| 预告路径 | 保留（区间下限倒推当季，同一套闸；报告标注推算） | ⚠ 沿用现有流程，可否决 |
| 深度 | L1 全量（名片+单季四期表+催化）+ L2 框架精选（五维+S/A/B/R） | L2 枚举判定由对话 Claude 承担 |
| 输出 | 标的池 Base（新表）+ 飞书文档《YYYY年M月D日 财报阅读》 | |
| 砍掉 | 巨潮下载、cron、headless GLM、公告雷达、运行日志表 | |

## 2. 工作流：三段式（抄 a-share-review 模式）

用户的三步漏斗 ↔ 旧项目机制 ↔ 新技能实现 的对应关系（漏斗逐级收窄，后步只处理前步幸存者）：

| 用户口述的三步 | 旧项目对应机制 | 新技能实现 |
|---|---|---|
| ① 营收/利润同比 >40% 初筛 | R1 同比硬线 + R2/R3 卫生闸 + R2.5 规模闸 + R4 锚定闸 | pipeline.py `--draft`（纯 python） |
| ② 近 4 季同比环比计算呈现 | quarters() 单季差分 + qt() 四期表 | 同上，过闸者才建表 |
| ③ 结合 CAN SLIM/静水分析、过滤低基数假象 | framework_filter.py L2（五维评分 + H2 假引擎闸：低基数/纯涨价/并表） | framework.py 纯计算 + Claude 枚举判定（`--finalize`） |

```
用户: "读 10月30日的财报"
  │
  ▼ ① 草稿（确定性，python）
  python3 scripts/pipeline.py --date 2026-10-30 --draft
  → 取数 → 闸门 → outputs/2026-10-30/
      draft.md          机器稿（名片+单季表，催化留清洗原文占位）
      judge_input.json  判定输入（每家一卡）
      machine.json      结构化快照（幂等重跑覆盖）
  │
  ▼ ② Claude 判定（对话内，我）
  读 judge_input.json，逐批判定，写 outputs/2026-10-30/judge_output.json：
    a. 催化摘要：清洗原文压缩成恰好 n 条（原 GLM 职责①）
    b. L2 枚举：drive_type / alpha_role / is_pure_pricing_cycle /
       is_one_off_boost / stage_call（原 GLM 职责②，schema 严格枚举）
    c. one_line / final_note
  │
  ▼ ③ 终稿与发布（确定性，python）
  python3 scripts/pipeline.py --date 2026-10-30 --finalize --publish
  → 校验 judge_output（枚举合法/缺漏，缺漏走确定性兜底）→ L2 评分 → final.md
  → lark-cli 写标的池 Base（upsert by 代码）+ 发飞书文档 → 报 URL
```

**判定协议**（judge_input → judge_output，文件交接）：

- judge_input.json 每家一卡：code/name/board/main_business/catalyst(清洗后)/n(条数)/
  gm_trend/roe/np_cash_ratio/mktcap_yi/quarterly(四季 yoy/qoq)/realized_3y/stage_hint
- judge_output.json 严格 schema：
  `{code, drive_type∈{新品放量,出海新市场,量价齐升,国产替代,涨价周期,供需趋紧,其他},
    alpha_role∈{核心环节,一般配套,边缘}, is_pure_pricing_cycle:bool, is_one_off_boost:bool,
    stage_call∈{早期,中期,晚期,无意见}, catalyst_summary(恰好n条), one_line(≤20字), final_note(≤30字)}`
- 校验失败/缺漏 → 确定性兜底（drive=其他/alpha=一般配套/催化=清洗原文），**永不阻塞发布**

## 3. 文件结构

```
invest/earnings-reader/
├── SKILL.md              # 触发词 + 三段式工作流说明
├── docs/DESIGN.md        # 本文档
├── scripts/
│   ├── data.py           # 取数层（全免费源优先 + 问财预算申报）
│   ├── framework.py      # L2 纯计算（五维/硬闸/分级，自 framework_filter 移植）
│   └── pipeline.py       # 编排：draft / finalize / publish 三子命令
├── outputs/              # (gitignore) {date}/draft|final.md + judge_*.json + machine.json
└── cache/                # (gitignore) processed.json 幂等台账
```

## 4. 数据层（data.py）

沿用 h1-monitor 已实测的接口与坑注释，Mac 本机不再有 py3.6/内存枷锁：

| 函数 | 源 | 用途 | 额度 |
|---|---|---|---|
| disclosure_lists(date) | 东财 RPT_LICO_FN_CPD + RPT_PUBLIC_OP_NEWPREDICT | 当日披露清单（正式+预告） | 免费 |
| f10_amounts(code) | 东财 F10 RPT_F10_FINANCE_MAINFINADATA | 累计三指标+质地（毛利率/ROE/ROIC/负债/净现比） | 免费 |
| tx_names / kline | 腾讯 | 名称兜底 / 日K(距新高·YTD) | 免费 |
| org_info / quote_one / lift_next | 东财 F10 / push2 / RPT_LIFT_STAGE | 名片：板块/主营/实控人/市值/PE/解禁 | 免费 |
| biz_review(code) | 东财 emweb jyps | 本期经营评述（催化源①） | 免费 |
| iw_cards(codes) | 问财 5家/批 | 机构持股/股东户数/涨停次数 | **问财额度**：50家=10次 |
| quote_em / monthly_tx | 东财估值表 / 腾讯月K | L1.5：市值/PE、realized_3y/近3月6月 | 免费 |

降级约定（抄 a-share-review）：每个函数独立降级返回 None/[]，manifest.json 记 degraded 清单，
草稿尾部列「本次数据降级项」，不阻塞整体。`--no-wencai` 跳过问财（筹码行缺省显示 —）。

## 5. 闸门与计算层（pipeline.py 确定性部分）

**初筛闸 = 旧 v7.3 全套照搬**（每条规则都有真实案例锚定，出处 h1-monitor pipeline.py 头注）：

```
quarters(): 累计→单季差分（Q3=三季报累计−H1累计 等，原币精度）

R3 卫生闸   名称含 ST/退 → 出局                    （*ST联翔案例）
R2 亏损闸   当季归母≤0 → 出局；当季扣非≤0(有值) → 出局（非经常性收益化妆照妖）
            预告路径 = 区间下限倒推当季≤0 同样出局
R1 同比硬线 营收/归母/扣非 三指标：可算侧须全 >40%（任一可算且≤40 → 出局，
            防"营收虚增+利润暴跌"假成长——诺德股份/大族数控案例）；
            基数≤0 不可算 → 不否决（扭亏通道）
            环比无否决权（单季环比含季节噪声：船海Q4重/白酒Q4Q1备货/施工Q1停工），
            只参与 R4 锚与分档
R2.5 规模闸 扭亏类（归母+扣非同比全不可算）当季归母须 ≥0.5 亿
            （拦时代万恒 112 万蚊子腿、100万→500万=+400% 小基数游戏）
            预告路径 = 净利区间倒推当季下限 ≥0.5 亿，未披露净利即拦
R4 锚定闸   入池须至少一个指标 同比>40 或 环比>50（拦预告只报净利的躺平扭亏）

分档(呈现用): 特档·双高 = 最大可算同比>100 且 最大环比>50；其一 → 一档·单高；其余 → 二档·稳增
幂等: cache/processed.json 按 (code, notice_date) 去重；同日重跑覆盖 outputs
```

报告期识别：不手改常量——按区间内披露清单的 REPORTDATE 分布自动判定本季报告期；
指令可显式覆盖（"读 10月30日的三季报"）。

报告期识别：不手改常量——按区间内披露清单的 REPORTDATE 分布自动判定本季报告期
（如 2026-09-30 三季报）；指令可显式覆盖（"读 10月30日的三季报"）。

## 6. L2 框架层（framework.py）

自 h1-monitor/scripts/framework_filter.py 移植（= jingshui × CAN SLIM 的代码化，
即用户第③步"结合 can slim 静水分析、过滤低基数假象"）。改动仅两处：

| 件 | 处置 |
|---|---|
| classify_momentum / realized_bucket / stage_cell(3×3 判定表) | 照搬（纯计算） |
| score_engine/space/stage/quality/confirm 五维 | 照搬（纯计算） |
| **hard_gates（假象过滤核心）** | 照搬：**H2 假引擎** = 低基数（归母同比>300% 且 当季归母<0.5亿 且 ROE<5%）∨ 纯涨价周期（无量增）∨ 并表/一次性收益为主；**H1 晚期**（判定表晚期 × LLM 终判）；**H3 体量**（市值≥1000亿） |
| S 门（五维下限 + 每季≤5）/ R 组（骑乘观察）/ α 门槛（S/A/R 须核心环节） | 照搬 |
| llm_judge_batch（headless GLM 调用层） | **删除**，替换为 judge_output.json 文件协议 |
| render_section / sync_base | 照搬改写（表名/列名对齐新标的池） |

## 7. 输出设计

**标的池 Base**（新建，lark-cli 首次自动建，upsert by 股票代码，跨引擎共享——
tenscreener watch 接管、a-share-review 标注★）：

| 列 | 类型 | 说明 |
|---|---|---|
| 股票代码 | text | 主键 |
| 股票名称 | text | |
| 纳入日期 | date | 首次入池 |
| 来源 | text | 固定「财报阅读」+ 报告期 |
| 营收/归母/扣非同比% | number | 当季单季 |
| L2分级 | single_select | S/A/R/B/剔除 |
| 阶段 / 驱动 / 一句话 | text | L2 判定 |
| 状态 | single_select | 观察/深研/已买/已剔（默认观察，人工流转） |
| 报告链接 | text | 当日文档 URL |

**飞书文档**《YYYY年M月D日 财报阅读》：筛选口径 → 过闸名单（名片+单季表+催化，
特档/一档/二档分组）→ 框架精选节（S/A/R 表 + 剔除汇总）→ 降级清单。
落在「投资/财报阅读」文件夹（族约定）。

## 8. 实施计划（审过即动）

1. **族骨架**：建 invest/ARCHITECTURE.md + CONVENTIONS.md；git mv 六技能入族 + 重链软链
2. **earnings-reader 骨架**：SKILL.md + 三脚本空转（--date 解析/outputs 落盘）
3. **data.py**：取数层 + 降级清单（对 2026-10-30 真实重放验证清单数）
4. **pipeline draft**：闸门+分档+名片+draft.md/judge_input.json（对照 h1-monitor 10-30 报告人工核对闸门行为）
5. **framework.py + finalize**：L2 评分 + final.md
6. **publish**：Base 建表/写入 + 文档发布（拿 10-30 真数据全链路走一遍，先发测试文件夹）
7. **SKILL.md 定稿 + 移交**：真实触发一次全流程；h1-monitor 服务器 cron 暂不停（稳定一个披露季后再退役）

## 9. 验收标准

- 2026-10-30 重放：窗口[10-29~10-30]。**旧版"披露983家"实为分页欠采样（NOTICE_DATE 排序致 60% 重复，
  真值 2359 家）**；本版 em_pages 已修（SECURITY_CODE 主键+行键去重，实测 13 页 0 重复）。
  验收改为：过闸名单 ⊇ 旧 50 家（旧名单为完整数据集的子集），总数预期显著多于 50，逐家闸门理由可对
- 同日重跑 outputs 幂等覆盖，Base 不产生重复行
- judge_output 缺漏/枚举非法 → 兜底不阻塞
- 问财一次跑批 ≤ 15 次（50 家 10 次 + 余量）
- 全流程（draft→我判定→publish）在会话内一次跑通，文档 URL 可点开

## 10. 风险与开放问题

- 重披露日过闸量与旧项目同量级（10-30 实测 50 家）→ L2 判定分批（10 家/批）我逐批做，时间可控；
  上限保护：>120 家时按分档排序截断并在报告标注
- 问财额度与当日复盘冲突 → CONVENTIONS.md 额度表申报 + --no-wencai 逃生门
- lark-cli 用户身份建新 Base 需要首次手工确认权限
- 预告路径是否保留（§1 ⚠ 项）待用户最终确认
