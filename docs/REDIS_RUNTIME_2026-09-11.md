# Redis query cache and runtime diagnosis

This implements the optional cache slice in section 7 of
[the database architecture plan](AI_DATABASE_ARCHITECTURE_PLAN_2026-09-05.md).
It is a production call path: `agent_mcp.query_source_entities` invokes
`source_query.query_source_entities`, which now checks the optional Redis cache
after validating the source snapshot, local SQLite index and query cursor.
All clients using that source query API share the implementation.

## Scope and authority

- Only compact entity summary pages and their continuation cursor are cached.
  Full source records, geometry blobs, semantic commits and compiled deliveries
  are never stored or accepted from Redis.
- Every key binds source SHA, snapshot SHA, index SHA, index schema, query policy,
  normalized filters/projection/bounds, cursor position, page limit/byte budget,
  explicit revision domain and source-query implementation SHA.
- The revision domain is `immutable_source:no_semantic_overlay`. This API does
  not return mutable semantic decisions. Adding an overlay to this API requires
  binding its actual durable semantic revision before enabling cache reuse.
- Source/index/cursor checks execute before a cache lookup. Redis cannot hide a
  missing or modified source artifact or bypass a stale-cursor rejection.
- Each envelope has a checksum, schema/binding validation and a local expiry
  check, in addition to Redis TTL. Malformed, expired, oversized and inconsistent
  entries fall through to SQLite. Responses recalculate bytes and elapsed time.
- `GETRANGE` bounds a read to 96 KiB plus one overflow sentinel; query responses
  remain under their existing 64 KiB maximum. `SET EX` publishes value and TTL
  together. Keys contain digests, not drawing names, labels or paths.
- SHA checks detect accidental corruption. They do not authenticate a hostile
  Redis administrator. Redis access must be restricted to trusted application
  processes; no semantic commit or geometry authority is granted by a cache hit.

Redis remains optional. Missing redis-py, invalid settings, cache eviction,
connection failures and write failures retain the SQLite query path. A failed
connection opens a five-second process-local circuit to avoid repeated network
attempts. Short query time budgets skip Redis to preserve SQLite fallback time.
Connections use bounded socket/connect timeouts, no retry loop and an eight-slot
pool. DNS resolution remains subject to the host resolver; the supplied local
deployment uses a literal loopback IP.

## Local enablement

The runtime extra is `cad2gis[redis]` (`redis>=5,<8`); release lockfiles pin the
tested client. No Redis server is embedded in Windows, Linux or Android packages.
The cache is disabled until the backend process receives its URL.

```powershell
python -m pip install 'cad2gis[redis]'
docker compose -f packaging/redis.compose.yaml up -d
$env:CAD2GIS_REDIS_URL = 'redis://127.0.0.1:16379/0'
$env:CAD2GIS_REDIS_CACHE_TTL_SECONDS = '120'
$env:CAD2GIS_REDIS_TIMEOUT_MS = '100'
cad2gis doctor --deep --json
```

In Linux shells use `export CAD2GIS_REDIS_URL=redis://127.0.0.1:16379/0`.
Set these variables for the API/MCP/desktop backend process, then restart that
process. TTL accepts 1–3600 seconds, timeout accepts 1–1000 ms. URL query strings
are rejected so they cannot override client timeout or TLS policy. Remove the
URL variable to return to direct SQLite queries. Do not configure the Android
browser client with Redis credentials; it calls the ordinary backend API.

The Compose service pins the tested official Redis image digest, listens only
on `127.0.0.1:16379`, limits memory and uses `allkeys-lru`. It has no persistent
volume: losing this data is expected. This eviction-enabled instance must not
host queues. A remote installation requires separately managed access controls
and `rediss://` TLS; this work does not deploy an external Redis service.

## Runtime diagnosis and selected reliability upgrade

