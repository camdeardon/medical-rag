import pytest
import sqlite3
from app.database import (
    create_user,
    get_user_by_email,
    update_user_status,
    add_subscription,
    update_subscription_stats,
    get_system_stats
)

def test_create_user(db_conn):
    # Test creation
    user = create_user("dbtest@example.com", "fakehash")
    assert user["email"] == "dbtest@example.com"
    assert user["is_admin"] == 0

    # Test retrieval
    user2 = get_user_by_email("dbtest@example.com")
    assert user["id"] == user2["id"]

def test_set_user_admin(db_conn):
    user = create_user("admintest@example.com", "fakehash")
    assert user["is_admin"] == 0
    
    update_user_status(user["id"], is_approved=True, is_admin=True)
    
    row = db_conn.execute("SELECT is_admin FROM users WHERE id=?", (user["id"],)).fetchone()
    assert row["is_admin"] == 1

def test_add_subscription_and_stats(db_conn):
    user = create_user("subtest@example.com", "fakehash")
    
    sub = add_subscription(
        user_id=user["id"],
        query="COVID-19",
        max_results=50,
        article_type="Review",
        journals="Lancet",
        sort_by="date",
        min_citations=5
    )
    
    assert sub["query"] == "COVID-19"
    assert sub["max_results"] == 50
    assert sub["article_type"] == "Review"
    assert sub["journals"] == "Lancet"
    assert sub["sort_by"] == "date"
    assert sub["min_citations"] == 5
    assert sub["is_active"] == 1

    # Test updating stats
    update_subscription_stats(user["id"], sub["id"], 12)
    
    # Should update last_run_at and total_ingested
    row = db_conn.execute("SELECT total_ingested, last_run_at FROM subscriptions WHERE id=?", (sub["id"],)).fetchone()
    assert row["total_ingested"] == 12
    assert row["last_run_at"] is not None

def test_get_system_stats(db_conn):
    stats = get_system_stats()
    assert "db_size_mb" in stats
