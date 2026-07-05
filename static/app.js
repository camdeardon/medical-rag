const $ = (id) => document.getElementById(id);

// ── Auth Logic ───────────────────────────────────────────────────────────────
let _token = localStorage.getItem("medical_rag_token");
let _user = null;
let _isSignup = false;

async function apiFetch(url, options = {}) {
  const headers = {
    ...options.headers,
    "Authorization": `Bearer ${_token}`,
  };
  
  if (options.body && !(options.body instanceof FormData)) {
    headers["Content-Type"] = "application/json";
  }

  const r = await fetch(url, { ...options, headers });
  
  if (r.status === 401) {
    showAuth();
    throw new Error("Unauthorized");
  }
  
  return r;
}

function showAuth() {
    $("auth-overlay").classList.add("active");
}

function hideAuth() {
    $("auth-overlay").classList.remove("active");
}

async function checkAuth() {
    // Check URL for token (from Google SSO redirect)
    const urlParams = new URLSearchParams(window.location.search);
    const tokenFromUrl = urlParams.get("token");
    if (tokenFromUrl) {
        _token = tokenFromUrl;
        localStorage.setItem("medical_rag_token", _token);
        // Clean up URL
        window.history.replaceState({}, document.title, window.location.pathname);
    }

    if (!_token) {
        showAuth();
        return;
    }
    try {
        const r = await apiFetch("/api/auth/me");
        if (r.ok) {
            _user = await r.json();
            if (!_user.is_approved) {
                showPendingApproval();
                return;
            }
            renderUser();
            hideAuth();
        } else {
            showAuth();
        }
    } catch {
        showAuth();
    }
}

function showPendingApproval() {
    $("auth-overlay").classList.add("active");
    $("auth-title").textContent = "Approval Required";
    $("auth-sub").textContent = "Your account is pending admin approval. Please check back later or contact an admin.";
    $("auth-form").innerHTML = "";
    $("auth-err").classList.add("hidden");
}

function renderUser() {
    if (_user) {
        $("user-email").textContent = _user.email;
        $("user-profile").classList.remove("hidden");
        if (_user.is_admin) {
            $("tab-admin").classList.remove("hidden");
        } else {
            $("tab-admin").classList.add("hidden");
        }
    } else {
        $("user-profile").classList.add("hidden");
        $("tab-admin").classList.add("hidden");
    }
}

$("logout-btn")?.addEventListener("click", () => {
    _token = null;
    _user = null;
    localStorage.removeItem("medical_rag_token");
    location.reload();
});

// ── UI Helpers ───────────────────────────────────────────────────────────────
function autoResize(el) {
  el.style.height = "auto";
  el.style.height = el.scrollHeight + "px";
}

$("q")?.addEventListener("input", () => autoResize($("q")));
$("discover-q")?.addEventListener("input", () => autoResize($("discover-q")));

function escapeHtml(s) {
  const d = document.createElement("div");
  d.textContent = s;
  return d.innerHTML;
}

const INTENT_COLOURS = {
  OVERVIEW: "#6366f1", MECHANISM: "#8b5cf6", COMPARISON: "#ec4899",
  EVIDENCE: "#0891b2", SPECIFIC: "#059669", SAFETY: "#dc2626", GENERAL: "#71717a",
};

const CONFIDENCE_STYLES = {
  HIGH: { bg: "#dcfce7", color: "#166534", label: "HIGH CONFIDENCE" },
  MODERATE: { bg: "#fef9c3", color: "#854d0e", label: "MODERATE CONFIDENCE" },
  LOW: { bg: "#fee2e2", color: "#991b1b", label: "LOW CONFIDENCE" },
  INSUFFICIENT: { bg: "#f3f4f6", color: "#6b7280", label: "INSUFFICIENT EVIDENCE" },
};

// ── Tab Navigation ───────────────────────────────────────────────────────────
const tabBtns = document.querySelectorAll(".tab-nav .tab-btn");
const tabPanels = document.querySelectorAll(".main > .tab-panel");
const subTabBtns = document.querySelectorAll(".sub-tab-btn");
const subTabPanels = document.querySelectorAll(".sub-tab-panel");

