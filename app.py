"""SAP B1 bridge: read-only HTTP access to SAP Business One 9.3 on HANA.

Runs on a machine on the Spyder network that can reach HANA, published with Cloudflare Tunnel.

    GET /health                          -> {"status": "ok"} (no key, no SAP data; for monitoring)
    GET /api/health                      -> HANA connection check (key required; cached HEALTH_CACHE_SECONDS)
    GET /api/catalog                     -> the reports and queries available
    GET /api/reports/<name>              -> a saved query as a tab-delimited file (like SAP Export to Excel)
    GET /api/reports/<name>?format=json  -> the same rows as JSON
    GET /api/data/<name>?param=value     -> a small named query as JSON (truncated: true when it hit max_rows)

Every /api call needs the header  X-API-Key: <one of API_KEYS>.  Add &refresh=1 to skip the cache (at most once per
REFRESH_MIN_SECONDS per report; sooner calls get the result just read).
If HANA fails and an earlier result is cached, that result is served with the header  X-Stale: 1  so the dashboard keeps
showing the last good data. Error messages say what failed in plain words; the details are only in the bridge log.
How waiting, back-off and limits work: cache.py. Every setting: config.py and .env.example.
"""
import bisect
import dataclasses
import datetime
import gzip
import hmac
import io
import json
import logging
import os
import pathlib
import re
import time
import uuid

from dotenv import load_dotenv
from flask import Flask, Response, abort, g, jsonify, request
from werkzeug.wsgi import wrap_file

BASE = pathlib.Path(__file__).resolve().parent
# .env from the bridge's own folder, whatever the working directory (a Windows service starts elsewhere)
load_dotenv(BASE / ".env")

import cache  # noqa: E402
import config  # noqa: E402
import hana  # noqa: E402
from reports import QUERIES, REPORTS  # noqa: E402

SQL_DIR = BASE / "sql"

app = Flask(__name__)
app.json.sort_keys = False
app.url_map.merge_slashes = False      # set before any route: //api/x must be a 404, never a 308 redirect
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("sap-bridge")
access_log = logging.getLogger("sap-bridge.access")

SETTINGS = None          # config.Settings
STATE = None             # cache.ResultCache
REPORT_SQL = {}          # report name -> SQL, read and checked once at start-up


def configure(settings=None, **overrides):
    """Read the settings and the report SQL, and start with an empty cache. Called once at import; tests call it
    again (with overrides) to start clean."""
    global SETTINGS, STATE, REPORT_SQL
    s = settings or config.from_env(known_queries=set(QUERIES))
    if overrides:
        s = dataclasses.replace(s, **overrides)
    sql = {name: hana.prepare_sql((SQL_DIR / spec["file"]).read_text(encoding="utf-8"))
           for name, spec in REPORTS.items()}
    SETTINGS, REPORT_SQL = s, sql
    hana.configure(s)
    STATE = cache.ResultCache(s)
    return s


def reset_state(**overrides):
    return configure(config.from_env(known_queries=set(QUERIES)), **overrides)


def warn_about_settings():
    for w in SETTINGS.warnings:
        log.warning("Setting: %s", w)
    if not SETTINGS.api_keys:
        log.warning("Setting: API_KEYS is empty: every /api call is refused until a key is set.")


configure()

MESSAGES = {
    hana.AUTH: "SAP HANA refused the bridge's sign-in. Check HANA_USER and HANA_PASSWORD in the bridge's .env.",
    hana.TIMEOUT: "SAP HANA took too long to answer. Try again in a few minutes.",
    hana.UNREACHABLE: "SAP HANA could not be reached from the bridge PC. Is the HANA server up and on the network?",
    hana.QUERY: "The query failed in SAP HANA. A table or column in sql/ may not match this company database.",
    hana.OTHER: "SAP HANA returned an error.",
}


class TooManyRows(Exception):
    """A report returned more rows than REPORT_MAX_ROWS: treated as a failed run, so the last good result is served."""


def plain_error(exc):
    """What went wrong, in words safe to show a dashboard user: no host names, user names or SQL."""
    return MESSAGES[hana.error_class(exc)]


def failed(what, exc, status=502):
    """A JSON error with a plain message and a log reference. A cache.Failed was logged when the run failed: reuse its
    reference. Anything else is logged here."""
    if isinstance(exc, cache.Failed):
        ref, cause = exc.ref, exc.cause
    else:
        ref, cause = uuid.uuid4().hex[:8], exc
        log.error("%s failed (ref %s): %s", what, ref, exc, exc_info=exc)
    g.error_ref = ref
    return jsonify(error=f"{plain_error(cause)} (bridge log ref {ref})"), status


