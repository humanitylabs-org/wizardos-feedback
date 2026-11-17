'use strict';
// Feedback 0.3.1: the page title and one 'Report or request' button (two-step sheet: pick a kind, then words), then
// 'Your feedback', newest first, as flat rows with thin dividers: kind icon, title, a small status pill in plain words,
// progress dots and a relative time. Chips filter it (All · Open · Done · Declined, plus Bugs · Ideas when they fit on
// one line), remembered on this device. Tap a row for the full text, a dated timeline and words from the team
// ('From the team: “…”' under a decline: the reason the reviewer wrote).
const $ = id => document.getElementById(id);
const mk = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; };
const KINDS = [['bug', 'Something’s broken', 'An app or part of Wizard isn’t working right'],
  ['feature', 'I’d like something new', 'An idea, a wish, or something you miss']];
const svgNS = 'http://www.w3.org/2000/svg';
function icon(name, cls) {
  const s = document.createElementNS(svgNS, 'svg'); s.setAttribute('class', 'fb-i' + (cls ? ' ' + cls : '')); s.setAttribute('aria-hidden', 'true');
  const u = document.createElementNS(svgNS, 'use'); u.setAttribute('href', '#i-' + name); s.append(u); return s;
}
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
// '11 Nov, 9:52' in the viewer's own time (year only when it is not this year).
function when(iso, withTime = true) {
  const d = new Date(iso); if (!iso || isNaN(d)) return '';
  const out = `${d.getDate()} ${MONTHS[d.getMonth()]}` + (d.getFullYear() !== new Date().getFullYear() ? ' ' + d.getFullYear() : '');
  return withTime ? `${out}, ${d.getHours()}:${String(d.getMinutes()).padStart(2, '0')}` : out;
}
// '2 hours ago', 'yesterday', '3 days ago', then a plain date.
function ago(iso) {
  const d = new Date(iso); if (!iso || isNaN(d)) return '';
  const s = (Date.now() - d) / 1000, m = Math.round(s / 60), h = Math.round(s / 3600), days = Math.round(s / 86400);
  if (s < 60) return 'just now';
  if (m < 60) return m === 1 ? 'a minute ago' : `${m} minutes ago`;
  if (h < 24) return h === 1 ? 'an hour ago' : `${h} hours ago`;
  if (days < 2) return 'yesterday';
  if (days < 7) return `${days} days ago`;
  return when(iso, false);
}
// The status line in the viewer's time zone: 'Declined by the team · 11 Nov, 9:52', 'Done ✓ · 11 Nov'.
const line = it => it.status_base && it.status_at && when(it.status_at) ? `${it.status_base} · ${when(it.status_at, it.status_show === 'datetime')}` : (it.status || '');
const TONE = { done: 'ok', not_planned: 'off', building: 'fb-on', checking: 'fb-on', ready: 'fb-on', unknown: 'fb-wait' };
const tone = k => TONE[k] || 'fb-wait';
const kindName = k => k === 'bug' ? 'Something’s broken' : 'An idea';
function dots(step) {
  const w = mk('span', 'fb-dots'); w.setAttribute('aria-hidden', 'true');
  for (let i = 0; i < 4; i++) { const d = mk('i'); d.dataset.on = String(step >= 0 && i <= step); w.append(d); }
  if (step < 0) w.dataset.off = 'true';
  return w;
}
const TZ = (() => { try { return Intl.DateTimeFormat().resolvedOptions().timeZone || ''; } catch (_) { return ''; } })();
const PROMPTS = {
  bug: ['What happened, and what should happen?', 'What happened? What should have happened instead? If it’s about one app, name it.'],
  feature: ['What would you like, and why?', 'What you want, and why it would help. If it’s about one app, name it.'],
};
const UPDATES = '/wizard-os-home/store-install/_updates';
let me = null, items = [], pending = [], kind = null, shots = [], updates = null, devTimer = null, draftHold = null, draftTimer = null;

function say(text, kind = '') { const s = $('status'); s.classList.toggle('wz-sr', !kind); s.classList.toggle('note', kind === 'note'); s.textContent = text; }
async function api(path, data) {
  const init = data === undefined ? { cache: 'no-store' } : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) };
  const r = await fetch(path, init);
  let body = {};
  try { body = await r.json(); } catch (_) {}
  if (!r.ok) { const e = new Error(body.error || 'Something went wrong. Try again.'); e.reason = body.reason; throw e; }
  return body;
}

