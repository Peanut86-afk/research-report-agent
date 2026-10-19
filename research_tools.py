# -*- coding: utf-8 -*-
"""research_tools.py - Shared tool layer for the research-report agent family.

Pure-urllib tools (no veadk dependency) reused by BOTH the plain-loop version
(research_report_agent.py) and the AgentKit versions (report generation +
follow-up Q&A). Keeping tools in one module is the 'Tools' layer of the
agent-harness structure.

Tools:
    get_quote        live A-share quote (Tencent, free, no key)
    get_financials   local data/{code}_financials.json (read-only)
    retrieve_report  BM25 search over report_lib/ chunks, returns [doc:N]
    search_reports   REAL online broker-report titles (EastMoney, no key)
    search_financials REAL disclosed financials (EastMoney F10, no key)
"""
import json
import math
import re
import urllib.parse
import urllib.request
from pathlib import Path

# ---------------------------------------------------------------- config ----
_CFG_PATH = Path(__file__).parent / "config.json"
with open(_CFG_PATH, encoding="utf-8") as f:
    _cfg = json.load(f)

API_KEY = _cfg["api_key"]          # required - fail fast if missing
MODEL = _cfg.get("model", "doubao-seed-evolving")
BASE_URL = _cfg.get("base_url", "https://ark.cn-beijing.volces.com/api/v3")
TIMEOUT = 300

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
    """Fetch live A-share quote from Tencent's free endpoint; returns flat JSON.

    Args:
        code: A-share stock code, e.g. "600256".
    """
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
    """Load financial summary from local data/{code}_financials.json.

    Args:
        code: A-share stock code, e.g. "600256".
    """
    if not _CODE_RE.match(code):
        return f"error: invalid A-share code {code!r}"
    path = Path(__file__).parent / "data" / f"{code}_financials.json"
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

    Args:
        query: search terms, e.g. "马朗煤矿 产能" or "天然气 长协".
        k: number of chunks to return (default 3).

    Returns top-k chunks with [doc:N] citation handles. Facts in the chunks are
    from real broker reports - cite them as [doc:N] in the final report.
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
    parts = []
    for c in top:
        parts.append(
            f"[doc:{c['id']}] (src={c['src']}, page={c['page']}, title={c['title']}): "
            f"{c['text'][:600]}"
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

    Args:
        code: A-share stock code, e.g. "000001".
        k: number of reports to return (default 3).

    Returns recent report titles with broker, rating and publish date. Use
    this when the local report_lib/ has no chunks for the target stock.
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
    get_quote,
    get_financials,
    retrieve_report,
    search_reports,
    search_financials,
]
