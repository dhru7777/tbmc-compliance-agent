const API_BASE = (window.TBMC_CONFIG && window.TBMC_CONFIG.API_BASE) || "http://127.0.0.1:8000";
const SUBMIT_TIMEOUT_MS = 300000;
/** Agent chat UI + coach — off until future scope. */
const AGENT_CHAT_ENABLED = false;

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
let serverHasDocuments = false;

function showSection(id) {
  document.querySelectorAll(".section").forEach((s) => s.classList.remove("active"));
  document.getElementById(id).classList.add("active");
  document.body.classList.remove("landing-active");
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function formatCoachMarkdown(text) {
  return escapeHtml(text || "").replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
}

function renderChatMessages(messages, latestCoach) {
  if (!AGENT_CHAT_ENABLED) return;
  const box = document.getElementById("agent-chat-messages");
  const actions = document.getElementById("agent-chat-actions");
  const objective = document.getElementById("agent-chat-objective");
  if (!box) return;
  const list = messages || [];
  const userMsgs = list.filter((m) => m.role === "user");
  const coach = latestCoach || [...list].reverse().find((m) => m.role === "assistant");
  const display = coach ? [...userMsgs, coach] : userMsgs;

  if (!display.length) {
    box.innerHTML = `<p class="agent-chat-empty">Ask a question if you need help.</p>`;
  } else {
    box.innerHTML = display
      .map((m) => {
        const role = m.role === "user" ? "user" : "assistant";
        const text = m.message || m.content || "";
        return `<div class="agent-chat-bubble ${role}">${formatCoachMarkdown(text)}</div>`;
      })
      .join("");
  }
  box.scrollTop = box.scrollHeight;

  if (objective) {
    const st = coach?.objective_status || "in_progress";
    objective.textContent = st === "achieved" ? "Done" : st === "blocked" ? "Blocked" : "";
    objective.classList.toggle("achieved", st === "achieved");
    objective.classList.toggle("hidden", st === "in_progress" || !st);
  }

  if (actions && coach?.suggested_actions?.length) {
    actions.innerHTML = coach.suggested_actions
      .map(
        (a, i) =>
          `<button type="button" class="agent-chat-action-btn" data-chat-action="${escapeHtml(a.action)}" data-chat-label="${escapeHtml(a.label || "")}" data-action-idx="${i}">${escapeHtml(a.label || a.action)}</button>`
      )
      .join("");
    actions.querySelectorAll("[data-chat-action]").forEach((btn) => {
      btn.addEventListener("click", () => handleCoachAction(btn.dataset.chatAction, btn.dataset.chatLabel));
    });
  } else if (actions) {
    actions.innerHTML = "";
  }
}

function handleCoachAction(action, label) {
  if (action === "run_verification") {
    document.getElementById("kyb-submit-btn")?.click();
    return;
  }
  if (action === "upload_document") {
    document.getElementById("enterprise-doc-file")?.click();
    document.querySelector(".documents-section")?.scrollIntoView({ behavior: "smooth", block: "start" });
    return;
  }
  if (action === "view_scorecard") {
    transitionWizardStep(2);
    return;
  }
  if (action === "fix_form_field") {
    document.querySelector(".company-details-section")?.scrollIntoView({ behavior: "smooth", block: "start" });
  }
}

async function loadChatHistory() {
  if (!AGENT_CHAT_ENABLED) return;
  if (!kybSessionId) return;
  try {
    const data = await apiJson(`/api/enterprise/kyb/${kybSessionId}/chat`, null, "GET", 8000);
    renderChatMessages(data.chat_messages);
  } catch {
    /* ignore */
  }
}

async function sendChatMessage(text) {
  if (!AGENT_CHAT_ENABLED) return;
  if (!kybSessionId || !text?.trim()) return;
  const input = document.getElementById("agent-chat-input");
  try {
    const data = await apiJson(
      `/api/enterprise/kyb/${kybSessionId}/chat`,
      { message: text.trim() },
      "POST",
      60000
    );
    renderChatMessages(data.chat_messages, data.coach_turn);
    if (input) input.value = "";
  } catch (err) {
    alert(`Agent chat failed: ${err.message}`);
  }
}

function applyCoachFromResponse(_data) {
  if (!AGENT_CHAT_ENABLED) return;
}

function hideMissingDocsPanel() {
  /* reserved */
}

function showLanding() {
  document.querySelectorAll(".section").forEach((s) => s.classList.remove("active"));
  document.getElementById("landing").classList.add("active");
  document.body.classList.add("landing-active");
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function renderAuditTrail(attempts) {
  const panel = document.getElementById("audit-trail-panel");
  const list = document.getElementById("audit-trail-list");
  if (!panel || !list) return;
  if (!attempts?.length) {
    panel.classList.add("hidden");
    return;
  }
  list.innerHTML = attempts
    .slice()
    .reverse()
    .map((a) => {
      const when = a.at ? new Date(a.at).toLocaleString() : "";
      const status = a.pipeline_status || a.kyb_status || "—";
      return `<li>Attempt ${a.attempt}: ${escapeHtml(String(status))}${a.flags_count != null ? ` · ${a.flags_count} flag(s)` : ""} <span class="audit-time">${escapeHtml(when)}</span></li>`;
    })
    .join("");
  panel.classList.remove("hidden");
}

let networkTabEnabled = false;

const NETWORK_HUB = { id: "tbmc", label: "TBMC", sub: "Clearinghouse", r: 48 };
const NETWORK_RING = { cx: 300, cy: 238, radius: 168 };
const NETWORK_PEERS = [
  { id: "issuer-atlas", label: "Atlas Stable Mint", sub: "Issuer", r: 32, kind: "issuer" },
  { id: "issuer-harbor", label: "Harbor Reserve", sub: "Issuer", r: 32, kind: "issuer" },
  { id: "biz-clearline", label: "Clearline Treasury", sub: "Business", r: 30, kind: "business" },
  { id: "biz-summit", label: "Summit Pay", sub: "Business", r: 30, kind: "business" },
];
const NEW_MEMBER_RING_INDEX = 2;

function ringPosition(index, total, radius, cx, cy) {
  const angle = -Math.PI / 2 + (2 * Math.PI * index) / total;
  return { x: cx + radius * Math.cos(angle), y: cy + radius * Math.sin(angle) };
}

function layoutNetworkNodes(company) {
  const hub = { ...NETWORK_HUB, ...NETWORK_RING, x: NETWORK_RING.cx, y: NETWORK_RING.cy };
  const newMember = {
    id: "new-member",
    label: truncateLabel(company, 20),
    sub: "New member",
    kind: "new",
    r: 36,
  };
  const members = [...NETWORK_PEERS];
  members.splice(NEW_MEMBER_RING_INDEX, 0, newMember);

  members.forEach((node, index) => {
    const pos = ringPosition(index, members.length, NETWORK_RING.radius, NETWORK_RING.cx, NETWORK_RING.cy);
    node.x = pos.x;
    node.y = pos.y;
    node.ringIndex = index;
  });

  return { hub, members, newMember };
}

function setNetworkTabEnabled(enabled) {
  networkTabEnabled = enabled;
  const dot = document.querySelector('.wizard-step-dot[data-step="3"]');
  if (!dot) return;
  dot.classList.toggle("wizard-step-locked", !enabled);
  dot.classList.toggle("network-ready", enabled);
  dot.title = enabled ? "View clearinghouse network" : "Available after verification passes";
}

function truncateLabel(text, max = 22) {
  const t = (text || "").trim();
  if (t.length <= max) return t;
  return `${t.slice(0, max - 1)}…`;
}

function renderNetworkGraph(data) {
  const wrap = document.getElementById("network-graph-wrap");
  const subtitle = document.getElementById("network-panel-subtitle");
  const note = document.getElementById("network-panel-note");
  const legend = document.getElementById("network-legend");
  if (!wrap) return;

  const company =
    document.getElementById("kyb_legal_name")?.value?.trim() ||
    data?.verification_record?.legal_name ||
    "Verified business";
  const c4Id = data?.layered_credentials?.credentials?.C4?.credential_id;
  const shortC4 = c4Id ? `${c4Id.slice(0, 8)}…` : "";

  const { hub, members, newMember } = layoutNetworkNodes(company);

  if (subtitle) {
    subtitle.textContent = `${company} has been admitted to the TBMC clearinghouse — connected to issuers, businesses, and settlement routes.`;
  }

  function linkPath(x1, y1, x2, y2, r1, r2) {
    const dx = x2 - x1;
    const dy = y2 - y1;
    const dist = Math.hypot(dx, dy) || 1;
    const ux = dx / dist;
    const uy = dy / dist;
    const sx = x1 + ux * (r1 + 4);
    const sy = y1 + uy * (r1 + 4);
    const ex = x2 - ux * (r2 + 6);
    const ey = y2 - uy * (r2 + 6);
    return `M ${sx} ${sy} L ${ex} ${ey}`;
  }

  function hubLink(peer, isNew) {
    const d = linkPath(hub.x, hub.y, peer.x, peer.y, hub.r, peer.r);
    if (isNew) {
      return `<path class="link link-new" d="${d}"/><path class="link-new-flow" d="${d}"/>`;
    }
    return `<path class="link" d="${d}"/><path class="link-flow" d="${d}"/>`;
  }

  function ringTouchesNew(index, total) {
    const prev = (index - 1 + total) % total;
    const next = (index + 1) % total;
    return index === newMember.ringIndex || prev === newMember.ringIndex || next === newMember.ringIndex;
  }

  const hubLinks = members.map((peer) => hubLink(peer, peer.id === newMember.id)).join("");

  const ringLinks = members
    .map((peer, index) => {
      const next = members[(index + 1) % members.length];
      const d = linkPath(peer.x, peer.y, next.x, next.y, peer.r, next.r);
      const isNewSegment = ringTouchesNew(index, members.length);
      const cls = isNewSegment ? "link link-ring link-ring-new" : "link link-ring";
      return `<path class="${cls}" d="${d}"/>`;
    })
    .join("");

  const allNodes = [hub, ...members];
  const nodes = allNodes
    .map((n) => {
      const cls = ["node", n.kind || (n.id === "tbmc" ? "hub" : "")].filter(Boolean).join(" ");
      const lines = _wrapSvgLabel(n.label, 14);
      const lineOffset = ((lines.length - 1) * 6) / 2;
      const textY = n.y + (n.sub ? -2 : 4) + lineOffset;
      const subY = textY + 14 + (lines.length - 1) * 10;
      const joinClass = n.id === newMember.id ? " node-joining" : "";
      return `<g class="${cls}${joinClass}">
        <circle cx="${n.x}" cy="${n.y}" r="${n.r}"/>
        ${lines.map((line, i) => `<text x="${n.x}" y="${textY + i * 11}" class="node-label">${escapeHtml(line)}</text>`).join("")}
        ${n.sub ? `<text x="${n.x}" y="${subY}" class="node-type">${escapeHtml(n.sub)}</text>` : ""}
      </g>`;
    })
    .join("");

  wrap.innerHTML = `<svg class="network-graph" viewBox="0 0 600 460" role="img" aria-label="TBMC clearinghouse network with connected members">
    <circle class="network-ring-guide" cx="${NETWORK_RING.cx}" cy="${NETWORK_RING.cy}" r="${NETWORK_RING.radius}" />
    ${ringLinks}
    ${hubLinks}
    ${nodes}
  </svg>`;

  if (legend) {
    legend.innerHTML = `
      <span class="network-legend-item"><span class="network-legend-swatch hub"></span> TBMC clearinghouse</span>
      <span class="network-legend-item"><span class="network-legend-swatch new"></span> New member</span>
      <span class="network-legend-item"><span class="network-legend-swatch issuer"></span> Issuer</span>
      <span class="network-legend-item"><span class="network-legend-swatch business"></span> Business</span>
      <span class="network-legend-item">— gold = USDC via hub · ring = shared network</span>`;
  }

  if (note) {
    note.textContent = c4Id
      ? `${company} is on the clearinghouse ring with ${NETWORK_PEERS.length} existing members. Settlement and proof-of-ownership route through TBMC using master credential C4 (${shortC4}).`
      : `Members connect on a shared clearinghouse ring. TBMC routes USDC settlement between issuers and businesses. Complete verification to admit a new node.`;
  }
}

function _wrapSvgLabel(text, maxLen) {
  const words = (text || "").split(/\s+/);
  const lines = [];
  let line = "";
  for (const w of words) {
    const next = line ? `${line} ${w}` : w;
    if (next.length > maxLen && line) {
      lines.push(line);
      line = w;
    } else {
      line = next;
    }
  }
  if (line) lines.push(line);
  return lines.length ? lines : ["—"];
}

function openNetworkTab(data) {
  if (!networkTabEnabled) return;
  renderNetworkGraph(data || lastSubmitResult);
  transitionWizardStep(3);
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

function setDocExtractStatus(text, isError = false) {
  const el = document.getElementById("doc-extract-status");
  if (!el) return;
  if (!text) {
    el.textContent = "";
    el.classList.add("hidden");
    el.classList.remove("error");
    return;
  }
  el.textContent = text;
  el.classList.remove("hidden");
  el.classList.toggle("error", isError);
}

function setFieldIfEmpty(id, value) {
  const el = document.getElementById(id);
  if (!el || el.value.trim() || !value) return false;
  el.value = value;
  return true;
}

function applySuggestedClaims(claims) {
  if (!claims) return 0;
  let filled = 0;
  if (setFieldIfEmpty("kyb_legal_name", claims.legal_name)) filled += 1;
  if (setFieldIfEmpty("kyb_state", claims.state)) filled += 1;
  if (setFieldIfEmpty("kyb_ein", claims.ein)) filled += 1;
  if (setFieldIfEmpty("kyb_address", claims.operating_address)) filled += 1;
  if (setFieldIfEmpty("kyb_purpose", claims.business_purpose)) filled += 1;

  const hasOwners = owners.some((o) => (o.name || "").trim());
  if (!hasOwners && Array.isArray(claims.beneficial_owners) && claims.beneficial_owners.length) {
    owners.length = 0;
    claims.beneficial_owners.forEach((o) => owners.push({ name: o.name || "", ownership_pct: o.ownership_pct ?? 25 }));
    renderOwnerRows();
    filled += 1;
  }

  const hasPersons = controlPersons.some((p) => (p.name || "").trim());
  if (!hasPersons && Array.isArray(claims.control_persons) && claims.control_persons.length) {
    controlPersons.length = 0;
    claims.control_persons.forEach((p) =>
      controlPersons.push({ name: p.name || "", title: p.title || "Control person" })
    );
    renderPersonRows();
    filled += 1;
  }

  // Backfill from extraction even when empty owner rows exist
  if (!document.getElementById("kyb_ein")?.value?.trim() && claims.ein) {
    document.getElementById("kyb_ein").value = claims.ein;
    filled += 1;
  }
  if (!document.getElementById("kyb_address")?.value?.trim() && claims.operating_address) {
    document.getElementById("kyb_address").value = claims.operating_address;
    filled += 1;
  }
  if (!document.getElementById("kyb_purpose")?.value?.trim() && claims.business_purpose) {
    document.getElementById("kyb_purpose").value = claims.business_purpose;
    filled += 1;
  }
  if (!document.getElementById("kyb_state")?.value?.trim() && claims.state) {
    document.getElementById("kyb_state").value = claims.state;
    filled += 1;
  }
  return filled;
}

let docExtractInFlight = null;

async function extractAndAutofillFromDocs() {
  if (!pendingDocs.length) return;
  await ensureSession();

  const fd = new FormData();
  const trialCompanyId = getTrialCompanyIdForSubmit();
  if (trialCompanyId) fd.append("trial_company_id", trialCompanyId);
  pendingDocs.forEach((d) => {
    fd.append("documents", d.file, d.file.name);
    fd.append("document_labels", d.label.trim() || defaultDocLabel(d.file.name));
  });

  setDocExtractStatus("");
  try {
    const data = await apiForm(`/api/enterprise/kyb/${kybSessionId}/extract-documents`, fd, 180000);
    const filled = applySuggestedClaims(data.suggested_claims);
    serverHasDocuments = (data.document_count || 0) > 0;
    updateSubmitButtonState();
    const warnings = (data.warnings || []).filter(Boolean);
    if (warnings.length) {
      setDocExtractStatus(`${warnings[0]}${warnings.length > 1 ? ` (+${warnings.length - 1} more)` : ""}`, true);
    } else {
      setDocExtractStatus("");
    }
  } catch (err) {
    setDocExtractStatus(`Document read failed: ${err.message}`, true);
  }
}

function queueDocExtract() {
  docExtractInFlight = (docExtractInFlight || Promise.resolve())
    .then(() => extractAndAutofillFromDocs())
    .catch(() => {});
  return docExtractInFlight;
}

async function addSelectedDocs() {
  const fileInput = document.getElementById("enterprise-doc-file");
  const files = Array.from(fileInput.files || []);
  if (!files.length) {
    alert("Select one or more files.");
    return;
  }
  for (const file of files) {
    pendingDocs.push({ label: defaultDocLabel(file.name), file });
  }
  if (files.some((f) => /\.(txt|md)$/i.test(f.name))) {
    document.getElementById("demo-company-select").value = "";
    document.getElementById("demo-company-hint")?.classList.add("hidden");
    document.getElementById("kyb_address").value = "";
    document.getElementById("kyb_ein").value = "";
    document.getElementById("kyb_purpose").value = "";
    owners.length = 0;
    owners.push({ name: "", ownership_pct: 25 });
    controlPersons.length = 0;
    controlPersons.push({ name: "", title: "CEO" });
    renderOwnerRows();
    renderPersonRows();
  }
  fileInput.value = "";
  renderDocList();
  await queueDocExtract();
}

function getTrialCompanyIdForSubmit() {
  return document.getElementById("demo-company-select")?.value?.trim() || "";
}

function getUserInputs() {
  const rawEin = document.getElementById("kyb_ein").value.trim();
  const ein = /^X{2}-X{7}$/i.test(rawEin.replace(/\s/g, "")) ? "" : rawEin;
  return {
    legal_name: document.getElementById("kyb_legal_name").value.trim(),
    state: document.getElementById("kyb_state").value.trim().toUpperCase(),
    ein,
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
    SKIP: "Not verified",
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
  if (step.agent === "orchestrator" && step.type === "think" && label.includes("Preparing deterministic")) {
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
  if (step.checklist_num || step.trace_visible === false) return false;
  const label = traceStepLabel(step);
  if (step.type === "act" && label.includes("scorecard rule")) return false;
  if (step.agent === "research_planner" && step.type === "think" && (step.action === "skip_search" || step.action === "finish")) {
    return false;
  }
  if (step.agent === "orchestrator" && step.type === "think" && label.includes("vendor mock")) {
    return false;
  }
  if (step.agent === "research_planner" && step.type !== "think") return false;
  if (step.agent === "document_gap_advisor" && step.type === "think") return true;
  if (step.claims) return false;
  return true;
}

function traceStepLabel(step) {
  const raw = step.label || shortWords(step.message, 6);
  return raw.trim() || "…";
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

const TRACE_PHASE_MS = { think: 1000, act: 1500, observe: 1200, complete: 900 };
const TRACE_STEP_GAP_MS = 350;
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
  if (!list) return;
  list.scrollTop = list.scrollHeight;
}

function completeActiveTraceRow(doneText, step) {
  if (!agentTraceActiveRow) return;
  agentTraceActiveRow.classList.remove("active");
  agentTraceActiveRow.classList.add("done");
  const failed =
    step?.vendor_batch_passed === false ||
    step?.checklist_result === "FLAG" ||
    step?.checklist_result === "BLOCK" ||
    step?.kyb_status === "blocked" ||
    (doneText && /\bfailed\b/i.test(doneText) && step?.kyb_status !== "flagged");
  if (failed) agentTraceActiveRow.classList.add("failed");
  const textEl = agentTraceActiveRow.querySelector(".agent-trace-text");
  if (doneText && textEl) textEl.textContent = doneText;
  const icon = agentTraceActiveRow.querySelector(".agent-trace-icon");
  if (icon) {
    icon.innerHTML = failed
      ? '<span class="agent-fail" aria-hidden="true">✕</span>'
      : '<span class="agent-check" aria-hidden="true">✓</span>';
  }
  agentTraceActiveRow = null;
  scrollTraceList();
}

function shortWords(text, max = 6) {
  if (!text) return "…";
  const words = String(text)
    .replace(/[^\w\s]/g, " ")
    .split(/\s+/)
    .filter(Boolean);
  const label = words.slice(0, max).join(" ");
  if (!label) return "…";
  return label.charAt(0).toUpperCase() + label.slice(1);
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
    startTraceRow(shortWords(step.message, 6));
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
    completeActiveTraceRow(label, step);
    scrollTraceList();
    if (step.type === "observe" && step.kyb_status) {
      setAgentTraceStatus("Done");
    }
    await pause(TRACE_STEP_GAP_MS);
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

function getCertificatePdfUrl(data, kind = "compliance") {
  if (!data && !kybSessionId) return null;
  const urls = data?.certificate_urls;
  const path =
    (urls && urls[kind]) ||
    (kind === "compliance" ? data?.certificate_pdf_url : null) ||
    (kybSessionId && kind === "compliance"
      ? `/api/enterprise/kyb/${kybSessionId}/credential.pdf`
      : kybSessionId
        ? `/api/enterprise/kyb/${kybSessionId}/credentials/${kind}.pdf`
        : null);
  return path ? `${API_BASE}${path}` : null;
}

const CERTIFICATE_TABS = [
  { id: "compliance", label: "Compliance" },
  { id: "kyc", label: "KYC (C1)" },
  { id: "kyb", label: "KYB (C2)" },
  { id: "kya", label: "KYA Agent" },
];

let certificateZoom = 1;
let certificateBlobUrl = null;
const CERT_ZOOM_MIN = 0.5;
const CERT_ZOOM_MAX = 2.5;
const CERT_ZOOM_STEP = 0.12;
const CERT_FRAME_BASE_HEIGHT = 720;

function revokeCertificateBlobUrl() {
  if (certificateBlobUrl) {
    URL.revokeObjectURL(certificateBlobUrl);
    certificateBlobUrl = null;
  }
}

async function loadCertificatePdfIntoFrame(pdfUrl, column) {
  const frame = document.getElementById("certificate-frame");
  const viewport = document.getElementById("certificate-viewport");
  document.getElementById("certificate-pdf-error")?.remove();
  if (!frame) return;

  try {
    const res = await fetch(pdfUrl, { mode: "cors" });
    if (!res.ok) {
      let detail = `HTTP ${res.status}`;
      try {
        const err = await res.json();
        detail = err.detail || detail;
      } catch {
        /* ignore */
      }
      throw new Error(detail);
    }
    const blob = await res.blob();
    if (!blob.size) {
      throw new Error("PDF response was empty");
    }
    if (blob.type && blob.type.includes("json")) {
      const text = await blob.text();
      throw new Error(text.slice(0, 160) || "Server did not return a PDF");
    }
    revokeCertificateBlobUrl();
    certificateBlobUrl = URL.createObjectURL(blob);
    frame.src = certificateBlobUrl;
  } catch (err) {
    frame.removeAttribute("src");
    if (viewport) {
      const msg = document.createElement("p");
      msg.id = "certificate-pdf-error";
      msg.className = "certificate-pdf-error";
      msg.textContent = `Could not load PDF preview: ${err.message}. Try Open PDF or Download PDF.`;
      viewport.prepend(msg);
    }
  } finally {
    column?.classList.remove("is-loading");
  }
}

function setCertificateZoom(scale) {
  certificateZoom = Math.min(CERT_ZOOM_MAX, Math.max(CERT_ZOOM_MIN, Math.round(scale * 100) / 100));
  const inner = document.getElementById("certificate-zoom-inner");
  const label = document.getElementById("certificate-zoom-label");
  if (inner) {
    inner.style.transform = `scale(${certificateZoom})`;
    inner.style.marginBottom = `${CERT_FRAME_BASE_HEIGHT * (certificateZoom - 1)}px`;
  }
  if (label) label.textContent = `${Math.round(certificateZoom * 100)}%`;
}

function bindCertificateZoomControls() {
  const viewport = document.getElementById("certificate-viewport");
  document.getElementById("cert-zoom-out")?.addEventListener("click", () => {
    setCertificateZoom(certificateZoom - CERT_ZOOM_STEP);
  });
  document.getElementById("cert-zoom-in")?.addEventListener("click", () => {
    setCertificateZoom(certificateZoom + CERT_ZOOM_STEP);
  });
  document.getElementById("cert-zoom-reset")?.addEventListener("click", () => {
    setCertificateZoom(1);
  });
  viewport?.addEventListener(
    "wheel",
    (e) => {
      e.preventDefault();
      setCertificateZoom(certificateZoom + (e.deltaY < 0 ? CERT_ZOOM_STEP : -CERT_ZOOM_STEP));
    },
    { passive: false }
  );
  setCertificateZoom(certificateZoom);
}

function showCertificatePanel(data) {
  const layout = document.getElementById("scorecard-layout");
  const inner = document.getElementById("certificate-column-inner");
  const column = document.getElementById("certificate-column");
  if (!inner || !column) return;

  const payload = data || lastSubmitResult;
  const activeTab = payload?._certTab || "compliance";
  const pdfUrl = getCertificatePdfUrl(payload, activeTab);

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

  const tabsHtml = CERTIFICATE_TABS.map(
    (t) =>
      `<button type="button" class="certificate-tab${t.id === activeTab ? " is-active" : ""}" data-cert-tab="${t.id}">${escapeHtml(t.label)}</button>`
  ).join("");

  inner.innerHTML = `
    <div class="certificate-column-header">
      <h3 class="certificate-column-title">Verification documents</h3>
      <p class="certificate-column-meta">Separate PDFs for compliance, KYC, KYB, and agent audit proof</p>
      <div class="certificate-tabs" role="tablist">${tabsHtml}</div>
    </div>
    <div class="certificate-zoom-toolbar" aria-label="PDF zoom controls">
      <button type="button" class="btn btn-secondary btn-sm" id="cert-zoom-out" title="Zoom out">−</button>
      <span class="certificate-zoom-label" id="certificate-zoom-label">100%</span>
      <button type="button" class="btn btn-secondary btn-sm" id="cert-zoom-in" title="Zoom in">+</button>
      <button type="button" class="btn btn-secondary btn-sm" id="cert-zoom-reset" title="Reset zoom">Fit</button>
      <span class="certificate-zoom-hint">Scroll or use +/− to zoom</span>
    </div>
    <div class="certificate-viewport" id="certificate-viewport">
      <div class="certificate-zoom-inner" id="certificate-zoom-inner">
        <iframe class="certificate-frame" id="certificate-frame" title="Verification certificate"></iframe>
      </div>
    </div>
    <div class="certificate-column-actions">
      <a class="btn btn-primary certificate-download" href="${escapeHtml(pdfUrl)}" download="tbmc-${activeTab}-certificate.pdf">Download PDF</a>
      <a class="btn btn-secondary certificate-open" href="${escapeHtml(pdfUrl)}" target="_blank" rel="noopener">Open PDF</a>
    </div>`;

  certificateZoom = 1;
  bindCertificateZoomControls();

  inner.querySelectorAll("[data-cert-tab]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const tab = btn.getAttribute("data-cert-tab");
      if (payload) payload._certTab = tab;
      if (lastSubmitResult) lastSubmitResult._certTab = tab;
      showCertificatePanel(payload);
    });
  });

  loadCertificatePdfIntoFrame(src, column);
}

function bindScorecardActions(data) {
  const genBtn = document.getElementById("kyb-generate-cert");
  const networkBtn = document.getElementById("kyb-view-network");

  networkBtn?.addEventListener("click", () => openNetworkTab(data));

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
  const confidenceLabel =
    confidence != null ? `${Math.round(Number(confidence) * 100)}%` : "—";

  const cost = data.cost_analysis;
  const costUsd = cost?.total_cost_usd;
  const costLine =
    costUsd != null
      ? ` · API cost $${Number(costUsd).toFixed(4)} (${cost.live_api_calls || 0} live call${cost.live_api_calls === 1 ? "" : "s"})`
      : "";

  return `
    <div class="scorecard-header">
      <h3 class="scorecard-title">${escapeHtml(statusWord)}</h3>
      <p class="scorecard-meta">${sc.flags_count} flag${sc.flags_count === 1 ? "" : "s"} · ${sc.blocks_count} block${sc.blocks_count === 1 ? "" : "s"}${costLine}</p>
    </div>
    ${
      sc.kyb_status === "passed"
        ? `<div class="admission-panel">
            <p class="admission-panel-title">Clearinghouse admission</p>
            <p class="admission-panel-meta">Confidence score: ${escapeHtml(confidenceLabel)}</p>
            <div class="admission-actions">
              <button type="button" class="btn btn-primary" id="kyb-generate-cert">Generate certificate</button>
              <button type="button" class="btn btn-secondary" id="kyb-view-network">View network</button>
            </div>
            <p class="admission-panel-hint">Opens separate PDFs for compliance admission, KYC, KYB, and KYA agent proof.</p>
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
  const hasDocs = pendingDocs.length > 0 || serverHasDocuments;
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

async function apiForm(path, formData, timeoutMs = 60000) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(`${API_BASE}${path}`, {
      method: "POST",
      body: formData,
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
      throw new Error("Request timed out — extraction may still be running. Try again in a moment.");
    }
    if (err instanceof TypeError || /load failed|failed to fetch|networkerror/i.test(String(err.message))) {
      throw new Error(`Cannot reach API at ${API_BASE}.`);
    }
    throw err;
  } finally {
    clearTimeout(timer);
  }
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

const RIVERSTONE_TRIAL = {
  id: "riverstone-holdings",
  label: "Riverstone Holdings LLC — complete package",
  hint: "Loads all 8 KYB documents (formation, EIN, ownership, address, purpose, and ID).",
};

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
    const fromApi = data.companies || [];
    const riverstone = fromApi.find((c) => c.id === RIVERSTONE_TRIAL.id);
    const staleBackend = fromApi.length > 0 && !riverstone;

    const companies = riverstone ? [riverstone] : [RIVERSTONE_TRIAL];

    select.innerHTML =
      '<option value="">Select trial package…</option>' +
      companies
        .map((c) => `<option value="${escapeHtml(c.id)}">${escapeHtml(c.label)}</option>`)
        .join("");

    if (statusEl) {
      if (staleBackend) {
        statusEl.textContent =
          "API is on an old deploy — redeploy Railway service tbmc-compliance-api (feisty-beauty) from latest main. Riverstone is shown but the 8-file bundle loads from agent-skill/mock documents on the server.";
        statusEl.classList.add("error");
      } else {
        statusEl.textContent =
          "Select Riverstone Holdings to load all 8 KYB documents, or upload your own files.";
      }
      statusEl.classList.remove("hidden");
    }
  } catch (err) {
    select.innerHTML =
      `<option value="">Select trial package…</option>` +
      `<option value="${escapeHtml(RIVERSTONE_TRIAL.id)}">${escapeHtml(RIVERSTONE_TRIAL.label)}</option>`;
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

async function fetchTrialMockBundle(companyId) {
  const data = await apiJson(
    `/api/enterprise/demo-companies/${encodeURIComponent(companyId)}/documents`,
    null,
    "GET",
    30000
  );
  return data.documents || [];
}

function applyTrialDocumentsToPending(profile, documents) {
  pendingDocs.length = 0;
  for (const doc of documents) {
    pendingDocs.push({
      label: doc.label || defaultDocLabel(doc.filename),
      file: new File([doc.content], doc.filename, { type: "text/plain" }),
    });
  }
  renderDocList();
}

async function refreshTrialDocumentsIfSelected() {
  const companyId = document.getElementById("demo-company-select")?.value;
  if (!companyId) return;
  const profile = await apiJson(`/api/enterprise/demo-companies/${companyId}`, null, "GET", 10000);
  if (profile.package_kind === "mock_bundle") {
    const documents = await fetchTrialMockBundle(companyId);
    applyTrialDocumentsToPending(profile, documents);
    return;
  }
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

    if (profile.package_kind === "mock_bundle") {
      const documents = await fetchTrialMockBundle(companyId);
      applyTrialDocumentsToPending(profile, documents);
      await queueDocExtract();
    } else {
      const { blob, filename } = await fetchTrialPdf(companyId);
      pendingDocs.push({
        label: profile.document_label || defaultDocLabel(filename),
        file: new File([blob], filename, { type: "application/pdf" }),
      });
      renderDocList();
    }

    renderOwnerRows();
    renderPersonRows();
    document.getElementById("enterprise-doc-file").value = "";

    if (hintEl) {
      const count = profile.document_count || pendingDocs.length;
      hintEl.textContent =
        profile.hint || (count ? `${count} documents loaded.` : profile.hint || "");
      hintEl.classList.remove("hidden");
    }
  } catch (err) {
    alert(`Could not load trial company: ${err.message}`);
    document.getElementById("demo-company-select").value = "";
  }
}

async function initKybSession() {
  kybSessionId = null;
  serverHasDocuments = false;
  resetCertificatePanel();
  setNetworkTabEnabled(false);
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
  setDocExtractStatus("");
  document.getElementById("demo-company-select").value = "";
  document.getElementById("demo-company-hint")?.classList.add("hidden");
  hideMissingDocsPanel();
  document.getElementById("audit-trail-panel")?.classList.add("hidden");
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

  if (AGENT_CHAT_ENABLED) {
    document.getElementById("agent-chat-form")?.addEventListener("submit", async (e) => {
      e.preventDefault();
      const input = document.getElementById("agent-chat-input");
      const text = input?.value?.trim();
      if (!text) return;
      await sendChatMessage(text);
    });
  }

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
      if (target === 3) {
        if (!networkTabEnabled) return;
        openNetworkTab(lastSubmitResult);
        return;
      }
      if (target > currentStep) return;
      setWizardStep(target);
      if (target === 1) {
        renderVerifyChecklist(checklistTemplate);
        document.getElementById("verify-panel")?.setAttribute("open", "");
      }
    });
  });

  document.getElementById("kyb-step3-back")?.addEventListener("click", () => {
    setWizardStep(2);
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
      if (pendingDocs.length) queueDocExtract();
      else setDocExtractStatus("");
    }
  });

  document.getElementById("demo-company-select").addEventListener("change", (e) => {
    applyDemoCompany(e.target.value);
  });

  document.getElementById("kyb-submit-btn").addEventListener("click", async () => {
    const submitBtn = document.getElementById("kyb-submit-btn");
    const loading = document.getElementById("kyb-submit-loading");

    if (pendingDocs.length === 0) {
      alert("Upload at least one document (formation, SOS filing, or similar) before running verification.");
      return;
    }

    submitBtn.disabled = true;
    loading.classList.remove("hidden");
    resetCertificatePanel();
    setLoadingMessage("Reading documents…");
    resetChecklistPending();
    setVerifySidebarStatus("Verifying…");
    setAgentTraceStatus("Working…");
    clearAgentTrace();
    document.getElementById("verify-panel")?.setAttribute("open", "");
    document.getElementById("agent-trace-panel")?.setAttribute("open", "");
    requestAnimationFrame(() => scrollToVerificationPanels());

    try {
      await ensureSession();
      await extractAndAutofillFromDocs();
      const inputs = getUserInputs();
      setLoadingMessage("Running agent verification…");
      await refreshTrialDocumentsIfSelected();

      const fd = new FormData();
      fd.append("legal_name", inputs.legal_name);
      fd.append("state", inputs.state);
      fd.append("ein", inputs.ein);
      fd.append("operating_address", inputs.operating_address);
      fd.append("business_purpose", inputs.business_purpose);
      fd.append("monthly_volume_low_usd", inputs.monthly_volume_low_usd);
      fd.append("monthly_volume_high_usd", inputs.monthly_volume_high_usd);
      const trialCompanyId = getTrialCompanyIdForSubmit();
      if (trialCompanyId) {
        fd.append("trial_company_id", trialCompanyId);
      }
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

      renderAuditTrail(data.verify_attempts);

      if (data.pipeline_status === "needs_documents") {
        setVerifySidebarStatus("Documents needed");
        setAgentTraceStatus("Done");
        serverHasDocuments = true;
        updateSubmitButtonState();
        return;
      }

      hideMissingDocsPanel();
      setLoadingMessage("Preparing results…");
      await revealChecklistResults(data.scorecard?.items);
      applyScorecardFallback(data.scorecard?.items, streamOutcome.appliedChecklist);

      const status = data.scorecard?.kyb_status || data.pipeline_status;
      setVerifySidebarStatus(status === "passed" ? "All confirmed" : status === "blocked" ? "Blocked" : "Review needed");
      setAgentTraceStatus("Done");

      syncChecklistFromScorecard(data.scorecard?.items);
      document.getElementById("kyb-scorecard").innerHTML = renderScorecard(data);
      bindScorecardActions(data);
      setNetworkTabEnabled(data.scorecard?.kyb_status === "passed");
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

  document.getElementById("kyb-view-audit-md")?.addEventListener("click", async () => {
    if (!kybSessionId) return;
    const res = await fetch(`${API_BASE}/api/enterprise/kyb/${kybSessionId}/record`);
    const data = await res.json();
    const pre = document.getElementById("kyb-md-preview");
    if (pre) {
      pre.textContent = data.markdown || "(empty)";
      pre.classList.remove("hidden");
      pre.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }
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
