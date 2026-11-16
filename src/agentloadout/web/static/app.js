"use strict";
const $ = (s) => document.querySelector(s);
const state = { items: [], meta: {}, kind: "all", shown: 60 };
const KIND_LABEL = { mcp: "MCP", skill: "Skill", app: "Aplikasi" };

const API_HEADERS = { "Content-Type": "application/json", "X-Agentloadout": "1" };
async function api(path, body) {
  const r = await fetch(path, { method: "POST", headers: API_HEADERS, body: JSON.stringify(body || {}) });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error || r.status);
  return j;
}
const HARNESS_NAME = { "claude-code": "Claude Code", codex: "Codex", cursor: "Cursor", gemini: "Gemini CLI", opencode: "OpenCode", openbot: "OpenBot", claude: "~/.claude/skills", agents: "~/.agents/skills" };

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
  const fi = $("#f-install").checked, fs = $("#f-safe").checked, fd = $("#f-installed").checked, fu = $("#f-unchecked").checked;
  let list = state.items.filter((it) =>
    (state.kind === "all" || it.kind === state.kind) &&
    (!cat || it.category === cat) &&
    (!fi || (it.install || []).length) &&
    (!fs || !hasHigh(it)) &&
    (!fd || isInstalled(it)) &&
    (!fu || it.checked === 0) &&
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
  // checked: 0 = not examined (GitHub API quota ran out); NULL (older catalogs) counts as examined
  const unchecked = it.checked === 0 ? '<span class="state wait" title="Detail repo (README, daftar skill, cara pasang) belum diambil karena kuota API GitHub habis. Dilengkapi di pembaruan berikutnya; token GitHub mempercepatnya.">Belum diperiksa</span>' : "";
  return `<article class="card" data-id="${esc(it.id)}">
    <div class="meta"><span class="dot ${it.kind}"></span><span class="kindtxt">${KIND_LABEL[it.kind]}</span><span>·</span><span class="owner">${esc(it.full_name.split("/")[0])} · ${esc(it.category)}</span>${stateBadge(it)}${unchecked}</div>
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
  const filtersOn = q || cat || $("#f-install").checked || $("#f-safe").checked || $("#f-installed").checked || $("#f-unchecked").checked;
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
      ${canAct ? `<button class="mini" data-skill="${i}">Pasang</button>` : ""}
    </li>`).join("") : "";
  let actions;
  const tg = (it.targets || []);
  const tgLine = tg.length ? `<p class="ver">Terpasang di: <b>${tg.map((h) => esc(HARNESS_NAME[h] || h)).join(", ")}</b></p>` : "";
  if (installed && state.meta.openbot_webhook) {
    const ver = st.installed_version ? `<p class="ver">Versi terpasang <b>${esc(shortVer(st.installed_version))}</b>${hasUpdate(it) ? ` · <span class="upd">terbaru ${esc(shortVer(st.latest_version))}</span>` : st.latest_version ? " · sudah terbaru" : ""}</p>` : "";
    const isMcp = /^(mcp|skill):/.test(st.ref || "");
    actions = `${ver}<div class="actions three">
      ${hasUpdate(it) ? `<button class="btn primary" id="b-update" ${canAct ? "" : "disabled"}>Update</button>` : ""}
      ${isMcp ? `<button class="btn tinted" id="b-toggle" ${canAct ? "" : "disabled"}>${isOff(it) ? "Nyalakan" : "Matikan"}</button>` : ""}
      <button class="btn danger" id="b-remove" ${canAct ? "" : "disabled"}>Lepas (OpenBot)</button>
    </div>${tgLine}`;
  } else {
    actions = `${tgLine}<div class="actions">
      <button class="btn tinted" id="b-noah">Salin untuk agen</button>
      ${it.kind === "app" ? "" : `<button class="btn primary" id="b-install" ${canAct ? "" : "disabled"}>${multi ? "Pilih skill di bawah" : "Pasang…"}</button>`}
    </div>
    ${tg.length ? `<div class="actions"><button class="btn danger" id="b-unplug" ${canAct ? "" : "disabled"}>Lepas dari harness…</button></div>` : ""}`;
  }
  const lockNote = !canAct ? `<p class="note lock">Pasang dan Lepas hanya bisa dari komputer ini atau perangkat Tailscale. Dari internet publik, katalog hanya bisa dilihat.</p>` : "";
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
  on("#b-install", () => multi ? $(".skills")?.scrollIntoView({ behavior: "smooth" }) : openChooser(it, "install"));
  on("#b-unplug", () => openChooser(it, "remove"));
  on("#b-update", (b) => sendAction(it, "update", b));
  on("#b-remove", (b) => sendAction(it, "remove", b));
  on("#b-toggle", (b) => sendAction(it, isOff(it) ? "enable" : "disable", b));
  $("#detail").querySelectorAll("[data-skill]").forEach((b) => b.addEventListener("click", () => openChooser(it, "install", skillList[+b.dataset.skill].path)));
  const sh = $("#sheet");
  sh.classList.remove("closing");
  sh.hidden = false;
  sh.querySelector(".sheet-card").scrollTop = 0;
  document.body.style.overflow = "hidden";
}

