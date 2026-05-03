# Medical RAG

Evidence-based biomedical Q&A powered by PubMed, Pinecone vector search, and chain-of-thought reasoning.

Ask a question in plain English — the system decomposes it into a structured PubMed search strategy, retrieves and reranks evidence, then synthesises a cited answer with a transparent reasoning trace.

---

## Architecture

```
User Question
     │
     ▼
┌─────────────────────────────────────────┐
│          Input Chain of Thought         │
│  Intent analysis → Entity extraction    │
│  MeSH term mapping → Query construction │
│  Embedding query reformulation          │
└─────────────┬───────────────────────────┘
              │
     ┌────────┴────────┐
     ▼                 ▼
 Pinecone          PubMed Live
 Vector Search     E-utilities Search
     │                 │
     └────────┬────────┘
              ▼
     Cohere Reranker (v3.5)
              │
              ▼
┌─────────────────────────────────────────┐
│         Output Chain of Thought         │
│  Evidence inventory → Synthesis         │
│  Confidence assessment → Gap analysis   │
└─────────────┬───────────────────────────┘
              │
              ▼
     Cited Answer + Reasoning Trace
```

### What makes it different

Most RAG systems embed the user's raw question and do a single vector search. This system adds **two reasoning layers**:

1. **Input CoT** — Before any retrieval, an LLM analyses the question to determine intent (overview vs. mechanism vs. safety, etc.), extracts biomedical entities, maps them to formal MeSH descriptors, and constructs an optimised Boolean PubMed query with field tags and publication-type filters. A vague question like *"tell me about immunology"* becomes a targeted search for recent review articles using proper controlled vocabulary.

2. **Output CoT** — After retrieval and reranking, a second reasoning step inventories each piece of evidence, synthesises findings, identifies contradictions and gaps, and assigns a confidence level. The full trace is returned to the user for transparency.

---

## Project Structure

```
medical-rag/
├── app/
│   ├── main.py            # FastAPI application + API routes
│   ├── config.py           # Pydantic settings (env vars, feature flags)
│   ├── rag.py              # Core pipeline: reason → retrieve → rerank → synthesise
│   ├── reasoning.py        # InputReasoner + OutputReasoner (CoT prompts)
│   ├── reranker.py         # Cohere cross-encoder reranking
│   ├── pubmed_live.py      # Real-time PubMed search at query time
│   └── mesh_lookup.py      # MeSH term validation via NCBI E-utilities
├── static/
│   ├── index.html          # Single-page frontend
│   ├── app.js              # UI logic + CoT rendering
│   └── styles.css          # Styling (vanilla CSS)
├── pubmed_ingest.py        # Batch ingestion: PubMed → chunk → embed → Pinecone
├── pinecone_inspect.py     # Utility to inspect Pinecone index contents
├── requirements.txt
├── .env                    # API keys (not committed)
└── .gitignore
```

---

## Prerequisites

- **Python 3.11+**
- A **Pinecone** account and index (serverless or pod-based)
- An **OpenAI** API key
- A **Cohere** API key (for reranking — optional but recommended)

---

## Setup

### 1. Clone and create a virtual environment

```bash
git clone <your-repo-url> medical-rag
cd medical-rag
python -m venv .venv
source .venv/bin/activate
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure environment variables

Create a `.env` file in the project root:

```env
OPENAI_API_KEY=sk-...
PINECONE_API_KEY=pcsk_...
PINECONE_INDEX=medical-rag
COHERE_API_KEY=...           # optional — enables reranking

# Optional NCBI API key for higher rate limits
NCBI_API_KEY=...
NCBI_EMAIL=your@email.com
```

### 4. Create a Pinecone index

If you haven't already, create an index in the [Pinecone console](https://app.pinecone.io/) or via the API:

- **Name**: `medical-rag` (or whatever you set in `PINECONE_INDEX`)
- **Dimensions**: `1536` (matches `text-embedding-ada-002`)
- **Metric**: `cosine`

### 5. Ingest PubMed data

Populate your index with articles:

```bash
python pubmed_ingest.py
```

By default, this searches PubMed for the query defined at the bottom of the script, fetches abstracts (and optionally PMC full-text for open-access articles), chunks them, embeds with OpenAI, and upserts into Pinecone.

To customise the search:

```python
# pubmed_ingest.py — edit the __main__ block
if __name__ == "__main__":
    ingest_pubmed_query("your search query here", max_results=200)
