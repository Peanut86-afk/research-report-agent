"""research_report_agent.py - LLM-driven investment research report agent.

Generates a broker-style deep report (Chinese) following output_spec.md,
which was distilled from a real Kysec deep report (600256.SH, 2026-10-03).

Pipeline (agent loop):
    user asks for a report on a stock
        -> LLM decides which tools to call (get_quote / get_financials)
        -> tools return real data, fed back to the LLM
        -> LLM writes the report strictly per output_spec.md

Security design (same rules as our other agents):
    - API key lives in config.json, never in source code.
    - Stock codes are validated against a whitelist regex before any URL is built.
    - No shell execution, no arbitrary URLs.
    - Financial data either comes from a local data/ file (user-provided,
      read-only) or is reported as unavailable - never fabricated.
    - Tool results are only data; the LLM cannot modify files or run commands.

Usage:
    python research_report_agent.py 600256         # by A-share code
    python research_report_agent.py 600256 "广汇能源"   # code + optional name hint
"""
import json
import math
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

# ---------------------------------------------------------------- config ----
with open("config.json", encoding="utf-8") as f:
    _cfg = json.load(f)

API_KEY = _cfg["api_key"]          # required - fail fast if missing
MODEL = _cfg.get("model", "doubao-seed-evolving")
BASE_URL = _cfg.get("base_url", "https://ark.cn-beijing.volces.com/api/v3")
TIMEOUT = 300
MAX_ROUNDS = 6
MAX_TOKENS = 12000

# ----------------------------------------------------------------- system ----
SYSTEM = (
    "You are a professional sell-side equity analyst (中文券商分析师). "
    "You write Chinese investment research reports for A-share companies. "
    "You MUST follow the report structure and rules in output_spec.md exactly: "
    "header block, core views, financial summary table, company overview, "
    "per-business analysis, earnings forecast & recommendation, risk factors, "
    "appendix. Every number must come from a tool result; never invent data. "
    "Mark any missing figure as [需补充]. Distinguish facts from forecasts and "
    "always state forecast assumptions. Always include a risk section and end "
    "the report with the disclaimer: 本报告为学习用途的模拟研报，不构成投资建议。 "
    "Use get_quote for market data, get_financials for local financials, "
    "retrieve_report to search the local broker-report library, and "
    "search_reports / search_financials to fetch REAL online data when local "
    "data is missing. "
    "RAG RULE: before writing each section, call retrieve_report for the "
    "relevant topic (e.g. '马朗煤矿 产能', '天然气 长协', '盈利预测 上调'), and every "
    "claim that comes from a retrieved chunk MUST be cited with its [doc:N] "
    "handle. Facts from retrieved chunks are real broker-report content - "
    "quote them, do not paraphrase their numbers. "
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
    "for own assumptions, or 预测来源：{券商} {日期}研报（{评级}） when citing "
    "an institution's forecast."
    "DATA-DRIVEN REQUIREMENTS (non-negotiable): "
    "(1) Per-business section: for EACH business, cite its sales volume, revenue "
    "and gross margin from the financials tool output, compute the YoY trend "
    "across 2024A-2025A-2026E-2027E-2028E, and explain the driver logic "
    "(capacity release / price spread / project ramp-up) behind the forecast. "
    "Never describe a segment without numbers. "
    "(2) Earnings forecast section: state 2026E-2028E net profit attributable "
    "(亿元), EPS and implied PE; if the tool output contains a previous forecast, "
    "show the upgrade explicitly (原预测 vs 新预测) and why. "
    "(3) Valuation section: compare the company's forward PE/PB with the "
    "peer-average PE from the tool output and conclude on valuation position. "
    "(4) Financial summary: use the three statements and ratio data (gross "
    "margin, net margin, ROE, debt ratio, OCF) as-is from the tool output. "
    "(5) A report with empty [需补充] placeholders is a FAILURE: whenever the "
    "financials tool returns data, fill every section with real figures."
)

# ------------------------------------------------------------------ tools ----
# A-share code whitelist: SH 6xxxxx, SZ 0xxxxx / 3xxxxx, BJ 8/4/9xxxxx
_CODE_RE = re.compile(r"^(6\d{5}|0\d{5}|3\d{5}|8\d{5}|4\d{5}|9\d{5})$")


def _market_prefix(code: str) -> str:
    """Map an A-share code to its Tencent quote market prefix."""
    if code.startswith("6"):
        return "sh"
    if code.startswith(("0", "3")):
        return "sz"
    return "bj"