function device() {
  const ua = navigator.userAgent || '';
  const os = /iPhone/.test(ua) ? 'iPhone' : /iPad/.test(ua) || (/Macintosh/.test(ua) && navigator.maxTouchPoints > 1) ? 'iPad'
    : /Android/.test(ua) ? 'Android' : /Macintosh|Mac OS X/.test(ua) ? 'Mac' : /Windows/.test(ua) ? 'Windows' : /Linux/.test(ua) ? 'Linux' : 'Unknown device';
  const br = /Edg\//.test(ua) ? 'Edge' : /Firefox\//.test(ua) ? 'Firefox' : /Chrome\//.test(ua) ? 'Chrome' : /Safari\//.test(ua) ? 'Safari' : 'browser';
  return `${os} · ${br}`;
}
const wizardVersion = () => updates && updates.installed ? String(updates.installed) : '';

// ---------- account + who can send ----------
function paintAccount() {
  const on = !!(me && me.signed_in);
  $('acct').hidden = !on;
  $('acct-name').textContent = on ? 'Signed in as ' + (me.name || me.login || '') : '';
  $('signin').hidden = on || !me;
  const gate = $('gate');
  // Signed out with sending off: the sign-in link itself says why ('Sign in with GitHub to send feedback').
  const oneLine = !!(me && !on && !me.can_send && me.send_reason === 'Sign in with GitHub to send feedback.');
  signinLabel = oneLine ? 'Sign in with GitHub to send feedback' : 'Sign in with GitHub';
  if (!$('oauth-go').disabled) $('oauth-go').textContent = signinLabel;
  gate.hidden = !me || me.can_send || oneLine;
  gate.textContent = me && !me.can_send ? (me.send_reason || 'Sign in with GitHub to send feedback.') : '';
  $('rr-offer').hidden = !(me && me.signin_offer && !me.signed_in);
  if (me && me.signin_offer) $('rr-offer').textContent = me.signin_offer;
}
let signinLabel = 'Sign in with GitHub';
async function loadStatus() { me = await api('api/status'); paintAccount(); }

// ---------- My feedback ----------
function row(it) {
  const li = mk('li');
  const b = mk('button', 'fb-row'); b.type = 'button';
  const tile = mk('span', 'fb-tile fb-tile-' + (it.kind === 'bug' ? 'bug' : 'idea')); tile.append(icon(it.kind === 'bug' ? 'bug' : 'idea'));
  const main = mk('span', 'fb-row-main');
  const t = mk('b', 'fb-row-title', it.title || 'Untitled');
  const text = line(it);
  const pill = mk('span', 'wz-pill fb-pill ' + tone(it.status_key), text);
  const meta = mk('span', 'fb-row-meta');
  meta.append(dots(it.pending ? -2 : it.step == null ? 0 : it.step), mk('small', null, it.pending ? '' : 'Sent ' + ago(it.created)));
  main.append(t, pill, meta);
  b.append(tile, main, icon('chev', 'fb-chev'));
  b.setAttribute('aria-label', `${it.title}. ${kindName(it.kind)}. ${text}. ${it.pending ? '' : 'Sent ' + ago(it.created)}`);
  if (it.pending) b.disabled = true; else b.onclick = () => openDetail(it);
  li.append(b);
  return li;
}
// ---------- filter chips (remembered on this device) ----------
const FKEY = 'feedback.filter.v1';
const STATUS_F = [['all', 'All'], ['open', 'Open'], ['done', 'Done'], ['declined', 'Declined']];
const KIND_F = [['bug', 'Bugs'], ['idea', 'Ideas']];
const bucket = it => it.status_key === 'done' ? 'done' : it.status_key === 'not_planned' ? 'declined' : 'open';
const kindKey = it => it.kind === 'bug' ? 'bug' : 'idea';
let filt = (() => { try { const f = JSON.parse(localStorage.getItem(FKEY) || '{}'); return { s: STATUS_F.some(x => x[0] === f.s) ? f.s : 'all', k: KIND_F.some(x => x[0] === f.k) ? f.k : '' }; } catch (_) { return { s: 'all', k: '' }; } })();
let kindFits = true;
function setFilter(f) { filt = { ...filt, ...f }; try { localStorage.setItem(FKEY, JSON.stringify(filt)); } catch (_) {} paintList(); }
const passes = it => (filt.s === 'all' || bucket(it) === filt.s) && (!filt.k || !kindFits || kindKey(it) === filt.k);
function chip(label, n, on, fn) {
  const b = mk('button', 'wz-chip', label); b.type = 'button'; b.setAttribute('aria-pressed', String(on));
  b.append(mk('small', null, String(n))); b.setAttribute('aria-label', `${label}, ${n}`); b.onclick = fn; return b;
}
function paintFilter(all) {
  const bar = $('filter');
  bar.hidden = all.length < 2;
  if (bar.hidden) return;
  const inKind = it => !filt.k || !kindFits || kindKey(it) === filt.k;
  const st = STATUS_F.map(([k, label]) => chip(label, all.filter(it => inKind(it) && (k === 'all' || bucket(it) === k)).length, filt.s === k, () => setFilter({ s: k })));
  const kd = KIND_F.map(([k, label]) => chip(label, all.filter(it => (filt.s === 'all' || bucket(it) === filt.s) && kindKey(it) === k).length, filt.k === k, () => setFilter({ k: filt.k === k ? '' : k })));
  bar.replaceChildren(...st, mk('i', 'fb-gap'), ...kd);
  // Bugs · Ideas only when the whole row fits on one line (390 px phones usually only fit the status chips)
  const fits = bar.scrollWidth <= bar.clientWidth + 1;
  if (!fits) { bar.replaceChildren(...st); if (kindFits && filt.k) { kindFits = false; return paintList(); } }
  kindFits = fits;
}
function paintList() {
  const all = [...pending, ...items];
  paintFilter(all);
  const shown = all.filter(it => it.pending || passes(it));
  $('items').replaceChildren(...shown.map(row));
  $('loading').hidden = true;
  $('empty').hidden = all.length > 0;
  const none = all.length > 0 && !shown.length;
  $('empty-filter').hidden = !none;
  $('empty-filter').textContent = none ? ({ open: 'Nothing open right now.', done: 'Nothing done yet.', declined: 'Nothing declined.' }[filt.s] || 'Nothing here.') : '';
  $('local-note').hidden = !(me && !me.signed_in && all.length);
}
addEventListener('resize', () => { kindFits = true; if (items.length || pending.length) paintList(); });
async function loadList() {
  try { const r = await api('api/feedback' + (TZ ? '?tz=' + encodeURIComponent(TZ) : '')); items = r.items || []; paintList(); }
  catch (e) { $('loading').hidden = true; paintList(); say(e.message, 'error'); }
}

