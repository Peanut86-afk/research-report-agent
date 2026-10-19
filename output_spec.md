# Investment Research Report Output Spec

Derived from a real broker deep-report template (Kysec / 开源证券, company deep report on 广汇能源 600256.SH, 2026-10-03). This spec defines the exact structure, section patterns and data rules the research-report agent must follow when generating a report.

## 0. Language & Form

- Report body is written in **Simplified Chinese** (professional sell-side style).
- The agent emits **one Markdown document** per report.
- Every number must come from a tool result, the user's input, or a cited source. Anything not sourced must be marked `[需补充]` and its missing nature stated. **Never invent financial figures.**
- Forecasts must state their assumptions inline (e.g. "假设马朗煤矿核增顺利推进").

## 1. Report Header (首页数据块)

Order fixed:

| Field | Source |
|---|---|
| 机构 · 报告类型 | template constant |
| 报告标题：`{公司简称}：{一句话投资逻辑}` | LLM summary of the thesis |
| 报告日期 · 行业归属 | tool / input |
| 投资评级：`{评级}（{维持/上调/首次}）` | LLM judgment + reasoning |
| 分析师 / 联系人 / 证书编号 | config or `[需补充]` |
| 当前股价 / 一年最高最低 / 总市值 / 流通市值 / 总股本 / 流通股本 | `get_quote` result |
| 核心观点（2-4 条 bullet） | LLM; each bullet = `{板块或主题}：{亮点} + {关键数据支撑}` |
| 财务摘要和估值指标表 | `get_financials` result |

Core-views bullet formula (from the source report):
- `●煤炭业务：富油煤资源优势突出，产能扩张量利提升`
- `●其他业务：天然气释放盈利弹性，煤化工装置提效及项目建设贡献增量`
- `●风险提示：环保政策风险，煤价上涨不及预期，生产安全事故`

Financial summary table (columns `2024A / 2025A / 2026E / 2027E / 2028E`):
营业收入(百万元)、YOY(%)、归母净利润(百万元)、YOY(%)、毛利率(%)、净利率(%)、ROE(%)、EPS(摊薄/元)、P/E(倍)、P/B(倍)

> **Derived-metric rule:** YOY, 净利率, EPS, 隐含 PE 等派生指标只要底层数据齐（含
> 更早年份，可用 `search_financials(start_year=…)` 取），必须自行计算并标注
> **（自行计算）**，不得标 `[需补充]`。只有底层数据本身缺失时才允许 `[需补充]`。
> **Forecast-window rule:** 默认预测窗口为 **2026E–2027E 两年**。第三年（2028E）
> 仅在存在具体依据（如项目投产时间表、券商预测）时给出；否则要么省略，要么
> 标注 **远期外推示意（可靠性递减，审慎使用）**，不得与机构预测同权呈现。
> **Forecast-marking rule:** 每个预测数值所在的**列头/期次必须显式标注"预测"**
> （如 `2026E（预测）`、`2027E（预测）`），且表格下方注明预测**来源**：
> 本报告模拟测算（假设见预测章节）→ 写"预测来源：本报告模拟测算（假设见 §5.1），
> 非券商一致预期"；引用机构预测 → 写"预测来源：{券商} {日期}研报（{评级}）"。

## 2. Table of Contents (目录)

Seven chapters, numbered, with 附：财务预测摘要. Chapter titles use the pattern `{序号}、{核心资源或模式}，{成长逻辑}`:

1. 国家级稀缺{资源}，全产业链协同闭环（公司概况）
2. {业务一}：{资源优势}，{成长潜力}（2.1 资源储备/产能扩张；2.2 定价模式/盈利空间）
3. {业务二}：{双轮/模式驱动}，锁定高价值市场
4. {业务三}：产业链纵深延伸，周期对冲增厚盈利
5. 绿色/新业务：{新曲线}打开第二增长曲线
6. 盈利预测与投资建议（6.1 盈利预测；6.2 投资建议）
7. 风险因素
附：财务预测摘要

## 3. Company Overview (第 1 章)

- **3.1 转型与发展史**：时间轴叙事（成立→上市→进入行业→重大项目节点→绿色转型），一句话概括公司定位。
- **3.2 股权与治理**：实际控制人、主要股东及持股比例、股权流动性一句话结论。
- **全产业链协同逻辑**：资源（国内+海外双区域）→加工→运输→销售一体化闭环，2-3 句说明协同如何转化为成本/定价优势。

## 4. Business-Section Analysis Pattern (第 2-5 章，每业务一节的固定模式)

Each business section follows the same skeleton:

