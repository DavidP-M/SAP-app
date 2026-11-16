"""The dashboard contract (CONTRACT-dashboard.md, sections 3 to 9), checked with HANA faked.

Run:  python -m unittest discover tests
"""
import csv
import datetime
import decimal
import re
import time
import unittest

import sqlcols
from test_bridge import ROOT, Base, bridge, hana

KPI_COLUMNS = """
ExportDate SourceType DocEntry DocumentNumber LineNumber CustomerPONumber DocumentDate ClosedDate CloseDateSource
DeliveryDocNumber DueDate HeaderDueDate LineDueDate CustomerVendorCode CustomerVendorName GroupCode
CustomerVendorGroup DocumentOwner OrderType Department Project ItemCode ItemDescription ItemGroupName Warehouse
Quantity OpenQuantity AmountLocal AmountForeign Currency Status DaysPastDue KPIStatus KPIArea LineRescheduleDate
RescheduleDate RescheduleFlag DueDateRiskFlag Confirmed FillStatus OnTimeStatus PriceFlag PerfectOrder CoverageType
InventoryItem TrackingNumber ShipMethod ShipToCountry ShipDate
""".split()

SUPPLY_DEMAND_COLUMNS = """
ExportDate ItemCode ItemDescription ItemGroupName WarehouseCode WarehouseName MonthStart YearMonth DemandOpenQty
DemandNoCommitQty DemandOpenValue UsableSupplyQty RescheduledSupplyQty UnconfirmedSupplyQty NoCommitSupplyQty
LateSupplyQty RiskSupplyQty DemandOrderCount MonthlyNetQty CumulativeNetQty MonthlyCoverageStatus
CumulativeCoverageStatus SupplyRiskFlag
""".split()

# The attached supply / demand query (which must stay byte for byte) computes MonthStart inside its SUMMARY step and
# sorts by it, but does not output it. The dashboard groups by YearMonth and does not read MonthStart. Reported to the
# owner; see test_supply_demand_monthstart_is_not_output below.
NOT_OUTPUT_BY_ATTACHED_SQL = {"MonthStart"}

ELEVEN_QUERIES = ["open_sales_orders", "sales_by_month", "stock_by_warehouse", "open_purchase_orders",
                  "so_detail", "po_detail", "item_stock", "customer_orders", "order_deliveries", "item_info",
                  "production_orders"]

PACK_SQL = {   # report file -> the dashboard's query it must equal byte for byte (after the 2-line header)
    "kpi_dashboard.sql": "sap_b1_hana_kpi_current_dashboard_so_po_with_lagging.sql",
    "supply_demand.sql": "sap_b1_hana_supply_demand_snapshot_v2.sql",
}


def sql_text(name):
    return (ROOT / "sql" / name).read_text(encoding="utf-8")


class Section3SqlFiles(unittest.TestCase):
    def test_reports_table(self):
        self.assertEqual(list(bridge.REPORTS), ["kpi", "supply-demand"])
        self.assertEqual({n: (r["file"], r["filename"], r["ttl"]) for n, r in bridge.REPORTS.items()}, {
            "kpi": ("kpi_dashboard.sql", "SAP_live_KPI_{date}.txt", 300),
            "supply-demand": ("supply_demand.sql", "SAP_live_supply_demand_{date}.txt", 300),
        })

    def test_kpi_columns_are_the_contract_list_in_order(self):
        branches = sqlcols.select_lists(sql_text("kpi_dashboard.sql"))
        self.assertEqual(len(branches), 4)                              # SO, delivery, PO, receipt
        for b in branches:
            self.assertEqual(len(b), 49)
        self.assertEqual(sqlcols.columns(sql_text("kpi_dashboard.sql")), KPI_COLUMNS)

    def test_supply_demand_has_the_contract_columns_in_order(self):
        got = sqlcols.columns(sql_text("supply_demand.sql"))
        expected = [c for c in SUPPLY_DEMAND_COLUMNS if c not in NOT_OUTPUT_BY_ATTACHED_SQL]
        self.assertEqual([c for c in got if c in expected], expected)

    def test_supply_demand_monthstart_is_not_output(self):
        """Documents the one gap between the contract's list and the attached query. If the dashboard team adds
        MonthStart to its query (and so to the pack's copy), update NOT_OUTPUT_BY_ATTACHED_SQL."""
        got = sqlcols.columns(sql_text("supply_demand.sql"))
        self.assertNotIn("MonthStart", got)
        self.assertIn('AS "MonthStart"', sql_text("supply_demand.sql"))    # computed inside SUMMARY

    def test_fixtures_have_the_49_column_shape(self):
        with open(ROOT / "tests" / "fixtures" / "synthetic_kpi_rows.csv", newline="", encoding="utf-8") as fh:
            self.assertEqual(next(csv.reader(fh)), KPI_COLUMNS)
        first = (ROOT / "tests" / "fixtures" / "kpi_from_bridge.txt").read_bytes().split(b"\r\n", 1)[0]
        self.assertEqual(first.decode("utf-8").split("\t"), KPI_COLUMNS)