// ---------------------------------------------------------------- multi-harness install (plans)
async function loadHarnesses() {
  try { state.harnesses = (await (await fetch("/api/harnesses")).json()).harnesses; } catch { state.harnesses = []; }
}

function openChooser(it, action, skillPath) {
  const det = (state.harnesses || []).filter((h) => h.detected);
  const installedOn = new Set(it.targets || []);
  const pool = action === "remove" ? det.filter((h) => installedOn.has(h.id) || (it.kind === "skill" && ["claude-code", "codex", "cursor", "gemini", "opencode"].includes(h.id))) : det;
  const title = action === "remove" ? "Lepas dari mana?" : "Pasang ke mana?";
  const what = skillPath ? `skill <b>${esc(skillPath.split("/").pop())}</b>` : `<b>${esc(it.name)}</b>`;
  $("#detail").insertAdjacentHTML("afterbegin", "");
  const box = document.createElement("div");
  box.className = "chooser";
  box.innerHTML = `<h3>${title}</h3><p class="note">${action === "remove" ? "Lepas" : "Pasang"} ${what}. Harness yang terdeteksi di komputer ini:</p>
    ${pool.length ? `<div class="hs">${pool.map((h) => `<label class="h-opt"><input type="checkbox" value="${h.id}" ${action === "remove" ? (installedOn.has(h.id) ? "checked" : "") : (h.id === "openbot" && !state.meta.openbot_webhook ? "" : "checked")}><span><b>${esc(h.name)}</b><small>${esc(h.config || "")}</small></span></label>`).join("")}</div>`
      : '<p class="note lock">Tidak ada harness yang terdeteksi. Pasang Claude Code, Codex, Cursor, Gemini CLI, atau OpenCode dulu.</p>'}
    <div class="actions"><button class="btn ghost" data-x>Batal</button><button class="btn primary" data-go ${pool.length ? "" : "disabled"}>Buat rencana</button></div>
    <div class="plan-out"></div>`;
  const d = $("#detail .detail");
  d.prepend(box);
  $(".sheet-card").scrollTop = 0;
  box.querySelector("[data-x]").onclick = () => box.remove();
  box.querySelector("[data-go]").onclick = async (ev) => {
    const targets = [...box.querySelectorAll("input:checked")].map((x) => x.value);
    if (!targets.length) return toast("Pilih minimal satu harness");
    busy(ev.target, true, "Menyusun…");
    try {
      const p = await api("/api/plans", { item_id: it.id, targets, action, skill_path: skillPath || null });
      busy(ev.target, false); ev.target.remove();
      box.querySelector(".plan-out").innerHTML = planHtml(p);
      wirePlan(box, p, it);
    } catch (e) { busy(ev.target, false); toast("Gagal: " + e.message); }
  };
}

