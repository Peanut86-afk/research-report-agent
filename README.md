# Research Report Agent

LLM-driven agent that generates broker-style Chinese deep reports. The report
structure was **distilled from a real broker report** (Kysec / 开源证券 company
deep report on 广汇能源 600256.SH, 2026-10-03, PDF in
`C:\Users\Chang\Downloads\H3_AP202610051830145021_1.pdf`).

## Files

| File | Role |
|---|---|
| `output_spec.md` | **The output contract.** Section structure, header block, per-business analysis pattern, forecast table, risk factors, appendix, honesty rules — extracted from the real report. |
| `research_tools.py` | **Shared Tools layer**: the 5 tools (`get_quote`, `get_financials`, `retrieve_report`, `search_reports`, `search_financials`) + config. Pure urllib, no veadk dependency. Reused by all agent versions. |
| `research_report_agent.py` | Plain-loop (hand-written ReAct) report generator, 5 tools, self-contained copy of the tools. |
| `research_qa_agent.py` | **Plain-loop follow-up Q&A** over a generated report: user asks, agent calls tools, answers WITH cited references, honestly says 没有找到相关依据 when nothing matches. Reuses the 5 tools. |
| `research_agent_agentkit.py` | **AgentKit (VeADK) report version**: same tools via `FunctionTool`, loop owned by the AgentKit `Runner`. |
| `research_qa_agent_agentkit.py` | **AgentKit follow-up Q&A version**: same ReAct loop, memory = AgentKit session (same `session_id` across turns). |
| `config.json` | Ark API key / model / base URL (key never in source code). Used by the LLM calls in the plain-loop versions. |
| `data/` | Per-stock financials as `{code}_financials.json` (e.g. `data/600256_financials.json`, extracted from the real broker PDF pages 21-23). |
| `report_lib/` | **RAG library**: `chunks.json` (per-page chunks with `[doc:N]` ids) + `bm25_index.json`. Built by `build_report_library.py` from broker-report PDFs. |
| `build_report_library.py` | PDF -> per-page text -> clean -> chunk -> BM25 index. Add more PDFs by passing them as args. |
| `validate_financials.py` | Checks the financials JSON structure and key numbers. |

## Run

```powershell
# in agents\research-report-agent
python research_report_agent.py 600256    # plain-loop report
python research_report_agent.py 000001    # works for any A-share code
```

The agent: (1) reads config, (2) asks the LLM what it needs, (3) the LLM calls
the tools, (4) data is fed back, (5) the LLM writes the full Markdown report
per `output_spec.md`, citing retrieved chunks as `[doc:N]`, and saves it to
`report_{code}.md`.

**Follow-up Q&A (plain-loop)** — ask questions about a generated report, answers
must cite sources, honest "no evidence" when nothing found:

```powershell
python research_qa_agent.py 600256
# > 马朗煤矿核增到什么产能？
# > 平安银行现在什么估值水平？     (cross-stock: uses online data, not 600256 chunks)
# > exit
```

## RAG pipeline (route v1: generate-first, A-share only)

```text
broker-report PDF(s)  --build_report_library.py-->  report_lib/chunks.json
                                                         + bm25_index.json
retrieve_report(query)  --BM25 top-k-->  [doc:N] chunks (src/page/title)
LLM generation          --cite [doc:N]-->  report_600256.md
```

- Overseas (US) research documents are deferred (SEC EDGAR needs a VPN from
  mainland China); live quote tool already supports `us`/`hk` prefixes when we
  turn that on.

## AgentKit version (final runtime)

veadk is installed only in `agents\agentkit-demo\.venv`. **Always run AgentKit
versions with that venv python:**

```powershell
# 0. local run (venv python!) - only needed if you want to test locally first
#    VeADK reads the LONG env names (not VOLC_ACCESSKEY):
$env:VOLCENGINE_ACCESS_KEY = "<your AK>"
$env:VOLCENGINE_SECRET_KEY = "<your SK>"
$env:CLOUD_PROVIDER = "volcengine"
& "C:\Users\Chang\OneDrive\Desktop\agent 开发\agents\agentkit-demo\.venv\Scripts\python.exe" research_agent_agentkit.py 600256
#    -> report_agentkit_600256.md
& "C:\Users\Chang\OneDrive\Desktop\agent 开发\agents\agentkit-demo\.venv\Scripts\python.exe" research_qa_agent_agentkit.py 600256
#    (both agents pin model_name="doubao-seed-evolving"; no Ark key needed locally -
#     VeADK exchanges AK/SK for the account's first ARK API key automatically)
```

