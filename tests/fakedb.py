"""A stand-in for hdbcli.dbapi, so tests drive the real hana.connect() and hana.run() without a database.

    with fakedb.installed(rows=[...], description=[...]) as db:
        hana.run("SELECT ...")
        db.connect_kwargs      # what hana.connect() passed to dbapi.connect
        db.fetches             # ("all",) or ("many", n) or ("many", n) repeated, in call order

Failures use the same shape as the real driver: hdbcli.dbapi.Error has .errorcode and .errortext, and
args[0] is the code (checked against hdbcli 2.30.27).
"""
import contextlib
import threading
import time

import hana


class Error(Exception):
    """Same attributes as hdbcli.dbapi.Error."""
    def __init__(self, errorcode, errortext):
        super().__init__(errorcode, errortext)
        self.errorcode, self.errortext = errorcode, errortext

    def __str__(self):
        return f"({self.errorcode}, '{self.errortext}')"


class FakeDb:
    def __init__(self, rows=(), description=(("A",),), delay=0.0, fail=None, connect_fail=None):
        self.rows = list(rows)
        self.description = list(description)
        self.delay = delay              # seconds execute() sleeps (a slow query)
        self.fail = fail                # exception execute() raises
        self.connect_fail = connect_fail
        self.connect_kwargs = None
        self.fetches = []
        self.executed = []
        self.closed = 0
        self.lock = threading.Lock()

    # -- the module-level API hana.py uses: dbapi.connect(**kw), dbapi.Error
    Error = Error

    def connect(self, **kw):
        with self.lock:
            self.connect_kwargs = dict(kw)
        if self.connect_fail:
            raise self.connect_fail
        return _Conn(self)


class _Conn:
    def __init__(self, db):
        self.db = db

    def cursor(self):
        return _Cursor(self.db)

    def close(self):
        with self.db.lock:
            self.db.closed += 1


class _Cursor:
    def __init__(self, db):
        self.db = db
        self.description = None
        self._pos = 0

    def execute(self, sql, params=()):
        self.db.executed.append((sql, tuple(params)))
        if self.db.delay:
            time.sleep(self.db.delay)
        if self.db.fail:
            raise self.db.fail
        self.description = [tuple(d) + (None,) * (7 - len(d)) for d in self.db.description]

    def fetchall(self):
        self.db.fetches.append(("all",))
        rows, self._pos = self.db.rows[self._pos:], len(self.db.rows)
        return rows

    def fetchmany(self, n):
        self.db.fetches.append(("many", n))
        rows = self.db.rows[self._pos:self._pos + n]
        self._pos += len(rows)
        return rows

    def close(self):
        pass


@contextlib.contextmanager
def installed(**kw):
    """Replace hana.dbapi with a FakeDb for the duration; yields the FakeDb."""
    db = FakeDb(**kw)
    orig = hana.dbapi
    hana.dbapi = db
    try:
        yield db
    finally:
        hana.dbapi = orig
