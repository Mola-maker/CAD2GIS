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


def _effective_cad_role(entity: SourceEntity, route_pattern) -> str:
    """Cad role with the reviewed route-layer exemption applied.

    Reader heuristics reclassify route geometry by drawing position; when the
    reviewed registry marks the entity's layer as a route layer and the reader
    recorded reclassification provenance, the original role decides instead.
    Records without provenance (older bundles) keep current behaviour.
    """

    if (
        route_pattern is not None
        and route_pattern.search(entity.layer)
        and entity.raw_properties.get("cad_role_original")
    ):
        return str(entity.raw_properties["cad_role_original"])
    return entity.cad_role


def _root_entities(
    entities: Sequence[SourceEntity],
    route_pattern=None,
) -> tuple[list[SourceEntity], str]:
    model_candidates = [
        entity
        for entity in entities
        if entity.layout_role.casefold() == "model"
        and entity.dwg_type.upper() not in _NON_ENTITY_TYPES
    ]
    if any(entity.cad_role.casefold() == "model" for entity in model_candidates):
        # At least one entity kept its original "model" role, so the reader
        # heuristics did not blanket-reclassify the layout; the route-layer
        # exemption may legitimately rescue reclassified route geometry.
        preferred_model = [
            entity
            for entity in model_candidates
            if _effective_cad_role(entity, route_pattern).casefold() == "model"
        ]
        return sorted(preferred_model, key=lambda entity: entity.entity_key), (
            "cad-role-partition"
        )
    if model_candidates:
        # Some vendor drawings place the actual plan in Model space but their
        # title/legend heuristics cover every entity.  Falling back to the
        # authoritative layout domain is explicit and triggers strict block
        # expansion below; it is never a silent role rewrite.  This guard must
        # stay reachable even when the route-layer exemption would restore a
        # handful of route entities, otherwise the plan domain silently
        # shrinks to route layers only.
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
        # Same blanket-reclassification guard as the model branch above: only
        # honour the route-layer exemption when at least one entity kept its
        # original "plan" role.
        preferred_plan = [
            entity
            for entity in plan_candidates
            if _effective_cad_role(entity, route_pattern).casefold() == "plan"
        ]
        return sorted(preferred_plan, key=lambda entity: entity.entity_key), (
            "plan-layout-partition"
        )
    if plan_candidates:
        return sorted(plan_candidates, key=lambda entity: entity.entity_key), (
            "plan-layout-fallback"
        )
    return [], "unavailable"


def _detect_orphan_definitions(
    definitions: Mapping[str, list[SourceEntity]],
    inventory: Sequence[SourceEntity],
    root_layouts: Sequence[str],
) -> tuple[list[dict[str, Any]], list[str], set[str]]:
    """Find block definitions never INSERT-referenced in the root layouts.

    An orphan definition holds entity records but no INSERT inside the
    selected root layouts references it, so the expansion walk never reaches
    its members.  An orphan *root* is an orphan that no other orphan
    references; nested orphans roll up into their referencing root so members
    are never double-counted.  Returns the per-root entries, the sorted
    deduplicated member entity keys, and the full orphan name set.
    """

    layouts = set(root_layouts)
    referenced = {
        _block_name(entity).upper()
        for entity in inventory
        if entity.layout in layouts and entity.dwg_type.upper() == "INSERT"
    }
    referenced.discard("")
    orphan_names = {name for name in definitions if name not in referenced}
    if not orphan_names:
        return [], [], set()
    nested_referenced = {
        target
        for name in orphan_names
        for member in definitions[name]
        if member.dwg_type.upper() == "INSERT"
        for target in [_block_name(member).upper()]
        if target in orphan_names and target != name
    }
    roots = sorted(orphan_names - nested_referenced)

    entries: list[dict[str, Any]] = []
    member_keys: set[str] = set()
    for name in roots:
        keys: set[str] = set()
        layers: Counter[str] = Counter()
        nested_insert_count = 0
        pending = [name]
        seen: set[str] = set()
        while pending:
            current = pending.pop()
            if current in seen:
                continue
            seen.add(current)
            for member in definitions.get(current, ()):
                keys.add(member.entity_key)
                layers[member.layer] += 1
                if member.dwg_type.upper() == "INSERT":
                    nested_insert_count += 1
                    target = _block_name(member).upper()
                    if target in definitions:
                        pending.append(target)
        entries.append({
            "block_name": name,
            "member_count": len(keys),
            "layer_distribution": dict(
                sorted(layers.items(), key=lambda item: (-item[1], item[0]))[:10]
            ),
            "nested_insert_count": nested_insert_count,
        })
        member_keys.update(keys)
    return entries, sorted(member_keys), orphan_names


