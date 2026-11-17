"""Measure the bridge against the hardening targets, with HANA faked (tools/harness.py).

    python tools/loadtest.py --code .               --out before.json   # any checkout of the bridge
    python tools/loadtest.py --code . --only s6,s7  --out quick.json

Each scenario starts its own harness process (fresh memory figures), runs, and stops it. Clients run in separate
processes so their CPU does not count against the server's. Numbers from a shared laptop or VM are only comparable
with each other: measure before and after on the same machine.
"""
import argparse
import http.client
import json
import multiprocessing
import os
import pathlib
import statistics
import subprocess
import sys
import threading
import time
import urllib.request

KEY = "harness-key-0123456789abcdef0123456789abcdef"
HERE = pathlib.Path(__file__).resolve().parent
PORT, CONTROL = 18085, 18086


# ------------------------------------------------------------------ harness process and helpers
class Harness:
    def __init__(self, code, env=None):
        self.code, self.env = code, dict(os.environ, **(env or {}))
        self.proc = None

    def __enter__(self):
        self.proc = subprocess.Popen([sys.executable, str(HERE / "harness.py"), "--code", str(self.code),
                                      "--port", str(PORT), "--control-port", str(CONTROL)],
                                     env=self.env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        self.log = []
        threading.Thread(target=self._drain, daemon=True).start()
        deadline = time.time() + 20
        while time.time() < deadline:
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{PORT}/health", timeout=1).read()
                return self
            except Exception:
                time.sleep(0.2)
        raise RuntimeError("harness did not start:\n" + "".join(self.log[-30:]))

    def _drain(self):
        for line in self.proc.stdout:
            self.log.append(line)

    def __exit__(self, *exc):
        self.proc.terminate()
        try:
            self.proc.wait(10)
        except subprocess.TimeoutExpired:
            self.proc.kill()

    def set(self, **kw):
        q = "&".join(f"{k}={'' if v is None else v}" for k, v in kw.items())
        return json.loads(urllib.request.urlopen(urllib.request.Request(
            f"http://127.0.0.1:{CONTROL}/set?{q}", method="POST"), timeout=5).read())

    def reset(self):
        urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{CONTROL}/reset", method="POST"), timeout=5)

    def stats(self):
        return json.loads(urllib.request.urlopen(f"http://127.0.0.1:{CONTROL}/stats", timeout=5).read())


def get(path, keyed=True, timeout=300, conn=None):
    """One GET; returns (status, headers dict, body length, seconds)."""
    own = conn is None
    if own:
        conn = http.client.HTTPConnection("127.0.0.1", PORT, timeout=timeout)
    t = time.perf_counter()
    try:
        conn.request("GET", path, headers={"X-API-Key": KEY} if keyed else {})
        r = conn.getresponse()
        body = r.read()
        return r.status, dict(r.getheaders()), len(body), time.perf_counter() - t, body[:300]
    except Exception as exc:
        return None, {}, 0, time.perf_counter() - t, repr(exc).encode()
    finally:
        if own:
            conn.close()


def parallel(n, fn):
    out = [None] * n
    ts = [threading.Thread(target=lambda i=i: out.__setitem__(i, fn(i))) for i in range(n)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    return out


def pct(values, p):
    v = sorted(values)
    return v[min(len(v) - 1, int(round(p / 100 * (len(v) - 1))))] if v else None


# ------------------------------------------------------------------ scenarios
def s1_health_while_slow(code):
    """/health while a slow report runs and 12 users wait. Target: under 200 ms."""
    with Harness(code) as h:
        h.set(rows=500, delay=8)
        waiters = threading.Thread(target=lambda: parallel(12, lambda i: get("/api/reports/kpi")))
        waiters.start()
        time.sleep(1.0)
        lat = []
        for _ in range(20):
            s, _, _, sec, _ = get("/health", keyed=False, timeout=30)
            lat.append(sec * 1000)
            time.sleep(0.1)
        waiters.join()
        return {"health_ms_max": round(max(lat), 1), "health_ms_p50": round(statistics.median(lat), 1),
                "target": "max < 200 ms", "ok": max(lat) < 200}


def s2_down_after_good(code):
    """HANA down after a good read, 8 users. Target: all 200 + X-Stale, <= 1 attempt per back-off window,
    all answered within one attempt (3 s) + 1 s."""
    with Harness(code, {"REFRESH_MIN_SECONDS": "1"}) as h:
        h.set(rows=500)
        if get("/api/reports/kpi")[0] != 200:
            raise RuntimeError("priming the report failed")
        h.set(fail=-10709, fail_delay=3)
        time.sleep(1.2)
        h.reset()
        t0 = time.perf_counter()
        wave1 = parallel(8, lambda i: get("/api/reports/kpi?refresh=1"))
        w1 = time.perf_counter() - t0
        execs_1 = h.stats()["executes"]
        time.sleep(1.2)
        wave2 = parallel(8, lambda i: get("/api/reports/kpi?refresh=1"))
        execs_2 = h.stats()["executes"] - execs_1
        res = wave1 + wave2
        ok_stale = all(r[0] == 200 and r[1].get("X-Stale") == "1" for r in res)
        slowest = max(r[3] for r in wave1)
        return {"statuses": sorted({r[0] for r in res}), "all_200_stale": ok_stale,
                "hana_attempts_wave1": execs_1, "hana_attempts_wave2_within_window": execs_2,
                "slowest_s": round(slowest, 2), "wave1_s": round(w1, 2),
                "target": "all 200 stale, <=1 attempt per window, slowest <= 4 s",
                "ok": ok_stale and execs_1 <= 1 and execs_2 == 0 and slowest <= 4.0}


def s3_down_nothing_cached(code):
    """HANA down, nothing cached, 8 users. Target: JSON 502, <= 1 attempt per back-off window."""
    with Harness(code) as h:
        h.set(rows=500, fail=-10709, fail_delay=3)
        wave1 = parallel(8, lambda i: get("/api/reports/kpi"))
        execs_1 = h.stats()["executes"]
        wave2 = parallel(8, lambda i: get("/api/reports/kpi"))
        execs_2 = h.stats()["executes"] - execs_1
        res = wave1 + wave2
        plain = all(r[0] == 502 and r[1].get("Content-Type", "").startswith("application/json") for r in res)
        return {"statuses": sorted({r[0] for r in res}), "json_502": plain, "hana_attempts_wave1": execs_1,
                "hana_attempts_wave2_within_window": execs_2, "slowest_s": round(max(r[3] for r in res), 2),
                "sample": res[0][4].decode(errors="replace")[:140],
                "target": "plain JSON 502, <=1 attempt per window",
                "ok": plain and execs_1 <= 1 and execs_2 == 0}


def s4_hang(code):
    """HANA accepts and never answers. Target: the request ends at the configured timeout; others keep answering."""
    with Harness(code, {"HANA_QUERY_TIMEOUT_MS": "6000"}) as h:
        h.set(rows=500, hang=1)
        box = {}
        t = threading.Thread(target=lambda: box.update(r=get("/api/reports/kpi")))
        t.start()
        time.sleep(0.5)
        others = []
        for _ in range(10):
            others.append(get("/health", keyed=False, timeout=30)[3] * 1000)
            others.append(get("/api/catalog", timeout=30)[3] * 1000)
            time.sleep(0.2)
        t.join()
        s, _, _, sec, body = box["r"]
        return {"hung_request_status": s, "hung_request_s": round(sec, 2), "others_ms_max": round(max(others), 1),
                "body": body.decode(errors="replace")[:120],
                "target": "ends at ~6 s (configured), others fast",
                "ok": s is not None and 5.5 <= sec <= 7.5 and max(others) < 200}


def _lookups(h, n, threads=4):
    nums = list(range(1, n + 1))
    results = []
    lock = threading.Lock()

    def worker(k):
        conn = http.client.HTTPConnection("127.0.0.1", PORT, timeout=120)
        for i in nums[k::threads]:
            r = get(f"/api/data/so_detail?docnum={i}", conn=conn)
            with lock:
                results.append(r[0])
        conn.close()
    parallel(threads, worker)
    return results


def s5_lookups(code, env=None):
    """3,000 distinct lookup values. Target: memory flat after the cache bound; HANA runs stop at the ceiling."""
    with Harness(code, env) as h:
        h.set(lookup_rows=200)
        rss0 = h.stats()["rss_mb"]
        r1 = _lookups(h, 600)
        st1 = h.stats()
        r2 = _lookups(h, 3000)
        st2 = h.stats()
        statuses = {}
        for s in r1 + r2:
            statuses[s] = statuses.get(s, 0) + 1
        return {"rss_start_mb": round(rss0), "rss_after_600_mb": round(st1["rss_mb"]),
                "rss_after_3000_mb": round(st2["rss_mb"]), "peak_rss_mb": round(st2["peak_rss_mb"]),
                "hana_runs": st2["executes"], "statuses": statuses,
                "target": "memory flat after the bound; runs capped by the per-minute ceiling"}


def _client_proc(n_threads, seconds, path, q):
    lat, errors = [], 0
    stop = time.time() + seconds
    lock = threading.Lock()

    def worker():
        nonlocal errors
        conn = http.client.HTTPConnection("127.0.0.1", PORT, timeout=60)
        while time.time() < stop:
            s, _, _, sec, _ = get(path, conn=conn)
            with lock:
                if s == 200:
                    lat.append(sec)
                else:
                    errors += 1
            if s is None:
                conn.close()
                conn = http.client.HTTPConnection("127.0.0.1", PORT, timeout=60)
        conn.close()
    ts = [threading.Thread(target=worker) for _ in range(n_threads)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    q.put((lat, errors))


def s6_cached_single(code):
    """Cached report, 5,000 rows, one user. Target: under 10 ms server-side."""
    with Harness(code) as h:
        h.set(rows=5000)
        get("/api/reports/kpi")
        conn = http.client.HTTPConnection("127.0.0.1", PORT, timeout=60)
        lat = [get("/api/reports/kpi", conn=conn)[3] * 1000 for _ in range(200)]
        size = get("/api/reports/kpi")[2]
        server = _server_ms(h)
        return {"body_bytes": size, "client_ms_p50": round(statistics.median(lat), 2),
                "client_ms_p95": round(pct(lat, 95), 2), "server_ms_p50_from_access_log": server,
                "target": "under 10 ms server-side", "ok": (server or statistics.median(lat)) < 10}


def _server_ms(h):
    """Median server-side duration of cached report hits, read from the access log (new code only)."""
    import re
    vals = [float(m.group(1)) for line in h.log
            for m in [re.search(r" GET /api/reports/kpi .* ms=([0-9.]+) .* cache=hit\b", line)] if m]
    return round(statistics.median(vals), 2) if vals else None


def s7_cached_concurrent(code, users=32, procs=4, seconds=10):
    """Cached report, 5,000 rows, 32 users. Target: over 500 req/s, p95 under 100 ms."""
    with Harness(code) as h:
        h.set(rows=5000)
        get("/api/reports/kpi")
        q = multiprocessing.Queue()
        ps = [multiprocessing.Process(target=_client_proc, args=(users // procs, seconds, "/api/reports/kpi", q))
              for _ in range(procs)]
        cpu0 = h.stats()["cpu_s"]
        t = time.time()
        [p.start() for p in ps]
        results = [q.get() for _ in ps]
        [p.join() for p in ps]
        elapsed = time.time() - t
        server_cpu = (h.stats()["cpu_s"] - cpu0) / seconds
        lat = [x for r in results for x in r[0]]
        errors = sum(r[1] for r in results)
        rps = len(lat) / seconds
        return {"requests": len(lat), "errors": errors, "req_per_s": round(rps, 1),
                "p50_ms": round(pct(lat, 50) * 1000, 1), "p95_ms": round(pct(lat, 95) * 1000, 1),
                "elapsed_s": round(elapsed, 1), "server_cpu_cores_used": round(server_cpu, 2),
                "machine_cores": os.cpu_count(), "target": "> 500 req/s, p95 < 100 ms",
                "ok": rps > 500 and pct(lat, 95) < 0.1}


def s8_json_100k(code):
    """100,000 rows, 8 concurrent JSON requests. Target: peak memory under 600 MB."""
    with Harness(code) as h:
        h.set(rows=100000)
        before = h.stats()
        t = time.perf_counter()
        first = get("/api/reports/kpi", timeout=600)
        run_s = time.perf_counter() - t
        res = parallel(8, lambda i: get("/api/reports/kpi?format=json", timeout=600))
        st = h.stats()
        return {"rss_start_mb": round(before["rss_mb"]), "first_tsv_request_s": round(run_s, 1),
                "tsv_bytes": first[2], "json_statuses": sorted({r[0] for r in res}),
                "json_bytes": res[0][2], "slowest_json_s": round(max(r[3] for r in res), 1),
                "peak_rss_mb": round(st["peak_rss_mb"]), "rss_end_mb": round(st["rss_mb"]),
                "target": "peak < 600 MB", "ok": st["peak_rss_mb"] < 600}


SCENARIOS = {"s1": s1_health_while_slow, "s2": s2_down_after_good, "s3": s3_down_nothing_cached, "s4": s4_hang,
             "s5": s5_lookups, "s5b": lambda c: s5_lookups(c, {"LOOKUP_MAX_PER_MINUTE": "1000000"}),
             "s6": s6_cached_single, "s7": s7_cached_concurrent, "s8": s8_json_100k}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--code", required=True)
    ap.add_argument("--only", default=",".join(SCENARIOS))
    ap.add_argument("--out")
    args = ap.parse_args()
    results = {}
    for name in args.only.split(","):
        t = time.time()
        try:
            results[name] = SCENARIOS[name](pathlib.Path(args.code).resolve())
        except Exception as exc:
            results[name] = {"error": repr(exc)}
        results[name]["took_s"] = round(time.time() - t, 1)
        print(name, json.dumps(results[name]), flush=True)
    if args.out:
        pathlib.Path(args.out).write_text(json.dumps(results, indent=1) + "\n")


if __name__ == "__main__":
    main()
