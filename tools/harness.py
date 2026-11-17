"""Run the real bridge under waitress with HANA faked, and switch the fake while it runs.

    python tools/harness.py --code . --port 18085 --control-port 18086

--code points at a checkout of the bridge (so an older commit can be measured the same way). The fake replaces the
HANA driver module (hana.dbapi), so the bridge's own hana.connect() / hana.run() code is what runs.

Control (plain HTTP on --control-port, localhost only):
    GET  /stats                 {"connects", "executes", "running", "rss_mb", "peak_rss_mb", "threads"}
    POST /set?rows=5000&lookup_rows=200&delay=0&fail=&fail_delay=0&hang=0
         rows         rows a report returns          lookup_rows  rows a query with ? parameters returns
         delay        seconds each execute takes     fail         an error code: execute raises it (-10709, 10, 260...)
         fail_delay   seconds before the failure     hang         1: execute waits for the driver's
                                                                   communicationTimeout, then fails like the driver
    POST /reset                 counters back to 0

Nothing here talks to a real database. Linux only for the memory figures (it reads /proc/self/status).
"""
import argparse
import datetime
import decimal
import http.server
import json
import os
import pathlib
import random
import sys
import threading
import time
import urllib.parse


class DriverError(Exception):
    """Same shape as hdbcli.dbapi.Error: .errorcode, .errortext, args[0] the code."""
    def __init__(self, errorcode, errortext):
        super().__init__(errorcode, errortext)
        self.errorcode, self.errortext = errorcode, errortext

    def __str__(self):
        return f"({self.errorcode}, '{self.errortext}')"


ERRORS = {
    -10709: "Connect failed (connect timeout expired) (10.0.0.20:30015)",
    -10807: "Connection down: [89006] System call 'recv' failed, rc=110:Connection timed out",
    10: "authentication failed",
    260: 'invalid column name: T1."U_X": line 9 col 5',
}

KPI_COLS = [f"Col{i:02d}" for i in range(49)]
LOOKUP_COLS = ["DocNum", "CardCode", "CardName", "DocDate", "DocDueDate", "DocStatus", "LineNum", "ItemCode",
               "Dscription", "Quantity", "OpenQty", "Price", "Currency", "LineTotal", "OpenSum", "WhsCode",
               "ShipDate", "LineStatus"]


def kpi_row(i):
    """49 values with the mix of types a KPI line has: dates, decimals, integers, NULLs and text."""
    d = datetime.date(2026, 1 + i % 12, 1 + i % 28)
    out = []
    for c in range(49):
        k = c % 7
        if k == 0:
            out.append(d)
        elif k == 1:
            out.append(decimal.Decimal(f"{(i * 37 + c) % 99999}.{(i * 101 + c) % 1000000:06d}"))
        elif k == 2:
            out.append(i * 7 + c)
        elif k == 3:
            out.append(None)
        elif k == 4:
            out.append(f"Item description {i % 997} for line {c}")
        elif k == 5:
            out.append("Open" if i % 3 else "Closed")
        else:
            out.append(f"SPY-{i % 5000:05d}")
    return tuple(out)


def lookup_row(i, key):
    d = datetime.date(2026, 1 + i % 12, 1 + i % 28)
    return (key, "C1001", "Synthetic customer " + str(i % 50), d, d, "O", i, f"SPY-{i:05d}", "Synthetic item " * 3,
            decimal.Decimal("3.000000"), decimal.Decimal("1.000000"), decimal.Decimal("12.500000"), "CAD",
            decimal.Decimal("37.500000"), decimal.Decimal("12.500000"), "11", d, "O")


class FakeHana:
    """Stands in for the hdbcli.dbapi module."""
    Error = DriverError

    def __init__(self):
        self.lock = threading.Lock()
        self.cfg = {"rows": 5000, "lookup_rows": 200, "delay": 0.0, "fail": None, "fail_delay": 0.0, "hang": 0}
        self.connects = self.executes = self.running = 0

    def connect(self, **kw):
        with self.lock:
            self.connects += 1
        return _Conn(self, kw)


class _Conn:
    def __init__(self, hana, kw):
        self.hana, self.kw = hana, kw

    def cursor(self):
        return _Cursor(self.hana, self.kw)

    def close(self):
        pass


