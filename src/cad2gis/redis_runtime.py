"""Optional, disposable Redis cache for compact query summaries.

SQLite/source manifests remain authoritative. This module never stores conversion
receipts, commits semantic revisions, acquires publication locks or schedules jobs.
Redis failures are a cache miss, including invalid configuration and corrupt data.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import hashlib
import importlib.util
import json
import math
import os
import threading
import time
from typing import Any
from urllib.parse import urlsplit


CACHE_SCHEMA = "cad2gis.query_summary_cache.v1"
MAX_ENTRY_BYTES = 98304
URL_ENV = "CAD2GIS_REDIS_URL"
TTL_ENV = "CAD2GIS_REDIS_CACHE_TTL_SECONDS"
TIMEOUT_ENV = "CAD2GIS_REDIS_TIMEOUT_MS"


def _encode(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_encode(value)).hexdigest()


@dataclass(frozen=True)
class QueryCacheBinding:
    source: str
    snapshot: str
    index: str
    schema: str
    policy: str
    filters: str
    revision: str
    implementation: str

    def identity(self) -> dict[str, str]:
        value = dict(vars(self))
        if any(not isinstance(item, str) or not item for item in value.values()):
            raise ValueError("Every query-cache binding must be explicit")
        return value

    @property
    def key(self) -> str:
        # Source names, CAD labels, paths and filter text never appear in keys.
        return "cad2gis:query-summary:v1:" + _digest(self.identity())


@dataclass(frozen=True, repr=False)
class _Settings:
    url: str
    ttl_seconds: int
    timeout_ms: int


def _settings() -> _Settings | None:
    url = os.environ.get(URL_ENV, "").strip()
    if not url:
        return None
    parsed = urlsplit(url)
    # URL query options can override socket timeouts/TLS validation in redis-py.
    # Accept a narrow URL shape and supply all client policy in code instead.
    if (parsed.scheme not in {"redis", "rediss"} or not parsed.hostname
            or parsed.query or parsed.fragment
            or parsed.path not in {"", "/"} and not parsed.path[1:].isdigit()):
        raise ValueError("Invalid Redis URL")
    if parsed.port is not None and not 1 <= parsed.port <= 65535:
        raise ValueError("Invalid Redis port")
    ttl = int(os.environ.get(TTL_ENV, "120"))
    timeout = int(os.environ.get(TIMEOUT_ENV, "100"))
    if not 1 <= ttl <= 3600 or not 1 <= timeout <= 1000:
        raise ValueError("Invalid Redis TTL or timeout")
    return _Settings(url, ttl, timeout)


class QuerySummaryCache:
    """A bounded cache; the small process-local circuit avoids outage retry storms."""

    def __init__(self, client: Any, *, ttl_seconds: int, timeout_ms: int):
        self._client = client
        self.ttl_seconds = ttl_seconds
        self.timeout_ms = timeout_ms
        self._lock = threading.Lock()
        self._unavailable_until = 0.0
        self._counts = dict.fromkeys(
            ("hits", "misses", "corrupt", "expired", "unavailable", "writes",
             "oversize", "budget_bypass"), 0)

    def _count(self, name: str) -> None:
        with self._lock:
            self._counts[name] += 1

    def statistics(self) -> dict[str, Any]:
        with self._lock:
            return {**self._counts, "circuit_open": time.monotonic() < self._unavailable_until}

    def _failure(self) -> None:
        with self._lock:
            self._counts["unavailable"] += 1
            self._unavailable_until = time.monotonic() + 5.0

    def _can_attempt(self, deadline: float | None) -> bool:
        with self._lock:
            if time.monotonic() < self._unavailable_until:
                return False
        # Reserve connection/read time plus ample time for SQLite fallback.
        # A short user query budget bypasses Redis instead of risking failure.
        if deadline is not None and deadline - time.perf_counter() < self.timeout_ms / 250 + 0.01:
            self._count("budget_bypass")
            return False
        return True

    def get(self, binding: QueryCacheBinding, *, deadline: float | None = None) -> dict | None:
        if not self._can_attempt(deadline):
            return None
        try:
            # GETRANGE also bounds reception of a wrongly populated oversized key.
            raw = self._client.getrange(binding.key, 0, MAX_ENTRY_BYTES)
            if not raw:
                self._count("misses")
                return None
            if not isinstance(raw, bytes) or len(raw) > MAX_ENTRY_BYTES:
                self._count("corrupt")
                return None
            envelope = json.loads(raw)
            if (not isinstance(envelope, dict)
                    or set(envelope) != {"schema", "binding", "expires_at", "payload", "sha256"}):
                self._count("corrupt")
                return None
            checksum = envelope.pop("sha256")
            expiry = envelope["expires_at"]
            if (envelope["schema"] != CACHE_SCHEMA or envelope["binding"] != binding.identity()
                    or checksum != _digest(envelope) or not isinstance(envelope["payload"], dict)
                    or type(expiry) not in {int, float} or not math.isfinite(expiry)
                    or expiry > time.time() + self.ttl_seconds + 1):
                self._count("corrupt")
                return None
            if expiry <= time.time():
                self._count("expired")
                return None
            self._count("hits")
            return envelope["payload"]
        except (ValueError, TypeError, UnicodeError, RecursionError):
            self._count("corrupt")
            return None
        except Exception:
            # Optional infrastructure must never change a valid query's result.
            # Exception text is intentionally not logged: it can contain URLs.
            self._failure()
            return None

    def put(self, binding: QueryCacheBinding, payload: dict, *, deadline: float | None = None) -> None:
        if not self._can_attempt(deadline):
            return
        try:
            envelope = {"schema": CACHE_SCHEMA, "binding": binding.identity(),
                        "expires_at": time.time() + self.ttl_seconds, "payload": payload}
            raw = _encode({**envelope, "sha256": _digest(envelope)})
            if len(raw) > MAX_ENTRY_BYTES:
                self._count("oversize")
                return
            self._client.set(binding.key, raw, ex=self.ttl_seconds)
            self._count("writes")
        except (ValueError, TypeError, RecursionError):
            self._count("corrupt")
        except Exception:
            self._failure()

    def probe(self) -> bool:
        """PING only; doctor does not create keys or change application data."""
        try:
            ready = bool(self._client.ping())
        except Exception:
            self._failure()
            return False
        if ready:
            with self._lock:
                self._unavailable_until = 0.0
        return ready


@lru_cache(maxsize=4)
def _cache(settings: _Settings) -> QuerySummaryCache:
    import redis
    from redis.backoff import NoBackoff
    from redis.retry import Retry

    client = redis.Redis.from_url(
        settings.url, decode_responses=False,
        socket_connect_timeout=settings.timeout_ms / 1000,
        socket_timeout=settings.timeout_ms / 1000,
        retry=Retry(NoBackoff(), 0), retry_on_timeout=False,
        max_connections=8, health_check_interval=0,
    )
    return QuerySummaryCache(client, ttl_seconds=settings.ttl_seconds,
                             timeout_ms=settings.timeout_ms)


def query_summary_cache() -> QuerySummaryCache | None:
    try:
        settings = _settings()
        return _cache(settings) if settings is not None else None
    except Exception:
        return None


def redis_status(*, probe: bool = False) -> dict[str, Any]:
    """Redacted status; connectivity is only checked when explicitly requested."""
    report: dict[str, Any] = {
        "status": "not_configured", "required": False, "configured": False,
        "role": "rebuildable_query_summaries", "authority": "sqlite_and_source_manifests",
        "fallback": "sqlite", "queue_backend": "durable_local_sqlite_outbox",
        "streams_enabled": False, "conversion_receipt_cache_enabled": False,
        "connectivity_checked": False, "probe_scope": "ping_only",
        "cache_commands_observed_successfully": False,
    }
    try:
        settings = _settings()
    except Exception:
        return {**report, "status": "invalid_configuration", "configured": True}
    if settings is None:
        return report
    report.update(configured=True, ttl_seconds=settings.ttl_seconds,
                  timeout_ms=settings.timeout_ms)
    try:
        present = importlib.util.find_spec("redis") is not None
    except (ImportError, ValueError):
        present = False
    if not present:
        return {**report, "status": "dependency_missing", "remediation": "pip install 'cad2gis[redis]'"}
    cache = query_summary_cache()
    if cache is None:
        return {**report, "status": "client_unavailable"}
    report.update(status="configured_unverified", statistics=cache.statistics())
    if probe:
        report.update(status="ready" if cache.probe() else "unavailable",
                      connectivity_checked=True, statistics=cache.statistics())
    elif report["statistics"]["circuit_open"]:
        report["status"] = "unavailable"
    counts = report["statistics"]
    report["cache_commands_observed_successfully"] = bool(
        counts["writes"] and (counts["hits"] or counts["misses"]))
    return report
