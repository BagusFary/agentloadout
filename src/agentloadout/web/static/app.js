"use strict";
const $ = (s) => document.querySelector(s);
const state = { items: [], meta: {}, kind: "all", shown: 60 };
const KIND_LABEL = { mcp: "MCP", skill: "Skill", app: "Aplikasi" };

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmt = (n) => (n >= 1000 ? (n / 1000).toFixed(n >= 10000 ? 0 : 1) + "k" : String(n ?? 0));

function ago(iso) {
  if (!iso) return "-";
  const d = (Date.now() - new Date(iso).getTime()) / 86400000;
  if (d < 1) return "hari ini";
  if (d < 2) return "kemarin";
  if (d < 31) return Math.floor(d) + " hari lalu";
  if (d < 365) return Math.floor(d / 30) + " bln lalu";
  return Math.floor(d / 365) + " thn lalu";
}

function momentumText(it) {
  if (it.momentum_basis === "7d" && it.stars_7d != null) return `+${fmt(it.stars_7d)}/minggu`;
  return `~${it.momentum < 10 ? it.momentum.toFixed(1) : Math.round(it.momentum)}/hari`;
}

function toast(msg) {
  const t = $("#toast");
  clearTimeout(toast._t); clearTimeout(toast._h);
  t.classList.remove("out"); t.textContent = msg; t.hidden = false;
  t.style.animation = "none"; void t.offsetWidth; t.style.animation = "";
  toast._t = setTimeout(() => { t.classList.add("out"); toast._h = setTimeout(() => (t.hidden = true), 260); }, 2000);
}

function busy(btn, on, label) {
  if (on) { btn._label = btn.innerHTML; btn.classList.add("busy"); btn.innerHTML = `<span class="spin"></span>${label || ""}`; }
  else { btn.classList.remove("busy"); if (btn._label != null) btn.innerHTML = btn._label; }
}

async function copy(text) {
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    const ta = document.createElement("textarea");
    ta.value = text; document.body.appendChild(ta); ta.select();
    document.execCommand("copy"); ta.remove();
  }
  toast("Tersalin");
}

function hasUpdate(it) {
  const st = it.install_state;
  return !!(st && st.status === "installed" && st.latest_version && st.installed_version && st.latest_version !== st.installed_version);
}
function isOff(it) { return isInstalled(it) && it.install_state.enabled === 0; }
function shortVer(v) { return v && /^[0-9a-f]{7,40}$/.test(v) ? v.slice(0, 7) : v; }

function hasHigh(it) { return (it.risks || []).some((r) => r.level === "high"); }
function isInstalled(it) { return it.install_state && it.install_state.status === "installed"; }

function filtered() {
  const q = $("#q").value.trim().toLowerCase();
  const cat = $("#cat").value;
  const fi = $("#f-install").checked, fs = $("#f-safe").checked, fd = $("#f-installed").checked;
  let list = state.items.filter((it) =>
    (state.kind === "all" || it.kind === state.kind) &&
    (!cat || it.category === cat) &&
    (!fi || (it.install || []).length) &&
    (!fs || !hasHigh(it)) &&
    (!fd || isInstalled(it)) &&
    (!q || [it.name, it.full_name, it.description, it.description_id, (it.topics || []).join(" "), (it.skills || []).map((k) => k.name).join(" ")].join(" ").toLowerCase().includes(q))
  );
  const s = $("#sort").value;
  const key = {
    momentum: (it) => it.momentum || 0,
    stars: (it) => it.stars || 0,
    pushed: (it) => Date.parse(it.pushed_at || 0),
    created: (it) => Date.parse(it.created_at || 0),
  }[s];
  return list.sort((a, b) => key(b) - key(a));
}

function stateBadge(it) {
  const st = it.install_state && it.install_state.status;
  if (st === "installed" && hasUpdate(it)) return '<span class="state wait">Update tersedia</span>';
  if (st === "installed" && isOff(it)) return '<span class="state bad">Mati</span>';
  if (st === "installed") return '<span class="state ok">Terpasang</span>';
  if (st === "requested") return '<span class="state wait">Diminta</span>';
  if (st === "review") return '<span class="state wait">Diperiksa</span>';
  if (st === "failed") return '<span class="state bad">Gagal</span>';
  return "";
}

