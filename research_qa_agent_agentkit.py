"""research_qa_agent_agentkit.py - AgentKit (VeADK) follow-up Q&A loop.

The user has just read a generated research report and asks follow-up
questions. Each turn the agent decides which tool to call, tool results carry
the evidence, and the answer MUST cite sources ([doc:N] / data source). If no
source has the answer, the agent says so instead of making it up.

This is the SAME ReAct loop as the plain-loop research_qa_agent.py, but the
loop itself is now owned by the AgentKit Runner, and the conversation memory
is the AgentKit session (same session_id across turns).

Harness structure (for interview reference):
    Agent      = veadk.Agent(instruction=QA_SYSTEM, tools=[...])
    Tools      = research_tools.py (5 pure-urllib functions)
    Memory     = AgentKit session: same session_id accumulates the dialogue
    Knowledge  = report_lib/ (BM25 RAG) + data/ + online EastMoney
    Guardrails = mandatory citation, honest 'no evidence' answer, fact/inference split

Usage:
    1. Credentials: veadk needs Volcengine AK/SK env vars (see README).
    2. Run with the venv python that has veadk:
         <repo>\\agents\\agentkit-demo\\.venv\\Scripts\\python.exe research_qa_agent_agentkit.py 600256
    3. Type questions; 'exit' to quit.
"""

import asyncio
import sys

from agentkit.apps import AgentkitSimpleApp
from google.adk.tools import FunctionTool
from veadk import Agent, Runner

from research_tools import (
    get_financials,
    get_quote,
    retrieve_report,
    search_financials,
    search_reports,
)

QA_SYSTEM = (
    "You are a Chinese investment-research assistant. The user has just read "
    "a broker-style research report and asks follow-up questions about it. "
    "Answer rules (non-negotiable): "
    "(1) EVIDENCE FIRST: every factual claim must come from a tool result. "
    "Cite the source next to each claim: research-library chunks as [doc:N], "
    "live quotes as (get_quote, timestamp), financials as (source, report "
    "period), online reports as (broker, title, date). "
    "(2) If the local report library or online sources do not contain the "
    "answer, say clearly: 没有找到相关依据 and explain what IS available. "
    "Never invent numbers, events or ratings. "
    "(3) Distinguish facts from your own inference; label inference as 推断. "
    "(4) The local library chunks belong to a specific company - when the "
    "question is about a different company, use search_reports / "
    "search_financials instead and do NOT cite those chunks. "
    "(5) Be concise: answer the question directly, do not re-print the whole "
    "report. Reply in Chinese."
)

get_quote_tool = FunctionTool(func=get_quote)
get_financials_tool = FunctionTool(func=get_financials)
retrieve_report_tool = FunctionTool(func=retrieve_report)
search_reports_tool = FunctionTool(func=search_reports)
search_financials_tool = FunctionTool(func=search_financials)

agent = Agent(
    name="researchQaAgent",
    description="Answers follow-up questions about a generated research report, with cited evidence from the report library and online sources.",
    instruction=QA_SYSTEM,
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
    """Cloud entrypoint: a follow-up question -> runner.run with platform
    session memory (same session_id = same conversation, kept by AgentKit)."""
    prompt = payload["prompt"]
    user_id = headers["user_id"]
    session_id = headers["session_id"]
    return await runner.run(messages=prompt, user_id=user_id, session_id=session_id)


@app.ping
def ping() -> str:
    return "pong!"


def main(argv: list) -> int:
    """Interactive CLI: same session_id => AgentKit keeps the conversation."""
    if not argv:
        print(__doc__)
        return 1
    code = argv[0]
    print(f"研报问答助手已就绪（AgentKit，标的 {code}）。输入问题，'exit' 退出。")

    async def ask(q: str) -> str:
        resp = await runner.run(
            messages=q,
            user_id="local_qa_user",
            session_id=f"qa_{code}",
        )
        return resp

    try:
        while True:
            q = input("> ").strip()
            if q.lower() in ("exit", "quit", "q"):
                break
            if not q:
                continue
            answer = asyncio.run(ask(q))
            print("\n" + str(answer) + "\n")
    except (EOFError, KeyboardInterrupt):
        print("\nbye")
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 1:
        sys.exit(main(sys.argv[1:]))
    app.run(host="0.0.0.0", port=8001)
