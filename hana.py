"""Read-only access to the SAP Business One HANA database (SAP's hdbcli driver), and value formatting.

Every run opens a short-lived connection, executes one SELECT and closes it.
Settings come from config.Settings (set once at start-up with configure()); timeouts are passed to the driver in
milliseconds (connectTimeout, communicationTimeout: checked against hdbcli 2.30 and SAP's SQLDBC property reference).
"""
import contextlib
import datetime
import decimal
import math
import re

from hdbcli import dbapi

import config

SETTINGS = None          # config.Settings; configure() sets it


def configure(settings):
    global SETTINGS
    SETTINGS = settings


def _settings():
    return SETTINGS if SETTINGS is not None else config.from_env()


class QueryShapeError(Exception):
    """The query's result cannot be served as it is (for example two columns with the same name)."""


def connect(settings=None):
    s = settings or _settings()
    props = dict(
        address=s.hana_host,
        port=s.hana_port,
        user=s.hana_user,
        password=s.hana_password,
        currentSchema=s.hana_schema,             # the SAP B1 company database, e.g. SBO_SPYDER
        encrypt=s.hana_encrypt,
        sslValidateCertificate=s.hana_validate_cert,
        # A HANA server that is down or stuck fails the run instead of holding it: milliseconds.
        connectTimeout=s.hana_connect_timeout_ms,
        communicationTimeout=s.hana_query_timeout_ms,
    )
    if s.hana_truststore:
        props["sslTrustStore"] = s.hana_truststore
    if s.hana_cert_hostname:
        props["sslHostNameInCertificate"] = s.hana_cert_hostname
    return dbapi.connect(**props)


# ---------------------------------------------------------------- the SELECT-only guard (a typo guard; the HANA grant
# is the real control: the bridge's user can only SELECT)
_TOKEN = re.compile(r"""(?P<comment>--[^\n]*|/\*.*?\*/)|(?P<string>'(?:[^']|'')*')|(?P<quoted>"(?:[^"]|"")*")
                        |(?P<word>[A-Za-z_][A-Za-z0-9_$#]*)|(?P<other>\S)""", re.S | re.X)
_FORBIDDEN = {"INSERT", "UPDATE", "DELETE", "UPSERT", "MERGE", "INTO", "CREATE", "ALTER", "DROP", "TRUNCATE",
              "GRANT", "REVOKE", "CALL", "EXEC", "EXECUTE", "DO", "COMMIT", "ROLLBACK", "SAVEPOINT", "LOCK", "SET",
              "LOAD", "UNLOAD", "IMPORT", "EXPORT", "RENAME", "COMMENT", "REFRESH", "RECOVER", "BACKUP"}
_COMMENTS = re.compile(r"--[^\n]*|/\*.*?\*/", re.S)


def _words(sql):
    """(kind, text) tokens with comments dropped; string literals and quoted identifiers are single tokens."""
    return [(m.lastgroup, m.group(m.lastgroup)) for m in _TOKEN.finditer(sql) if m.lastgroup != "comment"]


def prepare_sql(text):
    """Make a Query Manager style .sql file ready to send: drop the trailing ';' and refuse anything that is not a
    single SELECT / WITH statement reading data. Refuses write and DDL keywords anywhere outside string literals and
    quoted identifiers (so WITH ... DELETE and SELECT ... INTO are refused too). REPLACE(...) as a function is allowed.
    This is a guard against mistakes, not the security boundary: the HANA user can only SELECT."""
    sql = text.strip()
    while sql.endswith(";"):
        sql = sql[:-1].rstrip()
    body = _COMMENTS.sub(" ", sql).strip()
    if not re.match(r"(?is)^(select|with)\b", body):
        raise ValueError("Only SELECT / WITH queries are allowed.")
    toks = _words(sql)
    if any(kind == "other" and tok == ";" for kind, tok in toks):
        raise ValueError("Only one statement per query file is allowed.")
    for i, (kind, tok) in enumerate(toks):
        if kind != "word":
            continue
        up = tok.upper()
        if up == "REPLACE" and not (i + 1 < len(toks) and toks[i + 1][1] == "("):
            raise ValueError("REPLACE is only allowed as a function.")
        if up in _FORBIDDEN:
            raise ValueError(f"'{up}' is not allowed: the bridge only reads.")
    return sql


# ---------------------------------------------------------------- running
def _columns(cur):
    cols = [d[0] for d in cur.description]
    dupes = sorted({c for c in cols if cols.count(c) > 1})
    if dupes:
        raise QueryShapeError(f"The query returns these column names more than once: {', '.join(dupes)}.")
    return cols


def run(sql, params=(), max_rows=None):
    """Run a SELECT. Returns (column names, list of row tuples).
    With max_rows, reads at most max_rows + 1 rows, so the caller can tell the result was cut."""
    conn = connect()
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        cols = _columns(cur)
        rows = cur.fetchall() if max_rows is None else cur.fetchmany(max_rows + 1)
        cur.close()
        return cols, [tuple(r) for r in rows]
    finally:
        conn.close()


def batches(sql, params=(), batch_rows=5000):
    """Run a SELECT and read it in batches: yields the column names first, then lists of row tuples.
    The connection closes when the caller has read everything or stops early."""
    conn = connect()
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        yield _columns(cur)
        while True:
            rows = cur.fetchmany(batch_rows)
            if not rows:
                break
            yield [tuple(r) for r in rows]
        cur.close()
    finally:
        conn.close()


def ping():
    cols, rows = run('SELECT CURRENT_TIMESTAMP AS "ServerTime", CURRENT_SCHEMA AS "Schema" FROM DUMMY')
    return {c: json_value(v) for c, v in zip(cols, rows[0])}


