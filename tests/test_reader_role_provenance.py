"""Tests for cad_role reclassification provenance in the AutoCAD reader."""

from __future__ import annotations

from cad2gis.reader.autocad import partition_model_legend, partition_plan_roles


def _record(
    handle,
    centroid,
    *,
    points=(),
    text="",
    block_name="",
    closed=False,
    cad_role="model",
    object_name="ACDBLWPOLYLINE",
    with_raw_properties=True,
):
    record = {
        "handle": handle,
        "cad_role": cad_role,
        "centroid": centroid,
        "points": points,
        "text": text,
        "block_name": block_name,
        "closed": closed,
        "object_name": object_name,
    }
    if with_raw_properties:
        record["raw_properties"] = {"handle": handle}
    return record


def _assert_provenance(record, rule, original, new_role):
    assert record["cad_role"] == new_role
    assert record["cad_role_original"] == original
    reclassification = record["role_reclassification"]
    assert reclassification["rule"] == rule
    assert reclassification["from"] == original
    assert reclassification["to"] == new_role
    assert reclassification["reason"]
    raw_properties = record["raw_properties"]
    assert raw_properties["cad_role_original"] == record["cad_role_original"]
    assert raw_properties["role_reclassification"] == reclassification


def _assert_no_provenance(record):
    assert "cad_role_original" not in record
    assert "role_reclassification" not in record
    raw_properties = record.get("raw_properties")
    if raw_properties is not None:
        assert "cad_role_original" not in raw_properties
        assert "role_reclassification" not in raw_properties


def test_title_block_name_reclassification_records_provenance():
    target = _record("T1", (0.5, 0.5), points=[(0, 0), (1, 1)], block_name="TITLE BLOCK")
    untouched = _record("U1", (0.2, 0.2), points=[(0.2, 0.2)])

    partition_plan_roles([target, untouched])

    _assert_provenance(target, "plan_roles_title_block_name", "model", "title_block")
    assert untouched["cad_role"] == "model"
    _assert_no_provenance(untouched)


def test_frame_span_reclassification_records_provenance():
    target = _record(
        "F1",
        (50, 40),
        points=[(0, 0), (100, 0), (100, 80), (0, 80)],
        closed=True,
    )

    partition_plan_roles([target])

    _assert_provenance(target, "plan_roles_frame_span", "model", "frame")


def test_legend_region_reclassification_records_provenance():
    anchor = _record("L1", (80, 50), points=[(80, 50)], text="LEGEND")
    target = _record("T1", (90, 60), points=[(90, 60)])
    outside = _record("O1", (10, 10), points=[(10, 10)])

    partition_plan_roles([anchor, target, outside])

    _assert_provenance(target, "plan_roles_legend_region", "model", "style_legend")
    assert outside["cad_role"] == "model"
    _assert_no_provenance(outside)


def test_design_summary_reclassification_records_provenance():
    target = _record("S1", (10, 10), points=[(10, 10), (20, 20)], text="DESIGN SUMMARY")
    untouched = _record("U1", (50, 50), points=[(50, 50)])

    partition_plan_roles([target, untouched])

    _assert_provenance(target, "plan_roles_design_summary", "model", "design_summary")
    _assert_no_provenance(untouched)


def test_title_region_reclassification_records_provenance_without_raw_properties():
    target = _record(
        "T1",
        (10, 10),
        points=[(10, 10), (20, 20)],
        text="PROJECT NAME",
        with_raw_properties=False,
    )

    partition_plan_roles([target])

    assert target["cad_role"] == "title_block"
    assert target["cad_role_original"] == "model"
    assert target["role_reclassification"]["rule"] == "plan_roles_title_region"
    assert target["role_reclassification"]["from"] == "model"
    assert target["role_reclassification"]["to"] == "title_block"
    assert "raw_properties" not in target


def test_same_role_reclassification_records_no_provenance():
    record = _record(
        "N1", (0.5, 0.5), points=[(0, 0), (1, 1)],
        block_name="TITLE BLOCK", cad_role="title_block",
    )

    partition_plan_roles([record])

    assert record["cad_role"] == "title_block"
    _assert_no_provenance(record)


def _insert(handle, x, y, **kwargs):
    return _record(
        handle, (x, y),
        points=[(x, y)], object_name="ACDBBLOCKREFERENCE", **kwargs,
    )


def test_model_legend_reclassifies_only_model_role_records():
    records = [_insert(f"I{index}", float(index), 0.0) for index in range(10)]
    target = _insert("T1", 1000.0, 0.0)
    plan_record = _insert("P1", 1000.0, 0.0, cad_role="plan")
    records.extend([target, plan_record])

    partition_model_legend(records)

    _assert_provenance(target, "model_legend_gap", "model", "style_legend")
    assert plan_record["cad_role"] == "plan"
    _assert_no_provenance(plan_record)
    for record in records[:10]:
        assert record["cad_role"] == "model"
        _assert_no_provenance(record)


def test_second_reclassification_keeps_first_original():
    records = [_insert(f"I{index}", float(index), 0.0) for index in range(10)]
    target = _insert("T1", 1000.0, 10.0)
    anchor = _insert("L1", 1000.0, 200.0, text="LEGEND")
    records.extend([target, anchor])

    partition_model_legend(records)
    partition_plan_roles(records)

    assert target["cad_role"] == "title_block"
    assert target["cad_role_original"] == "model"
    assert target["role_reclassification"]["rule"] == "plan_roles_legend_region"
    assert target["role_reclassification"]["from"] == "style_legend"
    assert target["role_reclassification"]["to"] == "title_block"
    raw_properties = target["raw_properties"]
    assert raw_properties["cad_role_original"] == "model"
    assert raw_properties["role_reclassification"] == target["role_reclassification"]
