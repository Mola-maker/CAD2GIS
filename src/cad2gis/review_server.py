"""Real-time web review surface for immutable CAD2GIS run artifacts.

The review workspace is deliberately separate from the conversion run.  Edits
are optimistic, revisioned GeoJSON overlays and never rewrite source,
evidence, or delivery GeoPackages.
"""

from contextlib import contextmanager
import hashlib
import json
import math
import os
import re
import shlex
import sqlite3
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

from .cad2gis_v3.artifact_io import read_json_object


REVIEW_SCHEMA = "cad2gis.review_workspace.v1"
_FEATURE_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,256}$")
_PUBLIC_DEMO_CONTENT = re.compile(
    r"<!-- public-demo-only:start -->.*?<!-- public-demo-only:end -->", re.DOTALL,
)


class ReviewServerError(ValueError):
    """Review configuration or an edit payload violates the contract."""


class ReviewConflictError(ReviewServerError):
    """Optimistic revision does not match the current feature revision."""


def _application_review_html(document: str) -> str:
    """Render the shared map console without public demo content, before paint.

    A hash jump or a later JavaScript hide still exposes the historical landing
    page while scripts load. Explicit template boundaries keep the installed
    workspace and public demo on one console without sending demo fixtures to a
    selected real result.
    """
    if "<!-- public-demo-only:start -->" not in document:
        raise ReviewServerError("Review workspace template boundaries are missing")
    document = _PUBLIC_DEMO_CONTENT.sub("", document)
    if 'id="hero-page"' in document or "<!-- public-demo-only:" in document:
        raise ReviewServerError("Review workspace template boundaries are invalid")
    return document.replace("<body>", '<body class="live-review">', 1).replace(
        "</head>",
        '<link id="cad2gis-review-theme" rel="stylesheet" '
        'href="/_app/assets/review-theme.css">\n</head>',
        1,
    )


def _json_object(path: Path) -> dict[str, Any]:
    try:
        return read_json_object(path)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise ReviewServerError(str(exc)) from exc


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _finite_coordinates(value: Any, path: str = "geometry.coordinates") -> None:
    if not isinstance(value, (list, tuple)):
        raise ReviewServerError(f"{path} must be an array")
    if value and all(not isinstance(item, (list, tuple)) for item in value):
        if len(value) < 2:
            raise ReviewServerError(f"{path} coordinate requires longitude and latitude")
        if any(isinstance(item, bool) or not isinstance(item, (int, float)) for item in value):
            raise ReviewServerError(f"{path} contains a non-numeric coordinate")
        try:
            numbers = [float(item) for item in value]
        except (OverflowError, ValueError) as exc:
            raise ReviewServerError(f"{path} contains a non-finite coordinate") from exc
        lon, lat = numbers[:2]
        if not all(math.isfinite(item) for item in numbers):
            raise ReviewServerError(f"{path} contains a non-finite coordinate")
        if not -180.0 <= lon <= 180.0 or not -90.0 <= lat <= 90.0:
            raise ReviewServerError(f"{path} must be EPSG:4326 longitude/latitude")
        return
    if not value:
        raise ReviewServerError(f"{path} must not be empty")
    for index, item in enumerate(value):
        _finite_coordinates(item, f"{path}[{index}]")


