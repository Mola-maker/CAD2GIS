from __future__ import annotations

import hashlib
import json
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
    assert bundle["cad_scene_graph"]["schema_version"] == (
        "cad2gis.cad_scene_graph.v1"
    )
    assert len(bundle["cad_scene_graph"]["graph_sha256"]) == 64
    assert bundle["cad_scene_graph"]["authority"][
        "ai_may_rank_existing_ids_only"
    ] is True
    scene_graph = json.loads(
        (root / "review" / "cad_scene_graph.json").read_text(encoding="utf-8")
    )
    assert scene_graph["graph_sha256"] == bundle["cad_scene_graph"][
        "graph_sha256"
    ]
    scene_visual_path = root / bundle["scene_visual"]["relative_path"]
    scene_visual = json.loads(scene_visual_path.read_text(encoding="utf-8"))
    assert scene_visual["cad_scene_graph_sha256"] == scene_graph["graph_sha256"]
    assert scene_visual["render_conserved"] is True
    assert bundle["scene_understanding"]["status"] == "available"
    assert bundle["scene_understanding"]["layouts"]
    assert bundle["scene_understanding"]["prompt_region_index"]
    assert bundle["scene_understanding"]["retrieval"]["context_tool"] == (
        "get_scene_visual_region_context"
    )
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


def test_project_validation_rejects_tampered_scene_visual(tmp_path: Path) -> None:
    _, root, _ = _project(tmp_path)
    visual_path = root / "reasoning" / "scene_visual" / "manifest.json"
    visual_path.write_bytes(visual_path.read_bytes() + b"\n")

    with pytest.raises(ValueError, match="Scene visual manifest digest is stale"):
        validate_project(project_dir=root)


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

    failed = _proposal(bundle, route_layers=[])
    with pytest.raises(onboarding.OnboardingError, match="no semantic features"):
        onboarding.compile_onboarding_proposal(
            source=source,
            project_dir=root,
            proposal=failed,
            proposer={"provider": "test", "model": "fixture"},
        )
    restored_profile = json.loads(
        (root / "config" / "source_profile.json").read_text(encoding="utf-8")
    )
    restored_registry = json.loads(
        (root / "config" / "mapping_registry.json").read_text(encoding="utf-8")
    )
    assert restored_profile["review"]["status"] == "draft"
    assert restored_registry["review"]["status"] == "draft"

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
            "block_attributes": {},
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


def test_bundle_without_cgeocs_exposes_nominal_local_fallback_candidate(
    tmp_path: Path,
) -> None:
    _, root, _ = _project(tmp_path, cgeocs="", insunits=0)

    bundle = onboarding.prepare_onboarding_bundle(root)

    assert len(bundle["crs_candidates"]) == 1
    candidate = bundle["crs_candidates"][0]
    assert candidate == {
        "candidate_id": "CAD-LOCAL:NO-CGEOCS:INSUNITS-0",
        "source_crs": "EPSG:3857",
        "target_crs": "EPSG:3857",
        "drawing_units": "unknown_local",
        "source_coordinate_scale_to_m": 1.0,
        "source_coordinate_scale_reviewed": False,
        "evidence": {
            "dwg_cgecs": "",
            "dwg_insunits": 0,
            "authority": "LOCAL_FALLBACK_NO_CGEOCS",
            "absolute_accuracy": "requires_gcp_registration",
            "nominal": True,
        },
        "candidate_sha256": candidate["candidate_sha256"],
    }


def test_bundle_with_unknown_cgeocs_also_falls_back(tmp_path: Path) -> None:
    _, root, _ = _project(tmp_path, cgeocs="LOCAL-GRID-UNKNOWN", insunits=4)

    candidate = onboarding.prepare_onboarding_bundle(root)["crs_candidates"][0]

    assert candidate["candidate_id"] == "CAD-LOCAL:NO-CGEOCS:INSUNITS-4"
    assert candidate["evidence"]["authority"] == "LOCAL_FALLBACK_NO_CGEOCS"
    assert candidate["source_coordinate_scale_reviewed"] is False


