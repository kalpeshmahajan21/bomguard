// BOMGuard front end: upload -> stream per-part results -> brief.
const $ = (s) => document.querySelector(s);
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const safeUrl = (u) => (/^https?:\/\//i.test(u || "") ? u : "#");
const fmt = (n) => (n >= 1000 ? (n / 1000).toFixed(1) + "k" : String(n));

const state = { session: null, parts: new Map(), results: new Map(), es: null };

const BAND = { critical: "crit", watch: "watch", healthy: "ok" };
const LIFE_BAND = { Active: "ok", Generic: "ok", NRND: "watch", "Last Time Buy": "crit", EOL: "crit", Obsolete: "crit", Unknown: "unk" };
const STOCK_BAND = { "In stock": "ok", "Multi-source": "ok", Limited: "watch", "Out of stock": "crit", Unknown: "unk" };
const FIT = { "Drop-in": "fit-drop", "Pin-compatible": "fit-pin", Functional: "fit-func" };
const COLOR = { crit: "var(--crit)", watch: "var(--watch)", ok: "var(--ok)", unk: "var(--unk)" };

// ---------- config / header ----------
fetch("/api/config").then((r) => r.json()).then((c) => {
  $("#demo-banner").hidden = !c.mock;
  const short = (m) => m.split("/").pop();
  const keys = c.mock ? [] : [
    `<span class="key ${c.keys.nebius ? "on" : "off"}">Token Factory</span>`,
    `<span class="key ${c.keys.tavily ? "on" : "off"}">Tavily</span>`,
  ];
  $("#model-meta").innerHTML = keys.join("");
  $("#model-meta").title = Object.values(c.models).map(short).join("\n");
});

// ---------- intake ----------
const drop = $("#drop");
["dragenter", "dragover"].forEach((e) => drop.addEventListener(e, (ev) => { ev.preventDefault(); drop.classList.add("over"); }));
["dragleave", "drop"].forEach((e) => drop.addEventListener(e, (ev) => { ev.preventDefault(); drop.classList.remove("over"); }));
drop.addEventListener("drop", (ev) => { const f = ev.dataTransfer.files[0]; if (f) start({ file: f }); });
$("#file").addEventListener("change", (ev) => { const f = ev.target.files[0]; if (f) start({ file: f }); });
$("#run-sample").addEventListener("click", () => start({ sample: true }));
$("#show-example").addEventListener("click", showExample);

// A saved live run, rendered through the same code a live scan uses, so judges see a full report instantly.
async function showExample() {
  showError("");
  if (state.es) state.es.close();
  let ex;
  try {
    const r = await fetch("/api/example");
    ex = await r.json();
    if (!r.ok) throw new Error(ex.detail || "Couldn't load the example.");
  } catch (e) {
    showError(e.message);
    return;
  }
  resetBoard();
  state.session = ex.session;
  state.parts = new Map(ex.parts.map((p) => [p.row, p]));
  state.results = new Map(ex.results.map((r) => [r.row, r]));
  renderSkeleton(ex);
  ex.results.forEach((r) => renderRow(r, false));
  renderCounts();
  renderUsage(ex.usage);
  renderBrief(ex.brief);
  ["fast", "tavily", "extract", "reason"].forEach((t) => setChip(t, "used"));
  ["t1", "t2", "t3", "t4"].forEach((id) => $("#" + id).classList.add("done"));
  $("#stat-fast").innerHTML = `${ex.parts.length} rows · ${fmt(ex.usage.tiers.fast.tokens)} tok`;
  $("#file-name").textContent += ` · checked in ${ex.seconds}s`;
  const note = $("#example-note");
  note.textContent = `Saved result of a live run on ${ex.generated_at}. Click "Check the sample" above to run it live now.`;
  note.hidden = false;
  sortRows();
  $("#exports").hidden = false;
  $("#dl-md").href = `/api/report/${ex.session}.md`;
  $("#dl-csv").href = `/api/report/${ex.session}.csv`;
  $("#results").hidden = false;
  $("#results").scrollIntoView({ behavior: "smooth", block: "start" });
}

function showError(msg) {
  const el = $("#intake-error");
  el.textContent = msg;
  el.hidden = !msg;
}

async function start({ file, sample }) {
  showError("");
  $("#example-note").hidden = true;
  if (state.es) state.es.close();
  const btn = $("#run-sample");
  btn.disabled = true;
  resetBoard();
  setChip("fast", "active");
  liveTrace([]);
  const fd = new FormData();
  if (sample) fd.append("use_sample", "true");
  if (file) fd.append("file", file);

  let data;
  try {
    const r = await fetch("/api/bom", { method: "POST", body: fd });
    data = await r.json();
    if (!r.ok) throw new Error(data.detail || "Upload failed.");
  } catch (e) {
    showError(e.message);
    setChip("fast", "");
    btn.disabled = false;
    return;
  }
  setChip("fast", "used");
  $("#stat-fast").innerHTML = `${data.parts.length} rows · ${fmt(data.usage.fast.tokens)} tok`;
  $("#t1").classList.add("done");

  state.session = data.session;
  state.parts = new Map(data.parts.map((p) => [p.row, p]));
  state.results = new Map();
  renderSkeleton(data);
  $("#results").hidden = false;
  $("#results").scrollIntoView({ behavior: "smooth", block: "start" });
  analyze();
}

// ---------- streaming ----------
function analyze() {
  const es = new EventSource(`/api/analyze/${state.session}`);
  state.es = es;
  setChip("tavily", "active");
  setChip("extract", "active");
  liveTrace(["t2", "t3"]);

  es.addEventListener("stage", (e) => {
    const d = JSON.parse(e.data);
    if (d.stage === "brief") {
      setChip("tavily", "used"); setChip("extract", "used"); setChip("reason", "active");
      liveTrace(["t4"]);
      $("#headline").textContent = "Nemotron Ultra is writing the briefing…";
      return;
    }
    if (d.stage === "alternatives") { setChip("reason", "active"); liveTrace(["t2", "t3", "t4"]); }
    const label = { research: "Searching the web", extract: "Reading sources", alternatives: "Finding replacements" }[d.stage];
    const cell = document.querySelector(`#row-${d.row} .status`);
    if (cell && label) cell.innerHTML = `<span class="working">${label}</span>`;
  });

  es.addEventListener("part", (e) => {
    const r = JSON.parse(e.data);
    state.results.set(r.row, r);
    renderRow(r, true);
    renderCounts();
  });

  es.addEventListener("usage", (e) => renderUsage(JSON.parse(e.data)));
  es.addEventListener("brief", (e) => renderBrief(JSON.parse(e.data)));
  es.addEventListener("done", (e) => {
    es.close();
    const d = JSON.parse(e.data);
    setChip("reason", "used");
    liveTrace([]);
    ["t1", "t2", "t3", "t4"].forEach((id) => $("#" + id).classList.add("done"));
    $("#file-name").textContent += ` · checked in ${d.seconds}s`;
    sortRows();
    $("#exports").hidden = false;
    $("#dl-md").href = `/api/report/${state.session}.md`;
    $("#dl-csv").href = `/api/report/${state.session}.csv`;
    $("#run-sample").disabled = false;
  });
  es.addEventListener("error", (e) => {
    if (e.data) showError(JSON.parse(e.data).message);
    if (es.readyState === EventSource.CLOSED || e.data) { es.close(); $("#run-sample").disabled = false; }
  });
}

// ---------- board ----------
function resetBoard() {
  ["fast", "tavily", "extract", "reason"].forEach((t) => { setChip(t, ""); $("#stat-" + t).textContent = "idle"; });
  ["t1", "t2", "t3", "t4"].forEach((id) => $("#" + id).classList.remove("live", "done"));
}
function setChip(tier, cls) {
  const el = $("#chip-" + tier);
  el.classList.remove("active", "used");
  if (cls) el.classList.add(cls);
}
function liveTrace(ids) {
  ["t1", "t2", "t3", "t4"].forEach((id) => $("#" + id).classList.toggle("live", ids.includes(id)));
}
function renderUsage(u) {
  for (const k of ["fast", "extract", "reason"]) {
    const t = u.tiers[k];
    if (t.calls) $("#stat-" + k).innerHTML = `${t.calls} calls · ${fmt(t.tokens)} tok`;
  }
  const tv = u.tavily;
  if (tv.searches || tv.cache_hits) $("#stat-tavily").innerHTML = `${tv.searches} searches · ${tv.sources} sources`;
}

// ---------- table ----------
function renderSkeleton(data) {
  $("#file-name").textContent = `${data.name} · ${data.parts.length} lines`;
  $("#headline").textContent = "Checking every part against the live web…";
  $("#health").textContent = "–";
  $("#g-fg").style.strokeDashoffset = 314.16;
  $("#brief").hidden = true;
  $("#exports").hidden = true;
  $("#rows").innerHTML = data.parts.map((p) => `
    <tr id="row-${p.row}" tabindex="0" data-row="${p.row}">
      <td class="ref" title="${esc(p.refdes)}">${esc(p.refdes || "#" + p.row)}</td>
      <td><div class="part-mpn">${esc(p.mpn || p.description)}</div><div class="part-sub">${esc([p.manufacturer, p.category].filter(Boolean).join(" · "))}</div></td>
      <td class="status" colspan="4"><span class="queued">Queued</span></td>
    </tr>`).join("");
  renderCounts();
}

function renderRow(r, fresh) {
  const tr = $("#row-" + r.row);
  if (!tr) return;
  const lb = LIFE_BAND[r.lifecycle] || "unk";
  const sb = STOCK_BAND[r.availability] || "unk";
  const rb = BAND[r.band] || "unk";
  const alts = (r.alternatives || []).map((a) => `${esc(a.mpn)} <span class="fit">${esc(a.fit)}</span>`).join("<br>");
  tr.innerHTML = `
    <td class="ref" title="${esc(r.refdes)}">${esc(r.refdes || "#" + r.row)}</td>
    <td><div class="part-mpn">${esc(r.mpn || r.description)}</div><div class="part-sub">${esc([r.manufacturer, r.category || r.description].filter(Boolean).join(" · "))}</div></td>
    <td><span class="pill pill-${lb}">${esc(r.lifecycle)}</span>${r.confidence === "low" ? ' <span class="part-sub">low confidence</span>' : ""}</td>
    <td><span class="pill pill-${sb}">${esc(r.availability)}</span></td>
    <td class="num"><div class="risk-cell"><span class="risk-bar"><i style="width:${r.risk}%;background:${COLOR[rb]}"></i></span><span class="risk-num">${r.risk}</span></div></td>
    <td class="alts">${alts || '<span class="part-sub">—</span>'}</td>`;
  tr.dataset.risk = r.risk;
  if (fresh) { tr.classList.remove("fresh"); void tr.offsetWidth; tr.classList.add("fresh"); }
}

function sortRows() {
  const body = $("#rows");
  [...body.children].sort((a, b) => (+b.dataset.risk || 0) - (+a.dataset.risk || 0)).forEach((tr) => body.appendChild(tr));
}

function renderCounts() {
  const rs = [...state.results.values()];
  const c = { critical: 0, watch: 0, healthy: 0 };
  rs.forEach((r) => (c[r.band] = (c[r.band] || 0) + 1));
  const pending = state.parts.size - rs.length;
  $("#counts").innerHTML = `
    <div class="c-crit"><dd>${c.critical}</dd><dt>critical</dt></div>
    <div class="c-watch"><dd>${c.watch}</dd><dt>to watch</dt></div>
    <div class="c-ok"><dd>${c.healthy}</dd><dt>healthy</dt></div>
    ${pending ? `<div><dd>${pending}</dd><dt>in progress</dt></div>` : ""}`;
}

// ---------- brief ----------
function renderBrief(b) {
  const h = b.health;
  $("#health").textContent = h;
  const g = $("#g-fg");
  g.style.stroke = h >= 75 ? "var(--ok)" : h >= 50 ? "var(--watch)" : "var(--crit)";
  g.style.strokeDashoffset = 314.16 * (1 - h / 100);
  $("#headline").textContent = b.headline || "Analysis complete.";
  const URG = { now: ["Now", "urg-now"], "this quarter": ["This quarter", "urg-quarter"], monitor: ["Monitor", "urg-monitor"] };
  $("#top-risks").innerHTML = (b.top_risks || []).map((t) => {
    const [label, cls] = URG[t.urgency] || ["Review", "urg-monitor"];
    return `<li><span class="pill urg ${cls}">${label}</span><b>${esc(t.mpn)}</b><p>${esc(t.why)} <em>${esc(t.action)}</em></p></li>`;
  }).join("") || '<li class="empty">No parts need action.</li>';
  $("#next-steps").innerHTML = (b.next_steps || []).map((s) => `<li>${esc(s)}</li>`).join("");
  const m = b.market_notes || [];
  $("#market").hidden = !m.length;
  $("#market-notes").innerHTML = m.map((n) => `<li>${esc(n.claim)} <a href="${esc(safeUrl(n.url))}" target="_blank" rel="noopener">source</a></li>`).join("");
  $("#brief").hidden = false;
}

// ---------- drawer ----------
const drawer = $("#drawer");
let lastFocus = null;
$("#rows").addEventListener("click", (e) => { const tr = e.target.closest("tr"); if (tr) openPart(+tr.dataset.row); });
$("#rows").addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { const tr = e.target.closest("tr"); if (tr) { e.preventDefault(); openPart(+tr.dataset.row); } } });
drawer.addEventListener("click", (e) => { if (e.target.closest("[data-close]")) closeDrawer(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && drawer.classList.contains("open")) closeDrawer(); });

