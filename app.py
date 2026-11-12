"""SAP B1 bridge: read-only HTTP access to SAP Business One 9.3 on HANA.

Runs on a machine on the Spyder network that can reach HANA, published with Cloudflare Tunnel.

    GET /health                          -> {"status": "ok"} (no key, no SAP data; for monitoring)
    GET /api/health                      -> HANA connection check (key required)
    GET /api/catalog                     -> the reports and queries available
    GET /api/reports/<name>              -> a saved query as a tab-delimited file (like SAP Export to Excel)
    GET /api/reports/<name>?format=json  -> the same rows as JSON
    GET /api/data/<name>?param=value     -> a small named query as JSON

Every /api call needs the header  X-API-Key: <one of API_KEYS>.  Add &refresh=1 to skip the cache (at most once per
REFRESH_MIN_SECONDS per report; sooner calls get the result just read).
If HANA fails and an earlier result is cached, that result is served with the header  X-Stale: 1  so the dashboard keeps
showing the last good data. Error messages say what failed in plain words; the details are only in the bridge log.
"""
import datetime
import hmac
import logging
import os
import pathlib
import threading
import time
import uuid

from dotenv import load_dotenv
from flask import Flask, Response, abort, jsonify, request

BASE = pathlib.Path(__file__).resolve().parent
load_dotenv(BASE / ".env")   # the bridge's own folder, whatever the working directory (a Windows service starts elsewhere)

import hana  # noqa: E402
from reports import QUERIES, REPORTS  # noqa: E402

SQL_DIR = BASE / "sql"
REFRESH_MIN_SECONDS = int(os.environ.get("REFRESH_MIN_SECONDS", "30"))
API_KEYS = [k.strip() for k in os.environ.get("API_KEYS", "").split(",") if k.strip()]

app = Flask(__name__)
app.json.sort_keys = False
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("sap-bridge")

_cache = {}                      # key -> (timestamp, payload)
_cache_lock = threading.Lock()
_run_locks = {}                  # one HANA run per key at a time; others wait for its result


def _lock_for(key):
    with _cache_lock:
        return _run_locks.setdefault(key, threading.Lock())


class Stale(Exception):
    """HANA failed but an earlier result exists: .entry holds it, .cause the error."""
    def __init__(self, entry, cause):
        super().__init__(str(cause))
        self.entry, self.cause = entry, cause


def cached(key, ttl, produce, refresh=False):
    """Return (timestamp, payload). Concurrent callers for the same key share one HANA run.
    refresh=1 is honoured at most once per REFRESH_MIN_SECONDS. If the run fails and an older result is cached,
    raises Stale carrying it, so the caller can serve the last good data."""
    asked = time.time()
    with _cache_lock:
        hit = _cache.get(key)
    if hit and asked - hit[0] < (REFRESH_MIN_SECONDS if refresh else ttl):
        return hit
    with _lock_for(key):
        with _cache_lock:
            hit = _cache.get(key)
        # A run that finished while this request waited for the lock is fresh enough, even for refresh=1.
        if hit and (hit[0] >= asked or time.time() - hit[0] < (REFRESH_MIN_SECONDS if refresh else ttl)):
            return hit
        started = time.time()
        try:
            payload = produce()
        except Exception as exc:
            if hit:
                raise Stale(hit, exc) from exc
            raise
        entry = (time.time(), payload)
        with _cache_lock:
            _cache[key] = entry
        log.info("ran %s in %.1fs", key, time.time() - started)
        return entry


def plain_error(exc):
    """What went wrong, in words safe to show a dashboard user: no host names, user names or SQL."""
    text = str(exc).lower()
    if "authentication" in text or "invalid username or password" in text or "password" in text and "expired" in text:
        return "SAP HANA refused the bridge's sign-in. Check HANA_USER and HANA_PASSWORD in the bridge's .env."
    if any(w in text for w in ("timeout", "timed out")):
        return "SAP HANA took too long to answer. Try again in a few minutes."
    if any(w in text for w in ("connection", "refused", "socket", "unreachable", "network", "host")):
        return "SAP HANA could not be reached from the bridge PC. Is the HANA server up and on the network?"
    if any(w in text for w in ("sql syntax", "invalid column", "invalid table", "invalid name", "not found", "sql error")):
        return "The query failed in SAP HANA. A table or column in sql/ may not match this company database."
    return "SAP HANA returned an error."


def failed(what, exc):
    """Log the full error with an id; return a JSON 502 with a plain message and that id."""
    ref = uuid.uuid4().hex[:8]
    log.error("%s failed (ref %s): %s", what, ref, exc, exc_info=exc)
    return jsonify(error=f"{plain_error(exc)} (bridge log ref {ref})"), 502


# ---------------------------------------------------------------- security
@app.before_request
def require_key():
    if not request.path.startswith("/api/"):
        return None
    if not API_KEYS:
        return jsonify(error="The bridge is not configured: set API_KEYS in .env."), 503
    supplied = request.headers.get("X-API-Key", "")
    if not any(hmac.compare_digest(supplied.encode(), k.encode()) for k in API_KEYS):
        return jsonify(error="Missing or invalid API key."), 401
    return None


