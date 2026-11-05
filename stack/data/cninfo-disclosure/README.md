# cninfo-disclosure · 巨潮信息披露下载

> 一个 Claude Code Skill：下载 A 股上市公司**财报**、**招股说明书** PDF 与**券商研报**清单。财报/招股书走巨潮资讯网，港股财报走 HKEX 披露易，券商研报走东方财富。统一入口。

数据全部来自官方/权威渠道：
- **A股公告**：[巨潮资讯网 cninfo.com.cn](http://www.cninfo.com.cn)（证监会指定信息披露平台）
- **券商研报**：[东方财富研报中心 data.eastmoney.com](https://data.eastmoney.com/report)（卖方研报聚合）
- **港股**：[HKEX 披露易 hkexnews.hk](https://www1.hkexnews.hk)（港交所）

## ✨ 特性

- 📄 **财报**：年报 / 半年报 / 一季报 / 三季报，按「股票 + 年份」定位，支持批量多年/多类型
- 📜 **招股书**：首发 IPO 招股说明书，按「股票」定位（无需年份），自动识别板块（科创板/创业板/主板/北交所）
- 📊 **券商研报**：卖方研报清单，按「股票」定位（无需年份），输出 CSV+Markdown，含评级/分析师/盈利预测/PDF直链（东方财富）
- 🇭🇰 **港股**：年报 / 中期报告（HKEX 披露易，Playwright 驱动）
- 🧠 **智能筛选正本**：自动排除摘要 / 更正 / H股 / 确认意见 / 过程稿等干扰项
- ⚡ **零依赖**（A股）：纯 Python3 标准库；港股可选 Playwright
- 🏗️ **底座架构**：巨潮查询/PDF下载抽成共享模块，财报与招股书复用同一底座

## 📦 安装

作为 Claude Code Skill 安装到 `~/.claude/skills/`：

```bash
git clone https://github.com/Ming-H/cninfo-disclosure.git ~/.claude/skills/cninfo-disclosure
```

港股下载需额外安装（A股不需要）：

```bash
pip install playwright   # 使用系统 Chrome，无需 playwright install chromium
```

## 🚀 用法

```bash
cd ~/.claude/skills/cninfo-disclosure

# 财报（需 --year）
python3 scripts/cli.py download --stock "三一重工" --year 2025 -o /tmp/
python3 scripts/cli.py download --stock "三一重工" --year 2025 --type semi -o /tmp/    # 半年报
python3 scripts/cli.py download --stock "三一重工" --year 2023-2025 -o /tmp/           # 批量多年

# 招股书（无需 --year）
python3 scripts/cli.py download --stock "宁德时代" --type prospectus -o /tmp/          # 创业板 2018
python3 scripts/cli.py download --stock 600519 --type prospectus -o /tmp/              # 茅台 2001（早期也有）
python3 scripts/cli.py search --stock "宁德时代" --type prospectus                     # 只搜不下载

# 券商研报清单（无需 --year，东方财富）
python3 scripts/cli.py download --stock 688578 --type research -o /tmp/                # 艾力斯，落地 CSV+MD
python3 scripts/cli.py search --stock "688578" --type research                         # 只搜不下载

# 港股财报
python3 scripts/cli.py download --stock 01888 --year 2025 -m hk -o /tmp/
```

在 Claude Code 中直接用自然语言：「下载宁德时代的招股说明书」「下三一重工 2023-2025 年报」即可触发本 skill。

## 🏗️ 架构

```
scripts/
├── cninfo_client.py   # 共享底座：巨潮公告查询API + PDF下载 + 通用工具
├── reports.py         # 财报（annual/semi/q1/q3）
├── prospectus.py      # 招股书（首发IPO招股书）
├── research.py        # 券商研报（东方财富，输出 CSV+Markdown 清单）
├── hkex.py            # 港股（Playwright 驱动 HKEX 披露易）
└── cli.py             # 统一入口（search / download）
```

巨潮公告查询 API 支持「公告类别（category）+ 全文搜索（searchkey）」，因此财报与招股书复用同一接口——招股书只是换了筛选规则、去掉了按年份定位的逻辑（招股书是上市时的一次性文件）。

## 📜 招股书筛选规则

- 必含「招股说明书」+ 首发特征（「首次公开发行」；早期老式标题由宽松逻辑兜底）
- 排除：H股 / 港股 / 确认意见 / 意向书 / 摘要 / 英文 / 过程稿（申报稿/上会稿/注册稿）/ 再融资（配股/增发/可转债）
- 全时段宽日期范围搜索（上市时点因公司而异）

## 🗺️ 路线图

- [x] **v1**：巨潮已上市公司首发招股书 + 全部财报 + 港股财报
- [ ] **v2**：再融资招股书（配股/增发/可转债）+ 港股招股章程
- [ ] **v3**：IPO 审核中预披露招股书（证监会/交易所审核系统，需 Playwright）

## 📌 数据源与免责

- 数据来源：[巨潮资讯网](http://www.cninfo.com.cn) / [东方财富研报中心](https://data.eastmoney.com/report) / [HKEX 披露易](https://www1.hkexnews.hk)，均为官方/权威免费渠道。
- 所有 PDF 版权归原公告作者公司所有，本工具仅供个人学习研究使用，**非投资建议**。

## 📄 License

MIT
