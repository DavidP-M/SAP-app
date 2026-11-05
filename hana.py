"""Read-only access to the SAP Business One HANA database (SAP's hdbcli driver).

Every run opens a short-lived connection, executes one SELECT and closes it.
"""
import datetime
import decimal
import os
import re

from hdbcli import dbapi


def connect():
    return dbapi.connect(
        address=os.environ["HANA_HOST"],
        port=int(os.environ.get("HANA_PORT", "30015")),
        user=os.environ["HANA_USER"],
        password=os.environ["HANA_PASSWORD"],
        currentSchema=os.environ["HANA_SCHEMA"],  # the SAP B1 company database, e.g. SBO_SPYDER
        encrypt=os.environ.get("HANA_ENCRYPT", "true").lower() == "true",
        sslValidateCertificate=os.environ.get("HANA_VALIDATE_CERT", "false").lower() == "true",
    )


_COMMENTS = re.compile(r"--[^\n]*|/\*.*?\*/", re.S)


def prepare_sql(text):
    """Make a Query Manager style .sql file ready to send: drop the trailing ';' and refuse anything
    that is not a single SELECT / WITH statement (the bridge never writes to SAP)."""
    sql = text.strip()
    while sql.endswith(";"):
        sql = sql[:-1].rstrip()
    body = _COMMENTS.sub(" ", sql).strip()
    if not re.match(r"(?is)^(select|with)\b", body):
        raise ValueError("Only SELECT / WITH queries are allowed.")
    if ";" in body:
        raise ValueError("Only one statement per query file is allowed.")
    return sql


def run(sql, params=()):
    """Run a SELECT. Returns (column names, list of row tuples)."""
    conn = connect()
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        cols = [d[0] for d in cur.description]
        rows = cur.fetchall()
        cur.close()
        return cols, [tuple(r) for r in rows]
    finally:
        conn.close()


def ping():
    cols, rows = run('SELECT CURRENT_TIMESTAMP AS "ServerTime", CURRENT_SCHEMA AS "Schema" FROM DUMMY')
    return {c: json_value(v) for c, v in zip(cols, rows[0])}


# ---------------------------------------------------------------- value formatting
def json_value(v):
    if isinstance(v, decimal.Decimal):
        return float(v)
    if isinstance(v, datetime.datetime):
        return v.isoformat(sep=" ", timespec="seconds")
    if isinstance(v, datetime.date):
        return v.isoformat()
    if isinstance(v, (bytes, bytearray, memoryview)):
        return bytes(v).hex()
    return v


def text_value(v):
    """One cell of the tab-delimited export, in a form the dashboard's parser reads:
    dates as YYYY-MM-DD, plain decimals (no thousands separators), no tabs or line breaks."""
    if v is None:
        return ""
    if isinstance(v, datetime.datetime):
        if v.time() == datetime.time(0):
            return v.date().isoformat()
        return v.isoformat(sep=" ", timespec="seconds")
    if isinstance(v, datetime.date):
        return v.isoformat()
    if isinstance(v, decimal.Decimal):
        s = format(v, "f")
        if "." in s:
            s = s.rstrip("0").rstrip(".")
        return "0" if s in ("-0", "") else s
    if isinstance(v, float):
        return repr(v)
    if isinstance(v, (bytes, bytearray, memoryview)):
        return bytes(v).hex()
    return re.sub(r"[\t\r\n]+", " ", str(v))


def to_tsv(cols, rows):
    """Tab-delimited UTF-8 text with a header row: the same layout as SAP's Export to Excel."""
    lines = ["\t".join(text_value(c) for c in cols)]
    lines += ["\t".join(text_value(v) for v in row) for row in rows]
    return "\r\n".join(lines) + "\r\n"
