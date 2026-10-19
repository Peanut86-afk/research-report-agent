# -*- coding: utf-8 -*-
"""build_report_library.py - Turn broker-report PDFs into a searchable chunk library.

Pipeline (reuses the aurora-town RAG design):
    PDF -> per-page text -> clean markdown noise -> heading+paragraph chunks
    -> BM25 index (optional) / vector cache (via Ark embedding) -> library.json

Outputs (in report_lib/):
    chunks.json        : [{id, title, text, src, page}]
    bm25_index.json    : {df, doc_len, avg_len, n}
    vector_cache.json  : {chunk_id: embedding} (built lazily when retrieval=vector)

Usage:
    python build_report_library.py <pdf1> [<pdf2> ...]
"""
import json
import re
import sys
from pathlib import Path

# Try PyMuPDF if available; otherwise require pre-extracted text files.
try:
    import pymupdf
    HAVE_PYMUPDF = True
except Exception:  # noqa: BLE001
    HAVE_PYMUPDF = False

OUT_DIR = Path(__file__).parent / "report_lib"
OUT_DIR.mkdir(exist_ok=True)


def pdf_to_text(pdf_path: Path) -> list:
    """Extract per-page text; returns [(page_no, text), ...]."""
    if not HAVE_PYMUPDF:
        raise RuntimeError("pymupdf not installed - cannot extract PDF text")
    with pymupdf.open(str(pdf_path)) as doc:
        return [(i + 1, page.get_text("text", sort=True))
                for i, page in enumerate(doc)]


def clean(text: str) -> str:
    """Strip markdown/ocr noise so chunks and tokens stay clean."""
    text = re.sub(r"```.*?```", "", text, flags=re.S)
    text = re.sub(r"[*_`]", "", text)
    text = re.sub(r"!\[.*?\]\(.*?\)", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def chunk_pages(pages: list, src: str) -> list:
    """Chunk per page (broker reports have clear per-page semantic boundaries:
    tables, figures, section breaks). Long pages are split at paragraph gaps.
    Each chunk = {id, title, text, src, page}; id doubles as [doc:N]."""
    chunks, cur_id = [], 1
    MAX_CHARS = 800  # split a page further when it exceeds this

    def add_chunk(title, text, page_no):
        nonlocal cur_id
        chunks.append({
            "id": cur_id,
            "title": title,
            "text": text,
            "src": src,
            "page": page_no,
        })
        cur_id += 1

    for page_no, page_text in pages:
        cleaned = clean(page_text)
        if not cleaned:
            continue
        first_line = cleaned.splitlines()[0][:40] if cleaned.splitlines() else ""
        if len(cleaned) <= MAX_CHARS:
            add_chunk(f"p{page_no}: {first_line}", cleaned, page_no)
            continue
        # Long page: split at blank-line paragraph gaps, keep chunks >= ~150 chars.
        paras = [p.strip() for p in re.split(r"\n\s*\n", cleaned) if p.strip()]
        buf, buf_len = [], 0
        for para in paras:
            if buf and buf_len + len(para) > MAX_CHARS:
                add_chunk(f"p{page_no}: {first_line}", " ".join(buf), page_no)
                buf, buf_len = [para], len(para)
            else:
                buf.append(para)
                buf_len += len(para)
        if buf:
            add_chunk(f"p{page_no}: {first_line}", " ".join(buf), page_no)
    return chunks


def tokenize(text: str) -> list:
    """Chinese unigram+bigram, English words as-is (BM25 tokenizer)."""
    text = text.lower()
    tokens = []
    for han in re.findall(r"[\u4e00-\u9fff]+", text):
        tokens.extend(han)
        tokens.extend(han[i:i + 2] for i in range(len(han) - 1))
    tokens.extend(re.findall(r"[a-z]+", text))
    return tokens


def build_bm25(chunks: list) -> dict:
    df, doc_len = {}, {}
    for c in chunks:
        terms = tokenize(c["text"])
        doc_len[c["id"]] = len(terms)
        for t in set(terms):
            df[t] = df.get(t, 0) + 1
    return {"df": df, "doc_len": doc_len,
            "avg_len": sum(doc_len.values()) / max(len(doc_len), 1),
            "n": len(chunks)}


def main(argv: list) -> int:
    if not argv:
        print(__doc__)
        return 1
    all_chunks = []
    for arg in argv:
        p = Path(arg)
        if not p.exists():
            print(f"skip (not found): {p}")
            continue
        print(f"extracting: {p.name} ...")
        pages = pdf_to_text(p)
        chunks = chunk_pages(pages, p.name)
        print(f"  {len(pages)} pages -> {len(chunks)} chunks")
        all_chunks.extend(chunks)

    if not all_chunks:
        print("no chunks produced")
        return 1

    (OUT_DIR / "chunks.json").write_text(
        json.dumps(all_chunks, ensure_ascii=False, indent=1), encoding="utf-8")
    (OUT_DIR / "bm25_index.json").write_text(
        json.dumps(build_bm25(all_chunks), ensure_ascii=False), encoding="utf-8")
    print(f"library ready: {OUT_DIR}")
    print(f"  chunks.json      {len(all_chunks)} chunks")
    print(f"  bm25_index.json  ready (vector_cache built lazily on first vector query)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