def get_quote(code: str) -> str:
    """Fetch live quote from Tencent's free endpoint; returns a flat summary."""
    if not _CODE_RE.match(code):
        return f"error: invalid A-share code {code!r}"
    url = f"http://qt.gtimg.cn/q={_market_prefix(code)}{code}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        raw = urllib.request.urlopen(req, timeout=TIMEOUT).read().decode("gbk", "ignore")
        payload = raw.split('="', 1)[1].rstrip('";\n')
        f = payload.split("~")
        if len(f) < 50 or f[0] == "":
            return f"error: quote unavailable for {code} (delisted or not found)"
        out = {
            "code": f[2],
            "name": f[1],
            "price": f[3],
            "prev_close": f[4],
            "open": f[5],
            "change": f[31],
            "change_pct": f[32],
            "high": f[33],
            "low": f[34],
            "volume_wan": f[36],
            "amount_wan": f[37],
            "turnover_pct": f[38],
            "pe": f[39],
            "pb": f[46],
            "float_mktcap_yi": f[44],
            "total_mktcap_yi": f[45],
            "time": f[30],
        }
        return json.dumps(out, ensure_ascii=False)
    except Exception as exc:  # noqa: BLE001 - endpoint may be down
        return f"error: quote fetch failed: {exc}"


def get_financials(code: str) -> str:
    """Load financial data. Prefers local file data/{code}_financials.json
    (user-provided, read-only); otherwise reports it as unavailable.
    Never fabricates numbers."""
    if not _CODE_RE.match(code):
        return f"error: invalid A-share code {code!r}"
    path = f"data/{code}_financials.json"
    try:
        with open(path, encoding="utf-8") as f:
            return json.dumps(json.load(f), ensure_ascii=False)
    except FileNotFoundError:
        return (
            f"financial data unavailable for {code}: no data/{code}_financials.json. "
            "Tell the user: 财务摘要数据需补充（data 目录下没有该股票的财务数据文件），"
            "报告中的财务摘要与盈利预测相关表格标注 [需补充]。"
        )
    except Exception as exc:  # noqa: BLE001
        return f"error: financial data unreadable: {exc}"


# --------------------------------------------------- report library (RAG) ----
_LIB_DIR = Path(__file__).parent / "report_lib"


def _load_library():
    """Load chunks + BM25 index from report_lib/. Returns (chunks, bm25)."""
    chunks = json.loads((_LIB_DIR / "chunks.json").read_text(encoding="utf-8"))
    bm25 = json.loads((_LIB_DIR / "bm25_index.json").read_text(encoding="utf-8"))
    # JSON object keys are strings; doc_len keys are chunk ids -> convert back
    bm25["doc_len"] = {int(k): v for k, v in bm25["doc_len"].items()}
    return chunks, bm25


def _tokenize(text: str) -> list:
    """Chinese unigram+bigram, English words as-is."""
    text = text.lower()
    tokens = []
    for han in re.findall(r"[\u4e00-\u9fff]+", text):
        tokens.extend(han)
        tokens.extend(han[i:i + 2] for i in range(len(han) - 1))
    tokens.extend(re.findall(r"[a-z]+", text))
    return tokens


def _bm25_score(q_terms: list, chunk: dict, bm25: dict, k1=1.5, b=0.75) -> float:
    df, doc_len, avg_len, n = bm25["df"], bm25["doc_len"], bm25["avg_len"], bm25["n"]
    terms = _tokenize(chunk["text"])
    dl = doc_len[chunk["id"]]
    score = 0.0
    for t in set(q_terms):
        f = terms.count(t)
        if not f:
            continue
        idf = math.log((n - df.get(t, 0) + 0.5) / (df.get(t, 0) + 0.5) + 1)
        score += idf * (f * (k1 + 1)) / (f + k1 * (1 - b + b * dl / avg_len))
    return score


def retrieve_report(query: str, k: int = 3) -> str:
    """Search the local broker-report library (report_lib/).

    Returns the top-k chunks as text with [doc:N] citation handles. Uses BM25
    (zero-dependency). Facts in the returned chunks are sourced from real
    broker reports - cite them as [doc:N] in the final report.
    """
    if not query.strip():
        return "error: empty query"
    try:
        chunks, bm25 = _load_library()
    except Exception as exc:  # noqa: BLE001
        return f"error: report library unavailable: {exc} (run build_report_library.py first)"
    q_terms = _tokenize(query)
    if not q_terms:
        return "error: query has no searchable terms"
    scored = sorted(chunks, key=lambda c: _bm25_score(q_terms, c, bm25), reverse=True)
    top = scored[:k]
    lines = [f"library hit {i + 1}:" for i in range(len(top))]
    parts = []
    for c in top:
        snippet = c["text"][:600]
        parts.append(
            f"[doc:{c['id']}] (src={c['src']}, page={c['page']}, title={c['title']}): {snippet}"
        )
    return "\n".join(parts)


