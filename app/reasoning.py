"""Chain-of-thought reasoning for medical RAG — input decomposition + output synthesis."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from typing import Any

from app.config import settings

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class ReasonedQuery:
    """Output of the InputReasoner — a fully decomposed query plan."""

    original_question: str

    # CoT trace
    intent_analysis: str = ""
    intent_type: str = "GENERAL"  # OVERVIEW | MECHANISM | COMPARISON | EVIDENCE | SPECIFIC | SAFETY
    identified_entities: list[str] = field(default_factory=list)
    mesh_terms: list[str] = field(default_factory=list)
    query_strategy: str = ""

    # Constructed queries
    pubmed_query: str = ""
    embedding_queries: list[str] = field(default_factory=list)
    search_filters: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ReasonedAnswer:
    """Output of the OutputReasoner — a synthesised answer with reasoning trace."""

    answer: str = ""
    confidence: str = "MODERATE"  # HIGH | MODERATE | LOW | INSUFFICIENT
    evidence_summary: list[dict[str, str]] = field(default_factory=list)
    synthesis: str = ""
    evidence_gaps: list[str] = field(default_factory=list)

    def trace_dict(self) -> dict[str, Any]:
        return {
            "confidence": self.confidence,
            "evidence_summary": self.evidence_summary,
            "synthesis": self.synthesis,
            "evidence_gaps": self.evidence_gaps,
        }


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

INPUT_COT_SYSTEM = """\
You are a biomedical search strategist. Given a user's question about medicine \
or biology, reason step-by-step to construct the most effective PubMed search \
strategy.

## Step 1 — Intent Analysis
What is the user actually asking?  Classify the intent into exactly ONE of:
  OVERVIEW   — broad topic review (e.g. "tell me about immunology")
  MECHANISM  — how something works (e.g. "how do mRNA vaccines work")
  COMPARISON — comparing treatments or approaches
  EVIDENCE   — looking for clinical evidence / trials
  SPECIFIC   — narrow factual question
  SAFETY     — drug interactions, side-effects, contraindications

Write 2-4 sentences explaining your reasoning.

## Step 2 — Entity Extraction
List every biomedical entity you can identify: diseases, drugs, genes, \
proteins, pathways, organisms, anatomical structures, procedures.

## Step 3 — MeSH Term Mapping
Map extracted entities to their formal MeSH descriptors.  Use the controlled \
NLM vocabulary.  Examples:
  "heart attack"         → Myocardial Infarction
  "blood pressure drugs" → Antihypertensive Agents
  "cancer immunotherapy" → Immunotherapy, Neoplasms

## Step 4 — PubMed Query Construction
Build a PubMed E-utilities query using:
  • MeSH terms with [mh] field tags for precision
  • Title/Abstract terms with [tiab] for recent / un-indexed articles
  • Boolean operators AND, OR
  • Publication-type filters adapted to intent:
      OVERVIEW  → Review[pt] OR Systematic Review[pt]
      EVIDENCE  → Clinical Trial[pt] OR Meta-Analysis[pt]
      SAFETY    → include "Drug Interactions"[mh] or "Drug-Related Side Effects and Adverse Reactions"[mh]
  • Date filter if appropriate (prefer recent 5-6 years for overview)
  • English[la]

## Step 5 — Embedding Query Reformulation
Rewrite the original question as 2-3 precise, semantically rich queries \
optimised for vector similarity search against biomedical abstracts.

─────────────────────────────────────────────────
Respond ONLY with valid JSON matching this schema (no markdown fences):
{
  "intent_analysis": "<string>",
  "intent_type": "<OVERVIEW|MECHANISM|COMPARISON|EVIDENCE|SPECIFIC|SAFETY>",
  "identified_entities": ["<string>", ...],
  "mesh_terms": ["<string>", ...],
  "query_strategy": "<string>",
  "pubmed_query": "<string>",
  "embedding_queries": ["<string>", ...],
  "search_filters": {
    "date_range": "<YYYY:YYYY | empty string>",
    "publication_types": ["<string>", ...],
    "language": "English"
  }
}
"""

OUTPUT_COT_SYSTEM = """\
You are a biomedical evidence synthesizer.  You will receive:
1. A user's original question
2. How the question was decomposed (query analysis)
3. Retrieved evidence passages from PubMed

Reason step-by-step, then produce a final answer.

## Step 1 — Evidence Inventory
For each retrieved passage, note:
  • What claim or fact does it support?
  • Relevance to the question: HIGH / MEDIUM / LOW
  • Study type (review, trial, case report, etc.) if discernible

## Step 2 — Evidence Synthesis
Group related findings.  Identify:
  • Points of consensus across sources
  • Contradictions or conflicting evidence
  • Gaps — aspects of the question NOT covered

## Step 3 — Confidence Assessment
Rate overall confidence:
  HIGH         — multiple concordant high-quality sources
  MODERATE     — some supporting evidence but gaps exist
  LOW          — limited or conflicting evidence
  INSUFFICIENT — cannot answer from available evidence

## Step 4 — Structured Answer
Write a clear, well-organised answer.  For every factual claim, cite the \
supporting PMID(s) in brackets like [PMID 12345678].  Use short paragraphs \
or bullet points.  If the evidence is insufficient, say so honestly.
Add a disclaimer that this is not medical advice.

