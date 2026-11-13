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
  if (typeof n !== "number" || !Number.isFinite(n)) return "–";
  const u = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return `${n.toFixed(n >= 10 || i === 0 ? 0 : 1)} ${u[i]}`;
}
const rate = (n) => (n == null ? "–" : `${bytes(n)}/s`);
const pct = (n) => (n == null ? "–" : `${Math.round(n)}%`);
// Agent data is untrusted: never call number methods on something that might not be a number.
const fixed = (v, d) => (typeof v === "number" && Number.isFinite(v) ? v.toFixed(d) : "–");
const temp = (n) => (n == null ? "–" : `${fixed(n, 1)}°`);
const watts = (n) => (typeof n === "number" ? `${fixed(n, n < 10 ? 2 : 1)} W` : "–");
const POWER_NOTE = "Board power from the Pi's power chip (PMIC): its measured supply rails. Things fed straight from 5 V (USB devices, NVMe, fan) aren't included, so a wall meter reads more.";

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

// Card edge colour: blue when cool, amber when warm, red when hot. Never green.
// Card edge colour: green when cool, amber when warm, red when hot (hue blended between stops).
const HEAT_STOPS = [[45, 135], [58, 40], [70, 0]]; // [°C, hue]
function heat(t) {
  if (typeof t !== "number") return null;
  let hue = HEAT_STOPS[0][1];
  if (t >= HEAT_STOPS.at(-1)[0]) hue = HEAT_STOPS.at(-1)[1];
  else for (let i = 1; i < HEAT_STOPS.length; i++) {
    const [t0, h0] = HEAT_STOPS[i - 1], [t1, h1] = HEAT_STOPS[i];
    if (t > t0 && t <= t1) hue = h0 + ((h1 - h0) * (t - t0)) / (t1 - t0);
  }
  return `hsl(${Math.round(hue)} 62% 45%)`;
}

const memPct = (s) => (s && s.mem_total ? (s.mem_used / s.mem_total) * 100 : null);
const level = (v) => (v == null ? "" : v >= 90 ? "crit" : v >= 75 ? "high" : "");

// Pi RAM is sold in power-of-two sizes; the kernel reports a bit less (e.g. 7.6 GiB on an 8 GB board).
function ramBytes(n) {
  return n ? 2 ** Math.ceil(Math.log2(n / 2 ** 20)) * 2 ** 20 : null;
}

function ramSize(n) {
  if (!n) return null;
  const mib = ramBytes(n) / 2 ** 20;
  return mib >= 1024 ? `${mib / 1024} GB` : `${mib} MB`;
}

// "2.1 of 8 GB": both numbers in the total's unit.
function usedOf(used, total) {
  if (used == null || !total) return "";
  const unit = total >= 2 ** 30 ? [2 ** 30, "GB"] : [2 ** 20, "MB"];
  const fmt = (v) => (v < 10 ? String(Number(v.toFixed(1))) : String(Math.round(v)));
  return `${fmt(used / unit[0])} of ${fmt(total / unit[0])} ${unit[1]}`;
}

// "Debian GNU/Linux 13 (trixie)" -> "Debian 13", "Ubuntu 24.04.5 LTS" -> "Ubuntu 24.04".
function osShort(os) {
  if (typeof os !== "string" || !os) return "";
  const m = os.match(/^(Debian|Raspbian) GNU\/Linux (\d+)/) || os.match(/^(Ubuntu) (\d+\.\d+)/);
  return m ? `${m[1]} ${m[2]}` : os;
}

// "SD", "eMMC", "NVMe", "USB", "SATA" from the agent; older agents don't say, so guess NVMe
// from the device name and otherwise just say "disk".
function storageKind(spec) {
  if (typeof spec.storage === "string" && spec.storage) return spec.storage;
  return /nvme/.test(spec.root_device || "") ? "NVMe" : "disk";
}

// The names people use: "Raspberry Pi Compute Module 4 Rev 1.1" -> "CM4",
// "Raspberry Pi 3 Model B Rev 1.2" -> "Pi 3B", "Raspberry Pi 5 Model B Rev 1.1" -> "Pi 5".
function modelShort(model) {
  if (typeof model !== "string") return "";
  let m = model.replace(/^Raspberry /, "").replace(/ Rev [\d.]+$/, "");
  m = m.replace(/^Pi Compute Module (\d+)(.*)$/, "CM$1$2").replace(/^(CM\d+) Plus$/, "$1+");
  m = m.replace(/^Pi 5 Model B$/, "Pi 5");                        // the Pi 5 only comes as a B
  m = m.replace(/ Model ([AB]) Plus$/, "$1+").replace(/ Model ([AB])$/, "$1");
  return m;
}

// The card's single subtitle: model, OS, cores and disk type (sizes are in the bars below).
function cardSubtitle(spec, host) {
  const parts = [modelShort(spec.model) || host];
  if (spec.os) parts.push(osShort(spec.os));
  if (spec.cores) parts.push(`${spec.cores} core${spec.cores === 1 ? "" : "s"}`);
  if (spec.root_size && storageKind(spec) !== "disk") parts.push(storageKind(spec)); // only once known
  return parts.join(", ");
}

function hardwareLine(spec, s) {
  const parts = [];
  if (spec.os) parts.push(osShort(spec.os));
  if (spec.cores) parts.push(`${spec.cores} core${spec.cores === 1 ? "" : "s"}`);
  const ram = ramSize(spec.ram_total || (s && s.mem_total));
  if (ram) parts.push(`${ram} RAM`);
  if (spec.root_size) parts.push(`${bytes(spec.root_size)} ${storageKind(spec)}`);
  return parts.join(", ");
}

const WARN_TEXT = { hot: "Running hot", disk: "Disk nearly full", mem: "RAM nearly full", throttled: "Throttled or under-voltage",
                    agent_outdated: "Agent out of date", wifi_weak: "Weak Wi-Fi", storage_readonly: "Disk read-only",
                    storage_errors: "Disk errors", storage_worn: "Disk wearing out" };
const WARN_SHORT = { hot: "Hot", disk: "Disk", mem: "RAM", throttled: "Throttled", agent_outdated: "Old agent",
                     wifi_weak: "Weak Wi-Fi", storage_readonly: "Read-only", storage_errors: "Disk errors", storage_worn: "Worn" };
const WARN_LEVEL = (w) => (["hot", "throttled", "storage_readonly", "storage_errors"].includes(w) ? "bad" : "warn");
const STATUS_TEXT = { online: "Online", offline: "Offline", auth_failed: "Token rejected" };

