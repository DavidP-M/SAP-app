"""The six Query Manager reports (folder "Kareena - Qry", version 10.19): their Excel exports were loaded by hand.

- Each sql/qm_*.sql file is the saved query byte for byte after a two-line header (SHA-256 of the text received).
- The columns each query outputs, read from the SQL text: what the dashboard (or anyone) gets, by name.
- Each report answers like the others: tab-delimited, its own file name, row count, refresh time.

    python tests/test_qm_reports.py --write-fixtures tests/fixtures/qm      # bytes the bridge serves, synthetic rows
    DASHBOARD_REPO=../spyder-kpi-dashboard node tests/qm_roundtrip.test.js  # those files through the dashboard's code
"""
import datetime
import decimal
import hashlib
import pathlib
import re
import sys
import unittest

import sqlcols
from test_bridge import ROOT, Base, bridge

# report -> (file, SHA-256 of the saved query as received on 2026-11-17, the Query Manager name)
QM = {
    "sales-orders": ("qm_1_sales_orders.sql",
                     "6ee150230ac47f2bfade6bf8d5e012f00c350def0496818bb324ce44bc1f72e2", "1. KPI_Sales Orders _ 10.19"),
    "sales-deliveries": ("qm_2_sales_deliveries.sql",
                         "0c0be45ca8a07a5c13ff44100ba61e79d57feb0e998ea10aab856a9acbfa0ec3",
                         "2. KPI_Sales Deliveries_10.19"),
    "purchase-orders": ("qm_3_purchase_order_lines.sql",
                        "f5e638410bc5a335ecab64c43e9910febbaad30aa121dc1849c4cc9dc1698d2a",
                        "3. KPI_Purchase Order Lines_10.19"),
    "po-receipts": ("qm_4_po_receipts_grpo.sql",
                    "5f214f532785b7acc66780d3f4d4502eb9ec8d83b24534bc5211c5ad8a57a7ad",
                    "4. KPI_PO Receipts GRPO_10.19"),
    "production-kitting": ("qm_5_production_orders_kitting.sql",
                           "94780afc0e1321d37358c3dca4bc82c4da1ac3e11bfd34398c57fbf937591349",
                           "5.KPI_Production Orders and Kitting_10.19"),
    "mrp-forecast": ("qm_6_mrp_forecast.sql",
                     "0a9dbf2ecb72d993c8b9d4b3f296c0bcafd37e0bbe4aa78056c9c524d36bbca6", "6.KPI_MRP Forecast_10.19"),
}

# The first 45 columns are the same in the four KPI queries; the rest differ.
KPI_HEAD = ("ExportDate SourceType DocEntry DocumentNumber LineNumber CustomerPONumber DocumentDate ClosedDate "
            "CloseDateSource DeliveryDocNumber DueDate HeaderDueDate LineDueDate CustomerVendorCode CustomerVendorName "
            "GroupCode CustomerVendorGroup DocumentOwner OrderType Department Project ItemCode ItemDescription "
            "ItemGroupName Warehouse Quantity OpenQuantity AmountLocal AmountForeign Currency Status DaysPastDue "
            "KPIStatus KPIArea LineRescheduleDate RescheduleDate RescheduleFlag DueDateRiskFlag Confirmed FillStatus "
            "OnTimeStatus PriceFlag PerfectOrder CoverageType DeliveredQuantity InventoryItem BillAndHold "
            "DownPaymentDocNumber DownPaymentDate ItemOnHand").split()