─────────────────────────────────────────────────
Respond ONLY with valid JSON (no markdown fences):
{
  "answer": "<string — the polished user-facing answer>",
  "confidence": "<HIGH|MODERATE|LOW|INSUFFICIENT>",
  "evidence_summary": [
    {"pmid": "<string>", "claim": "<string>", "relevance": "<HIGH|MEDIUM|LOW>"}
  ],
  "synthesis": "<string — 2-4 sentence synthesis narrative>",
  "evidence_gaps": ["<string>", ...]
}
"""


# ---------------------------------------------------------------------------
# Reasoners
# ---------------------------------------------------------------------------


class InputReasoner:
    """Decomposes a raw user question into a structured query plan."""

    def __init__(self, llm=None):
        if llm is None:
            from langchain_openai import ChatOpenAI
            self._llm = ChatOpenAI(
                model=settings.reasoning_model,
                temperature=0,
                api_key=settings.openai_api_key,
            )
        else:
            self._llm = llm

    # ---- public ----

    def reason(self, question: str) -> ReasonedQuery:
        rq = ReasonedQuery(original_question=question)
        try:
            raw = self._call_llm(question)
            data = self._parse_json(raw)
            rq.intent_analysis = data.get("intent_analysis", "")
            rq.intent_type = data.get("intent_type", "GENERAL")
            rq.identified_entities = data.get("identified_entities", [])
            rq.mesh_terms = data.get("mesh_terms", [])
            rq.query_strategy = data.get("query_strategy", "")
            rq.pubmed_query = data.get("pubmed_query", "")
            rq.embedding_queries = data.get("embedding_queries", [question])
            rq.search_filters = data.get("search_filters", {})
        except Exception:
            log.exception("InputReasoner failed — falling back to raw question")
            rq.embedding_queries = [question]
            rq.pubmed_query = question
        return rq

    # ---- private ----

    def _call_llm(self, question: str) -> str:
        out = self._llm.invoke(
            [
                {"role": "system", "content": INPUT_COT_SYSTEM},
                {"role": "user", "content": question},
            ]
        )
        return out.content if hasattr(out, "content") else str(out)

    @staticmethod
    def _parse_json(text: str) -> dict:
        """Robustly parse JSON from LLM output, stripping markdown fences."""
        cleaned = text.strip()
        if cleaned.startswith("```"):
            # Strip ```json ... ``` wrappers
            lines = cleaned.split("\n")
            lines = lines[1:]  # drop opening fence
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            cleaned = "\n".join(lines)
        return json.loads(cleaned)


class OutputReasoner:
    """Synthesises retrieved evidence into a reasoned answer with trace."""

    def __init__(self, llm=None):
        if llm is None:
            from langchain_openai import ChatOpenAI
            self._llm = ChatOpenAI(
                model=settings.reasoning_model,
                temperature=0,
                api_key=settings.openai_api_key,
            )
        else:
            self._llm = llm

    def synthesize(
        self,
        question: str,
        query_analysis: ReasonedQuery,
        context_blocks: list[str],
    ) -> ReasonedAnswer:
        ra = ReasonedAnswer()
        try:
            raw = self._call_llm(question, query_analysis, context_blocks)
            data = InputReasoner._parse_json(raw)
            ra.answer = data.get("answer", "")
            ra.confidence = data.get("confidence", "MODERATE")
            ra.evidence_summary = data.get("evidence_summary", [])
            ra.synthesis = data.get("synthesis", "")
            ra.evidence_gaps = data.get("evidence_gaps", [])
        except Exception:
            log.exception("OutputReasoner failed — falling back to basic answer")
            ra.answer = self._fallback(question, context_blocks)
        return ra

    # ---- private helpers ----

    def _call_llm(
        self,
        question: str,
        query_analysis: ReasonedQuery,
        context_blocks: list[str],
    ) -> str:
        context = "\n\n---\n\n".join(context_blocks)
        analysis_summary = (
            f"Intent: {query_analysis.intent_type}\n"
            f"Entities: {', '.join(query_analysis.identified_entities)}\n"
            f"MeSH terms: {', '.join(query_analysis.mesh_terms)}\n"
            f"Strategy: {query_analysis.query_strategy}"
        )
        user_msg = (
            f"## Original Question\n{question}\n\n"
            f"## Query Analysis\n{analysis_summary}\n\n"
            f"## Retrieved Evidence\n{context}"
        )
        out = self._llm.invoke(
            [
                {"role": "system", "content": OUTPUT_COT_SYSTEM},
                {"role": "user", "content": user_msg},
            ]
        )
        return out.content if hasattr(out, "content") else str(out)

    def _fallback(self, question: str, context_blocks: list[str]) -> str:
        """Simple fallback if structured synthesis fails."""
        context = "\n\n---\n\n".join(context_blocks)
        out = self._llm.invoke(
            [
                {
                    "role": "system",
                    "content": (
                        "You are a biomedical literature assistant. Answer using ONLY "
                        "the context excerpts below. If the context does not support "
                        "an answer, say so. Cite PMIDs. This is not medical advice."
                    ),
                },
                {"role": "user", "content": f"Context:\n{context}\n\nQuestion: {question}"},
            ]
        )
        return out.content if hasattr(out, "content") else str(out)
