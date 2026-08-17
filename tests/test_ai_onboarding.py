from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

from cad2gis.cad2gis_v3 import onboarding
from cad2gis.cad2gis_v3.model import SourceEntity
from cad2gis.cad2gis_v3.project_profile import bootstrap_project, validate_project


def _record(
    source: Path,
    source_sha256: str,
    *,
    entity_key: str,
    dwg_type: str,
    layer: str = "0",
    layout: str = "Model",
    cad_role: str = "model",
    points: tuple[tuple[float, float], ...] = (),
    text: str = "",
) -> dict:
    return {
        "entity_key": entity_key,
        "source_sha256": source_sha256,
        "source_file": source.name,
        "handle": entity_key,
        "layout": layout,
        "layout_role": cad_role,
        "cad_role": cad_role,
        "layer": layer,
        "object_name": f"AcDb{dwg_type.title()}",
        "dwg_type_name": dwg_type,
        "points": points,
        "centroid": points[0] if points else (0.0, 0.0),
        "closed": False,
        "text": text,
        "block_name": "",
        "block_attributes": {},
        "raw_properties": {},
    }


def _project(
    tmp_path: Path,
    *,
    cgeocs: str = "UTM84-49S",
    insunits: int = 6,
) -> tuple[Path, Path, str]:
    source = tmp_path / "source.dwg"
    source.write_bytes(b"source-bound-ai-onboarding-fixture")
    source_sha256 = hashlib.sha256(source.read_bytes()).hexdigest()
    records = [
        _record(
            source,
            source_sha256,
            entity_key="metadata",
            dwg_type="DOCUMENT_METADATA",
            layout="Document",
            cad_role="metadata",
            text=f"CGEOCS={cgeocs};INSUNITS={insunits}",
        ),
        _record(
            source,
            source_sha256,
            entity_key="route",
            dwg_type="LINE",
            layer="FO 48C CABLE",
            points=((100.0, 200.0), (110.0, 200.0)),
        ),
    ]
    root = tmp_path / "project"
    bootstrap_project(source=source, project_dir=root, records=records)
    return source, root, source_sha256


def _proposal(bundle: dict, *, route_layers: list[str] | None = None) -> dict:
    return {
        "schema_version": onboarding.ONBOARDING_PROPOSAL_SCHEMA,
        "bundle_sha256": bundle["bundle_sha256"],
        "source_sha256": bundle["source"]["sha256"],
        "inventory_sha256": bundle["inventory_sha256"],
        "crs_candidate_id": bundle["crs_candidates"][0]["candidate_id"],
        "route_layers": (
            ["FO 48C CABLE"] if route_layers is None else route_layers
        ),
        "homepass_layers": [],
        "span_dimension_layers": [],
        "sling_wire_layers": [],
        "zpm_boundary_layers": [],
        "block_families": {"BOITE": [], "PTECH": [], "SITE": []},
        "insert_layer_families": {"BOITE": [], "PTECH": [], "SITE": []},
        "confidence": {"semantics": 0.9, "crs": 1.0},
        "rationale": "Observed route layer and direct DWG CRS metadata.",
    }


def test_bundle_exposes_only_deterministic_metadata_crs_candidate(
    tmp_path: Path,
) -> None:
    _, root, source_sha256 = _project(tmp_path)

    bundle = onboarding.prepare_onboarding_bundle(root)

    assert bundle["source"]["sha256"] == source_sha256
    assert bundle["crs_candidates"] == [
        {
            "candidate_id": "CAD-METADATA:UTM84-49S:INSUNITS-6",
            "source_crs": "EPSG:32749",
            "target_crs": "EPSG:32749",
            "drawing_units": "metre",
            "source_coordinate_scale_to_m": 1.0,
            "source_coordinate_scale_reviewed": True,
            "evidence": {
                "dwg_cgeocs": "UTM84-49S",
                "dwg_insunits": 6,
                "dwg_insunits_name": "metre",
                "dwg_insunits_role": "block_insertion_scale_hint",
                "coordinate_unit_basis": "source_crs_axis",
                "source_crs_axis_unit": "metre",
                "source_crs_axis_metres_per_unit": 1.0,
                "authority": "DWG_DIRECT",
            },
            "candidate_sha256": bundle["crs_candidates"][0][
                "candidate_sha256"
            ],
        }
    ]
    assert bundle["deterministic_role_suggestions"]["route_layers"] == [
        "FO 48C CABLE"
    ]


def test_projected_crs_axis_controls_wcs_scale_not_insunits(
    tmp_path: Path,
) -> None:
    _, root, _ = _project(
        tmp_path,
        cgeocs="Indonesian1974.UTM-46N",
        insunits=4,
    )

    candidate = onboarding.prepare_onboarding_bundle(root)["crs_candidates"][0]

    assert candidate["source_crs"] == "EPSG:23846"
    assert candidate["drawing_units"] == "metre"
    assert candidate["source_coordinate_scale_to_m"] == 1.0
    assert candidate["evidence"]["dwg_insunits_name"] == "millimetre"
    assert candidate["evidence"]["coordinate_unit_basis"] == "source_crs_axis"


