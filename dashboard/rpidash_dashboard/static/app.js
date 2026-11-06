"use strict";

const REFRESH_MS = 5000;

// ---------- helpers ----------
const $ = (sel) => document.querySelector(sel);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

async function api(method, path, body) {
  const opts = { method, headers: {} };
  if (body !== undefined) { opts.headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(body); }
  const r = await fetch(path, opts);
  if (r.status === 204) return null;
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.detail || `HTTP ${r.status}`);
  return data;
}

let toastTimer;
function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove("show"), 3500);
}

function bytes(n) {
  if (n == null) return "–";
  const u = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return `${n.toFixed(n >= 10 || i === 0 ? 0 : 1)} ${u[i]}`;
}
const rate = (n) => (n == null ? "–" : `${bytes(n)}/s`);
const pct = (n) => (n == null ? "–" : `${Math.round(n)}%`);
const temp = (n) => (n == null ? "–" : `${n.toFixed(1)}°`);

function duration(s) {
  if (s == null) return "–";
  const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
  return d ? `${d}d ${h}h` : h ? `${h}h ${m}m` : `${m}m`;
}

function ago(iso) {
  if (!iso) return "never";
  const s = (Date.now() - Date.parse(iso)) / 1000;
  return s < 90 ? "just now" : `${duration(s)} ago`;
}

// 30 °C -> cool blue, 80 °C -> red. This colour is the card's left edge.
function heat(t) {
  if (t == null) return null;
  const x = Math.min(1, Math.max(0, (t - 30) / 50));
  return `hsl(${Math.round(210 - 210 * x)} 70% 50%)`;
}

const memPct = (s) => (s && s.mem_total ? (s.mem_used / s.mem_total) * 100 : null);
const level = (v) => (v == null ? "" : v >= 90 ? "crit" : v >= 75 ? "high" : "");

const WARN_TEXT = { hot: "Running hot", disk: "Disk nearly full", mem: "RAM nearly full", throttled: "Throttled or under-voltage" };
const STATUS_TEXT = { online: "Online", offline: "Offline", auth_failed: "Token rejected" };

// ---------- overview ----------
function bar(label, v) {
  const w = v == null ? 0 : Math.min(100, v);
  return `<span>${label}</span><span class="bar"><i class="${level(v)}" style="width:${w}%"></i></span><span class="num">${pct(v)}</span>`;
}

function card(p) {
  const s = p.live && p.live.sample;
  const spec = p.spec || {};
  const el = document.createElement("article");
  el.className = `card ${p.status}`;
  const h = s && heat(s.temp_c);
  if (h) el.style.setProperty("--heat", h);
  let body;
  if (s) {
    body = `<div class="bars">${bar("CPU", s.cpu_pct)}${bar("RAM", memPct(s))}${bar("Disk", s.disk_pct)}</div>
      <div class="meta"><span>↓ ${rate(s.net_rx_bps)}</span><span>↑ ${rate(s.net_tx_bps)}</span><span>up ${duration(p.live.uptime_s)}</span></div>`;
  } else {
    body = `<div class="meta"><span>${esc(spec.os || "")}</span><span>${spec.ram_total ? bytes(spec.ram_total) + " RAM" : ""}</span><span>last seen ${ago(p.last_seen)}</span></div>`;
  }
  const chips = p.warnings.map((w) => `<span class="chip ${w === "disk" || w === "mem" ? "warn" : "bad"}">${WARN_TEXT[w] || esc(w)}</span>`);
  if (p.status !== "online") chips.unshift(`<span class="chip ${p.status === "auth_failed" ? "bad" : "off"}">${STATUS_TEXT[p.status]}</span>`);
  el.innerHTML = `
    <div class="card-head"><span class="dot ${p.status}"></span><h2><a class="stretch" href="/pi/${encodeURIComponent(p.id)}">${esc(p.hostname)}</a></h2>
      <span class="temp">${s ? temp(s.temp_c) : ""}</span></div>
    <div class="model">${esc(spec.model || p.host)}</div>
    ${body}
    <div class="card-foot">${chips.length ? `<div class="chips">${chips.join("")}</div>` : ""}
      <button class="small remove" title="Remove from this dashboard">Remove</button></div>`;
  el.querySelector(".remove").addEventListener("click", async (e) => {
    e.preventDefault();
    if (!confirm(`Remove ${p.hostname} from the dashboard? The Pi keeps running its agent.`)) return;
    await api("DELETE", `/api/pis/${encodeURIComponent(p.id)}`);
    toast(`Removed ${p.hostname}`);
    refreshOverview();
  });
  return el;
}

async function refreshOverview() {
  let pis;
  try { pis = await api("GET", "/api/pis"); } catch (e) { toast(`Couldn't load Pis: ${e.message}`); return; }
  const grid = $("#pis");
  grid.replaceChildren(...pis.map(card));
  $("#empty").hidden = pis.length > 0;
  const online = pis.filter((p) => p.status === "online").length;
  $("#summary").textContent = pis.length ? `${online} of ${pis.length} online` : "";
}