const LEVEL = { high: "tinggi", medium: "sedang", info: "info" };

function card(it) {
  const risks = (it.risks || []).filter((r) => r.level !== "info").slice(0, 2)
    .map((r) => `<span class="tag"><span class="dot ${r.level}"></span>${esc(r.label)}</span>`).join("");
  return `<article class="card" data-id="${esc(it.id)}">
    <div class="meta"><span class="dot ${it.kind}"></span><span class="kindtxt">${KIND_LABEL[it.kind]}</span><span>·</span><span class="owner">${esc(it.full_name.split("/")[0])} · ${esc(it.category)}</span>${stateBadge(it)}</div>
    <h3>${esc(it.name)}</h3>
    <p class="desc">${esc(it.description_id || it.description || "Tanpa deskripsi")}</p>
    <div class="stats"><span>★ ${fmt(it.stars)}</span><span class="hot">↑ ${momentumText(it)}</span><span>${ago(it.pushed_at)}</span>${(it.install || []).length ? "<span>Siap pasang</span>" : ""}</div>
    ${risks ? `<div class="tags">${risks}</div>` : ""}
  </article>`;
}

const PAGE = 36;
let lastList = [];

function render(animate = true) {
  lastList = filtered();
  const grid = $("#grid");
  state.shown = PAGE;
  grid.innerHTML = lastList.length ? lastList.slice(0, PAGE).map(card).join("") : '<p class="empty">Tidak ada yang cocok.</p>';
  if (animate) stagger(grid.children, 0);
  $("#sentinel").hidden = lastList.length <= PAGE;
  $("#summary").textContent = `${lastList.length} hasil`;
  renderFresh();
}

function freshRows() {
  const q = $("#q").value.trim(), cat = $("#cat").value;
  const filtersOn = q || cat || $("#f-install").checked || $("#f-safe").checked || $("#f-installed").checked;
  const pool = state.items.filter((it) => state.kind === "all" || it.kind === state.kind);
  if (filtersOn || !pool.length) return [];
  const DAY = 86400000, now = Date.now();
  const rows = [];
  const rising = pool.filter((it) => it.momentum_basis === "7d" && it.stars_7d > 0)
    .sort((a, b) => b.stars_7d - a.stars_7d).slice(0, 12);
  if (rising.length) rows.push(["Naik minggu ini", "bintang bertambah paling banyak 7 hari terakhir", rising]);
  const fresh = pool.filter((it) => now - Date.parse(it.created_at) < 21 * DAY)
    .sort((a, b) => b.momentum - a.momentum).slice(0, 12);
  if (fresh.length) rows.push(["Baru minggu ini", "repo baru yang langsung ramai", fresh]);
  return rows;
}

function renderFresh() {
  const box = $("#fresh");
  const rows = freshRows();
  box.hidden = !rows.length;
  box.innerHTML = rows.map(([title, sub, list]) => `
    <div class="fresh-head"><h2>${title}</h2><span>${sub}</span></div>
    <div class="rail">${list.map((it) => `
      <article class="mini-card" data-id="${esc(it.id)}">
        <div class="meta"><span class="dot ${it.kind}"></span><span class="kindtxt">${KIND_LABEL[it.kind]}</span></div>
        <h3>${esc(it.name)}</h3>
        <p>${esc(it.description_id || it.description || "")}</p>
        <div class="stats"><span>★ ${fmt(it.stars)}</span><span class="hot">↑ ${it.momentum_basis === "7d" && it.stars_7d != null ? "+" + fmt(it.stars_7d) : momentumText(it)}</span><span>${ago(it.created_at)}</span></div>
      </article>`).join("")}</div>`).join("");
}

function appendMore() {
  if (state.shown >= lastList.length) return;
  const grid = $("#grid");
  const start = state.shown;
  state.shown += PAGE;
  grid.insertAdjacentHTML("beforeend", lastList.slice(start, state.shown).map(card).join(""));
  stagger([...grid.children].slice(start), 0);
  $("#sentinel").hidden = state.shown >= lastList.length;
}

function stagger(nodes, offset) {
  // only the first ~14 visible cards animate; the rest just appear (cheap)
  [...nodes].slice(0, 14).forEach((el, i) => {
    el.style.setProperty("--i", i + offset);
    el.classList.add("in");
    el.addEventListener("animationend", () => el.classList.remove("in"), { once: true });
  });
}

