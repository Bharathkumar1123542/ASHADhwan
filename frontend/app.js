"use strict";
/* ASHADhwani — Voice Field Register. Live frontend: real backend, real data. */

const $ = (s) => document.querySelector(s);
const DAY = 864e5;
const TODAY = (() => { const d = new Date(); d.setHours(0, 0, 0, 0); return d; })();
const pad = (n) => String(n).padStart(2, "0");
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const parseDay = (iso) => { const [y, m, d] = String(iso).split("-").map(Number); return new Date(y, (m || 1) - 1, d || 1); };
const dayDiff = (iso) => Math.round((parseDay(iso) - TODAY) / DAY);
const fmtD = (d) => d.toLocaleDateString("en-IN", { day: "numeric", month: "short" });
const fmtDY = (d) => d.toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" });
const fmtT = (d) => d.toLocaleTimeString("en-IN", { hour: "numeric", minute: "2-digit" });
const rel = (n) => (n === 0 ? "Today" : n === 1 ? "Tomorrow" : n === -1 ? "Yesterday" : fmtD(new Date(+TODAY + n * DAY)));
const sameDay = (a, b) => a.toDateString() === b.toDateString();
const cap = (s) => (s ? s.charAt(0).toUpperCase() + s.slice(1) : s);
const store = {
  get(k) { try { return localStorage.getItem(k); } catch (_) { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch (_) {} },
};

const LANG = { en: "English", hi: "हिन्दी", kn: "ಕನ್ನಡ" };
const SR_LANG = { en: "en-IN", hi: "hi-IN", kn: "kn-IN" };
const VT = { pregnancy_anc: "ANC checkup", newborn_postnatal: "Newborn / postnatal", immunisation: "Immunisation" };
const FIELDS = [
  ["beneficiary_id", "beneficiary ID"], ["beneficiary_name", "name"], ["visit_type", "visit type"],
  ["weight_kg", "weight"], ["vaccinations_due", "vaccinations due"], ["next_visit_date", "next visit date"],
];
const STATE_LABEL = { idle: "Idle", collecting: "Collecting", confirming: "Awaiting confirmation", saved: "Saved" };

/* ---------------- state ---------------- */
const S = {
  view: "dash", workers: [], worker: null, records: [], locale: {},
  bq: "", vq: "", rtype: "", rb: "", rd: "all", ftab: "due",
  muted: store.get("asha.muted") === "1", sr: store.get("asha.sr") || "", dangers: [],
};
const defaultsV = () => ({
  active: false, state: "idle", ws: null, benef: null, formVersion: 0, display: {}, missing: [], changed: [],
  prompt: "", promptKind: "", transcript: "", interim: false, saved: null, queue: [], reconnects: 0,
  micWanted: false, micFailed: false, rec: null, recRunning: false, speaking: 0,
});
const V = defaultsV();

const t = (k, fb) => (typeof S.locale[k] === "string" ? S.locale[k] : fb);
const vtLabel = (vt) => t("visit_type_label_" + vt, VT[vt] || vt || "");
const initials = (n) => String(n || "?").split(/\s+/).map((w) => w[0]).slice(0, 2).join("").toUpperCase();

/* ---------------- data helpers ---------------- */
async function api(path, opts) {
  const r = await fetch(path, opts);
  if (!r.ok) throw new Error(r.status);
  return r.json();
}
function benefs() {
  const m = new Map();
  for (const v of S.records) {            // records arrive newest first
    let b = m.get(v.beneficiary_id);
    if (!b) {
      b = { id: v.beneficiary_id, name: v.beneficiary_name, vt: v.visit_type, last: new Date(v.confirmed_at),
            next: v.next_visit_date, weight: v.weight_kg, visits: 0, danger: false };
      m.set(v.beneficiary_id, b);
    }
    b.visits++;
    if (v.danger_sign_flag) b.danger = true;
  }
  return [...m.values()];
}
const allFus = () => S.records.flatMap((v) => (v.follow_ups || []).map((f) => ({ ...f, b: v.beneficiary_id, name: v.beneficiary_name, visit_id: v.visit_id })));
const fuDiff = (f) => (f.due_date ? dayDiff(f.due_date) : 0);
const isRef = (f) => f.type === "phc_referral";

/* ---------------- small UI pieces ---------------- */
const pill = (txt, k) => `<span class="pill ${k || ""}">${esc(txt)}</span>`;
const avatar = (n, lg) => `<span class="av${lg ? " lg" : ""}">${esc(initials(n))}</span>`;
const empty = (txt) => `<div class="empty">${txt}</div>`;
const head = (title, sub, btn) => `<div class="head"><div><h1>${title}</h1><p>${sub || ""}</p></div>${btn || ""}</div>`;
const voiceBtn = (id, cls, label) => `<button class="btn ${cls || "pri"}" data-act="vstart" data-id="${esc(id || "")}">🎙 ${label || (id ? "Voice entry" : "Start voice entry")}</button>`;
const stepsInner = (s) => ["Choose", "Speak", "Confirm", "Saved"].map((l, i) =>
  `<div class="st ${s === i + 1 ? "cur" : s > i + 1 ? "done" : ""}"><b>${s > i + 1 ? "✓" : i + 1}</b><span>${l}</span></div>`).join("");

function fuCard(f, acts) {
  const diff = fuDiff(f), late = !f.done && diff < 0;
  const cls = f.done ? "d" : isRef(f) ? "u" : "n";
  const id = esc(f.follow_up_id);
  const btns = f.done
    ? `<button class="btn sm" data-act="fundo" data-id="${id}">Undo</button>`
    : `<button class="btn sm pri" data-act="fdone" data-id="${id}">✓ Done</button><button class="btn sm" data-act="fresch" data-id="${id}">Reschedule +2d</button>`;
  return `<div class="al ${cls}"><div><b>${esc(f.description)}</b><small>${esc(f.name)} · ${esc(f.b)}</small>
    <small>Due: ${rel(diff)}${late ? " (overdue)" : ""} · ${f.done ? "Completed" : isRef(f) ? "Urgent" : "Reminder"}</small></div>
    ${acts ? `<div class="acts">${btns}</div>` : ""}</div>`;
}

function recRow(v) {
  const d = new Date(v.confirmed_at);
  const vacc = (v.vaccinations_due || []).length ? v.vaccinations_due.join(", ") : "None";
  return `<div class="item">${avatar(v.beneficiary_name)}<div class="grow"><b>${esc(v.beneficiary_name)}</b> <small>${esc(v.beneficiary_id)}</small>
    <small>${fmtDY(d)}, ${fmtT(d)} · Weight ${esc(v.weight_kg)} kg · Vaccinations: ${esc(vacc)} · Next visit ${fmtD(parseDay(v.next_visit_date))}</small></div>
    ${pill(vtLabel(v.visit_type), "t")}${v.danger_sign_flag ? pill("PHC referral " + (v.danger_sign_codes || []).join(", "), "u") : ""}</div>`;
}

/* ---------------- views ---------------- */
const VIEWS = {
  dash() {
    const h = new Date().getHours(), hi = h < 12 ? "Good morning" : h < 17 ? "Good afternoon" : "Good evening";
    const fus = allFus(), open = fus.filter((f) => !f.done);
    const due = open.filter((f) => fuDiff(f) <= 2).sort((a, b) => Number(isRef(b)) - Number(isRef(a)) || fuDiff(a) - fuDiff(b));
    const refs = open.filter(isRef);
    const todayN = S.records.filter((v) => sameDay(new Date(v.confirmed_at), TODAY)).length;
    const st = (n, l, v, cls) => `<button class="stat ${cls || ""}" data-act="go" data-id="${v}"><b>${n}</b><small>${l}</small></button>`;
    return `${head(`${hi}, ${esc(S.worker.display_name)}`, new Date().toLocaleDateString("en-IN", { weekday: "long", day: "numeric", month: "long", year: "numeric" }), voiceBtn("", "pri big"))}
    <div class="grid g5">${st(todayN, "Visits today", "records")}${st(benefs().length, "Beneficiaries", "benef")}${st(due.length, "Follow-ups due", "follow")}${st(refs.length, "Open PHC referrals", "follow", refs.length ? "red" : "")}${st(S.records.length, "Records saved", "records")}</div>
    ${refs.length ? `<div class="card"><h2>Open PHC referrals</h2>${refs.map((f) => fuCard(f, true)).join("")}</div>` : ""}
    <div class="grid g2"><div class="card"><div class="rowb"><h2>Follow-ups due</h2><button class="btn sm" data-act="go" data-id="follow">View all</button></div>
      ${due.filter((f) => !isRef(f)).length ? due.filter((f) => !isRef(f)).slice(0, 4).map((f) => fuCard(f, false)).join("") : empty("Nothing due. Well done!")}</div>
    <div class="card"><div class="rowb"><h2>Recently saved</h2><button class="btn sm" data-act="go" data-id="records">View all</button></div>
      ${S.records.length ? S.records.slice(0, 4).map(recRow).join("") : empty("No records yet. Start a voice entry.")}</div></div>`;
  },

  voice() { return V.active ? voiceSkeleton() : voiceChoose(); },

  benef() {
    if (S.bsel) { const b = benefs().find((x) => x.id === S.bsel); if (b) return profile(b); S.bsel = null; }
    const q = S.bq.toLowerCase(), all = benefs();
    const rows = all.filter((b) => !q || (b.name + b.id).toLowerCase().includes(q));
    const openIds = new Set(allFus().filter((f) => !f.done).map((f) => f.b));
    return `${head("Beneficiaries", `${rows.length} of ${all.length} shown`, voiceBtn("", "pri"))}
    <div class="card"><input id="bq" data-s="bq" placeholder="Search by name or ID…" value="${esc(S.bq)}">
    ${rows.length ? rows.map((b) => `<div class="item click" data-act="bopen" data-id="${esc(b.id)}">${avatar(b.name)}<div class="grow"><b>${esc(b.name)}</b>
      <small>${esc(b.id)} · ${esc(vtLabel(b.vt))} · Last visit ${rel(Math.round((new Date(b.last).setHours(0, 0, 0, 0) - TODAY) / DAY))}</small></div>
      ${b.danger ? pill("PHC referral", "u") : ""}${openIds.has(b.id) ? pill("Follow-up open", "a") : pill("Up to date", "g")}</div>`).join("")
      : empty(all.length ? "No beneficiaries match." : "No beneficiaries yet. They appear here once you save a visit by voice.")}</div>`;
  },

  records() {
    const lim = { all: 99999, 7: 7, 30: 30 }[S.rd];
    const rows = S.records.filter((v) => {
      const age = Math.floor((TODAY - new Date(v.confirmed_at).setHours(0, 0, 0, 0)) / DAY);
      return (!S.rtype || v.visit_type === S.rtype) && (!S.rb || v.beneficiary_id === S.rb) && age <= lim;
    });
    return `${head("Records", `${rows.length} saved visit records`)}
    <div class="card"><div class="chips">${["", ...Object.keys(VT)].map((k) => `<button class="chip${S.rtype === k ? " on" : ""}" data-act="rtype" data-id="${k}">${k ? esc(vtLabel(k)) : "All"}</button>`).join("")}</div>
    <div class="grid g2"><div><label>Beneficiary</label><select data-sel="rb"><option value="">All</option>${benefs().map((b) => `<option value="${esc(b.id)}"${S.rb === b.id ? " selected" : ""}>${esc(b.name)}</option>`).join("")}</select></div>
    <div><label>Date</label><select data-sel="rd">${[["all", "All time"], ["7", "Last 7 days"], ["30", "Last 30 days"]].map(([v, l]) => `<option value="${v}"${S.rd === v ? " selected" : ""}>${l}</option>`).join("")}</select></div></div></div>
    <div class="card">${rows.length ? rows.map(recRow).join("") : empty("No records match.")}</div>`;
  },

  follow() {
    const tabs = { due: "Due today", over: "Overdue", up: "Upcoming", done: "Completed" };
    const match = (f, k) => (k === "done" ? f.done : !f.done && (k === "due" ? fuDiff(f) === 0 : k === "over" ? fuDiff(f) < 0 : fuDiff(f) > 0));
    const all = allFus();
    const list = all.filter((f) => match(f, S.ftab)).sort((a, b) => Number(isRef(b)) - Number(isRef(a)) || fuDiff(a) - fuDiff(b));
    return `${head("Follow-ups", "Created automatically when a record is saved")}
    <div class="card"><div class="chips">${Object.entries(tabs).map(([k, l]) => `<button class="chip${S.ftab === k ? " on" : ""}" data-act="ftab" data-id="${k}">${l} (${all.filter((f) => match(f, k)).length})</button>`).join("")}</div>
    ${list.length ? list.map((f) => fuCard(f, true)).join("") : empty("Nothing in this list.")}</div>`;
  },

  reports() {
    const R = S.records, fus = allFus();
    const weeks = [0, 0, 0, 0, 0, 0];
    for (const v of R) {
      const d = new Date(v.confirmed_at); d.setHours(0, 0, 0, 0);
      const w = Math.floor((TODAY - d) / DAY / 7);
      if (w >= 0 && w < 6) weeks[5 - w]++;
    }
    const keys = Object.keys(VT), types = keys.map((k) => R.filter((v) => v.visit_type === k).length);
    const done = fus.filter((f) => f.done).length, rate = fus.length ? Math.round((done / fus.length) * 100) : 0;
    const refs = R.filter((v) => v.danger_sign_flag).length;
    const bars = (arr, lab) => { const m = Math.max(1, ...arr); return `<div class="bars">${arr.map((v, i) => `<div>${v}<i style="height:${(v / m) * 90}px"></i>${esc(lab[i])}</div>`).join("")}</div>`; };
    return `${head("Reports", "Computed from the records saved on this register")}
    <div class="grid g2"><div class="card"><h2>Visits per week</h2>${bars(weeks, ["5w", "4w", "3w", "2w", "Last", "This"])}</div>
    <div class="card"><h2>Visits by type</h2>${bars(types, keys.map((k) => vtLabel(k).split(" ")[0]))}</div>
    <div class="card"><h2>Follow-up completion</h2><span class="big-n">${rate}%</span><div class="meter"><i style="width:${rate}%"></i></div><small>${done} of ${fus.length} follow-ups completed</small></div>
    <div class="card"><h2>PHC referrals raised</h2><span class="big-n">${refs}</span><small>visit records with DS-01 or DS-02 flagged</small></div>
    <div class="card"><h2>Records saved</h2><span class="big-n">${R.length}</span><small>${benefs().length} beneficiaries</small></div></div>`;
  },

  settings() {
    const w = S.worker;
    return `${head("Settings")}
    <div class="card"><h2>Worker profile</h2><div class="kv"><span>Name</span><span>${esc(w.display_name)}</span><span>Language</span><span>${esc(LANG[w.language] || w.language)}</span><span>Worker ID</span><span>${esc(w.worker_id)}</span></div>
    <p class="mut">Switch worker from the selector at the top. The assistant speaks in the worker's language.</p></div>
    <div class="card"><h2>Speech recognition language</h2><div class="chips">${[["", "Auto (worker's language)"], ["en", "English"], ["hi", "हिन्दी"], ["kn", "ಕನ್ನಡ"]].map(([k, l]) => `<button class="chip${S.sr === k ? " on" : ""}" data-act="setsr" data-id="${k}">${l}</button>`).join("")}</div></div>
    <div class="card"><h2>Spoken read-back</h2><div class="chips"><button class="chip${S.muted ? "" : " on"}" data-act="setmute" data-id="0">On</button><button class="chip${S.muted ? " on" : ""}" data-act="setmute" data-id="1">Off</button></div></div>
    <div class="card"><h2>How this register stays safe</h2><ul class="safe">
      <li>Nothing is saved until you confirm the current record.</li>
      <li>All spoken replies come from fixed language templates.</li>
      <li>Danger signs DS-01 and DS-02 raise an immediate PHC referral alert.</li>
      <li>The app records and refers only. It never gives diagnosis, treatment or medical advice.</li></ul></div>`;
  },
};

function profile(b) {
  const vs = S.records.filter((v) => v.beneficiary_id === b.id);
  const fus = allFus().filter((f) => f.b === b.id && !f.done);
  return `<button class="btn link" data-act="bback">← All beneficiaries</button>
  <div class="card"><div class="bhead">${avatar(b.name, true)}<div class="grow"><h1>${esc(b.name)}</h1><small>${esc(b.id)} · ${esc(vtLabel(b.vt))}</small></div>${voiceBtn(b.id)}</div></div>
  <div class="grid g2"><div class="card"><h2>Summary</h2><div class="kv"><span>Visits recorded</span><span>${b.visits}</span><span>Last visit</span><span>${fmtDY(b.last)}</span><span>Latest weight</span><span>${esc(b.weight)} kg</span><span>Next visit</span><span>${fmtDY(parseDay(b.next))}</span></div></div>
  <div class="card"><h2>Open follow-ups</h2>${fus.length ? fus.map((f) => fuCard(f, true)).join("") : empty("No open follow-ups.")}</div>
  <div class="card span2"><h2>Visit history</h2>${vs.map(recRow).join("")}</div></div>`;
}

function voiceChoose() {
  const q = S.vq.toLowerCase(), rows = benefs().filter((b) => !q || (b.name + b.id).toLowerCase().includes(q));
  return `${head("Voice entry", "Who are you visiting?")}<div class="steps">${stepsInner(1)}</div>
  <div class="card"><h2>New beneficiary</h2><p class="mut">Speak the ID, name and visit details. The assistant asks for anything missing.</p>${voiceBtn("", "pri big")}</div>
  <div class="card"><h2>Existing beneficiary</h2><input id="vq" data-s="vq" placeholder="Search by name or ID…" value="${esc(S.vq)}">
  ${rows.length ? rows.map((b) => `<div class="item click" data-act="vstart" data-id="${esc(b.id)}">${avatar(b.name)}<div class="grow"><b>${esc(b.name)}</b><small>${esc(b.id)} · ${esc(vtLabel(b.vt))}</small></div>${pill("Pre-fills ID & name", "t")}</div>`).join("") : empty("No saved beneficiaries yet.")}</div>`;
}

function voiceSkeleton() {
  return `${head('<span id="vTitle">Voice entry</span>', '<span id="vSub"></span>', `<button class="btn sm" data-act="vcancel">✕ End visit</button>`)}
  <div id="vSteps" class="steps"></div>
  <div id="vWho" class="card"></div>
  <div class="g2v">
    <div>
      <div class="card"><div class="arow"><div class="avatar">AI</div><div class="grow"><label style="margin:0 0 4px">ASSISTANT</label>
        <div id="vWave" class="wave">${Array.from({ length: 9 }, (_, i) => `<i style="--d:${i}"></i>`).join("")}</div></div><span id="vPill" class="pill"></span></div>
        <p id="vPrompt" class="prompt"></p><div class="meter"><i id="vProg"></i></div><small id="vProgText"></small></div>
      <div class="card reccard">
        <button id="vMic" class="mic" type="button" data-act="vmic" aria-label="Microphone">🎙</button>
        <div id="vMicStatus" class="micstat"></div>
        <div id="vHeard" class="heard"></div>
        <form id="vForm" class="note-form" autocomplete="off"><input id="vInput" placeholder="Type a note or correction…"><button class="btn pri" type="submit">Send</button></form>
        <div class="muterow"><button class="btn sm" type="button" data-act="setmute" data-id="${S.muted ? "0" : "1"}">${S.muted ? "🔇 Voice off" : "🔊 Voice on"}</button></div>
      </div>
    </div>
    <div>
      <div class="card"><div class="rowb"><h2 id="vRecTitle">Visit record</h2><span id="vSaved" class="pill g hidden">Saved ✓</span></div>
        <div id="vFields" class="fields"></div>
        <div id="vActions"><div class="actions"><button id="vConfirm" class="btn pri" type="button" data-act="vconfirm" disabled>✓ Confirm &amp; save</button>
          <button id="vCorrect" class="btn" type="button" data-act="vcorrect" disabled>✎ Correct</button></div>
          <button id="vDiscard" class="btn link" style="width:100%;margin-top:6px" type="button" data-act="vdiscard" disabled>Discard this record</button></div>
        <div id="vSavedBar" class="actions hidden"><button class="btn pri" data-act="vnew">+ New voice entry</button><button class="btn" data-act="go" data-id="records">View records</button></div></div>
      <div class="card"><h2>Follow-ups &amp; next actions</h2><div id="vFu"></div></div>
    </div>
  </div>
  <details class="card demo"><summary>Demo shortcuts (same pipeline as voice)</summary><div class="demo-grid">
    <button class="btn sm" type="button" data-say="ID AB12346 Sunita Devi ANC checkup weight 58 kilo TT due next visit 28 December">Full note</button>
    <button class="btn sm" type="button" data-say="yes">Say “yes”</button>
    <button class="btn sm" type="button" data-say="No, weight is 62 kilo">Fix weight</button>
    <button class="btn sm dng" type="button" data-say="Patient has bleeding in pregnancy">Danger DS-01</button>
    <button class="btn sm dng" type="button" data-say="Newborn baby has high fever">Danger DS-02</button>
    <button class="btn sm" type="button" data-say="discard">Discard</button></div></details>`;
}

/* ---------------- shell ---------------- */
const NAV = [["dash", "⌂", "Dashboard"], ["voice", "🎙", "Voice Entry"], ["benef", "☺", "Beneficiaries"], ["records", "▤", "Records"], ["follow", "⏰", "Follow-ups"], ["reports", "▥", "Reports"], ["settings", "⚙", "Settings"]];

function chrome() {
  const urgent = allFus().filter((f) => !f.done && isRef(f)).length;
  $("#nav").innerHTML = NAV.map(([k, i, l]) =>
    `<button class="${S.view === k ? "on" : ""}" data-act="go" data-id="${k}"><i>${i}</i>${l}${k === "follow" && urgent ? `<em>${urgent}</em>` : ""}</button>`).join("");
}
function render() {
  chrome();
  if (!S.worker) { $("#page").innerHTML = head("ASHADhwani", "Connecting to the server…"); return; }
  $("#page").innerHTML = VIEWS[S.view]();
  if (S.view === "voice" && V.active) paintVoice();
}
const renderSafe = () => { if (!(S.view === "voice" && V.active)) render(); };

function go(v) {
  if (!VIEWS[v]) return;
  if (S.view === "voice" && v !== "voice") leaveVoice();
  const entering = v === "voice" && S.view !== "voice";
  S.view = v;
  if (v !== "benef") S.bsel = null;
  if (location.hash.slice(1) !== v) location.hash = v;
  render();
  window.scrollTo(0, 0);
  if (entering) enterVoice();
}

let toastTimer;
function toast(msg) {
  const e = $("#toast"); e.textContent = msg; e.classList.remove("hidden");
  clearTimeout(toastTimer); toastTimer = setTimeout(() => e.classList.add("hidden"), 2200);
}
function setNotice(msg) { const n = $("#notice"); n.textContent = msg || ""; n.classList.toggle("hidden", !msg); }
function setConn(on) { $("#connDot").classList.toggle("on", Boolean(on)); }

/* ---------------- danger banner (server text only) ---------------- */
function paintDanger() {
  $("#dangerBanner").classList.toggle("hidden", !S.dangers.length);
  $("#dangerLines").innerHTML = S.dangers.map((d) => `<div class="danger-line">${esc(d.text)}</div>`).join("");
  const id = (S.dangers.find((d) => d.id) || {}).id;
  $("#dangerWho").textContent = id ? "Beneficiary: " + id : "";
}
function addDanger(m) {
  if (!S.dangers.some((d) => d.code === m.code)) S.dangers.push({ code: m.code, text: m.text, id: m.beneficiary_id });
  paintDanger(); paintVoice();
  window.scrollTo({ top: 0, behavior: "smooth" });
}
function clearDangers() { S.dangers = []; paintDanger(); }

/* ---------------- speech out (template text from the server only) ---------------- */
function speak(text, kind) {
  if (!text || S.muted || !("speechSynthesis" in window)) return;
  if (S.view !== "voice" && kind !== "danger") return;
  const u = new SpeechSynthesisUtterance(text);
  u.lang = SR_LANG[(S.worker || {}).language] || "en-IN";
  const vs = speechSynthesis.getVoices() || [];
  const voice = vs.find((x) => x.lang.replace("_", "-").toLowerCase().startsWith(u.lang.toLowerCase())) ||
                vs.find((x) => x.lang.toLowerCase().startsWith(u.lang.slice(0, 2)));
  if (voice) u.voice = voice;
  u.rate = 0.95;
  let fired = false;
  const done = () => {
    if (fired) return; fired = true;
    V.speaking = Math.max(0, V.speaking - 1);
    paintMic();
    if (!V.speaking) setTimeout(resumeMic, 250);
  };
  u.onend = done; u.onerror = done;
  V.speaking++;
  suspendMic(); paintMic();
  speechSynthesis.speak(u);
  setTimeout(done, 5000 + text.length * 110);           // watchdog: never leave the mic stuck
}
function cancelSpeech() {
  if ("speechSynthesis" in window) speechSynthesis.cancel();
  V.speaking = 0; paintMic();
}

/* ---------------- mic (browser STT + graceful fallback to typing) ---------------- */
function paintMic() {
  const b = $("#vMic"); if (!b) return;
  b.classList.remove("listening", "speaking", "off");
  const listening = V.micWanted && !V.micFailed && !V.speaking;
  $("#vWave").classList.toggle("on", V.speaking > 0 || listening);
  let msg;
  if (V.micFailed) { b.classList.add("off"); msg = "MIC OFF — TYPE BELOW"; }
  else if (V.speaking) { b.classList.add("speaking"); msg = "SPEAKING — tap mic to interrupt"; }
  else if (V.micWanted) { b.classList.add("listening"); msg = "LISTENING"; }
  else msg = "PAUSED — tap mic to resume";
  $("#vMicStatus").textContent = msg;
}
function safeStart() { if (!V.rec || V.recRunning) return; try { V.rec.start(); V.recRunning = true; } catch (_) {} }
function suspendMic() { if (V.rec && V.recRunning) { try { V.rec.stop(); } catch (_) {} } }
function resumeMic() { if (V.active && S.view === "voice" && V.micWanted && !V.micFailed && !V.speaking) safeStart(); }
function micFail(msg) {
  V.micFailed = true; V.micWanted = false; V.recRunning = false;
  setNotice(msg); paintMic();
  const i = $("#vInput"); if (i) i.focus();
}
function stopRec() {
  V.micWanted = false;
  if (V.rec) { V.rec.onend = null; try { V.rec.stop(); } catch (_) {} }
  V.rec = null; V.recRunning = false;
}
function initRec() {
  stopRec();
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SR) { micFail("Voice input isn't supported in this browser (use Chrome or Edge). Type below."); return; }
  const rec = new SR();
  rec.lang = SR_LANG[S.sr || (S.worker || {}).language] || "en-IN";
  rec.continuous = true; rec.interimResults = true;
  rec.onstart = () => { V.recRunning = true; };
  rec.onresult = (ev) => {
    if (V.speaking) return;                              // ignore the app's own voice
    let fin = "", interim = "";
    for (let i = ev.resultIndex; i < ev.results.length; i++) {
      const c = ev.results[i][0].transcript;
      if (ev.results[i].isFinal) fin += c + " "; else interim += c;
    }
    if (interim) { V.transcript = interim; V.interim = true; paintHeard(); }
    if (fin.trim()) sendTurn(fin.trim());
  };
  rec.onerror = (e) => {
    if (e.error === "no-speech" || e.error === "aborted") return;
    const why = {
      "not-allowed": "Microphone permission denied. Allow mic access, or type below.",
      "service-not-allowed": "Voice service blocked in this browser. Type below.",
      network: "Voice service unreachable (needs internet). Type below.",
      "audio-capture": "No microphone found. Type below.",
    };
    micFail(why[e.error] || `Voice error (${e.error}). Type below.`);
  };
  rec.onend = () => { V.recRunning = false; resumeMic(); };
  V.rec = rec; V.micFailed = false; V.micWanted = true;
  setNotice(""); safeStart(); paintMic();
}

