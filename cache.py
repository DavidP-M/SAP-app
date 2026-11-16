"""The bridge's result cache: one HANA run per key, bounded waiting, failure back-off and limits.

How a request gets its answer (ResultCache.get):

1. A result younger than its ttl (or than REFRESH_MIN_SECONDS for refresh=1) is served from memory: "hit".
2. If the last run for this key failed less than FAILURE_BACKOFF_SECONDS ago, HANA is not asked again: the last good
   result is served ("stale"), or, with nothing cached, the same failure (same log reference) is returned.
3. Otherwise the request joins the run already in progress for this key, or starts one. The run happens in its own
   thread, not in the web server's request thread, so it finishes and fills the cache even if every caller gave up.
4. The request waits for the run at most REQUEST_WAIT_SECONDS (kept under Cloudflare's ~100 s limit). At most
   HANA_WAITERS_MAX request threads may wait at once (default: web server threads - 2), so /health and cached answers
   always have a free thread. A request that cannot wait, or waited too long, gets the last good result ("stale") or a
   503 in plain words.

Kinds: "report" and "health" entries are kept (a report entry is the last good result); "data" (lookups) entries live in
an LRU of LOOKUP_CACHE_MAX entries, and new lookup runs are limited to LOOKUP_MAX_CONCURRENT at once and
LOOKUP_MAX_PER_MINUTE. Ages use a monotonic clock; the wall clock only stamps when HANA was read.
"""
import collections
import logging
import threading
import time
import uuid

log = logging.getLogger("sap-bridge")


class Busy(Exception):
    """The bridge will not wait for HANA now. The message is shown to dashboard users as is (503)."""


class Failed(Exception):
    """A HANA run failed. .cause is the exception, .ref the log reference it was logged under."""

    def __init__(self, cause, ref):
        super().__init__(str(cause))
        self.cause, self.ref = cause, ref


class Entry:
    """One cached result. .payload is what produce() returned; variants (JSON, gzip) are built once, on demand."""
    __slots__ = ("payload", "mono", "wall", "_lock", "_variants")

    def __init__(self, payload, mono, wall):
        self.payload, self.mono, self.wall = payload, mono, wall
        self._lock = threading.Lock()
        self._variants = {}

    def variant(self, name, build, slot=None):
        """The variant called name, building it the first time. slot (a non-blocking acquire / release pair) limits
        how many request threads may wait here while it is built."""
        v = self._variants.get(name)
        if v is not None:
            return v
        if slot is not None and not slot.acquire(blocking=False):
            raise Busy("The bridge is busy preparing this data. Try again in a moment.")
        try:
            with self._lock:
                v = self._variants.get(name)
                if v is None:
                    v = build()
                    self._variants[name] = v
                return v
        finally:
            if slot is not None:
                slot.release()


class _Flight:
    __slots__ = ("done", "entry", "error")

    def __init__(self):
        self.done = threading.Event()
        self.entry = None
        self.error = None


