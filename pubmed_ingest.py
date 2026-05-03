# pubmed_ingest.py
import os
import requests
import time
from pathlib import Path
from xml.etree import ElementTree as ET

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")
# Deferring heavy langchain imports to local functions

BASE_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
EMAIL = "camadamdeardon+pubmed@gmail.com"  # NCBI requires this for rate limiting courtesy
API_KEY = None  # optional — get one free at ncbi.nlm.nih.gov/account

# Set to "1" / "true" to also pull PMC full text when the paper is open access (slower, more API calls).
INCLUDE_PMC_FULLTEXT = os.environ.get("PUBMED_INCLUDE_PMC_FULLTEXT", "1").lower() in (
    "1",
    "true",
    "yes",
)


def _local_tag(elem: ET.Element) -> str:
    t = elem.tag
    return t.rsplit("}", 1)[-1] if "}" in t else t


def _norm_pmc_id(raw: str) -> str:
    s = raw.strip()
    if s.upper().startswith("PMC"):
        s = s[3:]
    return s


def _parse_structured_abstract(abstract_parent: ET.Element | None) -> tuple[str, dict[str, str]]:
    """Return (flat abstract, sections dict) from Abstract/AbstractText nodes."""
    if abstract_parent is None:
        return "", {}
    sections_dict: dict[str, str] = {}
    parts: list[str] = []

    for ab in abstract_parent:
        if _local_tag(ab) != "AbstractText":
            continue
        label = (ab.get("Label") or ab.get("NlmCategory") or "").strip() or "Abstract"
        chunk = "".join(ab.itertext()).strip()
        if not chunk:
            continue
        key = label.upper() if label else "ABSTRACT"
        if key in sections_dict:
            sections_dict[key] = sections_dict[key] + " " + chunk
        else:
            sections_dict[key] = chunk
        parts.append(f"{label}: {chunk}" if label != "Abstract" else chunk)

    flat = "\n\n".join(parts) if parts else ""
    return flat, sections_dict


def _mesh_terms(article: ET.Element) -> list[str]:
    out: list[str] = []
    for mh in article.findall(".//MeshHeading"):
        desc = mh.find("DescriptorName")
        if desc is not None and (desc.text or "").strip():
            q = mh.find("QualifierName")
            if q is not None and (q.text or "").strip():
                out.append(f"{desc.text} / {q.text}")
            else:
                out.append(desc.text or "")
    return out


def _keywords(article: ET.Element) -> list[str]:
    out: list[str] = []
    for kw in article.findall(".//Keyword"):
        if kw.text and kw.text.strip():
            out.append(kw.text.strip())
    return out


def _doi(article: ET.Element) -> str:
    for aid in article.findall(".//ArticleId"):
        if aid.get("IdType") == "doi" and aid.text:
            return aid.text.strip()
    for eloc in article.findall(".//ELocationID"):
        if eloc.get("EIdType") == "doi" and eloc.text:
            return eloc.text.strip()
    return ""


def _sec_to_text(sec: ET.Element, depth: int = 0) -> str:
    """Flatten JATS sec (section) into markdown-style headings + paragraphs."""
    lines: list[str] = []
    prefix = "#" * min(2 + depth, 4) + " "
    title_el = None
    for child in sec:
        if _local_tag(child) == "title":
            title_el = child
            break
    if title_el is not None:
        t = "".join(title_el.itertext()).strip()
        if t:
            lines.append(f"\n{prefix}{t}\n")

    for child in sec:
        tag = _local_tag(child)
        if tag == "p":
            t = "".join(child.itertext()).strip()
            if t:
                lines.append(t)
        elif tag == "sec":
            lines.append(_sec_to_text(child, depth + 1))
    return "\n".join(lines).strip()


def _pmc_body_to_text(root: ET.Element) -> str:
    body = None
    for el in root.iter():
        if _local_tag(el) == "body":
            body = el
            break
    if body is None:
        return ""

    chunks: list[str] = []
    for sec in body:
        if _local_tag(sec) == "sec":
            chunks.append(_sec_to_text(sec, 0))
        elif _local_tag(sec) == "p":
            t = "".join(sec.itertext()).strip()
            if t:
                chunks.append(t)
    return "\n\n".join(c for c in chunks if c).strip()


