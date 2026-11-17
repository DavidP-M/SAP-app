"""Part C: the hardening items C1 to C17, with HANA faked.  Run:  python -m unittest discover tests"""
import dataclasses
import datetime
import decimal
import gzip
import json
import logging
import pathlib
import re
import tempfile
import threading
import time
import unittest
import uuid

import fakedb
import sqlcols
from test_bridge import ROOT, Base, bridge, hana

import cache
import config

KEY = {"X-API-Key": "key-dashboard"}


def slow(seconds, result, calls):
    def run(sql, params=(), max_rows=None):
        calls.append((sql, params))
        time.sleep(seconds)
        return result
    return run


def failing(exc, calls, delay=0.0):
    def run(sql, params=(), max_rows=None):
        calls.append((sql, params))
        time.sleep(delay)
        raise exc
    return run


def in_threads(n, fn):
    out = [None] * n
    ts = [threading.Thread(target=lambda i=i: out.__setitem__(i, fn(i))) for i in range(n)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    return out


def get(path, headers=KEY):
    return bridge.app.test_client().get(path, headers=headers)


class C1BoundedWaiting(Base):
    overrides = {"request_wait_seconds": 0.3}

    def test_nothing_cached_gives_up_waiting_with_a_plain_503(self):
        hana.run = slow(1.0, self.result, self.calls)
        t = time.perf_counter()
        r = self.c.get("/api/reports/kpi", headers=self.h)
        self.assertLess(time.perf_counter() - t, 0.9)
        self.assertEqual(r.status_code, 503)
        self.assertEqual(r.json["error"], "SAP HANA is still working on this. Try again in a minute.")
        time.sleep(1.0)                                              # the run went on and filled the cache
        r = self.c.get("/api/reports/kpi", headers=self.h)
        self.assertEqual((r.status_code, len(self.calls)), (200, 1))
        self.assertNotIn("X-Stale", r.headers)

    def test_cached_result_is_served_stale_when_waiting_runs_out(self):
        first = self.c.get("/api/reports/kpi", headers=self.h)
        bridge.STATE.age(("report", "kpi"), 3600)
        hana.run = slow(1.0, self.result, self.calls)
        t = time.perf_counter()
        r = self.c.get("/api/reports/kpi", headers=self.h)
        self.assertLess(time.perf_counter() - t, 0.9)
        self.assertEqual((r.status_code, r.headers.get("X-Stale")), (200, "1"))
        self.assertEqual(r.data, first.data)

    def test_defaults_keep_the_contract_timeout_and_stay_under_cloudflare(self):
        s = config.from_env({})
        self.assertEqual(s.hana_query_timeout_ms, 240000)
        self.assertLess(s.request_wait_seconds, 100)                # Cloudflare cuts a request at about 100 s


class C2NoStampede(Base):
    overrides = {"failure_backoff_seconds": 5.0}

    def test_nothing_cached_one_attempt_and_the_same_error(self):
        hana.run = failing(fakedb.Error(-10709, "Connect failed (connect timeout expired)"), self.calls, 0.2)
        rs = in_threads(8, lambda i: get("/api/reports/kpi"))
        rs += in_threads(8, lambda i: get("/api/reports/kpi?refresh=1"))
        self.assertEqual(len(self.calls), 1)
        self.assertEqual({r.status_code for r in rs}, {502})
        self.assertEqual(len({r.json["error"] for r in rs}), 1)      # one failure, one log reference

    def test_after_a_good_read_all_get_stale_and_hana_is_asked_once(self):
        self.c.get("/api/reports/kpi", headers=self.h)
        bridge.STATE.age(("report", "kpi"), 3600)
        self.calls.clear()
        hana.run = failing(fakedb.Error(-10709, "Connect failed"), self.calls, 0.3)
        t = time.perf_counter()
        rs = in_threads(8, lambda i: get("/api/reports/kpi"))
        self.assertLess(time.perf_counter() - t, 0.3 + 1.0)
        rs += in_threads(8, lambda i: get("/api/reports/kpi?refresh=1"))
        self.assertEqual(len(self.calls), 1)
        self.assertTrue(all(r.status_code == 200 and r.headers.get("X-Stale") == "1" for r in rs))

    def test_tries_again_after_the_back_off(self):
        bridge.reset_state(failure_backoff_seconds=0.2)
        hana.run = failing(RuntimeError("socket closed"), self.calls)
        self.c.get("/api/reports/kpi", headers=self.h)
        self.c.get("/api/reports/kpi", headers=self.h)
        self.assertEqual(len(self.calls), 1)
        time.sleep(0.3)
        self.c.get("/api/reports/kpi", headers=self.h)
        self.assertEqual(len(self.calls), 2)


class C3HealthKeepsAThread(Base):
    overrides = {"hana_waiters_max": 1, "request_wait_seconds": 5.0}

    def test_over_the_cap_answers_at_once(self):
        hana.run = slow(1.0, self.result, self.calls)
        box = {}
        t = threading.Thread(target=lambda: box.update(r=get("/api/reports/kpi")))
        t.start()
        time.sleep(0.2)
        t0 = time.perf_counter()
        r = get("/api/reports/kpi")                                  # the one waiting slot is taken
        self.assertLess(time.perf_counter() - t0, 0.2)
        self.assertEqual(r.status_code, 503)
        self.assertIn("busy", r.json["error"])
        t.join()
        self.assertEqual(box["r"].status_code, 200)

    def test_default_cap_leaves_two_threads(self):
        self.assertEqual((config.from_env({}).threads, config.from_env({}).hana_waiters_max), (16, 14))
        self.assertEqual(config.from_env({"THREADS": "8"}).hana_waiters_max, 6)
        with self.assertRaises(ValueError):
            config.from_env({"THREADS": "8", "HANA_WAITERS_MAX": "7"})


class C4NeverRedirect(Base):
    """Through a real waitress server: the test client cannot send a raw //path (it reads // as a host name)."""
    PATHS = ["//api/catalog", "///api/catalog", "/api//catalog", "/api/catalog/", "/api/reports/kpi/",
             "/api//reports/kpi", "/api/reports/", "/api/data/", "/api/data/so_detail/", "//api/reports/kpi",
             "/api/reports/kpi//", "/api/./catalog", "/api/reports/%2e%2e/catalog", "/api/data//so_detail",
             "/api", "/api/", "//api/data/so_detail?docnum=1"]

    def setUp(self):
        super().setUp()
        from waitress.server import create_server
        self.srv = create_server(bridge.app, host="127.0.0.1", port=0, threads=4)
        threading.Thread(target=self.srv.run, daemon=True).start()

    def tearDown(self):
        self.srv.close()
        super().tearDown()

    def raw(self, path, keyed=True):
        import http.client
        c = http.client.HTTPConnection("127.0.0.1", self.srv.effective_port, timeout=10)
        c.request("GET", path, headers=KEY if keyed else {})
        r = c.getresponse()
        body = r.read()
        c.close()
        return r.status, r.getheader("Content-Type"), body

    def test_odd_paths_are_json_404_never_3xx(self):
        for p in self.PATHS:
            status, ctype, body = self.raw(p)
            with self.subTest(p):
                self.assertEqual(status, 404, body[:80])
                self.assertTrue(ctype.startswith("application/json"))
        self.assertEqual(self.raw("/api/catalog")[0], 200)

    def test_odd_paths_without_a_key_are_refused_not_redirected(self):
        for p in ("//api/catalog", "///api/reports/kpi", "/api//data/so_detail", "/api/catalog/"):
            self.assertEqual(self.raw(p, keyed=False)[0], 401, p)

    def test_no_route_ends_in_a_slash(self):
        self.assertEqual([r.rule for r in bridge.app.url_map.iter_rules() if r.rule.endswith("/") and r.rule != "/"],
                         [])
        self.assertFalse(bridge.app.url_map.merge_slashes)


class C5LookupLimits(Base):
    overrides = {"lookup_cache_max": 3, "lookup_max_per_minute": 1000}

    def test_cache_is_bounded_and_reports_stay(self):
        self.c.get("/api/reports/kpi", headers=self.h)
        for n in range(1, 11):
            self.assertEqual(self.c.get(f"/api/data/so_detail?docnum={n}", headers=self.h).status_code, 200)
        size = bridge.STATE.size()
        self.assertEqual((size["lookups"], size["pinned"], size["flights"]), (3, 1, 0))
        before = len(self.calls)
        self.c.get("/api/reports/kpi", headers=self.h)                       # never evicted: no new run
        self.c.get("/api/data/so_detail?docnum=10", headers=self.h)          # still cached
        self.assertEqual(len(self.calls), before)
        self.c.get("/api/data/so_detail?docnum=1", headers=self.h)           # evicted: runs again
        self.assertEqual(len(self.calls), before + 1)

    def test_per_minute_ceiling(self):
        bridge.reset_state(lookup_max_per_minute=3)
        codes = [self.c.get(f"/api/data/so_detail?docnum={n}", headers=self.h).status_code for n in range(1, 6)]
        self.assertEqual(codes, [200, 200, 200, 503, 503])
        self.assertEqual(len(self.calls), 3)
        r = self.c.get("/api/data/so_detail?docnum=9", headers=self.h)
        self.assertEqual(r.json["error"], "Too many SAP lookups in the last minute. Try again in a minute.")
        self.assertEqual(self.c.get("/api/data/so_detail?docnum=1", headers=self.h).status_code, 200)  # cached

    def test_concurrency_cap(self):
        bridge.reset_state(lookup_max_concurrent=1)
        hana.run = slow(0.5, self.result, self.calls)
        rs = in_threads(4, lambda i: get(f"/api/data/so_detail?docnum={i + 1}"))
        self.assertEqual(sorted(r.status_code for r in rs), [200, 503, 503, 503])
        self.assertEqual(len(self.calls), 1)


class C6EnabledQueries(Base):
    overrides = {"enabled_queries": ("so_detail", "item_info")}

    def test_disabled_queries_vanish(self):
        cat = self.c.get("/api/catalog", headers=self.h).json
        self.assertEqual(list(cat["queries"]), ["so_detail", "item_info"])
        r = self.c.get("/api/data/po_detail?docnum=5", headers=self.h)
        self.assertEqual((r.status_code, r.json["error"]), (404, "Unknown query 'po_detail'."))
        self.assertEqual(self.c.get("/api/data/so_detail?docnum=5", headers=self.h).status_code, 200)
        self.assertEqual(self.calls[-1][1], (5,))

    def test_setting(self):
        self.assertEqual(config.from_env({}).enabled_queries, ())             # empty: all
        s = config.from_env({"ENABLED_QUERIES": " so_detail , item_info "}, known_queries=set(bridge.QUERIES))
        self.assertEqual(s.enabled_queries, ("so_detail", "item_info"))
        with self.assertRaises(ValueError):
            config.from_env({"ENABLED_QUERIES": "so_detail,nope"}, known_queries=set(bridge.QUERIES))


class C7JsonValues(unittest.TestCase):
    def test_values(self):
        jv = hana.json_value
        self.assertEqual(jv(datetime.time(10, 42, 5, 123)), "10:42:05")
        self.assertEqual(jv(datetime.datetime(2026, 11, 12, 10, 42, 5)), "2026-11-12 10:42:05")
        self.assertIsNone(jv(float("nan")))
        self.assertIsNone(jv(float("inf")))
        self.assertIsNone(jv(decimal.Decimal("NaN")))
        self.assertIsNone(jv(decimal.Decimal("-Infinity")))
        self.assertIsNone(jv(decimal.Decimal("1E+400")))                  # too big for a float
        self.assertEqual(jv(decimal.Decimal("12345678901234567890.123456")), 1.2345678901234567e19)
        self.assertEqual(jv(uuid.UUID(int=1)), "00000000-0000-0000-0000-000000000001")
        self.assertEqual(jv(datetime.timedelta(seconds=90)), "0:01:30")
        self.assertIs(jv(True), True)
        json.dumps([jv(v) for v in (float("nan"), decimal.Decimal("NaN"), datetime.time(1, 2, 3))], allow_nan=False)

    def test_duplicate_column_names_fail_loudly(self):
        with fakedb.installed(rows=[(1, 2)], description=[("A",), ("A",)]):
            with self.assertRaises(hana.QueryShapeError):
                hana.run("SELECT 1 AS A, 2 AS A FROM DUMMY")


class C7Endpoints(Base):
    def test_time_and_nan_in_json(self):
        self.result = (["T", "N"], [(datetime.time(8, 30), float("nan"))])
        r = self.c.get("/api/reports/kpi?format=json", headers=self.h)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(json.loads(r.data)["rows"], [{"T": "08:30:00", "N": None}])
        self.assertNotIn(b"NaN", r.data)

    def test_duplicate_columns_are_a_plain_502(self):
        with fakedb.installed(rows=[(1, 2)], description=[("A",), ("A",)]):
            hana.run, hana.batches = self._orig, self._orig_batches
            r = self.c.get("/api/reports/kpi", headers=self.h)
        self.assertEqual(r.status_code, 502)
        self.assertTrue(r.json["error"].startswith("The query failed in SAP HANA."))


class C8ErrorClasses(unittest.TestCase):
    CASES = [
        (fakedb.Error(10, "authentication failed"), hana.AUTH),
        (fakedb.Error(414, "user is forced to change password"), hana.AUTH),
        (fakedb.Error(260, 'invalid column name: host: line 1'), hana.QUERY),              # "host" in a query error
        (fakedb.Error(259, "invalid table name: Could not find table/view CONNECTION"), hana.QUERY),
        (fakedb.Error(258, "insufficient privilege: Not authorized"), hana.QUERY),
        # measured with hdbcli 2.30 against a silent socket: a connect timeout is "could not be reached"
        (fakedb.Error(-10709, "Connect failed (connect timeout expired) (127.0.0.1:1 -> 127.0.0.1:2)"),
         hana.UNREACHABLE),
        (fakedb.Error(-10709, "Connection failed (RTE:[89006] System call 'connect' failed, rc=111:Connection refused"),
         hana.UNREACHABLE),
        (fakedb.Error(-10709, "Connection failed (RTE:[300012] Cannot create SSL engine: certificate verify failed)"),
         hana.OTHER),
        (fakedb.Error(-10807, "Connection down: [89006] System call 'recv' failed, rc=110:Connection timed out"),
         hana.TIMEOUT),
        (fakedb.Error(-10807, "Connection down: [89008] Socket closed by peer"), hana.UNREACHABLE),
        (fakedb.Error(2048, "column store error: timeout of host thread"), hana.OTHER),       # a code wins over words
        (RuntimeError("the host said hello"), hana.OTHER),                                    # no code, no match
        (RuntimeError("connection refused by hana.test:30015"), hana.UNREACHABLE),
        (RuntimeError("timeout"), hana.TIMEOUT),
        (TimeoutError(), hana.TIMEOUT),
        (RuntimeError("SQL error 260: invalid column name"), hana.QUERY),
        (RuntimeError("authentication failed"), hana.AUTH),
    ]

    def test_classes(self):
        for exc, want in self.CASES:
            with self.subTest(str(exc)):
                self.assertEqual(hana.error_class(exc), want)

    def test_contract_wording(self):
        self.assertEqual(bridge.MESSAGES, {
            hana.AUTH: "SAP HANA refused the bridge's sign-in. Check HANA_USER and HANA_PASSWORD in the bridge's .env.",
            hana.TIMEOUT: "SAP HANA took too long to answer. Try again in a few minutes.",
            hana.UNREACHABLE: "SAP HANA could not be reached from the bridge PC. "
                              "Is the HANA server up and on the network?",
            hana.QUERY: "The query failed in SAP HANA. A table or column in sql/ may not match this company database.",
            hana.OTHER: "SAP HANA returned an error.",
        })


class C9EncodedOnce(Base):
    def test_tsv_is_encoded_once_per_run(self):
        counted = []
        orig = hana.tsv_line
        hana.tsv_line = lambda values: counted.append(1) or orig(values)
        try:
            a = self.c.get("/api/reports/kpi", headers=self.h).data
            n = len(counted)
            b = self.c.get("/api/reports/kpi", headers=self.h).data
        finally:
            hana.tsv_line = orig
        self.assertEqual(n, len(self.result[1]) + 1)
        self.assertEqual(len(counted), n)
        self.assertEqual(a, b)

    def test_json_is_built_once_on_first_request(self):
        built = []
        orig = bridge._report_json_rows
        bridge._report_json_rows = lambda result: built.append(1) or orig(result)
        try:
            self.c.get("/api/reports/kpi", headers=self.h)
            self.assertEqual(built, [])                                 # not built until someone asks
            a = self.c.get("/api/reports/kpi?format=json", headers=self.h).data
            b = self.c.get("/api/reports/kpi?format=json", headers=self.h).data
        finally:
            bridge._report_json_rows = orig
        self.assertEqual((built, a), ([1], b))


class C10ReportSize(Base):
    def test_too_many_rows_is_a_failed_run_and_the_last_good_result_stays(self):
        first = self.c.get("/api/reports/kpi", headers=self.h)
        bridge.SETTINGS = dataclasses.replace(bridge.SETTINGS, report_max_rows=2)
        bridge.STATE.age(("report", "kpi"), 3600)
        r = self.c.get("/api/reports/kpi", headers=self.h)
        self.assertEqual((r.status_code, r.headers.get("X-Stale"), r.data), (200, "1", first.data))
        r = self.c.get("/api/reports/supply-demand", headers=self.h)    # nothing cached
        self.assertEqual(r.status_code, 502)

    def test_reads_in_batches(self):
        hana.run, hana.batches = self._orig, self._orig_batches
        bridge.reset_state(fetch_batch_rows=5)
        rows = [(i, f"r{i}") for i in range(12)]
        with fakedb.installed(rows=rows, description=[("N",), ("S",)]) as db:
            r = self.c.get("/api/reports/kpi", headers=self.h)
        self.assertEqual((r.status_code, r.headers["X-Row-Count"]), (200, "12"))
        self.assertEqual(db.fetches, [("many", 5)] * 4)                 # 5 + 5 + 2 + empty; never fetchall
        self.assertEqual(r.data.count(b"\r\n"), 13)


class C11HealthCached(Base):
    def test_parallel_health_checks_share_one_hana_call(self):
        n = []
        orig = hana.ping
        hana.ping = lambda: n.append(1) or time.sleep(0.2) or {"ServerTime": "x", "Schema": "y"}
        try:
            rs = in_threads(20, lambda i: get("/api/health"))
            again = get("/api/health")
        finally:
            hana.ping = orig
        self.assertEqual(len(n), 1)                                     # 20 parallel calls, one HANA connection
        codes = [r.status_code for r in rs]
        self.assertGreaterEqual(codes.count(200), 14)                   # up to HANA_WAITERS_MAX wait for it
        self.assertTrue(all(r.status_code == 200 or "busy" in r.json["error"] for r in rs))
        self.assertEqual(again.status_code, 200)                        # then cached

    def test_failed_health_is_never_served_stale(self):
        orig = hana.ping
        hana.ping = lambda: {"ServerTime": "x", "Schema": "y"}
        try:
            get("/api/health")
            bridge.STATE.age(("health",), 3600)
            hana.ping = lambda: (_ for _ in ()).throw(fakedb.Error(10, "authentication failed"))
            r = get("/api/health")
        finally:
            hana.ping = orig
        self.assertEqual(r.status_code, 503)
        self.assertTrue(r.json["error"].startswith("SAP HANA refused the bridge's sign-in."))


class C12Gzip(Base):
    def test_off_by_default(self):
        r = self.c.get("/api/reports/kpi", headers=dict(self.h, **{"Accept-Encoding": "gzip"}))
        self.assertNotIn("Content-Encoding", r.headers)
        self.assertNotIn("Vary", r.headers)

    def test_on(self):
        plain = self.c.get("/api/reports/kpi", headers=self.h)
        bridge.SETTINGS = dataclasses.replace(bridge.SETTINGS, gzip_responses=True)
        r = self.c.get("/api/reports/kpi", headers=dict(self.h, **{"Accept-Encoding": "gzip, deflate"}))
        self.assertEqual((r.headers["Content-Encoding"], r.headers["Vary"]), ("gzip", "Accept-Encoding"))
        self.assertEqual(gzip.decompress(r.data), plain.data)
        self.assertEqual(int(r.headers["Content-Length"]), len(r.data))
        self.assertEqual(r.headers["X-Row-Count"], plain.headers["X-Row-Count"])
        r = self.c.get("/api/reports/kpi", headers=self.h)                 # caller did not ask: plain
        self.assertNotIn("Content-Encoding", r.headers)


class C13Certificates(unittest.TestCase):
    def test_validation_on_by_default_with_trust_store(self):
        s = config.from_env({})
        self.assertTrue(s.hana_validate_cert)
        s = config.from_env({"HANA_TRUSTSTORE": r"C:\sap-bridge\hana-ca.pem", "HANA_CERT_HOSTNAME": "spyder-hana1"})
        with fakedb.installed() as db:
            hana.connect(s)
        self.assertIs(db.connect_kwargs["sslValidateCertificate"], True)
        self.assertEqual(db.connect_kwargs["sslTrustStore"], r"C:\sap-bridge\hana-ca.pem")
        self.assertEqual(db.connect_kwargs["sslHostNameInCertificate"], "spyder-hana1")

    def test_loud_warning_when_off(self):
        s = config.from_env({"HANA_VALIDATE_CERT": "false"})
        self.assertTrue(any("HANA_VALIDATE_CERT is false" in w for w in s.warnings))
        with fakedb.installed() as db:
            hana.connect(s)
        self.assertIs(db.connect_kwargs["sslValidateCertificate"], False)
        self.assertNotIn("sslTrustStore", db.connect_kwargs)

    def test_example_env_documents_it(self):
        text = (ROOT / ".env.example").read_text(encoding="utf-8")
        self.assertRegex(text, r"(?m)^HANA_VALIDATE_CERT=true\b")
        self.assertRegex(text, r"(?m)^#?\s*HANA_TRUSTSTORE=")


class C14KeysAndAccessLog(Base):
    LONG = "x" * 40

    def test_labelled_and_bare_keys(self):
        keys, warnings = config.parse_api_keys(f"dashboard:{self.LONG}, {self.LONG}y ,gw:short")
        self.assertEqual([(k.label, k.key) for k in keys],
                         [("dashboard", self.LONG), ("key2", self.LONG + "y"), ("gw", "short")])
        self.assertEqual(len(warnings), 1)
        self.assertIn("'gw'", warnings[0])
        self.assertNotIn("short", warnings[0].replace("shorter", ""))      # the key itself is never in a warning
        with self.assertRaises(ValueError):
            config.parse_api_keys(f"a:{self.LONG},a:{self.LONG}z")

    def test_access_log_line(self):
        keys, _ = config.parse_api_keys(f"dashboard:{self.LONG}")
        bridge.reset_state(api_keys=keys)
        with self.assertLogs("sap-bridge.access", logging.INFO) as logs:
            self.c.get("/api/data/customer_orders?cardcode=C99887&months=6", headers={"X-API-Key": self.LONG})
            self.c.get("/api/data/customer_orders?cardcode=C99887&months=6", headers={"X-API-Key": self.LONG})
            self.c.get("/api/catalog", headers={"X-API-Key": "wrong"})
        first, second, refused = logs.output
        self.assertRegex(first, r"ref=[0-9a-f]{8} GET /api/data/customer_orders key=dashboard status=200 ms=\d+\.\d "
                                r"bytes=\d+ cache=miss$")
        self.assertIn("cache=hit", second)
        self.assertIn("key=- status=401", refused)
        for line in logs.output:
            self.assertNotIn(self.LONG, line)
            self.assertNotIn("C99887", line)                                 # parameter values stay out
            self.assertNotIn("months", line)


class C15SqlLoadedOnce(Base):
    def test_changing_sql_files_needs_a_restart(self):
        self.c.get("/api/reports/kpi", headers=self.h)
        sent = self.calls[-1][0]
        with tempfile.TemporaryDirectory() as tmp:
            for f in ("kpi_dashboard.sql", "supply_demand.sql"):
                pathlib.Path(tmp, f).write_text("SELECT 'changed' AS \"X\" FROM DUMMY", encoding="utf-8")
            orig = bridge.SQL_DIR
            bridge.SQL_DIR = pathlib.Path(tmp)
            try:
                bridge.STATE.age(("report", "kpi"), 3600)
                self.c.get("/api/reports/kpi", headers=self.h)
            finally:
                bridge.SQL_DIR = orig
        self.assertEqual(self.calls[-1][0], sent)

    def test_stricter_guard(self):
        for bad in ("WITH a AS (SELECT 1 x FROM DUMMY) DELETE FROM T", "SELECT * INTO T2 FROM T",
                    "SELECT 1 FROM DUMMY FOR UPDATE", "SELECT 1 FROM DUMMY; SELECT 2 FROM DUMMY",
                    "WITH a AS (SELECT 1 x FROM DUMMY) INSERT INTO T SELECT * FROM a", "SELECT REPLACE INTO T"):
            with self.subTest(bad), self.assertRaises(ValueError):
                hana.prepare_sql(bad)
        ok = hana.prepare_sql("SELECT REPLACE(\"N\", 'a', 'b') AS \"Into\", 'DELETE; me' AS \"X\" FROM T;")
        self.assertTrue(ok.endswith("FROM T"))


class C16TunnelExample(unittest.TestCase):
    def test_access_enforced_at_the_tunnel(self):
        text = (ROOT / "deploy" / "cloudflared-config.example.yml").read_text(encoding="utf-8")
        self.assertRegex(text, r"(?m)^ {6}access:\n {8}required: true\b")
        self.assertRegex(text, r"(?m)^ {8}teamName: \S+")
        self.assertRegex(text, r"(?m)^ {8}audTag:\n {10}- \S+")
        self.assertIn("Protect with Access", text)                          # the dashboard-managed way


class C17Grants(unittest.TestCase):
    def tables_read(self):
        found = set()
        for spec in bridge.REPORTS.values():
            found |= set(sqlcols.tables((ROOT / "sql" / spec["file"]).read_text(encoding="utf-8")))
        for q in bridge.QUERIES.values():
            found |= set(sqlcols.tables(q["sql"]))
        return found

    def test_per_table_grants_cover_every_table_read(self):
        text = (ROOT / "sql" / "create_readonly_user.sql").read_text(encoding="utf-8")
        granted = set(re.findall(r'(?m)^GRANT SELECT ON "SBO_SPYDER"\."([A-Z0-9]+)" TO DASHBOARD_RO;', text))
        self.assertEqual(granted, self.tables_read())
        self.assertEqual(len(granted), 32)
        self.assertRegex(text, r'(?m)^-- GRANT SELECT ON SCHEMA "SBO_SPYDER" TO DASHBOARD_RO;')
        self.assertNotRegex(text, r"(?m)^GRANT SELECT ON SCHEMA")

    def test_no_usable_sample_password(self):
        text = (ROOT / "sql" / "create_readonly_user.sql").read_text(encoding="utf-8")
        self.assertNotIn("Change_Me_Strong_Pwd_2026", text)
        pw = re.search(r'PASSWORD "([^"]+)"', text).group(1)
        self.assertFalse(re.search(r"[A-Z]", pw) and re.search(r"[0-9]", pw))   # fails HANA's default A1a layout


class CacheUnit(unittest.TestCase):
    """The cache on its own: flights finish even if every caller gave up; nothing leaks."""
    def test_no_leaks_after_many_keys(self):
        s = dataclasses.replace(config.from_env({}), lookup_cache_max=10, lookup_max_per_minute=10 ** 6,
                                lookup_max_concurrent=4)
        c = cache.ResultCache(s)
        for i in range(500):
            c.get(("data", "q", (i,)), "data", 60, lambda: i)
        self.assertEqual(c.size(), {"pinned": 0, "lookups": 10, "flights": 0, "failures": 0})

    def test_failures_are_bounded(self):
        s = dataclasses.replace(config.from_env({}), lookup_max_per_minute=10 ** 6, lookup_max_concurrent=4)
        c = cache.ResultCache(s)
        for i in range(1200):
            with self.assertRaises(cache.Failed):
                c.get(("data", "q", (i,)), "data", 60, lambda: (_ for _ in ()).throw(RuntimeError("x")))
        self.assertEqual(c.size()["failures"], 1000)


if __name__ == "__main__":
    unittest.main()
