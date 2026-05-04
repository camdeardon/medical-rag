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
    $("auth-email").focus();
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
            renderUser();
            hideAuth();
        } else {
            showAuth();
        }
    } catch {
        showAuth();
    }
}

function renderUser() {
    if (_user) {
        $("user-email").textContent = _user.email;
        $("user-profile").classList.remove("hidden");
    } else {
        $("user-profile").classList.add("hidden");
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
    const activeSub = document.querySelector(".sub-tab-btn.active")?.dataset.subtab;
    if (activeSub === "subscribe") {
      loadIngestTasks();
      loadSubscriptions();
    } else if (activeSub === "discover") {
      renderRecentSearches();
    }
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
    container.innerHTML = subs.reverse().map(s => `
      <div class="sub-card ${s.is_active ? 'sub-active' : ''}">
        <div class="sub-card-header">
          <div class="sub-card-left">
            <span class="sub-query" title="${escapeHtml(s.query)}">${escapeHtml(s.query)}</span>
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
    `).join("");
  } catch (e) { console.error(e); }
}

async function addSubscription() {
  const q = $("sub-query-input").value.trim();
  const max = parseInt($("sub-max-results").value, 10) || 100;
  if (q.length < 2) return;

  const btn = $("sub-add-btn");
  btn.disabled = true;
  btn.textContent = "Subscribing...";

  try {
    const r = await apiFetch("/api/subscriptions", {
      method: "POST",
      body: JSON.stringify({ query: q, max_results: max }),
    });
    if (r.ok) {
      $("sub-query-input").value = "";
      loadSubscriptions();
    }
  } catch (e) { console.error(e); }
  finally {
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
