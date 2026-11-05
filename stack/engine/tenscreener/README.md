# tenscreener · 十倍股单标的判定与生命周期监测

> 一个 Claude Code Skill:融合 **CAN SLIM**(欧奈尔)与**静水超级成长股**体系,**对给定单标的做分级判定**——Stage1 三线40%硬闸(秒级否决)→ Stage2 报告深读 → Stage3 静水/CAN SLIM/lynch 三典判定 → Stage4 条件发布(仅十倍股候选发飞书全量文档,否定不落盘)。另有 **watch/track** 覆盖"披露确认 → 持有退出"全周期。收敛到 1-2 只、买哪只,永远是投资人的决策,本工具不产出推荐名单。

## ✨ 特性

- **分级判定(v4.1)**:`judge --code` 三线40%硬闸秒级否决(当季营收/归母/扣非可算侧同比全>40%才继续)→ 过闸才 PDF 深读+三典判定;仅判定为十倍股候选才发飞书,否定不落盘不发
- **双通道(辅助发现)**:scout 三级漏斗(L1→L2→L3 TPS)找左侧错杀 + **runner 趋势中继在上涨期间识别十倍股**(五阶段:主升期/强势待确认/回调击球区/深调考验区/退潮;不恐高不设涨幅上限);已用中际旭创(+1197%)与若羽臣(+806%)真实回放验收
- **三线多季序列**:近四季单季 营收/归母/扣非 同比一次取回;过线条数/连续季数/共振形态(triple/profit_led/…)进排序权重,**单季掉线不剔除**(若羽臣 24Q4 教训)
- **生命周期监测**:`watch`(财报披露后右侧确认:持续超预期+创一年新高+大盘β)、`track`(持有退出:增速降档/扣非背离/回撤裁定——回撤≥40% 触发的是"扛住 vs 基本面转折"裁定,不是卖出指令)
- **TPS 七因子**:行业空间 + 终点对标 + 市值非线性衰减 + 成长性(含持续性/共振)+ 催化 + 模式 + 估值(PEG 接通)
- **问财额度友好**:全部查询经 `wencai.py` 当日缓存 + API 计数(同日重跑 0 消耗);批量空格分隔;`--archive` 归档每次运行
- **内置排雷**:周期反转(净利>300%)、扣非/归母背离(昭衍新药式)、一次性注水

## 📦 安装

```bash
# 作为 Claude Code Skill 安装(符号链接到开发目录,自动同步)
ln -s /Users/z/Documents/work/my-projects/my-skills/tenscreener ~/.claude/skills/tenscreener
```

**依赖**:
- `IWENCAI_API_KEY` 环境变量(同花顺问财)
- 配套 skill:`hithink-finance-query`(财务/行情)、`hithink-zhishu-query`(大盘 M)、`cninfo-disclosure`(下财报)、`can-slim`/`jingshui`(方法论参照)

## 🚀 用法

```bash
cd ~/.claude/skills/tenscreener

# ⓪ 单标的分级判定(主路径): Stage1硬闸自动前置, 不过闸秒级否决
python3 scripts/cli.py judge --code 603259          # 硬闸→过闸则深读产 pack.md
#   → Claude 读 pack.md 写 verdict.json(三典判定) →
python3 scripts/cli.py judge --code 603259 --publish # 候选才发飞书; 否定不落盘
python3 scripts/cli.py judge --code 600900 --force-full  # 绕闸直读(边缘案例)

# ① 双通道(辅助发现):scout 左侧错杀 + runner 右侧趋势中继(上涨期间识别)
python3 scripts/cli.py screen --report --archive
python3 scripts/cli.py runner --archive            # 主升期/回调击球区/深调考验区五阶段

# runner 单票回放(验收"上涨期间识别"):阶段时间线+首次亮灯摘要
python3 scripts/cli.py runner backtest 300308 --beg 2024-01-01

# ② 只跑某一级
python3 scripts/cli.py l1 --out /tmp/l1.json
python3 scripts/cli.py l2 --candidates /tmp/l1.json --out /tmp/l2.json
python3 scripts/cli.py l3 --l2 /tmp/l2.json --report --sector-max-cap 2000

# ③ 生命周期:筛选结果入 watchlist
python3 scripts/cli.py watch import --from-l1 outputs/20260830/l1.json --top 5
python3 scripts/cli.py watch import --from-runner outputs/20260830/runner.json
python3 scripts/cli.py watch add 003010 若羽臣 --note "备注"

# ④ 财报季跑确认(scouted → confirmed/waiting/invalidated)
python3 scripts/cli.py watch --run                 # 报告归档 outputs/YYYYMMDD/watch.md
python3 scripts/cli.py watch --run --confirm-pos 0.95 --no-market-gate

# ⑤ 人工建仓后,每季财报后跑退出监测
python3 scripts/cli.py track promote 003010 --cost 25.6 --date 2025-05-06
python3 scripts/cli.py track --run                 # ok/warn/exit_signal
python3 scripts/cli.py track keep 003010           # 人工否决告警,继续持有
python3 scripts/cli.py track close 003010 --reason "增速降档"

# 调试/测试
python3 scripts/cli.py screen --dump-raw           # 打印问财返回原始 key(字段漂移排查)
python3 tests/gen_fixtures.py && python3 tests/run_tests.py
```