# ------------------------------------------- online search (EastMoney, no key) ----
_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}


def _http_get(url: str) -> str:
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return resp.read().decode("utf-8", "ignore")


def _em_market_suffix(code: str) -> str:
    if code.startswith("6"):
        return "SH"
    if code.startswith(("0", "3")):
        return "SZ"
    return "BJ"


def search_reports(code: str, k: int = 3) -> str:
    """Search EastMoney's public report feed for REAL analyst reports on a stock.

    Returns recent report titles with broker, rating and publish date. Use this
    when the local report_lib/ has no chunks for the target stock.
    """
    if not _CODE_RE.match(code):
        return f"error: invalid A-share code {code!r}"
    import datetime
    year = datetime.date.today().year
    url = (
        "https://reportapi.eastmoney.com/report/list?"
        f"industryCode=*&pageSize={k}&industry=*&rating=*&ratingChange=*"
        f"&beginTime={year}-01-01&endTime={year}-12-31&pageNo=1&fields=&qType=0"
        f"&orgCode=&code={code}&rcode="
    )
    try:
        j = json.loads(_http_get(url))
        hits = j.get("data") or []
        if not hits:
            return f"no online research reports found for {code} in {year}"
        return "\n".join(
            f"- [{h.get('orgSName')}] {h.get('title')} | 评级={h.get('emRatingName')}"
            f" | {h.get('publishDate', '')[:10]}"
            for h in hits[:k]
        )
    except Exception as exc:  # noqa: BLE001
        return f"error: report search failed: {exc}"


def search_financials(code: str, start_year: int = None, end_year: int = None) -> str:
    """Fetch DISCLOSED financials (actuals) for a stock from EastMoney F10.

    Args:
        code: A-share stock code, e.g. "000001".
        start_year: earliest year to include, e.g. 2020. Default = current
            year minus 2 (covers the last 2 full years + current year).
        end_year: latest year to include, e.g. 2025. Default = current year.

    Returns per-period revenue and net profit attributable, filtered to the
    requested year range. These are realized numbers, NOT forecasts - do not
    present them as predictions.
    """
    if not _CODE_RE.match(code):
        return f"error: invalid A-share code {code!r}"
    import datetime
    this_year = datetime.date.today().year
    lo = start_year if start_year is not None else this_year - 2
    hi = end_year if end_year is not None else this_year
    if lo > hi:
        return f"error: start_year {lo} > end_year {hi}"
    secucode = f"{code}.{_em_market_suffix(code)}"
    url = (
        "https://datacenter-web.eastmoney.com/api/data/v1/get?"
        "reportName=RPT_F10_FINANCE_MAINFINADATA&columns=ALL&filter="
        + urllib.parse.quote(f'(SECUCODE="{secucode}")')
        + "&pageNumber=1&pageSize=40&sortTypes=-1&sortColumns=REPORT_DATE"
    )
    try:
        j = json.loads(_http_get(url))
        rows = (j.get("result") or {}).get("data") or []
        out = []
        for r in rows:
            rd = (r.get("REPORT_DATE") or "")[:10]
            if not rd:
                continue
            yr = int(rd[:4])
            if not (lo <= yr <= hi):
                continue
            out.append({
                "report_date": rd,
                "revenue": r.get("TOTALOPERATEREVE"),
                "net_profit_attributable": r.get("PARENTNETPROFIT"),
            })
        if not out:
            return f"no online financials found for {code} in {lo}-{hi}"
        return json.dumps(out, ensure_ascii=False)
    except Exception as exc:  # noqa: BLE001
        return f"error: financial search failed: {exc}"


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_quote",
            "description": "获取A股实时行情（价格/涨跌幅/市值/PE/PB等）",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "A股代码，如 600256"}
                },
                "required": ["code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_financials",
            "description": "获取财务摘要（读本地 data/{code}_financials.json，缺失则标注需补充）",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "A股代码，如 600256"}
                },
                "required": ["code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "retrieve_report",
            "description": "检索本地研报库（真实券商研报），返回 top-k 片段（带 [doc:N] 引用）。"
                           "写报告前调用它获取研报原文支撑（产能/长协/项目/预测等章节内容），"
                           "并在报告中以 [doc:N] 标注引用",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string",
                              "description": "检索关键词，如 '马朗煤矿 产能' 或 '天然气 长协'"},
                    "k": {"type": "integer", "description": "返回片段数，默认 3"}
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_reports",
            "description": "在线搜索该股票的券商研报（东方财富公开接口，无需key）。"
                           "当本地研报库没有该股票内容时使用，返回研报标题/机构/评级/日期",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "A股代码，如 000001"},
                    "k": {"type": "integer", "description": "返回条数，默认 3"}
                },
                "required": ["code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_financials",
            "description": "在线获取已披露财务数据（东方财富F10，实际值非预测）。"
                           "当本地 data/ 没有该股票财务文件时使用",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "A股代码，如 000001"}
                },
                "required": ["code"],
            },
        },
    },
]

