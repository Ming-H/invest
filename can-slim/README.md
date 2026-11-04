# can-slim · 笑傲股市 CAN SLIM 选股 Skill

> 把威廉·欧奈尔《笑傲股市》的 **CAN SLIM** 选股法则，抽象成可执行的 Claude Code skill。
> 配合同花顺问财接口取数，按 **C / A / N / S / L / I / M** 七要素做选股体检。

⚠️ **声明**：本技能提供选股**框架与体检清单**，**非投资建议，不荐股、不给买卖点承诺**。CAN SLIM 原版阈值源自美股，A 股应用需自行校准。市场有风险。

---

## 这是什么

一个**混合型** skill：
- **知识层**：CAN SLIM 七要素方法论（当季收益 / 年度收益 / 新催化新高 / 供需 / 领涨强度 / 机构持仓 / 大盘方向）。
- **执行层**：分步选股工作流 + 轻量辅助脚本，调用问财接口批量取数、汇总成体检表。

## 依赖

1. 同花顺问财三个 skill（数据源）：
   - `hithink-finance-query`（C / A / I）
   - `hithink-market-query`（N / S / L）
   - `hithink-zhishu-query`（M）
2. 环境变量 **`IWENCAI_API_KEY`**：从 https://www.iwencai.com/skillhub 获取。

## 安装

### 方式 A：符号链接（开发友好，改了即生效）
```bash
ln -s /path/to/my-skills/can-slim ~/.claude/skills/can-slim
```

### 方式 B：复制
```bash
rsync -a --exclude='.git' --exclude='.DS_Store' \
  /path/to/my-skills/can-slim/ ~/.claude/skills/can-slim/
```

安装后**新开一个 Claude Code 会话**，输入"用 CAN SLIM 帮我选股"或 `/can-slim` 即可触发。

## 辅助脚本

```bash
# 初筛候选
python3 scripts/canslim_screener.py --screen "最近一季净利润同比增长大于25%且股价创52周新高的股票"

# 对指定股票做七要素体检
python3 scripts/canslim_screener.py --tickers "宁德时代,比亚迪"
```

脚本只取数汇总，**不打分**——最终判断由 Claude 按 `SKILL.md` 七要素表综合做出。

## 文件结构

```
can-slim/
├── SKILL.md                         # 主入口：方法论 + 选股工作流 + 问财对接
├── scripts/canslim_screener.py      # 轻量取数汇总脚本
├── references/
│   ├── canslim-deep-dive.md         # 七要素详细标准与执行纪律（严格对照原书）
│   ├── iwencai-query-templates.md   # 问财 query 模板库（含实测可用问法与避坑）
│   └── case-study-20261005-songfa.md # 实测案例：新判据在真实标的上的判定范例
├── README.md
└── LICENSE
```

## 参考

- 威廉·欧奈尔《笑傲股市》（*How to Make Money in Stocks*）
- IBD（Investor's Business Daily）CAN SLIM 体系

## License

MIT
