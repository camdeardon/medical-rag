"""Live PubMed search integration for RAG."""

import logging
from typing import Any

log = logging.getLogger(__name__)


def live_pubmed_search(query: str, max_results: int = 20) -> list[Any]:
    """Search PubMed and fetch LangChain documents on the fly."""
    # Lazy import to avoid loading heavy langchain/requests at module level
    from pubmed_ingest import search_pubmed, fetch_abstracts
    
    if not query:
        return []
        
    try:
        log.info("Performing live PubMed search: %s", query)
        pmids = search_pubmed(query, max_results=max_results)
        
        if not pmids:
            return []
            
        # Fetch the articles as LangChain documents
        # We set include_pmc_fulltext=False to keep the live response fast
        docs = fetch_abstracts(pmids, include_pmc_fulltext=False)
        log.info("Live PubMed search fetched %d docs", len(docs))
        return docs
        
    except Exception:
        log.exception("Live PubMed search failed")
        return []
