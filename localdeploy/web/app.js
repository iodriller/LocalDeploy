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
  fitCache: {},
  currentQueue: [],
  activeController: null,
  queueCancelled: false,
  // Chat playground (in-memory per page load).
  chatMessages: [],
  chatImages: [],
  chatController: null,
  chatSessionBusy: false,
  // Your-models list sort + bulk selection.
  installedSort: "default",
  installedSelection: new Set(),
  installedList: [],
  // Opt-in server-side benchmark history (reports/benchmark-history).
  benchHistoryServer: false,
  // Provider catalog (fetched once, filtered/sorted/paged client-side).
  catalog: { rows: [], providers: [], loaded: false, page: 0 },
  remoteCatalogLoaded: false,
  unifiedSearchSeq: 0,
  chatFiles: [],
  remoteCatalog: { sourceRows: [], rows: [], fits: {}, query: "", sort: "popularity-desc" },
  unloadingModels: new Set(),
  chatSessionOperation: null,
  pullRetry: null,
  // Monitor tab (Release R3): server holds hardware history; tok/s history is
  // accumulated client-side per poll since the snapshot only reports current values.
  monitor: { timer: null, tpsHistory: [] },
  // Deployment manifests (Release R5): the last validated, recreate-eligible manifest.
  manifestToRecreate: null,
  // Automated bakeoff (Release R6).
  bakeoff: { controller: null },
};

const BENCHMARK_RUNS_KEY = "localdeploy.benchmarkRuns.v1";
const BENCH_HISTORY_SERVER_KEY = "localdeploy.benchHistoryServer.v1";

function initTooltips() {
  const tooltip = $("#ui-tooltip");
  if (!tooltip) return;

  let activeTrigger = null;
  let pinned = false;
  let positionFrame = null;

  const triggerFor = (node) => (node instanceof Element ? node.closest("[data-tooltip]") : null);

  const positionTooltip = () => {
    positionFrame = null;
    if (activeTrigger && !activeTrigger.isConnected) {
      hideTooltip();
      return;
    }
    if (!activeTrigger || !tooltip.classList.contains("is-visible")) return;

    const gap = 8;
    const edge = 10;
    const triggerRect = activeTrigger.getBoundingClientRect();
    const tooltipRect = tooltip.getBoundingClientRect();
    let top = triggerRect.top - tooltipRect.height - gap;
    if (top < edge) top = triggerRect.bottom + gap;
    if (top + tooltipRect.height > window.innerHeight - edge) {
      top = Math.max(edge, triggerRect.top - tooltipRect.height - gap);
    }
    const centered = triggerRect.left + triggerRect.width / 2 - tooltipRect.width / 2;
    const left = Math.max(edge, Math.min(centered, window.innerWidth - tooltipRect.width - edge));
    tooltip.style.left = `${Math.round(left)}px`;
    tooltip.style.top = `${Math.round(top)}px`;
  };

  const schedulePosition = () => {
    if (positionFrame == null) positionFrame = requestAnimationFrame(positionTooltip);
  };

  const hideTooltip = () => {
    if (positionFrame != null) cancelAnimationFrame(positionFrame);
    positionFrame = null;
    activeTrigger?.classList.remove("tooltip-open");
    if (activeTrigger?.hasAttribute("aria-expanded")) activeTrigger.setAttribute("aria-expanded", "false");
    activeTrigger = null;
    pinned = false;
    tooltip.classList.remove("is-visible");
    tooltip.setAttribute("aria-hidden", "true");
  };

  const showTooltip = (trigger, shouldPin = false) => {
    const text = trigger?.dataset.tooltip?.trim();
    if (!text) return;
    const keepPinned = activeTrigger === trigger && pinned;
    if (activeTrigger && activeTrigger !== trigger) {
      activeTrigger.classList.remove("tooltip-open");
      if (activeTrigger.hasAttribute("aria-expanded")) activeTrigger.setAttribute("aria-expanded", "false");
    }
    activeTrigger = trigger;
    pinned = shouldPin || keepPinned;
    tooltip.textContent = text;
    tooltip.classList.add("is-visible");
    tooltip.setAttribute("aria-hidden", "false");
    trigger.classList.add("tooltip-open");
    if (trigger.hasAttribute("aria-expanded")) trigger.setAttribute("aria-expanded", "true");
    positionTooltip();
  };

  document.addEventListener("pointerover", (event) => {
    const trigger = triggerFor(event.target);
    if (!trigger || trigger.contains(event.relatedTarget)) return;
    showTooltip(trigger);
  });
  document.addEventListener("pointerout", (event) => {
    const trigger = triggerFor(event.target);
    if (!trigger || trigger !== activeTrigger || trigger.contains(event.relatedTarget) || pinned || trigger.matches(":focus")) return;
    hideTooltip();
  });
  document.addEventListener("focusin", (event) => {
    const trigger = triggerFor(event.target);
    if (trigger) showTooltip(trigger);
  });
  document.addEventListener("focusout", (event) => {
    const trigger = triggerFor(event.target);
    // event.target usually isn't a tooltip trigger at all (e.g. any other
    // button losing focus) — trigger is null then, and null === activeTrigger
    // is true exactly when no tooltip is open, so this must bail out before
    // touching trigger.matches(...) instead of relying on the equality check.
    if (!trigger || trigger !== activeTrigger || pinned || trigger.matches(":hover")) return;
    hideTooltip();
  });
  document.addEventListener("click", (event) => {
    const trigger = triggerFor(event.target);
    if (!trigger) {
      if (pinned) hideTooltip();
      return;
    }
    if (trigger.classList.contains("help-tip")) {
      event.preventDefault();
      event.stopPropagation();
    }
    if (trigger === activeTrigger && pinned) hideTooltip();
    else showTooltip(trigger, true);
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && activeTrigger) {
      hideTooltip();
      return;
    }
    const trigger = triggerFor(event.target);
    if (!trigger || (event.key !== "Enter" && event.key !== " ")) return;
    event.preventDefault();
    if (trigger === activeTrigger && pinned) hideTooltip();
    else showTooltip(trigger, true);
  });
  window.addEventListener("resize", schedulePosition);
  window.addEventListener("scroll", schedulePosition, true);
  new MutationObserver(() => {
    if (activeTrigger && !activeTrigger.isConnected) hideTooltip();
  }).observe(document.body, { childList: true, subtree: true });
}

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

// --- opt-in server-side history (reports/benchmark-history) -----------------
// localStorage dies with the browser profile; when the toggle is on, completed
// runs are mirrored to the server as JSON files and merged back in on load.
async function syncRunToServer(run) {
  try {
    const res = await postJSON("/benchmark/history/save", { run });
    if (!res.success) toast(res.error || "Could not store run on server.", "error");
  } catch (err) {
    toast(`Server history save failed: ${err.message}`, "error");
  }
}

async function loadServerHistory(notify = false) {
  try {
    const res = await getJSON("/benchmark/history");
    if (!res.success) return;
    const existing = new Set(state.benchmarkRuns.map((r) => r.id));
    const fresh = (res.runs || []).filter((r) => r && r.id && !existing.has(r.id));
    if (fresh.length) {
      addBenchmarkRuns(fresh.map((r) => ({ ...r, source: "server-history" })), false);
    }
    if (notify) toast(`Loaded ${fresh.length} run(s) from server history.`, "success");
  } catch {
    /* best-effort: a missing server history never breaks the workspace */
  }
}

