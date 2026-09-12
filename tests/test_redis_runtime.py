from __future__ import annotations

from dataclasses import replace
import json
import os
import time

import pytest

from cad2gis import redis_runtime
from cad2gis.redis_runtime import QueryCacheBinding, QuerySummaryCache


class MemoryRedis:
    def __init__(self):
        self.values = {}
        self.expiries = {}
        self.reads = []
        self.fail_reads = False
        self.fail_writes = False

    def getrange(self, key, first, last):
        self.reads.append((key, first, last))
        if self.fail_reads:
            raise ConnectionError("private redis://password@server")
        return self.values.get(key, b"")[first:last + 1]

    def set(self, key, value, *, ex):
        if self.fail_writes:
            raise ConnectionError("private Redis write error")
        self.values[key] = value
        self.expiries[key] = ex
        return True

    def ping(self):
        if self.fail_reads:
            raise ConnectionError("private Redis URL")
        return True


@pytest.fixture
def binding():
    return QueryCacheBinding(source="source", snapshot="snapshot", index="index", schema="schema",
                             policy="policy", filters="filters", revision="revision",
                             implementation="implementation")


@pytest.fixture
def cache(monkeypatch):
    client = MemoryRedis()
    cache = QuerySummaryCache(client, ttl_seconds=120, timeout_ms=10)
    monkeypatch.setenv(redis_runtime.URL_ENV, "redis://127.0.0.1:16379/0")
    monkeypatch.setattr(redis_runtime, "_cache", lambda settings: cache)
    return cache, client


def test_cache_disabled_without_url_does_not_import_or_connect(monkeypatch):
    monkeypatch.delenv(redis_runtime.URL_ENV, raising=False)
    monkeypatch.setattr(redis_runtime, "_cache", lambda settings: pytest.fail("created Redis client"))
    assert redis_runtime.query_summary_cache() is None
    report = redis_runtime.redis_status(probe=True)
    assert report["status"] == "not_configured" and report["connectivity_checked"] is False
    assert report["streams_enabled"] is report["conversion_receipt_cache_enabled"] is False


@pytest.mark.parametrize("field", list(QueryCacheBinding.__dataclass_fields__))
def test_every_authority_and_query_dimension_isolated(cache, binding, field):
    adapter, client = cache
    adapter.put(binding, {"items": [{"entity_key": "A"}], "next_cursor": None})
    changed = replace(binding, **{field: getattr(binding, field) + "-new"})
    assert adapter.get(changed) is None
    assert adapter.get(binding)["items"] == [{"entity_key": "A"}]
    assert client.expiries[binding.key] == 120
    assert "source" not in binding.key and "filters" not in binding.key


@pytest.mark.parametrize("damage", ["json", "checksum", "binding", "oversize", "expired", "shape"])
def test_corrupt_or_expired_entry_is_a_miss(cache, binding, damage, monkeypatch):
    adapter, client = cache
    adapter.put(binding, {"items": [], "next_cursor": None})
    raw = client.values[binding.key]
    if damage == "json":
        client.values[binding.key] = b"not-json"
    elif damage == "oversize":
        client.values[binding.key] = b"x" * (redis_runtime.MAX_ENTRY_BYTES * 2)
    elif damage == "shape":
        client.values[binding.key] = b"[]"
    elif damage == "expired":
        now = time.time()
        monkeypatch.setattr(redis_runtime.time, "time", lambda: now + 121)
    else:
        envelope = json.loads(raw)
        if damage == "checksum":
            envelope["payload"]["items"] = [{"entity_key": "changed"}]
        else:
            envelope["binding"]["revision"] = "other-revision"
            core = {key: value for key, value in envelope.items() if key != "sha256"}
            envelope["sha256"] = redis_runtime._digest(core)
        client.values[binding.key] = json.dumps(envelope).encode()
    assert adapter.get(binding) is None
    assert client.reads[-1][1:] == (0, redis_runtime.MAX_ENTRY_BYTES)


