"""Real-time web review surface for immutable CAD2GIS run artifacts.

The review workspace is deliberately separate from the conversion run.  Edits
are optimistic, revisioned GeoJSON overlays and never rewrite source,
evidence, or delivery GeoPackages.
"""

import json
import math
import os
import re
import sqlite3
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping


REVIEW_SCHEMA = "cad2gis.review_workspace.v1"
_FEATURE_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,256}$")


class ReviewServerError(ValueError):
    """Review configuration or an edit payload violates the contract."""


class ReviewConflictError(ReviewServerError):
    """Optimistic revision does not match the current feature revision."""


def _json_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ReviewServerError(f"JSON root must be an object: {path}")
    return payload


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
        lon, lat = float(value[0]), float(value[1])
        if not math.isfinite(lon) or not math.isfinite(lat):
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
    encoded = json.dumps(properties, ensure_ascii=False, allow_nan=False)
    if len(encoded.encode("utf-8")) > 256 * 1024:
        raise ReviewServerError("Feature properties exceed 256 KiB")
    return {
        "type": "Feature",
        "id": feature_id,
        "geometry": geometry,
        "properties": properties,
    }


class SQLiteReviewStore:
    """Revisioned local review overlay suitable for the bundled demo."""

    def __init__(self, path: str | Path, *, session_id: str):
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.session_id = str(session_id)
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

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
    ) -> dict[str, Any]:
        normalized = _normalized_feature(feature)
        feature_id = normalized["id"]
        now = _utc_now()
        event_id = uuid.uuid4().hex
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                """
                SELECT revision FROM review_features
                WHERE session_id = ? AND feature_id = ?
                """,
                (self.session_id, feature_id),
            ).fetchone()
            current_revision = 0 if current is None else int(current["revision"])
            if expected_revision is not None and expected_revision != current_revision:
                raise ReviewConflictError(
                    f"Feature {feature_id} revision is {current_revision}, "
                    f"not {expected_revision}"
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
                SELECT revision FROM review_features
                WHERE session_id = ? AND feature_id = ?
                """,
                (self.session_id, feature_id),
            ).fetchone()
            if current is None:
                raise ReviewServerError(f"Unknown review feature: {feature_id}")
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
                SELECT feature_id, revision, ST_AsGeoJSON(geometry)::jsonb AS geometry,
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
    ) -> dict[str, Any]:
        normalized = _normalized_feature(feature)
        feature_id = normalized["id"]
        event_id = uuid.uuid4()
        properties = {
            **normalized["properties"],
            "_review_actor": str(actor or "anonymous"),
        }
        stored = {**normalized, "properties": properties}
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT revision FROM cad2gis_review.features
                WHERE session_id = %s AND feature_id = %s
                FOR UPDATE
                """,
                (self.session_id, feature_id),
            )
            current = cursor.fetchone()
            current_revision = 0 if current is None else int(current["revision"])
            if expected_revision is not None and expected_revision != current_revision:
                raise ReviewConflictError(
                    f"Feature {feature_id} revision is {current_revision}, "
                    f"not {expected_revision}"
                )
            revision = current_revision + 1
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
            cursor.execute(
                """
                SELECT revision FROM cad2gis_review.features
                WHERE session_id = %s AND feature_id = %s
                FOR UPDATE
                """,
                (self.session_id, feature_id),
            )
            current = cursor.fetchone()
            if current is None:
                raise ReviewServerError(f"Unknown review feature: {feature_id}")
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
        path = str(evidence.get("path") or "")
        return Path(path).expanduser().resolve() if path else None

    def _native_point_lookup(self) -> dict[str, Any] | None:
        """Map source entity keys to CAD-native point sequences."""
        evidence = self._evidence_path()
        if evidence is None or not evidence.is_file():
            return None
        try:
            import sqlite3
            connection = sqlite3.connect(str(evidence))
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
                lookup: dict[str, Any] = {}
                for entity_key, native_points, cad_role in cursor.execute(
                    "SELECT entity_key, native_points, cad_role "
                    "FROM cad_entities"
                ):
                    if not native_points or cad_role != "model":
                        continue
                    lookup[entity_key] = native_points
                return lookup
            finally:
                connection.close()
        except Exception:
            return None

    def _geometry_from_native_points(
        self,
        native_lookup: dict[str, Any] | None,
        properties: dict[str, Any],
        delivery_geometry: Any,
    ) -> Any | None:
        """Rebuild the CAD-native geometry for a delivery feature.

        Point/lines/polygons are reconstructed from the immutable
        ``native_points`` JSON of the evidence ledger; the geometry type is
        taken from the delivery feature so the layer contract is preserved.
        """
        if not native_lookup:
            return None
        entity_key = str(properties.get("source_entity_key") or "")
        native_points = native_lookup.get(entity_key)
        if not native_points:
            return None
        try:
            from osgeo import ogr
            points = json.loads(native_points)
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
                        native_lookup, properties, geometry,
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


