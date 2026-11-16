"""Every bridge setting, read once from the environment (.env) and checked at start-up.

A bad value stops the bridge with a message naming the setting, instead of failing later on a request.
"""
import dataclasses
import os
import re

_LABEL = re.compile(r"^[A-Za-z0-9_.-]{1,32}$")
MIN_KEY_LENGTH = 32


@dataclasses.dataclass(frozen=True)
class ApiKey:
    label: str
    key: str


@dataclasses.dataclass(frozen=True)
class Settings:
    # HANA
    hana_host: str = ""
    hana_port: int = 30015
    hana_user: str = ""
    hana_password: str = dataclasses.field(default="", repr=False)
    hana_schema: str = ""
    hana_encrypt: bool = True
    hana_validate_cert: bool = True
    hana_truststore: str = ""
    hana_cert_hostname: str = ""
    hana_connect_timeout_ms: int = 15000
    hana_query_timeout_ms: int = 240000
    # Web server
    host: str = "127.0.0.1"
    port: int = 8085
    threads: int = 16
    # Caching and limits
    refresh_min_seconds: int = 30
    request_wait_seconds: float = 75.0
    failure_backoff_seconds: float = 15.0
    hana_waiters_max: int = 14
    health_cache_seconds: float = 15.0
    lookup_cache_max: int = 500
    lookup_max_concurrent: int = 2
    lookup_max_per_minute: int = 60
    report_max_rows: int = 500000
    fetch_batch_rows: int = 5000
    gzip_responses: bool = False
    enabled_queries: tuple = ()          # empty: all
    api_keys: tuple = dataclasses.field(default=(), repr=False)
    warnings: tuple = ()


def _int(env, name, default, lo=None, hi=None):
    raw = env.get(name, "").strip()
    if raw == "":
        return default
    try:
        val = int(raw)
    except ValueError:
        raise ValueError(f"{name} must be a whole number (got {raw!r}).") from None
    if (lo is not None and val < lo) or (hi is not None and val > hi):
        raise ValueError(f"{name} must be between {lo} and {hi} (got {val}).")
    return val


def _float(env, name, default, lo=0.0):
    raw = env.get(name, "").strip()
    if raw == "":
        return default
    try:
        val = float(raw)
    except ValueError:
        raise ValueError(f"{name} must be a number (got {raw!r}).") from None
    if val < lo or val != val:
        raise ValueError(f"{name} must be at least {lo} (got {raw}).")
    return val


def _bool(env, name, default):
    raw = env.get(name, "").strip().lower()
    if raw == "":
        return default
    if raw in ("true", "1", "yes", "on"):
        return True
    if raw in ("false", "0", "no", "off"):
        return False
    raise ValueError(f"{name} must be true or false (got {raw!r}).")


def parse_api_keys(raw):
    """API_KEYS: comma-separated. Each item is either a bare key or label:key (label: letters, digits, . _ -,
    at most 32). Bare keys get the labels key1, key2, ... Returns (keys, warnings)."""
    keys, warnings, seen = [], [], set()
    for n, item in enumerate((x.strip() for x in raw.split(",")), start=1):
        if not item:
            continue
        label, _, key = item.partition(":")
        if not key or not _LABEL.fullmatch(label):
            label, key = f"key{n}", item
        key = key.strip()
        if not key:
            continue
        if label in seen:
            raise ValueError(f"API_KEYS has the label '{label}' twice.")
        seen.add(label)
        if len(key) < MIN_KEY_LENGTH:
            warnings.append(f"API key '{label}' is shorter than {MIN_KEY_LENGTH} characters; make a new one with "
                            "python -c \"import secrets; print(secrets.token_urlsafe(32))\"")
        keys.append(ApiKey(label, key))
    return tuple(keys), warnings


def from_env(env=None, known_queries=None):
    env = os.environ if env is None else env
    threads = _int(env, "THREADS", 16, 4, 64)
    keys, warnings = parse_api_keys(env.get("API_KEYS", ""))
    enabled = tuple(q.strip() for q in env.get("ENABLED_QUERIES", "").split(",") if q.strip())
    if known_queries is not None:
        unknown = [q for q in enabled if q not in known_queries]
        if unknown:
            raise ValueError(f"ENABLED_QUERIES names queries that do not exist: {', '.join(unknown)}.")
    validate = _bool(env, "HANA_VALIDATE_CERT", True)
    encrypt = _bool(env, "HANA_ENCRYPT", True)
    if not encrypt:
        warnings.append("HANA_ENCRYPT is false: the HANA password and all data cross the network unencrypted.")
    elif not validate:
        warnings.append("HANA_VALIDATE_CERT is false: the bridge does not check that it is talking to the real HANA "
                        "server. Install the HANA certificate and set HANA_VALIDATE_CERT=true.")
    waiters_default = threads - 2
    s = Settings(
        hana_host=env.get("HANA_HOST", ""),
        hana_port=_int(env, "HANA_PORT", 30015, 1, 65535),
        hana_user=env.get("HANA_USER", ""),
        hana_password=env.get("HANA_PASSWORD", ""),
        hana_schema=env.get("HANA_SCHEMA", ""),
        hana_encrypt=encrypt,
        hana_validate_cert=validate,
        hana_truststore=env.get("HANA_TRUSTSTORE", "").strip(),
        hana_cert_hostname=env.get("HANA_CERT_HOSTNAME", "").strip(),
        hana_connect_timeout_ms=_int(env, "HANA_CONNECT_TIMEOUT_MS", 15000, 1000, 600000),
        hana_query_timeout_ms=_int(env, "HANA_QUERY_TIMEOUT_MS", 240000, 1000, 3600000),
        host=env.get("HOST", "127.0.0.1").strip() or "127.0.0.1",
        port=_int(env, "PORT", 8085, 1, 65535),
        threads=threads,
        refresh_min_seconds=_int(env, "REFRESH_MIN_SECONDS", 30, 0, 3600),
        request_wait_seconds=_float(env, "REQUEST_WAIT_SECONDS", 75.0, 0.1),
        failure_backoff_seconds=_float(env, "FAILURE_BACKOFF_SECONDS", 15.0, 0.0),
        hana_waiters_max=_int(env, "HANA_WAITERS_MAX", waiters_default, 1, threads - 2),
        health_cache_seconds=_float(env, "HEALTH_CACHE_SECONDS", 15.0, 0.0),
        lookup_cache_max=_int(env, "LOOKUP_CACHE_MAX", 500, 1, 100000),
        lookup_max_concurrent=_int(env, "LOOKUP_MAX_CONCURRENT", 2, 1, 32),
        lookup_max_per_minute=_int(env, "LOOKUP_MAX_PER_MINUTE", 60, 1, 1000000),
        report_max_rows=_int(env, "REPORT_MAX_ROWS", 500000, 1, 50000000),
        fetch_batch_rows=_int(env, "FETCH_BATCH_ROWS", 5000, 1, 1000000),
        gzip_responses=_bool(env, "GZIP_RESPONSES", False),
        enabled_queries=enabled,
        api_keys=keys,
        warnings=tuple(warnings),
    )
    return s