`doctor` now includes the optional `redis_query_cache` check, and also checks
the required `resvg_py` renderer so a missing SVG renderer cannot report a ready
conversion environment. Redis warnings do not make local conversion unavailable.

`debug_mcp(scope="runtime")` reports actual Redis connectivity using PING.
Other scopes report configuration/circuit state without initiating a probe.
The output distinguishes `not_configured`, `invalid_configuration`,
`dependency_missing`, `client_unavailable`, `configured_unverified`, `ready` and
`unavailable`. It reports process-local hits/misses/corruption/expiry/outage,
writes, oversized entries, budget bypasses and circuit state. URLs/passwords
and exception strings are omitted. `probe_scope=ping_only` means a successful
probe does not prove SET/GETRANGE ACL permissions; the separate
`cache_commands_observed_successfully` flag requires observed cache read/write
success in that process.

The reliability brainstorm considered (1) observable offline fallback,
(2) read-only database disk/lock diagnosis, and (3) explicit source-bound index
rebuild with row-count validation. This release implements (1), including the
circuit and distinction between connectivity and observed cache commands.
(2) and (3) remain follow-up candidates; no automatic VACUUM/deletion or index
rewrite was introduced.

## Queue boundary

The existing `semantic_store.py` SQLite `compile_jobs`/`outbox`, idempotency keys,
generation/CAS publication checks and reconciliation remain the durable local
compiler implementation. **Redis Streams, Pub/Sub and Redis leases are not
enabled.** No lock is presented as a solution to multi-host SQLite writes.
Conversion stage receipts retain `cacheable=false`; their reserved keys are
not used for conversion output reuse.

A future queue adapter must add durable job/outbox relay, duplicate delivery,
pending recovery, generation fencing at result commit and ACK after durable
result. It must also reconcile non-terminal jobs after Redis loses messages.
The presence of existing tables alone is not a completed distributed scheduler.

## Verification evidence

Local validation used Windows Python 3.12, redis-py 7.4.1 and Redis server
7.4.11 in a dedicated Podman/WSL container. The image digest is
`sha256:ff02b58f971e7d7d156a1267e283fcbbeee91773b6aa36c49dac28ecfe28eadf`.
The dedicated container was removed after validation; other containers/images
were not cleaned up.

Evidence directory: `E:/branch_CAD2GIS/validation/redis-20260911/`.

- `pytest.xml`: 74 passing tests for cache, source queries, doctor entrypoints
  and MCP diagnostics, including the explicit real Redis test (no skip).
- `pytest-renderer.xml`: after adding the required SVG renderer check, 34
  cache/doctor tests passed; the real Redis test was skipped in this second
  invocation because its disposable container had already been removed. Its
  real-service execution is recorded in the preceding 74-test run.
- `ready.json`, `offline.json`, `recovered.json`: public MCP source query and
  runtime diagnosis before shutdown, during actual container shutdown and after
  restart with its disposable data lost. Each returned the same 20-item digest
  `4a554e19144e15b2996ad1ad9299c435778886694fbe52544d85201ca983445a`;
  source manifests remained unchanged. Healthy/recovered passes recorded cache
  hits; offline diagnosis reported `unavailable` and queries used SQLite.
- `verify_local_redis.py`: the local replay harness used for these three phases.
- Targeted tests cover all eight key identity dimensions, explicit TTL expiry,
  damaged values and checksums, oversized reads, eviction/missing entries,
  read/write outage fallback, retry circuit, short budgets, invalid settings,
  redaction, source corruption before a warm cache lookup and MCP integration.

Measured warm query timings in the small synthetic fixture are observations,
not a production performance or real-DWG accuracy claim. Full CAD geometry and
GCP/OSM quality gates remain separate from cache correctness.

Primary references: [Redis Python connection/pooling/retry documentation](https://redis.io/docs/latest/develop/clients/redis-py/connect/),
[SET with expiration](https://redis.io/docs/latest/commands/set/),
[GETRANGE](https://redis.io/docs/latest/commands/getrange/).
