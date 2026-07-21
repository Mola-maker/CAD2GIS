"""Direct native-to-delivery CRS transformation with round-trip diagnostics."""

from __future__ import annotations

import hashlib
import json
import math

from osgeo import osr
import pyproj
from pyproj import Geod, Transformer

from ..apd_rules import set_traditional_axis_order
from .units import (
    UnitCrsContract,
    UnitCrsContractError,
    build_unit_crs_contract,
)


def _qgis_rotation(transform_point, native_anchor, cad_radians):
    """Return the target-grid angle for one native CAD direction.

    A CRS operation can rotate a direction even when no residual calibration
    is active.  Consequently both the anchor and a deterministic one-unit
    direction probe must pass through the exact delivery point operation.  A
    malformed/non-finite operation or a collapsed probe is not renderable and
    fails closed rather than silently falling back to the CAD angle.
    """
    try:
        anchor = (float(native_anchor[0]), float(native_anchor[1]))
    except (IndexError, KeyError, TypeError, ValueError, OverflowError) as exc:
        raise ValueError("native rotation anchor must contain two finite coordinates") from exc
    try:
        angle = float(cad_radians)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("CAD rotation must be a finite number") from exc
    if not all(math.isfinite(value) for value in (*anchor, angle)):
        raise ValueError("native rotation anchor and CAD rotation must be finite")

    direction = (math.cos(angle), math.sin(angle))
    probe = (anchor[0] + direction[0], anchor[1] + direction[1])
    try:
        transformed_anchor = transform_point(anchor)
        transformed_probe = transform_point(probe)
        target_anchor = (
            float(transformed_anchor[0]), float(transformed_anchor[1]),
        )
        target_probe = (
            float(transformed_probe[0]), float(transformed_probe[1]),
        )
    except (IndexError, KeyError, TypeError, ValueError, OverflowError) as exc:
        raise ValueError("delivery rotation projection failed") from exc
    if not all(math.isfinite(value) for value in (*target_anchor, *target_probe)):
        raise ValueError("delivery rotation projection produced non-finite coordinates")

    target_dx = target_probe[0] - target_anchor[0]
    target_dy = target_probe[1] - target_anchor[1]
    target_length = math.hypot(target_dx, target_dy)
    coordinate_scale = max(1.0, *(abs(value) for value in (*target_anchor, *target_probe)))
    minimum_length = 8.0 * math.ulp(coordinate_scale)
    if not math.isfinite(target_length) or target_length <= minimum_length:
        raise ValueError("delivery rotation direction probe is degenerate")

    target_ccw = math.degrees(math.atan2(target_dy, target_dx))
    if not math.isfinite(target_ccw):
        raise ValueError("delivery rotation direction is non-finite")
    result = (-target_ccw) % 360.0
    if not math.isfinite(result):
        raise ValueError("delivery rotation result is non-finite")
    return result


