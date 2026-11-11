"""The one status mapping: python3 -m unittest discover -s tests"""
import os
import sys
import tempfile
import unittest

os.environ.setdefault('DATA_DIR', tempfile.mkdtemp())
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))
import server  # noqa: E402

S = server.status_of


class StatusMapping(unittest.TestCase):
    def test_open_stages(self):
        self.assertEqual(S('open', None, ['triage', 'bug'])['line'], 'Received — sorting it')
        self.assertEqual(S('open', None, ['feature'])['line'], 'In line to be built')
        self.assertEqual(S('open', None, [{'name': 'Building'}])['line'], 'Being built')
        self.assertEqual(S('open', None, ['ai-review', 'building'])['line'], 'Being checked')
        self.assertEqual(S('open', None, ['ready-to-test', 'ai-review'])['line'], 'Ready for the team to try')

    def test_closed(self):
        done = S('closed', 'completed', ['building'], '2026-11-10T08:00:00Z')
        self.assertEqual((done['key'], done['line'], done['step']), ('done', 'Done ✓ (10 Nov 2026)', 3))
        self.assertEqual(S('CLOSED', 'not_planned', [])['line'], 'Not planned')
        self.assertEqual(S('closed', 'duplicate', [])['key'], 'not_planned')
        self.assertEqual(S('closed', None, [])['line'], 'Done ✓')

    def test_steps(self):
        self.assertEqual([S('open', None, l)['step'] for l in (['triage'], [], ['building'], ['ai-review'], ['ready-to-test'])], [0, 0, 1, 2, 2])
        steps = server._steps(S('open', None, ['building']))
        self.assertEqual([s['state'] for s in steps], ['done', 'now', 'next', 'next'])
        self.assertTrue(all(s['state'] == 'off' for s in server._steps(S('closed', 'not_planned', []))))

    def test_relay_keys(self):
        for k in ('waiting_review', 'received', 'queued', 'building', 'checking', 'ready', 'done', 'not_planned'):
            self.assertEqual(server.keyed(k)['key'], k)
        self.assertEqual(server.keyed('waiting_review')['line'], 'Waiting for a person to review')
        self.assertEqual(server.keyed('nonsense')['key'], 'unknown')

    def test_kind_and_title(self):
        self.assertEqual(server.kind_of(['triage', 'bug']), 'bug')
        self.assertEqual(server.kind_of(['feature']), 'idea')
        self.assertEqual(server.title_of('The clock is wrong. It shows UTC.'), 'The clock is wrong')

    def test_humans_only(self):
        for bot in ('wizardosbuilder', 'oscarhenrycollins', 'defleur-agents', 'github-actions', 'dependabot[bot]', ''):
            self.assertFalse(server.human(bot), bot)
        self.assertTrue(server.human('miguel-dev'))


if __name__ == '__main__':
    unittest.main()
