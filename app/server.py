"""Feedback: report a bug or ask for something new, then follow *your own* feedback in plain words.

Split from the WizardOS Tracker's reporter (Tracker app/report.py + web/report.js, copied, never imported).
Stdlib only. One service layer for the view (HTTP API) and agents (MCP). The host gateway owns authentication; the
GitHub token lives on this box only (DATA_DIR/private/account.json, 0600) and is never returned to the view or MCP.

Two ways to send:
- Signed in with GitHub as a humanitylabs-org member: files exactly like the Tracker reporter (wizard-os, labels
  triage + bug/feature, as the person); the WizardOS Builder's triage step moves it to the right repo.
- The relay (FEEDBACK_RELAY_URL or policy relay_url set): POST {relay}/v1/report, follow with GET
  {relay}/v1/status?receipt=A,B. Signed-in non-members use it whenever it is set and pass their GitHub token; signed-out
  people use it only behind the switch `anonymous` (default OFF). The relay decides who goes straight to the builder and
  who waits for a person to review. Client side only; the relay itself is a separate service.
"""
import base64
import binascii
import datetime as dt
import hashlib
import json
import os
import re
import secrets
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

ROOT = Path(__file__).resolve().parent.parent if '__file__' in globals() else Path(os.environ.get('APP_ROOT', '.')).resolve()
DATA = Path(os.environ.get('DATA_DIR', ROOT / '.data'))
APP_ID = 'feedback'
VERSION = '0.3.2'
SOURCE_REPO = 'humanitylabs-org/wizardos-feedback'
STATIC_TYPES = {'.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8', '.svg': 'image/svg+xml'}
USER_AGENT = f'wizardos-feedback/{VERSION} (+https://github.com/{SOURCE_REPO})'
GITHUB = os.environ.get('GITHUB_API', 'https://api.github.com').rstrip('/')
GITHUB_WEB = os.environ.get('GITHUB_WEB', 'https://github.com').rstrip('/')
ORG = os.environ.get('FEEDBACK_ORG', 'humanitylabs-org').strip()
TRIAGE_REPO = 'wizard-os'
TRIAGE_LABEL = 'triage'
MEDIA_REPO = os.environ.get('FEEDBACK_MEDIA_REPO', 'humanitylabs-org/wizard-issue-media')
BUILDER = os.environ.get('FEEDBACK_BUILDER', 'wizardosbuilder').strip().lower()
# The 'WizardOS Tracker' GitHub App (device flow on, no secret). A client id is public, not a secret.
CLIENT_ID = os.environ.get('GITHUB_APP_CLIENT_ID') or 'Iv23li1x6wuDUDj6Herx'
CACHE_SECONDS = 60
MEMBER_SECONDS = 600
LOCK = threading.RLock()

KINDS = {'bug': "Something's broken", 'feature': "I'd like something new"}
SHOT_TYPES = {'image/png': 'png', 'image/jpeg': 'jpg', 'image/webp': 'webp', 'image/gif': 'gif'}
SHOT_MAX = 4 * 1024 * 1024
SHOT_COUNT = 4
RELAY_SHOT_COUNT, RELAY_SHOT_MAX = 3, 2 * 1024 * 1024
RELAY_BATCH = 50
BODY_MAX = SHOT_COUNT * SHOT_MAX * 4 // 3 + 256 * 1024
SENT = 'Sent. The team picks it up in a minute or two. You can follow it below.'
SENT_REVIEW = 'Sent. Someone on the team will look at it soon. You can follow it below.'
NOT_TEAM = 'Feedback from outside the team opens soon.'
SIGN_IN_FIRST = 'Sign in with GitHub to send feedback.'
NO_MEMBER_CHECK = 'Could not check your team membership. Try again in a minute.'
SIGN_IN_OFFER = 'Sign in with GitHub to follow it from any device and see replies from the team'
RELAY_DOWN = "Couldn't send right now. Your words are saved; try again in a minute."
RELAY_BUSY = "That's a lot of reports in a short time. Try again in a few minutes."

SWITCHES = [
    {'id': 'anonymous', 'name': 'Send without the team',
     'description': 'Let people send feedback without signing in, through the Wizard relay; a person on the '
                    'team reviews it first. Needs a relay address.', 'default': False, 'issues': []},
]


class Problem(Exception):
    def __init__(self, code, message, reason=None):
        super().__init__(message)
        self.code, self.message, self.reason = code, message, reason


# ---------- small storage (atomic JSON) ----------

def _csv_env(name):
    return {x.strip().lower() for x in os.environ.get(name, '').split(',') if x.strip()}


def _private():
    p = DATA / 'private'
    p.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(p, 0o700)
    except OSError:
        pass
    return p


def _read(path, fallback):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return fallback


def _write(path, doc, mode=0o644):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name('.' + path.name + '.' + secrets.token_hex(4))
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(fd, 'w') as f:
        json.dump(doc, f)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def account():
    return _read(_private() / 'account.json', None)


def _save_account(acct):
    _write(_private() / 'account.json', acct, 0o600)


def policy():
    return _read(DATA / 'policy.json', {})


DEFAULT_RELAY_URL = 'https://wizardos-feedback-relay.ai-wizards-previews.workers.dev'


def relay_url():
    v = str(os.environ.get('FEEDBACK_RELAY_URL') or policy().get('relay_url') or DEFAULT_RELAY_URL)
    ok = re.match(r'^https://\S+$', v) or re.match(r'^http://(127\.0\.0\.1|localhost)(:\d+)?(/\S*)?$', v)
    return v.rstrip('/') if ok else ''


# ---------- switches (wizard.app-switches/1) ----------

def switches_doc():
    saved = _read(DATA / 'switches.json', {})
    return {'contract': 'wizard.app-switches/1', 'switches': [
        dict(s, on=saved[s['id']] if isinstance(saved.get(s['id']), bool) else s['default']) for s in SWITCHES]}


def switch_on(sid):
    return any(s['id'] == sid and s['on'] for s in switches_doc()['switches'])


def set_switch(args):
    sid, on = args.get('id'), args.get('on')
    if sid not in {s['id'] for s in SWITCHES} or not isinstance(on, bool):
        raise Problem(400, 'Send {"id": "anonymous", "on": true|false}.')
    with LOCK:
        saved = {k: v for k, v in _read(DATA / 'switches.json', {}).items() if isinstance(v, bool)}
        saved[sid] = on
        _write(DATA / 'switches.json', saved)
    return switches_doc()


# ---------- the one status mapping (labels/state -> status_key + plain line) ----------