1. **章节标题公式**：`{业务}：{资源/模式定语}，{成长动词+结果}`（如 "煤炭业务：富油煤资源优势突出，产能扩张成长潜力突出"）。
2. **经营数据段**（一段话，必须全部来自工具/来源）：
   - 营收/毛利/毛利率 + yoy；占公司营收/毛利比重 + pct 变化
   - 细分产品产/销量 + yoy + 占比
   - 吨/方级单位盈利：`吨价/吨成本/吨毛利 + yoy`
3. **表格**（如资源储量、产能、重点项目）：
   - 表格模板：主体 | 区位 | 股权比例 | 概况/产能/进度
4. **横向对比**：与 4-10 家可比公司对比一个关键指标（毛利率/费用率/ROE/可采年限/长协比例/股息率），给出"居行业前列/中游/低位"的定性结论。
5. **前瞻与催化**：在建项目时间表（投产年份）、产能规划、价格展望，说明对后续盈利的影响。

Chart titles in the source follow `图N：2026H1公司XX业务实现营收XX亿元，同比+XX%` — the agent renders numbers into text; actual charts are optional (marked `[图表占位]`).

## 5. Earnings Forecast & Investment Recommendation (第 6 章)

- **6.1 盈利预测**：分业务给出预测（**默认 2026E–2027E；2028E 仅在存在产能/项目进度等依据时给出，否则省略或标注"远期外推示意"**），每业务一段：
  - 假设句（产能/销量/价格假设）
  - `预计2026-2028年{业务}销量X/X/X，营收X/X/X亿元，毛利X/X/X亿元`
  - 汇总表（分业务 销量/营收/成本 × 2024-2028E + 合计）
- **6.2 投资建议**：
  - 上调/维持/下调预测说明（含原预测对比：`原2026-2028年预测分别为X/X/X亿元`）
  - `预计2026-2028年归母净利润分别为X/X/X亿元，EPS分别为X/X/X元，当前股价对应PE分别为X/X/X倍`
  - 估值对比表（可比公司 收盘价/EPS/PE × 2025-2028E + 行业均值 + 目标公司），注明数据口径（Wind 一致预测 vs 本所预测）
  - 结论句：`维持"买入"评级`

## 6. Risk Factors (第 7 章)

3-4 条编号风险，每条格式：`（N）{风险名}：{机制一句话}`。来源报告示例：
- （1）环保政策风险：节能降碳政策若对煤炭需求压制超预期，影响公司业绩。
- （2）煤价上涨不及预期：国内煤价上行不及预期，则对公司利润释放产生负面影响。
- （3）生产安全事故：公司煤矿若遇严重生产安全事故，则面临停产整顿风险。

## 7. Appendix: Financial Forecast Summary (附)

Three statements + ratios, columns `2024A-2028E`:
- 资产负债表：流动资产/现金/应收/存货/固定资产/资产总计/负债/股东权益…
- 利润表：营业收入/营业成本/费用/营业利润/净利润/归母净利润/EBITDA/EPS…
- 现金流量表：经营/投资/筹资活动现金流…
- 主要财务比率：成长能力(营收/营业利润/归母净利润 yoy)、盈利能力(毛利率/净利率/ROE/ROIC)、偿债能力(资产负债率/净负债比率/流动/速动比率)、营运能力(总资产/应收/应付周转率)、每股指标(EPS/每股经营现金流/每股净资产)、估值比率(P/E、P/B、EV/EBITDA)

If the tool cannot supply full statements, render what is available and mark the rest `[需补充]`.

## 8. Compliance & Honesty Rules

- **事实 vs 判断分离**：经营数据与预测判断必须分开表述；预测句一律带假设。
- **来源标注**：每个数据块末尾标注来源（如 `数据来源：Wind、公司公告`、`数据来源：工具get_quote`）。
- **不编造**：无数据的指标写 `[需补充]`，不得用"约/大概"代替缺失数据。
- **派生指标自算**：底层数据齐的派生指标（同比/净利率/EPS/PE 等）必须自行计算并标注（自行计算），能用更早年份数据算的（`search_financials(start_year=…)`）不算"缺失"。
- **预测窗口克制**：预测默认两年（2026E–2027E），第三年必须有依据或明确标注"远期外推示意"。
- **预测显式标注**：每个预测数值所在的列头/期次标"预测"，并注明来源（本报告模拟测算+假设章节 / 券商+日期+评级）。
- **风险必列**：报告必须有"风险因素"章节，不得只讲利好。
- 报告仅供学习研究，结尾附免责声明：`本报告为学习用途的模拟研报，不构成投资建议。`
