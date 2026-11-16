"""The real hana.connect(), hana.run() and hana.batches(), driven through a fake hdbcli.dbapi (tests/fakedb.py), and
the endpoints on top of them with nothing else replaced."""
import datetime
import decimal
import json
import unittest

import fakedb
from test_bridge import Base, bridge, hana

import config

MIXED_COLS = [("Time",), ("Nan",), ("Inf",), ("Big",), ("Tiny",), ("Null",), ("Blob",), ("Text",), ("Stamp",)]
MIXED_ROW = (datetime.time(7, 5, 9), float("nan"), float("-inf"), decimal.Decimal("123456789012345678901234.5"),
             decimal.Decimal("0.000001000"), None, b"\x00\xab", "a\tb\nc", datetime.datetime(2026, 11, 12, 0, 0))


class Driver(Base):
    def setUp(self):
        super().setUp()
        hana.run, hana.batches = self._orig, self._orig_batches          # the real functions

    def test_mixed_types_through_tsv_and_json(self):
        with fakedb.installed(rows=[MIXED_ROW], description=MIXED_COLS):
            tsv = self.c.get("/api/reports/kpi", headers=self.h).data.decode()
            js = self.c.get("/api/reports/kpi?format=json", headers=self.h)
        self.assertEqual(tsv.split("\r\n")[1].split("\t"),
                         ["07:05:09", "", "", "123456789012345678901234.5", "0.000001", "", "00ab", "a b c",
                          "2026-11-12"])
        row = json.loads(js.data)["rows"][0]                              # strict JSON: no NaN tokens
        self.assertEqual(row, {"Time": "07:05:09", "Nan": None, "Inf": None, "Big": 1.2345678901234568e23,
                               "Tiny": 1e-06, "Null": None, "Blob": "00ab", "Text": "a\tb\nc",
                               "Stamp": "2026-11-12 00:00:00"})

    def test_lookup_reads_max_rows_plus_one(self):
        rows = [(45001, i) for i in range(500)]
        with fakedb.installed(rows=rows, description=[("DocNum",), ("LineNum",)]) as db:
            r = self.c.get("/api/data/so_detail?docnum=45001", headers=self.h).json
        self.assertEqual(db.fetches, [("many", 201)])
        self.assertEqual(db.executed[0][1], (45001,))
        self.assertEqual((r["row_count"], r["truncated"], len(r["rows"])), (200, True, 200))

    def test_timeouts_and_tls_settings_reach_the_driver(self):
        s = config.from_env({"HANA_HOST": "h", "HANA_PORT": "30041", "HANA_CONNECT_TIMEOUT_MS": "7000",
                             "HANA_QUERY_TIMEOUT_MS": "60000", "HANA_ENCRYPT": "true"})
        with fakedb.installed(rows=[(1,)]) as db, hana.configured(s):
            hana.run("SELECT 1 AS A FROM DUMMY")
        kw = db.connect_kwargs
        self.assertEqual((kw["address"], kw["port"], kw["connectTimeout"], kw["communicationTimeout"]),
                         ("h", 30041, 7000, 60000))
        self.assertIs(kw["encrypt"], True)
        self.assertIs(kw["sslValidateCertificate"], True)

    def test_connection_is_closed_when_execute_fails(self):
        with fakedb.installed(fail=fakedb.Error(260, "invalid column name")) as db:
            r = self.c.get("/api/reports/kpi", headers=self.h)
            r2 = self.c.get("/api/data/so_detail?docnum=1", headers=self.h)
        self.assertEqual((r.status_code, r2.status_code), (502, 502))
        self.assertEqual(db.closed, 2)
        self.assertTrue(r.json["error"].startswith("The query failed in SAP HANA."))

    def test_batches_stop_early_and_close(self):
        bridge.reset_state(report_max_rows=3, fetch_batch_rows=2)
        rows = [(i,) for i in range(10)]
        with fakedb.installed(rows=rows, description=[("N",)]) as db:
            r = self.c.get("/api/reports/kpi", headers=self.h)
        self.assertEqual(r.status_code, 502)
        self.assertEqual(db.fetches, [("many", 2), ("many", 2)])           # stopped at 4 > 3, did not read on
        self.assertEqual(db.closed, 1)

    def test_ping(self):
        with fakedb.installed(rows=[(datetime.datetime(2026, 11, 12, 10, 42, 5), "SBO_TEST")],
                              description=[("ServerTime",), ("Schema",)]):
            r = self.c.get("/api/health", headers=self.h)
        self.assertEqual(r.json, {"status": "ok", "hana": {"ServerTime": "2026-11-12 10:42:05", "Schema": "SBO_TEST"}})


if __name__ == "__main__":
    unittest.main()
