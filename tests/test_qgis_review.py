import copy
import math
import sqlite3
import hashlib
import json

import pytest

from cad2gis.qgis_review import (
    _evidence_controls,
    coordinate_pair,
    fit_translation,
    sha,
    _matching_context,
    _review_title,
)


def test_matching_provenance_survives_without_display_correction():
    record = {"schema_version": "cad2gis.osm-matching.v1", "status": "matched", "revision": 2,
              "source_sha256": "a" * 64, "authority": "relative_osm", "absolute_accuracy_verified": False}
    record["match_sha256"] = hashlib.sha256(json.dumps(record, sort_keys=True, separators=(",", ":"),
                                                       ensure_ascii=False).encode()).hexdigest()
    context = {"schema": "cad2gis.qgis-matching-context.v1", "delivery_sha256": "b" * 64,
               "source_sha256": "a" * 64, "matching": record}
    assert _matching_context(context, ["b" * 64]) == context
    assert "OSM matched" in _review_title(None, context)
    assert "nominal" not in _review_title(None, context)
    assert "not supplied" in _review_title(None, None)
    with pytest.raises(ValueError, match="different CAD delivery"):
        _matching_context(context, ["c" * 64])
    record["revision"] = 3
    with pytest.raises(ValueError, match="hash mismatch"):
        _matching_context(context, ["b" * 64])


def test_translation_preserves_pairwise_distance_and_reports_inconsistent_controls():
    pairs = [((0, 0), (30, 40)), ((100, 50), (132, 88))]
    result = fit_translation(pairs)
    assert (result["dx"], result["dy"]) == (31, 39)
    assert result["after_residuals_map_m"] == pytest.approx([math.sqrt(2)] * 2)
    moved = [(s[0] + result["dx"], s[1] + result["dy"]) for s, _ in pairs]
    assert math.dist(*moved) == math.dist(*(s for s, _ in pairs))
    assert result["independent_accuracy_verified"] is False


@pytest.mark.parametrize(
    "pairs",
    [
        [],
        [((0, 0), (1, 2))],
        [((0, 0), (1, 2))] * 2,
        [((0, 0), (1, 2)), ((1, float("nan")), (2, 3))],
    ],
)
def test_translation_rejects_underconstrained_or_invalid_controls(pairs):
    with pytest.raises(ValueError):
        fit_translation(pairs)


def test_coordinates_are_explicit_not_cad_format_numbers():
    assert coordinate_pair(r"\H0.8333x; 5.468081° , 95.368299°") == (
        95.368299,
        5.468081,
    )
    for text in [
        "5.468081 95.368299",
        "91.234567°,95.123456°",
        "5.123456°,95.123456° 6.123456°,96.123456°",
    ]:
        with pytest.raises(ValueError):
            coordinate_pair(text)


@pytest.fixture
def evidence(tmp_path):
    path = tmp_path / "source.gpkg"
    with sqlite3.connect(path) as db:
        db.execute(
            "create table source_text(cad_handle text,text text,owner_handle text,source_sha256 text,cad_layout text)"
        )
        db.executemany(
            "insert into source_text values (?,?,?,?,?)",
            [
                ("c1", "5.468081°,95.368299°", "table1", "dwg-hash", "Model"),
                ("i1", "POLE.1", "table1", "dwg-hash", "Model"),
                ("c2", "5.468867°,95.361535°", "table2", "dwg-hash", "Model"),
                ("i2", "POLE.2", "table2", "dwg-hash", "Model"),
            ],
        )
    config = dict(
        schema="cad2gis.qgis-coordinate-review.v1",
        model="translation",
        scope="project_display_only",
        source_evidence_sha256=sha(path),
        source_sha256="dwg-hash",
        controls=[
            dict(
                layer="PTECH",
                source_handle="p1",
                coordinate_text_handle="c1",
                identity_text_handle="i1",
                source_label="POLE.1",
            ),
            dict(
                layer="PTECH",
                source_handle="p2",
                coordinate_text_handle="c2",
                identity_text_handle="i2",
                source_label="POLE.2",
            ),
        ],
    )
    return path, config


def test_coordinate_evidence_requires_hash_identity_and_common_table(evidence):
    path, config = evidence
    assert len(_evidence_controls(config, path)) == 2
    for key, bad in [
        ("source_evidence_sha256", "other"),
        ("source_sha256", "other"),
        ("scope", "canonical"),
    ]:
        corrupted = copy.deepcopy(config)
        corrupted[key] = bad
        with pytest.raises(ValueError):
            _evidence_controls(corrupted, path)
    corrupted = copy.deepcopy(config)
    corrupted["controls"][0]["identity_text_handle"] = "i2"
    with pytest.raises(ValueError, match="same CAD table"):
        _evidence_controls(corrupted, path)
    corrupted = copy.deepcopy(config)
    corrupted["controls"][0]["source_label"] = "OTHER"
    with pytest.raises(ValueError, match="identity mismatch"):
        _evidence_controls(corrupted, path)
    corrupted = copy.deepcopy(config)
    corrupted["controls"][1] = corrupted["controls"][0]
    with pytest.raises(ValueError, match="Duplicate"):
        _evidence_controls(corrupted, path)
