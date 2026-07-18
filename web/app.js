"use strict";

// ---------------------------------------------------------------------------
// Small helpers
// ---------------------------------------------------------------------------
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

const state = {
  profiles: [],
  profileData: {},
  profileModels: {},
  defaultProfile: null,
  vramTotalMb: null,
  vramFreeMb: null,
  vramBudgetMb: null,
  installedByName: {},
  // False until /registry/installed has answered once — before that we can't
  // tell "nothing pulled" from "not checked yet", so nothing gets hidden.
  installedLoaded: false,
  benchShowUnpulled: false,
  servedModels: [],
  runningDetails: [],
  runningPlacements: {},
  lastHardware: null,
  testBenchInfo: null,
  questionSetValidation: null,
  lastRun: null,
  benchmarkRuns: [],
  liveBenchmarkRuns: [],
  benchmarkSelectedProfiles: [],
  selectedRunIds: [],
  compareBaselineId: null,
  activeRunId: null,
  fitRefreshTimer: null,
  currentQueue: [],
  activeController: null,
  queueCancelled: false,
};

const BENCHMARK_RUNS_KEY = "localdeploy.benchmarkRuns.v1";

function downloadFile(filename, content, mime) {
  const blob = new Blob([content], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

// Extract a report card's JSON from an uploaded file (raw JSON or embedded HTML).
function extractCard(text) {
  try {
    const j = JSON.parse(text);
    if (j && (j.kind === "localdeploy.report_card" || Array.isArray(j.tests))) return j;
  } catch {
    /* not raw JSON; try embedded */
  }
  const m = text.match(/<script[^>]*id=["']localdeploy-card["'][^>]*>([\s\S]*?)<\/script>/);
  if (m) {
    // Reverse the server's html.escape(..., quote=False): &lt; and &gt; first,
    // then &amp; last so escaped entities round-trip correctly.
    const unescaped = m[1].replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&amp;/g, "&");
    try {
      return JSON.parse(unescaped);
    } catch {
      /* fall through */
    }
  }
  return null;
}

function runId() {
  return `run-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
}

function hashText(text) {
  let h = 2166136261;
  for (let i = 0; i < text.length; i++) {
    h ^= text.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return (h >>> 0).toString(16).padStart(8, "0");
}

function loadBenchmarkRuns() {
  try {
    const raw = localStorage.getItem(BENCHMARK_RUNS_KEY);
    const parsed = raw ? JSON.parse(raw) : [];
    state.benchmarkRuns = Array.isArray(parsed) ? parsed.slice(0, 80) : [];
    state.benchmarkRuns.forEach((r) => {
      r.source = r.source || "restored-history";
      r.id = r.id || runId();
    });
  } catch {
    state.benchmarkRuns = [];
  }
}

function saveBenchmarkRuns() {
  try {
    localStorage.setItem(BENCHMARK_RUNS_KEY, JSON.stringify(state.benchmarkRuns.slice(0, 80)));
  } catch {
    /* localStorage can be disabled; the current session still works */
  }
}

function allBenchmarkRuns() {
  const liveIds = new Set(state.liveBenchmarkRuns.map((r) => r.id));
  return [...state.liveBenchmarkRuns, ...state.benchmarkRuns.filter((r) => !liveIds.has(r.id))];
}

function runLabel(run) {
  const model = run?.modelId || run?.model_id || run?.profile || "run";
  const dev = run?.actualDevice || run?.requestedDevice || run?.device;
  return `${model}${dev ? `/${String(dev).toUpperCase()}` : ""}`;
}

function summaryFromTests(tests) {
  const rows = tests || [];
  const successes = rows.filter((t) => t.success);
  const mean = (arr) => (arr.length ? arr.reduce((s, v) => s + Number(v || 0), 0) / arr.length : 0);
  const tps = successes.map((t) => t.approx_tokens_per_second).filter((v) => v != null);
  return {
    tests: rows.length,
    passed: successes.length,
    avg_accuracy: Number(mean(rows.map((t) => t.accuracy || 0)).toFixed(3)),
    avg_latency_s: Number(mean(rows.map((t) => t.elapsed_seconds || 0)).toFixed(3)),
    avg_tokens_per_second: tps.length ? Number(mean(tps).toFixed(2)) : null,
  };
}

function categorySummary(tests) {
  const groups = {};
  for (const t of tests || []) {
    (groups[t.category || "?"] ||= []).push(t);
  }
  return Object.keys(groups)
    .sort()
    .map((category) => {
      const rows = groups[category];
      return { category, ...summaryFromTests(rows) };
    });
}

function normalizeRunRecord(input, source = "current-run") {
  const tests = input.tests || [];
  const summary = input.summary || summaryFromTests(tests);
  const profile = input.profile || input.profileName || null;
  const modelId = input.modelId || input.model_id || profile || null;
  const requestedDevice = input.requestedDevice || input.device || null;
  const actualDevice = input.actualDevice || input.device || requestedDevice || null;
  return {
    id: input.id || runId(),
    createdAt: input.createdAt || input.generated_at || new Date().toISOString(),
    profile,
    modelId,
    requestedDevice,
    actualDevice,
    questionSetName: input.questionSetName || "Imported report card",
    questionSetHash: input.questionSetHash || null,
    hardware: input.hardware || {},
    tests,
    summary,
    category_summary: input.category_summary || input.categorySummary || categorySummary(tests),
    elapsedSeconds: input.elapsedSeconds ?? summary.elapsed_seconds ?? null,
    source,
  };
}

function addBenchmarkRuns(runs, selectNew = true) {
  const normalized = runs.map((r) => normalizeRunRecord(r, r.source || "current-run"));
  const existing = new Set(state.benchmarkRuns.map((r) => r.id));
  const fresh = normalized.filter((r) => !existing.has(r.id));
  const previousSelected = state.selectedRunIds.filter((id) => existing.has(id));
  const previousLatest = state.benchmarkRuns[0]?.id ? [state.benchmarkRuns[0].id] : [];
  state.benchmarkRuns = [...fresh, ...state.benchmarkRuns].slice(0, 80);
  if (selectNew && fresh.length) {
    const freshIds = fresh.map((r) => r.id);
    let nextSelected = previousSelected.length ? previousSelected : previousLatest;
    freshIds.forEach((id) => {
      if (!nextSelected.includes(id)) nextSelected.push(id);
    });
    // Keep every newly completed run selected (no cap): a 5th+ model must stay
    // in the dashboard/queue instead of silently dropping an earlier one.
    state.selectedRunIds = nextSelected;
    state.compareBaselineId = state.compareBaselineId && nextSelected.includes(state.compareBaselineId)
      ? state.compareBaselineId
      : nextSelected[0];
    state.activeRunId = fresh[0].id;
    state.lastRun = fresh[0];
  }
  saveBenchmarkRuns();
  renderBenchmarkWorkspace();
}

function liveRecordFromQueueItem(item) {
  const tests = item.tests || [];
  return {
    ...normalizeRunRecord(
      {
        ...item,
        hardware: state.lastHardware || {},
        summary: summaryFromTests(tests),
        category_summary: categorySummary(tests),
        source: "live-run",
      },
      "live-run"
    ),
    status: item.status,
    progress: item.progress || 0,
  };
}

function syncLiveBenchmarkRun(item, select = false) {
  if (!item || !(item.tests || []).length) return;
  const record = liveRecordFromQueueItem(item);
  const idx = state.liveBenchmarkRuns.findIndex((r) => r.id === record.id);
  if (idx >= 0) state.liveBenchmarkRuns[idx] = record;
  else state.liveBenchmarkRuns.unshift(record);
  state.activeRunId = record.id;
  if (select && !state.selectedRunIds.includes(record.id)) {
    state.selectedRunIds = [...state.selectedRunIds, record.id];
    if (!state.compareBaselineId) state.compareBaselineId = record.id;
  }
  renderBenchmarkWorkspace();
}

function removeLiveBenchmarkRun(id, rerender = true) {
  const before = state.liveBenchmarkRuns.length;
  state.liveBenchmarkRuns = state.liveBenchmarkRuns.filter((r) => r.id !== id);
  if (rerender && before !== state.liveBenchmarkRuns.length) renderBenchmarkWorkspace();
}

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
  );
}

// Shimmering placeholder rows shown while a card's first fetch is in flight,
// so a slow local backend reads as "loading" instead of "frozen".
function skeletonHtml(lines = 2) {
  return `<div class="skeleton-block">${Array.from({ length: lines }, () => `<div class="skeleton-line"></div>`).join("")}</div>`;
}

function toast(message, kind = "info") {
  const node = document.createElement("div");
  node.className = `toast ${kind}`;
  node.textContent = message;
  // Errors are announced assertively and stay until dismissed (click) so they
  // aren't missed; info/success auto-dismiss politely.
  if (kind === "error") {
    node.setAttribute("role", "alert");
    node.title = "Click to dismiss";
    node.addEventListener("click", () => node.remove());
  } else {
    setTimeout(() => node.remove(), 5000);
  }
  $("#toasts").appendChild(node);
}

// Tick a live "…Ns" counter into an element so long operations clearly show
// progress instead of looking frozen. Returns a stop() function.
function startElapsed(el, label = "working") {
  if (!el) return () => {};
  const t0 = Date.now();
  const render = () => {
    const s = Math.round((Date.now() - t0) / 1000);
    el.innerHTML = `<span class="spin-inline"></span> ${esc(label)}… <b>${s}s</b>`;
  };
  render();
  const id = setInterval(render, 1000);
  return () => clearInterval(id);
}

// ---- optional API token (opt-in; nothing happens unless the server sets one) -
function getToken() {
  try {
    return localStorage.getItem("localdeploy_token") || "";
  } catch {
    return "";
  }
}
function setToken(t) {
  try {
    localStorage.setItem("localdeploy_token", t);
  } catch {
    /* ignore */
  }
}
// Bootstrap: a `?token=…` in the URL is stored once, then stripped from the bar.
(function bootstrapToken() {
  const params = new URLSearchParams(window.location.search);
  const t = params.get("token");
  if (t) {
    setToken(t);
    params.delete("token");
    const q = params.toString();
    history.replaceState(null, "", window.location.pathname + (q ? `?${q}` : ""));
  }
})();
function authHeaders() {
  const t = getToken();
  return t ? { "X-API-Token": t } : {};
}

// ---- light/dark theme (persisted; defaults to OS preference) ----------------
const THEME_KEY = "localdeploy_theme";
function currentTheme() {
  const explicit = document.documentElement.dataset.theme;
  if (explicit === "light" || explicit === "dark") return explicit;
  return window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
}
function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  const btn = $("#btn-theme");
  if (btn) {
    btn.textContent = theme === "light" ? "☀" : "☾";
    btn.title = theme === "light" ? "Switch to dark theme" : "Switch to light theme";
  }
}
function toggleTheme() {
  const next = currentTheme() === "light" ? "dark" : "light";
  try {
    localStorage.setItem(THEME_KEY, next);
  } catch {
    /* theme still applies for this session */
  }
  applyTheme(next);
}
applyTheme(currentTheme());
$("#btn-theme")?.addEventListener("click", toggleTheme);
// If the server rejects us, prompt for the token once and let the user retry.
function handle401(resp) {
  if (resp && resp.status === 401) {
    const t = window.prompt("This server requires an API token. Enter it:");
    if (t) {
      setToken(t.trim());
      toast("Token saved — retry your action.", "success");
    }
    return true;
  }
  return false;
}

// Parse a Response, throwing a useful message on any non-OK status. FastAPI
// error bodies are JSON ({"detail": ...}), so we surface that text instead of
// letting an error body masquerade as a successful payload.
async function parseOrThrow(url, resp) {
  if (resp.ok) return resp.json();
  let detail = `HTTP ${resp.status}`;
  try {
    const ct = resp.headers.get("content-type") || "";
    if (ct.includes("application/json")) {
      const body = await resp.json();
      detail = body.detail || body.error || body.message || JSON.stringify(body);
    } else {
      const text = await resp.text();
      if (text) detail = text.slice(0, 300);
    }
  } catch {
    /* keep the status-based message */
  }
  throw new Error(detail);
}

async function getJSON(url) {
  const resp = await fetch(url, { headers: authHeaders() });
  if (handle401(resp)) throw new Error("unauthorized");
  return parseOrThrow(url, resp);
}

async function postJSON(url, body) {
  const resp = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify(body ?? {}),
  });
  if (handle401(resp)) throw new Error("unauthorized");
  return parseOrThrow(url, resp);
}

// Read a fetch Response as Server-Sent Events; calls onEvent(obj) per `data:`
// line and resolves when the stream ends or a [DONE] marker arrives.
async function streamSSE(response, onEvent) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  const processBlock = (block) => {
    for (const line of block.split("\n")) {
      if (!line.startsWith("data: ")) continue;
      const payload = line.slice(6);
      if (payload === "[DONE]") return true;
      try {
        onEvent(JSON.parse(payload));
      } catch {
        /* ignore non-JSON keepalives */
      }
    }
    return false;
  };
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let idx;
    while ((idx = buf.indexOf("\n\n")) >= 0) {
      const block = buf.slice(0, idx);
      buf = buf.slice(idx + 2);
      if (processBlock(block)) return;
    }
  }
  // Flush any trailing event that wasn't terminated by a blank line so a final
  // run_end/summary isn't dropped (which would leave the run looking hung).
  if (buf.trim()) processBlock(buf);
}

// POST that may return JSON (e.g. a blocked action) or an SSE stream.
// `signal` (optional) lets the caller abort an in-flight stream (cancel a pull).
async function postMaybeStream(url, body, onEvent, signal) {
  const resp = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify(body ?? {}),
    signal,
  });
  if (handle401(resp)) throw new Error("unauthorized");
  const ct = resp.headers.get("content-type") || "";
  if (ct.includes("text/event-stream")) {
    await streamSSE(resp, onEvent);
    return { streamed: true };
  }
  return { streamed: false, json: await resp.json() };
}

function busy(button, on) {
  if (!button) return;
  button.disabled = on;
  button.classList.toggle("loading", on);
}

function updateHwChip(hw) {
  const chip = $("#hw-chip");
  const ram = hw?.system?.ram_total_mb ? ` · ${fmtMb(hw.system.ram_total_mb)} RAM` : "";
  if (hw && hw.gpu_available && hw.gpus?.[0]) {
    const g = hw.gpus[0];
    const mem = g.unified_memory ? "unified memory" : `${fmtMb(g.vram_total_mb)} VRAM`;
    chip.innerHTML = `<span class="dot"></span>${esc(g.name)} · ${mem}${ram}`;
  } else {
    chip.innerHTML = `<span class="dot none"></span>CPU only${ram}`;
  }
  chip.classList.remove("hidden");
}

function syncHardwareState(hw) {
  const g = hw?.gpus?.[0];
  state.vramTotalMb = g?.vram_total_mb ?? null;
  state.vramFreeMb = g?.vram_free_mb ?? null;
  if (state.vramBudgetMb == null && state.vramTotalMb != null) {
    state.vramBudgetMb = state.vramTotalMb;
    const input = $("#vram-budget-gb");
    if (input && !input.value.trim()) input.value = (state.vramBudgetMb / 1024).toFixed(1);
  }
  updateHardwareVramUI();
  updateVramBudgetUI();
}

function setVramBudgetMb(mb) {
  if (mb == null || !Number.isFinite(Number(mb)) || Number(mb) <= 0) return;
  state.vramBudgetMb = Math.round(Number(mb));
  const input = $("#vram-budget-gb");
  if (input) input.value = (state.vramBudgetMb / 1024).toFixed(1);
  updateVramBudgetUI();
  scheduleFitRefresh();
}

function updateVramBudgetUI() {
  const label = $("#vram-budget-label");
  const live = $("#vram-live-label");
  const help = $("#vram-budget-help");
  const meter = $("#vram-budget-meter");
  if (!label || !live || !help || !meter) return;
  const budget = targetVram();
  label.textContent = budget ? `${fmtMb(budget)} for model fit checks` : "No VRAM budget set";
  const total = state.vramTotalMb;
  const free = state.vramFreeMb;
  live.textContent = total ? `${fmtMb(free)} live free / ${fmtMb(total)} total` : "";
  if (total && free != null) {
    const usedPct = Math.max(0, Math.min(100, ((total - free) / total) * 100));
    const budgetPct = budget ? Math.max(0, Math.min(100, (budget / total) * 100)) : 0;
    meter.innerHTML = `
      <div class="capacity-fill used" style="width:${usedPct.toFixed(1)}%"></div>
      <div class="capacity-marker" style="left:${budgetPct.toFixed(1)}%" title="Fit budget"></div>`;
  } else {
    meter.innerHTML = "";
  }
  if (budget && total && free != null && free < budget * 0.5) {
    help.textContent =
      `Fit checks use a ${fmtMb(budget)} budget. Live free VRAM is lower right now; choose "Current free" to filter against the memory available without unloading models.`;
  } else if (budget) {
    help.textContent = `Installed model badges, saved profile scan, and Hugging Face search all use this budget.`;
  } else {
    help.textContent = "Set a GPU budget to filter models by fit.";
  }
  updateProgressRail();
}

// ---------------------------------------------------------------------------
// Setup progress rail + recommended next action
// ---------------------------------------------------------------------------
function progressRailSteps() {
  return {
    hardware: !!state.lastHardware,
    budget: !!state.vramBudgetMb,
    model: Object.keys(state.installedByName).length > 0,
    deploy: (state.servedModels || []).length > 0,
  };
}

function nextActionHtml(title, detail, targetSel) {
  return `<div><strong>${esc(title)}</strong><div class="muted small">${esc(detail)}</div></div>
    <button class="btn primary compact next-action-go" data-target="${esc(targetSel)}">Take me there</button>`;
}

function updateNextActionCard() {
  const card = $("#next-action-card");
  if (!card) return;
  const steps = progressRailSteps();
  let html = "";
  if (!steps.hardware) {
    html = nextActionHtml(
      "Check hardware first",
      "See your GPU, VRAM, CPU, and RAM before picking a model to pull.",
      "#btn-hardware"
    );
  } else if (!steps.budget) {
    html = nextActionHtml(
      "Set a fit budget",
      "Confirm the GPU budget used for fit checks, pulls, and Hugging Face search.",
      "#vram-budget-gb"
    );
  } else if (!steps.model) {
    html = nextActionHtml(
      "Get your first model",
      "No models installed yet — get a recommended one that fits your hardware.",
      "#btn-starter-pack"
    );
  } else if (!steps.deploy) {
    html = nextActionHtml(
      "Deploy a model",
      "A model is ready — deploy it from Your models to start serving it.",
      "#your-models-card"
    );
  }
  card.innerHTML = html;
  card.classList.toggle("hidden", !html);
  const goBtn = card.querySelector(".next-action-go");
  if (goBtn) {
    goBtn.addEventListener("click", () => {
      const target = document.querySelector(goBtn.dataset.target);
      if (!target) return;
      target.scrollIntoView({ behavior: "smooth", block: "center" });
      target.focus?.();
    });
  }
}

function updateProgressRail() {
  const rail = $("#progress-rail");
  if (!rail) return;
  const steps = progressRailSteps();
  let activeAssigned = false;
  $$(".rail-step", rail).forEach((el) => {
    const done = !!steps[el.dataset.step];
    const active = !done && !activeAssigned;
    el.classList.toggle("done", done);
    el.classList.toggle("active", active);
    if (active) activeAssigned = true;
  });
  updateNextActionCard();
}

function updateHardwareVramUI() {
  const label = $("#hardware-vram-label");
  const live = $("#hardware-vram-live-label");
  const help = $("#hardware-vram-help");
  const meter = $("#hardware-vram-meter");
  if (!label || !live || !help || !meter) return;
  const total = state.vramTotalMb;
  const free = state.vramFreeMb;
  if (!total || free == null) {
    label.textContent = "No separate VRAM reading";
    live.textContent = "";
    meter.innerHTML = "";
    help.textContent = "This machine may be CPU-only or use unified memory.";
    return;
  }
  const used = Math.max(0, total - free);
  const usedPct = Math.max(0, Math.min(100, (used / total) * 100));
  label.textContent = `${fmtMb(used)} used / ${fmtMb(total)} total`;
  live.textContent = `${fmtMb(free)} free now`;
  meter.innerHTML = `<div class="capacity-fill used" style="width:${usedPct.toFixed(1)}%"></div>`;
  const running = (state.runningDetails || [])
    .map((m) => ({
      name: m.name,
      vramMb: m.size_vram_mb ?? (m.size_vram ? Math.round(m.size_vram / 1_000_000) : null),
    }))
    .filter((m) => m.name && m.vramMb);
  const ollamaMb = running.reduce((sum, m) => sum + m.vramMb, 0);
  if (ollamaMb > 0) {
    const names = running.map((m) => `${m.name} (${fmtMb(m.vramMb)})`).join(", ");
    const unattributed = Math.max(0, used - ollamaMb);
    help.textContent =
      unattributed <= 256 ? `Ollama model VRAM: ${names}.` : "";
  } else {
    help.textContent = "";
  }
}

function scheduleFitRefresh() {
  clearTimeout(state.fitRefreshTimer);
  state.fitRefreshTimer = setTimeout(() => {
    $$(".mrow[data-model]", $("#installed-body")).forEach((row) => fitCheckRow(row));
    if (!$("#fit-finder-body")?.textContent.includes("not been scanned")) scanConfiguredFits();
    if (!$("#updates-body")?.textContent.includes("No Hugging Face search")) checkUpdates();
  }, 350);
}

// Render the CPU + RAM block shared by the GPU and CPU-only hardware views.
function cpuRamRows(sys) {
  if (!sys) return "";
  const cores =
    sys.physical_cores != null
      ? `${esc(sys.physical_cores)} cores / ${esc(sys.logical_cores ?? "?")} threads`
      : `${esc(sys.logical_cores ?? "?")} logical cores`;
  const ram =
    sys.ram_total_mb != null
      ? `${fmtMb(sys.ram_total_mb)} total · ${fmtMb(sys.ram_available_mb ?? 0)} available`
      : `<span class="muted">RAM details unavailable.</span> <button class="btn compact install-psutil-btn">Install psutil</button>`;
  return `
    <span class="k">CPU</span><span>${esc(sys.cpu_model || "Unknown")}</span>
    <span class="k">Cores</span><span>${cores}</span>
    <span class="k">RAM</span><span>${ram}</span>`;
}

// Read the VRAM budget the user wants to validate against (manual or probed).
function targetVram() {
  const raw = $("#vram-budget-gb")?.value?.trim() || "";
  if (raw !== "") {
    const n = Number(raw);
    return Number.isFinite(n) && n > 0 ? Math.round(n * 1024) : null;
  }
  return state.vramBudgetMb ?? state.vramTotalMb ?? state.vramFreeMb;
}

async function refreshLiveModelState(includeInstalled = false) {
  const jobs = [refreshStatus(), checkHardware()];
  if (includeInstalled) jobs.push(refreshInstalled());
  await Promise.allSettled(jobs);
}

// ---------------------------------------------------------------------------
// Tabs
// ---------------------------------------------------------------------------
function activateTab(name) {
  $$(".tab").forEach((t) => {
    const active = t.dataset.tab === name;
    t.classList.toggle("active", active);
    t.setAttribute("aria-selected", active ? "true" : "false");
  });
  $$(".tab-panel").forEach((p) => p.classList.toggle("active", p.id === `tab-${name}`));
  if (name === "serve") {
    void refreshLiveModelState(true);
  } else if (name === "bench") {
    void refreshStatus();
  }
}

$$(".tab").forEach((tab) => tab.addEventListener("click", () => activateTab(tab.dataset.tab)));

// ---------------------------------------------------------------------------
// Bootstrap
// ---------------------------------------------------------------------------
async function loadProfiles() {
  try {
    const data = await getJSON("/profiles");
    const profiles = data.profiles || {};
    state.profileData = profiles;
    state.profiles = Object.keys(profiles);
    state.profileModels = {};
    state.profiles.forEach((name) => (state.profileModels[name] = profiles[name]?.model_id || name));
    state.defaultProfile = data.default_profile || state.profiles[0] || null;
    renderProfileSelectOptions();
    renderBenchmarkProfileChips();
    updateBenchmarkSummary();
    renderBenchmarkWorkspace();
    setProfileActionsEnabled(state.profiles.length > 0);
    setConn(true);
  } catch (err) {
    setConn(false);
    setProfileActionsEnabled(false);
    toast(`Could not load profiles: ${err.message}`, "error");
  }
}

// (Re)build both profile <select>s, annotating profiles whose model isn't
// pulled so a dropdown never silently offers a model that can't deploy.
// Called again after the installed list loads, preserving the selection.
function renderProfileSelectOptions() {
  const build = (current) =>
    state.profiles
      .map((name) => {
        const p = state.profileData[name] || {};
        let label = p.model_id ? `${name} — ${p.model_id}` : name;
        if (profileIsUnpulled(name)) {
          label += ` (${installedStatusForProfile(name).label})`;
        }
        const selectedName = current && state.profiles.includes(current) ? current : state.defaultProfile;
        const sel = name === selectedName ? " selected" : "";
        return `<option value="${esc(name)}"${sel}>${esc(label)}</option>`;
      })
      .join("");
  ["#profile-select", "#bench-profile-select"].forEach((sel) => {
    const el = $(sel);
    if (el) el.innerHTML = build(el.value);
  });
}

// Guard actions that need a profile so they can't fire a blank profile name.
function setProfileActionsEnabled(enabled) {
  ["#btn-serve", "#btn-switch", "#btn-run"].forEach((sel) => {
    const el = $(sel);
    if (el) el.disabled = !enabled;
  });
}

function setConn(ok) {
  const pill = $("#conn-pill");
  pill.textContent = ok ? "API: connected" : "API: unreachable";
  pill.className = `conn ${ok ? "ok" : "bad"}`;
}

function selectedBenchProfiles() {
  const selected = state.benchmarkSelectedProfiles.filter((name) => state.profiles.includes(name));
  if (selected.length) return selected;
  const fallback = $("#bench-profile-select")?.value;
  return fallback ? [fallback] : [];
}

function installedStatusForProfile(profileName) {
  const p = state.profileData[profileName] || {};
  if ((p.backend || "ollama") === "llamacpp") {
    // GGUF file profiles: presence is reported by the server (model_file_exists).
    if (p.model_file_exists === false) return { label: "file missing", cls: "off" };
    return { label: "gguf on disk", cls: "on" };
  }
  const model = state.profileModels[profileName] || profileName;
  if (state.installedByName[model]) return { label: "pulled", cls: "on" };
  const base = model.split(":")[0];
  const hit = Object.keys(state.installedByName).some((name) => name === model || name.split(":")[0] === base);
  return hit ? { label: "variant pulled", cls: "tight" } : { label: "not pulled", cls: "off" };
}

// A profile is hidden by default when its model isn't actually on the machine:
// an Ollama profile whose model isn't pulled, or a llama.cpp profile whose
// GGUF file the server reports missing.
function profileIsUnpulled(name) {
  const p = state.profileData[name] || {};
  if ((p.backend || "ollama") === "llamacpp") return p.model_file_exists === false;
  if (!state.installedLoaded) return false;
  return installedStatusForProfile(name).label === "not pulled";
}

function renderBenchmarkProfileChips() {
  const body = $("#bench-profile-chips");
  if (!body) return;
  if (!state.profiles.length) {
    body.innerHTML = `<div class="empty-state">No profiles yet. Pull a model in <b>Setup &amp; Deploy → Get a model</b> and it appears here automatically.</div>`;
    return;
  }
  const currentlySelected = new Set(selectedBenchProfiles());
  if (!state.benchmarkSelectedProfiles.length) {
    if (currentlySelected.size) {
      state.benchmarkSelectedProfiles = Array.from(currentlySelected);
    } else if (state.defaultProfile) {
      state.benchmarkSelectedProfiles = [state.defaultProfile];
      currentlySelected.add(state.defaultProfile);
    }
  }
  const filter = ($("#bench-profile-filter")?.value || "").trim().toLowerCase();
  const matchesFilter = (name) => {
    const model = state.profileModels[name] || name;
    return !filter || name.toLowerCase().includes(filter) || model.toLowerCase().includes(filter);
  };
  // Not-pulled profiles stay hidden unless revealed (or already selected —
  // never hide something the user has checked).
  const candidates = state.profiles.filter(matchesFilter);
  const hidden = state.benchShowUnpulled
    ? []
    : candidates.filter((name) => profileIsUnpulled(name) && !currentlySelected.has(name));
  const visibleProfiles = candidates.filter((name) => !hidden.includes(name));
  const toggleHtml = hidden.length || state.benchShowUnpulled
    ? `<button class="link-btn" id="bench-toggle-unpulled" type="button">${
        state.benchShowUnpulled
          ? "Hide profiles without a pulled model"
          : `Show ${hidden.length} hidden (model not pulled)`
      }</button>`
    : "";
  if (!visibleProfiles.length) {
    body.innerHTML = filter
      ? `<div class="empty-state">No profiles match this filter.${toggleHtml ? ` ${toggleHtml}` : ""}</div>`
      : `<div class="empty-state">No pulled models to benchmark yet. Pull one in <b>Setup &amp; Deploy</b> first.${toggleHtml ? ` ${toggleHtml}` : ""}</div>`;
    wireUnpulledToggle(body);
    return;
  }
  body.innerHTML = visibleProfiles
    .map((name) => {
      const p = state.profileData[name] || {};
      const model = state.profileModels[name] || name;
      const status = installedStatusForProfile(name);
      const checked = currentlySelected.has(name) ? " checked" : "";
      const backend = p.backend || "ollama";
      return `<label class="profile-chip-card${checked ? " selected" : ""}">
        <input type="checkbox" value="${esc(name)}"${checked} />
        <span class="profile-chip-main">
          <b title="${esc(name)}">${esc(name)}</b>
          <span class="profile-chip-model" title="${esc(model)}">${esc(model)}</span>
        </span>
        <span class="profile-chip-badges">
          <span class="badge">${esc(backend)}</span>
          <span class="badge ${esc(status.cls)}">${esc(status.label)}</span>
        </span>
      </label>`;
    })
    .join("") + (toggleHtml ? `<div class="chip-grid-footer">${toggleHtml}</div>` : "");
  $$('input[type="checkbox"]', body).forEach((input) => {
    input.addEventListener("change", () => {
      const selected = new Set(state.benchmarkSelectedProfiles);
      if (input.checked) selected.add(input.value);
      else selected.delete(input.value);
      state.benchmarkSelectedProfiles = Array.from(selected);
      input.closest(".profile-chip-card")?.classList.toggle("selected", input.checked);
      const first = selectedBenchProfiles()[0];
      if (first) $("#bench-profile-select").value = first;
      updateBenchmarkSummary();
    });
  });
  wireUnpulledToggle(body);
}

function wireUnpulledToggle(root) {
  $("#bench-toggle-unpulled", root)?.addEventListener("click", () => {
    state.benchShowUnpulled = !state.benchShowUnpulled;
    renderBenchmarkProfileChips();
  });
}

function currentQuestionSetInfo() {
  const raw = $("#qs-editor")?.value?.trim() || "";
  if (!raw) {
    const count = state.testBenchInfo?.test_count;
    const cats = Object.keys(state.testBenchInfo?.categories || {}).length;
    return {
      questions: null,
      name: "Built-in LocalDeploy bench",
      hash: "builtin",
      meta: count ? `${count} tests${cats ? ` · ${cats} categories` : ""}` : "Built-in suite",
    };
  }
  const parsed = JSON.parse(raw);
  const count = Array.isArray(parsed.questions) ? parsed.questions.length : 0;
  const cats = new Set((parsed.questions || []).map((q) => q.category || "?")).size;
  const validation = state.questionSetValidation?.valid ? "validated" : "not validated";
  return {
    questions: parsed,
    name: "Custom JSON question set",
    hash: hashText(raw),
    meta: `${count} question${count === 1 ? "" : "s"}${cats ? ` · ${cats} categories` : ""} · ${validation}`,
  };
}

function updateBenchmarkSummary() {
  const profiles = selectedBenchProfiles();
  const setName = $("#bench-set-name");
  const setMeta = $("#bench-set-meta");
  try {
    const q = currentQuestionSetInfo();
    if (setName) setName.textContent = q.name;
    if (setMeta) setMeta.textContent = q.meta;
  } catch {
    if (setName) setName.textContent = "Custom JSON question set";
    if (setMeta) setMeta.textContent = "Invalid JSON until validated.";
  }
  const selected = $("#bench-selected-count");
  if (selected) selected.textContent = `${profiles.length} profile${profiles.length === 1 ? "" : "s"}`;
  const history = $("#bench-history-count");
  if (history) history.textContent = `${state.benchmarkRuns.length} run${state.benchmarkRuns.length === 1 ? "" : "s"}`;
  const exportSelected = $("#btn-export-selected");
  if (exportSelected) exportSelected.disabled = state.selectedRunIds.length === 0;
}

// ---------------------------------------------------------------------------
// Tab 1 — Hardware
// ---------------------------------------------------------------------------
async function checkHardware() {
  const btn = $("#btn-hardware");
  busy(btn, true);
  $("#hardware-body").innerHTML = skeletonHtml(3);
  try {
    const hw = await getJSON("/system/hardware");
    updateHwChip(hw);
    syncHardwareState(hw);
    const body = $("#hardware-body");
    if (!hw.gpu_available) {
      state.vramTotalMb = null;
      state.vramFreeMb = null;
      state.lastHardware = { gpu: null, vram_total_mb: null, vram_free_mb: null, system: hw.system };
      body.innerHTML = `<div class="muted" style="margin-bottom:.5rem">${esc(hw.message || "No GPU detected.")}</div>
        <div class="kv">${cpuRamRows(hw.system)}</div>`;
      wireHardwareActions(body);
      updateVramBudgetUI();
      return;
    }
    const g = hw.gpus?.[0];
    if (!g) {
      body.innerHTML = `<div class="muted">GPU reported but no details available.</div>`;
      wireHardwareActions(body);
      return;
    }
    state.lastHardware = {
      gpu: g.name,
      vram_total_mb: g.vram_total_mb,
      vram_free_mb: g.vram_free_mb,
      system: hw.system,
    };
    // Apple Silicon (Metal) has no separate VRAM — show the unified-memory note
    // and any hardware message instead of "? total · ? free".
    const vramLine = g.unified_memory
      ? `Unified memory (shared with system RAM)`
      : `${fmtMb(g.vram_total_mb)} total · ${fmtMb(g.vram_free_mb)} free · ${fmtMb(g.vram_used_mb)} used`;
    const note = hw.message
      ? `<div class="muted small" style="margin-bottom:.5rem">${esc(hw.message)}</div>`
      : "";
    body.innerHTML = `${note}<div class="kv">
      <span class="k">GPU</span><span>${esc(g.name)}</span>
      <span class="k">VRAM</span><span>${vramLine}</span>
      <span class="k">Driver</span><span>${esc(g.driver_version ?? "?")}</span>
      ${cpuRamRows(hw.system)}
    </div>`;
    wireHardwareActions(body);
  } catch (err) {
    toast(`Hardware check failed: ${err.message}`, "error");
  } finally {
    busy(btn, false);
    updateProgressRail();
  }
}

function wireHardwareActions(root) {
  $$(".install-psutil-btn", root).forEach((btn) => {
    btn.addEventListener("click", () => installPsutil(btn));
  });
}

async function installPsutil(btn) {
  busy(btn, true);
  try {
    const res = await postJSON("/system/install-psutil", {});
    if (!res.success) throw new Error(res.error || "install failed");
    toast(res.message || "psutil installed.", "success");
    if (res.hardware) {
      updateHwChip(res.hardware);
      syncHardwareState(res.hardware);
    }
    await checkHardware();
  } catch (err) {
    toast(`psutil install failed: ${err.message}`, "error");
  } finally {
    busy(btn, false);
  }
}

function fmtMb(mb) {
  if (mb == null) return "?";
  return mb >= 1024 ? `${(mb / 1024).toFixed(1)} GB` : `${mb} MB`;
}

function pct(value, total) {
  if (!value || !total) return 0;
  return Math.max(0, Math.min(100, (value / total) * 100));
}

function vramBarHtml(usedMb, totalMb, label) {
  if (!usedMb || !totalMb) return "";
  const width = pct(usedMb, totalMb);
  const cls = width > 92 ? "danger" : width > 78 ? "warn" : "ok";
  return `<div class="mini-meter" title="${esc(label || "")}">
    <div class="mini-meter-fill ${cls}" style="width:${width.toFixed(1)}%"></div>
  </div>`;
}

function formatExpires(expiresAt) {
  if (!expiresAt) return "No keep-alive expiry reported";
  const normalized = String(expiresAt).replace(/\.(\d{3})\d+/, ".$1");
  const t = new Date(normalized).getTime();
  if (!Number.isFinite(t)) return `Expires ${expiresAt}`;
  const delta = Math.round((t - Date.now()) / 1000);
  if (delta <= 0) return "Expiry reached; unload may be pending";
  if (delta < 90) return `Expires in ${delta}s`;
  const mins = Math.round(delta / 60);
  if (mins < 90) return `Expires in ${mins}m`;
  return `Expires in ${(mins / 60).toFixed(1)}h`;
}

// ---------------------------------------------------------------------------
// Tab 1 — Status
// ---------------------------------------------------------------------------
async function refreshStatus() {
  const btn = $("#btn-status");
  busy(btn, true);
  $("#status-body").innerHTML = skeletonHtml(2);
  try {
    const s = await getJSON("/system/status");
    state.servedModels = s.served_models || [];
    state.runningDetails = s.ollama?.running || [];
    if (s.hardware) {
      syncHardwareState(s.hardware);
      state.lastHardware = {
        gpu: s.hardware.gpus?.[0]?.name ?? null,
        vram_total_mb: s.hardware.gpus?.[0]?.vram_total_mb ?? null,
        vram_free_mb: s.hardware.gpus?.[0]?.vram_free_mb ?? null,
        system: s.hardware.system,
      };
    }
    // Record each running model's *measured* placement so a benchmark can tag
    // its report card with the device the model actually runs on (not a guess).
    state.runningPlacements = {};
    state.runningDetails.forEach((m) => {
      if (m.name) state.runningPlacements[m.name] = { placement: m.placement, gpu_percent: m.gpu_percent };
    });
    const body = $("#status-body");
    const reachable = s.ollama?.reachable
      ? `<span class="badge on">Ollama online</span>`
      : `<span class="badge off">Ollama offline</span>`;
    let served;
    if (state.servedModels.length) {
      served = s.ollama.running
        .map((m) => {
          const place =
            m.placement === "Split"
              ? `<span class="badge split">${esc(m.gpu_percent)}% GPU</span>`
              : m.placement
                ? `<span class="badge ${m.placement === "GPU" ? "fits" : "cpu"}">${esc(m.placement)}</span>`
                : "";
          const total = state.vramTotalMb;
          const vramMb = m.size_vram_mb ?? (m.size_vram ? Math.round(m.size_vram / 1_000_000) : null);
          const diskMb = m.size_mb ?? (m.size ? Math.round(m.size / 1_000_000) : null);
          const usedLabel = total && vramMb ? `${fmtMb(vramMb)} / ${fmtMb(total)} GPU VRAM` : `VRAM ${fmtMb(vramMb)}`;
          const activity = m.activity === "loaded" ? "Loaded / warm" : esc(m.activity || "Loaded");
          return `<div class="running-card" data-model="${esc(m.name)}">
            <div class="running-top">
              <div>
                <div class="model-title">${esc(m.name)}</div>
                <div class="muted small">${esc(activity)} · ${esc(formatExpires(m.expires_at))}</div>
              </div>
              <div class="row gap">
                ${place}
                <button class="btn danger compact kill-model-btn" title="Unload this model from memory/VRAM">Unload</button>
              </div>
            </div>
            ${vramBarHtml(vramMb, total, usedLabel)}
            <div class="model-meta-grid">
              <span>VRAM</span><b>${esc(usedLabel)}</b>
              <span>Model size</span><b>${fmtMb(diskMb)}</b>
              <span>GPU residency</span><b>${m.gpu_percent != null ? `${esc(m.gpu_percent)}%` : "?"}</b>
            </div>
            <div class="muted small">${esc(m.activity_note || "Ollama keeps this model warm until the keep-alive expires.")}</div>
          </div>`;
        })
        .join("");
    } else {
      served = `<div class="muted">No model is currently loaded. Deploy a profile below; unload controls appear here after Ollama lists a loaded model.</div>`;
    }
    body.innerHTML = `<div style="margin-bottom:.5rem">${reachable}</div>${served}`;
    $$(".kill-model-btn", body).forEach((b) =>
      b.addEventListener("click", () => killRunningModel(b.closest(".running-card").dataset.model, b))
    );
  } catch (err) {
    toast(`Status failed: ${err.message}`, "error");
  } finally {
    busy(btn, false);
    updateProgressRail();
  }
}

async function killRunningModel(name, btn) {
  if (!name) return;
  busy(btn, true);
  try {
    const res = await postJSON("/models/stop", { model: name });
    if (res.success) {
      toast(res.message || `Unloaded ${name}.`, "success");
      await refreshLiveModelState(true);
    } else {
      toast(res.error || res.message || "Could not unload model.", "error");
    }
  } catch (err) {
    toast(`Unload failed: ${err.message}`, "error");
  } finally {
    busy(btn, false);
  }
}

// Map a profile's running model to the device it's actually placed on, by
// matching the profile's model_id against the live placement map. Returns
// "gpu" | "cpu" | "split" | null (null = not loaded / placement unknown).
function detectDevice(profileName) {
  const modelId = String(state.profileModels[profileName] || profileName);
  const base = modelId.split(":")[0];
  for (const [name, info] of Object.entries(state.runningPlacements || {})) {
    if (name === modelId || name.split(":")[0] === base) {
      return info.placement ? info.placement.toLowerCase() : null;
    }
  }
  return null;
}

// ---------------------------------------------------------------------------
// Tab 1 — Deploy / unload / replace
// ---------------------------------------------------------------------------
function showServeResult(res) {
  const node = $("#serve-result");
  const ok = res && res.success;
  node.className = `result ${ok ? "ok" : "err"}`;
  node.textContent = (res && (res.message || res.error)) || (ok ? "Done." : "Failed.");
}

// Label the warm-up wait so loading a large model (slow on CPU) shows a live
// elapsed counter instead of an apparently-frozen button.
function warmupLabel(device) {
  return device === "cpu"
    ? "Loading on CPU (large models can take a minute)"
    : "Loading model into memory";
}

async function serveModel() {
  const btn = $("#btn-serve");
  const device = $("#serve-device").value;
  busy(btn, true);
  const node = $("#serve-result");
  node.className = "result";
  const stop = startElapsed(node, warmupLabel(device));
  try {
    const res = await postJSON("/models/serve", {
      profile: $("#profile-select").value,
      keep_alive: $("#keep-alive").value.trim() || "60m",
      device,
    });
    stop();
    showServeResult(res);
    await refreshLiveModelState(true);
  } catch (err) {
    stop();
    node.className = "result err";
    node.textContent = `Deploy failed: ${err.message}`;
    toast(`Deploy failed: ${err.message}`, "error");
  } finally {
    busy(btn, false);
  }
}

async function switchModel() {
  const btn = $("#btn-switch");
  const device = $("#serve-device").value;
  busy(btn, true);
  const node = $("#serve-result");
  node.className = "result";
  const stop = startElapsed(node, warmupLabel(device));
  try {
    const res = await postJSON("/models/switch", {
      to_profile: $("#profile-select").value,
      from_model: state.servedModels[0] || null,
      keep_alive: $("#keep-alive").value.trim() || "60m",
      device,
    });
    stop();
    showServeResult(res);
    await refreshLiveModelState(true);
  } catch (err) {
    stop();
    node.className = "result err";
    node.textContent = `Deploy and replace failed: ${err.message}`;
    toast(`Deploy and replace failed: ${err.message}`, "error");
  } finally {
    busy(btn, false);
  }
}

// ---------------------------------------------------------------------------
// Tab 1 — Installed models + fit check
// ---------------------------------------------------------------------------
// Tiered fit badge (Phase 3): green = comfortable, yellow = tight / CPU-only
// (soft warnings), red = won't fit anywhere (hard). Falls back to the coarse
// verdict for older responses without a severity.
function fitBadge(res) {
  const sevCls = { ok: "fits", soft: "tight", hard: "wont", unknown: "unknown" };
  const cls = sevCls[res.severity] || (res.verdict === "FITS" ? "fits" : res.verdict === "WONT_FIT" ? "wont" : "unknown");
  const label = res.tier ? res.tier.replace(/_/g, " ") : res.verdict || "?";
  return `<span class="badge ${cls}" title="${esc(res.headline || "")}">${esc(label)}</span>`;
}

function parseParamSize(value) {
  const m = String(value || "").match(/(\d+(?:\.\d+)?)\s*B/i);
  return m ? Number(m[1]) : null;
}

function fitRequestForModel(model, details = {}, sizeBytes = null) {
  return {
    model_id: model,
    params_b: parseParamSize(details.parameter_size),
    quant: details.quantization_level || null,
    context: details.context_length ? Math.min(Number(details.context_length), 8192) : null,
    size_bytes: sizeBytes || null,
    free_vram_mb: targetVram(),
  };
}

function fitMeterHtml(res) {
  const req = res?.estimate_gb?.required;
  const budget = targetVram();
  if (req == null || !budget) return "";
  const budgetGb = budget / 1024;
  const width = Math.max(0, Math.min(130, (req / budgetGb) * 100));
  const cls = res.severity === "hard" ? "danger" : res.severity === "soft" ? "warn" : "ok";
  return `<div class="mini-meter" title="Estimated ${req} GB against ${budgetGb.toFixed(1)} GB budget">
    <div class="mini-meter-fill ${cls}" style="width:${Math.min(width, 100).toFixed(1)}%"></div>
  </div>`;
}

async function refreshInstalled() {
  const btn = $("#btn-installed");
  busy(btn, true);
  const body = $("#installed-body");
  body.innerHTML = skeletonHtml(3);
  try {
    const data = await getJSON("/registry/installed");
    if (!data.success) {
      state.installedByName = {};
      state.installedLoaded = false; // unknown, so nothing gets hidden as "not pulled"
      renderBenchmarkProfileChips();
      renderProfileSelectOptions();
      body.innerHTML = `<div class="muted">${esc(data.error || "Ollama unreachable.")}</div>`;
      return;
    }
    state.installedByName = {};
    state.installedLoaded = true;
    if (!data.installed.length) {
      renderBenchmarkProfileChips();
      renderProfileSelectOptions();
      body.innerHTML = `<div class="muted">No models pulled yet — grab one from <b>Get a model</b> above.</div>`;
      return;
    }
    data.installed.forEach((m) => {
      if (m.name) state.installedByName[m.name] = m;
    });
    renderBenchmarkProfileChips();
    renderProfileSelectOptions();
    body.innerHTML =
      `<div class="mlist">` +
      data.installed
        .map((m) => {
          const size = m.size ? fmtMb(Math.round(m.size / 1e6)) : "";
          const d = m.details || {};
          const quant = d.quantization_level
            ? `<span class="badge" style="font-size:.72rem">${esc(d.quantization_level)}</span>`
            : "";
          const params = d.parameter_size ? `<span class="meta">${esc(d.parameter_size)}</span>` : "";
          const date = m.modified_at ? `<span class="meta">${esc(m.modified_at.slice(0, 10))}</span>` : "";
          const loaded = state.servedModels.includes(m.name) ? `<span class="badge on">loaded</span>` : "";
          const loadedHint = loaded ? `<span class="meta">Unload from Served model card</span>` : "";
          return `<div class="mrow model-row" data-model="${esc(m.name)}">
            <div class="model-row-main">
              <span class="name">${esc(m.name)}</span>
              <span class="model-row-meta">${params}${quant}${date}<span class="meta">${esc(size)}</span>${loaded}${loadedHint}</span>
              <span class="fit"></span>
            </div>
            <span class="spacer"></span>
            <button class="btn primary start-installed-btn">Deploy</button>
            <button class="btn fit-btn">Fit check</button>
            <button class="btn edit-tuning-btn" title="Edit this model's run profile (context, KV cache, GPU layers…)">Edit tuning</button>
            <button class="btn danger del-btn" title="Delete from disk">Delete</button>
          </div>`;
        })
        .join("") +
      `</div>`;
    $$(".fit-btn", body).forEach((b) =>
      b.addEventListener("click", () => fitCheckRow(b.closest(".mrow")))
    );
    $$(".del-btn", body).forEach((b) =>
      b.addEventListener("click", () => deleteModel(b.closest(".mrow").dataset.model, b))
    );
    $$(".start-installed-btn", body).forEach((b) =>
      b.addEventListener("click", () => startInstalledModel(b.closest(".mrow").dataset.model, b))
    );
    $$(".edit-tuning-btn", body).forEach((b) =>
      b.addEventListener("click", () => openTuningEditor(b.closest(".mrow").dataset.model))
    );
    // Auto-run the fit check for each row so warnings appear without a click.
    $$(".mrow", body).forEach((row) => fitCheckRow(row));
  } catch (err) {
    toast(`Installed list failed: ${err.message}`, "error");
  } finally {
    busy(btn, false);
    updateProgressRail();
  }
}

async function fitCheckRow(row) {
  const model = row.dataset.model;
  const installed = state.installedByName[model] || {};
  const details = installed.details || {};
  const slot = $(".fit", row);
  const btn = $(".fit-btn", row);
  busy(btn, true);
  slot.textContent = "…";
  try {
    const res = await postJSON("/system/fit-check", fitRequestForModel(model, details, installed.size));
    if (res.verdict) {
      const req = res.estimate_gb?.required;
      const free = res.free_vram_gb;
      const detail = req != null ? ` ~${req} GB${free != null ? ` / ${free} GB budget` : ""}` : "";
      slot.innerHTML = `<div class="fit-summary">${fitBadge(res)}<span class="meta">${esc(detail)}</span></div>${fitMeterHtml(res)}`;
    } else {
      slot.innerHTML = `<span class="muted">${esc(res.message || "n/a")}</span>`;
    }
  } catch (err) {
    slot.innerHTML = `<span class="muted">error</span>`;
    toast(`Fit check failed: ${err.message}`, "error");
  } finally {
    busy(btn, false);
  }
}

async function startInstalledModel(name, btn) {
  if (!name) return;
  busy(btn, true);
  const node = $("#serve-result");
  node.className = "result";
  const stop = startElapsed(node, "Loading model into memory");
  try {
    const res = await postJSON("/models/serve", {
      model: name,
      keep_alive: $("#keep-alive").value.trim() || "60m",
      device: $("#serve-device").value,
    });
    stop();
    showServeResult(res);
    await refreshLiveModelState(true);
  } catch (err) {
    stop();
    toast(`Deploy failed: ${err.message}`, "error");
  } finally {
    busy(btn, false);
  }
}

function findInstalledModel(modelId) {
  if (!modelId) return null;
  if (state.installedByName[modelId]) return state.installedByName[modelId];
  // Match case-insensitively and tolerate the differences between what the user
  // asked to pull and how Ollama stores it: an implicit ":latest" tag, and
  // "hf.co/Org/Repo-GGUF" landing as a lowercased name. Fall back to comparing
  // the last path segment so an HF GGUF pull still resolves to its installed row.
  const want = String(modelId).toLowerCase();
  const wantNoTag = want.split(":")[0];
  const tail = (s) => s.split("/").pop();
  const names = Object.values(state.installedByName);
  const norm = (m) => String(m.name || "").toLowerCase();
  return (
    names.find((m) => norm(m) === want) ||
    names.find((m) => norm(m) === `${want}:latest`) ||
    names.find((m) => norm(m).split(":")[0] === wantNoTag) ||
    names.find((m) => tail(norm(m).split(":")[0]) === tail(wantNoTag)) ||
    null
  );
}

// The actual installed key for a model the user asked to pull (for scroll/flip
// targeting), or the original string if it isn't installed (yet).
function resolveInstalledName(modelId) {
  return findInstalledModel(modelId)?.name || modelId;
}

function fitMatchesFilter(res, filter) {
  if (filter === "gpu") return res?.success && res?.verdict === "FITS";
  if (filter === "runnable") return res?.success && res?.severity !== "hard";
  return true;
}

function severityRank(sev) {
  return { ok: 0, soft: 1, unknown: 2, hard: 3 }[sev] ?? 4;
}

async function scanConfiguredFits() {
  const btn = $("#btn-fit-profiles");
  const body = $("#fit-finder-body");
  const filter = $("#fit-filter").value;
  busy(btn, true);
  body.innerHTML = `<span class="spin-inline"></span> Scanning saved run profiles against ${esc(fmtMb(targetVram()))}...`;
  try {
    if (!Object.keys(state.installedByName).length) {
      await refreshInstalled();
    }
    const rows = await Promise.all(
      state.profiles.map(async (profile) => {
        const p = state.profileData[profile] || {};
        const modelId = p.model_id || profile;
        const installed = findInstalledModel(modelId);
        const fit = await postJSON("/system/fit-check", {
          profile,
          size_bytes: installed?.size || null,
          free_vram_mb: targetVram(),
        });
        return { profile, p, modelId, installed, fit };
      })
    );
    const filtered = rows
      .filter((r) => fitMatchesFilter(r.fit, filter))
      .sort((a, b) => {
        const s = severityRank(a.fit?.severity) - severityRank(b.fit?.severity);
        if (s !== 0) return s;
        return (a.fit?.estimate_gb?.required ?? 999) - (b.fit?.estimate_gb?.required ?? 999);
      });
    if (!filtered.length) {
      body.innerHTML = `<div class="muted">No saved run profiles match this filter at ${esc(fmtMb(targetVram()))}.</div>`;
      return;
    }
    body.innerHTML = `<div class="fit-grid">` + filtered.map(renderProfileFitCard).join("") + `</div>`;
    $$(".select-profile-btn", body).forEach((b) =>
      b.addEventListener("click", () => selectProfile(b.dataset.profile))
    );
    $$(".fit-start-profile-btn", body).forEach((b) =>
      b.addEventListener("click", () => startProfile(b.dataset.profile, b))
    );
    $$(".fit-pull-btn", body).forEach((b) =>
      b.addEventListener("click", () => pullModel(b.dataset.model, b))
    );
    $$(".toggle-enabled-btn", body).forEach((b) =>
      b.addEventListener("click", () => toggleProfileEnabled(b.dataset.profile, b.dataset.enabled === "true", b))
    );
    $$(".edit-profile-btn", body).forEach((b) =>
      b.addEventListener("click", () => openTuningEditor(b.dataset.model, b.dataset.profile))
    );
    $$(".delete-profile-btn", body).forEach((b) =>
      b.addEventListener("click", () => deleteProfile(b.dataset.profile, b))
    );
  } catch (err) {
    body.innerHTML = `<div class="muted">Scan failed.</div>`;
    toast(`Fit scan failed: ${err.message}`, "error");
  } finally {
    busy(btn, false);
  }
}

// One-click cleanup: delete every Ollama profile whose model isn't pulled.
// Keeps config.json an honest mirror of what's actually on the machine.
async function cleanOrphanProfiles(btn) {
  if (!state.installedLoaded) await refreshInstalled();
  const orphans = state.profiles.filter((name) => {
    const p = state.profileData[name] || {};
    if ((p.backend || "ollama") === "llamacpp") return p.model_file_exists === false;
    if (!state.installedLoaded) return false;
    return !findInstalledModel(p.model_id || name);
  });
  if (!orphans.length) {
    toast("No orphan profiles — every profile's model is pulled.", "success");
    return;
  }
  const preview = orphans.slice(0, 12).join("\n  ");
  const more = orphans.length > 12 ? `\n  …and ${orphans.length - 12} more` : "";
  if (!window.confirm(`Remove ${orphans.length} profile(s) whose model is not pulled?\n\n  ${preview}${more}\n\n(Models on disk are not touched; profiles are recreated automatically if you pull the model again.)`)) return;
  busy(btn, true);
  let removed = 0;
  try {
    for (const profile of orphans) {
      const res = await postJSON("/profiles/delete", { profile });
      if (res.success) removed += 1;
      else toast(res.error || `Could not delete ${profile}.`, "error");
    }
    toast(`Removed ${removed} orphan profile(s).`, "success");
    await loadProfiles();
    if (!$("#fit-finder-body").textContent.includes("have not been scanned")) {
      await scanConfiguredFits();
    }
  } catch (err) {
    toast(`Cleanup failed: ${err.message}`, "error");
  } finally {
    busy(btn, false);
  }
}

async function deleteProfile(profile, btn) {
  if (!profile) return;
  if (!window.confirm(`Remove the profile "${profile}" from config.json? (This does not delete the model from disk.)`)) return;
  busy(btn, true);
  try {
    const res = await postJSON("/profiles/delete", { profile });
    if (!res.success) {
      toast(res.error || "Could not delete profile.", "error");
      return;
    }
    toast(`Removed profile ${profile}.`, "success");
    await loadProfiles();
    await scanConfiguredFits();
  } catch (err) {
    toast(`Delete failed: ${err.message}`, "error");
  } finally {
    busy(btn, false);
  }
}

// ---------------------------------------------------------------------------
// Tab 1 — Tuning editor (edit a model's run profile: context, KV cache, GPU…)
// ---------------------------------------------------------------------------
// The fields a user can tune, with input types. `model_id` is upserted so the
// profile is created on the fly for models pulled before auto-create existed.
const TUNING_FIELDS = [
  { key: "description", label: "Description", type: "text" },
  { key: "supports_vision", label: "Vision (multimodal)", type: "checkbox" },
  { key: "think", label: "Thinking mode", type: "checkbox" },
  { key: "max_images", label: "Max images per request", type: "number" },
  { key: "context_limit", label: "Context limit", type: "number" },
  { key: "safe_context_limit", label: "Safe context limit", type: "number" },
  { key: "max_output_tokens", label: "Max output tokens", type: "number" },
  { key: "temperature", label: "Temperature", type: "number", step: "0.05" },
  { key: "top_p", label: "Top-p", type: "number", step: "0.05" },
  { key: "repeat_penalty", label: "Repeat penalty", type: "number", step: "0.05" },
  { key: "timeout_seconds", label: "Timeout (s)", type: "number" },
  { key: "flash_attention", label: "Flash attention (llama.cpp)", type: "checkbox" },
  { key: "kv_cache_type_k", label: "KV cache type K (e.g. q8_0)", type: "text" },
  { key: "kv_cache_type_v", label: "KV cache type V (e.g. q8_0)", type: "text" },
  { key: "gpu_layers", label: "GPU layers (number or 'all')", type: "text" },
];

function profileForModel(modelId, profileName) {
  if (profileName && state.profileData[profileName]) return { name: profileName, data: state.profileData[profileName] };
  const hit = Object.entries(state.profileData).find(([, v]) => v.model_id === modelId);
  return hit ? { name: hit[0], data: hit[1] } : { name: null, data: {} };
}

function openTuningEditor(modelId, profileName) {
  const { name, data } = profileForModel(modelId, profileName);
  const heading = name || modelId;
  const fieldsHtml = TUNING_FIELDS.map((f) => {
    const val = data[f.key];
    if (f.type === "checkbox") {
      return `<label class="check tuning-field"><input type="checkbox" data-key="${f.key}" ${val ? "checked" : ""} /><span>${esc(f.label)}</span></label>`;
    }
    const step = f.step ? ` step="${f.step}"` : "";
    const v = val == null ? "" : esc(String(val));
    return `<label class="field tuning-field"><span>${esc(f.label)}</span><input type="${f.type}"${step} data-key="${f.key}" value="${v}" /></label>`;
  }).join("");
  const overlay = document.createElement("div");
  overlay.className = "modal-overlay";
  overlay.innerHTML = `<div class="modal-card" role="dialog" aria-modal="true">
      <div class="card-head">
        <div><h3 class="sub">Edit tuning</h3><div class="muted small">Model: <code>${esc(modelId)}</code>${name ? ` · profile <code>${esc(name)}</code>` : " · a profile will be created"}</div></div>
        <button class="btn compact modal-close">✕</button>
      </div>
      <div class="tuning-grid">${fieldsHtml}</div>
      <div class="row gap" style="justify-content:flex-end;margin-top:0.75rem">
        <button class="btn modal-close">Cancel</button>
        <button class="btn primary modal-save">Save profile</button>
      </div>
    </div>`;
  document.body.appendChild(overlay);
  const close = () => overlay.remove();
  overlay.addEventListener("click", (e) => { if (e.target === overlay) close(); });
  $$(".modal-close", overlay).forEach((b) => b.addEventListener("click", close));
  $(".modal-save", overlay).addEventListener("click", async (e) => {
    const saveBtn = e.currentTarget;
    const fields = {};
    $$("input[data-key]", overlay).forEach((inp) => {
      const key = inp.dataset.key;
      if (inp.type === "checkbox") fields[key] = inp.checked;
      else if (inp.type === "number") fields[key] = inp.value.trim() === "" ? null : Number(inp.value);
      else fields[key] = inp.value.trim() === "" ? null : inp.value.trim();
    });
    busy(saveBtn, true);
    try {
      const payload = name ? { profile: name, fields } : { model_id: modelId, fields };
      const res = await postJSON("/profiles/upsert", payload);
      if (!res.success) {
        toast(res.error || "Could not save profile.", "error");
        return;
      }
      toast(`Saved profile ${res.profile}.`, "success");
      close();
      await loadProfiles();
    } catch (err) {
      toast(`Save failed: ${err.message}`, "error");
    } finally {
      busy(saveBtn, false);
    }
  });
}

function renderProfileFitCard(row) {
  const { profile, p, modelId, installed, fit } = row;
  const req = fit?.estimate_gb?.required;
  const margin = fit?.margin_gb;
  const installedBadge = installed ? `<span class="badge on">installed</span>` : `<span class="badge off">not pulled</span>`;
  const vision = p.supports_vision ? `<span class="badge">vision</span>` : "";
  const backend = p.backend ? `<span class="badge">${esc(p.backend)}</span>` : "";
  const enabledNow = p.enabled !== false;
  const enabledBadge = enabledNow ? `<span class="badge on">enabled</span>` : `<span class="badge off">disabled</span>`;
  const toggleBtn = `<button class="btn compact toggle-enabled-btn" data-profile="${esc(profile)}" data-enabled="${enabledNow ? "false" : "true"}" title="${enabledNow ? "Disable" : "Enable"} this profile for Auto-pick">${enabledNow ? "Disable" : "Enable"}</button>`;
  const action = installed
    ? `<button class="btn fit-start-profile-btn" data-profile="${esc(profile)}">Deploy</button>`
    : `<button class="btn fit-pull-btn" data-model="${esc(modelId)}">Pull</button>`;
  const detail = [
    req != null ? `needs ~${req} GB` : null,
    margin != null ? `${margin >= 0 ? "+" : ""}${margin} GB headroom` : null,
    p.safe_context_limit ? `${p.safe_context_limit} ctx` : null,
    p.max_output_tokens ? `${p.max_output_tokens} max output` : null,
  ].filter(Boolean).join(" · ");
  const desc = p.description ? `<div class="muted small">${esc(p.description)}</div>` : "";
  return `<div class="fit-card">
    <div class="running-top">
      <div>
        <div class="model-title">${esc(profile)}</div>
        <div class="muted small">Model: <code>${esc(modelId)}</code></div>
      </div>
      ${fitBadge(fit || {})}
    </div>
    ${fitMeterHtml(fit)}
    ${desc}
    <div class="muted small">${esc(fit?.headline || "No fit estimate available.")}</div>
    <div class="muted small">${esc(detail)}</div>
    <div class="row gap wrap fit-card-actions">
      ${enabledBadge}${installedBadge}${vision}${backend}
      <span class="spacer"></span>
      ${toggleBtn}
      <button class="btn compact edit-profile-btn" data-profile="${esc(profile)}" data-model="${esc(modelId)}" title="Edit tuning">Edit</button>
      <button class="btn compact danger delete-profile-btn" data-profile="${esc(profile)}" title="Remove this profile from config.json">Delete</button>
      <button class="btn select-profile-btn" data-profile="${esc(profile)}">Select</button>
      ${action}
    </div>
  </div>`;
}

async function toggleProfileEnabled(profile, enabled, btn) {
  busy(btn, true);
  try {
    const res = await postJSON("/system/set-enabled", { profile, enabled });
    if (!res.success) {
      toast(res.error || "Could not update profile.", "error");
      return;
    }
    if (state.profileData[profile]) state.profileData[profile].enabled = enabled;
    toast(`${profile} ${enabled ? "enabled" : "disabled"} for Auto-pick.`, "success");
    await scanConfiguredFits();
  } catch (err) {
    toast(`Update failed: ${err.message}`, "error");
  } finally {
    busy(btn, false);
  }
}

function selectProfile(profile) {
  if (!profile) return;
  $("#profile-select").value = profile;
  $("#bench-profile-select").value = profile;
  toast(`Selected ${profile}.`, "success");
}

async function startProfile(profile, btn) {
  if (!profile) return;
  selectProfile(profile);
  busy(btn, true);
  const node = $("#serve-result");
  node.className = "result";
  const stop = startElapsed(node, "Loading model into memory");
  try {
    const res = await postJSON("/models/serve", {
      profile,
      keep_alive: $("#keep-alive").value.trim() || "60m",
      device: $("#serve-device").value,
    });
    stop();
    showServeResult(res);
    await refreshLiveModelState(true);
  } catch (err) {
    stop();
    toast(`Deploy failed: ${err.message}`, "error");
  } finally {
    busy(btn, false);
  }
}

// ---------------------------------------------------------------------------
// Tab 1 — Starter pack (curated first-pull picks for the detected budget)
// ---------------------------------------------------------------------------
function renderStarterCard(c) {
  const installed = !!findInstalledModel(c.pull_name);
  const action = installed
    ? `<span class="badge on">installed</span>`
    : `<button class="btn starter-pull-btn" data-model="${esc(c.pull_name)}">Pull</button>`;
  const vision = c.vision ? `<span class="badge">vision</span>` : "";
  return `<div class="fit-card">
    <div class="running-top">
      <div>
        <div class="model-title">${esc(c.id)}</div>
        <div class="muted small">${esc(c.use_case || "")}</div>
      </div>
      <span class="badge" title="Hand-curated quality rating; 5 = best-in-class for its size class">tier ${esc(c.tier)}/5</span>
    </div>
    <div class="muted small">${esc(c.description || "")}</div>
    <div class="muted small">${esc(c.reasoning || "")}</div>
    <div class="row gap wrap fit-card-actions">
      ${vision}
      <span class="spacer"></span>
      ${action}
    </div>
  </div>`;
}

async function starterPack(source) {
  const btn = source?.currentTarget || source || $("#btn-starter-pack");
  busy(btn, true);
  const body = $("#starter-pack-body");
  body.innerHTML = `<div class="muted"><span class="spin-inline"></span> Finding models that fit your hardware…</div>`;
  try {
    const data = await postJSON("/registry/starter-pack", {
      free_vram_mb: targetVram(),
      margin_gb: 2.0,
      limit: 5,
    });
    if (!data.success || data.budget_gb == null) {
      body.innerHTML = `<div class="muted">${esc(data.message || "Could not determine a fit budget.")}</div>`;
      return;
    }
    const unit = data.budget_source === "vram" ? "VRAM" : "RAM";
    const marginNote = data.margin_relaxed ? "" : ` (after a ${esc(data.margin_gb)} GB safety margin)`;
    const header = `<div class="muted small" style="margin-bottom:0.5rem">Budget: ~${esc(data.budget_gb)} GB usable ${unit}${marginNote} from ~${esc(data.raw_budget_gb)} GB detected.</div>`;
    const note = data.message ? `<div class="muted small">${esc(data.message)}</div>` : "";
    if (!data.candidates.length) {
      body.innerHTML = header + note + `<div class="muted">No curated models fit this budget.</div>`;
      return;
    }
    body.innerHTML = header + note + `<div class="fit-grid">` + data.candidates.map(renderStarterCard).join("") + `</div>`;
    $$(".starter-pull-btn", body).forEach((b) =>
      b.addEventListener("click", () => pullModel(b.dataset.model, b))
    );
  } catch (err) {
    body.innerHTML = `<div class="muted">Starter pack lookup failed.</div>`;
    toast(`Starter pack failed: ${err.message}`, "error");
  } finally {
    busy(btn, false);
  }
}

// ---------------------------------------------------------------------------
// Tab 1 — Hugging Face model search
// ---------------------------------------------------------------------------
function fmtNum(n) {
  if (n == null) return null;
  const value = Number(n);
  if (!Number.isFinite(value)) return null;
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(1)}k`;
  return String(value);
}

function hfStatsHtml(candidate) {
  const downloads = fmtNum(candidate.downloads);
  const likes = fmtNum(candidate.likes);
  const items = [
    downloads == null ? "downloads not reported" : `${downloads} downloads`,
    likes == null ? "likes not reported" : `${likes} likes`,
  ];
  return `<span class="meta hf-stats" title="Hugging Face downloads and likes; the search API does not return a quality rating.">${esc(items.join(" · "))}</span>`;
}

async function checkUpdates(source) {
  const btn = source?.currentTarget || source || $("#btn-hf-search");
  busy(btn, true);
  const body = $("#updates-body");
  body.innerHTML = `<div class="muted">Checking Hugging Face…</div>`;

  // Build request from the search form (Phase 5).
  const searchRaw = ($("#hf-search")?.value || "").trim();
  const limitVal = parseInt($("#hf-limit")?.value || "5", 10);
  const ggufOnly = $("#hf-gguf-only")?.checked !== false;
  const payload = {
    limit: Number.isFinite(limitVal) && limitVal > 0 ? limitVal : 5,
    gguf_only: ggufOnly,
    fit_filter: $("#hf-fit-filter")?.value || "all",
    free_vram_mb: targetVram(),
  };
  if (searchRaw) payload.queries = searchRaw.split(",").map((s) => s.trim()).filter(Boolean);

  try {
    const data = await postJSON("/registry/check-updates", payload);
    if (!data.online && (!data.results || !data.results.length)) {
      body.innerHTML = `<div class="muted">${esc(data.message || "Offline.")}</div>`;
      return;
    }
    const blocks = (data.results || [])
      .map((group) => {
        const rows = (group.candidates || [])
          .map((c) => {
            const flag = c.installed_match ? `<span class="badge on">installed</span>` : "";
            const date = c.last_modified ? `<span class="meta">${esc(c.last_modified.slice(0, 10))}</span>` : "";
            const pull = c.pullable && c.pull_name
              ? `<button class="btn hf-pull-btn" data-model="${esc(c.pull_name)}">Pull</button>`
              : "";
            const fit = c.fit?.success
              ? `<div class="hf-fit">${fitBadge(c.fit)}<span class="meta">~${esc(c.fit.estimate_gb?.required ?? "?")} GB / ${esc(c.fit.free_vram_gb ?? "?")} GB budget</span>${fitMeterHtml(c.fit)}</div>`
              : "";
            return `<div class="mrow">
              <div class="model-row-main">
                <a class="name" href="https://huggingface.co/${esc(c.id)}" target="_blank" rel="noopener">${esc(c.id)}</a>
                <span class="model-row-meta">${date}${hfStatsHtml(c)}${flag}</span>
                ${fit}
              </div>
              <span class="spacer"></span>${pull}
            </div>`;
          })
          .join("");
        return `<h3 class="sub">“${esc(group.query)}”</h3><div class="mlist">${rows || '<div class="muted">none</div>'}</div>`;
      })
      .join("");
    const note = data.online ? "" : `<div class="muted small">${esc(data.message || "")}</div>`;
    body.innerHTML = blocks + note;
    // Wire the per-candidate Pull buttons (GGUF repos via Ollama's hf.co/ shortcut).
    $$(".hf-pull-btn", body).forEach((b) =>
      b.addEventListener("click", () => pullModel(b.dataset.model, b))
    );
  } catch (err) {
    body.innerHTML = `<div class="muted">Check failed.</div>`;
    toast(`Update check failed: ${err.message}`, "error");
  } finally {
    busy(btn, false);
  }
}

// ---------------------------------------------------------------------------
// Tab 1 — Ollama install/start helper (shown when a pull can't reach Ollama)
// ---------------------------------------------------------------------------
function hideOllamaHelp() {
  $("#ollama-help")?.classList.add("hidden");
}

function showOllamaHelp(retryModel, retryBtn, message) {
  const help = $("#ollama-help");
  if (!help) return;
  const retryHtml = retryModel
    ? `<button class="btn primary compact" id="btn-retry-pull">Retry pull</button>`
    : "";
  const detail = message || `Install it (or start it if it's already installed), then ${retryModel ? "retry the pull" : "try pulling a model"}.`;
  help.innerHTML = `<div><strong>Ollama isn't reachable</strong><div class="muted small">${esc(detail)}</div></div>
    <div class="row gap">
      <button class="btn compact" id="btn-install-ollama">Install / start Ollama</button>
      ${retryHtml}
    </div>`;
  help.classList.remove("hidden");
  help.scrollIntoView({ behavior: "smooth", block: "nearest" });
  $("#btn-install-ollama").addEventListener("click", async (e) => {
    const b = e.currentTarget;
    busy(b, true);
    try {
      const res = await postJSON("/system/install-ollama", {});
      toast(res.message || res.error || "Done.", res.success ? "success" : "error");
      if (res.success) hideOllamaHelp();
    } catch (err) {
      toast(`Install failed: ${err.message}`, "error");
    } finally {
      busy(b, false);
    }
  });
  if (retryModel) {
    $("#btn-retry-pull").addEventListener("click", () => {
      hideOllamaHelp();
      pullModel(retryModel, retryBtn);
    });
  }
}

// Flip any rendered Pull button for `model` (starter pack, HF search, saved-
// profile scan) to an "installed" badge without waiting on a full re-render.
function markModelInstalledInUI(model) {
  let selector;
  try {
    selector = `[data-model="${CSS.escape(model)}"]`;
  } catch {
    return;
  }
  $$(`.starter-pull-btn${selector}, .hf-pull-btn${selector}, .fit-pull-btn${selector}`).forEach((b) => {
    const badge = document.createElement("span");
    badge.className = "badge on";
    badge.textContent = "installed";
    b.replaceWith(badge);
  });
}

// ---------------------------------------------------------------------------
// Tab 1 — Pull progress panel (%, speed, ETA, destination, completion)
// ---------------------------------------------------------------------------
function fmtBytes(n) {
  if (n == null || !Number.isFinite(n)) return "";
  if (n >= 1e9) return `${(n / 1e9).toFixed(2)} GB`;
  if (n >= 1e6) return `${(n / 1e6).toFixed(0)} MB`;
  if (n >= 1e3) return `${(n / 1e3).toFixed(0)} KB`;
  return `${n} B`;
}

function fmtDuration(seconds) {
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ${s % 60}s`;
  const h = Math.floor(m / 60);
  return `${h}h ${m % 60}m`;
}

// Update the pull-progress panel. `percent === null` renders an indeterminate bar
// (used while Ollama is doing sizeless work like "pulling manifest"/"verifying").
function setPullProgress({ percent, status, stats }) {
  const bar = $("#pull-progress-bar");
  const pctLabel = $("#pull-progress-percent");
  if (percent === null) {
    bar.classList.add("indeterminate");
    bar.style.width = "100%";
    pctLabel.textContent = "";
  } else if (percent != null) {
    const p = Math.max(0, Math.min(100, percent));
    bar.classList.remove("indeterminate");
    bar.style.width = `${p.toFixed(1)}%`;
    bar.setAttribute("aria-valuenow", String(Math.round(p)));
    pctLabel.textContent = `${Math.round(p)}%`;
  }
  if (status != null) $("#pull-progress-status").textContent = status;
  if (stats != null) $("#pull-progress-stats").textContent = stats;
}

function showPullDone(model) {
  const done = $("#pull-progress-done");
  done.innerHTML = `<div class="pull-done-row">
      <div><strong>✓ Pulled — ${esc(model)} is ready</strong>
        <div class="muted small">A run profile was created for it. Deploy it from Your models, or right here.</div>
      </div>
      <button class="btn primary compact" id="btn-deploy-pulled" data-model="${esc(model)}">Deploy now</button>
    </div>`;
  done.classList.remove("hidden");
  $("#btn-deploy-pulled").addEventListener("click", (e) => {
    const b = e.currentTarget;
    startInstalledModel(resolveInstalledName(b.dataset.model), b);
  });
}

// Scroll to + briefly flash the freshly-pulled model's row so it never looks
// like the pull "vanished" (the row lives lower in the page than the pull panel).
function highlightInstalledModel(model) {
  const name = resolveInstalledName(model);
  if (!name) return;
  let row;
  try {
    row = $(`#installed-body [data-model="${CSS.escape(name)}"]`);
  } catch {
    return;
  }
  if (!row) return;
  row.scrollIntoView({ behavior: "smooth", block: "center" });
  row.classList.add("just-added");
  setTimeout(() => row.classList.remove("just-added"), 2600);
}

// ---------------------------------------------------------------------------
// Tab 1 — Pull (streamed, fit-gated)
// ---------------------------------------------------------------------------
async function pullModel(modelArg, triggerBtn) {
  // Called from the manual Pull button (no args), or a candidate row's own
  // Pull button (model name + that button, so the card shows it's pulling).
  const model = typeof modelArg === "string" && modelArg ? modelArg : $("#pull-model").value.trim();
  if (!model) {
    toast("Enter a model name to pull.", "error");
    return;
  }
  $("#pull-model").value = model;
  const manualBtn = $("#btn-pull");
  const btn = triggerBtn || manualBtn;
  const usesSpinner = btn === manualBtn;
  const cardOriginalHtml = usesSpinner ? null : btn.innerHTML;
  const cancelBtn = $("#btn-pull-cancel");
  const log = $("#pull-log");

  hideOllamaHelp();
  // Reset + reveal the progress panel.
  const panel = $("#pull-progress");
  $("#pull-progress-done").classList.add("hidden");
  $("#pull-progress-done").innerHTML = "";
  $("#pull-progress-title").textContent = model;
  $("#pull-progress-dest").textContent = "";
  setPullProgress({ percent: 0, status: "Starting…", stats: "" });
  panel.classList.remove("hidden");
  panel.scrollIntoView({ behavior: "smooth", block: "nearest" });
  log.textContent = "";
  const append = (line) => {
    log.textContent += line + "\n";
    log.scrollTop = log.scrollHeight;
  };

  if (usesSpinner) busy(btn, true);
  else {
    btn.disabled = true;
    btn.textContent = "Pulling…";
  }

  // Wire a cancel that aborts the stream (closes the connection; Ollama stops).
  const controller = new AbortController();
  cancelBtn.hidden = false;
  const onCancel = () => controller.abort();
  cancelBtn.addEventListener("click", onCancel);

  let ollamaUnreachable = false;
  let sawError = false;
  const checkOllamaError = (msg) => /ollama is (not running|installed but not reachable)|not reachable at/i.test(msg || "");

  // Progress accounting across the model's layers (Ollama streams per-digest
  // total/completed). Overall % = sum(completed)/sum(total); speed is a smoothed
  // byte-rate; ETA derives from the remaining bytes at that rate.
  const digests = {};
  let lastTs = performance.now();
  let lastBytes = 0;
  let speed = 0; // bytes/sec, EMA
  let overallPct = null;

  const recompute = (statusText) => {
    let total = 0;
    let completed = 0;
    for (const d of Object.values(digests)) {
      total += d.total || 0;
      completed += d.completed || 0;
    }
    const now = performance.now();
    const dt = (now - lastTs) / 1000;
    if (dt >= 0.4 && completed >= lastBytes) {
      const inst = (completed - lastBytes) / dt;
      speed = speed ? speed * 0.7 + inst * 0.3 : inst;
      lastTs = now;
      lastBytes = completed;
    }
    if (total > 0) {
      overallPct = (completed / total) * 100;
      const parts = [`${fmtBytes(completed)} / ${fmtBytes(total)}`];
      if (speed > 0) parts.push(`${(speed / 1e6).toFixed(1)} MB/s`);
      const eta = speed > 0 ? (total - completed) / speed : null;
      if (eta != null && Number.isFinite(eta) && eta > 0.5) parts.push(`ETA ${fmtDuration(eta)}`);
      setPullProgress({ percent: overallPct, status: statusText, stats: parts.join(" · ") });
    } else {
      setPullProgress({ percent: null, status: statusText, stats: "" });
    }
  };

  try {
    const out = await postMaybeStream(
      "/models/pull",
      { model, free_vram_mb: targetVram(), allow_override: $("#pull-override").checked },
      (evt) => {
        if (evt.destination_label && !$("#pull-progress-dest").textContent) {
          $("#pull-progress-dest").textContent = `→ ${evt.destination_label}`;
        }
        if (evt.error) {
          append(`error: ${evt.error}`);
          sawError = true;
          if (checkOllamaError(evt.error)) ollamaUnreachable = true;
          setPullProgress({ status: `Error: ${evt.error}` });
          return;
        }
        // A soft fit note (e.g. "won't fit GPU — will run on CPU") rides the start event.
        if (evt.note) append(`note: ${evt.note}`);
        if (evt.digest && evt.total) {
          digests[evt.digest] = { total: evt.total, completed: evt.completed || 0 };
        }
        if (evt.status) {
          const layerPct = evt.total && evt.completed ? Math.round((evt.completed / evt.total) * 100) : null;
          append(`${evt.status}${layerPct != null ? ` ${layerPct}%` : ""}`);
          recompute(evt.status);
          if (!usesSpinner) btn.textContent = overallPct != null ? `Pulling ${Math.round(overallPct)}%` : "Pulling…";
        }
      },
      controller.signal
    );
    if (!out.streamed) {
      const j = out.json || {};
      if (j.blocked_by === "fit-check") {
        const msg = j.message || "Model may not fit available memory.";
        append(`blocked by fit check: ${msg}`);
        if (j.fit?.estimate_gb) append(`  needs ~${j.fit.estimate_gb.required} GB`);
        setPullProgress({ percent: 0, status: `Blocked by fit check — tick "Warn only; pull anyway" to continue.` });
      } else {
        const msg = j.error || "Pull could not start.";
        append(msg);
        if (checkOllamaError(msg)) ollamaUnreachable = true;
        setPullProgress({ status: `Error: ${msg}` });
      }
    } else if (sawError) {
      // Stream opened but reported a failure mid-way — don't claim success or
      // flip any card to "installed".
    } else {
      append("done.");
      setPullProgress({ percent: 100, status: "Pulled successfully.", stats: "" });
      await refreshInstalled();
      markModelInstalledInUI(model);
      showPullDone(model);
      highlightInstalledModel(model);
      toast(`Pulled ${model}.`, "success");
    }
  } catch (err) {
    if (err.name === "AbortError") {
      append("cancelled.");
      setPullProgress({ status: "Cancelled." });
      toast("Pull cancelled.", "info");
    } else {
      append(`error: ${err.message}`);
      setPullProgress({ status: `Error: ${err.message}` });
    }
  } finally {
    cancelBtn.hidden = true;
    cancelBtn.removeEventListener("click", onCancel);
    if (usesSpinner) busy(btn, false);
    else {
      btn.disabled = false;
      btn.innerHTML = cardOriginalHtml;
    }
    if (ollamaUnreachable) showOllamaHelp(model, triggerBtn);
  }
}

// Free all loaded models from memory/VRAM (the "unstick / reset" button).
async function freeMemory() {
  const btn = $("#btn-free");
  busy(btn, true);
  try {
    const res = await postJSON("/models/free", {});
    if (res.success) {
      toast(res.message || "Memory freed.", "success");
      await refreshLiveModelState(true);
    } else {
      toast(res.error || "Could not free memory.", "error");
    }
  } catch (err) {
    toast(`Free memory failed: ${err.message}`, "error");
  } finally {
    busy(btn, false);
  }
}

// Delete a model from disk (with confirm), then refresh the installed list.
async function deleteModel(name, btn) {
  if (!window.confirm(`Delete "${name}" from disk? This frees space but you'll need to pull it again.`)) {
    return;
  }
  busy(btn, true);
  try {
    const res = await postJSON("/models/delete", { model: name });
    if (res.success) {
      toast(res.message || `Deleted ${name}.`, "success");
      await refreshInstalled();
    } else {
      toast(res.error || res.message || "Could not delete.", "error");
    }
  } catch (err) {
    toast(`Delete failed: ${err.message}`, "error");
  } finally {
    busy(btn, false);
  }
}

// ---------------------------------------------------------------------------
// Tab 2 — Question set: example / upload / validate
// ---------------------------------------------------------------------------
function updateBenchHelp(info) {
  if (!info?.test_count) return;
  const graderTypes = $("#grader-types")?.innerHTML || "…";
  state.testBenchInfo = info;
  const categories = Object.entries(info.categories || {})
    .map(([name, count]) => `${name} (${count})`)
    .join(", ");
  $("#bench-help").innerHTML =
    `Leave the editor empty to run the built-in LocalDeploy test bench: <b>${esc(info.test_count)} tests</b>` +
    (categories ? ` across ${esc(categories)}` : "") +
    `. Use JSON here only when you want a custom set: each question needs <code>name</code>, <code>category</code>, <code>prompt</code>, <code>max_output_tokens</code>, and a <code>grader</code> (one of <span id="grader-types">${graderTypes}</span>).`;
}

async function useBuiltInBench() {
  try {
    const info = await getJSON("/benchmark/test-bench");
    updateBenchHelp(info);
    $("#qs-editor").value = JSON.stringify(info.question_set || { version: 1, questions: [] }, null, 2);
    state.questionSetValidation = null;
    $("#validate-result").className = "result ok";
    $("#validate-result").textContent =
      `Loaded LocalDeploy test bench JSON: ${info.test_count} tests.`;
    $("#question-set-details").open = true;
    updateBenchmarkSummary();
  } catch (err) {
    toast(`Could not load LocalDeploy test bench metadata: ${err.message}`, "error");
  }
}

async function loadExample() {
  try {
    const example = await getJSON("/benchmark/example");
    $("#qs-editor").value = JSON.stringify(example, null, 2);
    state.questionSetValidation = null;
    $("#validate-result").className = "result";
    $("#validate-result").textContent = "Loaded a small JSON sample. Run benchmark will use this custom set until you clear the editor.";
    $("#question-set-details").open = true;
    updateBenchmarkSummary();
  } catch (err) {
    toast(`Could not load JSON sample: ${err.message}`, "error");
  }
}

function uploadFile(input) {
  const file = input.files?.[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = () => {
    $("#qs-editor").value = String(reader.result || "");
    state.questionSetValidation = null;
    $("#validate-result").textContent = "";
    $("#question-set-details").open = true;
    updateBenchmarkSummary();
  };
  reader.onerror = () => toast("Could not read file.", "error");
  reader.readAsText(file);
  input.value = "";
}

function parseEditor() {
  const raw = $("#qs-editor").value.trim();
  if (!raw) throw new Error("The editor is empty.");
  try {
    return JSON.parse(raw);
  } catch (err) {
    throw new Error(`Invalid JSON: ${err.message}`);
  }
}

async function validateSet() {
  const node = $("#validate-result");
  let payload;
  try {
    payload = parseEditor();
  } catch (err) {
    state.questionSetValidation = { valid: false };
    node.className = "result err";
    node.textContent = err.message;
    $("#question-set-details").open = true;
    return null;
  }
  try {
    const report = await postJSON("/benchmark/validate", payload);
    if (report.valid) {
      state.questionSetValidation = { valid: true, questionCount: report.question_count };
      node.className = "result ok";
      node.textContent = `Valid — ${report.question_count} question(s).`;
      $("#question-set-details").open = true;
    } else {
      state.questionSetValidation = { valid: false };
      node.className = "result err";
      $("#question-set-details").open = true;
      node.innerHTML =
        `Invalid set:` +
        `<ul class="err-list">` +
        (report.errors || [])
          .map((e) => `<li>${esc(e.name ? `[${e.name}] ` : e.index >= 0 ? `row ${e.index}: ` : "")}${esc(e.error)}</li>`)
          .join("") +
        `</ul>`;
    }
    return report;
  } catch (err) {
    node.className = "result err";
    node.textContent = `Validation failed: ${err.message}`;
    return null;
  }
}

// ---------------------------------------------------------------------------
// Tab 2 — Run (streamed)
// ---------------------------------------------------------------------------

// Portable per-test fields for a report card (shared by single + dual runs).
function collectTest(evt) {
  return {
    name: evt.name,
    category: evt.category,
    success: evt.success,
    accuracy: evt.accuracy,
    elapsed_seconds: evt.elapsed_seconds,
    approx_tokens_per_second: evt.approx_tokens_per_second ?? null,
    error: evt.error || null,
    warning: evt.warning || null,
    response_preview: evt.response_preview || "",
  };
}

// Append one test_result row (+ collapsible preview) to a tbody. Shared by the
// live queue view and the persisted detailed-results table.
function appendResultRow(tbody, evt, runName = "") {
  const tps = evt.approx_tokens_per_second ?? null;
  const resultBadge = evt.success ? `<span class="pass">PASS</span>` : `<span class="fail">FAIL</span>`;
  const errSnippet = !evt.success && evt.error
    ? ` <span class="muted small" title="${esc(evt.error)}">${esc(evt.error.slice(0, 50))}${evt.error.length > 50 ? "…" : ""}</span>`
    : "";
  const warnBadge = evt.warning ? ` <span class="warn-badge" title="${esc(evt.warning)}">⚠</span>` : "";
  const tpsCell = tps != null ? `${tps.toFixed(1)}` : `<span class="muted">—</span>`;
  const hasPreview = !!evt.response_preview;

  const tr = document.createElement("tr");
  tr.innerHTML = `<td>${esc(runName)}</td>
    <td>${esc(evt.name)}</td>
    <td>${esc(evt.category)}</td>
    <td>${resultBadge}${errSnippet}${warnBadge}</td>
    <td class="num">${esc(evt.elapsed_seconds)}s</td>
    <td class="num">${tpsCell}</td>
    <td class="num">${esc(evt.accuracy)}</td>
    <td><button class="btn-preview${hasPreview ? "" : " btn-preview-none"}" aria-label="Toggle response preview" title="${hasPreview ? "Show/hide response" : "No response captured"}">▸</button></td>`;
  tbody.appendChild(tr);

  const previewTr = document.createElement("tr");
  previewTr.className = "preview-row hidden";
  previewTr.innerHTML = `<td colspan="8"><div class="response-preview">${esc(evt.response_preview || "(no preview)")}</div></td>`;
  tbody.appendChild(previewTr);
  const toggleBtn = tr.querySelector(".btn-preview");
  if (hasPreview) {
    toggleBtn.addEventListener("click", () => {
      const hidden = previewTr.classList.toggle("hidden");
      toggleBtn.textContent = hidden ? "▸" : "▾";
    });
  } else {
    toggleBtn.disabled = true;
  }
}

// Update the run progress bar's width + ARIA value together.
function setProgress(pct) {
  const bar = $("#run-progress-bar");
  bar.style.width = `${pct}%`;
  bar.setAttribute("aria-valuenow", String(pct));
  const label = $("#run-active-percent");
  if (label) label.textContent = `${Math.max(0, Math.min(100, Math.round(pct)))}%`;
}

function setActiveRun(item, done = 0, total = 0) {
  const panel = $("#run-active");
  if (!panel) return;
  if (!item) {
    panel.classList.add("hidden");
    return;
  }
  panel.classList.remove("hidden");
  $("#run-active-title").textContent = runLabel(item);
  $("#run-active-detail").textContent = item.current || item.status || "Running";
  const elapsed = item.startedAt ? `${Math.round((Date.now() - item.startedAt) / 1000)}s elapsed` : "";
  const tests = total ? `${done} of ${total} tests` : "";
  $("#run-active-count").textContent = [tests, elapsed].filter(Boolean).join(" · ") || item.status || "";
}

function buildRunQueue(profiles, deviceChoice, questionInfo) {
  const devices = deviceChoice === "both" ? ["cpu", "gpu"] : [deviceChoice || "auto"];
  return profiles.flatMap((profile) =>
    devices.map((device) => ({
      id: runId(),
      profile,
      modelId: state.profileModels[profile] || profile,
      requestedDevice: device,
      actualDevice: null,
      questionSetName: questionInfo.name,
      questionSetHash: questionInfo.hash,
      createdAt: new Date().toISOString(),
      status: "waiting",
      progress: 0,
      current: "Waiting",
      tests: [],
      elapsedSeconds: null,
      error: null,
    }))
  );
}

function renderRunQueue(queue) {
  const slot = $("#run-queue");
  if (!queue.length) {
    slot.innerHTML = `<div class="muted small">No benchmark runs are queued.</div>`;
    return;
  }
  const finished = queue.filter((item) => ["complete", "failed", "stopped"].includes(item.status)).length;
  slot.innerHTML =
    (finished > 1
      ? `<div class="queue-toolbar"><button class="btn compact" id="btn-clear-finished" title="Remove completed, failed, and stopped rows from the queue">Clear finished (${finished})</button></div>`
      : "") +
    queue
      .map((item) => {
        const cls =
          item.status === "complete"
            ? "on"
            : item.status === "failed"
              ? "wont"
              : item.status === "stopped"
                ? "tight"
                : item.status === "deploying" || item.status === "running"
                  ? "cpu"
                  : "off";
        const label =
          {
            waiting: "Queued",
            deploying: "Deploying",
            running: "Running",
            complete: "Finished",
            failed: "Failed",
            stopped: "Stopped",
          }[item.status] || item.status;
        const elapsed = item.elapsedSeconds != null
          ? `${item.elapsedSeconds}s`
          : item.startedAt
            ? `${Math.round((Date.now() - item.startedAt) / 1000)}s`
            : "0s";
        const canMove = item.status === "waiting";
        const isFinished = ["complete", "failed", "stopped"].includes(item.status);
        const showProgress = ["deploying", "running"].includes(item.status) && (item.status === "deploying" || item.total || item.progress);
        const indeterminate = item.status === "deploying" && !item.progress;
        const progress = showProgress
          ? `<div class="queue-row-progress"><div class="run-progress-track"><div class="run-progress-fill${indeterminate ? " indeterminate" : ""}"${indeterminate ? "" : ` style="width:${esc(item.progress || 0)}%"`}></div></div></div>`
          : "";
        // Waiting rows can be reordered + removed; finished rows can be dismissed.
        const controls =
          canMove || isFinished
            ? `<div class="queue-actions" aria-label="Queue controls">
                ${canMove ? `<button class="btn compact queue-move" data-id="${esc(item.id)}" data-delta="-1" title="Move up">Up</button>
                <button class="btn compact queue-move" data-id="${esc(item.id)}" data-delta="1" title="Move down">Down</button>` : ""}
                <button class="btn compact danger queue-remove" data-id="${esc(item.id)}" title="${canMove ? "Remove from queue" : "Dismiss"}">${canMove ? "Remove" : "Dismiss"}</button>
              </div>`
            : "";
        const reason = item.status === "failed" && item.error
          ? `<span class="muted small queue-reason" title="${esc(item.error)}">${esc(item.error)}</span>`
          : "";
        return `<div class="queue-row queue-status-${esc(item.status)}" data-id="${esc(item.id)}">
          <span class="queue-status-dot" aria-hidden="true"></span>
          <div class="queue-row-main">
            <b>${esc(runLabel(item))}</b>
            <span class="muted small">${esc(item.profile)} · ${esc(item.current || item.status)} · ${esc(elapsed)}</span>
            ${progress}
            ${reason}
          </div>
          <div class="queue-row-side">
            <span class="badge ${cls}">${esc(label)}</span>
            ${controls}
          </div>
        </div>`;
      })
      .join("");
  $("#btn-clear-finished", slot)?.addEventListener("click", clearFinishedQueue);
  $$(".queue-remove", slot).forEach((btn) =>
    btn.addEventListener("click", () => removeQueuedRun(btn.dataset.id))
  );
  $$(".queue-move", slot).forEach((btn) =>
    btn.addEventListener("click", () => moveQueuedRun(btn.dataset.id, Number(btn.dataset.delta)))
  );
}

// Remove all finished (complete/failed/stopped) rows from the queue, leaving
// waiting and active runs intact.
function clearFinishedQueue() {
  const queue = state.currentQueue || [];
  state.currentQueue = queue.filter((item) => !["complete", "failed", "stopped"].includes(item.status));
  renderRunQueue(state.currentQueue);
}

function removeQueuedRun(id) {
  const queue = state.currentQueue || [];
  const idx = queue.findIndex((item) => item.id === id);
  if (idx < 0) return;
  // Never yank the row that's actively deploying/running out from under the
  // run loop; use the active-run Stop button for that.
  if (["deploying", "running"].includes(queue[idx].status)) return;
  const wasWaiting = queue[idx].status === "waiting";
  queue.splice(idx, 1);
  renderRunQueue(queue);
  const summary = $("#run-summary");
  if (summary && wasWaiting) {
    summary.className = "result";
    summary.textContent = `Removed a waiting benchmark. ${queue.filter((q) => q.status === "waiting").length} waiting.`;
  }
}

function moveQueuedRun(id, delta) {
  const queue = state.currentQueue || [];
  const idx = queue.findIndex((item) => item.id === id);
  const next = idx + delta;
  if (idx < 0 || next < 0 || next >= queue.length) return;
  if (queue[idx].status !== "waiting" || queue[next].status !== "waiting") return;
  [queue[idx], queue[next]] = [queue[next], queue[idx]];
  renderRunQueue(queue);
}

async function runQueueItem(item, questionInfo, timeout, controller) {
  const summary = $("#run-summary");
  const body = { profiles: [item.profile], timeout };
  if (item.requestedDevice && item.requestedDevice !== "auto") body.device = item.requestedDevice;
  if (questionInfo.questions) body.questions = questionInfo.questions;

  let total = 0;
  let done = 0;
  const started = Date.now();
  item.startedAt = started;
  item.status = "running";
  item.current = "Starting";
  setActiveRun(item);
  renderRunQueue(state.currentQueue || []);
  let out;
  try {
    out = await postMaybeStream("/benchmark/run", body, (evt) => {
    if (evt.event === "deploy_start") {
      item.status = "deploying";
      item.current = `Deploying on ${(evt.device || item.requestedDevice).toUpperCase()}`;
      setActiveRun(item, done, total);
    } else if (evt.event === "deploy_end") {
      item.current = "Deployment complete";
      if (evt.warning) item.warning = evt.warning;
      setActiveRun(item, done, total);
    } else if (evt.event === "run_start") {
      item.status = "running";
      item.done = 0;
      total = evt.test_count || 0;
      item.total = total;
      item.current = `0 / ${total} tests`;
      setProgress(0);
      setActiveRun(item, 0, total);
    } else if (evt.event === "profile_start") {
      item.status = "running";
      item.current = "Model warm-up";
      setActiveRun(item, done, total);
    } else if (evt.event === "test_start") {
      item.status = "running";
      item.current = `Running ${evt.name || "test"}`;
      setActiveRun(item, done, total);
    } else if (evt.event === "test_result") {
      item.status = "running";
      done++;
      item.tests.push(collectTest(evt));
      item.progress = total ? Math.round((done / total) * 100) : item.progress;
      item.done = done;
      item.total = total;
      item.current = `${done} / ${total || "?"} tests`;
      setProgress(item.progress);
      setActiveRun(item, done, total);
      syncLiveBenchmarkRun(item, true);
    } else if (evt.event === "profile_aborted") {
      item.status = "failed";
      item.error = evt.reason || "profile aborted";
      item.current = item.error;
      setActiveRun(item, done, total);
      removeLiveBenchmarkRun(item.id);
    } else if (evt.event === "profile_end") {
      if (evt.actual_device) item.actualDevice = evt.actual_device;
      item.current = "Benchmark complete";
      setActiveRun(item, done || total, total);
      syncLiveBenchmarkRun(item, true);
    } else if (evt.event === "benchmark_unload_end") {
      item.current = "Temporary benchmark model unloaded";
      setActiveRun(item, done || total, total);
    } else if (evt.event === "benchmark_unload_error") {
      item.current = "Benchmark complete; unload needs manual check";
      item.warning = evt.error || "temporary benchmark unload failed";
      setActiveRun(item, done || total, total);
    } else if (evt.event === "run_end") {
      item.elapsedSeconds = evt.elapsed_seconds;
      item.progress = 100;
      setProgress(100);
      setActiveRun(item, done || total, total);
      syncLiveBenchmarkRun(item, true);
    } else if (evt.event === "error") {
      item.status = "failed";
      item.error = evt.error || "run failed";
      item.current = item.error;
      setActiveRun(item, done, total);
      removeLiveBenchmarkRun(item.id);
    }
    renderRunQueue(state.currentQueue || []);
    }, controller.signal);
  } catch (err) {
    // A whole-queue cancel sets queueCancelled and bubbles up so the loop stops.
    // A per-item Stop aborts only this run: mark it stopped and let the caller
    // continue with the rest of the queue.
    if (err.name === "AbortError") {
      if (state.queueCancelled) throw err;
      item.status = "stopped";
      item.current = "Stopped";
      item.error = "Stopped by user";
      setActiveRun(null);
      removeLiveBenchmarkRun(item.id);
      renderRunQueue(state.currentQueue || []);
      const note = $("#run-summary");
      if (note) {
        note.className = "result";
        note.textContent = `Stopped ${runLabel(item)}. Continuing with the rest of the queue.`;
      }
      return null;
    }
    throw err;
  }

  if (!out.streamed) {
    const j = out.json || {};
    item.status = "failed";
    item.error = j.error || "Run could not start.";
    item.current = item.error;
    removeLiveBenchmarkRun(item.id);
    renderRunQueue(state.currentQueue || []);
    return null;
  }
  if (item.status === "failed") {
    removeLiveBenchmarkRun(item.id);
    return null;
  }
  if (!item.tests.length) {
    item.status = "failed";
    item.error = "No test results streamed.";
    item.current = item.error;
    removeLiveBenchmarkRun(item.id);
    renderRunQueue(state.currentQueue || []);
    return null;
  }
  item.actualDevice = item.actualDevice || (item.requestedDevice === "auto" ? null : item.requestedDevice);
  item.status = "complete";
  item.progress = 100;
  item.current = "Complete";
  item.elapsedSeconds = item.elapsedSeconds ?? Number(((Date.now() - started) / 1000).toFixed(2));
  const run = normalizeRunRecord(
    {
      ...item,
      hardware: state.lastHardware || {},
      source: "current-run",
      summary: summaryFromTests(item.tests),
      category_summary: categorySummary(item.tests),
    },
    "current-run"
  );
  summary.className = "result";
  summary.textContent = `Completed ${runLabel(run)}. Continuing queue.`;
  removeLiveBenchmarkRun(item.id, false);
  renderRunQueue(state.currentQueue || []);
  return run;
}

async function runBenchmark() {
  const btn = $("#btn-run");
  const cancelBtn = $("#btn-run-cancel");
  const summary = $("#run-summary");
  const profiles = selectedBenchProfiles();
  if (!profiles.length) {
    summary.className = "result err";
    summary.textContent = "Select at least one profile.";
    return;
  }
  let questionInfo;
  try {
    questionInfo = currentQuestionSetInfo();
  } catch (err) {
    summary.className = "result err";
    summary.textContent = `Question set error: ${err.message}`;
    return;
  }

  const deviceChoice = ($("#bench-device")?.value || "auto").toLowerCase();
  const queue = buildRunQueue(profiles, deviceChoice, questionInfo);
  state.currentQueue = queue;
  renderRunQueue(queue);
  $("#run-table").classList.remove("hidden");
  const categoryRollup = $("#run-category-rollup");
  if (categoryRollup) categoryRollup.innerHTML = "";
  summary.className = "result";
  summary.textContent = `Running ${queue.length} queued benchmark${queue.length === 1 ? "" : "s"}.`;
  setProgress(0);
  setActiveRun(queue[0]);
  btn.disabled = true;
  cancelBtn.hidden = false;
  $("#btn-export").disabled = true;

  state.queueCancelled = false;
  // The global Cancel stops the whole queue: flag it, then abort whatever run
  // is currently active so its stream unwinds immediately.
  const onCancel = () => {
    state.queueCancelled = true;
    state.activeController?.abort();
  };
  cancelBtn.addEventListener("click", onCancel);
  const completed = [];
  const timeout = Number($("#bench-timeout").value) || 240;
  const activeTimer = setInterval(() => {
    const active = queue.find((q) => ["deploying", "running"].includes(q.status));
    if (active) setActiveRun(active, active.done || 0, active.total || 0);
  }, 1000);
  try {
    let index = 0;
    while (index < queue.length) {
      if (state.queueCancelled) throw new DOMException("Aborted", "AbortError");
      const item = queue[index];
      if (!item || item.status !== "waiting") {
        index++;
        continue;
      }
      // One controller per item so the active-run Stop button can abort just
      // this run while the queue keeps going.
      const itemController = new AbortController();
      state.activeController = itemController;
      const run = await runQueueItem(item, questionInfo, timeout, itemController);
      state.activeController = null;
      if (run) {
        completed.push(run);
        addBenchmarkRuns([run], true);
      }
      index++;
    }
    if (completed.length) {
      summary.className = "result ok";
      summary.textContent = `Completed ${completed.length}/${queue.length} benchmark run${queue.length === 1 ? "" : "s"}.`;
    } else {
      renderBenchmarkWorkspace();
      summary.className = "result err";
      summary.textContent = "No benchmark runs completed.";
    }
  } catch (err) {
    if (err.name === "AbortError") {
      queue.filter((q) => ["waiting", "deploying", "running"].includes(q.status)).forEach((q) => {
        q.status = "failed";
        q.current = "Cancelled";
      });
      summary.className = "result";
      summary.textContent = "Benchmark queue cancelled.";
      toast("Benchmark queue cancelled.", "info");
    } else {
      // A mid-stream failure (dropped connection, server crash) must not leave
      // the active row stuck "running"/"deploying" forever — removeQueuedRun
      // refuses to remove rows in those states, and the dashboard would keep
      // showing it as an active run indefinitely otherwise.
      queue.filter((q) => ["waiting", "deploying", "running"].includes(q.status)).forEach((q) => {
        q.status = "failed";
        q.error = q.error || err.message || "Unexpected error";
        q.current = q.error;
        removeLiveBenchmarkRun(q.id, false);
      });
      summary.className = "result err";
      summary.textContent = `Benchmark queue failed: ${err.message}`;
    }
    renderRunQueue(queue);
  } finally {
    clearInterval(activeTimer);
    setActiveRun(null);
    state.activeController = null;
    state.queueCancelled = false;
    cancelBtn.hidden = true;
    cancelBtn.removeEventListener("click", onCancel);
    btn.disabled = false;
    await refreshLiveModelState(true);
    updateBenchmarkSummary();
  }
}

// ---------------------------------------------------------------------------
// Tab 2 — Report cards: export + compare
// ---------------------------------------------------------------------------
function runsForDashboard() {
  const runs = allBenchmarkRuns().filter((r) => (r.tests || []).length);
  if (!state.selectedRunIds.length) return runs;
  const selected = new Set(state.selectedRunIds);
  return runs.filter((r) => selected.has(r.id));
}

function renderWinners(runs) {
  const slot = $("#winner-badges");
  if (!runs.length) {
    slot.innerHTML = "";
    return;
  }
  const ranked = [...runs].sort((a, b) =>
    (b.summary.passed || 0) - (a.summary.passed || 0) ||
    (b.summary.avg_accuracy || 0) - (a.summary.avg_accuracy || 0) ||
    (a.summary.avg_latency_s || 999999) - (b.summary.avg_latency_s || 999999)
  );
  const top = ranked[0];
  const fastest = [...runs].filter((r) => r.summary.avg_latency_s != null).sort((a, b) => a.summary.avg_latency_s - b.summary.avg_latency_s)[0];
  const bestTps = [...runs].filter((r) => r.summary.avg_tokens_per_second != null).sort((a, b) => b.summary.avg_tokens_per_second - a.summary.avg_tokens_per_second)[0];
  const categories = new Set(runs.flatMap((r) => (r.category_summary || categorySummary(r.tests)).map((c) => c.category)));
  slot.innerHTML = [
    `<div class="metric-tile"><span>Top run</span><strong>${esc(runLabel(top))}</strong><small>${esc(top.summary.passed)}/${esc(top.summary.tests)} passed</small></div>`,
    `<div class="metric-tile"><span>Accuracy</span><strong>${esc(top.summary.avg_accuracy)}</strong><small>${esc(top.questionSetName || "benchmark")}</small></div>`,
    fastest ? `<div class="metric-tile"><span>Fastest</span><strong>${esc(fastest.summary.avg_latency_s)}s</strong><small>${esc(runLabel(fastest))}</small></div>` : "",
    bestTps ? `<div class="metric-tile"><span>Best tok/s</span><strong>${esc(bestTps.summary.avg_tokens_per_second)}</strong><small>${esc(runLabel(bestTps))}</small></div>` : "",
    `<div class="metric-tile"><span>Categories</span><strong>${esc(categories.size)}</strong><small>${esc(runs.length)} run${runs.length === 1 ? "" : "s"}</small></div>`,
  ].join("");
}

function renderLeaderboard(runs) {
  const slot = $("#leaderboard-body");
  if (!runs.length) {
    slot.innerHTML = "Benchmark results appear here after the first streamed test result.";
    return;
  }
  const rows = [...runs]
    .sort((a, b) =>
      (b.summary.passed || 0) - (a.summary.passed || 0) ||
      (b.summary.avg_accuracy || 0) - (a.summary.avg_accuracy || 0) ||
      (a.summary.avg_latency_s || 999999) - (b.summary.avg_latency_s || 999999)
    )
    .map((r, i) => `<div class="leaderboard-row">
      <span class="rank">${i + 1}</span>
      <div class="leaderboard-name"><b>${esc(runLabel(r))}</b><span>${esc(r.questionSetName || "")}</span></div>
      <div class="leaderboard-metrics">
        <span><b>${esc(r.summary.passed)}/${esc(r.summary.tests)}</b> passed</span>
        <span><b>${esc(r.summary.avg_accuracy)}</b> acc</span>
        <span><b>${esc(r.summary.avg_latency_s)}s</b> latency</span>
        <span><b>${esc(r.summary.avg_tokens_per_second ?? "—")}</b> tok/s</span>
      </div>
    </div>`)
    .join("");
  slot.innerHTML = `<div class="leaderboard-list">${rows}</div>`;
}

function heatColor(acc) {
  const n = Math.max(0, Math.min(1, Number(acc || 0)));
  if (n >= 0.8) return "heat-good";
  if (n >= 0.5) return "heat-warn";
  return "heat-bad";
}

function renderHeatmap(runs) {
  const slot = $("#heatmap-body");
  if (!runs.length) {
    slot.innerHTML = "Run benchmarks to fill the category heatmap.";
    return;
  }
  const categories = Array.from(new Set(runs.flatMap((r) => (r.category_summary || categorySummary(r.tests)).map((c) => c.category)))).sort();
  const cols = `minmax(140px, 180px) repeat(${runs.length}, minmax(120px, 160px))`;
  const cells = categories
    .map((cat) => `<div class="heat-category">${esc(cat)}</div>${runs
      .map((r) => {
        const c = (r.category_summary || categorySummary(r.tests)).find((x) => x.category === cat);
        const acc = c?.avg_accuracy ?? null;
        return `<div class="heat-cell ${acc == null ? "" : heatColor(acc)}" title="${esc(runLabel(r))} · ${esc(cat)}">${acc == null ? "—" : Number(acc).toFixed(2)}</div>`;
      })
      .join("")}`)
    .join("");
  slot.innerHTML = `<div class="heatmap-scroll"><div class="heatmap-grid" style="grid-template-columns:${cols}">
    <div class="heat-head">Category</div>${runs.map((r) => `<div class="heat-head">${esc(runLabel(r))}</div>`).join("")}
    ${cells}
  </div></div>`;
}

function renderScatter(runs) {
  const slot = $("#scatter-body");
  const points = runs.filter((r) => r.summary.avg_latency_s != null && r.summary.avg_accuracy != null);
  if (!points.length) {
    slot.innerHTML = "Run at least one benchmark to plot speed and quality.";
    return;
  }
  if (points.length < 2) {
    const r = points[0];
    slot.innerHTML = `<div class="scatter-single">
      <span class="eyebrow">One run captured</span>
      <strong>${esc(r.summary.avg_accuracy)} accuracy</strong>
      <span>${esc(r.summary.avg_latency_s)}s latency · ${esc(r.summary.avg_tokens_per_second ?? "—")} tok/s</span>
      <small>Run another model or device to compare speed vs quality.</small>
    </div>`;
    return;
  }
  const maxLat = Math.max(...points.map((r) => Number(r.summary.avg_latency_s || 0)), 1);
  const labels = points
    .map((r, i) => {
      const x = 46 + (Number(r.summary.avg_latency_s || 0) / maxLat) * 260;
      const y = 170 - Number(r.summary.avg_accuracy || 0) * 125;
      return `<g>
        <circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="6" />
        <text x="${(x + 10).toFixed(1)}" y="${(y + 4).toFixed(1)}">${esc(String(i + 1))}</text>
        <title>${esc(runLabel(r))}: ${esc(r.summary.avg_latency_s)}s, acc ${esc(r.summary.avg_accuracy)}</title>
      </g>`;
    })
    .join("");
  slot.innerHTML = `<svg class="scatter" viewBox="0 0 340 205" role="img" aria-label="Speed quality scatter">
    <line x1="40" y1="180" x2="316" y2="180"></line><line x1="40" y1="34" x2="40" y2="180"></line>
    <line class="gridline" x1="40" y1="96" x2="316" y2="96"></line>
    <text x="118" y="199">avg latency, lower is better</text><text x="8" y="28">accuracy</text>
    ${labels}
  </svg>`;
}

function renderMatrix(runs) {
  const slot = $("#matrix-body");
  if (!runs.length) {
    slot.innerHTML = "Run benchmarks to fill the pass/fail matrix.";
    return;
  }
  const tests = Array.from(new Set(runs.flatMap((r) => (r.tests || []).map((t) => t.name)))).sort();
  const maxRows = 80;
  const rows = tests.slice(0, maxRows)
    .map((name) => `<tr><th>${esc(name)}</th>${runs
      .map((r) => {
        const t = (r.tests || []).find((x) => x.name === name);
        const cls = !t ? "" : t.success ? "matrix-pass" : "matrix-fail";
        return `<td><span class="matrix-pill ${cls}">${t ? `${t.success ? "PASS" : "FAIL"} · ${Number(t.accuracy || 0).toFixed(2)}` : "—"}</span></td>`;
      })
      .join("")}</tr>`)
    .join("");
  const note = tests.length > maxRows ? `<div class="muted small matrix-note">Showing first ${maxRows} of ${tests.length} tests. Use Detailed results for filters.</div>` : "";
  slot.innerHTML = `<div class="table-wrap matrix-wrap"><table class="results matrix">
    <thead><tr><th>Test</th>${runs.map((r) => `<th>${esc(runLabel(r))}</th>`).join("")}</tr></thead>
    <tbody>${rows}</tbody></table></div>${note}`;
}

function renderDetailedResults(runs = runsForDashboard()) {
  const table = $("#run-table");
  const tbody = $("tbody", table);
  const runFilter = $("#detail-filter-run");
  const catFilter = $("#detail-filter-category");
  const resultFilter = $("#detail-filter-result")?.value || "";
  const selectedRun = runFilter?.value || "";
  const selectedCat = catFilter?.value || "";
  const allRuns = allBenchmarkRuns();
  const categories = Array.from(new Set(allRuns.flatMap((r) => (r.tests || []).map((t) => t.category || "?")))).sort();
  if (runFilter) {
    const current = runFilter.value;
    runFilter.innerHTML = `<option value="">All runs</option>${allRuns.map((r) => `<option value="${esc(r.id)}">${esc(runLabel(r))}</option>`).join("")}`;
    runFilter.value = current;
  }
  if (catFilter) {
    const current = catFilter.value;
    catFilter.innerHTML = `<option value="">All categories</option>${categories.map((c) => `<option value="${esc(c)}">${esc(c)}</option>`).join("")}`;
    catFilter.value = current;
  }
  const rows = [];
  for (const r of allRuns) {
    if (selectedRun && r.id !== selectedRun) continue;
    for (const t of r.tests || []) {
      if (selectedCat && t.category !== selectedCat) continue;
      if (resultFilter === "pass" && !t.success) continue;
      if (resultFilter === "fail" && t.success) continue;
      if (resultFilter === "slow" && t.success) continue;
      rows.push({ run: r, test: t });
    }
  }
  if (resultFilter === "slow") rows.sort((a, b) => Number(b.test.elapsed_seconds || 0) - Number(a.test.elapsed_seconds || 0));
  tbody.innerHTML = "";
  rows.slice(0, 250).forEach(({ run, test }) => appendResultRow(tbody, test, runLabel(run)));
  table.classList.toggle("hidden", rows.length === 0);
}

function renderRunLibrary() {
  const body = $("#run-library");
  if (!body) return;
  const runs = allBenchmarkRuns();
  if (!runs.length) {
    body.innerHTML = `<div class="muted small">No active, completed, or imported runs yet.</div>`;
    return;
  }
  const selected = new Set(state.selectedRunIds);
  body.innerHTML = runs
    .map((r) => {
      const live = r.source === "live-run";
      const badgeClass = live ? "cpu" : r.source === "imported-card" ? "cpu" : "on";
      const source = live ? `${r.status || "running"} · ${r.progress || 0}%` : `${r.source} · ${new Date(r.createdAt).toLocaleString()}`;
      const action = live
        ? `<span class="muted small">Active</span>`
        : `<button class="btn compact danger run-delete" data-id="${esc(r.id)}" title="Remove this run from history" aria-label="Remove run">×</button>`;
      return `<div class="run-library-row${selected.has(r.id) ? " selected" : ""}${live ? " live" : ""}">
      <label class="run-library-pick">
        <input type="checkbox" value="${esc(r.id)}"${selected.has(r.id) ? " checked" : ""} />
        <span><b>${esc(runLabel(r))}</b><span class="muted small">${esc(source)}</span></span>
        <span class="badge ${badgeClass}">${esc(r.summary.passed)}/${esc(r.summary.tests)}</span>
      </label>
      ${action}
    </div>`;
    })
    .join("");
  $$('input[type="checkbox"]', body).forEach((input) => {
    input.addEventListener("change", () => {
      const ids = new Set(state.selectedRunIds);
      if (input.checked) ids.add(input.value);
      else ids.delete(input.value);
      state.selectedRunIds = Array.from(ids);
      if (!state.selectedRunIds.includes(state.compareBaselineId)) state.compareBaselineId = state.selectedRunIds[0] || null;
      state.activeRunId = input.checked ? input.value : state.activeRunId;
      renderBenchmarkWorkspace();
    });
  });
  $$(".run-delete", body).forEach((btn) =>
    btn.addEventListener("click", () => deleteBenchmarkRun(btn.dataset.id))
  );
}

// Remove a single run from the local history (and any selection/baseline that
// pointed at it), so the library can be pruned without clearing everything.
function deleteBenchmarkRun(id) {
  state.benchmarkRuns = state.benchmarkRuns.filter((r) => r.id !== id);
  state.selectedRunIds = state.selectedRunIds.filter((x) => x !== id);
  if (state.compareBaselineId === id) state.compareBaselineId = state.selectedRunIds[0] || null;
  if (state.activeRunId === id) state.activeRunId = null;
  if (state.lastRun?.id === id) state.lastRun = null;
  saveBenchmarkRuns();
  renderBenchmarkWorkspace();
}

function renderCompareControls() {
  const baseline = $("#compare-baseline");
  const runs = allBenchmarkRuns();
  const selectedCompleted = state.selectedRunIds.filter((id) => state.benchmarkRuns.some((r) => r.id === id)).length;
  if (baseline) {
    baseline.innerHTML = state.selectedRunIds
      .map((id) => {
        const r = runs.find((x) => x.id === id);
        return r ? `<option value="${esc(id)}">${esc(runLabel(r))}</option>` : "";
      })
      .join("");
    baseline.value = state.compareBaselineId || state.selectedRunIds[0] || "";
  }
  const status = $("#compare-status");
  if (status) status.textContent = `${state.selectedRunIds.length} selected. Select 2 or more runs to compare.`;
  $("#btn-export-selected").disabled = selectedCompleted === 0;
  $("#btn-export").disabled = !(state.benchmarkRuns.find((r) => r.id === state.activeRunId) || state.lastRun);
}

function selectedComparisonRuns() {
  const runs = allBenchmarkRuns();
  return state.selectedRunIds.map((id) => runs.find((r) => r.id === id)).filter(Boolean);
}

function renderComparison(runs, baseline) {
  if (!runs.length || !baseline) {
    $("#compare-body").innerHTML = "";
    $("#response-drawer").classList.add("hidden");
    return;
  }
  state.compareBaselineId = baseline.id;
  const baseSummary = baseline.summary || summaryFromTests(baseline.tests);
  const delta = (a, b) => (a == null || b == null ? "—" : (Number(b) - Number(a)).toFixed(3));
  const rows = runs
    .map((r) => {
      const s = r.summary || summaryFromTests(r.tests);
      return `<tr><td><b>${esc(runLabel(r))}</b>${r.id === baseline.id ? ` <span class="badge on">baseline</span>` : ""}</td>
        <td class="num">${esc(s.passed)}/${esc(s.tests)}</td>
        <td class="num">${esc(s.avg_accuracy)} <span class="muted">(${esc(delta(baseSummary.avg_accuracy, s.avg_accuracy))})</span></td>
        <td class="num">${esc(s.avg_latency_s)}s <span class="muted">(${esc(delta(baseSummary.avg_latency_s, s.avg_latency_s))})</span></td>
        <td class="num">${esc(s.avg_tokens_per_second ?? "—")} <span class="muted">(${esc(delta(baseSummary.avg_tokens_per_second, s.avg_tokens_per_second))})</span></td></tr>`;
    })
    .join("");
  const tests = Array.from(new Set(runs.flatMap((r) => (r.tests || []).map((t) => t.name)))).sort();
  const testButtons = tests
    .map((name) => `<button class="btn compact response-compare-btn" data-test="${esc(name)}">${esc(name)}</button>`)
    .join("");
  $("#compare-body").innerHTML = `<div class="table-wrap"><table class="results">
    <thead><tr><th>Run</th><th class="num">Passed</th><th class="num">Accuracy Δ</th><th class="num">Latency Δ</th><th class="num">tok/s Δ</th></tr></thead>
    <tbody>${rows}</tbody></table></div>
    <h3 class="sub">Response detail</h3><div class="response-test-list">${testButtons}</div>`;
  $$(".response-compare-btn", $("#compare-body")).forEach((b) => {
    b.addEventListener("click", () => renderResponseDrawer(b.dataset.test, runs));
  });
}

function renderAutoComparison() {
  const runs = selectedComparisonRuns();
  const baseline = allBenchmarkRuns().find((r) => r.id === state.compareBaselineId) || runs[0];
  if (runs.length >= 2 && baseline) {
    renderComparison(runs, baseline);
  } else {
    $("#compare-body").innerHTML = "";
    $("#response-drawer").classList.add("hidden");
  }
}

function renderBenchmarkWorkspace() {
  const runs = runsForDashboard();
  const hasRuns = runs.length > 0;
  $("#results-dashboard-card")?.classList.toggle("hidden", !hasRuns);
  $("#detailed-results-card")?.classList.toggle("hidden", !hasRuns);
  renderWinners(runs);
  renderLeaderboard(runs);
  renderHeatmap(runs);
  renderScatter(runs);
  renderMatrix(runs);
  renderRunLibrary();
  renderCompareControls();
  renderAutoComparison();
  renderDetailedResults(runs);
  updateBenchmarkSummary();
}

async function exportOneRun(run, btn) {
  busy(btn, true);
  try {
    const out = await postJSON("/benchmark/export", {
      profile: run.profile,
      model_id: run.modelId,
      device: run.actualDevice || run.requestedDevice,
      hardware: run.hardware,
      tests: run.tests,
      summary: run.summary,
      category_summary: run.category_summary,
    });
    if (!out.success) throw new Error(out.error || "export failed");
    const name = (run.profile || run.modelId || "model").replace(/[^\w.-]+/g, "_");
    const devSuffix = run.actualDevice || run.requestedDevice ? `-${run.actualDevice || run.requestedDevice}` : "";
    downloadFile(`localdeploy-card-${name}${devSuffix}.html`, out.html, "text/html");
    toast("Report card downloaded.", "success");
  } finally {
    busy(btn, false);
  }
}

async function exportCard() {
  const run = state.benchmarkRuns.find((r) => r.id === state.activeRunId) || state.lastRun;
  if (!run) {
    toast("Select or run a benchmark first.", "error");
    return;
  }
  const btn = $("#btn-export");
  try {
    await exportOneRun(run, btn);
  } catch (err) {
    toast(`Export failed: ${err.message}`, "error");
  }
}

async function exportSelectedRuns() {
  const runs = state.selectedRunIds.map((id) => state.benchmarkRuns.find((r) => r.id === id)).filter(Boolean);
  if (!runs.length) {
    toast("Select at least one completed run to export.", "error");
    return;
  }
  const btn = $("#btn-export-selected");
  if (runs.length === 1) {
    try {
      await exportOneRun(runs[0], btn);
    } catch (err) {
      toast(`Export failed: ${err.message}`, "error");
    }
    return;
  }
  downloadFile(
    `localdeploy-benchmark-runs-${new Date().toISOString().slice(0, 10)}.json`,
    JSON.stringify({ kind: "localdeploy.run_bundle", version: 1, runs }, null, 2),
    "application/json"
  );
  toast("Selected runs exported as JSON bundle.", "success");
}

function readCardFiles(input) {
  const files = Array.from(input.files || []);
  if (!files.length) return;
  Promise.all(
    files.map(
      (file) =>
        new Promise((resolve) => {
          const reader = new FileReader();
          reader.onload = () => {
            const text = String(reader.result || "");
            let runs = [];
            try {
              const parsed = JSON.parse(text);
              if (parsed.kind === "localdeploy.run_bundle" && Array.isArray(parsed.runs)) runs = parsed.runs;
            } catch {
              /* maybe a report card HTML */
            }
            const card = runs.length ? null : extractCard(text);
            if (card) runs = [card];
            resolve(runs.map((r) => ({ ...normalizeRunRecord(r, "imported-card"), source: "imported-card" })));
          };
          reader.onerror = () => resolve([]);
          reader.readAsText(file);
        })
    )
  ).then((groups) => {
    const runs = groups.flat();
    if (!runs.length) toast("No LocalDeploy report cards found.", "error");
    else {
      addBenchmarkRuns(runs, true);
      toast(`Imported ${runs.length} run${runs.length === 1 ? "" : "s"}.`, "success");
    }
  });
  input.value = "";
}

function compareSelectedRuns() {
  const runs = selectedComparisonRuns();
  const baseline = state.benchmarkRuns.find((r) => r.id === ($("#compare-baseline")?.value || state.compareBaselineId)) || runs[0];
  if (runs.length < 2 || !baseline) {
    toast("Select 2 or more runs to compare.", "error");
    return;
  }
  renderComparison(runs, baseline);
}

function renderResponseDrawer(testName, runs) {
  const drawer = $("#response-drawer");
  drawer.classList.remove("hidden");
  drawer.innerHTML = `<div class="card-head"><h3 class="sub">Responses: ${esc(testName)}</h3><button class="btn compact" id="close-response-drawer">Close</button></div>
    <div class="response-compare-grid">${runs
      .map((r) => {
        const t = (r.tests || []).find((x) => x.name === testName);
        return `<div class="response-compare-card">
          <b>${esc(runLabel(r))}</b>
          <div class="muted small">${t ? `${t.success ? "PASS" : "FAIL"} · acc ${t.accuracy} · ${t.elapsed_seconds}s` : "No result for this run"}</div>
          <pre>${esc(t?.response_preview || t?.error || "(no response preview)")}</pre>
        </div>`;
      })
      .join("")}</div>`;
  $("#close-response-drawer").addEventListener("click", () => drawer.classList.add("hidden"));
}

// ---------------------------------------------------------------------------
// Tab 1 — Auto-pick a profile (Step 14)
// ---------------------------------------------------------------------------
function renderRecommendProgress(body, candidates, current) {
  const rows = candidates
    .map((c) => {
      const cls = c.status === "done" ? "ok" : c.status === "running" ? "running" : "";
      const detail =
        c.status === "done"
          ? `acc ${c.avg_accuracy} · ${c.avg_latency_s}s`
          : c.status === "running"
            ? `${esc(c.currentTest || "starting…")}`
            : "queued";
      return `<li class="tune-candidate-row ${cls}">
        <span class="spin-inline ${c.status === "running" ? "" : "hidden"}"></span>
        <b>${esc(c.profile)}</b><span class="muted small">${esc(detail)}</span>
      </li>`;
    })
    .join("");
  body.innerHTML = `
    <div class="tune-progress">
      <div class="running-top">
        <div>
          <div class="model-title">${esc(current)}</div>
          <div class="muted small">Evaluating candidates against the ${esc(fmtMb(targetVram()))} GPU budget</div>
        </div>
        <span class="spin-inline"></span>
      </div>
      <div class="run-progress-track"><div class="run-progress-fill indeterminate" role="progressbar" aria-label="Tune progress"></div></div>
      <ul class="tune-candidate-list">${rows || '<li class="muted small">Fit-checking saved profiles…</li>'}</ul>
    </div>`;
}

// Named scoring tilts for the three preset buttons. Each reuses the same
// /system/recommend/stream ranking — only the accuracy/speed/headroom weights
// change — so presets stay valid for any user's config.json profiles instead
// of hardcoding specific model names.
const RECOMMEND_PRESETS = {
  safe: { quality_weight: 0.4, speed_weight: 0.3, headroom_weight: 0.3 },
  quality: { quality_weight: 0.8, speed_weight: 0.1, headroom_weight: 0.1 },
  fast: { quality_weight: 0.15, speed_weight: 0.65, headroom_weight: 0.2 },
};

async function recommendTune(weights = null) {
  const btn = $("#btn-recommend");
  const body = $("#recommend-body");
  busy(btn, true);
  $$(".preset-btn").forEach((b) => (b.disabled = true));
  const candidates = [];
  const byProfile = {};
  const addCandidate = (profile) => {
    if (byProfile[profile]) return byProfile[profile];
    const row = { profile, status: "queued" };
    byProfile[profile] = row;
    candidates.push(row);
    return row;
  };
  renderRecommendProgress(body, candidates, "Fit-checking saved profiles…");
  let finalResult = null;
  // Quick (default) samples a handful of the fastest tests per candidate to
  // keep tuning snappy; Full runs the entire test bench for a slower, more
  // thorough score. sample_size is clamped server-side to the test count.
  const fullBench = $("#recommend-full-bench")?.checked;
  const sampleSize = fullBench ? 9999 : 3;
  try {
    const result = await postMaybeStream(
      "/system/recommend/stream",
      { free_vram_mb: targetVram(), sample_size: sampleSize, ...(weights || {}) },
      (evt) => {
        if (evt.event === "recommend_start") {
          (evt.candidates || []).forEach(addCandidate);
          renderRecommendProgress(body, candidates, "Benchmarking candidates…");
        } else if (evt.event === "candidate_start") {
          const row = addCandidate(evt.profile);
          row.status = "running";
          renderRecommendProgress(body, candidates, `Benchmarking ${evt.profile}…`);
        } else if (evt.event === "test_start") {
          const row = addCandidate(evt.profile);
          row.currentTest = evt.name;
          renderRecommendProgress(body, candidates, `Benchmarking ${evt.profile}…`);
        } else if (evt.event === "candidate_end") {
          const row = addCandidate(evt.profile);
          row.status = "done";
          row.avg_accuracy = evt.avg_accuracy;
          row.avg_latency_s = evt.avg_latency_s;
          renderRecommendProgress(body, candidates, `Benchmarking candidates…`);
        } else if (evt.event === "error") {
          finalResult = { success: false, error: evt.error };
        } else if (evt.event === "recommend_end") {
          finalResult = { success: true, ...evt };
        }
      }
    );
    const res = result.streamed ? finalResult : result.json;
    if (!res) throw new Error("Stream ended without a result.");
    if (!res.success && res.success !== undefined) {
      body.innerHTML = `<div class="muted">${esc(res.error || "Could not run.")}</div>`;
      return;
    }
    if (!res.recommended) {
      body.innerHTML = `<div class="muted">${esc(res.message || "No profile fits the available VRAM.")}</div>`;
      return;
    }
    const rec = res.recommended;
    const rows = (res.candidates || [])
      .map((c) => {
        const star = c.profile === rec.profile ? " ★" : "";
        const p = state.profileData[c.profile] || {};
        const model = p.model_id || state.profileModels[c.profile] || c.profile;
        const installed = installedStatusForProfile(c.profile);
        return `<tr><td><b>${esc(c.profile)}${star}</b><div class="muted small">${esc(model)}</div></td>
          <td>${esc(p.backend || "ollama")}</td>
          <td><span class="badge ${installed.cls}">${esc(installed.label)}</span></td>
          <td class="num">${esc(c.avg_accuracy)}</td>
          <td class="num">${esc(c.avg_latency_s)}s</td>
          <td class="num">${esc(c.margin_gb ?? "—")}</td>
          <td class="num">${esc(c.score)}</td></tr>`;
      })
      .join("");
    const skipped = (res.skipped || [])
      .map((s) => `<li>${esc(s.profile)} — ${esc(s.reason)}${s.required_gb ? ` (~${esc(s.required_gb)} GB)` : ""}</li>`)
      .join("");
    const sampleTests = (res.sample_tests || []).map((t) => `<code>${esc(t)}</code>`).join(", ");
    body.innerHTML = `
      <div class="result ok tune-result">
        <div><b>Recommended:</b> ${esc(rec.profile)}</div>
        <div class="muted small">Evaluated enabled saved profiles that fit the selected GPU budget. Models must already be pulled or reachable; failed benchmark responses score 0.</div>
        <div class="muted small">Why: ${esc(rec.reasoning)}. Score favors accuracy first, then speed, then VRAM headroom.</div>
        ${sampleTests ? `<div class="muted small">Benchmarked sample tests: ${sampleTests}</div>` : ""}
        <button class="btn set-default-btn" data-profile="${esc(rec.profile)}">Set as default</button>
      </div>
      <div class="table-wrap" style="margin-top:.5rem"><table class="results">
        <thead><tr><th>Saved profile / model</th><th>Backend</th><th>Status</th><th class="num">Accuracy</th><th class="num">Latency</th><th class="num">Headroom</th><th class="num">Score</th></tr></thead>
        <tbody>${rows}</tbody></table></div>
      ${skipped ? `<h3 class="sub">Skipped (won’t fit)</h3><ul class="err-list">${skipped}</ul>` : ""}`;
    const sd = body.querySelector(".set-default-btn");
    if (sd) sd.addEventListener("click", () => setDefaultProfile(sd.dataset.profile, sd));
  } catch (err) {
    body.innerHTML = `<div class="muted">Tuning failed — ${esc(err.message)}</div>`;
    toast(`Tune failed: ${err.message}`, "error");
  } finally {
    busy(btn, false);
    $$(".preset-btn").forEach((b) => (b.disabled = false));
  }
}

async function setDefaultProfile(profile, btn) {
  busy(btn, true);
  try {
    const res = await postJSON("/system/set-default", { profile });
    if (res.success) {
      toast(`Default profile set to ${profile}.`, "success");
      state.defaultProfile = profile;
    } else {
      toast(res.error || "Could not set default.", "error");
    }
  } catch (err) {
    toast(`Set default failed: ${err.message}`, "error");
  } finally {
    busy(btn, false);
  }
}

async function loadGraderTypes() {
  try {
    const benchInfo = await getJSON("/benchmark/test-bench");
    updateBenchHelp(benchInfo);
    const example = await getJSON("/benchmark/example");
    // grader_types are returned by /benchmark/validate; fetch them cheaply.
    const report = await postJSON("/benchmark/validate", example);
    if (report.grader_types) {
      $("#grader-types").innerHTML = report.grader_types.map((t) => `<code>${esc(t)}</code>`).join(", ");
    }
  } catch {
    $("#grader-types").textContent = "contains_all, json_array_min_len, number_within, exact_match, classification_set";
  }
}

// ---------------------------------------------------------------------------
// Wire up
// ---------------------------------------------------------------------------
$("#brand-home")?.addEventListener("click", () => activateTab("serve"));
$("#btn-hardware").addEventListener("click", checkHardware);
$("#btn-status").addEventListener("click", refreshStatus);
$("#btn-serve").addEventListener("click", serveModel);
$("#btn-switch").addEventListener("click", switchModel);
$("#btn-installed").addEventListener("click", refreshInstalled);
$("#btn-updates")?.addEventListener("click", (e) => checkUpdates(e));
$("#btn-hf-search")?.addEventListener("click", (e) => checkUpdates(e));
$("#btn-fit-profiles")?.addEventListener("click", scanConfiguredFits);
$("#btn-clean-orphans")?.addEventListener("click", (e) => cleanOrphanProfiles(e.currentTarget));
$("#btn-starter-pack")?.addEventListener("click", (e) => starterPack(e));
$("#btn-free").addEventListener("click", freeMemory);
$("#btn-pull").addEventListener("click", () => pullModel());
$("#btn-builtin-bench").addEventListener("click", useBuiltInBench);
$("#btn-example").addEventListener("click", loadExample);
$("#btn-validate").addEventListener("click", validateSet);
$("#btn-run").addEventListener("click", runBenchmark);
$("#btn-stop-active")?.addEventListener("click", () => {
  if (state.activeController && !state.activeController.signal.aborted) {
    state.activeController.abort();
    toast("Stopping the current run…", "info");
  }
});
$("#btn-select-all-runs")?.addEventListener("click", () => {
  state.selectedRunIds = allBenchmarkRuns().map((r) => r.id);
  if (!state.selectedRunIds.includes(state.compareBaselineId)) {
    state.compareBaselineId = state.selectedRunIds[0] || null;
  }
  renderBenchmarkWorkspace();
});
$("#btn-deselect-all-runs")?.addEventListener("click", () => {
  state.selectedRunIds = [];
  state.compareBaselineId = null;
  renderBenchmarkWorkspace();
});
$("#upload-json").addEventListener("change", (e) => uploadFile(e.target));
$("#btn-recommend").addEventListener("click", () => recommendTune());
$$(".preset-btn").forEach((b) =>
  b.addEventListener("click", () => recommendTune(RECOMMEND_PRESETS[b.dataset.preset]))
);
$("#btn-export").addEventListener("click", exportCard);
$("#btn-export-selected").addEventListener("click", exportSelectedRuns);
$("#btn-compare").addEventListener("click", compareSelectedRuns);
$("#card-import").addEventListener("change", (e) => readCardFiles(e.target));
$("#btn-clear-runs").addEventListener("click", () => {
  const n = state.benchmarkRuns.length;
  if (!n) {
    toast("No saved runs to clear.", "info");
    return;
  }
  if (!window.confirm(`Clear all ${n} saved run${n === 1 ? "" : "s"} (including imported cards)? This cannot be undone.`)) {
    return;
  }
  state.benchmarkRuns = [];
  state.selectedRunIds = [];
  state.compareBaselineId = null;
  state.activeRunId = null;
  state.lastRun = null;
  saveBenchmarkRuns();
  renderBenchmarkWorkspace();
  toast(`Cleared ${n} run${n === 1 ? "" : "s"}.`, "success");
});
$("#compare-baseline").addEventListener("change", (e) => {
  state.compareBaselineId = e.target.value || null;
  renderAutoComparison();
});
$("#bench-profile-filter")?.addEventListener("input", renderBenchmarkProfileChips);
["#detail-filter-run", "#detail-filter-category", "#detail-filter-result"].forEach((sel) => {
  $(sel)?.addEventListener("change", () => renderDetailedResults());
});

$("#fit-filter")?.addEventListener("change", () => {
  if (!$("#fit-finder-body").textContent.includes("not been scanned")) scanConfiguredFits();
});
$("#hf-fit-filter")?.addEventListener("change", () => {
  if (!$("#updates-body").textContent.includes("No Hugging Face search")) checkUpdates();
});
$("#vram-budget-gb")?.addEventListener("input", () => {
  const raw = $("#vram-budget-gb").value.trim();
  state.vramBudgetMb = raw ? targetVram() : null;
  updateVramBudgetUI();
  scheduleFitRefresh();
});
$$(".vram-preset").forEach((btn) => {
  btn.addEventListener("click", () => {
    if (btn.dataset.vramPreset === "detected") setVramBudgetMb(state.vramTotalMb);
    else if (btn.dataset.vramPreset === "free") setVramBudgetMb(state.vramFreeMb);
    else if (btn.dataset.vramGb) setVramBudgetMb(Number(btn.dataset.vramGb) * 1024);
  });
});

// Keyboard shortcuts: Enter pulls; Cmd/Ctrl+Enter runs the benchmark from the editor.
$("#pull-model").addEventListener("keydown", (e) => {
  if (e.key === "Enter") {
    e.preventDefault();
    pullModel();
  }
});
$("#qs-editor").addEventListener("keydown", (e) => {
  if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
    e.preventDefault();
    runBenchmark();
  }
});
$("#qs-editor").addEventListener("input", () => {
  state.questionSetValidation = null;
  updateBenchmarkSummary();
});

// Make the file-upload "buttons" (a <label> wrapping a hidden <input>) reachable
// and operable by keyboard — labels aren't tab stops and hidden inputs can't be
// focused, so Tab + Enter/Space wouldn't otherwise open the file picker.
$$(".btn.file").forEach((label) => {
  label.setAttribute("tabindex", "0");
  label.setAttribute("role", "button");
  label.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      label.querySelector('input[type="file"]')?.click();
    }
  });
});