@app.after_request
def no_store(resp):
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp


# ---------------------------------------------------------------- helpers
def _stamp(ts):
    """ISO time with the bridge PC's UTC offset, e.g. 2026-11-12T10:42:05-06:00."""
    return datetime.datetime.fromtimestamp(ts).astimezone().isoformat(timespec="seconds")


def _params(spec):
    values = {}
    for name, typ, default, lo, hi in spec.get("params", []):
        raw = request.args.get(name, default)
        try:
            val = typ(raw)
        except (TypeError, ValueError):
            abort(400, description=f"Parameter '{name}' must be {typ.__name__}.")
        if (lo is not None and val < lo) or (hi is not None and val > hi):
            abort(400, description=f"Parameter '{name}' must be between {lo} and {hi}.")
        values[name] = val
    order = spec.get("param_map") or [p[0] for p in spec.get("params", [])]
    return tuple(values[n] for n in order), values


def _report_sql(spec):
    return hana.prepare_sql((SQL_DIR / spec["file"]).read_text(encoding="utf-8"))


# ---------------------------------------------------------------- routes
@app.get("/health")
def health_public():
    return jsonify(status="ok")


@app.get("/api/health")
def health():
    try:
        return jsonify(status="ok", hana=hana.ping())
    except Exception as exc:
        resp, _ = failed("HANA health check", exc)
        resp.status_code = 503
        return resp


@app.get("/api/catalog")
def catalog():
    return jsonify(
        reports={n: {"description": r["description"], "cache_seconds": r["ttl"]} for n, r in REPORTS.items()},
        queries={n: {"description": q.get("description", ""),
                     "params": [{"name": p[0], "default": p[2], "min": p[3], "max": p[4]} for p in q.get("params", [])]}
                 for n, q in QUERIES.items()},
    )


@app.get("/api/reports/<name>")
def report(name):
    spec = REPORTS.get(name)
    if spec is None:
        abort(404, description=f"Unknown report '{name}'.")
    stale = False
    try:
        sql = _report_sql(spec)
        ts, (cols, rows) = cached(("report", name), spec["ttl"], lambda: hana.run(sql),
                                  refresh=request.args.get("refresh") == "1")
    except Stale as s:
        log.warning("Report %s failed, serving the result from %s: %s", name, _stamp(s.entry[0]), s.cause)
        ts, (cols, rows) = s.entry
        stale = True
    except Exception as exc:
        return failed(f"Report {name}", exc)

    if request.args.get("format") == "json":
        return jsonify(report=name, refreshed_at=_stamp(ts), row_count=len(rows), stale=stale,
                       rows=[{c: hana.json_value(v) for c, v in zip(cols, r)} for r in rows])

    filename = spec["filename"].format(date=datetime.date.fromtimestamp(ts).isoformat())
    headers = {
        "Content-Disposition": f'attachment; filename="{filename}"',
        "X-Row-Count": str(len(rows)),
        "X-Refreshed-At": _stamp(ts),
    }
    if stale:
        headers["X-Stale"] = "1"
    return Response(hana.to_tsv(cols, rows).encode("utf-8"), mimetype="text/tab-separated-values", headers=headers)


@app.get("/api/data/<name>")
def data(name):
    spec = QUERIES.get(name)
    if spec is None:
        abort(404, description=f"Unknown query '{name}'.")
    params, shown = _params(spec)
    stale = False
    try:
        ts, (cols, rows) = cached(("data", name, params), spec.get("ttl", 60),
                                  lambda: hana.run(spec["sql"], params),
                                  refresh=request.args.get("refresh") == "1")
    except Stale as s:
        log.warning("Query %s failed, serving the result from %s: %s", name, _stamp(s.entry[0]), s.cause)
        ts, (cols, rows) = s.entry
        stale = True
    except Exception as exc:
        return failed(f"Query {name}", exc)
    return jsonify(query=name, params=shown, refreshed_at=_stamp(ts), row_count=len(rows), stale=stale,
                   rows=[{c: hana.json_value(v) for c, v in zip(cols, r)} for r in rows])


@app.errorhandler(400)
@app.errorhandler(404)
@app.errorhandler(405)
def json_error(err):
    return jsonify(error=err.description), err.code


@app.errorhandler(500)
def server_error(err):
    exc = getattr(err, "original_exception", None) or err
    ref = uuid.uuid4().hex[:8]
    log.error("Unexpected error (ref %s): %s", ref, exc, exc_info=exc)
    return jsonify(error=f"The bridge hit an unexpected error (bridge log ref {ref})."), 500


if __name__ == "__main__":
    from waitress import serve

    host = os.environ.get("HOST", "127.0.0.1")   # cloudflared runs on the same machine
    port = int(os.environ.get("PORT", "8085"))
    log.info("SAP bridge on http://%s:%s", host, port)
    serve(app, host=host, port=port, threads=8, channel_timeout=600)
