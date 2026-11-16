"""Capture what callers see (status, headers, body) for a fixed set of requests, with HANA faked.

Used to prove that internal changes keep every response the dashboard can observe byte for byte the same.
Times and log references change from run to run, so they are replaced by placeholders before comparing.

    python tests/golden.py write tests/fixtures/golden_responses.json   # record (done once, at the patched commit)
    python -m unittest discover tests                                    # test_golden compares against it
"""
import datetime
import decimal
import json
import os
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.update(HANA_HOST="hana.test", HANA_USER="DASHBOARD_RO", HANA_PASSWORD="x", HANA_SCHEMA="SBO_TEST",
                  API_KEYS="key-dashboard,key-gateway")

# Headers a caller can observe; Date and Server are added by the web server, not the app.
HEADERS = ["Content-Type", "Content-Disposition", "X-Row-Count", "X-Refreshed-At", "X-Stale", "Cache-Control",
           "X-Content-Type-Options", "Content-Length", "Allow"]

ISO = re.compile(rb"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}")
REF = re.compile(rb"bridge log ref [0-9a-f]{8}")
FILEDATE = re.compile(rb"_(\d{4}-\d{2}-\d{2})\.txt")

KPI_COLS = ["DocNum", "DocDate", "Stamp", "Amount", "Rate", "Name", "Note", "Blob", "Empty"]
KPI_ROWS = [
    (45001, datetime.date(2026, 11, 12), datetime.datetime(2026, 11, 12), decimal.Decimal("45001.000000"), 0.00001,
     "Firefly RV", "tab\there\r\nand line", b"\x01\xff", None),
    (45002, datetime.date(2026, 1, 2), datetime.datetime(2026, 1, 2, 8, 30, 1), decimal.Decimal("-0.000"), 1234.5,
     "Ünïcödé €", "", b"", None),
    (45003, None, None, decimal.Decimal("1234.50"), float("nan"), 'quote " and =1+1', "x", None, None),
]
LOOKUP_COLS = ["DocNum", "CardCode", "DocDate", "Quantity", "Price", "LineStatus"]
LOOKUP_ROWS = [(45001, "C1001", datetime.date(2026, 11, 12), decimal.Decimal("3.000000"), decimal.Decimal("12.5"), "O"),
               (45001, "C1001", datetime.date(2026, 11, 12), decimal.Decimal("1.5"), None, "C")]

REQUESTS = [
    ("GET", "/health", True), ("GET", "/api/health", False), ("GET", "/api/catalog", False),
    ("GET", "/api/catalog", True),
    ("GET", "/api/reports/kpi", True), ("GET", "/api/reports/kpi?format=json", True),
    ("GET", "/api/reports/supply-demand", True), ("GET", "/api/reports/nope", True),
    ("POST", "/api/reports/kpi", True), ("GET", "/nowhere", True),
    ("GET", "/api/data/open_sales_orders", True), ("GET", "/api/data/sales_by_month?months=6", True),
    ("GET", "/api/data/sales_by_month?months=0", True), ("GET", "/api/data/stock_by_warehouse", True),
    ("GET", "/api/data/open_purchase_orders", True), ("GET", "/api/data/so_detail?docnum=45001", True),
    ("GET", "/api/data/so_detail", True), ("GET", "/api/data/so_detail?docnum=1.5", True),
    ("GET", "/api/data/po_detail?docnum=88001", True), ("GET", "/api/data/item_stock?item=SPY-100", True),
    ("GET", "/api/data/item_stock?item=O'Brien", True), ("GET", "/api/data/item_stock?item=" + "A" * 51, True),
    ("GET", "/api/data/customer_orders?cardcode=C1001&months=6", True),
    ("GET", "/api/data/order_deliveries?docnum=45001", True), ("GET", "/api/data/item_info?item=SPY-100", True),
    ("GET", "/api/data/production_orders?status=ALL", True),
    ("GET", "/api/data/production_orders?status=closed", True), ("GET", "/api/data/nope", True),
]


def normalise(body):
    body = ISO.sub(b"<ISO>", body)
    body = REF.sub(b"bridge log ref <REF>", body)
    return body


def capture(client, hana_module, headers_ok):
    """Run REQUESTS (plus stale and failure cases) and return a JSON-able list of observed responses."""
    def fake_run(sql, params=(), max_rows=None):
        if "DocNum" in sql and "ORDR" in sql and "?" in sql or "OPOR" in sql and "?" in sql or "?" in sql:
            return list(LOOKUP_COLS), list(LOOKUP_ROWS)
        return list(KPI_COLS), list(KPI_ROWS)

    hana_module.run = fake_run
    if hasattr(hana_module, "batches"):
        def fake_batches(sql, params=(), batch_rows=5000):
            cols, rows = hana_module.run(sql, params)
            yield cols
            for i in range(0, len(rows), batch_rows):
                yield rows[i:i + batch_rows]
        hana_module.batches = fake_batches
    hana_module.ping = lambda: {"ServerTime": "2026-11-12 10:42:05", "Schema": "SBO_TEST"}
    out = []

    def record(label, r):
        hdrs = {h: r.headers.get(h) for h in HEADERS if r.headers.get(h) is not None}
        if "X-Refreshed-At" in hdrs:
            hdrs["X-Refreshed-At"] = "<ISO>" if ISO.fullmatch(hdrs["X-Refreshed-At"].encode()) else "BAD"
        if "Content-Disposition" in hdrs:
            hdrs["Content-Disposition"] = FILEDATE.sub(b"_<DATE>.txt", hdrs["Content-Disposition"].encode()).decode()
        body = normalise(r.get_data())
        if "Content-Length" in hdrs:          # depends on the placeholders' lengths; checked as body length instead
            hdrs["Content-Length"] = "<len>" if int(hdrs["Content-Length"]) == len(r.get_data()) else "MISMATCH"
        out.append({"request": label, "status": r.status_code, "headers": hdrs, "body": body.decode("utf-8")})

    for method, path, keyed in REQUESTS:
        h = headers_ok if keyed else {}
        r = client.open(path, method=method, headers=h)
        record(f"{method} {path} {'keyed' if keyed else 'no key'}", r)

    # HANA failing with something cached: the last good result, marked stale.
    def down(sql, params=(), max_rows=None):
        raise RuntimeError("SQL error 260: invalid column name")
    hana_module.run = down
    for path in ("/api/reports/kpi?refresh=1", "/api/reports/kpi?format=json&refresh=1",
                 "/api/data/so_detail?docnum=45001&refresh=1"):
        record(f"GET {path} keyed, HANA down, cached", client.get(path, headers=headers_ok))
    # HANA failing with nothing cached: a plain 502.
    for path in ("/api/reports/supply-demand?refresh=1", "/api/data/so_detail?docnum=777"):
        record(f"GET {path} keyed, HANA down, nothing cached", client.get(path, headers=headers_ok))
    return out


def run_capture():
    import app as bridge
    import hana
    bridge.REFRESH_MIN_SECONDS = 0
    if hasattr(bridge, "reset_state"):
        bridge.reset_state(refresh_min_seconds=0)
    client = bridge.app.test_client()
    return capture(client, hana, {"X-API-Key": "key-dashboard"})


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "write":
        data = run_capture()
        pathlib.Path(sys.argv[2]).write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        print(len(data), "responses recorded")
    else:
        print(__doc__)