function switchTab(target) {
  tabBtns.forEach((b) => {
    const isActive = b.dataset.tab === target;
    b.classList.toggle("active", isActive);
    b.setAttribute("aria-selected", isActive);
  });
  tabPanels.forEach((p) => {
    p.classList.toggle("active", p.id === `panel-${target}`);
  });

  if (target === "knowledge") {
    const activeSub = document.querySelector("#panel-knowledge .sub-tab-btn.active")?.dataset.subtab;
    if (activeSub === "subscribe") {
      loadIngestTasks();
      loadSubscriptions();
    } else if (activeSub === "discover") {
      renderRecentSearches();
    }
  } else if (target === "admin") {
    const activeSub = document.querySelector("#panel-admin .sub-tab-btn.active")?.dataset.subtab;
    if (activeSub === "stats") loadAdminStats();
    else if (activeSub === "users") loadAdminUsers();
    else if (activeSub === "evaluations") loadAdminEvals();
  }
}

tabBtns.forEach((btn) => {
  btn.addEventListener("click", () => switchTab(btn.dataset.tab));
});

subTabBtns.forEach((btn) => {
  btn.addEventListener("click", () => {
    const target = btn.dataset.subtab;
    subTabBtns.forEach((b) => {
      const isActive = b.dataset.subtab === target;
      b.classList.toggle("active", isActive);
      b.setAttribute("aria-selected", isActive);
    });
    subTabPanels.forEach((p) => {
      p.classList.toggle("active", p.id === `subpanel-${target}`);
    });

    if (target === "subscribe") {
      loadIngestTasks();
      loadSubscriptions();
    } else if (target === "discover") {
      renderRecentSearches();
    } else if (target === "stats") {
      loadAdminStats();
    } else if (target === "users") {
      loadAdminUsers();
    } else if (target === "evaluations") {
      loadAdminEvals();
    }
  });
});

async function loadStats() {
  const el = $("stats");
  try {
    const r = await fetch("/api/stats");
    if (!r.ok) throw new Error();
    const data = await r.json();
    const total = data.stats?.total_vector_count ?? data.stats?.namespaces?.[""]?.vector_count ?? "—";
    el.textContent = `${data.index} · ${Number(total).toLocaleString()} vectors`;
  } catch {
    el.textContent = "index unavailable";
  }
}

// ── Phase 1 & 2: Discovery ───────────────────────────────────────────────────
let _discoveredPmids = [];

async function discover() {
  const q = $("discover-q").value.trim();
  const k = parseInt($("discover-k").value, 10) || 20;
  const btn = $("discover-btn");

  if (q.length < 2) return showError("discover-err", "Please enter a topic.");

  btn.disabled = true;
  $("discover-btn-text").textContent = "Thinking...";
  $("discover-spinner").classList.remove("hidden");
  $("discover-loading").classList.remove("hidden");
  $("discover-results").classList.add("hidden");
  $("discover-err").classList.add("hidden");

  try {
    const r = await apiFetch("/api/discover", {
      method: "POST",
      body: JSON.stringify({ topic: q, max_results: k }),
    });
    const data = await r.json();
    if (!r.ok) throw new Error(data.detail || "Discovery failed");

    renderDiscoveryResults(data);
    saveRecentSearch(q);
  } catch (e) {
    showError("discover-err", e.message);
  } finally {
    btn.disabled = false;
    $("discover-btn-text").textContent = "Discover";
    $("discover-spinner").classList.add("hidden");
    $("discover-loading").classList.add("hidden");
  }
}

function renderDiscoveryResults(data) {
  const qa = data.query_analysis;
  const articles = data.articles;
  _discoveredPmids = articles.map(a => a.pmid);

  // Render Reasoning
  const intentType = qa.intent_type || "GENERAL";
  const intentColor = INTENT_COLOURS[intentType] || INTENT_COLOURS.GENERAL;
  
  $("discover-analysis-content").innerHTML = `
    <div class="cot-field">
      <span class="cot-field-label">Intent Analysis <span class="intent-badge" style="background:${intentColor}18; color:${intentColor}">${intentType}</span></span>
      <p class="cot-field-value">${escapeHtml(qa.intent_analysis)}</p>
    </div>
    <div class="cot-field">
      <span class="cot-field-label">Search Strategy</span>
      <p class="cot-field-value">${escapeHtml(qa.query_strategy)}</p>
    </div>
    <div class="cot-field">
      <span class="cot-field-label">Optimized PubMed Query</span>
      <pre class="pubmed-query-code">${escapeHtml(qa.pubmed_query)}</pre>
    </div>
  `;

  // Render Articles
  $("discover-count").textContent = articles.length;
  $("discover-list").innerHTML = articles.map((a, i) => `
    <li class="source-card">
      <span class="source-num">${i + 1}</span>
      <div class="source-body">
        <a class="title" href="https://pubmed.ncbi.nlm.nih.gov/${a.pmid}/" target="_blank">${escapeHtml(a.title)}</a>
        <div class="source-meta">PMID ${a.pmid} · ${escapeHtml(a.journal)} · ${a.year}</div>
      </div>
    </li>
  `).join("");

  $("discover-results").classList.remove("hidden");
}

