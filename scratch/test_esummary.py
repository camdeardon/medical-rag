import requests
import json
pmid = "29432135"
url = f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi?db=pubmed&id={pmid}&retmode=json"
r = requests.get(url)
data = r.json()
print(json.dumps(data, indent=2))
