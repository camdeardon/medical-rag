import pytest
from unittest.mock import patch

def test_get_public_stats(client):
    response = client.get("/api/stats")
    assert response.status_code == 200
    data = response.json()
    assert "users" in data
    assert "queries" in data

def test_auth_me(client, mock_user):
    response = client.get("/api/auth/me")
    assert response.status_code == 200
    data = response.json()
    assert data["email"] == mock_user["email"]
    assert data["is_admin"] == False

def test_admin_stats_denied_for_regular_user(client):
    response = client.get("/api/admin/system-stats")
    assert response.status_code == 403

def test_admin_stats_allowed_for_admin(admin_client):
    response = admin_client.get("/api/admin/system-stats")
    assert response.status_code == 200
    assert "db_size_mb" in response.json()

@patch("app.main.run_subscription")
def test_create_subscription(mock_run, client):
    payload = {
        "query": "Cancer Research",
        "max_results": 15,
        "article_type": "Review",
        "journals": "Nature, Science",
        "sort_by": "date",
        "min_citations": 10
    }
    response = client.post("/api/subscriptions", json=payload)
    assert response.status_code == 201
    
    data = response.json()
    assert data["query"] == payload["query"]
    assert data["max_results"] == payload["max_results"]
    assert data["article_type"] == payload["article_type"]
    assert data["journals"] == payload["journals"]
    assert data["min_citations"] == payload["min_citations"]
    
    # Ensure background task was triggered
    mock_run.assert_called_once()