def _elink_pubmed_to_pmc(pmids: list[str]) -> dict[str, str]:
    """Map PMID -> PMC ID (numeric string) for articles in PubMed Central."""
    if not pmids:
        return {}
    params = {
        "dbfrom": "pubmed",
        "db": "pmc",
        "id": ",".join(pmids),
        "retmode": "xml",
        "email": EMAIL,
    }
    if API_KEY:
        params["api_key"] = API_KEY
    resp = requests.get(f"{BASE_URL}/elink.fcgi", params=params)
    resp.raise_for_status()
    tree = ET.fromstring(resp.content)
    pmid_to_pmc: dict[str, str] = {}
    for linkset in tree.findall(".//LinkSet"):
        id_el = linkset.find("IdList/Id")
        if id_el is None or not id_el.text:
            continue
        pmid = id_el.text.strip()
        for linksetdb in linkset.findall("LinkSetDb"):
            if (linksetdb.findtext("DbTo") or "").strip() != "pmc":
                continue
            for link in linksetdb.findall("Link"):
                cid = link.find("Id")
                if cid is not None and cid.text:
                    pmid_to_pmc[pmid] = _norm_pmc_id(cid.text)
                    break
            break
    return pmid_to_pmc


def _fetch_pmc_fulltext_batch(pmc_ids: list[str]) -> dict[str, str]:
    """PMC ID -> plain text from article body (open-access XML only)."""
    if not pmc_ids:
        return {}
    params = {
        "db": "pmc",
        "id": ",".join(pmc_ids),
        "retmode": "xml",
        "email": EMAIL,
    }
    if API_KEY:
        params["api_key"] = API_KEY
    resp = requests.get(f"{BASE_URL}/efetch.fcgi", params=params)
    resp.raise_for_status()
    out: dict[str, str] = {}
    root = ET.fromstring(resp.content)
    for article in root.iter():
        if _local_tag(article) != "article":
            continue
        pmcid_el = None
        for el in article.iter():
            if _local_tag(el) == "article-id" and el.get("pub-id-type") == "pmcid":
                pmcid_el = el
                break
        if pmcid_el is None or not (pmcid_el.text or "").strip():
            for el in article.iter():
                if _local_tag(el) == "article-id" and el.get("pub-id-type") == "pmcaid":
                    pmcid_el = el
                    break
        pmc_key = _norm_pmc_id((pmcid_el.text or "").strip()) if pmcid_el is not None else ""
        text = _pmc_body_to_text(article)
        if pmc_key and text:
            out[pmc_key] = text
    return out


def search_pubmed(query: str, max_results: int = 50) -> list[str]:
    """Step 1: search by keyword, get back a list of PMIDs."""
    params = {
        "db": "pubmed",
        "term": query,
        "retmax": max_results,
        "retmode": "json",
        "email": EMAIL,
        "sort": "relevance",
    }
    if API_KEY:
        params["api_key"] = API_KEY

    resp = requests.get(f"{BASE_URL}/esearch.fcgi", params=params)
    resp.raise_for_status()
    ids = resp.json()["esearchresult"]["idlist"]
    print(f"Found {len(ids)} articles for: '{query}'")
    return ids


