"""Reviewed APD (As Plan Drawing) semantic candidates and evidence-bound
annotation linking."""

from __future__ import annotations

import hashlib
import math
import re
from collections import defaultdict
from copy import deepcopy
from dataclasses import replace
from fnmatch import fnmatchcase
from typing import Any, Iterable, Mapping, Sequence

from .cable_legend import cable_spec_name, ptech_type_name
from .config import MappingRegistry
from .curve_geometry import supports_endpoint_bridge
from .family_validation import annotation_pattern_specificity
from .model import CadStyle, Feature, Relation, SourceEntity
from .spatial_filter import is_placeholder_text, is_pole_identifier_shape


_ANNOTATION_CARRIER_TYPES = frozenset({
    "TEXT", "MTEXT", "ATTRIB", "ATTDEF", "MLEADER", "MULTILEADER",
    "TABLE", "TABLE_CELL",
})

SEMANTIC_COVERAGE_SCHEMA_VERSION = "cad2gis-semantic-coverage-v1"
OBSERVABILITY_POLICIES = frozenset({"warn", "abstain", "fail"})
_ROUTE_ENTITY_TYPES = frozenset({"LINE", "LWPOLYLINE", "POLYLINE", "POLYLINE_2D", "POLYLINE_3D"})

# EMR-xxxx labels mark the orange concentric square equipment symbol drawn
# beside an uplink FWA/OLT spur.  The symbol is drawn with short FO-core
# rectangles; those loops are equipment symbology, not deployed cable spans.
_EMR_LABEL_RE = re.compile(r"(?i)^EMR[^A-Za-z0-9]+\d+$")
_EMR_SYMBOL_RADIUS_M = 60.0
_EMR_SHORT_CABLE_LENGTH_M = 80.0

# A pole drawn a few metres off a cable endpoint is a drafting gap, not a
# disconnected network node.  FTTH construction requires every PTECH to
# terminate on the cable; bridge the unique near-miss endpoint.
_PTECH_CABLE_ENDPOINT_BRIDGE_M = 12.0
_PTECH_CABLE_ENDPOINT_MIN_GAP_M = 0.5


_ZNRO_BOUNDARY_TOLERANCE_M = 3.0
_ZNRO_CONSERVATIVE_GAP_BRIDGE_M = 8.0
# Frame-derived BOITE admission requires a reviewed asset-identifier label.
# Generic alphanumeric labels such as ``NP7`` have specificity 1 and prove a
# pole legend specimen, not a FAT device.
_BOITE_FRAME_MIN_LABEL_SPECIFICITY = 2
# A frame-derived BOITE is a deployed telecom device only on device-semantic
# layers.  Base-map layers (``Basic Map``) contain buildings/parcels and are
# never promoted regardless of nearby labels.
_BOITE_FRAME_DEVICE_LAYER_TOKENS = ("FAT", "CLOSURE", "OTB", "FDT", "FIBER")


def _point_segment_distance(
    point: Sequence[float],
    start: Sequence[float],
    end: Sequence[float],
) -> float:
    px, py = float(point[0]), float(point[1])
    ax, ay = float(start[0]), float(start[1])
    bx, by = float(end[0]), float(end[1])
    dx, dy = bx - ax, by - ay
    segment_sq = dx * dx + dy * dy
    if segment_sq <= 0.0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / segment_sq))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _polyline_nearest_distance(
    point: Sequence[float],
    polyline: Sequence[Sequence[float]],
) -> float:
    return min(
        (
            _point_segment_distance(point, start, end)
            for start, end in zip(polyline, polyline[1:])
        ),
        default=math.inf,
    )


def _is_red_boundary(entity: SourceEntity) -> bool:
    """A ZNRO boundary candidate must render red in the source drawing."""
    true_color = str(getattr(entity.style, "true_color", "") or "").strip().upper().lstrip("#")
    return entity.style.aci_color == 1 or true_color in {
        "FF0000", "E00000", "CC0000", "B00000",
    }


def _znro_boundary_style(entity: SourceEntity) -> CadStyle:
    """The reviewed legend renders ZNRO as a red dashed boundary."""
    return replace(
        entity.style,
        aci_color=1,
        true_color="#FF0000",
        entity_aci_color=1,
        layer_aci_color=1,
        linetype="DASHED",
        entity_linetype="DASHED",
        layer_linetype="DASHED",
    )


def _znro_feature(
    points: Sequence[Sequence[float]],
    *,
    source_key: str,
    source_handle: str,
    source_layer: str,
    style: CadStyle,
    operation: str,
) -> Feature:
    return Feature(
        feature_key=hashlib.sha256(
            f"ZNRO|{source_key}".encode()
        ).hexdigest(),
        feature_class="ZNRO",
        geometry_kind="Polygon",
        native_points=[tuple(float(coord) for coord in point) for point in points],
        source_entity_key=source_key,
        source_handle=source_handle,
        source_layer=source_layer,
        geometry_role="SOURCE_BOUNDARY",
        style=style,
        attributes={"CODE": f"ZNRO-CAD-{source_handle}"},
        display_label="",
        label_provenance="UNAVAILABLE",
        field_provenance={"CODE": "DWG_DERIVED:znro-parent-zone"},
        lineage=[{
            "operation": operation,
            "source_entity_key": source_key,
            "max_displacement_m": 0.0,
        }],
    )


def _znro_boundary_features(rings: Sequence[Mapping[str, Any]]) -> list[Feature]:
    features = []
    for ring in rings:
        entity = ring["entity"]
        feature = _znro_feature(
            ring.get("points", entity.points),
            source_key=entity.entity_key,
            source_handle=entity.handle,
            source_layer=entity.layer,
            style=_znro_boundary_style(entity),
            operation="identity",
        )
        if ring.get("repair_lineage"):
            feature.geometry_role = "DERIVED_BOUNDARY_REPAIR"
            feature.lineage = [deepcopy(ring["repair_lineage"])]
        features.append(feature)
    return features


def _znro_synthetic_feature(
    points: Sequence[Sequence[float]],
    *,
    source_layer: str,
    operation: str,
    source_handle: str | None = None,
) -> Feature:
    rounded = [tuple(float(coord) for coord in point) for point in points]
    source_key = hashlib.sha256(
        (
            "ZNRO|" + "|".join(
                f"{point[0]:.6f},{point[1]:.6f}" for point in rounded
            )
        ).encode()
    ).hexdigest()
    return _znro_feature(
        rounded,
        source_key=source_key,
        source_handle=source_handle or source_layer,
        source_layer=source_layer,
        style=CadStyle(aci_color=1, true_color="#FF0000", linetype="DASHED"),
        operation=operation,
    )


def _is_placeholder_block(entity: SourceEntity) -> bool:
    """Annotation/info-card INSERTs carry only documentation placeholders.

    Real deployed devices (FAT cabinets, FDT closures) carry an effective
    attribute value (e.g. F=4, F=7, F=1 — a numeric capacity/sequence);
    info cards carry D/F/L documentation fields whose values are
    single-character placeholders ("-", "X", "K", "C", ...).  The check is
    value-shape based (numeric vs placeholder), never layer/block-name
    hardcoded.
    """
    attributes = getattr(entity, "block_attributes", {}) or {}
    if not attributes:
        return True
    return all(
        _is_placeholder_attribute_value(value)
        for value in attributes.values()
    )


def _is_placeholder_attribute_value(value: Any) -> bool:
    """A real device attribute carries a non-zero numeric value; everything
    else ("-", "X", "C", "0", free-form docs) is a documentation placeholder."""
    text = str(value).strip()
    if not text:
        return True
    return not (text.isdigit() and int(text) != 0)


def _non_default_explicit_style(style: Any) -> bool:
    """A block attribute style that is deliberately coloured.

    ACI 7 (black/white) and ByLayer/ByBlock are default presentation; they
    must not override a coloured device layer (e.g. an orange FAT box with a
    black attribute template).  Any other explicit colour is evidence that
    the visible symbol/label is drawn in that colour.
    """
    true_color = str(getattr(style, "true_color", "") or "").strip().lstrip("#")
    if true_color and true_color.upper() not in {"000000", "FFFFFF"}:
        return True
    return int(getattr(style, "aci_color", 256) or 256) not in {0, 7, 256}


def _attributed_block_style(entity: SourceEntity, styles_by_root: Mapping[str, list[tuple[str, Any]]]):
    """Return the explicit style of the block attribute that fills this INSERT.

    Device INSERTs are commonly drawn on a layer whose colour differs from the
    attribute text inside the referenced block (e.g. a blue closure number on
    a red CLOSURE layer).  When the INSERT itself is ByLayer/ByBlock, the
    attribute definition carries the visible symbol style and must win.

    Some readers materialize ATTDEF carriers with an empty ``text`` field
    (the actual value lives on the INSERT's ``block_attributes``).  Such
    empty carriers are still accepted when their definition is the only
    non-default explicitly coloured attribute of the block.
    """
    if entity.style.entity_true_color.strip() or entity.style.entity_aci_color not in {0, 256}:
        return None
    attribute_values = {
        str(value).strip() for value in entity.block_attributes.values()
    }
    if not attribute_values:
        return None
    candidates = []
    for text, style in styles_by_root.get(entity.entity_key, ()):
        if not _non_default_explicit_style(style):
            continue
        if text and text not in attribute_values:
            continue
        candidates.append((text, style))
    distinct = {
        (
            style.aci_color,
            style.true_color.strip(),
            style.linetype,
            style.lineweight,
        ): style
        for _, style in candidates
    }
    if len(distinct) == 1:
        return next(iter(distinct.values()))
    return None


def _is_polygon_ring(points: Sequence[tuple[float, float]]) -> bool:
    """ZPM boundary polylines are closed into polygons at the warehouse.

    Reviewed zpm_boundary layers carry zone outlines drawn as LWPOLYLINEs;
    the warehouse materialiser closes the ring regardless of the DWG closed
    flag, matching the reviewed reference converter (all ≥3-point outlines
    on the boundary layer become polygons).
    """
    return len(points) >= 3


def _is_open_rectangle_callout(points: Sequence[Sequence[float]]) -> bool:
    """True for a 4-vertex polyline shaped like a rectangle missing one side.

    APD annotation frames are drawn as three edges of a rectangle (the fourth
    side is left open for the leader line).  The two long edges are parallel,
    the short middle edge is roughly perpendicular to them, and the missing
    side is not closed back to the start vertex.
    """
    if len(points) != 4:
        return False
    p0, p1, p2, p3 = (tuple(float(v) for v in p) for p in points)
    v0 = (p1[0] - p0[0], p1[1] - p0[1])
    v1 = (p2[0] - p1[0], p2[1] - p1[1])
    v2 = (p3[0] - p2[0], p3[1] - p2[1])
    missing = (p3[0] - p0[0], p3[1] - p0[1])
    n0 = math.hypot(*v0)
    n1 = math.hypot(*v1)
    n2 = math.hypot(*v2)
    n_missing = math.hypot(*missing)
    if min(n0, n1, n2, n_missing) <= 0.0:
        return False
    u0 = (v0[0] / n0, v0[1] / n0)
    u1 = (v1[0] / n1, v1[1] / n1)
    u2 = (v2[0] / n2, v2[1] / n2)
    u_missing = (missing[0] / n_missing, missing[1] / n_missing)
    cross_02 = abs(u0[0] * u2[1] - u0[1] * u2[0])
    dot_02 = abs(u0[0] * u2[0] + u0[1] * u2[1])
    dot_01 = abs(u0[0] * u1[0] + u0[1] * u1[1])
    cross_1m = abs(u1[0] * u_missing[1] - u1[1] * u_missing[0])
    dot_1m = abs(u1[0] * u_missing[0] + u1[1] * u_missing[1])
    if cross_02 > 0.15 or dot_02 < 0.7 or dot_01 > 0.3:
        return False
    # The fourth side is not drawn: the gap between start and end must be
    # parallel to the middle edge (the two perpendicular edges together with
    # the two long edges describe the open annotation frame).
    return cross_1m <= 0.15 and dot_1m >= 0.7