def test_local_fallback_proposal_compiles_and_parks_on_gcp_registration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, root, source_sha256 = _project(tmp_path, cgeocs="", insunits=0)
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

    result = onboarding.compile_onboarding_proposal(
        source=source,
        project_dir=root,
        proposal=_proposal(bundle),
        proposer={"provider": "test", "model": "fixture"},
    )

    assert result["status"] == "auto_accepted"
    assert result["feature_counts"] == {"CABLE": 1}
    validation = validate_project(project_dir=root)
    assert validation["status"] == "registration_required"
    assert validation["conversion_allowed"] is False
    assert any("GCP" in action for action in validation["next_actions"])
    profile = json.loads(
        (root / "config" / "source_profile.json").read_text(encoding="utf-8")
    )
    assert profile["drawing"]["drawing_units"] == "unknown_local"
    assert profile["drawing"]["source_coordinate_scale_to_m"] == 1.0
    assert profile["drawing"]["source_coordinate_scale_reviewed"] is False
    assert profile["crs"]["source_crs"] == "EPSG:3857"
    assert profile["review"]["status"] == "auto_accepted"
    assert profile["expectations"]["feature_counts"] == {"CABLE": 1}


def test_nominal_local_contract_shape_and_direct_transformer() -> None:
    from cad2gis.cad2gis_v3 import units
    from cad2gis.cad2gis_v3.georef import DirectTransformer

    with pytest.raises(units.UnitCrsContractError, match="dwg_insunits"):
        units.build_unit_crs_contract(
            0,
            "EPSG:3857",
            "EPSG:3857",
            source_coordinate_scale_to_m=1.0,
        )

    contract = units.build_unit_crs_contract(
        0,
        "EPSG:3857",
        "EPSG:3857",
        source_coordinate_scale_to_m=1.0,
        source_coordinate_scale_reviewed=False,
        nominal_local=True,
    )

    assert contract.coordinate_mode == "nominal_local_gcp_required"
    assert contract.can_direct_transform is False
    assert contract.source_coordinate_scale_reviewed is False
    assert contract.source_coordinate_scale_origin == (
        "ONBOARDING:nominal-local-placeholder-unreviewed-scale"
    )
    assert contract.cad_unit.insunits == 0
    assert contract.cad_unit.metres_per_unit == 1.0
    assert contract.source_to_crs_axis_factor == pytest.approx(1.0)

    transformer = DirectTransformer(
        "EPSG:3857", "EPSG:3857", unit_contract=contract,
    )
    assert transformer.source_to_crs_axis_factor == pytest.approx(1.0)

    from cad2gis.cad2gis_v3.calibration import (
        _validate_transformer_unit_contract,
    )

    _validate_transformer_unit_contract(
        transformer, "EPSG:3857", "EPSG:3857",
    )


def test_nominal_local_convert_without_gcp_fails_closed_pointing_to_gcp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    from cad2gis.cad2gis_v3 import pipeline

    source = tmp_path / "fixture.dwg"
    source.write_bytes(b"nominal-local-convert-fixture")
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    (tmp_path / "source_profile.json").write_text("{}", encoding="utf-8")
    (tmp_path / "mapping_registry.json").write_text("{}", encoding="utf-8")

    class FakeProfile:
        source_sha256 = source_hash
        dwg_insunits = 0
        source_crs = "EPSG:3857"
        target_crs = "EPSG:3857"
        drawing_units = "unknown_local"
        source_coordinate_scale_to_m = 1.0
        source_coordinate_scale_reviewed = False
        local_registration_strategy = None
        local_registration_reviewed = False

        def require_reviewed(self):
            return None

        def validate_source(self, path):
            assert Path(path).resolve() == source.resolve()
            return source_hash

    monkeypatch.setattr(
        pipeline.SourceProfile, "load", staticmethod(lambda _: FakeProfile())
    )
    monkeypatch.setattr(
        pipeline.MappingRegistry,
        "load",
        staticmethod(lambda *_args: SimpleNamespace()),
    )
    monkeypatch.setattr(
        pipeline, "_validate_project_bindings", lambda *_args: None,
    )

    with pytest.raises(RuntimeError, match="GCP"):
        pipeline.convert(
            pipeline.ConversionRequest(
                source=source,
                run_dir=tmp_path / "runs" / "fixture",
                source_profile=tmp_path / "source_profile.json",
                mapping_registry=tmp_path / "mapping_registry.json",
            )
        )