// Initial load — populate the live sections so a newcomer sees real state
// for hardware, served models, and installed models.
function setOllamaPill(installed, reachable) {
  const pill = $("#ollama-status-pill");
  if (!pill) return;
  let cls = "chip";
  let text = "";
  if (!installed) {
    cls += " bad";
    text = "Ollama: not installed";
  } else if (!reachable) {
    cls += " warn";
    text = "Ollama: not running";
  } else {
    cls += " ok";
    text = "Ollama: running";
  }
  pill.className = cls;
  pill.textContent = text;
  pill.classList.remove("hidden");
}

async function checkOllamaAvailability() {
  try {
    const res = await getJSON("/system/ollama-status");
    setOllamaPill(res.installed, res.reachable);
    if (!res.installed) {
      showOllamaHelp(null, null, "Ollama isn't installed, so pulling and serving models won't work yet. Install it, then come back here.");
    }
  } catch {
    // Non-fatal: the pull flow will surface this again if it matters.
  }
}

// Segmented control inside "Get a model": show one panel at a time.
function wireGetModelSegments() {
  const btns = $$(".seg-btn");
  btns.forEach((b) =>
    b.addEventListener("click", () => {
      const seg = b.dataset.seg;
      btns.forEach((x) => {
        const on = x === b;
        x.classList.toggle("active", on);
        x.setAttribute("aria-selected", on ? "true" : "false");
      });
      $$(".seg-panel").forEach((p) =>
        p.classList.toggle("hidden", p.dataset.segPanel !== seg)
      );
    })
  );
}

(async function init() {
  wireGetModelSegments();
  loadBenchmarkRuns();
  await loadProfiles();
  renderBenchmarkWorkspace();
  await Promise.allSettled([
    checkHardware(),
    refreshStatus(),
    refreshInstalled(),
    loadGraderTypes(),
    checkOllamaAvailability(),
  ]);
})();