def busy(exc):
    g.cache = "busy"
    return jsonify(error=str(exc)), 503


# ---------------------------------------------------------------- security and the access log
def _is_api(path):
    return path.lstrip("/").startswith("api/") or path.lstrip("/") == "api"


@app.before_request
def require_key():
    g.t0 = time.perf_counter()
    g.ref = uuid.uuid4().hex[:8]
    g.key_label = "-"
    g.cache = "-"
    if not _is_api(request.path):
        return None
    if not SETTINGS.api_keys:
        return jsonify(error="The bridge is not configured: set API_KEYS in .env."), 503
    supplied = request.headers.get("X-API-Key", "").encode()
    label = None
    for k in SETTINGS.api_keys:                      # compare with every key, so timing does not tell which matched
        if hmac.compare_digest(supplied, k.key.encode()):
            label = k.label
    if label is None:
        return jsonify(error="Missing or invalid API key."), 401
    g.key_label = label
    raw = request.environ.get("REQUEST_URI") or request.environ.get("RAW_URI") or request.path
    if "//" in raw.split("?", 1)[0]:
        # waitress folds //api/x into /api/x before Flask sees it; an odd path is still a 404, never an answer or a
        # redirect.
        return jsonify(error="The requested URL was not found on the server."), 404
    return None


@app.after_request
def finish(resp):
    if 300 <= resp.status_code < 400:               # never redirect: the dashboard reads a 3xx as an Access refusal
        resp = jsonify(error="The requested URL was not found on the server.")
        resp.status_code = 404
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    if SETTINGS.gzip_responses:
        resp.headers.add("Vary", "Accept-Encoding")
    if _is_api(request.path):
        ms = (time.perf_counter() - g.get("t0", time.perf_counter())) * 1000
        size = resp.headers.get("Content-Length", "-")
        err = g.get("error_ref")
        access_log.info("ref=%s %s %s key=%s status=%s ms=%.1f bytes=%s cache=%s%s", g.get("ref", "-"),
                        request.method, request.path, g.get("key_label", "-"), resp.status_code, ms, size,
                        g.get("cache", "-"), f" error_ref={err}" if err else "")
    return resp


# ---------------------------------------------------------------- helpers
def _stamp(ts):
    """ISO time with the bridge PC's UTC offset, e.g. 2026-11-12T10:42:05-06:00."""
    return datetime.datetime.fromtimestamp(ts).astimezone().isoformat(timespec="seconds")


def _dumps(obj):
    """JSON exactly as Flask's jsonify writes it (compact, key order kept)."""
    return app.json.dumps(obj, separators=(",", ":"))


_SAFE_TEXT = re.compile(r"^[A-Za-z0-9 ._/\-]+$")
_WHOLE_NUMBER = re.compile(r"[+-]?[0-9]+")
_TYPE_NAMES = {int: "a whole number", str: "text"}


def _param_specs(spec):
    """Each param as (name, type, default, min, max, choices); default None means required."""
    for p in spec.get("params", []):
        name, typ, default, lo, hi = p[:5]
        yield name, typ, default, lo, hi, (p[5] if len(p) > 5 else None)


def _params(spec):
    values = {}
    for name, typ, default, lo, hi, choices in _param_specs(spec):
        raw = request.args.get(name)
        raw = raw.strip() if raw is not None else ""
        if raw == "":
            if default is None:
                abort(400, description=f"Parameter '{name}' is required.")
            values[name] = default
            continue
        if choices:
            val = raw.lower()
            if val not in choices:
                abort(400, description=f"Parameter '{name}' must be one of: {', '.join(choices)}.")
        elif typ is str:
            if hi is not None and len(raw) > hi:
                abort(400, description=f"Parameter '{name}' must be at most {hi} characters.")
            if not _SAFE_TEXT.fullmatch(raw):
                abort(400, description=f"Parameter '{name}' may contain only letters, digits, spaces and . _ / -")
            val = raw
        else:
            try:
                if typ is int and not _WHOLE_NUMBER.fullmatch(raw):
                    raise ValueError(raw)
                val = typ(raw)
            except (TypeError, ValueError):
                abort(400, description=f"Parameter '{name}' must be {_TYPE_NAMES.get(typ, typ.__name__)}.")
            if (lo is not None and val < lo) or (hi is not None and val > hi):
                abort(400, description=f"Parameter '{name}' must be between {lo} and {hi}.")
        values[name] = val
    order = spec.get("param_map") or [p[0] for p in spec.get("params", [])]
    return tuple(values[n] for n in order), values


