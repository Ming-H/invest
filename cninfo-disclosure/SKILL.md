---
name: cninfo-disclosure
description: 下载上市公司信息披露与券商研报——财报（年报/半年报/一季报/三季报）、招股说明书（首发 IPO 招股书）、券商研报清单。财报/招股书走巨潮资讯网，港股财报走 HKEX 披露易，券商研报走东方财富。按公司名称或代码查询，自动筛选正本（排除摘要/更正/H股等）。当用户需要下载财报/年报/季报、招股说明书/招股书 PDF、查看券商研报/卖方研报/分析师评级/盈利预测、查看公告原文时使用。
license: Complete terms in LICENSE.txt
---

# 巨潮信息披露下载 使用指南

## 版本

`3.1.0`（新增券商研报清单：东方财富研报中心，输出 CSV+Markdown，含评级/分析师/盈利预测/PDF直链）

## 技能概述

本技能是「信息披露 + 研报」下载底座，统一支持三类文档：

- **财报**（`reports.py`）：年报 / 半年报 / 一季报 / 三季报 —— 按 **股票 + 年份** 定位（巨潮）
- **招股书**（`prospectus.py`）：首发招股说明书（IPO）—— 按 **股票** 定位（**无需年份**，巨潮）
- **券商研报**（`research.py`）：卖方研报清单 —— 按 **股票** 定位（**无需年份**，东方财富），输出 CSV+Markdown，含评级/分析师/盈利预测/PDF直链

港股财报走 HKEX 披露易（`hkex.py`，需 Playwright）。

数据来源：
- **A股公告**：巨潮资讯网（http://www.cninfo.com.cn）— 证监会指定信息披露平台
- **券商研报**：东方财富研报中心（https://data.eastmoney.com/report）— 卖方研报聚合
- **港股**：HKEX 披露易（https://www1.hkexnews.hk）— 港交所

## 架构（底座 + 子模块）

```
scripts/
├── cninfo_client.py   # 共享底座：巨潮公告查询API + PDF下载 + 通用工具（纯标准库）
├── reports.py         # 财报（annual/semi/q1/q3）
├── prospectus.py      # 招股书（首发IPO招股书）
├── research.py        # 券商研报（东方财富，输出 CSV+Markdown 清单）
├── hkex.py            # 港股（Playwright 驱动 HKEX 披露易）
└── cli.py             # 统一入口（search / download）
```

## 用法

### 财报（需 `--year`）

```bash
# 年报（默认）
python3 scripts/cli.py download --stock "三一重工" --year 2025 -o /tmp/

# 半年报 / 一季报 / 三季报
python3 scripts/cli.py download --stock "三一重工" --year 2025 --type semi -o /tmp/
python3 scripts/cli.py download --stock "徐工机械" --year 2025 --type q1 -o /tmp/

# 批量：多年（范围/枚举）或多年多类型
python3 scripts/cli.py download --stock "三一重工" --year 2023-2025 -o /tmp/
python3 scripts/cli.py download --stock "三一重工" --year 2025 --type all -o /tmp/

# 只搜索不下载
python3 scripts/cli.py search --stock "三一重工" --year 2025
```

### 招股书（无需 `--year`）

```bash
# 下载首发招股说明书（公司名或代码均可）
python3 scripts/cli.py download --stock "宁德时代" --type prospectus -o /tmp/
python3 scripts/cli.py download --stock 600519 --type prospectus -o /tmp/      # 茅台（2001年早期也有）

# 只搜索
python3 scripts/cli.py search --stock "宁德时代" --type prospectus
```

返回示例（招股书）：

```json
{
  "success": true,
  "stock": "宁德时代",
  "doc_type": "prospectus",
  "report_label": "招股说明书",
  "board": "创业板",
  "title": "首次公开发行股票并在创业板上市招股说明书",
  "date": "2018-05-29",
  "pdf_url": "https://static.cninfo.com.cn/finalpage/2018-05-29/1205010303.PDF",
  "downloaded": "/tmp/宁德时代_招股说明书.pdf",
  "file_size": "13.5MB"
}
```

### 券商研报（无需 `--year`，东方财富）

```bash
# 下载券商研报清单（公司名或代码均可），落地 CSV + Markdown
python3 scripts/cli.py download --stock "艾力斯" --type research -o /tmp/
python3 scripts/cli.py download --stock 688578 --type research -o /tmp/

# 只搜索（输出 JSON 研报列表）
python3 scripts/cli.py search --stock "688578" --type research
```

输出为**研报清单**（CSV + Markdown），含：日期 / 机构 / 分析师 / 评级 / 报告标题 / 行业 / 盈利预测（EPS、PE）/ 每篇研报的 **PDF 直链**。研报模块输出清单而非单个 PDF（每篇 PDF 链接已在清单中，可按需下载）。

返回示例（研报）：

```json
{
  "success": true,
  "stock": "688578",
  "doc_type": "research",
  "report_label": "券商研报",
  "stock_name": "艾力斯",
  "stock_code": "688578",
  "total": 17,
  "date_span": "2023-09-13 ~ 2026-05-09",
  "org_count": 6,
  "analyst_count": 8,
  "csv": "/tmp/艾力斯_券商研报清单.csv",
  "markdown": "/tmp/艾力斯_券商研报清单.md"
}
```

### 港股财报

