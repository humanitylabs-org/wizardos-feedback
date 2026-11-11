"""Feedback 0.3.1: decline reasons ('From the team'), kind changes, flat page + filter chips.
Run: python3 -m unittest discover -s tests"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault('DATA_DIR', tempfile.mkdtemp())
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'app'))
import server  # noqa: E402

WEB = ROOT / 'web'


class RelayNote(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.p = [mock.patch.object(server, 'DATA', self.d), mock.patch.object(server, 'relay_url', lambda: 'https://relay.test')]
        for p in self.p:
            p.start()

    def tearDown(self):
        for p in self.p:
            p.stop()

    def refresh(self, answer, kind='bug'):
        sent = [{'id': 'local-r1', 'receipt': 'r1', 'kind': kind, 'title': 'T', 'text': 'words words', 'created': '2026-11-11T01:00:00Z',
                 'status_key': 'waiting_review'}]
        with mock.patch.object(server, '_relay_call', lambda *a, **k: {'results': {'r1': answer}}):
            out = server.refresh_local(sent, now=10 ** 10)
        server._write(self.d / 'sent.json', out)
        return out[0]

    def test_declined_with_reason(self):
        rec = self.refresh({'status_key': 'not_planned', 'line': 'Declined by the team', 'kind': 'bug', 'closed_at': '2026-11-11T02:00:00Z',
                            'team_note': 'Already works in 0.3.170 <!-- x --> — update first.'})
        self.assertEqual(rec['team_note'], 'Already works in 0.3.170  — update first.')
        d = server.feedback_get('local-r1')
        self.assertEqual(d['team_note']['text'], rec['team_note'])
        self.assertEqual(d['team_note']['who'], 'The team')
        self.assertTrue(d['note'])

    def test_no_reason_no_note(self):
        self.refresh({'status_key': 'not_planned', 'closed_at': '2026-11-11T02:00:00Z'})
        self.assertIsNone(server.feedback_get('local-r1')['team_note'])

    def test_note_only_when_declined(self):
        rec = self.refresh({'status_key': 'building', 'team_note': 'leak?'})
        self.assertNotIn('team_note', rec)

    def test_kind_follows_the_team(self):
        rec = self.refresh({'status_key': 'received', 'kind': 'feature'}, kind='bug')
        self.assertEqual(rec['kind'], 'idea')
        rows = server.feedback_list()['items'] if not server.account() else []
        self.assertTrue(not rows or rows[0]['kind'] == 'idea')
        rec = self.refresh({'status_key': 'received', 'kind': 'bug'}, kind='idea')
        self.assertEqual(rec['kind'], 'bug')


class SignedInReason(unittest.TestCase):
    def test_reason_of(self):
        R = server.reason_of
        self.assertEqual(R('wizardos-feedback-relay[bot]', 'Declined by @rev.\n\n<!-- feedback-reason -->\nNot for now.'), 'Not for now.')
        self.assertEqual(R('someone', 'Declined.\n\n<!-- feedback-reason -->\nfake'), '')
        drop = 'Dropped (not needed) \u2014 Miguel (@miguel)\n\nWhy: Settings already does this\n\nBuilder: its code is merged behind its switch.'
        self.assertEqual(R('miguel', drop), 'Settings already does this')
        self.assertEqual(R('oscarhenrycollins', 'Not a bug / won\u2019t fix \u2014 Oscar (@oscarhenrycollins)\n\nWhy: Works as designed'), 'Works as designed')
        self.assertEqual(R('miguel', 'just a comment\n\nWhy: no'), '')

    def node(self, comments):
        return {'data': {'node': {'id': 'I_abc123', 'title': 'T', 'body': 'b', 'state': 'CLOSED', 'stateReason': 'NOT_PLANNED',
                                  'createdAt': '2026-11-11T01:00:00Z', 'closedAt': '2026-11-11T02:00:00Z',
                                  'labels': {'nodes': [{'name': 'feature'}]}, 'author': {'login': 'me'},
                                  'comments': {'nodes': comments}, 'timelineItems': {'nodes': []}}}}

    def get(self, comments):
        with mock.patch.object(server, 'account', lambda: {'login': 'me', 'token': 'x'}), \
                mock.patch.object(server, 'gh', lambda *a, **k: self.node(comments)):
            return server.feedback_get('I_abc123')

    def test_tracker_drop_by_skip_listed_account_shows(self):
        d = self.get([{'author': {'login': 'oscarhenrycollins'}, 'createdAt': '2026-11-11T01:59:00Z',
                       'body': 'Dropped (not needed) \u2014 Oscar (@oscarhenrycollins)\n\nWhy: We have this already'}])
        self.assertEqual(d['team_note']['text'], 'We have this already')
        self.assertEqual(d['comments'], [])

    def test_relay_reason_beats_older_chat(self):
        d = self.get([{'author': {'login': 'miguel'}, 'createdAt': '2026-11-11T01:10:00Z', 'body': 'Looking at it'},
                      {'author': {'login': 'wizardos-feedback-relay[bot]'}, 'createdAt': '2026-11-11T01:59:00Z',
                       'body': 'Declined by @miguel.\n\n<!-- feedback-reason -->\nNot something we will build.'}])
        self.assertEqual(d['team_note']['text'], 'Not something we will build.')
        self.assertEqual([c['text'] for c in d['comments']], ['Looking at it'])

    def test_closing_comment_by_a_person_still_works(self):
        d = self.get([{'author': {'login': 'miguel'}, 'createdAt': '2026-11-11T01:59:00Z', 'body': 'Out of scope, sorry.'}])
        self.assertEqual(d['team_note']['text'], 'Out of scope, sorry.')


class Page(unittest.TestCase):
    html = (WEB / 'index.html').read_text()
    js = (WEB / 'app.js').read_text()
    css = (WEB / 'styles.css').read_text()

    def test_version(self):
        self.assertEqual(server.VERSION, '0.3.1')
        self.assertEqual(json.loads((ROOT / 'runtipi/config.json').read_text())['version'], '0.3.1')
        self.assertIn("version: '0.3.1'", self.js)
        self.assertIn('version: 0.3.1', (ROOT / 'wizard.md').read_text())

    def test_no_welcome_card(self):
        for gone in ('We’re listening', 'Tell us what’s broken', 'fb-hero', 'fb-empty-icon'):
            self.assertNotIn(gone, self.html + self.css)
        self.assertIn('id="report-open">Report or request</button>', self.html)

    def test_flat_rows(self):
        self.assertIn('.fb-list > li + li { border-top: 1px solid var(--line); }', self.css)
        row = self.css[self.css.index('.fb-row {'):].split('}')[0]
        self.assertIn('border: 0', row)
        self.assertIn('background: none', row)
        self.assertNotIn('wz-sec fb-mine', self.html)

    def test_filters(self):
        for w in ("['all', 'All']", "['open', 'Open']", "['done', 'Done']", "['declined', 'Declined']", "['bug', 'Bugs']", "['idea', 'Ideas']"):
            self.assertIn(w, self.js)
        self.assertIn("localStorage.setItem(FKEY", self.js)
        self.assertIn('bar.scrollWidth <= bar.clientWidth + 1', self.js)
        self.assertIn('id="empty-filter"', self.html)

    def test_team_note_words(self):
        self.assertIn('From the team:', self.html)
        self.assertIn('`“${tn.text}”`', self.js)

    def test_js_parses(self):
        r = subprocess.run(['node', '--check', str(WEB / 'app.js')], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == '__main__':
    unittest.main()