def test_outage_circuit_limits_retries_and_probe_recovers(cache, binding):
    adapter, client = cache
    client.fail_reads = True
    assert adapter.get(binding) is None
    assert adapter.get(binding) is None
    adapter.put(binding, {"items": []})
    assert len(client.reads) == 1 and client.values == {}
    assert adapter.statistics()["unavailable"] == 1
    client.fail_reads = False
    assert adapter.probe() is True
    assert not adapter.statistics()["circuit_open"]
    adapter.put(binding, {"items": []})
    assert adapter.get(binding) == {"items": []}


def test_short_budget_does_not_spend_time_on_optional_network(cache, binding):
    adapter, client = cache
    assert adapter.get(binding, deadline=time.perf_counter() + 0.001) is None
    adapter.put(binding, {"items": []}, deadline=time.perf_counter() + 0.001)
    assert client.reads == [] and client.values == {}
    assert adapter.statistics()["budget_bypass"] == 2


@pytest.mark.parametrize("setting,value", [
    (redis_runtime.URL_ENV, "redis://user:private-password@host/0?socket_timeout=99999"),
    (redis_runtime.URL_ENV, "https://user:private-password@host"),
    (redis_runtime.URL_ENV, "redis://host:99999/0"),
    (redis_runtime.TTL_ENV, "0"),
    (redis_runtime.TIMEOUT_ENV, "NaN"),
])
def test_invalid_configuration_is_redacted_and_falls_back(monkeypatch, setting, value):
    monkeypatch.setenv(redis_runtime.URL_ENV, "redis://user:private-password@host/0")
    monkeypatch.setenv(setting, value)
    assert redis_runtime.query_summary_cache() is None
    report = redis_runtime.redis_status(probe=True)
    assert report["status"] == "invalid_configuration"
    assert report["fallback"] == "sqlite"
    assert "private-password" not in json.dumps(report)


def test_diagnostics_report_actual_outage_and_never_write_keys(cache, monkeypatch):
    from cad2gis import mcp_diagnostics

    adapter, client = cache
    find_spec = redis_runtime.importlib.util.find_spec
    monkeypatch.setattr(redis_runtime.importlib.util, "find_spec",
                        lambda name: object() if name == "redis" else find_spec(name))
    before = dict(client.values)
    assert redis_runtime.redis_status()["status"] == "configured_unverified"
    assert redis_runtime.redis_status(probe=True)["status"] == "ready"
    client.fail_reads = True
    report = redis_runtime.redis_status(probe=True)
    assert report["status"] == "unavailable" and report["connectivity_checked"]
    assert "private" not in json.dumps(report)
    assert client.values == before
    # Identity diagnostics are read-only and don't turn a stale config into a
    # connectivity success claim; runtime scope alone explicitly probes.
    result = mcp_diagnostics.diagnose(scope="identity", tool_schemas=[])
    assert result["redis"]["status"] == "unavailable"
    assert result["redis"]["streams_enabled"] is False


def _source_snapshot(tmp_path):
    from cad2gis.cad2gis_v3.source_export import export_source

    source = tmp_path / "source.dwg"
    source.write_bytes(b"redis-source-query-fixture")
    root = tmp_path / "snapshot"
    records = [{"entity_key": key, "handle": key, "dwg_type_name": "LINE", "layout": "Model",
                "cad_role": "model", "layer": "routes", "points": [[0.0, 0.0], [1.0, 2.0]],
                "text": "NP7-" + key} for key in ("A", "B", "C")]
    export_source(source=source, run_dir=root, records=records)
    return root