def fetch_abstracts(
    pmids: list[str],
    *,
    include_pmc_fulltext: bool | None = None,
    max_fulltext_chars: int = 120_000,
):
    """Fetch PubMed records; optionally merge PMC full text for OA articles."""
    try:
        from langchain_core.documents import Document
    except ImportError:
        from langchain.schema import Document
    include_pmc = INCLUDE_PMC_FULLTEXT if include_pmc_fulltext is None else include_pmc_fulltext
    docs: list[Document] = []

    pmc_map: dict[str, str] = {}
    if include_pmc and pmids:
        pmc_map = _elink_pubmed_to_pmc(pmids)
        time.sleep(0.12)
        if pmc_map:
            unique_pmc = list({v for v in pmc_map.values()})
            pmc_text_by_id: dict[str, str] = {}
            for j in range(0, len(unique_pmc), 20):
                batch = unique_pmc[j : j + 20]
                pmc_text_by_id.update(_fetch_pmc_fulltext_batch(batch))
                time.sleep(0.15)
        else:
            pmc_text_by_id = {}
    else:
        pmc_text_by_id = {}

    for i in range(0, len(pmids), 20):
        batch = pmids[i : i + 20]
        params = {
            "db": "pubmed",
            "id": ",".join(batch),
            "rettype": "abstract",
            "retmode": "xml",
            "email": EMAIL,
        }
        if API_KEY:
            params["api_key"] = API_KEY

        resp = requests.get(f"{BASE_URL}/efetch.fcgi", params=params)
        resp.raise_for_status()

        root = ET.fromstring(resp.content)

        for article in root.findall(".//PubmedArticle"):
            pmid = article.findtext(".//PMID", "")
            title = (article.findtext(".//ArticleTitle") or "").strip()
            abstract_parent = None
            for abs_el in article.findall(".//Abstract"):
                abstract_parent = abs_el
                break

            flat_abstract, abstract_sections = _parse_structured_abstract(abstract_parent)
            if not flat_abstract and not abstract_sections:
                continue

            journal = article.findtext(".//Journal/Title", "")
            year = article.findtext(".//PubDate/Year", "")
            mesh = _mesh_terms(article)
            kws = _keywords(article)
            doi = _doi(article)

            section_lines = []
            for k, v in sorted(abstract_sections.items()):
                section_lines.append(f"## {k}\n{v}")
            abstract_block = (
                "\n\n".join(section_lines) if section_lines else flat_abstract
            )

            pmc_id = pmc_map.get(pmid, "")
            fulltext = ""
            if include_pmc and pmc_id and pmc_id in pmc_text_by_id:
                fulltext = pmc_text_by_id[pmc_id]
                if len(fulltext) > max_fulltext_chars:
                    fulltext = (
                        fulltext[:max_fulltext_chars]
                        + "\n\n[...truncated for embedding size...]"
                    )

            header = [f"Title: {title}"]
            if doi:
                header.append(f"DOI: {doi}")
            if mesh:
                header.append("MeSH: " + "; ".join(mesh[:40]))
            if kws:
                header.append("Keywords: " + "; ".join(kws[:30]))

            blocks = ["\n".join(header), f"\n## Abstract\n{abstract_block}"]
            if fulltext:
                blocks.append(f"\n## Full text (PMC open access)\n{fulltext}")

            page_content = "\n".join(blocks)

            meta_mesh = "; ".join(mesh[:25]) if mesh else ""
            docs.append(
                Document(
                    page_content=page_content,
                    metadata={
                        "pmid": pmid,
                        "title": title,
                        "journal": journal,
                        "year": year,
                        "doi": doi,
                        "mesh_terms": meta_mesh[:8000],
                        "pmcid": f"PMC{pmc_id}" if pmc_id else "",
                        "has_pmc_fulltext": bool(fulltext),
                        "source": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
                        "type": "pubmed",
                    },
                )
            )

        time.sleep(0.15)
        print(f"  Fetched batch {i//20 + 1} ({len(docs)} articles so far)")

    return docs


def ingest_pubmed_query(query: str, max_results: int = 100, progress_cb=None):
    """Full pipeline: search → fetch → chunk → embed → store."""
    if progress_cb: progress_cb("Searching PubMed...")
    pmids = search_pubmed(query, max_results)
    if not pmids:
        return 0
    return ingest_pmids(pmids, progress_cb=progress_cb)


def ingest_pmids(pmids: list[str], progress_cb=None):
    """Fetch abstracts for list of PMIDs, chunk, embed, and store in Pinecone."""
    if not pmids:
        return 0
    
    if progress_cb: progress_cb(f"Fetching {len(pmids)} abstracts...")
    docs = fetch_abstracts(pmids)
    print(f"Got {len(docs)} articles with content")
    if not docs:
        return 0

    if progress_cb: progress_cb(f"Chunking {len(docs)} articles...")

    try:
        from langchain_text_splitters import RecursiveCharacterTextSplitter
    except ImportError:
        from langchain.text_splitter import RecursiveCharacterTextSplitter
        
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=1200,
        chunk_overlap=120,
    )
    chunks = splitter.split_documents(docs)

    if not os.environ.get("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY is not set")
    if not os.environ.get("PINECONE_API_KEY"):
        raise RuntimeError("PINECONE_API_KEY is not set")

    from langchain_openai import OpenAIEmbeddings
    from langchain_pinecone import PineconeVectorStore
    from app.config import settings
    
    if progress_cb: progress_cb(f"Embedding {len(chunks)} chunks & storing in Pinecone...")
    embeddings = OpenAIEmbeddings(model=settings.embedding_model)
    PineconeVectorStore.from_documents(chunks, embeddings, index_name=settings.pinecone_index)
    
    msg = f"Ingested {len(chunks)} chunks from {len(docs)} PubMed articles"
    print(msg)
    if progress_cb: progress_cb(msg)
    
    return len(docs)


if __name__ == "__main__":
    ingest_pubmed_query("advancements in kerataconus treatment or management", max_results=1000)