function initServerHistoryToggle() {
  const cb = $("#bench-history-server");
  if (!cb) return;
  try {
    state.benchHistoryServer = localStorage.getItem(BENCH_HISTORY_SERVER_KEY) === "1";
  } catch {}
  cb.checked = state.benchHistoryServer;
  cb.addEventListener("change", async () => {
    state.benchHistoryServer = cb.checked;
    try {
      localStorage.setItem(BENCH_HISTORY_SERVER_KEY, cb.checked ? "1" : "0");
    } catch {}
    if (cb.checked) {
      // Push what this browser already has, then pull anything it's missing.
      for (const run of state.benchmarkRuns) await syncRunToServer(run);
      await loadServerHistory(true);
      toast("Completed runs will also be saved under reports/benchmark-history.", "success");
    } else {
      toast("Server-side storing is off. Files already under reports/ are kept.", "info");
    }
  });
  if (state.benchHistoryServer) void loadServerHistory();
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
  const ttft = successes.map((t) => t.metrics?.ttft_ms).filter((v) => v != null);
  return {
    tests: rows.length,
    passed: successes.length,
    avg_accuracy: Number(mean(rows.map((t) => t.accuracy || 0)).toFixed(3)),
    avg_latency_s: Number(mean(rows.map((t) => t.elapsed_seconds || 0)).toFixed(3)),
    avg_tokens_per_second: tps.length ? Number(mean(tps).toFixed(2)) : null,
    avg_ttft_ms: ttft.length ? Number(mean(ttft).toFixed(1)) : null,
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
  const profileProvenance = (input.provenance?.profiles || {})[profile] || {};
  return {
    id: input.id || runId(),
    createdAt: input.createdAt || input.generated_at || new Date().toISOString(),
    profile,
    modelId,
    backend: input.backend || profileProvenance.backend || null,
    requestedDevice,
    actualDevice,
    questionSetName: input.questionSetName || "Imported report card",
    questionSetHash: input.questionSetHash || null,
    hardware: input.hardware || {},
    provenance: input.provenance || {},
    variance: input.variance || summary.variance || {},
    aggregates: input.aggregates || [],
    repetitions: input.repetitions || summary.repetitions || 1,
    peakVramMb: input.peakVramMb ?? input.peak_vram_mb ?? null,
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
  if (state.benchHistoryServer) {
    // Mirror only freshly completed local runs — imports and server-restored
    // records are already durable somewhere.
    fresh.filter((r) => r.source === "current-run").forEach((r) => void syncRunToServer(r));
  }
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

function dismissToast(node) {
  if (!node || node.classList.contains("toast-hide")) return;
  node.classList.add("toast-hide");
  node.addEventListener("transitionend", () => node.remove(), { once: true });
  // Fallback in case the transitionend event doesn't fire (e.g. reduced-motion).
  setTimeout(() => node.remove(), 400);
}

function toast(message, kind = "info") {
  const node = document.createElement("div");
  node.className = `toast ${kind}`;
  node.textContent = message;
  // Errors are announced assertively and get more time on screen, but every
  // toast eventually fades on its own — a pile of undismissed error toasts
  // is worse than losing one you didn't read in time. Click still dismisses
  // early either way.
  const timeoutMs = kind === "error" ? 9000 : 5000;
  if (kind === "error") node.setAttribute("role", "alert");
  node.title = "Click to dismiss";
  node.addEventListener("click", () => dismissToast(node));
  setTimeout(() => dismissToast(node), timeoutMs);
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
    const pool = hw.gpu_summary?.best_pool_total_mb ?? g.vram_total_mb;
    const count = hw.gpu_summary?.gpu_count || hw.gpus.length;
    const mem = g.unified_memory ? "unified memory" : `${fmtMb(pool)} compatible VRAM`;
    chip.innerHTML = `<span class="dot"></span>${count > 1 ? `${count} GPUs` : esc(g.name)} · ${mem}${ram}`;
  } else {
    chip.innerHTML = `<span class="dot none"></span>CPU only${ram}`;
  }
  chip.classList.remove("hidden");
}

function syncHardwareState(hw) {
  const g = hw?.gpus?.[0];
  state.vramTotalMb = hw?.gpu_summary?.best_pool_total_mb ?? g?.vram_total_mb ?? null;
  state.vramFreeMb = hw?.gpu_summary?.best_pool_free_mb ?? g?.vram_free_mb ?? null;
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
      "#btn-recommend-models"
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
    if (state.remoteCatalog.rows.length) {
      void loadRemoteCatalogFits().then(() => renderRemoteCatalog());
    }
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
      : `<span class="muted">RAM details unavailable.</span>`;
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
  } else if (name === "chat") {
    void refreshLiveModelState(true).then(() => $("#chat-input")?.focus());
  }
  if (name === "monitor") {
    startMonitorPolling();
  } else {
    stopMonitorPolling();
  }
}

// ---------------------------------------------------------------------------
// Monitor tab (Release R3)
// ---------------------------------------------------------------------------
const MONITOR_POLL_MS = 5000;
const MONITOR_TPS_HISTORY_MAX = 180;

function svgSparkline(series, { unit = "", suffix = "", color = "var(--accent)", max = null } = {}) {
  const values = series.map((v) => (v == null ? null : Number(v)));
  const known = values.filter((v) => v != null);
  if (!known.length) {
    return `<div class="muted small monitor-spark-empty">No data yet — stay on this tab to build history.</div>`;
  }
  const width = 320;
  const height = 64;
  const vmax = max != null ? max : Math.max(...known, 1);
  const n = values.length;
  const points = values
    .map((v, i) => {
      if (v == null) return null;
      const x = n <= 1 ? width : (i / (n - 1)) * width;
      const y = height - Math.max(0, Math.min(1, v / (vmax || 1))) * height;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .filter(Boolean)
    .join(" ");
  const last = known[known.length - 1];
  return `<svg class="monitor-spark" viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" role="img" aria-label="Rolling chart, latest ${esc(String(last))}${esc(unit)}">
      <polyline points="${esc(points)}" fill="none" stroke="${color}" stroke-width="2" vector-effect="non-scaling-stroke"></polyline>
    </svg>
    <div class="muted small monitor-spark-caption">latest: ${esc(String(last))}${esc(unit)}${suffix ? ` · ${esc(suffix)}` : ""}</div>`;
}

function renderMonitorOverview(snap) {
  const hw = snap.hardware || {};
  $("#monitor-ollama-pill").textContent = snap.ollama_reachable ? "Ollama: connected" : "Ollama: unreachable";
  $("#monitor-ollama-pill").classList.toggle("chip-bad", !snap.ollama_reachable);
  const rows = [
    ["VRAM used", hw.vram_used_mb != null ? `${fmtMb(hw.vram_used_mb)} / ${fmtMb(hw.vram_total_mb)}` : "—"],
    ["GPU utilization", hw.gpu_utilization_pct != null ? `${hw.gpu_utilization_pct}%` : "not reported by this GPU"],
    ["RAM used", hw.ram_used_mb != null ? `${fmtMb(hw.ram_used_mb)} / ${fmtMb(hw.ram_total_mb)}` : "—"],
    ["CPU utilization", hw.cpu_percent != null ? `${hw.cpu_percent}%` : "—"],
  ];
  $("#monitor-overview").innerHTML = rows
    .map(([label, value]) => `<div class="monitor-stat"><span class="muted small">${esc(label)}</span><strong>${esc(value)}</strong></div>`)
    .join("");

  const hwHistory = (snap.history && snap.history.hardware) || [];
  $("#monitor-chart-vram").innerHTML = svgSparkline(hwHistory.map((h) => h.vram_pct), { unit: "%" });
  $("#monitor-chart-gpu").innerHTML = svgSparkline(hwHistory.map((h) => h.gpu_utilization_pct), { unit: "%" });

  const modelsForTps = snap.models || [];
  const activeRates = modelsForTps.map((m) => m.recent_tokens_per_second).filter((v) => v != null);
  const avgTps = activeRates.length ? activeRates.reduce((a, b) => a + b, 0) / activeRates.length : null;
  state.monitor.tpsHistory.push(avgTps);
  if (state.monitor.tpsHistory.length > MONITOR_TPS_HISTORY_MAX) state.monitor.tpsHistory.shift();
  $("#monitor-chart-tps").innerHTML = svgSparkline(state.monitor.tpsHistory, { unit: " tok/s" });
}

function renderMonitorAlerts(alerts) {
  const box = $("#monitor-alerts");
  if (!alerts || !alerts.length) {
    box.classList.add("hidden");
    box.innerHTML = "";
    return;
  }
  box.classList.remove("hidden");
  box.innerHTML = alerts
    .map((a) => `<div class="monitor-alert monitor-alert-${esc(a.level)}">${a.level === "warning" ? "⚠" : "ℹ"} ${esc(a.text)}</div>`)
    .join("");
}

function renderMonitorModelCard(m) {
  const uptime = m.uptime_seconds != null ? fmtDuration(m.uptime_seconds) : "unknown";
  const vram = m.size_vram_mb != null ? fmtMb(m.size_vram_mb) : "—";
  const placement = m.placement ? `<span class="badge ${m.placement === "GPU" ? "on" : m.placement === "Split" ? "split" : "cpu"}">${esc(m.placement)}</span>` : "";
  const deviceNote = m.requested_device && m.placement && m.requested_device.toUpperCase() !== m.placement.toUpperCase()
    ? `<div class="muted small">Requested ${esc(m.requested_device)}, actually on ${esc(m.placement)}</div>`
    : "";
  return `<div class="fit-card model-card">
    <div class="running-top">
      <div class="model-identity">
        <span class="model-mark running" aria-hidden="true">M</span>
        <div>
          <div class="model-title">${esc(m.name)}</div>
          <div class="muted small">Running for ${esc(uptime)}</div>
        </div>
      </div>
      ${placement}
    </div>
    <div class="monitor-model-stats">
      <div><span class="muted small">VRAM</span><strong>${esc(vram)}</strong></div>
      <div><span class="muted small">tok/s (recent)</span><strong>${m.recent_tokens_per_second != null ? esc(m.recent_tokens_per_second) : "—"}</strong></div>
      <div><span class="muted small">Requests</span><strong>${esc(m.request_count)}${m.failure_count ? ` (${esc(m.failure_count)} failed)` : ""}</strong></div>
      <div><span class="muted small">TTFT (median)</span><strong>${m.median_ttft_ms != null ? `${esc(m.median_ttft_ms)} ms` : "—"}</strong></div>
    </div>
    ${deviceNote}
    <div class="row gap wrap fit-card-actions">
      <span class="spacer"></span>
      <button class="btn compact monitor-stop-btn" data-model="${esc(m.name)}">Stop</button>
    </div>
  </div>`;
}

function renderMonitorModels(models) {
  const body = $("#monitor-models");
  if (!models || !models.length) {
    body.innerHTML = `<div class="monitor-empty">
      <p class="muted">Nothing to monitor yet — this fills in once a model is running.</p>
      <p class="muted small">Head to <b>Setup &amp; Deploy</b>, pull or pick a model, and deploy it. Come back here to watch its VRAM, throughput, and request history live.</p>
      <button class="btn primary compact" id="monitor-goto-deploy">Go to Setup &amp; Deploy</button>
    </div>`;
    $("#monitor-goto-deploy")?.addEventListener("click", () => activateTab("serve"));
    return;
  }
  body.innerHTML = models.map(renderMonitorModelCard).join("");
  $$(".monitor-stop-btn", body).forEach((b) =>
    b.addEventListener("click", async () => {
      busy(b, true);
      try {
        await postJSON("/models/stop", { model: b.dataset.model });
        toast(`Stopped '${b.dataset.model}'.`, "success");
      } catch (err) {
        toast(`Stop failed: ${err.message}`, "error");
      } finally {
        busy(b, false);
        void refreshMonitor();
      }
    })
  );
}

function renderMonitorRequests(requests) {
  const tbody = $("#monitor-requests-table tbody");
  const empty = $("#monitor-requests-empty");
  if (!requests || !requests.length) {
    tbody.innerHTML = "";
    $("#monitor-requests-table").classList.add("hidden");
    empty.classList.remove("hidden");
    return;
  }
  empty.classList.add("hidden");
  $("#monitor-requests-table").classList.remove("hidden");
  tbody.innerHTML = requests
    .map((r) => {
      const time = new Date(r.ts * 1000).toLocaleTimeString();
      const result = r.success ? `<span class="badge on">ok</span>` : `<span class="badge wont" title="${esc(r.error || "")}">error</span>`;
      return `<tr>
        <td>${esc(time)}</td>
        <td>${esc(r.model || "—")}</td>
        <td><span class="badge">${esc(r.source || "—")}</span></td>
        <td>${result}</td>
        <td class="num">${r.prompt_tokens ?? "—"}</td>
        <td class="num">${r.output_tokens ?? "—"}</td>
        <td class="num">${r.ttft_ms != null ? `${r.ttft_ms} ms` : "—"}</td>
        <td class="num">${r.tokens_per_second ?? "—"}</td>
        <td class="num">${r.elapsed_seconds}s</td>
      </tr>`;
    })
    .join("");
}

async function refreshMonitor() {
  try {
    const snap = await getJSON("/system/monitor");
    renderMonitorOverview(snap);
    renderMonitorAlerts(snap.alerts);
    renderMonitorModels(snap.models);
    renderMonitorRequests(snap.requests);
  } catch (err) {
    $("#monitor-overview").innerHTML = `<div class="muted">Monitor snapshot failed: ${esc(err.message)}</div>`;
  }
}

function startMonitorPolling() {
  if (state.monitor.timer) return;
  void refreshMonitor();
  state.monitor.timer = setInterval(() => void refreshMonitor(), MONITOR_POLL_MS);
}

function stopMonitorPolling() {
  if (state.monitor.timer) {
    clearInterval(state.monitor.timer);
    state.monitor.timer = null;
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
    renderChatModelOptions();
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

// (Re)build profile <select>s, annotating profiles whose model isn't
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
      state.lastHardware = { ...hw, gpu: null, vram_total_mb: null, vram_free_mb: null };
      body.innerHTML = `<div class="muted" style="margin-bottom:.5rem">${esc(hw.message || "No GPU detected.")}</div>
        <div class="kv">${cpuRamRows(hw.system)}</div>`;
      updateVramBudgetUI();
      return;
    }
    const g = hw.gpus?.[0];
    if (!g) {
      body.innerHTML = `<div class="muted">GPU reported but no details available.</div>`;
      return;
    }
    state.lastHardware = {
      ...hw,
      gpu: g.name,
      vram_total_mb: hw.gpu_summary?.best_pool_total_mb ?? g.vram_total_mb,
      vram_free_mb: hw.gpu_summary?.best_pool_free_mb ?? g.vram_free_mb,
    };
    const note = hw.message
      ? `<div class="muted small" style="margin-bottom:.5rem">${esc(hw.message)}</div>`
      : "";
    const gpuRows = (hw.gpus || []).map((gpu, index) => {
      const memory = gpu.unified_memory
        ? "Unified memory"
        : `${fmtMb(gpu.vram_total_mb)} total · ${fmtMb(gpu.vram_free_mb)} free`;
      const source = gpu.vram_estimated ? "estimated" : (gpu.memory_source || gpu.backend || "detected");
      return `<span class="k">GPU ${index + 1}</span><span>${esc(gpu.name)} · ${esc(gpu.vendor || "unknown")} / ${esc(gpu.backend || "unknown")}</span>
        <span class="k">Memory</span><span>${memory} · ${esc(source)}</span>`;
    }).join("");
    const pool = hw.gpu_summary?.best_pool_total_mb
      ? `<span class="k">Fit pool</span><span>${fmtMb(hw.gpu_summary.best_pool_total_mb)} total · ${fmtMb(hw.gpu_summary.best_pool_free_mb)} free compatible memory</span>`
      : "";
    body.innerHTML = `${note}<div class="kv">
      ${gpuRows}
      ${pool}
      ${cpuRamRows(hw.system)}
    </div>`;
  } catch (err) {
    toast(`Hardware check failed: ${err.message}`, "error");
  } finally {
    busy(btn, false);
    updateProgressRail();
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

const QUANT_EXPLANATIONS = {
  F16: "16-bit floating-point weights. Highest memory use and a reference-quality baseline.",
  BF16: "16-bit brain floating-point weights. Reference quality with the same broad memory class as F16.",
  Q8_0: "8-bit quantization. Near-reference quality, but much larger than common 4-bit or 5-bit files.",
  Q6_K: "About 6-bit K-quant. High quality when there is enough RAM or VRAM headroom.",
  Q5_K_M: "5-bit K-quant, medium variant. More memory and usually more quality than Q4_K_M.",
  Q4_K_M: "4-bit K-quant, medium variant. A common balance of model quality, size, and speed.",
  Q4_0: "Older 4-bit quantization. Small and widely supported, but usually less accurate than Q4_K_M.",
  Q4_0_QAT: "4-bit quantization-aware-trained weights. The model was trained to preserve quality at this precision.",
  IQ4_XS: "Importance-aware 4-bit, extra-small variant. Saves memory to fit larger models or context windows.",
  Q3_K_M: "3-bit K-quant, medium variant. Very compact, with a more noticeable quality tradeoff.",
};

function quantExplanation(value) {
  const quant = String(value || "").trim().toUpperCase();
  if (!quant) return "Quantization reduces model weight precision to use less disk space and memory.";
  if (QUANT_EXPLANATIONS[quant]) return QUANT_EXPLANATIONS[quant];
  const bits = quant.match(/(?:^|_)[QI]?(\d+)(?:_|$)/)?.[1];
  const family = quant.includes("_K") ? " It uses the newer K-quant family." : "";
  const variant = quant.endsWith("_M") ? " M means the medium mixed-precision variant." : "";
  return `${bits ? `Approximately ${bits}-bit quantized weights.` : "Quantized model weights."}${family}${variant}`;
}

function quantLabelHtml(value, className = "badge") {
  if (!value) return `<span class="muted">—</span>`;
  const explanation = quantExplanation(value);
  return `<span class="${esc(className)} quant-label tooltip-target" tabindex="0" role="button" aria-expanded="false" data-tooltip="${esc(explanation)}" aria-label="${esc(value)}: ${esc(explanation)}">${esc(value)}<span class="quant-info" aria-hidden="true">?</span></span>`;
}

function ollamaModelNamesMatch(left, right) {
  const a = String(left || "").toLowerCase();
  const b = String(right || "").toLowerCase();
  if (!a || !b) return false;
  if (a === b) return true;
  if (!a.includes(":") && b === `${a}:latest`) return true;
  if (!b.includes(":") && a === `${b}:latest`) return true;
  return false;
}

function runningDetailForInstalled(modelName) {
  return (
    (state.runningDetails || []).find((item) => ollamaModelNamesMatch(item.name, modelName)) ||
    ((state.servedModels || []).some((name) => ollamaModelNamesMatch(name, modelName)) ? { name: modelName } : null)
  );
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
        ...s.hardware,
        gpu: s.hardware.gpus?.[0]?.name ?? null,
        vram_total_mb: s.hardware.gpu_summary?.best_pool_total_mb ?? s.hardware.gpus?.[0]?.vram_total_mb ?? null,
        vram_free_mb: s.hardware.gpu_summary?.best_pool_free_mb ?? s.hardware.gpus?.[0]?.vram_free_mb ?? null,
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
          return `<div class="running-card model-card" data-model="${esc(m.name)}">
            <div class="running-top">
              <div class="model-identity">
                <span class="model-mark running" aria-hidden="true">R</span>
                <div>
                  <div class="model-title">${esc(m.name)}</div>
                  <div class="muted small">${esc(activity)} · ${esc(formatExpires(m.expires_at))}</div>
                </div>
              </div>
              <div class="row gap">
                ${place}
                <button class="btn danger compact kill-model-btn" title="Unload this model from memory/VRAM">Unload</button>
              </div>
            </div>
            ${vramBarHtml(vramMb, total, usedLabel)}
            <div class="model-meta-grid">
              <span>⚡ VRAM in use</span><b>${esc(usedLabel)}</b>
              <span>💾 Size on disk</span><b>${fmtMb(diskMb)}</b>
              <span>GPU residency</span><b>${m.gpu_percent != null ? `${esc(m.gpu_percent)}%` : "?"}</b>
            </div>
            ${apiSnippetHtml(m.name)}
            <div class="row gap wrap" style="margin-top:0.4rem">
              <button class="btn compact export-manifest-btn" data-model="${esc(m.name)}">Export deployment</button>
              <button class="btn compact use-elsewhere-btn" data-model="${esc(m.name)}">Use elsewhere ↗</button>
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
    $$(".export-manifest-btn", body).forEach((b) => b.addEventListener("click", () => exportManifest(b.dataset.model, b)));
    $$(".use-elsewhere-btn", body).forEach((b) => b.addEventListener("click", () => openIntegrationSnippets(b.dataset.model, b)));
    wireCopyButtons(body);
  } catch (err) {
    state.servedModels = [];
    state.runningDetails = [];
    state.runningPlacements = {};
    toast(`Status failed: ${err.message}`, "error");
  } finally {
    busy(btn, false);
    if (state.installedLoaded) renderInstalledList();
    renderChatModelOptions();
    updateProgressRail();
  }
}

// The full local API address for a served model, with one-click copy: the
// endpoint URL for OpenAI-compatible clients, and a ready-to-run curl.
function apiEndpointBase() {
  return `${window.location.origin}/v1/chat/completions`;
}

function apiDocsUrl() {
  return `${window.location.origin}/docs`;
}

function curlSnippetFor(model) {
  return [
    `curl ${apiEndpointBase()} \\`,
    `  -H "Content-Type: application/json" \\`,
    `  -d '{"model": "${model}", "messages": [{"role": "user", "content": "Hello!"}]}'`,
  ].join("\n");
}

function apiSnippetHtml(model) {
  const url = apiEndpointBase();
  const docs = apiDocsUrl();
  return `<div class="api-snippet">
    <span class="eyebrow">🔌 Use via API</span>
    <div class="api-snippet-row">
      <code class="api-url" title="OpenAI-compatible endpoint — point any client here with model: &quot;${esc(model)}&quot; and any API key">${esc(url)}</code>
      <button class="btn compact copy-btn" data-copy="${esc(url)}" title="Copy endpoint URL">⧉ URL</button>
      <button class="btn compact copy-btn" data-copy="${esc(curlSnippetFor(model))}" title="Copy a ready-to-run curl request for this model">⧉ curl</button>
      <a class="btn compact api-docs-link" href="${esc(docs)}" target="_blank" rel="noopener" title="Open Swagger UI with every endpoint and its request and response definitions">API docs ↗</a>
    </div>
  </div>`;
}

function wireCopyButtons(root) {
  $$(".copy-btn", root).forEach((b) =>
    b.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(b.dataset.copy);
        const was = b.textContent;
        b.textContent = "✓ Copied";
        setTimeout(() => (b.textContent = was), 1400);
      } catch (err) {
        toast(`Copy failed: ${err.message}`, "error");
      }
    })
  );
}

function waitMs(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function waitForModelToUnload(name, attempts = 12) {
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    if (attempt) await waitMs(400);
    const status = await getJSON("/system/status");
    const running = status.ollama?.running || [];
    state.runningDetails = running;
    state.servedModels = status.served_models || running.map((item) => item.name).filter(Boolean);
    if (!running.some((item) => ollamaModelNamesMatch(item.name, name))) return true;
  }
  return false;
}

async function killRunningModel(name, btn) {
  if (!name) return;
  if (state.unloadingModels.has(name)) return;
  state.unloadingModels.add(name);
  const isChatModel = ollamaModelNamesMatch(chatSelectedModel(), name);
  if (isChatModel) state.chatSessionOperation = "unloading";
  busy(btn, true);
  const originalText = btn?.textContent;
  if (btn) btn.textContent = "Unloading…";
  updateChatModelState();
  try {
    const res = await postJSON("/models/stop", { model: name });
    if (!res.success) throw new Error(res.error || res.message || "Could not unload model.");
    const confirmed = res.confirmed === true || res.status === "unloaded" || await waitForModelToUnload(name);
    if (!confirmed) throw new Error(`Ollama still reports ${name} as loaded. Retry unload or refresh status.`);
    toast(`Unloaded ${name}.`, "success");
    await refreshLiveModelState(true);
  } catch (err) {
    toast(`Unload failed: ${err.message}`, "error");
  } finally {
    state.unloadingModels.delete(name);
    if (isChatModel) state.chatSessionOperation = null;
    busy(btn, false);
    if (btn && btn.isConnected && originalText) btn.textContent = originalText;
    if (state.installedLoaded) renderInstalledList();
    updateChatModelState();
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
    // A real refresh invalidates cached fit results (models may have been
    // pulled/deleted, or free VRAM shifted); re-sorting alone keeps the cache.
    state.fitCache = {};
    if (!data.success) {
      state.installedByName = {};
      state.installedList = [];
      state.installedLoaded = false; // unknown, so nothing gets hidden as "not pulled"
      renderBenchmarkProfileChips();
      renderProfileSelectOptions();
      renderChatModelOptions();
      body.innerHTML = `<div class="muted">${esc(data.error || "Ollama unreachable.")}</div>`;
      return;
    }
    state.installedByName = {};
    state.installedLoaded = true;
    state.installedList = data.installed;
    // Drop selections for models that no longer exist.
    const names = new Set(data.installed.map((m) => m.name));
    state.installedSelection = new Set([...state.installedSelection].filter((n) => names.has(n)));
    if (!data.installed.length) {
      renderBenchmarkProfileChips();
      renderProfileSelectOptions();
      renderChatModelOptions();
      updateDiskSummary();
      body.innerHTML = `<div class="muted">No models pulled yet — grab one from <b>Get a model</b> above.</div>`;
      return;
    }
    data.installed.forEach((m) => {
      if (m.name) state.installedByName[m.name] = m;
    });
    renderBenchmarkProfileChips();
    renderProfileSelectOptions();
    renderChatModelOptions();
    renderInstalledList();
  } catch (err) {
    state.installedByName = {};
    state.installedList = [];
    state.installedLoaded = false;
    renderChatModelOptions();
    toast(`Installed list failed: ${err.message}`, "error");
  } finally {
    busy(btn, false);
    updateProgressRail();
    if (state.remoteCatalog.rows.length) renderRemoteCatalog();
  }
}

function sortedInstalled() {
  const list = state.installedList.slice();
  const sort = state.installedSort;
  if (sort === "size") list.sort((a, b) => (b.size || 0) - (a.size || 0));
  else if (sort === "recent") list.sort((a, b) => String(b.modified_at || "").localeCompare(String(a.modified_at || "")));
  else if (sort === "name") list.sort((a, b) => String(a.name).localeCompare(String(b.name)));
  return list;
}

function updateDiskSummary() {
  const slot = $("#models-disk-summary");
  if (!slot) return;
  const list = state.installedList;
  if (!list.length) {
    slot.textContent = "";
    return;
  }
  const totalGb = list.reduce((sum, m) => sum + (m.size || 0), 0) / 1e9;
  slot.textContent = `${list.length} model${list.length === 1 ? "" : "s"} · ${totalGb.toFixed(1)} GB on disk`;
}

function updateBulkDeleteBar() {
  const bar = $("#bulk-delete-bar");
  if (!bar) return;
  const selected = [...state.installedSelection];
  if (!selected.length) {
    bar.classList.add("hidden");
    return;
  }
  const bytes = selected.reduce((sum, n) => sum + (state.installedByName[n]?.size || 0), 0);
  $("#bulk-delete-label").textContent =
    `${selected.length} model${selected.length === 1 ? "" : "s"} selected · ${(bytes / 1e9).toFixed(1)} GB`;
  bar.classList.remove("hidden");
}

function renderInstalledList() {
  const body = $("#installed-body");
  if (!body) return;
  updateDiskSummary();
  body.innerHTML =
    `<div class="mlist">` +
    sortedInstalled()
      .map((m) => {
        const diskGb = m.size ? (m.size / 1e9).toFixed(1) : null;
        const d = m.details || {};
        const quant = d.quantization_level ? quantLabelHtml(d.quantization_level, "badge model-quant") : "";
        const params = d.parameter_size ? `<span class="meta">${esc(d.parameter_size)}</span>` : "";
        const date = m.modified_at ? `<span class="meta">updated ${esc(m.modified_at.slice(0, 10))}</span>` : "";
        const disk = diskGb
          ? `<span class="meta disk-chip" title="Space this model takes on your drive (not the memory it needs to run)">💾 ${esc(diskGb)} GB disk</span>`
          : "";
        const running = runningDetailForInstalled(m.name);
        const unloading = [...state.unloadingModels].some((name) => ollamaModelNamesMatch(name, m.name));
        const loaded = running ? `<span class="badge on">● loaded</span>` : "";
        const loadedHint = running ? `<span class="meta">${esc(formatExpires(running.expires_at))}</span>` : "";
        const primaryAction = running || unloading
          ? `<button class="btn danger unload-installed-btn${unloading ? " loading" : ""}" title="Unload this model from RAM and VRAM"${unloading ? " disabled" : ""}>${unloading ? "Unloading…" : "■ Unload"}</button>`
          : `<button class="btn primary start-installed-btn" title="Load this model into memory and start serving it">▶ Deploy</button>`;
        const checked = state.installedSelection.has(m.name) ? " checked" : "";
        return `<div class="mrow model-row" data-model="${esc(m.name)}">
          <input type="checkbox" class="model-select" title="Select for bulk delete"${checked} />
          <div class="model-row-main">
            <span class="name">${esc(m.name)}</span>
            <span class="model-row-meta">${params}${quant}${disk}${date}${loaded}${loadedHint}</span>
            <span class="fit"><span class="muted small">⚡ checking VRAM fit…</span></span>
          </div>
          <span class="spacer"></span>
          ${primaryAction}
          <button class="btn edit-tuning-btn" title="Edit this model's run profile (context, KV cache, GPU layers…)">⚙ Tune</button>
          <button class="btn compact fit-btn" title="Re-run the VRAM fit estimate (runs automatically when the list loads)">↻</button>
          <button class="btn danger del-btn" title="Delete from disk${diskGb ? ` — frees ${esc(diskGb)} GB` : ""}">🗑 Delete</button>
        </div>`;
      })
      .join("") +
    `</div>`;
  $$(".fit-btn", body).forEach((b) =>
    b.addEventListener("click", () => fitCheckRow(b.closest(".mrow"), true))
  );
  $$(".del-btn", body).forEach((b) =>
    b.addEventListener("click", () => deleteModel(b.closest(".mrow").dataset.model, b))
  );
  $$(".start-installed-btn", body).forEach((b) =>
    b.addEventListener("click", () => startInstalledModel(b.closest(".mrow").dataset.model, b))
  );
  $$(".unload-installed-btn", body).forEach((b) =>
    b.addEventListener("click", () => killRunningModel(b.closest(".mrow").dataset.model, b))
  );
  $$(".edit-tuning-btn", body).forEach((b) =>
    b.addEventListener("click", () => openTuningEditor(b.closest(".mrow").dataset.model))
  );
  $$(".model-select", body).forEach((cb) =>
    cb.addEventListener("change", () => {
      const model = cb.closest(".mrow").dataset.model;
      if (cb.checked) state.installedSelection.add(model);
      else state.installedSelection.delete(model);
      updateBulkDeleteBar();
    })
  );
  updateBulkDeleteBar();
  // Auto-run the fit check for each row so warnings appear without a click.
  $$(".mrow", body).forEach((row) => fitCheckRow(row));
}

async function bulkDeleteSelected(btn) {
  const selected = [...state.installedSelection];
  if (!selected.length) return;
  const bytes = selected.reduce((sum, n) => sum + (state.installedByName[n]?.size || 0), 0);
  const preview = selected.slice(0, 12).join("\n  ");
  const more = selected.length > 12 ? `\n  …and ${selected.length - 12} more` : "";
  if (!window.confirm(`Delete ${selected.length} model(s) from disk, freeing ~${(bytes / 1e9).toFixed(1)} GB?\n\n  ${preview}${more}\n\nThis cannot be undone (models can be pulled again).`)) return;
  busy(btn, true);
  let deleted = 0;
  try {
    for (const model of selected) {
      const res = await postJSON("/models/delete", { model });
      if (res.success) {
        deleted += 1;
        state.installedSelection.delete(model);
      } else {
        toast(res.error || res.message || `Could not delete ${model}.`, "error");
      }
    }
    toast(`Deleted ${deleted} model(s).`, "success");
    await refreshLiveModelState(true);
  } catch (err) {
    toast(`Bulk delete failed: ${err.message}`, "error");
  } finally {
    busy(btn, false);
  }
}

async function fitCheckRow(row, force = false) {
  const model = row.dataset.model;
  const installed = state.installedByName[model] || {};
  const details = installed.details || {};
  const slot = $(".fit", row);
  const btn = $(".fit-btn", row);
  const render = (res) => {
    if (res.verdict) {
      const req = res.estimate_gb?.required;
      const free = res.free_vram_gb;
      const detail = req != null
        ? `⚡ needs ~${req} GB VRAM${free != null ? ` of ${free} GB budget` : ""}`
        : "";
      slot.innerHTML = `<div class="fit-summary">${fitBadge(res)}<span class="meta" title="Estimated memory to run this model (weights + KV cache + overhead) — different from its size on disk">${esc(detail)}</span></div>${fitMeterHtml(res)}`;
    } else {
      slot.innerHTML = `<span class="muted">${esc(res.message || "n/a")}</span>`;
    }
  };
  // Re-sorting or re-selecting re-renders the whole list; without a cache that
  // refires one fit request per model every time. Budget changes make a new key.
  const cacheKey = `${model}|${targetVram() ?? "auto"}`;
  if (!force && state.fitCache[cacheKey]) {
    render(state.fitCache[cacheKey]);
    return;
  }
  busy(btn, true);
  slot.textContent = "…";
  try {
    const res = await postJSON("/system/fit-check", fitRequestForModel(model, details, installed.size));
    state.fitCache[cacheKey] = res;
    render(res);
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

// ---------------------------------------------------------------------------
// Deployment manifests + integration snippets (Release R5)
// ---------------------------------------------------------------------------
function simpleModal(title, subtitle, bodyHtml) {
  const overlay = document.createElement("div");
  overlay.className = "modal-overlay";
  overlay.innerHTML = `<div class="modal-card" role="dialog" aria-modal="true">
      <div class="card-head">
        <div><h3 class="sub">${esc(title)}</h3>${subtitle ? `<div class="muted small">${subtitle}</div>` : ""}</div>
        <button class="btn compact modal-close">✕</button>
      </div>
      ${bodyHtml}
    </div>`;
  document.body.appendChild(overlay);
  const close = () => overlay.remove();
  overlay.addEventListener("click", (e) => { if (e.target === overlay) close(); });
  $$(".modal-close", overlay).forEach((b) => b.addEventListener("click", close));
  return overlay;
}

async function exportManifest(modelId, btn) {
  busy(btn, true);
  try {
    const { name } = profileForModel(modelId);
    const out = await postJSON("/system/manifest/export", { profile: name, model_id: modelId });
    if (!out.success) throw new Error(out.error || "Export failed.");
    const overlay = simpleModal(
      "Deployment manifest",
      `<code>${esc(modelId)}</code> — reproducible, human-readable YAML (JSON also available below).`,
      `<pre class="log manifest-yaml">${esc(out.yaml)}</pre>
       <div class="row gap wrap" style="margin-top:0.5rem">
         <button class="btn primary" id="manifest-copy-yaml">Copy YAML</button>
         <button class="btn" id="manifest-download-yaml">Download .yaml</button>
         <button class="btn" id="manifest-download-json">Download .json</button>
       </div>`
    );
    $("#manifest-copy-yaml", overlay)?.addEventListener("click", async () => {
      await navigator.clipboard.writeText(out.yaml);
      toast("Manifest YAML copied.", "success");
    });
    $("#manifest-download-yaml", overlay)?.addEventListener("click", () =>
      downloadFile(`localdeploy-manifest-${modelId.replace(/[^\w.-]+/g, "_")}.yaml`, out.yaml, "text/yaml")
    );
    $("#manifest-download-json", overlay)?.addEventListener("click", () =>
      downloadFile(`localdeploy-manifest-${modelId.replace(/[^\w.-]+/g, "_")}.json`, out.json, "application/json")
    );
  } catch (err) {
    toast(`Export failed: ${err.message}`, "error");
  } finally {
    busy(btn, false);
  }
}

async function openIntegrationSnippets(modelId, btn) {
  busy(btn, true);
  try {
    const { data } = profileForModel(modelId);
    const context = data?.safe_context_limit || data?.context_limit || 8192;
    const out = await getJSON(`/system/integration-snippets?model=${encodeURIComponent(modelId)}&context=${context}`);
    if (!out.success) throw new Error("Could not load integration snippets.");
    const cardsHtml = out.cards
      .map(
        (c, i) => `<div class="fit-card integration-card">
          <div class="model-title">${esc(c.label)}</div>
          <pre class="log integration-snippet">${esc(c.snippet)}</pre>
          <button class="btn compact integration-copy-btn" data-idx="${i}">Copy</button>
        </div>`
      )
      .join("");
    const overlay = simpleModal(
      "Use elsewhere",
      `<code>${esc(modelId)}</code> — copy-paste config for common tools, using this app's OpenAI-compatible <code>/v1</code> endpoints.`,
      `<div class="fit-grid">${cardsHtml}</div>`
    );
    $$(".integration-copy-btn", overlay).forEach((b) =>
      b.addEventListener("click", async () => {
        await navigator.clipboard.writeText(out.cards[Number(b.dataset.idx)].snippet);
        toast("Copied.", "success");
      })
    );
  } catch (err) {
    toast(`Could not load integration snippets: ${err.message}`, "error");
  } finally {
    busy(btn, false);
  }
}

function compatibilityReportHtml(report) {
  const symbolFor = { ok: "✓", bad: "✕", info: "△", unknown: "?" };
  const rows = (report.diffs || [])
    .map((d) => `<div class="compat-row compat-${esc(d.symbol)}"><span class="compat-mark">${symbolFor[d.symbol] || "•"}</span> ${esc(d.text)}</div>`)
    .join("");
  const subs = (report.substitutions || []).map((s) => `<div class="compat-row compat-info"><span class="compat-mark">△</span> ${esc(s)}</div>`).join("");
  const verdict = report.can_recreate
    ? `<span class="badge on">Can recreate here</span>`
    : `<span class="badge wont">Cannot recreate as-is</span>`;
  return `<div class="row gap wrap" style="margin-bottom:0.5rem">${verdict}</div>${rows}${subs}`;
}

function parseManifestInput(text) {
  const trimmed = (text || "").trim();
  if (!trimmed) return null;
  try {
    const parsed = JSON.parse(trimmed);
    return parsed?.manifest || parsed; // accept either the raw manifest or an export{...} envelope
  } catch {
    return null;
  }
}

async function validateManifest(btn) {
  const text = $("#manifest-import-text")?.value;
  const manifest = parseManifestInput(text);
  const body = $("#manifest-body");
  if (!manifest) {
    body.innerHTML = `<div class="muted">Paste or load a manifest JSON first (YAML export files should be re-saved/copied as JSON, or use "Download .json" from Export deployment).</div>`;
    $("#btn-manifest-recreate").disabled = true;
    return;
  }
  busy(btn, true);
  body.innerHTML = `<div class="muted"><span class="spin-inline"></span> Checking compatibility…</div>`;
  try {
    const out = await postJSON("/system/manifest/validate", { manifest });
    if (!out.success) throw new Error(out.error || "Validation failed.");
    body.innerHTML = compatibilityReportHtml(out);
    state.manifestToRecreate = out.can_recreate ? manifest : null;
    $("#btn-manifest-recreate").disabled = !out.can_recreate;
  } catch (err) {
    body.innerHTML = `<div class="muted">${esc(err.message)}</div>`;
    $("#btn-manifest-recreate").disabled = true;
  } finally {
    busy(btn, false);
  }
}

async function recreateManifest(btn) {
  const manifest = state.manifestToRecreate;
  if (!manifest) return;
  const body = $("#manifest-body");
  busy(btn, true);
  body.innerHTML = `<div class="muted"><span class="spin-inline"></span> Recreating deployment…</div>`;
  const log = [];
  try {
    await postMaybeStream("/system/manifest/recreate", { manifest }, (evt) => {
      if (evt.event === "pull_start") log.push(`Pulling ${evt.model}…`);
      else if (evt.event === "pull_end") log.push(`Pulled ${evt.model}.`);
      else if (evt.event === "serve_start") log.push(`Serving at context ${evt.context}…`);
      else if (evt.event === "recreate_end") {
        log.push(`Placement: ${evt.placement_observed || "unknown"} (manifest recorded ${evt.placement_expected || "unknown"}).`);
        if (evt.observed_vram_gb != null) log.push(`Observed VRAM here: ${evt.observed_vram_gb} GB (manifest: ${evt.manifest_observed_vram_gb ?? "n/a"} GB).`);
      } else if (evt.event === "error") log.push(`Error: ${evt.error}`);
      body.innerHTML = `<div class="log">${log.map(esc).join("<br/>")}</div>`;
    });
    toast("Deployment recreated.", "success");
    void refreshStatus();
  } catch (err) {
    toast(`Recreate failed: ${err.message}`, "error");
  } finally {
    busy(btn, false);
  }
}

function wireManifestZone() {
  $("#btn-manifest-validate")?.addEventListener("click", (e) => validateManifest(e.currentTarget));
  $("#btn-manifest-recreate")?.addEventListener("click", (e) => recreateManifest(e.currentTarget));
  $("#manifest-import-file")?.addEventListener("change", async (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    $("#manifest-import-text").value = await file.text();
  });
}

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
const REASON_KIND_LABEL = { estimated: "estimated", published: "published spec", measured: "measured here" };

function reasonListHtml(reasons) {
  if (!reasons || !reasons.length) return "";
  return `<ul class="reason-list">${reasons
    .map((r) => {
      const mark = r.kind === "measured" ? "⚡" : r.kind === "published" ? "△" : "✓";
      return `<li class="reason-item reason-${esc(r.kind)}"><span class="reason-mark">${mark}</span> ${esc(r.text)} <span class="badge reason-kind-badge" title="Provenance">${esc(REASON_KIND_LABEL[r.kind] || r.kind)}</span></li>`;
    })
    .join("")}</ul>`;
}

const RECOMMEND_BUCKET_TITLE = { recommended: "★ Recommended", faster: "⚡ Faster", higher_quality: "◆ Higher quality" };

function renderRecommendCard(c) {
  if (!c) return "";
  const installed = !!findInstalledModel(c.pull_name);
  const action = installed
    ? `<span class="badge on">installed</span><button class="btn primary starter-deploy-btn" data-model="${esc(c.pull_name)}">Deploy</button>`
    : `<button class="btn primary starter-pull-btn" data-model="${esc(c.pull_name)}">Download and start</button>`;
  const vision = c.vision ? `<span class="badge">vision</span>` : "";
  const confBadge = `<span class="badge confidence-${esc(c.confidence)}" title="How much this estimate should be trusted">confidence: ${esc(c.confidence)}</span>`;
  return `<div class="fit-card model-card recommendation-card">
    <div class="bucket-label">${esc(RECOMMEND_BUCKET_TITLE[c.bucket] || c.bucket)}</div>
    <div class="running-top">
      <div class="model-identity">
        <span class="model-mark" aria-hidden="true">M</span>
        <div>
          <div class="model-title">${esc(c.id)}</div>
          <div class="muted small">${esc(c.use_case || "")}</div>
        </div>
      </div>
      <span class="badge" title="Hand-curated quality rating; 5 is strongest in its size class">quality ${esc(c.tier)}/5</span>
    </div>
    <div class="model-card-copy">${esc(c.description || "")}</div>
    <div class="muted small model-card-reason">${esc(c.why_summary || "")}</div>
    <details class="why-recommended">
      <summary>Why this model?</summary>
      ${reasonListHtml(c.reasons)}
    </details>
    <div class="row gap wrap fit-card-actions">
      ${vision}
      ${confBadge}
      <span class="spacer"></span>
      ${action}
    </div>
  </div>`;
}

function wireRecommendCardActions(body) {
  $$(".starter-pull-btn", body).forEach((b) =>
    b.addEventListener("click", () => pullModel(b.dataset.model, b))
  );
  $$(".starter-deploy-btn", body).forEach((b) =>
    b.addEventListener("click", () => startInstalledModel(resolveInstalledName(b.dataset.model), b))
  );
}

// ---------------------------------------------------------------------------
// Automated bakeoff — "Compare top models for me" (Release R6)
// Disabled per product decision — commented out rather than deleted so it's
// easy to re-enable later. The backend (/system/bakeoff/run) is untouched.
// ---------------------------------------------------------------------------
/*
function bakeoffDownloadBudgetGb() {
  const sel = $("#bakeoff-budget")?.value;
  if (sel === "custom") return Math.max(1, Number($("#bakeoff-budget-custom")?.value || 50));
  return Number(sel || 30);
}

function wireBakeoffBudgetSelect() {
  $("#bakeoff-budget")?.addEventListener("change", (e) => {
    $("#bakeoff-budget-custom-wrap")?.classList.toggle("hidden", e.target.value !== "custom");
  });
}

function bakeoffCandidateRowHtml(id, state_) {
  const st = state_ || { phase: "queued" };
  const phaseLabel = {
    queued: "Queued", pulling: "Downloading…", deploying: "Deploying…",
    benchmarking: "Benchmarking…", done: "Done", failed: "Failed",
  }[st.phase] || st.phase;
  const cls = st.phase === "done" ? "on" : st.phase === "failed" ? "wont" : st.phase === "queued" ? "off" : "cpu";
  const detail = st.phase === "failed" ? st.reason || "" : st.detail || "";
  return `<div class="bakeoff-row" data-model="${esc(id)}">
    <span class="badge ${cls}">${esc(phaseLabel)}</span>
    <b>${esc(id)}</b>
    <span class="muted small">${esc(detail)}</span>
  </div>`;
}

function renderBakeoffProgress(candidateOrder, candidateStates) {
  const body = $("#bakeoff-body");
  body.innerHTML = `<div class="bakeoff-progress">${candidateOrder.map((id) => bakeoffCandidateRowHtml(id, candidateStates[id])).join("")}</div>`;
}

function renderBakeoffResult(evt) {
  const body = $("#bakeoff-body");
  const rows = (evt.ranked || [])
    .map(
      (r, i) => `<tr class="${r.profile === evt.winner ? "bakeoff-winner-row" : ""}">
        <td>${i === 0 ? "🏆 " : ""}${esc(r.profile)}</td>
        <td class="num">${esc(r.passed)}/${esc(r.tests)}</td>
        <td class="num">${esc(r.avg_accuracy)}</td>
        <td class="num">${esc(r.avg_latency_s)}s</td>
        <td class="num">${r.margin_gb != null ? `${esc(r.margin_gb)} GB` : "—"}</td>
      </tr>`
    )
    .join("");
  body.innerHTML = `<div class="bakeoff-winner-card">
      <div class="eyebrow">Winner${evt.winner_deployed ? " · deployed" : ""}</div>
      <h3 class="sub">${esc(evt.winner)}</h3>
    </div>
    <div class="table-wrap"><table class="results">
      <thead><tr><th>Model</th><th class="num">Passed</th><th class="num">Accuracy</th><th class="num">Avg latency</th><th class="num">Headroom</th></tr></thead>
      <tbody>${rows}</tbody>
    </table></div>
    <div class="row gap wrap" style="margin-top:0.5rem">
      <button class="btn compact" id="bakeoff-export-winner">Export winner's deployment</button>
      ${evt.losers && evt.losers.length ? `<button class="btn compact danger" id="bakeoff-remove-losers">Remove ${evt.losers.length} other downloaded model(s)</button>` : ""}
    </div>`;
  $("#bakeoff-export-winner")?.addEventListener("click", (e) => exportManifest(evt.winner, e.currentTarget));
  $("#bakeoff-remove-losers")?.addEventListener("click", async (e) => {
    if (!window.confirm(`Delete these ${evt.losers.length} model(s) from disk?\n\n${evt.losers.join("\n")}`)) return;
    busy(e.currentTarget, true);
    for (const model of evt.losers) {
      try {
        await postJSON("/models/delete", { model });
      } catch {
        // best-effort; move on to the next loser rather than aborting the whole cleanup
      }
    }
    toast("Removed losing models.", "success");
    await refreshLiveModelState(true);
    busy(e.currentTarget, false);
  });
}

async function runBakeoff(source) {
  const btn = source?.currentTarget || source || $("#btn-bakeoff-run");
  busy(btn, true);
  const body = $("#bakeoff-body");
  body.innerHTML = `<div class="muted"><span class="spin-inline"></span> Selecting fit-safe candidates…</div>`;
  const candidateStates = {};
  let candidateOrder = [];
  try {
    await postMaybeStream(
      "/system/bakeoff/run",
      {
        use_case: $("#rec-use-case")?.value || null,
        priority: $("#rec-priority")?.value || "balanced",
        expected_context: parseInt($("#rec-context")?.value || "8192", 10),
        download_budget_gb: bakeoffDownloadBudgetGb(),
        free_vram_mb: targetVram(),
      },
      (evt) => {
        if (evt.event === "bakeoff_start") {
          candidateOrder = evt.candidates || [];
          candidateOrder.forEach((id) => (candidateStates[id] = { phase: "queued" }));
          renderBakeoffProgress(candidateOrder, candidateStates);
        } else if (evt.event === "candidate_start") {
          candidateStates[evt.model] = { phase: "pulling", detail: `~${evt.download_gb} GB` };
          renderBakeoffProgress(candidateOrder, candidateStates);
        } else if (evt.event === "pull_progress") {
          candidateStates[evt.model] = { phase: "pulling", detail: evt.status || "downloading…" };
          renderBakeoffProgress(candidateOrder, candidateStates);
        } else if (evt.event === "deploy_start") {
          candidateStates[evt.model] = { phase: "deploying", detail: "Deploying…" };
          renderBakeoffProgress(candidateOrder, candidateStates);
        } else if (evt.event === "test_start") {
          candidateStates[evt.model] = { phase: "benchmarking", detail: `Running ${evt.name}…` };
          renderBakeoffProgress(candidateOrder, candidateStates);
        } else if (evt.event === "candidate_end") {
          candidateStates[evt.model] = { phase: "done", detail: `accuracy ${evt.avg_accuracy} · ${evt.avg_latency_s}s avg` };
          renderBakeoffProgress(candidateOrder, candidateStates);
        } else if (evt.event === "candidate_failed") {
          candidateStates[evt.model] = { phase: "failed", reason: evt.reason };
          renderBakeoffProgress(candidateOrder, candidateStates);
        } else if (evt.event === "bakeoff_end") {
          renderBakeoffResult(evt);
        } else if (evt.event === "error") {
          body.innerHTML = `<div class="muted">${esc(evt.error)}</div>`;
        }
      }
    );
  } catch (err) {
    body.innerHTML = `<div class="muted">Bakeoff failed: ${esc(err.message)}</div>`;
    toast(`Bakeoff failed: ${err.message}`, "error");
  } finally {
    busy(btn, false);
  }
}
*/

async function recommendModels(source) {
  const btn = source?.currentTarget || source || $("#btn-recommend-models");
  busy(btn, true);
  const body = $("#starter-pack-body");
  body.innerHTML = `<div class="muted"><span class="spin-inline"></span> Finding models that fit your workload and hardware…</div>`;
  try {
    const data = await postJSON("/registry/recommend", {
      use_case: $("#rec-use-case")?.value || null,
      priority: $("#rec-priority")?.value || "balanced",
      expected_context: parseInt($("#rec-context")?.value || "8192", 10),
      usage_mode: $("#rec-usage-mode")?.value || "single_user_chat",
      free_vram_mb: targetVram(),
      margin_gb: 2.0,
    });
    if (!data.success || (!data.recommended && !data.faster && !data.higher_quality)) {
      body.innerHTML = `<div class="muted">${esc(data.message || "Could not determine a fit budget.")}</div>`;
      return;
    }
    const unit = data.budget_source === "vram" ? "VRAM" : "RAM";
    const marginNote = data.margin_relaxed ? "" : ` (after a 2 GB safety margin)`;
    const header = `<div class="muted small" style="margin-bottom:0.5rem">Budget: ~${esc(data.budget_gb)} GB usable ${unit}${marginNote} from ~${esc(data.raw_budget_gb)} GB detected.</div>`;
    const note = data.message ? `<div class="muted small">${esc(data.message)}</div>` : "";
    const cards = [data.recommended, data.faster, data.higher_quality].filter(Boolean);
    body.innerHTML = header + note + `<div class="fit-grid">` + cards.map(renderRecommendCard).join("") + `</div>`;
    wireRecommendCardActions(body);
  } catch (err) {
    body.innerHTML = `<div class="muted">Recommendation lookup failed.</div>`;
    toast(`Recommendation failed: ${err.message}`, "error");
  } finally {
    busy(btn, false);
  }
}

// ---------------------------------------------------------------------------
// Tab 1 — Hugging Face model search
// ---------------------------------------------------------------------------
const CATALOG_PAGE_SIZE = 12;

async function loadProviderCatalog(source) {
  const btn = source?.currentTarget || source || $("#btn-provider-refresh");
  const body = $("#provider-catalog-body");
  busy(btn, true);
  body.innerHTML = `<div class="muted"><span class="spin-inline"></span> Checking local providers…</div>`;
  try {
    const data = await getJSON("/registry/providers");
    state.catalog.rows = data.models || [];
    state.catalog.providers = data.providers || [];
    state.catalog.loaded = true;
    state.catalog.page = 0;
    renderProviderStatuses();
    populateCatalogProviderFilter();
    renderProviderCatalog();
  } catch (err) {
    body.innerHTML = `<div class="muted">Provider inventory failed: ${esc(err.message)}</div>`;
  } finally {
    busy(btn, false);
  }
}

// How to bring an unreachable runtime online, shown in the status chip tooltip.
const PROVIDER_HINTS = {
  ollama: "Install/start Ollama from ollama.com",
  lmstudio: "In LM Studio: Developer -> Start local server (port 1234)",
  vllm: "Set VLLM_BASE_URL in .env and start your vLLM server",
  docker: "Enable Docker Model Runner (port 12434)",
  llamacpp: "Start llama-server and set ENABLE_LLAMA_CPP=true",
  openai: "Point a profile at your OpenAI-compatible server",
};

function renderProviderStatuses() {
  const slot = $("#provider-statuses");
  if (!slot) return;
  slot.innerHTML = state.catalog.providers
    .map((provider) => {
      const n = (provider.models || []).length;
      const hint = provider.reachable
        ? `${provider.base_url} — ${n} model${n === 1 ? "" : "s"}`
        : `${provider.error || "unreachable"}. ${PROVIDER_HINTS[provider.provider] || ""}`;
      return `<span class="badge ${provider.reachable ? "on" : "off"}" title="${esc(hint)}">${provider.reachable ? "●" : "○"} ${esc(provider.provider)}${provider.reachable ? ` · ${n}` : ""}</span>`;
    })
    .join(" ");
}

function populateCatalogProviderFilter() {
  const sel = $("#catalog-provider");
  if (!sel) return;
  const current = sel.value;
  const names = [...new Set(state.catalog.rows.map((m) => m.provider))].sort();
  sel.innerHTML = `<option value="">All</option>` + names.map((n) => `<option value="${esc(n)}"${n === current ? " selected" : ""}>${esc(n)}</option>`).join("");
}

function catalogParamsB(model) {
  const explicit = parseFloat(String(model.parameters || ""));
  if (Number.isFinite(explicit) && explicit > 0) return explicit;
  const match = String(model.model || "").toLowerCase().match(/(\d+(?:\.\d+)?)\s*b\b/);
  return match ? parseFloat(match[1]) : null;
}

function catalogSizeBucket(paramsB) {
  if (paramsB == null) return null;
  if (paramsB < 4) return "tiny";
  if (paramsB < 8) return "small";
  if (paramsB < 15) return "mid";
  return "large";
}

function filteredCatalogRows() {
  const q = ($("#catalog-search")?.value || "").trim().toLowerCase();
  const provider = $("#catalog-provider")?.value || "";
  const size = $("#catalog-size")?.value || "";
  const sort = $("#catalog-sort")?.value || "tps";
  const rows = state.catalog.rows.filter((m) => {
    if (provider && m.provider !== provider) return false;
    if (size && catalogSizeBucket(catalogParamsB(m)) !== size) return false;
    if (q) {
      const hay = `${m.model} ${m.publisher || ""} ${m.provider} ${m.quant || ""}`.toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  });
  const by = {
    tps: (a, b) => (b.tokens_per_second ?? -1) - (a.tokens_per_second ?? -1) || a.model.localeCompare(b.model),
    name: (a, b) => a.model.localeCompare(b.model),
    params: (a, b) => (catalogParamsB(b) ?? -1) - (catalogParamsB(a) ?? -1) || a.model.localeCompare(b.model),
    provider: (a, b) => a.provider.localeCompare(b.provider) || a.model.localeCompare(b.model),
  };
  return rows.sort(by[sort] || by.tps);
}

function renderProviderCatalog() {
  const body = $("#provider-catalog-body");
  const pager = $("#catalog-pagination");
  if (!body || !state.catalog.loaded) return;
  const rows = filteredCatalogRows();
  if (!rows.length) {
    body.innerHTML = `<div class="empty-state">No models match${state.catalog.rows.length ? " these filters" : " — no reachable runtime reported any models yet"}.</div>`;
    pager?.classList.add("hidden");
    return;
  }
  const pages = Math.max(1, Math.ceil(rows.length / CATALOG_PAGE_SIZE));
  state.catalog.page = Math.min(state.catalog.page, pages - 1);
  const startIdx = state.catalog.page * CATALOG_PAGE_SIZE;
  const pageRows = rows.slice(startIdx, startIdx + CATALOG_PAGE_SIZE);
  body.innerHTML = `<div class="table-wrap provider-table-wrap"><table class="results provider-table">
      <thead><tr><th>Model</th><th>Runtime</th><th>Publisher</th><th class="num">Params</th><th>Quant</th><th class="num" title="Measured from your saved benchmark runs">tok/s ⚡</th><th class="num">Context</th><th></th></tr></thead>
      <tbody>${pageRows
        .map(
          (model) => `<tr>
        <td><b>${esc(model.model)}</b></td>
        <td><span class="badge">${esc(model.provider)}</span></td>
        <td>${esc(model.publisher || "—")}</td>
        <td class="num">${esc(model.parameters || (catalogParamsB(model) != null ? catalogParamsB(model) + "B" : "—"))}</td>
        <td>${quantLabelHtml(model.quant, "quant-code")}</td>
        <td class="num" title="${model.benchmark_samples ? `${esc(model.benchmark_samples)} saved samples` : "Not benchmarked yet — run it in Benchmark & Compare"}">${model.tokens_per_second != null ? esc(model.tokens_per_second) : "—"}</td>
        <td class="num">${model.context != null ? esc(model.context) : "—"}</td>
        <td><button class="btn compact provider-profile-btn" data-model="${esc(model.model)}" data-provider="${esc(model.provider)}" data-base-url="${esc(model.base_url)}" title="Create a run profile for this model">＋ Add profile</button></td>
      </tr>`
        )
        .join("")}</tbody>
    </table></div>`;
  if (pager) {
    pager.classList.toggle("hidden", pages <= 1);
    $("#catalog-page-label").textContent = `${startIdx + 1}–${Math.min(startIdx + CATALOG_PAGE_SIZE, rows.length)} of ${rows.length} models`;
    $("#catalog-prev").disabled = state.catalog.page === 0;
    $("#catalog-next").disabled = state.catalog.page >= pages - 1;
  }
  $$(".provider-profile-btn", body).forEach((profileBtn) =>
    profileBtn.addEventListener("click", async () => {
      busy(profileBtn, true);
      try {
        const result = await postJSON("/profiles/upsert", {
          model_id: profileBtn.dataset.model,
          backend: profileBtn.dataset.provider,
          base_url: profileBtn.dataset.baseUrl,
        });
        if (!result.success) throw new Error(result.error || "Profile creation failed.");
        await loadProfiles();
        toast(`Profile ${result.profile} is ready.`, "success");
      } catch (err) {
        toast(`Could not add provider profile: ${err.message}`, "error");
      } finally {
        busy(profileBtn, false);
      }
    })
  );
}

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

function remoteCatalogSearchButton(source) {
  const candidate = source?.currentTarget || source;
  return candidate?.id === "btn-hf-search" ? candidate : $("#btn-hf-search");
}

// Looks like an exact pullable tag ("gemma3:4b", "hf.co/org/repo")? Then the
// results offer pulling it verbatim, which absorbs the old "pull by name" flow.
function exactTagQuery(query) {
  if (/^hf\.co\//i.test(query)) return query;
  if (/^[\w.\-]+(?:\/[\w.\-]+)?:[\w.\-]+$/.test(query)) return query;
  return null;
}

function sourceBadge(source) {
  return source === "huggingface"
    ? `<span class="badge src-hf" title="Hugging Face models are pulled through Ollama's hf.co/ shortcut"><span class="source-dot"></span>Hugging Face</span>`
    : `<span class="badge src-ollama" title="Official Ollama model library"><span class="source-dot"></span>Ollama</span>`;
}

// "270m" / "0.8b" / "7b" -> billions of parameters (null when unparseable).
function paramsFromSizeToken(token) {
  const m = String(token || "").toLowerCase().match(/^([\d.]+)\s*([mb])$/);
  if (!m) return null;
  const value = parseFloat(m[1]);
  if (!Number.isFinite(value)) return null;
  return m[2] === "m" ? value / 1000 : value;
}

function popularityNumber(value) {
  if (value == null) return null;
  if (Number.isFinite(Number(value))) return Number(value);
  const match = String(value).trim().match(/^([\d.]+)\s*([kmb]?)$/i);
  if (!match) return null;
  return Number(match[1]) * ({ "": 1, k: 1e3, m: 1e6, b: 1e9 }[match[2].toLowerCase()] || 1);
}

function paramsFromModelName(name) {
  const matches = [...String(name || "").toLowerCase().matchAll(/(?:^|[-_:/])(\d+(?:\.\d+)?)\s*([mb])(?:$|[-_.])/g)];
  if (!matches.length) return null;
  const match = matches[matches.length - 1];
  const value = Number(match[1]);
  return match[2] === "m" ? value / 1000 : value;
}

function expandRemoteCatalog(models) {
  return (models || []).flatMap((model) => {
    let variants = Array.isArray(model.variants) ? model.variants : [];
    if (!variants.length && model.sizes?.length) {
      variants = model.sizes.map((label) => ({
        label,
        params_b: paramsFromSizeToken(label),
        pull_name: `${model.name}:${label}`,
      }));
    }
    if (!variants.length) variants = [{ label: null, params_b: paramsFromModelName(model.name), pull_name: model.pull_name }];
    return variants.map((variant, index) => {
      const paramsB = Number(variant.params_b) > 0 ? Number(variant.params_b) : paramsFromModelName(model.name);
      const pullName = variant.pull_name || model.pull_name || model.name;
      return {
        ...model,
        row_id: `${model.source}:${model.name}:${variant.label || index}`,
        family: model.family || model.name,
        size_label: variant.label || (paramsB != null ? `${paramsB}b` : null),
        params_b: paramsB,
        quant: variant.quant || null,
        context: variant.context || null,
        download_bytes: variant.download_bytes || null,
        pull_name: pullName,
        popularity: model.popularity ?? popularityNumber(model.pulls),
      };
    });
  });
}

function remoteFit(row) {
  return row.params_b != null ? state.remoteCatalog.fits[String(row.params_b)] || null : null;
}

function fitBadgeForCatalog(row) {
  const fit = remoteFit(row);
  if (!fit) return `<span class="badge unknown" title="The parameter size is unknown, so fit cannot be estimated">unknown</span>`;
  const labels = { ok: "fits GPU", soft: fit.tier === "cpu_only" ? "CPU only" : "tight", hard: "will not fit", unknown: "unknown" };
  const classes = { ok: "fits", soft: "tight", hard: "wont", unknown: "unknown" };
  return `<span class="badge ${classes[fit.severity] || "unknown"}" data-tooltip="Estimated need: ${esc(fit.required_gb)} GB at a typical Q4 quant and 4K context. Compare quants for an exact estimate." tabindex="0">${esc(labels[fit.severity] || "unknown")}</span>`;
}

function remoteSizeMatches(paramsB, filter) {
  if (filter === "all") return true;
  if (paramsB == null) return filter === "unknown";
  if (filter === "under4") return paramsB < 4;
  if (filter === "4to8") return paramsB >= 4 && paramsB <= 8;
  if (filter === "8to15") return paramsB > 8 && paramsB < 15;
  if (filter === "15to35") return paramsB >= 15 && paramsB < 35;
  if (filter === "over35") return paramsB >= 35;
  return true;
}

function remoteCatalogFilteredRows() {
  const source = $("#remote-source-filter")?.value || "all";
  const size = $("#remote-size-filter")?.value || "all";
  const fit = $("#remote-fit-filter")?.value || "all";
  const capability = $("#remote-cap-filter")?.value || "all";
  const installedOnly = !!$("#remote-installed-filter")?.checked;
  const rows = state.remoteCatalog.rows.filter((row) => {
    if (source !== "all" && row.source !== source) return false;
    if (!remoteSizeMatches(row.params_b, size)) return false;
    const rowFit = remoteFit(row);
    if (fit !== "all" && (rowFit?.severity || "unknown") !== fit) return false;
    if (capability !== "all" && !(row.capabilities || []).includes(capability)) return false;
    if (installedOnly && !row.installed_match && !findInstalledModel(row.pull_name)) return false;
    return true;
  });
  const sort = state.remoteCatalog.sort || $("#remote-sort")?.value || "popularity-desc";
  const [key, direction = "asc"] = sort.split("-");
  const factor = direction === "desc" ? -1 : 1;
  const value = (row) => {
    if (key === "model") return String(row.name || "").toLowerCase();
    if (key === "source") return String(row.source || "");
    if (key === "params") return row.params_b ?? null;
    if (key === "updated") return row.updated ? String(row.updated) : null;
    return row.popularity ?? null;
  };
  return rows.sort((a, b) => {
    const av = value(a);
    const bv = value(b);
    if (av == null && bv != null) return 1;
    if (bv == null && av != null) return -1;
    if (av == null && bv == null) return String(a.name).localeCompare(String(b.name));
    const compared = typeof av === "string" ? av.localeCompare(bv) : av - bv;
    return compared * factor || String(a.name).localeCompare(String(b.name));
  });
}

function catalogSortHeading(label, key, className = "") {
  const active = state.remoteCatalog.sort?.startsWith(`${key}-`);
  const direction = active && state.remoteCatalog.sort.endsWith("desc") ? "descending" : active ? "ascending" : "none";
  const arrow = direction === "ascending" ? " ↑" : direction === "descending" ? " ↓" : "";
  return `<th class="${className}" aria-sort="${direction}"><button class="catalog-sort-btn" data-sort-key="${key}">${esc(label)}${arrow}</button></th>`;
}

function remoteCatalogRow(row) {
  const wanted = String(row.pull_name || "").toLowerCase().replace(/:latest$/, "");
  const exactInstalled = Object.values(state.installedByName).some((model) =>
    String(model.name || "").toLowerCase().replace(/:latest$/, "") === wanted
  );
  const installed = exactInstalled || (row.params_b == null && row.installed_match)
    ? `<span class="badge on" title="A matching model is installed">installed</span>`
    : "";
  const caps = (row.capabilities || []).slice(0, 3).map((c) => `<span class="badge">${esc(c)}</span>`).join("");
  const params = row.params_b != null ? `${row.params_b < 1 ? row.params_b * 1000 + "M" : row.params_b + "B"}` : "—";
  const pullAction = row.pullable === false
    ? `<a class="btn compact" href="${esc(row.url)}" target="_blank" rel="noopener">View</a>`
    : `<button class="btn primary compact library-pull-btn" data-model="${esc(row.pull_name)}" title="Pull this exact ${row.size_label ? `${esc(row.size_label)} size` : "model"}">Pull</button>`;
  const quantAction = row.params_b != null
    ? `<button class="btn compact quant-jump-btn" data-model="${esc(row.source === "ollama" && row.size_label ? `${row.family}:${row.size_label}` : row.name)}" data-params="${esc(row.params_b)}" data-source="${esc(row.source)}" data-family="${esc(row.family)}">Compare quants</button>`
    : "";
  return `<tr data-catalog-row="${esc(row.row_id)}">
    <td class="catalog-model-cell" data-label="Model">
      <div class="catalog-result-title"><a href="${esc(row.url)}" target="_blank" rel="noopener">${esc(row.name)}</a>${installed}</div>
      <div class="catalog-caps">${caps}</div>
      ${row.description ? `<div class="muted small catalog-description" title="${esc(row.description)}">${esc(row.description)}</div>` : ""}
    </td>
    <td data-label="Source">${sourceBadge(row.source)}</td>
    <td class="num" data-label="Parameters"><strong>${esc(params)}</strong></td>
    <td data-label="Best fit">${fitBadgeForCatalog(row)}</td>
    <td class="num" data-label="Popularity" title="Pulls on Ollama or downloads on Hugging Face">${esc(row.pulls || "—")}</td>
    <td class="muted small" data-label="Updated">${esc(row.updated || "—")}</td>
    <td class="catalog-actions" data-label="Actions"><div class="catalog-action-stack">${quantAction}${pullAction}</div></td>
  </tr>`;
}

function updateRemoteFilterSummary(total, shown) {
  const slot = $("#remote-active-filters");
  if (!slot) return;
  const controls = [
    ["Source", $("#remote-source-filter")], ["Parameters", $("#remote-size-filter")],
    ["Fit", $("#remote-fit-filter")], ["Capability", $("#remote-cap-filter")],
  ];
  const active = controls.filter(([, control]) => control && control.value !== "all")
    .map(([label, control]) => `<span class="filter-chip">${label}: ${esc(control.options[control.selectedIndex].textContent)}</span>`);
  if ($("#remote-installed-filter")?.checked) active.push(`<span class="filter-chip">Installed only</span>`);
  slot.classList.toggle("hidden", !active.length);
  slot.innerHTML = active.length ? `<span class="muted small">Showing ${shown} of ${total} size-specific rows</span>${active.join("")}` : "";
}

function renderRemoteCatalog(message = null) {
  const body = $("#updates-body");
  if (!body) return;
  const rows = remoteCatalogFilteredRows();
  updateRemoteFilterSummary(state.remoteCatalog.rows.length, rows.length);
  const exact = exactTagQuery(state.remoteCatalog.query);
  const exactRow = exact
    ? `<div class="exact-pull-row">Exact pullable tag detected <button class="btn primary compact library-pull-btn" data-model="${esc(exact)}">Pull ${esc(exact)}</button></div>`
    : "";
  if (!rows.length) {
    body.innerHTML = `${exactRow}<div class="empty-state">No size-specific models match the current filters.</div>`;
  } else {
    body.innerHTML = `${exactRow}<div class="catalog-result-count"><strong>${rows.length}</strong> size-specific result${rows.length === 1 ? "" : "s"}<span class="muted small">One row per model parameter size; compare quants before pulling when memory is tight.</span></div>
      <div class="table-wrap remote-catalog-wrap"><table class="results catalog-table">
        <thead><tr>${catalogSortHeading("Model", "model")}${catalogSortHeading("Source", "source")}${catalogSortHeading("Parameters", "params", "num")}<th>Best fit</th>${catalogSortHeading("Popularity", "popularity", "num")}${catalogSortHeading("Updated", "updated")}<th><span class="sr-only">Actions</span></th></tr></thead>
        <tbody>${rows.map(remoteCatalogRow).join("")}</tbody></table></div>${message ? `<p class="muted small">${esc(message)}</p>` : ""}`;
  }
  $$(".library-pull-btn", body).forEach((pullBtn) => pullBtn.addEventListener("click", () => pullModel(pullBtn.dataset.model, pullBtn)));
  $$(".quant-jump-btn", body).forEach((qBtn) => qBtn.addEventListener("click", () => openQuantAdvisor(qBtn.dataset.model, Number(qBtn.dataset.params), qBtn.dataset.source, qBtn.dataset.family)));
  $$(".catalog-sort-btn", body).forEach((sortBtn) => sortBtn.addEventListener("click", () => {
    const key = sortBtn.dataset.sortKey;
    const current = state.remoteCatalog.sort || "";
    state.remoteCatalog.sort = current.startsWith(`${key}-`) && current.endsWith("asc") ? `${key}-desc` : `${key}-asc`;
    const select = $("#remote-sort");
    if (select && [...select.options].some((option) => option.value === state.remoteCatalog.sort)) select.value = state.remoteCatalog.sort;
    renderRemoteCatalog(message);
  }));
}

async function loadRemoteCatalogFits() {
  const params = [...new Set(state.remoteCatalog.rows.map((row) => row.params_b).filter((value) => value > 0))];
  state.remoteCatalog.fits = {};
  if (!params.length) return;
  try {
    const res = await postJSON("/system/fit-batch", { params_b: params, free_vram_mb: targetVram() });
    (res.items || []).forEach((item) => (state.remoteCatalog.fits[String(item.params_b)] = item));
  } catch {
    // The catalog remains useful when a hardware fit estimate is unavailable.
  }
}

async function searchUnifiedModels(source) {
  const btn = remoteCatalogSearchButton(source);
  const body = $("#updates-body");
  const statusSlot = $("#catalog-source-status");
  const query = ($("#hf-search")?.value || "").trim();
  const seq = ++state.unifiedSearchSeq;
  busy(btn, true);
  state.remoteCatalogLoaded = true;
  body.innerHTML = `<div class="muted"><span class="spin-inline"></span> Searching the Ollama library and Hugging Face…</div>`;
  try {
    const data = await postJSON("/registry/search-models", { query, limit: 30 });
    if (seq !== state.unifiedSearchSeq) return; // a newer keystroke superseded this
    if (statusSlot) {
      statusSlot.innerHTML = Object.entries(data.sources || {})
        .map(([name, s]) => `<span class="badge ${s.online ? "on" : "off"}" title="${esc(s.error || `${s.count} results`)}">${s.online ? "●" : "○"} ${esc(name)}${s.online ? ` · ${s.count}` : ""}</span>`)
        .join(" ");
    }
    if (!data.online && !(data.results || []).length) {
      body.innerHTML = `<div class="empty-state">${esc(data.message || "No model source is reachable.")}</div>`;
      return;
    }
    state.remoteCatalog.sourceRows = data.results || [];
    state.remoteCatalog.rows = expandRemoteCatalog(data.results || []);
    state.remoteCatalog.query = query;
    state.remoteCatalog.sort = $("#remote-sort")?.value || "popularity-desc";
    renderRemoteCatalog(data.message);
    await loadRemoteCatalogFits();
    if (seq === state.unifiedSearchSeq) renderRemoteCatalog(data.message);
  } catch (err) {
    if (seq !== state.unifiedSearchSeq) return;
    body.innerHTML = `<div class="empty-state">Model search failed.</div>`;
    toast(`Model search failed: ${err.message}`, "error");
  } finally {
    busy(btn, false);
  }
}

// Jump to the quant advisor pre-filled with a model from the results table.
function openQuantAdvisor(modelId, paramsB = null, source = "ollama", family = null) {
  const input = $("#quant-model");
  if (input) input.value = modelId;
  if (input) {
    input.dataset.params = paramsB || "";
    input.dataset.source = source || "ollama";
    input.dataset.family = family || "";
  }
  $('.seg-btn[data-seg="quant"]')?.click();
  quantAdvise($("#btn-quant-advise"));
}

// Back-compat alias: older wiring and tests refer to checkUpdates.
const checkUpdates = searchUnifiedModels;

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
      <div class="row gap wrap">
        <button class="btn compact" id="btn-view-pulled" data-model="${esc(model)}">View in Your models</button>
        <button class="btn primary compact" id="btn-deploy-pulled" data-model="${esc(model)}">Deploy now</button>
      </div>
    </div>`;
  done.classList.remove("hidden");
  $("#btn-pull-dismiss").hidden = false;
  $("#btn-deploy-pulled").addEventListener("click", (e) => {
    const b = e.currentTarget;
    startInstalledModel(resolveInstalledName(b.dataset.model), b);
  });
  $("#btn-view-pulled").addEventListener("click", (e) => highlightInstalledModel(e.currentTarget.dataset.model));
}

function dismissPullProgress() {
  state.pullRetry = null;
  $("#pull-progress")?.classList.add("hidden");
  $("#pull-progress-actions")?.classList.add("hidden");
  $("#pull-progress-done")?.classList.add("hidden");
  $("#btn-pull-dismiss").hidden = true;
}

function showPullTerminal(kind, model, message) {
  const actions = $("#pull-progress-actions");
  const retry = kind === "cancelled" || kind === "failed" || kind === "blocked";
  actions.innerHTML = `<div>
      <strong>${kind === "cancelled" ? "Pull cancelled" : kind === "blocked" ? "Pull blocked" : "Pull failed"}</strong>
      <div class="muted small">${esc(message)}</div>
    </div>
    <div class="row gap wrap">
      ${retry ? `<button class="btn primary compact" id="btn-pull-retry">Retry</button>` : ""}
      <button class="btn compact" id="btn-pull-close">Dismiss</button>
    </div>`;
  actions.className = `pull-progress-actions ${kind}`;
  $("#btn-pull-dismiss").hidden = false;
  $("#btn-pull-close")?.addEventListener("click", dismissPullProgress);
  $("#btn-pull-retry")?.addEventListener("click", () => {
    actions.classList.add("hidden");
    pullModel(model);
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
  state.pullRetry = model;
  $("#pull-progress-done").classList.add("hidden");
  $("#pull-progress-done").innerHTML = "";
  $("#pull-progress-actions").classList.add("hidden");
  $("#pull-progress-actions").innerHTML = "";
  $("#btn-pull-dismiss").hidden = true;
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
  const onCancel = () => {
    cancelBtn.disabled = true;
    cancelBtn.textContent = "Cancelling…";
    setPullProgress({ percent: null, status: "Cancelling request…", stats: "Downloaded layers may be retained by Ollama for a later retry." });
    controller.abort();
  };
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
        showPullTerminal("blocked", model, msg);
      } else {
        const msg = j.error || "Pull could not start.";
        append(msg);
        if (checkOllamaError(msg)) ollamaUnreachable = true;
        setPullProgress({ status: `Error: ${msg}` });
        showPullTerminal("failed", model, msg);
      }
    } else if (sawError) {
      // Stream opened but reported a failure mid-way — don't claim success or
      // flip any card to "installed".
      showPullTerminal("failed", model, "The model source reported an error. Open Raw log for details.");
    } else {
      append("done.");
      setPullProgress({ percent: 100, status: "Pulled successfully.", stats: "" });
      await Promise.all([loadProfiles(), refreshLiveModelState(true)]);
      markModelInstalledInUI(model);
      showPullDone(model);
      highlightInstalledModel(model);
      toast(`Pulled ${model}.`, "success");
    }
  } catch (err) {
    if (err.name === "AbortError") {
      append("cancelled.");
      setPullProgress({ percent: 0, status: "Cancelled.", stats: "The transfer stopped. Ollama may keep completed layers for a faster retry." });
      showPullTerminal("cancelled", model, "The transfer stopped. Dismiss this panel or retry the same model.");
      toast("Pull cancelled.", "info");
    } else {
      append(`error: ${err.message}`);
      setPullProgress({ status: `Error: ${err.message}` });
      showPullTerminal("failed", model, err.message);
    }
  } finally {
    cancelBtn.hidden = true;
    cancelBtn.disabled = false;
    cancelBtn.textContent = "Cancel";
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
  const bytes = state.installedByName[name]?.size || 0;
  const frees = bytes ? ` This frees ${(bytes / 1e9).toFixed(1)} GB of disk space.` : "";
  if (!window.confirm(`Delete "${name}" from disk?${frees}\n\nYou can always pull it again later.`)) {
    return;
  }
  busy(btn, true);
  try {
    const res = await postJSON("/models/delete", { model: name });
    if (res.success) {
      toast(res.message || `Deleted ${name}.`, "success");
      await refreshLiveModelState(true);
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
// Tab 1 — Quant advisor: fit-check every common quant of one model size
// ---------------------------------------------------------------------------
function quantBadge(v) {
  const cls = { ok: "on", soft: "tight", hard: "off" }[v.severity] || "";
  const label =
    v.severity === "ok" ? "comfortable"
    : v.tier === "tight" ? "tight"
    : v.tier === "cpu_only" ? "CPU only"
    : v.severity === "hard" ? "won't fit"
    : "unknown";
  return `<span class="badge ${cls}">${esc(label)}</span>`;
}

// Fill the "Pull it" column with real, published tags for this family+size,
// so pulling a specific quant is one click on a tag that actually exists —
// no guessing tag-name conventions.
async function attachQuantPullButtons(res, source = "ollama") {
  const family = res.model?.family;
  const paramsB = res.model?.params_b;
  const cells = $$(".quant-pull-cell");
  const extra = $("#quant-tags-extra");
  if (source !== "ollama") {
    cells.forEach((c) => (c.innerHTML = `<span class="muted small" title="Open the Hugging Face repository to choose an exact GGUF file">repository</span>`));
    return;
  }
  if (!family || !cells.length) {
    cells.forEach((c) => (c.innerHTML = `<span class="muted small" title="Not an Ollama library model — pull it by its own name">—</span>`));
    return;
  }
  let data;
  try {
    data = await postJSON("/registry/library-tags", { model: family });
  } catch {
    data = null;
  }
  if (!data?.success || !data.online || !(data.tags || []).length) {
    cells.forEach((c) => (c.innerHTML = `<span class="muted small" title="${esc(data?.message || "Tag list unavailable")}">—</span>`));
    return;
  }
  const sizeToken = `${String(paramsB).replace(/\.0$/, "")}b`;
  const norm = (s) => String(s).toLowerCase().replace(/[-_]/g, "");
  const sizeTags = data.tags.filter((tag) => norm(tag.tag).includes(norm(sizeToken)));
  cells.forEach((cell) => {
    const quant = cell.dataset.quant;
    const match = sizeTags.find((tag) => norm(tag.tag).includes(norm(quant)));
    if (!match) {
      cell.innerHTML = `<span class="muted small" title="ollama.com publishes no ${esc(sizeToken)} tag with ${esc(quant)}">not published</span>`;
      return;
    }
    cell.innerHTML = match.installed
      ? `<span class="badge on" title="${esc(match.full)} is already pulled">✓ pulled</span>`
      : `<button class="btn primary compact quant-pull-btn" data-model="${esc(match.full)}" title="Pull ${esc(match.full)}${match.size ? ` (${esc(match.size)} download)` : ""} — fit-checked first">↓ ${esc(match.size || "Pull")}</button>`;
  });
  if (extra) {
    const chips = data.tags
      .slice(0, 80)
      .map((tag) =>
        tag.installed
          ? `<span class="badge on" title="already pulled">${esc(tag.tag)}</span>`
          : `<button class="btn compact quant-pull-btn" data-model="${esc(tag.full)}" title="Pull ${esc(tag.full)}${tag.size ? ` (${esc(tag.size)} download)` : ""}">${esc(tag.tag)}${tag.size ? ` · ${esc(tag.size)}` : ""}</button>`
      )
      .join("");
    extra.innerHTML = `<details class="quant-all-tags"><summary>All ${data.tags.length} published ${esc(family)} tags (click any to pull)</summary><div class="quant-tag-grid">${chips}</div></details>`;
  }
  $$(".quant-pull-btn").forEach((b) => b.addEventListener("click", () => pullModel(b.dataset.model, b)));
}

async function quantAdvise(btn) {
  const modelId = $("#quant-model").value.trim();
  const input = $("#quant-model");
  const explicitParams = Number(input?.dataset.params) || undefined;
  const source = input?.dataset.source || "ollama";
  const body = $("#quant-body");
  if (!modelId) {
    body.innerHTML = `<div class="muted">Enter a model name that includes the size, e.g. <code>gemma3:12b</code>.</div>`;
    return;
  }
  busy(btn, true);
  body.innerHTML = skeletonHtml(3);
  try {
    const res = await postJSON("/system/quant-advisor", {
      model_id: modelId,
      params_b: explicitParams,
      context: Number($("#quant-context").value) || undefined,
      free_vram_mb: targetVram(),
    });
    if (!res.success) {
      body.innerHTML = `<div class="muted">${esc(res.message || "Could not estimate.")}</div>`;
      return;
    }
    const budget = res.free_vram_gb != null ? `${res.free_vram_gb} GB budget` : "no VRAM budget";
    const rows = res.variants
      .map((v) => {
        const margin = v.margin_gb != null ? `${v.margin_gb >= 0 ? "+" : ""}${v.margin_gb} GB` : "—";
        return `<tr>
          <td>${quantLabelHtml(v.quant, "quant-code")}</td>
          <td class="num">~${esc(v.weights_gb)} GB</td>
          <td class="num">~${esc(v.required_gb)} GB</td>
          <td class="num">${esc(margin)}</td>
          <td>${quantBadge(v)}</td>
          <td class="muted small">${esc(v.quality)}</td>
          <td class="quant-pull-cell" data-quant="${esc(v.quant)}"><span class="muted small">…</span></td>
        </tr>`;
      })
      .join("");
    const link = source === "ollama" && res.tags_url
      ? `<a href="${esc(res.tags_url)}" target="_blank" rel="noopener">Browse ${esc(res.model.family)}'s actual tags on ollama.com ↗</a>`
      : source === "huggingface"
        ? `<a href="https://huggingface.co/${esc(input?.dataset.family || modelId)}" target="_blank" rel="noopener">Browse exact GGUF files on Hugging Face ↗</a>`
        : "";
    const familyRows = state.remoteCatalog.rows.filter((row) => row.family === (input?.dataset.family || res.model.family) && row.params_b != null);
    const sizeSwitcher = familyRows.length > 1
      ? `<div class="quant-size-switcher"><span class="muted small">Parameter size</span>${familyRows.map((row) => `<button class="btn compact quant-size-btn${row.params_b === res.model.params_b ? " active" : ""}" data-model="${esc(row.source === "ollama" && row.size_label ? `${row.family}:${row.size_label}` : row.name)}" data-params="${esc(row.params_b)}" data-source="${esc(row.source)}" data-family="${esc(row.family)}">${esc(row.size_label || `${row.params_b}b`)}</button>`).join("")}</div>`
      : "";
    body.innerHTML = `
      <div class="next-action" style="margin-bottom:0.6rem"><b>${esc(res.recommendation)}</b></div>
      ${sizeSwitcher}
      <div class="muted small" style="margin-bottom:0.5rem">${esc(res.model.params_b)}B parameters · ${esc(res.model.context)} context · ${esc(budget)}</div>
      <div class="table-wrap"><table class="results quant-table">
        <thead><tr><th title="Lower precision uses less memory but can reduce quality">Quant</th><th class="num" title="Estimated model weight size">Weights</th><th class="num" title="Estimated weights, KV cache, and runtime overhead">Needs</th><th class="num" title="Memory budget remaining after the estimate">Headroom</th><th>Fit</th><th>Quality</th><th class="quant-pull-col">Pull it</th></tr></thead>
        <tbody>${rows}</tbody>
      </table></div>
      <div id="quant-tags-extra"></div>
      <p class="muted small quant-legend"><b>Reading quant names:</b> the number is the approximate bit class, <code>K</code> is the newer K-quant family, and <code>M</code> is its medium mixed-precision variant. Hover or focus a quant for its tradeoff.</p>
      <p class="muted small" style="margin-top:0.5rem">${esc(res.note)} ${link}</p>`;
    $$(".quant-size-btn", body).forEach((sizeBtn) => sizeBtn.addEventListener("click", () => openQuantAdvisor(sizeBtn.dataset.model, Number(sizeBtn.dataset.params), sizeBtn.dataset.source, sizeBtn.dataset.family)));
    void attachQuantPullButtons(res, source);
  } catch (err) {
    body.innerHTML = `<div class="muted">Estimate failed.</div>`;
    toast(`Quant advisor failed: ${err.message}`, "error");
  } finally {
    busy(btn, false);
  }
}

// ---------------------------------------------------------------------------
// Tab: Chat playground — installed Ollama models with explicit load state
// ---------------------------------------------------------------------------
function chatSelectedModel() {
  return $("#chat-model")?.value || "";
}

function profileForChatModel(model = chatSelectedModel(), enabledOnly = true) {
  if (!model) return "";
  return (
    state.profiles.find((name) => {
      const profile = state.profileData[name] || {};
      return (
        (profile.backend || "ollama") === "ollama" &&
        profile.model_id === model &&
        (!enabledOnly || profile.enabled === true)
      );
    }) || ""
  );
}

function chatSelectedProfile() {
  return profileForChatModel();
}

function runningChatModel(model = chatSelectedModel()) {
  if (!model) return null;
  return (state.runningDetails || []).find((item) => ollamaModelNamesMatch(item.name, model)) || null;
}

function chatModelIsReady() {
  return !!(runningChatModel() && chatSelectedProfile());
}

function chatProfileSupportsVision() {
  const profile = state.profileData[chatSelectedProfile()] || {};
  return !!profile.supports_vision;
}

function renderChatModelOptions() {
  const select = $("#chat-model");
  if (!select) return;
  const current = select.value;
  const models = state.installedList || [];
  if (!state.installedLoaded) {
    select.innerHTML = `<option value="">Checking installed models…</option>`;
    select.disabled = true;
    updateChatModelState();
    return;
  }
  if (!models.length) {
    select.innerHTML = `<option value="">No installed models</option>`;
    select.disabled = true;
    updateChatModelState();
    return;
  }
  const names = models.map((item) => item.name).filter(Boolean);
  const defaultModel = state.profileModels[state.defaultProfile];
  const preferred =
    (names.includes(current) && current) ||
    names.find((name) => state.servedModels.includes(name)) ||
    (names.includes(defaultModel) && defaultModel) ||
    names[0];
  select.innerHTML = names
    .map((name) => {
      const loaded = state.servedModels.includes(name) ? " · loaded" : "";
      return `<option value="${esc(name)}"${name === preferred ? " selected" : ""}>${esc(name + loaded)}</option>`;
    })
    .join("");
  select.disabled = false;
  updateChatModelState();
}

function updateChatModelState() {
  const attach = $("#chat-attach-label");
  const hint = $("#chat-hint");
  const session = $("#chat-session-state");
  const action = $("#btn-chat-session");
  const duration = $("#chat-keep-alive");
  const input = $("#chat-input");
  const send = $("#btn-chat-send");
  const progress = $("#chat-session-progress");
  const progressLabel = $("#chat-session-progress-label");
  if (!attach || !hint || !session || !action || !duration || !input || !send) return;
  const model = chatSelectedModel();
  const running = runningChatModel(model);
  const profile = chatSelectedProfile();
  const ready = !!(model && running && profile);
  const vision = chatProfileSupportsVision();
  const operation = state.chatSessionOperation || (state.chatSessionBusy ? "loading" : null);
  const canAttach = ready && !operation;
  attach.classList.toggle("disabled", !canAttach);
  attach.title = canAttach
    ? vision
      ? "Attach text files or images to your next message"
      : "Attach text files; images require a vision-capable model"
    : "Load an installed model before attaching files";
  if ((!ready || !vision) && state.chatImages.length) {
    state.chatImages = [];
    renderChatAttachments();
  }
  input.disabled = !ready || !!operation;
  send.disabled = (!ready || !!operation) && !state.chatController;
  input.placeholder = operation ? `${operation === "unloading" ? "Unloading" : "Loading"} ${model}…` : ready ? "Message this local model" : "Load an installed model to start chatting";
  duration.disabled = ready || !!operation;
  action.disabled = !model || !!operation || !!state.chatController;
  session.className = `chat-session-state${ready && !operation ? " ready" : operation ? " loading" : ""}`;
  progress?.classList.toggle("hidden", !operation);
  if (progressLabel && operation) progressLabel.textContent = `${operation === "unloading" ? "Unloading" : "Loading"} ${model}…`;
  if (!state.installedLoaded) {
    hint.textContent = "Checking models installed in Ollama…";
    session.lastElementChild.textContent = "Checking availability";
    action.textContent = "Load model";
  } else if (!model) {
    hint.textContent = "No local models are installed. Pull one from Setup & Deploy to use Chat.";
    session.lastElementChild.textContent = "Nothing installed";
    action.textContent = "Load model";
  } else if (operation) {
    const unloading = operation === "unloading";
    hint.textContent = `${unloading ? "Releasing" : "Loading"} ${model} ${unloading ? "from" : "into"} RAM and VRAM…`;
    session.lastElementChild.textContent = unloading ? "Unloading" : "Loading";
    action.textContent = unloading ? "Unloading…" : "Loading…";
  } else if (ready) {
    const placement = running.placement ? ` · ${running.placement}${running.gpu_percent != null ? ` ${running.gpu_percent}% GPU` : ""}` : "";
    hint.textContent = `${model} is ready · ${formatExpires(running.expires_at)}${placement}`;
    session.lastElementChild.textContent = "Ready";
    action.textContent = "Unload";
  } else if (running) {
    hint.textContent = `${model} is loaded, but its chat profile is not enabled yet.`;
    session.lastElementChild.textContent = "Needs chat setup";
    action.textContent = "Enable chat";
  } else {
    const durationLabel = duration.options[duration.selectedIndex]?.textContent || "60 minutes";
    hint.textContent = `${model} is installed on disk. Load it to keep it ready for ${durationLabel}.`;
    session.lastElementChild.textContent = "On disk";
    action.textContent = "Load model";
  }
  if (!state.chatMessages.length) renderChatWelcome();
}

async function toggleChatSession() {
  const model = chatSelectedModel();
  const btn = $("#btn-chat-session");
  if (!model || state.chatSessionBusy) return;
  if (chatModelIsReady()) {
    await killRunningModel(model, btn);
    return;
  }
  state.chatSessionBusy = true;
  state.chatSessionOperation = "loading";
  updateChatModelState();
  try {
    const profileResult = await postJSON("/profiles/upsert", {
      model_id: model,
      backend: "ollama",
      fields: { enabled: true },
    });
    if (!profileResult.success) throw new Error(profileResult.error || "Could not enable chat for this model.");
    await loadProfiles();
    const keepAlive = $("#chat-keep-alive")?.value || "60m";
    const result = await postJSON("/models/serve", {
      model,
      keep_alive: keepAlive,
      device: "auto",
    });
    if (!result.success) throw new Error(result.error || result.message || "Could not load the model.");
    await refreshStatus();
    toast(result.warning || `${model} is ready for chat.`, result.warning ? "info" : "success");
  } catch (err) {
    toast(`Could not start chat: ${err.message}`, "error");
  } finally {
    state.chatSessionBusy = false;
    state.chatSessionOperation = null;
    updateChatModelState();
  }
}

function chatImageUrl(image) {
  return typeof image === "string" ? image : image?.url || "";
}

function renderChatAttachments() {
  const slot = $("#chat-attachments");
  if (!slot) return;
  if (!state.chatImages.length && !state.chatFiles.length) {
    slot.classList.add("hidden");
    slot.innerHTML = "";
    return;
  }
  slot.classList.remove("hidden");
  slot.innerHTML = "";
  state.chatFiles.forEach((file, i) => {
    const chip = document.createElement("span");
    chip.className = "chat-file-chip";
    const label = document.createElement("span");
    label.textContent = `${file.name} · ${fmtBytes(file.size || file.content?.length || 0)}`;
    const x = document.createElement("button");
    x.type = "button";
    x.textContent = "×";
    x.title = "Remove file";
    x.addEventListener("click", () => {
      state.chatFiles.splice(i, 1);
      renderChatAttachments();
    });
    chip.append(label, x);
    slot.appendChild(chip);
  });
  state.chatImages.forEach((image, i) => {
    const chip = document.createElement("span");
    chip.className = "chat-attach-chip";
    const img = document.createElement("img");
    img.src = chatImageUrl(image);
    img.alt = typeof image === "object" && image.name ? image.name : `attachment ${i + 1}`;
    const x = document.createElement("button");
    x.type = "button";
    x.textContent = "×";
    x.title = "Remove image";
    x.addEventListener("click", () => {
      state.chatImages.splice(i, 1);
      renderChatAttachments();
    });
    chip.append(img, x);
    slot.appendChild(chip);
  });
}

function addChatImages(files) {
  const maxImageBytes = 10 * 1024 * 1024;
  const maxTextBytes = 200 * 1024;
  Array.from(files || []).forEach((file) => {
    if (file.type.startsWith("image/")) {
      if (!chatProfileSupportsVision()) {
        toast("The selected profile isn't marked vision-capable — attach text files instead.", "error");
        return;
      }
      if (file.size > maxImageBytes) {
        toast(`${file.name} is over 10 MB — skipped.`, "error");
        return;
      }
      const reader = new FileReader();
      reader.onload = () => {
        state.chatImages.push({ url: String(reader.result), name: file.name, size: file.size, type: file.type });
        renderChatAttachments();
      };
      reader.readAsDataURL(file);
      return;
    }
    // Anything else is treated as text and embedded into the message.
    if (file.size > maxTextBytes) {
      toast(`${file.name} is over 200 KB — too large to embed as text.`, "error");
      return;
    }
    const reader = new FileReader();
    reader.onload = () => {
      state.chatFiles.push({ name: file.name, content: String(reader.result), size: file.size, type: file.type || "text/plain" });
      renderChatAttachments();
    };
    reader.readAsText(file);
  });
}

function parseChatJson(text) {
  let raw = String(text || "").trim();
  const fenced = raw.match(/^```(?:json)?\s*\n?([\s\S]*?)\n?```$/i);
  if (fenced) raw = fenced[1].trim();
  if (!raw || !["{", "["].includes(raw[0])) return null;
  try {
    const value = JSON.parse(raw);
    return { value, raw: JSON.stringify(value, null, 2) };
  } catch {
    return null;
  }
}

function appendJsonTreeValue(parent, value, key = null, depth = 0) {
  const isContainer = value !== null && typeof value === "object";
  if (!isContainer) {
    const row = document.createElement("div");
    row.className = "json-tree-row";
    if (key !== null) {
      const keyNode = document.createElement("span");
      keyNode.className = "json-key";
      keyNode.textContent = `${key}:`;
      row.appendChild(keyNode);
    }
    const scalar = document.createElement("span");
    scalar.className = `json-value json-${value === null ? "null" : typeof value}`;
    scalar.textContent = typeof value === "string" ? `"${value}"` : String(value);
    row.appendChild(scalar);
    parent.appendChild(row);
    return;
  }
  const entries = Array.isArray(value) ? value.map((item, index) => [index, item]) : Object.entries(value);
  const details = document.createElement("details");
  details.className = "json-tree-node";
  details.open = depth < 2;
  const summary = document.createElement("summary");
  const type = Array.isArray(value) ? "array" : "object";
  summary.textContent = `${key !== null ? `${key}: ` : ""}${type} · ${entries.length} item${entries.length === 1 ? "" : "s"}`;
  details.appendChild(summary);
  const children = document.createElement("div");
  children.className = "json-tree-children";
  entries.forEach(([childKey, child]) => appendJsonTreeValue(children, child, childKey, depth + 1));
  details.appendChild(children);
  parent.appendChild(details);
}

function renderChatJson(container, parsed) {
  const shell = document.createElement("div");
  shell.className = "chat-json";
  const toolbar = document.createElement("div");
  toolbar.className = "chat-json-toolbar";
  const treeBtn = document.createElement("button");
  treeBtn.type = "button";
  treeBtn.className = "btn compact active";
  treeBtn.textContent = "Tree";
  const rawBtn = document.createElement("button");
  rawBtn.type = "button";
  rawBtn.className = "btn compact";
  rawBtn.textContent = "Raw";
  const copyBtn = document.createElement("button");
  copyBtn.type = "button";
  copyBtn.className = "btn compact";
  copyBtn.textContent = "Copy";
  const downloadBtn = document.createElement("button");
  downloadBtn.type = "button";
  downloadBtn.className = "btn compact";
  downloadBtn.textContent = "Download JSON";
  toolbar.append(treeBtn, rawBtn, copyBtn, downloadBtn);
  const tree = document.createElement("div");
  tree.className = "json-tree";
  appendJsonTreeValue(tree, parsed.value);
  const raw = document.createElement("pre");
  raw.className = "json-raw hidden";
  const code = document.createElement("code");
  code.textContent = parsed.raw;
  raw.appendChild(code);
  const selectView = (showRaw) => {
    tree.classList.toggle("hidden", showRaw);
    raw.classList.toggle("hidden", !showRaw);
    treeBtn.classList.toggle("active", !showRaw);
    rawBtn.classList.toggle("active", showRaw);
  };
  treeBtn.addEventListener("click", () => selectView(false));
  rawBtn.addEventListener("click", () => selectView(true));
  copyBtn.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(parsed.raw);
      copyBtn.textContent = "Copied";
      setTimeout(() => (copyBtn.textContent = "Copy"), 1200);
    } catch { /* clipboard unavailable */ }
  });
  downloadBtn.addEventListener("click", () => downloadFile("localdeploy-response.json", parsed.raw, "application/json"));
  shell.append(toolbar, tree, raw);
  container.appendChild(shell);
}