def _enabled_queries():
    names = SETTINGS.enabled_queries
    return {n: q for n, q in QUERIES.items() if not names or n in names}


def _refresh():
    return request.args.get("refresh") == "1"


def _wants_gzip():
    return SETTINGS.gzip_responses and request.accept_encodings["gzip"] > 0


class ChunkFile(io.RawIOBase):
    """A read-only, seekable file over cached byte chunks, without joining them into one copy.

    Served through the server's wsgi.file_wrapper: waitress then sends straight from the cached bytes. Handing it a
    plain iterable instead makes waitress copy each body into its own buffer, and spill bodies over 1 MB
    (outbuf_overflow) to a temporary file on disk."""

    def __init__(self, parts):
        super().__init__()
        self._parts = [memoryview(p) for p in parts if len(p)]
        self._starts, pos = [], 0
        for part in self._parts:
            self._starts.append(pos)
            pos += len(part)
        self._size, self._pos = pos, 0

    def readable(self):
        return True

    def seekable(self):
        return True

    def tell(self):
        return self._pos

    def seek(self, offset, whence=io.SEEK_SET):
        base = {io.SEEK_SET: 0, io.SEEK_CUR: self._pos, io.SEEK_END: self._size}[whence]
        self._pos = max(0, base + offset)
        return self._pos

    def readinto(self, buf):
        want, done = len(buf), 0
        i = bisect.bisect_right(self._starts, self._pos) - 1
        while done < want and 0 <= i < len(self._parts) and self._pos < self._size:
            part, off = self._parts[i], self._pos - self._starts[i]
            n = min(want - done, len(part) - off)
            buf[done:done + n] = part[off:off + n]
            done += n
            self._pos += n
            i += 1
        return done

    def __len__(self):
        return self._size


def _stream(parts, mimetype, headers):
    body = ChunkFile(parts)
    resp = Response(wrap_file(request.environ, body), mimetype=mimetype, headers=headers, direct_passthrough=True)
    resp.headers["Content-Length"] = str(len(body))
    return resp


# ---------------------------------------------------------------- producing results (in the cache's run thread)
@dataclasses.dataclass
class ReportResult:
    cols: list
    row_count: int
    tsv: list            # byte chunks: the header line, then one chunk per batch
    compact: list        # per batch: the rows as a JSON array of arrays (values already JSON-ready)


def produce_report(name):
    """Read the report in batches and encode as it comes, so no full list of rows is ever held."""
    s = SETTINGS
    gen = hana.batches(REPORT_SQL[name], (), s.fetch_batch_rows)
    try:
        cols = next(gen)
        tsv = [(hana.tsv_line(cols) + "\r\n").encode("utf-8")]
        compact = []
        count = 0
        for batch in gen:
            count += len(batch)
            if count > s.report_max_rows:
                raise TooManyRows(f"Report {name} returned more than REPORT_MAX_ROWS={s.report_max_rows} rows.")
            tsv.append(("\r\n".join([hana.tsv_line(r) for r in batch]) + "\r\n").encode("utf-8"))
            compact.append(_dumps([[hana.json_value(v) for v in r] for r in batch]))
    finally:
        close = getattr(gen, "close", None)       # stop the read early (too many rows): closes the HANA connection
        if close is not None:
            close()
    return ReportResult(cols, count, tsv, compact)


def _report_json_rows(result):
    """Build the JSON rows of a report from its compact form: once per HANA run, on the first JSON request."""
    parts, first = [], True
    for batch in result.compact:
        rows = json.loads(batch)
        if not rows:
            continue
        text = ",".join(_dumps(dict(zip(result.cols, r))) for r in rows)
        parts.append(("" if first else ",") + text)
        first = False
    return [b"["] + [p.encode("utf-8") for p in parts] + [b"]"]


@dataclasses.dataclass
class LookupResult:
    row_count: int
    truncated: bool
    rows_json: bytes


def produce_lookup(spec, params):
    max_rows = spec.get("max_rows")
    cols, rows = hana.run(spec["sql"], params, max_rows=max_rows)
    truncated = max_rows is not None and len(rows) > max_rows
    if truncated:
        rows = rows[:max_rows]
    body = _dumps([{c: hana.json_value(v) for c, v in zip(cols, r)} for r in rows]).encode("utf-8")
    return LookupResult(len(rows), truncated, body)


# ---------------------------------------------------------------- routes
@app.get("/health")
def health_public():
    return jsonify(status="ok")