/* ---------------- voice session over WebSocket ---------------- */
function sendTurn(text) {
  const tx = (text || "").trim();
  if (!tx || !V.active) return;
  V.transcript = tx; V.interim = false; paintHeard();
  if (V.ws && V.ws.readyState === WebSocket.OPEN) { V.ws.send(JSON.stringify({ type: "worker.turn", text: tx })); return; }
  V.queue.push(tx);
  setNotice("Reconnecting to the server…");
  if (!V.ws) connect();
}

function connect() {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  const ws = new WebSocket(`${proto}//${location.host}/ws/session`);
  V.ws = ws;
  ws.onopen = () => {
    V.reconnects = 0; setConn(true);
    const m = { type: "session.start", worker_id: S.worker.worker_id };
    if (V.benef) m.prefill = { beneficiary_id: V.benef.id, beneficiary_name: V.benef.name, visit_type: V.benef.vt };
    ws.send(JSON.stringify(m));
  };
  ws.onmessage = (e) => { let m; try { m = JSON.parse(e.data); } catch (_) { return; } onMsg(m); };
  ws.onclose = () => {
    if (V.ws !== ws) return;
    V.ws = null; setConn(false);
    if (V.active && V.state !== "saved" && V.reconnects < 5) {
      setNotice("Connection lost. Reconnecting… (the unsaved draft restarts)");
      setTimeout(() => { if (V.active && !V.ws) connect(); }, 1000 * ++V.reconnects);
    }
  };
  ws.onerror = () => {};
}