// ---------- overview ----------
function bar(label, v, amount = "") {
  const w = v == null ? 0 : Math.min(100, v);
  return `<span>${label}</span><span class="bar"><i class="${level(v)}" style="width:${w}%"></i></span>` +
    `<span class="of">${amount}</span><span class="num">${pct(v)}</span>`;
}

function card(p) {
  const s = p.live && p.live.sample;
  const spec = p.spec || {};
  const el = document.createElement("article");
  el.className = `card ${p.status}`;
  el.dataset.id = p.id;
  el.draggable = true;
  const h = s && heat(s.temp_c);
  if (h) el.style.setProperty("--heat", h);
  let body;
  if (s) {
    body = `<div class="bars">${bar("CPU", s.cpu_pct)}${bar("RAM", memPct(s), usedOf(s.mem_used, ramBytes(spec.ram_total || s.mem_total)))}${bar("Disk", s.disk_pct, usedOf(spec.root_size && (s.disk_pct / 100) * spec.root_size, spec.root_size))}</div>
      <div class="meta"><span>↓ ${rate(s.net_rx_bps)}</span><span>↑ ${rate(s.net_tx_bps)}</span><span>up ${duration(p.live.uptime_s)}</span>${typeof s.power_w === "number" ? `<span title="${POWER_NOTE}">${watts(s.power_w)}</span>` : ""}</div>`;
  } else {
    body = `<div class="meta"><span>${esc(spec.os || "")}</span><span>last seen ${ago(p.last_seen)}</span></div>`;
  }
  // Cards skip "Disk read-only"; it shows on the Pi's Storage health panel instead.
  const chips = p.warnings.filter((w) => w !== "storage_readonly").map((w) => `<span class="chip ${WARN_LEVEL(w)}">${WARN_TEXT[w] || esc(w)}</span>`);
  const stressing = p.live && p.live.stress ? Object.entries(p.live.stress).filter(([, v]) => v).map(([k]) => esc(k).toUpperCase()) : [];
  if (stressing.length) chips.unshift(`<span class="chip warn">Stress running: ${stressing.join(", ")}</span>`);
  if (p.status !== "online") chips.unshift(`<span class="chip ${p.status === "auth_failed" ? "bad" : "off"}">${STATUS_TEXT[p.status]}</span>`);
  el.innerHTML = `
    <div class="card-head"><button class="grip" aria-label="Move ${esc(p.hostname)} (drag, or use arrow keys)" title="Drag to reorder">⠿</button><span class="dot ${p.status}"></span><h2><a class="stretch" href="/pi/${encodeURIComponent(p.id)}" draggable="false">${esc(p.hostname)}</a></h2>
      <span class="temp">${s ? temp(s.temp_c) : ""}</span>
      ${pick(p)}</div>
    <div class="model" title="${esc([spec.model, hardwareLine(spec, s)].filter(Boolean).join(" – "))}">${esc(cardSubtitle(spec, p.host))}</div>
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

// ---------- selection for actions on several Pis ----------
const selected = new Set();
const pick = (p) => `<input type="checkbox" class="pick" data-id="${esc(p.id)}" aria-label="Select ${esc(p.hostname)}" ${selected.has(p.id) ? "checked" : ""}>`;

function updateBulkBar() {
  const known = new Set((lastPis || []).map((p) => p.id));
  for (const id of [...selected]) if (!known.has(id)) selected.delete(id);
  const n = selected.size;
  $("#bulk-bar").hidden = n === 0;
  $("#bulk-count").textContent = `${n} selected`;
  const all = $("#select-all");
  all.checked = n > 0 && n === known.size;
  all.indeterminate = n > 0 && n < known.size;
}

function selectedNames() {
  return (lastPis || []).filter((p) => selected.has(p.id)).map((p) => p.hostname);
}

async function runBulk(kind) {
  const ids = [...selected], names = selectedNames();
  if (kind === "clear") { selected.clear(); renderPis(lastPis || []); return; }
  if (kind === "bench") {
    try {
      const res = await api("POST", "/api/bench", { ids, tests: ["cpu", "disk"] });
      const failed = Object.entries(res).filter(([, r]) => r.error);
      if (failed.length) toast(`Couldn't start on ${failed.length}: ${failed[0][1].error}`);
    } catch (e) { toast(`Couldn't start benchmarks: ${e.message}`); return; }
    location.href = "/bench";
    return;
  }
  const verb = kind === "reboot" ? "Reboot" : "Shut down";
  const warn = kind === "reboot" ? "They'll be offline for about a minute and their history restarts."
    : "They stay off until someone reconnects their power; the dashboard can't turn them back on.";
  if (!confirm(`${verb} ${names.length} Pi${names.length === 1 ? "" : "s"}: ${names.join(", ")}?\n\n${warn}`)) return;
  let res;
  try { res = await api("POST", "/api/actions", { ids, action: kind }); } catch (e) { toast(`${verb} failed: ${e.message}`); return; }
  const name = Object.fromEntries((lastPis || []).map((p) => [p.id, p.hostname]));
  const ok = Object.entries(res).filter(([, r]) => r.ok).map(([id]) => name[id] || id);
  const bad = Object.entries(res).filter(([, r]) => r.error).map(([id, r]) => `${name[id] || id}: ${r.error}`);
  toast(`${verb}: ${ok.length ? ok.join(", ") : "none"}${bad.length ? `. Failed: ${bad.join("; ")}` : ""}`);
  selected.clear();
  refreshOverview();
}

// ---------- list view ----------
let layout = "cards";
try {
  const saved = localStorage.getItem("overviewLayout");
  layout = ["cards", "list", "table"].includes(saved) ? saved : "cards";
} catch { /* private mode */ }

function usage(label, v, amount) {
  return `<span class="cell usage"><span class="lbl">${label}</span>` +
    `<span class="bar"><i class="${level(v)}" style="width:${v == null ? 0 : Math.min(100, v)}%"></i></span>` +
    `<span class="val">${amount ? `<span class="of">${amount}</span> ` : ""}${pct(v)}</span></span>`;
}

