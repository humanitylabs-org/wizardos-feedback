"""The one send-routing decision and relay error words: python3 -m unittest discover -s tests"""
import os
import sys
import tempfile
import unittest

os.environ.setdefault('DATA_DIR', tempfile.mkdtemp())
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))
import server  # noqa: E402

R = server.route


class Routing(unittest.TestCase):
    def test_member_goes_direct(self):
        for relay in (False, True):
            for anon in (False, True):
                self.assertEqual(R(True, True, relay, anon), ('github', ''))

    def test_non_member_uses_relay_when_set(self):
        self.assertEqual(R(True, False, True, False), ('relay', ''))
        self.assertEqual(R(True, None, True, False), ('relay', ''))
        self.assertEqual(R(True, False, False, True), (None, server.NOT_TEAM))
        self.assertEqual(R(True, None, False, False), (None, server.NO_MEMBER_CHECK))

    def test_signed_out_needs_relay_and_switch(self):
        self.assertEqual(R(False, None, True, True), ('relay', ''))
        self.assertEqual(R(False, None, True, False), (None, server.SIGN_IN_FIRST))
        self.assertEqual(R(False, None, False, True), (None, server.SIGN_IN_FIRST))
        self.assertEqual(R(False, None, False, False), (None, server.SIGN_IN_FIRST))

    def test_relay_errors_are_plain(self):
        e = server.relay_problem(429)
        self.assertEqual((e.code, e.message, e.reason), (429, server.RELAY_BUSY, 'rate_limited'))
        for code in (500, 502, 503):
            e = server.relay_problem(code)
            self.assertEqual((e.code, e.message, e.reason), (503, server.RELAY_DOWN, 'relay_down'))
        e = server.relay_problem(400, b'{"error": "too_short", "message": "Say a little more."}')
        self.assertEqual((e.code, e.message), (400, 'Say a little more.'))

    def test_not_found_reads_as_received(self):
        self.assertEqual(server.keyed('not_found')['line'], 'Got it — the team is sorting it')


if __name__ == '__main__':
    unittest.main()