function openPart(row) {
  const r = state.results.get(row);
  const p = state.parts.get(row);
  const x = r || p;
  if (!x) return;
  const fitCls = (f) => FIT[f] || "fit-func";
  const body = !r ? `<p class="empty">Still being checked. Results appear here as soon as this part is done.</p>` : `
    <dl class="d-facts">
      <div><dt>Lifecycle</dt><dd style="color:${COLOR[LIFE_BAND[r.lifecycle] || "unk"]}">${esc(r.lifecycle)}</dd></div>
      <div><dt>Stock</dt><dd style="color:${COLOR[STOCK_BAND[r.availability] || "unk"]}">${esc(r.availability)}</dd></div>
      <div><dt>RoHS</dt><dd>${esc(r.rohs)}</dd></div>
      <div><dt>Risk score · confidence</dt><dd>${r.risk} / 100 · ${esc(r.confidence)}</dd></div>
    </dl>
    <p class="d-summary">${esc(r.summary)}</p>
    ${r.successor ? `<p class="reco">Named successor: <b class="mono">${esc(r.successor)}</b></p>` : ""}
    ${(r.alternatives || []).length ? `<h3>Replacements</h3>${r.alternatives.map((a) => `
      <div class="alt-card">
        <div class="alt-head"><b>${esc(a.mpn)}</b><span class="pill ${fitCls(a.fit)}">${esc(a.fit)}</span></div>
        <p>${esc(a.manufacturer)}. ${esc(a.differences)}</p>
        <a href="${esc(safeUrl(a.url))}" target="_blank" rel="noopener">${esc(a.source_title || a.url)}</a>
      </div>`).join("")}${r.recommendation ? `<p class="reco">${esc(r.recommendation)}</p>` : ""}` : ""}
    <h3>Evidence</h3>
    ${(r.claims || []).length ? `<ul class="claims">${r.claims.map((c) => `
      <li>${esc(c.claim)}<a href="${esc(safeUrl(c.url))}" target="_blank" rel="noopener">${esc(c.source_title || c.url)}</a></li>`).join("")}</ul>`
      : `<p class="empty">No cited evidence. ${r.lifecycle === "Generic" ? "Generic parts are skipped to save searches." : "Treat this result as unverified."}</p>`}
    ${(r.sources || []).length ? `<h3>All sources read (${r.sources.length})</h3><ul class="claims">${r.sources.map((s) => `
      <li><a href="${esc(safeUrl(s.url))}" target="_blank" rel="noopener">${esc(s.title || s.url)}</a></li>`).join("")}</ul>` : ""}`;
  $("#drawer-body").innerHTML = `
    <h2 class="d-mpn" id="d-title">${esc(x.mpn || x.description)}</h2>
    <p class="d-sub">${esc([x.manufacturer, x.category, x.refdes].filter(Boolean).join(" · "))}</p>${body}`;
  lastFocus = document.activeElement;
  drawer.classList.add("open");
  drawer.setAttribute("aria-hidden", "false");
  drawer.querySelector(".drawer-close").focus();
}
function closeDrawer() {
  drawer.classList.remove("open");
  drawer.setAttribute("aria-hidden", "true");
  if (lastFocus) lastFocus.focus();
}