STEPS = [('received', 'Received'), ('building', 'Being built'), ('checking', 'Being tested'), ('done', 'Done')]
STATUS = {   # the relay contract's status_keys, plus 'unknown' for anything else. Plain words for people, no jargon.
    'waiting_review': ('Waiting for the team to look at it', 0),
    'received': ('Got it — the team is sorting it', 0),
    'queued': ('Coming up — in line to be built', 0),
    'building': ('Being built now', 1),
    'checking': ('Being tested', 2),
    'ready': ('Almost there — the team is trying it out', 2),
    'done': ('Done ✓', 3),
    'not_planned': ('Declined by the team', -1),
    'unknown': ('Sent', 0),
}
DECLINED_NOTE = 'The team looked at this and decided not to go ahead with it.'
MONTHS = ('Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec')


def _tzinfo():
    """The box's local time zone: FEEDBACK_TIMEZONE, else the one the owner's browser last reported, else UTC."""
    name = os.environ.get('FEEDBACK_TIMEZONE') or _read(DATA / 'timezone.json', {}).get('tz') or ''
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name) if name else dt.timezone.utc
    except Exception:   # unknown name or no tz database
        return dt.timezone.utc


def remember_tz(name):
    """The view passes the browser's zone (IANA name) so agents read the same local times."""
    name = str(name or '')[:64]
    if not re.fullmatch(r'[A-Za-z_]+(/[A-Za-z0-9_+-]+){0,2}', name) or name == _read(DATA / 'timezone.json', {}).get('tz'):
        return
    try:
        from zoneinfo import ZoneInfo
        ZoneInfo(name)
    except Exception:
        return
    with LOCK:
        _write(DATA / 'timezone.json', {'tz': name})


def _when(iso, with_time=True, now=None, tz=None):
    """'11 Nov, 9:52' in local time (year added only when it is not this year); '' when unreadable."""
    try:
        d = dt.datetime.fromisoformat(str(iso).replace('Z', '+00:00'))
    except ValueError:
        return ''
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    tz = tz or _tzinfo()
    d = d.astimezone(tz)
    year = (now or dt.datetime.now(tz)).astimezone(tz).year
    out = f'{d.day} {MONTHS[d.month - 1]}' + (f' {d.year}' if d.year != year else '')
    return out + (f', {d.hour}:{d.minute:02d}' if with_time else '')


def status_of(state, state_reason=None, labels=(), closed_at=None):
    """GitHub issue facts -> {'key', 'line', 'step', 'base', 'at', 'show'}. Closed wins; then the Tracker's stage
    labels, latest stage first. No stage label = queued; `triage` = not sorted yet; an inbox item a person rejected
    (label rejected) is declined. step: 0..3 on Received → Being built → Being tested → Done, -1 when declined."""
    names = {str(n.get('name') if isinstance(n, dict) else n).lower() for n in labels or ()}
    state = str(state or '').lower()
    if state == 'closed':
        declined = str(state_reason or '').lower() in ('not_planned', 'duplicate') or 'rejected' in names
        key = 'not_planned' if declined else 'done'
    elif 'rejected' in names:
        key = 'not_planned'
    elif 'needs-review' in names:
        key = 'waiting_review'
    elif 'ready-to-test' in names:
        key = 'ready'
    elif 'ai-review' in names:
        key = 'checking'
    elif 'building' in names:
        key = 'building'
    elif TRIAGE_LABEL in names:
        key = 'received'
    else:
        key = 'queued'
    return keyed(key, closed_at)


def keyed(key, when=None):
    """A status_key (from status_of or the relay) -> {'key', 'line', 'step', 'base', 'at', 'show'}.
    Done gets its day, declined its day and time: 'Declined by the team · 11 Nov, 9:52'. `base` + `at` + `show`
    ('date'|'datetime') let the screen redo the date in the viewer's own time zone."""
    key = 'received' if key == 'not_found' else key   # the relay has not indexed a fresh report yet (~1 min)
    key = key if key in STATUS else 'unknown'
    base, step = STATUS[key]
    out = {'key': key, 'line': base, 'step': step, 'base': base, 'at': None, 'show': None}
    if key in ('done', 'not_planned') and when and _when(when):
        show = 'date' if key == 'done' else 'datetime'
        out.update(line=f'{base} · {_when(when, show == "datetime")}', at=when, show=show)
    return out


def kind_of(labels):
    names = {str(n.get('name') if isinstance(n, dict) else n).lower() for n in labels or ()}
    return 'bug' if 'bug' in names else 'idea'


# ---------- GitHub client (60 s read cache) ----------

_cache = {}


def _gh_raw(token, method, path, body=None, accept='application/vnd.github+json'):
    url = GITHUB + path
    data = None if body is None else json.dumps(body).encode()
    headers = {'Authorization': 'Bearer ' + token, 'Accept': accept, 'X-GitHub-Api-Version': '2022-11-28', 'User-Agent': USER_AGENT}
    if data is not None:
        headers['Content-Type'] = 'application/json'
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read(8 * 1024 * 1024) or b'null')
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read(65536) or b'{}')
        except ValueError:
            return e.code, {}
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return 0, None


def gh(method, path, body=None, cache=True):
    """A GitHub call as the signed-in person. Renews an expiring GitHub App token once on 401."""
    acct = _fresh_account()
    if not acct:
        raise Problem(401, SIGN_IN_FIRST, 'signed_out')
    key = (hashlib.sha256(acct['token'].encode()).hexdigest()[:12], method, path, json.dumps(body, sort_keys=True))
    if cache and method in ('GET', 'GRAPHQL'):
        with LOCK:
            hit = _cache.get(key)
        if hit and time.time() - hit[0] < CACHE_SECONDS:
            return hit[1]
    real, where = ('POST', '/graphql') if method == 'GRAPHQL' else (method, path)
    code, out = _gh_raw(acct['token'], real, where, body)
    if code == 401:
        acct = refresh_account(force=True)
        if not acct:
            raise Problem(401, 'GitHub signed you out. Tap Sign in with GitHub again.', 'token_dead')
        code, out = _gh_raw(acct['token'], real, where, body)
    if code == 0:
        raise Problem(502, 'Could not reach GitHub. Check the internet connection and try again.')
    msg = str(out.get('message') or '') if isinstance(out, dict) else ''
    if code in (403, 429) and 'rate limit' in msg.lower():
        raise Problem(429, 'GitHub is busy. Try again in a minute.')
    if code >= 400:
        raise Problem(502 if code >= 500 else code, f'GitHub answered {code}' + (f': {msg}' if msg else '.'))
    if method == 'GRAPHQL' and isinstance(out, dict) and out.get('errors') and not out.get('data'):
        raise Problem(502, ('GitHub: ' + '; '.join(str(e.get('message', '')) for e in out['errors']))[:300])
    with LOCK:
        if method in ('GET', 'GRAPHQL'):
            if cache:
                _cache[key] = (time.time(), out)
                if len(_cache) > 300:
                    _cache.clear()
        else:
            _cache.clear()
    return out