class Section4Tsv(Base):
    def test_every_value_rule(self):
        tv = hana.text_value
        self.assertEqual(tv(None), "")
        self.assertEqual(tv(datetime.date(2026, 11, 12)), "2026-11-12")
        self.assertEqual(tv(datetime.datetime(2026, 11, 12)), "2026-11-12")                 # midnight timestamp
        self.assertEqual(tv(datetime.datetime(2026, 11, 12, 10, 42, 5)), "2026-11-12 10:42:05")
        self.assertEqual(tv(decimal.Decimal("1234.50")), "1234.5")
        self.assertEqual(tv(decimal.Decimal("45001.000")), "45001")
        self.assertEqual(tv(decimal.Decimal("45001.000000")), "45001")
        self.assertEqual(tv(decimal.Decimal("-0")), "0")
        self.assertEqual(tv(decimal.Decimal("-0.000")), "0")
        self.assertEqual(tv(1e-05), "0.00001")
        self.assertEqual(tv(float("inf")), "")
        self.assertEqual(tv(float("-inf")), "")
        self.assertEqual(tv(float("nan")), "")
        self.assertEqual(tv("a\tb\r\nc\nd\re"), "a b c d e")
        self.assertEqual(tv('say "hi", =1+1'), 'say "hi", =1+1')                            # nothing else escaped
        self.assertEqual(tv(b"\x01\xff"), "01ff")

    def test_lines_end_with_crlf_and_cells_with_single_tabs(self):
        body = self.c.get("/api/reports/kpi", headers=self.h).data
        self.assertTrue(body.endswith(b"\r\n"))
        self.assertNotIn(b"\n", body.replace(b"\r\n", b""))
        lines = body.split(b"\r\n")[:-1]
        self.assertTrue(all(line.count(b"\t") == 48 for line in lines))
        self.assertEqual(len(lines), 1 + len(self.result[1]))