async function addPi(host) {
  const pi = await api("POST", "/api/pis", { host });
  toast(`Added ${pi.hostname}`);
  refreshOverview();
}

function overview() {
  $("#add-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const host = $("#add-host").value.trim();
    if (!host) return;
    try { await addPi(host); $("#add-host").value = ""; }
    catch (err) { toast(`No agent answered at ${host}. Check the address and that rpidash-agent is running.`); }
  });
  $("#scan").addEventListener("click", async (e) => {
    const btn = e.currentTarget, box = $("#scan-results");
    btn.disabled = true; btn.textContent = "Scanning…";
    try {
      const found = await api("POST", "/api/scan");
      const fresh = found.filter((f) => !f.known);
      box.hidden = false;
      if (!fresh.length) {
        box.innerHTML = `<h2>Scan finished</h2><p class="muted">Found ${found.length} agent${found.length === 1 ? "" : "s"}, all already on the dashboard.</p>`;
      } else {
        box.innerHTML = `<h2>Found ${fresh.length} new Pi${fresh.length === 1 ? "" : "s"}</h2><ul>${fresh
          .map((f) => `<li><strong>${esc(f.hostname)}</strong><span class="muted">${esc(f.host)}</span><button class="small" data-host="${esc(f.host)}">Add</button></li>`)
          .join("")}</ul>`;
        box.querySelectorAll("button[data-host]").forEach((b) =>
          b.addEventListener("click", async () => {
            b.disabled = true;
            try { await addPi(b.dataset.host); b.closest("li").remove(); } catch (err) { toast(err.message); b.disabled = false; }
          }));
      }
    } catch (err) { toast(`Scan failed: ${err.message}`); }
    btn.disabled = false; btn.textContent = "Scan network";
  });
  refreshOverview();
  setInterval(refreshOverview, REFRESH_MS);
}

// ---------- detail ----------
let charts = {};
let currentRange = "1h";

function cssVar(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }

function makeChart(id, datasets, opts = {}) {
  const ctx = document.getElementById(id);
  const ink = cssVar("--muted"), line = cssVar("--line");
  return new Chart(ctx, {
    type: "line",
    data: { labels: [], datasets: datasets.map((d) => ({ borderWidth: 1.5, pointRadius: (c) => (c.chart.data.labels.length < 3 ? 3 : 0), tension: 0.2, spanGaps: false, ...d })) },
    options: {
      animation: false, responsive: true, maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      plugins: { legend: { display: datasets.length > 1, labels: { color: ink, boxWidth: 12 } } },
      scales: {
        x: { ticks: { color: ink, maxTicksLimit: 6, maxRotation: 0 }, grid: { color: line } },
        y: { ticks: { color: ink, callback: opts.tick }, grid: { color: line }, min: opts.min, max: opts.max },
      },
    },
  });
}

function initCharts() {
  const accent = cssVar("--accent"), bad = cssVar("--bad"), ok = cssVar("--ok");
  charts.temp = makeChart("c-temp", [{ label: "°C", borderColor: bad }]);
  charts.cpu = makeChart("c-cpu", [{ label: "CPU", borderColor: accent }], { min: 0, max: 100 });
  charts.mem = makeChart("c-mem", [{ label: "RAM", borderColor: accent }], { min: 0, max: 100 });
  charts.disk = makeChart("c-disk", [{ label: "Disk", borderColor: accent }], { min: 0, max: 100 });
  charts.net = makeChart("c-net", [{ label: "Download", borderColor: accent }, { label: "Upload", borderColor: ok }], { min: 0, tick: (v) => rate(v) });
}

const RANGE_S = { "1h": 3600, "6h": 21600, "24h": 86400, "7d": 604800 };