function onMsg(m) {
  switch (m.type) {
    case "session.ready":
      setNotice("");
      while (V.queue.length && V.ws && V.ws.readyState === WebSocket.OPEN) V.ws.send(JSON.stringify({ type: "worker.turn", text: V.queue.shift() }));
      break;
    case "state.change": V.state = m.state; paintVoice(); break;
    case "form.update":
      V.formVersion = m.form_version; V.display = m.display || {};
      V.missing = m.missing_fields || []; V.changed = m.changed_fields || [];
      paintVoice(); break;
    case "transcript.final": V.transcript = m.text; V.interim = false; paintHeard(); break;
    case "agent.speak.start": V.prompt = m.text; V.promptKind = m.kind; paintPrompt(); speak(m.text, m.kind); break;
    case "danger.alert": addDanger(m); break;
    case "record.saved": V.saved = m; toast("Record saved"); loadRecords(); break;
    case "visit.reset":
      clearDangers(); V.display = {}; V.missing = []; V.saved = null; V.transcript = ""; V.benef = null;
      paintVoice(); paintHeard(); break;
    case "error": setNotice(m.text || "Something went wrong."); break;
    default: break;
  }
}

const curStep = () => (!V.active ? 1 : V.state === "confirming" ? 3 : V.state === "saved" ? 4 : 2);

