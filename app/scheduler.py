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


def _search_pubmed(query: str, max_results: int = 100) -> list[str]:
    """Search PubMed and return list of PMIDs."""
    params = {
        "db": "pubmed",
        "term": query,
        "retmax": max_results,
        "retmode": "json",
        "email": EMAIL,
        "sort": "relevance",
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
        all_pmids = _search_pubmed(query, max_results)
    except Exception:
        log.exception("PubMed search failed for subscription #%d", sub_id)
        return 0

    if not all_pmids:
        log.info("No results for subscription #%d", sub_id)
        update_subscription_stats(sub_id, 0)
        return 0

    # 2) Filter out already-seen PMIDs
    seen = get_seen_pmids(sub_id)
    new_pmids = [p for p in all_pmids if p not in seen]

    if not new_pmids:
        log.info("Subscription #%d: all %d articles already seen", sub_id, len(all_pmids))
        update_subscription_stats(sub_id, 0)
        return 0

    log.info(
        "Subscription #%d: %d new articles (of %d total)",
        sub_id, len(new_pmids), len(all_pmids),
    )

    # 3) Fetch titles for recording
    try:
        titles = _fetch_article_titles(new_pmids)
    except Exception:
        log.exception("Failed to fetch titles for subscription #%d", sub_id)
        titles = {}

    # 4) Ingest into Pinecone via the existing pipeline
    try:
        _ingest_pmids(new_pmids)
    except Exception:
        log.exception("Ingestion failed for subscription #%d", sub_id)
        # Still mark PMIDs as seen to avoid retrying endlessly
        # (they may have partially ingested)

    # 5) Record PMIDs as seen
    pmid_title_pairs = [(p, titles.get(p, "")) for p in new_pmids]
    mark_pmids_seen(sub_id, pmid_title_pairs)
    update_subscription_stats(sub_id, len(new_pmids))

    log.info("Subscription #%d: ingested %d new articles", sub_id, len(new_pmids))
    return len(new_pmids)


def _ingest_pmids(pmids: list[str]) -> None:
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
    PineconeVectorStore.from_documents(
        chunks, embeddings, index_name=settings.pinecone_index
    )
    log.info("Ingested %d chunks from %d articles", len(chunks), len(docs))


def run_all_subscriptions() -> dict[str, int]:
    """Run all active subscriptions. Returns {sub_id: new_count}."""
    subs = get_subscriptions()
    active = [s for s in subs if s.get("is_active")]

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
