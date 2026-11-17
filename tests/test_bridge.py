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
             "LineRescheduleDate", "RescheduleDate", "ShipDate"}
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


def fake_batches(sql, params=(), batch_rows=5000):
    """hana.batches built on whatever hana.run is right now, so tests that replace hana.run cover reports too."""
    cols, rows = hana.run(sql, params)
    yield cols
    for i in range(0, len(rows), batch_rows):
        yield rows[i:i + batch_rows]


class Base(unittest.TestCase):
    overrides = {}

    def setUp(self):
        bridge.reset_state(**self.overrides)
        self.calls = []
        self.result = synthetic_hana_rows()

        def fake_run(sql, params=(), max_rows=None):
            self.calls.append((sql, params))
            self.max_rows = max_rows
            return self.result

        self._orig = hana.run
        self._orig_batches = hana.batches
        hana.run = fake_run
        hana.batches = fake_batches
        self.c = bridge.app.test_client()
        self.h = {"X-API-Key": "key-dashboard"}

    def tearDown(self):
        hana.run = self._orig
        hana.batches = self._orig_batches
        bridge.reset_state()


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
        bridge.reset_state(api_keys=())
        self.assertEqual(self.c.get("/api/catalog", headers=self.h).status_code, 503)

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
        for name in bridge.REPORTS:
            sql = bridge.REPORT_SQL[name]                       # read and checked once, at start-up
            self.assertFalse(sql.endswith(";"), name)
            self.assertGreater(len(sql), 500, name)

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
        bridge.STATE.age(("report", "kpi"), bridge.SETTINGS.refresh_min_seconds + 1)
        self.c.get("/api/reports/kpi?refresh=1", headers=self.h)
        self.assertEqual(len(self.calls), 2)

    def test_parallel_requests_share_one_run(self):
        slow = self.result

        def slow_run(sql, params=()):
            self.calls.append(sql)
            time.sleep(0.3)
            return slow

        hana.run = slow_run
        def one():
            bridge.app.test_client().get("/api/reports/supply-demand", headers=self.h)
        threads = [threading.Thread(target=one) for _ in range(5)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(len(self.calls), 1)

    def test_hana_error_is_502_json(self):
        def boom(sql, params=()):
            raise RuntimeError("connection refused by hana.test:30015 for user DASHBOARD_RO")
        hana.run = boom
        r = self.c.get("/api/reports/kpi", headers=self.h)
        self.assertEqual(r.status_code, 502)
        self.assertIn("could not be reached", r.json["error"])
        self.assertIn("bridge log ref", r.json["error"])
        for secret in ("hana.test", "DASHBOARD_RO", "30015"):
            self.assertNotIn(secret, r.json["error"])   # no host, port or user name reaches the dashboard

    def test_failed_refresh_serves_last_good_result(self):
        first = self.c.get("/api/reports/kpi", headers=self.h)
        def boom(sql, params=()):
            raise RuntimeError("timeout")
        hana.run = boom
        bridge.STATE.age(("report", "kpi"), 3600)                                   # expired
        r = self.c.get("/api/reports/kpi", headers=self.h)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers.get("X-Stale"), "1")
        self.assertEqual(r.data, first.data)

    def test_refresh_is_rate_limited(self):
        self.c.get("/api/reports/kpi?refresh=1", headers=self.h)
        self.c.get("/api/reports/kpi?refresh=1", headers=self.h)
        self.assertEqual(len(self.calls), 1)   # the second refresh within REFRESH_MIN_SECONDS reuses the first

    def test_refreshed_at_has_a_utc_offset(self):
        r = self.c.get("/api/reports/kpi", headers=self.h)
        self.assertRegex(r.headers["X-Refreshed-At"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}$")


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
        self.assertEqual(tv(0.00001), "0.00001")          # never exponent notation
        self.assertEqual(tv(1234.5), "1234.5")
        self.assertEqual(tv(float("nan")), "")


class Data(Base):
    def test_params_validated_and_bound(self):
        self.assertEqual(self.c.get("/api/data/sales_by_month?months=abc", headers=self.h).status_code, 400)
        self.assertEqual(self.c.get("/api/data/sales_by_month?months=500", headers=self.h).status_code, 400)
        self.assertEqual(self.c.get("/api/data/sales_by_month?months=6", headers=self.h).status_code, 200)
        self.assertEqual(self.calls[-1][1], (6, 6))


LOOKUPS = {   # the Ask AI lookups and the SAP tables each must read
    "so_detail": ["ORDR", "RDR1"],
    "po_detail": ["OPOR", "POR1"],
    "item_stock": ["OITW", "OITM", "OWHS"],
    "customer_orders": ["ORDR", "RDR1", "ODLN", "DLN1"],
    "order_deliveries": ["ORDR", "ODLN", "DLN1", "OINV", "INV1"],
    "item_info": ["OITM", "OITB", "ITM1", "OCRD"],
    "production_orders": ["OWOR"],
}


class Lookups(Base):
    def get(self, path):
        return self.c.get(path, headers=self.h)

    def assert_400(self, path, words):
        r = self.get(path)
        self.assertEqual(r.status_code, 400, path)
        self.assertIn(words, r.json["error"], path)

    def test_sql_is_parameterised_select_on_the_right_tables(self):
        for name, tables in LOOKUPS.items():
            spec = bridge.QUERIES[name]
            sql = hana.prepare_sql(spec["sql"])                      # one SELECT / WITH, nothing else
            order = spec.get("param_map") or [p[0] for p in spec["params"]]
            self.assertEqual(sql.count("?"), len(order), name)
            self.assertNotRegex(sql, r"%s|\{|\}", name)              # values are bound, never formatted in
            for t in tables:
                self.assertIn(f'"{t}"', sql, f"{name} reads {t}")
            self.assertEqual(spec["max_rows"], 200, name)
            self.assertTrue(60 <= spec["ttl"] <= 120, name)
            self.assertTrue(spec["description"], name)

    def test_join_rules(self):
        q = bridge.QUERIES
        self.assertIn('"BaseType" = 17', q["customer_orders"]["sql"])        # deliveries made from the order
        od = q["order_deliveries"]["sql"]
        for rule in ('"BaseType" = 17', '"BaseType" = 15', '"BaseLine" = D."LineNum"'):
            self.assertIn(rule, od)
        self.assertIn('"PriceList" = 1', q["item_info"]["sql"])
        self.assertIn("'P', 'R'", q["production_orders"]["sql"])

    def test_catalog_shape(self):
        cat = self.get("/api/catalog").json["queries"]
        for name in LOOKUPS:
            self.assertEqual(cat[name]["max_rows"], 200)
            for p in cat[name]["params"]:
                self.assertEqual(set(p) - {"choices"}, {"name", "type", "default", "min", "max", "required"})
        self.assertEqual(cat["so_detail"]["params"],
                         [{"name": "docnum", "type": "int", "default": None, "min": 1, "max": 2147483647,
                           "required": True}])
        self.assertEqual(cat["item_stock"]["params"][0],
                         {"name": "item", "type": "str", "default": None, "min": None, "max": 50, "required": True})
        months = cat["customer_orders"]["params"][1]
        self.assertEqual((months["default"], months["required"]), (12, False))
        status = cat["production_orders"]["params"][1]
        self.assertEqual((status["default"], status["required"], status["choices"]), ("open", False, ["open", "all"]))
        self.assertFalse(cat["sales_by_month"]["params"][0]["required"])
        self.assertIsNone(cat["open_sales_orders"]["max_rows"])

    def test_required_params(self):
        for path in ("so_detail", "po_detail?docnum=", "item_stock", "item_stock?item=%20%20", "item_info",
                     "order_deliveries", "customer_orders?months=6"):
            self.assert_400(f"/api/data/{path}", "is required")
        self.assertEqual(self.calls, [])

    def test_text_too_long(self):
        self.assert_400("/api/data/item_stock?item=" + "A" * 51, "at most 50 characters")
        self.assert_400("/api/data/customer_orders?cardcode=" + "C" * 16, "at most 15 characters")
        self.assertEqual(self.get("/api/data/item_info?item=" + "A" * 50).status_code, 200)
        self.assertEqual(self.get("/api/data/customer_orders?cardcode=" + "C" * 15).status_code, 200)

    def test_text_bad_characters(self):
        for bad in ("O'Brien", "A1;DROP", "50%25", "a%09b", "x*", '"ITM"'):
            self.assert_400(f"/api/data/item_stock?item={bad}", "may contain only")
        self.assertEqual(self.calls, [])

    def test_ints_out_of_range(self):
        for path in ("so_detail?docnum=0", "so_detail?docnum=2147483648", "po_detail?docnum=-5",
                     "customer_orders?cardcode=C1&months=0", "customer_orders?cardcode=C1&months=61"):
            self.assert_400(f"/api/data/{path}", "must be between")
        for bad in ("12a", "1_000", "1.5", "%D9%A3"):                       # ASCII digits only
            self.assert_400(f"/api/data/order_deliveries?docnum={bad}", "must be a whole number")

    def test_choices(self):
        self.assert_400("/api/data/production_orders?status=closed", "must be one of: open, all")
        self.assertEqual(self.get("/api/data/production_orders?status=ALL").status_code, 200)
        self.assertEqual(self.calls[-1][1], ("", "", "all"))

    def test_values_are_stripped_and_bound_in_order(self):
        cases = {
            "so_detail?docnum=%2012345%20": (12345,),
            "item_stock?item=%20AB-100/2.5%20": ("AB-100/2.5",),
            "customer_orders?cardcode=C_001": ("C_001", 12),
            "customer_orders?cardcode=C001&months=6": ("C001", 6),
            "production_orders": ("", "", "open"),
            "production_orders?item=FG%20100": ("FG 100", "FG 100", "open"),
        }
        for path, bound in cases.items():
            r = self.get(f"/api/data/{path}")
            self.assertEqual(r.status_code, 200, path)
            self.assertEqual(self.calls[-1][1], bound, path)
            self.assertEqual(self.max_rows, 200, path)

    def test_truncated_at_max_rows(self):
        self.result = (["DocNum", "LineNum"], [(5, n) for n in range(201)])   # hana.run gives max_rows + 1 at most
        r = self.get("/api/data/so_detail?docnum=5")
        self.assertEqual((r.json["row_count"], len(r.json["rows"]), r.json["truncated"]), (200, 200, True))
        self.assertEqual(r.json["rows"][-1], {"DocNum": 5, "LineNum": 199})
        self.result = (["DocNum", "LineNum"], [(6, n) for n in range(200)])
        r = self.get("/api/data/so_detail?docnum=6")
        self.assertEqual((r.json["row_count"], r.json["truncated"]), (200, False))
        r = self.get("/api/data/open_sales_orders")                           # no cap: never truncated
        self.assertIs(r.json["truncated"], False)
        self.assertIsNone(self.max_rows)

    def test_run_reads_at_most_max_rows_plus_one(self):
        fetched = []

        class FakeCur:
            description = [("DocNum",)]
            def execute(self, sql, params):
                pass

            def fetchall(self):
                fetched.append("all")
                return [(1,), (2,), (3,)]

            def fetchmany(self, n):
                fetched.append(n)
                return [(1,), (2,), (3,)][:n]

            def close(self):
                pass

        class Conn:
            def cursor(self): return FakeCur()
            def close(self): pass

        orig, hana.connect = hana.connect, Conn
        hana.run = self._orig
        try:
            self.assertEqual(hana.run("SELECT 1 FROM DUMMY", (), max_rows=1), (["DocNum"], [(1,), (2,)]))
            self.assertEqual(hana.run("SELECT 1 FROM DUMMY")[1], [(1,), (2,), (3,)])
        finally:
            hana.connect = orig
        self.assertEqual(fetched, [2, "all"])


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