function planHtml(p) {
  const spec = p.spec ? (p.spec.url || `${p.spec.command} ${(p.spec.args || []).join(" ")}`) : "";
  return `<div class="plan">
    <div class="plan-h">Rencana <b>${esc(p.id)}</b> · ${esc(p.action === "install" ? "pasang" : "lepas")} ${esc(p.kind)}</div>
    ${spec ? `<pre>${esc(spec)}</pre>` : ""}
    ${p.skill ? `<p class="note">Folder skill: <code>${esc(p.skill.path)}</code> → <code>${esc(p.skill.slug)}</code></p>` : ""}
    <ul class="changes">${p.changes.map((c) => `<li><b>${esc(HARNESS_NAME[c.harness] || c.harness)}</b><span>${esc(c.detail)}</span></li>`).join("")}</ul>
    ${(p.warnings || []).map((w) => `<p class="note warn">⚠ ${esc(w)}</p>`).join("")}
    ${(p.risks || []).length ? `<p class="note">Label risiko: ${p.risks.map(esc).join(", ")}</p>` : ""}
    <p class="note">Config lama dicadangkan otomatis sebelum diubah.</p>
    <input class="pin-input" data-pin type="password" inputmode="numeric" autocomplete="off" pattern="[0-9]*" placeholder="PIN persetujuan" aria-label="PIN persetujuan">
    <p class="note">PIN dibuat sekali oleh Anda lewat <code>agentloadout set-pin</code> di terminal. Jangan berikan ke agen.</p>
    <div class="actions"><button class="btn ghost" data-reject>Tolak</button><button class="btn primary" data-approve>Setujui & ${p.action === "install" ? "pasang" : "lepas"}</button></div>
    <div class="plan-res"></div></div>`;
}

function wirePlan(root, p, it) {
  root.querySelector("[data-reject]").onclick = async () => { try { await api(`/api/plans/${p.id}/reject`); toast("Rencana ditolak"); root.remove?.(); refreshPlans(); } catch (e) { toast("Gagal: " + e.message); } };
  root.querySelector("[data-approve]").onclick = async (ev) => {
    const pinEl = root.querySelector("[data-pin]");
    const pin = pinEl ? pinEl.value.trim() : "";
    if (!pin) return toast("Masukkan PIN persetujuan");
    busy(ev.target, true, "Memasang…");
    try {
      const r = await api(`/api/plans/${p.id}/approve`, { apply: true, pin });
      if (pinEl) { pinEl.value = ""; pinEl.remove(); }
      busy(ev.target, false);
      root.querySelector(".plan-res").innerHTML = `<ul class="changes">${(r.results || []).map((x) => `<li><b>${x.ok === false ? "✗" : x.ok ? "✓" : "…"} ${esc(HARNESS_NAME[x.harness] || x.harness)}</b><span>${esc(x.error || x.result || (x.backup ? "selesai · cadangan dibuat" : "selesai"))}</span></li>`).join("")}</ul>${r.status === "awaiting_agent" ? `<p class="note warn">${esc(STATUS_LABEL.awaiting_agent)}: langkah OpenBot belum dikerjakan. Minta agen OpenBot menjalankannya; statusnya baru selesai setelah agen melapor.</p>` : ""}${r.openbot ? `<p class="note">${esc(r.openbot)}</p>` : ""}`;
      toast(r.status === "applied" ? "Selesai" : (STATUS_LABEL[r.status] || "Sebagian gagal, lihat rincian"));
      root.querySelectorAll("[data-approve],[data-reject]").forEach((b) => b.remove());
      await load(true); refreshPlans();
    } catch (e) { busy(ev.target, false); if (pinEl) pinEl.value = ""; toast("Gagal: " + e.message); }
  };
}

const STATUS_LABEL = { pending: "Menunggu persetujuan", approved: "Disetujui", rejected: "Ditolak", revoked: "Dicabut (PIN diganti/direset)",
  applied: "Selesai", failed: "Gagal", partial: "Sebagian berhasil", awaiting_agent: "Menunggu agen OpenBot",
  incomplete: "Tidak lengkap: agen OpenBot tidak melapor" };

// pending plans created by agents
async function refreshPlans() {
  try {
    const j = await (await fetch("/api/plans")).json();
    const plans = j.plans || [];
    const pr = $("#pinreset");
    pr.hidden = !j.pin_reset_recent;
    if (j.pin_reset_recent) pr.textContent = `PIN persetujuan direset pada ${new Date(j.pin_reset_at).toLocaleString("id-ID", { dateStyle: "medium", timeStyle: "short" })}. Bukan Anda? Jalankan agentloadout set-pin --reset di laptop.`;
    state.waiting = plans.filter((p) => p.status === "awaiting_agent" || p.status === "incomplete");
    state.pending = plans.filter((p) => p.status === "pending");
    const b = $("#pending");
    b.hidden = !state.pending.length;
    b.querySelector("b").textContent = state.pending.length;
  } catch { /* ignore */ }
}

