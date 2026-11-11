'use strict';
// Feedback 0.1: one 'Report or request' button (two-step sheet, copied from the Tracker reporter) and 'My feedback',
// newest first, each with its kind and one plain status line. Tap a row for the full text, progress and team comments.
const $ = id => document.getElementById(id);
const mk = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; };
const KINDS = [['bug', 'Something’s broken'], ['feature', 'I’d like something new']];
const PROMPTS = {
  bug: ['What happened, and what should happen?', 'What happened? What should have happened instead? If it’s about one app, name it.'],
  feature: ['What would you like, and why?', 'What you want, and why it would help. If it’s about one app, name it.'],
};
const UPDATES = '/wizard-os-home/store-install/_updates';
let me = null, items = [], pending = [], kind = null, shots = [], updates = null, devTimer = null, draftHold = null, draftTimer = null;

function say(text, kind = '') { const s = $('status'); s.classList.toggle('wz-sr', !kind); s.textContent = text; }
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
const day = iso => { const d = new Date(iso); return isNaN(d) ? '' : d.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' }); };

// ---------- account + who can send ----------
function paintAccount() {
  const on = !!(me && me.signed_in);
  $('acct').hidden = !on;
  $('acct-name').textContent = on ? 'Signed in as ' + (me.login || '') : '';
  $('signin').hidden = on || !me;
  const gate = $('gate');
  gate.hidden = !me || me.can_send;
  gate.textContent = me && !me.can_send ? (me.send_reason || 'Sign in with GitHub to send feedback.') : '';
}
async function loadStatus() { me = await api('api/status'); paintAccount(); }

// ---------- My feedback ----------
function row(it) {
  const li = mk('li');
  const b = mk('button', 'wz-row fb-row'); b.type = 'button';
  const main = mk('span', 'wz-main');
  const t = mk('b', null, it.title || 'Untitled');
  const line = mk('small', 'fb-line ' + 'fb-' + (it.status_key || 'unknown'), it.status);
  main.append(t, line);
  const chip = mk('span', 'wz-chip fb-kind', it.kind === 'bug' ? 'Bug' : 'Idea');
  b.append(main, chip);
  b.setAttribute('aria-label', `${it.title}. ${it.kind === 'bug' ? 'Bug' : 'Idea'}. ${it.status}`);
  if (it.pending) b.disabled = true; else b.onclick = () => openDetail(it);
  li.append(b);
  return li;
}
function paintList() {
  const all = [...pending, ...items];
  $('items').replaceChildren(...all.map(row));
  $('loading').hidden = true;
  $('empty').hidden = all.length > 0;
  $('count').hidden = !all.length; $('count').textContent = String(all.length);
  $('empty-p').textContent = me && !me.signed_in ? 'What you send shows up here. Sign in with GitHub to see everything you’ve sent from any Wizard.' : 'What you send shows up here, with how it’s going.';
}
async function loadList() {
  try { const r = await api('api/feedback'); items = r.items || []; paintList(); }
  catch (e) { $('loading').hidden = true; paintList(); say(e.message, 'error'); }
}

// ---------- detail sheet ----------
let detailFor = null;
function paintDetail(d) {
  $('d-kind').textContent = d.kind === 'bug' ? 'Bug' : 'Idea';
  $('d-when').textContent = d.created ? 'Sent ' + day(d.created) : '';
  $('d-title').textContent = d.title || '';
  $('d-line').textContent = d.status || '';
  const steps = d.steps || [];
  $('d-steps').replaceChildren(...steps.map(s => { const li = mk('li', 'fb-step', s.label); li.dataset.state = s.state; if (s.state === 'now') li.setAttribute('aria-current', 'step'); return li; }));
  $('d-steps').hidden = !steps.length || d.status_key === 'not_planned';
  $('d-text').textContent = d.text || '';
  const cs = d.comments || [];
  $('d-comments-h').hidden = !cs.length;
  $('d-comments').replaceChildren(...cs.map(c => { const li = mk('li', 'fb-comment'); li.append(mk('p', 'fb-meta', `${c.who} · ${day(c.when)}`), mk('p', 'fb-c-text', c.text)); return li; }));
}
async function openDetail(it) {
  detailFor = it.id;
  paintDetail({ ...it, steps: [], text: '', comments: [] });   // the sheet opens at once with what the row knows
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
  $('rr-kinds').replaceChildren(...KINDS.map(([key, name]) => {
    const b = mk('button', 'rr-pick', name); b.type = 'button'; b.setAttribute('role', 'radio');
    b.setAttribute('aria-checked', String(key === kind));
    b.onclick = () => { const first = !kind; kind = key; paintForm(); saveDraft(); if (first) $('rr-text').focus(); };
    return b;
  }));
  $('rr-s2').hidden = !kind;
  const [ask, ph] = PROMPTS[kind] || PROMPTS.bug;
  $('rr-ask').textContent = ask; $('rr-text').placeholder = ph;
  $('rr-auto').textContent = 'Adds: ' + [wizardVersion() && 'WizardOS ' + wizardVersion(), device()].filter(Boolean).join(' · ');
  const ready = kind && $('rr-text').value.trim().length >= 8;
  const can = !!(me && me.can_send);
  $('rr-go').disabled = !ready || !can;
  if (!can && kind) $('rr-status').textContent = (me && me.send_reason) || 'Sign in with GitHub to send feedback.';
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
function addShots(files) {
  const max = (me && me.shot_count) || 4, big = (me && me.shot_max) || 4 * 1024 * 1024;
  for (const f of [...(files || [])]) {
    if (shots.length >= max) { $('rr-status').textContent = `Up to ${max} screenshots.`; break; }
    if (!/^image\/(png|jpeg|webp|gif)$/.test(f.type)) { $('rr-status').textContent = 'Use a PNG, JPEG, WebP or GIF image.'; continue; }
    if (f.size > big) { $('rr-status').textContent = 'That screenshot is too big (4 MB max).'; continue; }
    const s = { type: f.type, url: '' }; shots.push(s);
    const r = new FileReader();
    r.onload = () => { s.url = String(r.result); paintShots(); };
    r.onerror = () => { shots = shots.filter(o => o !== s); paintShots(); };
    r.readAsDataURL(f);
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
    paintList(); say(out.sent || 'Sent.', 'note');
    kind = null; shots = []; op = null; $('rr-text').value = ''; syncHold();
    setTimeout(loadList, 4000);
  }).catch(err => {
    pending = pending.filter(p => p !== temp); paintList();
    openForm(); $('rr-status').textContent = err.message + ' Tap Send to try again.';
  });
}

// ---------- Sign in with GitHub (device flow, Tracker 0.24 UX) ----------
function stopDevice() { clearTimeout(devTimer); devTimer = null; $('oauth-code').hidden = true; $('oauth-go').hidden = false; }
function showCode(r) {
  $('oauth-user-code').textContent = r.user_code;
  $('oauth-open').href = /^https:\/\//.test(r.verification_uri || '') ? r.verification_uri : 'https://github.com/login/device';
  $('oauth-wait').textContent = 'Waiting for GitHub… this page signs you in by itself.';
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
  const b = $('oauth-go'); b.disabled = true; b.textContent = 'Asking GitHub for a code…';
  api('api/device/start', {}).then(r => { say(''); showCode(r); })
    .catch(err => say(err.message, 'error'))
    .finally(() => { b.disabled = false; b.textContent = 'Sign in with GitHub'; });
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
$('report-open').onclick = openForm;
$('rr-form').addEventListener('submit', send);
$('rr-text').addEventListener('input', () => { paintForm(); saveDraft(); });
for (const ev of ['paste', 'drop']) $('rr-text').addEventListener(ev, e => {
  const fs = [...(((e.clipboardData || e.dataTransfer) || {}).files || [])].filter(f => /^image\//.test(f.type));
  if (fs.length) { e.preventDefault(); addShots(fs); }
});
$('rr-cancel').onclick = () => WzSheet.close($('report'));
$('detail').addEventListener('close', () => { detailFor = null; });

if (!window.WizardApp || WizardApp.version !== 1) {
  say('Wizard system functions are unavailable. Open this through Wizard.', 'error');
} else {
  WizardApp.ready({ name: 'feedback', version: '0.1.0' });
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
