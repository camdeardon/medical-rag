"""SQLite persistence for query subscriptions and PMID deduplication."""

from __future__ import annotations

import sqlite3
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from app.config import settings

log = logging.getLogger(__name__)

_DB_PATH: Path | None = None


def _get_db_path() -> Path:
    global _DB_PATH
    if _DB_PATH is None:
        _DB_PATH = Path(settings.db_path)
        _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    return _DB_PATH


def _conn() -> sqlite3.Connection:
    """Open a connection with row_factory set."""
    conn = sqlite3.connect(str(_get_db_path()))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    email           TEXT UNIQUE NOT NULL,
    hashed_password TEXT NOT NULL,
    is_approved     INTEGER DEFAULT 0,
    is_admin        INTEGER DEFAULT 0,
    created_at      TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS subscriptions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL,
    query           TEXT NOT NULL,
    max_results     INTEGER DEFAULT 100,
    is_active       INTEGER DEFAULT 1,
    created_at      TEXT DEFAULT (datetime('now')),
    last_run_at     TEXT,
    run_count       INTEGER DEFAULT 0,
    articles_found  INTEGER DEFAULT 0,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS seen_pmids (
    pmid            TEXT NOT NULL,
    subscription_id INTEGER NOT NULL,
    user_id         INTEGER NOT NULL,
    title           TEXT,
    ingested_at     TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (pmid, subscription_id),
    FOREIGN KEY (subscription_id) REFERENCES subscriptions(id) ON DELETE CASCADE,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS evaluation_logs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL,
    question        TEXT NOT NULL,
    answer          TEXT NOT NULL,
    sources_json    TEXT,
    query_analysis_json TEXT,
    reasoning_trace_json TEXT,
    created_at      TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);
"""


def init_db() -> None:
    """Create tables if they don't exist and handle simple migrations."""
    conn = _conn()
    try:
        conn.executescript(_SCHEMA)
        
        # Simple migration: Add user_id to subscriptions and seen_pmids if missing
        for table in ["subscriptions", "seen_pmids"]:
            cur = conn.execute(f"PRAGMA table_info({table})")
            cols = [r["name"] for r in cur.fetchall()]
            if "user_id" not in cols:
                log.info("Migrating table %s: adding user_id column", table)
                # Default to 1 (first user) or NULL? Let's use NULLable for existing rows if we don't have a user yet.
                # But our schema says NOT NULL. Since we just added accounts, we might want to wipe or set a default.
                # For safety on existing data, we'll add it as nullable first or with a default of 1.
                try:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN user_id INTEGER REFERENCES users(id) ON DELETE CASCADE")
                except sqlite3.OperationalError as e:
                    log.warning("Migration failed for %s: %s", table, e)
        
        # Migrations for users table
        for col in ["is_approved", "is_admin"]:
            cur = conn.execute("PRAGMA table_info(users)")
            cols = [r["name"] for r in cur.fetchall()]
            if col not in cols:
                log.info("Migrating table users: adding %s column", col)
                try:
                    conn.execute(f"ALTER TABLE users ADD COLUMN {col} INTEGER DEFAULT 0")
                except sqlite3.OperationalError as e:
                    log.warning("Migration failed for users.%s: %s", col, e)
        
        # Make the first user an admin and approved automatically to bootstrap
        conn.execute("UPDATE users SET is_approved = 1, is_admin = 1 WHERE id = 1")
        
        conn.commit()
        log.info("Database initialized at %s", _get_db_path())
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# User CRUD
# ---------------------------------------------------------------------------


def create_user(email: str, hashed_password: str) -> dict[str, Any]:
    """Insert a new user."""
    conn = _conn()
    try:
        # Hardcode the super-admin
        is_admin = 1 if email.lower() == "camadamdeardon@gmail.com" else 0
        is_approved = 1 if is_admin else 0
        
        cur = conn.execute(
            "INSERT INTO users (email, hashed_password, is_admin, is_approved) VALUES (?, ?, ?, ?)",
            (email, hashed_password, is_admin, is_approved),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM users WHERE id = ?", (cur.lastrowid,)
        ).fetchone()
        return dict(row)
    finally:
        conn.close()