在 Claude Code 中自然语言:"**帮我用 CAN SLIM 和静水找十倍股**"/"**跑一下 watchlist 确认**" 即可触发。

## 📋 watchlist 状态机

```
scouted ──confirm(自动)──► confirmed ──promote(人工)──► holding ⇄ exit_signal(自动告警)
   │                           │                           │
   └──invalidated(自动)──► closed ◄──close(人工)───────────┘   closed --reopen→ scouted
```

- 脚本只自动迁移 confirm/证伪;**建仓/清仓永远人工**(不荐股纪律)
- 状态文件 `outputs/watchlist.json`(版本校验 + .bak 备份)
- 全部判定条件的方法论出处与阈值说明见 `references/lifecycle.md`

## 🏗️ 架构

```
tenscreener/
├── SKILL.md                   # 触发 + 工作流
├── README.md
├── scripts/
│   ├── wencai.py              # 问财封装:当日缓存/API计数/离线回放(防query漂移哨兵)
│   ├── quarters.py            # 报告期日历 + 三线单季同比序列 + 纯函数分析
│   ├── monitor.py             # market_gate/price_position/confirm_check/exit_check
│   ├── watchlist.py           # 状态文件 + 状态机
│   ├── scorer.py              # TPS 七因子评分模型(纯标准库)
│   ├── l1_screener.py         # L1 问财初筛(排雷 + 三线序列排序加权)
│   ├── l2_analyzer.py         # L2 深度体检(CAN SLIM×静水打分,报告期动态)
│   ├── l3_evaluator.py        # L3 TPS 评估 + 报告(含三线四季表)
│   ├── kline.py               # 腾讯前复权日K封装(免额度,缓存+离线哨兵)
│   ├── runner.py              # 右侧趋势中继:scan 五阶段 + backtest 时间线
│   ├── judge.py               # 单标的分级判定(Stage1硬闸/深读/素材包/条件发布)
│   └── cli.py                 # 统一入口(judge/screen/l1/l2/l3/watch/track/runner)
├── tests/                     # 114 用例 + Stage1硬闸 + 若羽臣四时点回放 + 双股票 runner 验收
├── references/
│   ├── canslim-checklist.md   # CAN SLIM 七要素
│   ├── jingshui-checklist.md  # 静水超级成长股清单
│   ├── tenbaggers-playbook.md # 历史10倍股启动特征
│   └── lifecycle.md           # 生命周期出处映射 + 阈值说明
└── outputs/                   # 运行产物(gitignore):cache/ 报告归档/ watchlist.json
```

## 🧮 TPS 十倍潜力分

```
TPS = 0.30·F1行业空间 + 0.20·F2终点可达 + 0.15·F3市值衰减
    + 0.15·F4成长性 + 0.10·F5催化 + 0.05·F6生意模式 + 0.05·F7估值
```
- F2/F3/F4/F7 脚本自动算(F4 含连续过线持续性,单线孤立打折;F7 PEG 优先)
- F1/F5/F6 由 Claude 读研报/财报定性回填

分级:S(≥80 极具10倍潜力)/ A(≥65)/ B(≥50)/ C(≥35)/ D(<35)

## 📌 声明

- 数据来源:同花顺问财(iWenCai)、巨潮资讯(cninfo)
- **非投资建议、不荐股、不给买卖点**;exit_signal 是告警不是指令;10 倍是潜力评估非承诺
- A 股有风险,需自行结合大盘(M)、仓位、止损综合判断

## 📄 License

MIT