function listRow(p) {
  const s = p.live && p.live.sample;
  const spec = p.spec || {};
  const el = document.createElement("article");
  el.className = `card row ${p.status}`;
  el.dataset.id = p.id;
  el.draggable = true;
  const h = s && heat(s.temp_c);
  if (h) el.style.setProperty("--heat", h);
  const chips = p.warnings.map((w) => `<span class="chip ${WARN_LEVEL(w)}">${WARN_TEXT[w] || esc(w)}</span>`);
  const stressing = p.live && p.live.stress ? Object.entries(p.live.stress).filter(([, v]) => v).map(([k]) => esc(k).toUpperCase()) : [];
  if (stressing.length) chips.unshift(`<span class="chip warn">Stress: ${stressing.join(", ")}</span>`);
  const status = p.status === "online" ? (chips.length ? chips.join("") : `<span class="muted">OK</span>`)
    : `<span class="chip ${p.status === "auth_failed" ? "bad" : "off"}">${STATUS_TEXT[p.status]}</span> <span class="muted">seen ${ago(p.last_seen)}</span>`;
  el.innerHTML = `
    <span class="cell pickcell"><button class="grip" aria-label="Move ${esc(p.hostname)} (drag, or use arrow keys)" title="Drag to reorder">⠿</button>${pick(p)}</span>
    <span class="cell name"><span class="dot ${p.status}"></span><a class="stretch" href="/pi/${encodeURIComponent(p.id)}" draggable="false">${esc(p.hostname)}</a>
      <span class="sub">${esc(spec.model || p.host)}<br>${esc(hardwareLine(spec, s))}</span></span>
    <span class="cell temp num">${s ? temp(s.temp_c) : "–"}</span>
    ${s ? usage("CPU", s.cpu_pct, "") : `<span class="cell"></span>`}
    ${s ? usage("RAM", memPct(s), usedOf(s.mem_used, ramBytes(spec.ram_total || s.mem_total))) : `<span class="cell"></span>`}
    ${s ? usage("Disk", s.disk_pct, usedOf(spec.root_size && (s.disk_pct / 100) * spec.root_size, spec.root_size)) : `<span class="cell"></span>`}
    <span class="cell net">${s ? `↓ ${rate(s.net_rx_bps)}<br>↑ ${rate(s.net_tx_bps)}` : ""}</span>
    <span class="cell uptime">${s ? duration(p.live.uptime_s) : ""}</span>
    <span class="cell status">${status}</span>
    <button class="small remove" title="Remove from this dashboard">Remove</button>`;
  el.querySelector(".remove").addEventListener("click", async (e) => {
    e.preventDefault();
    if (!confirm(`Remove ${p.hostname} from the dashboard? The Pi keeps running its agent.`)) return;
    await api("DELETE", `/api/pis/${encodeURIComponent(p.id)}`);
    toast(`Removed ${p.hostname}`);
    refreshOverview();
  });
  return el;
}

// ---------- table view: sortable, numbers only ----------
const TABLE_COLS = [
  // [key, heading, value(p) for sorting, cell html(p), numeric]
  ["name", "Pi", (p) => p.hostname, (p) => `<a href="/pi/${encodeURIComponent(p.id)}">${esc(p.hostname)}</a>`, false],
  ["model", "Model", (p) => (p.spec || {}).model || "", (p) => {
    const m = (p.spec || {}).model || "";
    return `<span title="${esc(m)}">${esc(modelShort(m))}</span>`;
  }, false],
  ["os", "OS", (p) => osShort((p.spec || {}).os), (p) => {
    const os = (p.spec || {}).os || "";
    return os ? `<span title="${esc(os)}">${esc(osShort(os))}</span>` : "–";
  }, false],
  ["status", "Status", (p) => p.status, (p) => `<span class="dot ${p.status}" title="${STATUS_TEXT[p.status]}"></span>${p.status === "online" ? "" : STATUS_TEXT[p.status]}`, false],
  ["temp", "Temp °C", (p) => smp(p).temp_c, (p) => {
    const t = smp(p).temp_c, h = heat(t);
    return h ? `<span class="swatch" style="background:${h}"></span>${fixed(t, 1)}` : "–";
  }, true],
  ["cpu", "CPU %", (p) => smp(p).cpu_pct, (p) => fixed(smp(p).cpu_pct, 0), true],
  ["load", "Load", (p) => smp(p).load1, (p) => fixed(smp(p).load1, 2), true],
  ["ram", "RAM", (p) => smp(p).mem_used, (p) => {
    const total = ramBytes((p.spec || {}).ram_total || smp(p).mem_total);
    return typeof smp(p).mem_used === "number" ? usedOf(smp(p).mem_used, total) : (total ? `<span class="of">${ramSize(total)}</span>` : "–");
  }, true],
  ["rampct", "RAM %", (p) => memPct(smp(p)), (p) => fixed(memPct(smp(p)), 0), true],
  ["disk", "Disk", (p) => diskUsed(p), (p) => {
    const size = (p.spec || {}).root_size;
    return diskUsed(p) != null ? usedOf(diskUsed(p), size) : (size ? `<span class="of">${bytes(size)}</span>` : "–");
  }, true],
  ["diskpct", "Disk %", (p) => smp(p).disk_pct, (p) => fixed(smp(p).disk_pct, 0), true],
  ["net", "Network", (p) => smp(p).net_rx_bps, (p) => (p.live ? `↓ ${rate(smp(p).net_rx_bps)} ↑ ${rate(smp(p).net_tx_bps)}` : "–"), true],
  ["power", "Power", (p) => smp(p).power_w, (p) => (typeof smp(p).power_w === "number" ? `<span title="${POWER_NOTE}">${watts(smp(p).power_w)}</span>` : "–"), true],
  ["fan", "Fan rpm", (p) => smp(p).fan_rpm, (p) => (typeof smp(p).fan_rpm === "number" ? String(smp(p).fan_rpm) : "–"), true],
  ["uptime", "Uptime", (p) => p.live && p.live.uptime_s, (p) => (p.live ? duration(p.live.uptime_s) : "–"), true],
  ["warnings", "Warnings", (p) => p.warnings.length,
   (p) => p.warnings.map((w) => `<span class="chip ${WARN_LEVEL(w)}" title="${esc(WARN_TEXT[w] || w)}">${WARN_SHORT[w] || esc(w)}</span>`).join(" "), false],
];
const smp = (p) => (p.live && p.live.sample) || {};
const diskUsed = (p) => {
  const s = smp(p), size = (p.spec || {}).root_size;
  return typeof s.disk_pct === "number" && size ? (s.disk_pct / 100) * size : null;
};

let tableSort = { key: null, dir: 1 }; // key null = the saved (drag) order
try { tableSort = JSON.parse(localStorage.getItem("overviewTableSort")) || tableSort; } catch { /* fine */ }

