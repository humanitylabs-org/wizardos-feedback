---
wizard: 0.2
id: feedback
name: Feedback
type: service
category: app
version: 0.2.0
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
- **Signed in, not on the team** (or membership unknown): sends through the relay whenever a relay address is set
  (`FEEDBACK_RELAY_URL` env or `relay_url` in `DATA_DIR/policy.json`), passing `gh_token`.
- **Not signed in**: one-tap send through the relay when the address is set *and* the app switch `anonymous` is on
  (off by default). Under Send a soft line offers "Sign in with GitHub to follow it from any device and see replies
  from the team" (starts the same device-flow sign-in; never blocks sending). After sending: "Sent. A person on the
  team reviews it first." The relay decides who goes straight to the builder and who waits for a person
  ("Waiting for a person to review").
- Otherwise Send is off with the reason ("Sign in with GitHub to send feedback"; non-members without a relay:
  "Feedback from outside the team opens soon"). `route()` in app/server.py is the one decision (tests).
- Relay-sent items are kept on this box in `DATA_DIR/sent.json` (id, kind, title, created, receipt); signed out, the
  list says "Saved on this Wizard. Sign in with GitHub to see replies." Relay screenshots: ≤3, ≤2 MB each (bigger ones
  are shrunk to JPEG in the browser). Relay down/5xx/no answer: "Couldn't send right now. Your words are saved; try
  again in a minute." (form and screenshots kept); 429: "That's a lot of reports in a short time. Try again in a few
  minutes."

## Relay contract (client side; fixed)
- `POST {relay}/v1/report` `{kind, text, wizard_version?, device?, gh_token?, screenshots? (≤3, ≤2 MB, {type, data base64})}`
  → `{receipt, status_key, line}`. Signed-in non-members pass `gh_token`.
- `GET {relay}/v1/status?receipt=A,B` (≤50) → `{results: {receipt: {status_key, line, title, kind, updated, done_at}}}`.
- status_keys: `waiting_review, received, queued, building, checking, ready, done, not_planned`; `not_found` (a fresh
  report, ~1 min) shows as "Received — sorting it". Errors: JSON `{error, message}`; 429 on rate limit.

## Status mapping
`status_of()` in app/server.py is the one mapping (tests: `python3 -m unittest discover -s tests`). Closed completed →
Done ✓ (date); closed not planned → Not planned; `ready-to-test` → Ready for the team to try; `ai-review` → Being
checked; `building` → Being built; `triage` → Received — sorting it; no stage label → In line to be built.

The GitHub token stays on this box (`DATA_DIR/private/account.json`, 0600); it is never returned to the view or MCP.
Appearance: shared kit only ([style guide](https://github.com/humanitylabs-org/wizard-os/blob/main/style/APP-DESIGN-RULES.md)).

## Changelog
- **0.2.0**: Send without signing in through the relay (switch `anonymous`, default off) with a soft GitHub sign-in
  offer; signed-in non-members use the relay whenever it is set; plain relay-down and rate-limit messages that keep the
  form; relay screenshots capped at 3 × 2 MB (shrunk in the browser); "Saved on this Wizard" note on the signed-out list.
- **0.1.0**: Report or request + My feedback with plain status; GitHub sign-in path, relay path behind switch `anonymous`.