function paintPrompt() {
  const p = $("#vPrompt"); if (!p) return;
  p.textContent = V.prompt || "Connecting…";
  p.classList.toggle("dangerp", V.promptKind === "danger");
}
function paintHeard() {
  const h = $("#vHeard"); if (!h) return;
  const has = Boolean(V.transcript && V.transcript.trim());
  h.textContent = has ? V.transcript.trim() : "Your words appear here.";
  h.classList.toggle("has", has); h.classList.toggle("interim", has && V.interim);
}
function paintVoice() {
  if (!$("#vFields")) return;
  const st = V.state;
  $("#vSteps").innerHTML = stepsInner(curStep());
  $("#vSub").textContent = st === "confirming" ? "Check the record, then confirm" : st === "saved" ? "Record saved" : "Speak the visit details";

  const nm = V.display.beneficiary_name || "", id = V.display.beneficiary_id || "";
  $("#vWho").innerHTML = `<div class="bhead"><span class="av lg">${esc(nm ? initials(nm) : "＋")}</span><div class="grow"><h2>${esc(nm || "New beneficiary")}</h2>
    <small>${esc([id, V.display.visit_type].filter(Boolean).join(" · ") || "Speak the ID, name and visit details")}</small></div>
    ${pill(LANG[(S.worker || {}).language] || "", "t")}</div>`;

  paintPrompt();
  const pl = $("#vPill"); pl.textContent = STATE_LABEL[st] || ""; pl.className = "pill " + (st === "confirming" ? "a" : st === "saved" ? "g" : "");

  let filled = 0;
  $("#vFields").innerHTML = FIELDS.map(([k, def]) => {
    const val = V.display[k] || ""; if (val) filled++;
    const cls = ["field", val ? "filled" : "", st === "collecting" && V.missing[0] === k ? "active" : "", V.changed.includes(k) ? "flash" : ""].join(" ").replace(/\s+/g, " ");
    return `<div class="${cls}"><span class="k">${esc(cap(t("label_" + k, def)))}</span><span class="v">${esc(val) || "—"}</span></div>`;
  }).join("");
  $("#vProg").style.width = (filled / FIELDS.length) * 100 + "%";
  $("#vProgText").textContent = `${filled} of ${FIELDS.length} captured`;

  const any = filled > 0;
  $("#vConfirm").disabled = st !== "confirming";
  $("#vCorrect").disabled = !(any && st !== "saved");
  $("#vDiscard").disabled = !(any && st !== "saved");
  $("#vActions").classList.toggle("hidden", st === "saved");
  $("#vSavedBar").classList.toggle("hidden", st !== "saved");
  $("#vSaved").classList.toggle("hidden", st !== "saved");

  const items = [];
  if (st !== "saved") {
    for (const d of S.dangers) items.push(`<div class="fu urgent">${esc(d.text)}<small>Not saved yet — confirm the record to log this follow-up</small></div>`);
  } else {
    for (const f of (V.saved && V.saved.follow_ups) || []) {
      items.push(`<div class="fu${f.type === "phc_referral" ? " urgent" : ""}">${esc(f.description)}${f.due_date ? `<small>Due ${rel(dayDiff(f.due_date))}</small>` : ""}</div>`);
    }
  }
  $("#vFu").innerHTML = items.join("") || empty("Follow-ups appear here.");

  V.changed = [];                       // flash only once per change
  paintMic(); paintHeard();
}