async function ingestDiscovered() {
  if (_discoveredPmids.length === 0) return;
  
  const btn = $("ingest-all-btn");
  btn.disabled = true;
  btn.textContent = "Starting Ingestion...";

  try {
    const r = await apiFetch("/api/ingest", {
      method: "POST",
      body: JSON.stringify({ pmids: _discoveredPmids }),
    });
    if (!r.ok) throw new Error("Failed to start ingestion");
    
    // Switch to Knowledge -> Subscribe sub-tab to show progress
    document.querySelector('[data-tab="knowledge"]').click();
    document.querySelector('[data-subtab="subscribe"]').click();
  } catch (e) {
    showError("discover-err", e.message);
    btn.disabled = false;
    btn.textContent = "Save All to Library";
  }
}

// ── Phase 4: Chat (RAG) ──────────────────────────────────────────────────────
async function ask() {
  const q = $("q").value.trim();
  const k = parseInt($("k").value, 10) || 6;
  const btn = $("btn");

  if (q.length < 2) return showError("err", "Please enter a question.");

  btn.disabled = true;
  $("btn-text").textContent = "Thinking...";
  $("btn-spinner").classList.remove("hidden");
  $("loading").classList.remove("hidden");
  $("results").classList.add("hidden");
  $("err").classList.add("hidden");

  try {
    const r = await apiFetch("/api/query", {
      method: "POST",
      body: JSON.stringify({ question: q, k }),
    });
    const data = await r.json();
    if (!r.ok) throw new Error(data.detail || "Query failed");

    renderChatResults(data);
  } catch (e) {
    showError("err", e.message);
  } finally {
    btn.disabled = false;
    $("btn-text").textContent = "Ask";
    $("btn-spinner").classList.add("hidden");
    $("loading").classList.add("hidden");
  }
}

function renderChatResults(data) {
  $("answer").textContent = data.answer;
  
  const trace = data.reasoning_trace;
  if (trace) {
    const style = CONFIDENCE_STYLES[trace.confidence] || CONFIDENCE_STYLES.MODERATE;
    const cb = $("confidence-badge");
    cb.textContent = style.label;
    cb.style.background = style.bg;
    cb.style.color = style.color;
    cb.classList.remove("hidden");

    const analysis = data.query_analysis;
    let analysisHtml = "";
    if (analysis) {
      const intentType = analysis.intent_type || "GENERAL";
      const intentColor = INTENT_COLOURS[intentType] || INTENT_COLOURS.GENERAL;
      analysisHtml = `
        <div class="cot-field">
          <span class="cot-field-label">Search Strategy <span class="intent-badge" style="background:${intentColor}18; color:${intentColor}">${intentType}</span></span>
          <p class="cot-field-value">${escapeHtml(analysis.query_strategy)}</p>
        </div>
        <div class="cot-field">
          <span class="cot-field-label">Optimized PubMed Query</span>
          <pre class="pubmed-query-code">${escapeHtml(analysis.pubmed_query)}</pre>
        </div>
      `;
    }

    $("reasoning-trace-content").innerHTML = `
      ${analysisHtml}
      <div class="cot-field">
        <span class="cot-field-label">Synthesis</span>
        <p class="cot-field-value">${escapeHtml(trace.synthesis)}</p>
      </div>
      <div class="cot-field">
        <span class="cot-field-label">Gaps</span>
        <ul class="gaps-list">${(trace.evidence_gaps || []).map(g => `<li>${escapeHtml(g)}</li>`).join("")}</ul>
      </div>
    `;
  }

  $("sources").innerHTML = (data.sources || []).map((s, i) => `
    <li class="source-card">
      <span class="source-num">${i + 1}</span>
      <div class="source-body">
        <a class="title" href="${s.url}" target="_blank">${escapeHtml(s.title)}</a>
        <div class="source-meta">PMID ${s.pmid} · ${escapeHtml(s.journal)} · ${s.year}</div>
        <p class="source-excerpt">${escapeHtml(s.excerpt)}</p>
      </div>
    </li>
  `).join("");

  $("results").classList.remove("hidden");
}

