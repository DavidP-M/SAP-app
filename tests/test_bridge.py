"""Bridge tests with HANA mocked (no SAP connection needed).  Run:  python -m unittest discover tests"""
import csv
import datetime
import decimal
import os
import pathlib
import sys
import threading
import time
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.update(HANA_HOST="hana.test", HANA_USER="DASHBOARD_RO", HANA_PASSWORD="x", HANA_SCHEMA="SBO_TEST",
                  API_KEYS="key-dashboard,key-gateway")

import app as bridge  # noqa: E402
import hana  # noqa: E402

FIXTURE_CSV = ROOT / "tests" / "fixtures" / "synthetic_kpi_rows.csv"
DATE_COLS = {"ExportDate", "DocumentDate", "ClosedDate", "DueDate", "HeaderDueDate", "LineDueDate",
             "LineRescheduleDate", "RescheduleDate"}
NUM_COLS = {"DocEntry", "DocumentNumber", "LineNumber", "GroupCode", "Quantity", "OpenQuantity", "AmountLocal",
            "AmountForeign", "DaysPastDue"}


def synthetic_hana_rows():
    """The synthetic sample export, typed the way hdbcli returns HANA columns (date, Decimal, int, None)."""
    with open(FIXTURE_CSV, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        cols = reader.fieldnames
        rows = []
        for r in reader:
            out = []
            for c in cols:
                v = r[c].strip()
                if v == "":
                    out.append(None)
                elif c in DATE_COLS:
                    out.append(datetime.date.fromisoformat(v))
                elif c in NUM_COLS:
                    out.append(int(v) if c in {"DocEntry", "DocumentNumber", "LineNumber", "GroupCode", "DaysPastDue"}
                               else decimal.Decimal(v + ".000000"))
                else:
                    out.append(v)
            rows.append(tuple(out))
    return cols, rows


class Base(unittest.TestCase):
    def setUp(self):
        bridge._cache.clear()
        self.calls = []
        self.result = synthetic_hana_rows()

        def fake_run(sql, params=()):
            self.calls.append((sql, params))
            return self.result

        self._orig = hana.run
        hana.run = fake_run
        self.c = bridge.app.test_client()
        self.h = {"X-API-Key": "key-dashboard"}

    def tearDown(self):
        hana.run = self._orig


class Security(Base):
    def test_api_needs_key(self):
        self.assertEqual(self.c.get("/api/catalog").status_code, 401)
        self.assertEqual(self.c.get("/api/catalog", headers={"X-API-Key": "wrong"}).status_code, 401)
        self.assertEqual(self.c.get("/api/catalog", headers={"X-API-Key": "key-gateway"}).status_code, 200)

    def test_public_health_has_no_data(self):
        r = self.c.get("/health")
        self.assertEqual(r.json, {"status": "ok"})
        self.assertEqual(self.calls, [])

    def test_no_keys_configured_refuses(self):
        keep = bridge.API_KEYS
        bridge.API_KEYS = []
        try:
            self.assertEqual(self.c.get("/api/catalog", headers=self.h).status_code, 503)
        finally:
            bridge.API_KEYS = keep

    def test_unknown_names_404(self):
        self.assertEqual(self.c.get("/api/reports/drop_table", headers=self.h).status_code, 404)
        self.assertEqual(self.c.get("/api/data/../../etc", headers=self.h).status_code, 404)

    def test_only_select_sql(self):
        for bad in ["DELETE FROM OITM", "UPDATE OITM SET x=1", "SELECT 1 FROM DUMMY; DROP TABLE X", "-- c\nCALL p()"]:
            with self.assertRaises(ValueError, msg=bad):
                hana.prepare_sql(bad)
        self.assertEqual(hana.prepare_sql("-- note\nSELECT 1 FROM DUMMY;\n"), "-- note\nSELECT 1 FROM DUMMY")
        self.assertTrue(hana.prepare_sql("WITH a AS (SELECT 1 x FROM DUMMY) SELECT * FROM a").startswith("WITH"))


class Reports(Base):
    def test_shipped_sql_files_are_valid_selects(self):
        for name, spec in bridge.REPORTS.items():
            sql = bridge._report_sql(spec)
            self.assertFalse(sql.endswith(";"), name)
            self.assertGreater(len(sql), 1000, name)

    def test_kpi_report_is_tab_file(self):
        r = self.c.get("/api/reports/kpi", headers=self.h)
        self.assertEqual(r.status_code, 200)
        self.assertIn("text/tab-separated-values", r.headers["Content-Type"])
        self.assertRegex(r.headers["Content-Disposition"], r'filename="SAP_live_KPI_\d{4}-\d{2}-\d{2}\.txt"')
        text = r.data.decode("utf-8")
        lines = text.split("\r\n")
        self.assertEqual(lines[0].split("\t")[:3], ["ExportDate", "SourceType", "DocEntry"])
        first = dict(zip(lines[0].split("\t"), lines[1].split("\t")))
        self.assertEqual(first["ExportDate"], "2026-09-10")
        self.assertEqual(first["AmountLocal"], "12500")       # Decimal('12500.000000') -> plain number
        self.assertEqual(r.headers["X-Row-Count"], str(len(self.result[1])))
        self.assertIn("1111-11-11", text)                     # no-commit dates survive

    def test_json_format(self):
        r = self.c.get("/api/reports/kpi?format=json", headers=self.h)
        self.assertEqual(r.json["rows"][0]["AmountLocal"], 12500.0)
        self.assertEqual(list(r.json["rows"][0])[0], "ExportDate")

    def test_cache_and_refresh(self):
        self.c.get("/api/reports/kpi", headers=self.h)
        self.c.get("/api/reports/kpi", headers=self.h)
        self.assertEqual(len(self.calls), 1)
        self.c.get("/api/reports/kpi?refresh=1", headers=self.h)
        self.assertEqual(len(self.calls), 2)

    def test_parallel_requests_share_one_run(self):
        slow = self.result

        def slow_run(sql, params=()):
            self.calls.append(sql)
            time.sleep(0.3)
            return slow

        hana.run = slow_run
        threads = [threading.Thread(target=lambda: bridge.app.test_client().get("/api/reports/supply-demand", headers=self.h))
                   for _ in range(5)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(len(self.calls), 1)

    def test_hana_error_is_502_json(self):
        def boom(sql, params=()):
            raise RuntimeError("connection refused")
        hana.run = boom
        r = self.c.get("/api/reports/kpi", headers=self.h)
        self.assertEqual(r.status_code, 502)
        self.assertIn("connection refused", r.json["error"])


class Values(unittest.TestCase):
    def test_text_values(self):
        tv = hana.text_value
        self.assertEqual(tv(None), "")
        self.assertEqual(tv(datetime.date(2026, 11, 5)), "2026-11-05")
        self.assertEqual(tv(datetime.datetime(2026, 11, 5)), "2026-11-05")
        self.assertEqual(tv(datetime.datetime(2026, 11, 5, 8, 30, 1)), "2026-11-05 08:30:01")
        self.assertEqual(tv(decimal.Decimal("1234.500000")), "1234.5")
        self.assertEqual(tv(decimal.Decimal("-0.000")), "0")
        self.assertEqual(tv(decimal.Decimal("1E+3")), "1000")
        self.assertEqual(tv("Line\twith\r\nbreaks"), "Line with breaks")


class Data(Base):
    def test_params_validated_and_bound(self):
        self.assertEqual(self.c.get("/api/data/sales_by_month?months=abc", headers=self.h).status_code, 400)
        self.assertEqual(self.c.get("/api/data/sales_by_month?months=500", headers=self.h).status_code, 400)
        self.assertEqual(self.c.get("/api/data/sales_by_month?months=6", headers=self.h).status_code, 200)
        self.assertEqual(self.calls[-1][1], (6, 6))


def write_dashboard_fixture(path):
    """The exact bytes the bridge serves for /api/reports/kpi, for the dashboard round-trip test."""
    t = Base("run")
    t.setUp()
    try:
        data = t.c.get("/api/reports/kpi", headers=t.h).data
    finally:
        t.tearDown()
    pathlib.Path(path).write_bytes(data)
    return len(data)


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--write-fixture":
        print(write_dashboard_fixture(sys.argv[2]), "bytes")
    else:
        unittest.main()