function pisTable(pis) {
  const col = TABLE_COLS.find((c) => c[0] === tableSort.key);
  const rows = col ? [...pis].sort((a, b) => {
    const x = col[2](a), y = col[2](b);
    if (x == null || x === "") return 1; // blanks last either way
    if (y == null || y === "") return -1;
    return (typeof x === "number" ? x - y : String(x).localeCompare(String(y))) * tableSort.dir;
  }) : pis;
  const wrap = document.createElement("div");
  wrap.className = "table-scroll";
  wrap.innerHTML = `<table class="pis-table"><thead><tr><th class="pickcol"><span class="sr">Select</span></th>${TABLE_COLS.map(([k, label, , , num]) => {
    const sorted = tableSort.key === k ? (tableSort.dir > 0 ? "ascending" : "descending") : "none";
    const arrow = sorted === "ascending" ? " ▲" : sorted === "descending" ? " ▼" : "";
    return `<th scope="col" class="${num ? "num" : ""}" aria-sort="${sorted}"><button class="sort" data-key="${k}">${label}${arrow}</button></th>`;
  }).join("")}</tr></thead><tbody>${rows.map((p) => `<tr class="${p.status}"><td class="pickcol">${pick(p)}</td>${TABLE_COLS.map(([k, , , cell, num], i) =>
    i === 0 ? `<th scope="row">${cell(p)}</th>` : `<td class="${num ? "num" : ""}">${cell(p)}</td>`).join("")}</tr>`).join("")}</tbody></table>`;
  wrap.querySelectorAll("button.sort").forEach((b) => b.addEventListener("click", () => {
    const k = b.dataset.key;
    // first click sorts (numbers biggest first), second reverses, third returns to the saved order
    if (tableSort.key !== k) tableSort = { key: k, dir: TABLE_COLS.find((c) => c[0] === k)[4] ? -1 : 1 };
    else if (tableSort.dir === (TABLE_COLS.find((c) => c[0] === k)[4] ? -1 : 1)) tableSort.dir *= -1;
    else tableSort = { key: null, dir: 1 };
    try { localStorage.setItem("overviewTableSort", JSON.stringify(tableSort)); } catch { /* fine */ }
    if (lastPis) renderPis(lastPis);
    document.querySelector(`#pis button.sort[data-key="${k}"]`)?.focus();
  }));
  return wrap;
}