// ---------- detail sheet ----------
let detailFor = null;
function paintDetail(d) {
  const k = $('d-kind'); k.replaceChildren(icon(d.kind === 'bug' ? 'bug' : 'idea'), document.createTextNode(kindName(d.kind)));
  $('d-when').textContent = d.created ? 'Sent ' + ago(d.created) : '';
  $('d-title').textContent = d.title || '';
  $('d-line').textContent = line(d);
  $('d-line').className = 'wz-pill fb-pill ' + tone(d.status_key);
  const declined = d.status_key === 'not_planned';
  $('d-declined').hidden = !declined || !d.note;
  $('d-note').textContent = d.note || '';
  const tn = d.team_note;
  $('d-team').hidden = !tn || !declined;
  $('d-declined').hidden = !declined || !(d.note || tn);
  $('d-team-text').textContent = tn ? `“${tn.text}”` : '';
  if (tn && tn.when) $('d-team').title = when(tn.when);
  const steps = d.steps || [];
  $('d-steps').replaceChildren(...steps.map(s => {
    const li = mk('li', 'fb-tl'); li.dataset.state = s.state;
    if (s.state === 'now') li.setAttribute('aria-current', 'step');
    const dot = mk('span', 'fb-tl-dot'); if (s.state === 'done') dot.append(icon('check'));
    const body = mk('span', 'fb-tl-body');
    const label = s.key === 'done' && s.state === 'done' ? 'Done ✓' : s.label;
    body.append(mk('b', null, label), mk('small', null, s.at ? when(s.at) : s.state === 'now' ? 'Now' : s.state === 'next' ? 'Next' : ''));
    li.append(dot, body); return li;
  }));
  $('d-steps').hidden = !steps.length;
  $('d-text').textContent = d.text || '';
  $('d-text-h').hidden = !d.text;
  const cs = d.comments || [];
  $('d-comments-h').hidden = !cs.length;
  $('d-comments').replaceChildren(...cs.map(c => { const li = mk('li', 'fb-comment'); li.append(mk('p', 'fb-meta', `${c.who} · ${when(c.when)}`), mk('p', 'fb-c-text', c.text)); return li; }));
}
async function openDetail(it) {
  detailFor = it.id;
  paintDetail({ ...it, steps: [], text: '', comments: [], note: '', team_note: null });   // the sheet opens at once with what the row knows
  $('d-status').textContent = 'Loading…';
  WzSheet.open($('detail'));
  try {
    const d = await api('api/feedback/item?id=' + encodeURIComponent(it.id));
    if (detailFor !== it.id) return;
    paintDetail(d); $('d-status').textContent = '';
  } catch (e) { if (detailFor === it.id) $('d-status').textContent = e.message; }
}

