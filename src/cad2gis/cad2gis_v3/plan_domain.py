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


def _root_entities(
    entities: Sequence[SourceEntity],
) -> tuple[list[SourceEntity], str]:
    model_candidates = [
        entity
        for entity in entities
        if entity.layout_role.casefold() == "model"
        and entity.dwg_type.upper() not in _NON_ENTITY_TYPES
    ]
    preferred_model = [
        entity
        for entity in model_candidates
        if entity.cad_role.casefold() == "model"
    ]
    if preferred_model:
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
    preferred_plan = [
        entity
        for entity in plan_candidates
        if entity.cad_role.casefold() == "plan"
    ]
    if preferred_plan:
        return sorted(preferred_plan, key=lambda entity: entity.entity_key), (
            "plan-layout-partition"
        )
    if plan_candidates:
        return sorted(plan_candidates, key=lambda entity: entity.entity_key), (
            "plan-layout-fallback"
        )
    return [], "unavailable"


def build_plan_domain(
    raw_entities: Iterable[SourceEntity],
    *,
    require_complete_fallback: bool = True,
) -> PlanDomainView:
    """Build the exact drawing-space view consumed by semantic conversion."""

    inventory = tuple(raw_entities)
    roots, selection_mode = _root_entities(inventory)
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

    output: list[SourceEntity] = []
    for root in roots:
        if root.cad_role.casefold() in _DRAWING_CAD_ROLES:
            output.append(root)
            continue
        raw_properties = copy.deepcopy(root.raw_properties)
        raw_properties["plan_domain"] = {
            "schema_version": PLAN_DOMAIN_SCHEMA_VERSION,
            "materialization": "layout-root-role-normalization",
            "source_cad_role": root.cad_role,
            "root_entity_key": root.entity_key,
        }
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

    fallback_strict = require_complete_fallback and selection_mode in {
        "layout-role-fallback",
        "plan-layout-fallback",
    }
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
    catalog_roots, scene_partition = detect_style_catalog_entities(roots)
    diagnostics["scene_partition"] = scene_partition
    diagnostics["semantic_entity_count"] = len(output)
    return PlanDomainView(tuple(output), diagnostics, catalog_roots=catalog_roots)


__all__ = [
    "PLAN_DOMAIN_SCHEMA_VERSION",
    "PlanDomainError",
    "PlanDomainView",
    "build_plan_domain",
]