function timeLabel(ts, range) {
  const d = new Date(ts * 1000);
  return range === "7d" ? d.toLocaleString([], { weekday: "short", hour: "2-digit", minute: "2-digit" })
                        : d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

async function loadHistory(id) {
  let h;
  try { h = await api("GET", `/api/pis/${encodeURIComponent(id)}/history?range=${currentRange}`); }
  catch (e) { $("#since").textContent = `History unavailable: ${e.message}`; return; }
  const labels = h.ts.map((t) => timeLabel(t, currentRange));
  const set = (c, ...series) => { c.data.labels = labels; series.forEach((s, i) => (c.data.datasets[i].data = s)); c.update(); };
  set(charts.temp, h.temp_c);
  set(charts.cpu, h.cpu_pct);
  set(charts.mem, h.mem_used.map((u, i) => (h.mem_total[i] ? (u / h.mem_total[i]) * 100 : null)));
  set(charts.disk, h.disk_pct);
  set(charts.net, h.net_rx_bps, h.net_tx_bps);
  if (h.since == null) {
    $("#since").textContent = "No readings yet.";
  } else {
    const restarted = h.since > Date.now() / 1000 - RANGE_S[currentRange];
    $("#since").textContent = `History since ${new Date(h.since * 1000).toLocaleString()}` +
      (restarted ? " (the Pi rebooted or its agent restarted, so earlier readings are gone)" : "");
  }
}

function renderLive(p) {
  const dot = $("#status-dot");
  dot.className = `dot ${p.status}`;
  dot.title = STATUS_TEXT[p.status];
  const s = p.live && p.live.sample;
  const rows = s ? [
    ["Status", STATUS_TEXT[p.status]], ["Temperature", temp(s.temp_c)], ["CPU", `${pct(s.cpu_pct)} (load ${s.load1.toFixed(2)})`],
    ["RAM", `${bytes(s.mem_used)} of ${bytes(s.mem_total)}`], ["Swap", bytes(s.swap_used)], ["Disk", pct(s.disk_pct)],
    ["Network", `↓ ${rate(s.net_rx_bps)}  ↑ ${rate(s.net_tx_bps)}`], ["Uptime", duration(p.live.uptime_s)],
    ["Throttling", s.throttled == null ? "Unknown" : s.throttled ? `Yes (0x${s.throttled.toString(16)})` : "None"],
  ] : [["Status", STATUS_TEXT[p.status]], ["Last seen", ago(p.last_seen)]];
  $("#live").innerHTML = rows.map(([k, v]) => `<dt>${k}</dt><dd>${esc(v)}</dd>`).join("") +
    (p.warnings.length ? `<dt>Warnings</dt><dd>${p.warnings.map((w) => WARN_TEXT[w] || esc(w)).join(", ")}</dd>` : "");

  const services = (p.live && p.live.services) || [];
  $("#no-services").hidden = !(p.live && services.length === 0);
  $("#services tbody").innerHTML = services.map((sv) => `<tr><td>${esc(sv.name)}</td><td class="state-${esc(sv.state)}">${esc(sv.state)}</td>
    <td class="actions">${["start", "stop", "restart"].map((a) => `<button class="small" data-svc="${esc(sv.name)}" data-action="${a}">${a[0].toUpperCase() + a.slice(1)}</button>`).join(" ")}</td></tr>`).join("");

  const top = (p.live && p.live.top) || [];
  $("#top tbody").innerHTML = top.map((t) => `<tr><td>${esc(t.name)} <span class="muted">${t.pid}</span></td><td class="num">${t.cpu_pct.toFixed(1)}%</td><td class="num">${t.mem_pct.toFixed(1)}%</td></tr>`).join("");
}

function renderSpec(spec) {
  if (!spec) return;
  const ifaces = (spec.interfaces || []).map((i) => `${i.name}: ${i.ipv4 || "no IPv4"}${i.mac ? ` (${i.mac})` : ""}`).join("\n");
  const rows = [
    ["Model", spec.model], ["CPU", `${spec.cpu} (${spec.cores} cores)`], ["RAM", bytes(spec.ram_total)],
    ["Storage", `${spec.root_device || ""} ${bytes(spec.root_size)}`], ["OS", spec.os], ["Kernel", `${spec.kernel} (${spec.arch})`],
    ["Serial", spec.serial], ["Network", ifaces], ["Booted", spec.boot_time ? new Date(spec.boot_time * 1000).toLocaleString() : ""],
    ["Agent", spec.agent_version],
  ];
  $("#spec tbody").innerHTML = rows.filter(([, v]) => v).map(([k, v]) => `<tr><th>${k}</th><td style="white-space:pre-line">${esc(v)}</td></tr>`).join("");
}

function detail(id, spec) {
  renderSpec(spec);
  initCharts();
  const refresh = async () => {
    try {
      const pis = await api("GET", "/api/pis");
      const p = pis.find((x) => x.id === id);
      if (p) { renderLive(p); if (p.spec) renderSpec(p.spec); }
    } catch (e) { toast(`Couldn't refresh: ${e.message}`); }
  };
  $("#services").addEventListener("click", async (e) => {
    const b = e.target.closest("button[data-svc]");
    if (!b) return;
    const { svc, action } = b.dataset;
    b.disabled = true;
    try {
      const r = await api("POST", `/api/pis/${encodeURIComponent(id)}/services/${encodeURIComponent(svc)}/${action}`);
      toast(r.ok ? `${svc}: ${action} done` : `${svc}: ${action} failed. ${r.output}`);
    } catch (err) { toast(`${svc}: ${err.message}`); }
    b.disabled = false;
    refresh();
  });
  $("#reboot").addEventListener("click", async () => {
    const name = document.querySelector(".pi-title").textContent.trim();
    if (!confirm(`Reboot ${name} now? It will be offline for about a minute and its history will restart.`)) return;
    try { await api("POST", `/api/pis/${encodeURIComponent(id)}/reboot`); toast(`Rebooting ${name}`); }
    catch (err) { toast(`Reboot failed: ${err.message}`); }
  });
  document.querySelectorAll(".seg button").forEach((b) => b.addEventListener("click", () => {
    document.querySelectorAll(".seg button").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
    currentRange = b.dataset.range;
    loadHistory(id);
  }));
  refresh();
  loadHistory(id);
  setInterval(refresh, REFRESH_MS);
  setInterval(() => loadHistory(id), 30000);
}