# ---------------------------------------------------------------- what went wrong, from the driver's error code
# Codes from SAP's SQL error code list and hdbcli client errors (checked: hdbcli 2.30.27 raises dbapi.Error with
# .errorcode and .errortext, args[0] the code). -10709: connection failed (refused, reset, connect timeout, TLS set-up);
# -10807: connection down after connecting (for example a receive timeout); -10821: session not connected.
AUTH, TIMEOUT, UNREACHABLE, QUERY, OTHER = "auth", "timeout", "unreachable", "query", "other"
_AUTH_CODES = {10, 414, 663}                     # authentication failed; password change forced; user locked
_QUERY_CODES = {257, 258, 259, 260, 261, 262, 263, 264, 265, 266, 267, 268, 269, 270, 274, 287, 291, 307, 328, 339,
                362}
_TLS_TEXT = re.compile(r"\b(ssl|tls|certificate|crypto)\b|RTE:\[30\d{4}\]", re.I)
_TIMEOUT_TEXT = re.compile(r"\btimeout\b|\btimed out\b|\btime-out\b", re.I)


def error_code(exc):
    code = getattr(exc, "errorcode", None)
    if code is None and getattr(exc, "args", None):
        first = exc.args[0]
        if isinstance(first, int) and not isinstance(first, bool):
            code = first
    return code if isinstance(code, int) and not isinstance(code, bool) else None


def error_class(exc):
    """One of AUTH, TIMEOUT, UNREACHABLE, QUERY, OTHER. Uses the driver's error code where there is one; the message
    text only decides within a code family, or when there is no code at all."""
    if isinstance(exc, QueryShapeError):
        return QUERY
    code = error_code(exc)
    text = getattr(exc, "errortext", None) or str(exc)
    if code is not None:
        if code in _AUTH_CODES:
            return AUTH
        if code in _QUERY_CODES:
            return QUERY
        if code == -10709:                            # never got a working connection
            return OTHER if _TLS_TEXT.search(text) else UNREACHABLE
        if code in (-10807, -10821, -10108):          # connection lost after it was made
            return TIMEOUT if _TIMEOUT_TEXT.search(text) else UNREACHABLE
        return OTHER
    # No code: an exception that did not come from the driver. Decide on whole phrases, most specific first.
    low = text.lower()
    if re.search(r"\bauthentication failed\b|\binvalid username or password\b", low):
        return AUTH
    if re.search(r"\b(sql syntax error|invalid column name|invalid table name|invalid schema name)\b", low):
        return QUERY
    if isinstance(exc, TimeoutError) or _TIMEOUT_TEXT.search(low):
        return TIMEOUT
    if isinstance(exc, ConnectionError) or re.search(
            r"\b(connection refused|connection reset|network is unreachable|no route to host|"
            r"name or service not known)\b", low):
        return UNREACHABLE
    return OTHER


# ---------------------------------------------------------------- value formatting
def json_value(v):
    """A value as the JSON outputs carry it: DECIMAL -> float, DATE -> "YYYY-MM-DD", TIMESTAMP -> "YYYY-MM-DD
    HH:MM:SS", TIME -> "HH:MM:SS", NULL / NaN / infinity -> null, binary -> hex, anything else unknown -> text."""
    if v is None or isinstance(v, (str, bool, int)):
        return v
    if isinstance(v, float):
        return v if math.isfinite(v) else None
    if isinstance(v, decimal.Decimal):
        if not v.is_finite():
            return None
        f = float(v)
        return f if math.isfinite(f) else None
    if isinstance(v, datetime.datetime):
        return v.isoformat(sep=" ", timespec="seconds")
    if isinstance(v, datetime.date):
        return v.isoformat()
    if isinstance(v, datetime.time):
        return v.isoformat(timespec="seconds")
    if isinstance(v, (bytes, bytearray, memoryview)):
        return bytes(v).hex()
    return str(v)


_TABS = re.compile(r"[\t\r\n]+")


def text_value(v):
    """One cell of the tab-delimited export, in a form the dashboard's parser reads:
    dates as YYYY-MM-DD, plain decimals (no thousands separators), no tabs or line breaks."""
    if v is None:
        return ""
    if isinstance(v, str):
        return _TABS.sub(" ", v)
    if isinstance(v, datetime.datetime):
        if v.time() == datetime.time(0):
            return v.date().isoformat()
        return v.isoformat(sep=" ", timespec="seconds")
    if isinstance(v, datetime.date):
        return v.isoformat()
    if isinstance(v, decimal.Decimal):
        if not v.is_finite():
            return ""
        s = format(v, "f")
        if "." in s:
            s = s.rstrip("0").rstrip(".")
        return "0" if s in ("-0", "") else s
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        # Plain digits, never exponent notation (repr(0.00001) is "1e-05"): the dashboard reads numbers as typed.
        if not math.isfinite(v):
            return ""
        return text_value(decimal.Decimal(repr(v)))
    if isinstance(v, (bytes, bytearray, memoryview)):
        return bytes(v).hex()
    return _TABS.sub(" ", str(v))


def tsv_line(values):
    return "\t".join([text_value(v) for v in values])


def to_tsv(cols, rows):
    """Tab-delimited UTF-8 text with a header row: the same layout as SAP's Export to Excel."""
    lines = [tsv_line(cols)]
    lines += [tsv_line(row) for row in rows]
    return "\r\n".join(lines) + "\r\n"


@contextlib.contextmanager
def configured(settings):
    """For tests: use these settings for the duration."""
    old = SETTINGS
    configure(settings)
    try:
        yield
    finally:
        configure(old)
