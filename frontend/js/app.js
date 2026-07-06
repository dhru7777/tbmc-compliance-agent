const API_BASE = (window.TBMC_CONFIG && window.TBMC_CONFIG.API_BASE) || "http://127.0.0.1:8000";
const SUBMIT_TIMEOUT_MS = 300000;

let kybSessionId = null;
let checklistTemplate = [];
let checklistAnimationToken = 0;
/** Locked results for the current verification run — trace animation must not wipe these. */
let checklistRunState = {};
const owners = [];
const controlPersons = [];
const pendingDocs = [];
/** Last submit payload — used when user clicks Generate certificate. */
let lastSubmitResult = null;
let networkAdmissionGranted = false;

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
    monthly_volume_low_usd: document.getElementById("kyb_volume_low").value.trim(),
    monthly_volume_high_usd: document.getElementById("kyb_volume_high").value.trim(),
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

function isFinalChecklistResult(result) {
  return result === "PASS" || result === "FLAG" || result === "BLOCK" || result === "SKIP";
}

function renderVerifyChecklist(items) {
  const list = document.getElementById("verify-checklist");
  if (!list) return;
  list.innerHTML = items
    .map((item) => {
      const locked = checklistRunState[item.num];
      const state = locked?.result || item.state || item.result || "PENDING";
      const cls = checklistStateClass(state);
      const detail = locked?.detail || item.detail || "";
      const recommendation = locked?.recommendation || item.recommendation || "";
      const title =
        detail && isFinalChecklistResult(state)
          ? recommendation
            ? `${detail}\n\n→ ${recommendation}`
            : detail
          : "";
      return `<li class="verify-item ${cls}" data-num="${item.num}"${title ? ` title="${escapeHtml(title)}"` : ""}>
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

function rememberChecklistResult(num, result, detail, recommendation) {
  if (!isFinalChecklistResult(result)) return;
  checklistRunState[num] = { result, detail: detail || "", recommendation: recommendation || "" };
  const item = checklistTemplate.find((i) => i.num === num);
  if (item) {
    item.state = result;
    item.result = result;
    item.detail = detail || "";
    item.recommendation = recommendation || "";
  }
}

function setVerifyItemState(num, result, detail, recommendation) {
  if (result === "CHECKING" && checklistRunState[num]) return;

  rememberChecklistResult(num, result, detail, recommendation);

  const row = document.querySelector(`.verify-item[data-num="${num}"]`);
  if (!row) return;
  row.className = `verify-item ${checklistStateClass(result)}`;
  row.querySelector(".verify-badge").textContent = checklistStatusLabel(result);
  if (detail && isFinalChecklistResult(result)) {
    row.title = recommendation ? `${detail}\n\n→ ${recommendation}` : detail;
  } else if (result === "CHECKING") {
    row.removeAttribute("title");
  }
}

function syncChecklistFromScorecard(items) {
  for (const item of items || []) {
    rememberChecklistResult(item.num, item.result, item.detail, item.recommendation);
  }
  renderVerifyChecklist(checklistTemplate);
}


function markChecklistChecking(nums) {
  nums.forEach((n) => {
    if (!checklistRunState[n]) setVerifyItemState(n, "CHECKING");
  });
}

function markChecklistForTraceStep(step) {
  if (!step || step.type === "ping" || step.type === "complete") return;
  const label = (step.label || "").toLowerCase();
  if (step.agent === "orchestrator" && step.type === "think" && label.includes("apply rule")) {
    markChecklistChecking(Array.from({ length: 10 }, (_, i) => i + 1));
  }
}

function applyTraceSideEffects(step) {
  if (!step || step.type === "ping") return;
  markChecklistForTraceStep(step);
  if (step.type === "observe" && step.checklist_num && step.checklist_result) {
    setVerifyItemState(
      step.checklist_num,
      step.checklist_result,
      step.checklist_detail,
      step.checklist_recommendation
    );
  }
}

function shouldShowTraceStep(step) {
  if (!step || step.type === "ping") return false;
  if (step.type === "error") return true;
  if (step.type === "finished" || step.type === "complete") return false;
  if (!REACT_PHASES.has(step.type)) return false;
  if (step.checklist_num) return false;
  const label = traceStepLabel(step);
  if (step.type === "act" && label === "apply rule") return false;
  if (step.agent === "research_planner" && step.type !== "think") return false;
  if (step.claims) return false;
  return true;
}

function traceStepLabel(step) {
  return (step.label || shortWords(step.message, 4)).toLowerCase();
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

async function playTraceStep(step) {
  if (!step || step.type === "ping") return;

  applyTraceSideEffects(step);
  if (!shouldShowTraceStep(step)) return;

  const epoch = traceQueueEpoch;
  const label = traceStepLabel(step);
  const pause = (ms) => sleep(ms).then(() => epoch === traceQueueEpoch);

  if (step.type === "error") {
    startTraceRow(shortWords(step.message, 4));
    if (await pause(TRACE_PHASE_MS.act)) {
      if (agentTraceActiveRow) agentTraceActiveRow.classList.add("error");
      completeActiveTraceRow();
      setAgentTraceStatus("Error");
    }
    return;
  }

  startTraceRow(label);
  if (step.type === "think") setAgentTraceStatus("Thinking…");
  else if (step.type === "act") setAgentTraceStatus("Acting…");
  else setAgentTraceStatus("Observing…");

  const ms = TRACE_PHASE_MS[step.type] || 420;
  if (await pause(ms)) {
    completeActiveTraceRow(label);
    scrollTraceList();
    if (step.type === "observe" && step.kyb_status) {
      setAgentTraceStatus("Done");
    }
  }
}

function enqueueAgentTraceStep(step) {
  traceStepQueue = traceStepQueue.then(() => playTraceStep(step)).catch(() => {});
}

function traceStepKey(step) {
  if (!step) return "";
  return `${step.ts || 0}|${step.agent || ""}|${step.type || ""}|${step.label || step.message || ""}`;
}

function enqueueStreamTraceStep(step, streamedKeys) {
  if (!step || step.type === "ping") return;
  const key = traceStepKey(step);
  if (streamedKeys.has(key)) return;
  streamedKeys.add(key);
  enqueueAgentTraceStep(step);
}

function replayAgentTraceFromResult(agentTrace, streamedKeys) {
  for (const step of agentTrace || []) {
    if (!REACT_PHASES.has(step.type)) continue;
    enqueueStreamTraceStep(step, streamedKeys);
  }
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
  const streamedTraceKeys = new Set();

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

      if (payload.type === "complete" && payload.session_id) {
        finalResult = payload;
        replayAgentTraceFromResult(payload.agent_trace, streamedTraceKeys);
      } else if (payload.type === "error") {
        throw new Error(payload.message || "Verification error");
      } else if (REACT_PHASES.has(payload.type)) {
        if (payload.type === "observe" && payload.checklist_num && payload.checklist_result) {
          appliedChecklist.add(payload.checklist_num);
        }
        enqueueStreamTraceStep(payload, streamedTraceKeys);
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
  checklistRunState = {};
  checklistTemplate = checklistTemplate.map((i) => ({
    ...i,
    state: "PENDING",
    result: "PENDING",
    detail: "",
    recommendation: "",
  }));
  renderVerifyChecklist(checklistTemplate);
}

function resetCertificatePanel() {
  networkAdmissionGranted = false;
  lastSubmitResult = null;
  const layout = document.getElementById("scorecard-layout");
  const column = document.getElementById("certificate-column");
  const inner = document.getElementById("certificate-column-inner");
  layout?.classList.remove("has-certificate");
  column?.classList.add("hidden");
  column?.setAttribute("hidden", "");
  column?.classList.remove("is-active", "is-loading");
  if (inner) inner.innerHTML = "";
}

function getCertificatePdfUrl(data) {
  if (!data && !kybSessionId) return null;
  const path =
    data?.certificate_pdf_url ||
    (kybSessionId ? `/api/enterprise/kyb/${kybSessionId}/credential.pdf` : null);
  return path ? `${API_BASE}${path}` : null;
}

function showCertificatePanel(data) {
  const layout = document.getElementById("scorecard-layout");
  const inner = document.getElementById("certificate-column-inner");
  const column = document.getElementById("certificate-column");
  if (!inner || !column) return;

  const payload = data || lastSubmitResult;
  const pdfUrl = getCertificatePdfUrl(payload);

  if (!pdfUrl) {
    alert("Certificate not available. Complete verification with a passing score first.");
    return;
  }

  layout?.classList.add("has-certificate");
  column.classList.remove("hidden");
  column.removeAttribute("hidden");
  column.classList.add("is-active", "is-loading");
  const cacheBust = `t=${Date.now()}`;
  const src = pdfUrl.includes("?") ? `${pdfUrl}&${cacheBust}` : `${pdfUrl}?${cacheBust}`;

  inner.innerHTML = `
    <div class="certificate-column-header">
      <h3 class="certificate-column-title">Compliance certificate</h3>
      <p class="certificate-column-meta">Signed by the TBMC clearinghouse compliance agent</p>
    </div>
    <iframe class="certificate-frame" id="certificate-frame" src="${escapeHtml(src)}" title="Compliance certificate"></iframe>
    <div class="certificate-column-actions">
      <a class="btn btn-primary certificate-download" href="${escapeHtml(pdfUrl)}" download="tbmc-compliance-certificate.pdf">Download PDF</a>
      <a class="btn btn-secondary certificate-open" href="${escapeHtml(pdfUrl)}" target="_blank" rel="noopener">Open PDF</a>
    </div>`;

  const frame = document.getElementById("certificate-frame");
  if (frame) {
    frame.addEventListener("load", () => column.classList.remove("is-loading"), { once: true });
  }
}

function bindScorecardActions(data) {
  const genBtn = document.getElementById("kyb-generate-cert");

  genBtn?.addEventListener("click", () => {
    if (networkAdmissionGranted) {
      showCertificatePanel(data);
      return;
    }
    networkAdmissionGranted = true;
    if (genBtn) {
      genBtn.disabled = true;
      genBtn.textContent = "Certificate generated";
      genBtn.classList.remove("btn-primary");
      genBtn.classList.add("btn-secondary");
    }
    showCertificatePanel(data);
    columnScrollIntoView();
  });
}

function columnScrollIntoView() {
  if (window.matchMedia("(max-width: 900px)").matches) {
    document.getElementById("certificate-column")?.scrollIntoView({ behavior: "smooth", block: "start" });
  }
}

function scrollToVerificationPanels() {
  const header = document.querySelector(".site-header");
  const headerOffset = header ? header.getBoundingClientRect().height + 12 : 72;
  const target =
    document.querySelector(".kyb-sidebar") ||
    document.getElementById("agent-trace-panel") ||
    document.getElementById("wizard-steps");
  if (!target) {
    window.scrollTo({ top: 0, behavior: "smooth" });
    return;
  }
  const y = target.getBoundingClientRect().top + window.scrollY - headerOffset;
  window.scrollTo({ top: Math.max(0, y), behavior: "smooth" });
}

function renderScorecard(data) {
  lastSubmitResult = data;
  const sc = data.scorecard;
  const cred = data.credential;
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

  const confidence = sc.confidence_score != null ? sc.confidence_score : cred?.confidence_score;
  const creditLimit = cred?.allowed_scope?.credit_limit_usd;
  const confidenceLabel =
    confidence != null ? `${Math.round(Number(confidence) * 100)}%` : "—";

  return `
    <div class="scorecard-header">
      <h3 class="scorecard-title">${escapeHtml(statusWord)}</h3>
      <p class="scorecard-meta">${sc.flags_count} flag${sc.flags_count === 1 ? "" : "s"} · ${sc.blocks_count} block${sc.blocks_count === 1 ? "" : "s"}</p>
    </div>
    ${
      sc.kyb_status === "passed"
        ? `<div class="admission-panel">
            <p class="admission-panel-title">Clearinghouse admission</p>
            <p class="admission-panel-meta">Confidence score: ${escapeHtml(confidenceLabel)}${
              creditLimit != null
                ? ` · Approved credit limit: $${Number(creditLimit).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })} USDC`
                : ""
            }</p>
            <div class="admission-actions">
              <button type="button" class="btn btn-primary" id="kyb-generate-cert">Generate certificate</button>
            </div>
            <p class="admission-panel-hint">Generates your signed compliance certificate and previews the PDF on the right.</p>
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
    if (err instanceof TypeError || /load failed|failed to fetch|networkerror/i.test(String(err.message))) {
      throw new Error(
        `Cannot reach API at ${API_BASE}. On mobile, use the deployed site (not localhost). Check Netlify API_BASE points to Railway HTTPS.`
      );
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
  const statusEl = document.getElementById("demo-company-status");
  if (!select) return;
  if (statusEl) {
    statusEl.classList.add("hidden");
    statusEl.classList.remove("error");
  }
  try {
    const data = await apiJson("/api/enterprise/demo-companies", null, "GET", 10000);
    const companies = data.companies || [];
    if (!companies.length) throw new Error("No trial packages returned");
    select.innerHTML =
      '<option value="">Select sample package…</option>' +
      companies
        .map((c) => `<option value="${escapeHtml(c.id)}">${escapeHtml(c.label)}</option>`)
        .join("");
    if (statusEl) {
      const complete = companies.filter((c) => c.complete).length;
      const incomplete = companies.length - complete;
      statusEl.textContent = `Select a trial package (${complete} complete, ${incomplete} incomplete) or upload your own document.`;
      statusEl.classList.remove("hidden");
    }
  } catch (err) {
    select.innerHTML = '<option value="">Trial packages unavailable</option>';
    if (statusEl) {
      statusEl.textContent =
        err.message?.includes("Cannot reach API")
          ? err.message
          : "Trial packages could not load. Check that the backend is deployed with the latest code.";
      statusEl.classList.add("error");
      statusEl.classList.remove("hidden");
    }
  }
}

async function fetchTrialPdf(companyId) {
  const pdfRes = await fetch(
    `${API_BASE}/api/enterprise/demo-companies/${encodeURIComponent(companyId)}/document.pdf?t=${Date.now()}`,
    { cache: "no-store" }
  );
  if (!pdfRes.ok) throw new Error("Could not load trial PDF");
  const blob = await pdfRes.blob();
  const filename =
    pdfRes.headers.get("Content-Disposition")?.match(/filename="([^"]+)"/)?.[1] || "trial_document.pdf";
  return { blob, filename };
}

async function refreshTrialPdfIfSelected() {
  const companyId = document.getElementById("demo-company-select")?.value;
  if (!companyId) return;
  const profile = await apiJson(`/api/enterprise/demo-companies/${companyId}`, null, "GET", 10000);
  const { blob, filename } = await fetchTrialPdf(companyId);
  pendingDocs.length = 0;
  pendingDocs.push({
    label: profile.document_label || defaultDocLabel(filename),
    file: new File([blob], filename, { type: "application/pdf" }),
  });
  renderDocList();
}

async function applyDemoCompany(companyId) {
  const hintEl = document.getElementById("demo-company-hint");
  if (!companyId) {
    if (hintEl) hintEl.classList.add("hidden");
    return;
  }

  try {
    const profile = await apiJson(`/api/enterprise/demo-companies/${companyId}`, null, "GET", 10000);
    const { blob, filename } = await fetchTrialPdf(companyId);

    pendingDocs.length = 0;
    owners.length = 0;
    controlPersons.length = 0;

    document.getElementById("kyb_legal_name").value = profile.legal_name || "";
    document.getElementById("kyb_state").value = profile.state || "";
    document.getElementById("kyb_ein").value = profile.ein || "";
    document.getElementById("kyb_address").value = profile.operating_address || "";
    document.getElementById("kyb_purpose").value = profile.business_purpose || "";
    document.getElementById("kyb_volume_low").value = profile.monthly_volume_low_usd ?? "";
    document.getElementById("kyb_volume_high").value = profile.monthly_volume_high_usd ?? "";

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
  resetCertificatePanel();
  owners.length = 0;
  controlPersons.length = 0;
  pendingDocs.length = 0;

  document.getElementById("kyb_legal_name").value = "";
  document.getElementById("kyb_state").value = "";
  document.getElementById("kyb_ein").value = "";
  document.getElementById("kyb_address").value = "";
  document.getElementById("kyb_purpose").value = "";
  document.getElementById("kyb_volume_low").value = "";
  document.getElementById("kyb_volume_high").value = "";
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
    renderVerifyChecklist(checklistTemplate);
    document.getElementById("verify-panel")?.setAttribute("open", "");
  });

  document.querySelectorAll(".wizard-step-dot").forEach((dot) => {
    dot.addEventListener("click", () => {
      const target = parseInt(dot.dataset.step, 10);
      const current = document.querySelector(".wizard-step-dot.active");
      const currentStep = current ? parseInt(current.dataset.step, 10) : 1;
      if (target >= currentStep) return;
      setWizardStep(target);
      if (target === 1) {
        renderVerifyChecklist(checklistTemplate);
        document.getElementById("verify-panel")?.setAttribute("open", "");
      }
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
    resetCertificatePanel();
    setLoadingMessage("Running agent verification…");
    resetChecklistPending();
    setVerifySidebarStatus("Verifying…");
    setAgentTraceStatus("Working…");
    clearAgentTrace();
    document.getElementById("verify-panel")?.setAttribute("open", "");
    document.getElementById("agent-trace-panel")?.setAttribute("open", "");
    requestAnimationFrame(() => scrollToVerificationPanels());

    try {
      await ensureSession();
      await refreshTrialPdfIfSelected();

      const fd = new FormData();
      fd.append("legal_name", inputs.legal_name);
      fd.append("state", inputs.state);
      fd.append("ein", inputs.ein);
      fd.append("operating_address", inputs.operating_address);
      fd.append("business_purpose", inputs.business_purpose);
      fd.append("monthly_volume_low_usd", inputs.monthly_volume_low_usd);
      fd.append("monthly_volume_high_usd", inputs.monthly_volume_high_usd);
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
      setLoadingMessage("Agent finishing up…");
      await waitForAgentTraceQueue();

      setLoadingMessage("Preparing results…");
      await revealChecklistResults(data.scorecard?.items);
      applyScorecardFallback(data.scorecard?.items, streamOutcome.appliedChecklist);

      const status = data.scorecard.kyb_status;
      setVerifySidebarStatus(status === "passed" ? "All confirmed" : status === "blocked" ? "Blocked" : "Review needed");
      setAgentTraceStatus("Done");

      syncChecklistFromScorecard(data.scorecard?.items);
      document.getElementById("kyb-scorecard").innerHTML = renderScorecard(data);
      bindScorecardActions(data);
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
