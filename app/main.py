from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from datetime import datetime
from threading import Thread

from fastapi import FastAPI, HTTPException
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.config import PROJECT_ROOT, settings
from app.database import (
    init_db,
    add_subscription,
    get_subscriptions,
    get_subscription,
    toggle_subscription,
    delete_subscription,
)
from app.rag import answer_question
from app.scheduler import start_scheduler, stop_scheduler, run_subscription, run_all_subscriptions

log = logging.getLogger(__name__)

STATIC_DIR = PROJECT_ROOT / "static"


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown."""
    init_db()
    start_scheduler()
    log.info("Medical RAG started — scheduler active")
    yield
    stop_scheduler()
    log.info("Medical RAG shutting down")


app = FastAPI(title="Medical RAG", version="0.2.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class QueryBody(BaseModel):
    question: str = Field(..., min_length=2, max_length=4000)
    k: int = Field(6, ge=1, le=20)


class QueryResponse(BaseModel):
    answer: str
    sources: list[dict]
    reasoning_trace: dict | None = None
    query_analysis: dict | None = None


class SubscriptionCreate(BaseModel):
    query: str = Field(..., min_length=2, max_length=4000)
    max_results: int = Field(100, ge=10, le=1000)


class SubscriptionToggle(BaseModel):
    is_active: bool


class IngestBody(BaseModel):
    query: str | None = Field(None, min_length=2, max_length=4000)
    pmids: list[str] | None = Field(None)
    max_results: int = Field(50, ge=1, le=2000)


class DiscoverBody(BaseModel):
    topic: str = Field(..., min_length=2, max_length=4000)
    max_results: int = Field(20, ge=1, le=100)


class ArticleSummary(BaseModel):
    pmid: str
    title: str
    year: str = ""
    journal: str = ""


class DiscoverResponse(BaseModel):
    query_analysis: dict
    articles: list[ArticleSummary]


# In-memory store for ingestion tasks
_ingest_tasks: dict[str, dict] = {}


# ---------------------------------------------------------------------------
# Existing endpoints
# ---------------------------------------------------------------------------

@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/stats")
def index_stats():
    if not settings.pinecone_api_key:
        raise HTTPException(503, detail="PINECONE_API_KEY not configured")
    try:
        from pinecone import Pinecone
        pc = Pinecone(api_key=settings.pinecone_api_key)
        idx = pc.Index(settings.pinecone_index)
        stats = idx.describe_index_stats()
        return {
            "index": settings.pinecone_index,
            "stats": jsonable_encoder(stats.to_dict()),
        }
    except Exception as e:
        raise HTTPException(502, detail=str(e)) from e


@app.post("/api/query", response_model=QueryResponse)
def query(body: QueryBody):
    if not settings.openai_api_key:
        raise HTTPException(503, detail="OPENAI_API_KEY not configured")
    if not settings.pinecone_api_key:
        raise HTTPException(503, detail="PINECONE_API_KEY not configured")
    try:
        result = answer_question(body.question.strip(), k=body.k)
        return QueryResponse(**result)
    except Exception as e:
        raise HTTPException(500, detail=str(e)) from e


# ---------------------------------------------------------------------------
# Subscription endpoints
# ---------------------------------------------------------------------------

@app.get("/api/subscriptions")
def list_subscriptions():
    """List all subscriptions."""
    return get_subscriptions()


@app.post("/api/subscriptions", status_code=201)
def create_subscription(body: SubscriptionCreate):
    """Create a new subscription."""
    sub = add_subscription(body.query.strip(), body.max_results)
    return sub


@app.patch("/api/subscriptions/{sub_id}")
def patch_subscription(sub_id: int, body: SubscriptionToggle):
    """Toggle a subscription's active state."""
    sub = toggle_subscription(sub_id, body.is_active)
    if sub is None:
        raise HTTPException(404, detail="Subscription not found")
    return sub


@app.delete("/api/subscriptions/{sub_id}")
def remove_subscription(sub_id: int):
    """Delete a subscription."""
    ok = delete_subscription(sub_id)
    if not ok:
        raise HTTPException(404, detail="Subscription not found")
    return {"deleted": True}


@app.post("/api/subscriptions/{sub_id}/run")
def trigger_subscription(sub_id: int):
    """Manually trigger a subscription run now (in a background thread)."""
    sub = get_subscription(sub_id)
    if sub is None:
        raise HTTPException(404, detail="Subscription not found")

    # Run in a background thread so we don't block the request
    def _bg():
        try:
            count = run_subscription(sub)
            log.info("Manual run of subscription #%d complete: %d new articles", sub_id, count)
        except Exception:
            log.exception("Manual run of subscription #%d failed", sub_id)

    thread = Thread(target=_bg, daemon=True, name=f"sub-run-{sub_id}")
    thread.start()

    return {"status": "started", "subscription_id": sub_id}