function applyLayout() {
  $("#pis").classList.toggle("list", layout === "list");
  $("#pis").classList.toggle("grid", layout === "cards");
  $("#pis").classList.toggle("tableview", layout === "table");
  $("#list-head").hidden = layout !== "list";
  document.querySelectorAll(".view-bar .seg button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.layout === layout)));
}

// ---------- drag to reorder ----------
let dragging = null;

// Pis that aren't online sit after the online ones; each group keeps the saved order.
const isUp = (p) => p.status === "online";
const displayOrder = (pis) => [...pis.filter(isUp), ...pis.filter((p) => !isUp(p))];

// The saved order with each group's slots refilled in the order the cards now show, so moving
// cards never writes "offline last" into it: a Pi that comes back returns to its old place.
function mergedOrder(saved, shownIds) {
  const up = new Set(saved.filter(isUp).map((p) => p.id));
  const queue = { true: shownIds.filter((id) => up.has(id)), false: shownIds.filter((id) => !up.has(id)) };
  return saved.map((p) => queue[up.has(p.id)].shift() ?? p.id);
}

async function saveOrder() {
  const shown = [...document.querySelectorAll("#pis .card")].map((c) => c.dataset.id);
  const ids = lastPis ? mergedOrder(lastPis, shown) : shown;
  try { await api("PUT", "/api/order", { ids }); } catch (e) { toast(`Couldn't save the order: ${e.message}`); }
}

function cardAfterPointer(grid, x, y) {
  // The card whose centre comes after the pointer in reading order (row by row).
  for (const c of grid.querySelectorAll(".card:not(.dragging)")) {
    const r = c.getBoundingClientRect();
    if (y < r.top) return c;
    if (y <= r.bottom && x < r.left + r.width / 2) return c;
  }
  return null;
}

function setupReorder() {
  const grid = $("#pis");
  grid.addEventListener("dragstart", (e) => {
    const c = e.target.closest(".card");
    if (!c) return;
    dragging = c;
    c.classList.add("dragging");
    e.dataTransfer.effectAllowed = "move";
    e.dataTransfer.setData("text/plain", c.dataset.id);
  });
  grid.addEventListener("dragover", (e) => {
    if (!dragging) return;
    e.preventDefault();
    const after = cardAfterPointer(grid, e.clientX, e.clientY);
    if (after !== dragging.nextElementSibling) grid.insertBefore(dragging, after);
  });
  grid.addEventListener("drop", (e) => e.preventDefault());
  grid.addEventListener("dragend", () => {
    if (!dragging) return;
    dragging.classList.remove("dragging");
    dragging = null;
    saveOrder();
  });
  grid.addEventListener("keydown", (e) => {
    const grip = e.target.closest(".grip");
    if (!grip) return;
    const c = grip.closest(".card");
    const back = e.key === "ArrowLeft" || e.key === "ArrowUp";
    const fwd = e.key === "ArrowRight" || e.key === "ArrowDown";
    if (!back && !fwd) return;
    e.preventDefault();
    if (back && c.previousElementSibling) grid.insertBefore(c, c.previousElementSibling);
    else if (fwd && c.nextElementSibling) grid.insertBefore(c.nextElementSibling, c);
    else return;
    grip.focus();
    saveOrder();
  });
}

let lastPis = null; // the latest /api/pis answer, so switching view or sorting redraws instantly

async function refreshOverview() {
  if (dragging) return; // don't rebuild cards under the pointer
  let pis;
  try { pis = await api("GET", "/api/pis"); } catch (e) { toast(`Couldn't load Pis: ${e.message}`); return; }
  if (dragging) return;
  lastPis = pis;
  renderPis(pis);
}

function renderPis(pis) {
  const grid = $("#pis");
  const focusedGrip = document.activeElement && document.activeElement.closest(".grip") &&
    document.activeElement.closest(".card").dataset.id;
  const shown = displayOrder(pis);
  if (layout === "table") grid.replaceChildren(pisTable(shown));
  else grid.replaceChildren(...shown.map(layout === "list" ? listRow : card));
  updateBulkBar();
  if (focusedGrip) grid.querySelector(`.card[data-id="${CSS.escape(focusedGrip)}"] .grip`)?.focus();
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
  setupReorder();
  applyLayout();
  $("#pis").addEventListener("change", (e) => {
    const box = e.target.closest("input.pick");
    if (!box) return;
    box.checked ? selected.add(box.dataset.id) : selected.delete(box.dataset.id);
    updateBulkBar();
  });
  $("#pis").addEventListener("click", (e) => { if (e.target.closest("input.pick")) e.stopPropagation(); });
  $("#select-all").addEventListener("change", (e) => {
    selected.clear();
    if (e.target.checked) for (const p of lastPis || []) selected.add(p.id);
    renderPis(lastPis || []);
  });
  document.querySelectorAll("#bulk-bar button").forEach((b) => b.addEventListener("click", () => runBulk(b.dataset.bulk)));
  document.querySelectorAll(".view-bar .seg button").forEach((b) => b.addEventListener("click", () => {
    layout = b.dataset.layout;
    try { localStorage.setItem("overviewLayout", layout); } catch { /* fine */ }
    applyLayout();
    if (lastPis) renderPis(lastPis);
    else refreshOverview();
  }));
  refreshOverview();
  setInterval(refreshOverview, REFRESH_MS);
}

// ---------- detail ----------
function signalWord(dbm) {
  return dbm >= -55 ? "excellent" : dbm >= -65 ? "good" : dbm >= -70 ? "fair" : "weak";
}

function linkText(link) {
  if (!link || !link.interface) return "No default route";
  if (link.kind === "Wi-Fi") {
    return typeof link.signal_dbm === "number"
      ? `Wi-Fi (${link.interface}), ${link.signal_dbm} dBm, ${signalWord(link.signal_dbm)}` : `Wi-Fi (${link.interface})`;
  }
  return `${link.kind || "Wired"} (${link.interface})${link.speed_mbps ? `, ${link.speed_mbps} Mbit/s` : ""}`;
}

function renderStorage(h) {
  if (!h) return;
  const rows = [["Device", h.device || "–"],
    ["Mounted", h.read_only == null ? "–" : h.read_only ? "Read-only (the system can't save changes)" : "Read-write"],
    ["Kernel I/O errors", h.io_errors == null ? "–" : h.io_errors === 0 ? "None this boot" : `${h.io_errors} this boot`]];
  if (h.emmc_life_used_pct) rows.push(["eMMC wear", `${h.emmc_life_used_pct[0]}–${h.emmc_life_used_pct[1]}% of its life used (${h.emmc_pre_eol || "?"})`]);
  if (h.nvme) {
    const n = h.nvme;
    rows.push(["NVMe wear", `${n.percent_used}% used, ${n.available_spare}% spare left`],
              ["NVMe health", n.critical_warning ? `Warning flags 0x${n.critical_warning.toString(16)}` : "No warnings"],
              ["NVMe", `${n.temperature_c}°C, ${n.media_errors} media errors, ${fixed(n.written_gb, 0)} GB written, ${n.power_on_hours} h on`]);
  }
  if (!h.emmc_life_used_pct && !h.nvme) rows.push(["Wear", "SD cards don't report wear; watch for errors and read-only"]);
  $("#storage tbody").innerHTML = rows.map(([k, v]) => `<tr><th>${k}</th><td>${esc(v)}</td></tr>`).join("");
}

function renderPeripherals(p) {
  if (!p) return;
  const list = (items) => (items.length ? items.map(esc).join("<br>") : `<span class="muted">None</span>`);
  const displays = p.displays.map((d) => `${d.name}: ${d.connected ? "connected" : "not connected"}`);
  const usb = p.usb.map((u) => (u.manufacturer ? `${u.product} (${u.manufacturer})` : u.product));
  const i2c = p.i2c.map((d) => `${d.name} at ${d.address}`);
  $("#peripherals tbody").innerHTML = [["Displays", list(displays)], ["Cameras", list(p.cameras)],
    ["USB", list(usb)], ["I²C (kernel drivers)", list(i2c)]].map(([k, v]) => `<tr><th>${k}</th><td>${v}</td></tr>`).join("");
}

const EVENT_TEXT = (e) => {
  if (e.kind === "offline") return e.status === "auth_failed" ? "Stopped accepting the token" : "Went offline";
  if (e.kind === "online") return `Back online after ${duration(e.down_s)} (${e.cause === "reboot" ? "rebooted" : "network dropout"})`;
  if (e.kind === "action") return `${e.what[0].toUpperCase()}${e.what.slice(1)} from the dashboard${e.ok ? "" : ` failed: ${e.error || ""}`}`;
  return e.kind;
};

function eventRows(evs, withPi, names) {
  return evs.map((e) => `<tr class="ev-${esc(e.kind)}${e.cause ? ` ev-${esc(e.cause)}` : ""}"><td class="when">${esc(when(e.at))}</td>` +
    (withPi ? `<td>${esc(names[e.pi] || e.pi)}</td>` : "") + `<td>${esc(EVENT_TEXT(e))}</td></tr>`).join("");
}
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
  charts.wifi = makeChart("c-wifi", [{ label: "dBm", borderColor: cssVar("--accent") }], { max: -20 });
  charts.power = makeChart("c-power", [{ label: "W", borderColor: cssVar("--bad") }], { min: 0 });
  charts.fan = makeChart("c-fan", [{ label: "rpm", borderColor: cssVar("--warn") }], { min: 0 });
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
  const wifi = h.wifi_dbm || [];
  set(charts.wifi, wifi);
  $("#wifi-fig").hidden = !wifi.some((v) => v != null);
  const power = h.power_w || [];
  const hasPower = power.some((v) => v != null);
  set(charts.power, power);
  $("#power-fig").hidden = !hasPower;
  const fan = h.fan_rpm || [];
  const hasFan = fan.some((v) => v != null);
  set(charts.fan, fan);
  $("#c-fan").hidden = !hasFan && h.ts.length > 0;
  $("#no-fan").hidden = hasFan || h.ts.length === 0;
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
    ["Status", STATUS_TEXT[p.status]], ["Temperature", temp(s.temp_c)], ["CPU", `${pct(s.cpu_pct)} (load ${fixed(s.load1, 2)})`],
    ["RAM", `${bytes(s.mem_used)} of ${bytes(s.mem_total)}`], ["Swap", bytes(s.swap_used)], ["Disk", pct(s.disk_pct)],
    ["Link", linkText(p.live.link)],
    ["Network", `↓ ${rate(s.net_rx_bps)}  ↑ ${rate(s.net_tx_bps)}`], ["Uptime", duration(p.live.uptime_s)],
    ["Fan", s.fan_rpm == null ? "No sensor" : `${s.fan_rpm} rpm`],
    ...(typeof s.power_w === "number" ? [["Board power", watts(s.power_w)]] : []),
    ["Throttling", typeof s.throttled !== "number" ? "Unknown" : s.throttled ? `Yes (0x${s.throttled.toString(16)})` : "None"],
  ] : [["Status", STATUS_TEXT[p.status]], ["Last seen", ago(p.last_seen)]];
  $("#live").innerHTML = rows.map(([k, v]) => `<dt>${k}</dt><dd>${esc(v)}</dd>`).join("") +
    (p.warnings.length ? `<dt>Warnings</dt><dd>${p.warnings.map((w) => WARN_TEXT[w] || esc(w)).join(", ")}</dd>` : "");

  const services = (p.live && p.live.services) || [];
  $("#no-services").hidden = !(p.live && services.length === 0);
  // The agent itself goes first. Only Restart: stopping it would cut the dashboard off from this Pi.
  // Agents older than 0.2.2 don't report agent_uptime_s and can't restart themselves.
  const live = p.live;
  const canRestart = live && typeof live.agent_uptime_s === "number";
  const agentRow = live ? `<tr class="agent-row"><td>rpidash-agent <span class="muted">${esc(live.version || "")}${canRestart ? ` · up ${esc(duration(live.agent_uptime_s))}` : ""}</span></td>
    <td class="state-active">active</td>
    <td class="actions">${canRestart ? `<button class="small" data-agent-restart>Restart</button>` : ""}</td></tr>` : "";
  $("#services tbody").innerHTML = agentRow + services.map((sv) => `<tr><td>${esc(sv.name)}</td><td class="state-${esc(sv.state)}">${esc(sv.state)}</td>
    <td class="actions">${["start", "stop", "restart"].map((a) => `<button class="small" data-svc="${esc(sv.name)}" data-action="${a}">${a[0].toUpperCase() + a.slice(1)}</button>`).join(" ")}</td></tr>`).join("");

  const top = (p.live && p.live.top) || [];
  $("#top tbody").innerHTML = top.map((t) => `<tr><td>${esc(t.name)} <span class="muted">${esc(t.pid)}</span></td><td class="num">${fixed(t.cpu_pct, 1)}%</td><td class="num">${fixed(t.mem_pct, 1)}%</td></tr>`).join("");
}