def get_user_by_email(email: str) -> dict[str, Any] | None:
    """Find a user by email."""
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT * FROM users WHERE email = ?", (email,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_user_by_id(user_id: int) -> dict[str, Any] | None:
    """Find a user by ID."""
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT * FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_all_users() -> list[dict[str, Any]]:
    """Return all users."""
    conn = _conn()
    try:
        rows = conn.execute("SELECT * FROM users ORDER BY created_at DESC").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def update_user_status(user_id: int, is_approved: bool, is_admin: bool) -> bool:
    """Update user approval and admin status."""
    conn = _conn()
    try:
        cur = conn.execute(
            "UPDATE users SET is_approved = ?, is_admin = ? WHERE id = ?",
            (1 if is_approved else 0, 1 if is_admin else 0, user_id),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def get_user_details_admin(user_id: int) -> dict[str, Any] | None:
    """Get aggregated details for a specific user (admin view)."""
    import json
    user = get_user_by_id(user_id)
    if not user:
        return None
        
    conn = _conn()
    try:
        # Get total saved articles (seen_pmids)
        row = conn.execute("SELECT COUNT(*) as cnt FROM seen_pmids WHERE user_id = ?", (user_id,)).fetchone()
        saved_count = row["cnt"] if row else 0
        
        # Get subscriptions
        subs_rows = conn.execute("SELECT * FROM subscriptions WHERE user_id = ? ORDER BY created_at DESC", (user_id,)).fetchall()
        subscriptions = [dict(r) for r in subs_rows]
        
        # Get evaluation logs
        eval_rows = conn.execute("SELECT * FROM evaluation_logs WHERE user_id = ? ORDER BY created_at DESC LIMIT 50", (user_id,)).fetchall()
        evals = []
        for r in eval_rows:
            d = dict(r)
            d["sources"] = json.loads(d["sources_json"]) if d["sources_json"] else []
            d["query_analysis"] = json.loads(d["query_analysis_json"]) if d["query_analysis_json"] else None
            d["reasoning_trace"] = json.loads(d["reasoning_trace_json"]) if d["reasoning_trace_json"] else None
            del d["sources_json"]
            del d["query_analysis_json"]
            del d["reasoning_trace_json"]
            evals.append(d)
            
        return {
            "user": user,
            "saved_articles_count": saved_count,
            "subscriptions": subscriptions,
            "evaluations": evals
        }
    finally:
        conn.close()


def get_advanced_analytics(user_id: int | None = None) -> dict[str, Any]:
    """Model data around recurring queries, areas of interest, and data yield."""
    from collections import Counter
    import re
    conn = _conn()
    try:
        # 1. Get all queries from evaluation logs
        if user_id:
            evals = conn.execute("SELECT question FROM evaluation_logs WHERE user_id = ?", (user_id,)).fetchall()
            subs = conn.execute("SELECT query, articles_found FROM subscriptions WHERE user_id = ?", (user_id,)).fetchall()
        else:
            evals = conn.execute("SELECT question FROM evaluation_logs").fetchall()
            subs = conn.execute("SELECT query, articles_found FROM subscriptions").fetchall()
            
        questions = [r["question"].strip().lower() for r in evals]
        sub_queries = [r["query"].strip().lower() for r in subs]
        
        # Combine all to find recurring queries
        all_queries = questions + sub_queries
        query_counts = Counter(all_queries)
        top_queries = [{"query": q, "count": c} for q, c in query_counts.most_common(10)]
        
        # 3. Areas of Interest (NLP extraction using RAKE)
        from rake_nltk import Rake
        # Initialize RAKE
        rake = Rake(min_length=1, max_length=3)
        # Extract keywords from each query and count
        all_phrases = []
        for q in all_queries:
            if not q.strip(): continue
            rake.extract_keywords_from_text(q)
            phrases = rake.get_ranked_phrases()
            if not phrases:
                # Fallback to the whole query if rake doesn't find anything
                all_phrases.append(q)
            else:
                all_phrases.extend(phrases)
                
        phrase_counts = Counter(all_phrases)
        top_areas = [{"topic": w, "mentions": c} for w, c in phrase_counts.most_common(10)]
        
        # 4. Data Yield (Articles contained on each piece/subscription)
        # Group by subscription query
        yield_map = {}
        for r in subs:
            q = r["query"].strip().lower()
            if q not in yield_map:
                yield_map[q] = 0
            yield_map[q] += r["articles_found"]
            
        yield_list = [{"query": q, "articles": c} for q, c in yield_map.items()]
        yield_list.sort(key=lambda x: x["articles"], reverse=True)
        top_yields = yield_list[:10]
        
        # 5. User Leaderboard (only if global)
        leaderboard = []
        if not user_id:
            users_stats = conn.execute("""
                SELECT u.id, u.email, 
                       (SELECT COUNT(*) FROM evaluation_logs WHERE user_id = u.id) as q_count,
                       (SELECT COUNT(*) FROM seen_pmids WHERE user_id = u.id) as a_count
                FROM users u
                ORDER BY (q_count + a_count) DESC LIMIT 10
            """).fetchall()
            leaderboard = [{"id": r["id"], "email": r["email"], "activity_score": r["q_count"] + r["a_count"]} for r in users_stats]
        
        return {
            "recurring_queries": top_queries,
            "areas_of_interest": top_areas,
            "data_yield": top_yields,
            "user_leaderboard": leaderboard
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Evaluation Logs CRUD
# ---------------------------------------------------------------------------

import json

def add_evaluation_log(
    user_id: int,
    question: str,
    answer: str,
    sources: list[dict],
    query_analysis: dict | None,
    reasoning_trace: dict | None
) -> int:
    """Log an evaluation query."""
    conn = _conn()
    try:
        cur = conn.execute(
            """INSERT INTO evaluation_logs 
               (user_id, question, answer, sources_json, query_analysis_json, reasoning_trace_json)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                user_id,
                question,
                answer,
                json.dumps(sources) if sources else "[]",
                json.dumps(query_analysis) if query_analysis else None,
                json.dumps(reasoning_trace) if reasoning_trace else None,
            )
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def get_evaluation_logs(limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
    """Retrieve evaluation logs."""
    conn = _conn()
    try:
        rows = conn.execute(
            """SELECT el.*, u.email as user_email 
               FROM evaluation_logs el
               JOIN users u ON el.user_id = u.id
               ORDER BY el.created_at DESC
               LIMIT ? OFFSET ?""",
            (limit, offset)
        ).fetchall()
        
        result = []
        for r in rows:
            d = dict(r)
            d["sources"] = json.loads(d["sources_json"]) if d["sources_json"] else []
            d["query_analysis"] = json.loads(d["query_analysis_json"]) if d["query_analysis_json"] else None
            d["reasoning_trace"] = json.loads(d["reasoning_trace_json"]) if d["reasoning_trace_json"] else None
            del d["sources_json"]
            del d["query_analysis_json"]
            del d["reasoning_trace_json"]
            result.append(d)
        return result
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Subscription CRUD
# ---------------------------------------------------------------------------


def add_subscription(user_id: int, query: str, max_results: int = 100) -> dict[str, Any]:
    """Insert a new subscription and return it."""
    conn = _conn()
    try:
        cur = conn.execute(
            "INSERT INTO subscriptions (user_id, query, max_results) VALUES (?, ?, ?)",
            (user_id, query, max_results),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM subscriptions WHERE id = ?", (cur.lastrowid,)
        ).fetchone()
        return dict(row)
    finally:
        conn.close()


def get_subscriptions(user_id: int) -> list[dict[str, Any]]:
    """Return all subscriptions for a user."""
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT * FROM subscriptions WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_all_active_subscriptions() -> list[dict[str, Any]]:
    """Return every active subscription across all users."""
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT * FROM subscriptions WHERE is_active = 1"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_subscription(user_id: int, sub_id: int) -> dict[str, Any] | None:
    """Return a single subscription by ID, scoped to user."""
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT * FROM subscriptions WHERE id = ? AND user_id = ?",
            (sub_id, user_id),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def toggle_subscription(user_id: int, sub_id: int, is_active: bool) -> dict[str, Any] | None:
    """Toggle active state, scoped to user."""
    conn = _conn()
    try:
        conn.execute(
            "UPDATE subscriptions SET is_active = ? WHERE id = ? AND user_id = ?",
            (1 if is_active else 0, sub_id, user_id),
        )
        conn.commit()
        return get_subscription(user_id, sub_id)
    finally:
        conn.close()


def delete_subscription(user_id: int, sub_id: int) -> bool:
    """Delete a subscription, scoped to user."""
    conn = _conn()
    try:
        cur = conn.execute(
            "DELETE FROM subscriptions WHERE id = ? AND user_id = ?",
            (sub_id, user_id),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# PMID tracking
# ---------------------------------------------------------------------------


def get_seen_pmids(user_id: int, subscription_id: int) -> set[str]:
    """Return the set of PMIDs already ingested for this subscription."""
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT pmid FROM seen_pmids WHERE subscription_id = ? AND user_id = ?",
            (subscription_id, user_id),
        ).fetchall()
        return {r["pmid"] for r in rows}
    finally:
        conn.close()


def get_all_seen_pmids(user_id: int) -> set[str]:
    """Return every PMID across all subscriptions for this user."""
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT DISTINCT pmid FROM seen_pmids WHERE user_id = ?",
            (user_id,),
        ).fetchall()
        return {r["pmid"] for r in rows}
    finally:
        conn.close()


def mark_pmids_seen(
    user_id: int, subscription_id: int, pmids: list[tuple[str, str]]
) -> None:
    """Record PMIDs as seen."""
    if not pmids:
        return
    conn = _conn()
    try:
        conn.executemany(
            "INSERT OR IGNORE INTO seen_pmids (pmid, subscription_id, user_id, title) VALUES (?, ?, ?, ?)",
            [(p, subscription_id, user_id, t) for p, t in pmids],
        )
        conn.commit()
    finally:
        conn.close()


def update_subscription_stats(
    user_id: int, sub_id: int, new_articles: int
) -> None:
    """Update stats, scoped to user."""
    conn = _conn()
    try:
        conn.execute(
            """UPDATE subscriptions 
               SET last_run_at = datetime('now'),
                   run_count = run_count + 1,
                   articles_found = articles_found + ?
               WHERE id = ? AND user_id = ?""",
            (new_articles, sub_id, user_id),
        )
        conn.commit()
    finally:
        conn.close()


def get_subscription_pmid_count(user_id: int, sub_id: int) -> int:
    """Return the number of PMIDs seen for a subscription, scoped to user."""
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM seen_pmids WHERE subscription_id = ? AND user_id = ?",
            (sub_id, user_id),
        ).fetchone()
        return row["cnt"] if row else 0
    finally:
        conn.close()

# ---------------------------------------------------------------------------
# System Stats
# ---------------------------------------------------------------------------

def get_system_stats() -> dict[str, int]:
    """Return total counts for users, queries, and active subscriptions."""
    conn = _conn()
    try:
        users = conn.execute("SELECT COUNT(*) as cnt FROM users").fetchone()["cnt"]
        queries = conn.execute("SELECT COUNT(*) as cnt FROM evaluation_logs").fetchone()["cnt"]
        subscriptions = conn.execute("SELECT COUNT(*) as cnt FROM subscriptions WHERE is_active = 1").fetchone()["cnt"]
        return {
            "total_users": users,
            "total_queries": queries,
            "active_subscriptions": subscriptions,
        }
    finally:
        conn.close()
