"use strict";

// ---------------------------------------------------------------------------
// Small helpers
// ---------------------------------------------------------------------------
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

const state = {
  profiles: [],
  profileModels: {},
  defaultProfile: null,
  freeVramMb: null,
  servedModels: [],
  lastHardware: null,
  lastRun: null,
  cardA: null,
  cardB: null,
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
async function postMaybeStream(url, body, onEvent) {
  const resp = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify(body ?? {}),
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
  if (hw && hw.gpu_available && hw.gpus?.[0]) {
    const g = hw.gpus[0];
    chip.innerHTML = `<span class="dot"></span>${esc(g.name)} · ${fmtMb(g.vram_free_mb)} free`;
  } else {
    chip.innerHTML = `<span class="dot none"></span>CPU only`;
  }
  chip.classList.remove("hidden");
}

// Read the target VRAM the user wants to validate against (manual or probed).
function targetVram() {
  const raw = $("#vram-target").value.trim();
  if (raw !== "") {
    const n = Number(raw);
    return Number.isFinite(n) && n > 0 ? n : null;
  }
  return state.freeVramMb;
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
  ["#btn-serve", "#btn-stop", "#btn-switch", "#btn-run"].forEach((sel) => {
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
    const body = $("#hardware-body");
    if (!hw.gpu_available) {
      state.freeVramMb = null;
      state.lastHardware = { gpu: null, vram_total_mb: null, vram_free_mb: null };
      body.innerHTML = `<div class="muted">${esc(hw.message || "No GPU detected.")}</div>
        <div class="muted small">Logical cores: ${esc(hw.system?.logical_cores ?? "?")}</div>`;
      return;
    }
    const g = hw.gpus?.[0];
    if (!g) {
      body.innerHTML = `<div class="muted">GPU reported but no details available.</div>`;
      return;
    }
    state.freeVramMb = g.vram_free_mb ?? null;
    state.lastHardware = { gpu: g.name, vram_total_mb: g.vram_total_mb, vram_free_mb: g.vram_free_mb };
    if (state.freeVramMb != null && $("#vram-target").value.trim() === "") {
      $("#vram-target").value = state.freeVramMb;
    }
    body.innerHTML = `<div class="kv">
      <span class="k">GPU</span><span>${esc(g.name)}</span>
      <span class="k">VRAM</span><span>${fmtMb(g.vram_total_mb)} total · ${fmtMb(g.vram_free_mb)} free · ${fmtMb(g.vram_used_mb)} used</span>
      <span class="k">Driver</span><span>${esc(g.driver_version ?? "?")}</span>
      <span class="k">Cores</span><span>${esc(hw.system?.logical_cores ?? "?")}</span>
    </div>`;
  } catch (err) {
    toast(`Hardware check failed: ${err.message}`, "error");
  } finally {
    busy(btn, false);
  }
}

function fmtMb(mb) {
  if (mb == null) return "?";
  return mb >= 1024 ? `${(mb / 1024).toFixed(1)} GB` : `${mb} MB`;
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
    const body = $("#status-body");
    const reachable = s.ollama?.reachable
      ? `<span class="badge on">Ollama online</span>`
      : `<span class="badge off">Ollama offline</span>`;
    let served;
    if (state.servedModels.length) {
      served = s.ollama.running
        .map(
          (m) =>
            `<div class="mrow"><span class="name">${esc(m.name)}</span>
             <span class="meta">VRAM ${fmtMb(Math.round((m.size_vram || 0) / 1e6))}</span></div>`
        )
        .join("");
    } else {
      served = `<div class="muted">No model is currently loaded.</div>`;
    }
    body.innerHTML = `<div style="margin-bottom:.5rem">${reachable}</div>${served}`;
  } catch (err) {
    toast(`Status failed: ${err.message}`, "error");
  } finally {
    busy(btn, false);
  }
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

async function serveModel() {
  const btn = $("#btn-serve");
  busy(btn, true);
  try {
    const res = await postJSON("/models/serve", {
      profile: $("#profile-select").value,
      keep_alive: $("#keep-alive").value.trim() || "5m",
    });
    showServeResult(res);
    await refreshStatus();
  } catch (err) {
    toast(`Serve failed: ${err.message}`, "error");
  } finally {
    busy(btn, false);
  }
}

async function stopModel() {
  const btn = $("#btn-stop");
  busy(btn, true);
  try {
    const res = await postJSON("/models/stop", { profile: $("#profile-select").value });
    showServeResult(res);
    await refreshStatus();
  } catch (err) {
    toast(`Stop failed: ${err.message}`, "error");
  } finally {
    busy(btn, false);
  }
}

async function switchModel() {
  const btn = $("#btn-switch");
  busy(btn, true);
  try {
    const res = await postJSON("/models/switch", {
      to_profile: $("#profile-select").value,
      from_model: state.servedModels[0] || null,
      keep_alive: $("#keep-alive").value.trim() || "5m",
    });
    showServeResult(res);
    await refreshStatus();
  } catch (err) {
    toast(`Switch failed: ${err.message}`, "error");
  } finally {
    busy(btn, false);
  }
}

// ---------------------------------------------------------------------------
// Tab 1 — Installed models + fit check
// ---------------------------------------------------------------------------
function verdictBadge(verdict) {
  const cls = verdict === "FITS" ? "fits" : verdict === "WONT_FIT" ? "wont" : "unknown";
  return `<span class="badge ${cls}">${esc(verdict)}</span>`;
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
    if (!data.installed.length) {
      body.innerHTML = `<div class="muted">No models pulled yet.</div>`;
      return;
    }
    body.innerHTML =
      `<div class="mlist">` +
      data.installed
        .map((m) => {
          const size = m.size ? fmtMb(Math.round(m.size / 1e6)) : "";
          return `<div class="mrow" data-model="${esc(m.name)}">
            <span class="name">${esc(m.name)}</span>
            <span class="meta">${esc(size)}</span>
            <span class="spacer"></span>
            <span class="fit"></span>
            <button class="btn fit-btn">Fit check</button>
          </div>`;
        })
        .join("") +
      `</div>`;
    $$(".fit-btn", body).forEach((b) =>
      b.addEventListener("click", () => fitCheckRow(b.closest(".mrow")))
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
  const slot = $(".fit", row);
  const btn = $(".fit-btn", row);
  busy(btn, true);
  slot.textContent = "…";
  try {
    const res = await postJSON("/system/fit-check", { model_id: model, free_vram_mb: targetVram() });
    if (res.verdict) {
      const req = res.estimate_gb?.required;
      const free = res.free_vram_gb;
      const detail = req != null ? ` ~${req} GB${free != null ? ` / ${free} GB free` : ""}` : "";
      slot.innerHTML = `${verdictBadge(res.verdict)}<span class="meta">${esc(detail)}</span>`;
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

// ---------------------------------------------------------------------------
// Tab 1 — Check New Models (Hugging Face)
// ---------------------------------------------------------------------------
async function checkUpdates() {
  const btn = $("#btn-updates");
  busy(btn, true);
  const body = $("#updates-body");
  body.innerHTML = `<div class="muted">Checking Hugging Face…</div>`;
  try {
    const data = await postJSON("/registry/check-updates", {});
    if (!data.online && (!data.results || !data.results.length)) {
      body.innerHTML = `<div class="muted">${esc(data.message || "Offline.")}</div>`;
      return;
    }
    const blocks = (data.results || [])
      .map((group) => {
        const rows = (group.candidates || [])
          .map((c) => {
            const flag = c.installed_match ? `<span class="badge on">installed</span>` : "";
            const date = c.last_modified ? c.last_modified.slice(0, 10) : "";
            const pull = c.pullable && c.pull_name
              ? `<button class="btn hf-pull-btn" data-model="${esc(c.pull_name)}">Pull</button>`
              : "";
            return `<div class="mrow">
              <a class="name" href="https://huggingface.co/${esc(c.id)}" target="_blank" rel="noopener">${esc(c.id)}</a>
              <span class="meta">${esc(date)}</span>
              <span class="spacer"></span>${flag}${pull}
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
  const log = $("#pull-log");
  busy(btn, true);
  log.classList.remove("hidden");
  log.textContent = "";
  const append = (line) => {
    log.textContent += line + "\n";
    log.scrollTop = log.scrollHeight;
  };
  try {
    const out = await postMaybeStream(
      "/models/pull",
      { model, free_vram_mb: targetVram(), allow_override: $("#pull-override").checked },
      (evt) => {
        if (evt.error) append(`error: ${evt.error}`);
        else if (evt.status) {
          const pct =
            evt.total && evt.completed
              ? ` ${Math.round((evt.completed / evt.total) * 100)}%`
              : "";
          append(`${evt.status}${pct}`);
        } else {
          append(JSON.stringify(evt));
        }
      }
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
    append(`error: ${err.message}`);
  } finally {
    busy(btn, false);
  }
}

// ---------------------------------------------------------------------------
// Tab 2 — Question set: example / upload / validate
// ---------------------------------------------------------------------------
async function loadExample() {
  try {
    const example = await getJSON("/benchmark/example");
    $("#qs-editor").value = JSON.stringify(example, null, 2);
    $("#validate-result").textContent = "";
  } catch (err) {
    toast(`Could not load example: ${err.message}`, "error");
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

// ---------------------------------------------------------------------------
// Tab 2 — Run (streamed)
// ---------------------------------------------------------------------------
async function runBenchmark() {
  const btn = $("#btn-run");
  const summary = $("#run-summary");
  const table = $("#run-table");
  const tbody = $("tbody", table);

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
  tbody.innerHTML = "";
  table.classList.remove("hidden");
  summary.className = "result";
  // Live counter so the wait before the first result (model load) isn't silent.
  const stopTimer = startElapsed(summary, "Running");
  // Disable export until THIS run succeeds, so it can't point at a stale result.
  $("#btn-export").disabled = true;

  const selectedProfile = $("#bench-profile-select").value;
  const body = {
    profiles: [selectedProfile],
    timeout: Number($("#bench-timeout").value) || 240,
  };
  if (questions) body.questions = questions;

  const collected = [];
  try {
    const out = await postMaybeStream("/benchmark/run", body, (evt) => {
      if (evt.event === "test_result") {
        collected.push({
          name: evt.name,
          category: evt.category,
          success: evt.success,
          accuracy: evt.accuracy,
          elapsed_seconds: evt.elapsed_seconds,
        });
        const tr = document.createElement("tr");
        const result = evt.success
          ? `<span class="pass">PASS</span>`
          : `<span class="fail">FAIL</span>`;
        tr.innerHTML = `<td>${esc(evt.profile)}</td><td>${esc(evt.name)}</td>
          <td>${esc(evt.category)}</td><td>${result}</td>
          <td class="num">${esc(evt.elapsed_seconds)}s</td>
          <td class="num">${esc(evt.accuracy)}</td>`;
        tbody.appendChild(tr);
      } else if (evt.event === "profile_aborted") {
        const tr = document.createElement("tr");
        tr.innerHTML = `<td>${esc(evt.profile)}</td><td colspan="5" class="fail">aborted: ${esc(evt.reason)}</td>`;
        tbody.appendChild(tr);
      } else if (evt.event === "run_end") {
        stopTimer();
        const parts = (evt.profiles || [])
          .map((p) => `${esc(p.profile)}: ${p.passed}/${p.tests} passed, avg acc ${p.avg_accuracy}`)
          .join(" · ");
        summary.className = "result ok";
        summary.innerHTML = `Done in ${esc(evt.elapsed_seconds)}s — ${parts}`;
        if (collected.length) {
          state.lastRun = {
            profile: selectedProfile,
            model_id: state.profileModels[selectedProfile] || selectedProfile,
            hardware: state.lastHardware || {},
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
    });
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
    summary.className = "result err";
    summary.textContent = `Run failed: ${err.message}`;
  } finally {
    stopTimer();
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
    downloadFile(`localdeploy-card-${name}.html`, out.html, "text/html");
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

function updateCompareStatus() {
  const label = (c) => (c ? esc(c.model_id || c.profile || "card") : "—");
  $("#compare-status").innerHTML = `A: ${label(state.cardA)} &nbsp;·&nbsp; B: ${label(state.cardB)}`;
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
    const sd = diff.summary_delta || {};
    const arrow = (d) => (d == null ? "" : d > 0 ? ` ▲ +${d}` : d < 0 ? ` ▼ ${d}` : " =");
    const rows = (diff.tests || [])
      .map(
        (r) => `<tr><td>${esc(r.name)}</td>
          <td class="num">${esc(r.accuracy_a ?? "—")} → ${esc(r.accuracy_b ?? "—")}${esc(arrow(r.accuracy_delta))}</td>
          <td class="num">${esc(r.latency_a ?? "—")} → ${esc(r.latency_b ?? "—")}${esc(arrow(r.latency_delta))}</td></tr>`
      )
      .join("");
    $("#compare-body").innerHTML = `
      <div class="result">${esc(diff.label_a)} → ${esc(diff.label_b)} &nbsp;·&nbsp;
        avg accuracy${esc(arrow(sd.avg_accuracy))} &nbsp;·&nbsp; avg latency${esc(arrow(sd.avg_latency_s))} &nbsp;·&nbsp;
        passed ${esc(sd.passed_a ?? "?")} → ${esc(sd.passed_b ?? "?")}</div>
      <div class="table-wrap"><table class="results">
        <thead><tr><th>Test</th><th class="num">Accuracy (A → B)</th><th class="num">Latency (A → B)</th></tr></thead>
        <tbody>${rows}</tbody></table></div>`;
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
  const stopTimer = startElapsed(body, "Fit-checking and benchmarking your profiles");
  try {
    const res = await postJSON("/system/recommend", { free_vram_mb: targetVram() });
    stopTimer();
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
        return `<tr><td>${esc(c.profile)}${star}</td>
          <td class="num">${esc(c.avg_accuracy)}</td>
          <td class="num">${esc(c.avg_latency_s)}s</td>
          <td class="num">${esc(c.margin_gb ?? "—")}</td>
          <td class="num">${esc(c.score)}</td></tr>`;
      })
      .join("");
    const skipped = (res.skipped || [])
      .map((s) => `<li>${esc(s.profile)} — ${esc(s.reason)}${s.required_gb ? ` (~${esc(s.required_gb)} GB)` : ""}</li>`)
      .join("");
    body.innerHTML = `
      <div class="result ok">Recommended: <b>${esc(rec.profile)}</b> — ${esc(rec.reasoning)}
        &nbsp; <button class="btn set-default-btn" data-profile="${esc(rec.profile)}">Set as default</button></div>
      <div class="table-wrap" style="margin-top:.5rem"><table class="results">
        <thead><tr><th>Profile</th><th class="num">Accuracy</th><th class="num">Latency</th><th class="num">Headroom</th><th class="num">Score</th></tr></thead>
        <tbody>${rows}</tbody></table></div>
      ${skipped ? `<h3 class="sub">Skipped (won’t fit)</h3><ul class="err-list">${skipped}</ul>` : ""}`;
    const sd = body.querySelector(".set-default-btn");
    if (sd) sd.addEventListener("click", () => setDefaultProfile(sd.dataset.profile, sd));
  } catch (err) {
    stopTimer();
    body.innerHTML = `<div class="muted">Tuning failed — ${esc(err.message)}</div>`;
    toast(`Tune failed: ${err.message}`, "error");
  } finally {
    stopTimer();
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
$("#btn-stop").addEventListener("click", stopModel);
$("#btn-switch").addEventListener("click", switchModel);
$("#btn-installed").addEventListener("click", refreshInstalled);
$("#btn-updates").addEventListener("click", checkUpdates);
$("#btn-pull").addEventListener("click", () => pullModel());
$("#btn-example").addEventListener("click", loadExample);
$("#btn-validate").addEventListener("click", validateSet);
$("#btn-run").addEventListener("click", runBenchmark);
$("#upload-json").addEventListener("change", (e) => uploadFile(e.target));
$("#btn-recommend").addEventListener("click", recommendTune);
$("#btn-export").addEventListener("click", exportCard);
$("#btn-compare").addEventListener("click", compareCards);
$("#card-a").addEventListener("change", (e) => readCardFile(e.target, "cardA"));
$("#card-b").addEventListener("change", (e) => readCardFile(e.target, "cardB"));

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