function renderSpec(spec) {
  if (!spec) return;
  const ifaces = (spec.interfaces || []).map((i) => `${i.name}: ${i.ipv4 || "no IPv4"}${i.mac ? ` (${i.mac})` : ""}`).join("\n");
  const rows = [
    ["Model", spec.model], ["CPU", `${spec.cpu} (${spec.cores} cores)`], ["RAM", bytes(spec.ram_total)],
    ["Storage", `${bytes(spec.root_size)} ${storageKind(spec)} (${spec.root_device || "unknown device"})`], ["OS", spec.os], ["Kernel", `${spec.kernel} (${spec.arch})`],
    ["Serial", spec.serial], ["Network", ifaces], ["Booted", spec.boot_time ? new Date(spec.boot_time * 1000).toLocaleString() : ""],
    ["Agent", spec.agent_version],
  ];
  $("#spec tbody").innerHTML = rows.filter(([, v]) => v).map(([k, v]) => `<tr><th>${k}</th><td style="white-space:pre-line">${esc(v)}</td></tr>`).join("");
}

function detail(id, spec) {
  renderSpec(spec);
  const loadExtras = async () => {
    api("GET", `/api/pis/${encodeURIComponent(id)}/storage`).then(renderStorage).catch(() => {});
    api("GET", `/api/pis/${encodeURIComponent(id)}/peripherals`).then(renderPeripherals).catch(() => {});
    api("GET", `/api/events?pi=${encodeURIComponent(id)}&days=30`).then((r) => {
      $("#pi-events tbody").innerHTML = eventRows(r.events.slice(0, 15), false, {});
      $("#no-events").hidden = r.events.length > 0;
    }).catch(() => {});
  };
  loadExtras();
  setInterval(loadExtras, 60000);
  initCharts();
  const refresh = async () => {
    try {
      const pis = await api("GET", "/api/pis");
      const p = pis.find((x) => x.id === id);
      if (p) { renderLive(p); if (p.spec) renderSpec(p.spec); }
    } catch (e) { toast(`Couldn't refresh: ${e.message}`); }
  };
  $("#services").addEventListener("click", async (e) => {
    const ra = e.target.closest("button[data-agent-restart]");
    if (ra) {
      const name = document.querySelector(".pi-title").textContent.trim();
      if (!confirm(`Restart the agent on ${name}? It drops offline for a few seconds, its charts start over, and any running stress load or benchmark stops.`)) return;
      ra.disabled = true;
      try { await api("POST", `/api/pis/${encodeURIComponent(id)}/agent/restart`); toast(`Restarting the agent on ${name}`); }
      catch (err) { toast(`Agent restart failed: ${err.message}`); ra.disabled = false; }
      return;
    }
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
  $("#shutdown").addEventListener("click", async () => {
    const name = document.querySelector(".pi-title").textContent.trim();
    if (!confirm(`Shut down ${name} now? It stays off until someone unplugs and reconnects its power; the dashboard can't turn it back on.`)) return;
    try { await api("POST", `/api/pis/${encodeURIComponent(id)}/shutdown`); toast(`Shutting down ${name}`); }
    catch (err) { toast(`Shut down failed: ${err.message}`); }
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

// ---------- benchmarks & stress ----------
const BENCH_COLS = [
  ["cpu", "int_1", 0], ["cpu", "int_all", 0], ["cpu", "fp_1", 0], ["cpu", "fp_all", 0],
  ["mem", "mem_1", 0], ["mem", "mem_all", 0],
  ["disk", "seq_write_MBps", 1], ["disk", "seq_read_MBps", 1], ["disk", "rand4k_read_iops", 0], ["disk", "rand4k_write_iops", 0],
];
const LOWER_IS_BETTER = new Set(); // every column is higher-is-better
const TEST_NAME = { cpu: "CPU", mem: "RAM", disk: "disk" };

function when(iso) {
  return iso ? new Date(iso).toLocaleString([], { dateStyle: "medium", timeStyle: "short" }) : "";
}

// Benchmark table view: raw values, or each column relative to its lowest-performing Pi (1.0×).
let benchView = "values";
try { benchView = localStorage.getItem("benchView") === "relative" ? "relative" : "values"; } catch { /* private mode */ }

function relativeTo(key, v, worst) {
  // Higher is better everywhere except latency, where the worst is the highest value.
  return LOWER_IS_BETTER.has(key) ? worst / v : v / worst;
}

function benchCell(group, key, digits, best, worst, rootSize) {
  if (!group) return `<td class="num empty">–</td>`;
  // A disk result measured on different storage (e.g. before an SD card swap) is out of date.
  const otherDisk = typeof group.root_size === "number" && typeof rootSize === "number" &&
    Math.abs(group.root_size - rootSize) > rootSize * 0.02;
  const v = group[key];
  if (typeof v !== "number") {
    return group.error
      ? `<td class="num failed" title="${esc(group.error)} (${esc(when(group.error_at))})">failed</td>`
      : `<td class="num empty">–</td>`;
  }
  const stale = group.error ? ` Last run failed: ${group.error}` : "";
  const cls = (v === best && !otherDisk ? "num best" : "num") + (otherDisk ? " stale" : "");
  const raw = v.toLocaleString(undefined, { maximumFractionDigits: digits });
  const shown = benchView === "relative" && worst
    ? `${relativeTo(key, v, worst).toLocaleString(undefined, { minimumFractionDigits: 1, maximumFractionDigits: 1 })}×`
    : raw;
  const tip = benchView === "relative" ? `${raw}. ` : "";
  const moved = otherDisk ? ` Measured on a different disk (${bytes(group.root_size)}); run Disk again.` : "";
  return `<td class="${cls}" title="${esc(tip)}Measured ${esc(when(group.measured))}.${esc(stale)}${esc(moved)}">${shown}</td>`;
}

function renderBenchTable(rows, running) {
  const best = {}, worst = {};
  for (const [g, k] of BENCH_COLS) {
    const vals = rows.map((r) => r.results[g] && r.results[g][k]).filter((v) => typeof v === "number" && v > 0);
    if (vals.length > 1) best[k] = LOWER_IS_BETTER.has(k) ? Math.min(...vals) : Math.max(...vals);
    if (vals.length) worst[k] = LOWER_IS_BETTER.has(k) ? Math.max(...vals) : Math.min(...vals);
  }
  $("#bench-table tbody").innerHTML = rows.map((r) => {
    const run = running[r.id];
    const note = run ? `<span class="running">${run.unreachable ? "waiting for the Pi" : `running ${TEST_NAME[run.current] || ""}`}</span>` : "";
    const cpu = r.results.cpu;
    const sub = cpu && cpu.compiler ? `<span class="unit" title="${esc(cpu.compiler)}">${esc(r.model || "")}</span>` : `<span class="unit">${esc(r.model || "")}</span>`;
    return `<tr><th scope="row">${esc(r.hostname)} ${note}<br>${sub}</th>${BENCH_COLS.map(([g, k, d]) => benchCell(r.results[g], k, d, best[k], worst[k], r.root_size)).join("")}</tr>`;
  }).join("");
}

function tickList(box, pis, name, stressStatus) {
  const prev = new Set([...box.querySelectorAll("input:checked")].map((i) => i.value));
  const first = box.children.length === 0;
  box.innerHTML = pis.map((p) => {
    const on = first ? p.status === "online" : prev.has(p.id);
    const st = stressStatus && p.live && p.live.stress ? Object.entries(p.live.stress).filter(([, v]) => v).map(([k]) => esc(k)) : [];
    const extra = p.status !== "online" ? ` <span class="muted">(${STATUS_TEXT[p.status].toLowerCase()})</span>`
      : st.length ? ` <span class="chip warn">stress: ${st.join(", ")}</span>` : "";
    return `<label><input type="checkbox" name="${name}" value="${esc(p.id)}" ${on ? "checked" : ""} ${p.status === "online" ? "" : "disabled"}> ${esc(p.hostname)}${extra}</label>`;
  }).join("");
}

function bench() {
  let pollTimer;
  const ticked = (sel) => [...document.querySelectorAll(sel)].filter((i) => i.checked).map((i) => i.value);

  let lastTable = null;
  document.querySelectorAll(".results-head .seg button").forEach((b) => {
    b.setAttribute("aria-pressed", String(b.dataset.view === benchView));
    b.addEventListener("click", () => {
      benchView = b.dataset.view;
      try { localStorage.setItem("benchView", benchView); } catch { /* fine */ }
      document.querySelectorAll(".results-head .seg button").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
      $("#bench-note").textContent = benchView === "relative"
        ? "Each column is divided by its lowest-performing Pi (1.0×). Hover over a number for the measured value."
        : "The best result in each column is in bold. Hover over a number to see when it was measured.";
      if (lastTable) renderBenchTable(lastTable.rows, lastTable.running);
    });
  });
  if (benchView === "relative") document.querySelector('.results-head .seg button[data-view="relative"]').click();

  async function refresh() {
    clearTimeout(pollTimer);
    let pis = [], table;
    try { [pis, table] = await Promise.all([api("GET", "/api/pis"), api("GET", "/api/bench")]); }
    catch (e) { toast(`Couldn't load benchmarks: ${e.message}`); pollTimer = setTimeout(refresh, 10000); return; }
    tickList($("#bench-pis"), pis, "pi", false);
    lastTable = table;
    renderBenchTable(table.rows, table.running);
    const n = Object.keys(table.running).length;
    $("#bench-run").disabled = n > 0;
    $("#bench-progress").textContent = n ? `Running on ${n} Pi${n === 1 ? "" : "s"}…` : "";
    pollTimer = setTimeout(refresh, n ? 3000 : 15000);
  }

  $("#bench-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const ids = ticked('#bench-pis input[name="pi"]'), tests = ticked('input[name="test"]');
    if (!ids.length || !tests.length) { toast("Tick at least one Pi and one test."); return; }
    try {
      const res = await api("POST", "/api/bench", { ids, tests });
      const failed = Object.entries(res).filter(([, r]) => r.error);
      if (failed.length) toast(failed.map(([id, r]) => `${id.slice(0, 8)}: ${r.error}`).join("; "));
    } catch (err) { toast(`Couldn't start: ${err.message}`); }
    refresh();
  });

  refresh();
}

// ---------- stress tab: controls + live charts ----------
const PI_COLOURS = ["#2f6fb0", "#d55e00", "#009e73", "#cc79a7", "#56b4e9", "#e69f00", "#7a5195", "#8c8c8c"];
const LIVE_WINDOW_MS = 5 * 60 * 1000;
const LIVE_METRICS = {
  cpu: (s) => s.cpu_pct,
  mem: (s) => (s.mem_total ? (s.mem_used / s.mem_total) * 100 : null),
  temp: (s) => s.temp_c,
  disk: (s) => (typeof s.disk_read_bps === "number" ? (s.disk_read_bps + (s.disk_write_bps || 0)) / 1e6 : null),
  net: (s) => (typeof s.net_rx_bps === "number" ? (s.net_rx_bps * 8) / 1e6 : null),
  power: (s) => (typeof s.power_w === "number" ? s.power_w : null),
};

function liveChart(id, opts = {}) {
  const ink = cssVar("--muted"), line = cssVar("--line");
  return new Chart(document.getElementById(id), {
    type: "line",
    data: { datasets: [] },
    options: {
      animation: false, responsive: true, maintainAspectRatio: false, parsing: false, normalized: true,
      interaction: { mode: "nearest", intersect: false },
      plugins: { legend: { labels: { color: ink, boxWidth: 12 } } },
      scales: {
        x: { type: "linear", grid: { color: line },
             ticks: { color: ink, maxTicksLimit: 6, maxRotation: 0,
                      callback: (v) => new Date(v).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" }) } },
        y: { ticks: { color: ink }, grid: { color: line }, min: opts.min, max: opts.max, suggestedMax: opts.suggestedMax },
      },
    },
  });
}

function stressPage() {
  const charts = {
    cpu: liveChart("l-cpu", { min: 0, max: 100 }),
    mem: liveChart("l-mem", { min: 0, max: 100 }),
    temp: liveChart("l-temp", { suggestedMax: 80 }),
    disk: liveChart("l-disk", { min: 0 }),
    net: liveChart("l-net", { min: 0 }),
    power: liveChart("l-power", { min: 0 }),
  };
  const series = {}; // pi id -> {name, colour, points: {cpu: [], ...}}
  let order = [];
  const ticked = (sel) => [...document.querySelectorAll(sel)].filter((i) => i.checked).map((i) => i.value);

  async function seed(p) {
    // Start each line with the last 10 minutes of stored history, so the charts aren't empty.
    try {
      const h = await api("GET", `/api/pis/${encodeURIComponent(p.id)}/history?range=1h`);
      const since = Date.now() - 10 * 60 * 1000;
      h.ts.forEach((t, i) => {
        if (t * 1000 < since) return;
        const s = { cpu_pct: h.cpu_pct[i], mem_used: h.mem_used[i], mem_total: h.mem_total[i], temp_c: h.temp_c[i],
                    net_rx_bps: h.net_rx_bps[i], power_w: (h.power_w || [])[i],
                    disk_read_bps: (h.disk_read_bps || [])[i], disk_write_bps: (h.disk_write_bps || [])[i] };
        for (const [k, f] of Object.entries(LIVE_METRICS)) series[p.id].points[k].push({ x: t * 1000, y: f(s) });
      });
    } catch { /* fine: the line just starts now */ }
  }

  function draw() {
    const ids = ticked('#stress-pis input[name="spi"]');
    const now = Date.now();
    for (const [k, chart] of Object.entries(charts)) {
      chart.data.datasets = ids.filter((id) => series[id]).map((id) => ({
        label: series[id].name, data: series[id].points[k], borderColor: series[id].colour,
        backgroundColor: series[id].colour, borderWidth: 1.6, pointRadius: 0, tension: 0.2,
      }));
      chart.options.scales.x.min = now - LIVE_WINDOW_MS;
      chart.options.scales.x.max = now;
      chart.update();
    }
  }

  async function poll() {
    const ids = ticked('#stress-pis input[name="spi"]').filter((id) => series[id]);
    if (ids.length) {
      try {
        const readings = await api("GET", `/api/now?ids=${ids.map(encodeURIComponent).join(",")}`);
        const cutoff = Date.now() - LIVE_WINDOW_MS - 10000;
        for (const [id, s] of Object.entries(readings)) {
          if (!s || !series[id]) continue;
          for (const [k, f] of Object.entries(LIVE_METRICS)) {
            const pts = series[id].points[k];
            pts.push({ x: s.ts * 1000, y: f(s) });
            while (pts.length && pts[0].x < cutoff) pts.shift();
          }
        }
      } catch { /* keep the last picture; next poll retries */ }
    }
    draw();
    setTimeout(poll, 2000);
  }

  async function loadPis() {
    try {
      const pis = await api("GET", "/api/pis");
      order = pis.map((p) => p.id);
      tickList($("#stress-pis"), pis, "spi", true);
      for (const p of pis) {
        if (!series[p.id]) {
          series[p.id] = { name: p.hostname, colour: PI_COLOURS[order.indexOf(p.id) % PI_COLOURS.length],
                           points: { cpu: [], mem: [], temp: [], disk: [], net: [], power: [] } };
          if (p.status === "online") seed(p);
        }
      }
    } catch (e) { toast(`Couldn't load Pis: ${e.message}`); }
  }
  const refresh = loadPis; // used by the Start/Stop handlers below
  async function pisLoop() { await loadPis(); setTimeout(pisLoop, 10000); }

  $("#stress-pis").addEventListener("change", draw);
  $("#stress-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const ids = ticked('#stress-pis input[name="spi"]'), loads = ticked('input[name="load"]');
    const minutes = Number($("#stress-minutes").value);
    if (!ids.length || !loads.length) { toast("Tick at least one Pi and one load."); return; }
    if (!(minutes >= 1 && minutes <= 30)) { toast("Choose between 1 and 30 minutes."); return; }
    const body = { cpu: loads.includes("cpu"), ram: loads.includes("ram"), disk: loads.includes("disk"),
                   net: loads.includes("net"), minutes };
    const out = await Promise.allSettled(ids.map((id) => api("POST", `/api/pis/${encodeURIComponent(id)}/stress`, body)));
    const bad = out.filter((o) => o.status === "rejected");
    toast(bad.length ? `Stress failed on ${bad.length} Pi(s): ${bad[0].reason.message}` : `Stress started for ${minutes} min on ${ids.length} Pi(s)`);
    refresh();
  });

  $("#stress-stop").addEventListener("click", async () => {
    const ids = ticked('#stress-pis input[name="spi"]');
    if (!ids.length) { toast("Tick the Pis to stop."); return; }
    await Promise.allSettled(ids.map((id) => api("POST", `/api/pis/${encodeURIComponent(id)}/stress/stop`)));
    toast(`Stress stopped on ${ids.length} Pi(s)`);
    refresh();
  });

  loadPis().then(() => { poll(); setTimeout(pisLoop, 10000); });
}

// ---------- events tab ----------
function eventsPage() {
  let days = 7;
  async function load() {
    let pis = [], r;
    try { [pis, r] = await Promise.all([api("GET", "/api/pis"), api("GET", `/api/events?days=${days}`)]); }
    catch (e) { toast(`Couldn't load events: ${e.message}`); return; }
    const names = Object.fromEntries(pis.map((p) => [p.id, p.hostname]));
    $("#events-summary tbody").innerHTML = pis.map((p) => {
      const s = r.summary[p.id] || { dropouts: 0, reboots: 0, longest_outage_s: 0, downtime_s: 0 };
      return `<tr><th>${esc(p.hostname)}</th><td class="num">${s.dropouts}</td><td class="num">${s.reboots}</td>` +
        `<td class="num">${s.longest_outage_s ? duration(s.longest_outage_s) : "–"}</td>` +
        `<td class="num">${s.downtime_s ? duration(s.downtime_s) : "–"}</td></tr>`;
    }).join("");
    $("#timeline tbody").innerHTML = eventRows(r.events, true, names);
    $("#no-timeline").hidden = r.events.length > 0;
  }
  document.querySelectorAll(".seg button[data-days]").forEach((b) => b.addEventListener("click", () => {
    document.querySelectorAll(".seg button[data-days]").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
    days = Number(b.dataset.days);
    load();
  }));
  load();
  setInterval(load, 30000);
}