// ── Library / Subscriptions / Tasks ──────────────────────────────────────────
async function loadIngestTasks() {
  try {
    const r = await apiFetch("/api/ingest/tasks");
    const tasks = await r.json();
    const container = $("ingest-tasks-list");
    if (!container) return;
    
    if (tasks.length === 0) {
      if ($("ingest-status-section")) $("ingest-status-section").classList.add("hidden");
      return;
    }
    
    if ($("ingest-status-section")) $("ingest-status-section").classList.remove("hidden");
    container.innerHTML = tasks.reverse().map(t => `
      <div class="ingest-task-card">
        <div class="task-header">
          <span class="task-query">${escapeHtml(t.query)}</span>
          <span class="task-status-pill status-${t.status}">${t.status}</span>
        </div>
        <div class="task-progress">${escapeHtml(t.progress)}</div>
        <div class="task-meta">Articles: ${t.count}</div>
      </div>
    `).join("");

    if (tasks.some(t => t.status === "running")) setTimeout(loadIngestTasks, 3000);
  } catch (e) { console.error(e); }
}

async function loadSubscriptions() {
  try {
    const r = await apiFetch("/api/subscriptions");
    const subs = await r.json();
    const container = $("sub-list");
    const emptyState = $("sub-empty");
    if (!container) return;
    
    if (subs.length === 0) {
      if (emptyState) emptyState.classList.remove("hidden");
      container.innerHTML = "";
      return;
    }
    
    if (emptyState) emptyState.classList.add("hidden");
    container.innerHTML = subs.reverse().map(s => {
      const typeStr = s.article_type ? `<span style="font-size:0.8rem; background:#f1f5f9; padding:2px 6px; border-radius:4px; margin-left:8px; color:var(--text-light); border:1px solid #e2e8f0;">${escapeHtml(s.article_type)}</span>` : '';
      const journalsStr = s.journals ? `<div style="font-size:0.8rem; color:var(--text-light); margin-top:4px;">Journals: ${escapeHtml(s.journals)}</div>` : '';
      const citationsStr = s.min_citations > 0 ? `<div style="font-size:0.8rem; color:var(--text-light); margin-top:4px;">Min Citations: ${s.min_citations}+</div>` : '';
      
      return `
      <div class="sub-card ${s.is_active ? 'sub-active' : ''}">
        <div class="sub-card-header">
          <div class="sub-card-left">
            <span class="sub-query" title="${escapeHtml(s.query)}">
              <strong>${escapeHtml(s.query)}</strong>
              ${typeStr}
              ${journalsStr}
              ${citationsStr}
            </span>
            <span class="sub-status ${s.is_active ? "sub-status-active" : "sub-status-paused"}">${s.is_active ? "Active" : "Paused"}</span>
          </div>
          <div class="sub-card-actions">
             <button class="sub-action-btn sub-run-btn" onclick="runSub(${s.id})" title="Run manual search now">
               <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="5 3 19 12 5 21 5 3"></polygon></svg>
             </button>
             <button class="sub-action-btn" onclick="toggleSub(${s.id}, ${!s.is_active})" title="${s.is_active ? 'Pause collection' : 'Resume collection'}">
               ${s.is_active 
                 ? '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="6" y="4" width="4" height="16"></rect><rect x="14" y="4" width="4" height="16"></rect></svg>'
                 : '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 3l14 9-14 9V3z"/></svg>'
               }
             </button>
             <button class="sub-action-btn sub-delete-btn" onclick="deleteSub(${s.id})" title="Delete subscription">
               <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"></polyline><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path><line x1="10" y1="11" x2="10" y2="17"></line><line x1="14" y1="11" x2="14" y2="17"></line></svg>
             </button>
          </div>
        </div>
        <div class="sub-card-stats">
          <div class="sub-stat"><span class="sub-stat-label">Articles</span><span class="sub-stat-value">${s.articles_found}</span></div>
          <div class="sub-stat"><span class="sub-stat-label">Daily Max</span><span class="sub-stat-value">${s.max_results}</span></div>
          <div class="sub-stat"><span class="sub-stat-label">Runs</span><span class="sub-stat-value">${s.run_count}</span></div>
          <div class="sub-stat"><span class="sub-stat-label">Added</span><span class="sub-stat-value">${new Date(s.created_at).toLocaleDateString()}</span></div>
        </div>
      </div>
    `;}).join("");
  } catch (e) { console.error(e); }
}