COLUMNS = {
    "sales-orders": KPI_HEAD,
    "sales-deliveries": KPI_HEAD + ["TrackingNumber", "ShipMethod", "ShipToCountry", "ShipDate"],
    "purchase-orders": KPI_HEAD + ["Buyer"],
    "po-receipts": KPI_HEAD + ["Buyer"],
    "production-kitting": (
        "ExportDate SourceType DocEntry ProductionOrder ProductionStatus ParentItem ParentDescription "
        "ParentPlannedQty ParentCompletedQty ParentOpenQty Warehouse StartDate DueDate SalesOrder SalesOrderStatus "
        "Owner CustomerCode Customer ComponentLine Component ComponentDescription ComponentWarehouse "
        "ComponentPlannedQty ComponentIssuedQty ComponentRemaining ComponentOnHand ComponentOnOrder IssueMethod "
        "InventoryItem").split(),
    "mrp-forecast": "ExportDate ForecastCode ForecastName ItemCode ItemDescription Warehouse ForecastMonth ForecastQty"
                    .split(),
}
KPI_REPORTS = ["sales-orders", "sales-deliveries", "purchase-orders", "po-receipts"]


def file_bytes(name):
    return (ROOT / "sql" / QM[name][0]).read_bytes()


def saved_query(name):
    """The file without its two-line header: the text of the saved query."""
    return file_bytes(name).split(b"\r\n", 2)[2]


class SavedQueries(unittest.TestCase):
    def test_reports_are_registered(self):
        self.assertEqual(list(bridge.REPORTS)[2:], list(QM))
        for i, (name, (file, _, _qm)) in enumerate(QM.items(), 1):
            spec = bridge.REPORTS[name]
            self.assertEqual((spec["file"], spec["ttl"]), (file, 300))
            self.assertRegex(spec["filename"], r"^SAP_live_[A-Za-z_]+_\{date\}\.txt$")
            self.assertTrue(spec["description"].startswith(f"Query Manager {i}."), spec["description"])

    def test_files_are_the_saved_queries_byte_for_byte(self):
        for name, (_file, sha, qm) in QM.items():
            with self.subTest(name):
                header = file_bytes(name).split(b"\r\n", 2)[:2]
                self.assertTrue(all(h.startswith(b"-- ") for h in header))
                self.assertIn(qm.encode(), header[0])
                self.assertEqual(hashlib.sha256(saved_query(name)).hexdigest(), sha)
                self.assertTrue(saved_query(name).startswith(b"SELECT"))

    def test_columns_by_name_and_order(self):
        for name, cols in COLUMNS.items():
            with self.subTest(name):
                sql = saved_query(name).decode("utf-8")
                self.assertEqual(len(sqlcols.select_lists(sql)), 1)          # no UNION
                self.assertEqual(sqlcols.columns(sql), cols)

    def test_kpi_queries_look_like_kpi_exports_to_the_dashboard(self):
        """The dashboard's rule (isKpiExportHeaderSet): a document number column and a source type or due date."""
        norm = lambda c: re.sub(r"[^a-z0-9]+", "", c.lower())  # noqa: E731
        for name, cols in COLUMNS.items():
            h = {norm(c) for c in cols}
            kpi = bool(h & {"documentnumber", "sodocnum", "ponumber", "docnum"}) and bool(
                h & {"sourcetype", "doctype", "ordertype", "kpiarea", "duedate", "lineduedate", "headerduedate"})
            self.assertEqual(kpi, name in KPI_REPORTS, name)


