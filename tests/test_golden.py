"""Every response a caller can observe is byte for byte what the patched bridge (commit 9bbb49b + Part A) sent.

tests/fixtures/golden_responses.json was recorded with tests/golden.py before the hardening work. Times and log
references are placeholders. The one intended difference: a NaN or infinity in a JSON answer is now null (C7), because
"NaN" is not valid JSON.
"""
import json
import pathlib
import unittest

import golden

FIXTURE = pathlib.Path(__file__).resolve().parent / "fixtures" / "golden_responses.json"
INTENDED = [(':NaN', ':null'), (':Infinity', ':null'), (':-Infinity', ':null')]   # C7


class Golden(unittest.TestCase):
    def test_responses_unchanged(self):
        import app as bridge
        import hana
        expected = json.loads(FIXTURE.read_text(encoding="utf-8"))
        orig = (hana.run, getattr(hana, "batches", None), hana.ping)
        try:
            got = golden.run_capture()
        finally:
            hana.run, hana.batches, hana.ping = orig
            bridge.reset_state()
        self.assertEqual([g["request"] for g in got], [e["request"] for e in expected])
        for e, g in zip(expected, got):
            body = e["body"]
            for old, new in INTENDED:
                body = body.replace(old, new)
            with self.subTest(e["request"]):
                self.assertEqual(g["status"], e["status"])
                self.assertEqual(g["headers"], e["headers"])
                self.assertEqual(g["body"], body)


if __name__ == "__main__":
    unittest.main()