async function addSubscription() {
  const q = $("sub-query-input").value.trim();
  const max = parseInt($("sub-max-results").value, 10) || 100;
  
  const articleType = $("sub-type") ? $("sub-type").value : "All";
  const journals = $("sub-journals") ? $("sub-journals").value.trim() : "";
  const sortBy = $("sub-sort") ? $("sub-sort").value : "relevance";
  const minCitations = $("sub-min-citations") ? parseInt($("sub-min-citations").value, 10) || 0 : 0;

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
      sort_by: sortBy,
      min_citations: minCitations
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

async function toggleSub(id, active) {
  try {
    await apiFetch(`/api/subscriptions/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ is_active: active }),
    });
    loadSubscriptions();
  } catch (e) { console.error(e); }
}

async function deleteSub(id) {
  if (!confirm("Delete this subscription?")) return;
  try {
    await apiFetch(`/api/subscriptions/${id}`, { method: "DELETE" });
    loadSubscriptions();
  } catch (e) { console.error(e); }
}

async function runSub(id) {
  try {
    const r = await apiFetch(`/api/subscriptions/${id}/run`, { method: "POST" });
    if (r.ok) {
       alert("Manual run started in background.");
       loadIngestTasks();
    }
  } catch (e) { console.error(e); }
}

window.toggleSub = toggleSub;
window.deleteSub = deleteSub;
window.runSub = runSub;

// ── Admin Logic ──────────────────────────────────────────────────────────────
async function loadAdminUsers() {
  try {
    const r = await apiFetch("/api/admin/users");
    if (!r.ok) return;
    const users = await r.json();
    $("admin-users-list").innerHTML = users.map(u => `
      <div style="background: #f9fafb; padding: 12px; border-radius: 8px; border: 1px solid #e5e7eb; display: flex; justify-content: space-between; align-items: center;">
        <div>
          <strong>${escapeHtml(u.email)}</strong> 
          <span style="font-size: 0.8rem; color: #6b7280; margin-left: 8px;">Joined ${new Date(u.created_at).toLocaleDateString()}</span>
        </div>
        <div style="display: flex; gap: 8px;">
          <button class="action-btn" style="padding: 4px 8px; font-size: 0.8rem;" onclick="showUserDetails(${u.id})">Details</button>
          <label style="display: flex; align-items: center; gap: 4px; font-size: 0.85rem;">
            <input type="checkbox" ${u.is_approved ? 'checked' : ''} onchange="updateUser(${u.id}, this.checked, ${u.is_admin})"> Approved
          </label>
          <label style="display: flex; align-items: center; gap: 4px; font-size: 0.85rem;">
            <input type="checkbox" ${u.is_admin ? 'checked' : ''} onchange="updateUser(${u.id}, ${u.is_approved}, this.checked)"> Admin
          </label>
        </div>
      </div>
    `).join("");
    
    // Populate user filter dropdown
    const filter = $("admin-user-filter");
    if (filter && filter.options.length <= 1) {
       users.forEach(u => {
          const opt = document.createElement("option");
          opt.value = u.id;
          opt.textContent = u.email;
          filter.appendChild(opt);
       });
    }
  } catch (e) { console.error(e); }
}

async function showUserDetails(id) {
  try {
    const r = await apiFetch(`/api/admin/users/${id}/details`);
    if (!r.ok) return;
    const data = await r.json();
    
    $("ud-title").textContent = `Details for ${data.user.email}`;
    $("ud-saved").textContent = data.saved_articles_count.toLocaleString();
    
    if (data.subscriptions.length === 0) {
      $("ud-subs").innerHTML = "<li>No subscriptions</li>";
    } else {
      $("ud-subs").innerHTML = data.subscriptions.map(s => 
        `<li><strong>${escapeHtml(s.query)}</strong> (Max: ${s.max_results}, Runs: ${s.run_count}, Found: ${s.articles_found}) - ${s.is_active ? 'Active' : 'Paused'}</li>`
      ).join("");
    }
    
    if (data.evaluations.length === 0) {
      $("ud-evals").innerHTML = "<li>No queries logged</li>";
    } else {
      $("ud-evals").innerHTML = data.evaluations.map(e => 
        `<li style="margin-bottom: 12px; padding-bottom: 8px; border-bottom: 1px solid #e5e7eb;">
           <strong>Q:</strong> ${escapeHtml(e.question)}<br>
           <span style="font-size: 0.85rem; color: #6b7280;">A: ${escapeHtml(e.answer).substring(0, 100)}...</span>
         </li>`
      ).join("");
    }
    
    $("user-detail-modal").classList.remove("hidden");
  } catch (e) { console.error(e); }
}

async function updateUser(id, is_approved, is_admin) {
  try {
    await apiFetch(`/api/admin/users/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ is_approved, is_admin }),
    });
    loadAdminUsers();
  } catch (e) { console.error(e); }
}

