const API_BASE = (window.TBMC_CONFIG && window.TBMC_CONFIG.API_BASE) || "http://127.0.0.1:8000";
const SEARCH_TIMEOUT_MS = 90000;
const SUBMIT_TIMEOUT_MS = 300000;

let kybSessionId = null;
let kybPublicFacts = null;
let checklistTemplate = [];
let checklistAnimationToken = 0;
let searchDebounceTimer = null;
let crossCheckDebounceTimer = null;
let searchRequestId = 0;
const owners = [];
const controlPersons = [];
const pendingDocs = [];

function showSection(id) {
  document.querySelectorAll(".section").forEach((s) => s.classList.remove("active"));
  document.getElementById(id).classList.add("active");
  document.body.classList.remove("landing-active");
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function showLanding() {
  document.querySelectorAll(".section").forEach((s) => s.classList.remove("active"));
  document.getElementById("landing").classList.add("active");
  document.body.classList.add("landing-active");
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function setWizardStep(n) {
  document.querySelectorAll(".wizard-panel").forEach((p) => p.classList.remove("active"));
  document.getElementById(`kyb-step-${n}`).classList.add("active");
  document.querySelectorAll(".wizard-step-dot").forEach((d) => {
    const step = parseInt(d.dataset.step, 10);
    d.classList.remove("active", "done");
    if (step === n) d.classList.add("active");
    else if (step < n) d.classList.add("done");
  });
}

function escapeHtml(text) {
  return String(text)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function defaultDocLabel(filename) {
  return filename.replace(/\.[^.]+$/, "").replace(/[-_]+/g, " ").trim();
}

function addSelectedDocs() {
  const fileInput = document.getElementById("enterprise-doc-file");
  const files = Array.from(fileInput.files || []);
  if (!files.length) {
    alert("Select one or more files.");
    return;
  }
  for (const file of files) {
    pendingDocs.push({ label: defaultDocLabel(file.name), file });
  }
  fileInput.value = "";
  renderDocList();
}

function getUserInputs() {
  return {
    legal_name: document.getElementById("kyb_legal_name").value.trim(),
    state: document.getElementById("kyb_state").value.trim().toUpperCase(),
    ein: document.getElementById("kyb_ein").value.trim(),
    operating_address: document.getElementById("kyb_address").value.trim(),
    business_purpose: document.getElementById("kyb_purpose").value.trim(),
  };
}

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

function checklistStatusLabel(result) {
  const map = {
    PENDING: "—",
    CHECKING: "…",
    PASS: "Confirmed",
    FLAG: "Review",
    BLOCK: "Blocked",
    SKIP: "Skipped",
  };
  return map[result] || result;
}

function checklistStateClass(result) {
  return {
    PENDING: "pending",
    CHECKING: "checking",
    PASS: "pass",
    FLAG: "flag",
    BLOCK: "block",
    SKIP: "skip",
  }[result] || "pending";
}

function renderVerifyChecklist(items) {
  const list = document.getElementById("verify-checklist");
  if (!list) return;
  list.innerHTML = items
    .map((item) => {
      const state = item.state || item.result || "PENDING";
      const cls = checklistStateClass(state);
      return `<li class="verify-item ${cls}" data-num="${item.num}">
        <span class="verify-num">${item.num}</span>
        <div class="verify-copy">
          <span class="verify-name">${escapeHtml(item.item)}</span>
          <span class="verify-useful">${escapeHtml(item.useful_for || "")}</span>
        </div>
        <span class="verify-badge">${checklistStatusLabel(state)}</span>
      </li>`;
    })
    .join("");
}

function setVerifyItemState(num, result, detail, recommendation) {
  const row = document.querySelector(`.verify-item[data-num="${num}"]`);
  if (!row) return;
  row.className = `verify-item ${checklistStateClass(result)}`;
  row.querySelector(".verify-badge").textContent = checklistStatusLabel(result);
  if (detail && result !== "CHECKING" && result !== "PENDING") {
    row.title = recommendation ? `${detail}\n\n→ ${recommendation}` : detail;
  }
}

function setVerifySidebarStatus(text) {
  const el = document.getElementById("verify-sidebar-status");
  if (el) el.textContent = text;
}

function setAgentTraceStatus(text) {
  const el = document.getElementById("agent-trace-status");
  if (el) el.textContent = text;
}

let agentTraceActiveRow = null;

function clearAgentTrace() {
  agentTraceActiveRow = null;
  const list = document.getElementById("agent-trace-list");
  if (list) list.innerHTML = "";
  document.getElementById("public-search-skipped")?.classList.add("hidden");
  document.getElementById("kyb-public-facts").innerHTML = "";
  updatePublicRecordSummary(null);
}

function completeActiveTraceRow(doneText) {
  if (!agentTraceActiveRow) return;
  agentTraceActiveRow.classList.remove("active");
  agentTraceActiveRow.classList.add("done");
  const textEl = agentTraceActiveRow.querySelector(".agent-trace-text");
  if (doneText && textEl) textEl.textContent = doneText;
  const icon = agentTraceActiveRow.querySelector(".agent-trace-icon");
  if (icon) icon.innerHTML = '<span class="agent-check" aria-hidden="true">✓</span>';
  agentTraceActiveRow = null;
}

function startTraceRow(text) {
  completeActiveTraceRow();
  const list = document.getElementById("agent-trace-list");
  if (!list) return;
  const li = document.createElement("li");
  li.className = "agent-trace-row active";
  li.innerHTML = `<span class="agent-trace-icon"><span class="agent-spinner" aria-hidden="true"></span></span><span class="agent-trace-text">${escapeHtml(text)}</span>`;
  list.appendChild(li);
  agentTraceActiveRow = li;
  list.scrollTop = list.scrollHeight;
}

function appendAgentTraceStep(step) {
  if (!step) return;
  const { type, agent, message = "" } = step;

  if (agent === "orchestrator" && type === "think" && message.startsWith("Starting verification")) {
    startTraceRow("Starting verification…");
    return;
  }
  if (agent === "doc_extractor" && type === "act") {
    startTraceRow("Extracting documents…");
    return;
  }
  if (agent === "doc_extractor" && type === "observe" && message.includes("No documents")) {
    completeActiveTraceRow("No documents uploaded");
    return;
  }
  if (agent === "orchestrator" && type === "observe" && message.includes("Merged claims")) {
    const match = message.match(/docs:\s*(\d+)/);
    const n = match ? match[1] : "?";
    completeActiveTraceRow(`Documents extracted (${n} file${n === "1" ? "" : "s"})`);
    return;
  }
  if (agent === "research_planner" && type === "think") {
    startTraceRow("Checking public record requirements…");
    return;
  }
  if (agent === "research_planner" && type === "act") {
    if (message.includes("Skipping") || message.includes("not required")) {
      completeActiveTraceRow("Public search not required");
    } else if (message.includes("Research complete")) {
      completeActiveTraceRow("Research complete");
    } else {
      completeActiveTraceRow(message.length > 72 ? `${message.slice(0, 69)}…` : message);
    }
    return;
  }
  if (agent === "public_search" && type === "act" && message.includes("Searching")) {
    startTraceRow("Searching public records…");
    return;
  }
  if (agent === "public_search" && type === "observe") {
    completeActiveTraceRow(message.length > 72 ? `${message.slice(0, 69)}…` : message);
    return;
  }
  if (agent === "public_search" && type === "act") {
    startTraceRow(message.length > 72 ? `${message.slice(0, 69)}…` : message);
    return;
  }
  if (agent === "orchestrator" && type === "think" && message.includes("scorecard")) {
    startTraceRow("Running scorecard…");
    return;
  }
  if (type === "complete") {
    completeActiveTraceRow("Scorecard complete");
    const status = step.kyb_status || "";
    const label = status
      ? `Verification complete — ${status.replace(/_/g, " ")}`
      : "Verification complete";
    startTraceRow(label);
    completeActiveTraceRow(label);
    setAgentTraceStatus("Done");
    return;
  }
  if (type === "error") {
    completeActiveTraceRow();
    startTraceRow(message || "Verification error");
    if (agentTraceActiveRow) {
      agentTraceActiveRow.classList.add("error");
      completeActiveTraceRow(message || "Verification error");
    }
    setAgentTraceStatus("Error");
  }
}

function showPublicSearchOutcome(data) {
  const skippedEl = document.getElementById("public-search-skipped");
  const factsEl = document.getElementById("kyb-public-facts");
  const panel = document.getElementById("public-record-panel");

  if (data.search_performed && data.public_facts) {
    skippedEl?.classList.add("hidden");
    kybPublicFacts = data.public_facts;
    updatePublicRecordSummary(data.public_facts);
    if (factsEl) factsEl.innerHTML = renderPublicFacts(data.public_facts);
    panel?.setAttribute("open", "");
  } else {
    if (factsEl) factsEl.innerHTML = "";
    updatePublicRecordSummary(null);
    if (skippedEl) {
      skippedEl.textContent =
        "Public search not required — submitted documents and form data satisfied verification requirements.";
      skippedEl.classList.remove("hidden");
    }
    panel?.setAttribute("open", "");
  }
}

async function submitWithAgentStream(formData, signal) {
  const res = await fetch(`${API_BASE}/api/enterprise/kyb/${kybSessionId}/submit/stream`, {
    method: "POST",
    body: formData,
    signal,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `Submit failed (${res.status})`);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let finalResult = null;

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const parts = buffer.split("\n\n");
    buffer = parts.pop() || "";

    for (const part of parts) {
      const line = part.trim();
      if (!line.startsWith("data:")) continue;
      const payload = JSON.parse(line.slice(5).trim());
      if (payload.type === "ping") continue;
      if (payload.type === "complete") {
        finalResult = payload;
      } else if (payload.type === "error") {
        throw new Error(payload.message || "Verification error");
      } else {
        appendAgentTraceStep(payload);
        if (payload.type !== "complete" && payload.type !== "error") {
          setAgentTraceStatus("Working…");
        }
      }
    }
  }

  if (!finalResult) throw new Error("Verification ended without a result.");
  return finalResult;
}

async function revealChecklistResults(items) {
  const token = ++checklistAnimationToken;
  for (const item of items) {
    if (token !== checklistAnimationToken) return;
    setVerifyItemState(item.num, "CHECKING");
    await sleep(280);
    if (token !== checklistAnimationToken) return;
    setVerifyItemState(item.num, item.result, item.detail, item.recommendation);
    await sleep(220);
  }
}

async function loadChecklistTemplate() {
  try {
    const data = await apiJson("/api/enterprise/kyb/checklist", null, "GET", 10000);
    checklistTemplate = (data.items || []).map((i) => ({ ...i, state: "PENDING" }));
  } catch {
    checklistTemplate = [
      { num: 1, item: "Legal business name", useful_for: "Confirms identity", state: "PENDING" },
      { num: 2, item: "Formation documents", useful_for: "Proves legal existence", state: "PENDING" },
      { num: 3, item: "Proof of good standing", useful_for: "Confirms active status", state: "PENDING" },
      { num: 4, item: "OFAC sanctions screening", useful_for: "Blocks sanctioned entities", state: "PENDING" },
      { num: 5, item: "Business address", useful_for: "Confirms real location", state: "PENDING" },
      { num: 6, item: "Business purpose", useful_for: "Assesses risk profile", state: "PENDING" },
      { num: 7, item: "EIN (Tax ID)", useful_for: "Confirms tax identity", state: "PENDING" },
      { num: 8, item: "Beneficial ownership", useful_for: "Identifies real owners", state: "PENDING" },
      { num: 9, item: "Control person(s)", useful_for: "Identifies decision-makers", state: "PENDING" },
      { num: 10, item: "Government-issued ID", useful_for: "Verifies real humans", state: "PENDING" },
    ];
  }
  renderVerifyChecklist(checklistTemplate);
}

function resetChecklistPending() {
  checklistTemplate = checklistTemplate.map((i) => ({ ...i, state: "PENDING", result: "PENDING" }));
  renderVerifyChecklist(checklistTemplate);
}

function updatePublicRecordSummary(facts, searching = false) {
  const el = document.getElementById("public-record-summary-text");
  if (!el) return;
  if (searching) {
    el.textContent = "Searching…";
    return;
  }
  if (!facts) {
    el.textContent = "";
    return;
  }
  const name = facts.legal_name || "—";
  const status = facts.status || "unknown";
  const conf = Math.round((facts.confidence ?? 0) * 100);
  el.textContent = `${name} · ${status} · ${conf}%`;
}

function renderPublicFacts(facts) {
  if (!facts) return "";

  const apiErr = facts.search_error
    ? `<div class="api-error-banner">${facts.search_error}</div>`
    : "";
  const stateHint =
    facts.suggested_state && !document.getElementById("kyb_state").value.trim()
      ? `<p class="public-record-hint">State: ${facts.suggested_state}</p>`
      : facts.state_mismatch && facts.suggested_state
        ? `<p class="public-record-hint">Try state ${facts.suggested_state}</p>`
        : "";

  const rows = [
    ["Name", facts.legal_name],
    ["Status", facts.status],
    ["Type", facts.entity_type],
    ["State", facts.incorporation_state],
    ["Agent", facts.registered_agent_address],
    ["Purpose", facts.naics_or_purpose],
  ]
    .filter(([, v]) => v)
    .map(([k, v]) => `<div class="public-fact-row"><span>${k}</span><span>${escapeHtml(String(v))}</span></div>`)
    .join("");

  const sources = (facts.source_urls || []).slice(0, 3);
  const sourceLinks = sources.length
    ? `<div class="public-record-sources">${sources.map((u) => `<a href="${u}" target="_blank" rel="noopener">Source</a>`).join(" · ")}</div>`
    : "";

  return `
    ${apiErr}
    ${stateHint}
    <div class="public-facts-compact">${rows || "<span class='public-record-hint'>No details found</span>"}</div>
    ${sourceLinks}
  `;
}

function crossCheckLabel(key) {
  return { address: "Operating address", purpose: "Business purpose" }[key] || key;
}

function crossCheckBadge(result) {
  return {
    PASS: "Confirmed",
    FLAG: "Review",
    BLOCK: "Blocked",
    SKIP: "—",
  }[result] || result;
}

function crossCheckNote(key, result, detail) {
  const d = (detail || "").toLowerCase();
  if (result === "SKIP") {
    if (d.includes("operating address") || d.includes("add your operating")) return "Enter address above";
    if (d.includes("business purpose") || d.includes("add your business")) return "Enter purpose above";
    if (d.includes("waiting on public")) return "Waiting on public record";
    return detail;
  }
  if (result === "PASS" && key === "address") {
    if (d.includes("different street") || d.includes("both in")) return "Same state as registered agent";
    if (d.includes("matches address")) return "Matches public filing";
  }
  if (result === "PASS" && key === "purpose") {
    if (d.includes("overlap")) return "Aligns with NAICS on file";
  }
  if (result === "FLAG" && key === "address" && d.includes("registered agent")) {
    const m = detail.match(/in (\w{2}); registered agent on file is in (\w{2})/i);
    if (m) return `${m[1]} vs ${m[2]} on SOS filing`;
  }
  if (detail && detail.length > 72) {
    return detail.replace(/ — typical for corporations\.?$/i, "").replace(/Different street from registered agent, both in /i, "Same state · ");
  }
  return detail;
}

function crossCheckSummaryLine(checks) {
  const values = Object.values(checks || {});
  if (!values.length) return "";
  const confirmed = values.filter((c) => c.result === "PASS").length;
  const review = values.filter((c) => c.result === "FLAG" || c.result === "BLOCK").length;
  if (confirmed && !review) return `${confirmed} confirmed`;
  if (review && !confirmed) return `${review} need review`;
  if (confirmed && review) return `${confirmed} confirmed · ${review} review`;
  return "Add fields above to compare";
}

function renderCrossChecks(checks) {
  if (!checks || !Object.keys(checks).length) return "";

  const summary = crossCheckSummaryLine(checks);
  const rows = Object.entries(checks)
    .map(([key, c]) => {
      const label = crossCheckLabel(key);
      const state = (c.result || "SKIP").toLowerCase();
      const note = escapeHtml(crossCheckNote(key, c.result, c.detail));
      return `<li class="cross-check-item ${state}">
        <span class="cross-check-field">${escapeHtml(label)}</span>
        <span class="cross-check-badge">${crossCheckBadge(c.result)}</span>
        <span class="cross-check-note">${note}</span>
      </li>`;
    })
    .join("");

  const hasOutcome = Object.values(checks).some((c) => ["PASS", "FLAG", "BLOCK"].includes(c.result));

  return `<details class="cross-check-panel"${hasOutcome ? " open" : ""}>
    <summary class="cross-check-summary">
      <span class="cross-check-label">Public record match</span>
      <span class="cross-check-one-liner">${escapeHtml(summary)}</span>
    </summary>
    <ul class="cross-check-list">${rows}</ul>
  </details>`;
}

function renderScorecard(data) {
  const sc = data.scorecard;
  const flagged = (sc.items || []).filter((i) => i.result === "FLAG");

  const resultLabel = (result) => {
    if (result === "SKIP") return "Skipped";
    if (result === "PASS") return "Confirmed";
    if (result === "FLAG") return "Review";
    if (result === "BLOCK") return "Blocked";
    return result;
  };

  const resultClass = (result) =>
    ({
      PASS: "pass",
      FLAG: "flag",
      BLOCK: "block",
      SKIP: "skip",
    }[result] || "skip");

  const rows = sc.items
    .map((i) => {
      return `<tr>
        <td>${i.num}</td>
        <td>${escapeHtml(i.item)}</td>
        <td><span class="scorecard-result ${resultClass(i.result)}">${resultLabel(i.result)}</span></td>
        <td>${escapeHtml(i.detail)}</td>
      </tr>`;
    })
    .join("");

  const statusWord =
    sc.kyb_status === "passed" ? "Passed" : sc.kyb_status === "blocked" ? "Blocked" : "Review";

  const recommendationsHtml = flagged.length
    ? `<details class="scorecard-actions" open>
        <summary class="scorecard-actions-summary">
          <span>Action needed</span>
          <span class="scorecard-actions-count">${flagged.length} item${flagged.length === 1 ? "" : "s"}</span>
        </summary>
        <ul class="scorecard-actions-list">${flagged
          .map(
            (i) =>
              `<li><span class="scorecard-actions-item">${escapeHtml(i.item)}</span>${escapeHtml(i.recommendation || i.detail)}</li>`
          )
          .join("")}</ul>
      </details>`
    : "";

  return `
    <div class="scorecard-header">
      <h3 class="scorecard-title">${escapeHtml(statusWord)}</h3>
      <p class="scorecard-meta">${sc.flags_count} flag${sc.flags_count === 1 ? "" : "s"} · ${sc.blocks_count} block${sc.blocks_count === 1 ? "" : "s"}</p>
    </div>
    ${recommendationsHtml}
    <table class="scorecard-table">
      <thead><tr><th>#</th><th>Item</th><th>Result</th><th>Detail</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>
  `;
}

function renderOwnerRows() {
  document.getElementById("owners-list").innerHTML = owners
    .map(
      (o, i) => `
    <div class="owner-row">
      <input type="text" placeholder="Owner name" value="${o.name}" data-i="${i}" class="owner-name" />
      <input type="number" placeholder="%" value="${o.ownership_pct}" data-i="${i}" class="owner-pct" min="0" max="100" />
      <button type="button" class="btn btn-secondary remove-owner" data-i="${i}">Remove</button>
    </div>`
    )
    .join("");
}

function renderPersonRows() {
  document.getElementById("persons-list").innerHTML = controlPersons
    .map(
      (p, i) => `
    <div class="owner-row">
      <input type="text" placeholder="Name" value="${p.name}" data-i="${i}" class="person-name" />
      <input type="text" placeholder="Title" value="${p.title}" data-i="${i}" class="person-title" />
      <button type="button" class="btn btn-secondary remove-person" data-i="${i}">Remove</button>
    </div>`
    )
    .join("");
}

function renderDocList() {
  document.getElementById("enterprise-doc-list").innerHTML = pendingDocs
    .map(
      (d, i) => `
    <li class="doc-item">
      <div class="doc-item-fields">
        <input type="text" value="${escapeHtml(d.label)}" data-i="${i}" class="doc-label" placeholder="Document name" />
        <div class="doc-name">${escapeHtml(d.file.name)}</div>
      </div>
      <button type="button" data-i="${i}" class="remove-doc">Remove</button>
    </li>`
    )
    .join("");
}

async function apiJson(path, body, method = "POST", timeoutMs = 60000) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(`${API_BASE}${path}`, {
      method,
      headers: body && method !== "GET" ? { "Content-Type": "application/json" } : undefined,
      body: body && method !== "GET" ? JSON.stringify(body) : undefined,
      signal: controller.signal,
    });
    const data = await res.json();
    if (!res.ok) {
      const err = new Error(data.detail || JSON.stringify(data));
      err.status = res.status;
      throw err;
    }
    return data;
  } catch (err) {
    if (err.name === "AbortError") {
      throw new Error("Request timed out — the server may still be processing. Try again in a moment.");
    }
    throw err;
  } finally {
    clearTimeout(timer);
  }
}

async function ensureSession() {
  if (kybSessionId) {
    try {
      const res = await fetch(`${API_BASE}/api/enterprise/kyb/${kybSessionId}`, { method: "GET" });
      if (res.ok) return;
    } catch (_) {
      /* fall through to new session */
    }
  }
  const data = await apiJson("/api/enterprise/kyb/session", {}, "POST", 10000);
  kybSessionId = data.session_id;
}

function setLoadingMessage(text) {
  const el = document.getElementById("kyb-submit-loading");
  if (el) el.textContent = text;
}

function setSearchBadge(_text, searching = false) {
  const badge = document.getElementById("public-record-badge");
  if (!badge) return;
  badge.classList.add("hidden");
  if (searching) updatePublicRecordSummary(null, true);
}

async function runCrossCheckOnly() {
  if (!kybSessionId || !kybPublicFacts) return;
  const { operating_address, business_purpose } = getUserInputs();
  if (!operating_address && !business_purpose) {
    document.getElementById("kyb-cross-check-preview").innerHTML = "";
    return;
  }
  try {
    const data = await apiJson(
      `/api/enterprise/kyb/${kybSessionId}/cross-check`,
      { operating_address, business_purpose },
      "POST",
      10000
    );
    document.getElementById("kyb-cross-check-preview").innerHTML = renderCrossChecks(data.cross_checks);
  } catch (_) {
    /* cross-check preview is optional */
  }
}

async function runDebouncedSearch() {
  if (!kybSessionId) return;

  const { legal_name, state, operating_address, business_purpose } = getUserInputs();
  if (legal_name.length < 3) {
    setSearchBadge("");
    updatePublicRecordSummary(null);
    document.getElementById("kyb-public-facts").innerHTML = "";
    document.getElementById("kyb-cross-check-preview").innerHTML = "";
    document.getElementById("public-record-panel").open = false;
    return;
  }

  const reqId = ++searchRequestId;
  const loading = document.getElementById("kyb-search-loading");
  loading.classList.remove("hidden");
  updatePublicRecordSummary(null, true);
  setSearchBadge("…", true);

  try {
    const data = await apiJson(`/api/enterprise/kyb/${kybSessionId}/search`, {
      legal_name,
      state,
      operating_address,
      business_purpose,
    }, "POST", SEARCH_TIMEOUT_MS);
    if (reqId !== searchRequestId) return;

    kybPublicFacts = data.public_facts;
    if (data.public_facts?.suggested_state && !document.getElementById("kyb_state").value.trim()) {
      document.getElementById("kyb_state").value = data.public_facts.suggested_state;
    }
    updatePublicRecordSummary(data.public_facts);
    document.getElementById("kyb-public-facts").innerHTML = renderPublicFacts(data.public_facts);
    document.getElementById("kyb-cross-check-preview").innerHTML = renderCrossChecks(data.cross_checks);
    setSearchBadge("");
  } catch (err) {
    if (reqId !== searchRequestId) return;
    if (err.status === 404) {
      try {
        await ensureSession();
        return runDebouncedSearch();
      } catch (_) {
        /* show error below */
      }
    }
    document.getElementById("kyb-public-facts").innerHTML = `<div class="api-error-banner">${err.message}</div>`;
    setSearchBadge("Error");
  } finally {
    if (reqId === searchRequestId) loading.classList.add("hidden");
  }
}

function scheduleSearch() {
  clearTimeout(searchDebounceTimer);
  searchDebounceTimer = setTimeout(runDebouncedSearch, 1500);
}

function scheduleCrossCheck() {
  clearTimeout(crossCheckDebounceTimer);
  crossCheckDebounceTimer = setTimeout(runCrossCheckOnly, 600);
}

async function initKybSession() {
  kybSessionId = null;
  kybPublicFacts = null;
  searchRequestId = 0;
  owners.length = 0;
  controlPersons.length = 0;
  pendingDocs.length = 0;

  document.getElementById("kyb_legal_name").value = "";
  document.getElementById("kyb_state").value = "";
  document.getElementById("kyb_ein").value = "";
  document.getElementById("kyb_address").value = "";
  document.getElementById("kyb_purpose").value = "";
  document.getElementById("kyb-public-facts").innerHTML = "";
  updatePublicRecordSummary(null);
  clearAgentTrace();
  setAgentTraceStatus("Ready");
  document.getElementById("agent-trace-loading")?.classList.add("hidden");
  document.getElementById("public-record-panel")?.removeAttribute("open");
  document.getElementById("agent-trace-panel")?.setAttribute("open", "");
  document.getElementById("kyb-cross-check-preview").innerHTML = "";
  renderOwnerRows();
  renderPersonRows();
  renderDocList();
  document.getElementById("enterprise-doc-file").value = "";

  setWizardStep(1);
  setVerifySidebarStatus("Ready");
  document.getElementById("verify-panel")?.removeAttribute("open");

  await loadChecklistTemplate();
  const data = await apiJson("/api/enterprise/kyb/session", {}, "POST", 10000);
  kybSessionId = data.session_id;
}

document.addEventListener("DOMContentLoaded", () => {
  document.getElementById("go-enterprise").addEventListener("click", async () => {
    showSection("enterprise");
    try {
      await initKybSession();
    } catch (err) {
      alert(`Could not start session: ${err.message}`);
    }
  });


  document.querySelectorAll(".back-link").forEach((btn) => btn.addEventListener("click", showLanding));

  document.getElementById("kyb-step2-back").addEventListener("click", () => {
    setWizardStep(1);
  });

  document.querySelectorAll(".wizard-step-dot").forEach((dot) => {
    dot.addEventListener("click", () => {
      const target = parseInt(dot.dataset.step, 10);
      const current = document.querySelector(".wizard-step-dot.active");
      const currentStep = current ? parseInt(current.dataset.step, 10) : 1;
      if (target >= currentStep) return;
      setWizardStep(target);
    });
  });

  document.getElementById("add-owner-btn").addEventListener("click", () => {
    owners.push({ name: "", ownership_pct: 25 });
    renderOwnerRows();
  });
  document.getElementById("add-person-btn").addEventListener("click", () => {
    controlPersons.push({ name: "", title: "CEO" });
    renderPersonRows();
  });

  document.getElementById("owners-list").addEventListener("input", (e) => {
    const i = parseInt(e.target.dataset.i, 10);
    if (e.target.classList.contains("owner-name")) owners[i].name = e.target.value;
    if (e.target.classList.contains("owner-pct")) owners[i].ownership_pct = parseFloat(e.target.value) || 0;
  });
  document.getElementById("persons-list").addEventListener("input", (e) => {
    const i = parseInt(e.target.dataset.i, 10);
    if (e.target.classList.contains("person-name")) controlPersons[i].name = e.target.value;
    if (e.target.classList.contains("person-title")) controlPersons[i].title = e.target.value;
  });
  document.getElementById("owners-list").addEventListener("click", (e) => {
    if (e.target.classList.contains("remove-owner")) {
      owners.splice(parseInt(e.target.dataset.i, 10), 1);
      renderOwnerRows();
    }
  });
  document.getElementById("persons-list").addEventListener("click", (e) => {
    if (e.target.classList.contains("remove-person")) {
      controlPersons.splice(parseInt(e.target.dataset.i, 10), 1);
      renderPersonRows();
    }
  });

  document.getElementById("enterprise-add-doc").addEventListener("click", addSelectedDocs);
  document.getElementById("enterprise-doc-file").addEventListener("change", () => {
    if (document.getElementById("enterprise-doc-file").files.length) {
      addSelectedDocs();
    }
  });
  document.getElementById("enterprise-doc-list").addEventListener("input", (e) => {
    if (e.target.classList.contains("doc-label")) {
      const i = parseInt(e.target.dataset.i, 10);
      pendingDocs[i].label = e.target.value;
    }
  });
  document.getElementById("enterprise-doc-list").addEventListener("click", (e) => {
    if (e.target.classList.contains("remove-doc")) {
      pendingDocs.splice(parseInt(e.target.dataset.i, 10), 1);
      renderDocList();
    }
  });

  document.getElementById("kyb-submit-btn").addEventListener("click", async () => {
    const submitBtn = document.getElementById("kyb-submit-btn");
    const loading = document.getElementById("kyb-submit-loading");
    const inputs = getUserInputs();

    submitBtn.disabled = true;
    loading.classList.remove("hidden");
    setLoadingMessage("Running agent verification…");
    resetChecklistPending();
    setVerifySidebarStatus("Verifying…");
    setAgentTraceStatus("Working…");
    clearAgentTrace();
    document.getElementById("verify-panel")?.setAttribute("open", "");
    document.getElementById("agent-trace-panel")?.setAttribute("open", "");

    try {
      await ensureSession();

      const fd = new FormData();
      fd.append("legal_name", inputs.legal_name);
      fd.append("state", inputs.state);
      fd.append("ein", inputs.ein);
      fd.append("operating_address", inputs.operating_address);
      fd.append("business_purpose", inputs.business_purpose);
      fd.append("beneficial_owners", JSON.stringify(owners.filter((o) => o.name)));
      fd.append("control_persons", JSON.stringify(controlPersons.filter((p) => p.name)));
      pendingDocs.forEach((d) => {
        fd.append("documents", d.file, d.file.name);
        fd.append("document_labels", d.label.trim() || defaultDocLabel(d.file.name));
      });

      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), SUBMIT_TIMEOUT_MS);
      let data;
      try {
        data = await submitWithAgentStream(fd, controller.signal);
      } catch (err) {
        if (err.name === "AbortError") {
          throw new Error("Verification timed out. Try with fewer documents or check the backend terminal.");
        }
        throw err;
      } finally {
        clearTimeout(timer);
      }

      setAgentTraceStatus("Done");
      showPublicSearchOutcome(data);

      await revealChecklistResults(data.scorecard.items);
      const status = data.scorecard.kyb_status;
      setVerifySidebarStatus(status === "passed" ? "All confirmed" : status === "blocked" ? "Blocked" : "Review needed");

      document.getElementById("kyb-scorecard").innerHTML = renderScorecard(data);
      await sleep(400);
      setWizardStep(2);
    } catch (err) {
      appendAgentTraceStep({ type: "error", agent: "orchestrator", message: err.message });
      setAgentTraceStatus("Error");
      alert(`Verification failed: ${err.message}`);
      setVerifySidebarStatus("Error");
    } finally {
      submitBtn.disabled = false;
      loading.classList.add("hidden");
      setLoadingMessage("Extracting documents and running cross-check…");
    }
  });

  document.getElementById("kyb-download-md").addEventListener("click", async () => {
    if (!kybSessionId) return;
    const res = await fetch(`${API_BASE}/api/enterprise/kyb/${kybSessionId}/record`);
    const data = await res.json();
    const pre = document.getElementById("kyb-md-preview");
    pre.textContent = data.markdown || "(empty)";
    pre.classList.remove("hidden");
  });

  document.getElementById("issuer-form")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const form = e.target;
    const fd = new FormData();
    fd.append("issuer_name", form.issuer_name.value.trim());
    fd.append("stablecoin_ticker", form.stablecoin_ticker.value.trim());
    const res = await fetch(`${API_BASE}/api/issuer/submit`, { method: "POST", body: fd });
    const data = await res.json();
    document.getElementById("issuer-result").classList.add("visible", "success");
    document.getElementById("issuer-result").innerHTML = `<pre>${JSON.stringify(data, null, 2)}</pre>`;
  });
});