def _gh_form(url, fields):
    """POST a form to github.com (device flow endpoints), JSON back -> (status, dict or None)."""
    req = urllib.request.Request(url, data=urlencode(fields).encode(), method='POST',
                                 headers={'Accept': 'application/json', 'Content-Type': 'application/x-www-form-urlencoded', 'User-Agent': USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read(1 << 16) or b'{}')
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read(1 << 16) or b'{}')
        except ValueError:
            return e.code, None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return 0, None


# ---------- account: Sign in with GitHub (device flow, no secret) ----------

def _token_fields(d, now=None):
    now = now or time.time()
    out = {}
    if isinstance(d.get('refresh_token'), str) and d['refresh_token']:
        out['refresh_token'] = d['refresh_token']
    for k, to in (('expires_in', 'expires_at'), ('refresh_token_expires_in', 'refresh_expires_at')):
        try:
            if d.get(k) is not None:
                out[to] = int(now + int(d[k]))
        except (TypeError, ValueError):
            pass
    return out


def refresh_account(force=False):
    """Swap the refresh token for a new pair when due (or forced after a 401). -> account or None."""
    with LOCK:
        cur = account()
        if not cur or not cur.get('refresh_token'):
            return None if force else cur
        if not force and (not cur.get('expires_at') or cur['expires_at'] - time.time() > 600):
            return cur
        code, d = _gh_form(GITHUB_WEB + '/login/oauth/access_token', {'client_id': cur.get('client_id') or CLIENT_ID,
                                                                      'grant_type': 'refresh_token', 'refresh_token': cur['refresh_token']})
        d = d if isinstance(d, dict) else {}
        if not d.get('access_token'):
            if d.get('error') in ('bad_refresh_token', 'unauthorized_client'):
                cur.pop('refresh_token', None)
                _save_account(cur)
            return None
        new = {**cur, 'token': d['access_token'], 'refreshed_at': int(time.time()), **_token_fields(d)}
        _save_account(new)
        _cache.clear()
        return new


def _fresh_account():
    acct = account()
    if acct and acct.get('refresh_token') and acct.get('expires_at') and acct['expires_at'] - time.time() < 600:
        acct = refresh_account() or acct
    return acct


_member = {}


def is_member(fresh=False):
    """Is the signed-in person an active member of ORG? Cached 10 minutes. None when signed out or GitHub can't tell."""
    acct = account()
    if not acct:
        return None
    login = acct.get('login') or ''
    hit = _member.get(login)
    if hit and not fresh and time.time() - hit[0] < MEMBER_SECONDS:
        return hit[1]
    tok = (_fresh_account() or acct)['token']
    ok = None
    code, body = _gh_raw(tok, 'GET', f'/user/memberships/orgs/{ORG}')
    if code == 200 and isinstance(body, dict):
        ok = body.get('state') == 'active'
    if not ok:
        code, body = _gh_raw(tok, 'POST', '/graphql', {'query': 'query($o:String!){organization(login:$o){viewerIsAMember}}', 'variables': {'o': ORG}})
        v = (((body or {}).get('data') or {}).get('organization') or {}).get('viewerIsAMember') if isinstance(body, dict) else None
        if isinstance(v, bool):
            ok = v or bool(ok)
    if not ok:
        code, _ = _gh_raw(tok, 'GET', f'/orgs/{ORG}/members/{login}')
        if code == 204:
            ok = True
        elif code in (302, 404) and ok is None:
            ok = False
    if ok is not None:
        _member[login] = (time.time(), ok)
    return ok if ok is not None else (hit[1] if hit else None)


def sign_in(d, client_id):
    token = d['access_token']
    code, user = _gh_raw(token, 'GET', '/user')
    if code != 200 or not isinstance(user, dict) or not user.get('login'):
        raise Problem(502, 'GitHub did not say who you are. Try again.')
    acct = {'token': token, 'login': user['login'], 'name': user.get('name') or user['login'],
            'client_id': client_id, 'saved_at': int(time.time()), **_token_fields(d)}
    with LOCK:
        _save_account(acct)
        _cache.clear()
        _member.pop(user['login'], None)
    is_member(fresh=True)


def sign_out():
    with LOCK:
        try:
            (_private() / 'account.json').unlink()
        except FileNotFoundError:
            pass
        _cache.clear()
    return status()


DEVICE_ERRORS = {
    'device_flow_disabled': 'Sign in with GitHub is turned off for this app right now.',
    'incorrect_client_credentials': 'GitHub does not know this app. Tell the team.',
    'unsupported_grant_type': 'GitHub refused the sign-in request.', 'incorrect_device_code': 'GitHub lost this sign-in. Start again.',
    'access_denied': 'You cancelled on GitHub. Start again to sign in.', 'expired_token': 'The code expired. Start again.',
}
_flow = {}


def device_public(flow):
    if not flow:
        return {'state': 'none'}
    out = {k: flow.get(k) for k in ('state', 'user_code', 'verification_uri', 'interval', 'message', 'reason')}
    out['expires_in'] = max(0, int(flow['expires_at'] - time.time()))
    if flow['state'] == 'done':
        out['status'] = status()
    return out


def device_start():
    code, d = _gh_form(GITHUB_WEB + '/login/device/code', {'client_id': CLIENT_ID})
    d = d if isinstance(d, dict) else {}
    if code != 200 or not d.get('device_code') or not d.get('user_code'):
        err = d.get('error')
        raise Problem(502, DEVICE_ERRORS.get(err) or ('Could not reach GitHub. Try again.' if not code else f'GitHub answered {code}.'), err)
    uri = d.get('verification_uri') if str(d.get('verification_uri') or '').startswith('https://') else 'https://github.com/login/device'
    flow = {'device_code': d['device_code'], 'user_code': str(d['user_code'])[:20], 'verification_uri': uri,
            'interval': max(5, int(d.get('interval') or 5)), 'expires_at': time.time() + max(60, min(int(d.get('expires_in') or 900), 1800)),
            'state': 'pending', 'message': '', 'reason': None}
    with LOCK:
        old = _flow.get('cur')
        if old and old['state'] == 'pending':
            old['state'] = 'cancelled'
        _flow['cur'] = flow
    threading.Thread(target=device_run, args=(flow,), daemon=True).start()
    return device_public(flow)