class ResultCache:
    PINNED_KINDS = ("report", "health")

    def __init__(self, settings, clock=time.monotonic, wall=time.time):
        self.s = settings
        self.clock, self.wall = clock, wall
        self._lock = threading.Lock()
        self._pinned = {}                                 # report / health entries: never evicted
        self._lru = collections.OrderedDict()             # lookup entries
        self._flights = {}                                # key -> _Flight while a run is going
        self._failures = collections.OrderedDict()        # key -> (until, Failed)
        self.waiters = threading.BoundedSemaphore(settings.hana_waiters_max)
        self._lookup_running = 0
        self._lookup_starts = collections.deque(maxlen=settings.lookup_max_per_minute)   # bounded by the ceiling
        self.runs = 0                                     # HANA runs started (for tests and the harness)

    # -------------------------------------------------------------- reading and writing entries
    def _peek(self, key, kind):
        if kind in self.PINNED_KINDS:
            return self._pinned.get(key)
        e = self._lru.get(key)
        if e is not None:
            self._lru.move_to_end(key)
        return e

    def _store(self, key, kind, entry):
        if kind in self.PINNED_KINDS:
            self._pinned[key] = entry
            return
        self._lru[key] = entry
        self._lru.move_to_end(key)
        while len(self._lru) > self.s.lookup_cache_max:
            self._lru.popitem(last=False)

    def size(self):
        with self._lock:
            return {"pinned": len(self._pinned), "lookups": len(self._lru), "flights": len(self._flights),
                    "failures": len(self._failures)}

    def age(self, key, seconds):
        """For tests: make the entry for key look older (both clocks)."""
        with self._lock:
            e = self._pinned.get(key) or self._lru.get(key)
            if e is not None:
                e.mono -= seconds
                e.wall -= seconds

    def entry(self, key):
        with self._lock:
            return self._pinned.get(key) or self._lru.get(key)

    # -------------------------------------------------------------- the one entry point
    def get(self, key, kind, ttl, produce, refresh=False, allow_stale=True):
        """Return (entry, how), how being "hit", "miss" or "stale". Raises Busy or Failed."""
        window = self.s.refresh_min_seconds if refresh else ttl
        with self._lock:
            now = self.clock()
            entry = self._peek(key, kind)
            if entry is not None and now - entry.mono < window:
                return entry, "hit"
            failure = self._failures.get(key)
            if failure is not None:
                if now < failure[0]:
                    if entry is not None and allow_stale:
                        return entry, "stale"
                    raise failure[1]
                del self._failures[key]
            flight = self._flights.get(key)
            if flight is None:
                if kind == "data":
                    refusal = self._lookup_refusal(now)
                    if refusal:
                        if entry is not None and allow_stale:
                            return entry, "stale"
                        raise Busy(refusal)
                flight = _Flight()
                self._flights[key] = flight
                self.runs += 1
                threading.Thread(target=self._run, args=(key, kind, produce, flight), daemon=True,
                                 name=f"hana-{kind}").start()

        if not flight.done.is_set():
            if not self.waiters.acquire(blocking=False):
                if entry is not None and allow_stale:
                    return entry, "stale"
                raise Busy("The bridge is busy with other SAP requests. Try again in a moment.")
            try:
                finished = flight.done.wait(self.s.request_wait_seconds)
            finally:
                self.waiters.release()
            if not finished:
                if entry is not None and allow_stale:
                    return entry, "stale"
                raise Busy("SAP HANA is still working on this. Try again in a minute.")
        if flight.error is not None:
            if entry is not None and allow_stale:
                return entry, "stale"
            raise flight.error
        return flight.entry, "miss"

    def _lookup_refusal(self, now):
        """Called with the lock held. None if a new lookup run may start (and counts it), else the reason."""
        starts = self._lookup_starts
        while starts and now - starts[0] >= 60:
            starts.popleft()
        if self._lookup_running >= self.s.lookup_max_concurrent:
            return "Too many SAP lookups are running right now. Try again in a moment."
        if len(starts) >= self.s.lookup_max_per_minute:
            return "Too many SAP lookups in the last minute. Try again in a minute."
        starts.append(now)
        self._lookup_running += 1
        return None

    def _run(self, key, kind, produce, flight):
        started = self.clock()
        try:
            payload = produce()
            entry = Entry(payload, self.clock(), self.wall())
            with self._lock:
                self._store(key, kind, entry)
                self._failures.pop(key, None)
            flight.entry = entry
            log.info("ran %s in %.1fs", _label(key), self.clock() - started)
        except BaseException as exc:            # the thread must always finish the flight
            ref = uuid.uuid4().hex[:8]
            log.error("%s failed (ref %s): %s", _label(key), ref, exc, exc_info=exc)
            flight.error = Failed(exc, ref)
            with self._lock:
                self._failures[key] = (self.clock() + self.s.failure_backoff_seconds, flight.error)
                self._failures.move_to_end(key)
                while len(self._failures) > 1000:
                    self._failures.popitem(last=False)
        finally:
            with self._lock:
                self._flights.pop(key, None)
                if kind == "data":
                    self._lookup_running -= 1
            flight.done.set()


def _label(key):
    """How a key is named in the log: reports and queries by name; lookup parameter values are left out."""
    if key and key[0] == "data":
        return f"query {key[1]}"
    if key and key[0] == "report":
        return f"report {key[1]}"
    return str(key[0])