async function loadAdminEvals() {
  try {
    const r = await apiFetch("/api/admin/evaluations");
    if (!r.ok) return;
    const evals = await r.json();
    $("admin-evals-list").innerHTML = evals.map(e => `
      <div class="result-block" style="margin-bottom: 20px; padding: 16px; background: #fff; border: 1px solid #e5e7eb; border-radius: 8px;">
        <div style="font-size: 0.8rem; color: #6b7280; margin-bottom: 8px;">User: ${escapeHtml(e.user_email)} · ${new Date(e.created_at).toLocaleString()}</div>
        <p class="block-label">Question</p>
        <p style="margin-bottom: 12px; font-weight: 500;">${escapeHtml(e.question)}</p>
        <p class="block-label">Answer</p>
        <p style="margin-bottom: 12px; font-size: 0.95rem; line-height: 1.5;">${escapeHtml(e.answer)}</p>
        <details class="cot-details" style="margin-top: 10px;">
          <summary class="cot-summary" style="padding: 6px 12px;">
             <span class="cot-title">Reasoning Trace</span>
          </summary>
          <div class="cot-content" style="padding: 12px;">
             <pre style="white-space: pre-wrap; font-size: 0.8rem;">${escapeHtml(JSON.stringify(e.reasoning_trace, null, 2))}</pre>
          </div>
        </details>
      </div>
    `).join("");
  } catch (e) { console.error(e); }
}

let _charts = {};

async function loadAdminStats() {
  try {
    const userId = $("admin-user-filter")?.value || "";
    const url = userId ? `/api/admin/advanced-analytics?user_id=${userId}` : `/api/admin/advanced-analytics`;
    
    // Fetch system stats for top cards if system-wide
    if (!userId) {
        const sr = await apiFetch("/api/admin/system-stats");
        if (sr.ok) {
            const stats = await sr.json();
            $("admin-stats-content").innerHTML = `
              <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 16px;">
                 <div style="background: #f9fafb; padding: 20px; border-radius: 8px; border: 1px solid #e5e7eb; text-align: center;">
                    <div style="font-size: 0.9rem; color: #6b7280; font-weight: 500;">Total Users</div>
                    <div style="font-size: 2rem; color: #111827; font-weight: 700;">${stats.total_users}</div>
                 </div>
                 <div style="background: #f9fafb; padding: 20px; border-radius: 8px; border: 1px solid #e5e7eb; text-align: center;">
                    <div style="font-size: 0.9rem; color: #6b7280; font-weight: 500;">Queries Logged</div>
                    <div style="font-size: 2rem; color: #4F46E5; font-weight: 700;">${stats.total_queries}</div>
                 </div>
                 <div style="background: #f9fafb; padding: 20px; border-radius: 8px; border: 1px solid #e5e7eb; text-align: center;">
                    <div style="font-size: 0.9rem; color: #6b7280; font-weight: 500;">Active Subscriptions</div>
                    <div style="font-size: 2rem; color: #10B981; font-weight: 700;">${stats.active_subscriptions}</div>
                 </div>
              </div>
            `;
            $("admin-stats-summary").style.display = "block";
        }
    } else {
        $("admin-stats-summary").style.display = "none";
    }

    const r = await apiFetch(url);
    if (!r.ok) return;
    const data = await r.json();
    
    const destroyChart = (id) => { if (_charts[id]) _charts[id].destroy(); };
    
    // Brand Palette
    const brandIndigo = '#4F46E5';
    const brandEmerald = '#10B981';
    const indigoScale = ['#312E81', '#3730A3', '#4338CA', '#4F46E5', '#6366F1', '#818CF8', '#A5B4FC', '#C7D2FE'];
    
    // 1. Data Yield (Vertical Bar) - Top wide chart
    destroyChart('chart-yield');
    const ctxYield = document.getElementById('chart-yield').getContext('2d');
    _charts['chart-yield'] = new Chart(ctxYield, {
        type: 'bar',
        data: {
            labels: data.data_yield.map(y => y.query.substring(0, 40) + (y.query.length > 40 ? "..." : "")),
            datasets: [{ label: 'Articles Found', data: data.data_yield.map(y => y.articles), backgroundColor: brandEmerald, borderRadius: 4 }]
        },
        options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } } }
    });

    // 2. Areas of Interest (Doughnut)
    destroyChart('chart-areas');
    const ctxAreas = document.getElementById('chart-areas').getContext('2d');
    _charts['chart-areas'] = new Chart(ctxAreas, {
        type: 'doughnut',
        data: {
            labels: data.areas_of_interest.map(a => a.topic),
            datasets: [{ data: data.areas_of_interest.map(a => a.mentions), backgroundColor: indigoScale, borderWidth: 0 }]
        },
        options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { position: 'right' } } }
    });

    // 3. Recurring Queries (Horizontal Bar)
    destroyChart('chart-queries');
    const ctxQueries = document.getElementById('chart-queries').getContext('2d');
    _charts['chart-queries'] = new Chart(ctxQueries, {
        type: 'bar',
        data: {
            labels: data.recurring_queries.map(q => q.query.substring(0, 30) + (q.query.length > 30 ? "..." : "")),
            datasets: [{ label: 'Times Asked', data: data.recurring_queries.map(q => q.count), backgroundColor: brandIndigo, borderRadius: 4 }]
        },
        options: { indexAxis: 'y', responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } } }
    });

    // 4. User Leaderboard (Only if no user filter)
    if (!userId && data.user_leaderboard) {
        $("leaderboard-panel").style.display = "block";
        destroyChart('chart-users');
        const ctxUsers = document.getElementById('chart-users').getContext('2d');
        _charts['chart-users'] = new Chart(ctxUsers, {
            type: 'bar',
            data: {
                labels: data.user_leaderboard.map(u => u.email),
                datasets: [{ label: 'Activity Score', data: data.user_leaderboard.map(u => u.activity_score), backgroundColor: brandEmerald, borderRadius: 4 }]
            },
            options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } } }
        });
    } else {
        $("leaderboard-panel").style.display = "none";
    }

  } catch (e) { console.error(e); }
}