def device_poll_once(flow):
    """One poll; seconds until the next, or None when finished. Respects interval and slow_down."""
    if flow['state'] != 'pending':
        return None
    if time.time() >= flow['expires_at']:
        flow.update(state='expired', message=DEVICE_ERRORS['expired_token'], device_code=None)
        return None
    code, d = _gh_form(GITHUB_WEB + '/login/oauth/access_token', {'client_id': CLIENT_ID, 'device_code': flow['device_code'],
                                                                  'grant_type': 'urn:ietf:params:oauth:grant-type:device_code'})
    d = d if isinstance(d, dict) else {}
    if flow['state'] != 'pending':
        return None
    if d.get('access_token'):
        flow['device_code'] = None
        try:
            sign_in(d, CLIENT_ID)
            flow.update(state='done', message='Signed in.')
        except Problem as e:
            flow.update(state='error', message=e.message, reason=e.reason)
        return None
    err = d.get('error')
    if err == 'authorization_pending':
        return flow['interval']
    if err == 'slow_down':
        flow['interval'] = max(flow['interval'] + 5, int(d.get('interval') or 0))
        return flow['interval']
    if err in DEVICE_ERRORS:
        flow.update(state='expired' if err == 'expired_token' else 'denied' if err == 'access_denied' else 'error',
                    message=DEVICE_ERRORS[err], reason=err, device_code=None)
        return None
    return flow['interval'] + (5 if not code or code >= 500 else 0)


def device_run(flow, sleep=time.sleep):
    wait = flow['interval']
    while wait is not None:
        sleep(wait)
        wait = device_poll_once(flow)


def device_status():
    return device_public(_flow.get('cur'))


def device_cancel():
    with LOCK:
        flow = _flow.pop('cur', None)
    if flow and flow['state'] == 'pending':
        flow.update(state='cancelled', device_code=None)
    return {'state': 'none'}


# ---------- what the screen and agents may do right now ----------

def route(signed_in, member, relay, anonymous):
    """The one send-routing decision -> ('github'|'relay'|None, reason).
    Members file on GitHub directly. Signed-in non-members (or membership unknown) go through the relay whenever one is
    set, with their GitHub token; the relay decides builder or human review. Signed out goes through the relay only
    when the switch `anonymous` is on too. Otherwise sending is off, with the reason shown."""
    if signed_in:
        if member:
            return 'github', ''
        if relay:
            return 'relay', ''
        return None, NOT_TEAM if member is False else NO_MEMBER_CHECK
    if relay and anonymous:
        return 'relay', ''
    return None, SIGN_IN_FIRST


def send_path():
    acct = account()
    return route(bool(acct), is_member() if acct else None, bool(relay_url()), switch_on('anonymous'))


def status():
    acct = account()
    path, why = send_path()
    relay = path == 'relay'
    return {'signed_in': bool(acct), 'login': (acct or {}).get('login'), 'name': (acct or {}).get('name'),
            'member': is_member() if acct else None, 'can_send': bool(path), 'send_via': path, 'send_reason': why,
            'signin_offer': SIGN_IN_OFFER if relay and not acct else '',
            'anonymous': switch_on('anonymous'), 'relay': bool(relay_url()), 'version': VERSION,
            'kinds': [{'key': k, 'name': v} for k, v in KINDS.items()],
            'shot_max': RELAY_SHOT_MAX if relay else SHOT_MAX, 'shot_count': RELAY_SHOT_COUNT if relay else SHOT_COUNT}


# ---------- the text that gets filed (copied from Tracker report.py) ----------

_SAFE = re.compile(r'[^\w .,:;()+/@#&\'·-]')


def _clean(v, n):
    return _SAFE.sub('', str(v or ''))[:n].strip()


