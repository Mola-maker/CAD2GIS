from __future__ import annotations

import json
import math
import sqlite3
from hashlib import sha256

import pytest

import cad2gis.cad2gis_v3.source_gpkg as source_gpkg
from cad2gis.cad2gis_v3.model import CURVE_FACTS_SCHEMA, SourceEntity
from cad2gis.cad2gis_v3.source_gpkg import write_source_gpkg


@pytest.fixture
def sample_entities() -> list[SourceEntity]:
    common = {
        "source_sha256": "a" * 64,
        "source_file": "fixture.dwg",
        "layout": "Model",
        "layout_role": "model",
        "cad_role": "model",
        "layer": "SOURCE",
        "block_attributes": {},
        "raw_properties": {"extraction_backend": "fixture"},
    }

    def entity(key: str, kind: str, points, **overrides) -> SourceEntity:
        record = {
            **common,
            "entity_key": key,
            "handle": key.upper(),
            "object_name": f"AcDb{kind.title()}",
            "dwg_type_name": kind,
            "points": points,
            "centroid": points[0] if points else (0.0, 0.0),
            **overrides,
        }
        return SourceEntity.from_record(record)

    curve_facts = {
        "schema_version": CURVE_FACTS_SCHEMA,
        "coordinate_system": "WCS",
        "primitive_type": "LWPOLYLINE",
        "vertices_wcs": [[0.0, 0.0, 0.0], [2.0, 0.0, 0.0]],
        "bulges": [0.25, 0.0],
        "closed": False,
        "native_length": 2.1,
        "native_length_source": "reader",
    }
    return [
        entity("point-1", "POINT", [(4.0, 5.0)]),
        entity(
            "line-1",
            "LWPOLYLINE",
            [(0.0, 0.0), (2.0, 0.0)],
            native_length=2.1,
            curve_facts=curve_facts,
            aci_color=3,
            true_color="00FF00",
            linetype="DASHED",
            lineweight=35,
            raw_properties={
                "extraction_backend": "fixture",
                "custom": {"z": 2, "a": 1},
            },
        ),
        entity(
            "polygon-1",
            "LWPOLYLINE",
            [(0.0, 0.0), (3.0, 0.0), (3.0, 2.0), (0.0, 2.0), (0.0, 0.0)],
            closed=True,
        ),
        entity("text-1", "TEXT", [(8.0, 9.0)], text="immutable label"),
        entity(
            "block-1",
            "INSERT",
            [(10.0, 11.0)],
            block_name="CABINET",
            block_attributes={"TAG": "B-1"},
        ),
        entity("metadata-1", "IMAGE", []),
        entity(
            "invalid-line-1",
            "LINE",
            [(1.0, 1.0)],
            raw_properties={
                "extraction_backend": "fixture",
                "reader_backend_status": "Failure: truncated geometry",
            },
        ),
    ]


def test_source_gpkg_accounts_for_every_entity(tmp_path, sample_entities):
    output = tmp_path / "source.gpkg"
    result = write_source_gpkg(output, sample_entities, "EPSG:3857")
    with sqlite3.connect(output) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute(
            "SELECT COUNT(*) FROM source_entity_accounting"
        ).fetchone()[0] == len(sample_entities)
        assert connection.execute(
            "SELECT SUM(entity_count) FROM source_conservation_ledger"
        ).fetchone()[0] == len(sample_entities)
        materialized_count = sum(
            connection.execute(f'SELECT COUNT(*) FROM "{layer_name}"').fetchone()[0]
            for layer_name in (
                "source_points",
                "source_lines",
                "source_polygons",
                "source_text",
                "source_blocks",
                "source_metadata",
            )
        )
        assert materialized_count == len(sample_entities)
        assert connection.execute(
            "SELECT COUNT(DISTINCT entity_key) FROM source_entity_accounting"
        ).fetchone()[0] == len(sample_entities)
    assert result.entity_count == len(sample_entities)


def test_source_gpkg_allows_explicitly_unregistered_cad_coordinates(
    tmp_path, sample_entities
):
    output = tmp_path / "source.gpkg"
    result = write_source_gpkg(output, sample_entities, None)
    with sqlite3.connect(output) as connection:
        srs_ids = {
            row[0]
            for row in connection.execute(
                "SELECT DISTINCT srs_id FROM gpkg_geometry_columns"
            )
        }
        assert len(srs_ids) == 1
        srs_id = next(iter(srs_ids))
        definition = connection.execute(
            "SELECT definition FROM gpkg_spatial_ref_sys WHERE srs_id = ?",
            (srs_id,),
        ).fetchone()[0]
        assert "Undefined SRS" in definition
    assert result.entity_count == len(sample_entities)