def _normalized_feature(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("type") != "Feature":
        raise ReviewServerError("Review payload must be a GeoJSON Feature")
    feature_id = str(value.get("id", "")).strip()
    if not _FEATURE_ID.fullmatch(feature_id):
        raise ReviewServerError(
            "Feature id must use 1-256 letters, digits, dot, colon, underscore, or hyphen"
        )
    geometry = value.get("geometry")
    if not isinstance(geometry, dict):
        raise ReviewServerError("Feature geometry must be an object")
    geometry_type = str(geometry.get("type", ""))
    if geometry_type not in {
        "Point", "MultiPoint", "LineString", "MultiLineString",
        "Polygon", "MultiPolygon",
    }:
        raise ReviewServerError(f"Unsupported review geometry: {geometry_type!r}")
    _finite_coordinates(geometry.get("coordinates"))
    properties = value.get("properties", {})
    if not isinstance(properties, dict):
        raise ReviewServerError("Feature properties must be an object")
    try:
        encoded = json.dumps(properties, ensure_ascii=False, allow_nan=False)
    except (ValueError, TypeError, OverflowError) as exc:
        raise ReviewServerError("Feature properties must be finite JSON values") from exc
    if len(encoded.encode("utf-8")) > 256 * 1024:
        raise ReviewServerError("Feature properties exceed 256 KiB")
    return {
        "type": "Feature",
        "id": feature_id,
        "geometry": geometry,
        "properties": properties,
    }


def _expected_revision(value: Any, *, required: bool = False) -> None:
    if value is None and not required:
        return
    if type(value) is not int or value < 0:
        raise ReviewServerError("expected_revision must be a non-negative integer; use 0 for a new GCP")


async def _request_object(request: Any) -> dict[str, Any]:
    try:
        payload = await request.json()
    except (ValueError, UnicodeError) as exc:
        raise ReviewServerError("Request body must be valid JSON") from exc
    if not isinstance(payload, dict):
        raise ReviewServerError("Request body must be an object")
    return payload


def _review_update(
    feature: dict[str, Any], previous: dict[str, Any] | None,
    expected_revision: int | None, gcp_action: Mapping[str, Any] | None,
    initial_gcp_state: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Enforce GCP invariants against the row locked by either store."""
    is_gcp = feature["properties"].get("_kind") == "cad_map_gcp"
    was_gcp = previous is not None and (previous.get("properties") or {}).get("_kind") == "cad_map_gcp"
    _expected_revision(expected_revision, required=is_gcp or was_gcp or gcp_action is not None)
    if was_gcp and not is_gcp:
        raise ReviewServerError("An existing GCP must retain its kind and immutable CAD pick")
    if is_gcp or gcp_action is not None:
        from .gcp_workflow import edit_web_control

        try:
            if initial_gcp_state is not None:
                if previous is not None or gcp_action is not None:
                    raise ValueError("Saved GCP editor state can only initialize a new control")
                # Only the bound matching-record importer passes this keyword;
                # public feature writes cannot supply or replace stored history.
                previous = {**feature, "properties": {
                    **feature["properties"], "_gcp_edit": dict(initial_gcp_state),
                }}
            feature = edit_web_control(feature, previous, action=gcp_action)
        except (ValueError, TypeError, KeyError) as exc:
            raise ReviewServerError(str(exc)) from exc
    return {**feature, "properties": {key: value for key, value in feature["properties"].items()
                                      if key not in {"_review_revision", "_review_updated_at"}}}


class SQLiteReviewStore:
    """Revisioned local review overlay suitable for the bundled demo."""

    def __init__(self, path: str | Path, *, session_id: str):
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.session_id = str(session_id)
        self._lock = threading.RLock()
        self._initialize()

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=30.0)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA foreign_keys=ON")
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS review_features (
                    session_id TEXT NOT NULL,
                    feature_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    feature_json TEXT NOT NULL,
                    deleted INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (session_id, feature_id)
                );
                CREATE TABLE IF NOT EXISTS review_events (
                    event_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    feature_id TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_review_events_session
                ON review_events(session_id, created_at, event_id);
                CREATE INDEX IF NOT EXISTS idx_review_events_session_sequence
                ON review_events(session_id);
                """
            )

    def feature_collection(self) -> dict[str, Any]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT feature_json, revision, updated_at
                FROM review_features
                WHERE session_id = ? AND deleted = 0
                ORDER BY feature_id
                """,
                (self.session_id,),
            ).fetchall()
        features = []
        for row in rows:
            feature = json.loads(row["feature_json"])
            feature["properties"] = {
                **dict(feature.get("properties") or {}),
                "_review_revision": int(row["revision"]),
                "_review_updated_at": row["updated_at"],
            }
            features.append(feature)
        return {
            "type": "FeatureCollection",
            "features": features,
            "schema_version": REVIEW_SCHEMA,
            "session_id": self.session_id,
        }

    def events(self, *, after: str = "", limit: int = 200) -> list[dict[str, Any]]:
        if not 1 <= limit <= 1000:
            raise ReviewServerError("event limit must be between 1 and 1000")
        query = """
            SELECT event_id, feature_id, operation, revision, payload_json, created_at
            FROM review_events
            WHERE session_id = ?
        """
        parameters: list[Any] = [self.session_id]
        if after:
            query += """
                AND rowid > (
                    SELECT rowid
                    FROM review_events
                    WHERE session_id = ? AND event_id = ?
                )
            """
            parameters.extend((self.session_id, after))
        query += " ORDER BY rowid LIMIT ?"
        parameters.append(limit)
        with self._connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [{
            "event_id": row["event_id"],
            "feature_id": row["feature_id"],
            "operation": row["operation"],
            "revision": int(row["revision"]),
            "payload": json.loads(row["payload_json"]),
            "created_at": row["created_at"],
        } for row in rows]

    def upsert(
        self,
        feature: Any,
        *,
        expected_revision: int | None,
        actor: str,
        gcp_action: Mapping[str, Any] | None = None,
        initial_gcp_state: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized = _normalized_feature(feature)
        feature_id = normalized["id"]
        now = _utc_now()
        event_id = uuid.uuid4().hex
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                """
                SELECT revision, feature_json, deleted FROM review_features
                WHERE session_id = ? AND feature_id = ?
                """,
                (self.session_id, feature_id),
            ).fetchone()
            current_revision = 0 if current is None else int(current["revision"])
            _expected_revision(expected_revision)
            if expected_revision is not None and expected_revision != current_revision:
                raise ReviewConflictError(
                    f"Feature {feature_id} revision is {current_revision}, "
                    f"not {expected_revision}"
                )
            if gcp_action is not None and (current is None or current["deleted"]):
                raise ReviewConflictError("The GCP was deleted; reload the control points")
            normalized = _review_update(
                normalized, json.loads(current["feature_json"]) if current is not None else None,
                expected_revision, gcp_action, initial_gcp_state,
            )
            revision = current_revision + 1
            properties = {
                **normalized["properties"],
                "_review_actor": str(actor or "anonymous"),
            }
            stored = {**normalized, "properties": properties}
            payload_json = json.dumps(
                stored, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                allow_nan=False,
            )
            connection.execute(
                """
                INSERT INTO review_features(
                    session_id, feature_id, revision, feature_json, deleted, updated_at
                ) VALUES (?, ?, ?, ?, 0, ?)
                ON CONFLICT(session_id, feature_id) DO UPDATE SET
                    revision = excluded.revision,
                    feature_json = excluded.feature_json,
                    deleted = 0,
                    updated_at = excluded.updated_at
                """,
                (self.session_id, feature_id, revision, payload_json, now),
            )
            event = {
                "event_id": event_id,
                "feature_id": feature_id,
                "operation": "upsert",
                "revision": revision,
                "payload": stored,
                "created_at": now,
            }
            connection.execute(
                """
                INSERT INTO review_events(
                    event_id, session_id, feature_id, operation, revision,
                    payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id, self.session_id, feature_id, "upsert", revision,
                    json.dumps(stored, ensure_ascii=False, sort_keys=True),
                    now,
                ),
            )
            connection.commit()
        return event

    def delete(
        self,
        feature_id: str,
        *,
        expected_revision: int | None,
        actor: str,
    ) -> dict[str, Any]:
        if not _FEATURE_ID.fullmatch(feature_id):
            raise ReviewServerError("Invalid feature id")
        now = _utc_now()
        event_id = uuid.uuid4().hex
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                """
                SELECT revision, feature_json FROM review_features
                WHERE session_id = ? AND feature_id = ?
                """,
                (self.session_id, feature_id),
            ).fetchone()
            if current is None:
                raise ReviewServerError(f"Unknown review feature: {feature_id}")
            _expected_revision(expected_revision, required=(
                json.loads(current["feature_json"]).get("properties", {}).get("_kind") == "cad_map_gcp"
            ))
            current_revision = int(current["revision"])
            if expected_revision is not None and expected_revision != current_revision:
                raise ReviewConflictError(
                    f"Feature {feature_id} revision is {current_revision}, "
                    f"not {expected_revision}"
                )
            revision = current_revision + 1
            connection.execute(
                """
                UPDATE review_features
                SET revision = ?, deleted = 1, updated_at = ?
                WHERE session_id = ? AND feature_id = ?
                """,
                (revision, now, self.session_id, feature_id),
            )
            payload = {"actor": str(actor or "anonymous")}
            connection.execute(
                """
                INSERT INTO review_events(
                    event_id, session_id, feature_id, operation, revision,
                    payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id, self.session_id, feature_id, "delete", revision,
                    json.dumps(payload, ensure_ascii=False), now,
                ),
            )
            connection.commit()
        return {
            "event_id": event_id,
            "feature_id": feature_id,
            "operation": "delete",
            "revision": revision,
            "payload": payload,
            "created_at": now,
        }


class PostGISReviewStore:
    """Production review overlay with the same optimistic event contract."""

    def __init__(self, dsn: str, *, session_id: str):
        if not str(dsn).strip():
            raise ReviewServerError("PostGIS DSN must not be empty")
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:  # pragma: no cover - optional deployment
            raise ReviewServerError(
                "PostGIS review requires the cad2gis[review-postgis] extra"
            ) from exc
        self._psycopg = psycopg
        self._dict_row = dict_row
        self.dsn = str(dsn)
        self.session_id = str(session_id)
        self._initialize()

    def _connect(self):
        return self._psycopg.connect(self.dsn, row_factory=self._dict_row)

    def _initialize(self) -> None:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT to_regtype('geometry') AS geometry_type")
            if cursor.fetchone()["geometry_type"] is None:
                raise ReviewServerError(
                    "PostGIS extension is not installed in the configured database"
                )
            cursor.execute("CREATE SCHEMA IF NOT EXISTS cad2gis_review")
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS cad2gis_review.features (
                    session_id TEXT NOT NULL,
                    feature_id TEXT NOT NULL,
                    revision BIGINT NOT NULL,
                    geometry geometry(Geometry, 4326) NOT NULL,
                    properties JSONB NOT NULL,
                    deleted BOOLEAN NOT NULL DEFAULT FALSE,
                    updated_at TIMESTAMPTZ NOT NULL,
                    PRIMARY KEY (session_id, feature_id)
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS cad2gis_review.events (
                    event_id UUID PRIMARY KEY,
                    event_seq BIGSERIAL UNIQUE,
                    session_id TEXT NOT NULL,
                    feature_id TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    revision BIGINT NOT NULL,
                    payload JSONB NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL
                )
                """
            )
            cursor.execute(
                """
                ALTER TABLE cad2gis_review.events
                ADD COLUMN IF NOT EXISTS event_seq BIGSERIAL
                """
            )
            cursor.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS events_event_seq_idx
                ON cad2gis_review.events(event_seq)
                """
            )
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS events_session_sequence_idx
                ON cad2gis_review.events(session_id, event_seq)
                """
            )

    def feature_collection(self) -> dict[str, Any]:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT feature_id, revision, ST_AsGeoJSON(geometry, 15)::jsonb AS geometry,
                       properties, updated_at
                FROM cad2gis_review.features
                WHERE session_id = %s AND deleted = FALSE
                ORDER BY feature_id
                """,
                (self.session_id,),
            )
            rows = cursor.fetchall()
        return {
            "type": "FeatureCollection",
            "features": [{
                "type": "Feature",
                "id": row["feature_id"],
                "geometry": row["geometry"],
                "properties": {
                    **dict(row["properties"]),
                    "_review_revision": int(row["revision"]),
                    "_review_updated_at": row["updated_at"].isoformat(),
                },
            } for row in rows],
            "schema_version": REVIEW_SCHEMA,
            "session_id": self.session_id,
        }

    def events(self, *, after: str = "", limit: int = 200) -> list[dict[str, Any]]:
        if not 1 <= limit <= 1000:
            raise ReviewServerError("event limit must be between 1 and 1000")
        query = """
            SELECT event_id, feature_id, operation, revision, payload, created_at
            FROM cad2gis_review.events
            WHERE session_id = %s
        """
        parameters: list[Any] = [self.session_id]
        if after:
            query += """
                AND event_seq > (
                    SELECT event_seq
                    FROM cad2gis_review.events
                    WHERE session_id = %s AND event_id = %s::uuid
                )
            """
            parameters.extend((self.session_id, after))
        query += " ORDER BY event_seq LIMIT %s"
        parameters.append(limit)
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(query, parameters)
            rows = cursor.fetchall()
        return [{
            "event_id": str(row["event_id"]),
            "feature_id": row["feature_id"],
            "operation": row["operation"],
            "revision": int(row["revision"]),
            "payload": row["payload"],
            "created_at": row["created_at"].isoformat(),
        } for row in rows]

    def upsert(
        self,
        feature: Any,
        *,
        expected_revision: int | None,
        actor: str,
        gcp_action: Mapping[str, Any] | None = None,
        initial_gcp_state: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized = _normalized_feature(feature)
        feature_id = normalized["id"]
        event_id = uuid.uuid4()
        with self._connect() as connection, connection.cursor() as cursor:
            # FOR UPDATE cannot lock a row that does not exist yet. Serialize
            # creates too, so two clients cannot both create revision 1.
            cursor.execute("SELECT pg_advisory_xact_lock(hashtext(%s), hashtext(%s))",
                           (self.session_id, feature_id))
            cursor.execute(
                """
                SELECT revision, ST_AsGeoJSON(geometry, 15)::jsonb AS geometry, properties, deleted
                FROM cad2gis_review.features
                WHERE session_id = %s AND feature_id = %s
                FOR UPDATE
                """,
                (self.session_id, feature_id),
            )
            current = cursor.fetchone()
            current_revision = 0 if current is None else int(current["revision"])
            _expected_revision(expected_revision)
            if expected_revision is not None and expected_revision != current_revision:
                raise ReviewConflictError(
                    f"Feature {feature_id} revision is {current_revision}, "
                    f"not {expected_revision}"
                )
            if gcp_action is not None and (current is None or current["deleted"]):
                raise ReviewConflictError("The GCP was deleted; reload the control points")
            previous = ({"type": "Feature", "id": feature_id,
                         "geometry": current["geometry"], "properties": current["properties"]}
                        if current is not None else None)
            normalized = _review_update(normalized, previous, expected_revision, gcp_action, initial_gcp_state)
            revision = current_revision + 1
            properties = {**normalized["properties"], "_review_actor": str(actor or "anonymous")}
            stored = {**normalized, "properties": properties}
            cursor.execute(
                """
                INSERT INTO cad2gis_review.features(
                    session_id, feature_id, revision, geometry, properties,
                    deleted, updated_at
                ) VALUES (
                    %s, %s, %s,
                    ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326),
                    %s::jsonb, FALSE, NOW()
                )
                ON CONFLICT(session_id, feature_id) DO UPDATE SET
                    revision = EXCLUDED.revision,
                    geometry = EXCLUDED.geometry,
                    properties = EXCLUDED.properties,
                    deleted = FALSE,
                    updated_at = EXCLUDED.updated_at
                RETURNING updated_at
                """,
                (
                    self.session_id, feature_id, revision,
                    json.dumps(normalized["geometry"], ensure_ascii=False),
                    json.dumps(properties, ensure_ascii=False),
                ),
            )
            created_at = cursor.fetchone()["updated_at"]
            cursor.execute(
                """
                INSERT INTO cad2gis_review.events(
                    event_id, session_id, feature_id, operation, revision,
                    payload, created_at
                ) VALUES (%s, %s, %s, 'upsert', %s, %s::jsonb, %s)
                """,
                (
                    event_id, self.session_id, feature_id, revision,
                    json.dumps(stored, ensure_ascii=False), created_at,
                ),
            )
        return {
            "event_id": str(event_id),
            "feature_id": feature_id,
            "operation": "upsert",
            "revision": revision,
            "payload": stored,
            "created_at": created_at.isoformat(),
        }

    def delete(
        self,
        feature_id: str,
        *,
        expected_revision: int | None,
        actor: str,
    ) -> dict[str, Any]:
        if not _FEATURE_ID.fullmatch(feature_id):
            raise ReviewServerError("Invalid feature id")
        event_id = uuid.uuid4()
        payload = {"actor": str(actor or "anonymous")}
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(hashtext(%s), hashtext(%s))",
                           (self.session_id, feature_id))
            cursor.execute(
                """
                SELECT revision, properties FROM cad2gis_review.features
                WHERE session_id = %s AND feature_id = %s
                FOR UPDATE
                """,
                (self.session_id, feature_id),
            )
            current = cursor.fetchone()
            if current is None:
                raise ReviewServerError(f"Unknown review feature: {feature_id}")
            _expected_revision(expected_revision, required=(
                (current["properties"] or {}).get("_kind") == "cad_map_gcp"
            ))
            current_revision = int(current["revision"])
            if expected_revision is not None and expected_revision != current_revision:
                raise ReviewConflictError(
                    f"Feature {feature_id} revision is {current_revision}, "
                    f"not {expected_revision}"
                )
            revision = current_revision + 1
            cursor.execute(
                """
                UPDATE cad2gis_review.features
                SET revision = %s, deleted = TRUE, updated_at = NOW()
                WHERE session_id = %s AND feature_id = %s
                RETURNING updated_at
                """,
                (revision, self.session_id, feature_id),
            )
            created_at = cursor.fetchone()["updated_at"]
            cursor.execute(
                """
                INSERT INTO cad2gis_review.events(
                    event_id, session_id, feature_id, operation, revision,
                    payload, created_at
                ) VALUES (%s, %s, %s, 'delete', %s, %s::jsonb, %s)
                """,
                (
                    event_id, self.session_id, feature_id, revision,
                    json.dumps(payload), created_at,
                ),
            )
        return {
            "event_id": str(event_id),
            "feature_id": feature_id,
            "operation": "delete",
            "revision": revision,
            "payload": payload,
            "created_at": created_at.isoformat(),
        }


class GeoPackageProvider:
    """Read-only GeoJSON projection over the immutable delivery GeoPackage."""

    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser().resolve()
        if not self.path.is_file():
            raise ReviewServerError(f"Delivery GeoPackage does not exist: {self.path}")

    def _dataset(self):
        try:
            from osgeo import ogr
        except ImportError as exc:  # pragma: no cover - runtime preflight
            raise ReviewServerError("GDAL/OGR is required by the review server") from exc
        dataset = ogr.Open(str(self.path), 0)
        if dataset is None:
            raise ReviewServerError(f"Cannot open delivery GeoPackage: {self.path}")
        return dataset

    def _evidence_path(self) -> Path | None:
        manifest_path = self.path.parent / "run_manifest.json"
        if not manifest_path.is_file():
            # Second-level delivery partitions share the parent run evidence.
            if (self.path.parent / "partition_manifest.json").is_file():
                manifest_path = self.path.parent.parent / "run_manifest.json"
        if not manifest_path.is_file():
            return None
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        artifacts = manifest.get("artifacts")
        if not isinstance(artifacts, dict):
            return None
        evidence = artifacts.get("evidence")
        if not isinstance(evidence, dict):
            return None
        return _artifact_path(
            manifest,
            "evidence",
            run_dir=manifest_path.parent,
        )

    def _native_point_lookup(self) -> dict[str, Any] | None:
        """Read feature geometry and segment materialization from the ledger."""
        evidence = self._evidence_path()
        if evidence is None or not evidence.is_file():
            return None
        try:
            connection = sqlite3.connect(evidence.as_uri() + "?mode=ro", uri=True)
            connection.row_factory = sqlite3.Row
            try:
                cursor = connection.cursor()
                columns = {
                    row[1]
                    for row in cursor.execute(
                        "PRAGMA table_info(cad_entities)"
                    ).fetchall()
                }
                if "entity_key" not in columns or "native_points" not in columns:
                    return None
                lookup: dict[str, Any] = {"entities": {}, "features": {}, "by_source": {}}
                for row in cursor.execute("SELECT * FROM cad_entities"):
                    if not row["native_points"]:
                        continue
                    facts = json.loads(row["curve_facts"] or "{}") if "curve_facts" in columns else {}
                    lookup["entities"][row["entity_key"]] = {
                        "points": json.loads(row["native_points"]),
                        "curve_facts": facts,
                    }
                feature_columns = {
                    row[1] for row in connection.execute("PRAGMA table_info(feature_candidates)")
                }
                lineage = {}
                lineage_columns = {
                    row[1] for row in connection.execute("PRAGMA table_info(georef_feature_lineage)")
                }
                if {"feature_key", "native_points_json"}.issubset(lineage_columns):
                    lineage = dict(connection.execute(
                        "SELECT feature_key, native_points_json FROM georef_feature_lineage"
                    ))
                if {"feature_key", "feature_class", "source_entity_key", "attributes"}.issubset(feature_columns):
                    for row in connection.execute("SELECT * FROM feature_candidates"):
                        attributes = json.loads(row["attributes"] or "{}")
                        materialization = attributes.get("curve_materialization") or {}
                        points = materialization.get("delivery_native_points")
                        canonical = points is not None
                        if points is None:
                            raw = (row["native_points"] if "native_points" in feature_columns else None)
                            canonical = bool(raw)
                            raw = raw or lineage.get(row["feature_key"])
                            points = json.loads(raw) if raw else None
                            # Non-cable delivery uses feature.native_points
                            # directly; only cables substitute materialized
                            # curves for their immutable control vertices.
                            canonical = canonical or (row["feature_class"] != "CABLE" and points is not None)
                        lookup["features"][row["feature_key"]] = {
                            "points": points,
                            "canonical": canonical,
                            "segments": materialization.get("source_segments"),
                        }
                        key = (row["feature_class"], row["source_entity_key"])
                        lookup["by_source"].setdefault(key, []).append(row["feature_key"])
                return lookup
            finally:
                connection.close()
        except (sqlite3.Error, ValueError, TypeError) as exc:
            raise ReviewServerError(f"Cannot read native geometry evidence: {exc}") from exc

    def _geometry_from_native_points(
        self,
        native_lookup: dict[str, Any] | None,
        properties: dict[str, Any],
        delivery_geometry: Any,
        layer_name: str = "",
    ) -> Any | None:
        """Recover the exact feature/segment in CAD WCS, including curves."""
        if not native_lookup:
            return None
        entity_key = str(properties.get("source_entity_key") or "")
        feature_key = str(properties.get("route_key") or properties.get("feature_key") or "")
        feature_class = "CABLE" if layer_name == "CABLE_SEGMENT" else layer_name
        if not feature_key:
            candidates = native_lookup["by_source"].get((feature_class, entity_key), [])
            if len(candidates) > 1:
                raise ReviewServerError("Native geometry has ambiguous feature lineage")
            feature_key = candidates[0] if candidates else ""
        feature = native_lookup["features"].get(feature_key) or {}
        source = native_lookup["entities"].get(entity_key) or {}
        points = feature.get("points")
        segment_index = properties.get("segment_index")
        if segment_index is not None:
            segments = feature.get("segments")
            if segments is not None:
                if not isinstance(segment_index, int) or not 0 <= segment_index < len(segments):
                    raise ReviewServerError("Native geometry has an invalid source segment index")
                points = segments[segment_index]["delivery_native_points"]
            else:
                # Legacy straight-only evidence can still be read. Curves
                # without canonical materialization must never become chords.
                facts = source.get("curve_facts") or {}
                if any(facts.get("bulges", ())) or facts.get("primitive_type", "LINE") not in {
                    "LINE", "LWPOLYLINE", "POLYLINE", "2DPOLYLINE", "3DPOLYLINE",
                }:
                    raise ReviewServerError("Native curve materialization is missing; reconvert this run")
                source_points = points if points is not None else source.get("points") or []
                if not isinstance(segment_index, int) or not 0 <= segment_index < len(source_points) - 1:
                    raise ReviewServerError("Native geometry has an invalid source segment index")
                points = source_points[segment_index:segment_index + 2]
        elif points is None or not feature.get("canonical"):
            facts = source.get("curve_facts") or {}
            if any(facts.get("bulges", ())) or facts.get("primitive_type", "LINE") not in {
                "LINE", "LWPOLYLINE", "POLYLINE", "2DPOLYLINE", "3DPOLYLINE",
            }:
                raise ReviewServerError("Native curve materialization is missing; reconvert this run")
            if points is None:
                points = source.get("points")
        try:
            from osgeo import ogr
            if not isinstance(points, list) or not points:
                return None
            pairs = [
                (float(p[0]), float(p[1]))
                for p in points
                if isinstance(p, (list, tuple)) and len(p) >= 2
            ]
            if not pairs:
                return None
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
        geometry_type = delivery_geometry.GetGeometryType()
        geometry = None
        if geometry_type == ogr.wkbPoint:
            geometry = ogr.Geometry(ogr.wkbPoint)
            geometry.AddPoint_2D(*pairs[0])
        elif geometry_type in (ogr.wkbLineString, ogr.wkbMultiLineString):
            geometry = ogr.Geometry(ogr.wkbLineString)
            for pair in pairs:
                geometry.AddPoint_2D(*pair)
        elif geometry_type in (ogr.wkbPolygon, ogr.wkbMultiPolygon):
            ring = ogr.Geometry(ogr.wkbLinearRing)
            closed = list(pairs)
            if closed and closed[0] != closed[-1]:
                closed.append(closed[0])
            for pair in closed:
                ring.AddPoint_2D(*pair)
            geometry = ogr.Geometry(ogr.wkbPolygon)
            geometry.AddGeometry(ring)
        else:
            return None
        return geometry

    def layers(self) -> list[dict[str, Any]]:
        from osgeo import ogr

        dataset = self._dataset()
        values = []
        try:
            for index in range(dataset.GetLayerCount()):
                layer = dataset.GetLayerByIndex(index)
                definition = layer.GetLayerDefn()
                if definition.GetGeomType() == ogr.wkbNone:
                    continue
                values.append({
                    "name": layer.GetName(),
                    "feature_count": int(layer.GetFeatureCount()),
                    "geometry_type": int(definition.GetGeomType()),
                    "fields": [
                        definition.GetFieldDefn(field_index).GetName()
                        for field_index in range(definition.GetFieldCount())
                    ],
                })
        finally:
            dataset = None
        return values

    def geojson(
        self,
        layer_name: str,
        *,
        limit: int = 100_000,
        native_coordinates: bool = False,
    ) -> dict[str, Any]:
        if not 1 <= limit <= 250_000:
            raise ReviewServerError("layer limit must be between 1 and 250000")
        try:
            from osgeo import osr
        except ImportError as exc:  # pragma: no cover
            raise ReviewServerError("GDAL/OGR is required by the review server") from exc
        dataset = self._dataset()
        layer = dataset.GetLayerByName(layer_name)
        if layer is None:
            dataset = None
            raise ReviewServerError(f"Unknown delivery layer: {layer_name}")
        source_srs = layer.GetSpatialRef()
        transform = None
        if source_srs is not None and not native_coordinates:
            target_srs = osr.SpatialReference()
            target_srs.ImportFromEPSG(4326)
            if hasattr(target_srs, "SetAxisMappingStrategy"):
                target_srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
            source_srs = source_srs.Clone()
            if hasattr(source_srs, "SetAxisMappingStrategy"):
                source_srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
            if not source_srs.IsSame(target_srs):
                transform = osr.CoordinateTransformation(source_srs, target_srs)
        features = []
        truncated = False
        native_lookup = None
        if native_coordinates:
            native_lookup = self._native_point_lookup()
        try:
            layer.ResetReading()
            for index, item in enumerate(layer):
                if index >= limit:
                    truncated = True
                    break
                geometry = item.GetGeometryRef()
                if geometry is None:
                    continue
                properties = {
                    item.GetFieldDefnRef(field_index).GetName():
                    item.GetField(field_index)
                    for field_index in range(item.GetFieldCount())
                }
                if native_coordinates:
                    # The delivery GeoPackage stores transformed (EPSG:3857)
                    # geometry.  The CAD side of a GCP pair must be the
                    # immutable drawing coordinates, recovered from the
                    # evidence ledger via the source entity key.
                    native_geometry = self._geometry_from_native_points(
                        native_lookup, properties, geometry, layer_name,
                    )
                    if native_geometry is None:
                        continue
                    geometry = native_geometry
                else:
                    geometry = geometry.Clone()
                    if transform is not None and geometry.Transform(transform) != 0:
                        raise ReviewServerError(
                            f"Cannot transform layer {layer_name} to EPSG:4326"
                        )
                features.append({
                    "type": "Feature",
                    "id": f"{layer_name}:{item.GetFID()}",
                    "geometry": json.loads(geometry.ExportToJson()),
                    "properties": properties,
                })
        finally:
            dataset = None
        return {
            "type": "FeatureCollection",
            "features": features,
            "layer": layer_name,
            "truncated": truncated,
            "coordinate_space": (
                "cad_native" if native_coordinates else "EPSG:4326"
            ),
        }


class _WebSocketHub:
    def __init__(self):
        self._clients: set[Any] = set()
        self._lock = threading.RLock()

    async def connect(self, websocket: Any) -> None:
        await websocket.accept()
        with self._lock:
            self._clients.add(websocket)

    def disconnect(self, websocket: Any) -> None:
        with self._lock:
            self._clients.discard(websocket)

    async def broadcast(self, payload: dict[str, Any]) -> None:
        with self._lock:
            clients = tuple(self._clients)
        stale = []
        for client in clients:
            try:
                await client.send_json(payload)
            except Exception:  # pragma: no cover - client disconnect race
                stale.append(client)
        for client in stale:
            self.disconnect(client)


_PORTABLE_ARTIFACT_PATHS = {
    "source": Path("source.gpkg"),
    "evidence": Path("evidence.gpkg"),
    "delivery": Path("delivery.gpkg"),
    "styles": Path("qgis") / "styles" / "style_manifest.json",
    "evidence_graph": Path("reasoning") / "evidence_graph.json.gz",
    "visual_evidence": Path("reasoning") / "visual" / "manifest.json",
}


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _artifact_path(
    manifest: dict[str, Any],
    name: str,
    *,
    run_dir: Path | None = None,
) -> Path:
    """Resolve a copied run artifact without weakening content identity."""

    artifacts = manifest.get("artifacts")
    record = artifacts.get(name) if isinstance(artifacts, dict) else None
    raw_path = record.get("path") if isinstance(record, dict) else None
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise ReviewServerError(f"Run manifest has no {name} artifact")
    declared = Path(raw_path).expanduser()
    original = ((run_dir / declared) if run_dir is not None and not declared.is_absolute()
                else declared).resolve()
    relative = _PORTABLE_ARTIFACT_PATHS.get(name)
    expected_sha = str(record.get("sha256", "")).strip().casefold()
    if re.fullmatch(r"[0-9a-f]{64}", expected_sha) is None:
        raise ReviewServerError(f"Run manifest has no valid {name} artifact SHA-256")
    portable = (run_dir / relative).resolve() if run_dir is not None and relative is not None else None
    # A selected copied run is the review authority even while the old path
    # still exists. Never silently substitute its files if this copy changed.
    candidate = portable if portable is not None and portable.is_file() else original
    if not candidate.is_file():
        raise ReviewServerError(f"{name} artifact was not found: {candidate}")
    if _sha256_file(candidate) != expected_sha:
        raise ReviewServerError(f"{name} SHA-256 mismatch: {candidate}")
    return candidate


def _source_path(
    manifest: dict[str, Any],
    *,
    run_dir: Path,
) -> tuple[Path | None, str | None]:
    """Resolve a source drawing by content identity, never by name alone."""

    record = manifest.get("source")
    raw_path = record.get("path") if isinstance(record, dict) else None
    expected_sha = (
        str(record.get("sha256", "")).strip().casefold()
        if isinstance(record, dict) else ""
    )
    if not isinstance(raw_path, str) or not raw_path.strip():
        return None, "运行清单未记录源 CAD 路径"
    if re.fullmatch(r"[0-9a-f]{64}", expected_sha) is None:
        return None, "运行清单缺少有效的源 CAD SHA-256，无法安全重连"

    declared = Path(raw_path).expanduser()
    candidates = [
        declared if declared.is_absolute() else run_dir / declared,
        run_dir / declared.name,
        run_dir / "source" / declared.name,
    ]
    checked: set[Path] = set()
    for candidate in candidates:
        candidate = candidate.resolve()
        if candidate in checked or not candidate.is_file():
            continue
        checked.add(candidate)
        if _sha256_file(candidate) == expected_sha:
            return candidate, None
    return (
        None,
        "源 CAD 在当前机器不可用或内容哈希不匹配；请按原 SHA-256 "
        "重新附加源文件后再复制转换命令",
    )


def _svg_candidate_path(
    manifest: dict[str, Any], run_dir: Path, relative_path: str,
) -> Path:
    """Serve only content-bound SVG review artifacts from the selected run."""
    record = manifest.get("svg_candidates") or {}
    if record.get("mode") != "candidate" or record.get("status") != "completed":
        raise ReviewServerError("SVG candidates are unavailable for this run")
    relative = Path(relative_path)
    if (relative.is_absolute() or relative.drive or ".." in relative.parts
            or "\\" in relative_path or relative.as_posix() != relative_path
            or relative.suffix.casefold() not in {".html", ".json", ".svg", ".png", ".csv", ".sqlite3"}):
        raise ReviewServerError("Invalid SVG candidate artifact path")
    root = run_dir.resolve() / "svg-candidates"
    path = (root / relative).resolve()
    if root.resolve() != root or root not in path.parents or not path.is_file():
        raise ReviewServerError("SVG candidate artifact not found in the current run")
    expected = (record.get("files") or {}).get(relative_path)
    if (not isinstance(expected, str) or re.fullmatch(r"[0-9a-f]{64}", expected) is None
            or _sha256_file(path) != expected):
        raise ReviewServerError("SVG candidate artifact SHA-256 mismatch")
    artifact_name = {
        "correspondence.json": "svg_candidates_report",
        "correspondence.html": "svg_candidates_html",
        "symbols.sqlite3": "svg_candidates_store",
    }.get(relative_path)
    bound = (manifest.get("artifacts") or {}).get(artifact_name)
    if isinstance(bound, dict) and bound.get("sha256") != expected:
        raise ReviewServerError("SVG candidate artifact disagrees with its manifest binding")
    return path


def _svg_candidates_summary(
    manifest: dict[str, Any], run_dir: Path, manifest_sha256: str,
) -> dict[str, Any]:
    record = manifest.get("svg_candidates") or {}
    result = {
        **{key: value for key, value in record.items() if key != "files"},
        "mode": record.get("mode", (manifest.get("modes") or {}).get("svg", "candidate")),
        "status": record.get("status", "not_recorded"),
        "auto_applied": record.get("auto_applied", False),
        "font_dirs": record.get("font_dirs", []),
        "report_url": None,
    }
    if result["status"] == "completed":
        try:
            report_path = _svg_candidate_path(manifest, run_dir, "correspondence.json")
            _svg_candidate_path(manifest, run_dir, "correspondence.html")
            report = _json_object(report_path)
            if report.get("source_sha256") != (manifest.get("source") or {}).get("sha256"):
                raise ReviewServerError("SVG candidate report belongs to a different CAD source")
            result.update(
                report_url=f"/api/svg-candidates/{manifest_sha256}/correspondence.html",
                point_count=report.get("all_points_accounted_for"),
                dispositions=report.get("dispositions", {}),
            )
        except (ReviewServerError, OSError) as exc:
            result["report_blocker"] = str(exc)
    return result


def _registration_transformer(manifest: dict[str, Any]):
    """Reconstruct the exact reviewed nominal transform, including CAD units."""
    from .cad2gis_v3.config import SourceProfile
    from .cad2gis_v3.georef import DirectTransformer, GeoDataTransformer
    from .cad2gis_v3.units import build_unit_crs_contract

    record = manifest.get("profiles", {}).get("source_profile", {})
    path = Path(str(record.get("path", "")))
    if not path.is_file() or not record.get("sha256"):
        raise ReviewServerError("Canonical preview requires the bound source profile")
    if hashlib.sha256(path.read_bytes()).hexdigest() != record["sha256"]:
        raise ReviewServerError("Canonical preview source profile hash mismatch")
    profile = SourceProfile.load(path)
    if profile.source_sha256 != manifest.get("source", {}).get("sha256"):
        raise ReviewServerError("Canonical preview source binding mismatch")
    units = build_unit_crs_contract(
        dwg_insunits=profile.dwg_insunits, source_crs=profile.source_crs, target_crs=profile.target_crs,
        source_coordinate_scale_to_m=profile.source_coordinate_scale_to_m,
        source_coordinate_scale_reviewed=profile.source_coordinate_scale_reviewed,
        local_registration_strategy=profile.local_registration_strategy,
        local_registration_reviewed=profile.local_registration_reviewed,
    )
    if not units.can_direct_transform and profile.geodata_registration is None:
        from .cad2gis_v3.osm_matching import LocalMatchingReference
        return LocalMatchingReference(profile.source_crs, profile.target_crs, units)
    if profile.geodata_registration is not None:
        return GeoDataTransformer(profile.source_crs, profile.target_crs,
            geodata_registration=profile.geodata_registration, unit_contract=units)
    return DirectTransformer(profile.source_crs, profile.target_crs, unit_contract=units)


def _registration_fit(capture: dict[str, Any], manifest: dict[str, Any]):
    from .cad2gis_v3.calibration import GroundControlPoint, fit_calibration

    if (capture.get("distribution_checks") or {}).get("checks_reusing_training_locations"):
        raise ReviewServerError("Held-out checks must use different CAD and reference locations from training controls")
    nominal = _registration_transformer(manifest)
    controls = tuple(GroundControlPoint(
        point_id=item["point_id"], cad_point=(item["cad_x"], item["cad_y"]),
        target_point=(item["target_easting"], item["target_northing"]), target_crs=item["target_crs"],
        role=item["role"], source=item["source"], accuracy_m=item["accuracy_m"],
        weight=item["weight"], enabled=item["enabled"],
    ) for item in capture["controls"])
    result = fit_calibration(controls, nominal, model="translation",
        expected_source_crs=capture["source_crs"], expected_target_crs=capture["target_crs"])
    return result, nominal


def _registration_controls(
    store: Any,
    manifest: dict[str, Any],
) -> dict[str, Any]:
    """Compile revisioned Web picks into explicit CAD/target-CRS controls."""

    crs = manifest.get("crs")
    if not isinstance(crs, dict):
        raise ReviewServerError("Run manifest has no CRS contract")
    source_crs = str(crs.get("source_crs", "")).strip()
    target_crs = str(crs.get("target_crs", "")).strip()
    if not source_crs or not target_crs:
        raise ReviewServerError("Run manifest CRS contract is incomplete")
    try:
        from pyproj import CRS, Transformer
        from pyproj.exceptions import ProjError

        target = CRS.from_user_input(target_crs)
        if not target.is_projected:
            raise ReviewServerError(
                "Web coordinate transfer requires a projected target CRS"
            )
        transformer = Transformer.from_crs(
            "EPSG:4326", target, always_xy=True,
        )
    except ReviewServerError:
        raise
    except Exception as exc:
        raise ReviewServerError(
            f"Could not initialize EPSG:4326 to {target_crs}: {exc}"
        ) from exc

    compiled = []
    for feature in store.feature_collection().get("features", ()):
        properties = feature.get("properties")
        geometry = feature.get("geometry")
        if (
            not isinstance(properties, dict)
            or properties.get("_kind") != "cad_map_gcp"
            or not isinstance(geometry, dict)
            or geometry.get("type") != "Point"
        ):
            continue
        from .gcp_workflow import edit_web_control

        try:
            validated = edit_web_control(feature)
            properties = validated["properties"]
            lon, lat = validated["geometry"]["coordinates"]
            easting, northing = transformer.transform(lon, lat, errcheck=True)
            values = (properties["cad_x"], properties["cad_y"], float(easting), float(northing))
        except (ValueError, TypeError, KeyError, ProjError) as exc:
            raise ReviewServerError(f"Control {feature.get('id')} is invalid: {exc}") from exc
        if not all(math.isfinite(value) for value in values):
            raise ReviewServerError(
                f"Control {feature.get('id')} has non-finite coordinates"
            )
        role = str(properties.get("role", "train")).strip().lower()
        if role not in {"train", "check"}:
            raise ReviewServerError(
                f"Control {feature.get('id')} role must be train or check"
            )
        compiled.append({
            "point_id": str(feature.get("id")),
            "cad_x": values[0],
            "cad_y": values[1],
            "longitude": lon,
            "latitude": lat,
            "target_easting": values[2],
            "target_northing": values[3],
            "target_crs": target_crs,
            "role": role,
            "source": (
                "OPENSTREETMAP_VISUAL_REFERENCE:EPSG:4326;"
                "relative_registration_only"
            ),
            # This prior exists only for the canonical fit's required weight
            # contract. It is never a measured OSM or output accuracy claim.
            "accuracy_m": properties.get("accuracy_m", 8.0),
            "accuracy_basis": ("operator_estimate" if "accuracy_m" in properties
                               else "default_reference_uncertainty_prior_not_measured_accuracy"),
            "weight": properties.get("weight", 1.0),
            "enabled": properties.get("enabled", True),
            "revision": int(properties.get("_review_revision", 0)),
            "editor_state": (feature["properties"].get("_gcp_edit") or properties["_gcp_edit"]),
        })
    compiled.sort(key=lambda item: item["point_id"])
    train = [item for item in compiled if item["role"] == "train" and item["enabled"]]
    check = [item for item in compiled if item["role"] == "check" and item["enabled"]]

    def has_two_dimensional_support(values: list[dict[str, Any]]) -> bool:
        # Both X/Y spans can be nonzero for a single diagonal line. Normalize
        # first to avoid precision loss from large projected/CAD coordinates.
        points = {(item["cad_x"], item["cad_y"]) for item in values}
        if len(points) < 3:
            return False
        x0, y0 = min(points)
        extent = max(max(abs(x - x0), abs(y - y0)) for x, y in points)
        if not math.isfinite(extent) or extent <= 1e-6:
            return False
        local = [((x - x0) / extent, (y - y0) / extent) for x, y in points]
        mx = sum(x for x, _ in local) / len(local)
        my = sum(y for _, y in local) / len(local)
        xx = sum((x - mx) ** 2 for x, _ in local)
        yy = sum((y - my) ** 2 for _, y in local)
        xy = sum((x - mx) * (y - my) for x, y in local)
        return xx * yy - xy * xy > 1e-8 * (xx + yy) ** 2

    training_sources = {(item["cad_x"], item["cad_y"]) for item in train}
    training_targets = {(item["longitude"], item["latitude"]) for item in train}
    reused_checks = [item["point_id"] for item in check
                     if (item["cad_x"], item["cad_y"]) in training_sources
                     or (item["longitude"], item["latitude"]) in training_targets]
    train_support = has_two_dimensional_support(train)
    check_support = has_two_dimensional_support(check)

    ready = (
        len({(item["cad_x"], item["cad_y"]) for item in train}) >= 4
        and len({(item["cad_x"], item["cad_y"]) for item in check}) >= 3
        and len({(item["longitude"], item["latitude"]) for item in train}) >= 4
        and len({(item["longitude"], item["latitude"]) for item in check}) >= 3
        and train_support
        and check_support
        and not reused_checks
    )
    return {
        "schema_version": "cad2gis.web_registration_capture.v1",
        "source_crs": source_crs,
        "target_crs": target_crs,
        "controls": compiled,
        "controls_sha256": hashlib.sha256(json.dumps(compiled, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
        "train_count": len(train),
        "check_count": len(check),
        "minimum_train_count": 4,
        "minimum_check_count": 3,
        "distribution_gate_passed": ready,
        "distribution_checks": {
            "train_two_dimensional": train_support,
            "check_two_dimensional": check_support,
            "checks_reusing_training_locations": reused_checks,
            "scope": "control_geometry_preflight; final coverage gates still apply",
        },
        "activation_ready": ready,
        "accuracy_class": "RELATIVE_OSM_REFERENCE_ONLY",
        "absolute_accuracy_verified": False,
        "precision_contract": {
            "nudge_steps_m": [0.01, 0.1, 1.0],
            "nudge_distance_method": "WGS84_ellipsoidal_geodesic",
            "nudge_step_is_accuracy_claim": False,
            "source_cad_pick_mutable": False,
            "train_role": "used_to_fit_transform",
            "check_role": "held_out_from_current_fit; operator_selected_reference",
            "absolute_accuracy": "unverified; OSM visual references are not surveyed control",
        },
    }


def _gcp_profile_payload(
    capture: dict[str, Any],
    manifest: dict[str, Any],
    *,
    enabled: bool,
) -> dict[str, Any]:
    source = manifest.get("source")
    source_sha256 = (
        str(source.get("sha256", ""))
        if isinstance(source, dict) else ""
    )
    controls = [{
        key: item[key]
        for key in (
            "point_id", "cad_x", "cad_y", "target_easting",
            "target_northing", "target_crs", "role", "source",
            "accuracy_m", "weight", "enabled",
        )
    } for item in capture["controls"]]
    descriptions = {
        "point_id": "Stable review feature identifier.",
        "cad_x": "Source drawing X coordinate.",
        "cad_y": "Source drawing Y coordinate.",
        "target_easting": "Transferred target CRS easting.",
        "target_northing": "Transferred target CRS northing.",
        "target_crs": "Projected target CRS identifier.",
        "role": "train or independent check.",
        "source": "Coordinate provenance and accuracy class.",
        "accuracy_m": "Estimated reference accuracy in metres.",
        "weight": "Deterministic fitting weight.",
        "enabled": "Control activation switch.",
    }
    return {
        "schema_version": "cad2gis-gcp-profile-v1",
        "enabled": enabled,
        "source_sha256": source_sha256,
        "source_crs": capture["source_crs"],
        "target_crs": capture["target_crs"],
        "requested_model": "translation",
        "controls": controls,
        "control_schema": {
            "description": (
                "Web-selected CAD to OSM reference controls. Suitable for "
                "relative visual registration, not surveyed absolute accuracy."
            ),
            "required_fields": list(descriptions),
            "fields": descriptions,
        },
        "model_selection": {
            # OSM visual references are relative placement only: the CAD
            # drawing shares the target orientation, so a rotation/scale
            # similarity fit overfits the few controls.  requested_model
            # fixes the transform to translation (rotation/scale gates stay
            # strict for any future auto/affine fallback); candidate_order
            # must match the policy contract and is not the selector.
            "candidate_order": ["similarity", "translation", "affine"],
            "policy": "select_shape_preserving_model_with_independent_validation",
            "minimum_training_controls": {
                "translation": 3, "similarity": 4, "affine": 6,
            },
            "affine_gate": {
                "require_spatially_structured_similarity_residuals": True,
                "spatial_structure_reviewed": False,
                "require_holdout_improvement": True,
            },
            "nonlinear_models": {
                "enabled": False,
                "reason": "Web registration is shape-preserving by contract.",
            },
        },
        "robust": {
            "enabled": False,
            "max_iterations": 256,
            "outlier_threshold_m": None,
        },
        "transform_limits": {
            # CAD-local drawings are shifted by the OSM anchor translation
            # (millions of metres) into EPSG:3857; the pivot gate must allow
            # that reviewed shift, not reject it.  Twice the anchor magnitude
            # bounds the fitted translation to the known placement while
            # still catching gross misreferences.
            "max_pivot_shift_m": max(
                2.0 * math.hypot(
                    float(anchor.get("translation_dx", 0.0)),
                    float(anchor.get("translation_dy", 0.0)),
                ),
                5_000_000.0,
            ) if (anchor := (manifest.get("osm_anchor") or {})) else 5_000_000.0,
            # OSM relative references share the drawing orientation; a
            # similarity fallback may only nudge rotation, never re-orient.
            "max_abs_rotation_deg": 5.0,
            "max_scale_deviation_ratio": 1.5,
            "max_affine_condition_number": 1_000_000.0,
        },
        "validation": {
            "max_check_rmse_m": 20.0,
            "max_check_p95_m": 30.0,
            "max_check_error_m": 40.0,
            "min_check_points": 3,
            "spatial_distribution_reviewed": enabled,
            "spatial_distribution_review_source": (
                "CAD2GIS deterministic Web control distribution preflight; "
                "OSM relative reference only"
                if enabled else ""
            ),
            "affine_min_improvement_ratio": None,
        },
    }


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _matching_record(manifest: dict[str, Any]) -> dict[str, Any] | None:
    partition = manifest.get("delivery_partition") or {}
    region = partition.get("region_id")
    if region:
        entry = (manifest.get("delivery_partitions") or {}).get(region) or {}
        value = entry.get("osm_matching") or partition.get("osm_matching")
        if isinstance(value, dict):
            return value
    value = manifest.get("osm_matching") or (manifest.get("crs") or {}).get("osm_matching")
    return value if isinstance(value, dict) else None


def _matching_preview(capture: dict[str, Any], manifest: dict[str, Any], parent_sha: str):
    from .cad2gis_v3.osm_matching import refine_match, transformer_from_record

    if (capture.get("distribution_checks") or {}).get("checks_reusing_training_locations"):
        raise ReviewServerError("Held-out checks must use different CAD and reference locations from training controls")
    record = _matching_record(manifest)
    if not record:
        raise ReviewServerError("This run has no OSM matching record; create a standard OSM matching run first")
    nominal = _registration_transformer(manifest)
    region_id = (manifest.get("delivery_partition") or {}).get("region_id") or record.get("region_id")
    refined = refine_match(record, nominal, capture["controls"],
        source_sha256=manifest["source"]["sha256"], parent_run_sha256=parent_sha,
        **({"region_id": region_id} if region_id else {}))
    return refined, transformer_from_record(refined, nominal)


def _matching_fit_summary(record: dict[str, Any]) -> dict[str, Any]:
    quality = record.get("quality") or {}
    return {
        "selected_model": "translation", "target_crs": record.get("target_crs", "EPSG:3857"),
        "train_metrics": quality.get("train_metrics", quality.get("training", {})),
        "check_metrics": quality.get("check_metrics", quality.get("check", {})),
        "residuals": quality.get("residuals", []),
        "residual_distance_method": quality.get("residual_distance_method", "WGS84_ellipsoidal_geodesic"),
        "checks_used_for_fit": False,
        "accuracy_scope": "relative_reference_agreement; not_absolute_accuracy",
        "authority": "relative_osm", "absolute_accuracy_verified": False,
        "validation": {"passed": None},
    }


def _matching_conversion_options(manifest: dict[str, Any], source_run: Path,
                                 profile_path: Path, next_run: Path) -> dict[str, Any]:
    """Replay bound conversion inputs, including the original semantic authority."""
    source, blocker = _source_path(manifest, run_dir=source_run)
    if source is None:
        raise ReviewServerError(blocker)
    options: dict[str, Any] = {"source": source, "run_dir": next_run,
        "matching": "osm", "match_profile": profile_path}
    snapshot = (manifest.get("conversion_snapshot") or {}).get("artifacts") or {}
    profiles = manifest.get("profiles") or {}
    for name in ("source_profile", "mapping_registry", "decision_pack"):
        record = profiles.get(name) or snapshot.get(name)
        if name == "decision_pack":
            record = (manifest.get("artifacts") or {}).get(name) or record
        if not record:
            if name in {"source_profile", "mapping_registry"}:
                raise ReviewServerError(f"Re-matching requires the bound {name}")
            continue
        path = Path(str(record.get("path", ""))).expanduser()
        if not path.is_absolute():
            path = source_run / path
        if (not path.is_file() or not record.get("sha256")
                or _sha256_file(path) != record["sha256"]):
            raise ReviewServerError(f"Bound {name} is unavailable or its SHA-256 changed")
        # Keep each config beside its relative catalogs and partition contracts.
        options[name] = path.resolve()
    for name in ("delivery_partitions", "spatial_regions", "osm_anchor"):
        # Older runs did not freeze these optional inputs. New snapshots bind
        # absence as well as content so a rematch cannot silently change scope.
        if name not in snapshot:
            continue
        record = snapshot[name]
        if not isinstance(record, dict) or not record.get("path"):
            raise ReviewServerError(f"Bound {name} has an invalid snapshot descriptor")
        path = Path(str(record["path"])).expanduser()
        if not path.is_absolute():
            path = source_run / path
        if path.resolve() != (options["mapping_registry"].parent / f"{name}.json").resolve():
            raise ReviewServerError(f"Bound {name} path does not match the conversion configuration")
        if record.get("exists") is False:
            if path.exists():
                raise ReviewServerError(f"Bound {name} was absent but has appeared since the reviewed run")
        elif (not path.is_file() or not record.get("sha256")
                or _sha256_file(path) != record["sha256"]):
            raise ReviewServerError(f"Bound {name} is unavailable or its SHA-256 changed")
    modes = manifest.get("modes") or {}
    options["llm"] = modes.get("llm", "off")
    options["domain"] = modes.get("domain", "auto")
    svg = manifest.get("svg_candidates") or {}
    options["svg_mode"] = svg.get("mode", modes.get("svg", "candidate"))
    if options["svg_mode"] not in {"candidate", "off"}:
        raise ReviewServerError("Bound SVG mode must be candidate or off")
    font_dirs = svg.get("font_dirs", [])
    if (not isinstance(font_dirs, (list, tuple))
            or any(not isinstance(path, str) or not path.strip() for path in font_dirs)):
        raise ReviewServerError("Bound SVG font directories must be an array of paths")
    options["svg_font_dirs"] = tuple(
        (Path(path).expanduser() if Path(path).expanduser().is_absolute()
         else source_run / Path(path).expanduser()).resolve()
        for path in font_dirs
    )
    if manifest.get("geometry_repairs"):
        options["geometry_repairs"] = manifest["geometry_repairs"]
    replay = manifest.get("source_replay") or {}
    replay_path = Path(str(replay.get("source_run", source_run))).expanduser()
    if (replay_path / "source_manifest.json").is_file():
        from .cad2gis_v3.source_replay import load_native_snapshot
        _, receipt = load_native_snapshot(replay_path, source)
        if replay.get("snapshot_sha256") and receipt["snapshot_sha256"] != replay["snapshot_sha256"]:
            raise ReviewServerError("Source replay snapshot changed")
        options["source_run"] = replay_path.resolve()
    elif replay:
        raise ReviewServerError("The bound native source snapshot is unavailable; restore it before re-matching")
    semantic = manifest.get("semantic_revision")
    if semantic:
        store_path = semantic.get("store_path") or semantic.get("semantic_store")
        if not store_path or not semantic.get("job_id") or not options.get("source_run"):
            raise ReviewServerError("This semantic revision has no reproducible store/job/source binding; restore its semantic store before re-matching")
        from .cad2gis_v3.semantic_delivery import load_published_revision
        _, receipt = load_published_revision(Path(store_path), semantic["job_id"], semantic["snapshot_sha256"])
        for key in ("revision", "decisions_sha256", "semantic_gpkg_sha256"):
            if receipt.get(key) != semantic.get(key):
                raise ReviewServerError("The bound semantic revision changed")
        options.update(semantic_store=Path(store_path), semantic_job=semantic["job_id"])
    return options


def _matching_command(options: dict[str, Any]) -> str:
    # This is a copyable command only; execution calls the typed API below.
    def quote(value: Any) -> str:
        text = str(value)
        return "'" + text.replace("'", "''") + "'" if os.name == "nt" else shlex.quote(text)
    parts = ["cad2gis convert", quote(options["source"])]
    for key, value in options.items():
        if key == "svg_font_dirs":
            for directory in value:
                parts.extend(["--svg-font-dir", quote(directory)])
        elif key != "source":
            parts.extend(["--" + key.replace("_", "-"), quote(value)])
    return " ".join(parts)


def _execute_matching_conversion(options: dict[str, Any]) -> None:
    from .pipeline import convert_project
    convert_project(**options)


def _matching_result_view(manifest: dict[str, Any], run_dir: Path, region: str | None):
    """Retain the selected partition when a whole source is converted again."""
    if not region:
        return manifest, run_dir
    if ((manifest.get("delivery_partition") or {}).get("region_id") == region
            and (_matching_record(manifest) or {}).get("region_id") == region):
        return manifest, run_dir
    if Path(region).name != region or region in {".", ".."}:
        raise ReviewServerError("Invalid matching result partition")
    entry = (manifest.get("delivery_partitions") or {}).get(region)
    view_dir = run_dir / region
    partition = _json_object(view_dir / "partition_manifest.json")
    artifact = view_dir / "delivery.gpkg"
    if (not isinstance(entry, dict) or partition.get("region_id") != region
            or partition.get("delivery_counts") != entry.get("delivery_counts")
            or not artifact.is_file() or _sha256_file(artifact) != entry.get("sha256")):
        raise ReviewServerError("New matching partition does not match its parent manifest")
    value = json.loads(json.dumps(manifest))
    value["delivery_counts"] = dict(entry["delivery_counts"])
    value["delivery_partition"] = partition
    value["artifacts"]["delivery"] = {"path": str(artifact), "sha256": entry["sha256"]}
    value["parent_run_provenance"] = {"run_dir": str(run_dir),
        "manifest_path": str(run_dir / "run_manifest.json"),
        "manifest_sha256": _sha256_file(run_dir / "run_manifest.json"),
        "run_status": manifest.get("run_status"), "source_entity_count_scope": "parent_run",
        "validation_scope": "parent_run"}
    return value, view_dir


def create_review_app(
    run_dir: str | Path,
    *,
    workspace_dir: str | Path | None = None,
    qgis_server_url: str = "",
    qgis_project: str = "",
    qgis_layers: str = "",
    postgis_dsn: str | None = None,
):
    """Create the optional FastAPI review application."""

    try:
        from fastapi import FastAPI, HTTPException, Request, WebSocket
        from fastapi.responses import FileResponse, HTMLResponse
        from starlette.websockets import WebSocketDisconnect
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise ReviewServerError(
            "Review dependencies are missing; install cad2gis[review]"
        ) from exc

    # Activate a bundled pyramids-gis runtime on the server thread before
    # request handlers import ``osgeo``.  Conversion already performs this
    # activation, but ``cad2gis review`` starts in a fresh process and must not
    # depend on a previous conversion import side effect.
    from .native_runtime import ensure_osgeo_runtime

    gis_runtime = ensure_osgeo_runtime()
    if not gis_runtime.get("available"):
        raise ReviewServerError(
            "Review GIS runtime is unavailable; install cad2gis[agent] or "
            "cad2gis[portable] so GDAL/OGR can open the delivery GeoPackage "
            f"({gis_runtime.get('detail', 'no runtime detail')})"
        )

    run_path = Path(run_dir).expanduser().resolve()
    source_run_path = run_path
    manifest_path = run_path / "run_manifest.json"
    partition_manifest_path = run_path / "partition_manifest.json"
    if manifest_path.is_file():
        manifest = _json_object(manifest_path)
    elif partition_manifest_path.is_file():
        parent_manifest_path = run_path.parent / "run_manifest.json"
        if not parent_manifest_path.is_file():
            raise ReviewServerError(
                f"Partition manifest exists but parent run manifest is missing: {parent_manifest_path}"
            )
        manifest = json.loads(
            json.dumps(_json_object(parent_manifest_path))
        )
        partition = _json_object(partition_manifest_path)
        region_id = partition.get("region_id")
        entries = manifest.get("delivery_partitions", {})
        entry = entries.get(region_id) if isinstance(entries, dict) and isinstance(region_id, str) else None
        if not isinstance(entry, dict) or region_id != run_path.name:
            raise ReviewServerError("Partition has no matching parent delivery_partitions entry")
        declared_path = Path(str(entry.get("path", "")))
        if declared_path.name != "delivery.gpkg" or declared_path.parent.name != region_id:
            raise ReviewServerError("Parent partition delivery path does not match the selected region")
        counts = entry.get("delivery_counts")
        partition_counts = partition.get("delivery_counts")
        if (not isinstance(counts, dict) or not counts
                or any(not isinstance(key, str) or not key or not isinstance(value, int)
                       or isinstance(value, bool) or value < 0 for key, value in counts.items())
                or not isinstance(partition_counts, dict)
                or any(not isinstance(value, int) or isinstance(value, bool)
                       for value in partition_counts.values())
                or partition_counts != counts):
            raise ReviewServerError("Partition counts disagree with authoritative parent delivery counts")
        expected_sha = str(entry.get("sha256", "")).strip().casefold()
        partition_delivery = run_path / "delivery.gpkg"
        if (re.fullmatch(r"[0-9a-f]{64}", expected_sha) is None
                or not partition_delivery.is_file()
                or _sha256_file(partition_delivery) != expected_sha):
            raise ReviewServerError("Partition delivery SHA-256 does not match the parent manifest")
        source_run_path = run_path.parent
        manifest_path = parent_manifest_path
        manifest["parent_run_provenance"] = {
            "run_dir": str(source_run_path),
            "manifest_path": str(parent_manifest_path),
            "manifest_sha256": _sha256_file(parent_manifest_path),
            "run_status": manifest.get("run_status"),
            "source_entity_count_scope": "parent_run",
            "validation_scope": "parent_run",
        }
        manifest["delivery_counts"] = dict(counts)
        artifacts = manifest.get("artifacts")
        if not isinstance(artifacts, dict):
            artifacts = {}
            manifest["artifacts"] = artifacts
        artifacts["delivery"] = {
            "path": str(partition_delivery),
            "sha256": expected_sha,
        }
        manifest["delivery_partition"] = partition
        manifest["run_dir"] = str(run_path)
    else:
        raise ReviewServerError(f"Run manifest does not exist: {manifest_path}")
    delivery = GeoPackageProvider(
        _artifact_path(manifest, "delivery", run_dir=run_path)
    )
    workspace = (
        Path(workspace_dir).expanduser().resolve()
        if workspace_dir is not None
        else run_path.parent / f"{run_path.name}.review"
    )
    workspace.mkdir(parents=True, exist_ok=True)
    source_record = manifest.get("source")
    source_hash = (
        str(source_record.get("sha256", ""))
        if isinstance(source_record, dict) else ""
    )
    session_id = f"{run_path.name}:{source_hash[:16]}"
    active_path = workspace / "matching-active.json"
    if active_path.is_file():
        active = _json_object(active_path)
        if (active.get("source_sha256") == source_hash
                and Path(str(active.get("run_dir", ""))).resolve() == run_path):
            session_id = str(active["session_id"])
    resolved_postgis_dsn = (
        os.environ.get("CAD2GIS_REVIEW_POSTGIS_DSN", "")
        if postgis_dsn is None else postgis_dsn
    )
    store = (
        PostGISReviewStore(resolved_postgis_dsn, session_id=session_id)
        if resolved_postgis_dsn
        else SQLiteReviewStore(
            workspace / "review.sqlite3", session_id=session_id,
        )
    )
    store_kind = "postgis" if resolved_postgis_dsn else "sqlite"
    matching_lock = threading.Lock()
    if active_path.is_file():
        active = _json_object(active_path)
        if active.get("source_sha256") != source_hash or active.get("session_id") != session_id:
            raise ReviewServerError("Matching workspace belongs to a different source or review session")
        resumed_path = Path(active.get("source_run_dir", active["run_dir"])).resolve()
        resumed_manifest = resumed_path / "run_manifest.json"
        if not resumed_manifest.is_file() or _sha256_file(resumed_manifest) != active["manifest_sha256"]:
            raise ReviewServerError("Saved matching result manifest changed")
        resumed = _json_object(resumed_manifest)
        if (resumed.get("source") or {}).get("sha256") != source_hash:
            raise ReviewServerError("Saved matching result belongs to a different CAD source")
        resumed, view_dir = _matching_result_view(resumed, resumed_path, active.get("region_id"))
        resumed_delivery = _artifact_path(resumed, "delivery", run_dir=view_dir)
        if _sha256_file(resumed_delivery) != resumed["artifacts"]["delivery"]["sha256"]:
            raise ReviewServerError("Saved matching delivery SHA-256 mismatch")
        delivery = GeoPackageProvider(resumed_delivery)
        delivery.layers()
        manifest, run_path, source_run_path, manifest_path = resumed, view_dir, resumed_path, resumed_manifest
    if not _matching_record(manifest) and (manifest.get("profiles") or {}).get("source_profile"):
        # Legacy runs can enter the new workflow without reusing an old fitted
        # delivery as the CAD source. No place lookup or network access occurs.
        try:
            from .cad2gis_v3.osm_matching import LocalMatchingReference, match_osm
            nominal = _registration_transformer(manifest)
            _, base = match_osm(source=Path(str(manifest["source"].get("path", ""))),
                source_sha256=source_hash, features=[], nominal=nominal,
                coordinate_domain=(manifest.get("crs") or {}).get("coordinate_domain")
                    or {"passed": not isinstance(nominal, LocalMatchingReference)})
            manifest["osm_matching"] = base
        except (ValueError, OSError):
            pass  # The legacy GCP exporter still reports its specific profile error.
    # A result opened as a fresh workspace inherits the exact native picks
    # frozen into its match record. Existing revisioned edits take precedence.
    inherited = (_matching_record(manifest) or {}).get("controls", [])
    if inherited and not store.events(limit=1):
        from pyproj import Transformer
        for item in inherited:
            lon, lat = Transformer.from_crs(item["target_crs"], 4326, always_xy=True).transform(
                item["target_easting"], item["target_northing"])
            store.upsert({"type": "Feature", "id": item["point_id"],
                "geometry": {"type": "Point", "coordinates": [lon, lat]},
                "properties": {"_kind": "cad_map_gcp", "cad_x": item["cad_x"],
                    "cad_y": item["cad_y"], "role": item["role"]}},
                expected_revision=0, actor="matching-result",
                initial_gcp_state=item.get("editor_state"))
    hub = _WebSocketHub()
    svg_manifest_sha256 = _sha256_file(manifest_path)
    web_root = Path(__file__).resolve().parent / "webdemo"
    if not (web_root / "index.html").is_file():
        raise ReviewServerError(f"Review web assets are missing: {web_root}")

    app = FastAPI(title="CAD2GIS Review", version="1")

    @app.exception_handler(ReviewServerError)
    async def review_error_handler(_request: Request, exc: ReviewServerError):
        from fastapi.responses import JSONResponse

        status = 409 if isinstance(exc, ReviewConflictError) else 400
        return JSONResponse(status_code=status, content={"detail": str(exc)})

    @app.get("/")
    async def index(request: Request):
        if request.query_params.get("review"):
            return HTMLResponse(_application_review_html(
                (web_root / "index.html").read_text(encoding="utf-8"),
            ))
        return FileResponse(web_root / "index.html")

    @app.get("/assets/{asset_path:path}", include_in_schema=False)
    async def web_asset(asset_path: str):
        """Serve both top-level scripts and packaged binary/SVG assets."""

        relative = Path(asset_path)
        if relative.is_absolute() or ".." in relative.parts:
            raise HTTPException(status_code=404, detail="Asset not found")
        for root in (web_root, web_root / "assets"):
            resolved_root = root.resolve()
            candidate = (resolved_root / relative).resolve()
            if (
                candidate.is_file()
                and (candidate == resolved_root or resolved_root in candidate.parents)
            ):
                return FileResponse(candidate)
        raise HTTPException(status_code=404, detail="Asset not found")

    @app.get("/api/health")
    async def health():
        return {
            "status": "ok",
            "schema_version": REVIEW_SCHEMA,
            "session_id": session_id,
        }

    @app.get("/api/run")
    async def run_summary():
        resolved_source, source_blocker = _source_path(
            manifest, run_dir=source_run_path,
        )
        return {
            "schema_version": manifest.get("schema_version"),
            "run_dir": str(run_path),
            "workspace_dir": str(workspace),
            "run_status": manifest.get("run_status"),
            "source": manifest.get("source"),
            "source_available": resolved_source is not None,
            "source_resolved_path": (
                str(resolved_source) if resolved_source is not None else None
            ),
            "source_blocker": source_blocker,
            "source_entity_count": manifest.get("source_entity_count"),
            "delivery_counts": manifest.get("delivery_counts"),
            "delivery_partition": manifest.get("delivery_partition"),
            "parent_run_provenance": manifest.get("parent_run_provenance"),
            "crs": manifest.get("crs"),
            "osm_matching": _matching_record(manifest),
            "svg_candidates": _svg_candidates_summary(
                manifest, source_run_path, svg_manifest_sha256,
            ),
            "osm_anchor": manifest.get("osm_anchor"),
            "validation": manifest.get("validation"),
            "reasoning": manifest.get("reasoning"),
            "qgis_server": {
                "url": qgis_server_url,
                "project": qgis_project,
                "layers": qgis_layers,
            },
            "review_store": store_kind,
            "immutable_delivery": True,
        }

    @app.get("/api/registration")
    async def registration_summary():
        capture = _registration_controls(store, manifest)
        capture["matching"] = _matching_record(manifest)
        capture["matching_ready"] = False
        if capture["matching"]:
            if capture["train_count"]:
                try:
                    record, _ = _matching_preview(capture, manifest, _sha256_file(manifest_path))
                    capture.update(preview_match=record, preview_fit=_matching_fit_summary(record), matching_ready=True)
                except (ValueError, OSError) as exc:
                    capture["preview_error"] = str(exc)
            return capture
        if capture["train_count"]:
            try:
                result, _ = _registration_fit(capture, manifest)
                capture["preview_fit"] = {**result.to_dict(), "authority": "canonical_fit_engine",
                    "residual_distance_method": "target_projected_grid_metres",
                    "checks_used_for_fit": False,
                    "absolute_accuracy_verified": False,
                    "accuracy_scope": "relative_reference_agreement; not_absolute_accuracy",
                    "acceptance": "preview_only; final run still enforces profile and spatial coverage gates"}
            except (ValueError, OSError, ReviewServerError) as exc:
                capture["preview_error"] = str(exc)
        return capture

    @app.get("/api/matching/evidence")
    async def matching_evidence(expected_match_sha256: str | None = None):
        record = _matching_record(manifest)
        if not record:
            raise HTTPException(404, "This run has no OSM matching evidence")
        if expected_match_sha256 is not None and expected_match_sha256 != record.get("match_sha256"):
            raise ReviewConflictError("Matching result changed; reload the evidence layers")
        from .cad2gis_v3.osm_evidence import evidence_geojson

        return {
            "match_sha256": record.get("match_sha256"), "revision": record.get("revision"),
            "collections": evidence_geojson(record),
            "authority": "relative_osm", "absolute_accuracy_verified": False,
        }

    @app.get("/api/registration/preview/{layer_name}")
    async def registration_preview(layer_name: str, expected_controls_sha256: str, limit: int = 100_000):
        from pyproj import Transformer

        capture = _registration_controls(store, manifest)
        if capture["controls_sha256"] != expected_controls_sha256:
            raise HTTPException(409, "Control points changed; refresh the registration before previewing")
        try:
            if _matching_record(manifest):
                record, matched = _matching_preview(capture, manifest, _sha256_file(manifest_path))
                geographic = Transformer.from_crs(matched.target_crs, "EPSG:4326", always_xy=True)
                project_native = matched.point
                fit_payload = _matching_fit_summary(record)
            else:
                result, nominal = _registration_fit(capture, manifest)
                geographic = Transformer.from_crs(result.target_crs, "EPSG:4326", always_xy=True)
                def project_native(point):
                    return result.project_native(point, nominal)
                fit_payload = result.to_dict()
        except (ValueError, OSError) as exc:
            raise ReviewServerError(str(exc)) from exc
        collection = delivery.geojson(layer_name, limit=limit, native_coordinates=True)

        def project(coordinates):
            if coordinates and isinstance(coordinates[0], (int, float)):
                x, y = project_native(coordinates)
                return [*geographic.transform(x, y), *coordinates[2:]]
            return [project(child) for child in coordinates]

        def geometry(value):
            if value.get("type") == "GeometryCollection":
                for child in value.get("geometries", []):
                    geometry(child)
            elif "coordinates" in value:
                value["coordinates"] = project(value["coordinates"])

        for feature in collection["features"]:
            if feature.get("geometry"):
                geometry(feature["geometry"])
        collection["coordinate_reference"] = "EPSG:4326"
        collection["preview_only"] = True
        collection["fit"] = fit_payload
        return collection

    @app.post("/api/matching/rematch")
    async def rematch(request: Request):
        nonlocal manifest, run_path, source_run_path, manifest_path, delivery, svg_manifest_sha256
        from starlette.concurrency import run_in_threadpool

        payload = await _request_object(request)
        if not isinstance(payload, dict) or not isinstance(payload.get("execute", False), bool):
            raise HTTPException(400, "Request must be an object and execute must be boolean")
        if not matching_lock.acquire(blocking=False):
            raise ReviewConflictError("A matching conversion is already running")
        try:
            capture = _registration_controls(store, manifest)
            current = _matching_record(manifest)
            if not current:
                raise ReviewServerError("This run has no OSM matching record")
            if (payload.get("expected_controls_sha256") != capture["controls_sha256"]
                    or payload.get("expected_match_sha256") != current.get("match_sha256")
                    or type(payload.get("expected_match_revision")) is not int
                    or payload["expected_match_revision"] != current.get("revision")):
                raise ReviewConflictError("Control points or matching version changed; refresh the preview")
            parent_hash = _sha256_file(manifest_path)

            def prepare():
                refined, _ = _matching_preview(capture, manifest, parent_hash)
                version = f"r{refined['revision']}-{uuid.uuid4().hex}"
                version_dir = workspace / "matching" / version
                version_dir.mkdir(parents=True, exist_ok=False)
                profile_path = version_dir / "match-profile.json"
                # Exclusive files and directories preserve every earlier version.
                with profile_path.open("x", encoding="utf-8") as handle:
                    json.dump(refined, handle, ensure_ascii=False, indent=2, allow_nan=False)
                with (version_dir / "controls.json").open("x", encoding="utf-8") as handle:
                    json.dump(capture, handle, ensure_ascii=False, indent=2, allow_nan=False)
                next_run = version_dir / "run"
                options = _matching_conversion_options(manifest, source_run_path, profile_path, next_run)
                return refined, profile_path, next_run, options

            refined, profile_path, next_run, options = await run_in_threadpool(prepare)
            response = {"status": "prepared", "matching": refined,
                "profile": {"path": str(profile_path), "sha256": _sha256_file(profile_path)},
                "next_run_dir": str(next_run), "conversion_command": _matching_command(options),
                "current_run_dir": str(run_path), "controls_sha256": capture["controls_sha256"],
                "absolute_accuracy_verified": False}
            if payload.get("execute", False):
                try:
                    await run_in_threadpool(_execute_matching_conversion, options)
                    candidate_path = next_run / "run_manifest.json"
                    candidate = _json_object(candidate_path)
                    candidate_match = _matching_record(candidate) or {}
                    if ((candidate.get("source") or {}).get("sha256") != source_hash
                            or candidate_match.get("match_sha256") != refined["match_sha256"]):
                        raise ReviewServerError("New result does not match the reviewed source and matching profile")
                    region_id = (manifest.get("delivery_partition") or {}).get("region_id")
                    candidate, view_dir = _matching_result_view(candidate, next_run, region_id)
                    candidate_delivery = _artifact_path(candidate, "delivery", run_dir=view_dir)
                    if _sha256_file(candidate_delivery) != candidate["artifacts"]["delivery"]["sha256"]:
                        raise ReviewServerError("New delivery SHA-256 mismatch")
                    provider = GeoPackageProvider(candidate_delivery)
                    provider.layers()  # Open and verify the new result before switching views.
                    _write_json_atomic(active_path, {"source_sha256": source_hash,
                        "session_id": session_id, "run_dir": str(view_dir), "source_run_dir": str(next_run),
                        "region_id": region_id,
                        "manifest_sha256": _sha256_file(candidate_path)})
                    manifest, run_path, source_run_path, manifest_path = candidate, view_dir, next_run, candidate_path
                    svg_manifest_sha256 = _sha256_file(candidate_path)
                    delivery = provider
                    app.state.run_dir = run_path
                    response.update(status="completed", current_run_dir=str(run_path))
                    await hub.broadcast({"type": "matching_completed", "run_dir": str(run_path)})
                except Exception as exc:
                    response.update(status="failed", error=str(exc), current_run_dir=str(run_path))
            return response
        except (ValueError, OSError) as exc:
            if isinstance(exc, ReviewServerError):
                raise
            raise ReviewServerError(str(exc)) from exc
        finally:
            matching_lock.release()

    @app.post("/api/registration/export")
    async def export_registration(request: Request):
        payload = await _request_object(request)
        if not isinstance(payload, dict):
            raise HTTPException(400, "Request body must be an object")
        activate = payload.get("activate", True)
        if not isinstance(activate, bool):
            raise HTTPException(400, "activate must be boolean")
        capture = _registration_controls(store, manifest)
        if (payload.get("expected_controls_sha256") is not None
                and payload["expected_controls_sha256"] != capture["controls_sha256"]):
            raise ReviewConflictError("Control points changed; refresh the preview before exporting")
        if activate and not capture["activation_ready"]:
            raise ReviewServerError(
                "Activation requires at least 4 distributed training controls "
                "and 3 distributed independent check controls"
            )
        profile_payload = _gcp_profile_payload(
            capture, manifest, enabled=activate,
        )
        profile_path = workspace / "web_gcp_profile.json"
        _write_json_atomic(profile_path, profile_payload)
        try:
            from .cad2gis_v3.calibration import GCPProfile

            profile = GCPProfile.load(
                profile_path,
                expected_source_sha256=profile_payload["source_sha256"],
            )
        except Exception as exc:
            raise ReviewServerError(
                f"Generated GCP profile failed canonical validation: {exc}"
            ) from exc

        profiles = manifest.get("profiles")
        source_profile_record = (
            profiles.get("source_profile")
            if isinstance(profiles, dict) else None
        )
        mapping_record = (
            profiles.get("mapping_registry")
            if isinstance(profiles, dict) else None
        )
        source_profile_path = (
            Path(str(source_profile_record.get("path"))).expanduser().resolve()
            if isinstance(source_profile_record, dict)
            and source_profile_record.get("path")
            else None
        )
        mapping_path = (
            Path(str(mapping_record.get("path"))).expanduser().resolve()
            if isinstance(mapping_record, dict)
            and mapping_record.get("path")
            else None
        )
        generated_source_profile = None
        if activate and source_profile_path is not None and source_profile_path.is_file():
            expected_source_profile_sha = str(source_profile_record.get("sha256", ""))
            if _sha256_file(source_profile_path) != expected_source_profile_sha:
                raise ReviewServerError("Bound source profile SHA-256 mismatch; reload the original run")
            source_profile_payload = _json_object(source_profile_path)
            # Editing reference targets is not a review of looser engineering
            # coverage thresholds. Preserve every bound policy unchanged.
            generated_source_profile = workspace / "web_source_profile.json"
            _write_json_atomic(
                generated_source_profile, source_profile_payload,
            )
            try:
                from .cad2gis_v3.config import SourceProfile

                SourceProfile.load(generated_source_profile)
            except Exception as exc:
                raise ReviewServerError(
                    f"Generated source profile failed canonical validation: {exc}"
                ) from exc

        source_path, source_blocker = _source_path(
            manifest, run_dir=source_run_path,
        )
        next_run = workspace / "registered-run"
        command_parts = (
            [
                "cad2gis convert",
                f'"{source_path}"',
                f'--run-dir "{next_run}"',
                f'--gcp-profile "{profile_path}"',
            ]
            if source_path is not None else []
        )
        # The registered re-run must reproduce the reviewed source run's
        # supervision mode.  The web workflow edits coordinates only; it must
        # not silently downgrade an ``--llm assist`` source run to the CLI
        # default ``off`` (a different denoising path can drop PTECH/CABLE).
        source_modes = manifest.get("modes")
        source_modes = dict(source_modes) if isinstance(source_modes, Mapping) else {}
        inherited_llm = str(source_modes.get("llm", "") or "").strip().casefold()
        inherited_domain = str(source_modes.get("domain", "") or "").strip().casefold()
        if inherited_llm not in {"off", "observe", "assist"}:
            inherited_llm = ""
        if inherited_domain not in {"auto", "generic", "ftth_apd"}:
            inherited_domain = ""
        if inherited_llm:
            command_parts.append(f"--llm {inherited_llm}")
        if inherited_domain:
            command_parts.append(f"--domain {inherited_domain}")
        if generated_source_profile is not None:
            command_parts.append(
                f'--source-profile "{generated_source_profile}"'
            )
        if mapping_path is not None:
            command_parts.append(f'--mapping-registry "{mapping_path}"')
        registered_delivery = next_run / "delivery.gpkg"
        return {
            **capture,
            "profile": {
                "path": str(profile.path),
                "sha256": profile.sha256,
                "enabled": profile.enabled,
            },
            "source_profile": (
                str(generated_source_profile)
                if generated_source_profile is not None else None
            ),
            "source_run_modes": source_modes,
            "coverage_policy": {
                "preserved_from_source_profile": True,
                "final_conversion_enforces_bound_coverage": True,
                "preflight": "4 distinct non-collinear training controls and 3 distinct held-out checks",
            },
            "next_run_dir": str(next_run),
            "registered_delivery": (
                str(registered_delivery)
                if registered_delivery.is_file() else None
            ),
            "source_available": source_path is not None,
            "source_resolved_path": (
                str(source_path) if source_path is not None else None
            ),
            "source_blocker": source_blocker,
            "conversion_command": (
                " ".join(command_parts) if source_path is not None else None
            ),
            "warning": (
                "OSM controls improve relative placement only; absolute "
                "survey accuracy remains unverified. The web preview layers "
                "always show the pre-registration run; open next_run_dir/"
                "delivery.gpkg in QGIS to inspect the corrected output."
            ),
        }

    @app.get("/api/layers")
    async def layers():
        return {"layers": delivery.layers()}

    @app.get("/api/layers/{layer_name}/geojson")
    async def layer_geojson(layer_name: str, limit: int = 100_000):
        return delivery.geojson(layer_name, limit=limit)

    @app.get("/api/layers/{layer_name}/local-geojson")
    async def layer_local_geojson(layer_name: str, limit: int = 100_000):
        """Return immutable drawing coordinates without a nominal CRS lie.

        The local pane is the authoritative place for choosing the CAD side
        of a GCP pair when coordinate-domain validation has failed.
        """

        return delivery.geojson(
            layer_name, limit=limit, native_coordinates=True,
        )

    @app.get("/api/review/features")
    async def review_features():
        return store.feature_collection()

    @app.get("/api/review/events")
    async def review_events(after: str = "", limit: int = 200):
        return {
            "schema_version": REVIEW_SCHEMA,
            "events": store.events(after=after, limit=limit),
        }

    @app.post("/api/review/features")
    async def upsert_review_feature(request: Request):
        payload = await _request_object(request)
        if not isinstance(payload, dict):
            raise HTTPException(400, "Request body must be an object")
        expected = payload.get("expected_revision")
        if expected is not None and (
            isinstance(expected, bool) or not isinstance(expected, int) or expected < 0
        ):
            raise HTTPException(400, "expected_revision must be a non-negative integer")
        if matching_lock.locked():
            raise ReviewConflictError("Matching is running; wait before changing control points")
        event = store.upsert(
            payload.get("feature"),
            expected_revision=expected,
            actor=str(payload.get("actor", "web-review")),
        )
        await hub.broadcast({"type": "review_event", "event": event})
        return event

    @app.post("/api/review/features/{feature_id}/adjust")
    async def adjust_review_control(feature_id: str, request: Request):
        payload = await _request_object(request)
        if not isinstance(payload, dict):
            raise HTTPException(400, "Request body must be an object")
        expected = payload.get("expected_revision")
        _expected_revision(expected, required=True)
        if matching_lock.locked():
            raise ReviewConflictError("Matching is running; wait before changing control points")
        feature = next((item for item in store.feature_collection()["features"]
                        if item["id"] == feature_id), None)
        if feature is None:
            raise HTTPException(404, "GCP not found; reload the control points")
        event = store.upsert(
            feature, expected_revision=expected,
            actor=str(payload.get("actor", "registration-workspace")),
            gcp_action=payload,
        )
        await hub.broadcast({"type": "review_event", "event": event})
        return event

    @app.delete("/api/review/features/{feature_id}")
    async def delete_review_feature(
        feature_id: str,
        expected_revision: int | None = None,
        actor: str = "web-review",
    ):
        if matching_lock.locked():
            raise ReviewConflictError("Matching is running; wait before changing control points")
        event = store.delete(
            feature_id,
            expected_revision=expected_revision,
            actor=actor,
        )
        await hub.broadcast({"type": "review_event", "event": event})
        return event

    @app.get("/api/visual/{relative_path:path}")
    async def visual_file(relative_path: str):
        visual_root = (run_path / "reasoning" / "visual").resolve()
        path = (visual_root / relative_path).resolve()
        if visual_root not in path.parents or not path.is_file():
            raise HTTPException(404, "Visual artifact not found")
        return FileResponse(path)

    @app.get("/api/svg-candidates/{run_sha256}/{relative_path:path}")
    async def svg_candidate_file(run_sha256: str, relative_path: str):
        if run_sha256 != svg_manifest_sha256 or svg_manifest_sha256 != _sha256_file(manifest_path):
            raise HTTPException(409, "SVG report belongs to a different run; reload the current result")
        try:
            path = _svg_candidate_path(manifest, source_run_path, relative_path)
        except ReviewServerError as exc:
            raise HTTPException(404, str(exc)) from exc
        return FileResponse(path, headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; sandbox allow-same-origin allow-downloads",
        })

    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket):
        await hub.connect(websocket)
        try:
            await websocket.send_json({
                "type": "connected",
                "session_id": session_id,
            })
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            hub.disconnect(websocket)

    app.state.run_dir = run_path
    app.state.workspace_dir = workspace
    app.state.review_store = store
    return app


def run_review_server(
    run_dir: str | Path,
    *,
    workspace_dir: str | Path | None = None,
    host: str = "127.0.0.1",
    port: int = 8765,
    qgis_server_url: str = "",
    qgis_project: str = "",
    qgis_layers: str = "",
    postgis_dsn: str | None = None,
) -> None:
    """Run the local review server until interrupted."""

    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ReviewServerError(
            "The bundled review server is local-only; use a reverse proxy with "
            "authentication for network exposure"
        )
    if isinstance(port, bool) or not 1 <= int(port) <= 65535:
        raise ReviewServerError("port must be between 1 and 65535")
    try:
        import uvicorn
    except ImportError as exc:  # pragma: no cover
        raise ReviewServerError(
            "Review dependencies are missing; install cad2gis[review]"
        ) from exc
    app = create_review_app(
        run_dir,
        workspace_dir=workspace_dir,
        qgis_server_url=qgis_server_url,
        qgis_project=qgis_project,
        qgis_layers=qgis_layers,
        postgis_dsn=postgis_dsn,
    )
    uvicorn.run(app, host=host, port=int(port), log_level="info")


__all__ = [
    "REVIEW_SCHEMA",
    "GeoPackageProvider",
    "PostGISReviewStore",
    "ReviewConflictError",
    "ReviewServerError",
    "SQLiteReviewStore",
    "create_review_app",
    "run_review_server",
]
