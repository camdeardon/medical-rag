"""Lightweight MeSH term validation via NCBI E-utilities."""

from __future__ import annotations

import logging
from typing import Any

import requests

from app.config import settings

log = logging.getLogger(__name__)

BASE_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
EMAIL = "camadamdeardon+pubmed@gmail.com"


def validate_mesh_terms(terms: list[str]) -> dict[str, bool]:
    """Check which terms are valid MeSH descriptors via esearch.

    Returns a mapping {term: is_valid}.
    """
    results: dict[str, bool] = {}
    for term in terms:
        try:
            params: dict[str, Any] = {
                "db": "mesh",
                "term": f'"{term}"[MH]',
                "retmode": "json",
                "email": EMAIL,
            }
            ncbi_key = getattr(settings, "ncbi_api_key", None)
            if ncbi_key and ncbi_key != "...":
                params["api_key"] = ncbi_key
            resp = requests.get(f"{BASE_URL}/esearch.fcgi", params=params, timeout=8)
            resp.raise_for_status()
            count = int(resp.json().get("esearchresult", {}).get("count", 0))
            results[term] = count > 0
        except Exception:
            log.warning("MeSH validation failed for term: %s", term)
            results[term] = False  # assume invalid on error
    return results


def suggest_mesh_terms(free_text: str, max_suggestions: int = 8) -> list[str]:
    """Use NCBI's spell / suggestion API to find relevant MeSH terms.

    Falls back to an esearch-based approach if the suggestion endpoint
    doesn't return useful results.
    """
    suggestions: list[str] = []
    try:
        params: dict[str, Any] = {
            "db": "mesh",
            "term": free_text,
            "retmax": max_suggestions,
            "retmode": "json",
            "email": EMAIL,
        }
        ncbi_key = getattr(settings, "ncbi_api_key", None)
        if ncbi_key and ncbi_key != "...":
            params["api_key"] = ncbi_key
        resp = requests.get(f"{BASE_URL}/esearch.fcgi", params=params, timeout=8)
        resp.raise_for_status()
        data = resp.json().get("esearchresult", {})
        term_list = data.get("translationstack", [])
        for item in term_list:
            if isinstance(item, dict) and "term" in item:
                raw = item["term"]
                # Strip field tags for clean display
                clean = raw.split("[")[0].strip().strip('"')
                if clean and clean.lower() != "and" and clean.lower() != "or":
                    suggestions.append(clean)
    except Exception:
        log.warning("MeSH suggestion lookup failed for: %s", free_text)

    return suggestions[:max_suggestions]