function skeleton(n = 9) {
  const sk = '<article class="card sk"><i class="s"></i><i class="h"></i><i class="l"></i><i class="m"></i><i class="s"></i></article>';
  $("#grid").innerHTML = sk.repeat(n);
}

function topbar(on) { $("#topbar").classList.toggle("on", on); }

function noahPrompt(it) {
  const risk = (it.risks || []).map((r) => r.label).join(", ") || "tidak ada label otomatis";
  if (it.kind === "mcp") {
    const cfg = (it.install || []).find((s) => s.client.startsWith("JSON"));
    const env = (it.env_vars || []).map((e) => e.name).join(", ");
    return `Noah, tolong periksa lalu pasang server MCP "${it.name}" ke OpenBot supaya semua agen bisa memakainya.\n` +
      `Repo: ${it.url}\nLabel risiko otomatis: ${risk}.\n` +
      (cfg ? `Config yang disarankan:\n${cfg.snippet}\n` : "Belum ada config otomatis, cek README-nya.\n") +
      (env ? `Butuh variabel: ${env} (minta lewat form aman, jangan lewat chat).\n` : "") +
      `Cek dulu kode dan izinnya, jelaskan risikonya, lalu minta persetujuanku sebelum memasang.`;
  }
  if (it.kind === "skill") {
    const paths = (it.skill_paths || []).slice(0, 15).join(", ");
    return `Noah, tolong periksa skill dari ${it.url} lalu jadikan skill lokal OpenBot dan pasang ke semua agen.\n` +
      `Folder skill (SKILL.md): ${paths}${(it.skill_paths || []).length > 15 ? ", …" : ""}.\n` +
      `Label risiko otomatis: ${risk}.\nBaca SKILL.md dan skrip di dalamnya dulu, jelaskan isinya, lalu minta persetujuanku sebelum memasang.`;
  }
  const cmd = (it.install || [])[0];
  return `Noah, tolong periksa aplikasi "${it.name}" (${it.url}) untuk dipakai agen di laptop ini.\n` +
    (cmd ? `Perintah pasang yang disarankan: ${cmd.snippet}\n` : "Cara pasangnya ada di README.\n") +
    `Label risiko otomatis: ${risk}.\nCek dulu keamanannya dan minta persetujuanku sebelum menjalankan perintah apa pun.`;
}