// Render reply text into a bubble body: JSON gets a tree/raw inspector and
// fenced code blocks become styled <pre><code> elements with a copy button.
// <pre><code> elements with a copy button; everything else stays textContent
// (never innerHTML), so model output can't inject markup.
function renderChatText(container, text) {
  container.innerHTML = "";
  const parsedJson = parseChatJson(text);
  if (parsedJson) {
    renderChatJson(container, parsedJson);
    return;
  }
  const parts = String(text).split(/```([\w+-]*)\n?([\s\S]*?)(?:```|$)/g);
  // split() with two capture groups yields [text, lang, code, text, lang, code, …]
  for (let i = 0; i < parts.length; i += 3) {
    const plain = parts[i];
    if (plain) appendMarkdownLite(container, plain);
    if (i + 2 < parts.length) {
      const code = parts[i + 2] ?? "";
      const wrap = document.createElement("div");
      wrap.className = "chat-code";
      if (parts[i + 1]) {
        const lang = document.createElement("span");
        lang.className = "chat-code-lang";
        lang.textContent = parts[i + 1];
        wrap.appendChild(lang);
      }
      const copy = document.createElement("button");
      copy.type = "button";
      copy.className = "chat-code-copy";
      copy.textContent = "⧉";
      copy.title = "Copy code";
      copy.addEventListener("click", async () => {
        try {
          await navigator.clipboard.writeText(code);
          copy.textContent = "✓";
          setTimeout(() => (copy.textContent = "⧉"), 1200);
        } catch { /* clipboard unavailable */ }
      });
      wrap.appendChild(copy);
      const pre = document.createElement("pre");
      const codeEl = document.createElement("code");
      codeEl.textContent = code;
      pre.appendChild(codeEl);
      wrap.appendChild(pre);
      container.appendChild(wrap);
    }
  }
}

