"""Reader role heuristics must preserve their original source fact."""

from __future__ import annotations

from cad2gis.reader.autocad import partition_model_legend, partition_plan_roles


def _record(handle, centroid, **overrides):
    record = {
        "handle": handle,
        "cad_role": "model",
        "centroid": centroid,
        "points": [centroid],
        "text": "",
        "block_name": "",
        "closed": False,
        "object_name": "ACDBLWPOLYLINE",
        "raw_properties": {"handle": handle},
    }
    record.update(overrides)
    return record


def test_plan_role_reclassification_records_provenance():
    target = _record(
        "T1", (0.5, 0.5), points=[(0.0, 0.0), (1.0, 1.0)],
        block_name="TITLE BLOCK",
    )
    untouched = _record("U1", (0.2, 0.2))

    partition_plan_roles([target, untouched])

    assert target["cad_role"] == "title_block"
    assert target["cad_role_original"] == "model"
    assert target["role_reclassification"]["rule"] == (
        "plan_roles_title_block_name"
    )
    assert target["raw_properties"]["cad_role_original"] == "model"
    assert "cad_role_original" not in untouched


def test_model_legend_reclassification_is_traceable():
    records = [
        _record(
            f"I{index}", (float(index), 0.0),
            object_name="ACDBBLOCKREFERENCE",
        )
        for index in range(10)
    ]
    target = _record(
        "TARGET", (1000.0, 0.0), object_name="ACDBBLOCKREFERENCE",
    )
    records.append(target)

    partition_model_legend(records)

    assert target["cad_role"] == "style_legend"
    assert target["cad_role_original"] == "model"
    assert target["role_reclassification"]["rule"] == "model_legend_gap"