def _definition_closure(
    definitions: Mapping[str, list[SourceEntity]],
    root_name: str,
) -> set[str]:
    """All definition names reachable from ``root_name`` via nested INSERTs."""

    reachable: set[str] = set()
    pending = [root_name]
    while pending:
        current = pending.pop()
        if current in reachable:
            continue
        reachable.add(current)
        for member in definitions.get(current, ()):
            if member.dwg_type.upper() == "INSERT":
                target = _block_name(member).upper()
                if target in definitions:
                    pending.append(target)
    return reachable


def _block_base_point(
    members: Sequence[SourceEntity],
) -> tuple[float, ...] | None:
    """First available block base point from member transform facts."""

    for member in members:
        facts = member.raw_properties.get("transform_facts")
        if not isinstance(facts, Mapping):
            continue
        value = facts.get("block_base_point")
        if value is None:
            continue
        try:
            point = tuple(float(component) for component in value)
        except (TypeError, ValueError):
            continue
        if point:
            return point
    return None


def build_plan_domain(
    raw_entities: Iterable[SourceEntity],
    *,
    require_complete_fallback: bool = True,
    route_layer_pattern=None,
    plan_layouts: tuple[str, ...] = (),
    include_orphan_blocks: tuple[str, ...] | str | None = None,
    excluded_legend_entity_keys: Iterable[str] | None = None,
) -> PlanDomainView:
    """Build the exact drawing-space view consumed by semantic conversion."""

    inventory = tuple(raw_entities)
    declared_layouts = {str(name).casefold() for name in plan_layouts}
    declared_keys: set[str] = set()
    selection_input = inventory
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
                declared_view.append(
                    replace(
                        entity,
                        layout_role="plan",
                        raw_properties=raw_properties,
                    )
                )
                declared_keys.add(entity.entity_key)
            else:
                declared_view.append(entity)
        selection_input = tuple(declared_view)
    roots, selection_mode = _root_entities(
        selection_input, route_pattern=route_layer_pattern,
    )
    if declared_keys:
        # Reviewed declarations admit paper layouts on equal footing with the
        # model/plan hierarchy, which would otherwise never reach them
        # whenever Model-space candidates exist.
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
        "root_layouts": sorted({entity.layout for entity in roots}),
        "issues": [],
        "status": "PASS",
    }
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
        definitions, inventory, diagnostics["root_layouts"],
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
                f"{entry['member_count']} entities but no INSERT in the "
                "selected root layouts references it."
            ),
            "block_name": entry["block_name"],
            "member_count": entry["member_count"],
            "layer_distribution": entry["layer_distribution"],
            "nested_insert_count": entry["nested_insert_count"],
        })

    undeclared_layout_entities: Counter[str] = Counter()
    for entity in inventory:
        if (
            entity.layout_role.casefold() == "layout"
            and entity.dwg_type.upper() not in _NON_ENTITY_TYPES
            and entity.layout.casefold() not in declared_layouts
        ):
            undeclared_layout_entities[entity.layout] += 1
    diagnostics["plan_layouts"] = {
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
                "declaration hold entities that never enter the plan domain."
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
        materialization: dict[str, Any] = {}
        prior_record = raw_properties.get("plan_domain")
        if isinstance(prior_record, Mapping):
            materialization.update(prior_record)
        materialization.update({
            "schema_version": PLAN_DOMAIN_SCHEMA_VERSION,
            "materialization": "layout-root-role-normalization",
            "source_cad_role": root.cad_role,
            "root_entity_key": root.entity_key,
        })
        if effective_role != root.cad_role:
            exempted_root_count += 1
            materialization["route_layer_exemption"] = {
                "cad_role_original": effective_role,
            }
        raw_properties["plan_domain"] = materialization
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
        diagnostics["expanded_insert_count"] += 1
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
        and selection_mode in {
            "layout-role-fallback",
            "plan-layout-fallback",
        }
        # Reviewed plan_layouts declarations admit paper layouts explicitly;
        # the fallback-strict gate must not veto a reviewed declaration.
        and not any(root.entity_key in declared_keys for root in roots)
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

    if include_orphan_blocks is not None:
        orphan_root_names = {entry["block_name"] for entry in orphan_blocks}
        if include_orphan_blocks == "*":
            configured = sorted(orphan_root_names)
        else:
            configured = sorted(
                {str(name).strip().upper() for name in include_orphan_blocks}
            )
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
                "message": (
                    f"Orphan block recovery skipped for {name}: {reason}."
                ),
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
            if any(abs(component) > _EPSILON for component in base_point):
                skip_recovery(name, "block_base_point_not_origin")
                continue
            synthetic = replace(
                roots[0],
                entity_key=f"orphan-recovery-root:{name}",
                handle=f"orphan-recovery-root:{name}",
                dwg_type="INSERT",
                object_name="ACDBBLOCKREFERENCE",
                cad_role="model",
                block_name=name,
                points=((0.0, 0.0),),
                centroid=(0.0, 0.0),
                raw_properties={
                    "transform_facts": {
                        "insertion_point": (0.0, 0.0, 0.0),
                        "block_base_point": (0.0, 0.0, 0.0),
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
            if any(
                item["code"] == "unsupported_block_geometry_transform"
                for item in diagnostics["issues"][issue_marker:]
            ):
                del derived[derived_marker:]
                skip_recovery(name, "unsupported_block_geometry_transform")
                continue
            for entity in derived[derived_marker:]:
                entity.raw_properties["provenance"] = {
                    "orphan_block_recovery": name,
                }
            recovery["recovered"].append(name)
            closure = _definition_closure(definitions, name)
            covered.update(closure)
            recovered_member_keys.update(
                member.entity_key
                for definition in closure
                for member in definitions[definition]
            )
        if recovered_member_keys:
            diagnostics["orphan_member_entity_keys"] = [
                key
                for key in diagnostics["orphan_member_entity_keys"]
                if key not in recovered_member_keys
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
    if route_layer_pattern is not None:
        catalog_candidates, scene_partition = detect_style_catalog_entities(
            roots,
            exempt=lambda entity: route_layer_pattern.search(entity.layer),
        )
    else:
        catalog_candidates, scene_partition = detect_style_catalog_entities(roots)
    # Geometric catalog detection is evidence, not deletion authority.  The
    # same regular arrangements occur in real networks and in unfamiliar
    # vendor drawings.  Only source-bound reviewed exclusions below may remove
    # a candidate from the semantic plan-domain view.
    scene_partition = {
        **scene_partition,
        "detector_status": scene_partition.get("status"),
        "status": (
            "CANDIDATES_ONLY" if catalog_candidates else "NO_CANDIDATES"
        ),
        "candidate_entity_keys": sorted(catalog_candidates),
        "candidate_entity_count": len(catalog_candidates),
        "automatic_exclusion_applied": False,
    }
    if excluded_legend_entity_keys is not None:
        legend_declared = list(
            dict.fromkeys(str(key) for key in excluded_legend_entity_keys)
        )
        roots_by_key = {entity.entity_key: entity for entity in roots}
        legend_blocking = False
        for key in legend_declared:
            root = roots_by_key.get(key)
            if root is None or root.dwg_type.upper() != "INSERT":
                legend_blocking = True
                diagnostics["issues"].append({
                    "code": "legend_exclusion_not_insert_root",
                    "severity": "blocking",
                    "blocking": True,
                    "entity_key": key,
                    "message": (
                        f"Declared legend exclusion {key!r} is not a plan-"
                        "domain root INSERT entity; legend exclusions must "
                        "name reviewed root symbol INSERTs."
                    ),
                })
                continue
            if (
                route_layer_pattern is not None
                and route_layer_pattern.search(root.layer)
            ):
                legend_blocking = True
                diagnostics["issues"].append({
                    "code": "legend_exclusion_route_layer",
                    "severity": "blocking",
                    "blocking": True,
                    "entity_key": key,
                    "layer": root.layer,
                    "message": (
                        f"Declared legend exclusion {key!r} sits on route "
                        f"layer {root.layer!r}; route-layer entities are "
                        "reviewed network content and cannot be excluded "
                        "as legend symbols."
                    ),
                })
        legend_excluded: set[str] = set()
        if not legend_blocking and legend_declared:
            declared_set = set(legend_declared)
            kept: list[SourceEntity] = []
            for entity in output:
                root_key = str(
                    (entity.raw_properties.get("plan_domain") or {}).get(
                        "root_entity_key", ""
                    )
                )
                if entity.entity_key in declared_set or root_key in declared_set:
                    legend_excluded.add(entity.entity_key)
                    continue
                kept.append(entity)
            output = kept
        diagnostics["legend_exclusions"] = {
            "declared": legend_declared,
            "excluded": sorted(legend_excluded),
            "declared_count": len(legend_declared),
        }
        if legend_blocking:
            diagnostics["status"] = "FAIL"
            raise PlanDomainError(
                "Plan-domain legend exclusion failed closed: "
                "declared legend entity keys are not eligible root INSERTs",
                diagnostics,
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
    return PlanDomainView(tuple(output), diagnostics)


__all__ = [
    "PLAN_DOMAIN_SCHEMA_VERSION",
    "PlanDomainError",
    "PlanDomainView",
    "build_plan_domain",
]