$("admin-create-user-btn")?.addEventListener("click", async () => {
  const email = $("admin-create-user-email").value.trim();
  if (!email) return;
  const btn = $("admin-create-user-btn");
  btn.disabled = true;
  btn.textContent = "Creating...";
  try {
    const r = await apiFetch("/api/admin/users", {
      method: "POST",
      body: JSON.stringify({ email, password: "temp" }),
    });
    if (!r.ok) {
      const data = await r.json();
      alert(data.detail || "Error creating user");
    } else {
      $("admin-create-user-email").value = "";
      loadAdminUsers();
    }
  } catch (e) {
    console.error(e);
  } finally {
    btn.disabled = false;
    btn.textContent = "Create & Approve User";
  }
});

window.updateUser = updateUser;

function showError(id, msg) {
  const el = $(id);
  el.textContent = msg;
  el.classList.remove("hidden");
}

// ── Init ──────────────────────────────────────────────────────────────────────
$("discover-btn").addEventListener("click", discover);
$("ingest-all-btn").addEventListener("click", ingestDiscovered);
$("btn").addEventListener("click", ask);
$("sub-add-btn").addEventListener("click", addSubscription);

// ── Recent Searches (Discovery) ──────────────────────────────────────────────
function saveRecentSearch(q) {
  let recent = JSON.parse(localStorage.getItem("recent_discoveries") || "[]");
  recent = [q, ...recent.filter(x => x !== q)].slice(0, 8);
  localStorage.setItem("recent_discoveries", JSON.stringify(recent));
  renderRecentSearches();
}

function renderRecentSearches() {
  const container = $("discover-recent-list");
  const recent = JSON.parse(localStorage.getItem("recent_discoveries") || "[]");
  if (!container) return;

  if (recent.length === 0) {
    $("discover-recent").classList.add("hidden");
    return;
  }

  $("discover-recent").classList.remove("hidden");
  container.innerHTML = recent.map(q => `
    <button class="tag discover-history-item" onclick="useRecentSearch('${escapeHtml(q)}')">
      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="margin-right:4px; opacity:0.6"><polyline points="1 4 1 10 7 10"></polyline><path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10"></path></svg>
      ${escapeHtml(q)}
    </button>
  `).join("");
}

function useRecentSearch(q) {
  $("discover-q").value = q;
  autoResize($("discover-q"));
  discover();
}

window.useRecentSearch = useRecentSearch;

