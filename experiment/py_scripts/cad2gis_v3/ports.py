"""Deterministic block-footprint port candidates.

The reader owns the CAD facts used here.  This module deliberately does not
try to reconstruct an INSERT from a feature centroid, a zero block base point,
or a default orientation.  A port is only proposed when the complete INSERT
transform and an exact, linear block footprint are available.  Missing facts
are returned as structured, blocking diagnostics and source cable geometry is
never changed.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence


_EPSILON = 1.0e-9
_VECTOR_EPSILON = 1.0e-12
_MISSING = object()


Point = tuple[float, float]


def _as_float(value: Any, path: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{path} must be a finite number")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{path} must be a finite number") from exc
    if not math.isfinite(number):
        raise ValueError(f"{path} must be a finite number")
    return number


def _vector(value: Any, path: str, *, dimension: int = 3) -> tuple[float, ...]:
    if isinstance(value, (str, bytes)):
        raise ValueError(f"{path} must be a numeric vector")
    try:
        values = list(value)
    except TypeError as exc:
        raise ValueError(f"{path} must be a numeric vector") from exc
    if len(values) not in ({2, 3} if dimension == 3 else {dimension}):
        raise ValueError(f"{path} must contain {dimension} coordinates")
    result = tuple(_as_float(item, f"{path}[{index}]") for index, item in enumerate(values))
    if dimension == 3 and len(result) == 2:
        return result + (0.0,)
    return result


def _raw_properties(entity: Any) -> dict[str, Any]:
    raw = getattr(entity, "raw_properties", None)
    if raw is None and isinstance(entity, Mapping):
        raw = entity.get("raw_properties")
    return dict(raw) if isinstance(raw, Mapping) else {}


def _entity_value(entity: Any, *names: str) -> Any:
    """Read a fact from raw_properties first, then an explicit object field."""
    raw = _raw_properties(entity)
    nested = raw.get("transform_facts")
    if not isinstance(nested, Mapping):
        nested = raw.get("insert_transform")
    containers: list[Mapping[str, Any]] = []
    if isinstance(nested, Mapping):
        containers.append(nested)
    containers.append(raw)
    for container in containers:
        for name in names:
            if name in container:
                return container[name]
    for name in names:
        if isinstance(entity, Mapping) and name in entity:
            return entity[name]
        value = getattr(entity, name, _MISSING)
        if value is not _MISSING:
            return value
    return _MISSING


def _transform_container(entity: Any) -> Mapping[str, Any] | None:
    """Return the authoritative transform-facts object, if the reader sent one."""
    raw = _raw_properties(entity)
    for key in ("transform_facts", "insert_transform"):
        value = raw.get(key)
        if isinstance(value, Mapping):
            return value
    return None


def _legacy_transform_mode(entity: Any) -> bool:
    """Opt in to the narrow legacy fallback explicitly, never by default."""
    raw = _raw_properties(entity)
    value = raw.get("legacy_transform_facts")
    if value is True:
        return True
    return str(raw.get("transform_facts_compatibility", "")).strip().casefold() == "legacy"


def _transform_fact(entity: Any, names: Sequence[str], statuses: Sequence[str]) -> Any:
    """Read a transform fact without allowing unavailable values to fail open."""
    container = _transform_container(entity)
    if container is not None:
        for status_name in statuses:
            if status_name in container:
                status = str(container.get(status_name) or "").strip().casefold()
                if status != "available":
                    return _MISSING
        # A complete transform object is authoritative.  Do not fall through
        # to raw scale_x/style.rotation when its value is explicitly null.
        for name in names:
            if name in container:
                return container[name]
        # Some readers serialize scale as three scalar fields rather than a
        # tuple.  They are accepted only inside the authoritative object.
        if names == ("scale",) and all(name in container for name in ("scale_x", "scale_y", "scale_z")):
            return (container["scale_x"], container["scale_y"], container["scale_z"])
        return _MISSING
    if not _legacy_transform_mode(entity):
        return _MISSING
    # Legacy compatibility is explicit and opt-in.  It remains useful for
    # reviewed fixtures produced before transform_facts-v1, but never activates
    # merely because a SourceEntity has default scale/style values.
    return _entity_value(entity, *names)


def _fact_provenance(entity: Any, name: str) -> str:
    raw = _raw_properties(entity)
    for key in ("transform_facts_provenance", "fact_provenance"):
        provenance = raw.get(key)
        if isinstance(provenance, Mapping) and provenance.get(name):
            return str(provenance[name])
    return "reader_raw_properties" if raw else "reader_entity_field"


@dataclass(frozen=True)
class _TransformFacts:
    insertion: tuple[float, float, float]
    block_base: tuple[float, float, float]
    scale: tuple[float, float, float]
    rotation: float
    normal: tuple[float, float, float]
    extrusion: tuple[float, float, float]
    provenance: dict[str, str]


def _transform_facts(entity: Any) -> tuple[_TransformFacts | None, list[dict[str, Any]]]:
    """Validate the complete explicit INSERT transform contract.

    ``SourceEntity.scale`` and ``CadStyle.rotation`` are accepted only when
    they are non-default values; a default value alone is not evidence that
    the reader actually supplied the fact.  In production the reader should
    provide all values in ``raw_properties.transform_facts``.
    """
    missing: list[str] = []
    invalid: list[str] = []

    insertion_value = _transform_fact(
        entity,
        ("insertion_point", "insertion_point_wcs", "insert_point", "insertion"),
        ("insertion_point_status", "insertion_point_wcs_status"),
    )
    if insertion_value is _MISSING or insertion_value is None:
        missing.append("insertion_point")
        insertion = None
    else:
        try:
            insertion = _vector(insertion_value, "insertion_point")
        except ValueError:
            invalid.append("insertion_point")
            insertion = None

    base_value = _transform_fact(
        entity,
        ("block_base_point", "block_base", "definition_base_point", "base_point"),
        ("block_base_point_status", "block_base_status"),
    )
    if base_value is _MISSING or base_value is None:
        missing.append("block_base_point")
        block_base = None
    else:
        try:
            block_base = _vector(base_value, "block_base_point")
        except ValueError:
            invalid.append("block_base_point")
            block_base = None

    scale_value = _transform_fact(entity, ("scale",), ("scale_status",))
    if scale_value is _MISSING and _transform_container(entity) is None and _legacy_transform_mode(entity):
        raw = _raw_properties(entity)
        if all(name in raw for name in ("scale_x", "scale_y", "scale_z")):
            scale_value = (raw["scale_x"], raw["scale_y"], raw["scale_z"])
        else:
            candidate = getattr(entity, "scale", _MISSING)
            if candidate is not _MISSING and tuple(candidate) != (1.0, 1.0, 1.0):
                scale_value = candidate
    if scale_value is _MISSING or scale_value is None:
        missing.append("scale")
        scale = None
    else:
        try:
            scale = _vector(scale_value, "scale")
        except ValueError:
            invalid.append("scale")
            scale = None

    rotation_value = _transform_fact(
        entity,
        ("rotation", "insert_rotation"),
        ("rotation_status", "insert_rotation_status"),
    )
    if (
        rotation_value is _MISSING
        and _transform_container(entity) is None
        and _legacy_transform_mode(entity)
    ):
        style = getattr(entity, "style", None)
        candidate = getattr(style, "rotation", _MISSING)
        if candidate is not _MISSING and abs(float(candidate)) > _EPSILON:
            rotation_value = candidate
    if rotation_value is _MISSING or rotation_value is None:
        missing.append("rotation")
        rotation = None
    else:
        try:
            rotation = _as_float(rotation_value, "rotation")
        except ValueError:
            invalid.append("rotation")
            rotation = None

    normal_value = _transform_fact(
        entity,
        ("normal", "insert_normal"),
        ("normal_status", "insert_normal_status"),
    )
    if normal_value is _MISSING or normal_value is None:
        missing.append("normal")
        normal = None
    else:
        try:
            normal = _vector(normal_value, "normal")
        except ValueError:
            invalid.append("normal")
            normal = None

    extrusion_value = _transform_fact(
        entity,
        ("extrusion", "extrusion_direction", "insert_extrusion"),
        ("extrusion_status", "insert_extrusion_status"),
    )
    if extrusion_value is _MISSING or extrusion_value is None:
        missing.append("extrusion")
        extrusion = None
    else:
        try:
            extrusion = _vector(extrusion_value, "extrusion")
        except ValueError:
            invalid.append("extrusion")
            extrusion = None

    if missing or invalid:
        diagnostic = {
            "code": "missing_block_transform_facts" if missing else "invalid_block_transform_facts",
            "severity": "blocking",
            "blocking": True,
            "missing_facts": sorted(set(missing)),
            "invalid_facts": sorted(set(invalid)),
            "message": (
                "INSERT footprint transformation abstained; explicit reader facts "
                "are required and no block base/orientation defaults are allowed."
            ),
        }
        return None, [diagnostic]

    assert insertion is not None and block_base is not None and scale is not None
    assert rotation is not None and normal is not None and extrusion is not None
    normal_length = math.sqrt(sum(value * value for value in normal))
    extrusion_length = math.sqrt(sum(value * value for value in extrusion))
    if normal_length <= _VECTOR_EPSILON or extrusion_length <= _VECTOR_EPSILON:
        return None, [{
            "code": "invalid_block_orientation_facts",
            "severity": "blocking",
            "blocking": True,
            "invalid_facts": ["normal" if normal_length <= _VECTOR_EPSILON else "extrusion"],
            "message": "normal and extrusion must be non-zero vectors.",
        }]
    unit_normal = tuple(value / normal_length for value in normal)
    unit_extrusion = tuple(value / extrusion_length for value in extrusion)
    cross = (
        unit_normal[1] * unit_extrusion[2] - unit_normal[2] * unit_extrusion[1],
        unit_normal[2] * unit_extrusion[0] - unit_normal[0] * unit_extrusion[2],
        unit_normal[0] * unit_extrusion[1] - unit_normal[1] * unit_extrusion[0],
    )
    if math.sqrt(sum(value * value for value in cross)) > 1.0e-7:
        return None, [{
            "code": "inconsistent_block_orientation_facts",
            "severity": "blocking",
            "blocking": True,
            "invalid_facts": ["normal", "extrusion"],
            "message": "normal and extrusion must describe the same INSERT plane.",
        }]
    # The warehouse currently stores 2-D geometries.  An oblique INSERT would
    # require a target plane and cannot be projected without losing accuracy.
    if abs(unit_normal[0]) > 1.0e-7 or abs(unit_normal[1]) > 1.0e-7:
        return None, [{
            "code": "non_planar_block_orientation",
            "severity": "blocking",
            "blocking": True,
            "invalid_facts": ["normal", "extrusion"],
            "message": "oblique INSERT footprints are unsupported for 2-D delivery.",
        }]
    return _TransformFacts(
        insertion=insertion,
        block_base=block_base,
        scale=scale,
        rotation=rotation,
        normal=unit_normal,
        extrusion=unit_extrusion,
        provenance={
            "insertion_point": _fact_provenance(entity, "insertion_point"),
            "block_base_point": _fact_provenance(entity, "block_base_point"),
            "scale": _fact_provenance(entity, "scale"),
            "rotation": _fact_provenance(entity, "rotation"),
            "normal": _fact_provenance(entity, "normal"),
            "extrusion": _fact_provenance(entity, "extrusion"),
        },
    ), []


@dataclass(frozen=True)
class _Affine2D:
    m11: float = 1.0
    m12: float = 0.0
    m21: float = 0.0
    m22: float = 1.0
    tx: float = 0.0
    ty: float = 0.0

    def apply(self, point: Sequence[float]) -> Point:
        return (
            self.m11 * float(point[0]) + self.m12 * float(point[1]) + self.tx,
            self.m21 * float(point[0]) + self.m22 * float(point[1]) + self.ty,
        )

    def compose(self, local: "_Affine2D") -> "_Affine2D":
        """Return ``self(local(point))`` for a nested block reference."""
        return _Affine2D(
            self.m11 * local.m11 + self.m12 * local.m21,
            self.m11 * local.m12 + self.m12 * local.m22,
            self.m21 * local.m11 + self.m22 * local.m21,
            self.m21 * local.m12 + self.m22 * local.m22,
            self.m11 * local.tx + self.m12 * local.ty + self.tx,
            self.m21 * local.tx + self.m22 * local.ty + self.ty,
        )


def _affine_from_facts(facts: _TransformFacts) -> _Affine2D:
    cosine, sine = math.cos(facts.rotation), math.sin(facts.rotation)
    sx, sy = facts.scale[0], facts.scale[1]
    # R*S maps definition coordinates relative to the explicit block base.
    r11, r12, r21, r22 = cosine * sx, -sine * sy, sine * sx, cosine * sy
    # A negative-Z OCS is a reflected plan.  Keep that reflection explicit;
    # silently dropping it would move non-symmetric symbols.
    orientation_x = -1.0 if facts.normal[2] < 0.0 else 1.0
    m11, m12 = orientation_x * r11, orientation_x * r12
    m21, m22 = r21, r22
    bx, by = facts.block_base[0], facts.block_base[1]
    return _Affine2D(
        m11, m12, m21, m22,
        facts.insertion[0] - m11 * bx - m12 * by,
        facts.insertion[1] - m21 * bx - m22 * by,
    )


def _block_name(entity: Any) -> str:
    value = _entity_value(
        entity,
        "block_name",
        "container_block_name",
        "block_reference_name",
        "block_effective_name",
    )
    return "" if value is _MISSING or value is None else str(value).strip()


def _entity_kind(entity: Any) -> str:
    value = getattr(entity, "dwg_type", _MISSING)
    if value is _MISSING and isinstance(entity, Mapping):
        value = entity.get("dwg_type", entity.get("dwg_type_name", ""))
    return str(value or "").upper().removeprefix("ACDB")


def _entity_points(entity: Any) -> tuple[Sequence[float], ...]:
    points = getattr(entity, "points", _MISSING)
    if points is _MISSING and isinstance(entity, Mapping):
        points = entity.get("points", ())
    try:
        return tuple(points or ())
    except TypeError:
        return ()


def _curve_bulges(entity: Any) -> list[float] | None:
    raw = _raw_properties(entity)
    curve = raw.get("curve_facts")
    if not isinstance(curve, Mapping):
        curve = getattr(entity, "curve_facts", None)
    if not isinstance(curve, Mapping):
        return None
    bulges = curve.get("bulges")
    if bulges is None:
        return []
    try:
        return [_as_float(value, "curve_facts.bulges") for value in bulges]
    except (TypeError, ValueError):
        return None


def _linear_footprint_entity(entity: Any) -> tuple[bool, str]:
    kind = _entity_kind(entity)
    if kind in {"LINE"}:
        return (len(_entity_points(entity)) >= 2, "line_requires_two_points")
    if kind in {"LWPOLYLINE", "POLYLINE", "2DPOLYLINE", "3DPOLYLINE"}:
        bulges = _curve_bulges(entity)
        if bulges is not None and any(abs(value) > _EPSILON for value in bulges):
            return False, "bulged_polyline_footprint_not_exactly_supported"
        return (len(_entity_points(entity)) >= 2, "polyline_requires_two_points")
    if kind in {"CIRCLE", "ARC", "SPLINE", "ELLIPSE", "HELIX"}:
        return False, "curved_block_footprint_not_exactly_supported"
    if kind in {"TEXT", "MTEXT", "ATTRIB", "ATTDEF", "DIMENSION", "POINT"}:
        return True, "non_footprint_annotation"
    if kind in {"", "SEQEND", "ENDBLK", "BLOCK_RECORD"}:
        return True, "non_footprint_marker"
    return False, "unsupported_block_footprint_entity"


def _transformed_segments(
    definition_entities: Mapping[str, Sequence[Any]],
    root_instance: Any,
) -> tuple[list[tuple[Point, Point]], list[dict[str, Any]]]:
    """Expand an INSERT and nested INSERTs into exact 2-D linear segments."""
    root_facts, root_diagnostics = _transform_facts(root_instance)
    if root_facts is None:
        return [], root_diagnostics
    segments: list[tuple[Point, Point]] = []
    diagnostics: list[dict[str, Any]] = []
    root_name = _block_name(root_instance).upper()
    if not root_name:
        return [], [{
            "code": "missing_block_reference_name",
            "severity": "blocking",
            "blocking": True,
            "missing_facts": ["block_name"],
            "message": "INSERT has no block reference name.",
        }]

    def visit(name: str, parent_transform: _Affine2D, stack: tuple[str, ...]) -> None:
        if name in stack:
            diagnostics.append({
                "code": "cyclic_nested_block_definition",
                "severity": "blocking",
                "blocking": True,
                "block_name": name,
                "message": "Nested block definition cycle prevents exact footprint expansion.",
            })
            return
        entities = definition_entities.get(name, ())
        if not entities:
            diagnostics.append({
                "code": "missing_block_definition",
                "severity": "blocking",
                "blocking": True,
                "block_name": name,
                "message": "No reader block-definition entities were available.",
            })
            return
        next_stack = stack + (name,)
        for entity in entities:
            kind = _entity_kind(entity)
            if kind == "INSERT":
                child_name = _block_name(entity).upper()
                child_facts, child_diags = _transform_facts(entity)
                if child_facts is None:
                    for diagnostic in child_diags:
                        diagnostic = dict(diagnostic)
                        diagnostic["block_name"] = child_name
                        diagnostic["entity_key"] = getattr(entity, "entity_key", "")
                        diagnostics.append(diagnostic)
                    continue
                if not child_name:
                    diagnostics.append({
                        "code": "missing_nested_block_reference_name",
                        "severity": "blocking",
                        "blocking": True,
                        "message": "Nested INSERT has no block reference name.",
                    })
                    continue
                visit(child_name, parent_transform.compose(_affine_from_facts(child_facts)), next_stack)
                continue
            linear, reason = _linear_footprint_entity(entity)
            if not linear:
                diagnostics.append({
                    "code": reason,
                    "severity": "blocking",
                    "blocking": True,
                    "entity_key": getattr(entity, "entity_key", ""),
                    "dwg_type": kind,
                    "message": "Block footprint cannot be projected exactly from reader facts.",
                })
                continue
            if reason != "non_footprint_annotation":
                points = _entity_points(entity)
                if len(points) < 2:
                    continue
                transformed = [parent_transform.apply(point) for point in points]
                segments.extend(zip(transformed, transformed[1:]))
                if bool(getattr(entity, "closed", False)) and len(transformed) > 2:
                    if transformed[0] != transformed[-1]:
                        segments.append((transformed[-1], transformed[0]))

    visit(root_name, _affine_from_facts(root_facts), ())
    return segments, diagnostics


def _project(point: Point, start: Point, end: Point) -> Point:
    dx, dy = end[0] - start[0], end[1] - start[1]
    squared = dx * dx + dy * dy
    if squared <= 1.0e-18:
        return start
    fraction = max(
        0.0,
        min(1.0, ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / squared),
    )
    return start[0] + fraction * dx, start[1] + fraction * dy


def _nearest_asset(point: Point, assets: Iterable[Any], tolerance: float) -> Any | None:
    ranked = []
    for asset in assets:
        try:
            centroid = asset.native_centroid
        except AttributeError:
            points = _entity_points(asset)
            if not points:
                continue
            centroid = (
                sum(float(p[0]) for p in points) / len(points),
                sum(float(p[1]) for p in points) / len(points),
            )
        distance = math.dist(point, centroid)
        key = str(getattr(asset, "feature_key", ""))
        ranked.append((distance, key, asset))
    ranked.sort(key=lambda item: (item[0], item[1]))
    if not ranked or ranked[0][0] > tolerance:
        return None
    if len(ranked) > 1 and ranked[1][0] - ranked[0][0] <= 0.01:
        return None
    return ranked[0][2]


def _threshold(registry: Any, key: str, default: float) -> float:
    values = getattr(registry, "thresholds", registry if isinstance(registry, Mapping) else {})
    try:
        value = values.get(key, default)
    except AttributeError:
        value = default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) and number >= 0.0 else default


def build_port_candidates(entities, features, registry):
    """Return reviewed port candidates without mutating any route feature.

    Candidates with ``status`` beginning ``abstain_`` or equal to
    ``unsupported_curved_footprint`` are intentionally retained as evidence so
    downstream diagnostics can explain why no port geometry was published.
    """
    by_source = {
        getattr(entity, "entity_key", ""): entity
        for entity in entities
        if getattr(entity, "entity_key", "")
    }
    definitions: dict[str, list[Any]] = defaultdict(list)
    for entity in entities:
        layout = str(getattr(entity, "layout", ""))
        if layout.upper().startswith("BLOCKDEF:"):
            definitions[layout.split(":", 1)[1].upper()].append(entity)
    routes = [feature for feature in features if feature.feature_class == "CABLE"]
    supports = [feature for feature in features if feature.feature_class == "PTECH"]
    optical = [feature for feature in features if feature.feature_class in {"BOITE", "SITE"}]
    tolerance = _threshold(registry, "device_to_support_candidate", 1.0)
    exact = _threshold(registry, "exact", 0.05)
    near = _threshold(registry, "dimension_to_support", max(exact, 0.5))
    candidates, seen = [], set()

    def add_candidate(route, vertex_index: int, asset, attachment_kind: str) -> None:
        key = (route.feature_key, vertex_index, asset.feature_key, attachment_kind)
        if key in seen:
            return
        seen.add(key)
        route_point = tuple(route.native_points[vertex_index])
        instance = by_source.get(asset.source_entity_key)
        base = {
            "route_key": route.feature_key,
            "route_source_handle": route.source_handle,
            "vertex_index": vertex_index,
            "asset_key": asset.feature_key,
            "asset_source_handle": asset.source_handle,
            "block_name": _block_name(instance) if instance is not None else "",
            "attachment_kind": attachment_kind,
            "route_point_native": list(route_point),
            "port_point_native": None,
            "center_distance_m": math.dist(route_point, asset.native_centroid),
            "footprint_distance_m": None,
            "transform_basis": "explicit_reader_insert_transform_only",
            "transform_fact_provenance": {},
        }
        if instance is None:
            base.update({
                "status": "abstain_missing_source_entity",
                "diagnostic": {
                    "code": "missing_source_entity_for_asset",
                    "severity": "blocking",
                    "blocking": True,
                    "missing_facts": ["source_entity"],
                    "message": "Feature lineage does not identify an INSERT source entity.",
                },
            })
            candidates.append(base)
            return
        segments, diagnostics = _transformed_segments(definitions, instance)
        if diagnostics:
            # Preserve all reader failures in one deterministic diagnostic.  A
            # curved nested child must not be silently downgraded to a chord.
            codes = sorted({str(item.get("code", "unknown")) for item in diagnostics})
            if any("curved" in code or "bulge" in code for code in codes):
                status = "unsupported_curved_footprint"
            else:
                status = "abstain_block_footprint"
            base.update({
                "status": status,
                "diagnostic": {
                    "code": "block_footprint_unavailable",
                    "severity": "blocking",
                    "blocking": True,
                    "reasons": diagnostics,
                    "reason_codes": codes,
                    "message": "No exact block port was proposed from incomplete or unsupported footprint facts.",
                },
            })
            facts, fact_diagnostics = _transform_facts(instance)
            if facts is not None:
                base["transform_fact_provenance"] = dict(facts.provenance)
            elif fact_diagnostics:
                base["diagnostic"]["transform_reasons"] = fact_diagnostics
            candidates.append(base)
            return
        if not segments:
            base.update({
                "status": "abstain_missing_block_footprint",
                "diagnostic": {
                    "code": "missing_block_footprint_geometry",
                    "severity": "blocking",
                    "blocking": True,
                    "missing_facts": ["linear_block_definition_geometry"],
                    "message": "The INSERT block has no exact linear footprint geometry.",
                },
            })
            candidates.append(base)
            return
        ranked = sorted(
            (math.dist(route_point, projected), projected)
            for start, end in segments
            for projected in [_project(route_point, start, end)]
        )
        footprint_distance, port_point = ranked[0]
        status = (
            "on_symbol_geometry" if footprint_distance <= exact
            else "near_symbol_geometry" if footprint_distance <= near
            else "footprint_too_far"
        )
        facts, fact_diagnostics = _transform_facts(instance)
        base.update({
            "status": status,
            "port_point_native": list(port_point),
            "footprint_distance_m": footprint_distance,
            "transform_fact_provenance": dict(facts.provenance) if facts else {},
        })
        if facts is None:
            base["status"] = "abstain_invalid_block_transform"
            base["port_point_native"] = None
            base["footprint_distance_m"] = None
            base["diagnostic"] = {
                "code": "invalid_block_transform_facts",
                "severity": "blocking",
                "blocking": True,
                "reasons": fact_diagnostics,
                "message": "Port candidate was not published because transform facts became invalid.",
            }
        candidates.append(base)

    for route in routes:
        if not route.native_points:
            continue
        for vertex_index, point in enumerate(route.native_points):
            support = _nearest_asset(point, supports, tolerance)
            if support is not None:
                add_candidate(route, vertex_index, support, "support_port")
        for vertex_index in (0, len(route.native_points) - 1):
            asset = _nearest_asset(route.native_points[vertex_index], optical, tolerance)
            if asset is not None:
                add_candidate(route, vertex_index, asset, "optical_device_port")
    return sorted(
        candidates,
        key=lambda item: (
            item["route_source_handle"],
            item["vertex_index"],
            item["attachment_kind"],
            item["asset_source_handle"],
        ),
    )


__all__ = ["build_port_candidates"]
