"""RAG pipeline — chain-of-thought reasoning + multi-strategy retrieval."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from app.config import settings
from app.pubmed_live import live_pubmed_search
from app.reasoning import InputReasoner, OutputReasoner, ReasonedQuery
from app.reranker import rerank

log = logging.getLogger(__name__)

_store: PineconeVectorStore | None = None
_embeddings: OpenAIEmbeddings | None = None


def get_vectorstore():
    global _store, _embeddings
    if _store is None:
        from langchain_openai import OpenAIEmbeddings
        from langchain_pinecone import PineconeVectorStore
        
        if not settings.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY is not set")
        if not settings.pinecone_api_key:
            raise RuntimeError("PINECONE_API_KEY is not set")
        _embeddings = OpenAIEmbeddings(
            model=settings.embedding_model,
            api_key=settings.openai_api_key or None,
        )
        _store = PineconeVectorStore.from_existing_index(
            index_name=settings.pinecone_index,
            embedding=_embeddings,
        )
    return _store


@dataclass
class SourceRef:
    pmid: str
    title: str
    journal: str
    year: str
    url: str
    excerpt: str


def _doc_to_source(doc, max_excerpt: int = 400) -> SourceRef:
    m = doc.metadata or {}
    text = doc.page_content or ""
    if len(text) > max_excerpt:
        text = text[:max_excerpt].rsplit(" ", 1)[0] + "…"
    return SourceRef(
        pmid=str(m.get("pmid", "")),
        title=str(m.get("title", ""))[:500],
        journal=str(m.get("journal", "")),
        year=str(m.get("year", "")),
        url=str(m.get("source", "")),
        excerpt=text,
    )


# ---------------------------------------------------------------------------
# Multi-strategy retrieval
# ---------------------------------------------------------------------------


def _retrieve_multi_strategy(
    reasoned_query: ReasonedQuery,
    k: int = 6,
) -> list[Any]:
    """Retrieve from Pinecone (vector search) + live PubMed, then deduplicate."""
    store = get_vectorstore()
    all_docs: list[Any] = []
    seen_pmids: set[str] = set()

    # 1) Vector search — use each embedding query
    queries = reasoned_query.embedding_queries or [reasoned_query.original_question]
    per_query_k = max(k, 4)
    for eq in queries[:3]:  # cap at 3 embedding queries
        try:
            results = store.similarity_search(eq, k=per_query_k)
            for doc in results:
                pmid = str((doc.metadata or {}).get("pmid", ""))
                # keep first occurrence, skip duplicates
                key = pmid or doc.page_content[:80]
                if key not in seen_pmids:
                    seen_pmids.add(key)
                    all_docs.append(doc)
        except Exception:
            log.exception("Vector search failed for query: %s", eq[:80])

    # 2) Live PubMed search
    if settings.enable_live_search and reasoned_query.pubmed_query:
        try:
            live_docs = live_pubmed_search(
                reasoned_query.pubmed_query,
                max_results=min(k * 2, 20),
            )
            for doc in live_docs:
                pmid = str((doc.metadata or {}).get("pmid", ""))
                key = pmid or doc.page_content[:80]
                if key not in seen_pmids:
                    seen_pmids.add(key)
                    all_docs.append(doc)
        except Exception:
            log.exception("Live PubMed search failed")

    log.info("Multi-strategy retrieval: %d unique documents", len(all_docs))
    return all_docs


# ---------------------------------------------------------------------------
# Main entrypoint
# ---------------------------------------------------------------------------


def answer_question(question: str, k: int = 6) -> dict[str, Any]:
    """Full CoT pipeline: reason → retrieve → rerank → synthesize."""

    # ── Step 1: Input Chain of Thought ──────────────────────────────────
    input_reasoner = InputReasoner()
    reasoned_query = input_reasoner.reason(question)
    log.info(
        "Input CoT — intent=%s, pubmed_query=%s",
        reasoned_query.intent_type,
        reasoned_query.pubmed_query[:120] if reasoned_query.pubmed_query else "(none)",
    )

    # ── Step 2: Multi-strategy retrieval ────────────────────────────────
    docs = _retrieve_multi_strategy(reasoned_query, k=k)

    # ── Step 3: Rerank ──────────────────────────────────────────────────
    docs = rerank(docs, question, top_n=k)

    # ── Step 4: Build context blocks ────────────────────────────────────
    context_blocks: list[str] = []
    for d in docs:
        m = d.metadata or {}
        pmid = m.get("pmid", "?")
        title = (m.get("title") or "")[:200]
        context_blocks.append(
            f"[PMID {pmid} — {title}]\n{d.page_content}"
        )

    # ── Step 5: Output Chain of Thought ─────────────────────────────────
    output_reasoner = OutputReasoner()
    reasoned_answer = output_reasoner.synthesize(
        question, reasoned_query, context_blocks
    )

    # ── Step 6: Build sources list ──────────────────────────────────────
    seen: set[str] = set()
    sources: list[SourceRef] = []
    for d in docs:
        m = d.metadata or {}
        key = str(m.get("pmid", "")) + str(m.get("title", ""))[:40]
        if key in seen:
            continue
        seen.add(key)
        sources.append(_doc_to_source(d))

    # ── Assemble response ───────────────────────────────────────────────
    result: dict[str, Any] = {
        "answer": reasoned_answer.answer,
        "sources": [s.__dict__ for s in sources],
    }

    if settings.enable_reasoning_trace:
        result["reasoning_trace"] = reasoned_answer.trace_dict()
        result["query_analysis"] = reasoned_query.to_dict()

    return result