// ── Keyboard Shortcuts ───────────────────────────────────────────────────────
document.addEventListener("keydown", (e) => {
  if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
    e.preventDefault();
    const activePanel = document.querySelector(".tab-panel.active");
    if (!activePanel) return;

    if (activePanel.id === "panel-search") {
      $("btn").click();
    } else if (activePanel.id === "panel-knowledge") {
      const activeSub = document.querySelector(".sub-tab-panel:not(.hidden)");
      if (activeSub && activeSub.id === "subpanel-discover") {
        $("discover-btn").click();
      } else if (activeSub && activeSub.id === "subpanel-subscribe") {
        $("sub-add-btn").click();
      }
    }
  }
});

// ── User Tour (Pendo-like Guided Onboarding) ────────────────────────────────
const Tour = {
  currentStep: 0,
  steps: [
    {
      title: "Welcome to Medical RAG",
      content: "This platform helps you query medical literature using AI with full evidence backlinking. Let's show you around.",
      target: null
    },
    {
      title: "Chat with Evidence",
      content: "Ask any medical question here. The AI will search the knowledge base and provide cited answers.",
      target: "#panel-search .search-card",
      panel: "search"
    },
    {
      title: "Knowledge Hub",
      content: "This is where you manage your data. You can discover new papers or set up automated tracking.",
      target: '.tab-btn[data-tab="knowledge"]',
      panel: "knowledge"
    },
    {
      title: "Discover Literature",
      content: "Search the global PubMed database for new topics to expand your local knowledge base.",
      target: "#subpanel-discover .search-card",
      panel: "knowledge",
      subtab: "discover"
    },
    {
      title: "Automated Collection",
      content: "Turn your searches into subscriptions. We'll automatically collect new papers for you every day.",
      target: '.sub-tab-btn[data-subtab="subscribe"]',
      panel: "knowledge",
      subtab: "subscribe"
    }
  ],

  start() {
    this.currentStep = 0;
    $("tour-overlay").classList.add("active");
    $("tour-tooltip").classList.add("active");
    this.showStep();
  },

  showStep() {
    const step = this.steps[this.currentStep];
    
    // Handle tab switching for specific steps
    if (step.panel) switchTab(step.panel);
    if (step.subtab) {
        const subBtn = document.querySelector(`.sub-tab-btn[data-subtab="${step.subtab}"]`);
        if (subBtn) subBtn.click();
    }

    $("tour-step-counter").textContent = `Step ${this.currentStep + 1} of ${this.steps.length}`;
    $("tour-title").textContent = step.title;
    $("tour-content").textContent = step.content;
    $("tour-next").textContent = this.currentStep === this.steps.length - 1 ? "Finish" : "Next";

    if (step.target) {
      const el = document.querySelector(step.target);
      if (el) {
        const rect = el.getBoundingClientRect();
        const highlight = $("tour-highlight");
        highlight.classList.remove("hidden");
        highlight.style.top = `${rect.top + window.scrollY - 8}px`;
        highlight.style.left = `${rect.left + window.scrollX - 8}px`;
        highlight.style.width = `${rect.width + 16}px`;
        highlight.style.height = `${rect.height + 16}px`;

        const tooltip = $("tour-tooltip");
        // Position tooltip below or above highlight
        if (rect.bottom + 250 > window.innerHeight) {
             tooltip.style.top = `${rect.top - 200}px`;
        } else {
             tooltip.style.top = `${rect.bottom + 24}px`;
        }
        tooltip.style.left = `${Math.max(20, Math.min(window.innerWidth - 340, rect.left))}px`;
      }
    } else {
      $("tour-highlight").classList.add("hidden");
      // Center tooltip if no target
      const tooltip = $("tour-tooltip");
      tooltip.style.top = "50%";
      tooltip.style.left = "50%";
      tooltip.style.transform = "translate(-50%, -50%)";
    }
  },

  next() {
    this.currentStep++;
    if (this.currentStep < this.steps.length) {
      this.showStep();
    } else {
      this.end();
    }
  },

  end() {
    $("tour-overlay").classList.remove("active");
    $("tour-tooltip").classList.remove("active");
    $("tour-highlight").classList.add("hidden");
    localStorage.setItem("medical_rag_tour_completed", "true");
  }
};

$("tour-next").addEventListener("click", () => Tour.next());
$("tour-skip").addEventListener("click", () => Tour.end());
$("restart-tour").addEventListener("click", () => Tour.start());

checkAuth();
loadStats();
setInterval(loadStats, 30000);
renderRecentSearches();

// Auto-start tour for new users
if (!localStorage.getItem("medical_rag_tour_completed")) {
  setTimeout(() => Tour.start(), 1500);
}
