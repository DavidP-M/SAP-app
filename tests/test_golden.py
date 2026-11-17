"""Every response a caller can observe is byte for byte what the patched bridge (commit 9bbb49b + Part A) sent.

tests/fixtures/golden_responses.json was recorded with tests/golden.py before the hardening work. Times and log
references are placeholders. The one intended difference: a NaN or infinity in a JSON answer is now null (C7), because
"NaN" is not valid JSON. The catalog also lists the six Query Manager reports added later (reports.py); they are
compared apart, so the rest of the catalog must still be byte for byte the recorded one.
"""
import json
import pathlib
import unittest

import golden

FIXTURE = pathlib.Path(__file__).resolve().parent / "fixtures" / "golden_responses.json"
INTENDED = [(':NaN', ':null'), (':Infinity', ':null'), (':-Infinity', ':null')]   # C7
ADDED_REPORTS = ["sales-orders", "sales-deliveries", "purchase-orders", "po-receipts", "production-kitting",
                 "mrp-forecast"]


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
            got_body = g["body"]
            if e["request"] == "GET /api/catalog keyed":
                catalog = json.loads(got_body)
                self.assertEqual(list(catalog["reports"])[-len(ADDED_REPORTS):], ADDED_REPORTS)
                for name in ADDED_REPORTS:
                    del catalog["reports"][name]
                got_body = json.dumps(catalog, separators=(",", ":"), ensure_ascii=False) + "\n"
            with self.subTest(e["request"]):
                self.assertEqual(g["status"], e["status"])
                self.assertEqual(g["headers"], e["headers"])
                self.assertEqual(got_body, body)


if __name__ == "__main__":
    unittest.main()
