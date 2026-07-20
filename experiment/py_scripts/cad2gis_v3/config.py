"""Versioned, source-bound project profile and mapping registry loaders.

The runtime consumes one normalized contract regardless of whether the input
is the historical APD v4/v1 pair or the generic project schemas.  APD key
translation deliberately lives here, at the compatibility boundary, so the
conversion pipeline never needs to know project-specific family or census
names.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


PROJECT_PROFILE_SCHEMA_VERSION = "cad2gis-project-profile-v1"
MAPPING_REGISTRY_SCHEMA_VERSION = "cad2gis-mapping-registry-v2"


def _canonical_json_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(payload.encode("ascii")).hexdigest()


def _sha256(value: Any, name: str) -> str:
    result = str(value).strip().lower()
    if re.fullmatch(r"[0-9a-f]{64}", result) is None:
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    return result


def _nonnegative_int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


def _count_mapping(value: Any, name: str, *, allow_empty: bool = True) -> dict[str, int]:
    if not isinstance(value, dict) or (not allow_empty and not value):
        suffix = " non-empty" if not allow_empty else ""
        raise ValueError(f"{name} must be a{suffix} JSON object")
    result: dict[str, int] = {}
    for raw_key, raw_count in value.items():
        key = str(raw_key).strip()
        if not key:
            raise ValueError(f"{name} contains an empty key")
        result[key] = _nonnegative_int(raw_count, f"{name}.{key}")
    return result


@dataclass(frozen=True)
class ReviewRecord:
    """Explicit human-review state; draft and unreviewed are never runnable."""

    status: str
    reviewed_by: str = ""
    reviewed_at: str = ""
    provenance: str = ""

    @property
    def is_reviewed(self) -> bool:
        return self.status == "reviewed"

    @classmethod
    def from_mapping(cls, value: Any, name: str = "review") -> "ReviewRecord":
        if not isinstance(value, dict):
            raise ValueError(f"{name} must be an object")
        expected = {"status", "reviewed_by", "reviewed_at", "provenance"}
        missing, unknown = {"status"} - set(value), set(value) - expected
        if missing or unknown:
            raise ValueError(
                f"Invalid {name} keys; missing={sorted(missing)}, "
                f"unknown={sorted(unknown)}"
            )
        status = str(value["status"]).strip().casefold()
        if status not in {"draft", "unreviewed", "reviewed"}:
            raise ValueError(f"{name}.status must be draft, unreviewed, or reviewed")
        record = cls(
            status=status,
            reviewed_by=str(value.get("reviewed_by", "")).strip(),
            reviewed_at=str(value.get("reviewed_at", "")).strip(),
            provenance=str(value.get("provenance", "")).strip(),
        )
        if record.is_reviewed and not all(
            (record.reviewed_by, record.reviewed_at, record.provenance)
        ):
            raise ValueError(
                f"{name} reviewed state requires reviewed_by, reviewed_at, and provenance"
            )
        return record

    @classmethod
    def legacy_reviewed(cls, provenance: str) -> "ReviewRecord":
        return cls(
            status="reviewed",
            reviewed_by="legacy-reviewed-contract",
            reviewed_at="legacy-schema",
            provenance=provenance,
        )


@dataclass(frozen=True)
class DiagnosticGate:
    """One declarative assertion against a nested diagnostics path."""

    path: tuple[str, ...]
    operator: str
    value: Any

    @property
    def dotted_path(self) -> str:
        return ".".join(self.path)


def _diagnostic_gates(value: Any, name: str) -> tuple[DiagnosticGate, ...]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object keyed by diagnostic path")
    result = []
    for raw_path, raw_expectation in value.items():
        dotted_path = str(raw_path).strip()
        parts = tuple(part.strip() for part in dotted_path.split("."))
        if not dotted_path or any(not part for part in parts):
            raise ValueError(f"{name} contains an invalid diagnostic path: {raw_path!r}")
        if isinstance(raw_expectation, dict) and set(raw_expectation) in (
            {"operator", "value"}, {"op", "value"},
        ):
            operator = str(
                raw_expectation.get("operator", raw_expectation.get("op"))
            ).strip().casefold()
            expected = raw_expectation["value"]
        else:
            operator = "eq"
            expected = raw_expectation
        aliases = {"==": "eq", "equals": "eq", "<=": "le", ">=": "ge"}
        operator = aliases.get(operator, operator)
        if operator not in {"eq", "le", "ge"}:
            raise ValueError(
                f"{name}.{dotted_path} operator must be eq, le, or ge"
            )
        if operator in {"le", "ge"}:
            if isinstance(expected, bool) or not isinstance(expected, (int, float)):
                raise ValueError(
                    f"{name}.{dotted_path} ordered comparison requires a finite number"
                )
            expected = float(expected)
            if not math.isfinite(expected):
                raise ValueError(
                    f"{name}.{dotted_path} ordered comparison requires a finite number"
                )
        # Reject values that cannot participate in canonical JSON/hash binding.
        _canonical_json_sha256(expected)
        result.append(DiagnosticGate(parts, operator, expected))
    return tuple(sorted(result, key=lambda gate: gate.path))


@dataclass(frozen=True)
class AnnotationExpectation:
    family_id: str
    metrics: dict[str, int]


def _annotation_expectations(value: Any) -> tuple[AnnotationExpectation, ...]:
    if not isinstance(value, dict):
        raise ValueError("expectations.annotation_families must be an object")
    result = []
    for raw_family_id, raw_metrics in value.items():
        family_id = str(raw_family_id).strip()
        if re.fullmatch(r"[a-z][a-z0-9_]*", family_id) is None:
            raise ValueError(f"Invalid annotation expectation family_id: {family_id!r}")
        result.append(AnnotationExpectation(
            family_id=family_id,
            metrics=_count_mapping(
                raw_metrics,
                f"expectations.annotation_families.{family_id}",
                allow_empty=False,
            ),
        ))
    return tuple(sorted(result, key=lambda item: item.family_id))


@dataclass(frozen=True)
class ProjectExpectations:
    source_inventory: dict[str, int]
    feature_counts: dict[str, int]
    annotation_families: tuple[AnnotationExpectation, ...]
    source_geometry_gates: tuple[DiagnosticGate, ...]
    topology_gates: tuple[DiagnosticGate, ...]
    segment_gates: tuple[DiagnosticGate, ...]
    delivery_counts: dict[str, int]

    @classmethod
    def from_mapping(
        cls, value: Any, *, allow_incomplete: bool = False,
    ) -> "ProjectExpectations":
        if not isinstance(value, dict):
            raise ValueError("expectations must be an object")
        expected = {
            "source_inventory", "feature_counts", "annotation_families",
            "source_geometry_gates", "topology_gates", "segment_gates",
            "delivery_counts",
        }
        missing, unknown = expected - set(value), set(value) - expected
        if missing or unknown:
            raise ValueError(
                "Invalid expectations keys; "
                f"missing={sorted(missing)}, unknown={sorted(unknown)}"
            )
        return cls(
            source_inventory=_count_mapping(
                value["source_inventory"], "expectations.source_inventory",
                allow_empty=allow_incomplete,
            ),
            feature_counts=_count_mapping(
                value["feature_counts"], "expectations.feature_counts",
                allow_empty=allow_incomplete,
            ),
            annotation_families=_annotation_expectations(
                value["annotation_families"]
            ),
            source_geometry_gates=_diagnostic_gates(
                value["source_geometry_gates"],
                "expectations.source_geometry_gates",
            ),
            topology_gates=_diagnostic_gates(
                value["topology_gates"], "expectations.topology_gates",
            ),
            segment_gates=_diagnostic_gates(
                value["segment_gates"], "expectations.segment_gates",
            ),
            delivery_counts=_count_mapping(
                value["delivery_counts"], "expectations.delivery_counts",
            ),
        )


def _legacy_apd_expectations(census: Mapping[str, int]) -> ProjectExpectations:
    """Normalize the reviewed APD v4 census into generic runtime gates."""

    required = {
        "model_entities", "model_inserts", "model_dimensions", "plan_poles",
        "plan_fat", "plan_fdt", "direct_fat_annotations",
        "direct_new_pole_annotations", "direct_existing_pole_annotations",
        "homepass_labels", "positive_cable_routes",
    }
    missing = required - set(census)
    if missing:
        raise ValueError(
            "Legacy APD source profile lacks required census keys: "
            f"{sorted(missing)}"
        )
    route_occurrences = None
    if {
        "route_segments_with_span_dimension",
        "route_segments_without_span_dimension",
    }.issubset(census):
        route_occurrences = (
            census["route_segments_with_span_dimension"]
            + census["route_segments_without_span_dimension"]
        )
    family_metrics = {
        family_id: {
            "source_annotations": census[count_key],
            "target_assets": census[count_key],
            "assigned": census[count_key],
            "missing": 0,
            "unresolved": 0,
        }
        for family_id, count_key in {
            "fat": "direct_fat_annotations",
            "pole_new": "direct_new_pole_annotations",
            "pole_existing": "direct_existing_pole_annotations",
        }.items()
    }
    source_geometry_values = {}
    if "source_route_curve_facts" in census:
        source_geometry_values["curve_facts_checked"] = census[
            "source_route_curve_facts"
        ]
    topology_paths = {
        "source_route_components": "source_route_components",
        "source_route_graph.unique_nodes": "source_route_nodes",
        "source_route_graph.unique_edges": "source_route_edges",
        "source_route_graph.components": "source_route_components",
        "route_vertex_support.accepted": "route_vertices_exact_support",
        "route_vertex_support.candidate": "route_vertices_near_support_candidates",
        "route_vertex_support.unresolved": "route_vertices_unresolved",
    }
    segment_paths = {
        "span_roles.cable_route_span": "cable_route_span_dimensions",
        "span_roles.sling_wire_span": "sling_wire_span_dimensions",
        "route_segments_with_span_dimension": "route_segments_with_span_dimension",
        "route_segments_without_span_dimension": "route_segments_without_span_dimension",
        "source_route_native_lengths": "source_route_native_lengths",
        "accepted_span_dimensions": "accepted_span_dimensions",
        "candidate_span_dimensions": "candidate_span_dimensions",
        "unique_span_edges": "accepted_unique_span_edges",
        "unique_span_edges_all": "all_unique_span_edges",
    }
    segment_values = {
        diagnostic_path: census[census_key]
        for diagnostic_path, census_key in segment_paths.items()
        if census_key in census
    }
    if route_occurrences is not None:
        segment_values["route_segment_occurrences"] = route_occurrences
    if "span_measurement_max_abs_error_m" not in census:
        segment_values["span_measurement_max_abs_error_m"] = {
            "operator": "le", "value": 1e-6,
        }
    if "source_route_native_lengths" in census:
        segment_values["source_route_native_length_max_abs_delta_m"] = {
            "operator": "le", "value": 1e-6,
        }
    return ProjectExpectations(
        source_inventory={
            key: census[key]
            for key in ("model_entities", "model_inserts", "model_dimensions")
        },
        feature_counts={
            "PTECH": census["plan_poles"],
            "BOITE": census["plan_fat"],
            "SITE": census["plan_fdt"],
            "IMB": census["homepass_labels"],
            "CABLE": census["positive_cable_routes"],
        },
        annotation_families=_annotation_expectations(family_metrics),
        source_geometry_gates=_diagnostic_gates(
            source_geometry_values, "legacy.source_geometry_gates",
        ),
        topology_gates=_diagnostic_gates({
            diagnostic_path: census[census_key]
            for diagnostic_path, census_key in topology_paths.items()
            if census_key in census
        }, "legacy.topology_gates"),
        segment_gates=_diagnostic_gates(
            segment_values, "legacy.segment_gates",
        ),
        delivery_counts=(
            {} if route_occurrences is None
            else {"CABLE_SEGMENT": route_occurrences}
        ),
    )


def _load_json(path: str | Path) -> tuple[Path, dict[str, Any]]:
    resolved = Path(path).resolve()
    value = json.loads(resolved.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object: {resolved}")
    return resolved, value


def _unit_ratio(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number in [0, 1]")
    result = float(value)
    if not math.isfinite(result) or not 0.0 <= result <= 1.0:
        raise ValueError(f"{name} must be a finite number in [0, 1]")
    return result


@dataclass(frozen=True)
class SpatialCoveragePolicy:
    """Source-bound numeric gates for distribution of reviewed GCPs.

    The policy belongs to the drawing profile because coverage is measured
    against the complete model-space drawing extent, not against the GCP set
    itself.  A human review flag alone is therefore insufficient to activate
    delivery.
    """

    min_training_extent_x_ratio: float
    min_training_extent_y_ratio: float
    min_training_hull_area_ratio: float
    max_drawing_vertices_outside_training_bbox_ratio: float
    min_check_baseline_to_drawing_diagonal_ratio: float
    min_check_hull_area_ratio: float | None
    max_drawing_vertices_outside_training_hull_ratio: float | None = None

    @classmethod
    def from_mapping(cls, value: Any) -> "SpatialCoveragePolicy":
        if not isinstance(value, dict):
            raise ValueError("spatial_coverage_policy must be an object")
        legacy = {
            "min_training_extent_x_ratio",
            "min_training_extent_y_ratio",
            "min_training_hull_area_ratio",
            "max_drawing_vertices_outside_training_bbox_ratio",
            "min_check_baseline_to_drawing_diagonal_ratio",
        }
        expected = legacy | {
            "min_check_hull_area_ratio",
            "max_drawing_vertices_outside_training_hull_ratio",
        }
        missing, unknown = legacy - set(value), set(value) - expected
        if missing or unknown:
            raise ValueError(
                "Invalid spatial_coverage_policy keys; "
                f"missing={sorted(missing)}, unknown={sorted(unknown)}"
            )
        values = {
            name: _unit_ratio(value[name], f"spatial_coverage_policy.{name}")
            for name in sorted(legacy)
        }
        values["min_check_hull_area_ratio"] = (
            None
            if "min_check_hull_area_ratio" not in value
            else _unit_ratio(
                value["min_check_hull_area_ratio"],
                "spatial_coverage_policy.min_check_hull_area_ratio",
            )
        )
        values["max_drawing_vertices_outside_training_hull_ratio"] = (
            None
            if "max_drawing_vertices_outside_training_hull_ratio" not in value
            else _unit_ratio(
                value["max_drawing_vertices_outside_training_hull_ratio"],
                "spatial_coverage_policy."
                "max_drawing_vertices_outside_training_hull_ratio",
            )
        )
        return cls(**values)

    def to_dict(self) -> dict[str, float | None]:
        return {
            "min_training_extent_x_ratio": self.min_training_extent_x_ratio,
            "min_training_extent_y_ratio": self.min_training_extent_y_ratio,
            "min_training_hull_area_ratio": self.min_training_hull_area_ratio,
            "max_drawing_vertices_outside_training_bbox_ratio": (
                self.max_drawing_vertices_outside_training_bbox_ratio
            ),
            "min_check_baseline_to_drawing_diagonal_ratio": (
                self.min_check_baseline_to_drawing_diagonal_ratio
            ),
            "min_check_hull_area_ratio": self.min_check_hull_area_ratio,
            "max_drawing_vertices_outside_training_hull_ratio": (
                self.max_drawing_vertices_outside_training_hull_ratio
            ),
        }


@dataclass(frozen=True)
class SourceProfile:
    path: Path
    schema_version: str
    source_sha256: str
    dwg_cgeocs: str | None
    dwg_insunits: int | None
    source_crs: str | None
    target_crs: str | None
    drawing_units: str | None
    spatial_coverage_policy: SpatialCoveragePolicy | None
    expected_census: dict[str, int]
    expectations: ProjectExpectations
    review: ReviewRecord
    source_coordinate_scale_to_m: float | None = None
    source_coordinate_scale_reviewed: bool = False
    local_registration_strategy: str | None = None
    local_registration_reviewed: bool = False
    project_id: str = ""
    source_size_bytes: int | None = None
    inventory_sha256: str = ""

    @property
    def is_legacy(self) -> bool:
        return self.schema_version.startswith("cad2gis-source-profile-")

    @property
    def is_reviewed(self) -> bool:
        return self.review.is_reviewed

    def require_reviewed(self) -> None:
        if not self.is_reviewed:
            raise ValueError(
                f"Project profile is {self.review.status}; conversion requires reviewed state"
            )

    @classmethod
    def load(cls, path: str | Path) -> "SourceProfile":
        resolved, value = _load_json(path)
        schema_version = str(value.get("schema_version", ""))
        if schema_version == PROJECT_PROFILE_SCHEMA_VERSION:
            return cls._load_project_profile(resolved, value)

        legacy_base = {
            "schema_version", "source_sha256", "dwg_cgeocs", "dwg_insunits", "source_crs",
            "target_crs", "drawing_units", "expected_census",
        }
        required = (
            legacy_base
            if schema_version == "cad2gis-source-profile-v1"
            else legacy_base | {"spatial_coverage_policy"}
        )
        unknown = set(value) - required
        missing = required - set(value)
        if missing or unknown:
            raise ValueError(f"Invalid source profile keys; missing={sorted(missing)}, unknown={sorted(unknown)}")
        if schema_version not in {
            "cad2gis-source-profile-v1",
            "cad2gis-source-profile-v2",
            "cad2gis-source-profile-v3",
            "cad2gis-source-profile-v4",
        }:
            raise ValueError(f"Unsupported source profile: {value['schema_version']}")
        if (
            schema_version in {
                "cad2gis-source-profile-v3",
                "cad2gis-source-profile-v4",
            }
            and "min_check_hull_area_ratio" not in value["spatial_coverage_policy"]
        ):
            raise ValueError(
                f"{schema_version} requires min_check_hull_area_ratio"
            )
        if schema_version == "cad2gis-source-profile-v4":
            required_v4_coverage = {
                "max_drawing_vertices_outside_training_hull_ratio",
            }
            missing_v4_coverage = (
                required_v4_coverage - set(value["spatial_coverage_policy"])
            )
            if missing_v4_coverage:
                raise ValueError(
                    "cad2gis-source-profile-v4 requires spatial coverage keys: "
                    f"{sorted(missing_v4_coverage)}"
                )
        expected_census = _count_mapping(
            value["expected_census"], "expected_census", allow_empty=False,
        )
        return cls(
            path=resolved,
            schema_version=schema_version,
            source_sha256=_sha256(value["source_sha256"], "source_sha256"),
            dwg_cgeocs=str(value["dwg_cgeocs"]),
            dwg_insunits=int(value["dwg_insunits"]),
            source_crs=str(value["source_crs"]),
            target_crs=str(value["target_crs"]),
            drawing_units=str(value["drawing_units"]),
            source_coordinate_scale_to_m=None,
            source_coordinate_scale_reviewed=False,
            local_registration_strategy=None,
            local_registration_reviewed=False,
            spatial_coverage_policy=(
                None
                if schema_version == "cad2gis-source-profile-v1"
                else SpatialCoveragePolicy.from_mapping(
                    value["spatial_coverage_policy"]
                )
            ),
            expected_census=expected_census,
            expectations=_legacy_apd_expectations(expected_census),
            review=ReviewRecord.legacy_reviewed(
                f"legacy reviewed source contract {schema_version}"
            ),
        )

    @classmethod
    def _load_project_profile(
        cls, resolved: Path, value: dict[str, Any],
    ) -> "SourceProfile":
        expected = {
            "schema_version", "project_id", "review", "source_binding",
            "drawing", "crs", "spatial_coverage_policy", "expectations",
        }
        missing, unknown = expected - set(value), set(value) - expected
        if missing or unknown:
            raise ValueError(
                "Invalid project profile keys; "
                f"missing={sorted(missing)}, unknown={sorted(unknown)}"
            )
        project_id = str(value["project_id"]).strip()
        if not project_id:
            raise ValueError("project_id must be non-empty")
        review = ReviewRecord.from_mapping(value["review"], "project profile review")

        binding = value["source_binding"]
        binding_keys = {"source_sha256", "source_size_bytes", "inventory_sha256"}
        if not isinstance(binding, dict) or set(binding) != binding_keys:
            actual = set(binding) if isinstance(binding, dict) else set()
            raise ValueError(
                "Invalid source_binding keys; "
                f"missing={sorted(binding_keys - actual)}, "
                f"unknown={sorted(actual - binding_keys)}"
            )
        source_sha256 = _sha256(
            binding["source_sha256"], "source_binding.source_sha256"
        )
        inventory_sha256 = _sha256(
            binding["inventory_sha256"], "source_binding.inventory_sha256"
        )
        source_size_bytes = _nonnegative_int(
            binding["source_size_bytes"], "source_binding.source_size_bytes"
        )

        drawing = value["drawing"]
        drawing_keys = {"dwg_cgeocs", "dwg_insunits", "drawing_units"}
        drawing_extended_keys = {
            "source_coordinate_scale_to_m", "source_coordinate_scale_reviewed",
        }
        if (
            not isinstance(drawing, dict)
            or not drawing_keys.issubset(drawing)
            or set(drawing) - drawing_keys - drawing_extended_keys
        ):
            actual = set(drawing) if isinstance(drawing, dict) else set()
            raise ValueError(
                "Invalid drawing keys; "
                f"missing={sorted(drawing_keys - actual)}, "
                f"unknown={sorted(actual - drawing_keys - drawing_extended_keys)}"
            )
        crs = value["crs"]
        crs_keys = {"source_crs", "target_crs"}
        crs_extended_keys = {
            "local_registration_strategy", "local_registration_reviewed",
        }
        if (
            not isinstance(crs, dict)
            or not crs_keys.issubset(crs)
            or set(crs) - crs_keys - crs_extended_keys
        ):
            actual = set(crs) if isinstance(crs, dict) else set()
            raise ValueError(
                "Invalid crs keys; "
                f"missing={sorted(crs_keys - actual)}, "
                f"unknown={sorted(actual - crs_keys - crs_extended_keys)}"
            )

        def optional_string(raw: Any, name: str) -> str | None:
            if raw is None:
                return None
            if not isinstance(raw, str) or not raw.strip():
                raise ValueError(f"{name} must be null or a non-empty string")
            return raw.strip()

        dwg_cgeocs = optional_string(drawing["dwg_cgeocs"], "drawing.dwg_cgeocs")
        drawing_units = optional_string(
            drawing["drawing_units"], "drawing.drawing_units"
        )
        raw_insunits = drawing["dwg_insunits"]
        if raw_insunits is None:
            dwg_insunits = None
        elif isinstance(raw_insunits, bool) or not isinstance(raw_insunits, int):
            raise ValueError("drawing.dwg_insunits must be null or an integer")
        else:
            dwg_insunits = raw_insunits
        source_crs = optional_string(crs["source_crs"], "crs.source_crs")
        target_crs = optional_string(crs["target_crs"], "crs.target_crs")
        raw_scale = drawing.get("source_coordinate_scale_to_m")
        if raw_scale is None:
            source_coordinate_scale_to_m = None
        elif isinstance(raw_scale, bool) or not isinstance(raw_scale, (int, float)):
            raise ValueError(
                "drawing.source_coordinate_scale_to_m must be null or a finite positive number"
            )
        else:
            source_coordinate_scale_to_m = float(raw_scale)
            if (
                not math.isfinite(source_coordinate_scale_to_m)
                or source_coordinate_scale_to_m <= 0.0
            ):
                raise ValueError(
                    "drawing.source_coordinate_scale_to_m must be null or a finite positive number"
                )
        source_coordinate_scale_reviewed = drawing.get(
            "source_coordinate_scale_reviewed", False,
        )
        if type(source_coordinate_scale_reviewed) is not bool:
            raise ValueError(
                "drawing.source_coordinate_scale_reviewed must be boolean"
            )
        local_registration_strategy = optional_string(
            crs.get("local_registration_strategy"),
            "crs.local_registration_strategy",
        )
        local_registration_reviewed = crs.get(
            "local_registration_reviewed", False,
        )
        if type(local_registration_reviewed) is not bool:
            raise ValueError("crs.local_registration_reviewed must be boolean")
        raw_spatial_policy = value["spatial_coverage_policy"]
        spatial_policy = (
            None if raw_spatial_policy is None
            else SpatialCoveragePolicy.from_mapping(raw_spatial_policy)
        )
        expectations = ProjectExpectations.from_mapping(
            value["expectations"], allow_incomplete=not review.is_reviewed,
        )
        return cls(
            path=resolved,
            schema_version=PROJECT_PROFILE_SCHEMA_VERSION,
            source_sha256=source_sha256,
            dwg_cgeocs=dwg_cgeocs,
            dwg_insunits=dwg_insunits,
            source_crs=source_crs,
            target_crs=target_crs,
            drawing_units=drawing_units,
            source_coordinate_scale_to_m=source_coordinate_scale_to_m,
            source_coordinate_scale_reviewed=source_coordinate_scale_reviewed,
            local_registration_strategy=local_registration_strategy,
            local_registration_reviewed=local_registration_reviewed,
            spatial_coverage_policy=spatial_policy,
            expected_census=dict(expectations.source_inventory),
            expectations=expectations,
            review=review,
            project_id=project_id,
            source_size_bytes=source_size_bytes,
            inventory_sha256=inventory_sha256,
        )

    def validate_source(self, source: str | Path) -> str:
        resolved = Path(source).resolve()
        content = resolved.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        if digest != self.source_sha256:
            raise ValueError(f"Source hash mismatch: expected {self.source_sha256}, got {digest}")
        if self.source_size_bytes is not None and len(content) != self.source_size_bytes:
            raise ValueError(
                "Source size mismatch: expected "
                f"{self.source_size_bytes}, got {len(content)}"
            )
        return digest


@dataclass(frozen=True)
class AnnotationFamily:
    """A source-reviewed, mutually isolated annotation assignment domain."""

    family_id: str
    target_class: str
    text_pattern: str
    source_layer_pattern: str
    target_layer_pattern: str
    require_same_layer: bool
    max_distance_native_m: float
    rule_id: str
    provenance: str


@dataclass(frozen=True)
class MappingRegistry:
    path: Path
    schema_version: str
    source_sha256: str
    block_families: dict[str, tuple[str, ...]]
    layers: dict[str, tuple[str, ...]]
    positive_route_layer_regex: str
    field_rules: dict[str, dict[str, dict[str, Any]]]
    display_label_rules: dict[str, dict[str, Any]]
    annotation_families: tuple[AnnotationFamily, ...]
    decision_rules: dict[str, dict[str, str]]
    labels: dict[str, str]
    thresholds: dict[str, float]
    policy: dict[str, bool]
    review: ReviewRecord
    semantic_coverage_policy: str = "warn"
    semantic_coverage_allowlist: tuple[Any, ...] = ()
    style_coverage_policy: str = "warn"
    style_coverage_allowlist: tuple[Any, ...] = ()
    project_id: str = ""
    inventory_sha256: str = ""

    @property
    def is_legacy(self) -> bool:
        return self.schema_version == "cad2gis-mapping-registry-v1"

    @property
    def is_reviewed(self) -> bool:
        return self.review.is_reviewed

    def require_reviewed(self) -> None:
        if not self.is_reviewed:
            raise ValueError(
                f"Mapping registry is {self.review.status}; conversion requires reviewed state"
            )

    @classmethod
    def load(
        cls,
        path: str | Path,
        source_sha256: str,
        *,
        require_reviewed: bool = True,
    ) -> "MappingRegistry":
        resolved, value = _load_json(path)
        schema_version = str(value.get("schema_version", ""))
        common = {
            "schema_version", "block_families", "layers",
            "positive_route_layer_regex", "field_rules", "display_label_rules",
            "annotation_families", "decision_rules", "labels",
            "thresholds_native_m", "policy",
        }
        if schema_version == "cad2gis-mapping-registry-v1":
            required = common | {"source_sha256"}
            allowed = required | {"coverage"}
            review = ReviewRecord.legacy_reviewed(
                "legacy reviewed mapping registry cad2gis-mapping-registry-v1"
            )
            project_id = ""
            inventory_sha256 = ""
            raw_bound_hash = value.get("source_sha256")
            raw_coverage = value.get("coverage", {
                "semantics": {"policy": "warn", "allowlist": []},
                "styles": {"policy": "warn", "allowlist": []},
            })
        elif schema_version == MAPPING_REGISTRY_SCHEMA_VERSION:
            required = common | {"project_id", "review", "source_binding"}
            allowed = required | {"coverage"}
            review = ReviewRecord.from_mapping(
                value.get("review"), "mapping registry review"
            )
            project_id = str(value.get("project_id", "")).strip()
            if not project_id:
                raise ValueError("mapping registry project_id must be non-empty")
            binding = value.get("source_binding")
            binding_keys = {"source_sha256", "inventory_sha256"}
            if not isinstance(binding, dict) or set(binding) != binding_keys:
                actual = set(binding) if isinstance(binding, dict) else set()
                raise ValueError(
                    "Invalid mapping registry source_binding keys; "
                    f"missing={sorted(binding_keys - actual)}, "
                    f"unknown={sorted(actual - binding_keys)}"
                )
            raw_bound_hash = binding["source_sha256"]
            inventory_sha256 = _sha256(
                binding["inventory_sha256"],
                "mapping registry source_binding.inventory_sha256",
            )
            raw_coverage = value.get("coverage", {
                "semantics": {"policy": "fail", "allowlist": []},
                "styles": {"policy": "fail", "allowlist": []},
            })
        else:
            raise ValueError(f"Unsupported mapping registry: {schema_version}")
        missing, unknown = required - set(value), set(value) - allowed
        if missing or unknown:
            raise ValueError(f"Invalid mapping registry keys; missing={sorted(missing)}, unknown={sorted(unknown)}")
        bound_hash = _sha256(raw_bound_hash, "mapping registry source_sha256")
        if bound_hash != source_sha256.lower():
            raise ValueError("Mapping registry is stale or bound to a different DWG")
        mapping_fields = (
            "block_families", "layers", "field_rules", "display_label_rules",
            "decision_rules", "labels", "thresholds_native_m", "policy",
        )
        for field_name in mapping_fields:
            if not isinstance(value[field_name], dict):
                raise ValueError(f"{field_name} must be a JSON object")
        if not isinstance(value["positive_route_layer_regex"], str):
            raise ValueError("positive_route_layer_regex must be a string")
        try:
            re.compile(value["positive_route_layer_regex"])
        except re.error as exc:
            raise ValueError(f"Invalid positive_route_layer_regex: {exc}") from exc
        for name in ("block_families", "layers"):
            for key, members in value[name].items():
                if not str(key).strip() or not isinstance(members, list):
                    raise ValueError(f"{name}.{key} must be an array")
                if any(not isinstance(member, str) or not member.strip() for member in members):
                    raise ValueError(f"{name}.{key} contains an invalid name")
        for feature_class, rules in value["field_rules"].items():
            if not isinstance(rules, dict):
                raise ValueError(f"field_rules.{feature_class} must be an object")
            if any(not isinstance(rule, dict) for rule in rules.values()):
                raise ValueError(f"field_rules.{feature_class} rules must be objects")
        if any(not isinstance(rule, dict) for rule in value["display_label_rules"].values()):
            raise ValueError("display_label_rules values must be objects")
        all_rules = [
            rule
            for feature_rules in value["field_rules"].values()
            for rule in feature_rules.values()
        ] + list(value["display_label_rules"].values())
        if any(not rule.get("rule_id") or not rule.get("kind") or not rule.get("provenance") for rule in all_rules):
            raise ValueError("Every reviewed field/display rule requires rule_id, kind, and provenance")
        rule_ids = [str(rule["rule_id"]) for rule in all_rules]
        if len(rule_ids) != len(set(rule_ids)):
            raise ValueError("Reviewed mapping rule_id values must be unique")

        raw_annotation_families = value["annotation_families"]
        if not isinstance(raw_annotation_families, list) or (
            schema_version == "cad2gis-mapping-registry-v1"
            and not raw_annotation_families
        ):
            requirement = "a non-empty" if schema_version.endswith("-v1") else "a"
            raise ValueError(f"annotation_families must be {requirement} JSON array")
        annotation_family_keys = {
            "family_id", "target_class", "text_pattern", "source_layer_pattern",
            "target_layer_pattern", "require_same_layer", "max_distance_native_m",
            "rule_id", "provenance",
        }
        annotation_families = []
        family_ids = []
        annotation_rule_ids = []
        for index, raw_family in enumerate(raw_annotation_families):
            if not isinstance(raw_family, dict):
                raise ValueError(f"annotation_families[{index}] must be an object")
            missing_family = annotation_family_keys - set(raw_family)
            unknown_family = set(raw_family) - annotation_family_keys
            if missing_family or unknown_family:
                raise ValueError(
                    f"Invalid annotation family keys at index {index}; "
                    f"missing={sorted(missing_family)}, unknown={sorted(unknown_family)}"
                )
            string_fields = (
                "family_id", "target_class", "text_pattern", "source_layer_pattern",
                "target_layer_pattern", "rule_id", "provenance",
            )
            if any(type(raw_family[field]) is not str for field in string_fields):
                raise ValueError(
                    f"annotation_families[{index}] required string fields must be strings"
                )
            normalized = {field: raw_family[field].strip() for field in string_fields}
            if any(not normalized[field] for field in string_fields):
                raise ValueError(f"annotation_families[{index}] contains an empty required string")
            if re.fullmatch(r"[a-z][a-z0-9_]*", normalized["family_id"]) is None:
                raise ValueError(
                    f"Invalid annotation family_id: {normalized['family_id']!r}"
                )
            if normalized["target_class"] not in value["block_families"]:
                raise ValueError(
                    f"Annotation family {normalized['family_id']} targets unknown class "
                    f"{normalized['target_class']}"
                )
            if type(raw_family["require_same_layer"]) is not bool:
                raise ValueError(
                    f"Annotation family {normalized['family_id']} require_same_layer must be boolean"
                )
            threshold = raw_family["max_distance_native_m"]
            if isinstance(threshold, bool) or not isinstance(threshold, (int, float)):
                raise ValueError(
                    f"Annotation family {normalized['family_id']} max_distance_native_m "
                    "must be a finite positive number"
                )
            threshold = float(threshold)
            if not math.isfinite(threshold) or threshold <= 0.0:
                raise ValueError(
                    f"Annotation family {normalized['family_id']} max_distance_native_m "
                    "must be a finite positive number"
                )
            for field in ("text_pattern", "source_layer_pattern", "target_layer_pattern"):
                try:
                    compiled = re.compile(normalized[field])
                except re.error as exc:
                    raise ValueError(
                        f"Invalid {field} for annotation family {normalized['family_id']}: {exc}"
                    ) from exc
                if compiled.fullmatch(""):
                    raise ValueError(
                        f"Annotation family {normalized['family_id']} {field} must not match empty text"
                    )
            if normalized["rule_id"] not in normalized["provenance"]:
                raise ValueError(
                    f"Annotation family {normalized['family_id']} provenance must cite its rule_id"
                )
            family_ids.append(normalized["family_id"])
            annotation_rule_ids.append(normalized["rule_id"])
            annotation_families.append(AnnotationFamily(
                family_id=normalized["family_id"],
                target_class=normalized["target_class"],
                text_pattern=normalized["text_pattern"],
                source_layer_pattern=normalized["source_layer_pattern"],
                target_layer_pattern=normalized["target_layer_pattern"],
                require_same_layer=raw_family["require_same_layer"],
                max_distance_native_m=threshold,
                rule_id=normalized["rule_id"],
                provenance=normalized["provenance"],
            ))
        if len(family_ids) != len({family_id.casefold() for family_id in family_ids}):
            raise ValueError("annotation family_id values must be unique")
        if len(annotation_rule_ids) != len(set(annotation_rule_ids)):
            raise ValueError("annotation family rule_id values must be unique")
        legacy_required_decisions = {
            "annotation_assignment", "span_segment_measurement",
            "fdt_layout_identification", "fat_layout_sequence",
        }
        if schema_version == "cad2gis-mapping-registry-v1" and (
            set(value["decision_rules"]) != legacy_required_decisions
        ):
            raise ValueError(
                f"Invalid decision rules: expected {sorted(legacy_required_decisions)}, "
                f"got {sorted(value['decision_rules'])}"
            )
        decision_rule_ids = []
        for name, rule in value["decision_rules"].items():
            if not rule.get("rule_id") or not rule.get("method") or not rule.get("provenance"):
                raise ValueError(f"Decision rule {name} requires rule_id, method, and provenance")
            decision_rule_ids.append(str(rule["rule_id"]))
        all_rule_id_groups = (rule_ids, decision_rule_ids, annotation_rule_ids)
        flattened_rule_ids = [item for group in all_rule_id_groups for item in group]
        if len(flattened_rule_ids) != len(set(flattened_rule_ids)):
            raise ValueError("All reviewed rule_id values must be globally unique")
        legacy_expected_labels = {
            "site", "suspected_asset_id", "fdt_id_attribute", "fat_sequence_attribute",
        }
        if schema_version == "cad2gis-mapping-registry-v1" and (
            set(value["labels"]) != legacy_expected_labels
        ):
            raise ValueError(
                f"Invalid labels keys: expected {sorted(legacy_expected_labels)}, "
                f"got {sorted(value['labels'])}"
            )
        if any(type(item) is not str for item in value["labels"].values()):
            raise ValueError("All labels values must be strings")
        for key in ("site", "suspected_asset_id"):
            if key not in value["labels"]:
                continue
            try:
                pattern = re.compile(value["labels"][key])
            except re.error as exc:
                raise ValueError(f"Invalid labels.{key} pattern: {exc}") from exc
            if pattern.fullmatch(""):
                raise ValueError(f"labels.{key} must not match empty text")
        for key in ("fdt_id_attribute", "fat_sequence_attribute"):
            if key in value["labels"] and not value["labels"][key].strip():
                raise ValueError(f"labels.{key} must not be empty")
        thresholds: dict[str, float] = {}
        for raw_key, raw_value in value["thresholds_native_m"].items():
            if isinstance(raw_value, bool) or not isinstance(raw_value, (int, float)):
                raise ValueError(f"thresholds_native_m.{raw_key} must be finite and non-negative")
            threshold = float(raw_value)
            if not math.isfinite(threshold) or threshold < 0.0:
                raise ValueError(f"thresholds_native_m.{raw_key} must be finite and non-negative")
            thresholds[str(raw_key)] = threshold
        expected_policy = {
            "source_geometry_immutable", "crossing_is_connection",
            "support_is_optical_node", "force_route_components_connected",
            "generic_line_is_cable", "dimension_is_cable_geometry",
        }
        if set(value["policy"]) != expected_policy:
            raise ValueError(
                f"Invalid policy keys: expected {sorted(expected_policy)}, "
                f"got {sorted(value['policy'])}"
            )
        if any(type(item) is not bool for item in value["policy"].values()):
            raise ValueError("All policy values must be boolean")

        if not isinstance(raw_coverage, dict) or set(raw_coverage) != {"semantics", "styles"}:
            raise ValueError("coverage must contain exactly semantics and styles")

        def coverage_contract(name: str) -> tuple[str, tuple[Any, ...]]:
            contract = raw_coverage[name]
            if not isinstance(contract, dict) or set(contract) != {"policy", "allowlist"}:
                raise ValueError(f"coverage.{name} must contain policy and allowlist")
            coverage_policy = str(contract["policy"]).strip().casefold()
            if coverage_policy not in {"warn", "abstain", "fail"}:
                raise ValueError(
                    f"coverage.{name}.policy must be warn, abstain, or fail"
                )
            allowlist = contract["allowlist"]
            if not isinstance(allowlist, list) or any(
                not isinstance(item, (str, dict)) for item in allowlist
            ):
                raise ValueError(
                    f"coverage.{name}.allowlist must be an array of strings or objects"
                )
            _canonical_json_sha256(allowlist)
            return coverage_policy, tuple(allowlist)

        semantic_policy, semantic_allowlist = coverage_contract("semantics")
        style_policy, style_allowlist = coverage_contract("styles")
        if (
            schema_version == MAPPING_REGISTRY_SCHEMA_VERSION
            and review.is_reviewed
            and "warn" in {semantic_policy, style_policy}
        ):
            raise ValueError(
                "Reviewed project registries must use fail or explicit abstain "
                "coverage policy; warn is diagnostic-only"
            )
        result = cls(
            path=resolved,
            schema_version=schema_version,
            source_sha256=bound_hash,
            block_families={
                str(feature_class): tuple(str(name).upper() for name in names)
                for feature_class, names in value["block_families"].items()
            },
            layers={
                str(kind): tuple(str(name).upper() for name in names)
                for kind, names in value["layers"].items()
            },
            positive_route_layer_regex=str(value["positive_route_layer_regex"]),
            field_rules={
                str(feature_class): {
                    str(field_name): dict(rule)
                    for field_name, rule in rules.items()
                }
                for feature_class, rules in value["field_rules"].items()
            },
            display_label_rules={
                str(feature_class): dict(rule)
                for feature_class, rule in value["display_label_rules"].items()
            },
            annotation_families=tuple(annotation_families),
            decision_rules={
                str(name): {str(key): str(item) for key, item in rule.items()}
                for name, rule in value["decision_rules"].items()
            },
            labels={str(key): str(item) for key, item in value["labels"].items()},
            thresholds=thresholds,
            policy={str(key): item for key, item in value["policy"].items()},
            review=review,
            semantic_coverage_policy=semantic_policy,
            semantic_coverage_allowlist=semantic_allowlist,
            style_coverage_policy=style_policy,
            style_coverage_allowlist=style_allowlist,
            project_id=project_id,
            inventory_sha256=inventory_sha256,
        )
        if require_reviewed:
            result.require_reviewed()
        return result