@app.get("/api/health")
def health():
    try:
        entry, how = STATE.get(("health",), "health", SETTINGS.health_cache_seconds, hana.ping,
                               allow_stale=False)
    except cache.Busy as exc:
        return busy(exc)
    except Exception as exc:
        return failed("HANA health check", exc, status=503)
    g.cache = how
    return jsonify(status="ok", hana=entry.payload)


@app.get("/api/catalog")
def catalog():
    return jsonify(
        reports={n: {"description": r["description"], "cache_seconds": r["ttl"]} for n, r in REPORTS.items()},
        queries={n: {"description": q.get("description", ""),
                     "params": [_catalog_param(*p) for p in _param_specs(q)],
                     "max_rows": q.get("max_rows")}
                 for n, q in _enabled_queries().items()},
    )


def _catalog_param(name, typ, default, lo, hi, choices):
    """A param as the catalog lists it. For text, max is the longest value allowed."""
    entry = {"name": name, "type": typ.__name__, "default": default, "min": lo, "max": hi, "required": default is None}
    if choices:
        entry["choices"] = list(choices)
    return entry


@app.get("/api/reports/<name>")
def report(name):
    spec = REPORTS.get(name)
    if spec is None:
        abort(404, description=f"Unknown report '{name}'.")
    try:
        entry, how = STATE.get(("report", name), "report", spec["ttl"], lambda: produce_report(name),
                               refresh=_refresh())
    except cache.Busy as exc:
        return busy(exc)
    except Exception as exc:
        return failed(f"Report {name}", exc)
    g.cache = how
    stale = how == "stale"
    result = entry.payload

    if request.args.get("format") == "json":
        try:
            rows = entry.variant("json", lambda: _report_json_rows(result), slot=STATE.waiters)
        except cache.Busy as exc:
            return busy(exc)
        head = _dumps({"report": name, "refreshed_at": _stamp(entry.wall), "row_count": result.row_count,
                       "stale": stale})
        return _stream([head[:-1].encode("utf-8") + b',"rows":'] + rows + [b"}\n"], "application/json", {})

    filename = spec["filename"].format(date=datetime.date.fromtimestamp(entry.wall).isoformat())
    headers = {
        "Content-Disposition": f'attachment; filename="{filename}"',
        "X-Row-Count": str(result.row_count),
        "X-Refreshed-At": _stamp(entry.wall),
    }
    if stale:
        headers["X-Stale"] = "1"
    if _wants_gzip():
        try:
            gz = entry.variant("tsv.gz", lambda: gzip.compress(b"".join(result.tsv), compresslevel=6, mtime=0),
                               slot=STATE.waiters)
        except cache.Busy as exc:
            return busy(exc)
        headers["Content-Encoding"] = "gzip"
        return _stream([gz], "text/tab-separated-values", headers)
    return _stream(result.tsv, "text/tab-separated-values", headers)


@app.get("/api/data/<name>")
def data(name):
    spec = _enabled_queries().get(name)
    if spec is None:
        abort(404, description=f"Unknown query '{name}'.")
    params, shown = _params(spec)
    try:
        entry, how = STATE.get(("data", name, params), "data", spec.get("ttl", 60),
                               lambda: produce_lookup(spec, params), refresh=_refresh())
    except cache.Busy as exc:
        return busy(exc)
    except Exception as exc:
        return failed(f"Query {name}", exc)
    g.cache = how
    r = entry.payload
    head = _dumps({"query": name, "params": shown, "refreshed_at": _stamp(entry.wall), "row_count": r.row_count,
                   "stale": how == "stale", "truncated": r.truncated})
    return _stream([head[:-1].encode("utf-8") + b',"rows":', r.rows_json, b"}\n"], "application/json", {})


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
    g.error_ref = ref
    return jsonify(error=f"The bridge hit an unexpected error (bridge log ref {ref})."), 500


def serve_options():
    """waitress settings. channel_timeout (idle seconds per connection) stays above Cloudflare's ~100 s, and every
    request ends before it because waiting on HANA is capped at REQUEST_WAIT_SECONDS."""
    return {"threads": SETTINGS.threads, "channel_timeout": 120}


def main():
    from waitress import serve

    warn_about_settings()
    log.info("SAP bridge on http://%s:%s (%s threads)", SETTINGS.host, SETTINGS.port, SETTINGS.threads)
    serve(app, host=SETTINGS.host, port=SETTINGS.port, **serve_options())


if __name__ == "__main__":
    os.chdir(BASE)
    main()
