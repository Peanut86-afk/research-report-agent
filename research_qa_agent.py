# -*- coding: utf-8 -*-
"""research_qa_agent.py - Interactive Q&A loop over the research-report agent.

A ReAct-style loop: the user asks a follow-up question about a report, the
model decides which tool to call (retrieve_report / search_financials /
search_reports / get_quote), tool results carry the EVIDENCE back, and the
model answers WITH references ([doc:N] or explicit data source). If no
source has the answer, the model says so instead of making it up.

Usage:
    python research_qa_agent.py 600256        # start chat about 600256
    # then type questions; 'exit' or Ctrl+C to quit

Design notes (agent-harness structure):
    - Agent:  LLM + SYSTEM prompt (evidence-first rules)
    - Tools:  the 5 tools reused from research_report_agent
    - Memory: conversation messages accumulate across turns (short-term)
    - Knowledge: report_lib/ (BM25) + data/ + online (EastMoney)
    - Guardrails: mandatory citation, honest 'no evidence' answer
"""
import json
import sys

from research_report_agent import (
    TOOLS,
    call_llm,
    get_financials,
    get_quote,
    retrieve_report,
    search_financials,
    search_reports,
)

SYSTEM = (
    "You are a Chinese investment-research assistant. The user has just read a "
    "broker-style research report and asks follow-up questions about it. "
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


def _run_round(messages: list) -> str:
    """One model turn: let the model call tools as many times as it wants,
    then return its final text answer."""
    for _ in range(6):
        reply = call_llm(messages)
        choice = reply["choices"][0]
        msg = choice["message"]
        if msg.get("tool_calls"):
            messages.append({"role": "assistant",
                             "content": msg.get("content") or "",
                             "tool_calls": msg["tool_calls"]})
            results = []
            for tc in msg["tool_calls"]:
                fn = tc.get("function", {})
                name, args = fn.get("name", ""), json.loads(fn.get("arguments") or "{}")
                impl = {
                    "get_quote": get_quote,
                    "get_financials": get_financials,
                    "retrieve_report": retrieve_report,
                    "search_reports": search_reports,
                    "search_financials": search_financials,
                }.get(name)
                out = f"error: unknown tool {name}" if impl is None else impl(**args)
                results.append({"role": "tool",
                                "tool_call_id": tc.get("id", ""),
                                "content": str(out)})
            messages.extend(results)
            continue
        return msg.get("content") or "[空回答]"
    return "[agent] too many tool rounds without a final answer"


def main(argv: list) -> int:
    if not argv:
        print(__doc__)
        return 1
    code = argv[0]
    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user",
         "content": f"我们刚讨论了 {code} 的投资研究报告。你现在回答我对这份报告内容的追问，"
                    f"先调用工具核实依据再回答，引用必须带来源。若找不到依据就明确说没有。"},
    ]
    print(f"研报问答助手已就绪（标的 {code}）。输入问题，'exit' 退出。")
    try:
        while True:
            q = input("> ").strip()
            if q.lower() in ("exit", "quit", "q"):
                break
            if not q:
                continue
            messages.append({"role": "user", "content": q})
            answer = _run_round(messages)
            messages.append({"role": "assistant", "content": answer})
            print("\n" + answer + "\n")
    except (EOFError, KeyboardInterrupt):
        print("\nbye")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