function startVisit(b) {
  endVisit();
  Object.assign(V, defaultsV(), { active: true, state: "collecting", benef: b || null, prompt: "Connecting…" });
  connect();
  if (S.view === "voice") { render(); enterVoice(); } else go("voice");
}
function endVisit() {
  const ws = V.ws; V.ws = null;
  if (ws) { try { ws.send(JSON.stringify({ type: "session.end" })); ws.close(); } catch (_) {} }
  stopRec(); cancelSpeech();
  clearDangers();
  Object.assign(V, defaultsV());
}
function enterVoice() { if (V.active && V.state !== "saved") initRec(); paintMic(); }
function leaveVoice() { stopRec(); cancelSpeech(); }

/* ---------------- data loading ---------------- */
async function loadLocale(lang) {
  try { const r = await fetch(`/locales/${lang}.json`); S.locale = r.ok ? await r.json() : {}; } catch (_) { S.locale = {}; }
}
async function loadRecords(skipRender) {
  if (!S.worker) return;
  try {
    S.records = await api("/api/records?worker_id=" + encodeURIComponent(S.worker.worker_id));
    setConn(true);
    if (!V.active) setNotice("");
  } catch (_) { setNotice("Couldn't load records from the server."); setConn(false); }
  if (!skipRender) renderSafe(); else chrome();
}
async function loadWorkers() {
  try { S.workers = await api("/api/workers"); setConn(true); }
  catch (_) { setNotice("Couldn't reach the server. Is the backend running?"); setConn(false); render(); return; }
  const sel = $("#workerSelect");
  sel.innerHTML = S.workers.map((w) => `<option value="${esc(w.worker_id)}">${esc(w.display_name)} (${esc((w.language || "en").toUpperCase())})</option>`).join("");
  const pick = S.workers.find((w) => w.worker_id === store.get("asha.worker")) || S.workers[0];
  if (pick) { sel.value = pick.worker_id; await selectWorker(pick.worker_id, true); } else render();
}
async function selectWorker(id, initial) {
  const w = S.workers.find((x) => x.worker_id === id); if (!w) return;
  if (!initial) endVisit();
  S.worker = w; S.bsel = null; store.set("asha.worker", id);
  $("#langBadge").textContent = (w.language || "en").toUpperCase();
  await Promise.all([loadLocale(w.language || "en"), loadRecords(true)]);
  render();
}
async function fuAction(id, path, okMsg) {
  try {
    await api(`/api/followups/${encodeURIComponent(id)}/${path}${path.includes("?") ? "&" : "?"}worker_id=${encodeURIComponent(S.worker.worker_id)}`.replace("?&", "?"), { method: "POST" });
    toast(okMsg); await loadRecords();
  } catch (_) { toast("Couldn't update the follow-up"); }
}