// ---------- the report sheet ----------
function paintForm() {
  $('rr-kinds').replaceChildren(...KINDS.map(([key, name, sub]) => {
    const b = mk('button', 'rr-pick'); b.type = 'button'; b.setAttribute('role', 'radio');
    const t = mk('span', 'fb-tile fb-tile-' + (key === 'bug' ? 'bug' : 'idea')); t.append(icon(key === 'bug' ? 'bug' : 'idea'));
    const w = mk('span', 'rr-pick-main'); w.append(mk('b', null, name), mk('small', null, sub));
    b.append(t, w);
    b.setAttribute('aria-checked', String(key === kind));
    b.onclick = () => { const first = !kind; kind = key; paintForm(); saveDraft(); if (first) $('rr-text').focus(); };
    return b;
  }));
  $('rr-s2').hidden = !kind;
  const [ask, ph] = PROMPTS[kind] || PROMPTS.bug;
  $('rr-ask').textContent = ask; $('rr-text').placeholder = ph;
  $('rr-auto').textContent = 'We’ll also include: ' + [wizardVersion() && 'WizardOS ' + wizardVersion(), device()].filter(Boolean).join(' · ');
  const ready = kind && $('rr-text').value.trim().length >= 8;
  const can = !!(me && me.can_send);
  $('rr-go').disabled = !ready || !can;
  if (!can && kind) $('rr-status').textContent = (me && me.send_reason) || 'Sign in with GitHub to send feedback.';
  $('rr-offer').hidden = !(me && me.signin_offer && !me.signed_in);
  paintShots();
}
function paintShots() {
  const max = (me && me.shot_count) || 4, rowEl = $('rr-shot-row');
  const thumbs = shots.map((s, n) => {
    const t = mk('span', 'rr-thumb'), img = mk('img'); img.src = s.url; img.alt = 'Screenshot ' + (n + 1);
    const x = mk('button', 'rr-thumb-x', '×'); x.type = 'button'; x.setAttribute('aria-label', 'Remove screenshot ' + (n + 1));
    x.onclick = () => { shots = shots.filter(o => o !== s); paintShots(); };
    t.append(img, x); return t;
  });
  const pick = mk('label', 'wz-button secondary rr-shot-pick');
  const input = mk('input'); input.type = 'file'; input.multiple = true; input.hidden = true; input.accept = 'image/png,image/jpeg,image/webp,image/gif';
  input.onchange = () => addShots(input.files);
  pick.append(document.createTextNode(shots.length ? 'Add another' : 'Add a screenshot'), input);
  pick.hidden = shots.length >= max;
  rowEl.replaceChildren(...thumbs, pick);
}
// A screenshot bigger than this route allows (2 MB through the relay) is shrunk to a JPEG in the browser; if it still
// does not fit, it is left out with one plain line.
const bytesOf = url => Math.floor((url.length - url.indexOf(',') - 1) * 3 / 4);
const readUrl = f => new Promise((ok, no) => { const r = new FileReader(); r.onload = () => ok(String(r.result)); r.onerror = no; r.readAsDataURL(f); });
async function shrink(f, big) {
  const img = await createImageBitmap(f);
  const c = document.createElement('canvas');
  for (const side of [2560, 1920, 1440, 1080]) {
    const k = Math.min(1, side / Math.max(img.width, img.height));
    c.width = Math.round(img.width * k); c.height = Math.round(img.height * k);
    const g = c.getContext('2d'); g.fillStyle = getComputedStyle(document.body).backgroundColor; g.fillRect(0, 0, c.width, c.height); g.drawImage(img, 0, 0, c.width, c.height);
    for (const q of [0.85, 0.7, 0.55]) { const url = c.toDataURL('image/jpeg', q); if (bytesOf(url) <= big) return url; }
  }
  return null;
}
function addShots(files) {
  const max = (me && me.shot_count) || 4, big = (me && me.shot_max) || 4 * 1024 * 1024, mb = Math.round(big / 1048576);
  for (const f of [...(files || [])]) {
    if (shots.length >= max) { $('rr-status').textContent = `Up to ${max} screenshots.`; break; }
    if (!/^image\/(png|jpeg|webp|gif)$/.test(f.type)) { $('rr-status').textContent = 'Use a PNG, JPEG, WebP or GIF image.'; continue; }
    const s = { type: f.type, url: '' }; shots.push(s);
    const drop = msg => { shots = shots.filter(o => o !== s); paintShots(); if (msg) $('rr-status').textContent = msg; };
    (f.size <= big ? readUrl(f) : shrink(f, big).then(url => { if (url) s.type = 'image/jpeg'; return url; }))
      .then(url => { if (!url) return drop(`That screenshot is too big (${mb} MB max). Try a smaller one.`); s.url = url; paintShots(); })
      .catch(() => drop('That screenshot could not be read.'));
  }
  paintShots();
}
function syncHold() {
  const unsaved = !!$('rr-text').value.trim();
  if (unsaved && !draftHold && window.WizardApp && WizardApp.version === 1 && WizardApp.update) draftHold = WizardApp.update.hold('Unsent feedback');
  if (!unsaved && draftHold) { draftHold.release(); draftHold = null; }
}
function saveDraft() {
  syncHold();
  clearTimeout(draftTimer);
  draftTimer = setTimeout(() => api('api/draft', { kind, text: $('rr-text').value }).catch(() => {}), 600);
}
function openForm() {
  $('rr-status').textContent = '';
  paintForm();
  WzSheet.open($('report'));
  const first = $('rr-kinds').querySelector('.rr-pick'); if (first && !kind) first.focus(); else $('rr-text').focus();
}