class DirectTransformer:
    def __init__(
        self,
        source_crs: str,
        target_crs: str,
        *,
        unit_contract: UnitCrsContract | None = None,
    ):
        self.source_crs = source_crs
        self.target_crs = target_crs
        if unit_contract is None:
            # Backward compatibility is intentionally limited to the existing
            # metric/projected call shape.  Non-metre CAD always has to pass a
            # reviewed contract explicitly.
            unit_contract = build_unit_crs_contract(6, source_crs, target_crs)
            if (
                unit_contract.source_crs_axis_unit is None
                or not math.isclose(
                    unit_contract.source_crs_axis_unit.metres_per_unit,
                    1.0,
                    rel_tol=1e-12,
                    abs_tol=0.0,
                )
            ):
                raise UnitCrsContractError(
                    "DirectTransformer without unit_contract is supported only for "
                    "legacy metre CAD in a metre source CRS"
                )
        if not isinstance(unit_contract, UnitCrsContract):
            raise TypeError("unit_contract must be a UnitCrsContract")
        if not unit_contract.can_direct_transform:
            raise UnitCrsContractError(
                "DirectTransformer cannot apply a local registration contract; "
                "run the reviewed authoritative registration stage"
            )
        supplied_source = pyproj.CRS.from_user_input(source_crs)
        contract_source = pyproj.CRS.from_user_input(unit_contract.source_crs)
        supplied_target = pyproj.CRS.from_user_input(target_crs)
        contract_target = pyproj.CRS.from_user_input(unit_contract.target_crs)
        if not supplied_source.equals(contract_source):
            raise UnitCrsContractError(
                "unit_contract source_crs does not match DirectTransformer source_crs"
            )
        if not supplied_target.equals(contract_target):
            raise UnitCrsContractError(
                "unit_contract target_crs does not match DirectTransformer target_crs"
            )
        self.unit_contract = unit_contract
        self.unit_crs_manifest = unit_contract.to_manifest_dict()
        self.source_to_crs_axis_factor = float(
            unit_contract.source_to_crs_axis_factor
        )
        self.target_axis_scale_to_m = float(
            unit_contract.target_crs_axis_unit.metres_per_unit
        )
        # The nominal CRS operation is itself a first-class lineage step.  A
        # caller that has no reviewed residual calibration can therefore use
        # this transformer as the delivery step without inventing GCP data.
        self.lineage_model = "nominal_direct"
        self.source = osr.SpatialReference()
        self.target = osr.SpatialReference()
        if self.source.SetFromUserInput(source_crs) != 0:
            raise ValueError(f"Invalid source CRS: {source_crs}")
        if self.target.SetFromUserInput(target_crs) != 0:
            raise ValueError(f"Invalid target CRS: {target_crs}")
        set_traditional_axis_order(self.source, osr)
        set_traditional_axis_order(self.target, osr)
        self.forward = osr.CoordinateTransformation(self.source, self.target)
        self.reverse = osr.CoordinateTransformation(self.target, self.source)
        self.audit_forward = Transformer.from_crs(source_crs, target_crs, always_xy=True)
        self.to_geographic = Transformer.from_crs(source_crs, "EPSG:4326", always_xy=True)
        self.geod = Geod(ellps="WGS84")
        metric_identity = (
            unit_contract.cad_unit.insunits == 6
            and math.isclose(self.source_to_crs_axis_factor, 1.0, rel_tol=1e-12)
        )
        self.coordinate_provenance = (
            "DWG_DERIVED:direct-CRS-transform"
            if metric_identity
            else "DWG_DERIVED:reviewed-unit-contract+direct-CRS-transform"
        )
        self.grid_length_provenance = (
            f"DWG_DERIVED:{target_crs.replace(':', '')}-geometry-length"
            if math.isclose(self.target_axis_scale_to_m, 1.0, rel_tol=1e-12)
            else (
                f"DWG_DERIVED:{target_crs.replace(':', '')}-axis-to-metres-"
                "geometry-length"
            )
        )
        self.geodesic_provenance = "DWG_DERIVED:WGS84-geodesic"

    def point(self, point):
        source_x, source_y = self._source_axis_point(point)
        x, y, _ = self.forward.TransformPoint(source_x, source_y)
        return float(x), float(y)

    def points(self, points):
        return [self.point(point) for point in points]

    def target_to_source_point(self, point):
        """Invert one nominal target-grid point back to immutable CAD space.

        This is intentionally exposed for operator tooling such as GCP capture.
        It inverts only the nominal CRS operation; an accepted residual
        calibration is a separate target-space step and is never hidden here.
        """
        x, y, _ = self.reverse.TransformPoint(float(point[0]), float(point[1]))
        return (
            float(x) / self.source_to_crs_axis_factor,
            float(y) / self.source_to_crs_axis_factor,
        )

    def target_to_source_points(self, points):
        return [self.target_to_source_point(point) for point in points]

    def roundtrip_error(self, points):
        maximum = 0.0
        for point in points:
            target = self.point(point)
            source = self.target_to_source_point(target)
            maximum = max(maximum, self.source_length_to_m(
                math.hypot(source[0] - point[0], source[1] - point[1])
            ))
        return maximum

    def engine_crosscheck_error(self, points):
        maximum = 0.0
        for point in points:
            osr_target = self.point(point)
            proj_target = self.audit_forward.transform(*self._source_axis_point(point))
            maximum = max(maximum, self.target_length_to_m(
                math.dist(osr_target, proj_target)
            ))
        return maximum

    def operation_metadata(self, reference_point=None):
        if reference_point is not None:
            self.audit_forward.transform(*self._source_axis_point(reference_point))
        try:
            operation = self.audit_forward.get_last_used_operation()
        except (AttributeError, pyproj.exceptions.ProjError):
            operation = self.audit_forward
        accuracy = float(operation.accuracy)
        return {
            "description": operation.description,
            "definition": operation.definition,
            "declared_accuracy_m": None if accuracy < 0 else accuracy,
            "pyproj_version": pyproj.__version__,
            "proj_version": pyproj.proj_version_str,
            "absolute_accuracy_validation": "not independently verified; no surveyed GCP supplied",
            "unit_crs_contract": self.unit_contract.to_manifest_dict(),
        }

    def geodesic_length(self, points):
        total = 0.0
        for start, end in zip(points, points[1:]):
            lon1, lat1 = self.to_geographic.transform(*self._source_axis_point(start))
            lon2, lat2 = self.to_geographic.transform(*self._source_axis_point(end))
            _, _, distance = self.geod.inv(lon1, lat1, lon2, lat2)
            total += abs(distance)
        return total

    def _source_axis_point(self, point):
        return (
            float(point[0]) * self.source_to_crs_axis_factor,
            float(point[1]) * self.source_to_crs_axis_factor,
        )

    def source_length_to_m(self, value):
        return self.unit_contract.source_length_to_m(value)

    def target_length_to_m(self, value):
        return self.unit_contract.target_length_to_m(value)

    def grid_length_m(self, target_points):
        return self.target_length_to_m(sum(
            math.dist(start, end)
            for start, end in zip(target_points, target_points[1:])
        ))

    def qgis_rotation(self, native_anchor, cad_radians):
        """Transform a CAD direction into clockwise nominal target-grid degrees."""
        return _qgis_rotation(self.point, native_anchor, cad_radians)


