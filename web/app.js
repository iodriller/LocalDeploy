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
  servedModels: [],
  runningDetails: [],
  runningPlacements: {},
  lastHardware: null,
  testBenchInfo: null,
  lastRun: null,
  cardA: null,
  cardB: null,
  fitRefreshTimer: null,
};

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

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
  );
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
      `Using ${fmtMb(budget)} as the planning budget. Live free is lower than that budget; the Served model card attributes Ollama's share when a loaded model exposes it, and the rest is not attributable from this UI. Choose "Current free" to filter without unloading anything.`;
  } else if (budget) {
    help.textContent = `Installed model badges, saved profile scan, and Hugging Face search all use this budget.`;
  } else {
    help.textContent = "Set a GPU budget to filter models by fit.";
  }
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
    if (!$("#fit-finder-body")?.textContent.includes("Not scanned")) scanConfiguredFits();
    if (!$("#updates-body")?.textContent.includes("Not checked")) checkUpdates();
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

// ---------------------------------------------------------------------------
// Tabs
// ---------------------------------------------------------------------------
$$(".tab").forEach((tab) =>
  tab.addEventListener("click", () => {
    $$(".tab").forEach((t) => t.classList.toggle("active", t === tab));
    $$(".tab").forEach((t) => t.setAttribute("aria-selected", t === tab ? "true" : "false"));
    const name = tab.dataset.tab;
    $$(".tab-panel").forEach((p) => p.classList.toggle("active", p.id === `tab-${name}`));
  })
);

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
    const options = state.profiles
      .map((name) => {
        const p = profiles[name] || {};
        const label = p.model_id ? `${name} — ${p.model_id}` : name;
        const sel = name === state.defaultProfile ? " selected" : "";
        return `<option value="${esc(name)}"${sel}>${esc(label)}</option>`;
      })
      .join("");
    $("#profile-select").innerHTML = options;
    $("#bench-profile-select").innerHTML = options;
    setProfileActionsEnabled(state.profiles.length > 0);
    setConn(true);
  } catch (err) {
    setConn(false);
    setProfileActionsEnabled(false);
    toast(`Could not load profiles: ${err.message}`, "error");
  }
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

// ---------------------------------------------------------------------------
// Tab 1 — Hardware
// ---------------------------------------------------------------------------
async function checkHardware() {
  const btn = $("#btn-hardware");
  busy(btn, true);
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
                <button class="btn danger compact kill-model-btn">Kill</button>
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
      served = `<div class="muted">No model is currently loaded. Start a profile below; kill buttons appear here after Ollama lists a loaded model.</div>`;
    }
    body.innerHTML = `<div style="margin-bottom:.5rem">${reachable}</div>${served}`;
    $$(".kill-model-btn", body).forEach((b) =>
      b.addEventListener("click", () => killRunningModel(b.closest(".running-card").dataset.model, b))
    );
  } catch (err) {
    toast(`Status failed: ${err.message}`, "error");
  } finally {
    busy(btn, false);
  }
}