### Deploy to cloud (recommended - no local AK/SK needed on the runtime, IAM handles it)

The CLI lives in the venv (no PATH entry), so invoke it with `python -m`:

```powershell
cd "C:\Users\Chang\OneDrive\Desktop\agent 开发\agents\research-report-agent"
$env:AK = "C:\Users\Chang\OneDrive\Desktop\agent 开发\agents\agentkit-demo\.venv\Scripts\python.exe"

# 0. preflight: needs AK/SK env vars + a default VPC+subnet in cn-beijing,
#    and the account must pass ENTERPRISE real-name verification for AgentKit.
$env:VOLC_ACCESSKEY = "<your AK>"; $env:VOLC_SECRETKEY = "<your SK>"; $env:CLOUD_PROVIDER = "volcengine"
& $env:AK -m agentkit.toolkit.cli.cli auth admin doctor

# 1. one-time: provision UserPool + OIDC + STS and publish the login address
& $env:AK -m agentkit.toolkit.cli.cli auth admin sso-setup

# 2. browser SSO login with the address printed by sso-setup
& $env:AK -m agentkit.toolkit.cli.cli login <address-from-step-1>

# 3. build + deploy (creates cloud resources; agentkit.yaml -> research_agent_agentkit.py)
& $env:AK -m agentkit.toolkit.cli.cli launch

# 4. check status
& $env:AK -m agentkit.toolkit.cli.cli status
```

To deploy the Q&A loop instead, point `agentkit.yaml` at
`research_qa_agent_agentkit.py` and `launch` again. On the cloud the agent's
session_id (from the request headers) keeps the follow-up conversation alive.

Tools can always be verified without credentials:

```powershell
python -c "import research_tools as rt; print(rt.retrieve_report('马朗煤矿 产能'))"
python -c "import research_tools as rt; print(rt.get_quote('600256'))"
python -c "import research_tools as rt; print(rt.search_financials('000001'))"
```

> Note: the Ark API key in `config.json` is used by the plain-loop versions'
> LLM calls. The AgentKit versions authenticate through Volcengine AK/SK env
> vars locally, or VeFaaS IAM on the cloud. Do NOT run multiple processes on
> the same Ark key at once (gateway rate-limits it with HTTP 403; the code
> backs off 60s on 403).

## Data sources & honesty rules

- `get_quote` → Tencent free quote endpoint (`qt.gtimg.cn`), live, no key.
- `get_financials` → **read-only local file** `data/{code}_financials.json`.
- `retrieve_report` → **BM25 RAG** over `report_lib/` (real broker-report chunks,
  cited as `[doc:N]`). Chunks belong to a SPECIFIC company — when the target
  stock differs, they must NOT be cited (use `search_reports` instead).
- `search_reports` → EastMoney public feed: REAL analyst-report titles/ratings
  (no key, no正文).
- `search_financials` → EastMoney F10: DISCLOSED ACTUALS (revenue / net profit
  per period), not forecasts.
- **Data layering (non-negotiable):** local data/ + report_lib/ first → if
  missing, online search (real data, labeled as disclosed actuals) → only when
  neither exists, mark `[需补充]` with the reason. The agent **never fabricates**
  numbers; every claim must trace to a tool result or an explicitly marked slot.

## Security design (same rules as our other agents)

1. **No API key in code** — read from `config.json` only.
2. **Input whitelist** — stock codes validated by regex before any URL is built;
   no arbitrary URLs, no shell execution.
3. **Read-only data path** — `get_financials` only reads a local file; the LLM
   cannot write files or run commands.
4. **Auditable output** — the report must mark sourced vs `[需补充]` data and
   always include a risk section + disclaimer (learning use only, not advice).

## Mapping to the real report

| Real Kysec report | This agent |
|---|---|
| 首页数据块 + 核心观点 + 财务摘要表 | `output_spec.md` §1 |
| 目录 7 章 + 附录 | §2 |
| 每业务章节（数据段/表格/横向对比/前瞻） | §4 |
| 盈利预测与投资建议 | §5 |
| 风险因素 | §6 |
| 附：财务预测摘要（三表 + 比率） | §7 |
