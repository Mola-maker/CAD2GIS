from __future__ import annotations

import json
import math

import pytest

from cad2gis.agent_mcp import (
    MCPServiceError,
    create_decision_pack as mcp_create_decision_pack,
    get_evidence_node,
    inspect_run,
    list_endpoint_join_candidates,
    list_evidence_nodes,
    list_network_repair_candidates,
    list_registered_operations,
    validate_decision_pack as mcp_validate_decision_pack,
)
from cad2gis.cad2gis_v3.curve_geometry import (
    MATERIALIZATION_POLICY_VERSION,
)

from cad2gis.cad2gis_v3.decision_validation import (
    AutoDecisionPolicy,
    GeometrySnapshot,
    LengthSnapshot,
    TopologySnapshot,
    validate_crs_candidate,
    validate_geometry,
    validate_lengths,
    validate_topology,
)
from cad2gis.cad2gis_v3.decision_executor import execute_decision_pack
from cad2gis.cad2gis_v3.evidence_graph import (
    EvidenceGraph,
    EvidenceGraphError,
    build_stage_evidence_graph,
)
from cad2gis.cad2gis_v3.geometry_repairs import (
    CURVE_MATERIALIZER_ID,
    endpoint_pair_candidates,
)
from cad2gis.cad2gis_v3.model import CadStyle, Feature, Relation, SourceEntity
from cad2gis.cad2gis_v3.implementation import (
    SnapshotVerificationError,
    freeze_conversion_snapshot,
    verify_conversion_snapshot,
)
from cad2gis.cad2gis_v3.repair_decisions import (
    DecisionPack,
    RepairDecisionError,
    RepairOperation,
    load_decision_pack,
    write_decision_pack_atomic,
)


