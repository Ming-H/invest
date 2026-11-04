---
name: earnings-reader
description: 财报阅读技能（季频引擎）。当用户说"读财报"、"读X月X日的财报"、"财报阅读"、"这个季度的财报筛一下"、"财报筛选"，或在财报披露期想批量看当日新披露公司中谁增长强劲时使用。流程：读指定日期（默认昨日，支持区间）披露的财报→营收/归母/扣非三指标同比>40%闸门初筛→近4季同比环比表→名片数据→催化摘要与L2框架判定（结合静水/CAN SLIM，过滤低基数/纯涨价/并表假象）→标的池Base+飞书文档。单家公司深度精读请转 jingshui 技能（六段式）。
---

# 财报阅读（earnings-reader）

季频引擎：财报披露期手动触发，一天一句"读 X月X日的财报"。
前身 h1-monitor v7.3（流程沿用），设计与口径见 `docs/DESIGN.md`；族规约见 `../CONVENTIONS.md`。

## 标准工作流（三段式）

### ① 草稿（确定性，python）

```bash
python3 scripts/pipeline.py --date 2026-10-30 --draft        # 单日
python3 scripts/pipeline.py --since 2026-10-28 --until 2026-10-30 --draft   # 区间补跑
```

产出 `outputs/{日期}/`：`draft.md`（机器稿：筛选口径+过闸名单名片与四期表，催化为清洗原文）、
`judge_input.json`（判定输入卡）、`machine.json`（结构化快照，重跑覆盖）、`manifest.json`（降级清单）。
加 `--no-wencai` 跳过问财（筹码行显示 —，不耗额度）。**过闸 0 家也是正常结果**，如实报告。

### ② Claude 判定（对话内做，不调远端模型）

读 `outputs/{日期}/judge_input.json`，逐批（10 家/批）判定，合并写 `outputs/{日期}/judge_output.json`：

```json
[{"code": "688466", "drive_type": "出海新市场", "alpha_role": "核心环节",
  "is_pure_pricing_cycle": false, "is_one_off_boost": false, "stage_call": "无意见",
  "catalyst_summary": "1）…，2）…", "one_line": "≤20字", "final_note": "≤30字"}]
```

- `drive_type`∈{新品放量,出海新市场,量价齐升,国产替代,涨价周期,供需趋紧,其他}；
  `alpha_role`∈{核心环节,一般配套,边缘}；`stage_call` 仅当输入卡 `stage_hint`≠无意见 时判{早期,中期,晚期}，否则"无意见"
- `catalyst_summary`：把输入卡 `catalyst`（清洗原文）压缩成**恰好 n 条**（n=输入卡 `n`），编号 1）2），只转述业绩变动原因，禁止新增信息；数据不足时照抄清洗原文
- 判定依据输入卡的主营/催化/板块文本；**拿不准的枚举值选保守项**（其他/一般配套/无意见）
- 大批量时先写完所有批次再进 ③；写不完的留空（脚本走确定性兜底，不阻塞）

### ③ 终稿与发布（确定性，python）

```bash
python3 scripts/pipeline.py --date 2026-10-30 --finalize --publish
```

校验 judge_output（枚举合法/催化条数；缺漏走兜底）→ L2 五维评分+硬闸+S/A/R/B 分级 → `final.md`
→ lark-cli 写标的池 Base（upsert by 股票代码）+ 文档发布到飞书「投资/财报阅读」→ 报 URL 给用户。

## 口径速记（详规见 ../CONVENTIONS.md 与 docs/DESIGN.md §5）

- 闸门：三指标**可算侧同比全>40%**；基数≤0 不否决（扭亏通道，须当季归母≥0.5亿）；ST/退、当季亏损出局；环比无否决权只参与分档
- 假象过滤（L2 硬闸）：低基数（归母同比>300%+当季<0.5亿+ROE<5%）/纯涨价/并表一次性 → 剔除；市值≥1000亿 → 剔除；晚期 → 剔除
- 增速全部为**单季同比**（当季 vs 去年同季单季）；预告按区间下限倒推，报告标注推算
- 问财额度 ⌈过闸数/5⌉ 次/跑批；报告页脚带"非投资建议"

## 交接

- 发现值得深读的标的 → 建议用户转 jingshui 六段式（"用静水的方法分析 XX 三季报"）
- 入池后跟踪 → a-share-review 每日盯（标的池 Base 是共享的）；生命周期 → tenscreener