def _annotation_callout_component_keys(
    cables: Sequence[Feature],
    assets: Sequence[Feature],
) -> set[str]:
    """Exclude annotation-frame callouts that were misclassified as CABLE.

    The callout is an open rectangle (three sides of an annotation frame) and,
    when present, its 2-vertex leader line.  The leader connects the rectangle
    to another endpoint or to a PTECH/SITE point; real multi-vertex cable
    routes are never removed by this rule.
    """
    cable_list = list(cables)
    if not cable_list:
        return set()
    points_by_key = {
        feature.feature_key: [tuple(p[:2]) for p in feature.native_points]
        for feature in cable_list
        if feature.native_points
    }
    rectangle_keys = {
        key for key, points in points_by_key.items()
        if _is_open_rectangle_callout(points)
    }
    if not rectangle_keys:
        return set()

    other_endpoints = []
    for key, points in points_by_key.items():
        if len(points) >= 2:
            other_endpoints.extend((key, point) for point in (points[0], points[-1]))
    asset_points = [
        tuple(float(v) for v in feature.native_centroid)
        for feature in assets if feature.native_points
    ]

    noise_keys = set(rectangle_keys)
    for rect_key in rectangle_keys:
        rect_points = points_by_key[rect_key]
        for key, points in points_by_key.items():
            if key == rect_key or len(points) != 2:
                continue
            connected = any(
                math.dist(rect_point, line_endpoint) <= 1.0
                for rect_point in rect_points
                for line_endpoint in (points[0], points[-1])
            )
            if not connected:
                continue
            far_endpoint = (
                points[-1]
                if min(math.dist(points[0], point) for point in rect_points)
                <= min(math.dist(points[-1], point) for point in rect_points)
                else points[0]
            )
            reaches_network = (
                any(
                    math.dist(far_endpoint, endpoint) <= 1.0
                    for _, endpoint in other_endpoints
                    if endpoint not in rect_points
                )
                or any(
                    math.dist(far_endpoint, asset_point) <= 2.0
                    for asset_point in asset_points
                )
            )
            if reaches_network:
                noise_keys.add(key)
    return noise_keys


def _isolated_short_cable_keys(
    cables: Sequence[Feature],
    assets: Sequence[Feature],
    sling_layers: set[str],
) -> set[str]:
    """Short SLING_WIRE lines with no endpoint near an asset or another CABLE.

    Legend/style sheets draw one short sling specimen beside the symbol
    catalog (e.g. a cyan 9 m line on a SLING WIRE layer).  A real sling span
    always ends on a PTECH/SITE or continues into the cable network, so
    endpoint isolation is safe evidence for removal.  The rule is limited to
    reviewed sling layers so a short standalone FO feeder is never removed.
    """
    cable_list = list(cables)
    points_by_key = {
        feature.feature_key: [tuple(p[:2]) for p in feature.native_points]
        for feature in cable_list
        if feature.native_points
    }
    endpoints_by_key = {
        key: (points[0], points[-1])
        for key, points in points_by_key.items()
        if len(points) >= 2
    }
    asset_points = [
        tuple(float(v) for v in feature.native_centroid)
        for feature in assets if feature.native_points
    ]
    noise_keys: set[str] = set()
    for key, (start, end) in endpoints_by_key.items():
        points = points_by_key[key]
        if len(points) != 2:
            continue
        cable_feature = next(
            (feature for feature in cable_list if feature.feature_key == key),
            None,
        )
        if cable_feature is None or str(cable_feature.source_layer).strip().upper() not in sling_layers:
            continue
        if math.dist(start, end) > 20.0:
            continue
        if any(
            math.dist(endpoint, asset_point) <= 2.0
            for endpoint in (start, end)
            for asset_point in asset_points
        ):
            continue
        other_endpoints = [
            endpoint
            for other_key, (left, right) in endpoints_by_key.items()
            if other_key != key
            for endpoint in (left, right)
        ]
        if any(
            math.dist(endpoint, other) <= 2.0
            for endpoint in (start, end)
            for other in other_endpoints
        ):
            continue
        noise_keys.add(key)
    return noise_keys


def _dangling_cable_leader_keys(
    cables: Sequence[Feature],
    assets: Sequence[Feature],
) -> set[str]:
    """Remove 2-point leader lines that touch a multi-point CABLE at one end
    and dangle at the other.

    APD annotation leaders often ride on the same FO layer as the route they
    annotate: one endpoint lands on the real route, the other floats away from
    every PTECH/SITE.  A real cable span or lateral ends on an asset or on
    another cable at both ends.
    """
    cable_list = list(cables)
    points_by_key = {
        feature.feature_key: [tuple(p[:2]) for p in feature.native_points]
        for feature in cable_list if feature.native_points
    }
    asset_points = [
        tuple(float(v) for v in feature.native_centroid)
        for feature in assets if feature.native_points
    ]
    endpoint_lookup = [
        (other_key, endpoint)
        for other_key, points in points_by_key.items()
        if len(points) >= 2
        for endpoint in (points[0], points[-1])
    ]
    noise_keys: set[str] = set()
    for key, points in points_by_key.items():
        if len(points) != 2:
            continue
        shared_other_endpoint = None
        for endpoint_index, endpoint in enumerate(points):
            for other_key, other_endpoint in endpoint_lookup:
                if other_key == key:
                    continue
                if math.dist(endpoint, other_endpoint) <= 1.0:
                    shared_other_endpoint = endpoint_index
                    break
            if shared_other_endpoint is not None:
                break
        if shared_other_endpoint is None:
            continue
        other_endpoint = points[1 - shared_other_endpoint]
        if any(
            math.dist(other_endpoint, asset_point) <= 2.0
            for asset_point in asset_points
        ):
            continue
        noise_keys.add(key)
    return noise_keys


_ALLOWLIST_FIELDS = frozenset({
    "reason", "candidate_class", "source_layer", "dwg_type", "block_name",
})


class CoverageGateError(RuntimeError):
    """Fail-closed coverage error carrying the deterministic audit payload."""

    def __init__(self, domain: str, coverage: Mapping[str, Any]):
        self.domain = domain
        self.coverage = dict(coverage)
        reasons = ", ".join(
            f"{reason}={count}"
            for reason, count in self.coverage.get("by_reason", {}).items()
        ) or "none"
        super().__init__(
            f"{domain} coverage gate failed: {reasons}; inspect coverage records"
        )


def normalize_observability_policy(policy: str | None, *, default: str) -> str:
    """Validate a coverage policy without guessing a permissive fallback."""
    selected = default if policy is None else str(policy).strip().casefold()
    if selected not in OBSERVABILITY_POLICIES:
        raise ValueError(
            "coverage policy must be one of warn, abstain, fail; "
            f"got {policy!r}"
        )
    return selected


def _normalize_allowlist(
    allowlist: Sequence[str | Mapping[str, Any]] | None,
) -> tuple[dict[str, str], ...]:
    normalized: list[dict[str, str]] = []
    for index, item in enumerate(allowlist or ()):
        if isinstance(item, str):
            rule = {"reason": item}
        elif isinstance(item, Mapping):
            unknown = set(item) - _ALLOWLIST_FIELDS
            if unknown:
                raise ValueError(
                    f"coverage allowlist[{index}] has unknown keys: {sorted(unknown)}"
                )
            rule = {
                str(key): str(value)
                for key, value in item.items()
                if str(value).strip()
            }
        else:
            raise ValueError(
                f"coverage allowlist[{index}] must be a reason string or object"
            )
        if not rule.get("reason", "").strip():
            raise ValueError(
                f"coverage allowlist[{index}] requires a non-empty reason"
            )
        normalized.append(rule)
    return tuple(normalized)


def _matches_allowlist(record: Mapping[str, Any], rules: Iterable[Mapping[str, str]]) -> bool:
    for rule in rules:
        if all(
            fnmatchcase(
                str(record.get(field, "")).casefold(), pattern.casefold(),
            )
            for field, pattern in rule.items()
        ):
            return True
    return False


def build_coverage_report(
    records: Iterable[Mapping[str, Any]],
    *,
    schema_version: str,
    policy: str,
    allowlist: Sequence[str | Mapping[str, Any]] | None = None,
    inspected_count: int | None = None,
) -> dict[str, Any]:
    """Build the shared deterministic semantic/style coverage contract.

    Allowlisting is explicit and field-scoped.  Patterns use shell wildcards,
    are matched case-insensitively, and never change classification or style;
    they only acknowledge a reviewed unsupported case.
    """
    selected_policy = normalize_observability_policy(policy, default="fail")
    rules = _normalize_allowlist(allowlist)
    normalized_records: list[dict[str, Any]] = []
    for raw_record in records:
        record = {
            "source_entity_key": str(raw_record.get("source_entity_key", "")),
            "reason": str(raw_record.get("reason", "")),
            "candidate_class": str(raw_record.get("candidate_class", "")),
            "source_layer": str(raw_record.get("source_layer", "")),
            "dwg_type": str(raw_record.get("dwg_type", "")),
            **{
                str(key): value for key, value in raw_record.items()
                if key not in {
                    "source_entity_key", "reason", "candidate_class",
                    "source_layer", "dwg_type", "action", "allowlisted",
                }
            },
        }
        if not record["reason"]:
            raise ValueError("coverage record reason must be non-empty")
        is_allowlisted = _matches_allowlist(record, rules)
        record["allowlisted"] = is_allowlisted
        record["action"] = "allowlist" if is_allowlisted else selected_policy
        normalized_records.append(record)
    normalized_records.sort(key=lambda item: (
        item["reason"], item["source_layer"].casefold(), item["dwg_type"],
        item["source_entity_key"], item.get("candidate_class", ""),
    ))
    by_reason: dict[str, int] = {}
    for record in normalized_records:
        reason = record["reason"]
        by_reason[reason] = by_reason.get(reason, 0) + 1
    non_allowlisted = sum(not item["allowlisted"] for item in normalized_records)
    failed = sum(item["action"] == "fail" for item in normalized_records)
    abstained = sum(item["action"] == "abstain" for item in normalized_records)
    warned = sum(item["action"] == "warn" for item in normalized_records)
    status = "FAIL" if failed else "WATCH" if non_allowlisted else "PASS"
    counts = {
        "records": len(normalized_records),
        "allowlisted": len(normalized_records) - non_allowlisted,
        "non_allowlisted": non_allowlisted,
        "warned": warned,
        "abstained": abstained,
        "failed": failed,
    }
    if inspected_count is not None:
        counts["inspected"] = int(inspected_count)
    return {
        "schema_version": schema_version,
        "policy": selected_policy,
        "status": status,
        "passed": status == "PASS",
        "conversion_allowed": failed == 0,
        "counts": counts,
        "by_reason": dict(sorted(by_reason.items())),
        "records": normalized_records,
    }


def _coverage_record(
    entity: SourceEntity,
    reason: str,
    candidate_class: str = "",
    **detail: Any,
) -> dict[str, Any]:
    return {
        "source_entity_key": entity.entity_key,
        "reason": reason,
        "candidate_class": candidate_class,
        "source_layer": entity.layer,
        "dwg_type": entity.dwg_type,
        "source_handle": entity.handle,
        "block_name": entity.block_name,
        **detail,
    }


def _feature_key(entity: SourceEntity, feature_class: str) -> str:
    return hashlib.sha256(f"{entity.entity_key}|{feature_class}".encode("utf-8")).hexdigest()


def _generated_code(feature_class: str, handle: str) -> str:
    return f"{feature_class}-CAD-{handle.upper()}"


_GENERATED_CODE_PROVENANCE = "DWG_DERIVED:stable-handle-id"
_DEVICE_NUMBER_ATTRIBUTE_RE = re.compile(r"^\d+$")


def _device_number_attribute(entity: SourceEntity) -> str | None:
    """Return the numeric owned block-attribute text (e.g. FAT capacity).

    APD device blocks carry two independent labels: a text code from a
    nearby annotation layer and a bare numeric ATTRIB owned by the INSERT.
    The reader stores that ATTRIB text even when its tag is empty; this
    function recovers the numeric one without guessing from layer names.
    """
    values = list(entity.raw_properties.get("owned_attribute_texts") or ())
    for value in reversed(values):
        text = str(value).strip()
        if _DEVICE_NUMBER_ATTRIBUTE_RE.fullmatch(text):
            return text
    return None


def _annotation_target_eligible(feature: Feature) -> bool:
    """A target may receive a DWG text label only when its current label is
    not semantic evidence.

    ``classify_entities`` always pre-populates ``CODE`` with a stable
    handle-derived fallback (``PTECH-CAD-1A2B3C``) so targets would otherwise
    look permanently labelled and the annotation matcher would skip every
    real DWG text.  Eligibility is provenance-based, not label-content based:
    UNAVAILABLE and stable-handle-derived labels are replaceable; any label
    already carried by a DWG text/attribute/decision-pack assignment is not.
    """
    if feature.label_provenance == "UNAVAILABLE":
        return True
    code_provenance = str(feature.field_provenance.get("CODE", "") or "")
    return code_provenance == _GENERATED_CODE_PROVENANCE