class Section4Headers(Base):
    def test_headers_when_fresh(self):
        r = self.c.get("/api/reports/kpi", headers=self.h)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.mimetype, "text/tab-separated-values")
        self.assertRegex(r.headers["Content-Disposition"],
                         r'^attachment; filename="SAP_live_KPI_\d{4}-\d{2}-\d{2}\.txt"$')
        self.assertEqual(r.headers["X-Row-Count"], str(len(self.result[1])))
        self.assertRegex(r.headers["X-Refreshed-At"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}$")
        self.assertNotIn("X-Stale", r.headers)
        self.assertEqual(r.headers["Cache-Control"], "no-store")
        self.assertEqual(r.headers["X-Content-Type-Options"], "nosniff")

    def test_cached_answer_keeps_the_original_read_time(self):
        first = self.c.get("/api/reports/kpi", headers=self.h).headers["X-Refreshed-At"]
        time.sleep(1.1)
        again = self.c.get("/api/reports/kpi", headers=self.h)
        self.assertEqual(again.headers["X-Refreshed-At"], first)
        self.assertEqual(len(self.calls), 1)

    def test_json_format_shape(self):
        r = self.c.get("/api/reports/kpi?format=json", headers=self.h).json
        self.assertEqual(list(r), ["report", "refreshed_at", "row_count", "stale", "rows"])
        self.assertIs(r["stale"], False)


class Section5LastGoodResult(Base):
    def test_stale_carries_the_earlier_read_time(self):
        first = self.c.get("/api/reports/kpi", headers=self.h)

        def boom(sql, params=(), max_rows=None):
            raise RuntimeError("socket closed")
        hana.run = boom
        key = ("report", "kpi")
        bridge.STATE.age(key, 3600)                                                          # expired
        r = self.c.get("/api/reports/kpi", headers=self.h)
        self.assertEqual((r.status_code, r.headers.get("X-Stale")), (200, "1"))
        self.assertNotEqual(r.headers["X-Refreshed-At"], first.headers["X-Refreshed-At"])    # moved back an hour
        self.assertEqual(r.headers["X-Refreshed-At"], bridge._stamp(bridge.STATE.entry(key).wall))
        j = self.c.get("/api/reports/kpi?format=json", headers=self.h).json
        self.assertIs(j["stale"], True)

    def test_nothing_cached_is_a_plain_502(self):
        def boom(sql, params=(), max_rows=None):
            raise RuntimeError('SQL error 260: invalid column name: T1."U_X" at hana.test:30015 user DASHBOARD_RO')
        hana.run = boom
        r = self.c.get("/api/reports/kpi", headers=self.h)
        self.assertEqual(r.status_code, 502)
        msg = r.json["error"]
        self.assertTrue(msg.startswith("The query failed in SAP HANA."))
        self.assertRegex(msg, r"\(bridge log ref [0-9a-f]{8}\)$")
        for leak in ("hana.test", "30015", "DASHBOARD_RO", "U_X", "SELECT", "SBO_"):
            self.assertNotIn(leak, msg)


class Section7Catalog(Base):
    def test_eleven_queries_keyed_by_name_with_required_on_every_param(self):
        cat = self.c.get("/api/catalog", headers=self.h).json
        self.assertEqual(list(cat), ["reports", "queries"])
        self.assertIsInstance(cat["queries"], dict)
        self.assertEqual(list(cat["queries"]), ELEVEN_QUERIES)
        self.assertEqual(cat["reports"]["kpi"]["cache_seconds"], 300)
        for name, q in cat["queries"].items():
            self.assertEqual(list(q)[:2], ["description", "params"], name)
            self.assertTrue(q["description"].strip(), name)
            for p in q["params"]:
                self.assertIn(p["type"], ("int", "str"), name)
                self.assertIs(p["required"], p["default"] is None, name)


class Section7Lookups(Base):
    def test_json_shape(self):
        r = self.c.get("/api/data/so_detail?docnum=45001", headers=self.h).json
        self.assertEqual(list(r), ["query", "params", "refreshed_at", "row_count", "stale", "truncated", "rows"])
        self.assertEqual(r["params"], {"docnum": 45001})
        self.assertRegex(r["refreshed_at"], r"[+-]\d{2}:\d{2}$")

    def test_unknown_names_are_404_json(self):
        for path in ("/api/data/nope", "/api/reports/nope"):
            r = self.c.get(path, headers=self.h)
            self.assertEqual(r.status_code, 404)
            self.assertRegex(r.json["error"], r"^Unknown (report|query) 'nope'\.$")

    def test_undeclared_params_are_ignored(self):
        r = self.c.get("/api/data/so_detail?docnum=5&evil=1;DROP", headers=self.h)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.calls[-1][1], (5,))


class Section8Settings(unittest.TestCase):
    def test_env_example_documents_every_setting(self):
        text = (ROOT / ".env.example").read_text(encoding="utf-8")
        names = set(re.findall(r"^([A-Z_]+)=", text, re.M))
        for n in ("HANA_HOST", "HANA_PORT", "HANA_USER", "HANA_PASSWORD", "HANA_SCHEMA", "HANA_ENCRYPT",
                  "HANA_VALIDATE_CERT", "HANA_CONNECT_TIMEOUT_MS", "HANA_QUERY_TIMEOUT_MS", "HOST", "PORT",
                  "REFRESH_MIN_SECONDS", "API_KEYS"):
            self.assertIn(n, names)

    def test_requirements_have_no_new_packages(self):
        """The bridge asks for the same four packages (requirements.in); requirements.txt pins them and what they
        need to exact versions with hashes."""
        names = {re.split(r"[=<>!~ \[;]", line.strip(), maxsplit=1)[0].lower()
                 for line in (ROOT / "requirements.in").read_text(encoding="utf-8").splitlines()
                 if line.strip() and not line.lstrip().startswith("#")}
        self.assertEqual(names, {"flask", "hdbcli", "python-dotenv", "waitress"})
        lock = (ROOT / "requirements.txt").read_text(encoding="utf-8")
        pins = re.findall(r"(?m)^([a-z0-9_.-]+)==([0-9][^ ;\\]*)", lock)
        self.assertTrue({"flask", "hdbcli", "python-dotenv", "waitress", "werkzeug"} <= {n for n, _ in pins})
        for block in re.split(r"(?m)^(?=[a-z])", lock)[1:]:
            self.assertIn("==", block.splitlines()[0])                # exact version
            self.assertIn("--hash=sha256:", block)                    # and its hashes

if __name__ == "__main__":
    unittest.main()