/* ---------------- events ---------------- */
const A = {
  go: (d) => go(d.id),
  ackdanger: () => clearDangers(),
  vstart: (d) => { startVisit(d.id ? benefs().find((x) => x.id === d.id) || null : null); },
  vcancel: () => { endVisit(); render(); },
  vnew: () => startVisit(null),
  vmic: () => {
    if (!V.active) return;
    if (V.speaking) { cancelSpeech(); resumeMic(); return; }          // barge-in
    if (V.micFailed || !V.rec) { setNotice(""); initRec(); return; }
    if (V.micWanted) { V.micWanted = false; suspendMic(); } else { V.micWanted = true; safeStart(); }
    paintMic();
  },
  vconfirm: () => {
    if (V.state === "confirming" && V.ws && V.ws.readyState === WebSocket.OPEN)
      V.ws.send(JSON.stringify({ type: "confirm", form_version: V.formVersion }));
  },
  vcorrect: () => { const i = $("#vInput"); if (i) { i.placeholder = "Say or type the correction, e.g. no, weight is 2.8 kilo"; i.focus(); } },
  vdiscard: () => { if (V.ws && V.ws.readyState === WebSocket.OPEN) V.ws.send(JSON.stringify({ type: "discard" })); },
  bopen: (d) => { S.bsel = d.id; render(); },
  bback: () => { S.bsel = null; render(); },
  rtype: (d) => { S.rtype = d.id; render(); },
  ftab: (d) => { S.ftab = d.id; render(); },
  fdone: (d) => fuAction(d.id, "done", "Follow-up completed"),
  fundo: (d) => fuAction(d.id, "done?undo=true", "Follow-up reopened"),
  fresch: (d) => fuAction(d.id, "reschedule?days=2", "Rescheduled by 2 days"),
  setsr: (d) => { S.sr = d.id; store.set("asha.sr", d.id); render(); },
  setmute: (d) => {
    S.muted = d.id === "1"; store.set("asha.muted", S.muted ? "1" : "0");
    if (S.muted) cancelSpeech();
    render();
  },
};

