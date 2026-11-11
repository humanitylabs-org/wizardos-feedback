---
wizard: 0.2
id: feedback
name: Feedback
type: service
category: app
version: 0.1.0
summary: Report a bug or ask for something new, and follow your own feedback in plain words.
license: GPL-3.0-only
source: https://github.com/humanitylabs-org/wizardos-feedback
runtime:
  kind: runtipi
  config: runtipi/config.json
  compose: runtipi/docker-compose.yml
api:
  port: 8799
  path: /api
  spec: openapi.json
mcp:
  path: /mcp
  port: 8799
  transport: streamable-http
skill: skill/SKILL.md
view:
  path: /
  port: 8799
  source:
    path: web
    build: python3 app/build_view.py
    dist: web
provides:
- feedback
system: false
uses: []
needs: []
health: /healthz
switches: /api/switches
---

# Feedback

One screen: a big **Report or request** button (two steps: Something's broken / I'd like something new, then one text
box and optional screenshots) and **My feedback**, newest first, each with Bug/Idea and one plain status line. Tap a
row for the full text, a four-step progress (Received → Being built → Being checked → Done) and comments from people
on the team (builder and bot accounts are skipped). No repo names, labels or PR numbers are shown.

## Sending
- **Signed in with GitHub, member of humanitylabs-org**: filed exactly like the WizardOS Tracker reporter, in
  `humanitylabs-org/wizard-os` with labels `triage` + `bug`/`feature`, as the person. The builder moves it on.
  History is `org:humanitylabs-org author:<login> is:issue` (60 s cache), so Tracker-filed items show too.
- **Not signed in, or not on the team**: sends only through the relay, behind the app switch `anonymous` (off by
  default) *and* a relay address (`FEEDBACK_RELAY_URL` env or `relay_url` in `DATA_DIR/policy.json`). Otherwise the
  screen says "Sign in with GitHub to send feedback" (non-members: "Feedback from outside the team opens soon").
  Relay-sent items are kept on this box in `DATA_DIR/sent.json` (id, kind, title, created, receipt).

## Relay contract (client side; fixed)
- `POST {relay}/v1/report` `{kind, text, wizard_version?, device?, gh_token?, screenshots? (≤3, ≤2 MB, {type, data base64})}`
  → `{receipt, status_key, line}`. Signed-in non-members pass `gh_token`.
- `GET {relay}/v1/status?receipt=A,B` (≤50) → `{results: {receipt: {status_key, line, title, kind, updated, done_at}}}`.
- status_keys: `waiting_review, received, queued, building, checking, ready, done, not_planned`.

## Status mapping
`status_of()` in app/server.py is the one mapping (tests: `python3 -m unittest discover -s tests`). Closed completed →
Done ✓ (date); closed not planned → Not planned; `ready-to-test` → Ready for the team to try; `ai-review` → Being
checked; `building` → Being built; `triage` → Received — sorting it; no stage label → In line to be built.

The GitHub token stays on this box (`DATA_DIR/private/account.json`, 0600); it is never returned to the view or MCP.
Appearance: shared kit only ([style guide](https://github.com/humanitylabs-org/wizard-os/blob/main/style/APP-DESIGN-RULES.md)).