def test_proposal_rejects_invented_identifiers_and_weak_confidence(
    tmp_path: Path,
) -> None:
    _, root, _ = _project(tmp_path)
    bundle = onboarding.prepare_onboarding_bundle(root)
    invented = _proposal(bundle, route_layers=["NOT-IN-SOURCE"])
    with pytest.raises(onboarding.OnboardingError, match="observed identifiers"):
        onboarding.validate_onboarding_proposal(bundle, invented)

    weak = _proposal(bundle)
    weak["confidence"]["semantics"] = 0.5
    with pytest.raises(onboarding.OnboardingError, match="Semantic auto-acceptance"):
        onboarding.validate_onboarding_proposal(bundle, weak)


def test_compile_is_transactional_and_admits_exact_dry_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, root, source_sha256 = _project(tmp_path)
    bundle = onboarding.prepare_onboarding_bundle(root)
    route = SourceEntity.from_record(
        _record(
            source,
            source_sha256,
            entity_key="route",
            dwg_type="LINE",
            layer="FO 48C CABLE",
            points=((100.0, 200.0), (110.0, 200.0)),
        )
    )
    diagnostics = {
        "census": {
            "model_entities": 1,
            "model_inserts": 0,
            "model_dimensions": 0,
        }
    }
    monkeypatch.setattr(
        onboarding,
        "ingest",
        lambda *_args, **_kwargs: ([route], diagnostics),
    )

    # The route-regex check chain auto-repairs an empty route_layers proposal:
    # the FO 48C CABLE layer is unmatched, looks like a cable, and is appended
    # to the regex, so the dry run then yields features instead of failing.
    failed = _proposal(bundle, route_layers=[])
    result_repair = onboarding.compile_onboarding_proposal(
        source=source,
        project_dir=root,
        proposal=failed,
        proposer={"provider": "test", "model": "fixture"},
    )
    assert result_repair["status"] == "auto_accepted"
    assert result_repair["feature_counts"] == {"CABLE": 1}
    assert result_repair.get("family_validation", {}).get(
        "route_regex_check", {}
    ).get("status") == "extended"

    result = onboarding.compile_onboarding_proposal(
        source=source,
        project_dir=root,
        proposal=_proposal(bundle),
        proposer={"provider": "test", "model": "fixture"},
    )

    assert result["status"] == "auto_accepted"
    assert result["feature_counts"] == {"CABLE": 1}
    assert validate_project(project_dir=root)["conversion_allowed"] is True
    profile = json.loads(
        (root / "config" / "source_profile.json").read_text(encoding="utf-8")
    )
    registry = json.loads(
        (root / "config" / "mapping_registry.json").read_text(encoding="utf-8")
    )
    assert profile["review"] == registry["review"]
    assert profile["crs"]["source_crs"] == "EPSG:32749"
    assert profile["expectations"]["feature_counts"] == {"CABLE": 1}


def test_compile_annotation_families_targets_insert_layers_not_label_layers() -> None:
    families = onboarding._compile_annotation_families(
        [
            {
                "family_id": "pole_label",
                "text_pattern": r"^MR\.KLK\d+\.P\d+$",
                "target_class": "PTECH",
                "max_distance_native_m": 15.0,
                "source_layer": "POLE ID 2.5",
            },
            {
                "family_id": "same_layer_pole",
                "text_pattern": r"^EXT\.MR\.BDR\.P\d+$",
                "target_class": "PTECH",
                "max_distance_native_m": 15.0,
                "source_layer": "EXT POLE",
            },
        ],
        {"PTECH": ["EXT POLE", "NEW POLE"]},
    )

    pole, same = families
    assert pole["require_same_layer"] is False
    assert re.fullmatch(pole["target_layer_pattern"], "EXT POLE")
    assert re.fullmatch(pole["target_layer_pattern"], "NEW POLE")
    assert not re.fullmatch(pole["target_layer_pattern"], "POLE ID 2.5")

    assert same["require_same_layer"] is True
    assert re.fullmatch(same["target_layer_pattern"], "EXT POLE")
    assert not re.fullmatch(same["target_layer_pattern"], "NEW POLE")


def test_compile_annotation_families_fail_closed_without_target_layers() -> None:
    families = onboarding._compile_annotation_families(
        [
            {
                "family_id": "orphan_label",
                "text_pattern": r"^X+$",
                "target_class": "PTECH",
                "source_layer": "POLE ID",
            }
        ],
        {"PTECH": []},
    )
    assert families[0]["require_same_layer"] is False
    assert families[0]["target_layer_pattern"] == "(?!)"