// Send: the tap registers at once (sheet closes, a 'Sending…' row appears), then the write; on failure the words
// come back in the form with the error. One operation_id per send, so a retry is never a second report.
let op = null;
function send(e) {
  e.preventDefault();
  const text = $('rr-text').value.trim();
  if (!kind || text.length < 8) { $('rr-status').textContent = 'Say a little more (a sentence is enough).'; return; }
  if (!me || !me.can_send) { paintForm(); return; }
  op = op || ('fb-' + Date.now().toString(36) + Math.random().toString(36).slice(2, 8));
  const body = { kind, text, device: device(), wizard_version: wizardVersion(), operation_id: op, clear_draft: true,
    screenshots: shots.filter(s => s.url).map(s => ({ type: s.type, data: s.url.split(',', 2)[1] })) };
  const temp = { id: op, pending: true, kind: kind === 'bug' ? 'bug' : 'idea', title: text.split(/(?<=[.!?])\s|\n/)[0].slice(0, 90), status: 'Sending…', status_key: 'unknown' };
  pending.unshift(temp); paintList();
  WzSheet.close($('report'));
  say('Sending…');
  api('api/feedback', body).then(out => {
    pending = pending.filter(p => p !== temp);
    if (out.item) items = [out.item, ...items.filter(i => i.id !== out.item.id)];
    paintList(); say(out.sent || 'Sent.'); thanks(out.sent);
    kind = null; shots = []; op = null; $('rr-text').value = ''; syncHold();
    setTimeout(loadList, 4000);
  }).catch(err => {
    pending = pending.filter(p => p !== temp); paintList();
    openForm();
    $('rr-status').textContent = /^(relay_down|rate_limited)$/.test(err.reason || '') ? err.message : err.message + ' Tap Send to try again.';
  });
}

let thanksTimer = null;
function thanks(text) {
  $('thanks-p').textContent = (text || '').replace(/^Sent\.\s*/, '') || 'We’ll keep you posted here.';
  $('thanks').hidden = false; $('thanks').classList.remove('fb-pop'); void $('thanks').offsetWidth; $('thanks').classList.add('fb-pop');
  clearTimeout(thanksTimer); thanksTimer = setTimeout(() => { $('thanks').hidden = true; }, 12000);
}
$('thanks-x').onclick = () => { clearTimeout(thanksTimer); $('thanks').hidden = true; };