class DeliveryTransformer:
    """Apply an accepted target-space calibration after the nominal CRS step.

    Native CAD coordinates remain untouched.  The wrapper intentionally does
    not expose CRS round-trip checks: those belong to ``DirectTransformer`` and
    must not be confused with independent ground-control validation.
    """

    def __init__(self, nominal: DirectTransformer, calibration, profile_sha256=None):
        self.nominal = nominal
        self.calibration = calibration
        self.profile_sha256 = None if profile_sha256 is None else str(profile_sha256)
        self.source_crs = nominal.source_crs
        self.target_crs = nominal.target_crs
        self.unit_contract = getattr(nominal, "unit_contract", None)
        if self.unit_contract is None:
            # Compatibility adapter for the established hand-written nominal
            # transformers used by independent calibration tests.  It is
            # deliberately restricted to projected metre source/target CRSs;
            # a non-metre or unknown real converter still has to supply its
            # reviewed UnitCrsContract and can never inherit a guessed scale.
            self.unit_contract = build_unit_crs_contract(
                6, nominal.source_crs, nominal.target_crs,
            )
            if (
                self.unit_contract.source_crs_axis_unit is None
                or not math.isclose(
                    self.unit_contract.source_crs_axis_unit.metres_per_unit,
                    1.0,
                    rel_tol=1e-12,
                )
                or not math.isclose(
                    self.unit_contract.target_crs_axis_unit.metres_per_unit,
                    1.0,
                    rel_tol=1e-12,
                )
            ):
                raise UnitCrsContractError(
                    "Legacy nominal transformer adapter requires projected metre CRSs; "
                    "supply an explicit unit_contract"
                )
            self.unit_contract_origin = "legacy-metric-nominal-adapter"
        else:
            self.unit_contract_origin = "nominal-transformer"
        self.unit_crs_manifest = self.unit_contract.to_manifest_dict()
        self.target_axis_scale_to_m = (
            self.unit_contract.target_crs_axis_unit.metres_per_unit
        )
        self.source = nominal.source
        self.target = nominal.target
        self.geod = nominal.geod
        self.target_to_geographic = Transformer.from_crs(
            nominal.target_crs, "EPSG:4326", always_xy=True,
        )
        summary = calibration.to_dict()
        self.calibration_summary = summary
        selected_model = str(summary.get("selected_model", summary.get("model", "disabled")))
        self.calibration_active = selected_model not in {"disabled", "identity"}
        # Keep the historical calibration result (and its ``disabled`` model)
        # intact for audit compatibility, while giving the feature lineage a
        # stable name for the identity residual step.
        self.lineage_model = selected_model if self.calibration_active else "identity_residual"
        validation = dict(summary.get("validation") or {})
        if self.calibration_active and validation.get("passed") is not True:
            raise ValueError("Cannot construct delivery transformer from a failed calibration")
        if self.calibration_active:
            model = selected_model
            profile_token = "" if self.profile_sha256 is None else f"-{self.profile_sha256[:12]}"
            self.coordinate_provenance = (
                f"DWG_DERIVED:GCP-{model}{profile_token}-after-direct-CRS"
            )
            self.grid_length_provenance = (
                f"DWG_DERIVED:{nominal.target_crs}-GCP-{model}{profile_token}-geometry-length"
            )
            self.geodesic_provenance = (
                f"DWG_DERIVED:GCP-{model}{profile_token}-target-to-WGS84-geodesic"
            )
        else:
            self.coordinate_provenance = "DWG_DERIVED:direct-CRS-transform"
            self.grid_length_provenance = "DWG_DERIVED:EPSG9481-geometry-length"
            self.geodesic_provenance = "DWG_DERIVED:WGS84-geodesic"

    def point(self, point):
        return self.calibration.project_native(point, self.nominal)

    def points(self, points):
        return [self.point(point) for point in points]

    def geodesic_length(self, native_points):
        if not self.calibration_active:
            # Preserve byte-for-byte metric behaviour for the disabled profile.
            return self.nominal.geodesic_length(native_points)
        total = 0.0
        adjusted = self.points(native_points)
        for start, end in zip(adjusted, adjusted[1:]):
            lon1, lat1 = self.target_to_geographic.transform(*start)
            lon2, lat2 = self.target_to_geographic.transform(*end)
            _, _, distance = self.geod.inv(lon1, lat1, lon2, lat2)
            total += abs(distance)
        return total

    def source_length_to_m(self, value):
        return self.unit_contract.source_length_to_m(value)

    def target_length_to_m(self, value):
        return self.unit_contract.target_length_to_m(value)

    def grid_length_m(self, target_points):
        return self.target_length_to_m(sum(
            math.dist(start, end)
            for start, end in zip(target_points, target_points[1:])
        ))

    def qgis_rotation(self, native_anchor, cad_radians):
        """Transform a CAD direction into QGIS clockwise target-grid degrees."""
        return _qgis_rotation(self.point, native_anchor, cad_radians)


