"""SAP B1 bridge: read-only HTTP access to SAP Business One 9.3 on HANA.

Runs on a machine on the Spyder network that can reach HANA, published with Cloudflare Tunnel.

    GET /health                          -> {"status": "ok"} (no key, no SAP data; for monitoring)
    GET /api/health                      -> HANA connection check (key required)
    GET /api/catalog                     -> the reports and queries available
    GET /api/reports/<name>              -> a saved query as a tab-delimited file (like SAP Export to Excel)
    GET /api/reports/<name>?format=json  -> the same rows as JSON
    GET /api/data/<name>?param=value     -> a small named query as JSON

Every /api call needs the header  X-API-Key: <one of API_KEYS>.  Add &refresh=1 to skip the cache.
"""
import datetime
import hmac
import logging
import os
import pathlib
import threading
import time

from dotenv import load_dotenv
from flask import Flask, Response, abort, jsonify, request

load_dotenv()

import hana  # noqa: E402
from reports import QUERIES, REPORTS  # noqa: E402

BASE = pathlib.Path(__file__).resolve().parent
SQL_DIR = BASE / "sql"
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


def cached(key, ttl, produce, refresh=False):
    """Return (timestamp, payload). Concurrent callers for the same key share one HANA run."""
    asked = time.time()
    with _cache_lock:
        hit = _cache.get(key)
    if hit and not refresh and asked - hit[0] < ttl:
        return hit
    with _lock_for(key):
        with _cache_lock:
            hit = _cache.get(key)
        # A run that finished while this request waited for the lock is fresh enough, even for refresh=1.
        if hit and (hit[0] >= asked or (not refresh and time.time() - hit[0] < ttl)):
            return hit
        started = time.time()
        payload = produce()
        entry = (time.time(), payload)
        with _cache_lock:
            _cache[key] = entry
        log.info("ran %s in %.1fs", key, time.time() - started)
        return entry


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
    return datetime.datetime.fromtimestamp(ts).isoformat(timespec="seconds")


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
        log.exception("HANA health check failed")
        return jsonify(status="error", error=str(exc)), 503


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
    try:
        sql = _report_sql(spec)
        ts, (cols, rows) = cached(("report", name), spec["ttl"], lambda: hana.run(sql),
                                  refresh=request.args.get("refresh") == "1")
    except Exception as exc:
        log.exception("Report %s failed", name)
        return jsonify(error=f"SAP report '{name}' failed: {exc}"), 502

    if request.args.get("format") == "json":
        return jsonify(report=name, refreshed_at=_stamp(ts), row_count=len(rows),
                       rows=[{c: hana.json_value(v) for c, v in zip(cols, r)} for r in rows])

    filename = spec["filename"].format(date=datetime.date.fromtimestamp(ts).isoformat())
    return Response(
        hana.to_tsv(cols, rows).encode("utf-8"),
        mimetype="text/tab-separated-values",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Row-Count": str(len(rows)),
            "X-Refreshed-At": _stamp(ts),
        },
    )


@app.get("/api/data/<name>")
def data(name):
    spec = QUERIES.get(name)
    if spec is None:
        abort(404, description=f"Unknown query '{name}'.")
    params, shown = _params(spec)
    try:
        ts, (cols, rows) = cached(("data", name, params), spec.get("ttl", 60),
                                  lambda: hana.run(spec["sql"], params),
                                  refresh=request.args.get("refresh") == "1")
    except Exception as exc:
        log.exception("Query %s failed", name)
        return jsonify(error=f"SAP query '{name}' failed: {exc}"), 502
    return jsonify(query=name, params=shown, refreshed_at=_stamp(ts), row_count=len(rows),
                   rows=[{c: hana.json_value(v) for c, v in zip(cols, r)} for r in rows])


@app.errorhandler(400)
@app.errorhandler(404)
@app.errorhandler(405)
def json_error(err):
    return jsonify(error=err.description), err.code


if __name__ == "__main__":
    from waitress import serve

    host = os.environ.get("HOST", "127.0.0.1")   # cloudflared runs on the same machine
    port = int(os.environ.get("PORT", "8085"))
    log.info("SAP bridge on http://%s:%s", host, port)
    serve(app, host=host, port=port, threads=8, channel_timeout=600)