document.addEventListener("click", (e) => {
  const say = e.target.closest("[data-say]");
  if (say) { if (V.active) sendTurn(say.dataset.say); else toast("Start a voice entry first"); return; }
  const t = e.target.closest("[data-act]");
  if (t && A[t.dataset.act]) A[t.dataset.act](t.dataset, t);
});
document.addEventListener("submit", (e) => {
  if (e.target.id !== "vForm") return;
  e.preventDefault();
  const i = $("#vInput"), v = i.value; i.value = "";
  i.placeholder = "Type a note or correction…";
  sendTurn(v);
});
document.addEventListener("input", (e) => {
  const t = e.target;
  if (t.dataset && t.dataset.s) {
    S[t.dataset.s] = t.value;
    const p = t.selectionStart; render();
    const n = document.getElementById(t.id); if (n) { n.focus(); try { n.setSelectionRange(p, p); } catch (_) {} }
  }
});
document.addEventListener("change", (e) => {
  const t = e.target;
  if (t.id === "workerSelect") selectWorker(t.value);
  else if (t.dataset && t.dataset.sel) { S[t.dataset.sel] = t.value; render(); }
});
window.addEventListener("hashchange", () => { const v = location.hash.slice(1); if (VIEWS[v] && v !== S.view) go(v); });

if ("speechSynthesis" in window) speechSynthesis.getVoices();
if (VIEWS[location.hash.slice(1)]) S.view = location.hash.slice(1);
render();
loadWorkers();