def _append_label_provenance(existing: str | None, evidence: str) -> str:
    """Append label evidence without retaining an unavailable sentinel."""
    normalized = str(existing or "").strip()
    if not normalized or normalized.upper() == "UNAVAILABLE":
        return evidence
    return f"{normalized}|{evidence}"


def _minimum_cost_assignment(costs):
    """Rectangular Hungarian assignment; rows must not outnumber columns."""
    if not costs:
        return []
    row_count, column_count = len(costs), len(costs[0])
    if row_count > column_count or any(len(row) != column_count for row in costs):
        raise ValueError("Invalid rectangular assignment matrix")
    row_potential = [0] * (row_count + 1)
    column_potential = [0] * (column_count + 1)
    matched_row = [0] * (column_count + 1)
    predecessor = [0] * (column_count + 1)
    for row_index in range(1, row_count + 1):
        matched_row[0] = row_index
        current_column = 0
        minimum = [math.inf] * (column_count + 1)
        used = [False] * (column_count + 1)
        while True:
            used[current_column] = True
            current_row = matched_row[current_column]
            delta, next_column = math.inf, 0
            for column_index in range(1, column_count + 1):
                if used[column_index]:
                    continue
                reduced = (
                    costs[current_row - 1][column_index - 1]
                    - row_potential[current_row]
                    - column_potential[column_index]
                )
                if reduced < minimum[column_index]:
                    minimum[column_index] = reduced
                    predecessor[column_index] = current_column
                if minimum[column_index] < delta:
                    delta, next_column = minimum[column_index], column_index
            for column_index in range(column_count + 1):
                if used[column_index]:
                    row_potential[matched_row[column_index]] += delta
                    column_potential[column_index] -= delta
                else:
                    minimum[column_index] -= delta
            current_column = next_column
            if matched_row[current_column] == 0:
                break
        while True:
            next_column = predecessor[current_column]
            matched_row[current_column] = matched_row[next_column]
            current_column = next_column
            if current_column == 0:
                break
    assignment = [-1] * row_count
    for column_index in range(1, column_count + 1):
        if matched_row[column_index]:
            assignment[matched_row[column_index] - 1] = column_index - 1
    return assignment


def _annotation_link_kind(annotation: SourceEntity, target: Feature) -> tuple[int, str]:
    """Rank one annotation→target candidate by evidence strength.

    DSH-010 priority (strongest first):

    0. ``owner``     — materialized text owner handle equals the target INSERT handle;
    1. ``block_path`` — materialized ``instance_path`` points at the same root INSERT;
    2. ``family_contract`` — reviewed layer + text pattern + ACI family;
    3. ``distance``  — pure coordinate nearest neighbour (last fallback).
    """
    annotation_owner = str(getattr(annotation, "owner_handle", "") or "").strip()
    target_handle = str(target.source_handle or "").strip()
    if annotation_owner and target_handle and annotation_owner.casefold() == target_handle.casefold():
        return 0, "owner"
    plan_domain = annotation.raw_properties.get("plan_domain")
    if isinstance(plan_domain, Mapping):
        root_key = str(plan_domain.get("root_entity_key", "") or "")
        instance_path = plan_domain.get("instance_path", ())
        if isinstance(instance_path, (list, tuple)) and any(
            str(item) == target.source_entity_key for item in instance_path
        ):
            return 1, "block_path"
        if root_key and root_key == target.source_entity_key:
            return 1, "block_path"
    return 2, "family_contract"


def _annotation_layer_diagnostics(assignments, *, require_same_layer, derived_target_keys):
    """Account for the same explicit derived-target exception used by matching.

    A derived target is an existing source POINT/frame materialized by this
    classification run. This receipt does not authorize arbitrary cross-layer
    targets; the caller supplies the exact keys already admitted by matching.
    """
    cross_layer = [
        target for annotation, target, _ in assignments
        if annotation.layer.strip().casefold() != target.source_layer.strip().casefold()
    ]
    allowed_derived = sum(target.feature_key in derived_target_keys for target in cross_layer)
    return {
        "cross_layer_assignments": len(cross_layer),
        "allowed_derived_cross_layer_assignments": allowed_derived,
        "same_layer_policy_violations": len(cross_layer) - allowed_derived if require_same_layer else 0,
    }


def _assign_family_annotations(
    annotations, targets, tolerance, *, family_id="", require_same_layer=False,
    relation_priority: bool = True,
    cross_layer_target_keys: set[str] | frozenset[str] = frozenset(),
):
    """Maximum-cardinality, minimum-distance one-to-one annotation matching.

    With ``relation_priority`` (the default, DSH-010) candidates are ranked
    owner > block path > reviewed family contract > coordinate distance.
    A unique owner/block-path candidate is assigned directly: explicit DWG
    ownership is not a geometric ambiguity, so it is exempt from the legacy
    0.01 m multiple-optima abstention.  ``relation_priority=False`` restores
    the legacy pure-distance behaviour for fixtures that predate the relation
    contract.
    """
    annotations = sorted(annotations, key=lambda item: (item.text.casefold(), item.entity_key))
    targets = sorted(
        (target for target in targets if _annotation_target_eligible(target)),
        key=lambda item: (item.source_handle, item.feature_key),
    )
    candidate_records, eligible, failures = [], [], []
    distances = {}
    for annotation in annotations:
        candidates: list[tuple[int, float, str, str, Feature]] = []
        for target in targets:
            if (
                require_same_layer
                and target.feature_key not in cross_layer_target_keys
                and annotation.layer.strip().casefold()
                != target.source_layer.strip().casefold()
            ):
                continue
            distance = math.dist(annotation.centroid, target.native_centroid)
            if distance > tolerance:
                continue
            if relation_priority:
                rank, link_kind = _annotation_link_kind(annotation, target)
            else:
                rank, link_kind = 3, "distance"
            candidates.append((
                rank, distance, target.feature_key, link_kind, target,
            ))
        candidates.sort(key=lambda item: (item[0], item[1], item[2]))

        for rank, distance, target_key, link_kind, target in candidates:
            candidate_records.append({
                "annotation_key": annotation.entity_key,
                "family_id": family_id,
                "text": annotation.text.strip(),
                "source_layer": annotation.layer,
                "target_key": target_key,
                "target_handle": target.source_handle,
                "target_layer": target.source_layer,
                "distance_native_m": distance,
                "link_kind": link_kind,
                "relation_priority": rank,
                "selected": False,
                "status": "candidate",
            })

        if not candidates:
            failures.append({
                "kind": "annotation", "entity_key": annotation.entity_key,
                "family_id": family_id, "text": annotation.text,
                "source_layer": annotation.layer,
                "status": "outside_tolerance",
            })
            continue

        best_rank = candidates[0][0]
        best_group = [item for item in candidates if item[0] == best_rank]
        if best_rank in (0, 1):
            if len(best_group) == 1:
                # Explicit DWG ownership/path wins without a geometry tie test.
                rank, distance, target_key, link_kind, target = best_group[0]
                distances[(annotation.entity_key, target_key)] = distance
                eligible.append(annotation)
                continue
            failures.append({
                "kind": "annotation", "entity_key": annotation.entity_key,
                "family_id": family_id, "text": annotation.text,
                "source_layer": annotation.layer,
                "status": "multiple_optima",
                "link_kind": best_group[0][3],
            })
            for record in candidate_records:
                if record["annotation_key"] == annotation.entity_key:
                    record["status"] = "ambiguous"
            continue

        if len(best_group) > 1 and best_group[1][1] - best_group[0][1] <= 0.01:
            failures.append({
                "kind": "annotation", "entity_key": annotation.entity_key,
                "family_id": family_id, "text": annotation.text,
                "source_layer": annotation.layer,
                "status": "multiple_optima",
            })
            for record in candidate_records:
                if record["annotation_key"] == annotation.entity_key:
                    record["status"] = "ambiguous"
            continue
        for _rank, distance, target_key, _link_kind, _target in best_group:
            distances[(annotation.entity_key, target_key)] = distance
        eligible.append(annotation)

    if not eligible:
        return [], failures, candidate_records
    scale = 1_000_000
    unmatched_penalty = (len(eligible) + 1) * (math.ceil(tolerance * scale) + 1)
    invalid_cost = (len(eligible) + 1) * unmatched_penalty
    costs = []
    for annotation in eligible:
        real = [
            (
                int(round(distances[(annotation.entity_key, target.feature_key)] * scale))
                if (annotation.entity_key, target.feature_key) in distances
                else invalid_cost
            )
            for target in targets
        ]
        costs.append(real + [unmatched_penalty] * len(eligible))
    column_assignment = _minimum_cost_assignment(costs)
    assignments = []
    selected_pairs = set()
    for row_index, column_index in enumerate(column_assignment):
        annotation = eligible[row_index]
        if column_index < len(targets) and costs[row_index][column_index] < invalid_cost:
            target = targets[column_index]
            distance = distances[(annotation.entity_key, target.feature_key)]
            assignments.append((annotation, target, distance))
            selected_pairs.add((annotation.entity_key, target.feature_key))
        else:
            failures.append({
                "kind": "annotation", "entity_key": annotation.entity_key,
                "family_id": family_id, "text": annotation.text,
                "source_layer": annotation.layer,
                "status": "assignment_conflict",
            })
    for record in candidate_records:
        pair = (record["annotation_key"], record["target_key"])
        if pair in selected_pairs:
            record["selected"] = True
            record["status"] = "selected"
        elif record["status"] != "ambiguous" and relation_priority:
            # Remaining candidates lost to a stronger-evidence link for the
            # same annotation, not to a geometric ambiguity.
            record["status"] = "ranked_lower_priority"
    return assignments, failures, candidate_records


def _field_rule_value(entity: SourceEntity, rule: dict):
    kind = rule["kind"]
    if kind == "constant":
        return rule.get("value")
    if kind == "entity-text":
        return entity.text.strip() or None
    if kind == "block-attribute-integer":
        value = entity.block_attributes.get(str(rule["attribute"]).upper())
        try:
            return int(value) if value not in {None, ""} else None
        except ValueError:
            return None
    if kind == "layer-regex-integer":
        match = re.search(str(rule["pattern"]), entity.layer)
        return int(match.group(int(rule["group"]))) if match else None
    if kind == "layer-keyword-map":
        layer = entity.layer.upper()
        for keyword, value in rule["mapping"].items():
            if str(keyword).upper() in layer:
                return value
        return None
    if kind == "layer-suffix":
        return rule.get("value") if entity.layer.upper().rstrip().endswith(str(rule["suffix"]).upper()) else None
    raise ValueError(f"Unsupported reviewed field rule kind: {kind}")


def _registry_attributes(entity, feature_class, registry):
    attributes, provenance = {}, {}
    for field_name, rule in getattr(registry, "field_rules", {}).get(feature_class, {}).items():
        value = _field_rule_value(entity, rule)
        if value is not None and value != "":
            attributes[field_name] = value
            provenance[field_name] = str(rule["provenance"])
    return attributes, provenance


def _registry_display_label(feature_class, attributes, registry):
    rule = getattr(registry, "display_label_rules", {}).get(feature_class)
    if not rule:
        return "", "UNAVAILABLE"
    if rule["kind"] == "attribute-field":
        value = attributes.get(str(rule["field"]))
    elif rule["kind"] == "attribute-format":
        required = [str(field) for field in rule.get("required_fields", ())]
        value = (
            str(rule["template"]).format_map(attributes)
            if all(field in attributes for field in required)
            else None
        )
    else:
        raise ValueError(f"Unsupported reviewed display-label rule kind: {rule['kind']}")
    return (str(value), str(rule["provenance"])) if value not in {None, ""} else ("", "UNAVAILABLE")