function openDetail(id) {
  const it = state.items.find((x) => x.id === id);
  if (!it) return;
  const snippets = (it.install || []).map((s, i) => `
    <div class="snip"><header><span>${esc(s.client)}</span><button data-copy="${i}">Salin</button></header><pre>${esc(s.snippet)}</pre></div>`).join("");
  const risks = (it.risks || []).map((r) => `<li><span class="dot ${r.level}"></span><span>${esc(r.label)} <span class="note">· ${LEVEL[r.level] || r.level}</span></span></li>`).join("");
  const env = (it.env_vars || []).map((e) => `<li><span><code>${esc(e.name)}</code>${e.secret ? " · rahasia" : ""}<br><span class="note">${esc(e.description)}</span></span></li>`).join("");
  const canAct = !!state.meta.actions_allowed;
  const st = it.install_state || {};
  const installed = isInstalled(it);
  const skillList = (it.skills && it.skills.length ? it.skills : (it.skill_paths || []).map((p) => ({ path: p })));
  const multi = it.kind === "skill" && skillList.length > 1;
  const paths = multi ? skillList.slice(0, 40).map((sk, i) => `<li class="skill-row">
      <span class="sk-txt"><b>${esc(sk.name || sk.path.split("/").pop())}</b>${sk.description ? `<span class="note">${esc(sk.description)}</span>` : ""}<code>${esc(sk.path)}</code></span>
      ${!installed && canAct ? `<button class="mini" data-skill="${i}">Pasang</button>` : ""}
    </li>`).join("") : "";
  let actions;
  if (installed) {
    const ver = st.installed_version ? `<p class="ver">Versi terpasang <b>${esc(shortVer(st.installed_version))}</b>${hasUpdate(it) ? ` · <span class="upd">terbaru ${esc(shortVer(st.latest_version))}</span>` : st.latest_version ? " · sudah terbaru" : ""}</p>` : "";
    const isMcp = /^(mcp|skill):/.test(st.ref || "");
    actions = `${ver}<div class="actions three">
      ${hasUpdate(it) ? `<button class="btn primary" id="b-update" ${canAct ? "" : "disabled"}>Update</button>` : ""}
      ${isMcp ? `<button class="btn tinted" id="b-toggle" ${canAct ? "" : "disabled"}>${isOff(it) ? "Nyalakan" : "Matikan"}</button>` : ""}
      <button class="btn danger" id="b-remove" ${canAct ? "" : "disabled"}>Lepas</button>
    </div>`;
  } else {
    actions = `<div class="actions">
      <button class="btn tinted" id="b-noah">Salin untuk Noah</button>
      <button class="btn primary" id="b-install" ${canAct ? "" : "disabled"}>${multi ? "Pasang semua" : "Pasang ke OpenBot"}</button>
    </div>`;
  }
  const lockNote = !canAct ? `<p class="note lock">${state.meta.install_enabled ? "Pasang, Update, dan Lepas hanya bisa dari perangkat yang tersambung Tailscale. Dari internet publik, katalog hanya bisa dilihat." : "Tombol pasang belum aktif."}</p>` : "";
  $("#detail").innerHTML = `<div class="detail">
    <div class="meta"><span class="dot ${it.kind}"></span><span class="kindtxt">${KIND_LABEL[it.kind]}</span><span>·</span><span>${esc(it.category)}</span>${stateBadge(it)}</div>
    <h2>${esc(it.name)}</h2>
    <a href="${esc(it.url)}" target="_blank" rel="noopener">${esc(it.full_name)} ↗</a>
    <p class="lead">${esc(it.description_id || it.description || "")}</p>
    ${it.description_id && it.description ? `<p class="note orig">Asli: ${esc(it.description)}</p>` : ""}
    <div class="stats"><span>★ ${fmt(it.stars)}</span><span class="hot">↑ ${momentumText(it)}${it.momentum_basis === "lifetime" ? " (rata-rata)" : ""}</span><span>${ago(it.pushed_at)}</span>${it.language ? `<span>${esc(it.language)}</span>` : ""}<span>${esc(it.license || "tanpa lisensi")}</span></div>
    ${actions}
    ${lockNote}
    ${st.detail ? `<p class="note">Status: ${esc(st.status)} · ${esc(st.detail)}</p>` : ""}
    <div class="section"><h4>Risiko · label otomatis</h4>
      ${risks ? `<ul>${risks}</ul>` : '<p class="note">Tidak ada label otomatis. Tetap baca kode sebelum memasang.</p>'}</div>
    ${env ? `<div class="section"><h4>Variabel yang dibutuhkan</h4><ul>${env}</ul></div>` : ""}
    <div class="section"><h4>Cara pasang</h4>
      ${snippets || '<p class="note">Belum ada perintah otomatis. Lihat README repo.</p>'}</div>
    ${paths ? `<div class="section"><h4>Skill di repo ini · ${skillList.length}</h4><ul class="skills">${paths}</ul>${skillList.length > 40 ? `<p class="note">+${skillList.length - 40} skill lain di repo.</p>` : ""}</div>` : ""}
    ${(it.topics || []).length ? `<div class="section"><h4>Topik</h4><div class="topics">${it.topics.map((t) => `<span>${esc(t)}</span>`).join("")}</div></div>` : ""}
  </div>`;
  $("#detail").querySelectorAll("[data-copy]").forEach((b) => b.addEventListener("click", async () => {
    await copy(it.install[+b.dataset.copy].snippet);
    b.textContent = "Tersalin"; b.classList.add("done");
    setTimeout(() => { b.textContent = "Salin"; b.classList.remove("done"); }, 1400);
  }));
  const on = (sel, fn) => { const b = $(sel); if (b && canAct) b.addEventListener("click", () => fn(b)); };
  const bn = $("#b-noah"); if (bn) bn.addEventListener("click", () => copy(noahPrompt(it)));
  on("#b-install", (b) => sendAction(it, "install", b));
  on("#b-update", (b) => sendAction(it, "update", b));
  on("#b-remove", (b) => sendAction(it, "remove", b));
  on("#b-toggle", (b) => sendAction(it, isOff(it) ? "enable" : "disable", b));
  $("#detail").querySelectorAll("[data-skill]").forEach((b) => b.addEventListener("click", () => sendAction(it, "install", b, skillList[+b.dataset.skill].path)));
  const sh = $("#sheet");
  sh.classList.remove("closing");
  sh.hidden = false;
  sh.querySelector(".sheet-card").scrollTop = 0;
  document.body.style.overflow = "hidden";
}