```bash
python3 scripts/cli.py download --stock 01888 --year 2025 -m hk -o /tmp/                 # 年报
python3 scripts/cli.py download --stock 01888 --year 2025 --type interim -m hk -o /tmp/  # 中期报告
```

- **市场判断**（自动）：6位代码=A股、4-5位代码=港股、名称默认 A股（港股请用代码或 `-m hk`）。
- **港股报告类型**：只有 `annual`（年报）和 `interim`（中期报告），**无 q1/q3**（港交所不要求季报）。
- **依赖**：港股需 `pip install playwright`（用系统 Chrome，**无需** `playwright install chromium`）。A股仍为零依赖。

## 核心处理流程

1. **接收请求**：股票名称/代码 + 文档类型（财报还需年份）。
2. **判断文档类型**：prospectus → `prospectus.py`；research → `research.py`；财报 → `reports.py`；港股 → `hkex.py`。
3. **搜索公告**：调用巨潮公告查询 API（`cninfo_client.query_with_stock`，自动判断交易所、名称时两市都查并去重）。
4. **筛选正本**：按文档类型的 include/exclude 规则筛掉摘要、更正、干扰公告。
5. **下载 PDF**：校验 `%PDF-` 头，落地本地。
6. **返回 JSON**：含 success / 标题 / 日期 / pdf_url / 本地路径 / 文件大小。

### 招股书筛选规则（`prospectus.filter_prospectus`）

- 必含 **"招股说明书"**。
- 首发特征：含 **"首次公开发行"**（早期老式标题如茅台 2001 仅有"招股说明书"无此前缀，由第二轮宽松逻辑兜底）。
- 排除：H股 / 港股 / 境外 / 全球发售 / 确认意见 / 意向书 / 摘要 / 英文 / 撤销 / 中止 / 终止 / 失效 / 附录 / 附件 / 申报稿 / 上会稿 / 注册稿 / 反馈意见 / 配股 / 增发 / 可转债。
- 全时段宽日期范围搜索（上市时点因公司而异，可能很早）。
- 自动识别上市板块（科创板 / 创业板 / 主板 / 北交所）。

## CLI 参数

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `command` | STRING | 是 | `search`（搜索）或 `download`（下载）|
| `--stock` / `-s` | STRING | 是 | 股票名称或代码（如 "三一重工" / "600031" / "01888"）|
| `--year` / `-y` | STRING | 财报必填 | 年份：单年(2025)/范围(2023-2025)/枚举(2023,2025)。**招股书无需** |
| `--type` / `-t` | STRING | 否 | 文档类型（见下表，默认 annual）|
| `--output` / `-o` | STRING | 否 | 下载目录，仅 download（默认当前目录）|
| `--market` / `-m` | STRING | 否 | auto / a / hk，默认 auto |
| `--timeout` | INT | 否 | 请求超时秒数（默认 30）|

## 文档类型说明

| 类型 | 参数 | 说明 |
|------|------|------|
| 年报 | `annual`（默认） | 年度报告，次年 3-4 月发布 |
| 半年报 | `semi` | 半年度报告，当年 7-8 月 |
| 一季报 | `q1` | 第一季度报告，当年 4-5 月 |
| 三季报 | `q3` | 第三季度报告，当年 10-11 月 |
| **招股书** | `prospectus` | 首发招股说明书，**无需年份**（巨潮） |
| **券商研报** | `research` | 卖方研报清单，**无需年份**（东方财富） |
| 全部财报 | `all` | 年报+半年报+一季报+三季报 |
| 港股中期 | `interim` | 港股中期报告（仅港股，加 `-m hk`）|

## 空数据 / 错误处理

| 场景 | 处理方式 |
|------|---------|
| 招股书未找到 | 老公司（2008 年前）可能无电子版 → 引导到上交所/深交所官网；未上市公司（IPO 审核中）→ 证监会/交易所审核系统预披露 |
| 找到公告但无正本 | 返回 `available` 列表（相关公告标题/链接），由用户选择 |
| 财报未找到 | 提示报告可能未发布，或公司名/年份有误，引导访问巨潮 |
| 研报未找到 | 冷门标的可能暂无券商覆盖 → 引导访问东方财富研报中心核实 |
| 网络/反爬 | 提示重试或浏览器手动下载 |

## 数据来源标注

- 引用数据必须标注来源：**巨潮资讯网**（http://www.cninfo.com.cn）/ **东方财富研报中心**（https://data.eastmoney.com/report）/ **HKEX 披露易**（https://www1.hkexnews.hk）。
- 所有 PDF 版权归原公告作者公司所有，本工具仅供个人学习研究使用。

## 路线图

- **v1（本期）**：巨潮已上市公司**首发招股书** + 全部财报 + 港股财报。
- **v2**：再融资招股书（配股 / 增发 / 可转债）+ 港股招股章程。
- **v3**：IPO 审核中预披露招股书（证监会 / 交易所审核系统，需 Playwright）。

## 代码结构

```
cninfo-disclosure/
├── SKILL.md              # Skill 定义文件
├── README.md             # 项目说明
├── LICENSE.txt           # MIT 许可证
├── .gitignore
└── scripts/
    ├── cninfo_client.py  # 共享底座（巨潮 API + PDF 下载 + 工具）
    ├── reports.py        # 财报
    ├── prospectus.py     # 招股书
    ├── research.py       # 券商研报（东方财富）
    ├── hkex.py           # 港股（Playwright）
    └── cli.py            # 统一入口
```
