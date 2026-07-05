import requests
pmid = "29432135"
url = f"https://api.semanticscholar.org/graph/v1/paper/PMID:{pmid}?fields=citationCount"
r = requests.get(url)
print(r.json())
