const API_BASE = (window.TBMC_CONFIG && window.TBMC_CONFIG.API_BASE) || "http://127.0.0.1:8000";
const SUBMIT_TIMEOUT_MS = 300000;

let kybSessionId = null;
let checklistTemplate = [];
let checklistAnimationToken = 0;
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

function updateWizardDots(n) {
  document.querySelectorAll(".wizard-step-dot").forEach((d) => {
    const step = parseInt(d.dataset.step, 10);
    d.classList.remove("active", "done");
    if (step === n) d.classList.add("active");
    else if (step < n) d.classList.add("done");
  });
}

function setWizardStep(n) {
  document.querySelectorAll(".wizard-panel").forEach((p) => p.classList.remove("active", "wizard-exiting", "wizard-entering"));
  document.getElementById(`kyb-step-${n}`).classList.add("active");
  updateWizardDots(n);
}

async function transitionWizardStep(n) {
  const current = document.querySelector(".wizard-panel.active");
  const next = document.getElementById(`kyb-step-${n}`);
  if (!next || current === next) {
    setWizardStep(n);
    return;
  }
  if (current) {
    current.classList.add("wizard-exiting");
    await sleep(320);
    current.classList.remove("active", "wizard-exiting");
  }
  next.classList.add("active", "wizard-entering");
  updateWizardDots(n);
  await sleep(480);
  next.classList.remove("wizard-entering");
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

const DOC_CHECKLIST_ITEMS = [2, 7, 8, 9, 10];
const PUBLIC_CHECKLIST_ITEMS = [1, 3, 4, 5];

function markChecklistChecking(nums) {
  nums.forEach((n) => setVerifyItemState(n, "CHECKING"));
}

function markChecklistForTraceStep(step) {
  if (!step || step.type === "ping" || step.type === "checklist" || step.type === "complete") return;

  if (step.agent === "doc_extractor" && step.type === "act") {
    markChecklistChecking(DOC_CHECKLIST_ITEMS);
    return;
  }

  if (step.agent === "public_search" && (step.type === "think" || step.type === "act")) {
    markChecklistChecking(PUBLIC_CHECKLIST_ITEMS);
    return;
  }

  const label = (step.label || "").toLowerCase();
  if (step.agent === "orchestrator" && step.type === "think" && label.includes("rule")) {
    markChecklistChecking(Array.from({ length: 10 }, (_, i) => i + 1));
  }
}

function applyChecklistItemLive(item) {
  setVerifyItemState(item.num, "CHECKING");
  return sleep(160).then(() => {
    setVerifyItemState(item.num, item.result, item.detail, item.recommendation);
  });
}

function enqueueChecklistItem(item) {
  traceStepQueue = traceStepQueue
    .then(() => applyChecklistItemLive(item))
    .catch(() => {});
}

function applyScorecardFallback(items, appliedNums) {
  for (const item of items || []) {
    if (appliedNums.has(item.num)) continue;
    setVerifyItemState(item.num, item.result, item.detail, item.recommendation);
    appliedNums.add(item.num);
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
let traceStepQueue = Promise.resolve();
let traceQueueEpoch = 0;

const TRACE_PHASE_MS = { think: 420, act: 560, observe: 480, complete: 520 };
const REACT_PHASES = new Set(["think", "act", "observe"]);

function resetAgentTraceQueue() {
  traceQueueEpoch += 1;
  traceStepQueue = Promise.resolve();
}

function clearAgentTrace() {
  resetAgentTraceQueue();
  agentTraceActiveRow = null;
  const list = document.getElementById("agent-trace-list");
  if (list) list.innerHTML = "";
}

function scrollTraceList() {
  const list = document.getElementById("agent-trace-list");
  if (list) list.scrollTop = list.scrollHeight;
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

function shortWords(text, max = 4) {
  if (!text) return "…";
  const words = String(text)
    .replace(/[^\w\s]/g, " ")
    .split(/\s+/)
    .filter(Boolean);
  return words.slice(0, max).join(" ").toLowerCase() || "…";
}

function startTraceRow(label) {
  const list = document.getElementById("agent-trace-list");
  if (!list) return;
  const li = document.createElement("li");
  li.className = "agent-trace-row active";
  li.innerHTML = `<span class="agent-trace-icon"><span class="agent-spinner" aria-hidden="true"></span></span><span class="agent-trace-text">${escapeHtml(label)}</span>`;
  list.appendChild(li);
  agentTraceActiveRow = li;
  scrollTraceList();
}

function shouldSkipTraceStep(step, label) {
  if (step.agent === "orchestrator" && step.type === "think" && label.includes("docs then")) return true;
  if (step.agent === "orchestrator" && step.type === "observe" && (step.message || "").includes("Merged claims")) return true;
  return false;
}

async function playTraceStep(step) {
  if (!step || step.type === "ping") return;

  const epoch = traceQueueEpoch;
  markChecklistForTraceStep(step);
  const label = (step.label || shortWords(step.message, 4)).toLowerCase();
  const pause = (ms) => sleep(ms).then(() => epoch === traceQueueEpoch);

  if (step.type === "finished" || step.type === "complete") {
    startTraceRow((step.label || shortWords(`done ${step.kyb_status || ""}`, 4)).toLowerCase());
    if (await pause(TRACE_PHASE_MS.complete)) {
      completeActiveTraceRow();
      setAgentTraceStatus("Done");
    }
    return;
  }

  if (step.type === "error") {
    startTraceRow(shortWords(step.message, 4));
    if (await pause(TRACE_PHASE_MS.act)) {
      if (agentTraceActiveRow) agentTraceActiveRow.classList.add("error");
      completeActiveTraceRow();
      setAgentTraceStatus("Error");
    }
    return;
  }

  if (!REACT_PHASES.has(step.type) || shouldSkipTraceStep(step, label)) return;

  startTraceRow(label);
  if (step.type === "think") setAgentTraceStatus("Thinking…");
  else if (step.type === "act") setAgentTraceStatus("Acting…");
  else setAgentTraceStatus("Observing…");

  const ms = TRACE_PHASE_MS[step.type] || 420;
  if (await pause(ms)) {
    completeActiveTraceRow();
    scrollTraceList();
  }
}

function enqueueAgentTraceStep(step) {
  traceStepQueue = traceStepQueue.then(() => playTraceStep(step)).catch(() => {});
}

function waitForAgentTraceQueue() {
  return traceStepQueue;
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
  const appliedChecklist = new Set();

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

      if (payload.type === "checklist" && payload.num) {
        const item = {
          num: payload.num,
          result: payload.result,
          detail: typeof payload.detail === "string" ? payload.detail : payload.message,
          recommendation: payload.recommendation,
        };
        if (!appliedChecklist.has(item.num)) {
          appliedChecklist.add(item.num);
          enqueueChecklistItem(item);
        }
        continue;
      }

      if (payload.type === "complete" && payload.session_id) {
        finalResult = payload;
        applyScorecardFallback(payload.scorecard?.items, appliedChecklist);
        enqueueAgentTraceStep({ type: "finished", label: payload.scorecard?.kyb_status || "done" });
      } else if (payload.type === "error") {
        throw new Error(payload.message || "Verification error");
      } else {
        enqueueAgentTraceStep(payload);
      }
    }
  }

  if (!finalResult) throw new Error("Verification ended without a result.");
  return { result: finalResult, appliedChecklist };
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
    ${
      sc.kyb_status === "passed"
        ? `<div class="admission-panel">
            <p class="admission-panel-title">Admission to network</p>
            <p class="admission-panel-meta">Confidence score: 10 · All verification requirements satisfied.</p>
          </div>`
        : ""
    }
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

function updateSubmitButtonState() {
  const btn = document.getElementById("kyb-submit-btn");
  if (!btn) return;
  const hasDocs = pendingDocs.length > 0;
  btn.disabled = !hasDocs;
  btn.title = hasDocs ? "" : "Upload at least one document to run verification";
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
  updateSubmitButtonState();
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

async function loadDemoCompanyOptions() {
  const select = document.getElementById("demo-company-select");
  if (!select) return;
  try {
    const data = await apiJson("/api/enterprise/demo-companies", null, "GET", 10000);
    const companies = data.companies || [];
    select.innerHTML =
      '<option value="">Select sample package…</option>' +
      companies
        .map((c) => `<option value="${escapeHtml(c.id)}">${escapeHtml(c.label)}</option>`)
        .join("");
  } catch {
    select.innerHTML = '<option value="">Trial packages unavailable</option>';
  }
}

async function applyDemoCompany(companyId) {
  const hintEl = document.getElementById("demo-company-hint");
  if (!companyId) {
    if (hintEl) hintEl.classList.add("hidden");
    return;
  }

  try {
    const profile = await apiJson(`/api/enterprise/demo-companies/${companyId}`, null, "GET", 10000);
    const pdfRes = await fetch(`${API_BASE}/api/enterprise/demo-companies/${companyId}/document.pdf`);
    if (!pdfRes.ok) throw new Error("Could not load trial PDF");
    const blob = await pdfRes.blob();
    const filename =
      profile.document_filename ||
      pdfRes.headers.get("Content-Disposition")?.match(/filename="([^"]+)"/)?.[1] ||
      "trial_document.pdf";

    pendingDocs.length = 0;
    owners.length = 0;
    controlPersons.length = 0;

    document.getElementById("kyb_legal_name").value = profile.legal_name || "";
    document.getElementById("kyb_state").value = profile.state || "";
    document.getElementById("kyb_ein").value = profile.ein || "";
    document.getElementById("kyb_address").value = profile.operating_address || "";
    document.getElementById("kyb_purpose").value = profile.business_purpose || "";

    (profile.beneficial_owners || []).forEach((o) => owners.push({ ...o }));
    (profile.control_persons || []).forEach((p) => controlPersons.push({ ...p }));
    if (owners.length === 0) {
      owners.push({ name: "", ownership_pct: 25 });
    }
    if (controlPersons.length === 0) {
      controlPersons.push({ name: "", title: "CEO" });
    }

    pendingDocs.push({
      label: profile.document_label || defaultDocLabel(filename),
      file: new File([blob], filename, { type: "application/pdf" }),
    });

    renderOwnerRows();
    renderPersonRows();
    renderDocList();
    document.getElementById("enterprise-doc-file").value = "";

    if (hintEl) {
      hintEl.textContent = profile.hint || "";
      hintEl.classList.remove("hidden");
    }
  } catch (err) {
    alert(`Could not load trial company: ${err.message}`);
    document.getElementById("demo-company-select").value = "";
  }
}

async function initKybSession() {
  kybSessionId = null;
  owners.length = 0;
  controlPersons.length = 0;
  pendingDocs.length = 0;

  document.getElementById("kyb_legal_name").value = "";
  document.getElementById("kyb_state").value = "";
  document.getElementById("kyb_ein").value = "";
  document.getElementById("kyb_address").value = "";
  document.getElementById("kyb_purpose").value = "";
  clearAgentTrace();
  setAgentTraceStatus("Ready");
  document.getElementById("agent-trace-loading")?.classList.add("hidden");
  document.getElementById("agent-trace-panel")?.setAttribute("open", "");
  renderOwnerRows();
  renderPersonRows();
  renderDocList();
  document.getElementById("enterprise-doc-file").value = "";
  document.getElementById("demo-company-select").value = "";
  document.getElementById("demo-company-hint")?.classList.add("hidden");
  await loadDemoCompanyOptions();

  setWizardStep(1);
  setVerifySidebarStatus("Ready");
  document.getElementById("verify-panel")?.removeAttribute("open");

  await loadChecklistTemplate();
  const data = await apiJson("/api/enterprise/kyb/session", {}, "POST", 10000);
  kybSessionId = data.session_id;
}

document.addEventListener("DOMContentLoaded", () => {
  updateSubmitButtonState();

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

  document.getElementById("demo-company-select").addEventListener("change", (e) => {
    applyDemoCompany(e.target.value);
  });

  document.getElementById("kyb-submit-btn").addEventListener("click", async () => {
    const submitBtn = document.getElementById("kyb-submit-btn");
    const loading = document.getElementById("kyb-submit-loading");
    const inputs = getUserInputs();

    if (pendingDocs.length === 0) {
      alert("Upload at least one document (formation, SOS filing, or similar) before running verification.");
      return;
    }

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
      let streamOutcome;
      try {
        streamOutcome = await submitWithAgentStream(fd, controller.signal);
      } catch (err) {
        if (err.name === "AbortError") {
          throw new Error("Verification timed out. Try with fewer documents or check the backend terminal.");
        }
        throw err;
      } finally {
        clearTimeout(timer);
      }

      const data = streamOutcome.result;
      applyScorecardFallback(data.scorecard?.items, streamOutcome.appliedChecklist);

      const status = data.scorecard.kyb_status;
      setVerifySidebarStatus(status === "passed" ? "All confirmed" : status === "blocked" ? "Blocked" : "Review needed");

      setLoadingMessage("Preparing results…");
      await waitForAgentTraceQueue();
      document.getElementById("kyb-scorecard").innerHTML = renderScorecard(data);
      await sleep(500);
      await transitionWizardStep(2);
    } catch (err) {
      enqueueAgentTraceStep({ type: "error", agent: "orchestrator", message: err.message });
      await waitForAgentTraceQueue();
      setAgentTraceStatus("Error");
      alert(`Verification failed: ${err.message}`);
      setVerifySidebarStatus("Error");
    } finally {
      updateSubmitButtonState();
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