async function killRunningModel(name, btn) {
  if (!name) return;
  busy(btn, true);
  try {
    const res = await postJSON("/models/stop", { model: name });
    if (res.success) {
      toast(res.message || `Unloaded ${name}.`, "success");
      await Promise.allSettled([refreshStatus(), checkHardware(), refreshInstalled()]);
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
// Tab 1 — Serve / Stop / Switch
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
    await refreshStatus();
  } catch (err) {
    stop();
    node.className = "result err";
    node.textContent = `Serve failed: ${err.message}`;
    toast(`Serve failed: ${err.message}`, "error");
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
    await refreshStatus();
  } catch (err) {
    stop();
    node.className = "result err";
    node.textContent = `Switch failed: ${err.message}`;
    toast(`Switch failed: ${err.message}`, "error");
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
  try {
    const data = await getJSON("/registry/installed");
    if (!data.success) {
      body.innerHTML = `<div class="muted">${esc(data.error || "Ollama unreachable.")}</div>`;
      return;
    }
    state.installedByName = {};
    if (!data.installed.length) {
      body.innerHTML = `<div class="muted">No models pulled yet.</div>`;
      return;
    }
    data.installed.forEach((m) => {
      if (m.name) state.installedByName[m.name] = m;
    });
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
          const loadedHint = loaded ? `<span class="meta">Kill from Served model card</span>` : "";
          return `<div class="mrow model-row" data-model="${esc(m.name)}">
            <div class="model-row-main">
              <span class="name">${esc(m.name)}</span>
              <span class="model-row-meta">${params}${quant}${date}<span class="meta">${esc(size)}</span>${loaded}${loadedHint}</span>
              <span class="fit"></span>
            </div>
            <span class="spacer"></span>
            <button class="btn start-installed-btn">Start</button>
            <button class="btn fit-btn">Fit check</button>
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
    // Auto-run the fit check for each row so warnings appear without a click.
    $$(".mrow", body).forEach((row) => fitCheckRow(row));
  } catch (err) {
    toast(`Installed list failed: ${err.message}`, "error");
  } finally {
    busy(btn, false);
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
    await Promise.allSettled([refreshStatus(), checkHardware(), refreshInstalled()]);
  } catch (err) {
    stop();
    toast(`Start failed: ${err.message}`, "error");
  } finally {
    busy(btn, false);
  }
}

function findInstalledModel(modelId) {
  if (!modelId) return null;
  if (state.installedByName[modelId]) return state.installedByName[modelId];
  const noTag = String(modelId).split(":")[0];
  return (
    Object.values(state.installedByName).find((m) => m.name === `${modelId}:latest`) ||
    Object.values(state.installedByName).find((m) => String(m.name || "").split(":")[0] === noTag) ||
    null
  );
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
      b.addEventListener("click", () => {
        $("#pull-model").value = b.dataset.model;
        pullModel(b.dataset.model);
      })
    );
  } catch (err) {
    body.innerHTML = `<div class="muted">Scan failed.</div>`;
    toast(`Fit scan failed: ${err.message}`, "error");
  } finally {
    busy(btn, false);
  }
}

function renderProfileFitCard(row) {
  const { profile, p, modelId, installed, fit } = row;
  const req = fit?.estimate_gb?.required;
  const margin = fit?.margin_gb;
  const installedBadge = installed ? `<span class="badge on">installed</span>` : `<span class="badge off">not pulled</span>`;
  const vision = p.supports_vision ? `<span class="badge">vision</span>` : "";
  const backend = p.backend ? `<span class="badge">${esc(p.backend)}</span>` : "";
  const enabled = p.enabled === false ? `<span class="badge off">disabled</span>` : `<span class="badge on">enabled</span>`;
  const action = installed
    ? `<button class="btn fit-start-profile-btn" data-profile="${esc(profile)}">Start</button>`
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
      ${enabled}${installedBadge}${vision}${backend}
      <span class="spacer"></span>
      <button class="btn select-profile-btn" data-profile="${esc(profile)}">Select</button>
      ${action}
    </div>
  </div>`;
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
    await Promise.allSettled([refreshStatus(), checkHardware(), refreshInstalled()]);
  } catch (err) {
    stop();
    toast(`Start failed: ${err.message}`, "error");
  } finally {
    busy(btn, false);
  }
}

// ---------------------------------------------------------------------------
// Tab 1 — Check New Models (Hugging Face)
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
  const btn = source?.currentTarget || source || $("#btn-updates");
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
      b.addEventListener("click", () => {
        $("#pull-model").value = b.dataset.model;
        pullModel(b.dataset.model);
      })
    );
  } catch (err) {
    body.innerHTML = `<div class="muted">Check failed.</div>`;
    toast(`Update check failed: ${err.message}`, "error");
  } finally {
    busy(btn, false);
  }
}

// ---------------------------------------------------------------------------
// Tab 1 — Pull (streamed, fit-gated)
// ---------------------------------------------------------------------------
async function pullModel(modelArg) {
  // Called from the Pull button (no string arg) or a candidate row (a name).
  const model = typeof modelArg === "string" && modelArg ? modelArg : $("#pull-model").value.trim();
  if (!model) {
    toast("Enter a model name to pull.", "error");
    return;
  }
  const btn = $("#btn-pull");
  const cancelBtn = $("#btn-pull-cancel");
  const log = $("#pull-log");
  busy(btn, true);
  log.classList.remove("hidden");
  log.textContent = "";
  const append = (line) => {
    log.textContent += line + "\n";
    log.scrollTop = log.scrollHeight;
  };
  // Wire a cancel that aborts the stream (closes the connection; Ollama stops).
  const controller = new AbortController();
  cancelBtn.hidden = false;
  const onCancel = () => controller.abort();
  cancelBtn.addEventListener("click", onCancel);
  try {
    const out = await postMaybeStream(
      "/models/pull",
      { model, free_vram_mb: targetVram(), allow_override: $("#pull-override").checked },
      (evt) => {
        if (evt.error) {
          append(`error: ${evt.error}`);
          return;
        }
        // A soft fit note (e.g. "won't fit GPU — will run on CPU") rides the start event.
        if (evt.note) append(`note: ${evt.note}`);
        if (evt.status) {
          const pct =
            evt.total && evt.completed
              ? ` ${Math.round((evt.completed / evt.total) * 100)}%`
              : "";
          append(`${evt.status}${pct}`);
        }
        // Unknown event shapes are ignored rather than dumped as raw JSON (I23).
      },
      controller.signal
    );
    if (!out.streamed) {
      const j = out.json || {};
      if (j.blocked_by === "fit-check") {
        append(`blocked by fit check: ${j.message || ""}`);
        if (j.fit?.estimate_gb) append(`  needs ~${j.fit.estimate_gb.required} GB`);
        append("  tick “Override fit check” to pull anyway.");
      } else {
        append(j.error || "Pull could not start.");
      }
    } else {
      append("done.");
      await refreshInstalled();
    }
  } catch (err) {
    if (err.name === "AbortError") {
      append("cancelled.");
      toast("Pull cancelled.", "info");
    } else {
      append(`error: ${err.message}`);
    }
  } finally {
    cancelBtn.hidden = true;
    cancelBtn.removeEventListener("click", onCancel);
    busy(btn, false);
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
      await refreshStatus();
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
    $("#validate-result").className = "result ok";
    $("#validate-result").textContent =
      `Loaded LocalDeploy test bench JSON: ${info.test_count} tests. You can edit it, validate it, or run it like any other question set.`;
  } catch (err) {
    toast(`Could not load LocalDeploy test bench metadata: ${err.message}`, "error");
  }
}

