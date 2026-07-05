"""Background scheduler — runs subscribed queries daily."""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from threading import Thread
from xml.etree import ElementTree as ET

import requests

from app.config import settings, PROJECT_ROOT
from app.database import (
    get_subscriptions,
    get_all_active_subscriptions,
    get_seen_pmids,
    mark_pmids_seen,
    update_subscription_stats,
)

log = logging.getLogger(__name__)

BASE_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
EMAIL = "camadamdeardon+pubmed@gmail.com"

# Track running state
_scheduler_thread: Thread | None = None
_stop_flag = False


# ---------------------------------------------------------------------------
# PubMed helpers (reused from pubmed_ingest.py — lightweight versions)
# ---------------------------------------------------------------------------


def _search_pubmed(
    query: str, 
    max_results: int = 100,
    article_type: str | None = None,
    journals: str | None = None,
    sort_by: str = "relevance"
) -> list[str]:
    """Search PubMed and return list of PMIDs."""
    
    # Build advanced query
    entrez_query = f"({query})"
    if article_type and article_type.lower() != "all":
        entrez_query += f' AND ("{article_type}"[Publication Type])'
    if journals:
        # Split by comma, strip whitespace, wrap in [Journal]
        j_list = [j.strip() for j in journals.split(",") if j.strip()]
        if j_list:
            j_term = " OR ".join([f'"{j}"[Journal]' for j in j_list])
            entrez_query += f" AND ({j_term})"
            
    # PubMed sort parameter mapping
    sort_val = "relevance"
    if sort_by == "date":
        sort_val = "pub+date"

    params = {
        "db": "pubmed",
        "term": entrez_query,
        "retmax": max_results,
        "retmode": "json",
        "email": EMAIL,
        "sort": sort_val,
    }
    ncbi_key = getattr(settings, "ncbi_api_key", None)
    if ncbi_key and ncbi_key != "...":
        params["api_key"] = ncbi_key

    resp = requests.get(f"{BASE_URL}/esearch.fcgi", params=params, timeout=15)
    resp.raise_for_status()
    ids = resp.json().get("esearchresult", {}).get("idlist", [])
    return ids


def _fetch_article_titles(pmids: list[str]) -> dict[str, str]:
    """Fetch PMID -> title mapping for a batch of PMIDs."""
    if not pmids:
        return {}

    titles: dict[str, str] = {}
    for i in range(0, len(pmids), 50):
        batch = pmids[i : i + 50]
        params = {
            "db": "pubmed",
            "id": ",".join(batch),
            "rettype": "abstract",
            "retmode": "xml",
            "email": EMAIL,
        }
        ncbi_key = getattr(settings, "ncbi_api_key", None)
        if ncbi_key and ncbi_key != "...":
            params["api_key"] = ncbi_key

        resp = requests.get(f"{BASE_URL}/efetch.fcgi", params=params, timeout=15)
        resp.raise_for_status()
        root = ET.fromstring(resp.content)

        for article in root.findall(".//PubmedArticle"):
            pmid = article.findtext(".//PMID", "")
            title = (article.findtext(".//ArticleTitle") or "").strip()
            if pmid:
                titles[pmid] = title

        time.sleep(0.15)

    return titles

def _fetch_citation_counts(pmids: list[str]) -> dict[str, int]:
    """Fetch pmcrefcount (citations) for a batch of PMIDs."""
    if not pmids:
        return {}

    counts: dict[str, int] = {}
    for i in range(0, len(pmids), 50):
        batch = pmids[i : i + 50]
        params = {
            "db": "pubmed",
            "id": ",".join(batch),
            "retmode": "json",
            "email": EMAIL,
        }
        ncbi_key = getattr(settings, "ncbi_api_key", None)
        if ncbi_key and ncbi_key != "...":
            params["api_key"] = ncbi_key

        try:
            resp = requests.get(f"{BASE_URL}/esummary.fcgi", params=params, timeout=15)
            resp.raise_for_status()
            data = resp.json().get("result", {})
            for pmid in batch:
                if pmid in data:
                    counts[pmid] = data[pmid].get("pmcrefcount", 0) or 0
        except Exception:
            log.exception("Failed to fetch citation counts for batch")
            for pmid in batch:
                counts[pmid] = 0

        import time
        time.sleep(0.15)

    return counts


# ---------------------------------------------------------------------------
# Subscription runner
# ---------------------------------------------------------------------------