```

### 6. Run the app

```bash
python -m uvicorn app.main:app --port 8000
```

Then open [http://localhost:8000](http://localhost:8000) in your browser.

> **Note:** The first startup may take 30–60 seconds due to the OpenAI SDK import. Subsequent starts are near-instant thanks to Python's module cache.

---

## Usage

1. Type a question in the search box — anything from broad (*"what is immunology"*) to specific (*"does metformin reduce cancer mortality"*)
2. Click **Ask** or press **⌘+Enter**
3. The system will:
   - Decompose your question (visible in the 🧠 **Query Analysis** panel)
   - Search the vector index + live PubMed
   - Rerank results with Cohere
   - Synthesise an answer with citations
4. Expand the collapsible panels to inspect:
   - **Query Analysis** — intent, entities, MeSH terms, constructed PubMed query
   - **Evidence Reasoning** — synthesis narrative, evidence inventory table, confidence, gaps

---

## API

### `GET /api/health`

Returns `{"status": "ok"}`.

### `GET /api/stats`

Returns the Pinecone index name and vector count.

### `POST /api/query`

Submit a question and receive a reasoned, cited answer.

**Request:**

```json
{
  "question": "How do checkpoint inhibitors work in cancer?",
  "k": 6
}
```

**Response:**

```json
{
  "answer": "Checkpoint inhibitors work by blocking...",
  "sources": [
    {
      "pmid": "12345678",
      "title": "...",
      "journal": "...",
      "year": "2024",
      "url": "https://pubmed.ncbi.nlm.nih.gov/12345678/",
      "excerpt": "..."
    }
  ],
  "query_analysis": {
    "intent_type": "MECHANISM",
    "intent_analysis": "The user is asking about the mechanism of action...",
    "identified_entities": ["Checkpoint Inhibitors", "Neoplasms"],
    "mesh_terms": ["Immune Checkpoint Inhibitors", "Immunotherapy"],
    "pubmed_query": "\"Immune Checkpoint Inhibitors\"[mh] AND ...",
    "embedding_queries": ["mechanism of action checkpoint inhibitors..."],
    "query_strategy": "...",
    "search_filters": {}
  },
  "reasoning_trace": {
    "confidence": "HIGH",
    "synthesis": "Multiple concordant sources describe...",
    "evidence_summary": [
      {"pmid": "12345678", "claim": "...", "relevance": "HIGH"}
    ],
    "evidence_gaps": ["No evidence on long-term outcomes beyond 5 years"]
  }
}
```

---

## Configuration

All settings are configured via environment variables (loaded from `.env`):

| Variable | Default | Description |
|---|---|---|
| `OPENAI_API_KEY` | — | **Required.** OpenAI API key for embeddings and chat. |
| `PINECONE_API_KEY` | — | **Required.** Pinecone API key. |
| `PINECONE_INDEX` | `medical-rag` | Name of the Pinecone index. |
| `COHERE_API_KEY` | — | Cohere key for reranking. Reranking is skipped if unset. |
| `EMBEDDING_MODEL` | `text-embedding-ada-002` | OpenAI embedding model. |
| `CHAT_MODEL` | `gpt-4o-mini` | OpenAI model for basic chat (fallback). |
| `REASONING_MODEL` | `gpt-4o` | Stronger model used for CoT reasoning. |
| `ENABLE_LIVE_SEARCH` | `true` | Toggle real-time PubMed search at query time. |
| `ENABLE_RERANKING` | `true` | Toggle Cohere cross-encoder reranking. |
| `ENABLE_REASONING_TRACE` | `true` | Include CoT traces in API responses. |

---

## How the Ingestion Pipeline Works

`pubmed_ingest.py` runs a four-stage pipeline:

1. **Search** — Queries PubMed E-utilities (`esearch.fcgi`) to get a list of PMIDs
2. **Fetch** — Retrieves article metadata, structured abstracts, MeSH terms, and optionally full text from PubMed Central for open-access papers
3. **Chunk** — Splits documents using `RecursiveCharacterTextSplitter` (1200 chars, 120 overlap)
4. **Embed & Store** — Embeds chunks with `text-embedding-ada-002` and upserts into Pinecone

Each document includes rich metadata: PMID, title, journal, year, DOI, MeSH terms, and PMC full-text availability.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | FastAPI + Uvicorn |
| LLM | OpenAI GPT-4o (reasoning), GPT-4o-mini (fallback) |
| Embeddings | OpenAI `text-embedding-ada-002` |
| Vector Store | Pinecone |
| Reranker | Cohere Rerank v3.5 |
| Literature Source | PubMed / PubMed Central (NCBI E-utilities) |
| Frontend | Vanilla HTML/CSS/JS |
| Config | Pydantic Settings + python-dotenv |

---

## Disclaimer

This tool is for **research and educational purposes only**. It is not medical advice. Always consult a qualified healthcare professional for personal care decisions. Answers are generated from retrieved PubMed excerpts and may be incomplete or incorrect.