@app.post("/api/subscriptions/run-all")
def trigger_all_subscriptions():
    """Manually trigger all active subscriptions."""
    def _bg():
        try:
            results = run_all_subscriptions()
            log.info("Manual run-all complete: %s", results)
        except Exception:
            log.exception("Manual run-all failed")

    thread = Thread(target=_bg, daemon=True, name="sub-run-all")
    thread.start()

    return {"status": "started"}


# ---------------------------------------------------------------------------
# Discovery endpoint (Phase 1 & 2)
# ---------------------------------------------------------------------------

@app.post("/api/discover", response_model=DiscoverResponse)
def discover_articles(body: DiscoverBody):
    """
    Phase 1 & 2: User says what they want -> CoT reasoning -> PubMed discovery.
    """
    from app.reasoning import InputReasoner
    from pubmed_ingest import search_pubmed, fetch_abstracts
    
    # 1. Chain of Thought Reasoning
    reasoner = InputReasoner()
    analysis = reasoner.reason(body.topic)
    
    # 2. PubMed Query & Discovery
    pmids = search_pubmed(analysis.pubmed_query, max_results=body.max_results)
    
    # Fetch basic info for the articles to show in the UI
    articles = []
    if pmids:
        docs = fetch_abstracts(pmids, include_pmc_fulltext=False)
        for d in docs:
            m = d.metadata
            articles.append(ArticleSummary(
                pmid=str(m.get("pmid", "")),
                title=str(m.get("title", "Untitled")),
                year=str(m.get("year", "")),
                journal=str(m.get("journal", ""))
            ))
            
    return {
        "query_analysis": analysis.to_dict(),
        "articles": articles
    }


# ---------------------------------------------------------------------------
# Ingestion endpoints (Phase 3)
# ---------------------------------------------------------------------------

@app.post("/api/ingest")
def trigger_ingest(body: IngestBody):
    """Trigger a manual ingestion run from a query or specific PMIDs."""
    import uuid
    task_id = str(uuid.uuid4())
    
    label = body.query if body.query else f"{len(body.pmids or [])} specific articles"
    
    _ingest_tasks[task_id] = {
        "id": task_id,
        "query": label,
        "status": "running",
        "progress": "Starting ingestion...",
        "count": 0,
        "error": None,
        "started_at": datetime.now().isoformat(),
    }

    def _bg_ingest():
        try:
            log.info("Starting background ingestion for: %s", label)
            from pubmed_ingest import ingest_pubmed_query, ingest_pmids
            
            def _cb(p):
                _ingest_tasks[task_id]["progress"] = p
            
            if body.pmids:
                count = ingest_pmids(body.pmids, progress_cb=_cb)
            elif body.query:
                count = ingest_pubmed_query(body.query, body.max_results, progress_cb=_cb)
            else:
                raise ValueError("Either query or pmids must be provided")
            
            log.info("Background ingestion complete: %d articles", count or 0)
            _ingest_tasks[task_id]["status"] = "completed"
            _ingest_tasks[task_id]["progress"] = f"Finished. Ingested {count or 0} articles."
            _ingest_tasks[task_id]["count"] = count or 0
        except Exception as e:
            log.exception("Ingest task failed for: %s", label)
            _ingest_tasks[task_id]["status"] = "failed"
            _ingest_tasks[task_id]["error"] = str(e)
            _ingest_tasks[task_id]["progress"] = "Failed."

    Thread(target=_bg_ingest, daemon=True).start()
    return {"task_id": task_id}


@app.get("/api/ingest/status/{task_id}")
def get_ingest_status(task_id: str):
    """Get status of an ingestion task."""
    task = _ingest_tasks.get(task_id)
    if not task:
        raise HTTPException(404, detail="Task not found")
    return task


@app.get("/api/ingest/tasks")
def list_ingest_tasks():
    """List recent ingestion tasks."""
    return list(_ingest_tasks.values())


# ---------------------------------------------------------------------------
# Static files + SPA
# ---------------------------------------------------------------------------

if STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
def serve_app():
    index = STATIC_DIR / "index.html"
    if not index.is_file():
        return {"detail": "Frontend not found. Add static/index.html"}
    return FileResponse(index)