def _artifact_path(manifest: dict[str, Any], name: str) -> Path:
    artifacts = manifest.get("artifacts")
    record = artifacts.get(name) if isinstance(artifacts, dict) else None
    raw_path = record.get("path") if isinstance(record, dict) else None
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise ReviewServerError(f"Run manifest has no {name} artifact")
    return Path(raw_path).expanduser().resolve()


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
        coordinates = geometry.get("coordinates")
        if not isinstance(coordinates, list) or len(coordinates) < 2:
            continue
        lon, lat = float(coordinates[0]), float(coordinates[1])
        easting, northing = transformer.transform(lon, lat)
        values = (
            float(properties.get("cad_x")),
            float(properties.get("cad_y")),
            float(easting),
            float(northing),
        )
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
            "accuracy_m": 8.0,
            "weight": 1.0,
            "enabled": True,
            "revision": int(properties.get("_review_revision", 0)),
        })
    compiled.sort(key=lambda item: item["point_id"])
    train = [item for item in compiled if item["role"] == "train"]
    check = [item for item in compiled if item["role"] == "check"]

    def spans_both_axes(values: list[dict[str, Any]]) -> bool:
        if len(values) < 2:
            return False
        return (
            max(item["cad_x"] for item in values)
            - min(item["cad_x"] for item in values)
            > 1e-6
            and max(item["cad_y"] for item in values)
            - min(item["cad_y"] for item in values)
            > 1e-6
        )

    ready = (
        len(train) >= 4
        and len(check) >= 3
        and spans_both_axes(train)
        and spans_both_axes(check)
    )
    return {
        "schema_version": "cad2gis.web_registration_capture.v1",
        "source_crs": source_crs,
        "target_crs": target_crs,
        "controls": compiled,
        "train_count": len(train),
        "check_count": len(check),
        "minimum_train_count": 4,
        "minimum_check_count": 3,
        "distribution_gate_passed": ready,
        "activation_ready": ready,
        "accuracy_class": "RELATIVE_OSM_REFERENCE_ONLY",
        "absolute_accuracy_verified": False,
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
        from fastapi.responses import FileResponse
        from fastapi.staticfiles import StaticFiles
        from starlette.websockets import WebSocketDisconnect
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise ReviewServerError(
            "Review dependencies are missing; install cad2gis[review]"
        ) from exc

    run_path = Path(run_dir).expanduser().resolve()
    manifest_path = run_path / "run_manifest.json"
    if not manifest_path.is_file():
        raise ReviewServerError(f"Run manifest does not exist: {manifest_path}")
    manifest = _json_object(manifest_path)
    delivery = GeoPackageProvider(_artifact_path(manifest, "delivery"))
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
    hub = _WebSocketHub()
    web_root = Path(__file__).resolve().parent / "webdemo"
    if not (web_root / "index.html").is_file():
        raise ReviewServerError(f"Review web assets are missing: {web_root}")

    app = FastAPI(title="CAD2GIS Review", version="1")
    app.mount("/assets", StaticFiles(directory=web_root), name="assets")

    @app.exception_handler(ReviewServerError)
    async def review_error_handler(_request: Request, exc: ReviewServerError):
        from fastapi.responses import JSONResponse

        status = 409 if isinstance(exc, ReviewConflictError) else 400
        return JSONResponse(status_code=status, content={"detail": str(exc)})

    @app.get("/")
    async def index():
        return FileResponse(web_root / "index.html")

    @app.get("/api/health")
    async def health():
        return {
            "status": "ok",
            "schema_version": REVIEW_SCHEMA,
            "session_id": session_id,
        }

    @app.get("/api/run")
    async def run_summary():
        return {
            "schema_version": manifest.get("schema_version"),
            "run_dir": str(run_path),
            "workspace_dir": str(workspace),
            "run_status": manifest.get("run_status"),
            "source": manifest.get("source"),
            "crs": manifest.get("crs"),
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
        return _registration_controls(store, manifest)

    @app.post("/api/registration/export")
    async def export_registration(request: Request):
        payload = await request.json()
        if not isinstance(payload, dict):
            raise HTTPException(400, "Request body must be an object")
        activate = payload.get("activate", True)
        if not isinstance(activate, bool):
            raise HTTPException(400, "activate must be boolean")
        capture = _registration_controls(store, manifest)
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
            source_profile_payload = _json_object(source_profile_path)
            # OSM visual references are coarse and drawing geometry is
            # often concentrated in part of the sheet (e.g. poles cluster
            # in one area while the rest is residential labels, which are
            # unreliable on OSM).  Web registration therefore relaxes the
            # spatial-coverage gates: controls must cover the populated
            # region, not a uniform sheet rectangle.  Translation-only
            # fitting and the strict rotation gate keep the result stable.
            source_profile_payload["spatial_coverage_policy"] = {
                "min_training_extent_x_ratio": 0.20,
                "min_training_extent_y_ratio": 0.20,
                "min_training_hull_area_ratio": 0.04,
                "max_drawing_vertices_outside_training_bbox_ratio": 0.55,
                "min_check_baseline_to_drawing_diagonal_ratio": 0.05,
                "min_check_hull_area_ratio": 0.01,
                "max_drawing_vertices_outside_training_hull_ratio": 0.70,
            }
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

        source = manifest.get("source")
        source_path = (
            str(source.get("path", ""))
            if isinstance(source, dict) else ""
        )
        next_run = workspace / "registered-run"
        command_parts = [
            "cad2gis convert",
            f'"{source_path}"',
            f'--run-dir "{next_run}"',
            f'--gcp-profile "{profile_path}"',
        ]
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
            "next_run_dir": str(next_run),
            "registered_delivery": (
                str(registered_delivery)
                if registered_delivery.is_file() else None
            ),
            "conversion_command": " ".join(command_parts),
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
        payload = await request.json()
        if not isinstance(payload, dict):
            raise HTTPException(400, "Request body must be an object")
        expected = payload.get("expected_revision")
        if expected is not None and (
            isinstance(expected, bool) or not isinstance(expected, int) or expected < 0
        ):
            raise HTTPException(400, "expected_revision must be a non-negative integer")
        event = store.upsert(
            payload.get("feature"),
            expected_revision=expected,
            actor=str(payload.get("actor", "web-review")),
        )
        await hub.broadcast({"type": "review_event", "event": event})
        return event

    @app.delete("/api/review/features/{feature_id}")
    async def delete_review_feature(
        feature_id: str,
        expected_revision: int | None = None,
        actor: str = "web-review",
    ):
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
