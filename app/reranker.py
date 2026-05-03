"""Cohere-based cross-encoder reranker for retrieved documents."""

from __future__ import annotations

import logging
from typing import Any

from app.config import settings

log = logging.getLogger(__name__)


def rerank(
    docs: list[Any],
    query: str,
    top_n: int = 6,
) -> list[Any]:
    """Re-score *docs* against *query* using Cohere Rerank v3.5.

    Parameters
    ----------
    docs : list
        LangChain Document objects (must have `.page_content`).
    query : str
        The original user question.
    top_n : int
        How many top-ranked documents to return.

    Returns
    -------
    list
        The *top_n* documents reordered by relevance.
    """
    if not settings.enable_reranking or not settings.cohere_api_key:
        log.info("Reranking disabled or no Cohere key — skipping")
        return docs[:top_n]

    if not docs:
        return docs

    try:
        import cohere  # lazy import

        co = cohere.ClientV2(api_key=settings.cohere_api_key)
        results = co.rerank(
            model="rerank-v3.5",
            query=query,
            documents=[d.page_content for d in docs],
            top_n=min(top_n, len(docs)),
        )
        reranked = [docs[r.index] for r in results.results]
        log.info(
            "Reranked %d → %d docs (top score=%.3f)",
            len(docs),
            len(reranked),
            results.results[0].relevance_score if results.results else 0,
        )
        return reranked

    except Exception:
        log.exception("Cohere rerank failed — returning original order")
        return docs[:top_n]