def test_mcp_inspect_run_exposes_plan_domain_evidence(
    tmp_path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    manifest = {
        "schema_version": "cad2gis-run-manifest-v4",
        "run_status": "CONDITIONAL",
        "source": {"sha256": "a" * 64},
        "modes": {"domain": "auto", "llm": "off"},
        "delivery_counts": {"CABLE": 1},
        "plan_domain": {
            "schema_version": "cad2gis-plan-domain-v1",
            "selection_mode": "layout-role-fallback",
            "status": "PASS",
        },
        "reasoning": {},
        "artifacts": {},
        "validation": {},
    }
    (run_dir / "run_manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8",
    )
    monkeypatch.setenv("CAD2GIS_PROJECT_ROOTS", str(tmp_path))

    result = inspect_run(str(run_dir))

    assert result["plan_domain"] == manifest["plan_domain"]


SOURCE_SHA = "a" * 64
RATIONALE_SHA = "b" * 64
REQUEST_SHA = "c" * 64
RESPONSE_SHA = "d" * 64
POLICY_ID = "cad2gis.auto-decision.default.v1"


def _stage_graph() -> EvidenceGraph:
    entity = SourceEntity(
        entity_key="entity:1",
        source_sha256=SOURCE_SHA,
        source_file="drawing.dwg",
        handle="10A",
        layout="Model",
        layout_role="model",
        cad_role="model",
        layer="CABLE",
        object_name="AcDbPolyline",
        dwg_type="LWPOLYLINE",
        points=((0.0, 0.0), (10.0, 0.0)),
        centroid=(5.0, 0.0),
        closed=False,
        text="",
        block_name="",
        block_attributes={},
        style=CadStyle(aci_color=3),
        native_length=10.0,
        raw_properties={"extraction_backend": "test-reader"},
    )
    label = SourceEntity(
        entity_key="entity:label",
        source_sha256=SOURCE_SHA,
        source_file="drawing.dwg",
        handle="10B",
        layout="Model",
        layout_role="model",
        cad_role="model",
        layer="LABEL",
        object_name="AcDbText",
        dwg_type="TEXT",
        points=((5.0, 1.0),),
        centroid=(5.0, 1.0),
        closed=False,
        text="MR.DMPH.P104",
        block_name="",
        block_attributes={},
        style=CadStyle(aci_color=7),
    )
    feature = Feature(
        feature_key="feature:cable:1",
        feature_class="CABLE",
        geometry_kind="LINESTRING",
        native_points=[(0.0, 0.0), (10.0, 0.0)],
        source_entity_key=entity.entity_key,
        source_handle=entity.handle,
        source_layer=entity.layer,
        geometry_role="SOURCE_ROUTE",
        style=entity.style,
    )
    relation = Relation(
        relation_key="relation:label:1",
        relation_kind="label_candidate",
        source_key=label.entity_key,
        target_key=feature.feature_key,
        status="candidate",
        method="nearest_text",
        distance_native_m=1.0,
        evidence_keys=(label.entity_key, entity.entity_key),
    )
    return build_stage_evidence_graph(
        source_sha256=SOURCE_SHA,
        entities=[label, entity],
        features=[feature],
        relations=[relation],
        unresolved=[{"reason": "paper_space_title", "entity_key": "paper:1"}],
    )


def _operation(graph: EvidenceGraph, *, kind: str = "select_semantic_class") -> RepairOperation:
    nodes = graph.logical_index
    if kind == "select_semantic_class":
        return RepairOperation.create(
            operation=kind,
            entity_node_ids=[nodes["feature:cable:1"].node_id],
            evidence_node_ids=[nodes["entity:label"].node_id],
            parameters={"candidate_id": "CABLE", "policy_id": "semantic-v1"},
            confidence=0.93,
            agreement_count=1,
            rationale_sha256=RATIONALE_SHA,
        )
    if kind == "join_observed_endpoints":
        return RepairOperation.create(
            operation=kind,
            entity_node_ids=[
                nodes["entity:1"].node_id,
                nodes["feature:cable:1"].node_id,
            ],
            evidence_node_ids=[
                nodes["entity:1"].node_id,
                nodes["entity:label"].node_id,
            ],
            parameters={"endpoint_pair_id": "pair:1", "policy_id": "snap-v1"},
            confidence=0.96,
            agreement_count=2,
            rationale_sha256=RATIONALE_SHA,
        )
    if kind == "select_crs_candidate":
        return RepairOperation.create(
            operation=kind,
            entity_node_ids=[nodes["feature:cable:1"].node_id],
            evidence_node_ids=[
                nodes["entity:1"].node_id,
                nodes["entity:label"].node_id,
            ],
            parameters={"candidate_id": "EPSG:32651", "policy_id": "crs-v1"},
            confidence=0.97,
            agreement_count=2,
            rationale_sha256=RATIONALE_SHA,
        )
    raise AssertionError(kind)


def _pack(graph: EvidenceGraph, operation: RepairOperation) -> DecisionPack:
    return DecisionPack.create(
        source_sha256=SOURCE_SHA,
        evidence_graph_sha256=graph.graph_sha256,
        policy_id=POLICY_ID,
        proposer={
            "provider": "test",
            "model": "deterministic-fixture",
            "protocol": "mcp",
            "request_sha256": REQUEST_SHA,
            "response_sha256": RESPONSE_SHA,
        },
        operations=[operation],
    )


def _passing_reports(pack: DecisionPack, operation: RepairOperation):
    target = operation.entity_node_ids[0]
    geometry = validate_geometry(
        pack,
        operation,
        GeometrySnapshot.create({target: "before"}),
        GeometrySnapshot.create({target: "before"}),
    )
    topology = validate_topology(
        pack,
        operation,
        TopologySnapshot.create([]),
        TopologySnapshot.create([]),
    )
    length = validate_lengths(
        pack,
        operation,
        LengthSnapshot.create({target: 10.0}),
        LengthSnapshot.create({target: 10.0}),
        tolerance_native=1e-9,
    )
    return geometry, topology, length


def test_evidence_graph_is_order_independent_and_tamper_evident() -> None:
    first = _stage_graph()
    second = _stage_graph()
    assert first.graph_sha256 == second.graph_sha256
    assert first.to_dict() == second.to_dict()
    assert EvidenceGraph.from_dict(first.to_dict()) == first

    tampered = first.to_dict()
    tampered["nodes"][0]["facts"]["layer"] = "OTHER"
    with pytest.raises(EvidenceGraphError, match="content address"):
        EvidenceGraph.from_dict(tampered)


def test_repair_operation_rejects_unregistered_or_numeric_facts() -> None:
    graph = _stage_graph()
    target = graph.logical_index["feature:cable:1"].node_id
    evidence = graph.logical_index["entity:label"].node_id
    with pytest.raises(RepairDecisionError, match="Unregistered"):
        RepairOperation.create(
            operation="write_wkt",
            entity_node_ids=[target],
            evidence_node_ids=[evidence],
            parameters={},
            confidence=1.0,
            agreement_count=1,
            rationale_sha256=RATIONALE_SHA,
        )
    with pytest.raises(RepairDecisionError, match="non-empty string"):
        RepairOperation.create(
            operation="select_semantic_class",
            entity_node_ids=[target],
            evidence_node_ids=[evidence],
            parameters={"candidate_id": 32651, "policy_id": "semantic-v1"},
            confidence=1.0,
            agreement_count=1,
            rationale_sha256=RATIONALE_SHA,
        )
    with pytest.raises(RepairDecisionError, match="unknown=.*coordinates"):
        RepairOperation.create(
            operation="select_semantic_class",
            entity_node_ids=[target],
            evidence_node_ids=[evidence],
            parameters={
                "candidate_id": "CABLE",
                "policy_id": "semantic-v1",
                "coordinates": "1,2",
            },
            confidence=1.0,
            agreement_count=1,
            rationale_sha256=RATIONALE_SHA,
        )


def test_decision_pack_is_source_bound_and_atomic_roundtrip(tmp_path) -> None:
    graph = _stage_graph()
    operation = _operation(graph)
    pack = _pack(graph, operation)
    pack.validate_against(graph)

    path = write_decision_pack_atomic(tmp_path / "decision-pack.json", pack)
    loaded = load_decision_pack(path)
    assert loaded == pack
    assert json.loads(path.read_text(encoding="utf-8"))["pack_sha256"] == pack.pack_sha256

    foreign = EvidenceGraph.create(source_sha256="f" * 64, nodes=[])
    with pytest.raises(RepairDecisionError, match="another source"):
        pack.validate_against(foreign)


def test_auto_policy_accepts_only_complete_independent_validation() -> None:
    graph = _stage_graph()
    operation = _operation(graph)
    pack = _pack(graph, operation)
    reports = _passing_reports(pack, operation)
    policy = AutoDecisionPolicy()

    accepted = policy.evaluate(pack, operation, reports)
    assert accepted.disposition == "AUTO_ACCEPTED"
    assert not accepted.reasons

    quarantined = policy.evaluate(pack, operation, reports[:2])
    assert quarantined.disposition == "QUARANTINED"
    assert "length" in quarantined.reasons[0]


def test_geometry_repair_requires_agreement_and_preserves_length() -> None:
    graph = _stage_graph()
    operation = _operation(graph, kind="join_observed_endpoints")
    pack = _pack(graph, operation)
    target = operation.entity_node_ids[0]
    other = operation.entity_node_ids[1]
    reports = (
        validate_geometry(
            pack,
            operation,
            GeometrySnapshot.create({target: "a", other: "b"}),
            GeometrySnapshot.create(
                {target: "a2", other: "b"},
                max_deviation_native={target: 0.02},
            ),
        ),
        validate_topology(
            pack,
            operation,
            TopologySnapshot.create([]),
            TopologySnapshot.create([(target, other)]),
            declared_added_edges=[(target, other)],
        ),
        validate_lengths(
            pack,
            operation,
            LengthSnapshot.create({target: 10.0, other: 5.0}),
            LengthSnapshot.create({target: 10.0, other: 5.0}),
            tolerance_native=1e-9,
        ),
    )
    assert all(report.passed for report in reports)
    assert AutoDecisionPolicy().evaluate(pack, operation, reports).disposition == "AUTO_ACCEPTED"

    failed_length = validate_lengths(
        pack,
        operation,
        LengthSnapshot.create({target: 10.0, other: 5.0}),
        LengthSnapshot.create({target: 10.5, other: 5.0}),
        tolerance_native=0.01,
    )
    rejected = AutoDecisionPolicy().evaluate(
        pack, operation, (reports[0], reports[1], failed_length),
    )
    assert rejected.disposition == "REJECTED"


def test_georeference_auto_decision_requires_independent_check_points() -> None:
    graph = _stage_graph()
    operation = _operation(graph, kind="select_crs_candidate")
    pack = _pack(graph, operation)
    base_reports = _passing_reports(pack, operation)
    crs = validate_crs_candidate(
        pack,
        operation,
        model_family="similarity",
        training_rmse=0.45,
        independent_check_rmse=0.60,
        independent_check_count=3,
        max_check_rmse=1.0,
        spatial_coverage_passed=True,
    )
    assert crs.passed
    assert AutoDecisionPolicy().evaluate(
        pack, operation, (*base_reports, crs),
    ).disposition == "AUTO_ACCEPTED"

    no_checks = validate_crs_candidate(
        pack,
        operation,
        model_family="tps",
        training_rmse=0.0,
        independent_check_rmse=0.0,
        independent_check_count=0,
        max_check_rmse=1.0,
        spatial_coverage_passed=True,
    )
    assert not no_checks.passed
    assert AutoDecisionPolicy().evaluate(
        pack, operation, (*base_reports, no_checks),
    ).disposition == "REJECTED"


def test_executor_applies_only_existing_label_evidence() -> None:
    graph = _stage_graph()
    nodes = graph.logical_index
    operation = RepairOperation.create(
        operation="attach_existing_label",
        entity_node_ids=[
            nodes["feature:cable:1"].node_id,
            nodes["entity:label"].node_id,
        ],
        evidence_node_ids=[nodes["entity:label"].node_id],
        parameters={"policy_id": "label-v1"},
        confidence=0.95,
        agreement_count=1,
        rationale_sha256=RATIONALE_SHA,
    )
    pack = _pack(graph, operation)
    entity = SourceEntity(
        entity_key="entity:1", source_sha256=SOURCE_SHA, source_file="drawing.dwg",
        handle="10A", layout="Model", layout_role="model", cad_role="model",
        layer="CABLE", object_name="AcDbPolyline", dwg_type="LWPOLYLINE",
        points=((0.0, 0.0), (10.0, 0.0)), centroid=(5.0, 0.0), closed=False,
        text="", block_name="", block_attributes={}, style=CadStyle(), native_length=10.0,
    )
    label = SourceEntity(
        entity_key="entity:label", source_sha256=SOURCE_SHA, source_file="drawing.dwg",
        handle="10B", layout="Model", layout_role="model", cad_role="model",
        layer="LABEL", object_name="AcDbText", dwg_type="TEXT",
        points=((5.0, 1.0),), centroid=(5.0, 1.0), closed=False,
        text="MR.DMPH.P104", block_name="", block_attributes={}, style=CadStyle(),
    )
    feature = Feature(
        feature_key="feature:cable:1", feature_class="CABLE",
        geometry_kind="LINESTRING", native_points=[(0.0, 0.0), (10.0, 0.0)],
        source_entity_key="entity:1", source_handle="10A", source_layer="CABLE",
        geometry_role="SOURCE_ROUTE", style=CadStyle(),
    )
    result = execute_decision_pack(
        graph=graph, pack=pack, entities=[entity, label], features=[feature], relations=[],
    )
    assert result.applied_count == 1
    assert result.unresolved_count == 0
    assert feature.display_label == ""
    assert result.features[0].display_label == "MR.DMPH.P104"
    assert result.features[0].label_provenance.startswith("decision_pack:")


def _join_fixture():
    entities = [
        SourceEntity(
            entity_key=f"entity:{index}", source_sha256=SOURCE_SHA,
            source_file="drawing.dwg", handle=f"20{index}", layout="Model",
            layout_role="model", cad_role="model", layer="CABLE",
            object_name="AcDbPolyline", dwg_type="LWPOLYLINE",
            points=points, centroid=points[0], closed=False, text="",
            block_name="", block_attributes={}, style=CadStyle(),
            native_length=10.0,
        )
        for index, points in (
            (1, ((0.0, 0.0), (10.0, 0.0))),
            (2, ((10.2, 0.0), (20.2, 0.0))),
        )
    ]
    features = [
        Feature(
            feature_key=f"feature:cable:{index}", feature_class="CABLE",
            geometry_kind="LINESTRING", native_points=list(entity.points),
            source_entity_key=entity.entity_key, source_handle=entity.handle,
            source_layer=entity.layer, geometry_role="SOURCE_ROUTE",
            style=entity.style,
        )
        for index, entity in enumerate(entities, start=1)
    ]
    graph = build_stage_evidence_graph(
        source_sha256=SOURCE_SHA,
        entities=entities,
        features=features,
        relations=[],
        unresolved=[],
    )
    return graph, entities, features


def test_executor_joins_observed_endpoints_as_derived_relation() -> None:
    graph, entities, features = _join_fixture()
    candidate = min(
        endpoint_pair_candidates(graph, features[0], features[1]),
        key=lambda item: item.distance_native_m,
    )
    nodes = graph.logical_index
    operation = RepairOperation.create(
        operation="join_observed_endpoints",
        entity_node_ids=[
            nodes[features[0].feature_key].node_id,
            nodes[features[1].feature_key].node_id,
        ],
        evidence_node_ids=[
            nodes[entities[0].entity_key].node_id,
            nodes[entities[1].entity_key].node_id,
        ],
        parameters={
            "endpoint_pair_id": candidate.candidate_id,
            "policy_id": "snap-v1",
        },
        confidence=0.96,
        agreement_count=2,
        rationale_sha256=RATIONALE_SHA,
    )
    original_points = [list(feature.native_points) for feature in features]
    result = execute_decision_pack(
        graph=graph,
        pack=_pack(graph, operation),
        entities=entities,
        features=features,
        relations=[],
    )

    assert result.applied_count == 1
    assert [feature.native_points for feature in result.features] == original_points
    assert [feature.native_points for feature in features] == original_points
    assert len(result.relations) == 1
    relation = result.relations[0]
    assert relation.relation_kind == "derived_endpoint_connection"
    assert relation.distance_native_m == pytest.approx(0.2)
    assert relation.source_key.endswith(":endpoint:end")
    assert relation.target_key.endswith(":endpoint:start")
    assert all(report.passed for report in result.reports)
    summary = result.executions[0].to_dict()["simulation_summary"]
    assert summary["source_geometry_changed"] is False
    assert summary["native_length_changed"] is False
    receipt = result.receipt_dict()
    assert receipt["source_geometry_mutated"] is False
    assert receipt["derived_relations"][0]["relation_key"] == relation.relation_key
    network = result.derived_network_dict()
    assert network["relation_count"] == 1
    assert network["native_lengths_mutated"] is False


def test_endpoint_join_rejects_unregistered_candidate_without_mutation() -> None:
    graph, entities, features = _join_fixture()
    nodes = graph.logical_index
    operation = RepairOperation.create(
        operation="join_observed_endpoints",
        entity_node_ids=[
            nodes[features[0].feature_key].node_id,
            nodes[features[1].feature_key].node_id,
        ],
        evidence_node_ids=[
            nodes[entities[0].entity_key].node_id,
            nodes[entities[1].entity_key].node_id,
        ],
        parameters={
            "endpoint_pair_id": "pair:not-in-graph",
            "policy_id": "snap-v1",
        },
        confidence=0.99,
        agreement_count=2,
        rationale_sha256=RATIONALE_SHA,
    )
    result = execute_decision_pack(
        graph=graph, pack=_pack(graph, operation), entities=entities,
        features=features, relations=[],
    )
    assert result.applied_count == 0
    assert result.executions[0].disposition == "REJECTED"
    assert result.relations == ()


def test_executor_materializes_curve_from_reader_facts_only() -> None:
    curve_facts = {
        "schema_version": "cad2gis-curve-facts-v1",
        "coordinate_system": "WCS",
        "primitive_type": "LWPOLYLINE",
        "vertices_wcs": [[0.0, 0.0, 0.0], [10.0, 0.0, 0.0]],
        "bulges": [0.0, 0.0],
        "closed": False,
        "primitive_parameters": {},
        "native_length": 10.0,
        "native_length_source": "fixture",
    }
    entity = SourceEntity(
        entity_key="entity:curve", source_sha256=SOURCE_SHA,
        source_file="drawing.dwg", handle="30A", layout="Model",
        layout_role="model", cad_role="model", layer="CABLE",
        object_name="AcDbPolyline", dwg_type="LWPOLYLINE",
        points=((0.0, 0.0), (10.0, 0.0)), centroid=(5.0, 0.0),
        closed=False, text="", block_name="", block_attributes={},
        style=CadStyle(), native_length=10.0, curve_facts=curve_facts,
    )
    feature = Feature(
        feature_key="feature:curve", feature_class="CABLE",
        geometry_kind="LINESTRING", native_points=list(entity.points),
        source_entity_key=entity.entity_key, source_handle=entity.handle,
        source_layer=entity.layer, geometry_role="SOURCE_ROUTE",
        style=entity.style,
    )
    graph = build_stage_evidence_graph(
        source_sha256=SOURCE_SHA, entities=[entity], features=[feature],
        relations=[], unresolved=[],
    )
    nodes = graph.logical_index
    operation = RepairOperation.create(
        operation="materialize_native_curve",
        entity_node_ids=[nodes[feature.feature_key].node_id],
        evidence_node_ids=[nodes[entity.entity_key].node_id],
        parameters={
            "materializer_id": CURVE_MATERIALIZER_ID,
            "policy_id": MATERIALIZATION_POLICY_VERSION,
        },
        confidence=0.96,
        agreement_count=2,
        rationale_sha256=RATIONALE_SHA,
    )
    result = execute_decision_pack(
        graph=graph, pack=_pack(graph, operation), entities=[entity],
        features=[feature], relations=[],
    )
    assert result.applied_count == 1
    assert "curve_materialization" not in feature.attributes
    assert result.features[0].native_points == feature.native_points
    assert result.features[0].attributes["curve_materialization"][
        "source_native_length"
    ] == pytest.approx(10.0)
    assert all(report.passed for report in result.reports)


def _network_candidate_fixture(kind: str):
    if kind == "crossing_candidate":
        point_sets = (
            ((-1.0, 0.0), (1.0, 0.0)),
            ((0.0, -1.0), (0.0, 1.0)),
        )
    else:
        point_sets = (
            ((0.0, 0.0), (10.0, 0.0)),
            ((5.0, 0.0), (15.0, 0.0)),
        )
    entities = []
    features = []
    for index, points in enumerate(point_sets, start=1):
        entity = SourceEntity(
            entity_key=f"entity:network:{index}",
            source_sha256=SOURCE_SHA,
            source_file="drawing.dwg",
            handle=f"40{index}",
            layout="Model",
            layout_role="model",
            cad_role="model",
            layer="CABLE",
            object_name="AcDbPolyline",
            dwg_type="LWPOLYLINE",
            points=points,
            centroid=points[0],
            closed=False,
            text="",
            block_name="",
            block_attributes={},
            style=CadStyle(),
            native_length=math.dist(*points),
        )
        entities.append(entity)
        features.append(Feature(
            feature_key=f"feature:network:{index}",
            feature_class="CABLE",
            geometry_kind="LINESTRING",
            native_points=list(points),
            source_entity_key=entity.entity_key,
            source_handle=entity.handle,
            source_layer=entity.layer,
            geometry_role="SOURCE_ROUTE",
            style=entity.style,
        ))
    relation = Relation(
        relation_key=f"relation:{kind}",
        relation_kind=kind,
        source_key=f"{features[0].feature_key}:segment:0",
        target_key=f"{features[1].feature_key}:segment:0",
        status="candidate",
        method=f"deterministic-{kind}",
        distance_native_m=0.0,
        evidence_keys=tuple(entity.entity_key for entity in entities),
    )
    graph = build_stage_evidence_graph(
        source_sha256=SOURCE_SHA, entities=entities, features=features,
        relations=[relation], unresolved=[],
    )
    edge = next(item for item in graph.edges if item.kind == kind)
    return graph, entities, features, relation, edge


@pytest.mark.parametrize(
    ("candidate_kind", "operation_name", "parameter_name", "policy_id",
     "derived_kind"),
    [
        (
            "crossing_candidate",
            "split_at_observed_intersection",
            "intersection_evidence_id",
            "intersection-connect-v1",
            "derived_intersection_incidence",
        ),
        (
            "collinear_overlap_candidate",
            "merge_collinear_fragments",
            "group_id",
            "collinear-group-v1",
            "derived_collinear_group_member",
        ),
    ],
)
def test_executor_builds_derived_network_projection_without_revertexing(
    candidate_kind,
    operation_name,
    parameter_name,
    policy_id,
    derived_kind,
) -> None:
    graph, entities, features, relation, edge = _network_candidate_fixture(
        candidate_kind,
    )
    nodes = graph.logical_index
    operation = RepairOperation.create(
        operation=operation_name,
        entity_node_ids=[
            nodes[feature.feature_key].node_id for feature in features
        ],
        evidence_node_ids=[
            nodes[entity.entity_key].node_id for entity in entities
        ],
        parameters={
            parameter_name: edge.edge_id,
            "policy_id": policy_id,
        },
        confidence=0.96,
        agreement_count=2,
        rationale_sha256=RATIONALE_SHA,
    )
    original_points = [list(feature.native_points) for feature in features]
    result = execute_decision_pack(
        graph=graph, pack=_pack(graph, operation), entities=entities,
        features=features, relations=[relation],
    )

    assert result.applied_count == 1
    assert [feature.native_points for feature in result.features] == original_points
    derived = [
        item for item in result.relations
        if item.relation_kind == derived_kind
    ]
    assert len(derived) == 2
    assert all(report.passed for report in result.reports)
    summary = result.executions[0].to_dict()["simulation_summary"]
    assert summary["source_geometry_changed"] is False
    assert summary["native_length_changed"] is False


def test_executor_quarantines_geometry_without_registered_simulator() -> None:
    graph = _stage_graph()
    nodes = graph.logical_index
    operation = RepairOperation.create(
        operation="reverse_edge_direction",
        entity_node_ids=[nodes["feature:cable:1"].node_id],
        evidence_node_ids=[nodes["entity:1"].node_id],
        parameters={"policy_id": "direction-v1"},
        confidence=0.99,
        agreement_count=2,
        rationale_sha256=RATIONALE_SHA,
    )
    pack = _pack(graph, operation)
    result = execute_decision_pack(
        graph=graph, pack=pack, entities=[], features=[], relations=[],
    )
    assert result.applied_count == 0
    assert result.unresolved_count == 1
    assert result.executions[0].disposition == "QUARANTINED"


def test_conversion_snapshot_freezes_decision_pack_bytes(tmp_path) -> None:
    source = tmp_path / "source.dwg"
    source_profile = tmp_path / "source_profile.json"
    mapping_registry = tmp_path / "mapping_registry.json"
    decision_pack = tmp_path / "decision_pack.json"
    source.write_bytes(b"dwg")
    source_profile.write_text("{}", encoding="utf-8")
    mapping_registry.write_text("{}", encoding="utf-8")
    decision_pack.write_text('{"pack":"v1"}', encoding="utf-8")

    snapshot = freeze_conversion_snapshot(
        source,
        source_profile,
        mapping_registry,
        decision_pack=decision_pack,
        code_root=tmp_path,
        code_paths=(),
    )
    assert snapshot["decision_pack_sha256"] == snapshot["artifacts"]["decision_pack"]["sha256"]
    assert verify_conversion_snapshot(
        snapshot, decision_pack=decision_pack, code_root=tmp_path,
    )["verified"] is True

    decision_pack.write_text('{"pack":"tampered"}', encoding="utf-8")
    with pytest.raises(SnapshotVerificationError, match="decision_pack"):
        verify_conversion_snapshot(
            snapshot, decision_pack=decision_pack, code_root=tmp_path,
        )


def test_mcp_services_page_evidence_and_create_bound_pack(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CAD2GIS_PROJECT_ROOT", str(tmp_path))
    graph = _stage_graph()
    graph_path = tmp_path / "reasoning" / "evidence_graph.json"
    graph_path.parent.mkdir()
    graph_path.write_text(json.dumps(graph.to_dict()), encoding="utf-8")

    page = list_evidence_nodes(str(graph_path), kind="feature", limit=10)
    assert page["total"] == 1
    node = get_evidence_node(str(graph_path), page["nodes"][0]["node_id"])
    assert node["logical_id"] == "feature:cable:1"
    assert "attach_existing_label" in list_registered_operations()["operations"]

    label_id = graph.logical_index["entity:label"].node_id
    feature_id = graph.logical_index["feature:cable:1"].node_id
    pack_path = tmp_path / "reasoning" / "decision-pack.json"
    created = mcp_create_decision_pack(
        str(graph_path),
        str(pack_path),
        POLICY_ID,
        {
            "provider": "test",
            "model": "fixture",
            "protocol": "mcp",
            "request_sha256": REQUEST_SHA,
            "response_sha256": RESPONSE_SHA,
        },
        [{
            "operation": "attach_existing_label",
            "entity_node_ids": [feature_id, label_id],
            "evidence_node_ids": [label_id],
            "parameters": {"policy_id": "label-v1"},
            "confidence": 0.95,
            "agreement_count": 1,
            "rationale_sha256": RATIONALE_SHA,
        }],
    )
    assert created["operation_count"] == 1
    assert mcp_validate_decision_pack(str(graph_path), str(pack_path))["valid"] is True

    with pytest.raises(MCPServiceError, match="outside configured"):
        list_evidence_nodes(str(tmp_path.parent / "other" / "graph.json"))


def test_mcp_lists_source_derived_endpoint_candidates(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CAD2GIS_PROJECT_ROOT", str(tmp_path))
    graph, _, features = _join_fixture()
    graph_path = tmp_path / "reasoning" / "evidence_graph.json"
    graph_path.parent.mkdir()
    graph_path.write_text(json.dumps(graph.to_dict()), encoding="utf-8")
    nodes = graph.logical_index

    payload = list_endpoint_join_candidates(
        str(graph_path),
        nodes[features[0].feature_key].node_id,
        nodes[features[1].feature_key].node_id,
    )

    assert payload["evidence_graph_sha256"] == graph.graph_sha256
    assert len(payload["candidates"]) == 4
    closest = min(
        payload["candidates"], key=lambda item: item["distance_native_m"],
    )
    assert closest["distance_native_m"] == pytest.approx(0.2)
    assert closest["candidate_id"].startswith("epc_")


def test_mcp_lists_network_repair_candidates(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CAD2GIS_PROJECT_ROOT", str(tmp_path))
    graph, _, _, _, edge = _network_candidate_fixture(
        "crossing_candidate",
    )
    graph_path = tmp_path / "reasoning" / "evidence_graph.json"
    graph_path.parent.mkdir()
    graph_path.write_text(json.dumps(graph.to_dict()), encoding="utf-8")

    payload = list_network_repair_candidates(str(graph_path))

    assert payload["candidates"] == [{
        "candidate_edge_id": edge.edge_id,
        "kind": "crossing_candidate",
        "source_segment_key": "feature:network:1:segment:0",
        "target_segment_key": "feature:network:2:segment:0",
        "evidence_node_ids": list(edge.evidence_node_ids),
        "operation": "split_at_observed_intersection",
        "parameter": "intersection_evidence_id",
        "policy_ids": ["intersection-connect-v1"],
    }]