def test_source_query_hits_skip_entity_sql_but_validate_snapshot_and_cursor(tmp_path, cache, monkeypatch):
    from cad2gis.cad2gis_v3 import source_query

    adapter, client = cache
    root = _source_snapshot(tmp_path)
    statements = []
    original = source_query._readonly

    def traced(path):
        db = original(path)
        db.set_trace_callback(statements.append)
        return db

    monkeypatch.setattr(source_query, "_readonly", traced)
    first = source_query.query_source_entities(run_dir=root, limit=1)
    statements.clear()
    second = source_query.query_source_entities(run_dir=root, limit=1)
    assert second["items"] == first["items"]
    assert second["next_cursor"] == first["next_cursor"]
    assert not any("FROM entities e WHERE" in sql for sql in statements)
    assert adapter.statistics()["hits"] == 1
    following = source_query.query_source_entities(run_dir=root, cursor=first["next_cursor"], limit=1)
    assert following["items"][0]["entity_key"] != first["items"][0]["entity_key"]
    with pytest.raises(source_query.SourceQueryError, match="cursor"):
        source_query.query_source_entities(run_dir=root, layer="other", cursor=first["next_cursor"])
    # An existing warm Redis entry cannot hide a corrupted local source artifact.
    (root / "plan_entities.jsonl").write_text("corrupt", encoding="utf-8")
    with pytest.raises(source_query.SourceQueryError, match="hash/path"):
        source_query.query_source_entities(run_dir=root, limit=1)


@pytest.mark.parametrize("failure", ["read", "write", "corrupt"])
def test_source_query_falls_back_to_sqlite_on_cache_failure(tmp_path, cache, failure):
    from cad2gis.cad2gis_v3 import source_query

    adapter, client = cache
    root = _source_snapshot(tmp_path)
    reference = source_query.query_source_entities(run_dir=root)
    if failure == "read":
        client.fail_reads = True
    elif failure == "write":
        client.values.clear()
        client.fail_writes = True
    else:
        for key in client.values:
            client.values[key] = b"corrupt"
    result = source_query.query_source_entities(run_dir=root)
    assert result["items"] == reference["items"]
    assert result["metadata"] == reference["metadata"]
    assert result["returned_count"] == 3


def test_actual_mcp_query_uses_optional_cache(tmp_path, cache, monkeypatch):
    from cad2gis import agent_mcp

    adapter, client = cache
    root = _source_snapshot(tmp_path)
    monkeypatch.setenv("CAD2GIS_PROJECT_ROOTS", str(tmp_path))
    monkeypatch.delenv("CAD2GIS_PROJECT_ROOT", raising=False)
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    assert agent_mcp._roots() == (tmp_path.resolve(),)
    # The public MCP API invokes the same source query path as desktop/CLI.
    first = agent_mcp.query_source_entities(run_dir=str(root))
    second = agent_mcp.query_source_entities(run_dir=str(root))
    assert second["items"] == first["items"]
    assert adapter.statistics()["hits"] == 1


@pytest.mark.skipif(not os.environ.get("CAD2GIS_TEST_REDIS_URL"), reason="explicit disposable local Redis not supplied")
def test_real_redis_roundtrip_ttl_corruption_and_clear(tmp_path, monkeypatch):
    from cad2gis.cad2gis_v3 import source_query

    monkeypatch.setenv(redis_runtime.URL_ENV, os.environ["CAD2GIS_TEST_REDIS_URL"])
    monkeypatch.setenv(redis_runtime.TTL_ENV, "60")
    root = _source_snapshot(tmp_path)
    adapter = redis_runtime.query_summary_cache()
    assert adapter is not None and adapter.probe()
    original = source_query.query_source_entities(run_dir=root)
    repeated = source_query.query_source_entities(run_dir=root)
    assert repeated["items"] == original["items"]
    assert adapter.statistics()["hits"] >= 1
    # Restrict cleanup to exact keys containing this newly exported fixture's
    # source snapshot binding. Never FLUSH a configured Redis database.
    keys = []
    for key in adapter._client.scan_iter(match="cad2gis:query-summary:v1:*"):
        raw = adapter._client.get(key)
        try:
            envelope = json.loads(raw)
            if envelope["binding"]["snapshot"] == original["metadata"]["snapshot_sha256"]:
                keys.append(key)
        except (ValueError, KeyError, TypeError):
            pass
    assert keys and all(0 < adapter._client.ttl(key) <= 60 for key in keys)
    try:
        for key in keys:
            adapter._client.set(key, b"broken", ex=60)
        assert source_query.query_source_entities(run_dir=root)["items"] == original["items"]
        for key in keys:
            adapter._client.delete(key)
        assert source_query.query_source_entities(run_dir=root)["items"] == original["items"]
    finally:
        for key in keys:
            adapter._client.delete(key)