def test_source_gpkg_materializes_stable_layers_and_retains_facts(
    tmp_path, sample_entities
):
    output = tmp_path / "source.gpkg"
    result = write_source_gpkg(output, sample_entities, "EPSG:3857")

    with sqlite3.connect(output) as connection:
        connection.row_factory = sqlite3.Row
        contents = {
            row["table_name"]: row["data_type"]
            for row in connection.execute(
                "SELECT table_name, data_type FROM gpkg_contents"
            )
        }
        assert contents == {
            "source_blocks": "features",
            "source_lines": "features",
            "source_metadata": "attributes",
            "source_points": "features",
            "source_polygons": "features",
            "source_text": "features",
        }
        ledger = dict(
            connection.execute(
                "SELECT materialization_layer, entity_count "
                "FROM source_conservation_ledger ORDER BY materialization_layer"
            )
        )
        assert ledger == {
            "source_blocks": 1,
            "source_lines": 1,
            "source_metadata": 2,
            "source_points": 1,
            "source_polygons": 1,
            "source_text": 1,
        }
        metadata_keys = {
            row[0]
            for row in connection.execute(
                "SELECT entity_key FROM source_metadata ORDER BY entity_key"
            )
        }
        assert metadata_keys == {"invalid-line-1", "metadata-1"}
        line = connection.execute(
            "SELECT * FROM source_lines WHERE entity_key='line-1'"
        ).fetchone()
        assert json.loads(line["native_points"]) == [[0.0, 0.0], [2.0, 0.0]]
        assert line["native_length"] == pytest.approx(2.1)
        assert json.loads(line["raw_properties"]) == {
            "custom": {"a": 1, "z": 2},
            "extraction_backend": "fixture",
        }
        assert json.loads(line["curve_facts"])["bulges"] == [0.25, 0.0]
        assert line["curve_fingerprint"] == sample_entities[1].curve_fingerprint
        assert (
            line["aci_color"],
            line["true_color"],
            line["linetype"],
            line["lineweight"],
        ) == (3, "00FF00", "DASHED", 35)
        accounting = connection.execute(
            "SELECT materialization_layer, terminal_state, terminal_reasons "
            "FROM source_entity_accounting WHERE entity_key='invalid-line-1'"
        ).fetchone()
        assert tuple(accounting[:2]) == ("source_metadata", "errored")
        assert json.loads(accounting["terminal_reasons"]) == [
            "Failure: truncated geometry"
        ]

    assert result.path == output.resolve()
    assert result.layer_counts == ledger
    assert result.byte_sha256 == sha256(output.read_bytes()).hexdigest()
    assert len(result.logical_sha256) == 64


def test_source_gpkg_is_byte_deterministic(tmp_path, sample_entities):
    first = tmp_path / "first.gpkg"
    second = tmp_path / "second.gpkg"
    first_result = write_source_gpkg(first, sample_entities, "EPSG:3857")
    second_result = write_source_gpkg(
        second, list(reversed(sample_entities)), "EPSG:3857"
    )
    assert (
        sha256(first.read_bytes()).hexdigest()
        == sha256(second.read_bytes()).hexdigest()
    )
    assert first_result.byte_sha256 == second_result.byte_sha256
    assert first_result.logical_sha256 == second_result.logical_sha256


def test_closed_circle_stays_in_line_layer(tmp_path, sample_entities):
    circle = SourceEntity.from_record(
        {
            "entity_key": "closed-circle",
            "source_sha256": "a" * 64,
            "source_file": "fixture.dwg",
            "handle": "CIRCLE-1",
            "layout": "Model",
            "layout_role": "model",
            "cad_role": "model",
            "layer": "SOURCE",
            "object_name": "AcDbCircle",
            "dwg_type_name": "CIRCLE",
            "points": [(0.0, 0.0), (1.0, 0.0), (0.0, 1.0), (0.0, 0.0)],
            "centroid": (0.0, 0.0),
            "closed": True,
        }
    )
    output = tmp_path / "closed-circle.gpkg"
    write_source_gpkg(output, [*sample_entities[:1], circle], "EPSG:3857")

    with sqlite3.connect(output) as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM source_lines WHERE entity_key='closed-circle'"
            ).fetchone()[0]
            == 1
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM source_polygons WHERE entity_key='closed-circle'"
            ).fetchone()[0]
            == 0
        )


def test_nonfinite_source_facts_are_strictly_encoded_in_metadata(tmp_path):
    entity = SourceEntity.from_record(
        {
            "entity_key": "nonfinite-source",
            "source_sha256": "a" * 64,
            "source_file": "fixture.dwg",
            "handle": "NONFINITE-1",
            "layout": "Model",
            "layout_role": "model",
            "cad_role": "model",
            "layer": "SOURCE",
            "object_name": "AcDbLine",
            "dwg_type_name": "LINE",
            "points": [(math.nan, 2.0)],
            "centroid": (math.inf, -math.inf),
        }
    )
    output = tmp_path / "nonfinite-source.gpkg"
    write_source_gpkg(output, [entity], "EPSG:3857")

    def reject_constant(token):
        raise AssertionError(f"non-standard JSON constant: {token}")

    with sqlite3.connect(output) as connection:
        row = connection.execute(
            "SELECT native_points, native_centroid FROM source_metadata "
            "WHERE entity_key='nonfinite-source'"
        ).fetchone()
        assert row is not None
        assert json.loads(row[0], parse_constant=reject_constant) == [["NaN", 2.0]]
        assert json.loads(row[1], parse_constant=reject_constant) == [
            "Infinity",
            "-Infinity",
        ]


def test_post_staging_validation_failure_is_atomic(
    tmp_path, sample_entities, monkeypatch
):
    output = tmp_path / "source.gpkg"
    write_source_gpkg(output, sample_entities, "EPSG:3857")
    original = output.read_bytes()

    def fail_validation(*args, **kwargs):
        raise RuntimeError("forced validation failure")

    monkeypatch.setattr(source_gpkg, "_validate_staged", fail_validation)
    with pytest.raises(RuntimeError, match="forced validation failure"):
        source_gpkg.write_source_gpkg(output, sample_entities, "EPSG:3857")

    assert output.read_bytes() == original
    assert list(tmp_path.glob(".source.gpkg.*.tmp.gpkg")) == []


def test_source_gpkg_failure_does_not_replace_destination(tmp_path, sample_entities):
    output = tmp_path / "source.gpkg"
    write_source_gpkg(output, sample_entities, "EPSG:3857")
    original = output.read_bytes()

    with pytest.raises(ValueError, match="duplicate source entity key: point-1"):
        write_source_gpkg(
            output,
            [sample_entities[0], sample_entities[0]],
            "EPSG:3857",
        )

    assert output.read_bytes() == original
