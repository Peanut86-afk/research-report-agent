"""research_agent_agentkit.py - AgentKit (VeADK) version of the investment research agent.

Same tools as the plain-loop research_report_agent.py, shared from the
research_tools.py module (the 'Tools' layer), orchestrated by the AgentKit
Runner instead of a hand-written agent loop.

Harness structure (for interview reference):
    Agent      = veadk.Agent(instruction=SYSTEM, tools=[...])
    Tools      = research_tools.py (5 pure-urllib functions)
    Memory     = AgentKit session (same session_id keeps conversation)
    Knowledge  = report_lib/ (BM25 RAG) + data/ + online EastMoney
    Guardrails = mandatory [doc:N] citation, data layering, no fabrication

Usage:
    1. Configure credentials first (veadk needs Volcengine AK/SK):
         set VOLC_ACCESSKEY=... and VOLC_SECRETKEY=...   (or VeFaaS IAM on cloud)
    2. Run with the venv python that has veadk installed:
         <repo>\\agents\\agentkit-demo\\.venv\\Scripts\\python.exe research_agent_agentkit.py 600256
    3. Deploy to cloud (requires SSO):
         agentkit launch
"""

import json
import sys
from pathlib import Path

from agentkit.apps import AgentkitSimpleApp
from google.adk.tools import FunctionTool
from veadk import Agent, Runner

from research_tools import (
    MODEL,
    get_financials,
    get_quote,
    retrieve_report,
    search_financials,
    search_reports,
)

# ----------------------------------------------------------------- system ----
SYSTEM = (
    "You are a professional sell-side equity analyst (中文券商分析师). "
    "You write Chinese investment research reports for A-share companies. "
    "Report structure: header block, core views, financial summary table, "
    "company overview, per-business analysis, earnings forecast & "
    "recommendation, risk factors, appendix. Every number must come from a "
    "tool result; never invent data. Mark any missing figure as [需补充]. "
    "Distinguish facts from forecasts and always state forecast assumptions. "
    "Always include a risk section and end the report with the disclaimer: "
    "本报告为学习用途的模拟研报，不构成投资建议。 "
    "Use get_quote for market data, get_financials for local financials, "
    "retrieve_report to search the local broker-report library, and "
    "search_reports / search_financials to fetch REAL online data when local "
    "data is missing. "
    "RAG RULE: before writing each section, call retrieve_report for the "
    "relevant topic (e.g. '马朗煤矿 产能', '天然气 长协', '盈利预测 上调'), and every "
    "claim from a retrieved chunk MUST be cited with its [doc:N] handle. "
    "Facts from retrieved chunks are real broker-report content - quote "
    "them, do not paraphrase their numbers. "
    "DATA LAYERING (non-negotiable): (1) prefer local data/ financials and "
    "report_lib/ chunks for the target stock; (2) if local data is missing, "
    "call search_financials / search_reports to get REAL online data instead "
    "of leaving gaps; (3) online financials are DISCLOSED ACTUALS, not "
    "forecasts - label any forward-looking number as an assumption; (4) only "
    "mark [需补充] when neither local nor online sources have the figure. "
    "If the report needs earlier years (e.g. 2020A for a 5-year trend), call "
    "search_financials with start_year=2020 instead of marking [需补充]. "
    "The local report_lib/ chunks belong to a SPECIFIC company - if the target "
    "stock differs from that company, do NOT cite its chunks; use "
    "search_reports instead. "
    "Reply in Chinese."
    "DERIVED METRICS (non-negotiable): when the underlying figures are "
    "present, compute derived metrics yourself - YoY, net margin, EPS, implied "
    "PE etc - and label them (自行计算); use search_financials(start_year=...) "
    "to fetch earlier years if needed. Never mark a derivable metric as [需补充]. "
    "FORECAST HORIZON: default forecast window is 2026E-2027E; add a third "
    "year (2028E) ONLY when you have a concrete basis (capacity/project "
    "schedule or broker forecast), otherwise omit it or label it "
    "远期外推示意（可靠性递减，审慎使用）. "
    "FORECAST MARKING (non-negotiable): label every forecast figure with "
    "'预测' at its column/period (e.g. 2026E（预测）), and state its source "
    "below the table: 预测来源：本报告模拟测算（假设见预测章节），非券商一致预期 "
    "for own assumptions, or 预测来源：券商名 报告日期 研报（评级） "
    "(e.g. 预测来源：太平洋证券 2026-06-30 研报（买入）) when citing "
    "an institution's forecast."
    "DATA-DRIVEN REQUIREMENTS (non-negotiable): "
    "(1) Per-business section: for EACH business, cite sales volume, revenue "
    "and gross margin from the financials tool output, compute the YoY trend "
    "across 2024A-2025A-2026E-2027E-2028E, and explain the driver logic "
    "(capacity release / price spread / project ramp-up). Never describe a "
    "segment without numbers. "
    "(2) Earnings forecast section: state 2026E-2028E net profit attributable "
    "(亿元), EPS and implied PE; if the tool output contains a previous "
    "forecast, show the upgrade explicitly (原预测 vs 新预测) and why. "
    "(3) Valuation section: compare the company's forward PE/PB with the "
    "peer-average PE from the tool output and conclude on valuation position. "
    "(4) Financial summary: use the three statements and ratio data as-is "
    "from the tool output. "
    "(5) A report full of [需补充] placeholders is a FAILURE: whenever the "
    "financials tool returns data, fill every section with real figures."
)

# ------------------------------------------------------- AgentKit assembly ----
get_quote_tool = FunctionTool(func=get_quote)
get_financials_tool = FunctionTool(func=get_financials)
retrieve_report_tool = FunctionTool(func=retrieve_report)
search_reports_tool = FunctionTool(func=search_reports)
search_financials_tool = FunctionTool(func=search_financials)

agent = Agent(
    name="researchReportAgent",
    description="Generates A-share deep research reports (broker-style, data-driven, RAG + online search).",
    instruction=SYSTEM,
    model_name="doubao-seed-evolving",
    model_api_base="https://ark.cn-beijing.volces.com/api/v3/",
    tools=[
        get_quote_tool,
        get_financials_tool,
        retrieve_report_tool,
        search_reports_tool,
        search_financials_tool,
    ],
)
runner = Runner(agent=agent)

app = AgentkitSimpleApp()


@app.entrypoint
async def run(payload: dict, headers: dict) -> str:
    """Cloud entrypoint: prompt -> runner.run with platform session memory."""
    prompt = payload["prompt"]
    user_id = headers["user_id"]
    session_id = headers["session_id"]
    return await runner.run(messages=prompt, user_id=user_id, session_id=session_id)


@app.ping
def ping() -> str:
    return "pong!"


def main(argv: list) -> int:
    """Local smoke test: run the agent on a stock code."""
    if not argv:
        print(__doc__)
        return 1
    code = argv[0]
    import asyncio

    async def go() -> str:
        resp = await runner.run(
            messages=f"请为 A 股公司 {code} 生成一份完整的公司深度研究报告，"
                     f"先调用工具获取行情与财务数据，再输出 Markdown 报告。",
            user_id="local_smoke_test",
            session_id="s_smoke",
        )
        return resp

    report = asyncio.run(go())
    report = report.rstrip() + (
        "\n\n---\n*本报告为学习用途的模拟研报，不构成投资建议。*"
    )
    out_path = f"report_agentkit_{code}.md"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(report)
    print(report)
    print(f"\n[report saved to {out_path}]")
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 1:
        sys.exit(main(sys.argv[1:]))
    app.run(host="0.0.0.0", port=8000)
