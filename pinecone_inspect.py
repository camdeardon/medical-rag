#!/usr/bin/env python3
"""Print Pinecone index stats and run a sample similarity search (what's stored)."""
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from langchain_openai import OpenAIEmbeddings
from langchain_pinecone import PineconeVectorStore
from pinecone import Pinecone

load_dotenv(Path(__file__).resolve().parent / ".env")

INDEX_NAME = os.environ.get("PINECONE_INDEX_NAME", "medical-rag")
EMBEDDING_MODEL = "text-embedding-ada-002"


def main() -> None:
    if not os.environ.get("PINECONE_API_KEY"):
        sys.exit("Set PINECONE_API_KEY in .env")
    if not os.environ.get("OPENAI_API_KEY"):
        sys.exit("Set OPENAI_API_KEY in .env (same model as ingest)")

    pc = Pinecone(api_key=os.environ["PINECONE_API_KEY"])
    index = pc.Index(INDEX_NAME)
    stats = index.describe_index_stats()
    print(f"Index: {INDEX_NAME}\n{stats}\n")

    embeddings = OpenAIEmbeddings(model=EMBEDDING_MODEL)
    store = PineconeVectorStore.from_existing_index(
        index_name=INDEX_NAME,
        embedding=embeddings,
    )

    query = " ".join(sys.argv[1:]) or "immunotherapy cancer treatment"
    docs = store.similarity_search(query, k=5)
    print(f"Top {len(docs)} chunks for query: {query!r}\n")
    for i, doc in enumerate(docs, 1):
        meta = doc.metadata
        title = meta.get("title", "")[:80]
        pmid = meta.get("pmid", "")
        print(f"--- {i} pmid={pmid} title={title!r}")
        print(doc.page_content[:500] + ("…" if len(doc.page_content) > 500 else ""))
        print()


if __name__ == "__main__":
    main()