function openPending() {
  $("#detail").innerHTML = `<div class="detail"><h2>Menunggu persetujuan</h2><p class="lead">Rencana yang dibuat agen. Periksa sebelum menyetujui.</p>
    ${(state.pending || []).map((p) => `<div class="pending-item" data-pid="${esc(p.id)}"><p class="ver"><b>${esc(p.item_name)}</b> · dibuat ${esc(p.created_by)} · ${esc(ago(p.created_at))}</p>${planHtml(p)}</div>`).join("") || "<p class='note'>Tidak ada.</p>"}
    ${(state.waiting || []).length ? `<h3>Menunggu agen OpenBot</h3>${state.waiting.map((p) => `<p class="note ${p.status === "incomplete" ? "warn" : ""}"><b>${esc(p.item_name)}</b> · ${esc(STATUS_LABEL[p.status])} · ${esc(ago(p.applied_at))}</p>`).join("")}` : ""}</div>`;
  document.querySelectorAll(".pending-item").forEach((el) => wirePlan(el, state.pending.find((p) => p.id === el.dataset.pid)));
  const sh = $("#sheet"); sh.classList.remove("closing"); sh.hidden = false; document.body.style.overflow = "hidden";
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
    const r = await fetch("/api/install", { method: "POST", headers: API_HEADERS,
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
    pollStatus();
  } finally { topbar(false); }
}

function showUpdated(lr) {
  $("#updated").textContent = lr ? `Diperbarui ${new Date(lr.at).toLocaleString("id-ID", { dateStyle: "medium", timeStyle: "short" })}` : "Belum ada data";
}

let syncTimer = null, wasRunning = false, lastStatus = null;
const hhmm = (iso) => new Date(iso).toLocaleTimeString("id-ID", { hour: "2-digit", minute: "2-digit" });
const mins = (sec) => (sec < 90 ? "±1 menit" : `±${Math.round(sec / 60)} menit`);

// /api/status -> running banner, warnings, failure banner, summary and the "Perbarui sekarang" button
function showStatus(j) {
  const prev = lastStatus;
  lastStatus = j;
  const p = j.progress || {};
  const running = !!j.running;
  const box = $("#sync");
  box.hidden = !running;
  box.classList.toggle("first", running && !j.items);
  if (running) {
    $("#sync-label").textContent = !j.items ? "Katalog pertama sedang diambil" : (p.label || "Memperbarui katalog");
    const parts = [];
    if (!j.items && p.label) parts.push(p.label);
    if (p.total) parts.push(`${p.done || 0}/${p.total}`);
    if (j.started_at) parts.push(`mulai ${hhmm(j.started_at)}`);
    if (j.eta_seconds) parts.push(`perkiraan ${mins(j.eta_seconds)} lagi`);
    if (p.note) parts.push(p.note);
    $("#sync-sub").textContent = parts.join(" · ");
    const step = Math.max(p.step || 0, 0);
    const frac = (Math.max(step - 1, 0) + (p.total ? (p.done || 0) / p.total : 0.5)) / (p.steps || 4);
    box.classList.toggle("indet", !p.total);
    $("#sync-fill").style.setProperty("--p", Math.max(0.04, Math.min(1, frac)).toFixed(3));
  }
  // notes when idle: failure (red) first, then warnings (yellow)
  const note = $("#cnote");
  const msgs = [];
  if (!running && j.last_error) msgs.push(["bad", `Pembaruan katalog gagal ${when(j.last_error.at)}: ${esc(j.last_error.message)}`]);
  else if (!running && p.stale) msgs.push(["bad", esc(p.label)]);
  if (!running && !j.items) msgs.push(["warn", "Belum ada data katalog. Data diambil saat agen pertama kali memakainya, atau klik <b>Perbarui sekarang</b>."]);
  const warns = [];
  if (j.unchecked) warns.push(`${j.unchecked} item belum diperiksa (kuota API GitHub habis)`);
  if (j.summary && (j.summary.quota_exhausted || []).length) warns.push(`kuota API GitHub habis saat pembaruan terakhir`);
  if (!j.token_active) warns.push("tanpa token GitHub (batas 60 request/jam)");
  if (warns.length) msgs.push(["warn", warns.join(" · ") + ` <button class="pill" data-open-settings>Pasang token</button>`]);
  note.hidden = !msgs.length;
  note.innerHTML = msgs.map(([cls, m]) => `<p class="${cls}">${m}</p>`).join("");
  note.querySelectorAll("[data-open-settings]").forEach((b) => (b.onclick = openSettings));
  const s = j.summary;
  $("#summary-line").textContent = s && s.at ? `Katalog diperbarui ${when(s.at)} · ${s.items ?? j.items} item${s.seconds ? ` · ${Math.round(s.seconds / 60) || 1} menit` : ""}` : "";
  const btn = $("#refresh-now");
  btn.hidden = !j.actions_allowed;
  btn.disabled = running || j.cooldown_left > 0;
  btn.title = running ? "Pembaruan sedang berjalan" : j.cooldown_left > 0 ? `Bisa lagi dalam ${Math.ceil(j.cooldown_left / 60)} menit` : "Ambil data terbaru dari GitHub sekarang";
  // a finished run: only a real new last_run is "updated"; a failure says so instead
  if (wasRunning && !running) {
    if (j.last_error) toast("Pembaruan katalog gagal, lihat pesan merah");
    else if (j.last_run && (!prev || !prev.last_run || prev.last_run.at !== j.last_run.at)) { toast("Data katalog sudah diperbarui"); load(true); }
    else toast("Pembaruan katalog berhenti");
  } else if (!running && prev && j.last_run && prev.last_run && prev.last_run.at !== j.last_run.at) { load(true); }
  wasRunning = running;
  clearTimeout(syncTimer);
  syncTimer = setTimeout(pollStatus, running ? 4000 : 20000);
}

async function pollStatus() {
  if (document.hidden) { clearTimeout(syncTimer); syncTimer = setTimeout(pollStatus, 15000); return; }
  try {
    const r = await fetch("/api/status");
    if (r.status === 401) { location.href = "/login"; return; }
    showStatus(await r.json());
  } catch { clearTimeout(syncTimer); syncTimer = setTimeout(pollStatus, 15000); }
}

$("#refresh-now").addEventListener("click", async (ev) => {
  if (lastStatus && !lastStatus.token_active && !confirm("Tanpa token GitHub, kuota hanya 60 request/jam dan item yang belum diperiksa bisa bertambah. Tetap perbarui?")) return;
  ev.target.disabled = true;
  try { const r = await api("/api/refresh", {}); toast(r.warning ? "Pembaruan dimulai · " + r.warning : "Pembaruan katalog dimulai"); }
  catch (e) { toast("Gagal: " + e.message); }
  setTimeout(pollStatus, 800);
});

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
["#cat", "#sort", "#f-install", "#f-safe", "#f-installed", "#f-unchecked"].forEach((s) => $(s).addEventListener("change", () => render()));
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
loadHarnesses();
refreshPlans(); setInterval(() => { if (!document.hidden) refreshPlans(); }, 15000);
$("#pending").addEventListener("click", openPending);
$("#settings").addEventListener("click", openSettings);

// ---------------------------------------------------------------- security page
// Only shows status, logs out every device, and changes an existing hashed password.
// Creating or resetting the password and anything about the PIN happen in the terminal only.
const when = (iso) => (iso ? new Date(iso).toLocaleString("id-ID", { dateStyle: "medium", timeStyle: "short" }) : "-");
// ---------------------------------------------------------------- settings: GitHub token + security
// The token is only ever sent in a POST body, never shown again; the page only gets its kind and last 4.
const SRC_NAME = { env: "variabel GITHUB_TOKEN", env_file: "variabel GITHUB_TOKEN_FILE", managed: "disimpan di sini / set-token", config: "token_file di config.toml" };
function tokenHtml(t) {
  if (t.error) return `<h2>Pengaturan</h2><h3>Token GitHub</h3><p class="note warn">${esc(t.error)}</p>`;
  const chk = t.last_check || {};
  const exp = chk.expires ? when(chk.expires) : chk.expires_raw ? esc(chk.expires_raw) : null;
  const status = t.active
    ? `Aktif dari <b>${esc(SRC_NAME[t.active] || t.active)}</b> · ${esc(t.kind || "?")} · …${esc(t.last4 || "?")}${exp ? ` · kedaluwarsa ${exp}` : ""}${chk.core ? ` · batas ${chk.core.limit}/jam` : ""}`
    : "Belum ada token. Tanpa token, GitHub hanya memberi 60 request/jam, jadi sebagian item bisa belum diperiksa.";
  const managed = t.sources.find((x) => x.source === "managed");
  const shadow = managed && managed.shadowed ? `<p class="note warn">Token yang disimpan di sini <b>tidak dipakai</b>, karena ${esc(SRC_NAME[t.active])} menimpanya.</p>` : "";
  const scopes = chk.scopes && chk.scopes.length ? `<p class="note warn">Token classic ini punya scope ${esc(chk.scopes.join(", "))}; Agent Loadout tidak butuh scope apa pun.</p>` : "";
  return `<h2>Pengaturan</h2><h3>Token GitHub</h3><p class="note">${status}</p>${shadow}${scopes}
    <ul class="changes">${t.sources.map((x) => `<li><b>${x.active ? "✓" : x.present ? "·" : "–"} ${esc(SRC_NAME[x.source] || x.source)}</b><span>${x.active ? "dipakai" : x.shadowed ? "ada, tertimpa" : x.present ? "ada" : "kosong"}</span></li>`).join("")}</ul>
    <p class="note">Urutan: variabel GITHUB_TOKEN → GITHUB_TOKEN_FILE → disimpan di sini → token_file config.</p>
    <form class="tokform"><input type="password" name="token" autocomplete="off" spellcheck="false" placeholder="Tempel token GitHub (ghp_… atau github_pat_…)">
      <div class="actions"><button class="btn primary" type="submit" data-act="save">Simpan & uji</button><button class="btn ghost" type="button" data-act="test">Uji</button>${managed && managed.present ? '<button class="btn ghost" type="button" data-act="clear">Hapus</button>' : ""}</div></form>
    <div class="tok-res"></div>
    <p class="note">Saran: <a href="${esc(t.hint.url)}" target="_blank" rel="noopener noreferrer">token fine-grained</a>, Repository access = <b>Public repositories (read-only)</b>, tanpa izin akun, kedaluwarsa 90 hari. Alternatif: <a href="${esc(t.hint.classic_url)}" target="_blank" rel="noopener noreferrer">token classic</a> tanpa scope. Jangan pernah menempel token di chat agen.</p>`;
}

function wireToken(root) {
  const f = root.querySelector(".tokform");
  if (!f) return;
  const out = root.querySelector(".tok-res");
  const show = (r) => {
    const t = r.test || {};
    const exp = t.expires ? when(t.expires) : t.expires_raw ? esc(t.expires_raw) : "";
    out.innerHTML = `<p class="note ${t.ok === false ? "warn" : ""}">${t.ok ? `OK · batas ${t.core ? t.core.limit : "?"}/jam, sisa ${t.core ? t.core.remaining : "?"}${exp ? " · kedaluwarsa " + exp : ""}${t.kind ? " · " + esc(t.kind) + " …" + esc(t.last4) : " · tanpa token"}` : esc(t.error || "")}</p>${t.warning ? `<p class="note warn">${esc(t.warning)}</p>` : ""}`;
  };
  const go = async (act) => {
    const token = f.token.value.trim();
    f.token.value = "";
    try {
      if (act === "clear") { const r = await api("/api/settings/token/clear", {}); toast(r.note); return openSettings(); }
      if (act === "save" && !token) return toast("Tempel token dulu");
      const r = await api(act === "save" ? "/api/settings/token" : "/api/settings/token/test", token ? { token } : {});
      if (r.saved) { toast("Token disimpan"); await openSettings(); show(r); pollStatus(); } else show(r);
    } catch (e) { out.innerHTML = `<p class="note warn">${esc(e.message)}</p>`; }
  };
  f.onsubmit = (ev) => { ev.preventDefault(); go("save"); };
  f.querySelectorAll("button[type=button]").forEach((b) => (b.onclick = () => go(b.dataset.act)));
}

async function openSettings() {
  let s, t;
  try {
    s = await (await fetch("/api/security")).json();
    const tr = await fetch("/api/settings/token");
    t = await tr.json();
  } catch (e) { return toast("Gagal: " + e.message); }
  const sec = securityHtml(s);
  $("#detail").innerHTML = `<div class="detail security">${tokenHtml(t)}${sec}</div>`;
  wireToken($("#detail"));
  wireSecurity();
  const sh = $("#sheet"); sh.classList.remove("closing"); sh.hidden = false; document.body.style.overflow = "hidden";
}

function securityHtml(s) {
  const pw = s.password || {}, pin = s.pin || {};
  const pwText = { hashed: `Aktif (hash), dipasang ${esc(when(pw.set_at))} oleh ${esc(pw.set_by || "-")}${pw.reset_at ? ` · direset ${esc(when(pw.reset_at))}` : ""}`,
    legacy_file: "Aktif dari file teks (password_file). Jalankan <code>agentloadout set-password</code> di laptop untuk menyimpannya sebagai hash.",
    off: "Belum ada. Web hanya bisa dibuka di laptop ini. Untuk HP: <code>agentloadout set-password</code> di terminal laptop." }[pw.source] || "-";
  const pinText = !pin.set ? "Belum diset: <code>agentloadout set-pin</code> di terminal laptop."
    : pin.invalid ? "Catatan PIN rusak, persetujuan terkunci: <code>agentloadout set-pin --reset</code>."
    : `Diset ${esc(when(pin.set_at))} oleh ${esc(pin.set_by || "-")}${pin.reset_at ? ` · <b>direset ${esc(when(pin.reset_at))}</b>` : ""}${pin.locked ? " · terkunci sementara (5× salah)" : ""}`;
  return `<h2 class="sec-h">Keamanan</h2>
    <p class="lead">Akses ini: ${s.scope === "local" ? "dari laptop ini" : "dari luar laptop"}${s.https ? " (HTTPS)" : ""}${s.funnel ? " · lewat Funnel (hanya lihat)" : ""}.</p>
    <h3>Password web</h3><p class="note">${pwText}</p>
    ${pw.source === "hashed" && s.actions_allowed ? `<form class="pwform"><input type="password" name="old" autocomplete="current-password" placeholder="Password lama" required>
      <input type="password" name="new" autocomplete="new-password" placeholder="Password baru (10–128 karakter)" minlength="10" maxlength="128" required>
      <input type="password" name="new2" autocomplete="new-password" placeholder="Ulangi password baru" required>
      <div class="actions"><button class="btn primary" type="submit">Ganti password</button></div></form>` : ""}
    ${pw.enabled && s.actions_allowed ? `<div class="actions"><button class="btn ghost" data-logoutall>Keluar dari semua perangkat</button></div>` : ""}
    <p class="note">Lupa password? Di terminal laptop: <code>agentloadout set-password --reset</code>.</p>
    <h3>PIN persetujuan</h3><p class="note">${pinText}</p>
    <p class="note">PIN hanya bisa dibuat, diganti, atau direset di terminal laptop (<code>agentloadout set-pin</code>, <code>set-pin --reset</code>). Jangan berikan PIN ke agen.</p>`;
}

function wireSecurity() {
  const f = $("#detail .pwform");
  if (f) f.onsubmit = async (ev) => {
    ev.preventDefault();
    const d = Object.fromEntries(new FormData(f));
    if (d.new !== d.new2) return toast("Password baru tidak sama");
    try { const r = await api("/api/security/password", { old: d.old, new: d.new }); f.reset(); toast(r.note || "Password diganti"); }
    catch (e) { f.reset(); toast("Gagal: " + e.message); }
  };
  const lo = $("#detail [data-logoutall]");
  if (lo) lo.onclick = async () => { try { const r = await api("/api/security/logout-all"); toast(r.note || "Selesai"); } catch (e) { toast("Gagal: " + e.message); } };
}
load().catch((e) => { $("#updated").textContent = "Gagal memuat: " + e.message; $("#grid").innerHTML = '<p class="empty">Gagal memuat data. Coba muat ulang.</p>'; });
