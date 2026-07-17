"""Direct native-to-delivery CRS transformation with round-trip diagnostics."""

from __future__ import annotations

import hashlib
import json
import math

from osgeo import osr
import pyproj
from pyproj import Geod, Transformer

from apd_rules import set_traditional_axis_order


class DirectTransformer:
    def __init__(self, source_crs: str, target_crs: str):
        self.source_crs = source_crs
        self.target_crs = target_crs
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

    def point(self, point):
        x, y, _ = self.forward.TransformPoint(float(point[0]), float(point[1]))
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
        return float(x), float(y)

    def target_to_source_points(self, points):
        return [self.target_to_source_point(point) for point in points]

    def roundtrip_error(self, points):
        maximum = 0.0
        for point in points:
            target = self.point(point)
            x, y, _ = self.reverse.TransformPoint(target[0], target[1])
            maximum = max(maximum, math.hypot(x - point[0], y - point[1]))
        return maximum

    def engine_crosscheck_error(self, points):
        maximum = 0.0
        for point in points:
            osr_target = self.point(point)
            proj_target = self.audit_forward.transform(*point)
            maximum = max(maximum, math.dist(osr_target, proj_target))
        return maximum

    def operation_metadata(self, reference_point=None):
        if reference_point is not None:
            self.audit_forward.transform(*reference_point)
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
        }

    def geodesic_length(self, points):
        total = 0.0
        for start, end in zip(points, points[1:]):
            lon1, lat1 = self.to_geographic.transform(*start)
            lon2, lat2 = self.to_geographic.transform(*end)
            _, _, distance = self.geod.inv(lon1, lat1, lon2, lat2)
            total += abs(distance)
        return total


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

    def qgis_rotation(self, native_anchor, cad_radians):
        """Transform a CAD direction into QGIS clockwise target-grid degrees."""
        if not self.calibration_active:
            return (-math.degrees(float(cad_radians))) % 360.0
        anchor = (float(native_anchor[0]), float(native_anchor[1]))
        probe = (
            anchor[0] + math.cos(float(cad_radians)),
            anchor[1] + math.sin(float(cad_radians)),
        )
        target_anchor = self.point(anchor)
        target_probe = self.point(probe)
        target_ccw = math.degrees(math.atan2(
            target_probe[1] - target_anchor[1],
            target_probe[0] - target_anchor[0],
        ))
        return (-target_ccw) % 360.0


def feature_adjustment_records(features, nominal: DirectTransformer, delivery: DeliveryTransformer):
    """Create full target-space displacement evidence without editing source geometry."""
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
            "model": delivery.calibration_summary.get(
                "selected_model", delivery.calibration_summary.get("model", "identity"),
            ),
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