def test_compile_annotation_families_repairs_target_class_from_layer_semantics() -> None:
    from cad2gis.cad2gis_v3.family_validation import infer_target_class

    assert infer_target_class("POLE ID FDT 2 73") == "PTECH"

    families = onboarding._compile_annotation_families(
        [
            {
                "family_id": "pole_fdt_area",
                "text_pattern": r"^MR\.KLK\d+\.P\d+$",
                "target_class": "SITE",
                "max_distance_native_m": 15.0,
                "source_layer": "POLE ID FDT 2 73",
            }
        ],
        {
            "PTECH": ["EXT POLE", "NEW POLE FDT 1"],
            "SITE": ["FDT"],
        },
    )

    family = families[0]
    assert family["target_class"] == "PTECH"
    assert re.fullmatch(family["target_layer_pattern"], "NEW POLE FDT 1")
    assert not re.fullmatch(family["target_layer_pattern"], "FDT")
    assert "LAYER-TARGET-CLASS-REPAIR:PTECH" in family["provenance"]


def test_insert_layer_mapping_classifies_anonymous_dynamic_block() -> None:
    source_sha256 = "a" * 64
    entity = SourceEntity.from_record(
        {
            "entity_key": "insert",
            "source_sha256": source_sha256,
            "source_file": "fixture.dwg",
            "handle": "1",
            "layout": "Model",
            "layout_role": "model",
            "cad_role": "model",
            "layer": "FAT",
            "object_name": "AcDbBlockReference",
            "dwg_type_name": "INSERT",
            "points": ((1.0, 2.0),),
            "centroid": (1.0, 2.0),
            "closed": False,
            "text": "",
            "block_name": "*U17",
            "block_attributes": {"F": "1"},
            "raw_properties": {},
        }
    )
    registry = type(
        "Registry",
        (),
        {
            "block_families": {"BOITE": (), "PTECH": (), "SITE": ()},
            "insert_layer_families": {
                "BOITE": ("FAT",),
                "PTECH": (),
                "SITE": (),
            },
            "positive_route_layer_regex": "(?!)",
            "layers": {},
            "field_rules": {},
            "display_label_rules": {},
            "annotation_families": (),
            "decision_rules": {},
            "labels": {},
            "thresholds": {},
        },
    )()

    features, _, _, diagnostics = onboarding.classify_entities(
        [entity],
        registry,
        coverage_policy="abstain",
    )

    assert [feature.feature_class for feature in features] == ["BOITE"]
    assert diagnostics["coverage"]["conversion_allowed"] is True


def test_text_samples_use_drawing_space_and_prioritize_structured_labels() -> None:
    carriers = [
        {"layer": "FAT", "layout": "Model", "text": "KLDYA.011.B06", "aci_color": 30},
        {"layer": "FAT", "layout": "Model", "text": "KLDYA.011.B11", "aci_color": 30},
        {"layer": "FAT", "layout": "Model", "text": "KLDYA.011.C01", "aci_color": 30},
        {"layer": "FAT", "layout": "Model", "text": "KLDYA.011.A07", "aci_color": 30},
        {"layer": "FAT", "layout": "Model", "text": "KLDYA.011.A01", "aci_color": 30},
        {"layer": "FAT", "layout": "Model", "text": "KLDYA.011.C03", "aci_color": 30},
        {"layer": "FAT", "layout": "Model", "text": "KLDYA.011.B08", "aci_color": 30},
        {"layer": "FAT", "layout": "Model", "text": "KLDYA.011.B04", "aci_color": 30},
        {"layer": "FAT", "layout": "Model", "text": "KLDYA.012.A02", "aci_color": 30},
        {"layer": "FAT", "layout": "Model", "text": "A01", "aci_color": 30},
        {"layer": "FAT", "layout": "Model", "text": "A02", "aci_color": 30},
        {"layer": "FAT", "layout": "Model", "text": "A03", "aci_color": 30},
        {"layer": "FAT", "layout": "Model", "text": "A04", "aci_color": 30},
        {"layer": "FAT", "layout": "Model", "text": "A05", "aci_color": 30},
        {"layer": "FAT", "layout": "Model", "text": "A06", "aci_color": 30},
        {"layer": "FAT", "layout": "Model", "text": "A07", "aci_color": 30},
        {"layer": "FAT", "layout": "Model", "text": "A08", "aci_color": 30},
        {"layer": "FAT", "layout": "BLOCKDEF:sfsfsfs", "text": "\\pxqc;FAT A014"},
        {"layer": "FAT_Info", "layout": "BLOCKDEF:", "text": "KLDYA.011.B12"},
    ]
    samples = onboarding._text_samples(carriers, limit_per_layer=8)
    assert "FAT_Info" not in samples
    fat_texts = [item["text"] for item in samples["FAT"]]
    assert "\\pxqc;FAT A014" not in fat_texts
    # Multi-field identifiers are sampled before single-token stubs.
    assert fat_texts.index("KLDYA.011.B06") < fat_texts.index("A01")
    assert "KLDYA.012.A02" in fat_texts
