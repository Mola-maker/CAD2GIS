"""Deterministic materialized plan-domain view over an immutable CAD inventory.

CAD readers expose two different coordinate domains:

* drawing-space entities already expressed in drawing WCS; and
* block-definition entities expressed in a definition-local coordinate frame.

Semantic conversion must never classify definition-local geometry directly,
nor assume that every useful drawing uses ``cad_role == "model"``.  This
module selects drawing roots, expands nested INSERTs from complete reader
transform facts, and emits new lineage-bound ``SourceEntity`` instances.
The original reader inventory is never changed.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

from .model import CadStyle, SourceEntity
from .ports import Affine2D, resolve_insert_affine
from .scene_partition import detect_style_catalog_entities


PLAN_DOMAIN_SCHEMA_VERSION = "cad2gis-plan-domain-v1"
_BLOCK_LAYOUT_PREFIX = "BLOCKDEF:"
_DRAWING_CAD_ROLES = frozenset({"model", "plan"})
_NON_ENTITY_TYPES = frozenset({"DOCUMENT_METADATA", "BLOCK_RECORD"})
_CURVED_TYPES = frozenset({"ARC", "CIRCLE", "ELLIPSE", "SPLINE", "HELIX"})
_EPSILON = 1.0e-9


class PlanDomainError(RuntimeError):
    """Exact plan-domain materialization cannot be completed."""

    def __init__(self, message: str, diagnostics: Mapping[str, Any]):
        super().__init__(message)
        self.diagnostics = dict(diagnostics)


@dataclass(frozen=True)
class PlanDomainView:
    """One immutable derived view and its conservation diagnostics."""

    entities: tuple[SourceEntity, ...]
    diagnostics: dict[str, Any]
    catalog_roots: frozenset[str] = frozenset()


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(payload.encode("ascii")).hexdigest()


def _block_name(entity: SourceEntity) -> str:
    return str(
        entity.raw_properties.get("block_effective_name")
        or entity.raw_properties.get("block_reference_name")
        or entity.block_name
        or ""
    ).strip()


def _definition_name(entity: SourceEntity) -> str:
    layout = entity.layout.strip()
    if layout.upper().startswith(_BLOCK_LAYOUT_PREFIX):
        return layout[len(_BLOCK_LAYOUT_PREFIX):].strip().upper()
    container = str(entity.raw_properties.get("container_block_name", "")).strip()
    return container.upper()


def _is_similarity(affine: Affine2D) -> tuple[bool, float, bool]:
    x_scale = math.hypot(affine.m11, affine.m21)
    y_scale = math.hypot(affine.m12, affine.m22)
    orthogonality = affine.m11 * affine.m12 + affine.m21 * affine.m22
    scale = (x_scale + y_scale) / 2.0
    tolerance = max(1.0, scale) * 1.0e-8
    similarity = (
        scale > _EPSILON
        and abs(x_scale - y_scale) <= tolerance
        and abs(orthogonality) <= tolerance * max(1.0, scale)
    )
    determinant = affine.m11 * affine.m22 - affine.m12 * affine.m21
    return similarity, scale, determinant < 0.0


def _transform_point3(affine: Affine2D, value: Sequence[Any]) -> list[float]:
    x, y = affine.apply((float(value[0]), float(value[1])))
    z = float(value[2]) if len(value) > 2 else 0.0
    return [x, y, z]


def _transform_curve_facts(
    entity: SourceEntity,
    affine: Affine2D,
) -> tuple[dict[str, Any], float | None]:
    """Transform versioned WCS curve facts without inventing curve geometry."""

    if not entity.curve_facts:
        transformed_points = tuple(affine.apply(point) for point in entity.points)
        if entity.native_length is None:
            return {}, None
        if len(transformed_points) >= 2:
            length = math.fsum(
                math.dist(left, right)
                for left, right in zip(transformed_points, transformed_points[1:])
            )
            if entity.closed and len(transformed_points) > 2:
                length += math.dist(transformed_points[-1], transformed_points[0])
            return {}, length
        return {}, entity.native_length

    similarity, scale, reflected = _is_similarity(affine)
    facts = copy.deepcopy(entity.curve_facts)
    primitive = str(facts.get("primitive_type", entity.dwg_type)).upper()
    bulges = [float(value) for value in facts.get("bulges", ())]
    has_curvature = primitive in _CURVED_TYPES or any(
        abs(value) > _EPSILON for value in bulges
    )
    parameters = facts.get("primitive_parameters")
    reader_segments = (
        parameters.get("delivery_segments_wcs")
        if isinstance(parameters, Mapping)
        else None
    )
    if not similarity and (has_curvature or reader_segments):
        raise ValueError(
            "non-uniform INSERT scaling of curved geometry requires a reader-"
            "materialized world-space curve contract"
        )

    facts["vertices_wcs"] = [
        _transform_point3(affine, point)
        for point in facts.get("vertices_wcs", ())
    ]
    if reflected and bulges:
        facts["bulges"] = [-value for value in bulges]
    if isinstance(parameters, dict) and isinstance(reader_segments, (list, tuple)):
        transformed_segments = []
        for segment in reader_segments:
            if not isinstance(segment, Mapping):
                raise ValueError("delivery_segments_wcs contains a non-object")
            transformed = dict(segment)
            transformed["points_wcs"] = [
                _transform_point3(affine, point)
                for point in segment.get("points_wcs", ())
            ]
            if transformed.get("native_length") is not None:
                transformed_points = transformed["points_wcs"]
                transformed["native_length"] = (
                    float(str(transformed["native_length"])) * scale
                    if similarity
                    else math.fsum(
                        math.dist(left[:2], right[:2])
                        for left, right in zip(
                            transformed_points, transformed_points[1:]
                        )
                    )
                )
            transformed_segments.append(transformed)
        parameters["delivery_segments_wcs"] = transformed_segments
    transformed_vertices = facts["vertices_wcs"]
    transformed_linear_length = math.fsum(
        math.dist(left[:2], right[:2])
        for left, right in zip(transformed_vertices, transformed_vertices[1:])
    )
    if facts.get("native_length") is not None:
        facts["native_length"] = (
            float(facts["native_length"]) * scale
            if similarity
            else transformed_linear_length
        )
    facts["native_length_source"] = (
        f"{facts.get('native_length_source', '')}|{PLAN_DOMAIN_SCHEMA_VERSION}:"
        "similarity-transform"
    ).strip("|")
    return facts, (
        None
        if entity.native_length is None
        else (
            float(entity.native_length) * scale
            if similarity
            else transformed_linear_length
        )
    )


def _transform_style(style: CadStyle, affine: Affine2D) -> CadStyle:
    cosine, sine = math.cos(style.rotation), math.sin(style.rotation)
    x = affine.m11 * cosine + affine.m12 * sine
    y = affine.m21 * cosine + affine.m22 * sine
    rotation = style.rotation if math.hypot(x, y) <= _EPSILON else math.atan2(y, x)
    return replace(style, rotation=rotation)


def _materialized_key(
    entity: SourceEntity,
    root: SourceEntity,
    path: Sequence[str],
    affine: Affine2D,
) -> str:
    digest = _canonical_sha256({
        "schema_version": PLAN_DOMAIN_SCHEMA_VERSION,
        "source_sha256": entity.source_sha256,
        "root_entity_key": root.entity_key,
        "definition_entity_key": entity.entity_key,
        "instance_path": list(path),
        "affine": [
            affine.m11, affine.m12, affine.m21, affine.m22,
            affine.tx, affine.ty,
        ],
    })
    return f"plan:{digest}"


def _materialize_leaf(
    entity: SourceEntity,
    root: SourceEntity,
    path: Sequence[str],
    affine: Affine2D,
) -> SourceEntity:
    points = tuple(affine.apply(point) for point in entity.points)
    centroid = affine.apply(entity.centroid)
    curve_facts, native_length = _transform_curve_facts(entity, affine)
    key = _materialized_key(entity, root, path, affine)
    raw_properties = copy.deepcopy(entity.raw_properties)
    raw_properties["plan_domain"] = {
        "schema_version": PLAN_DOMAIN_SCHEMA_VERSION,
        "materialization": "nested-insert-affine",
        "root_entity_key": root.entity_key,
        "definition_entity_key": entity.entity_key,
        "instance_path": list(path),
        "affine": {
            "m11": affine.m11,
            "m12": affine.m12,
            "m21": affine.m21,
            "m22": affine.m22,
            "tx": affine.tx,
            "ty": affine.ty,
        },
    }
    return replace(
        entity,
        entity_key=key,
        handle=f"{root.handle}/{entity.handle}",
        layout=root.layout,
        layout_role="model",
        cad_role="model",
        points=points,
        centroid=centroid,
        style=_transform_style(entity.style, affine),
        owner_handle=root.handle,
        native_length=native_length,
        raw_properties=raw_properties,
        curve_facts=curve_facts,
        curve_fingerprint="",
    )


def _effective_cad_role(
    entity: SourceEntity,
    route_layer_pattern: re.Pattern[str] | None,
) -> str:
    """Restore a reader-reclassified role only for a reviewed route layer."""

    original = entity.raw_properties.get("cad_role_original")
    if (
        route_layer_pattern is not None
        and route_layer_pattern.search(entity.layer)
        and isinstance(original, str)
        and original.strip()
    ):
        return original
    return entity.cad_role


def _root_entities(
    entities: Sequence[SourceEntity],
    route_layer_pattern: re.Pattern[str] | None = None,
) -> tuple[list[SourceEntity], str]:
    model_candidates = [
        entity
        for entity in entities
        if entity.layout_role.casefold() == "model"
        and entity.dwg_type.upper() not in _NON_ENTITY_TYPES
    ]
    if any(entity.cad_role.casefold() == "model" for entity in model_candidates):
        preferred_model = [
            entity
            for entity in model_candidates
            if _effective_cad_role(
                entity, route_layer_pattern,
            ).casefold() == "model"
        ]
        return sorted(preferred_model, key=lambda entity: entity.entity_key), (
            "cad-role-partition"
        )
    if model_candidates:
        # Some vendor drawings place the actual plan in Model space but their
        # title/legend heuristics cover every entity.  Falling back to the
        # authoritative layout domain is explicit and triggers strict block
        # expansion below; it is never a silent role rewrite.
        return sorted(model_candidates, key=lambda entity: entity.entity_key), (
            "layout-role-fallback"
        )
    plan_candidates = [
        entity
        for entity in entities
        if entity.layout_role.casefold() == "plan"
        and entity.dwg_type.upper() not in _NON_ENTITY_TYPES
    ]
    if any(entity.cad_role.casefold() == "plan" for entity in plan_candidates):
        preferred_plan = [
            entity
            for entity in plan_candidates
            if _effective_cad_role(
                entity, route_layer_pattern,
            ).casefold() == "plan"
        ]
        return sorted(preferred_plan, key=lambda entity: entity.entity_key), (
            "plan-layout-partition"
        )
    if plan_candidates:
        return sorted(plan_candidates, key=lambda entity: entity.entity_key), (
            "plan-layout-fallback"
        )
    return [], "unavailable"


def _definition_closure(
    definitions: Mapping[str, list[SourceEntity]],
    root_name: str,
) -> set[str]:
    reachable: set[str] = set()
    pending = [root_name]
    while pending:
        current = pending.pop()
        if current in reachable:
            continue
        reachable.add(current)
        for member in definitions.get(current, ()):
            if member.dwg_type.upper() != "INSERT":
                continue
            target = _block_name(member).upper()
            if target in definitions:
                pending.append(target)
    return reachable


def _detect_orphan_definitions(
    definitions: Mapping[str, list[SourceEntity]],
    roots: Sequence[SourceEntity],
) -> tuple[list[dict[str, Any]], list[str], set[str]]:
    """Find definition trees unreachable from selected drawing-space roots."""

    reachable: set[str] = set()
    for root in roots:
        if root.dwg_type.upper() != "INSERT":
            continue
        name = _block_name(root).upper()
        if name in definitions:
            reachable.update(_definition_closure(definitions, name))
    orphan_names = set(definitions) - reachable
    nested_referenced = {
        target
        for name in orphan_names
        for member in definitions[name]
        if member.dwg_type.upper() == "INSERT"
        for target in [_block_name(member).upper()]
        if target in orphan_names and target != name
    }
    orphan_roots = sorted(orphan_names - nested_referenced)
    entries: list[dict[str, Any]] = []
    member_keys: set[str] = set()
    for name in orphan_roots:
        closure = _definition_closure(definitions, name) & orphan_names
        members = [
            member
            for definition in closure
            for member in definitions[definition]
        ]
        keys = {member.entity_key for member in members}
        layers = Counter(member.layer for member in members)
        entries.append({
            "block_name": name,
            "member_count": len(keys),
            "layer_distribution": dict(
                sorted(layers.items(), key=lambda item: (-item[1], item[0]))[:10]
            ),
            "nested_insert_count": sum(
                member.dwg_type.upper() == "INSERT" for member in members
            ),
        })
        member_keys.update(keys)
    return entries, sorted(member_keys), orphan_names


def _block_base_point(
    members: Sequence[SourceEntity],
) -> tuple[float, float, float] | None:
    for member in members:
        facts = member.raw_properties.get("transform_facts")
        if not isinstance(facts, Mapping):
            continue
        value = facts.get("block_base_point")
        if not isinstance(value, (list, tuple)) or len(value) < 2:
            continue
        try:
            point = tuple(float(component) for component in value[:3])
        except (TypeError, ValueError):
            continue
        if len(point) == 2:
            point = (*point, 0.0)
        if all(math.isfinite(component) for component in point):
            return point
    return None


def build_plan_domain(
    raw_entities: Iterable[SourceEntity],
    *,
    require_complete_fallback: bool = True,
    route_layer_pattern: re.Pattern[str] | None = None,
    plan_layouts: tuple[str, ...] = (),
    include_orphan_blocks: tuple[str, ...] | None = None,
    plan_domain_authority: str | None = None,
) -> PlanDomainView:
    """Build the exact drawing-space view consumed by semantic conversion."""

    inventory = tuple(raw_entities)
    requested_plan_layouts = tuple(plan_layouts)
    requested_orphan_blocks = tuple(include_orphan_blocks or ())
    declarations_authorized = (
        plan_domain_authority == "reviewed_source_profile"
    )
    if not declarations_authorized:
        plan_layouts = ()
        include_orphan_blocks = None
    declared_layouts = {name.casefold() for name in plan_layouts}
    declared_keys: set[str] = set()
    selection_input: tuple[SourceEntity, ...] = inventory
    if declared_layouts:
        declared_view: list[SourceEntity] = []
        for entity in inventory:
            if (
                entity.layout_role.casefold() == "layout"
                and entity.layout.casefold() in declared_layouts
                and entity.dwg_type.upper() not in _NON_ENTITY_TYPES
            ):
                raw_properties = copy.deepcopy(entity.raw_properties)
                raw_properties["plan_domain"] = {
                    "schema_version": PLAN_DOMAIN_SCHEMA_VERSION,
                    "materialization": "declared-plan-layout",
                    "declared_layout": entity.layout,
                }
                entity = replace(
                    entity,
                    layout_role="plan",
                    raw_properties=raw_properties,
                )
                declared_keys.add(entity.entity_key)
            declared_view.append(entity)
        selection_input = tuple(declared_view)
    roots, selection_mode = _root_entities(
        selection_input, route_layer_pattern,
    )
    if declared_keys:
        root_keys = {entity.entity_key for entity in roots}
        roots.extend(
            entity
            for entity in selection_input
            if entity.entity_key in declared_keys
            and entity.entity_key not in root_keys
        )
    definitions: dict[str, list[SourceEntity]] = {}
    for entity in inventory:
        name = _definition_name(entity)
        if name and entity.dwg_type.upper() not in _NON_ENTITY_TYPES:
            definitions.setdefault(name, []).append(entity)
    for values in definitions.values():
        values.sort(key=lambda entity: entity.entity_key)

    diagnostics: dict[str, Any] = {
        "schema_version": PLAN_DOMAIN_SCHEMA_VERSION,
        "selection_mode": selection_mode,
        "raw_entity_count": len(inventory),
        "selected_root_count": len(roots),
        "definition_count": len(definitions),
        "derived_entity_count": 0,
        "expanded_insert_count": 0,
        "expanded_nested_insert_count": 0,
        "root_layouts": sorted({entity.layout for entity in roots}),
        "plan_domain_authority": plan_domain_authority,
        "issues": [],
        "status": "PASS",
    }
    if (
        requested_plan_layouts or requested_orphan_blocks
    ) and not declarations_authorized:
        diagnostics["issues"].append({
            "code": "unreviewed_plan_domain_declaration",
            "severity": "warning",
            "blocking": False,
            "message": (
                "Plan-layout and orphan-block declarations remain "
                "evidence-only until the source profile is reviewed."
            ),
            "requested_plan_layouts": list(requested_plan_layouts),
            "requested_orphan_blocks": list(requested_orphan_blocks),
        })
    if not roots:
        diagnostics["status"] = "FAIL"
        diagnostics["issues"].append({
            "code": "plan_domain_unavailable",
            "severity": "blocking",
            "blocking": True,
            "message": "No model/plan drawing-space entities were present.",
        })
        raise PlanDomainError("Plan-domain selection failed", diagnostics)

    orphan_blocks, orphan_member_keys, orphan_names = _detect_orphan_definitions(
        definitions, roots,
    )
    diagnostics["orphan_blocks"] = orphan_blocks
    diagnostics["orphan_member_entity_keys"] = orphan_member_keys
    for entry in orphan_blocks:
        diagnostics["issues"].append({
            "code": "orphan_block_definition",
            "severity": "warning",
            "blocking": False,
            "message": (
                f"Block definition {entry['block_name']} holds "
                f"{entry['member_count']} entities but is unreachable from "
                "the selected drawing roots."
            ),
            **entry,
        })

    undeclared_layout_entities = Counter(
        entity.layout
        for entity in inventory
        if entity.layout_role.casefold() == "layout"
        and entity.dwg_type.upper() not in _NON_ENTITY_TYPES
        and entity.layout.casefold() not in declared_layouts
    )
    diagnostics["plan_layouts"] = {
        "requested": list(requested_plan_layouts),
        "declared": list(plan_layouts),
        "admitted": sorted(
            {root.layout for root in roots if root.entity_key in declared_keys}
        ),
        "undeclared": dict(sorted(undeclared_layout_entities.items())),
    }
    if undeclared_layout_entities:
        diagnostics["issues"].append({
            "code": "undeclared_layout_entities",
            "severity": "warning",
            "blocking": False,
            "message": (
                "Paper-space layouts outside the reviewed plan_layouts "
                "declaration remain evidence-only."
            ),
            "layouts": dict(sorted(undeclared_layout_entities.items())),
            "entity_count": sum(undeclared_layout_entities.values()),
        })

    output: list[SourceEntity] = []
    exempted_root_count = 0
    for root in roots:
        effective_role = _effective_cad_role(root, route_layer_pattern)
        if (
            effective_role == root.cad_role
            and root.cad_role.casefold() in _DRAWING_CAD_ROLES
        ):
            output.append(root)
            continue
        raw_properties = copy.deepcopy(root.raw_properties)
        plan_domain_record = dict(
            raw_properties.get("plan_domain")
            if isinstance(raw_properties.get("plan_domain"), Mapping)
            else {}
        )
        plan_domain_record.update({
            "schema_version": PLAN_DOMAIN_SCHEMA_VERSION,
            "materialization": "layout-root-role-normalization",
            "source_cad_role": root.cad_role,
            "root_entity_key": root.entity_key,
        })
        if effective_role != root.cad_role:
            exempted_root_count += 1
            plan_domain_record["route_layer_exemption"] = {
                "cad_role_original": effective_role,
            }
        raw_properties["plan_domain"] = plan_domain_record
        output.append(
            replace(root, cad_role="model", raw_properties=raw_properties)
        )
    derived: list[SourceEntity] = []

    def issue(
        *,
        code: str,
        message: str,
        root: SourceEntity,
        entity: SourceEntity,
        blocking: bool,
        **facts: Any,
    ) -> None:
        diagnostics["issues"].append({
            "code": code,
            "severity": "blocking" if blocking else "warning",
            "blocking": blocking,
            "root_entity_key": root.entity_key,
            "entity_key": entity.entity_key,
            "message": message,
            **facts,
        })

    def visit(
        *,
        root: SourceEntity,
        instance: SourceEntity,
        parent: Affine2D | None,
        stack: tuple[str, ...],
        path: tuple[str, ...],
        strict: bool,
    ) -> None:
        name = _block_name(instance).upper()
        if not name:
            issue(
                code="missing_block_reference_name",
                message="INSERT has no authoritative block reference name.",
                root=root,
                entity=instance,
                blocking=strict,
            )
            return
        if name in stack:
            issue(
                code="cyclic_nested_block_definition",
                message="Nested block cycle prevents exact plan-domain expansion.",
                root=root,
                entity=instance,
                blocking=strict,
                block_name=name,
                stack=list(stack),
            )
            return
        local, transform_issues = resolve_insert_affine(instance)
        if local is None:
            issue(
                code="missing_or_invalid_insert_transform",
                message="INSERT expansion requires complete reader transform facts.",
                root=root,
                entity=instance,
                blocking=strict,
                block_name=name,
                causes=transform_issues,
            )
            return
        affine = local if parent is None else parent.compose(local)
        members = definitions.get(name)
        if not members:
            issue(
                code="missing_block_definition",
                message="No reader block-definition inventory exists for INSERT.",
                root=root,
                entity=instance,
                blocking=strict,
                block_name=name,
            )
            return
        if parent is None:
            # DoD issue 4: expanded_insert_count reports exactly the drawing-
            # space (model) INSERT roots.  Nested block INSERTs are accounted
            # separately so a title block full of nested references cannot
            # hide a root-level abstention.
            diagnostics["expanded_insert_count"] += 1
        diagnostics["expanded_nested_insert_count"] += 1
        next_stack = stack + (name,)
        next_path = path + (instance.entity_key,)
        for member in members:
            if member.dwg_type.upper() == "INSERT":
                visit(
                    root=root,
                    instance=member,
                    parent=affine,
                    stack=next_stack,
                    path=next_path,
                    strict=strict,
                )
                continue
            try:
                derived.append(
                    _materialize_leaf(member, root, next_path, affine)
                )
            except (TypeError, ValueError) as exc:
                issue(
                    code="unsupported_block_geometry_transform",
                    message=str(exc),
                    root=root,
                    entity=member,
                    blocking=strict,
                    block_name=name,
                )

    fallback_strict = (
        require_complete_fallback
        and selection_mode in {"layout-role-fallback", "plan-layout-fallback"}
        and not declared_keys
    )
    for root in roots:
        if root.dwg_type.upper() == "INSERT":
            visit(
                root=root,
                instance=root,
                parent=None,
                stack=(),
                path=(),
                strict=fallback_strict,
            )

    if include_orphan_blocks:
        configured = sorted({name.strip().upper() for name in include_orphan_blocks})
        recovery: dict[str, Any] = {
            "configured": configured,
            "recovered": [],
            "skipped": [],
        }
        diagnostics["orphan_recovery"] = recovery
        covered: set[str] = set()
        recovered_member_keys: set[str] = set()

        def skip_recovery(name: str, reason: str) -> None:
            recovery["skipped"].append({"block_name": name, "reason": reason})
            diagnostics["issues"].append({
                "code": "orphan_block_recovery_skipped",
                "severity": "warning",
                "blocking": False,
                "message": f"Orphan block recovery skipped for {name}: {reason}.",
                "block_name": name,
                "reason": reason,
            })

        for name in configured:
            if name not in definitions:
                skip_recovery(name, "unknown_block_definition")
                continue
            if name not in orphan_names:
                skip_recovery(name, "not_orphan_block_definition")
                continue
            if name in covered:
                skip_recovery(name, "covered_by_recovered_root")
                continue
            base_point = _block_base_point(definitions[name])
            if base_point is None:
                skip_recovery(name, "block_base_point_unavailable")
                continue
            synthetic = replace(
                roots[0],
                entity_key=f"orphan-recovery-root:{name}",
                handle=f"orphan-recovery-root:{name}",
                dwg_type="INSERT",
                object_name="ACDBBLOCKREFERENCE",
                cad_role="model",
                block_name=name,
                points=((base_point[0], base_point[1]),),
                centroid=(base_point[0], base_point[1]),
                raw_properties={
                    "transform_facts": {
                        "insertion_point": base_point,
                        "block_base_point": base_point,
                        "scale": (1.0, 1.0, 1.0),
                        "rotation": 0.0,
                        "normal": (0.0, 0.0, 1.0),
                        "extrusion": (0.0, 0.0, 1.0),
                    },
                },
            )
            derived_marker = len(derived)
            issue_marker = len(diagnostics["issues"])
            visit(
                root=synthetic,
                instance=synthetic,
                parent=None,
                stack=(),
                path=(),
                strict=False,
            )
            new_issues = diagnostics["issues"][issue_marker:]
            if new_issues:
                del derived[derived_marker:]
                skip_recovery(name, "incomplete_transform_chain")
                continue
            for index in range(derived_marker, len(derived)):
                recovered = derived[index]
                raw_properties = copy.deepcopy(recovered.raw_properties)
                provenance = dict(
                    raw_properties.get("provenance")
                    if isinstance(raw_properties.get("provenance"), Mapping)
                    else {}
                )
                provenance["orphan_block_recovery"] = {
                    "block_name": name,
                    "authority": "reviewed_source_profile",
                }
                raw_properties["provenance"] = provenance
                plan_record = dict(raw_properties.get("plan_domain") or {})
                plan_record["orphan_block_recovery"] = name
                raw_properties["plan_domain"] = plan_record
                derived[index] = replace(
                    recovered, raw_properties=raw_properties,
                )
            recovery["recovered"].append(name)
            closure = _definition_closure(definitions, name)
            covered.update(closure)
            recovered_member_keys.update(
                member.entity_key
                for definition in closure
                for member in definitions.get(definition, ())
            )
        diagnostics["orphan_member_entity_keys"] = [
            key for key in orphan_member_keys if key not in recovered_member_keys
        ]

    derived.sort(key=lambda entity: entity.entity_key)
    output.extend(derived)
    diagnostics["derived_entity_count"] = len(derived)
    blocking = [
        item for item in diagnostics["issues"] if item.get("blocking") is True
    ]
    if blocking:
        diagnostics["status"] = "FAIL"
        raise PlanDomainError(
            "Plan-domain materialization failed closed: "
            f"{len(blocking)} blocking issue(s)",
            diagnostics,
        )
    if diagnostics["issues"]:
        diagnostics["status"] = "WATCH"
    catalog_candidates = [
        root
        for root in roots
        if route_layer_pattern is None
        or route_layer_pattern.search(root.layer) is None
    ]
    catalog_roots, scene_partition = detect_style_catalog_entities(
        catalog_candidates
    )
    if route_layer_pattern is not None:
        output_keys = {entity.entity_key for entity in output}
        diagnostics["route_layer_exemption"] = {
            "exempted_count": exempted_root_count,
            "route_layer_excluded_count": sum(
                1
                for entity in inventory
                if entity.layout_role.casefold() in {"model", "plan"}
                and entity.dwg_type.upper() not in _NON_ENTITY_TYPES
                and route_layer_pattern.search(entity.layer)
                and entity.entity_key not in output_keys
            ),
        }
    diagnostics["scene_partition"] = scene_partition
    diagnostics["semantic_entity_count"] = len(output)
    return PlanDomainView(tuple(output), diagnostics, catalog_roots=catalog_roots)


__all__ = [
    "PLAN_DOMAIN_SCHEMA_VERSION",
    "PlanDomainError",
    "PlanDomainView",
    "build_plan_domain",
]