// Minimal, escape-first markdown for chat text: headings, bullets, inline
// code/bold/italic and http(s) links. Everything is built with createElement +
// textContent — model output can never inject markup.
const _INLINE_MD = /(`[^`\n]+`|\*\*[^*\n]+\*\*|\*[^*\n]+\*|\[[^\]\n]+\]\(https?:\/\/[^\s)]+\))/g;

function appendInlineMarkdown(parent, text) {
  let last = 0;
  for (const match of text.matchAll(_INLINE_MD)) {
    if (match.index > last) parent.append(text.slice(last, match.index));
    const token = match[0];
    if (token.startsWith("`")) {
      const code = document.createElement("code");
      code.textContent = token.slice(1, -1);
      parent.appendChild(code);
    } else if (token.startsWith("**")) {
      const b = document.createElement("b");
      b.textContent = token.slice(2, -2);
      parent.appendChild(b);
    } else if (token.startsWith("*")) {
      const i = document.createElement("i");
      i.textContent = token.slice(1, -1);
      parent.appendChild(i);
    } else {
      const m = token.match(/^\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)$/);
      const a = document.createElement("a");
      a.textContent = m ? m[1] : token;
      if (m) {
        a.href = m[2];
        a.target = "_blank";
        a.rel = "noopener";
      }
      parent.appendChild(a);
    }
    last = match.index + token.length;
  }
  if (last < text.length) parent.append(text.slice(last));
}

