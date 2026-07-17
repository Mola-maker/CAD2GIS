"""Direct native-to-delivery CRS transformation with round-trip diagnostics."""

from __future__ import annotations

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


def enrich_delivery_metrics(features, transformer: DirectTransformer):
    """Populate delivery-coordinate metrics once, with explicit provenance."""
    for feature in features:
        if feature.geometry_kind == "Point" and feature.native_points:
            x, y = transformer.point(feature.native_points[0])
            feature.attributes.update({"X": x, "Y": y})
            feature.field_provenance.update({
                "X": "DWG_DERIVED:direct-CRS-transform",
                "Y": "DWG_DERIVED:direct-CRS-transform",
            })
        elif feature.geometry_kind == "LineString":
            target_points = transformer.points(feature.native_points)
            grid_length = sum(
                math.dist(start, end) for start, end in zip(target_points, target_points[1:])
            )
            geodesic_length = transformer.geodesic_length(feature.native_points)
            feature.attributes.update({
                "LONGUEUR": grid_length,
                "delivery_grid_length_m": grid_length,
                "geodesic_length_m": geodesic_length,
            })
            feature.field_provenance.update({
                "LONGUEUR": "DWG_DERIVED:EPSG9481-geometry-length",
                "delivery_grid_length_m": "DWG_DERIVED:EPSG9481-geometry-length",
                "geodesic_length_m": "DWG_DERIVED:WGS84-geodesic",
            })
