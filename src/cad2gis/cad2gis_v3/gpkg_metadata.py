"""Canonical GeoPackage metadata shared by all v3 SQLite writers."""

from __future__ import annotations

import os
from pathlib import Path
import sqlite3
import tempfile


# GeoPackage ``last_change`` values use the ISO 8601 UTC representation below.
# A fixed epoch makes metadata byte-stable without changing feature or audit
# semantics.  Keep the millisecond component because that is the form emitted
# by GDAL's GeoPackage driver and by the QGIS ``layer_styles`` convention.
CANONICAL_GPKG_TIMESTAMP = "1970-01-01T00:00:00.000Z"


def _table_columns(connection: sqlite3.Connection, table_name: str) -> set[str]:
    return {
        str(row[1])
        for row in connection.execute(f'PRAGMA table_info("{table_name}")')
    }


def normalize_geopackage_metadata(
    connection: sqlite3.Connection,
    contents_order: list[str] | tuple[str, ...] | None = None,
    *, vacuum: bool = True,
) -> None:
    """Replace writer-clock metadata using deterministic primary-key order.

    This deliberately touches only generated timestamps and (optionally) the
    scan order of ``gpkg_contents``; feature, audit, extent, CRS, and style
    payloads remain unchanged.  ``VACUUM`` rebuilds the file after the
    transaction so superseded wall-clock values cannot remain in unused SQLite
    cells and make otherwise equal databases differ by byte.
    """
    if connection.in_transaction:
        raise RuntimeError(
            "GeoPackage metadata normalization requires a committed database"
        )
    contents_columns = _table_columns(connection, "gpkg_contents")
    if not {"table_name", "last_change"}.issubset(contents_columns):
        raise RuntimeError("GeoPackage lacks required gpkg_contents metadata")

    style_columns = _table_columns(connection, "layer_styles")
    if style_columns and not {"id", "update_time"}.issubset(style_columns):
        raise RuntimeError("layer_styles lacks deterministic timestamp metadata")

    with connection:
        content_names = [
            str(row[0])
            for row in connection.execute(
                "SELECT table_name FROM gpkg_contents "
                "ORDER BY table_name COLLATE BINARY"
            )
        ]
        connection.executemany(
            "UPDATE gpkg_contents SET last_change=? WHERE table_name=?",
            (
                (CANONICAL_GPKG_TIMESTAMP, table_name)
                for table_name in content_names
            ),
        )

        if style_columns:
            style_ids = [
                int(row[0])
                for row in connection.execute(
                    "SELECT id FROM layer_styles ORDER BY id"
                )
            ]
            connection.executemany(
                "UPDATE layer_styles SET update_time=? WHERE id=?",
                (
                    (CANONICAL_GPKG_TIMESTAMP, style_id)
                    for style_id in style_ids
                ),
            )

        if contents_order is not None:
            # GDAL registers an empty layer in ``gpkg_contents`` only once its
            # first feature is written, which can move zero-feature layers to
            # the end.  Rewrite the rowid order explicitly so the default
            # GeoPackage scan (and QGIS's layer order) matches the reviewed
            # legend.  The temporary high-water offset avoids rowid conflicts.
            ordered = [str(name) for name in contents_order]
            remaining = [name for name in content_names if name not in ordered]
            desired = ordered + remaining
            offset = max(len(desired), 1) + 1000
            existing_names = [
                str(row[0])
                for row in connection.execute(
                    "SELECT table_name FROM gpkg_contents ORDER BY rowid"
                ).fetchall()
            ]
            for index, table_name in enumerate(existing_names):
                connection.execute(
                    "UPDATE gpkg_contents SET rowid=? WHERE table_name=?",
                    (offset + index, table_name),
                )
            for index, table_name in enumerate(desired, start=1):
                connection.execute(
                    "UPDATE gpkg_contents SET rowid=? WHERE table_name=?",
                    (index, table_name),
                )

    if vacuum:
        connection.execute("VACUUM")


def compact_geopackage(path: Path) -> None:
    """Compact a closed, staged database using space on its destination drive.

    VACUUM INTO avoids an equally large transient database on the system TEMP
    drive. The original survives any failure; replacement occurs only after
    SQLite closes and validates the compacted file. No global temp setting is
    changed, so independent conversions can use different output volumes.
    """
    path = Path(path).resolve()
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".compact", dir=path.parent)
    os.close(descriptor)
    compacted = Path(name)
    try:
        connection = sqlite3.connect(path)
        try:
            connection.execute("VACUUM INTO ?", (str(compacted),))
        finally:
            connection.close()
        connection = sqlite3.connect(compacted)
        try:
            if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("Compacted GeoPackage failed its integrity check")
        finally:
            connection.close()
        with compacted.open("r+b") as stream:
            os.fsync(stream.fileno())
        os.chmod(compacted, path.stat().st_mode)
        os.replace(compacted, path)
    finally:
        compacted.unlink(missing_ok=True)
