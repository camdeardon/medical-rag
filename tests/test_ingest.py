import pytest
from unittest.mock import patch, MagicMock
from pubmed_ingest import search_pubmed

@patch("pubmed_ingest.requests.get")
def test_search_pubmed_success(mock_get):
    # Mock successful response
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "esearchresult": {
            "idlist": ["12345", "67890"]
        }
    }
    mock_get.return_value = mock_resp
    
    ids = search_pubmed("test query")
    assert ids == ["12345", "67890"]

@patch("pubmed_ingest.requests.get")
def test_search_pubmed_missing_idlist(mock_get):
    # Mock error response (e.g. rate limit or invalid query)
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "esearchresult": {
            "error": "Phrase not found"
        }
    }
    mock_get.return_value = mock_resp
    
    # Should safely return empty list instead of KeyError
    ids = search_pubmed("invalid query that returns no idlist")
    assert ids == []

@patch("app.scheduler.requests.get")
def test_fetch_citation_counts(mock_get):
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "result": {
            "uids": ["123", "456"],
            "123": {"pmcrefcount": 10},
            "456": {"pmcrefcount": 0}
        }
    }
    mock_get.return_value = mock_resp
    
    from app.scheduler import _fetch_citation_counts
    counts = _fetch_citation_counts(["123", "456"])
    
    assert counts["123"] == 10
    assert counts["456"] == 0