class _Cursor:
    def __init__(self, hana, kw):
        self.hana, self.kw = hana, kw
        self.description = None
        self._rows = iter(())

    def execute(self, sql, params=()):
        h = self.hana
        with h.lock:
            h.executes += 1
            h.running += 1
            cfg = dict(h.cfg)
        try:
            if cfg["hang"]:
                # Like the real driver against a server that never answers: give up at communicationTimeout.
                wait = (self.kw.get("communicationTimeout") or 0) / 1000 or 3600
                time.sleep(wait)
                raise DriverError(-10807, ERRORS[-10807])
            if cfg["fail"] is not None:
                time.sleep(cfg["fail_delay"])
                code = int(cfg["fail"])
                raise DriverError(code, ERRORS.get(code, "simulated error"))
            if cfg["delay"]:
                time.sleep(cfg["delay"])
        finally:
            with h.lock:
                h.running -= 1
        if "?" in sql and "DUMMY" not in sql:
            key = params[0] if params else 0
            self.description = [(c, None, None, None, None, None, None) for c in LOOKUP_COLS]
            self._rows = (lookup_row(i, key) for i in range(cfg["lookup_rows"]))
        elif "DUMMY" in sql:
            self.description = [("ServerTime",) + (None,) * 6, ("Schema",) + (None,) * 6]
            self._rows = iter([(datetime.datetime(2026, 11, 12, 10, 42, 5), "SBO_FAKE")])
        else:
            self.description = [(c, None, None, None, None, None, None) for c in KPI_COLS]
            self._rows = (kpi_row(i) for i in range(cfg["rows"]))

    def fetchall(self):
        return list(self._rows)

    def fetchmany(self, n):
        out = []
        for row in self._rows:
            out.append(row)
            if len(out) >= n:
                break
        return out

    def close(self):
        pass


def _proc_status(field):
    try:
        for line in pathlib.Path("/proc/self/status").read_text().splitlines():
            if line.startswith(field + ":"):
                return int(line.split()[1]) / 1024
    except OSError:
        return None
    return None


def control_server(fake, port):
    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, obj):
            body = json.dumps(obj).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            with fake.lock:
                stats = {"connects": fake.connects, "executes": fake.executes, "running": fake.running,
                         "cfg": fake.cfg}
            t = os.times()
            stats.update(rss_mb=_proc_status("VmRSS"), peak_rss_mb=_proc_status("VmHWM"),
                         threads=threading.active_count(), cpu_s=round(t.user + t.system, 3))
            self._send(stats)

        def do_POST(self):
            u = urllib.parse.urlparse(self.path)
            q = dict(urllib.parse.parse_qsl(u.query, keep_blank_values=True))
            with fake.lock:
                if u.path == "/reset":
                    fake.connects = fake.executes = 0
                elif u.path == "/set":
                    for k, v in q.items():
                        if k == "fail":
                            fake.cfg[k] = None if v == "" else int(v)
                        elif k in ("delay", "fail_delay"):
                            fake.cfg[k] = float(v)
                        else:
                            fake.cfg[k] = int(v)
            self.do_GET()

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--code", default=str(pathlib.Path(__file__).resolve().parent.parent))
    ap.add_argument("--port", type=int, default=18085)
    ap.add_argument("--control-port", type=int, default=18086)
    args = ap.parse_args()

    code = pathlib.Path(args.code).resolve()
    sys.path.insert(0, str(code))
    os.environ.setdefault("HANA_HOST", "fake")
    os.environ.setdefault("HANA_USER", "FAKE")
    os.environ.setdefault("HANA_PASSWORD", "fake")
    os.environ.setdefault("HANA_SCHEMA", "SBO_FAKE")
    os.environ.setdefault("API_KEYS", "harness-key-0123456789abcdef0123456789abcdef")
    random.seed(1)

    import hana
    fake = FakeHana()
    hana.dbapi = fake
    import app

    control_server(fake, args.control_port)
    from waitress import serve
    if hasattr(app, "serve_options"):
        opts = app.serve_options()
    else:                                   # the patched 0b0411a code: what its __main__ used
        opts = {"threads": 8, "channel_timeout": 600}
    opts.update(host="127.0.0.1", port=args.port)
    print(f"harness ready on {args.port} (control {args.control_port}) code={code} opts={opts}", flush=True)
    serve(app.app, **opts)


if __name__ == "__main__":
    main()