def _ordered_source_span_metrics(feature):
    """Return one auditable metric record for every immutable CABLE segment."""
    expected_count = max(0, len(feature.native_points) - 1)
    raw_metrics = feature.attributes.get("span_metrics")
    if raw_metrics is None:
        raw_metrics = [
            {
                "segment_index": segment_index,
                "source_native_length_m": math.dist(start, end),
                "dimension_entity_key": None,
                "measurement_native_m": None,
                "measurement_delta_m": None,
                "status": "unmeasured_no_dimension",
            }
            for segment_index, (start, end) in enumerate(
                zip(feature.native_points, feature.native_points[1:])
            )
        ]
    if not isinstance(raw_metrics, list) or len(raw_metrics) != expected_count:
        raise RuntimeError(
            f"CABLE span metric count mismatch for {feature.feature_key}: "
            f"expected {expected_count}, got "
            f"{len(raw_metrics) if isinstance(raw_metrics, list) else type(raw_metrics).__name__}"
        )

    ordered = []
    for segment_index, (start, end) in enumerate(
        zip(feature.native_points, feature.native_points[1:])
    ):
        metric = dict(raw_metrics[segment_index])
        if metric.get("segment_index") != segment_index:
            raise RuntimeError(
                f"CABLE span metrics are not source ordered for {feature.feature_key}"
            )
        source_length = math.dist(start, end)
        recorded_source_length = float(metric.get("source_native_length_m"))
        if not math.isfinite(recorded_source_length) or abs(
            recorded_source_length - source_length
        ) > 1e-9:
            raise RuntimeError(
                f"CABLE source span length mismatch for {feature.feature_key}:segment:{segment_index}"
            )
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
            if delta is None or abs(float(delta) - expected_delta) > 1e-9:
                raise RuntimeError(
                    f"CABLE dimension delta mismatch for {feature.feature_key}:segment:{segment_index}"
                )
        elif metric.get("measurement_delta_m") is not None:
            raise RuntimeError(
                f"CABLE unmeasured span has a measurement delta for "
                f"{feature.feature_key}:segment:{segment_index}"
            )
        if metric.get("status") == "measured" and (
            not metric.get("dimension_entity_key") or measurement is None
        ):
            raise RuntimeError(
                f"CABLE measured span lacks DIMENSION evidence for "
                f"{feature.feature_key}:segment:{segment_index}"
            )
        ordered.append(metric)
    return ordered


def enrich_delivery_metrics(features, transformer: DirectTransformer):
    """Populate delivery-coordinate metrics once, with explicit provenance."""
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
            target_points = transformer.points(feature.native_points)
            if feature.feature_class == "CABLE":
                span_metrics = _ordered_source_span_metrics(feature)
                enriched_spans = []
                for metric, native_start, native_end, target_start, target_end in zip(
                    span_metrics,
                    feature.native_points,
                    feature.native_points[1:],
                    target_points,
                    target_points[1:],
                ):
                    enriched = dict(metric)
                    enriched.update({
                        "delivery_grid_length_m": math.dist(target_start, target_end),
                        "geodesic_length_m": transformer.geodesic_length(
                            (native_start, native_end)
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
                feature.attributes.update({
                    "span_count": len(enriched_spans),
                    "measured_span_count": measured_count,
                    "unmeasured_span_count": unmeasured_count,
                    "dimension_measured_sum_m": feature.attributes.get("dimension_length_m"),
                    "dimension_measurement_status": measurement_status,
                    "dimension_coverage_ratio": (
                        measured_count / len(enriched_spans) if enriched_spans else 0.0
                    ),
                    "span_schema_version": "cad2gis.cable_span_metrics.v1",
                    "span_unit": "m",
                    "span_metrics": enriched_spans,
                })
                grid_length = sum(
                    metric["delivery_grid_length_m"] for metric in enriched_spans
                )
                geodesic_length = sum(
                    metric["geodesic_length_m"] for metric in enriched_spans
                )
                feature.field_provenance.update({
                    "span_count": "DWG_DIRECT:polyline-segment-count",
                    "measured_span_count": "DWG_DERIVED:SPAN-CABLE-exact-segment-membership",
                    "unmeasured_span_count": "DWG_DERIVED:SPAN-CABLE-exact-segment-membership",
                    "dimension_measured_sum_m": "DWG_DIRECT:SPAN-CABLE-measurements",
                    "dimension_measurement_status": "DWG_DERIVED:SPAN-CABLE-measurement-coverage",
                    "dimension_coverage_ratio": "DWG_DERIVED:SPAN-CABLE-measurement-coverage",
                    "span_schema_version": "DWG_DERIVED:versioned-span-metric-contract",
                    "span_unit": "DWG_DIRECT:INSUNITS-6-metres",
                    "span_metrics": "DWG_DERIVED:per-source-segment-length-closure",
                })
            else:
                grid_length = sum(
                    math.dist(start, end)
                    for start, end in zip(target_points, target_points[1:])
                )
                geodesic_length = transformer.geodesic_length(feature.native_points)
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