function appendMarkdownLite(container, text) {
  for (const line of String(text).split("\n")) {
    const el = document.createElement("div");
    const heading = line.match(/^(#{1,3})\s+(.*)$/);
    const bullet = line.match(/^\s*[-*]\s+(.*)$/);
    if (heading) {
      el.className = `md-h${heading[1].length}`;
      appendInlineMarkdown(el, heading[2]);
    } else if (bullet) {
      el.className = "md-li";
      el.append("• ");
      appendInlineMarkdown(el, bullet[1]);
    } else if (!line.trim()) {
      el.className = "md-gap";
    } else {
      appendInlineMarkdown(el, line);
    }
    container.appendChild(el);
  }
}

// Build one message bubble with a compact role marker.
function appendChatBubble(role, text, images = [], files = []) {
  const list = $("#chat-messages");
  const row = document.createElement("div");
  row.className = `chat-row ${role}`;
  const avatar = document.createElement("div");
  avatar.className = "chat-avatar";
  avatar.textContent = role === "user" ? "U" : "AI";
  row.appendChild(avatar);
  const bubble = document.createElement("div");
  bubble.className = "chat-bubble";
  if (images.length) {
    const strip = document.createElement("div");
    strip.className = "chat-bubble-images";
    images.forEach((image) => {
      const img = document.createElement("img");
      img.src = chatImageUrl(image);
      img.alt = typeof image === "object" && image.name ? image.name : "attached image";
      strip.appendChild(img);
    });
    bubble.appendChild(strip);
  }
  if (files.length) {
    const fileStrip = document.createElement("div");
    fileStrip.className = "chat-bubble-files";
    files.forEach((file) => {
      const item = document.createElement("span");
      item.className = "chat-bubble-file";
      item.textContent = `${file.name} · ${fmtBytes(file.size || file.content?.length || 0)}`;
      item.title = "This file's text was included in the model request.";
      fileStrip.appendChild(item);
    });
    bubble.appendChild(fileStrip);
  }
  const body = document.createElement("div");
  body.className = "chat-bubble-text";
  if (text) renderChatText(body, text);
  bubble.appendChild(body);
  const meta = document.createElement("div");
  meta.className = "chat-bubble-meta muted small";
  bubble.appendChild(meta);
  row.appendChild(bubble);
  list.appendChild(row);
  list.scrollTop = list.scrollHeight;
  return { row, body, meta, bubble };
}

function chatMessagesForApi(profile) {
  const messages = [];
  const system = $("#chat-system")?.value.trim();
  if (system) messages.push({ role: "system", content: system });
  state.chatMessages.forEach((m) => {
    if (m.images?.length) {
      messages.push({
        role: m.role,
        content: [
          { type: "text", text: m.apiText || m.text },
          ...m.images.map((image) => ({ type: "image_url", image_url: { url: chatImageUrl(image) } })),
        ],
      });
    } else {
      messages.push({ role: m.role, content: m.apiText || m.text });
    }
  });
  return {
    model: profile,
    messages,
    stream: true,
    // Without this, each generate call resets Ollama's keep-alive to its 5m
    // default, silently undoing the session's "keep loaded" choice.
    keep_alive: $("#chat-keep-alive")?.value || undefined,
  };
}

async function sendChatMessage() {
  const input = $("#chat-input");
  const btn = $("#btn-chat-send");
  if (state.chatController) {
    // Button doubles as Stop while a reply is streaming.
    state.chatController.abort();
    return;
  }
  const typed = input.value.trim();
  const profile = chatSelectedProfile();
  if ((!typed && !state.chatFiles.length && !state.chatImages.length) || !chatModelIsReady() || !profile) {
    if (!chatModelIsReady()) toast("Load an installed model before sending a message.", "error");
    return;
  }
  // Attached text files ride along as fenced blocks: the model gets clear file
  // boundaries and the bubble renders them as code blocks.
  const fileBlocks = state.chatFiles
    .map((f) => "\n\n[Attached file: " + f.name + "]\n```\n" + f.content + "\n```")
    .join("");
  const displayText = typed || "Please review the attached file(s).";
  const apiText = displayText + fileBlocks;
  const images = state.chatImages.slice();
  const files = state.chatFiles.map((file) => ({ ...file }));
  state.chatImages = [];
  state.chatFiles = [];
  renderChatAttachments();
  input.value = "";
  input.style.height = "auto";

  $("#chat-messages .chat-welcome")?.remove();
  state.chatMessages.push({ role: "user", text: displayText, apiText, images, files });
  appendChatBubble("user", displayText, images, files);
  const assistant = appendChatBubble("assistant", "", []);
  assistant.bubble.classList.add("typing");

  const controller = new AbortController();
  state.chatController = controller;
  updateChatModelState();
  btn.textContent = "Stop";
  btn.classList.add("danger");
  const started = performance.now();
  let firstTokenAt = null;
  let reply = "";
  let failed = null;
  try {
    await postMaybeStream(
      "/v1/chat/completions",
      chatMessagesForApi(profile),
      (evt) => {
        if (evt.error?.message) {
          failed = evt.error.message;
          return;
        }
        const delta = evt.choices?.[0]?.delta?.content;
        if (delta) {
          if (firstTokenAt === null) firstTokenAt = performance.now();
          reply += delta;
          assistant.bubble.classList.remove("typing");
          renderChatText(assistant.body, reply);
          const list = $("#chat-messages");
          list.scrollTop = list.scrollHeight;
        }
      },
      controller.signal
    );
  } catch (err) {
    if (err.name === "AbortError") failed = "Stopped.";
    else failed = err.message;
  } finally {
    state.chatController = null;
    btn.textContent = "Send";
    btn.classList.remove("danger");
    assistant.bubble.classList.remove("typing");
    updateChatModelState();
  }

  const elapsed = (performance.now() - started) / 1000;
  if (failed && !reply) {
    assistant.bubble.classList.add("error");
    assistant.body.textContent = failed;
    // Drop the failed exchange so a retry doesn't resend a broken turn.
    state.chatMessages.pop();
    return;
  }
  state.chatMessages.push({ role: "assistant", text: reply, images: [] });
  // tok/s measured from the first streamed token, so model-load time (which
  // dominates a cold first message) doesn't poison the generation speed.
  const approxTokens = Math.max(1, Math.ceil(reply.length / 4));
  const genSeconds = firstTokenAt !== null ? (performance.now() - firstTokenAt) / 1000 : elapsed;
  const tps = genSeconds > 0.2 && approxTokens > 2 ? ` · ⚡ ~${(approxTokens / genSeconds).toFixed(1)} tok/s` : "";
  const firstTok = firstTokenAt !== null && (firstTokenAt - started) / 1000 > 2
    ? ` · first token ${((firstTokenAt - started) / 1000).toFixed(1)} s`
    : "";
  assistant.meta.textContent = `🕒 ${elapsed.toFixed(1)} s${firstTok}${tps}${failed ? ` · ${failed}` : ""}`;
}

const CHAT_SUGGESTIONS = [
  "Explain what a quantized model is, in two sentences.",
  "Write a haiku about running AI locally.",
  "Return a JSON object with three fields describing this machine's ideal use.",
];

function clearChat() {
  state.chatController?.abort();
  state.chatMessages = [];
  state.chatImages = [];
  state.chatFiles = [];
  renderChatAttachments();
  renderChatWelcome();
}

function renderChatWelcome() {
  const list = $("#chat-messages");
  if (!list || state.chatMessages.length) return;
  list.innerHTML = "";
  const welcome = document.createElement("div");
  welcome.className = "chat-welcome";
  const ready = chatModelIsReady();
  const model = chatSelectedModel();
  if (!state.installedLoaded) {
    welcome.innerHTML = `<div class="chat-welcome-mark">…</div>
      <h3>Checking local models</h3>
      <p class="muted small">Chat will show models that are actually installed in Ollama.</p>`;
    list.appendChild(welcome);
    return;
  }
  if (!model) {
    welcome.innerHTML = `<div class="chat-welcome-mark">+</div>
      <h3>No installed models</h3>
      <p class="muted small">Pull a model from Setup &amp; Deploy, then return here to load it.</p>`;
    const openCatalog = document.createElement("button");
    openCatalog.type = "button";
    openCatalog.className = "btn primary";
    openCatalog.textContent = "Open model catalog";
    openCatalog.addEventListener("click", () => activateTab("serve"));
    welcome.appendChild(openCatalog);
    list.appendChild(welcome);
    return;
  }
  if (!ready) {
    welcome.innerHTML = `<div class="chat-welcome-mark">${esc(model.slice(0, 1).toUpperCase())}</div>
      <h3>${esc(model)} is installed</h3>
      <p class="muted small">Use Load model above to warm it for the displayed keep-loaded duration.</p>`;
    list.appendChild(welcome);
    return;
  }
  welcome.innerHTML = `<div class="chat-welcome-mark">${esc(model.slice(0, 1).toUpperCase())}</div>
    <h3>${esc(model)} is ready</h3>
    <p class="muted small">Start with a prompt or choose one below.</p>`;
  const chips = document.createElement("div");
  chips.className = "chat-suggestion-chips";
  CHAT_SUGGESTIONS.forEach((text) => {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "chat-suggestion";
    chip.textContent = text;
    chip.addEventListener("click", () => {
      $("#chat-input").value = text;
      sendChatMessage();
    });
    chips.appendChild(chip);
  });
  welcome.appendChild(chips);
  list.appendChild(welcome);
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
    repetition: evt.repetition || 1,
    repetitions: evt.repetitions || 1,
    metrics: evt.metrics || {},
    tokens_per_second_source: evt.tokens_per_second_source || "estimated",
    warm_state: evt.warm_state || null,
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
  const rateTitle = evt.tokens_per_second_source === "backend" ? "Measured by the inference backend" : "Estimated from response length";
  const tpsCell = tps != null ? `<span title="${rateTitle}">${tps.toFixed(1)}</span>` : `<span class="muted">—</span>`;
  const testName = evt.repetitions > 1 ? `${evt.name} · r${evt.repetition}` : evt.name;
  const hasPreview = !!evt.response_preview;

  const tr = document.createElement("tr");
  tr.innerHTML = `<td>${esc(runName)}</td>
    <td>${esc(testName)}</td>
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

async function runQueueItem(item, questionInfo, timeout, repetitions, controller) {
  const summary = $("#run-summary");
  const body = { profiles: [item.profile], timeout, repetitions };
  if (item.requestedDevice && item.requestedDevice !== "auto") body.device = item.requestedDevice;
  if (questionInfo.questions) body.questions = questionInfo.questions;
  const pack = $("#bench-pack")?.value;
  if (pack && !questionInfo.questions) body.pack = pack;

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
      item.provenance = evt.provenance || {};
      item.repetitions = evt.repetitions || repetitions;
      setProgress(0);
      setActiveRun(item, 0, total);
    } else if (evt.event === "profile_start") {
      item.status = "running";
      item.current = "Model warm-up";
      setActiveRun(item, done, total);
    } else if (evt.event === "test_aggregate") {
      (item.aggregates ||= []).push(evt);
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
      item.variance = {
        latency_stdev_seconds: evt.summary?.latency_stdev_seconds ?? 0,
        tokens_per_second_stdev: evt.summary?.tokens_per_second_stdev ?? 0,
        by_test: item.aggregates || [],
      };
      item.peakVramMb = evt.summary?.peak_vram_mb ?? null;
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
      hardware: item.provenance?.hardware || state.lastHardware || {},
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
  $("#btn-contribute").disabled = true;

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
  const repetitions = Math.max(1, Math.min(10, Number($("#bench-repetitions")?.value) || 1));
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
      const run = await runQueueItem(item, questionInfo, timeout, repetitions, itemController);
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
  if (state.benchHistoryServer) {
    // Best-effort: the run may or may not have a server copy.
    void postJSON("/benchmark/history/delete", { id }).catch(() => {});
  }
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
  const hasActiveRun = !(state.benchmarkRuns.find((r) => r.id === state.activeRunId) || state.lastRun);
  $("#btn-export").disabled = hasActiveRun;
  $("#btn-contribute").disabled = hasActiveRun;
}

function selectedComparisonRuns() {
  const runs = allBenchmarkRuns();
  return state.selectedRunIds.map((id) => runs.find((r) => r.id === id)).filter(Boolean);
}

function regressionDiffsHtml(resp) {
  const diffRows = (resp.dimension_diffs || [])
    .map(
      (d) =>
        `<tr class="${d.changed ? "regression-changed" : ""}"><td>${esc(d.dimension)}</td><td>${esc(d.a ?? "—")}</td><td>${esc(d.b ?? "—")}</td><td>${d.changed ? "⚠ changed" : "same"}</td></tr>`
    )
    .join("");
  const sd = resp.summary_delta || {};
  const deltaCell = (v, unit = "") => (v == null ? "—" : `${v > 0 ? "+" : ""}${esc(v)}${unit}`);
  return `<h3 class="sub">What changed (regression check)</h3>
    <div class="row gap wrap regression-deltas">
      <span class="badge">tok/s Δ ${deltaCell(sd.avg_tokens_per_second)}</span>
      <span class="badge">TTFT Δ ${deltaCell(sd.avg_ttft_ms, " ms")}</span>
      <span class="badge">peak VRAM Δ ${deltaCell(sd.peak_vram_mb, " MB")}</span>
      <span class="badge">accuracy Δ ${deltaCell(sd.avg_accuracy)}</span>
    </div>
    ${
      diffRows
        ? `<div class="table-wrap"><table class="results"><thead><tr><th>Dimension</th><th>${esc(resp.label_a)}</th><th>${esc(resp.label_b)}</th><th></th></tr></thead><tbody>${diffRows}</tbody></table></div>`
        : `<div class="muted small">No provenance recorded on these runs to compare dimensions.</div>`
    }`;
}

async function renderComparison(runs, baseline) {
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
    <h3 class="sub">Response detail</h3><div class="response-test-list">${testButtons}</div>
    <div id="regression-diffs"></div>`;
  $$(".response-compare-btn", $("#compare-body")).forEach((b) => {
    b.addEventListener("click", () => renderResponseDrawer(b.dataset.test, runs));
  });
  // The richer provenance-aware diff (dimension changes, TTFT/VRAM deltas) is
  // only meaningful pairwise — a 3+ run comparison keeps the table above only.
  if (runs.length === 2) {
    const other = runs.find((r) => r.id !== baseline.id) || runs[1];
    try {
      const resp = await postJSON("/benchmark/compare", {
        card_a: runToCardPayload(baseline),
        card_b: runToCardPayload(other),
      });
      if (resp.success) {
        const slot = $("#regression-diffs");
        if (slot) slot.innerHTML = regressionDiffsHtml(resp);
      }
    } catch {
      // Regression detail is a bonus panel; the primary comparison table above still rendered.
    }
  }
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

// Map a normalized run object (camelCase, client-side) to the snake_case
// card shape report.py's build_card/benchmark_compare expect. One adapter
// reused by export (so provenance/peak VRAM survive a round-trip through the
// downloaded HTML) and by the regression-detection compare call below.
function runToCardPayload(run) {
  return {
    profile: run.profile,
    model_id: run.modelId,
    device: run.actualDevice || run.requestedDevice,
    hardware: run.hardware,
    tests: run.tests,
    summary: run.summary,
    category_summary: run.category_summary,
    provenance: run.provenance || {},
    repetitions: run.repetitions || 1,
    variance: run.variance || {},
    peak_vram_mb: run.peakVramMb ?? null,
  };
}

async function exportOneRun(run, btn) {
  busy(btn, true);
  try {
    const out = await postJSON("/benchmark/export", runToCardPayload(run));
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

// ---------------------------------------------------------------------------
// Community benchmark sharing — local-only preview/save (Release R8)
// ---------------------------------------------------------------------------
async function contributeRun() {
  const run = state.benchmarkRuns.find((r) => r.id === state.activeRunId) || state.lastRun;
  if (!run) {
    toast("Select or run a benchmark first.", "error");
    return;
  }
  const btn = $("#btn-contribute");
  busy(btn, true);
  try {
    const out = await postJSON("/system/community/preview", { card: runToCardPayload(run) });
    if (!out.success) throw new Error(out.error || "Preview failed.");
    const overlay = simpleModal(
      "Contribute this benchmark",
      `Exactly what would be shared — review before saving. ${esc(out.note)}`,
      `<pre class="log manifest-yaml">${esc(JSON.stringify(out.would_share, null, 2))}</pre>
       <details style="margin-top:0.5rem"><summary class="muted small">Never included</summary>
         <div class="muted small">${out.excluded_fields.map(esc).join(", ")}</div>
       </details>
       <div class="row gap wrap" style="margin-top:0.75rem">
         <button class="btn primary" id="contribute-save-local">Save locally</button>
       </div>
       <div id="contribute-save-status" class="muted small"></div>`
    );
    $("#contribute-save-local", overlay)?.addEventListener("click", async (e) => {
      busy(e.currentTarget, true);
      try {
        const saved = await postJSON("/system/community/export", { card: runToCardPayload(run) });
        if (!saved.success) throw new Error(saved.error || "Save failed.");
        $("#contribute-save-status", overlay).textContent = `Saved to ${saved.path}. ${saved.note}`;
        toast("Saved anonymized benchmark locally.", "success");
      } catch (err) {
        $("#contribute-save-status", overlay).textContent = `Save failed: ${err.message}`;
      } finally {
        busy(e.currentTarget, false);
      }
    });
  } catch (err) {
    toast(`Could not build preview: ${err.message}`, "error");
  } finally {
    busy(btn, false);
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

async function loadBenchmarkPacks() {
  const sel = $("#bench-pack");
  if (!sel) return;
  try {
    const data = await getJSON("/benchmark/packs");
    if (!data.success) return;
    sel.innerHTML =
      `<option value="">Full suite</option>` +
      data.packs
        .map((p) => `<option value="${esc(p.id)}" title="${esc(p.description)}">${esc(p.label)} (${esc(p.test_count)})</option>`)
        .join("");
  } catch {
    // Packs are a convenience filter; the full suite remains the default on failure.
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
$("#btn-hf-search")?.addEventListener("click", (e) => searchUnifiedModels(e));
$("#hf-search")?.addEventListener("keydown", (e) => {
  if (e.key === "Enter") {
    e.preventDefault();
    clearTimeout(state.unifiedSearchTimer);
    searchUnifiedModels(e);
  }
});
// Realtime: re-query both sources shortly after typing stops.
$("#hf-search")?.addEventListener("input", () => {
  clearTimeout(state.unifiedSearchTimer);
  state.unifiedSearchTimer = setTimeout(() => searchUnifiedModels(), 650);
});
["#remote-source-filter", "#remote-size-filter", "#remote-fit-filter", "#remote-cap-filter"].forEach((selector) =>
  $(selector)?.addEventListener("change", () => renderRemoteCatalog())
);
$("#remote-sort")?.addEventListener("change", (e) => {
  state.remoteCatalog.sort = e.target.value;
  renderRemoteCatalog();
});
$("#remote-installed-filter")?.addEventListener("change", () => renderRemoteCatalog());
$("#btn-remote-clear")?.addEventListener("click", () => {
  ["#remote-source-filter", "#remote-size-filter", "#remote-fit-filter", "#remote-cap-filter"].forEach((selector) => {
    const control = $(selector);
    if (control) control.value = "all";
  });
  if ($("#remote-installed-filter")) $("#remote-installed-filter").checked = false;
  state.remoteCatalog.sort = "popularity-desc";
  if ($("#remote-sort")) $("#remote-sort").value = state.remoteCatalog.sort;
  renderRemoteCatalog();
});
$("#btn-provider-refresh")?.addEventListener("click", (e) => loadProviderCatalog(e));
$("#catalog-search")?.addEventListener("input", () => { state.catalog.page = 0; renderProviderCatalog(); });
["#catalog-provider", "#catalog-size", "#catalog-sort"].forEach((sel) =>
  $(sel)?.addEventListener("change", () => { state.catalog.page = 0; renderProviderCatalog(); })
);
$("#catalog-prev")?.addEventListener("click", () => { state.catalog.page -= 1; renderProviderCatalog(); });
$("#catalog-next")?.addEventListener("click", () => { state.catalog.page += 1; renderProviderCatalog(); });
$("#btn-fit-profiles")?.addEventListener("click", scanConfiguredFits);
$("#btn-clean-orphans")?.addEventListener("click", (e) => cleanOrphanProfiles(e.currentTarget));
$("#btn-quant-advise")?.addEventListener("click", (e) => quantAdvise(e.currentTarget));
$("#quant-model")?.addEventListener("input", (e) => {
  e.target.dataset.params = "";
  e.target.dataset.source = "ollama";
  e.target.dataset.family = "";
});
$("#quant-model")?.addEventListener("keydown", (e) => {
  if (e.key === "Enter") {
    e.preventDefault();
    quantAdvise($("#btn-quant-advise"));
  }
});
$("#installed-sort")?.addEventListener("change", (e) => {
  state.installedSort = e.target.value;
  if (state.installedList.length) renderInstalledList();
});
$("#btn-bulk-delete")?.addEventListener("click", (e) => bulkDeleteSelected(e.currentTarget));
$("#btn-bulk-clear")?.addEventListener("click", () => {
  state.installedSelection.clear();
  $$("#installed-body .model-select").forEach((cb) => (cb.checked = false));
  updateBulkDeleteBar();
});
// Chat playground
$("#btn-chat-send")?.addEventListener("click", sendChatMessage);
$("#btn-chat-clear")?.addEventListener("click", clearChat);
$("#chat-model")?.addEventListener("change", updateChatModelState);
$("#chat-keep-alive")?.addEventListener("change", updateChatModelState);
$("#btn-chat-session")?.addEventListener("click", toggleChatSession);
$("#btn-chat-system-toggle")?.addEventListener("click", () => {
  const panel = $("#chat-system-panel");
  panel.classList.toggle("hidden");
  if (!panel.classList.contains("hidden")) $("#chat-system").focus();
});
$("#chat-images")?.addEventListener("change", (e) => {
  if (!chatModelIsReady()) toast("Load an installed model before attaching files.", "error");
  else addChatImages(e.target.files);
  e.target.value = "";
});
const chatComposer = $(".chat-composer");
chatComposer?.addEventListener("dragover", (e) => {
  if (!chatModelIsReady()) return;
  e.preventDefault();
  chatComposer.classList.add("dragging");
});
chatComposer?.addEventListener("dragleave", (e) => {
  if (!chatComposer.contains(e.relatedTarget)) chatComposer.classList.remove("dragging");
});
chatComposer?.addEventListener("drop", (e) => {
  e.preventDefault();
  chatComposer.classList.remove("dragging");
  if (!chatModelIsReady()) {
    toast("Load an installed model before dropping files here.", "error");
    return;
  }
  addChatImages(e.dataTransfer?.files);
});
$("#chat-input")?.addEventListener("input", (e) => {
  const el = e.target;
  el.style.height = "auto";
  el.style.height = Math.min(el.scrollHeight, 180) + "px";
});
$("#chat-input")?.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    // Only the Send/Stop button stops a stream; Enter while streaming is a no-op
    // so composing the next question can't accidentally cut off the reply.
    if (!state.chatController) sendChatMessage();
  }
});
$("#btn-recommend-models")?.addEventListener("click", (e) => recommendModels(e));
// Bakeoff wiring disabled along with its UI section above (see the commented-out block).
// $("#btn-bakeoff-run")?.addEventListener("click", (e) => runBakeoff(e));
// wireBakeoffBudgetSelect();
$("#btn-free").addEventListener("click", freeMemory);
$("#btn-pull").addEventListener("click", () => pullModel());
$("#btn-pull-dismiss")?.addEventListener("click", dismissPullProgress);
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
$("#btn-contribute").addEventListener("click", contributeRun);
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

// ---------------------------------------------------------------------------
// Update check (Release R7 Phase A) — best-effort, silent when offline/unavailable.
// ---------------------------------------------------------------------------
async function checkForUpdates() {
  const chip = $("#update-chip");
  if (!chip) return;
  try {
    const data = await getJSON("/system/update-check");
    if (!data.checked) {
      chip.classList.add("hidden");
      return;
    }
    if (data.update_available) {
      chip.textContent = `⬆ ${data.latest_version} available`;
      chip.title = `LocalDeploy ${data.latest_version} is available (you're on ${data.current_version}). Click to view.`;
      chip.classList.remove("hidden");
      chip.onclick = () => data.url && window.open(data.url, "_blank", "noopener");
    } else {
      chip.classList.add("hidden");
    }
  } catch {
    chip.classList.add("hidden");
  }
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
      if (seg === "hf" && !state.remoteCatalogLoaded) searchUnifiedModels();
    })
  );
}

(async function init() {
  initTooltips();
  wireGetModelSegments();
  wireManifestZone();
  loadBenchmarkRuns();
  clearChat();
  initServerHistoryToggle();
  await loadProfiles();
  renderBenchmarkWorkspace();
  await Promise.allSettled([
    checkHardware(),
    refreshStatus(),
    refreshInstalled(),
    loadGraderTypes(),
    checkOllamaAvailability(),
    loadBenchmarkPacks(),
    checkForUpdates(),
  ]);
})();
