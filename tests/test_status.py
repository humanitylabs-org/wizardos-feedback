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
        self.assertEqual(S('open', None, ['triage', 'bug'])['line'], 'Got it — the team is sorting it')
        self.assertEqual(S('open', None, ['feature'])['line'], 'Coming up — in line to be built')
        self.assertEqual(S('open', None, [{'name': 'Building'}])['line'], 'Being built now')
        self.assertEqual(S('open', None, ['ai-review', 'building'])['line'], 'Being tested')
        self.assertEqual(S('open', None, ['ready-to-test', 'ai-review'])['line'], 'Almost there — the team is trying it out')
        self.assertEqual(S('open', None, ['needs-review', 'bug'])['line'], 'Waiting for the team to look at it')

    def test_closed(self):
        tz = server._tzinfo()
        this_year = server.dt.datetime.now(tz).year
        done = S('closed', 'completed', ['building'], f'{this_year}-11-10T08:00:00Z')
        self.assertEqual((done['key'], done['line'], done['step']), ('done', 'Done ✓ · 10 Nov', 3))
        self.assertEqual(S('closed', 'completed', [], '2019-03-02T08:00:00Z')['line'], 'Done ✓ · 2 Mar 2019')
        self.assertEqual(S('closed', None, [])['line'], 'Done ✓')
        self.assertEqual(S('closed', 'duplicate', [])['key'], 'not_planned')
        no = S('CLOSED', 'not_planned', [], f'{this_year}-11-11T02:52:00Z')
        self.assertEqual((no['key'], no['step'], no['base'], no['show']), ('not_planned', -1, 'Declined by the team', 'datetime'))
        self.assertTrue(no['line'].startswith('Declined by the team · 11 Nov, '), no['line'])
        self.assertNotIn('Not planned', no['line'])

    def test_rejected_inbox_item_is_declined(self):
        self.assertEqual(S('closed', 'completed', ['rejected', 'bug'])['key'], 'not_planned')
        self.assertEqual(S('open', None, ['rejected'])['line'], 'Declined by the team')

    def test_local_time(self):
        import zoneinfo
        bkk = zoneinfo.ZoneInfo('Asia/Bangkok')
        now = server.dt.datetime(2026, 11, 11, tzinfo=bkk)
        self.assertEqual(server._when('2026-11-11T02:52:00Z', now=now, tz=bkk), '11 Nov, 9:52')
        self.assertEqual(server._when('2025-12-31T20:05:00Z', now=now, tz=bkk), '1 Jan, 3:05')
        self.assertEqual(server._when('2024-06-01T00:00:00Z', False, now=now, tz=bkk), '1 Jun 2024')
        self.assertEqual(server._when('nonsense'), '')

    def test_timeline(self):
        st = S('closed', 'not_planned', [], '2026-11-11T03:03:08Z')
        tl = server.timeline(st, '2026-11-11T02:34:52Z')
        self.assertEqual([(t['key'], t['state'], t['at']) for t in tl],
                         [('received', 'done', '2026-11-11T02:34:52Z'), ('declined', 'declined', '2026-11-11T03:03:08Z')])
        st = S('open', None, ['ai-review'])
        tl = server.timeline(st, 'c', [('building', 'b1'), ('ai-review', 'a1'), ('building', 'b2')])
        self.assertEqual([(t['state'], t['at']) for t in tl], [('done', 'c'), ('done', 'b1'), ('now', 'a1'), ('next', None)])
        done = S('closed', 'completed', [], '2026-11-10T08:00:00Z')
        self.assertEqual(server.timeline(done, 'c')[-1]['at'], '2026-11-10T08:00:00Z')

    def test_steps(self):
        self.assertEqual([S('open', None, l)['step'] for l in (['triage'], [], ['building'], ['ai-review'], ['ready-to-test'])], [0, 0, 1, 2, 2])
        steps = server._steps(S('open', None, ['building']))
        self.assertEqual([s['state'] for s in steps], ['done', 'now', 'next', 'next'])
        self.assertTrue(all(s['state'] == 'off' for s in server._steps(S('closed', 'not_planned', []))))

    def test_relay_keys(self):
        for k in ('waiting_review', 'received', 'queued', 'building', 'checking', 'ready', 'done', 'not_planned'):
            self.assertEqual(server.keyed(k)['key'], k)
        self.assertEqual(server.keyed('waiting_review')['line'], 'Waiting for the team to look at it')
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