def synthetic(name):
    """Three rows typed the way hdbcli returns them (date, Decimal, int, str, None) for a report's columns."""
    cols = COLUMNS[name]
    fixed = {
        "sales-orders": {"SourceType": "Sales Order", "OrderType": "Sales Order", "KPIArea": "Sales Orders",
                         "Status": "Open", "KPIStatus": "Open", "CoverageType": "Uncovered"},
        "sales-deliveries": {"SourceType": "Sales Order", "OrderType": "Sales Delivery", "KPIArea": "Sales Orders",
                             "Status": "Closed", "KPIStatus": "Closed", "CoverageType": "Covered",
                             "ShipMethod": "FedEx Ground", "ShipToCountry": "CA"},
        "purchase-orders": {"SourceType": "Purchase Order", "OrderType": "Purchase Order",
                            "KPIArea": "Purchase Orders", "Status": "Open", "KPIStatus": "Open",
                            "CoverageType": "PO Open"},
        "po-receipts": {"SourceType": "Purchase Order", "OrderType": "PO Receipt", "KPIArea": "Purchase Orders",
                        "Status": "Closed", "KPIStatus": "Closed", "CoverageType": "Covered"},
        "production-kitting": {"SourceType": "Production Order", "ProductionStatus": "Released",
                               "IssueMethod": "Manual"},
        "mrp-forecast": {"ForecastCode": "FC2026", "ForecastName": "2026 forecast"},
    }[name]
    base = 1000 * (list(QM).index(name) + 1)
    ints = {"DocEntry", "DocumentNumber", "LineNumber", "GroupCode", "DaysPastDue", "DownPaymentDocNumber",
            "ProductionOrder", "SalesOrder", "ComponentLine"}
    rows = []
    for i in range(3):
        row = []
        for c in cols:
            if c in fixed:
                v = fixed[c]
            elif c == "ExportDate":
                v = datetime.date(2026, 9, 10)
            elif c in ("ClosedDate", "DownPaymentDate", "LineRescheduleDate", "RescheduleDate"):
                v = None
            elif c.endswith("Date"):
                v = datetime.date(2026, 9, 1 + 7 * i)
            elif c in ints:
                v = {"LineNumber": i, "ComponentLine": i, "DaysPastDue": 0, "GroupCode": 100,
                     "DownPaymentDocNumber": None}.get(c, base + i)
            elif re.search(r"Qty|Quantity|Amount|OnHand|OnOrder|Remaining", c):
                v = decimal.Decimal(f"{(i + 1) * 12.5:.6f}")
            elif c == "Currency":
                v = "CAD"
            elif c in ("InventoryItem", "Confirmed"):
                v = "Y"
            elif c == "BillAndHold":
                v = "N"
            elif c == "Warehouse" or c == "ComponentWarehouse":
                v = "Spyder - Lacombe"
            elif c == "ForecastMonth":
                v = f"2026-{10 + i:02d}"
            elif c in ("ItemCode", "ParentItem", "Component"):
                v = f"SPY-{base + i}"
            else:
                v = f"{c} {i + 1}"
            row.append(v)
        rows.append(tuple(row))
    return cols, rows


class Endpoints(Base):
    def test_each_report_is_a_tab_file_with_its_columns(self):
        for name in QM:
            with self.subTest(name):
                self.result = synthetic(name)
                r = self.c.get(f"/api/reports/{name}", headers=self.h)
                self.assertEqual(r.status_code, 200)
                self.assertIn("text/tab-separated-values", r.headers["Content-Type"])
                stem = bridge.REPORTS[name]["filename"].split("{")[0]
                self.assertRegex(r.headers["Content-Disposition"], rf'filename="{stem}\d{{4}}-\d{{2}}-\d{{2}}\.txt"')
                self.assertEqual(r.headers["X-Row-Count"], "3")
                self.assertNotIn("X-Stale", r.headers)
                lines = r.data.decode("utf-8").split("\r\n")
                self.assertEqual(lines[0].split("\t"), COLUMNS[name])
                self.assertEqual(len(lines), 5)                          # header, 3 rows, "" after the last \r\n
                self.assertTrue(lines[1].startswith("2026-09-10\t"))
                self.assertIn(bridge.REPORT_SQL[name], [sql for sql, _ in self.calls])

    def test_listed_in_the_catalog(self):
        reports = self.c.get("/api/catalog", headers=self.h).json["reports"]
        self.assertEqual(list(reports), ["kpi", "supply-demand", *QM])


def write_fixtures(folder):
    folder = pathlib.Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    t = Endpoints("test_listed_in_the_catalog")
    t.setUp()
    try:
        for name in QM:
            t.result = synthetic(name)
            r = t.c.get(f"/api/reports/{name}", headers=t.h)
            filename = re.search(r'filename="([^"]+)"', r.headers["Content-Disposition"]).group(1)
            (folder / f"{name}.txt").write_bytes(r.data)
            print(name, filename, len(r.data), "bytes")
    finally:
        t.tearDown()


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--write-fixtures":
        write_fixtures(sys.argv[2])
    else:
        unittest.main()
