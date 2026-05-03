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
"""


def init_db() -> None:
    """Create tables if they don't exist."""
    conn = _conn()
    try:
        conn.executescript(_SCHEMA)
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
        cur = conn.execute(
            "INSERT INTO users (email, hashed_password) VALUES (?, ?)",
            (email, hashed_password),
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
