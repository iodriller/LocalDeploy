"use strict";

// ---------------------------------------------------------------------------
// Small helpers
// ---------------------------------------------------------------------------
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

const state = {
  profiles: [],
  defaultProfile: null,
  freeVramMb: null,
  servedModels: [],
};

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
  );
}

function toast(message, kind = "info") {
  const node = document.createElement("div");
  node.className = `toast ${kind}`;
  node.textContent = message;
  $("#toasts").appendChild(node);
  setTimeout(() => node.remove(), 5000);
}

async function getJSON(url) {
  const resp = await fetch(url);
  if (!resp.ok && resp.headers.get("content-type")?.includes("application/json") !== true) {
    throw new Error(`${url} -> HTTP ${resp.status}`);
  }
  return resp.json();
}

async function postJSON(url, body) {
  const resp = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body ?? {}),
  });
  return resp.json();
}

// Read a fetch Response as Server-Sent Events; calls onEvent(obj) per `data:`
// line and resolves when the stream ends or a [DONE] marker arrives.
async function streamSSE(response, onEvent) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let idx;
    while ((idx = buf.indexOf("\n\n")) >= 0) {
      const block = buf.slice(0, idx);
      buf = buf.slice(idx + 2);
      for (const line of block.split("\n")) {
        if (!line.startsWith("data: ")) continue;
        const payload = line.slice(6);
        if (payload === "[DONE]") return;
        try {
          onEvent(JSON.parse(payload));
        } catch {
          /* ignore non-JSON keepalives */
        }
      }
    }
  }
}

// POST that may return JSON (e.g. a blocked action) or an SSE stream.
async function postMaybeStream(url, body, onEvent) {
  const resp = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body ?? {}),
  });
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
    setConn(true);
  } catch (err) {
    setConn(false);
    toast(`Could not load profiles: ${err.message}`, "error");
  }
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
    const body = $("#hardware-body");
    if (!hw.gpu_available) {
      state.freeVramMb = null;
      body.innerHTML = `<div class="muted">${esc(hw.message || "No GPU detected.")}</div>
        <div class="muted small">Logical cores: ${esc(hw.system?.logical_cores ?? "?")}</div>`;
      return;
    }
    const g = hw.gpus[0];
    state.freeVramMb = g.vram_free_mb ?? null;
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
            return `<div class="mrow">
              <a class="name" href="https://huggingface.co/${esc(c.id)}" target="_blank" rel="noopener">${esc(c.id)}</a>
              <span class="meta">${esc(date)}</span>
              <span class="spacer"></span>${flag}
            </div>`;
          })
          .join("");
        return `<h3 class="sub">“${esc(group.query)}”</h3><div class="mlist">${rows || '<div class="muted">none</div>'}</div>`;
      })
      .join("");
    const note = data.online ? "" : `<div class="muted small">${esc(data.message || "")}</div>`;
    body.innerHTML = blocks + note;
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
async function pullModel() {
  const model = $("#pull-model").value.trim();
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
  summary.textContent = "Running…";

  const body = {
    profiles: [$("#bench-profile-select").value],
    timeout: Number($("#bench-timeout").value) || 240,
  };
  if (questions) body.questions = questions;

  try {
    const out = await postMaybeStream("/benchmark/run", body, (evt) => {
      if (evt.event === "test_result") {
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
        const parts = (evt.profiles || [])
          .map((p) => `${esc(p.profile)}: ${p.passed}/${p.tests} passed, avg acc ${p.avg_accuracy}`)
          .join(" · ");
        summary.className = "result ok";
        summary.innerHTML = `Done in ${esc(evt.elapsed_seconds)}s — ${parts}`;
      } else if (evt.event === "error") {
        summary.className = "result err";
        summary.textContent = `Run error: ${evt.error}`;
      }
    });
    if (!out.streamed) {
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
$("#btn-pull").addEventListener("click", pullModel);
$("#btn-example").addEventListener("click", loadExample);
$("#btn-validate").addEventListener("click", validateSet);
$("#btn-run").addEventListener("click", runBenchmark);
$("#upload-json").addEventListener("change", (e) => uploadFile(e.target));

// Initial load
(async function init() {
  await loadProfiles();
  await Promise.allSettled([checkHardware(), refreshStatus(), loadGraderTypes()]);
})();