const ACTION_TEXT = {
  install: ["pasang", "Noah akan memeriksa repo lalu meminta persetujuanmu di chat."],
  update: ["update", "Noah akan memeriksa perubahan versi baru lalu meminta persetujuanmu di chat."],
  remove: ["lepas", "Noah akan menjelaskan apa yang dilepas lalu meminta persetujuanmu di chat."],
  enable: ["nyalakan", "Langsung dinyalakan lagi untuk semua agen."],
  disable: ["matikan", "Langsung dimatikan untuk semua agen (tidak dihapus, bisa dinyalakan lagi)."],
};

async function sendAction(it, action, btn, skillPath) {
  const [verb, how] = ACTION_TEXT[action];
  const target = skillPath ? `skill "${skillPath.split("/").pop()}" dari ${it.name}` : `"${it.name}"`;
  if (!confirm(`${verb[0].toUpperCase() + verb.slice(1)} ${target}?\n\n${how}`)) return;
  busy(btn, true, btn.classList.contains("mini") ? "" : "Mengirim…");
  try {
    const r = await fetch("/api/install", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: it.id, action, skill_path: skillPath || null }) });
    const j = await r.json().catch(() => ({}));
    busy(btn, false);
    if (!r.ok) return toast("Gagal: " + (j.error || r.status));
    toast(action === "enable" || action === "disable" ? `Permintaan ${verb} terkirim` : "Permintaan terkirim ke Noah, cek chat");
    if (action === "install") it.install_state = { status: "requested", detail: "install dikirim ke Noah" };
    render(false); closeDetail();
  } catch (e) { busy(btn, false); toast("Gagal mengirim: " + e.message); }
}

function closeDetail() {
  const sh = $("#sheet");
  if (sh.hidden || sh.classList.contains("closing")) return;
  sh.classList.add("closing");
  const done = () => { sh.hidden = true; sh.classList.remove("closing"); document.body.style.overflow = ""; };
  const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (reduce) return done();
  sh.querySelector(".sheet-card").addEventListener("animationend", done, { once: true });
  setTimeout(() => { if (!sh.hidden) done(); }, 400);
}

async function load(quiet) {
  if (!quiet) skeleton();
  topbar(true);
  try {
    const r = await fetch("/api/items");
    if (r.status === 401) { location.href = "/login"; return; }
    const j = await r.json();
    state.items = j.items; state.meta = j.meta;
    const cats = [...new Set(state.items.map((i) => i.category))].sort();
    const curCat = $("#cat").value;
    $("#cat").innerHTML = '<option value="">Semua kategori</option>' + cats.map((c) => `<option>${esc(c)}</option>`).join("");
    $("#cat").value = curCat;
    const c = (k) => state.items.filter((i) => k === "all" || i.kind === k).length;
    ["all", "mcp", "skill", "app"].forEach((k) => ($("#c-" + k).textContent = c(k)));
    showUpdated(j.meta.last_run);
    requestAnimationFrame(moveSeg);
    if (quiet) { $("#grid").classList.remove("fade"); void $("#grid").offsetWidth; $("#grid").classList.add("fade"); }
    render(!quiet);
    showSync(j.meta.progress);
  } finally { topbar(false); }
}

function showUpdated(lr) {
  $("#updated").textContent = lr ? `Diperbarui ${new Date(lr.at).toLocaleString("id-ID", { dateStyle: "medium", timeStyle: "short" })}` : "Belum ada data";
}

