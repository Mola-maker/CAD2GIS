"""Canonical GeoPackage metadata shared by all v3 SQLite writers."""

from __future__ import annotations

import sqlite3


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


def normalize_geopackage_metadata(connection: sqlite3.Connection) -> None:
    """Replace writer-clock metadata using deterministic primary-key order.

    This deliberately touches only generated timestamps; feature, audit,
    extent, CRS, and style payloads remain unchanged.  ``VACUUM`` rebuilds the
    file after the transaction so superseded wall-clock values cannot remain
    in unused SQLite cells and make otherwise equal databases differ by byte.
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

    connection.execute("VACUUM")
