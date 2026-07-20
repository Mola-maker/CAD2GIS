"""Byte-level reproducibility contracts for v3 GeoPackage writers."""

from __future__ import annotations

import hashlib
import sqlite3
import time

from cad2gis_v3.evidence import write_evidence
from cad2gis_v3.georef import DirectTransformer
from cad2gis_v3.gpkg_metadata import CANONICAL_GPKG_TIMESTAMP
from cad2gis_v3.model import CadStyle, Feature, SourceEntity
from cad2gis_v3.styles import write_styles
from cad2gis_v3.warehouse import write_delivery


def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _assert_canonical_metadata(path, *, styled=False):
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert {
            row[0]
            for row in connection.execute(
                "SELECT last_change FROM gpkg_contents"
            )
        } == {CANONICAL_GPKG_TIMESTAMP}
        if styled:
            assert {
                row[0]
                for row in connection.execute(
                    "SELECT update_time FROM layer_styles"
                )
            } == {CANONICAL_GPKG_TIMESTAMP}


def _evidence_entity():
    return SourceEntity(
        entity_key="entity-1",
        source_sha256="source-sha256",
        source_file="source.dwg",
        handle="1",
        layout="Model",
        layout_role="model",
        cad_role="model",
        layer="ANNOTATION",
        object_name="ACDBTEXT",
        dwg_type="TEXT",
        points=((1.0, 2.0),),
        centroid=(1.0, 2.0),
        closed=False,
        text="source label",
        block_name="",
        block_attributes={},
        style=CadStyle(),
    )


def _delivery_feature(transformer):
    native_point = (13_681_914.403, 69_386.445)
    target_point = transformer.point(native_point)
    return Feature(
        feature_key="PTECH-1",
        feature_class="PTECH",
        geometry_kind="Point",
        native_points=[native_point],
        source_entity_key="entity-PTECH-1",
        source_handle="PTECH-1",
        source_layer="PTECH",
        geometry_role="SOURCE_ASSET",
        style=CadStyle(aci_color=3),
        attributes={
            "CODE": "PTECH-1",
            "X": target_point[0],
            "Y": target_point[1],
        },
        display_label="PTECH-1",
        label_provenance="DWG_DIRECT:test-fixture",
        lineage=[{"operation": "identity", "max_displacement_m": 0.0}],
    )


def test_delivery_and_embedded_styles_are_byte_reproducible(tmp_path):
    transformer = DirectTransformer("EPSG:3857", "EPSG:9481")
    features = [_delivery_feature(transformer)]
    deliveries = (tmp_path / "delivery-a.gpkg", tmp_path / "delivery-b.gpkg")

    write_delivery(deliveries[0], features, transformer)
    time.sleep(0.02)
    write_delivery(deliveries[1], features, transformer)

    assert _sha256(deliveries[0]) == _sha256(deliveries[1])
    for path in deliveries:
        _assert_canonical_metadata(path)

    write_styles(tmp_path / "styles-a", features, deliveries[0])
    time.sleep(0.02)
    write_styles(tmp_path / "styles-b", features, deliveries[1])

    assert _sha256(deliveries[0]) == _sha256(deliveries[1])
    for path in deliveries:
        _assert_canonical_metadata(path, styled=True)


def test_evidence_is_byte_reproducible(tmp_path):
    source_srs = DirectTransformer("EPSG:3857", "EPSG:9481").source
    outputs = (tmp_path / "evidence-a.gpkg", tmp_path / "evidence-b.gpkg")
    arguments = ([_evidence_entity()], [], [], [], {}, source_srs)

    write_evidence(outputs[0], *arguments)
    time.sleep(0.02)
    write_evidence(outputs[1], *arguments)

    assert _sha256(outputs[0]) == _sha256(outputs[1])
    for path in outputs:
        _assert_canonical_metadata(path)
