# lynch · 彼得·林奇的成功投资选股 Skill

> 把彼得·林奇《彼得·林奇的成功投资》（*One Up on Wall Street*）的方法论，抽象成可执行的 Claude Code skill。
> 核心：**投资于你所了解的 + 公司六分类 + 理想公司13特征 + 关键数字（P/E vs 增速）+ 两分钟独白 + 分类卖出纪律**。

⚠️ **声明**：本技能提供选股**框架与检查清单**，**非投资建议，不荐股、不给买卖点承诺**。书成于 1980 年代美股，A 股应用需自行校准。市场有风险。

---

## 这是什么

一个**知识型** skill（方法论 + 检查清单 + 问财取数模板）：

- **功能一 · 方法论问答**：怎么找十倍股、公司六分类、该避开什么、何时卖出、P/E 怎么看。
- **功能二 · 个股六分类体检**：给定公司 → 判类 → 关键数字取数核对 → 两分钟独白 → 分类卖出跟踪清单。

与同级投资技能构成三件套：

| skill | 来源 | 分工 |
|-------|------|------|
| `lynch` | 《彼得·林奇的成功投资》 | 定类别与估值锚（自下而上） |
| `jingshui` | 知乎 静水2008 | 验证景气与增速成色（行业 β） |
| `can-slim` | 欧奈尔《笑傲股市》 | 确认时机与大盘（七要素动量） |

## 依赖

- 可选联动：`hithink-finance-query` / `hithink-market-query`（问财取数，P/E、增速、机构持股、高管增持、存货等）
- 无强制依赖：纯方法论问答不需要任何 API

## 安装

```bash
git clone git@github.com:Ming-H/lynch.git
rsync -a --exclude='.git' lynch/ ~/.claude/skills/lynch/
```

新开 Claude Code 会话后，问"用林奇的方法分析 XX 公司"或"怎么找十倍股"即可触发。

## 文件结构

```
lynch/
├── SKILL.md                         # 主入口：理念+六分类+13特征+避雷+关键数字+路由
├── references/
│   ├── lynch-deep-dive.md           # 六分类买卖细则+两分钟独白模板+愚蠢清单+最终检查清单+A股校准
│   └── iwencai-query-templates.md   # 问财取数模板（P/E、净现比、存货、高管增持、机构覆盖）
├── README.md
└── LICENSE (MIT)
```

## 参考

- 彼得·林奇、约翰·罗瑟查尔德《彼得·林奇的成功投资》（*One Up on Wall Street*）
- 续作：《战胜华尔街》（*Beating the Street*）、《彼得·林奇教你理财》（*Learn to Earn*）

## License

MIT