def run_subscription(sub: dict) -> int:
    """Run a single subscription: search, filter seen, ingest new articles.
    
    Returns the number of new articles ingested.
    """
    sub_id = sub["id"]
    query = sub["query"]
    max_results = sub.get("max_results", 100)

    log.info("Running subscription #%d: %s", sub_id, query)

    # 1) Search PubMed
    try:
        all_pmids = _search_pubmed(
            query, 
            max_results,
            article_type=sub.get("article_type"),
            journals=sub.get("journals"),
            sort_by=sub.get("sort_by", "relevance")
        )
    except Exception:
        log.exception("PubMed search failed for subscription #%d", sub_id)
        return 0

    if not all_pmids:
        log.info("No results for subscription #%d", sub_id)
        update_subscription_stats(sub["user_id"], sub_id, 0)
        return 0

    # 2) Filter out already-seen PMIDs
    seen = get_seen_pmids(sub["user_id"], sub_id)
    new_pmids = [p for p in all_pmids if p not in seen]

    if not new_pmids:
        log.info("Subscription #%d: all %d articles already seen", sub_id, len(all_pmids))
        update_subscription_stats(sub["user_id"], sub_id, 0)
        return 0

    log.info(
        "Subscription #%d: %d new articles (of %d total)",
        sub_id, len(new_pmids), len(all_pmids),
    )
    
    # 2.5) Filter by min_citations
    min_citations = sub.get("min_citations", 0)
    if min_citations > 0:
        counts = _fetch_citation_counts(new_pmids)
        filtered_pmids = [p for p in new_pmids if counts.get(p, 0) >= min_citations]
        log.info("Subscription #%d: filtered from %d down to %d articles based on >=%d citations", 
                 sub_id, len(new_pmids), len(filtered_pmids), min_citations)
        new_pmids = filtered_pmids
        
    if not new_pmids:
        log.info("Subscription #%d: no articles passed citation filter", sub_id)
        update_subscription_stats(sub["user_id"], sub_id, 0)
        return 0

    # 3) Fetch titles for recording
    try:
        titles = _fetch_article_titles(new_pmids)
    except Exception:
        log.exception("Failed to fetch titles for subscription #%d", sub_id)
        titles = {}

    # 4) Ingest into Pinecone via the existing pipeline
    try:
        _ingest_pmids(sub["user_id"], new_pmids)
    except Exception:
        log.exception("Ingestion failed for subscription #%d", sub_id)

    # 5) Record PMIDs as seen
    pmid_title_pairs = [(p, titles.get(p, "")) for p in new_pmids]
    mark_pmids_seen(sub["user_id"], sub_id, pmid_title_pairs)
    update_subscription_stats(sub["user_id"], sub_id, len(new_pmids))

    log.info("Subscription #%d: ingested %d new articles", sub_id, len(new_pmids))
    return len(new_pmids)


def _ingest_pmids(user_id: int, pmids: list[str]) -> None:
    """Ingest a list of PMIDs into Pinecone using the existing pipeline."""
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")

    try:
        from langchain_core.documents import Document
    except ImportError:
        from langchain.schema import Document

    try:
        from langchain_text_splitters import RecursiveCharacterTextSplitter
    except ImportError:
        from langchain.text_splitter import RecursiveCharacterTextSplitter

    from langchain_openai import OpenAIEmbeddings
    from langchain_pinecone import PineconeVectorStore

    # Import fetch_abstracts from pubmed_ingest
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "pubmed_ingest", str(PROJECT_ROOT / "pubmed_ingest.py")
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    docs = mod.fetch_abstracts(pmids)
    if not docs:
        log.info("No documents with content for these PMIDs")
        return

    splitter = RecursiveCharacterTextSplitter(chunk_size=1200, chunk_overlap=120)
    chunks = splitter.split_documents(docs)

    if not chunks:
        return

    embeddings = OpenAIEmbeddings(model=settings.embedding_model)
    namespace = f"user_{user_id}"
    PineconeVectorStore.from_documents(
        chunks, embeddings, index_name=settings.pinecone_index, namespace=namespace
    )
    log.info("Ingested %d chunks from %d articles", len(chunks), len(docs))


def run_all_subscriptions() -> dict[str, int]:
    """Run all active subscriptions. Returns {sub_id: new_count}."""
    active = get_all_active_subscriptions()

    if not active:
        log.info("No active subscriptions to run")
        return {}

    results = {}
    for sub in active:
        try:
            count = run_subscription(sub)
            results[str(sub["id"])] = count
        except Exception:
            log.exception("Subscription #%d failed", sub["id"])
            results[str(sub["id"])] = 0

    return results


# ---------------------------------------------------------------------------
# Simple interval-based scheduler (thread-based, no APScheduler dependency)
# ---------------------------------------------------------------------------

def _scheduler_loop():
    """Runs in a background thread. Checks every 60s if it's time to run."""
    import datetime as dt
    
    global _stop_flag
    last_run_date: str | None = None

    while not _stop_flag:
        now = dt.datetime.now()
        today_str = now.strftime("%Y-%m-%d")
        target_hour = settings.scheduler_hour
        target_minute = settings.scheduler_minute

        # Run once per day at the target time
        if (
            now.hour == target_hour
            and now.minute == target_minute
            and last_run_date != today_str
        ):
            log.info("Scheduler: starting daily subscription run")
            try:
                run_all_subscriptions()
            except Exception:
                log.exception("Scheduler: daily run failed")
            last_run_date = today_str

        # Sleep 30 seconds between checks
        for _ in range(30):
            if _stop_flag:
                return
            time.sleep(1)


def start_scheduler() -> None:
    """Start the background scheduler thread."""
    global _scheduler_thread, _stop_flag
    _stop_flag = False
    _scheduler_thread = Thread(target=_scheduler_loop, daemon=True, name="subscription-scheduler")
    _scheduler_thread.start()
    log.info(
        "Scheduler started — daily run at %02d:%02d",
        settings.scheduler_hour,
        settings.scheduler_minute,
    )


def stop_scheduler() -> None:
    """Signal the scheduler thread to stop."""
    global _stop_flag
    _stop_flag = True
    log.info("Scheduler stop requested")