_TOOL_IMPL = {
    "get_quote": get_quote,
    "get_financials": get_financials,
    "retrieve_report": retrieve_report,
    "search_reports": search_reports,
    "search_financials": search_financials,
}


# ---------------------------------------------------------------- LLM I/O ----
def call_llm(messages: list) -> dict:
    """Send messages + tools to the chat-completions endpoint, return reply dict."""
    body = json.dumps({
        "model": MODEL,
        "messages": messages,
        "tools": TOOLS,
        "tool_choice": "auto",
        "max_tokens": MAX_TOKENS,
    }).encode("utf-8")
    req = urllib.request.Request(
        BASE_URL + "/chat/completions", data=body,
        headers={"Authorization": f"Bearer {API_KEY}",
                 "Content-Type": "application/json"})
    # Retry transient network errors with backoff. HTTP 403 usually means
    # gateway rate-limit on the API key - back off LONGER so retries do not
    # pile up and make the throttle worse.
    import time
    last_exc = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 403:
                print(f"[call_llm] HTTP 403 (gateway rate-limit?) - "
                      f"waiting 60s before retry {attempt + 1}/3...", flush=True)
                time.sleep(60)
            else:
                time.sleep(2 ** attempt)
            last_exc = exc
        except (urllib.error.URLError, TimeoutError) as exc:
            last_exc = exc
            time.sleep(2 ** attempt)
    raise last_exc


def run_tools(reply_message: dict) -> list:
    """Execute every tool call the model requested; return tool-result messages."""
    results = []
    for tc in reply_message.get("tool_calls") or []:
        fn = tc.get("function", {})
        name, args = fn.get("name", ""), json.loads(fn.get("arguments") or "{}")
        impl = _TOOL_IMPL.get(name)
        if impl is None:
            out = f"error: unknown tool {name}"
        else:
            out = impl(**args)
        results.append({
            "role": "tool",
            "tool_call_id": tc.get("id", ""),
            "content": str(out),
        })
    return results


def generate_report(code: str) -> str:
    """Run the agent loop until the model answers without tool calls
    and without being cut off by max_tokens (finish_reason == length)."""
    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user",
         "content": f"请为 A 股公司 {code} 生成一份完整的公司深度研究报告，"
                    f"结构严格遵循 output_spec.md（模板来源是广汇能源研报，但内容是给"
                    f"{code} 写的，不要套用广汇的行业和业务）。先调用工具：get_quote 获取"
                    f"行情；财务数据优先 get_financials（本地），本地没有就用 "
                    f"search_financials 在线获取已披露实际值；研报内容优先 "
                    f"retrieve_report（本地研报库，仅当库内是同一公司时才引用 [doc:N]），"
                    f"本地没有该股研报就用 search_reports 在线搜索券商研报观点；"
                    f"在线财务是实际值而非预测，预测部分须标明假设。再输出 Markdown 报告。"
                    f"报告较长，请一次写完所有章节：报告头/核心观点/财务摘要/公司概况/"
                    f"分业务分析/盈利预测与投资建议/风险因素/附录。"},
    ]
    for _ in range(MAX_ROUNDS):
        reply = call_llm(messages)
        choice = reply["choices"][0]
        msg = choice["message"]
        finish = choice.get("finish_reason", "")
        if msg.get("tool_calls"):
            messages.append({"role": "assistant",
                             "content": msg.get("content") or "",
                             "tool_calls": msg["tool_calls"]})
            messages.extend(run_tools(msg))
            continue
        content = msg.get("content") or ""
        if finish == "length":
            # Output hit max_tokens mid-sentence: ask the model to continue
            # from exactly where it stopped, without repeating earlier text.
            messages.append({"role": "assistant", "content": content})
            messages.append({"role": "user",
                             "content": "继续。从你刚才停下的地方接着写剩余章节，"
                                        "不要重复已经写过的内容。"})
            continue
        return content
    return "[agent] max rounds reached without a final answer"


def main(argv: list) -> int:
    if not argv:
        print(__doc__)
        return 1
    code = argv[0]
    report = generate_report(code)
    # Deterministic safety suffix: never rely on the model remembering it.
    report = report.rstrip() + (
        "\n\n---\n*本报告为学习用途的模拟研报，不构成投资建议。*"
    )
    out_path = f"report_{code}.md"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(report)
    print(report)
    print(f"\n[report saved to {out_path}]")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