let syncTimer = null, wasRunning = false;
function showSync(p) {
  const box = $("#sync");
  const running = !!(p && p.running);
  if (running) {
    box.hidden = false;
    $("#sync-label").textContent = p.label || "Memperbarui";
    const det = p.total ? `${p.done || 0}/${p.total}` : "";
    $("#sync-sub").textContent = `Memperbarui dari GitHub · ${p.step || 1}/${p.steps || 4}${det ? " · " + det : ""}`;
    const frac = ((p.step || 1) - 1 + (p.total ? (p.done || 0) / p.total : 0.5)) / (p.steps || 4);
    box.classList.toggle("indet", !p.total);
    $("#sync-fill").style.setProperty("--p", Math.max(0.04, Math.min(1, frac)).toFixed(3));
  } else {
    box.hidden = true;
  }
  if (wasRunning && !running) { toast("Data katalog sudah diperbarui"); load(true); }
  wasRunning = running;
  clearTimeout(syncTimer);
  syncTimer = setTimeout(pollStatus, running ? 4000 : 60000);
}

async function pollStatus() {
  if (document.hidden) { syncTimer = setTimeout(pollStatus, 15000); return; }
  try {
    const r = await fetch("/api/status");
    if (r.status === 401) { location.href = "/login"; return; }
    const j = await r.json();
    if (!wasRunning && j.last_run && state.meta.last_run && j.last_run.at !== state.meta.last_run.at) { load(true); return; }
    showSync(j.progress);
  } catch { syncTimer = setTimeout(pollStatus, 15000); }
}

function moveSeg() {
  const on = $("#tabs button.on"), ind = $(".seg-ind");
  if (!on || !ind) return;
  ind.style.setProperty("--x", on.offsetLeft + "px");
  ind.style.setProperty("--w", on.offsetWidth + "px");
}
addEventListener("resize", () => requestAnimationFrame(moveSeg));
requestAnimationFrame(moveSeg);

$("#tabs").addEventListener("click", (e) => {
  const b = e.target.closest("button"); if (!b || b.classList.contains("on")) return;
  document.querySelectorAll("#tabs button").forEach((x) => x.classList.toggle("on", x === b));
  moveSeg();
  state.kind = b.dataset.kind;
  requestAnimationFrame(() => render());
});
let qTimer;
$("#q").addEventListener("input", () => { clearTimeout(qTimer); qTimer = setTimeout(() => render(false), 140); });
["#cat", "#sort", "#f-install", "#f-safe", "#f-installed"].forEach((s) => $(s).addEventListener("change", () => render()));
new IntersectionObserver((ents) => { if (ents.some((e) => e.isIntersecting)) appendMore(); }, { rootMargin: "600px" }).observe($("#sentinel"));
$("#grid").addEventListener("click", (e) => { const c = e.target.closest(".card"); if (c) openDetail(c.dataset.id); });
$("#fresh").addEventListener("click", (e) => { const c = e.target.closest(".mini-card"); if (c) openDetail(c.dataset.id); });
const THEMES = ["auto", "light", "dark"];
const THEME_ICON = {
  auto: '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="8"/><path d="M12 4a8 8 0 0 1 0 16z" fill="currentColor"/></svg>',
  light: '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>',
  dark: '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/></svg>',
};
const THEME_NAME = { auto: "Otomatis", light: "Terang", dark: "Gelap" };
function applyTheme(t, announce) {
  const root = document.documentElement;
  if (announce) {
    root.classList.add("theme-anim");
    clearTimeout(applyTheme._t); applyTheme._t = setTimeout(() => root.classList.remove("theme-anim"), 420);
    $("#theme").classList.toggle("turn");
  }
  root.dataset.theme = t;
  localStorage.setItem("katalog-theme", t);
  $("#theme").innerHTML = THEME_ICON[t];
  $("#theme").title = "Tema: " + THEME_NAME[t];
  if (announce) toast("Tema: " + THEME_NAME[t]);
}
applyTheme(document.documentElement.dataset.theme || "auto");
$("#theme").addEventListener("click", () => {
  const cur = document.documentElement.dataset.theme || "auto";
  applyTheme(THEMES[(THEMES.indexOf(cur) + 1) % THEMES.length], true);
});

$("#sheet").addEventListener("click", (e) => { if (e.target.hasAttribute("data-close")) closeDetail(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeDetail(); });
load().catch((e) => { $("#updated").textContent = "Gagal memuat: " + e.message; $("#grid").innerHTML = '<p class="empty">Gagal memuat data. Coba muat ulang.</p>'; });
