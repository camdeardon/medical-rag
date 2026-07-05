import requests
import xml.etree.ElementTree as ET
pmid = "29432135"
url = f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/elink.fcgi?dbfrom=pubmed&linkname=pubmed_pubmed_citedin&id={pmid}"
r = requests.get(url)
root = ET.fromstring(r.content)
links = root.findall(".//Link/Id")
print(f"Citations for {pmid}: {len(links)}")