def _lineage_model(delivery, explicit_model=None):
    """Return the explicit model label used by target-space lineage records.

    ``adjusted_*`` is the established on-disk field name for the delivery
    coordinate space.  The model label distinguishes a direct nominal
    operation from an identity residual calibration so disabled/absent GCP
    profiles are never represented as a fabricated GCP model.
    """
    model = explicit_model
    if model is None:
        model = getattr(delivery, "lineage_model", None)
    if model is None:
        summary = getattr(delivery, "calibration_summary", {}) or {}
        model = summary.get("selected_model", summary.get("model"))
    if model is None or str(model).strip() == "":
        return "nominal_direct"
    model = str(model)
    if model in {"disabled", "identity"}:
        return "identity_residual"
    return model


def feature_adjustment_records(
    features, nominal: DirectTransformer, delivery, *, lineage_model=None,
):
    """Create full native/nominal/delivery evidence without editing geometry.

    The persisted schema historically calls the delivery arrays ``adjusted``;
    those fields are retained for accepted-GCP compatibility.  Every feature
    with native vertices receives one complete record, including direct and
    disabled identity paths.
    """
    model = _lineage_model(delivery, lineage_model)
    records = []
    for feature in features:
        nominal_points = nominal.points(feature.native_points)
        adjusted_points = delivery.points(feature.native_points)
        displacements = [
            math.dist(before, after)
            for before, after in zip(nominal_points, adjusted_points)
        ]
        if not nominal_points:
            continue
        nominal_centroid = (
            sum(point[0] for point in nominal_points) / len(nominal_points),
            sum(point[1] for point in nominal_points) / len(nominal_points),
        )
        adjusted_centroid = (
            sum(point[0] for point in adjusted_points) / len(adjusted_points),
            sum(point[1] for point in adjusted_points) / len(adjusted_points),
        )
        native_points_json = json.dumps(
            feature.native_points,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
        nominal_points_json = json.dumps(
            nominal_points,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
        adjusted_points_json = json.dumps(
            adjusted_points,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
        records.append({
            "feature_key": feature.feature_key,
            "feature_class": feature.feature_class,
            "source_entity_key": feature.source_entity_key,
            "native_points_json": native_points_json,
            "nominal_points_json": nominal_points_json,
            "adjusted_points_json": adjusted_points_json,
            "native_fingerprint": hashlib.sha256(
                native_points_json.encode("ascii")
            ).hexdigest(),
            "nominal_fingerprint": hashlib.sha256(
                nominal_points_json.encode("ascii")
            ).hexdigest(),
            "adjusted_fingerprint": hashlib.sha256(
                adjusted_points_json.encode("ascii")
            ).hexdigest(),
            "model": model,
            "nominal_centroid_easting": nominal_centroid[0],
            "nominal_centroid_northing": nominal_centroid[1],
            "adjusted_centroid_easting": adjusted_centroid[0],
            "adjusted_centroid_northing": adjusted_centroid[1],
            "centroid_dx_m": adjusted_centroid[0] - nominal_centroid[0],
            "centroid_dy_m": adjusted_centroid[1] - nominal_centroid[1],
            "mean_displacement_m": sum(displacements) / len(displacements),
            "max_displacement_m": max(displacements),
        })
    return records


def _ordered_source_span_metrics(feature, source_segments, transformer):
    """Return one auditable metric record for every immutable CABLE segment."""
    expected_count = len(source_segments)
    raw_metrics = feature.attributes.get("span_metrics")
    if raw_metrics is None:
        raw_metrics = [
            {
                "segment_index": segment_index,
                # Topology historically populated the field before the unit
                # contract was available.  It therefore contains a native CAD
                # length at this boundary and is normalized below.
                "source_native_length_m": segment["source_native_length"],
                "dimension_entity_key": None,
                "measurement_native_m": None,
                "measurement_delta_m": None,
                "status": "unmeasured_no_dimension",
            }
            for segment_index, segment in enumerate(source_segments)
        ]
    if not isinstance(raw_metrics, list) or len(raw_metrics) != expected_count:
        raise RuntimeError(
            f"CABLE span metric count mismatch for {feature.feature_key}: "
            f"expected {expected_count}, got "
            f"{len(raw_metrics) if isinstance(raw_metrics, list) else type(raw_metrics).__name__}"
        )

    ordered = []
    scale_to_m = transformer.unit_contract.source_coordinate_scale_to_m
    for segment_index, segment in enumerate(source_segments):
        metric = dict(raw_metrics[segment_index])
        if metric.get("segment_index") != segment_index:
            raise RuntimeError(
                f"CABLE span metrics are not source ordered for {feature.feature_key}"
            )
        source_length = float(segment["source_native_length"])
        if not math.isfinite(source_length) or source_length < 0.0:
            raise RuntimeError(
                f"CABLE source curve length is invalid for "
                f"{feature.feature_key}:segment:{segment_index}"
            )
        recorded_source_length = float(metric.get("source_native_length_m"))
        if not math.isfinite(recorded_source_length) or abs(
            recorded_source_length - source_length
        ) > 1e-9:
            # Allow a second enrichment pass only when the first pass recorded
            # the explicit normalization marker and the metre value closes.
            normalized_source_length = transformer.source_length_to_m(source_length)
            if not (
                metric.get("source_length_unit_contract")
                == transformer.unit_contract.schema_version
                and abs(recorded_source_length - normalized_source_length) <= 1e-9
            ):
                raise RuntimeError(
                    f"CABLE source span length mismatch for "
                    f"{feature.feature_key}:segment:{segment_index}"
                )
        else:
            normalized_source_length = transformer.source_length_to_m(source_length)
        measurement = metric.get("measurement_native_m")
        if measurement is not None:
            measurement = float(measurement)
            if not math.isfinite(measurement):
                raise RuntimeError(
                    f"Non-finite CABLE dimension measurement for "
                    f"{feature.feature_key}:segment:{segment_index}"
                )
            expected_delta = measurement - source_length
            delta = metric.get("measurement_delta_m")
            normalized_already = (
                metric.get("source_length_unit_contract")
                == transformer.unit_contract.schema_version
            )
            if normalized_already:
                # On a repeated pass the measurement value has already been
                # normalized, so compare it directly to the materialized metre
                # length plus its stored metre delta.
                measurement_m = measurement
                expected_delta_m = measurement_m - normalized_source_length
                if delta is None or abs(float(delta) - expected_delta_m) > 1e-9:
                    raise RuntimeError(
                        f"CABLE dimension delta mismatch for "
                        f"{feature.feature_key}:segment:{segment_index}"
                    )
            else:
                if delta is None or abs(float(delta) - expected_delta) > 1e-9:
                    raise RuntimeError(
                        f"CABLE dimension delta mismatch for "
                        f"{feature.feature_key}:segment:{segment_index}"
                    )
                measurement_m = transformer.source_length_to_m(measurement)
                expected_delta_m = transformer.source_length_to_m(expected_delta)
        elif metric.get("measurement_delta_m") is not None:
            raise RuntimeError(
                f"CABLE unmeasured span has a measurement delta for "
                f"{feature.feature_key}:segment:{segment_index}"
            )
        else:
            measurement_m = None
            expected_delta_m = None
        if metric.get("status") == "measured" and (
            not metric.get("dimension_entity_key") or measurement is None
        ):
            raise RuntimeError(
                f"CABLE measured span lacks DIMENSION evidence for "
                f"{feature.feature_key}:segment:{segment_index}"
            )
        metric.update({
            "source_native_length_m": normalized_source_length,
            "measurement_native_m": measurement_m,
            "measurement_delta_m": expected_delta_m,
            "source_segment_kind": str(segment["source_segment_kind"]),
            "native_length_source": str(segment["native_length_source"]),
            "source_length_unit_contract": transformer.unit_contract.schema_version,
            "source_drawing_unit": transformer.unit_contract.cad_unit.symbol,
            "source_coordinate_scale_to_m": scale_to_m,
        })
        ordered.append(metric)
    return ordered


def enrich_delivery_metrics(features, transformer: DirectTransformer):
    """Populate delivery-coordinate metrics once, with explicit provenance."""
    # Local import keeps the curve materializer independent from CRS code and
    # prevents a georef <-> topology import cycle.
    from .curve_geometry import delivery_points, delivery_segments

    for feature in features:
        if hasattr(transformer, "qgis_rotation"):
            qgis_rotation = transformer.qgis_rotation(
                feature.native_centroid, feature.style.rotation,
            )
            render_key = feature.style.render_key.rsplit("|ROT_QGIS:", 1)[0]
            feature.attributes.update({
                "delivery_style_qgis_rotation_deg": qgis_rotation,
                "delivery_style_render_key": f"{render_key}|ROT_QGIS:{qgis_rotation:.9f}",
            })
            feature.field_provenance.update({
                "delivery_style_qgis_rotation_deg": transformer.coordinate_provenance,
                "delivery_style_render_key": transformer.coordinate_provenance,
            })
        if feature.geometry_kind == "Point" and feature.native_points:
            x, y = transformer.point(feature.native_points[0])
            feature.attributes.update({"X": x, "Y": y})
            feature.field_provenance.update({
                "X": getattr(transformer, "coordinate_provenance", "DWG_DERIVED:direct-CRS-transform"),
                "Y": getattr(transformer, "coordinate_provenance", "DWG_DERIVED:direct-CRS-transform"),
            })
        elif feature.geometry_kind == "LineString":
            delivery_native_points = delivery_points(
                feature, require_materialized=False,
            )
            target_points = transformer.points(delivery_native_points)
            if feature.feature_class == "CABLE":
                # The production pipeline has already run strict curve
                # materialization and validation.  ``False`` preserves the
                # long-standing standalone enrichment API for explicitly
                # straight synthetic/legacy features; it never weakens the
                # production gate.
                source_segments = delivery_segments(
                    feature, require_materialized=False,
                )
                span_metrics = _ordered_source_span_metrics(
                    feature, source_segments, transformer,
                )
                enriched_spans = []
                for metric, segment in zip(span_metrics, source_segments):
                    segment_native_points = segment["delivery_native_points"]
                    segment_target_points = transformer.points(segment_native_points)
                    enriched = dict(metric)
                    enriched.update({
                        "delivery_grid_length_m": transformer.grid_length_m(
                            segment_target_points
                        ),
                        "geodesic_length_m": transformer.geodesic_length(
                            segment_native_points
                        ),
                    })
                    enriched_spans.append(enriched)
                measured_count = sum(
                    metric["status"] == "measured" for metric in enriched_spans
                )
                unmeasured_count = len(enriched_spans) - measured_count
                measurement_status = (
                    "complete" if unmeasured_count == 0
                    else "partial" if measured_count else "unavailable"
                )
                dimension_total_m = (
                    sum(
                        metric["measurement_native_m"]
                        for metric in enriched_spans
                        if metric["measurement_native_m"] is not None
                    )
                    if measured_count
                    else None
                )
                source_segment_sum_m = sum(
                    metric["source_native_length_m"] for metric in enriched_spans
                )
                source_autocad_length = feature.attributes.get(
                    "source_autocad_native_length_m"
                )
                already_normalized = (
                    feature.attributes.get("unit_crs_contract_schema_version")
                    == transformer.unit_contract.schema_version
                )
                if source_autocad_length is not None:
                    source_autocad_length = float(source_autocad_length)
                    if not already_normalized:
                        source_autocad_length = transformer.source_length_to_m(
                            source_autocad_length
                        )
                    source_cad_length_m = source_autocad_length
                    source_length_delta_m = (
                        source_autocad_length - source_segment_sum_m
                    )
                else:
                    source_cad_length_m = source_segment_sum_m
                    source_length_delta_m = None
                feature.attributes.update({
                    "source_cad_length_m": source_cad_length_m,
                    "source_segment_sum_m": source_segment_sum_m,
                    "source_native_length_delta_m": source_length_delta_m,
                    "source_autocad_native_length_m": source_autocad_length,
                    "dimension_length_m": dimension_total_m,
                    "span_count": len(enriched_spans),
                    "measured_span_count": measured_count,
                    "unmeasured_span_count": unmeasured_count,
                    "dimension_measured_sum_m": dimension_total_m,
                    "dimension_measurement_status": measurement_status,
                    "dimension_coverage_ratio": (
                        measured_count / len(enriched_spans) if enriched_spans else 0.0
                    ),
                    "span_schema_version": "cad2gis.cable_span_metrics.v1",
                    "span_unit": "m",
                    "span_metrics": enriched_spans,
                    "unit_crs_contract_schema_version": (
                        transformer.unit_contract.schema_version
                    ),
                })
                grid_length = sum(
                    metric["delivery_grid_length_m"] for metric in enriched_spans
                )
                geodesic_length = sum(
                    metric["geodesic_length_m"] for metric in enriched_spans
                )
                source_unit_provenance = {}
                if transformer.unit_contract.cad_unit.insunits != 6:
                    source_unit_provenance = {
                        "source_cad_length_m": (
                            "DWG_DERIVED:reviewed-unit-CRS-contract-to-metres"
                        ),
                        "source_segment_sum_m": (
                            "DWG_DERIVED:curve-materialization-and-reviewed-unit-contract"
                        ),
                        "source_native_length_delta_m": (
                            "DWG_DERIVED:AutoCAD-minus-materialized-segment-sum-metres"
                        ),
                        "source_autocad_native_length_m": (
                            "DWG_DERIVED:reviewed-unit-CRS-contract-to-metres"
                        ),
                        "dimension_length_m": (
                            "DWG_DERIVED:reviewed-unit-CRS-contract-to-metres"
                        ),
                    }
                feature.field_provenance.update({
                    "span_count": "DWG_DIRECT:polyline-segment-count",
                    "measured_span_count": "DWG_DERIVED:SPAN-CABLE-exact-segment-membership",
                    "unmeasured_span_count": "DWG_DERIVED:SPAN-CABLE-exact-segment-membership",
                    "dimension_measured_sum_m": "DWG_DIRECT:SPAN-CABLE-measurements",
                    "dimension_measurement_status": "DWG_DERIVED:SPAN-CABLE-measurement-coverage",
                    "dimension_coverage_ratio": "DWG_DERIVED:SPAN-CABLE-measurement-coverage",
                    "span_schema_version": "DWG_DERIVED:versioned-span-metric-contract",
                    "span_unit": (
                        "DWG_DIRECT:INSUNITS-6-metres"
                        if transformer.unit_contract.cad_unit.insunits == 6
                        else "DWG_DERIVED:reviewed-unit-CRS-contract-to-metres"
                    ),
                    "span_metrics": "DWG_DERIVED:per-source-segment-length-closure",
                    "unit_crs_contract_schema_version": (
                        "SYSTEM:cad2gis-unit-CRS-contract"
                    ),
                    **source_unit_provenance,
                })
            else:
                grid_length = transformer.grid_length_m(target_points)
                geodesic_length = transformer.geodesic_length(delivery_native_points)
            feature.attributes.update({
                "LONGUEUR": grid_length,
                "delivery_grid_length_m": grid_length,
                "geodesic_length_m": geodesic_length,
            })
            feature.field_provenance.update({
                "LONGUEUR": getattr(transformer, "grid_length_provenance", "DWG_DERIVED:EPSG9481-geometry-length"),
                "delivery_grid_length_m": getattr(transformer, "grid_length_provenance", "DWG_DERIVED:EPSG9481-geometry-length"),
                "geodesic_length_m": getattr(transformer, "geodesic_provenance", "DWG_DERIVED:WGS84-geodesic"),
            })