def title_of(text, limit=90):
    """The issue title: the first sentence (or line), cut at a word."""
    first = re.split(r'(?<=[.!?])\s|\n', text.strip(), maxsplit=1)[0].strip().rstrip('.')
    if len(first) <= limit:
        return first
    cut = first[:limit].rsplit(' ', 1)[0]
    return (cut if len(cut) > limit // 2 else first[:limit]).rstrip(' ,;:') + '…'


def compose(args):
    kind = args.get('kind')
    if kind == 'idea':
        kind = 'feature'
    if kind not in KINDS:
        raise Problem(400, "Pick Something's broken or I'd like something new.")
    text = args.get('text')
    if not isinstance(text, str) or len(text.strip()) < 8:
        raise Problem(400, 'Say a little more (a sentence is enough).')
    if len(text) > 8000:
        raise Problem(400, 'That is too long (8000 characters max).')
    return kind, text.strip()


def footer(args):
    bits = []
    v = _clean(args.get('wizard_version'), 60)
    if v:
        bits.append('WizardOS ' + v)
    d = _clean(args.get('device'), 80)
    if d:
        bits.append(d)
    return '\n\n---\nFiled with Feedback · waiting for the builder to sort it (label triage)' + ('\n' + ' · '.join(bits) if bits else '')


def screenshots(args):
    many = args.get('screenshots') or []
    if not isinstance(many, list):
        raise Problem(400, 'Screenshots must be a list of images.')
    if len(many) > SHOT_COUNT:
        raise Problem(400, f'Up to {SHOT_COUNT} screenshots at a time.')
    out = []
    for v in many:
        if not isinstance(v, dict) or v.get('type') not in SHOT_TYPES or not isinstance(v.get('data'), str):
            raise Problem(400, 'The screenshot must be a PNG, JPEG, WebP or GIF image.')
        data = v['data'].split(',', 1)[-1]
        try:
            raw = base64.b64decode(data, validate=True)
        except (binascii.Error, ValueError):
            raise Problem(400, 'The screenshot could not be read.') from None
        ext = SHOT_TYPES[v['type']]
        sig = {'png': raw[:8] == b'\x89PNG\r\n\x1a\n', 'jpg': raw[:3] == b'\xff\xd8\xff', 'gif': raw[:4] == b'GIF8',
               'webp': raw[:4] == b'RIFF' and raw[8:12] == b'WEBP'}
        if not raw or len(raw) > SHOT_MAX or not sig[ext]:
            raise Problem(400, 'The screenshot must be a PNG, JPEG, WebP or GIF image (4 MB max).')
        out.append((raw, ext, v['type'], data))
    return out


def _upload_shot(raw, ext):
    """The screenshot goes to the team's private media repo as the person (needs the GitHub App's Contents permission)."""
    path = time.strftime('reports/%Y/%m/', time.gmtime()) + secrets.token_hex(8) + '.' + ext
    out = gh('PUT', f'/repos/{MEDIA_REPO}/contents/{path}', {'message': 'Feedback screenshot', 'content': base64.b64encode(raw).decode()})
    return (((out or {}).get('content') or {}).get('html_url') or f'https://github.com/{MEDIA_REPO}/blob/main/{path}') + '?raw=true'


# ---------- send ----------

_ops = {}   # operation_id -> result (a double tap is one send)


def send(args):
    kind, text = compose(args)
    shots = screenshots(args)
    op = str(args.get('operation_id') or '')[:80]
    if op and op in _ops:
        return _ops[op]
    via, why = send_path()
    if not via:
        raise Problem(403, why, 'signed_out' if why == SIGN_IN_FIRST else 'not_member')
    if via == 'relay':
        if len(shots) > RELAY_SHOT_COUNT:
            raise Problem(400, f'Up to {RELAY_SHOT_COUNT} screenshots at a time.')
        if any(len(raw) > RELAY_SHOT_MAX for raw, _, _, _ in shots):
            raise Problem(400, 'Each screenshot can be up to 2 MB. Remove the big one and try again.')
    out = send_github(kind, text, shots, args) if via == 'github' else send_relay(kind, text, shots, args)
    if op:
        _ops[op] = out
    if args.get('clear_draft'):
        draft_set({})
    return out


def send_github(kind, text, shots, args):
    urls, failed = [], 0
    for raw, ext, _, _ in shots:
        if failed:
            failed += 1
            continue
        try:
            urls.append(_upload_shot(raw, ext))
        except Problem:
            failed += 1
    one = len(urls) == 1
    imgs = ''.join(f'\n\n![Screenshot{"" if one else " " + str(n)}]({u})' for n, u in enumerate(urls, 1))
    body = text + imgs + footer(args)
    made = gh('POST', f'/repos/{ORG}/{TRIAGE_REPO}/issues', {'title': title_of(text), 'body': body, 'labels': [TRIAGE_LABEL, kind]})
    item = _row(made)
    with LOCK:
        _just_sent[item['id']] = (time.time(), item)
    warn = f' {failed} screenshot{"s" if failed > 1 else ""} could not be attached.' if failed else ''
    return {'sent': SENT + warn, 'item': item}


def _relay_call(method, path, body=None, timeout=30):
    url = relay_url()
    if not url:
        raise Problem(503, SIGN_IN_FIRST, 'no_relay')
    data = None if body is None else json.dumps(body).encode()
    headers = {'Accept': 'application/json', 'User-Agent': USER_AGENT}
    if data is not None:
        headers['Content-Type'] = 'application/json'
    req = urllib.request.Request(url + path, data=data, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        out = json.loads(r.read(1 << 20) or b'{}')
    if not isinstance(out, dict):
        raise ValueError('not an object')
    return out


def send_relay(kind, text, shots, args):
    """Relay contract: POST {relay}/v1/report {kind, text, wizard_version?, device?, gh_token?, screenshots?}
    -> {receipt, status_key, line}. The receipt is kept on this box in sent.json and followed with /v1/status."""
    payload = {'kind': kind, 'text': text}
    for k, n in (('wizard_version', 60), ('device', 80)):
        v = _clean(args.get(k), n)
        if v:
            payload[k] = v
    if shots:
        payload['screenshots'] = [{'type': t, 'data': d} for _, _, t, d in shots]
    acct = _fresh_account()
    if acct:
        payload['gh_token'] = acct['token']   # a signed-in non-member: the relay files as them
    try:
        out = _relay_call('POST', '/v1/report', payload)
    except urllib.error.HTTPError as e:
        raise relay_problem(e.code, e.read(65536)) from None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        raise Problem(503, RELAY_DOWN, 'relay_down') from None
    receipt = str(out.get('receipt') or '')
    if not re.fullmatch(r'[A-Za-z0-9._-]{6,120}', receipt):
        raise Problem(503, RELAY_DOWN, 'relay_down')
    st = str(out.get('status_key') or 'waiting_review')
    rec = {'id': 'local-' + receipt, 'kind': 'bug' if kind == 'bug' else 'idea', 'title': title_of(text), 'text': text,
           'created': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'receipt': receipt,
           'status_key': st if st in STATUS else 'waiting_review', 'updated': None, 'done_at': None}
    with LOCK:
        sent = _read(DATA / 'sent.json', [])
        sent.append(rec)
        _write(DATA / 'sent.json', sent)
    return {'sent': SENT_REVIEW, 'item': _local_row(rec)}


def relay_problem(code, raw=b''):
    """A relay error answer -> one plain Problem. 429: slow down; 5xx: down; other 4xx: the relay's own words."""
    if code == 429:
        return Problem(429, RELAY_BUSY, 'rate_limited')
    if code >= 500:
        return Problem(503, RELAY_DOWN, 'relay_down')
    try:
        d = json.loads(raw or b'{}')
        msg = str(d.get('message') or d.get('error') or '') if isinstance(d, dict) else ''
    except ValueError:
        msg = ''
    return Problem(400, (msg or f'The feedback relay answered {code}.')[:300], 'relay_refused')


# ---------- history ----------

_just_sent = {}   # search lags a few seconds behind a new issue
MOVED = re.compile(r'Filed by @([A-Za-z0-9-]{1,39}) with (?:Report or request|Feedback)(?: \(moved from ([\w.-]+/[\w.-]+)#(\d+)\))?', re.I)


def _row(it):
    labels = it.get('labels') or []
    st = status_of(it.get('state'), it.get('state_reason'), labels, it.get('closed_at'))
    return {'id': it.get('node_id'), 'kind': kind_of(labels), 'title': it.get('title') or '', 'created': it.get('created_at'),
            **_status_fields(st), 'source': 'github'}


def _status_fields(st):
    return {'status_key': st['key'], 'status': st['line'], 'step': st['step'], 'status_base': st['base'],
            'status_at': st['at'], 'status_show': st['show']}


def _local_row(rec):
    st = keyed(rec.get('status_key') or 'waiting_review', rec.get('closed_at') or rec.get('done_at') or rec.get('updated'))
    return {'id': rec['id'], 'kind': rec.get('kind') or 'idea', 'title': rec.get('title') or '', 'created': rec.get('created'),
            **_status_fields(st), 'source': 'local'}


def _search(q):
    return (gh('GET', '/search/issues?' + urlencode({'q': q, 'sort': 'created', 'order': 'desc', 'per_page': 100})) or {}).get('items') or []


def list_github():
    login = account()['login']
    mine = _search(f'org:{ORG} author:{login} is:issue')
    # A report the builder had to copy into a public repo says 'Filed by @login … (moved from repo#N)': show the copy,
    # hide the original it closed as not planned.
    try:
        copies = []
        for c in _search(f'org:{ORG} author:{BUILDER} is:issue label:refiled "Filed by @{login}"'):
            m = MOVED.search(c.get('body') or '')
            if m and m.group(1).lower() == login.lower():
                copies.append(c)
    except Problem:
        copies = []
    gone = set()
    for c in copies:
        m = MOVED.search(c.get('body') or '')
        if m.group(2):
            gone.add(f'{m.group(2).lower()}#{m.group(3)}')

    def ref(it):
        return '/'.join((it.get('repository_url') or '').split('/')[-2:]).lower() + '#' + str(it.get('number'))
    rows = [_row(it) for it in mine + copies if ref(it) not in gone]
    seen = {r['id'] for r in rows}
    with LOCK:
        for k, (t, item) in list(_just_sent.items()):
            if time.time() - t > 180:
                _just_sent.pop(k, None)
            elif k not in seen:
                rows.append(item)
    return rows


def refresh_local(sent, now=None):
    """Ask the relay how local reports are doing: GET {relay}/v1/status?receipt=A,B (<= 50 per call)
    -> {results: {receipt: {status_key, line, title, kind, updated, done_at}}}. Keeps the last answer on any failure."""
    if not relay_url():
        return sent
    now = now or time.time()
    due = [r for r in sent if isinstance(r, dict) and r.get('receipt') and r.get('status_key') not in ('done', 'not_planned')
           and now - r.get('checked', 0) >= CACHE_SECONDS]
    changed = False
    for i in range(0, len(due), RELAY_BATCH):
        batch = due[i:i + RELAY_BATCH]
        try:
            res = _relay_call('GET', '/v1/status?' + urlencode({'receipt': ','.join(r['receipt'] for r in batch)}), timeout=10).get('results')
        except (urllib.error.URLError, TimeoutError, OSError, ValueError, Problem):
            continue
        if not isinstance(res, dict):
            continue
        for rec in batch:
            d = res.get(rec['receipt'])
            if isinstance(d, dict) and d.get('status_key') == 'not_found':
                rec['status_key'] = 'received'
            elif isinstance(d, dict) and d.get('status_key') in STATUS:
                rec.update(status_key=d['status_key'], updated=d.get('updated'), done_at=d.get('done_at'),
                           closed_at=d.get('closed_at') or d.get('done_at'))
                if d.get('title'):
                    rec['title'] = str(d['title'])[:200]
                if d.get('kind') in ('bug', 'feature'):   # 0.3.1: the team may approve it as the other kind
                    rec['kind'] = 'bug' if d['kind'] == 'bug' else 'idea'
                note = d.get('team_note')
                if isinstance(note, str) and note.strip() and d['status_key'] == 'not_planned':
                    rec['team_note'] = _plain(note)[:TEAM_NOTE_MAX]   # the reviewer's reason, shown as 'From the team'
                else:
                    rec.pop('team_note', None)
            rec['checked'] = now
            changed = True
    if changed:
        with LOCK:
            _write(DATA / 'sent.json', sent)
    return sent


def feedback_list(tz=None):
    remember_tz(tz)
    acct = account()
    local = [_local_row(r) for r in refresh_local(_read(DATA / 'sent.json', [])) if isinstance(r, dict) and r.get('id')]
    if acct:
        rows, source = list_github() + local, 'github'
    else:
        rows, source = local, 'local'
    rows.sort(key=lambda r: r.get('created') or '', reverse=True)
    return {'ok': True, 'source': source, 'signed_in': bool(acct), 'login': (acct or {}).get('login'), 'items': rows}


DETAIL = '''query($id:ID!){node(id:$id){... on Issue{id title body state stateReason createdAt closedAt
 labels(first:20){nodes{name}} author{login}
 comments(last:40){nodes{author{login} body createdAt}}
 timelineItems(first:100, itemTypes:[LABELED_EVENT]){nodes{... on LabeledEvent{createdAt label{name}}}}}}}'''
STEP_LABELS = {'building': 1, 'ai-review': 2, 'ready-to-test': 2}
FOOT = re.compile(r'\n+---\n+Filed with (?:Report or request|Feedback)[\s\S]*$')


def _plain(body):
    """The words people wrote: no hidden notes, no image links, no auto-filled footer."""
    b = re.sub(r'<!--[\s\S]*?-->', '', body or '')
    b = FOOT.sub('', b)
    b = re.sub(r'\n*Filed by @[\w-]+ with (?:Report or request|Feedback)[^\n]*', '', b)
    b = re.sub(r'!\[[^\]]*\]\([^)]*\)', '', b)
    return re.sub(r'\n{3,}', '\n\n', b).strip()


TEAM_NOTE_MAX = 500
REASON_MARKER = '<!-- feedback-reason -->'
# The Tracker's Drop / Not a bug comment: 'Dropped (not needed) — Name (@login)\n\nWhy: <reason>[\n\nBuilder: …]'
DROP_NOTE = re.compile(r'^(?:Not a bug / won\u2019t fix|Dropped \(not needed\)) \u2014 [^\n]*\n\nWhy: ([\s\S]+?)(?:\n\nBuilder:[\s\S]*)?$')


def reason_of(login, body):
    """The decline reason in a comment, or '': the relay's '<!-- feedback-reason -->' comment (bot) or the Why: of a
    Tracker Drop / Not a bug comment (a person, even one on the skip list). Only the reason text."""
    b = (body or '').replace('\r\n', '\n')
    low = (login or '').lower()
    if REASON_MARKER in b and low.endswith('[bot]'):
        return _plain(b.split(REASON_MARKER, 1)[1])[:TEAM_NOTE_MAX]
    m = DROP_NOTE.match(b.strip())
    if m and not low.endswith('[bot]'):
        return _plain(m.group(1))[:TEAM_NOTE_MAX]
    return ''


BOTS = {'wizardosbuilder', 'oscarhenrycollins', 'defleur-agents', 'github-actions', BUILDER} | _csv_env('FEEDBACK_SKIP_ACCOUNTS')


def human(login):
    """Team comments come from people: builder and bot accounts are skipped."""
    low = (login or '').lower()
    return bool(low) and low not in BOTS and not low.endswith('[bot]')


def _who(login, me):
    low = (login or '').lower()
    return 'You' if low == (me or '').lower() else 'The team'


def _steps(st, reached=None):
    """The four steps with state done/now/next ('off' for all when declined) and `at` = when each was reached
    (Received = sent; Done = closed; middle steps from the label events when known)."""
    reached = reached or {}
    return [{'key': k, 'label': lab, 'at': reached.get(i) if (st['step'] >= i or i == 0) else None,
             'state': 'off' if st['step'] < 0 else 'done' if i < st['step'] or (i == 3 and st['step'] == 3)
             else 'now' if i == st['step'] else 'next'} for i, (k, lab) in enumerate(STEPS)]


def timeline(st, created=None, events=()):
    """A friendly vertical timeline: one entry per step reached, each with its date, then what comes next.
    Declined: Received, then 'Declined by the team' with its day and time."""
    reached = {0: created}
    for name, when in sorted(events, key=lambda e: e[1] or ''):
        i = STEP_LABELS.get(str(name).lower())
        if i and i <= max(st['step'], 0) and not reached.get(i):
            reached[i] = when
    if st['key'] == 'done':
        reached[3] = st['at']
    if st['key'] == 'not_planned':
        return [{'key': 'received', 'label': 'Received', 'state': 'done', 'at': created},
                {'key': 'declined', 'label': STATUS['not_planned'][0], 'state': 'declined', 'at': st['at']}]
    return _steps(st, reached)


def feedback_get(fid):
    fid = str(fid or '')
    if fid.startswith('local-'):
        rec = next((r for r in _read(DATA / 'sent.json', []) if r.get('id') == fid), None)
        if not rec:
            raise Problem(404, 'That feedback is not on this Wizard.')
        row = _local_row(rec)
        st = keyed(row['status_key'], row['status_at'])
        tn = rec.get('team_note') if st['key'] == 'not_planned' and isinstance(rec.get('team_note'), str) and rec['team_note'].strip() else None
        return {**row, 'text': rec.get('text') or '', 'comments': [], 'steps': timeline(st, rec.get('created')),
                'note': DECLINED_NOTE if st['key'] == 'not_planned' else '',
                'team_note': {'who': 'The team', 'when': row['status_at'], 'text': tn} if tn else None}
    if not re.fullmatch(r'[A-Za-z0-9_=-]{6,80}', fid):
        raise Problem(400, 'Unknown feedback id.')
    if not account():
        raise Problem(401, SIGN_IN_FIRST, 'signed_out')
    node = ((gh('GRAPHQL', '', {'query': DETAIL, 'variables': {'id': fid}}) or {}).get('data') or {}).get('node')
    if not node:
        raise Problem(404, 'That feedback could not be found. It may have been removed.')
    labels = [n['name'] for n in (node.get('labels') or {}).get('nodes') or []]
    st = status_of(node.get('state'), node.get('stateReason'), labels, node.get('closedAt'))
    me = account()['login']
    people, reasons = [], []
    for c in (node.get('comments') or {}).get('nodes') or []:
        who = (c.get('author') or {}).get('login') or ''
        why = reason_of(who, c.get('body'))
        if why and who.lower() != (me or '').lower():   # 0.3.1: a decline reason (relay or Tracker Drop) is the team's note
            reasons.append({'who': 'The team', 'when': c.get('createdAt'), 'text': why})
            continue
        text = _plain(c.get('body'))
        if not text or not human(who):
            continue
        people.append({'who': _who(who, me), 'when': c.get('createdAt'), 'text': text[:4000]})
    people.sort(key=lambda c: c.get('when') or '')
    events = [(((e or {}).get('label') or {}).get('name') or '', (e or {}).get('createdAt'))
              for e in (node.get('timelineItems') or {}).get('nodes') or []]
    team_note = None
    if st['key'] == 'not_planned' and reasons:   # 0.3.1: the reviewer's written reason wins
        team_note = sorted(reasons, key=lambda c: c.get('when') or '')[-1]
    elif st['key'] == 'not_planned':   # the team's closing word, if a person left one: shown with the declined line
        last = [c for c in people if c['who'] == 'The team']
        if last:
            team_note = last[-1]
            people = [c for c in people if c is not team_note]
    return {'id': node['id'], 'kind': kind_of(labels), 'title': node.get('title') or '', 'created': node.get('createdAt'),
            'text': _plain(node.get('body')), **_status_fields(st), 'steps': timeline(st, node.get('createdAt'), events),
            'note': DECLINED_NOTE if st['key'] == 'not_planned' else '', 'team_note': team_note,
            'comments': people[-12:], 'source': 'github'}


def check_status(args):
    """POST /api/status-check: the status mapping on given facts (verify round trip, agents' self-check)."""
    cases = args.get('cases')
    if not isinstance(cases, list) or len(cases) > 50:
        raise Problem(400, 'Send {"cases": [{"state", "state_reason", "labels", "closed_at"}]}.')
    return {'results': [status_of(c.get('state'), c.get('state_reason'), c.get('labels') or [], c.get('closed_at')) for c in cases if isinstance(c, dict)]}


# ---------- the unsent draft (survives a reload or an update) ----------

def draft_get():
    d = _read(DATA / 'draft.json', {})
    return {'kind': d.get('kind') if d.get('kind') in KINDS else None, 'text': str(d.get('text') or '')[:8000]}


def draft_set(args):
    kind = args.get('kind')
    kind = 'feature' if kind == 'idea' else kind
    text = args.get('text') if isinstance(args.get('text'), str) else ''
    doc = {'kind': kind if kind in KINDS else None, 'text': text[:8000]}
    with LOCK:
        _write(DATA / 'draft.json', doc)
    return doc


# ---------- MCP ----------

TOOLS = [
    {'name': 'feedback_send', 'description': "Send the owner's feedback: kind 'bug' (something's broken) or 'feature' (something new), and their words "
     "(the first sentence becomes the title). Filed as the owner's own GitHub account, or through the Wizard relay (a person on the team "
     "reviews it first) when not signed in or not on the team. If it says to sign in, ask the owner to open Feedback and tap Sign in "
     "with GitHub; never ask for a token.",
     'inputSchema': {'type': 'object', 'properties': {'kind': {'type': 'string', 'enum': ['bug', 'feature', 'idea']}, 'text': {'type': 'string', 'minLength': 8, 'maxLength': 8000}},
                     'required': ['kind', 'text']}},
    {'name': 'feedback_list', 'description': "The owner's own feedback, newest first: id, title, kind (bug/idea) and a plain status line.",
     'inputSchema': {'type': 'object', 'properties': {}}},
    {'name': 'feedback_get', 'description': 'One feedback item by id (from feedback_list): full text, progress steps and comments from the team.',
     'inputSchema': {'type': 'object', 'properties': {'id': {'type': 'string'}}, 'required': ['id']}},
]
INSTRUCTIONS = ("Feedback files the owner's bug reports and requests and shows how each is doing. If a tool says to sign in, "
                "ask the owner to open Feedback and tap Sign in with GitHub. Never ask for a token; never run wam or docker.")


def rpc(msg):
    if not isinstance(msg, dict):
        return {'jsonrpc': '2.0', 'id': None, 'error': {'code': -32600, 'message': 'Expected a JSON-RPC object.'}}
    m, i, p = msg.get('method'), msg.get('id'), msg.get('params') or {}
    if 'id' not in msg:
        return None
    try:
        if m == 'initialize':
            r = {'protocolVersion': '2025-06-18', 'capabilities': {'tools': {}}, 'serverInfo': {'name': APP_ID, 'version': VERSION}, 'instructions': INSTRUCTIONS}
        elif m == 'tools/list':
            r = {'tools': TOOLS}
        elif m == 'tools/call':
            a = p.get('arguments') or {}
            n = p.get('name')
            if n == 'feedback_send':
                value = send({'kind': a.get('kind'), 'text': a.get('text'), 'device': 'Sent by the agent'})
            elif n == 'feedback_list':
                value = feedback_list()
            elif n == 'feedback_get':
                value = feedback_get(a.get('id'))
            else:
                return {'jsonrpc': '2.0', 'id': i, 'error': {'code': -32602, 'message': 'Unknown tool.'}}
            r = {'content': [{'type': 'text', 'text': json.dumps(value)}], 'structuredContent': value, 'isError': False}
        elif m == 'ping':
            r = {}
        else:
            return {'jsonrpc': '2.0', 'id': i, 'error': {'code': -32601, 'message': 'Unknown method.'}}
    except Problem as e:
        r = {'content': [{'type': 'text', 'text': e.message}], 'isError': True}
    return {'jsonrpc': '2.0', 'id': i, 'result': r}


# ---------- HTTP ----------

def _csv(name):
    return {x.strip().lower() for x in os.environ.get(name, '').split(',') if x.strip()}


# Trust never comes from the request itself: the Host must be one this app was configured for (stops DNS rebinding),
# and a browser Origin on writes must be configured too. Loopback on any port (wam's health check after a rename).
_LOOPBACK_HOST = re.compile(r'^(127\.0\.0\.1|localhost)(:\d+)?$')
_LOOPBACK_ORIGIN = re.compile(r'^http://(127\.0\.0\.1|localhost)(:\d+)?$')
TRUSTED_HOSTS = _csv('WIZARD_TRUSTED_HOSTS') | {'feedback:8799'}
TRUSTED_NAMES = {h for h in TRUSTED_HOSTS if ':' not in h}   # a bare name is trusted on every port (:8444 and :8796)
TRUSTED_ORIGINS = _csv('WIZARD_TRUSTED_ORIGINS')


class Handler(BaseHTTPRequestHandler):
    def _host_ok(self, host):
        return host in TRUSTED_HOSTS or host.rsplit(':', 1)[0] in TRUSTED_NAMES or bool(_LOOPBACK_HOST.match(host))

    def refused(self, write=False):
        host = (self.headers.get('Host') or '').lower()
        if not self._host_ok(host):
            self.send(403, {'error': 'Unknown host.'})
            return True
        if write:
            origin = (self.headers.get('Origin') or '').lower()
            same = origin.split('://', 1)[-1] == host
            if origin and not same and origin not in TRUSTED_ORIGINS and not _LOOPBACK_ORIGIN.match(origin):
                self.send(403, {'error': 'Cross-origin writes refused.'})
                return True
            if not origin and (self.headers.get('Sec-Fetch-Site') or '') == 'cross-site':
                self.send(403, {'error': 'Cross-origin writes refused.'})
                return True
        return False

    def send(self, code, body=None, content_type='application/json'):
        b = body if isinstance(body, bytes) else b'' if body is None else json.dumps(body).encode()
        self.send_response(code)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(b)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'self'; img-src 'self' data: blob:; object-src 'none'; base-uri 'self'; frame-ancestors 'self'")
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(b)

    def fail(self, e):
        self.send(e.code, {'error': e.message, 'reason': e.reason})

    def do_GET(self):
        if self.refused():
            return
        u = urlsplit(self.path)
        path, q = u.path, parse_qs(u.query)
        routes = {
            '/healthz': lambda: {'ok': True, 'app': APP_ID, 'version': VERSION},
            '/api/status': status,
            '/api/feedback': lambda: feedback_list(q.get('tz', [''])[0]),
            '/api/feedback/item': lambda: feedback_get(q.get('id', [''])[0]),
            '/api/device': device_status,
            '/api/switches': switches_doc,
            '/api/draft': draft_get,
        }
        if path in routes:
            try:
                return self.send(200, routes[path]())
            except Problem as e:
                return self.fail(e)
        name = 'index.html' if path == '/' else path[1:]
        kind = STATIC_TYPES.get(Path(name).suffix)
        if kind and '/' not in name and not name.startswith('.') and (ROOT / 'web' / name).is_file():
            return self.send(200, (ROOT / 'web' / name).read_bytes(), kind)
        if path == '/openapi.json':
            return self.send(200, (ROOT / 'openapi.json').read_bytes())
        self.send(405 if path == '/mcp' else 404, {'error': 'Use POST /mcp.' if path == '/mcp' else 'Not found.'})

    def do_HEAD(self):
        self.do_GET()

    def do_POST(self):
        if self.refused(write=True):
            return
        path = urlsplit(self.path).path
        if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
            return self.send(415, {'error': 'Use application/json.'})
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 <= size <= (BODY_MAX if path == '/api/feedback' else 65536):
                return self.send(413, {'error': 'Too big. Use fewer or smaller screenshots.'})
            body = json.loads(self.rfile.read(size))
            if path == '/mcp':
                if not isinstance(body, dict):
                    return self.send(400, {'error': 'Expected a JSON-RPC object.'})
                out = rpc(body)
                return self.send(202 if out is None else 200, out)
            if not isinstance(body, dict):
                return self.send(400, {'error': 'Expected a JSON object.'})
            routes = {
                '/api/feedback': lambda: send(body),
                '/api/device/start': device_start,
                '/api/device/cancel': device_cancel,
                '/api/signout': sign_out,
                '/api/switches': lambda: set_switch(body),
                '/api/status-check': lambda: check_status(body),
                '/api/draft': lambda: draft_set(body),
            }
            if path not in routes:
                return self.send(404, {'error': 'Not found.'})
            if path == '/api/switches' and self.headers.get('X-Wizard-Switch') != '1':
                return self.send(403, {'error': 'Switch changes need the X-Wizard-Switch: 1 header.'})
            return self.send(201 if path == '/api/feedback' else 200, routes[path]())
        except Problem as e:
            self.fail(e)
        except (ValueError, TypeError, AttributeError):
            self.send(400, {'error': 'Invalid JSON request.'})

    def log_message(self, format, *args):
        pass


if __name__ == '__main__':
    DATA.mkdir(parents=True, exist_ok=True)
    _private()
    ThreadingHTTPServer((os.environ.get('BIND', '0.0.0.0'), int(os.environ.get('PORT', '8799'))), Handler).serve_forever()
