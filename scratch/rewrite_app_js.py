import os

js_path = 'static/app.js'
with open(js_path, 'r') as f:
    content = f.read()

# Replace addSubscription function
start = content.find("async function addSubscription() {")
end = content.find("async function runSub(id) {")

new_func = """async function addSubscription() {
  const q = $("sub-query-input").value.trim();
  const max = parseInt($("sub-max-results").value, 10) || 100;
  
  const articleType = $("sub-type") ? $("sub-type").value : "All";
  const journals = $("sub-journals") ? $("sub-journals").value.trim() : "";
  const sortBy = $("sub-sort") ? $("sub-sort").value : "relevance";

  if (q.length < 2) return;

  const btn = $("sub-add-btn");
  btn.disabled = true;
  btn.textContent = "Subscribing...";

  try {
    const payload = {
      query: q,
      max_results: max,
      article_type: articleType === "All" ? null : articleType,
      journals: journals ? journals : null,
      sort_by: sortBy
    };
    
    const r = await apiFetch("/api/subscriptions", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    
    if (r.ok) {
      $("sub-query-input").value = "";
      if ($("sub-journals")) $("sub-journals").value = "";
      loadSubscriptions();
    } else {
      const err = await r.json();
      alert("Error: " + (err.detail || "Failed to add subscription"));
    }
  } catch (e) { 
      console.error(e); 
      alert("Network error");
  } finally {
    btn.disabled = false;
    btn.textContent = "Subscribe";
  }
}

"""

content = content[:start] + new_func + content[end:]

with open(js_path, 'w') as f:
    f.write(content)
print("Updated app.js")