// ---------- Sign in with GitHub (device flow, Tracker 0.24 UX) ----------
function stopDevice() { clearTimeout(devTimer); devTimer = null; $('oauth-code').hidden = true; $('oauth-go').hidden = false; }
function showCode(r) {
  $('oauth-user-code').textContent = r.user_code;
  $('oauth-open').href = /^https:\/\//.test(r.verification_uri || '') ? r.verification_uri : 'https://github.com/login/device';
  $('oauth-wait').textContent = 'Waiting… this page signs you in by itself.';
  $('oauth-code').hidden = false; $('oauth-go').hidden = true;
  clearTimeout(devTimer); devTimer = setTimeout(pollDevice, 2500);
}
async function pollDevice() {
  devTimer = null;
  let r;
  try { r = await api('api/device'); } catch (_) { devTimer = setTimeout(pollDevice, 5000); return; }
  if (r.state === 'pending') { devTimer = setTimeout(pollDevice, 2500); return; }
  stopDevice();
  if (r.state === 'done' && r.status) { me = r.status; paintAccount(); say('Signed in.', 'note'); $('loading').hidden = false; loadList(); return; }
  if (r.state !== 'none' && r.state !== 'cancelled') say(r.message || 'Sign-in did not finish. Try again.', 'error');
}
$('oauth-go').onclick = () => {
  const b = $('oauth-go'); b.disabled = true; b.textContent = 'Getting a sign-in code…';
  api('api/device/start', {}).then(r => { say(''); showCode(r); })
    .catch(err => say(err.message, 'error'))
    .finally(() => { b.disabled = false; b.textContent = signinLabel; });
};
$('oauth-copy').onclick = async () => {
  const code = $('oauth-user-code').textContent;
  let ok = false;
  try { await navigator.clipboard.writeText(code); ok = true; } catch (_) {
    try { const rg = document.createRange(); rg.selectNodeContents($('oauth-user-code')); const sel = getSelection(); sel.removeAllRanges(); sel.addRange(rg); ok = document.execCommand('copy'); } catch (_) {}
  }
  $('oauth-copy').textContent = ok ? 'Copied' : 'Select it';
  setTimeout(() => { $('oauth-copy').textContent = 'Copy'; }, 2000);
};
$('oauth-cancel').onclick = () => { stopDevice(); api('api/device/cancel', {}).catch(() => {}); say(''); };
$('signout').onclick = () => {
  me = { ...(me || {}), signed_in: false, login: null, can_send: false, send_reason: 'Sign in with GitHub to send feedback.' };
  items = items.filter(i => i.source === 'local'); paintAccount(); paintList();
  api('api/signout', {}).then(s => { me = s; paintAccount(); loadList(); }).catch(err => say(err.message, 'error'));
};

// ---------- wiring ----------
$('report-open').onclick = () => { $('thanks').hidden = true; openForm(); };
$('rr-form').addEventListener('submit', send);
$('rr-text').addEventListener('input', () => { paintForm(); saveDraft(); });
for (const ev of ['paste', 'drop']) $('rr-text').addEventListener(ev, e => {
  const fs = [...(((e.clipboardData || e.dataTransfer) || {}).files || [])].filter(f => /^image\//.test(f.type));
  if (fs.length) { e.preventDefault(); addShots(fs); }
});
$('rr-cancel').onclick = () => WzSheet.close($('report'));
// The soft sign-in offer under Send: starts the same device-flow sign-in on the main screen; the words stay saved.
$('rr-offer').onclick = () => { saveDraft(); WzSheet.close($('report')); $('oauth-go').click(); };
$('detail').addEventListener('close', () => { detailFor = null; });

if (!window.WizardApp || WizardApp.version !== 1) {
  say('Wizard system functions are unavailable. Open this through Wizard.', 'error');
} else {
  WizardApp.ready({ name: 'feedback', version: '0.3.2' });
  // One screen; sheets push their own Back step (WzSheet). Back never exits from here.
  WizardApp.back.register({ canGoBack: () => false, back: () => false });
}

Promise.allSettled([loadStatus(), api('api/draft'), fetch(UPDATES, { credentials: 'same-origin', cache: 'no-store' }).then(r => r.ok ? r.json() : null)])
  .then(([st, dr, up]) => {
    if (st.status === 'rejected') say(st.reason.message, 'error');
    if (dr.status === 'fulfilled' && dr.value) { kind = dr.value.kind || null; $('rr-text').value = dr.value.text || ''; syncHold(); }
    if (up.status === 'fulfilled') updates = up.value;
    if (me && !me.signed_in) api('api/device').then(r => { if (r.state === 'pending') showCode(r); }).catch(() => {});
    return loadList();
  });
document.addEventListener('visibilitychange', () => { if (!document.hidden && !devTimer) loadList(); });