async function loadExample() {
  try {
    const example = await getJSON("/benchmark/example");
    $("#qs-editor").value = JSON.stringify(example, null, 2);
    $("#validate-result").className = "result";
    $("#validate-result").textContent = "Loaded a small JSON sample. Run benchmark will use this custom set until you clear the editor.";
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
    $("#validate-result").textContent = "";
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
    node.className = "result err";
    node.textContent = err.message;
    return null;
  }
  try {
    const report = await postJSON("/benchmark/validate", payload);
    if (report.valid) {
      node.className = "result ok";
      node.textContent = `Valid — ${report.question_count} question(s).`;
    } else {
      node.className = "result err";
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

// Render a per-category rollup (passed · avg accuracy · avg latency) so
// strengths/weaknesses are visible at a glance — e.g. strong at code, weak at math.
function renderCategoryRollup(tests) {
  const slot = $("#run-category-rollup");
  if (!tests.length) {
    slot.innerHTML = "";
    return;
  }
  const cats = {};
  for (const t of tests) {
    const c = t.category || "?";
    (cats[c] ||= []).push(t);
  }
  const avg = (arr) => (arr.length ? arr.reduce((s, v) => s + v, 0) / arr.length : 0);
  const rows = Object.keys(cats)
    .sort()
    .map((c) => {
      const subset = cats[c];
      const passed = subset.filter((t) => t.success).length;
      const acc = avg(subset.map((t) => t.accuracy || 0)).toFixed(2);
      const lat = avg(subset.filter((t) => t.success).map((t) => t.elapsed_seconds || 0)).toFixed(2);
      return `<tr><td>${esc(c)}</td><td class="num">${passed}/${subset.length}</td>
        <td class="num">${acc}</td><td class="num">${lat}s</td></tr>`;
    })
    .join("");
  slot.innerHTML = `<h3 class="sub">By category</h3>
    <div class="table-wrap"><table class="results">
      <thead><tr><th>Category</th><th class="num">Passed</th><th class="num">Avg accuracy</th><th class="num">Avg latency</th></tr></thead>
      <tbody>${rows}</tbody></table></div>`;
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
  };
}

// Append one test_result row (+ collapsible preview) to a tbody. Shared so the
// single run and the CPU-vs-GPU dual run render results identically.
function appendResultRow(tbody, evt) {
  const tps = evt.approx_tokens_per_second ?? null;
  const resultBadge = evt.success ? `<span class="pass">PASS</span>` : `<span class="fail">FAIL</span>`;
  const errSnippet = !evt.success && evt.error
    ? ` <span class="muted small" title="${esc(evt.error)}">${esc(evt.error.slice(0, 50))}${evt.error.length > 50 ? "…" : ""}</span>`
    : "";
  const warnBadge = evt.warning ? ` <span class="warn-badge" title="${esc(evt.warning)}">⚠</span>` : "";
  const tpsCell = tps != null ? `${tps.toFixed(1)}` : `<span class="muted">—</span>`;
  const hasPreview = !!evt.response_preview;

  const tr = document.createElement("tr");
  tr.innerHTML = `<td>${esc(evt.name)}</td>
    <td>${esc(evt.category)}</td>
    <td>${resultBadge}${errSnippet}${warnBadge}</td>
    <td class="num">${esc(evt.elapsed_seconds)}s</td>
    <td class="num">${tpsCell}</td>
    <td class="num">${esc(evt.accuracy)}</td>
    <td><button class="btn-preview${hasPreview ? "" : " btn-preview-none"}" aria-label="Toggle response preview" title="${hasPreview ? "Show/hide response" : "No response captured"}">▸</button></td>`;
  tbody.appendChild(tr);

  const previewTr = document.createElement("tr");
  previewTr.className = "preview-row hidden";
  previewTr.innerHTML = `<td colspan="7"><div class="response-preview">${esc(evt.response_preview || "(no preview)")}</div></td>`;
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

// Aggregate stats from collected tests (mirrors the server's _summary()).
function computeRunStats(collected) {
  const successes = collected.filter((t) => t.success);
  const total = collected.length;
  const mean = (arr) => arr.reduce((s, v) => s + v, 0) / arr.length;
  const lats = successes.map((t) => t.elapsed_seconds).filter(Boolean);
  const tpsList = successes.map((t) => t.approx_tokens_per_second).filter((v) => v != null);
  return {
    passed: successes.length,
    total,
    avgAcc: total ? mean(collected.map((t) => t.accuracy || 0)).toFixed(3) : "—",
    avgLat: lats.length ? mean(lats).toFixed(2) : "—",
    avgTps: tpsList.length ? mean(tpsList).toFixed(1) : "—",
  };
}

function statStripHTML(model, st, totalSeconds) {
  return `<div class="run-stat-strip">
    <span><b>${esc(model)}</b></span>
    <span class="stat-sep">·</span><span><b>${st.passed}/${st.total}</b> passed</span>
    <span class="stat-sep">·</span><span>acc <b>${st.avgAcc}</b></span>
    <span class="stat-sep">·</span><span>avg latency <b>${st.avgLat}s</b></span>
    <span class="stat-sep">·</span><span>avg tok/s <b>${st.avgTps}</b></span>
    <span class="stat-sep">·</span><span>total <b>${esc(totalSeconds)}s</b></span>
  </div>`;
}

// Update the run progress bar's width + ARIA value together.
function setProgress(pct) {
  const bar = $("#run-progress-bar");
  bar.style.width = `${pct}%`;
  bar.setAttribute("aria-valuenow", String(pct));
}

async function runBenchmark() {
  const btn = $("#btn-run");
  const cancelBtn = $("#btn-run-cancel");
  const summary = $("#run-summary");
  const table = $("#run-table");
  const tbody = $("tbody", table);
  const progressWrap = $("#run-progress-wrap");
  const progressBar = $("#run-progress-bar");
  const currentTestLabel = $("#run-current-test");

  // Use the editor's question set if present; otherwise run built-in tests.
  let questions = null;
  if ($("#qs-editor").value.trim()) {
    try {
      questions = parseEditor();
    } catch (err) {
      summary.className = "result err";
      summary.textContent = err.message;
      return;
    }
  }

  busy(btn, true);
  const bothBtn = $("#btn-run-both");
  if (bothBtn) bothBtn.disabled = true;
  tbody.innerHTML = "";
  $("#run-category-rollup").innerHTML = "";
  table.classList.remove("hidden");
  summary.className = "result";
  // Live counter so the wait before the first result (model load) isn't silent.
  const stopTimer = startElapsed(summary, "Running");
  // Disable export until THIS run succeeds, so it can't point at a stale result.
  $("#btn-export").disabled = true;

  // Progress state — populated once run_start arrives.
  let runTotal = 0;
  let runDone = 0;
  const updateProgress = (label) => {
    if (runTotal <= 0) return;
    setProgress(Math.round((runDone / runTotal) * 100));
    currentTestLabel.textContent = label || `${runDone} / ${runTotal} completed`;
  };

  // AbortController so the Cancel button can stop an in-flight benchmark.
  const controller = new AbortController();
  cancelBtn.hidden = false;
  const onCancel = () => controller.abort();
  cancelBtn.addEventListener("click", onCancel);

  const selectedProfile = $("#bench-profile-select").value;
  const benchDevice = ($("#bench-device")?.value || "auto").toLowerCase();
  const body = {
    profiles: [selectedProfile],
    timeout: Number($("#bench-timeout").value) || 240,
  };
  if (questions) body.questions = questions;

  const collected = [];
  try {
    const out = await postMaybeStream("/benchmark/run", body, (evt) => {
      if (evt.event === "run_start") {
        runTotal = evt.test_count || 0;
        progressWrap.classList.remove("hidden");
        updateProgress(`0 / ${runTotal} completed — waiting for model to load…`);
      } else if (evt.event === "profile_start") {
        currentTestLabel.textContent = `Profile: ${esc(evt.profile)}${evt.model_id ? ` · ${esc(evt.model_id)}` : ""} — loading…`;
      } else if (evt.event === "test_result") {
        runDone++;
        collected.push(collectTest(evt));
        updateProgress(`${runDone} / ${runTotal} completed`);
        appendResultRow(tbody, evt);
      } else if (evt.event === "profile_aborted") {
        const tr = document.createElement("tr");
        tr.innerHTML = `<td colspan="7" class="fail">${esc(evt.profile)} aborted: ${esc(evt.reason)}</td>`;
        tbody.appendChild(tr);
      } else if (evt.event === "run_end") {
        stopTimer();
        progressWrap.classList.add("hidden");

        const runModel = state.profileModels[selectedProfile] || selectedProfile;
        summary.className = "result ok";
        summary.innerHTML = statStripHTML(runModel, computeRunStats(collected), evt.elapsed_seconds);
        renderCategoryRollup(collected);

        if (collected.length) {
          state.lastRun = {
            profile: selectedProfile,
            model_id: state.profileModels[selectedProfile] || selectedProfile,
            hardware: state.lastHardware || {},
            device: benchDevice !== "auto" ? benchDevice : null,
            tests: collected,
          };
          $("#btn-export").disabled = false;
          $("#btn-export").title = "Download a shareable report card";
        }
      } else if (evt.event === "error") {
        stopTimer();
        summary.className = "result err";
        summary.textContent = `Run error: ${evt.error}`;
      }
    }, controller.signal);

    // Finalize the device tag from the model's *actual* placement now that it's
    // loaded — the card label should reflect where it really ran, not a guess.
    // A manual GPU/CPU choice stays an explicit override; we warn on mismatch.
    if (state.lastRun && collected.length) {
      try {
        await refreshStatus();
        const detected = detectDevice(selectedProfile);
        if (benchDevice === "auto") {
          state.lastRun.device = detected;
          // Quiet inline note (no toast spam on repeated runs).
          if (detected) {
            const note = document.createElement("span");
            note.className = "muted small";
            note.innerHTML = ` &nbsp;·&nbsp; device ${esc(detected.toUpperCase())}`;
            summary.querySelector(".run-stat-strip")?.appendChild(note);
          }
        } else if (detected && detected !== benchDevice) {
          toast(
            `You tagged ${benchDevice.toUpperCase()} but the model is running on ${detected.toUpperCase()}. The card keeps your tag.`,
            "error"
          );
        }
      } catch {
        /* placement detection is best-effort; keep the manual/empty tag */
      }
    }

    if (!out.streamed) {
      stopTimer();
      const j = out.json || {};
      summary.className = "result err";
      if (j.validation) {
        summary.innerHTML =
          `Invalid question set:` +
          `<ul class="err-list">` +
          (j.validation.errors || []).map((e) => `<li>${esc(e.error)}</li>`).join("") +
          `</ul>`;
      } else {
        summary.textContent = j.error || "Run could not start.";
      }
    }
  } catch (err) {
    if (err.name === "AbortError") {
      stopTimer();
      summary.className = "result";
      summary.textContent = "Run cancelled.";
      toast("Benchmark cancelled.", "info");
    } else {
      summary.className = "result err";
      summary.textContent = `Run failed: ${err.message}`;
    }
  } finally {
    stopTimer();
    progressWrap.classList.add("hidden");
    cancelBtn.hidden = true;
    cancelBtn.removeEventListener("click", onCancel);
    if (bothBtn) bothBtn.disabled = false;
    busy(btn, false);
  }
}

// One-click CPU-vs-GPU: deploy the profile to CPU, benchmark it; deploy to GPU,
// benchmark it; then diff the two cards in the Compare panel. Reuses the same
// serve/benchmark/compare endpoints the manual flow uses — pure orchestration.
async function runBothCompare() {
  const profile = $("#bench-profile-select").value;
  const model = state.profileModels[profile] || profile;
  const timeout = Number($("#bench-timeout").value) || 240;

  let questions = null;
  if ($("#qs-editor").value.trim()) {
    try {
      questions = parseEditor();
    } catch (err) {
      toast(err.message, "error");
      return;
    }
  }

  // Without a GPU both phases land on CPU — warn before doing the long work.
  if (state.lastHardware && !state.lastHardware.gpu &&
      !window.confirm("No GPU was detected — both runs would execute on CPU and the comparison won't be meaningful. Continue anyway?")) {
    return;
  }

  const btn = $("#btn-run-both");
  const runBtn = $("#btn-run");
  const cancelBtn = $("#btn-run-cancel");
  const summary = $("#run-summary");
  const tbody = $("tbody", $("#run-table"));
  const progressWrap = $("#run-progress-wrap");
  const currentTestLabel = $("#run-current-test");

  busy(btn, true);
  runBtn.disabled = true;
  $("#btn-export").disabled = true;
  $("#run-table").classList.remove("hidden");
  $("#run-category-rollup").innerHTML = "";

  const controller = new AbortController();
  cancelBtn.hidden = false;
  const onCancel = () => controller.abort();
  cancelBtn.addEventListener("click", onCancel);

  const cards = {};
  try {
    for (const [phase, device] of [["1", "cpu"], ["2", "gpu"]]) {
      // 1) Deploy to the device (serve returns once the model is warm).
      summary.className = "result";
      summary.innerHTML = `<span class="spin-inline"></span> Phase ${phase}/2 — deploying on ${device.toUpperCase()}…`;
      const served = await postJSON("/models/serve", { profile, keep_alive: "60m", device });
      if (!served.success) throw new Error(`Could not deploy on ${device.toUpperCase()}: ${served.error || served.message || "serve failed"}`);

      // 2) Benchmark it, streaming rows into the shared table.
      tbody.innerHTML = "";
      progressWrap.classList.remove("hidden");
      setProgress(0);
      const stopTimer = startElapsed(summary, `Phase ${phase}/2 — running on ${device.toUpperCase()}`);
      const collected = [];
      let total = 0;
      let done = 0;
      let endSeconds = "—";
      let runError = null;
      const body = { profiles: [profile], timeout };
      if (questions) body.questions = questions;
      try {
        await postMaybeStream("/benchmark/run", body, (evt) => {
          if (evt.event === "run_start") {
            total = evt.test_count || 0;
          } else if (evt.event === "test_result") {
            done++;
            collected.push(collectTest(evt));
            appendResultRow(tbody, evt);
            if (total > 0) setProgress(Math.round((done / total) * 100));
            currentTestLabel.textContent = `${device.toUpperCase()} · ${done} / ${total || "?"} completed`;
          } else if (evt.event === "profile_aborted") {
            const tr = document.createElement("tr");
            tr.innerHTML = `<td colspan="7" class="fail">${esc(evt.profile)} aborted: ${esc(evt.reason)}</td>`;
            tbody.appendChild(tr);
          } else if (evt.event === "run_end") {
            endSeconds = evt.elapsed_seconds;
          } else if (evt.event === "error") {
            runError = evt.error;
          }
        }, controller.signal);
      } finally {
        stopTimer();
      }
      if (runError) throw new Error(runError);
      if (!collected.length) throw new Error(`No results from the ${device.toUpperCase()} run (is the model pulled?).`);

      // 3) Confirm where it actually ran; warn if Ollama didn't honor the request.
      let detected = null;
      try {
        await refreshStatus();
        detected = detectDevice(profile);
      } catch {
        /* best-effort */
      }
      if (detected && detected !== device) {
        toast(`Requested ${device.toUpperCase()} but the model ran on ${detected.toUpperCase()}.`, "error");
      }
      cards[device] = {
        profile,
        model_id: model,
        device: detected || device,
        hardware: state.lastHardware || {},
        tests: collected,
        _seconds: endSeconds,
      };
    }

    // 4) Diff the two cards and surface the comparison.
    progressWrap.classList.add("hidden");
    const diff = await postJSON("/benchmark/compare", { card_a: cards.cpu, card_b: cards.gpu });
    renderCompare(diff);
    state.cardA = cards.cpu;
    state.cardB = cards.gpu;
    updateCompareStatus();
    // Keep the GPU run exportable as the "last run".
    state.lastRun = cards.gpu;
    $("#btn-export").disabled = false;
    $("#btn-export").title = "Download the GPU report card";

    summary.className = "result ok";
    summary.innerHTML =
      `Compared <b>${esc(model)}</b> on CPU vs GPU — see the comparison below. ` +
      statStripHTML(`${model} (GPU)`, computeRunStats(cards.gpu.tests), cards.gpu._seconds);
    $("#compare-body").scrollIntoView({ behavior: "smooth", block: "nearest" });
  } catch (err) {
    progressWrap.classList.add("hidden");
    if (err.name === "AbortError") {
      summary.className = "result";
      summary.textContent = "CPU-vs-GPU run cancelled.";
      toast("Cancelled.", "info");
    } else {
      summary.className = "result err";
      summary.textContent = `CPU-vs-GPU run failed: ${err.message}`;
    }
  } finally {
    cancelBtn.hidden = true;
    cancelBtn.removeEventListener("click", onCancel);
    runBtn.disabled = false;
    busy(btn, false);
  }
}

// ---------------------------------------------------------------------------
// Tab 2 — Report cards: export + compare (Step 13)
// ---------------------------------------------------------------------------
async function exportCard() {
  if (!state.lastRun) {
    toast("Run a benchmark first.", "error");
    return;
  }
  const btn = $("#btn-export");
  busy(btn, true);
  try {
    const out = await postJSON("/benchmark/export", state.lastRun);
    if (!out.success) throw new Error(out.error || "export failed");
    const name = (state.lastRun.profile || "model").replace(/[^\w.-]+/g, "_");
    const devSuffix = state.lastRun.device ? `-${state.lastRun.device}` : "";
    downloadFile(`localdeploy-card-${name}${devSuffix}.html`, out.html, "text/html");
    toast("Report card downloaded.", "success");
  } catch (err) {
    toast(`Export failed: ${err.message}`, "error");
  } finally {
    busy(btn, false);
  }
}

function readCardFile(input, slot) {
  const file = input.files?.[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = () => {
    const card = extractCard(String(reader.result || ""));
    if (!card) {
      toast(`${file.name}: not a LocalDeploy report card.`, "error");
      return;
    }
    state[slot] = card;
    updateCompareStatus();
  };
  reader.onerror = () => toast("Could not read file.", "error");
  reader.readAsText(file);
  input.value = "";
}

function cardLabel(c) {
  if (!c) return "—";
  const name = c.model_id || c.profile || "card";
  const dev = c.device ? `/${c.device.toUpperCase()}` : "";
  return esc(`${name}${dev}`);
}

function updateCompareStatus() {
  $("#compare-status").innerHTML = `A: ${cardLabel(state.cardA)} &nbsp;·&nbsp; B: ${cardLabel(state.cardB)}`;
}

// Render a /benchmark/compare result into the Compare panel. Shared by the
// manual two-card compare and the one-click CPU-vs-GPU flow.
function renderCompare(diff) {
  const sd = diff.summary_delta || {};
  const arrow = (d) => (d == null ? "" : d > 0 ? ` ▲ +${d}` : d < 0 ? ` ▼ ${d}` : " =");
  const pair = (av, bv) => `${esc(av ?? "—")} → ${esc(bv ?? "—")}`;
  const rows = (diff.tests || [])
    .map(
      (r) => `<tr><td>${esc(r.name)}</td>
        <td class="num">${pair(r.accuracy_a, r.accuracy_b)}${esc(arrow(r.accuracy_delta))}</td>
        <td class="num">${pair(r.latency_a, r.latency_b)}${esc(arrow(r.latency_delta))}</td>
        <td class="num">${pair(r.tps_a, r.tps_b)}${esc(arrow(r.tps_delta))}</td></tr>`
    )
    .join("");
  // Only show the aggregate tok/s stat when at least one card carried it.
  const tpsStat =
    sd.tps_a != null || sd.tps_b != null
      ? ` &nbsp;·&nbsp; avg tok/s ${esc(sd.tps_a ?? "—")} → ${esc(sd.tps_b ?? "—")}${esc(arrow(sd.avg_tokens_per_second))}`
      : "";
  $("#compare-body").innerHTML = `
    <div class="result">${esc(diff.label_a)} → ${esc(diff.label_b)} &nbsp;·&nbsp;
      avg accuracy${esc(arrow(sd.avg_accuracy))} &nbsp;·&nbsp; avg latency${esc(arrow(sd.avg_latency_s))}${tpsStat} &nbsp;·&nbsp;
      passed ${esc(sd.passed_a ?? "?")} → ${esc(sd.passed_b ?? "?")}</div>
    <div class="table-wrap"><table class="results">
      <thead><tr><th>Test</th><th class="num">Accuracy (A → B)</th><th class="num">Latency (A → B)</th><th class="num">tok/s (A → B)</th></tr></thead>
      <tbody>${rows}</tbody></table></div>`;
}

async function compareCards() {
  if (!state.cardA || !state.cardB) {
    toast("Load both Card A and Card B first.", "error");
    return;
  }
  const btn = $("#btn-compare");
  busy(btn, true);
  try {
    const diff = await postJSON("/benchmark/compare", { card_a: state.cardA, card_b: state.cardB });
    renderCompare(diff);
  } catch (err) {
    toast(`Compare failed: ${err.message}`, "error");
  } finally {
    busy(btn, false);
  }
}

// ---------------------------------------------------------------------------
// Tab 1 — Tune for my GPU (Step 14)
// ---------------------------------------------------------------------------
async function recommendTune() {
  const btn = $("#btn-recommend");
  const body = $("#recommend-body");
  busy(btn, true);
  const names = state.profiles.length ? state.profiles : ["saved profiles"];
  let idx = 0;
  const renderProgress = (stage) => {
    const profile = names[idx % names.length];
    const model = state.profileModels[profile] || profile;
    body.innerHTML = `
      <div class="tune-progress">
        <div class="running-top">
          <div>
            <div class="model-title">${esc(stage)}</div>
            <div class="muted small">Current saved profile: ${esc(profile)} · model ${esc(model)}</div>
          </div>
          <span class="spin-inline"></span>
        </div>
        <div class="run-progress-track"><div class="run-progress-fill indeterminate" role="progressbar" aria-label="Tune progress"></div></div>
        <ol class="tune-steps">
          <li>Read enabled saved profiles from config.json</li>
          <li>Estimate each profile's model against the ${esc(fmtMb(targetVram()))} GPU budget</li>
          <li>Skip profiles that do not fit that GPU budget</li>
          <li>Run a short built-in benchmark on candidates that can answer</li>
          <li>Rank accuracy first, speed second, VRAM headroom third</li>
        </ol>
      </div>`;
  };
  renderProgress("Preparing GPU tuning");
  const timer = setInterval(() => {
    idx += 1;
    renderProgress(idx % 2 === 0 ? "Fit-checking profiles" : "Benchmarking candidates");
  }, 1800);
  try {
    const res = await postJSON("/system/recommend", { free_vram_mb: targetVram() });
    clearInterval(timer);
    if (!res.success) {
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
        return `<tr><td><b>${esc(c.profile)}${star}</b><div class="muted small">${esc(model)}</div></td>
          <td>${esc(p.backend || "ollama")}</td>
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
        <thead><tr><th>Saved profile / model</th><th>Backend</th><th class="num">Accuracy</th><th class="num">Latency</th><th class="num">Headroom</th><th class="num">Score</th></tr></thead>
        <tbody>${rows}</tbody></table></div>
      ${skipped ? `<h3 class="sub">Skipped (won’t fit)</h3><ul class="err-list">${skipped}</ul>` : ""}`;
    const sd = body.querySelector(".set-default-btn");
    if (sd) sd.addEventListener("click", () => setDefaultProfile(sd.dataset.profile, sd));
  } catch (err) {
    clearInterval(timer);
    body.innerHTML = `<div class="muted">Tuning failed — ${esc(err.message)}</div>`;
    toast(`Tune failed: ${err.message}`, "error");
  } finally {
    clearInterval(timer);
    busy(btn, false);
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
$("#btn-hardware").addEventListener("click", checkHardware);
$("#btn-status").addEventListener("click", refreshStatus);
$("#btn-serve").addEventListener("click", serveModel);
$("#btn-switch").addEventListener("click", switchModel);
$("#btn-installed").addEventListener("click", refreshInstalled);
$("#btn-updates").addEventListener("click", (e) => checkUpdates(e));
$("#btn-hf-search")?.addEventListener("click", (e) => checkUpdates(e));
$("#btn-fit-profiles")?.addEventListener("click", scanConfiguredFits);
$("#btn-free").addEventListener("click", freeMemory);
$("#btn-pull").addEventListener("click", () => pullModel());
$("#btn-builtin-bench").addEventListener("click", useBuiltInBench);
$("#btn-example").addEventListener("click", loadExample);
$("#btn-validate").addEventListener("click", validateSet);
$("#btn-run").addEventListener("click", runBenchmark);
$("#btn-run-both").addEventListener("click", runBothCompare);
$("#upload-json").addEventListener("change", (e) => uploadFile(e.target));
$("#btn-recommend").addEventListener("click", recommendTune);
$("#btn-export").addEventListener("click", exportCard);
$("#btn-compare").addEventListener("click", compareCards);
$("#card-a").addEventListener("change", (e) => readCardFile(e.target, "cardA"));
$("#card-b").addEventListener("change", (e) => readCardFile(e.target, "cardB"));

$("#fit-filter")?.addEventListener("change", () => {
  if (!$("#fit-finder-body").textContent.includes("Not scanned")) scanConfiguredFits();
});
$("#hf-fit-filter")?.addEventListener("change", () => {
  if (!$("#updates-body").textContent.includes("Not checked")) checkUpdates();
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
// (hardware, what's running, what's installed) instead of "Not loaded yet."
(async function init() {
  await loadProfiles();
  await Promise.allSettled([
    checkHardware(),
    refreshStatus(),
    refreshInstalled(),
    loadGraderTypes(),
  ]);
})();