def classify_entities(
    entities: list[SourceEntity],
    registry: MappingRegistry,
    *,
    coverage_policy: str | None = None,
    coverage_allowlist: Sequence[str | Mapping[str, Any]] | None = None,
    catalog_roots: frozenset[str] = frozenset(),
    project_id: str = "",
    project_slug: str = "",
    apply_geometry_repairs: bool = True,
    geometry_candidates: list | None = None,
):
    """Classify only reviewed semantic mappings and account for every abstention.

    ``coverage_policy`` and ``coverage_allowlist`` are stage-boundary inputs so
    a reviewed project profile can make its exceptions explicit.  A new/draft
    registry defaults to ``fail``.  Existing reviewed v1 registries default to
    ``warn`` for API compatibility, but production callers should always pass
    their reviewed policy explicitly.
    """
    features: list[Feature] = []
    unresolved: list[dict] = []
    coverage_records: list[dict[str, Any]] = []
    mapped_entities: set[str] = set()
    reverse_blocks = {
        block_name: feature_class
        for feature_class, names in getattr(registry, "block_families", {}).items()
        for block_name in names
    }
    reverse_insert_layers = {
        layer_name: feature_class
        for feature_class, names in getattr(
            registry, "insert_layer_families", {}
        ).items()
        for layer_name in names
    }
    route_regex = str(getattr(registry, "positive_route_layer_regex", "") or "")
    route_pattern = re.compile(route_regex) if route_regex else None
    home_layers = set(getattr(registry, "layers", {}).get("homepass", ()))
    zpm_layers = set(getattr(registry, "layers", {}).get("zpm_boundary", ()))
    sling_layers = set(getattr(registry, "layers", {}).get("sling_wire", ()))
    model_entities = [entity for entity in entities if entity.cad_role == "model"]
    entity_by_key = {entity.entity_key: entity for entity in model_entities}

    def _is_materialized_block_entity(entity: SourceEntity) -> bool:
        plan_domain = entity.raw_properties.get("plan_domain")
        return (
            isinstance(plan_domain, Mapping)
            and plan_domain.get("materialization") == "nested-insert-affine"
        )

    def _is_reviewed_orphan_route(entity: SourceEntity) -> bool:
        """Admit only explicitly recovered, transform-complete route geometry."""

        if route_pattern is None or route_pattern.search(entity.layer) is None:
            return False
        if entity.dwg_type.upper() not in _ROUTE_ENTITY_TYPES:
            return False
        if len(entity.points) < 2:
            return False
        plan_domain = entity.raw_properties.get("plan_domain")
        provenance = entity.raw_properties.get("provenance")
        recovery = (
            provenance.get("orphan_block_recovery")
            if isinstance(provenance, Mapping)
            else None
        )
        return (
            isinstance(plan_domain, Mapping)
            and plan_domain.get("materialization") == "nested-insert-affine"
            and isinstance(plan_domain.get("affine"), Mapping)
            and isinstance(plan_domain.get("orphan_block_recovery"), str)
            and isinstance(recovery, Mapping)
            and recovery.get("authority") == "reviewed_source_profile"
            and recovery.get("block_name")
            == plan_domain.get("orphan_block_recovery")
        )

    attributed_block_styles: dict[str, list[tuple[str, Any]]] = defaultdict(list)
    for entity in model_entities:
        if not _is_materialized_block_entity(entity):
            continue
        if entity.dwg_type.upper() not in {"ATTRIB", "ATTDEF"}:
            continue
        text = entity.text.strip()
        root_key = str(
            entity.raw_properties.get("plan_domain", {}).get("root_entity_key", "")
        )
        if root_key:
            attributed_block_styles[root_key].append((text, entity.style))

    for entity in model_entities:
        if entity.entity_key in catalog_roots:
            # Scene-partition catalog roots are aligned symbol specimens on a
            # legend/style sheet (e.g. coloured POLE/BOITE samples).  They are
            # diagnostic evidence only and must never become delivery assets.
            coverage_records.append(_coverage_record(
                entity, "scene_partition_catalog_root", "EVIDENCE_ONLY",
            ))
            continue
        if (
            _is_materialized_block_entity(entity)
            and entity.dwg_type.upper() not in _ANNOTATION_CARRIER_TYPES
            and not _is_reviewed_orphan_route(entity)
        ):
            # Issue 4 materializes block-definition members as evidence so
            # their TEXT/MTEXT labels can enter semantics.  Non-annotation
            # block geometry (title-frame swatches, hatch, sample lines) is
            # evidence-only: classifying it would silently change reviewed
            # feature inventories and legend detectors.
            coverage_records.append(_coverage_record(
                entity,
                "materialized_block_geometry_evidence_only",
                "EVIDENCE_ONLY",
            ))
            continue
        feature_class = None
        geometry_kind = "Point"
        geometry_role = "SOURCE_ASSET"
        dwg_type = entity.dwg_type.upper()
        if dwg_type == "INSERT":
            feature_class = (
                reverse_blocks.get(entity.block_name.upper())
                or reverse_insert_layers.get(entity.layer.upper())
            )
            if feature_class is None:
                coverage_records.append(_coverage_record(
                    entity, "unknown_insert_block", "UNMAPPED_INSERT",
                ))
            elif feature_class in {"BOITE", "SITE"} and _is_placeholder_block(entity):
                # Annotation/info-card blocks carry only documentation
                # placeholders (D/F/L = "-"/"X"/"K"/...), never a real
                # deployed device; they must not reach delivery.
                coverage_records.append(_coverage_record(
                    entity, "placeholder_annotation_block", feature_class,
                    block_name=entity.block_name,
                ))
                feature_class = None
        elif (
            dwg_type in _ROUTE_ENTITY_TYPES
            and entity.layer.upper() in zpm_layers
            and _is_polygon_ring(entity.points)
        ):
            feature_class, geometry_kind, geometry_role = "ZPM", "Polygon", "SOURCE_BOUNDARY"
        elif (
            dwg_type in _ROUTE_ENTITY_TYPES
            and route_pattern is not None
            and route_pattern.search(entity.layer)
        ):
            feature_class, geometry_kind, geometry_role = "CABLE", "LineString", "SOURCE_ROUTE"
        elif (
            dwg_type in _ROUTE_ENTITY_TYPES
            and entity.layer.upper() in sling_layers
        ):
            # SLING WIRE carries the aerial cable route (reviewed with the
            # green/purple FO core layers); every sling span is one CABLE.
            feature_class, geometry_kind, geometry_role = "CABLE", "LineString", "SOURCE_ROUTE"
        elif dwg_type in _ROUTE_ENTITY_TYPES:
            coverage_records.append(_coverage_record(
                entity, "unmatched_route_layer", "CABLE",
            ))
        elif (
            dwg_type in _ANNOTATION_CARRIER_TYPES
            and entity.layer.upper() in home_layers
        ):
            feature_class, geometry_role = "IMB", "SOURCE_HOME_LABEL"
        if not entity.points:
            coverage_records.append(_coverage_record(
                entity, "missing_geometry_points", feature_class or "UNMAPPED",
            ))
            continue
        if feature_class == "CABLE" and len(entity.points) < 2:
            coverage_records.append(_coverage_record(
                entity, "invalid_geometry_cardinality", "CABLE",
                point_count=len(entity.points),
            ))
            continue
        if not feature_class:
            continue

        attributes = {"CODE": _generated_code(feature_class, entity.handle)}
        provenance = {"CODE": "DWG_DERIVED:stable-handle-id"}
        reviewed_attributes, reviewed_provenance = _registry_attributes(
            entity, feature_class, registry,
        )
        attributes.update(reviewed_attributes)
        provenance.update(reviewed_provenance)
        if feature_class in {"BOITE", "SITE"}:
            device_number = _device_number_attribute(entity)
            if device_number is not None:
                attributes["DEVICE_NUMBER"] = device_number
                provenance["DEVICE_NUMBER"] = "DWG_DIRECT:block-attribute-text"
        if feature_class == "CABLE" and entity.native_length is not None:
            attributes["source_autocad_native_length_m"] = float(entity.native_length)
            provenance["source_autocad_native_length_m"] = (
                "DWG_DIRECT:AutoCAD-curve-distance"
            )
        display_label, label_provenance = _registry_display_label(
            feature_class, attributes, registry,
        )
        feature_style = entity.style
        if dwg_type == "INSERT" and feature_class in {"BOITE", "SITE"}:
            attributed_style = _attributed_block_style(
                entity, attributed_block_styles,
            )
            if attributed_style is not None:
                feature_style = replace(
                    entity.style,
                    aci_color=attributed_style.aci_color,
                    true_color=attributed_style.true_color,
                    linetype=attributed_style.linetype,
                    lineweight=attributed_style.lineweight,
                )

        features.append(Feature(
            feature_key=_feature_key(entity, feature_class),
            feature_class=feature_class,
            geometry_kind=geometry_kind,
            native_points=list(entity.points),
            source_entity_key=entity.entity_key,
            source_handle=entity.handle,
            source_layer=entity.layer,
            geometry_role=geometry_role,
            style=feature_style,
            attributes={key: value for key, value in attributes.items() if value is not None},
            display_label=display_label,
            label_provenance=label_provenance,
            field_provenance=provenance,
            lineage=[{"operation": "identity", "source_entity_key": entity.entity_key, "max_displacement_m": 0.0}],
        ))
        mapped_entities.add(entity.entity_key)

    relations: list[Relation] = []
    by_class = defaultdict(list)
    for feature in features:
        by_class[feature.feature_class].append(feature)
    callout_noise_keys = _annotation_callout_component_keys(
        by_class["CABLE"], by_class["PTECH"] + by_class["SITE"],
    )
    callout_noise_keys.update(_isolated_short_cable_keys(
        by_class["CABLE"],
        by_class["PTECH"] + by_class["SITE"],
        {
            str(layer).strip().upper()
            for layer in getattr(registry, "layers", {}).get("sling_wire", ())
        },
    ))
    callout_noise_keys.update(_dangling_cable_leader_keys(
        by_class["CABLE"], by_class["PTECH"] + by_class["SITE"],
    ))
    annotation_families = tuple(getattr(registry, "annotation_families", ()))
    compiled_families = sorted(
        [
            (
                family,
                re.compile(family.text_pattern),
                re.compile(family.source_layer_pattern),
                re.compile(family.target_layer_pattern),
            )
            for family in annotation_families
        ],
        # Richer patterns first: a FAT block can carry both a subordinate
        # short label (``A07``) and the full BOITE identifier
        # (``KLDYA.011.C01``).  The more specific family must claim the
        # target first so the delivery label is the full asset identifier,
        # not the port/slot stub.  Registry order remains the tie-break.
        key=lambda item: -annotation_pattern_specificity(item[0].text_pattern),
    )
    annotations_by_family = defaultdict(list)
    unclaimed_pole_annotations: list[SourceEntity] = []
    annotation_discovery_failures = []
    label_rules = getattr(registry, "labels", {})
    suspected_pattern = str(label_rules.get("suspected_asset_id", "") or "")
    site_pattern = str(label_rules.get("site", "") or "")
    suspected_asset_id = re.compile(suspected_pattern) if suspected_pattern else None
    known_non_assignment_labels = [re.compile(site_pattern)] if site_pattern else []
    # Reviewed semantic fallback for pole identifiers: text follows the
    # reviewed annotation-family patterns (target_class=PTECH) and the
    # layer carries POLE semantics.  It activates only for annotations and
    # targets that no reviewed exact family claimed, so reviewed families
    # keep priority.  Patterns come from the registry — never hardcoded.
    POLE_LABEL_FAMILY_ID = "pole_label_semantic"
    reviewed_pole_patterns = [
        re.compile(family.text_pattern)
        for family in annotation_families
        if family.target_class == "PTECH"
    ]
    _pole_bodies = []
    for _pattern in reviewed_pole_patterns:
        _body = _pattern.pattern
        # Hoist a leading (?i) global flag to the joined expression.
        while _body.startswith("(?i)"):
            _body = _body[4:]
        _pole_bodies.append(_body)
    pole_label_text = (
        re.compile("(?i)(?:" + "|".join(_pole_bodies) + ")")
        if _pole_bodies
        else re.compile(r"(?!)")
    )
    pole_label_layer = re.compile(r"(?i)POLE")
    for entity in model_entities:
        if entity.dwg_type not in _ANNOTATION_CARRIER_TYPES or not entity.text.strip():
            continue
        text = entity.text.strip()
        text_matches = [
            family
            for family, text_pattern, _, _ in compiled_families
            if text_pattern.fullmatch(text)
        ]
        exact_matches = [
            family
            for family, text_pattern, source_layer_pattern, _ in compiled_families
            if text_pattern.fullmatch(text)
            and source_layer_pattern.fullmatch(entity.layer.strip())
            and (
                family.aci_color is None
                or family.aci_color == entity.style.aci_color
            )
        ]
        if len(exact_matches) == 1:
            annotations_by_family[exact_matches[0].family_id].append(entity)
            continue
        if len(exact_matches) > 1:
            annotation_discovery_failures.append({
                "kind": "annotation",
                "entity_key": entity.entity_key,
                "family_id": "|".join(sorted(family.family_id for family in exact_matches)),
                "text": text,
                "source_layer": entity.layer,
                "status": "multiple_annotation_families",
            })
        elif (
            not text_matches
            and pole_label_text.fullmatch(text)
            and pole_label_layer.search(entity.layer)
        ):
            # Reviewed family did not claim this pole label (e.g. its
            # source layer was named after a legend sample).  The semantic
            # fallback carries the same reviewed assignment contract.
            annotations_by_family[POLE_LABEL_FAMILY_ID].append(entity)
            continue
        elif (
            not text_matches
            and pole_label_layer.search(entity.layer)
            and is_pole_identifier_shape(text)
            and not is_placeholder_text(text)
        ):
            # The reviewed registry families miss this project's actual
            # POLE-semantic label shape (observed on lamteh POLE ID and
            # kletek EXT POLE).  Claim it for the same semantic pole-label
            # fallback instead of silently leaving the PTECH CODE as a
            # generated handle.
            unclaimed_pole_annotations.append(entity)
            continue
        elif text_matches:
            boite_matches = [
                family for family in text_matches if family.target_class == "BOITE"
            ]
            if len(boite_matches) == 1:
                # Some APD drawings duplicate their FAT-code text on a
                # non-family layer (e.g. a basic-map numbering layer) while
                # the actual FAT geometry is a derived rectangular frame.
                # The reviewed family contract still owns the text shape;
                # keep the label with that family so the normal assignment
                # pass can claim a cross-layer frame target.  Non-derived
                # INSERT targets keep the reviewed same-layer contract.
                annotations_by_family[boite_matches[0].family_id].append(entity)
                annotation_discovery_failures.append({
                    "kind": "annotation",
                    "entity_key": entity.entity_key,
                    "family_id": boite_matches[0].family_id,
                    "text": text,
                    "source_layer": entity.layer,
                    "status": "cross_layer_boite_label",
                })
                continue
            annotation_discovery_failures.append({
                "kind": "annotation",
                "entity_key": entity.entity_key,
                "family_id": "|".join(sorted(family.family_id for family in text_matches)),
                "text": text,
                "source_layer": entity.layer,
                "status": "source_layer_mismatch",
            })
        elif suspected_asset_id is not None and suspected_asset_id.fullmatch(text) and not any(
            pattern.fullmatch(text) for pattern in known_non_assignment_labels
        ):
            annotation_discovery_failures.append({
                "kind": "annotation",
                "entity_key": entity.entity_key,
                "family_id": "UNRECOGNIZED",
                "text": text,
                "source_layer": entity.layer,
                "status": "unrecognized_asset_id",
            })
        elif not compiled_families and suspected_asset_id is None:
            coverage_records.append(_coverage_record(
                entity, "unreviewed_annotation_carrier", "LABEL",
                text=text,
            ))

    # Some validation drawings encode devices as bare POINT entities
    # (PTECH/SITE) or as small closed rectangular frames (BOITE) instead of
    # INSERTs.  A reviewed annotation family is the only evidence that such
    # geometry is a device: create one target feature per labelled candidate
    # so the family assignment contract can claim it.  Entities already
    # mapped as INSERT features, and entities without a matching reviewed
    # label, remain untouched.
    point_candidates = [
        entity for entity in model_entities
        if entity.dwg_type.upper() == "POINT"
        and entity.entity_key not in mapped_entities
    ]
    frame_candidates = []
    for entity in model_entities:
        if entity.entity_key in mapped_entities:
            continue
        if entity.dwg_type.upper() != "LWPOLYLINE":
            continue
        points = entity.points
        if len(points) < 4:
            continue
        is_closed_rectangle = math.dist(points[0], points[-1]) <= 1.0
        if is_closed_rectangle:
            area = abs(sum(
                points[i][0] * points[(i + 1) % len(points)][1]
                - points[(i + 1) % len(points)][0] * points[i][1]
                for i in range(len(points))
            )) / 2.0
            if not (0.05 <= area <= 400.0):
                continue
        elif not _is_open_rectangle_callout(points):
            continue
        frame_candidates.append(entity)
    integer_text_entities = [
        entity for entity in model_entities
        if entity.dwg_type in _ANNOTATION_CARRIER_TYPES
        and str(entity.text or "").strip().isdigit()
        and (
            "LABEL" in str(entity.layer).upper()
            or "FAT" in str(entity.layer).upper()
        )
    ]
    used_point_entities: set[str] = set()
    used_integer_texts: set[str] = set()
    derived_target_keys: set[str] = set()
    boite_frame_layers = {
        str(layer).strip().upper()
        for layer in getattr(registry, "insert_layer_families", {}).get("BOITE", ())
    }
    # Structural BOITE-frame admission: an LWPOLYLINE on a reviewed BOITE
    # layer is only a device when a reviewed BOITE annotation-family label
    # (e.g. ``TGGR04-1.022.A01``) proves it.  Unlabelled map/info rectangles
    # and pole-legend specimens stay abstained instead of being promoted by
    # their colour or their layer membership alone.
    boite_label_evidence: list[tuple[SourceEntity, float]] = []
    for family in annotation_families:
        if family.target_class != "BOITE":
            continue
        if (
            annotation_pattern_specificity(family.text_pattern)
            < _BOITE_FRAME_MIN_LABEL_SPECIFICITY
        ):
            continue
        tolerance = float(getattr(family, "max_distance_native_m", 15.0) or 15.0)
        for entity in annotations_by_family.get(family.family_id, ()):
            if not any(entity.entity_key == item.entity_key for item, _ in boite_label_evidence):
                boite_label_evidence.append((entity, tolerance))

    def _has_reviewed_boite_label(frame: Any) -> bool:
        return any(
            math.dist(frame.centroid, entity.centroid) <= tolerance
            for entity, tolerance in boite_label_evidence
        )

    def _device_number_for_frame(frame: Any) -> tuple[str | None, str | None, dict | None]:
        integer_nearby = sorted(
            (
                math.dist(frame.centroid, item.centroid),
                item.handle,
                item.entity_key,
                item,
            )
            for item in integer_text_entities
            if item.entity_key not in used_integer_texts
        )
        if integer_nearby and integer_nearby[0][0] <= 20.0:
            number_entity = integer_nearby[0][3]
            used_integer_texts.add(number_entity.entity_key)
            return (
                number_entity.text.strip(),
                "DWG_DERIVED:nearby-integer-label",
                {
                    "operation": "select_device_number_label",
                    "source_entity_key": number_entity.entity_key,
                    "source_handle": number_entity.handle,
                    "target_source_entity_key": frame.entity_key,
                    "field_name": "DEVICE_NUMBER",
                    "selected_value": number_entity.text.strip(),
                    "distance_native_m": integer_nearby[0][0],
                    "method": "nearest_unused_integer_label_within_20_native_m",
                    "review_status": "required",
                    "geometry_changed": False,
                },
            )
        return None, None, None

    # A small rectangular frame on a reviewed BOITE layer is promoted only
    # when a reviewed BOITE identifier label proves it is a deployed FAT
    # device.  Bare map rectangles, legend samples and info-card frames have
    # no such label and remain abstained coverage records.
    for frame in frame_candidates:
        frame_layer_upper = str(frame.layer).strip().upper()
        if frame_layer_upper not in boite_frame_layers:
            continue
        if not any(
            token in frame_layer_upper
            for token in _BOITE_FRAME_DEVICE_LAYER_TOKENS
        ):
            # Base-map rectangles (e.g. ``Basic Map``) are not deployed
            # devices even when a nearby BOITE-style text exists.
            continue
        if not _has_reviewed_boite_label(frame) and not _is_materialized_block_entity(frame):
            # Unlabelled raw LWPOLYLINE rectangles are map/info furniture;
            # only a reviewed BOITE identifier label or an actual nested-INSERT
            # block frame proves a deployed FAT/closure device.
            continue
        if any(
            math.dist(feature.native_centroid, frame.centroid) <= 0.5
            for feature in by_class["BOITE"]
        ):
            continue
        number_value, number_provenance, number_evidence = _device_number_for_frame(frame)
        attributes = {"CODE": _generated_code("BOITE", frame.handle)}
        provenance = {"CODE": _GENERATED_CODE_PROVENANCE}
        if number_value is not None:
            attributes["DEVICE_NUMBER"] = number_value
            provenance["DEVICE_NUMBER"] = number_provenance
        frame_feature = Feature(
            feature_key=_feature_key(frame, "BOITE"),
            feature_class="BOITE",
            geometry_kind="Point",
            native_points=[list(frame.centroid)],
            source_entity_key=frame.entity_key,
            source_handle=frame.handle,
            source_layer=frame.layer,
            geometry_role="SOURCE_ASSET",
            style=frame.style,
            attributes=attributes,
            display_label="",
            label_provenance="UNAVAILABLE",
            field_provenance=provenance,
            lineage=[{
                "operation": "rectangular_frame_centroid",
                "source_entity_key": frame.entity_key,
                "max_displacement_m": 0.0,
            }] + ([number_evidence] if number_evidence else []),
        )
        features.append(frame_feature)
        by_class["BOITE"].append(frame_feature)
        derived_target_keys.add(frame_feature.feature_key)
        mapped_entities.add(frame.entity_key)

    for family, _, _, _ in compiled_families:
        if family.target_class not in {"PTECH", "SITE"}:
            continue
        for annotation in sorted(
            annotations_by_family[family.family_id],
            key=lambda item: (item.text.casefold(), item.entity_key),
        ):
            if _is_materialized_block_entity(annotation):
                # Block-definition template labels (e.g. ``NP7`` inside a
                # pole symbol) must not fabricate new POINT targets; they
                # already label their own INSERT when applicable.
                continue
            # Do not materialize a POINT target when the annotation already
            # has an eligible INSERT-derived target inside the family
            # tolerance: the normal assignment loop owns that relationship.
            if any(
                _annotation_target_eligible(target)
                and math.dist(annotation.centroid, target.native_centroid)
                <= family.max_distance_native_m
                for target in by_class[family.target_class]
                if target.feature_key not in derived_target_keys
            ):
                continue
            nearby = sorted(
                (
                    math.dist(annotation.centroid, point.centroid),
                    point.handle,
                    point.entity_key,
                    point,
                )
                for point in point_candidates
                if point.entity_key not in used_point_entities
            )
            if not nearby:
                continue
            nearest = nearby[0]
            distance, point = nearest[0], nearest[3]
            if distance > family.max_distance_native_m:
                continue
            if any(
                math.dist(feature.native_centroid, point.centroid) <= 0.5
                for feature in by_class[family.target_class]
            ):
                continue
            used_point_entities.add(point.entity_key)
            centroid = list(point.points[0] if point.points else point.centroid)
            point_feature = Feature(
                feature_key=_feature_key(point, family.target_class),
                feature_class=family.target_class,
                geometry_kind="Point",
                native_points=[centroid],
                source_entity_key=point.entity_key,
                source_handle=point.handle,
                source_layer=point.layer,
                geometry_role="SOURCE_ASSET",
                style=annotation.style,
                attributes={"CODE": _generated_code(family.target_class, point.handle)},
                display_label="",
                label_provenance="UNAVAILABLE",
                field_provenance={"CODE": _GENERATED_CODE_PROVENANCE},
                lineage=[{
                    "operation": "identity",
                    "source_entity_key": point.entity_key,
                    "max_displacement_m": 0.0,
                }],
            )
            features.append(point_feature)
            by_class[family.target_class].append(point_feature)
            derived_target_keys.add(point_feature.feature_key)
            mapped_entities.add(point.entity_key)

    annotation_candidates = []
    annotation_assignments_by_family = {}
    annotation_assignments = defaultdict(lambda: {
        "source_annotations": 0,
        "assigned": 0,
        "missing": 0,
        "unresolved": 0,
        "cross_layer_assignments": 0,
        "total_distance_native_m": 0.0,
    })
    annotation_rule = (
        getattr(registry, "decision_rules", {}).get("annotation_assignment")
        if compiled_families else None
    )
    if compiled_families and not annotation_rule:
        raise ValueError(
            "annotation_assignment decision rule is required when annotation_families are configured"
        )
    fallback_annotations = annotations_by_family.pop(POLE_LABEL_FAMILY_ID, [])
    fallback_annotations.extend(unclaimed_pole_annotations)
    fallback_failure_annotations: list[SourceEntity] = []
    for family, _, _, target_layer_pattern in compiled_families:
        annotations = annotations_by_family[family.family_id]
        # Families that share a target layer are assigned sequentially: the
        # first family claims eligible targets, the next family only sees the
        # remaining eligible targets.  Blanket overlap exclusion used to make
        # two reviewed BOITE text families (``DMPH-...`` and ``MR.XXX...``)
        # mutually annihilate each other and leave every target labelled by a
        # generated handle.
        family_targets = [
            target
            for target in by_class[family.target_class]
            if (
                target_layer_pattern.fullmatch(target.source_layer.strip())
                or target.feature_key in derived_target_keys
            )
        ]
        assignments, failures, candidates = _assign_family_annotations(
            annotations,
            family_targets,
            family.max_distance_native_m,
            family_id=family.family_id,
            require_same_layer=family.require_same_layer,
            cross_layer_target_keys=derived_target_keys,
        )
        for item in failures:
            unresolved.append({**item, "target_class": family.target_class})
            source_entity = entity_by_key.get(item.get("entity_key"))
            if source_entity is not None:
                coverage_records.append(_coverage_record(
                    source_entity,
                    f"annotation_{item['status']}",
                    family.target_class,
                    family_id=family.family_id,
                    text=str(item.get("text", "")),
                ))
            if (
                source_entity is not None
                and pole_label_text.fullmatch(str(item.get("text", "")).strip())
                and pole_label_layer.search(str(source_entity.layer))
            ):
                fallback_failure_annotations.append(source_entity)
        for candidate in candidates:
            assignment_provenance = (
                f"{family.provenance}|RULE:{annotation_rule['rule_id']}"
            )
            annotation_candidates.append({
                **candidate,
                "target_class": family.target_class,
                "rule_id": family.rule_id,
                "provenance": assignment_provenance,
            })
        for entity, target, distance in assignments:
            assignment_provenance = (
                f"{family.provenance}|RULE:{annotation_rule['rule_id']}"
            )
            target.attributes["CODE"] = entity.text.strip()
            target.field_provenance["CODE"] = assignment_provenance
            target.display_label = entity.text.strip()
            target.label_provenance = assignment_provenance
            relation_key = hashlib.sha256(
                f"{entity.entity_key}|labels|{target.feature_key}".encode()
            ).hexdigest()
            relations.append(Relation(
                relation_key=relation_key, relation_kind="labels",
                source_key=entity.entity_key, target_key=target.feature_key,
                status="accepted",
                method=(
                    f"{family.family_id}:{family.rule_id}:"
                    f"{annotation_rule['rule_id']}:{annotation_rule['method']}"
                ),
                distance_native_m=distance,
                evidence_keys=(entity.entity_key, target.source_entity_key),
            ))
            mapped_entities.add(entity.entity_key)
        layer_diagnostics = _annotation_layer_diagnostics(
            assignments, require_same_layer=family.require_same_layer,
            derived_target_keys=derived_target_keys,
        )
        family_diagnostics = {
            "target_class": family.target_class,
            "target_assets": len(family_targets),
            "source_annotations": len(annotations),
            "assigned": len(assignments),
            "missing": len(annotations) - len(assignments),
            "unresolved": len(failures),
            **layer_diagnostics,
            "total_distance_native_m": sum(item[2] for item in assignments),
            "max_distance_native_m": family.max_distance_native_m,
            "require_same_layer": family.require_same_layer,
        }
        annotation_assignments_by_family[family.family_id] = family_diagnostics
        aggregate = annotation_assignments[family.target_class]
        for key in (
            "source_annotations", "assigned", "missing", "unresolved",
            "cross_layer_assignments", "total_distance_native_m",
        ):
            aggregate[key] += family_diagnostics[key]

    # Semantic pole-label fallback: assign POLE-semantic labels to PTECH
    # targets that no reviewed family labelled (permissive same-layer match).
    if fallback_annotations or fallback_failure_annotations:
        _seen_fallback: set[str] = set()
        _merged_fallback: list[SourceEntity] = []
        for _entity in fallback_annotations + fallback_failure_annotations:
            if _entity.entity_key in _seen_fallback:
                continue
            _seen_fallback.add(_entity.entity_key)
            _merged_fallback.append(_entity)
        fallback_annotations = _merged_fallback
        family_targets = [
            target
            for target in by_class["PTECH"]
            if _annotation_target_eligible(target)
        ]
        if family_targets:
            fallback_rule = (
                getattr(registry, "decision_rules", {}).get("annotation_assignment")
                or {"rule_id": "SEMANTIC-POLE-LABEL-001", "method": "global-minimum-cost-family-assignment"}
            )
            assignments, failures, candidates = _assign_family_annotations(
                fallback_annotations,
                family_targets,
                30.0,
                family_id=POLE_LABEL_FAMILY_ID,
                require_same_layer=False,
            )
            for item in failures:
                unresolved.append({**item, "target_class": "PTECH"})
                source_entity = entity_by_key.get(item.get("entity_key"))
                if source_entity is not None:
                    coverage_records.append(_coverage_record(
                        source_entity,
                        f"annotation_{item['status']}",
                        "PTECH",
                        family_id=POLE_LABEL_FAMILY_ID,
                        text=str(item.get("text", "")),
                    ))
            for candidate in candidates:
                annotation_candidates.append({
                    **candidate,
                    "target_class": "PTECH",
                    "rule_id": "SEMANTIC-POLE-LABEL-001",
                    "provenance": (
                        "DWG_DERIVED:pole-label-semantic|"
                        f"RULE:{fallback_rule['rule_id']}"
                    ),
                })
            for entity, target, distance in assignments:
                assignment_provenance = (
                    "DWG_DERIVED:pole-label-semantic|"
                    f"RULE:{fallback_rule['rule_id']}"
                )
                target.attributes["CODE"] = entity.text.strip()
                target.field_provenance["CODE"] = assignment_provenance
                target.display_label = entity.text.strip()
                target.label_provenance = assignment_provenance
                relation_key = hashlib.sha256(
                    f"{entity.entity_key}|labels|{target.feature_key}".encode()
                ).hexdigest()
                relations.append(Relation(
                    relation_key=relation_key, relation_kind="labels",
                    source_key=entity.entity_key, target_key=target.feature_key,
                    status="accepted",
                    method=(
                        f"{POLE_LABEL_FAMILY_ID}:SEMANTIC-POLE-LABEL-001:"
                        f"{fallback_rule['rule_id']}:{fallback_rule['method']}"
                    ),
                    distance_native_m=distance,
                    evidence_keys=(entity.entity_key, target.source_entity_key),
                ))
                mapped_entities.add(entity.entity_key)
            annotation_assignments_by_family[POLE_LABEL_FAMILY_ID] = {
                "target_class": "PTECH",
                "target_assets": len(family_targets),
                "source_annotations": len(fallback_annotations),
                "assigned": len(assignments),
                "missing": len(fallback_annotations) - len(assignments),
                "unresolved": len(failures),
                "cross_layer_assignments": sum(
                    entity.layer.strip().casefold() != target.source_layer.strip().casefold()
                    for entity, target, _ in assignments
                ),
                "total_distance_native_m": sum(item[2] for item in assignments),
                "max_distance_native_m": 15.0,
                "require_same_layer": False,
            }
            aggregate = annotation_assignments["PTECH"]
            for key in (
                "source_annotations", "assigned", "missing", "unresolved",
                "cross_layer_assignments", "total_distance_native_m",
            ):
                aggregate[key] += annotation_assignments_by_family[POLE_LABEL_FAMILY_ID][key]

    feature_by_key = {feature.feature_key: feature for feature in features}
    target_memberships = defaultdict(list)
    for family, _, source_layer_pattern, target_layer_pattern in compiled_families:
        for target in by_class[family.target_class]:
            target_layer = target.source_layer.strip()
            matched = (
                bool(source_layer_pattern.fullmatch(target_layer))
                if family.require_same_layer
                else bool(target_layer_pattern.fullmatch(target_layer))
            )
            if matched:
                target_memberships[target.feature_key].append(family.family_id)
    for target_key, family_ids in sorted(target_memberships.items()):
        target = feature_by_key[target_key]
        if not target.display_label:
            source_entity = entity_by_key.get(target.source_entity_key)
            if source_entity is not None:
                coverage_records.append(_coverage_record(
                    source_entity, "missing_reviewed_label", target.feature_class,
                    annotation_families=sorted(family_ids),
                ))

    # EMR equipment symbols: an ``EMR-xxxx`` label with one or more short
    # orange FO-core loops around it is an equipment symbol (the orange
    # concentric square), not cable spans and not legend noise.
    emr_label_entities = sorted(
        (
            entity
            for entity in model_entities
            if entity.dwg_type in _ANNOTATION_CARRIER_TYPES
            and _EMR_LABEL_RE.fullmatch(entity.text.strip())
        ),
        key=lambda entity: entity.text.casefold(),
    )
    for emr_entity in emr_label_entities:
        symbol_cables = [
            feature
            for feature in features
            if feature.feature_class == "CABLE"
            and feature.native_points
            and _polyline_nearest_distance(
                emr_entity.centroid, feature.native_points,
            ) <= _EMR_SYMBOL_RADIUS_M
            and sum(
                math.dist(start, end)
                for start, end in zip(feature.native_points, feature.native_points[1:])
            ) <= _EMR_SHORT_CABLE_LENGTH_M
        ]
        style = (
            symbol_cables[0].style
            if symbol_cables
            else CadStyle(aci_color=30, true_color="#FF7F00")
        )
        code = emr_entity.text.strip()
        emr_feature = Feature(
            feature_key=_feature_key(emr_entity, "EMR"),
            feature_class="EMR",
            geometry_kind="Point",
            native_points=[list(emr_entity.centroid)],
            source_entity_key=emr_entity.entity_key,
            source_handle=emr_entity.handle,
            source_layer=emr_entity.layer,
            geometry_role="SOURCE_ASSET",
            style=style,
            attributes={
                "CODE": code,
                "TYPE": "EMR",
                "STATUT": "DEPLOYE",
            },
            display_label=code,
            label_provenance="DWG_DIRECT:emr-label",
            field_provenance={
                "CODE": "DWG_DIRECT:emr-label",
                "TYPE": "DWG_DERIVED:emr-symbol-class",
                "STATUT": "DWG_DERIVED:reviewed-domain-default",
            },
            lineage=[{
                "operation": "identity",
                "source_entity_key": emr_entity.entity_key,
                "max_displacement_m": 0.0,
            }],
        )
        features.append(emr_feature)
        mapped_entities.add(emr_entity.entity_key)
        callout_noise_keys.update(
            feature.feature_key for feature in symbol_cables
        )

    # PATCHCORD OUTDOOR is carried by the ONT-MDU drawing layer in the
    # EMR-29619 spur.  It is a physical cable span and belongs in CABLE.
    patchcord_layers = {
        str(layer).strip().upper()
        for layer in getattr(registry, "layers", {}).get("patchcord", ())
    }
    for entity in model_entities:
        if entity.entity_key in mapped_entities:
            continue
        if entity.dwg_type not in _ROUTE_ENTITY_TYPES:
            continue
        if str(entity.layer).strip().upper() not in patchcord_layers:
            continue
        if len(entity.points) < 2:
            continue
        attributes = {
            "CODE": _generated_code("CABLE", entity.handle),
            "TYPE_CABLE": "RACCORDEMENT",
        }
        provenance = {
            "CODE": "DWG_DERIVED:stable-handle-id",
            "TYPE_CABLE": "DWG_DIRECT:patchcord-outdoor-layer",
        }
        display_label = "PATCHCORD OUTDOOR"
        patchcord_feature = Feature(
            feature_key=_feature_key(entity, "CABLE"),
            feature_class="CABLE",
            geometry_kind="LineString",
            native_points=list(entity.points),
            source_entity_key=entity.entity_key,
            source_handle=entity.handle,
            source_layer=entity.layer,
            geometry_role="SOURCE_ROUTE",
            style=entity.style,
            attributes=attributes,
            display_label=display_label,
            label_provenance="DWG_DIRECT:patchcord-outdoor-layer",
            field_provenance=provenance,
            lineage=[{
                "operation": "identity",
                "source_entity_key": entity.entity_key,
                "max_displacement_m": 0.0,
            }],
        )
        if entity.native_length is not None:
            patchcord_feature.attributes["source_autocad_native_length_m"] = float(
                entity.native_length
            )
            patchcord_feature.field_provenance["source_autocad_native_length_m"] = (
                "DWG_DIRECT:AutoCAD-curve-distance"
            )
        features.append(patchcord_feature)
        mapped_entities.add(entity.entity_key)

    # Real-world connectivity rule: a PTECH drawn just off a cable endpoint
    # is a drafting gap, not a disconnected pole.  Bridge the unique near
    # miss by moving the cable endpoint onto the PTECH point; topology then
    # records an exact ``connects`` relation.
    def _reindex_by_class(feature_list):
        indexed = defaultdict(list)
        for item in feature_list:
            indexed[item.feature_class].append(item)
        return indexed

    by_class = _reindex_by_class(features)
    bridged_endpoints = []
    for support in by_class["PTECH"] + by_class["EMR"]:
        if not support.native_points:
            continue
        support_point = support.native_points[0]
        deployed_cables = [
            route for route in by_class["CABLE"]
            if route.feature_key not in callout_noise_keys
        ]
        if any(
            _polyline_nearest_distance(support_point, route.native_points) <= 0.5
            for route in deployed_cables
        ):
            continue
        endpoint_candidates = []
        for route in deployed_cables:
            source_route = entity_by_key.get(route.source_entity_key)
            if source_route is None or not supports_endpoint_bridge(source_route):
                continue
            if any(item.get("operation") == "bridge_cable_endpoint_to_pole" for item in route.lineage):
                continue
            if len(route.native_points) < 2:
                continue
            for index, endpoint in (
                (0, route.native_points[0]),
                (-1, route.native_points[-1]),
            ):
                distance = math.dist(support_point, endpoint)
                if _PTECH_CABLE_ENDPOINT_MIN_GAP_M <= distance <= _PTECH_CABLE_ENDPOINT_BRIDGE_M:
                    endpoint_candidates.append((distance, route, index))
        if not endpoint_candidates:
            continue
        endpoint_candidates.sort(key=lambda item: item[0])
        best_distance, best_route, best_index = endpoint_candidates[0]
        if (
            len(endpoint_candidates) > 1
            and endpoint_candidates[1][0] - best_distance < 2.0
        ):
            continue
        source_endpoint = list(best_route.native_points[best_index])
        if not apply_geometry_repairs:
            if geometry_candidates is not None:
                geometry_candidates.append({
                    "operation": "bridge_cable_endpoint_to_pole", "feature_key": best_route.feature_key,
                    "source_entity_key": best_route.source_entity_key,
                    "support_source_entity_key": support.source_entity_key,
                    "endpoint_index": best_index % len(best_route.native_points),
                    "source_points": deepcopy(best_route.native_points),
                    "source_endpoint": source_endpoint, "target_endpoint": list(support_point),
                    "max_displacement_native": best_distance, "applied": False, "review_status": "required",
                })
            continue
        best_route.native_points[best_index] = [float(support_point[0]), float(support_point[1])]
        best_route.geometry_role = "DERIVED_ROUTE"
        best_route.lineage.append({
            "operation": "bridge_cable_endpoint_to_pole",
            "source_entity_key": support.source_entity_key,
            "route_source_entity_key": best_route.source_entity_key,
            "support_feature_key": support.feature_key,
            "support_handle": support.source_handle,
            "endpoint_index": best_index % len(best_route.native_points),
            "source_endpoint": source_endpoint,
            "target_endpoint": list(best_route.native_points[best_index]),
            "max_displacement_m": best_distance,
        })
        bridged_endpoints.append({
            "node_class": support.feature_class,
            "node": support.source_handle,
            "cable": best_route.source_handle,
            "gap_m": best_distance,
        })

    # Legend core detection: a drawing legend lays one sample per cable
    # type on the sheet edge — the sample cables repeat the same length
    # (each type drawn at the standard stub length), cluster in one column
    # (x-same-line), and span several distinct layers.  The core region
    # around those samples (points and lines alike) is legend material and
    # must not reach delivery or inflate the calibration coverage envelope.
    # Pure geometry/frequency — no layer names, labels, or coordinates are
    # hardcoded.
    _LEGEND_GROUP_MIN_MEMBERS = 3
    _LEGEND_GROUP_X_SPAN_M = 200.0
    _LEGEND_GROUP_Y_SPAN_M = 500.0
    _LEGEND_GROUP_MIN_LAYERS = 3
    _LEGEND_CORE_PADDING_M = 100.0
    # Density-connected legend core: the padding box around the sample
    # cables can also cover real deployments drawn close to the legend
    # column.  Only entities connected to a sample by a chain of
    # < _LEGEND_CLUSTER_EPS_M steps are legend material; isolated real
    # infrastructure (poles, ZPM boundaries, sling spans) survives.
    _LEGEND_CLUSTER_EPS_M = 30.0

    cable_features = [
        feature
        for feature in features
        if feature.feature_class == "CABLE" and feature.native_points
    ]
    length_groups: dict[int, list[Feature]] = defaultdict(list)
    for feature in cable_features:
        points = feature.native_points
        length = sum(
            math.dist(points[i], points[i + 1])
            for i in range(len(points) - 1)
        )
        length_groups[round(length)].append(feature)

    legend_sample_keys: set[str] = set()
    for group in length_groups.values():
        if len(group) < _LEGEND_GROUP_MIN_MEMBERS:
            continue
        starts = [feature.native_points[0] for feature in group]
        x_span = max(point[0] for point in starts) - min(point[0] for point in starts)
        y_span = max(point[1] for point in starts) - min(point[1] for point in starts)
        layer_count = len({feature.source_layer for feature in group})
        if (
            x_span <= _LEGEND_GROUP_X_SPAN_M
            and y_span <= _LEGEND_GROUP_Y_SPAN_M
            and layer_count >= _LEGEND_GROUP_MIN_LAYERS
        ):
            legend_sample_keys.update(
                feature.feature_key for feature in group
            )

    legend_core_keys: set[str] = set()
    if legend_sample_keys:
        sample_points = [
            feature.native_points[0]
            for feature in features
            if feature.feature_key in legend_sample_keys
        ]
        core_min_x = min(point[0] for point in sample_points) - _LEGEND_CORE_PADDING_M
        core_max_x = max(point[0] for point in sample_points) + _LEGEND_CORE_PADDING_M
        core_min_y = min(point[1] for point in sample_points) - _LEGEND_CORE_PADDING_M
        core_max_y = max(point[1] for point in sample_points) + _LEGEND_CORE_PADDING_M
        in_core: list[Feature] = [
            feature
            for feature in features
            if feature.feature_class != "IMB"
            and feature.native_points
            and core_min_x <= feature.native_points[0][0] <= core_max_x
            and core_min_y <= feature.native_points[0][1] <= core_max_y
        ]
        sample_key_set = frozenset(feature.feature_key for feature in features
                                   if feature.feature_key in legend_sample_keys)
        # Union-find over candidate starts + sample starts; a candidate is
        # excluded only when density-connected to a legend sample.
        parent: dict[int, int] = {}
        candidates = in_core + [
            feature for feature in features if feature.feature_key in sample_key_set
        ]

        def _find(node: int) -> int:
            while parent.get(node, node) != node:
                parent[node] = parent[parent[node]]
                node = parent[node]
            return node

        def _union(left: int, right: int) -> None:
            lroot, rroot = _find(left), _find(right)
            if lroot != rroot:
                parent[rroot] = lroot

        for index in range(len(candidates)):
            parent.setdefault(index, index)
        for left in range(len(candidates)):
            for right in range(left + 1, len(candidates)):
                if math.dist(candidates[left].native_points[0],
                             candidates[right].native_points[0]) <= _LEGEND_CLUSTER_EPS_M:
                    _union(left, right)
        sample_indices = {
            index for index, feature in enumerate(candidates)
            if feature.feature_key in sample_key_set
        }
        if sample_indices:
            legend_core_keys = set(legend_sample_keys)
            legend_core_keys.update(
                feature.feature_key
                for index, feature in enumerate(candidates)
                if index not in sample_indices
                and any(_find(index) == _find(sample_index) for sample_index in sample_indices)
            )

    # Unlabelled asset sample: a deployed asset INSERT carries its
    # identifier label nearby; a legend specimen (one per type on the sheet
    # edge) has none.  The identifier patterns come from the reviewed
    # annotation families (target_class=PTECH) — never hardcoded here.
    reviewed_label_patterns = [
        re.compile(family.text_pattern)
        for family in annotation_families
        if family.target_class == "PTECH"
    ]
    identifier_texts = [
        entity.centroid
        for entity in model_entities
        if entity.dwg_type in _ANNOTATION_CARRIER_TYPES
        and entity.text.strip()
        and not is_placeholder_text(entity.text)
        and any(
            pattern.fullmatch(entity.text.strip())
            for pattern in reviewed_label_patterns
        )
    ]
    if reviewed_label_patterns and identifier_texts:
        _LEGEND_ASSET_LABEL_RADIUS_M = 50.0
        for feature in features:
            if feature.feature_class not in {"PTECH", "BOITE", "SITE"}:
                continue
            source = entity_by_key.get(feature.source_entity_key)
            if (
                source is not None
                and source.dwg_type.upper() == "INSERT"
                and not any(
                    math.dist(source.centroid, text_centroid) <= _LEGEND_ASSET_LABEL_RADIUS_M
                    for text_centroid in identifier_texts
                )
            ):
                legend_core_keys.add(feature.feature_key)

    # Legend sample columns: a legend draws one specimen per pole/box type,
    # arranged in a strictly vertical (same x) or horizontal (same y)
    # equally-spaced column.  Real deployments follow roads, so their x/y
    # and spacing vary.  Pure geometry — independent of reviewed label
    # patterns, which an AI onboarding pass may mis-generate as placeholder
    # matches (MR.XXX.Pxxx) and thereby silently disable label-based rules.
    _LEGEND_COLUMN_MIN_MEMBERS = 3
    _LEGEND_COLUMN_TOLERANCE_FRACTION = 0.0005
    _LEGEND_COLUMN_TOLERANCE_MIN_M = 5.0
    _LEGEND_SPACING_CV_MAX = 0.2
    asset_inserts = [
        feature
        for feature in features
        if feature.feature_class in {"PTECH", "BOITE", "SITE"}
        and feature.geometry_kind == "Point"
        and feature.native_points
        and feature.feature_key not in legend_core_keys
        and entity_by_key.get(feature.source_entity_key) is not None
        and entity_by_key[feature.source_entity_key].dwg_type.upper() == "INSERT"
    ]
    if len(asset_inserts) >= _LEGEND_COLUMN_MIN_MEMBERS:
        _LEGEND_COLUMN_CABLE_RADIUS_M = 50.0
        _xs = [f.native_points[0][0] for f in asset_inserts]
        _ys = [f.native_points[0][1] for f in asset_inserts]
        _tol_x = max(_LEGEND_COLUMN_TOLERANCE_MIN_M,
                     _LEGEND_COLUMN_TOLERANCE_FRACTION * (max(_xs) - min(_xs)))
        _tol_y = max(_LEGEND_COLUMN_TOLERANCE_MIN_M,
                     _LEGEND_COLUMN_TOLERANCE_FRACTION * (max(_ys) - min(_ys)))

        def _regular_spacing(values: Sequence[float]) -> bool:
            ordered = sorted(values)
            spacings = [ordered[i + 1] - ordered[i] for i in range(len(ordered) - 1)]
            mean = sum(spacings) / len(spacings)
            if mean <= 0.0:
                return False
            variance = sum((s - mean) ** 2 for s in spacings) / len(spacings)
            return (variance ** 0.5) / mean <= _LEGEND_SPACING_CV_MAX

        # Real deployments hug the cable route; a legend column sits far
        # from it.  A row/column whose members lie on a cable polyline is
        # poles strung along a straight span — geometrically identical to a
        # specimen column — and must be kept.
        cable_paths = [
            feature.native_points
            for feature in features
            if feature.feature_class == "CABLE" and feature.native_points
        ]

        def _on_cable(point: tuple[float, float]) -> bool:
            for path in cable_paths:
                for left, right in zip(path, path[1:]):
                    dx, dy = right[0] - left[0], right[1] - left[1]
                    seg_sq = dx * dx + dy * dy
                    if seg_sq <= 0.0:
                        continue
                    t = max(
                        0.0,
                        min(1.0, ((point[0] - left[0]) * dx
                                  + (point[1] - left[1]) * dy) / seg_sq),
                    )
                    px, py = left[0] + t * dx, left[1] + t * dy
                    if math.hypot(point[0] - px, point[1] - py) <= _LEGEND_COLUMN_CABLE_RADIUS_M:
                        return True
            return False

        def _is_specimen_column(column: Sequence[Feature]) -> bool:
            if len(column) < _LEGEND_COLUMN_MIN_MEMBERS:
                return False
            if any(
                _on_cable(feature.native_points[0]) for feature in column
            ):
                return False
            return _regular_spacing([
                f.native_points[0][1 - _axis] for f in column
            ])

        for _axis in (0, 1):
            ordered = sorted(
                asset_inserts,
                key=lambda f: f.native_points[0][_axis],
            )
            group: list[Feature] = []
            for feature in ordered:
                if group and abs(
                    feature.native_points[0][_axis]
                    - group[-1].native_points[0][_axis]
                ) > (_tol_x if _axis == 0 else _tol_y):
                    if _is_specimen_column(group):
                        legend_core_keys.update(f.feature_key for f in group)
                    group = []
                group.append(feature)
            if _is_specimen_column(group):
                legend_core_keys.update(f.feature_key for f in group)

    for feature in features:
        if feature.feature_class == "CABLE":
            spec = cable_spec_name(feature.source_layer, feature.style)
            if spec:
                feature.attributes["delivery_style_render_key"] = f"CABLE:{spec}"
                feature.field_provenance["delivery_style_render_key"] = (
                    "DWG_DIRECT:cable-colour-legend"
                )
        elif feature.feature_class == "PTECH":
            type_name = ptech_type_name(feature)
            if type_name:
                feature.attributes["delivery_style_render_key"] = f"PTECH:{type_name}"
                feature.field_provenance["delivery_style_render_key"] = (
                    "DWG_DIRECT:pole-legend-category"
                )

    for feature in features:
        device_number = feature.attributes.get("DEVICE_NUMBER")
        if device_number:
            text_label = feature.display_label.strip()
            feature.display_label = (
                f"{text_label} · {device_number}" if text_label else str(device_number)
            )
            feature.label_provenance = _append_label_provenance(
                feature.label_provenance,
                feature.field_provenance.get("DEVICE_NUMBER", "UNAVAILABLE:device-number-source"),
            )

    retained: list[Feature] = []
    legend_source_keys: set[str] = set()
    for feature in features:
        if feature.feature_key in legend_core_keys:
            source = entity_by_key.get(feature.source_entity_key)
            legend_source_keys.add(feature.source_entity_key)
            coverage_records.append(_coverage_record(
                source, "legend_core_sample", feature.feature_class,
            ))
            continue
        if feature.feature_key in callout_noise_keys:
            source = entity_by_key.get(feature.source_entity_key)
            coverage_records.append(_coverage_record(
                source, "annotation_callout_noise", feature.feature_class,
            ))
            continue
        retained.append(feature)
    if len(retained) != len(features):
        features = retained
        feature_by_key = {feature.feature_key: feature for feature in features}
        retained_feature_keys = set(feature_by_key)
        for family, _, _, target_layer_pattern in compiled_families:
            family_diagnostics = annotation_assignments_by_family.get(
                family.family_id
            )
            if family_diagnostics is None:
                continue
            family_diagnostics["target_assets"] = sum(
                target.feature_key in retained_feature_keys
                and (
                    bool(target_layer_pattern.fullmatch(target.source_layer.strip()))
                    or target.feature_key in derived_target_keys
                )
                for target in by_class[family.target_class]
            )
    if legend_source_keys:
        # A deterministic legend specimen is evidence-only and never reaches
        # delivery.  Do not also report the same specimen as a deployed asset
        # that lacks a reviewed label; that stale record makes a strict
        # coverage gate fail for an object the classifier intentionally
        # removed.
        coverage_records = [
            record
            for record in coverage_records
            if not (
                record.get("reason") == "missing_reviewed_label"
                and record.get("source_entity_key") in legend_source_keys
            )
        ]
    # INFRASTRUCTURE is the continuous single-colour total set of every CABLE
    # coloured line. It repeats the classified cable geometry, including any
    # explicit endpoint bridge and its source lineage, with a legend
    # colour that is deliberately distinct from all cable-type colours.
    # Generation runs after the final noise filtering so the count always
    # equals the delivered CABLE count.
    for cable_feature in [
        feature for feature in features if feature.feature_class == "CABLE"
    ]:
        infrastructure_style = replace(
            cable_feature.style,
            aci_color=8,
            true_color="#808080",
            linetype="Continuous",
        )
        infrastructure_feature = Feature(
            feature_key=hashlib.sha256(
                f"INFRASTRUCTURE|{cable_feature.feature_key}".encode()
            ).hexdigest(),
            feature_class="INFRASTRUCTURE",
            geometry_kind="LineString",
            native_points=deepcopy(cable_feature.native_points),
            source_entity_key=cable_feature.source_entity_key,
            source_handle=cable_feature.source_handle,
            source_layer=cable_feature.source_layer,
            geometry_role=cable_feature.geometry_role,
            style=infrastructure_style,
            attributes={"CODE": f"INFRA-CAD-{cable_feature.source_handle}"},
            display_label="",
            label_provenance="UNAVAILABLE",
            field_provenance={
                "CODE": "DWG_DERIVED:cable-collection-member",
            },
            lineage=deepcopy(cable_feature.lineage),
        )
        features.append(infrastructure_feature)

    # ZNRO rules:
    # - SF projects never deliver ZNRO.
    # - A large red outer boundary ring in the main drawing is authoritative
    #   and may exist even when the drawing has no ZPM (Tinggar's two rings).
    # - Otherwise, when ZPM polygons exist, conservative ZNRO polygons preserve
    #   isolated parcels and fill narrow gaps within nearby parcel groups;
    #   independent groups remain separate.
    delivered_zpm = [
        feature for feature in features if feature.feature_class == "ZPM"
    ]
    project_is_sf = str(project_slug or project_id or "").lower().endswith("_sf")
    boundary_repairs = []
    if not project_is_sf:
        from shapely.geometry import LineString, Point, Polygon as ShapelyPolygon
        from shapely.validation import explain_validity

        from .znro_shape import conservative_znro_polygons

        boundary_entities = [
            entity for entity in model_entities
            if entity.dwg_type.upper() in {"LWPOLYLINE", "POLYLINE", "POLYLINE_2D"}
            and str(getattr(entity, "cad_role", "")).lower() == "model"
            and len(entity.points) >= 4
            and (
                "BOUNDARY" in entity.layer.upper()
                or "BATAS" in entity.layer.upper()
            )
            and _is_red_boundary(entity)
            and math.dist(entity.points[0], entity.points[-1]) <= 0.05
        ]
        boundary_rings: list[dict[str, Any]] = []
        for entity in sorted(
            boundary_entities, key=lambda item: (item.layer, item.handle),
        ):
            raw_polygon = ShapelyPolygon(entity.points)
            # Preserve valid source vertices and their ordering. An invalid
            # boundary may yield a usable candidate, but buffer(0) can remove
            # large spikes: that is a lossy derivation requiring review.
            repaired = not raw_polygon.is_valid
            polygon = raw_polygon.buffer(0) if repaired else raw_polygon
            if polygon.geom_type != "Polygon" or not polygon.is_valid:
                continue
            if polygon.area <= 0.0:
                continue
            if any(
                polygon.equals(existing["polygon"])
                for existing in boundary_rings
            ):
                continue
            points = list(polygon.exterior.coords) if repaired else list(entity.points)
            repair_lineage = None
            if repaired:
                delivered_polygon = ShapelyPolygon(points)
                repair_lineage = {
                    "operation": "repair_boundary_polygon",
                    "source_entity_key": entity.entity_key,
                    "method": "shapely.Polygon.buffer(0).exterior",
                    "geometry_changed": True,
                    "lossy": True,
                    "review_status": "required",
                    "source_validity": explain_validity(raw_polygon),
                    "result_is_valid": delivered_polygon.is_valid,
                    "max_displacement_m": float(
                        LineString(entity.points).hausdorff_distance(LineString(points))
                    ),
                    "displacement_metric": "boundary_hausdorff_native_m",
                    "source_vertex_count": len(entity.points),
                    "result_vertex_count": len(points),
                    "source_shoelace_area_m2": float(raw_polygon.area),
                    "result_area_m2": float(delivered_polygon.area),
                    "area_delta_m2": float(delivered_polygon.area - raw_polygon.area),
                    "discarded_interior_ring_count": len(polygon.interiors),
                    "discarded_interior_area_m2": float(delivered_polygon.area - polygon.area),
                }
            if repaired and not apply_geometry_repairs:
                if geometry_candidates is not None:
                    geometry_candidates.append({
                        **repair_lineage, "source_points": list(entity.points), "candidate_points": points,
                        "applied": False, "delivery_disposition": "invalid_source_boundary_withheld",
                    })
                unresolved.append({"kind": "source_boundary_repair", "entity_key": entity.entity_key,
                                   "status": "withheld_pending_review", "applied": False})
                continue
            boundary_rings.append({
                "entity": entity,
                "polygon": polygon,
                "points": points,
                "area": float(polygon.area),
                "repair_lineage": repair_lineage,
            })

        delivered_cable_points = [
            point
            for feature in features
            if feature.feature_class == "CABLE"
            for point in feature.native_points
        ]
        boundary_features: list[Feature] = []
        if delivered_zpm:
            zpm_polygons = [
                list(feature.native_points)
                for feature in delivered_zpm
                if len(feature.native_points) >= 3
            ]
            zpm_points = [point for polygon in zpm_polygons for point in polygon]
            enclosing_rings = [
                ring for ring in boundary_rings
                if all(
                    ring["polygon"].distance(Point(point)) <= _ZNRO_BOUNDARY_TOLERANCE_M
                    for point in zpm_points
                )
            ]
            if enclosing_rings:
                boundary_features = _znro_boundary_features(enclosing_rings)
            elif zpm_polygons:
                conservative_polygons = conservative_znro_polygons(
                    zpm_polygons,
                    gap_bridge_m=_ZNRO_CONSERVATIVE_GAP_BRIDGE_M,
                )
                boundary_features = [
                    _znro_synthetic_feature(
                        polygon.exterior.coords,
                        source_layer="ZPM-CONSERVATIVE-UNION",
                        operation="conservative_znro_component",
                        source_handle=f"ZPM-COMPONENT-{index + 1}",
                    )
                    for index, polygon in enumerate(conservative_polygons)
                ]
        elif boundary_rings and delivered_cable_points:
            largest_area = max(ring["area"] for ring in boundary_rings)
            large_outer_rings = [
                ring for ring in boundary_rings
                if ring["area"] >= max(100.0, largest_area * 0.02)
                and sum(
                    1 for point in delivered_cable_points
                    if ring["polygon"].distance(Point(point)) <= _ZNRO_BOUNDARY_TOLERANCE_M
                ) >= 3
            ]
            boundary_features = _znro_boundary_features(large_outer_rings)
        features.extend(boundary_features)
        for feature in boundary_features:
            for item in feature.lineage:
                if item.get("operation") == "repair_boundary_polygon":
                    repair = {
                        "kind": "source_boundary_repair",
                        "status": "review_required",
                        "entity_key": feature.source_entity_key,
                        "feature_key": feature.feature_key,
                        "source_handle": feature.source_handle,
                        "source_layer": feature.source_layer,
                        **deepcopy(item),
                    }
                    boundary_repairs.append(repair)
                    unresolved.append(deepcopy(repair))

    device_number_reviews = [
        {
            **deepcopy(item), "kind": "device_number_label_candidate",
            "status": "review_required", "feature_key": feature.feature_key,
            "entity_key": feature.source_entity_key,
        }
        for feature in features for item in feature.lineage
        if item.get("operation") == "select_device_number_label"
    ]
    unresolved.extend(deepcopy(device_number_reviews))
    selected_policy = normalize_observability_policy(
        coverage_policy,
        default="warn" if bool(getattr(registry, "is_reviewed", False)) else "fail",
    )
    coverage = build_coverage_report(
        coverage_records,
        schema_version=SEMANTIC_COVERAGE_SCHEMA_VERSION,
        policy=selected_policy,
        allowlist=coverage_allowlist,
        inspected_count=len(model_entities),
    )

    diagnostics = {
        "candidate_counts": {
            feature_class: len(items) for feature_class, items in sorted(by_class.items())
        },
        "mapped_entity_keys": sorted(mapped_entities),
        "annotation_assignments": {
            target_class: dict(values)
            for target_class, values in sorted(annotation_assignments.items())
        },
        "annotation_assignments_by_family": annotation_assignments_by_family,
        "unrecognized_suspected_asset_ids": sum(
            item["status"] == "unrecognized_asset_id"
            for item in annotation_discovery_failures
        ),
        "annotation_discovery_failure_counts": dict(sorted(
            (status, sum(item["status"] == status for item in annotation_discovery_failures))
            for status in {item["status"] for item in annotation_discovery_failures}
        )),
        "annotation_candidates": annotation_candidates,
        "emr_features": sum(
            1 for feature in features if feature.feature_class == "EMR"
        ),
        "ptech_cable_endpoint_bridges": bridged_endpoints,
        "source_boundary_repairs": boundary_repairs,
        "device_number_label_reviews": device_number_reviews,
        "coverage": coverage,
    }
    if not coverage["conversion_allowed"]:
        raise CoverageGateError("semantics", coverage)
    return features, relations, unresolved, diagnostics
